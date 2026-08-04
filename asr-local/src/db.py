"""
SQLite 初始化 + 基础 CRUD
严格对应 PRD §7.1 表结构；绝对时间戳在应用层写入、触发器禁止后续 UPDATE。
"""
from __future__ import annotations

import re
import sqlite3
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from config.settings import DB_PATH

SCHEMA_SQL = """
-- ====== 转录表 ======
CREATE TABLE IF NOT EXISTS transcripts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file             TEXT NOT NULL,
    file_hash               TEXT NOT NULL,
    recording_start_time    TEXT NOT NULL,
    processed_at            TEXT NOT NULL,
    segment_start_offset    REAL NOT NULL,
    segment_end_offset      REAL NOT NULL,
    absolute_start_time     TEXT NOT NULL,
    absolute_end_time       TEXT NOT NULL,
    speaker                 TEXT NOT NULL,
    speaker_match_score     REAL,
    text                    TEXT NOT NULL,
    audio_duration          REAL,
    confidence              REAL,
    language                TEXT DEFAULT 'zh',
    archive_name            TEXT,
    audio_path              TEXT,
    transcript_path         TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ====== 声纹库表 ======
CREATE TABLE IF NOT EXISTS voiceprints (
    person_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    person_name             TEXT NOT NULL UNIQUE,
    is_owner                INTEGER DEFAULT 0,
    embedding               BLOB NOT NULL,
    sample_audio_path       TEXT,
    enrolled_at             TEXT NOT NULL,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_source         ON transcripts(source_file);
CREATE INDEX IF NOT EXISTS idx_file_hash      ON transcripts(file_hash);
CREATE INDEX IF NOT EXISTS idx_speaker        ON transcripts(speaker);
CREATE INDEX IF NOT EXISTS idx_recording_time ON transcripts(recording_start_time);
CREATE INDEX IF NOT EXISTS idx_absolute_start ON transcripts(absolute_start_time);
CREATE INDEX IF NOT EXISTS idx_time_range     ON transcripts(absolute_start_time, absolute_end_time);

-- FTS5 全文搜索
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
    text,
    content='transcripts',
    content_rowid='id'
);

-- ====== 声纹簇表（PRD FR-003-CLUSTER：unknown_0001 全局递增 + 标注学习） ======
-- 每个匿名说话人簇一行：持久化聚合声纹向量，跨文件复用编号。
-- assigned_name 为 NULL 时是纯 unknown；被用户标注后写入姓名并持续学习。
CREATE TABLE IF NOT EXISTS speaker_clusters (
    cluster_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    label           TEXT NOT NULL UNIQUE,        -- unknown_0001 / unknown_0002 … 全局递增不复用
    embedding       BLOB NOT NULL,               -- 该簇的聚合声纹向量（float32）
    assigned_name   TEXT,                        -- 用户标注的姓名（关联 persons.person_name）
    skip_label      INTEGER DEFAULT 0,           -- v2.43：1 = 不标注（保持原编号，不参与标注流程）
    sample_count    INTEGER DEFAULT 1,           -- 累积样本数（用于增量平均学习）
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cluster_label ON speaker_clusters(label);
CREATE INDEX IF NOT EXISTS idx_cluster_name  ON speaker_clusters(assigned_name);

-- ====== 人物档案表（PRD FR-010：姓名唯一，与声纹解耦、通过姓名关联） ======
CREATE TABLE IF NOT EXISTS persons (
    person_name     TEXT PRIMARY KEY,            -- 唯一；中文/英文/混杂均可，无空格
    gender          TEXT,                        -- 男 / 女 / 其他
    birth_year      INTEGER,                     -- 出生年
    relation        TEXT,                        -- 与我的关系
    note            TEXT,                        -- 备注
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ====== 保护触发器：绝对时间戳一旦写入不得修改 ======
-- 注意：在应用层计算好随 INSERT 写入；不要 BEFORE INSERT 里算自身表的 UPDATE（会与本保护冲突且 BEFORE INSERT 时新行尚未存在）。
DROP TRIGGER IF EXISTS trg_protect_timestamp;
CREATE TRIGGER trg_protect_timestamp
BEFORE UPDATE OF absolute_start_time, absolute_end_time ON transcripts
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, '绝对时间戳不可手动修改');
END;

-- ====== FTS 同步触发器 ======
DROP TRIGGER IF EXISTS trg_fts_insert;
CREATE TRIGGER trg_fts_insert
AFTER INSERT ON transcripts
BEGIN
    INSERT INTO transcripts_fts(rowid, text) VALUES (NEW.id, NEW.text);
END;

DROP TRIGGER IF EXISTS trg_fts_update;
CREATE TRIGGER trg_fts_update
AFTER UPDATE OF text ON transcripts
BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, text)
    VALUES ('delete', OLD.id, OLD.text);
    INSERT INTO transcripts_fts(rowid, text) VALUES (NEW.id, NEW.text);
END;

DROP TRIGGER IF EXISTS trg_fts_delete;
CREATE TRIGGER trg_fts_delete
AFTER DELETE ON transcripts
BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, text)
    VALUES ('delete', OLD.id, OLD.text);
END;
"""


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    p = Path(db_path or DB_PATH)
    # v2.47 防护：未加载 .env 时 settings 走默认路径（HOME/audio_archive 等），
    # connect/init_db 会在 HOME 下制造残留目录（v2.21 问题复发：曾出现
    # /home/kevin/audio_archive 0 字节空库）。仅在"无显式 db_path + 默认路径 +
    # 未设置 ASR_ARCHIVE"时向 stderr 告警，帮助定位而非静默制造残留。
    if db_path is None and not os.environ.get("ASR_ARCHIVE") \
            and p.parent == Path.home() / "audio_archive":
        print(
            f"[db] 警告：未检测到 ASR_ARCHIVE，正在使用默认路径 {p} —— "
            "请先 source .env（run.sh / systemd 已自动加载），否则会制造 "
            "~/audio_archive 残留目录", file=sys.stderr)
    ensure_parent_dir(p)
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        # v2.43 迁移：老库 speaker_clusters 补充 skip_label（不标注）列
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(speaker_clusters)")}
        if "skip_label" not in cols:
            conn.execute(
                "ALTER TABLE speaker_clusters ADD COLUMN skip_label INTEGER DEFAULT 0")


