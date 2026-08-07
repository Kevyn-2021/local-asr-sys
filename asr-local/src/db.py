"""
SQLite 初始化 + 基础 CRUD
严格对应 PRD §7.1 表结构；绝对时间戳在应用层写入、触发器禁止后续 UPDATE。
"""
from __future__ import annotations

import re
import sqlite3
import os
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
    processing_started_at   TEXT,                    -- v2.67：该音频开始处理时间（成功文件写入）
    processing_completed_at TEXT,                    -- v2.67：该音频处理完成时间（成功文件写入）
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
    reset_on_next_match INTEGER DEFAULT 0,       -- v2.76：1 = 改标后待重置，下次命中用新样本替换向量
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
    # v2.83 防护：未加载 .env 时 settings 走默认路径（HOME/audio_archive 等），
    # 若照常创建会在 HOME 下制造 0 字节空库残留（v2.47 曾只告警，2026-08-07 实测
    # 复发：临时查询未 source .env → /home/kevin/audio_archive/transcripts.db）。
    # 漏加载 .env 的进程不应读写任何数据库，现改为直接报错拒绝（硬失败）。
    if db_path is None and not os.environ.get("ASR_ARCHIVE"):
        raise RuntimeError(
            "[db] 未检测到 ASR_ARCHIVE（未加载 .env），拒绝打开默认路径数据库 "
            f"{p}。请通过 run.sh / systemd 启动（会自动 source .env），或先手动 "
            "source <工程根>/.env；确需绕过时可显式传 db_path。")
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
        # v2.76 迁移：老库 speaker_clusters 补充改标重置标记列
        if "reset_on_next_match" not in cols:
            conn.execute(
                "ALTER TABLE speaker_clusters ADD COLUMN reset_on_next_match INTEGER DEFAULT 0")
        # v2.67 迁移：老库 transcripts 补充处理起止时间列（pipeline 新写入；旧记录为 NULL，
        # WebUI 展示时完成时间回退 processed_at、开始时间显示 —）
        tcols = {r["name"] for r in conn.execute("PRAGMA table_info(transcripts)")}
        if "processing_started_at" not in tcols:
            conn.execute("ALTER TABLE transcripts ADD COLUMN processing_started_at TEXT")
        if "processing_completed_at" not in tcols:
            conn.execute("ALTER TABLE transcripts ADD COLUMN processing_completed_at TEXT")


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
                "SELECT cluster_id, label, assigned_name, skip_label, sample_count, "
                "reset_on_next_match, embedding "
                "FROM speaker_clusters ORDER BY cluster_id"):
            out.append({
                "cluster_id": int(row["cluster_id"]),
                "label": str(row["label"]),
                "assigned_name": row["assigned_name"],
                "skip_label": int(row["skip_label"] or 0),
                "sample_count": int(row["sample_count"]),
                "reset_on_next_match": int(row["reset_on_next_match"] or 0),
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


def reset_cluster_vector(cluster_id: int, embedding_bytes: bytes,
                         db_path: Path | None = None) -> None:
    """v2.76 改标重置生效：用当前匹配样本替换簇向量（sample_count 回 1）并清除重置标记"""
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE speaker_clusters SET embedding=?, sample_count=1, "
            "reset_on_next_match=0, updated_at=CURRENT_TIMESTAMP WHERE cluster_id=?",
            (embedding_bytes, cluster_id))


def assign_cluster_name(cluster_id: int, person_name: str,
                        db_path: Path | None = None) -> None:
    """用户标注/改标：把某个 unknown 簇指派为某人。

    v2.76 改标即重置：当"原已标注且改标为他人"、或"给已累积过样本（sample_count>1，
    曾命名过或旧版学习过）的簇指派姓名"时，置 reset_on_next_match=1——下次处理命中
    时用新样本替换向量重新播种，避免旧姓名期间累积的错误样本持续污染、越用越不准；
    纯新建簇（sample_count=1）首次标注不重置，保留该文件作为身份依据。"""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT assigned_name, sample_count FROM speaker_clusters WHERE cluster_id=?",
            (cluster_id,)).fetchone()
        old = row["assigned_name"] if row else None
        reset = 0
        if row is not None and person_name != old:
            if old or (row["sample_count"] or 0) > 1:
                reset = 1
        conn.execute(
            "UPDATE speaker_clusters SET assigned_name=?, reset_on_next_match=?, "
            "updated_at=CURRENT_TIMESTAMP WHERE cluster_id=?",
            (person_name, reset, cluster_id))


def unassign_cluster_name(cluster_id: int, db_path: Path | None = None) -> None:
    """标注校准（v2.20）：把已标注的簇改回未知——清空 assigned_name，编号（label）保留。
    该编号是簇的稳定身份，改回后仍以此编号在 WebUI 未标注列表中出现，可随时再标注。
    v2.82：同时清除 reset_on_next_match（休眠的待重置标记）——未标注簇不参与学习/重置，
    看板「待重置簇」只反映已标注且待重置的簇；再次标注时 assign_cluster_name 会按
    sample_count 规则重新置位，行为不受影响。"""
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE speaker_clusters SET assigned_name=NULL, reset_on_next_match=0, "
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
            "c.reset_on_next_match, c.created_at, p.gender, p.relation "
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
    processing_started_at: str | None = None   # v2.67：文件开始处理时间
    processing_completed_at: str | None = None # v2.67：文件处理完成时间


def insert_segments(segments: Iterable[SegmentRow], db_path: Path | None = None) -> int:
    rows = list(segments)
    if not rows:
        return 0
    with connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO transcripts("
            "source_file, file_hash, recording_start_time, processed_at,"
            "processing_started_at, processing_completed_at,"
            "segment_start_offset, segment_end_offset,"
            "absolute_start_time, absolute_end_time,"
            "speaker, speaker_match_score, text,"
            "audio_duration, confidence, language,"
            "archive_name, audio_path, transcript_path"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    r.source_file, r.file_hash, r.recording_start_time, r.processed_at,
                    r.processing_started_at, r.processing_completed_at,
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
