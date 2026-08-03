"""
Pipeline: 串起 VAD + Diarization + 声纹识别 + ASR + 归档 + 入库
同时严格实现内存编排 (PRD §5.1.1)：阶段跑完后卸载模型再进下一个阶段
"""
from __future__ import annotations

import concurrent.futures
import gc
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from src.archive import _clean_asr_text, archive_audio, move_to_error, write_transcript_backups
from config.settings import (
    DIARIZATION_CONFIG,
    MEMORY_CONFIG,
    SUPPORTED_EXTENSIONS,
    TIME_SOURCE_PRIORITY,
    VOICEPRINT_CONFIG,
)
from src.db import SegmentRow, exists_file_hash, init_db, insert_segments
from src.utils.audio_utils import AudioLoad, load_audio
from src.utils.hash_utils import sha256_file
from src.utils.time_utils import (
    BJT,
    ExtractedTime,
    extract_recording_start_time,
    offset_to_absolute,
)

log = logging.getLogger("asr-pipeline")


class PipelineError(RuntimeError):
    pass


@dataclass
class PipelineResult:
    success: bool
    rows_written: int = 0
    archive_path: Path | None = None
    text_backups: tuple[Path, Path] | None = None
    error_msg: str = ""


class AsrPipeline:
    def __init__(self, *, hf_token: str, memory_stage_unload: bool = MEMORY_CONFIG["stage_unload"]):
        self.hf_token = hf_token
        self.unload = memory_stage_unload
        self.vad = None
        self.diar = None
        self.vp = None
        self.asr = None
        init_db()

    # ---- 带超时的模型加载 ----
    def _load_with_timeout(self, load_fn, name: str, timeout: int = 300):
        """带超时的模型加载包装器"""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(load_fn)
            try:
                future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise PipelineError(f"模型加载超时：{name}（超过 {timeout} 秒）")

    # ---- 资源控制 ----
    def _load_vad(self):
        if self.vad is None:
            def _do():
                from .vad import SileroVad
                self.vad = SileroVad()
                log.info("[pipeline] 加载 Silero VAD")
            self._load_with_timeout(_do, "Silero VAD")

    def _load_diar(self):
        if self.diar is None:
            def _do():
                from .diarization import Diarizer
                self.diar = Diarizer(self.hf_token)
                log.info("[pipeline] 加载 PyAnnote Diarization")
            self._load_with_timeout(_do, "PyAnnote Diarization")

    def _load_vp(self):
        if self.vp is None:
            def _do():
                from .voiceprint import VoiceprintEngine
                self.vp = VoiceprintEngine(self.hf_token)
                log.info("[pipeline] 加载声纹识别引擎")
            self._load_with_timeout(_do, "声纹识别引擎")

    def _load_asr(self):
        if self.asr is None:
            def _do():
                from .asr import QwenAsr
                self.asr = QwenAsr(self.hf_token)
                log.info("[pipeline] 加载 Qwen3-ASR")
            self._load_with_timeout(_do, "Qwen3-ASR")

    def _unload_diar(self):
        if self.unload and self.diar is not None:
            self.diar.unload()
            self.diar = None
            gc.collect()
            log.info("[pipeline] 卸载 Diarization 释放内存")

    def _unload_asr(self):
        if self.unload and self.asr is not None:
            self.asr.unload()
            self.asr = None
            gc.collect()
            log.info("[pipeline] 卸载 ASR 释放内存")

    @staticmethod
    def _report(status_cb: Callable[[str], None] | None, stage: str) -> None:
        if status_cb is not None:
            try:
                status_cb(stage)
            except Exception:
                pass

    # ---- 对外：处理单个文件 ----
    def process_file(self, audio_path: Path, *, time_override: datetime | None = None,
                     num_speakers: int | None = None,
                     confirm_time_cb: Callable[[ExtractedTime], ExtractedTime] | None = None,
                     status_cb: Callable[[str], None] | None = None) -> PipelineResult:
        path = Path(audio_path)
        if not path.exists():
            return PipelineResult(False, error_msg=f"文件不存在: {path}")
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return PipelineResult(False, error_msg=f"非支持格式: {path.suffix}")

        # (1) 内容哈希去重 (FR-001)
        try:
            file_hash = sha256_file(path)
            if exists_file_hash(file_hash):
                move_to_error(path, reason="重复文件：相同 SHA-256 已处理过")
                return PipelineResult(False, error_msg="重复文件，已跳过")
        except Exception as e:
            return PipelineResult(False, error_msg=f"哈希失败: {e}")

        # (2) 加载音频
        self._report(status_cb, "加载音频")
        try:
            audio: AudioLoad = load_audio(path)
        except Exception as e:
            move_to_error(path, reason=f"加载音频失败: {e}")
            return PipelineResult(False, error_msg=f"加载失败: {e}")

        # (3) 提取录音开始时间 (FR-001-TS)
        ext = extract_recording_start_time(
            path,
            manual=time_override.astimezone(BJT) if time_override else None,
            source_priority=list(TIME_SOURCE_PRIORITY),
        )
        if confirm_time_cb is not None:
            ext = confirm_time_cb(ext)
        recording_start = ext.recording_start.astimezone(BJT)
        duration_s = audio.duration_s

        # (4) 加载 VAD + 过滤
        self._report(status_cb, "VAD 语音检测")
        try:
            self._load_vad()
            vad_segs = self.vad.detect(audio)
        except Exception as e:
            move_to_error(path, reason=f"VAD 失败: {e}")
            return PipelineResult(False, error_msg=f"VAD 失败: {e}")

        # (5) Diarization — 运行在完整原始音频，时间戳基准统一
        # 长音频警告：>= 30 分钟的音频在低内存环境下有 OOM 风险
        if duration_s >= 1800:
            log.warning("[pipeline] 音频时长 %.1f 分钟，说话人分离将使用子进程隔离模式以降低 OOM 风险",
                        duration_s / 60)
        self._report(status_cb, "说话人分离")
        try:
            self._load_diar()
            diar = self.diar.run(audio, num_speakers=num_speakers)
        except Exception as e:
            log.error("[pipeline] 说话人分离失败: %s", e)
            move_to_error(path, reason=f"Diarization 失败: {e}")
            return PipelineResult(False, error_msg=f"Diarization 失败: {e}")
        self._unload_diar()

        # (6) 声纹匹配
        self._report(status_cb, "声纹匹配")
        try:
            self._load_vp()
            # 按匿名 speaker 聚合各段，先求统一向量
            by_sp: dict[str, list[tuple[float, float]]] = {}
            for ds in diar:
                by_sp.setdefault(ds.speaker_label, []).append((ds.start_offset_s, ds.end_offset_s))
            sp_match: dict[str, object] = {}
            for sp, ranges in by_sp.items():
                emb = self.vp.aggregate_speaker_embedding(audio, ranges)
                mr = self.vp.match_speaker(emb)
                # 全新说话人：全局递增新建声纹簇（PRD 需求 3，编号跨文件稳定、不复用）
                if mr is not None and mr.person_name == "NEW_CLUSTER" and emb is not None:
                    mr = self.vp.register_new_cluster(emb)
                sp_match[sp] = mr
        except Exception as e:
            # 声纹匹配失败不中止：退化为 UNKNOWN + None 得分
            log.warning("声纹匹配失败：%s，回退 UNKNOWN", e)
            sp_match = {ds.speaker_label: None for ds in diar}

        # (7) ASR — 对每个 diarization 段取音频做识别
        self._report(status_cb, "ASR 转录")
        try:
            self._load_asr()
        except Exception as e:
            move_to_error(path, reason=f"ASR 加载失败: {e}")
            return PipelineResult(False, error_msg=f"ASR 加载失败: {e}")
        # 与 VAD 取交集（VAD 里没有的短静音段跳过）
        # 注意：VadSegment 字段为 start_offset_s / end_offset_s（v2.18 修复误用 .start/.end）
        def vad_has_overlap(so: float, eo: float) -> bool:
            for v in vad_segs:
                if v.end_offset_s < so: continue
                if v.start_offset_s > eo: break
                if v.start_offset_s < eo and v.end_offset_s > so:
                    return True
            return False

        rows: list[SegmentRow] = []
        processed_at = datetime.now().astimezone(BJT).isoformat()
        archive_placeholder: Path | None = None

        for ds in diar:
            if not vad_has_overlap(ds.start_offset_s, ds.end_offset_s):
                continue
            # 裁出原始偏移对应的波形，送 ASR
            try:
                res = self.asr.run_segment(audio, ds.start_offset_s, ds.end_offset_s)
            except Exception as e:
                log.warning("ASR 段失败 [%s-%s]: %s", ds.start_offset_s, ds.end_offset_s, e)
                continue
            if not res.text.strip():
                continue
            # 清洗 ASR 文本：去除特殊 token（如 <|system|>、<|user|>、<|assistant|> 等）
            clean_text = _clean_asr_text(res.text)
            if not clean_text:
                continue
            # 说话人标签（声纹簇已含全局编号/姓名，直接用）
            mr = sp_match.get(ds.speaker_label)
            if mr is None:
                speaker_label = "unknown_0000"
                score = None
            else:
                speaker_label = mr.person_name
                score = mr.score

            abs_start = offset_to_absolute(recording_start, ds.start_offset_s).astimezone(BJT).isoformat()
            abs_end   = offset_to_absolute(recording_start, ds.end_offset_s).astimezone(BJT).isoformat()
            rows.append(SegmentRow(
                source_file=path.name,
                file_hash=file_hash,
                recording_start_time=recording_start.isoformat(),
                processed_at=processed_at,
                segment_start_offset=ds.start_offset_s,
                segment_end_offset=ds.end_offset_s,
                absolute_start_time=abs_start,
                absolute_end_time=abs_end,
                speaker=speaker_label,
                speaker_match_score=score,
                text=clean_text,
                audio_duration=duration_s,
                confidence=res.confidence,
                language=res.language,
                # archive/audio_path/text_backups 占位，归档后回填
                archive_name=None,
                audio_path=None,
                transcript_path=None,
            ))
        self._unload_asr()

        if not rows:
            move_to_error(path, reason="没有识别到有效语音段")
            return PipelineResult(False, error_msg="无有效语音段，已跳过")

        # (8) 归档 + 文本备份
        self._report(status_cb, "归档与入库")
        try:
            archive_p, archive_name = archive_audio(path, recording_start, duration_s)
            archive_placeholder = archive_p
            txt_paths = write_transcript_backups(
                rows, recording_start,
                source_file=path.name, archive_name=archive_name,
                audio_duration_s=duration_s,
            )
            organic_p = str(txt_paths[0])
        except Exception as e:
            return PipelineResult(False, error_msg=f"归档失败: {e}")

        # 回填
        for r in rows:
            r.archive_name = archive_name
            r.audio_path = str(archive_p)
            r.transcript_path = organic_p

        # (9) 入库
        try:
            n = insert_segments(rows)
            try:
                from .fts import sync_segments
                sync_segments(rows)  # 同步中文分词 FTS 索引（失败不阻断）
            except Exception as fe:
                log.warning("FTS 索引同步失败（不影响转录）: %s", fe)
        except Exception as e:
            return PipelineResult(False, error_msg=f"入库失败: {e}")

        return PipelineResult(True, rows_written=n, archive_path=archive_p, text_backups=txt_paths)