# ====== 去重检查 ======

def exists_file_hash(file_hash: str, db_path: Path | None = None) -> bool:
    with connect(db_path) as conn:
        cur = conn.execute("SELECT 1 FROM transcripts WHERE file_hash=? LIMIT 1", (file_hash,))
        return cur.fetchone() is not None


# ====== 声纹库操作 ======

def list_voiceprints(db_path: Path | None = None) -> list[sqlite3.Row]:
    with connect(db_path) as conn:
        return list(conn.execute(
            "SELECT person_id, person_name, is_owner, length(embedding) AS emb_bytes, "
            "sample_audio_path, enrolled_at FROM voiceprints ORDER BY person_id"))


def insert_voiceprint(
    person_name: str,
    embedding_bytes: bytes,
    *,
    is_owner: bool = False,
    sample_audio_path: str | None = None,
    enrolled_at: str | None = None,
    db_path: Path | None = None,
) -> int:
    enrolled_at = enrolled_at or datetime.now().astimezone().isoformat()
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO voiceprints(person_name, is_owner, embedding, sample_audio_path, enrolled_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (person_name, int(bool(is_owner)), embedding_bytes, sample_audio_path, enrolled_at),
        )
        return int(cur.lastrowid)


def load_all_voiceprints(db_path: Path | None = None) -> list[tuple[int, str, np.ndarray]]:  # type: ignore[name-defined]
    """返回 [(person_id, person_name, embedding_vector), ...]"""
    import numpy as np
    out = []
    with connect(db_path) as conn:
        for row in conn.execute("SELECT person_id, person_name, embedding FROM voiceprints"):
            vec = np.frombuffer(bytes(row["embedding"]), dtype=np.float32)
            out.append((int(row["person_id"]), str(row["person_name"]), vec))
    return out


# ====== 声纹簇操作（PRD FR-003-CLUSTER） ======

