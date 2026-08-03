"""
Qwen3-ASR-0.6B-hf 封装 — PRD FR-004
- 纯 CPU 推理，关闭 flash attn
- Transformers >=5.13 才能使用（实测 5.14.1 OK）
- 输入一段 (1,T) 的 torch tensor waveform，输出 text + 置信度
- 模型类是 AutoModelForMultimodalLM（Qwen3-ASR 是多模态模型，非 SpeechSeq2Seq）
- 输入必须通过 processor.apply_transcription_request() 构建（v2.18 重构）
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import torch
from transformers import AutoModelForMultimodalLM, AutoProcessor

from config.settings import ASR_CONFIG, MODELS_DIR
from src.utils.audio_utils import AudioLoad, segment_waveform

log = logging.getLogger("asr-asr")


@dataclass
class AsrResult:
    text: str
    confidence: float | None
    language: str


class QwenAsr:
    def __init__(self, hf_token: str):
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        repo = ASR_CONFIG["model_repo"]
        # v2.18 离线加载修复：Qwen3-ASR 模型以"自定义解压目录"存放于
        # MODELS_DIR/Qwen3-ASR-0.6B-hf/（含 config.json / model.safetensors / tokenizer 等）。
        # 注意 local_files_only=True + cache_dir 只认 HF hub 缓存格式
        # （models--Qwen--Qwen3-ASR-0.6B-hf/snapshots/...），匹配不到自定义目录时会
        # 回退联网下载，在无外网环境下直接失败（Cannot send a request / Network unreachable）。
        # 因此加载顺序：自定义目录 → hub 缓存(local_files_only) → 联网兜底。
        local_dir = MODELS_DIR / "Qwen3-ASR-0.6B-hf"
        if local_dir.exists():
            log.info("[asr] 使用本地模型目录 %s（完全离线）", local_dir)
            local_kwargs = {"token": hf_token}
            self.processor = AutoProcessor.from_pretrained(str(local_dir), **local_kwargs)
            self.model = AutoModelForMultimodalLM.from_pretrained(
                str(local_dir), torch_dtype=torch.float32,
                low_cpu_mem_usage=True, **local_kwargs,
            )
        else:
            # 兜底：hub 缓存优先，缺失时联网下载（依赖 HF_TOKEN）
            offline_kwargs = {"token": hf_token, "cache_dir": str(MODELS_DIR), "local_files_only": True}
            online_kwargs = {"token": hf_token, "cache_dir": str(MODELS_DIR)}
            try:
                self.processor = AutoProcessor.from_pretrained(repo, **offline_kwargs)
                self.model = AutoModelForMultimodalLM.from_pretrained(
                    repo, torch_dtype=torch.float32,
                    low_cpu_mem_usage=True, **offline_kwargs,
                )
            except Exception:
                log.warning("[asr] 本地缓存加载失败，尝试联网加载")
                self.processor = AutoProcessor.from_pretrained(repo, **online_kwargs)
                self.model = AutoModelForMultimodalLM.from_pretrained(
                    repo, torch_dtype=torch.float32,
                    low_cpu_mem_usage=True, **online_kwargs,
                )
        self.model.eval()
        self.cfg = ASR_CONFIG

    def run_segment(self, audio: AudioLoad, start: float, end: float) -> AsrResult:
        """对音频的一个片段做 ASR 转录。
        v2.18 重构：改用官方推荐的 apply_transcription_request() 入口——
        此前手动拼 input_features 的方式缺 input_ids 文本侧输入，
        报 "Audio features and audio tokens do not match"。
        返回 AsrResult(text, confidence, language)。"""
        wav = segment_waveform(audio, start, end)
        sr = audio.sample_rate
        with torch.inference_mode():
            # 官方推荐入口：自动处理 chat-template 格式化（语言 auto-detect 或强制指定）
            # sampling_rate 需放进 processor_kwargs（v2.18 修掉无谓 warning）
            inputs = self.processor.apply_transcription_request(
                audio=wav.squeeze(0).numpy(),
                language=self.cfg.get("language") or None,
                processor_kwargs={"sampling_rate": sr},
            )
            inputs = {k: v.to(self.model.device) for k, v in inputs.items()}

            generated = self.model.generate(**inputs, max_new_tokens=512)
            generated_ids = generated[:, inputs["input_ids"].shape[1]:]

            try:
                # 官方解析：仅提取纯转录文本（去掉 language 标签与 <asr_text> 标记）
                text = self.processor.decode(
                    generated_ids, return_format="transcription_only"
                )[0]
            except Exception:
                # 兜底：raw 解码后清洗
                raw = self.processor.decode(generated_ids)[0]
                text = raw.replace("<asr_text>", "").replace("</asr_text>", "").strip()
                # 去掉 language 前缀（如 "language Chinese"）
                text = re.sub(r'language\s+[a-zA-Z]+', '', text).strip()

            # 语言：官方 decode 可给出 parsed，此处取配置或 auto 标记
            language = self.cfg.get("language") or "zh"
        conf = 0.8 if text.strip() else 0.0
        return AsrResult(text.strip(), conf, language)

    def unload(self):
        try:
            del self.model, self.processor
        except Exception:
            pass
