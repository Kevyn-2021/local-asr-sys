"""音频 IO 工具：统一 16kHz 单声道加载；用 soundfile / pydub / librosa，避开 torchcodec（CPU 环境缺 CUDA 库）"""
from __future__ import annotations

import bisect
import gc
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf
import torch
from pydub import AudioSegment

TARGET_SR = 16000


@dataclass
class AudioLoad:
    """和 PyAnnote pipeline 字典输入格式兼容：{'waveform': (C,T), 'sample_rate': int}"""
    waveform: torch.Tensor  # shape (1, T)  torch.float32
    sample_rate: int
    duration_s: float
    original_path: Path


def _to_mono_float32(y: np.ndarray) -> np.ndarray:
    if y.ndim == 2:
        y = y.mean(axis=1)
    if not np.issubdtype(y.dtype, np.floating):
        y = y.astype(np.float32) / np.iinfo(y.dtype).max
    return y.astype(np.float32, order="C")


def load_audio(path: Path, *, target_sr: int = TARGET_SR) -> AudioLoad:
    """优先 soundfile；失败回退 pydub 解码 (ffmpeg)；返回 shape (1, T) 的 torch CPU tensor。"""
    path = Path(path)
    try:
        data, sr = sf.read(str(path), always_2d=False, dtype="float32")
        mono = _to_mono_float32(data)
    except Exception:
        # pydub fallback：通过 ffmpeg 解码，支持 mp3 / m4a / webm 等
        seg = AudioSegment.from_file(str(path))
        if seg.channels != 1:
            seg = seg.set_channels(1)
        if seg.frame_rate != target_sr:
            seg = seg.set_frame_rate(target_sr)
        raw = np.array(seg.get_array_of_samples(), dtype=np.float32)
        # normalize to [-1,1]
        width = seg.sample_width * 8
        peak = float(1 << (width - 1)) if width else 1.0
        mono = raw / peak
        sr = target_sr
    # 重采样（如果必要）
    if sr != target_sr:
        try:
            import librosa
            mono = librosa.resample(mono, orig_sr=sr, target_sr=target_sr)
        except Exception:
            raise RuntimeError(f"需要重采样 {sr}->{target_sr} 但 librosa 不可用")
        sr = target_sr
    T = torch.from_numpy(mono).unsqueeze(0).contiguous()  # (1, T)
    dur = float(T.shape[1]) / float(sr)
    # 释放 numpy 中间变量，降低 15W CPU 下的内存压力
    del mono, data
    gc.collect()
    return AudioLoad(waveform=T, sample_rate=sr, duration_s=dur, original_path=path)


def segment_waveform(full: AudioLoad, start_s: float, end_s: float) -> torch.Tensor:
    """从完整音频按原始偏移裁一段，保证时间戳体系不混乱。"""
    s = max(0, int(start_s * full.sample_rate))
    e = min(full.waveform.shape[1], int(end_s * full.sample_rate))
    if e <= s:
        e = s + 1
    return full.waveform[:, s:e].contiguous()


def build_speech_concatenation(
    audio: AudioLoad,
    vad_segments,
    *,
    gap_threshold_s: float = 0.1,
) -> tuple[AudioLoad, Callable[[float], float]]:
    """按 VAD 语音段拼接音频，切除静音，用于加速说话人分离（v2.31）。

    原理：PyAnnote Diarization 的 segmentation 滑窗按"总时长"遍历，静音也在白白计算；
    先按 Silero VAD 段拼接成连续音频，把输入长度压缩为"语音总时长"，
    分离结果的时间戳再映射回原始时间轴（对调用方完全无感）。

    - 拼接前先合并重叠/紧邻段：Silero 输出含 speech_pad_ms 边界，相邻语音段可能重叠，
      gap < gap_threshold_s 的段视为同一次说话，合并后切割边界更干净。
    - 映射函数 map_back(t_concat) -> t_original：拼接轴时间按段内线性比例映射回原始轴。
    - 段间按 VAD 边界紧邻拼接（不额外补静音），段内保留原始波形。
    - 若没有语音段，原样返回（map_back 为恒等函数）。

    vad_segments: 任意带 start_offset_s / end_offset_s 字段的对象列表（鸭子类型，避免循环依赖）。
    """
    if not vad_segments:
        return audio, lambda t: float(t)

    # 1. 合并重叠/紧邻段
    merged: list[tuple[float, float]] = []
    for seg in sorted(vad_segments, key=lambda s: s.start_offset_s):
        s, e = float(seg.start_offset_s), float(seg.end_offset_s)
        if e <= s:
            continue
        if merged and s <= merged[-1][1] + gap_threshold_s:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    if not merged:
        return audio, lambda t: float(t)

    # 2. 按合并后的语音段拼接波形
    sr = audio.sample_rate
    wav = audio.waveform  # (1, T)
    pieces: list[tuple[int, int]] = []
    for s, e in merged:
        si, ei = int(round(s * sr)), int(round(e * sr))
        if ei > si:
            pieces.append((si, ei))
    if not pieces:
        return audio, lambda t: float(t)

    parts = [wav[:, si:ei] for si, ei in pieces]
    concat_wav = torch.cat(parts, dim=1)
    concat_dur = float(concat_wav.shape[1]) / sr
    concat_audio = AudioLoad(
        waveform=concat_wav, sample_rate=sr,
        duration_s=concat_dur, original_path=audio.original_path,
    )

    # 3. 拼接轴 -> 原始轴 映射表：(concat_start, concat_end, orig_start, orig_end)
    table: list[tuple[float, float, float, float]] = []
    cursor = 0.0
    for si, ei in pieces:
        cs, ce = cursor, cursor + (ei - si) / sr
        table.append((cs, ce, si / sr, ei / sr))
        cursor = ce
    concat_starts = [t[0] for t in table]

    def map_back(t: float) -> float:
        if t <= 0.0:
            return table[0][2]
        idx = bisect.bisect_right(concat_starts, t) - 1
        if idx < 0:
            idx = 0
        cs, ce, os_, oe = table[idx]
        if t >= ce:
            return oe
        frac = (t - cs) / (ce - cs) if ce > cs else 0.0
        return os_ + frac * (oe - os_)

    return concat_audio, map_back
