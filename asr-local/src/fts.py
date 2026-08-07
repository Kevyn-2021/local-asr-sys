"""
中文全文搜索 — jieba 分词 + FTS5（修复 PRD §4.1 FR-008-S 中文搜不出）

背景：原 transcripts_fts 用 FTS5 默认 unicode61 分词器，只按空格/标点切分，
不识别中文词（如"停顿"不会成为独立 token），导致中文关键词 MATCH 不到。

方案：新建独立 FTS 表 transcripts_fts2，内容由应用层用 jieba 分词后以空格连接写入；
搜索时同样用 jieba 分词查询词。与原 content=transcripts 的触发器表解耦，独立维护。

- init_fts(): 建表（若不存在）+ 全量重建（若为空）
- sync_segments(rows): insert_segments 后同步写入新行
- search_ids(keyword): 返回匹配的 transcript id 列表
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("asr-fts")

FTS_TABLE = "transcripts_fts2"

_DDL = f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {FTS_TABLE} USING fts5(
    seg_text,
    tokenize='unicode61'
);
"""


def _jieba():
    import jieba
    jieba.setLogLevel(logging.WARNING)
    return jieba


def tokenize(text: str) -> str:
    """jieba 分词后以空格连接；剔除纯空白 token"""
    if not text:
        return ""
    words = _jieba().lcut(text)
    return " ".join(w for w in words if w.strip())


def _query_tokens(keyword: str) -> list[str]:
    """查询词分词；剔除空白与 FTS 特殊字符"""
    words = _jieba().lcut(keyword)
    out = []
    for w in words:
        w = w.strip()
        if not w:
            continue
        # 去除会触发 FTS5 语法错误的字符
        w = re.sub(r'["()*:^]', "", w)
        if w:
            out.append(w)
    return out


def init_fts(db_path: Path | None = None) -> int:
    """建表；若为空则从 transcripts 全量重建。返回索引行数"""
    from .db import connect
    with connect(db_path) as conn:
        conn.execute(_DDL)
        n = conn.execute(f"SELECT COUNT(*) AS c FROM {FTS_TABLE}").fetchone()["c"]
        if n:
            return n
        rows = conn.execute("SELECT id, text FROM transcripts ORDER BY id").fetchall()
        data = [(r["id"], tokenize(r["text"])) for r in rows]
        conn.executemany(
            f"INSERT INTO {FTS_TABLE}(rowid, seg_text) VALUES (?, ?)", data)
        log.info("FTS 中文索引重建完成：%d 行", len(data))
        return len(data)


def sync_segments(segments, db_path: Path | None = None) -> None:
    """insert_segments 后调用：把新插入的行同步进 FTS（取本文件最新插入的 id 段）

    v2.74 修复：原实现按 (file_hash, text) 反查 id 取最新一条，同一文件内出现
    两条相同文本时两个片段会命中同一 id，第二次 INSERT 触发 FTS5 rowid 唯一约束
    → "constraint failed"（仅告警，转录不受影响但索引缺行）。改为按 file_hash
    取最新 len(rows) 个 id 与本次插入行一一对应，并用 INSERT OR REPLACE 幂等写入。
    """
    from .db import connect
    rows = list(segments)
    if not rows:
        return
    with connect(db_path) as conn:
        conn.execute(_DDL)
        by_file: dict[str, list] = {}
        for r in rows:
            by_file.setdefault(getattr(r, "file_hash", None), []).append(r)
        data: list[tuple[int, str]] = []
        for fid, rlist in by_file.items():
            if not fid:
                continue
            # 本次批处理 = 该 file_hash 最新插入的 len(rlist) 行（单进程顺序插入）
            ids = [row["id"] for row in conn.execute(
                "SELECT id FROM transcripts WHERE file_hash=? ORDER BY id DESC LIMIT ?",
                (fid, len(rlist)))]
            ids.reverse()  # DESC 反转回插入顺序，与 rows 一一对应
            for rid, r in zip(ids, rlist):
                txt = getattr(r, "text", "")
                if rid is not None and txt:
                    data.append((rid, tokenize(txt)))
        if data:
            conn.executemany(
                f"INSERT OR REPLACE INTO {FTS_TABLE}(rowid, seg_text) VALUES (?, ?)",
                data)


def search_ids(keyword: str, db_path: Path | None = None) -> list[int]:
    """中文关键词搜索：jieba 分词后对 FTS 表 MATCH，返回 transcript id 列表"""
    from .db import connect
    tokens = _query_tokens(keyword)
    if not tokens:
        return []
    # 多 token 用 AND 连接，全部命中才返回
    match_q = " AND ".join(f'"{t}"' for t in tokens)
    with connect(db_path) as conn:
        conn.execute(_DDL)
        try:
            rows = conn.execute(
                f"SELECT rowid FROM {FTS_TABLE} WHERE {FTS_TABLE} MATCH ?",
                (match_q,)).fetchall()
            return [int(r["rowid"]) for r in rows]
        except Exception as e:
            log.warning("FTS 查询失败（%s），退回 LIKE: %s", match_q, e)
            like = f"%{keyword}%"
            rows = conn.execute(
                "SELECT id FROM transcripts WHERE text LIKE ?", (like,)).fetchall()
            return [int(r["id"]) for r in rows]
