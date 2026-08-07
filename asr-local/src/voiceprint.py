"""
声纹库 + 说话人识别 (Speaker Identification) — PRD FR-003-VID
- pyannote/embedding 提 512/192 维向量
- 余弦相似度比对
- 三档阈值 (auto/review/unknown)
- 1 号声纹固定为用户本人
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import torch
from pyannote.audio import Inference, Model

from config.settings import MODELS_DIR, VOICEPRINT_CONFIG
from src.utils.audio_utils import AudioLoad, segment_waveform

log = logging.getLogger("asr-voiceprint")


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na < 1e-8 or nb < 1e-8:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


@dataclass
class MatchResult:
    person_id: int | None
    person_name: str   # 注册人姓名 | UNKNOWN_XX | 疑似:NAME?
    score: float | None
    needs_review: bool


class VoiceprintEngine:
    def __init__(self, hf_token: str):
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        repo = VOICEPRINT_CONFIG["embedding_model"]
        # v2.17 离线优先：先设 HF_HUB_OFFLINE=1 完全离线加载本地缓存，
        # 缺失时才联网下载（HF_TOKEN 已配置）。
        os.environ["HF_HUB_OFFLINE"] = "1"
        try:
            model = Model.from_pretrained(repo, token=hf_token)
        except Exception:
            log.warning("[voiceprint] 本地缓存加载失败，尝试联网加载")
            os.environ.pop("HF_HUB_OFFLINE", None)
            model = Model.from_pretrained(repo, token=hf_token)
        self.inf = Inference(model, window="whole")
        self.cfg = VOICEPRINT_CONFIG
        self._library: list[tuple[int, str, np.ndarray]] = []
        self._clusters: list[dict] = []  # 声纹簇（PRD FR-003-CLUSTER）
        self._refresh()

    # ---- 声纹库管理 ----
    def _refresh(self):
        from src.db import load_all_voiceprints, load_all_clusters
        self._library = load_all_voiceprints()
        self._clusters = load_all_clusters()

    def list_registered(self) -> list[tuple[int, str]]:
        return [(pid, name) for pid, name, _ in self._library]

    def enroll_from_audio(
        self,
        audio: AudioLoad,
        person_name: str,
        *,
        is_owner: bool = False,
        sample_audio_path: str | None = None,
    ) -> int:
        vec = self._extract_single(audio)
        from src.db import insert_voiceprint
        pid = insert_voiceprint(
            person_name=person_name,
            embedding_bytes=vec.tobytes(),
            is_owner=is_owner,
            sample_audio_path=sample_audio_path,
        )
        self._refresh()
        return pid

    # ---- 向量提取 ----
    def _extract_single(self, audio: AudioLoad) -> np.ndarray:
        # Inference 接受 path 或 (T, C, sr) dict
        x = {"waveform": audio.waveform, "sample_rate": audio.sample_rate}
        with torch.inference_mode():
            emb = self.inf(x)
        arr = np.asarray(emb, dtype=np.float32).reshape(-1)
        if arr.size < 64:
            raise RuntimeError("声纹向量异常（太短）")
        return arr

    def _extract_for_segment(self, audio: AudioLoad, start: float, end: float) -> np.ndarray | None:
        if end - start < 1.0:  # 太短不提
            return None
        wav = segment_waveform(audio, start, end)
        seg = AudioLoad(waveform=wav, sample_rate=audio.sample_rate,
                        duration_s=float(wav.shape[1]) / audio.sample_rate,
                        original_path=audio.original_path)
        return self._extract_single(seg)

    # ---- 匹配 ----
    def aggregate_speaker_embedding(
        self,
        audio: AudioLoad,
        segments_of_speaker: Iterable[tuple[float, float]],
    ) -> np.ndarray | None:
        """同一匿名说话人的多段向量求均值（鲁棒性远好于单段）"""
        vs: list[np.ndarray] = []
        for s, e in segments_of_speaker:
            v = self._extract_for_segment(audio, s, e)
            if v is not None:
                vs.append(v)
        if not vs:
            return None
        return np.mean(np.vstack(vs), axis=0).astype(np.float32)

    def match_speaker(self, embedding: np.ndarray | None) -> MatchResult:
        """三级匹配（PRD FR-003-CLUSTER）：
        1. 命名声纹库（voiceprints）：>= auto 直接认出；>= review 记疑似
        2. 已标注簇（assigned_name 非空）：认出该簇对应的人（持续学习成果）
        3. 纯 unknown 簇：认出是之前出现过的某个 unknown，沿用其全局编号
        都不中则返回 None，由 pipeline 全局递增新建簇。

        v2.75 学习策略：只有已标注的簇（自动认出 = 命中已标注簇 / 手工标注）在命中时
        才做增量向量学习；纯 unknown / 已取消标注 / skip_label（不标注）的簇只沿用编号、
        不更新向量——避免低质量音源（低码率致分离/嵌入不准）把多人平均进同一簇造成污染。
        """
        if embedding is None:
            return MatchResult(None, "UNKNOWN", None, False)
        ta = self.cfg["threshold_auto"]
        tr = self.cfg["threshold_review"]

        # 1. 命名声纹库
        best_id, best_name, best_score = None, "", -1.0
        for pid, name, lib in self._library:
            s = _cosine(embedding, lib)
            if s > best_score:
                best_score, best_id, best_name = s, pid, name
        if best_score >= ta:
            return MatchResult(best_id, best_name, best_score, False)
        if best_score >= tr:
            return MatchResult(best_id, f"疑似:{best_name}?", best_score, True)

        # 2./3. 声纹簇（已标注簇优先认人，纯 unknown 簇沿用编号）
        best_c, best_cs = None, -1.0
        for c in self._clusters:
            s = _cosine(embedding, c["vec"])
            if s > best_cs:
                best_cs, best_c = s, c
        if best_c is not None and best_cs >= ta:
            if best_c["assigned_name"]:
                self._learn_into_cluster(best_c, embedding)  # 仅已标注簇持续学习（v2.75）
            if best_c["assigned_name"]:
                return MatchResult(best_c["cluster_id"], best_c["assigned_name"], best_cs, False)
            return MatchResult(best_c["cluster_id"], best_c["label"], best_cs, False)

        # 全新说话人：交给 pipeline 全局递增新建
        return MatchResult(None, "NEW_CLUSTER", best_cs, False)

    def _learn_into_cluster(self, cluster: dict, embedding: np.ndarray) -> None:
        """持续学习：把新向量按增量平均并入簇向量，强化判断（PRD 需求 1/2）"""
        try:
            from src.db import update_cluster_embedding
            n = cluster["sample_count"]
            new_vec = (cluster["vec"] * n + embedding) / (n + 1)
            new_vec = new_vec.astype(np.float32)
            update_cluster_embedding(cluster["cluster_id"], new_vec.tobytes(), n + 1)
            cluster["vec"] = new_vec
            cluster["sample_count"] = n + 1
        except Exception:
            pass  # 学习失败不影响主流程

    def register_new_cluster(self, embedding: np.ndarray) -> MatchResult:
        """pipeline 调用：为全新说话人分配全局递增编号并持久化（PRD 需求 3）"""
        from src.db import next_unknown_label, insert_cluster
        label = next_unknown_label()
        cid = insert_cluster(label, embedding.astype(np.float32).tobytes())
        new_c = {"cluster_id": cid, "label": label, "assigned_name": None, "skip_label": 0,
                 "sample_count": 1, "vec": embedding.astype(np.float32)}
        self._clusters.append(new_c)
        return MatchResult(cid, label, None, False)

    def unload(self):
        try:
            del self.inf
        except Exception:
            pass
