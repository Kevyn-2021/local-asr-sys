"""
归档 + 有机重命名 + 文本备份 — PRD FR-001-AR / FR-006 / FR-007
"""
from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config.settings import (
    ARCHIVE_AUDIO_DIR,
    ARCHIVE_TEXT_DIR,
    INBOX_ERROR_DIR,
    ORGANIC_OUTPUT_FORMAT,
    SUPPORTED_EXTENSIONS,
)
from src.db import SegmentRow
from src.utils.time_utils import BJT, fs_birth_time, organic_filename_for_archive


def _clean_asr_text(text: str) -> str:
    """清洗 ASR 输出的文本：去除特殊 token 及 chat 模板结构（如 system/user/assistant 角色标签），
    保留纯转录文字内容。

    处理层次：
    1. 去除所有 <|...|> 和 |...| 格式的特殊 token（如 <|im_start|>、<|endoftext|> 等）
    2. 按行检测 role label 并去除 system prompt 内容（从 system 到 assistant 之间的文本）
    3. 去除行内残留的 role label（如行内的 "system"、"user"、"assistant" 字眼）
    4. 合并多余空白"""
    t = text.strip()
    if not t:
        return ""

    # 1. 去除所有 <|...|> 和 |...| 格式的 token
    t = re.sub(r'<\|?[a-z_]+\|?>', '', t)
    t = re.sub(r'\|[a-z_]+\|', '', t)

    # 2. 按行处理：检测 role label 行，去除 system prompt 内容
    lines = t.split("\n")
    filtered = []
    in_system_prompt = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            # 在 system prompt 区域内跳过空行，否则保留空行作为段落分隔
            if in_system_prompt:
                continue
            continue

        # 检测 role label 行（单独成行的 system/user/assistant/function）
        lowered = stripped.lower()
        if lowered in ("system", "user", "assistant", "function"):
            if lowered == "system":
                in_system_prompt = True
            elif lowered == "assistant":
                in_system_prompt = False
            continue

        # 在 system prompt 区域内的内容全部跳过
        if in_system_prompt:
            continue

        # 正常内容行
        filtered.append(stripped)

    # 3. 如果 filtered 为空，说明全是 system prompt 内容，返回空
    if not filtered:
        return ""

    # 4. 合并后，去除行内残留的 role label（如内嵌的 "system"、"user"、"assistant" 字眼）
    t = " ".join(filtered)
    t = re.sub(r'(?:^|\s)(system|user|assistant|function)(?:\s|$)', ' ', t, flags=re.IGNORECASE)

    # 5. 去除 ASR 模型输出的语言前缀（如 "language Chinese"、"language english" 等）
    # 注意：[a-zA-Z]+ 不带尾部 \b，因为中文也是 \w 字符，在 Python 3 默认 UNICODE 模式下
    # Chinese 和 声 之间没有词边界
    t = re.sub(r'\blanguage\s+[a-zA-Z]+', '', t, flags=re.IGNORECASE)

    # 6. 合并多余空白
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _ensure_month(path: Path, dt: datetime) -> Path:
    sub = path / dt.strftime("%Y-%m")
    sub.mkdir(parents=True, exist_ok=True)
    return sub


def archive_audio(src: Path, recording_start: datetime, duration_s: float) -> tuple[Path, str]:
    """按 PRD FR-001-AR：移动到 processed_audio/YYYY-MM/，重命名 YYYY-MM-DD-HHMMSS-HHMMSS.ext，避免重名"""
    base = organic_filename_for_archive(recording_start, duration_s)
    ext = src.suffix or ".wav"
    dest_dir = _ensure_month(Path(ARCHIVE_AUDIO_DIR), recording_start)
    dest = dest_dir / f"{base}{ext}"
    i = 2
    while dest.exists():
        dest = dest_dir / f"{base}_{i}{ext}"
        i += 1
    shutil.move(str(src), str(dest))
    return dest, dest.name


