"""
PyAnnote Diarization 3.1 封装 — PRD FR-003
- 输入完整的 {"waveform":(C,T), "sample_rate":int} 字典（时间戳以"原始音频起点"为基准）
- 输出：speaker 匿名标签 + start/end_offset (秒, 相对音频起点)
- 不做"识别是谁"，"识别是谁"交给 voiceprint.py (FR-003-VID)

安全机制（v2.10+）：
- 子进程隔离：pipeline 推理在独立子进程中运行，OOM 不会拖垮主进程
- 超时保护：根据音频时长动态计算超时上限（基准 600s + 每 10 分钟音频 300s）
- 子进程崩溃检测：通过退出码和队列异常感知子进程 OOM / 段错误
"""
from __future__ import annotations

import logging
import multiprocessing
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
import torch
from pyannote.audio import Pipeline

from config.settings import DIARIZATION_CONFIG, MODELS_DIR
from src.utils.audio_utils import AudioLoad, build_speech_concatenation, load_audio

log = logging.getLogger("asr-diarization")


def _write_temp_wav(audio: AudioLoad) -> Path:
    """把内存中的（拼接）音频写为临时 wav 文件，供子进程隔离模式按路径重新加载。"""
    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="asr_concat_")
    os.close(fd)
    try:
        sf.write(tmp, audio.waveform.squeeze(0).numpy(), audio.sample_rate)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise
    return Path(tmp)


def _load_pipeline(hf_token: str):
    """加载 PyAnnote pipeline：优先本地缓存（HF_HUB_OFFLINE=1 离线模式），
    本地缺失时才允许联网下载。避免每次联网检查导致网络波动时卡住。
    注意：pyannote 4.x 的 Pipeline.from_pretrained() 不支持 local_files_only 参数，
    故使用 huggingface_hub 的离线模式环境变量实现同等效果。"""
    repo = DIARIZATION_CONFIG["model_repo"]
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        return Pipeline.from_pretrained(repo, token=hf_token)
    except Exception as e:
        log.warning("[diarization] 本地缓存加载失败（%s），尝试联网加载", e)
        os.environ.pop("HF_HUB_OFFLINE", None)
        return Pipeline.from_pretrained(repo, token=hf_token)


def _diarize_worker(audio_path: str, hf_token: str, num_speakers: int | None,
                    min_seg: float, merge_gap: float,
                    result_queue: multiprocessing.Queue):
    """子进程入口：独立加载模型并执行说话人分离。
    通过文件路径而非 tensor 传递音频，避免大 tensor 序列化开销。
    结果通过 multiprocessing.Queue 传回主进程。"""
    try:
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        pipeline = _load_pipeline(hf_token)
        if pipeline is None:
            result_queue.put(("error", f"PyAnnote pipeline 加载失败：{DIARIZATION_CONFIG['model_repo']}"))
            return

        # 在子进程中加载音频
        from src.utils.audio_utils import load_audio
        audio = load_audio(Path(audio_path))
        input_dict = {"waveform": audio.waveform, "sample_rate": audio.sample_rate}
        kwargs = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = int(num_speakers)

        anno = pipeline(input_dict, **kwargs)
        # pyannote 4.x 返回 DiarizeOutput 包装对象，需取其 .speaker_diarization 才是 Annotation
        # （3.x 直接返回 Annotation 对象）。TDD §3.2 记录过该 API 差异。
        if hasattr(anno, "speaker_diarization"):
            anno = anno.speaker_diarization

        # 后处理：合并短段、排序
        segments: list[tuple[str, float, float]] = []
        last: dict[str, tuple[float, float]] = {}
        for segment, _, speaker in anno.itertracks(yield_label=True):
            s, e = float(segment.start), float(segment.end)
            if (e - s) < min_seg:
                continue
            prev = last.get(speaker)
            if prev and (s - prev[1]) < merge_gap:
                last[speaker] = (prev[0], max(prev[1], e))
            else:
                if prev:
                    segments.append((speaker, prev[0], prev[1]))
                last[speaker] = (s, e)
        for sp, (s, e) in last.items():
            segments.append((sp, s, e))
        segments.sort(key=lambda x: x[1])

        result_queue.put(("success", segments))
    except Exception as e:
        result_queue.put(("error", str(e)))


@dataclass
class DiarizationSegment:
    speaker_label: str   # 匿名：SPEAKER_00, SPEAKER_01, ...
    start_offset_s: float
    end_offset_s: float


