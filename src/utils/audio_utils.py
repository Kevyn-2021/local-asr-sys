"""音频 IO 工具：统一 16kHz 单声道加载；用 soundfile / pydub / librosa，避开 torchcodec（CPU 环境缺 CUDA 库）"""
from __future__ import annotations

import gc
from dataclasses import dataclass
from pathlib import Path

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