def next_unknown_label(db_path: Path | None = None) -> str:
    """下一个全局 unknown 编号：MAX(已用编号)+1，四位，不复用（即使某编号已被指派姓名）。
    v2.18 健壮性改进：基于全部行的最大编号而非"最后插入行"——若删除过编号较大的簇，
    ORDER BY cluster_id DESC 只取最后一行会算出已被占用的编号，INSERT 时 UNIQUE 冲突。"""
    with connect(db_path) as conn:
        rows = conn.execute("SELECT label FROM speaker_clusters").fetchall()
        max_n = 0
        for r in rows:
            try:
                n = int(str(r["label"]).rsplit("_", 1)[1])
                if n > max_n:
                    max_n = n
            except Exception:
                continue
        return f"unknown_{max_n + 1:04d}"


def load_all_clusters(db_path: Path | None = None) -> list[dict]:
    """返回 [{cluster_id, label, assigned_name, skip_label, sample_count, vec}, ...]"""
    import numpy as np
    out = []
    with connect(db_path) as conn:
        for row in conn.execute(
                "SELECT cluster_id, label, assigned_name, skip_label, sample_count, embedding "
                "FROM speaker_clusters ORDER BY cluster_id"):
            out.append({
                "cluster_id": int(row["cluster_id"]),
                "label": str(row["label"]),
                "assigned_name": row["assigned_name"],
                "skip_label": int(row["skip_label"] or 0),
                "sample_count": int(row["sample_count"]),
                "vec": np.frombuffer(bytes(row["embedding"]), dtype=np.float32),
            })
    return out


def insert_cluster(label: str, embedding_bytes: bytes,
                   db_path: Path | None = None) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO speaker_clusters(label, embedding) VALUES (?, ?)",
            (label, embedding_bytes))
        return int(cur.lastrowid)


def update_cluster_embedding(cluster_id: int, embedding_bytes: bytes,
                             sample_count: int, db_path: Path | None = None) -> None:
    """增量学习：更新簇的聚合向量与样本数"""
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE speaker_clusters SET embedding=?, sample_count=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE cluster_id=?",
            (embedding_bytes, sample_count, cluster_id))


def assign_cluster_name(cluster_id: int, person_name: str,
                        db_path: Path | None = None) -> None:
    """用户标注：把某个 unknown 簇指派为某人"""
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE speaker_clusters SET assigned_name=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE cluster_id=?",
            (person_name, cluster_id))


def unassign_cluster_name(cluster_id: int, db_path: Path | None = None) -> None:
    """标注校准（v2.20）：把已标注的簇改回未知——清空 assigned_name，编号（label）保留。
    该编号是簇的稳定身份，改回后仍以此编号在 WebUI 未标注列表中出现，可随时再标注。"""
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE speaker_clusters SET assigned_name=NULL, "
            "updated_at=CURRENT_TIMESTAMP WHERE cluster_id=?",
            (cluster_id,))


def set_cluster_skip(cluster_id: int, skip: bool,
                     db_path: Path | None = None) -> None:
    """不标注标记（v2.43）：skip=True 设为「不标注」——保持原编号 unknown_XXXX，
    仅从「标注为某人」流程中隐藏；skip=False 恢复可标注。
    不改 label/embedding，不触发 transcripts 回填（编号未变，无需回填）。"""
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE speaker_clusters SET skip_label=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE cluster_id=?",
            (1 if skip else 0, cluster_id))


def get_cluster_label(cluster_id: int, db_path: Path | None = None) -> str | None:
    """获取声纹簇的 label（如 unknown_0001），用于后续更新 transcripts 表"""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT label FROM speaker_clusters WHERE cluster_id=?",
            (cluster_id,)).fetchone()
        return str(row["label"]) if row else None


