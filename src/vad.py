"""
Silero VAD 封装 — PRD FR-002
基于 Silero VAD（snakers4/silero-vad），通过 torch.hub 加载，
对音频做语音活动检测，输出语音段。
保留原始音频 start_offset/end_offset，绝不破坏整体时间戳体系。

选型理由：Silero VAD 在纯 VAD 任务上效果优于 PyAnnote segmentation-3.0 的 VAD 模式，
且模型更轻量（~1MB）、加载速度更快、延迟更低。通过代理可正常从 GitHub 下载。

说明：早期版本曾使用 PyAnnote VAD（复用 pyannote/segmentation-3.0），
经评估 Silero VAD 在纯语音活动检测场景下效果更优，已于 v2.7 切换。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import torch

from config.settings import MODELS_DIR, VAD_CONFIG
from src.utils.audio_utils import AudioLoad

log = logging.getLogger("asr-vad")

SILERO_CACHE_DIR = MODELS_DIR / "silero-vad"


@dataclass
class VadSegment:
    start_offset_s: float
    end_offset_s: float
    score: float


class SileroVad:
    def __init__(self):
        SILERO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        torch.hub.set_dir(str(SILERO_CACHE_DIR))
        # v2.17 离线优先：本地缓存仓库存在则完全离线加载（source='local'），
        # 不再与 GitHub 交互；仅当本地缓存缺失时才联网下载一次。
        local_repo = SILERO_CACHE_DIR / "snakers4_silero-vad_master"
        if local_repo.exists():
            self._model, self._utils = torch.hub.load(
                repo_or_dir=str(local_repo),
                model="silero_vad",
                force_reload=False,
                onnx=False,
                source="local",
                trust_repo=True,
            )
        else:
            self._model, self._utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                onnx=False,
                source="github",
                trust_repo=True,
            )
        (self._get_speech_ts, self._get_speech_ts_adaptive) = (
            self._utils[0], self._utils[1]
        )
        self._cfg = VAD_CONFIG
        log.info("[vad] 加载 Silero VAD 完成（%s）",
                 "本地缓存" if local_repo.exists() else "联网下载")

    def detect(self, audio: AudioLoad) -> list[VadSegment]:
        """输入内存波形，输出语音段（秒）"""
        import numpy as np

        sr = audio.sample_rate
        # Silero VAD 要求输入为 1D numpy array，float32，范围 [-1, 1]
        wav = audio.waveform.cpu().numpy()
        if wav.ndim > 1:
            # 多通道取平均
            wav = wav.mean(axis=0)
        wav = wav.astype(np.float32)

        # 使用 Silero 的 get_speech_timestamps 获取语音时间戳
        # 函数签名: (audio, model, threshold=0.5, sampling_rate=16000, ...)
        # 注意：sr 必须作为 sampling_rate 关键字参数传入，不能作为位置参数（第三个位置是 threshold）
        # 注意（v2.18）：返回的 start/end 是【采样点】而非毫秒（return_seconds=False 默认）。
        #   16kHz 下 1 秒 = 16000 采样点，必须除以 sampling_rate 才是秒。
        #   旧代码误除以 1000 当毫秒，导致 6 秒音频检测出 0~100 秒的语音段，
        #   使 VAD 与 Diarization 段永远交集失败 → "无有效语音段"。
        # 返回 [(start_sample, end_sample), ...]
        speech_segments = self._get_speech_ts(
            wav,
            self._model,
            threshold=self._cfg.get("threshold", 0.5),
            sampling_rate=sr,
            min_speech_duration_ms=int(self._cfg.get("min_speech_len_s", 0.25) * 1000),
            min_silence_duration_ms=100,
            speech_pad_ms=int(self._cfg.get("speech_pad_ms", 300)),
        )

        out: list[VadSegment] = []
        for seg in speech_segments:
            start_s = seg["start"] / sr  # 采样点 → 秒（v2.18 修复：除以采样率而非 1000）
            end_s = seg["end"] / sr
            out.append(VadSegment(
                start_offset_s=start_s,
                end_offset_s=end_s,
                score=1.0,
            ))

        log.info("[vad] 检测到 %d 个语音段", len(out))
        return out

    def run(self, audio: AudioLoad) -> list[VadSegment]:
        """向后兼容"""
        return self.detect(audio)

    def unload(self):
        try:
            del self._model
            del self._utils
        except Exception:
            pass