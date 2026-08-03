"""
时间戳工具
- 文件系统 birth time (statx.stx_btime)
- 文件名正则解析
- absolute_start = recording_start_time + offset 的计算
- 强制统一 Asia/Shanghai
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal

from config.settings import (
    FILENAME_TIME_PATTERNS,
    TIME_SOURCE_MISMATCH_THRESHOLD_SECONDS,
    TIME_SOURCE_PRIORITY,
    TIMEZONE,
)

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo(TIMEZONE)
except Exception:  # pragma: no cover
    LOCAL_TZ = timezone(timedelta(hours=8))


BJT = LOCAL_TZ  # 北京时间 (UTC+8)，代码里直接用


# ====================== birth time via statx ======================
# Python 3.12 的 os.stat() 在 Linux 5.8+ 上支持 st_birthtime，但为保险
# 我们直接通过 libc statx(2) 拿 stx_btime；失败再回退 st_mtime 等

class _StatxTimestamp(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_int64), ("tv_nsec", ctypes.c_uint32), ("__reserved", ctypes.c_int32)]

class _Statx(ctypes.Structure):
    _fields_ = [
        ("stx_mask",       ctypes.c_uint32),
        ("stx_blksize",    ctypes.c_uint32),
        ("stx_attributes", ctypes.c_uint64),
        ("stx_nlink",      ctypes.c_uint32),
        ("stx_uid",        ctypes.c_uint32),
        ("stx_gid",        ctypes.c_uint32),
        ("stx_mode",       ctypes.c_uint16),
        ("__spare0",       ctypes.c_uint16 * 1),
        ("stx_ino",        ctypes.c_uint64),
        ("stx_size",       ctypes.c_uint64),
        ("stx_blocks",     ctypes.c_uint64),
        ("stx_attributes_mask", ctypes.c_uint64),
        ("stx_atime",      _StatxTimestamp),
        ("stx_btime",      _StatxTimestamp),  # 创建/生成时间
        ("stx_ctime",      _StatxTimestamp),
        ("stx_mtime",      _StatxTimestamp),
        ("stx_rdev_major", ctypes.c_uint32),
        ("stx_rdev_minor", ctypes.c_uint32),
        ("stx_dev_major",  ctypes.c_uint32),
        ("stx_dev_minor",  ctypes.c_uint32),
        ("stx_mnt_id",     ctypes.c_uint64),
        ("stx_dio_mem_align", ctypes.c_uint32),
        ("stx_dio_offset_align", ctypes.c_uint32),
        ("stx_subvol",     ctypes.c_uint64),
        ("__spare2",       ctypes.c_uint64 * 13),
    ]

_STATX_BTIME = 0x800
_AT_FDCWD = -100
_EMPTY_PATH = b""


def _statx_btime(path: Path) -> datetime | None:
    """尝试用 Linux statx(2) 获取文件创建时间；失败返回 None"""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        libc.statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(_Statx)]
        libc.statx.restype = ctypes.c_int
        st = _Statx()
        ret = libc.statx(_AT_FDCWD, str(path).encode("utf-8"), 0, _STATX_BTIME, ctypes.byref(st))
        if ret != 0:
            return None
        if not (st.stx_mask & _STATX_BTIME):
            return None
        epoch = st.stx_btime.tv_sec + st.stx_btime.tv_nsec / 1e9
        return datetime.fromtimestamp(epoch, tz=BJT)
    except Exception:
        return None


def fs_birth_time(path: Path) -> datetime | None:
    """最优先：文件创建时间（Linux birth time）。失败 None"""
    path = Path(path)
    t = _statx_btime(path)
    if t is not None:
        return t
    # fallbacks: try st_birthtime (available in some builds), else None
    st = path.stat()
    bt = getattr(st, "st_birthtime", None)
    if bt is not None:
        return datetime.fromtimestamp(float(bt), tz=BJT)
    return None


# ====================== 文件名解析 ======================

def parse_filename_time(filename: str) -> datetime | None:
    stem = Path(filename).stem
    for pat in FILENAME_TIME_PATTERNS:
        m = re.search(pat, stem)
        if not m:
            continue
        try:
            y, mo, d, h, mi, s = [int(m.group(k)) for k in ("Y", "M", "D", "h", "m", "s")]
            return datetime(y, mo, d, h, mi, s, tzinfo=BJT)
        except Exception:
            continue
    return None


# ====================== 主入口：提取录音开始时间 ======================

TimeSource = Literal["file_birthtime", "filename", "audio_metadata", "manual"]

@dataclass
class ExtractedTime:
    recording_start: datetime
    source: TimeSource
    alternatives: dict[TimeSource, datetime | None]
    needs_confirmation: bool
    note: str = ""


def extract_recording_start_time(
    path: Path,
    *,
    manual: datetime | None = None,
    source_priority: list[TimeSource] | None = None,
    mismatch_threshold: int = TIME_SOURCE_MISMATCH_THRESHOLD_SECONDS,
) -> ExtractedTime:
    """按来源优先级提取录音开始绝对时间（PRD FR-001-TS）"""
    path = Path(path)
    prio: list[TimeSource] = list(source_priority or TIME_SOURCE_PRIORITY)
    alts: dict[TimeSource, datetime | None] = {
        "file_birthtime": fs_birth_time(path),
        "filename":     parse_filename_time(path.name),
        "audio_metadata": None,  # TODO: 从 ID3 / RIFF INFO 取（暂未实现）
        "manual":       manual,
    }
    # 先挑选第一个可用来源作为主值
    primary_source: TimeSource | None = None
    primary_value: datetime | None = None
    for src in prio:
        if alts.get(src) is not None:
            primary_source = src
            primary_value = alts[src]
            break
    if primary_value is None:
        # 所有来源都没有——兜底：用修改时间，并打上需要确认的标记
        primary_value = datetime.fromtimestamp(path.stat().st_mtime, tz=BJT)
        primary_source = "file_birthtime"
        note = "没有明确来源，回退到 st_mtime，请手动确认"
        return ExtractedTime(
            recording_start=primary_value,
            source=primary_source,
            alternatives=alts,
            needs_confirmation=True,
            note=note,
        )
    # 跨来源一致性校验：主值 vs 其他非 None 来源
    needs_confirmation = False
    note = ""
    for src, v in alts.items():
        if v is None or src == primary_source:
            continue
        diff_s = abs((primary_value - v).total_seconds())
        if diff_s > mismatch_threshold:
            needs_confirmation = True
            note = (f"来源不一致：主={primary_source}@{primary_value.isoformat()} "
                    f" vs {src}@{v.isoformat()} 差 {int(diff_s)}s > 阈值 {mismatch_threshold}s")
            break
    return ExtractedTime(
        recording_start=primary_value,
        source=primary_source,
        alternatives=alts,
        needs_confirmation=needs_confirmation,
        note=note,
    )


# ====================== 偏移量 → 绝对时间 ======================

def offset_to_absolute(recording_start: datetime, offset_s: float) -> datetime:
    """recording_start_time + offset_s，保留毫秒，返回北京时间"""
    return recording_start + timedelta(seconds=float(offset_s))


def organic_filename_for_archive(recording_start: datetime, duration_s: float) -> str:
    """PRD FR-001-AR：YYYY-MM-DD-HHMMSS-HHMMSS，起始=录音开始，结束=开始+时长"""
    end = recording_start + timedelta(seconds=float(duration_s))
    sdate = recording_start.strftime("%Y-%m-%d")
    sstart = recording_start.strftime("%H%M%S")
    send = end.strftime("%H%M%S")
    return f"{sdate}-{sstart}-{send}"