def update_transcripts_speaker(old_label: str, new_name: str,
                               db_path: Path | None = None) -> int:
    """声纹标注后，把 transcripts 表中所有 speaker=old_label 的记录更新为 new_name。
    使用 COLLATE NOCASE 实现大小写不敏感匹配，同时兼容旧格式标签（如 UNKNOWN_00）。
    返回更新行数。注意：FTS 索引不涉及 speaker 字段，无需重建。"""
    total = 0
    with connect(db_path) as conn:
        # 主标签：大小写不敏感匹配
        cur = conn.execute(
            "UPDATE transcripts SET speaker=? WHERE speaker=? COLLATE NOCASE",
            (new_name, old_label))
        total += cur.rowcount

        # 旧版兼容：如果 old_label 是 unknown_XXXX 格式，也搜索旧版 UNKNOWN_XX 格式
        # 旧版编号规则：unknown_0001 → UNKNOWN_00, unknown_0002 → UNKNOWN_01, 以此类推
        m = re.match(r"unknown_(\d{4})", old_label, re.IGNORECASE)
        if m:
            old_num = int(m.group(1))
            if old_num >= 1:
                old_legacy_label = f"UNKNOWN_{old_num - 1:02d}"
                cur = conn.execute(
                    "UPDATE transcripts SET speaker=? WHERE speaker=? COLLATE NOCASE",
                    (new_name, old_legacy_label))
                total += cur.rowcount
                # 也搜索旧版小写两位格式
                old_legacy_lower = f"unknown_{old_num - 1:02d}"
                if old_legacy_lower != old_label:
                    cur = conn.execute(
                        "UPDATE transcripts SET speaker=? WHERE speaker=? COLLATE NOCASE",
                        (new_name, old_legacy_lower))
                    total += cur.rowcount

    return total


def list_clusters_view(db_path: Path | None = None) -> list[dict]:
    """Web 展示用：不含向量，含档案关联"""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT c.cluster_id, c.label, c.assigned_name, c.skip_label, c.sample_count, "
            "c.created_at, p.gender, p.relation "
            "FROM speaker_clusters c "
            "LEFT JOIN persons p ON p.person_name = c.assigned_name "
            "ORDER BY c.cluster_id").fetchall()
        return [dict(r) for r in rows]


# ====== 人物档案操作（PRD FR-010） ======

def upsert_person(person_name: str, gender: str | None = None,
                  birth_year: int | None = None, relation: str | None = None,
                  note: str | None = None, db_path: Path | None = None) -> None:
    """姓名唯一；存在则更新资料，不存在则新建"""
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO persons(person_name, gender, birth_year, relation, note) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(person_name) DO UPDATE SET "
            "gender=excluded.gender, birth_year=excluded.birth_year, "
            "relation=excluded.relation, note=excluded.note, "
            "updated_at=CURRENT_TIMESTAMP",
            (person_name, gender, birth_year, relation, note))


def list_persons(db_path: Path | None = None) -> list[dict]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT p.person_name, p.gender, p.birth_year, p.relation, p.note, p.created_at, "
            "CASE WHEN EXISTS (SELECT 1 FROM speaker_clusters sc WHERE sc.assigned_name = p.person_name) "
            "THEN 1 ELSE 0 END AS has_voiceprint "
            "FROM persons p ORDER BY p.created_at").fetchall()
        return [dict(r) for r in rows]


def get_person(person_name: str, db_path: Path | None = None) -> dict | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT person_name, gender, birth_year, relation, note "
            "FROM persons WHERE person_name=?", (person_name,)).fetchone()
        return dict(row) if row else None


# ====== Transcript 批量插入 ======

@dataclass  # noqa: F821
class SegmentRow:
    """每段话的一行记录，由 pipeline 在应用层算好 absolute 时间戳后传入"""
    source_file: str
    file_hash: str
    recording_start_time: str   # ISO 8601
    processed_at: str
    segment_start_offset: float
    segment_end_offset: float
    absolute_start_time: str
    absolute_end_time: str
    speaker: str
    speaker_match_score: float | None
    text: str
    audio_duration: float | None = None
    confidence: float | None = None
    language: str = "zh"
    archive_name: str | None = None
    audio_path: str | None = None
    transcript_path: str | None = None


def insert_segments(segments: Iterable[SegmentRow], db_path: Path | None = None) -> int:
    rows = list(segments)
    if not rows:
        return 0
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO transcripts("
            "source_file, file_hash, recording_start_time, processed_at,"
            "segment_start_offset, segment_end_offset,"
            "absolute_start_time, absolute_end_time,"
            "speaker, speaker_match_score, text,"
            "audio_duration, confidence, language,"
            "archive_name, audio_path, transcript_path"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    r.source_file, r.file_hash, r.recording_start_time, r.processed_at,
                    r.segment_start_offset, r.segment_end_offset,
                    r.absolute_start_time, r.absolute_end_time,
                    r.speaker, r.speaker_match_score, r.text,
                    r.audio_duration, r.confidence, r.language,
                    r.archive_name, r.audio_path, r.transcript_path,
                )
                for r in rows
            ],
        )
    return len(rows)