def archive_error_files() -> int:
    """将 error/ 根目录下的全部错误文件（.error.txt 日志 + 失败音频文件）移入 archived/ 子文件夹，
    文件名附加原文件创建时间戳防重名。供 WebUI「准备处理收件箱」按钮与 process_inbox.py 复用（v2.17；v2.36 起含失败音频）。
    返回归档的文件数。"""
    if not INBOX_ERROR_DIR.exists():
        return 0
    archived_dir = INBOX_ERROR_DIR / "archived"
    archived_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for f in sorted(INBOX_ERROR_DIR.iterdir()):
        if not f.is_file():
            continue
        # v2.54 修复：只归档"错误日志（.error.txt）+ 失败音频"，不再无差别移动
        # error/ 根目录下的所有文件（曾把 README.txt 误当错误文件搬入 archived/ 并加时间戳改名）。
        # 注意不能用 f.suffix == ".txt" 判断（复合后缀 .error.txt 的 suffix 只有 .txt，见 v2.17 坑）。
        if not (f.name.endswith(".error.txt")
                or f.suffix.lower() in SUPPORTED_EXTENSIONS):
            continue
        try:
            # 取文件创建时间作为时间戳（优先 statx btime，回退 mtime）
            ts_src = fs_birth_time(f) or datetime.fromtimestamp(f.stat().st_mtime)
            ts = ts_src.strftime("%Y%m%d_%H%M%S")
            dest = archived_dir / f"{f.stem}_{ts}{f.suffix}"
            # 若目标已存在则追加序号避免覆盖
            if dest.exists():
                for n in range(1, 100):
                    dest = archived_dir / f"{f.stem}_{ts}_{n}{f.suffix}"
                    if not dest.exists():
                        break
            f.rename(dest)
            count += 1
        except Exception:
            continue
    return count


def move_to_error(src: Path, reason: str = "") -> None:
    """处理失败时，将原始音频文件移入 error/ 目录，并生成带时间戳的 .error.txt 日志。
    v2.36：失败文件统一移入 error/（不再留在收件箱），收件箱只保留待处理文件；
    移入文件名与 .error.txt 均附加产生错误的时间戳（YYYYMMDD_HHMMSS），
    防止不同批次 / 不同来源同名文件重名冲突；原始文件保留在 error/ 供排查，
    用户可手动移回收件箱重新处理。"""
    INBOX_ERROR_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 1) 原始音频文件移入 error/（保留原名；重名时附加时间戳与序号）
    if src.exists():
        dest = INBOX_ERROR_DIR / src.name
        if dest.exists() and dest != src:
            dest = INBOX_ERROR_DIR / f"{src.stem}_{ts}{src.suffix}"
            i = 2
            while dest.exists():
                dest = INBOX_ERROR_DIR / f"{src.stem}_{ts}_{i}{src.suffix}"
                i += 1
        shutil.move(str(src), str(dest))
    # 2) .error.txt 日志（带时间戳，防重名冲突）
    if reason:
        base = src.stem
        note = INBOX_ERROR_DIR / f"{base}_{ts}.error.txt"
        i = 2
        while note.exists():
            note = INBOX_ERROR_DIR / f"{base}_{ts}_{i}.error.txt"
            i += 1
        note.write_text(reason, encoding="utf-8")