class Diarizer:
    def __init__(self, hf_token: str):
        self.hf_token = hf_token
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        # 优先本地缓存加载，缺失时才联网下载（避免网络波动卡住加载）
        self.pipeline = _load_pipeline(hf_token)
        if self.pipeline is None:
            raise RuntimeError(f"PyAnnote pipeline 加载失败：{DIARIZATION_CONFIG['model_repo']}。请确认：① HF Token 正确 ② 已在 HuggingFace 上同意 {DIARIZATION_CONFIG['model_repo']} 与 pyannote/segmentation-3.0 的使用条款")
        # 所有时间戳必须"秒, 相对音频起点"，统一口径
        self.cfg = DIARIZATION_CONFIG

    def run(self, audio: AudioLoad, *, num_speakers: int | None = None,
            vad_segments=None) -> list[DiarizationSegment]:
        """运行说话人分离（子进程隔离模式）。
        当音频时长 >= 10 分钟时，自动切换到子进程模式以隔离 OOM 风险；
        短音频仍在主进程内直接运行以降低开销。
        v2.17：不设置自动超时——自动化超时要么误杀正常处理、要么干等，
        改为子进程无限等待 + 崩溃检测（OOM/段错误仍能感知），
        真·挂死由外部监控（每 10 分钟状态检查）发现后人工介入。
        v2.31：vad_segments 非空且 use_vad_concat 开启时，先按 VAD 语音段拼接
        （切除静音，缩短 segmentation 滑窗输入），分离后再把时间戳映射回原始时间轴，
        对调用方完全无感。"""
        if num_speakers is None:
            num_speakers = self.cfg.get("num_speakers")

        # v2.31 VAD 静音切除加速
        map_back = None
        work_audio = audio
        if vad_segments and self.cfg.get("use_vad_concat", True):
            work_audio, map_back = build_speech_concatenation(audio, vad_segments)
            if work_audio is not audio:
                log.info("[diarization] VAD 拼接加速：%.1f 分钟 -> %.1f 分钟（切除静音）",
                         audio.duration_s / 60, work_audio.duration_s / 60)

        audio_duration = work_audio.duration_s

        tmp_wav: Path | None = None
        try:
            # 短音频（< 10 分钟）：主进程直接运行，性能更好
            if audio_duration < 600:
                segments = self._run_in_process(work_audio, num_speakers)
            else:
                # 长音频（>= 10 分钟）：子进程隔离，防 OOM 拖垮主进程
                if work_audio is not audio:
                    # 拼接音频在内存中，无对应文件：写临时 wav 供子进程重新加载
                    tmp_wav = _write_temp_wav(work_audio)
                    work_audio.original_path = tmp_wav
                log.info("[diarization] 音频时长 %.1f 分钟，使用子进程隔离模式（无超时）",
                         audio_duration / 60)
                segments = self._run_in_subprocess(work_audio, num_speakers)

            # 时间戳映射回原始时间轴
            if map_back is not None and work_audio is not audio:
                for seg in segments:
                    seg.start_offset_s = map_back(seg.start_offset_s)
                    seg.end_offset_s = map_back(seg.end_offset_s)
            return segments
        finally:
            if tmp_wav is not None:
                try:
                    tmp_wav.unlink(missing_ok=True)
                except Exception:
                    pass

    def _run_in_process(self, audio: AudioLoad, num_speakers: int | None) -> list[DiarizationSegment]:
        """主进程内直接运行（短音频）"""
        input_dict = {"waveform": audio.waveform, "sample_rate": audio.sample_rate}
        kwargs = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = int(num_speakers)
        anno = self.pipeline(input_dict, **kwargs)
        # pyannote 4.x 返回 DiarizeOutput 包装对象，需取其 .speaker_diarization 才是 Annotation
        if hasattr(anno, "speaker_diarization"):
            anno = anno.speaker_diarization
        return self._parse_annotation(anno)

    def _run_in_subprocess(self, audio: AudioLoad, num_speakers: int | None) -> list[DiarizationSegment]:
        """子进程隔离模式：在独立进程中运行 pipeline，防止 OOM 拖垮主进程。
        v2.17：不设自动超时（无限 join）。子进程异常退出（OOM/段错误）仍能通过退出码感知；
        真·挂死由外部监控发现后人工介入，避免自动化超时误杀正常的长音频处理。"""
        audio_path = str(audio.original_path)
        ctx = multiprocessing.get_context("spawn")
        result_queue: multiprocessing.Queue = ctx.Queue()
        proc = ctx.Process(
            target=_diarize_worker,
            args=(audio_path, self.hf_token, num_speakers,
                  self.cfg["min_segment_s"], self.cfg["merge_gap_s"],
                  result_queue),
            name="diarization-worker",
        )
        proc.start()
        # 无限等待子进程完成（不设超时，避免误杀接近 1 倍实时的长音频）
        proc.join()

        exit_code = proc.exitcode
        if exit_code != 0:
            # 子进程异常退出（OOM / 段错误等）
            if exit_code == -9:
                raise RuntimeError(
                    f"说话人分离进程被系统终止（SIGKILL），很可能是内存不足（OOM）。"
                    f"音频时长 {audio.duration_s / 60:.1f} 分钟。"
                    f"建议：① 关闭其他应用释放内存 ② 将长音频拆分为较短片段处理。"
                )
            elif exit_code == -11:
                raise RuntimeError(
                    f"说话人分离进程段错误（SIGSEGV），可能是 PyAnnote 内部错误。"
                )
            else:
                raise RuntimeError(
                    f"说话人分离子进程异常退出，退出码 {exit_code}"
                )

        # 从队列获取结果
        try:
            status, data = result_queue.get(timeout=5)
        except Exception:
            raise RuntimeError("说话人分离子进程未返回结果（可能崩溃）")

        if status == "error":
            raise RuntimeError(f"说话人分离失败: {data}")

        # data 是 [(speaker, start, end), ...] 列表
        out: list[DiarizationSegment] = []
        for speaker, start, end in data:
            out.append(DiarizationSegment(speaker, start, end))
        return out

    def _parse_annotation(self, anno) -> list[DiarizationSegment]:
        """解析 PyAnnote Annotation 对象为 DiarizationSegment 列表"""
        out: list[DiarizationSegment] = []
        min_seg = self.cfg["min_segment_s"]
        merge_gap = self.cfg["merge_gap_s"]
        last: dict[str, tuple[float, float]] = {}
        for segment, _, speaker in anno.itertracks(yield_label=True):
            s, e = float(segment.start), float(segment.end)
            if (e - s) < min_seg:
                continue
            prev = last.get(speaker)
            if prev and (s - prev[1]) < merge_gap:
                last[speaker] = (prev[0], max(prev[1], e))
            else:
                if prev:
                    out.append(DiarizationSegment(speaker, *prev))
                last[speaker] = (s, e)
        for sp, (s, e) in last.items():
            out.append(DiarizationSegment(sp, s, e))
        out.sort(key=lambda x: x.start_offset_s)
        return out

    def unload(self):
        try:
            del self.pipeline
        except Exception:
            pass