def write_transcript_backups(
    rows: list[SegmentRow],
    recording_start: datetime,
    *,
    source_file: str,
    archive_name: str,
    audio_duration_s: float,
) -> tuple[Path, Path]:
    """
    写出 Organic (可读) + JSON 两份文本备份
    返回两者路径（SRT 字幕格式已移除，评估后认为非必要）
    """
    dt = recording_start
    base = organic_filename_for_archive(dt, audio_duration_s)
    dest_dir = _ensure_month(Path(ARCHIVE_TEXT_DIR), dt)
    organic_p = dest_dir / f"{base}.txt"
    json_p    = dest_dir / f"{base}.json"

    # ---- Organic ----
    lines: list[str] = []
    lines.append("# 转录记录")
    lines.append(f"# 源文件: {source_file}")
    lines.append(f"# 归档文件: {archive_name}")
    lines.append(f"# 录音开始时间: {dt.astimezone(BJT).strftime('%Y-%m-%d %H:%M:%S (+08:00)')}")
    lines.append(f"# 处理时间: {datetime.now().astimezone(BJT).strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"# 音频总时长: {audio_duration_s:.1f}秒")
    lines.append(f"# 时间戳格式: {ORGANIC_OUTPUT_FORMAT} (北京时间)")
    lines.append("# " + "=" * 50)
    lines.append("")
    for r in rows:
        score = f" ({r.speaker_match_score:.2f})" if r.speaker_match_score is not None else ""
        if ORGANIC_OUTPUT_FORMAT in ("absolute", "both"):
            start_s = r.absolute_start_time
            end_s = r.absolute_end_time
            lines.append(f"[{start_s} - {end_s}] {r.speaker}{score}:")
        if ORGANIC_OUTPUT_FORMAT in ("relative", "both"):
            def hms(s: float) -> str:
                h = int(s // 3600); m = int((s % 3600) // 60); sec = int(s % 60)
                return f"{h:02d}:{m:02d}:{sec:02d}"
            lines.append(f"  [{hms(r.segment_start_offset)} - {hms(r.segment_end_offset)}] (相对偏移)")
        lines.append(f"  {_clean_asr_text(r.text)}")
        lines.append("")
    organic_p.write_text("\n".join(lines), encoding="utf-8")

    # ---- JSON ----
    json_p.write_text(
        json.dumps(
            {
                "source_file": source_file,
                "archive_name": archive_name,
                "recording_start_time": rows[0].recording_start_time if rows else None,
                "audio_duration": audio_duration_s,
                "segments": [
                    {
                        "speaker": r.speaker,
                        "speaker_match_score": r.speaker_match_score,
                        "segment_start_offset": r.segment_start_offset,
                        "segment_end_offset":   r.segment_end_offset,
                        "absolute_start_time":  r.absolute_start_time,
                        "absolute_end_time":    r.absolute_end_time,
                        "text": _clean_asr_text(r.text),
                        "confidence": r.confidence,
                        "language": r.language,
                    }
                    for r in rows
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    return organic_p, json_p


def update_txt_files_speaker(old_label: str, new_name: str,
                             text_dir: Path | None = None) -> int:
    """声纹标注后，更新已有 TXT/JSON 文件中的说话人标签（SRT 已移除，非必要格式）。
    扫描 text_dir 目录下所有文本备份文件，将 old_label（不区分大小写）替换为 new_name。
    同时搜索旧版格式（如 old_label 为 unknown_0001 时，也搜索 UNKNOWN_00，因为旧版编号
    从 0 开始且大写两位，v2.3 迁移后改为小写四位从 1 开始）。
    返回更新的文件数。"""
    target = Path(text_dir or ARCHIVE_TEXT_DIR)
    if not target.exists():
        return 0
    count = 0

    # 构造主模式：不区分大小写（如 unknown_0001 也匹配 UNKNOWN_0001、Unknown_0001 等）
    patterns = [re.compile(re.escape(old_label), re.IGNORECASE)]

    # 构造旧版兼容模式：如果 old_label 是 unknown_XXXX 格式，也搜索旧版 UNKNOWN_XX 格式
    # 旧版编号规则：unknown_0001 → UNKNOWN_00, unknown_0002 → UNKNOWN_01, 以此类推
    m = re.match(r"unknown_(\d{4})", old_label, re.IGNORECASE)
    if m:
        old_num = int(m.group(1))
        if old_num >= 1:
            old_legacy_label = f"UNKNOWN_{old_num - 1:02d}"
            patterns.append(re.compile(re.escape(old_legacy_label), re.IGNORECASE))
            # 也搜索 "unknown_00"/"unknown_01" 等旧版小写两位格式
            old_legacy_lower = f"unknown_{old_num - 1:02d}"
            if old_legacy_lower != old_label:
                patterns.append(re.compile(re.escape(old_legacy_lower), re.IGNORECASE))

    for f in sorted(target.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in (".txt", ".json"):
            continue
        try:
            content = f.read_text(encoding="utf-8")
            new_content = content
            matched = False
            for pat in patterns:
                if pat.search(new_content):
                    new_content = pat.sub(new_name, new_content)
                    matched = True
            if not matched:
                continue
            f.write_text(new_content, encoding="utf-8")
            count += 1
        except Exception:
            continue
    return count
