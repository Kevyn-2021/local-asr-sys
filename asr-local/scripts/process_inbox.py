"""
手动处理收件箱 — 由 WebUI「开始处理」按钮触发（PRD FR-008-M）

由用户点击触发，扫描整个收件箱（含子文件夹），
逐个处理所有受支持的音频文件，处理前不再做"大小稳定"判断（用户点击即确认拷贝完成）。
状态实时写入 status.json，WebUI 状态带同步反映。

安全机制（v2.10+）：
- 信号处理器：SIGTERM/SIGINT 时自动清理锁文件和状态，避免残留"处理中"
- 子进程隔离：长音频的说话人分离在独立子进程中运行（diarization.py），OOM 不拖垮主进程

用法:
  PYTHONPATH=/home/kevin/asr_sys_local/asr-local \
    /home/kevin/asr_sys_local/asr-local/.venv/bin/python scripts/process_inbox.py
"""
from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ_ROOT))

from config.settings import (  # noqa: E402
    ARCHIVE_DIR,
    INBOX_DIR,
    INBOX_ERROR_DIR,
    LOG_PATH,
    SUPPORTED_EXTENSIONS,
)
from src.pipeline import AsrPipeline  # noqa: E402

log = logging.getLogger("asr-process-inbox")
STATUS_PATH = ARCHIVE_DIR / "status.json"
LOCK_PATH = ARCHIVE_DIR / "process_inbox.lock"


def _setup_logger():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _status(**kw):
    data = {}
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass

    # ── 进度时间戳追踪 ──
    new_state = kw.get("state")
    # 注意（v2.18 修复）：不能用 "key not in data" 判断——idle 清理时该键已存在但值为
    # None，导致下次任务启动时不写入，WebUI 进度表格"总任务"行永远空白。
    # 必须判断"值为空"才写入。
    if new_state == "processing" and not data.get("processing_start_time"):
        kw["processing_start_time"] = datetime.now().isoformat()
    elif new_state == "idle":
        # 处理结束，清除进度时间戳
        kw["processing_start_time"] = None
        kw["stage_start_time"] = None

    new_stage = kw.get("stage")
    if new_stage and new_stage != data.get("stage"):
        kw["stage_start_time"] = datetime.now().isoformat()

    data.update(kw)
    data["updated_at"] = datetime.now().isoformat()
    try:
        STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


# 格式优先级（FR-001-MULTI：数字越小越优先）
_FORMAT_PRIORITY = {
    ".wav": 0, ".flac": 1, ".m4a": 2, ".mp3": 3,
    ".opus": 4, ".ogg": 5, ".webm": 6,
}


def scan_inbox() -> list[Path]:
    """递归扫描收件箱（含子文件夹）的待处理音频；排除 error/ 目录；
    同 stem 多格式（FR-001-MULTI）按格式优先级规则选最优格式，其余作为兄弟文件后续一并归档"""
    if not INBOX_DIR.exists():
        return []
    # 先按 stem 分组收集所有文件
    by_stem: dict[str, list[Path]] = {}
    for p in sorted(INBOX_DIR.rglob("*")):
        if not p.is_file():
            continue
        if INBOX_ERROR_DIR in p.parents:
            continue
        ext = p.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        by_stem.setdefault(p.stem, []).append(p)
    # 每 stem 选最优格式
    files = []
    for stem, paths in by_stem.items():
        if len(paths) == 1:
            files.append(paths[0])
        else:
            # 按优先级排序，选最优
            best = min(paths, key=lambda p: _FORMAT_PRIORITY.get(p.suffix.lower(), 99))
            files.append(best)
    return files


def _acquire_lock() -> bool:
    """防止重复触发；锁文件超过 6 小时视为陈旧（上次异常退出）"""
    try:
        if LOCK_PATH.exists():
            age = time.time() - LOCK_PATH.stat().st_mtime
            if age < 6 * 3600:
                return False
        LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")
        return True
    except Exception:
        return False


def _release_lock():
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _delete_brother_files(processed: Path) -> int:
    """处理成功后，将收件箱中同 stem 不同格式的兄弟文件直接删除。
    只保留被处理的格式，避免归档目录中同时存在多种格式。
    返回删除的兄弟文件数。"""
    count = 0
    for f in sorted(processed.parent.rglob("*")):
        if not f.is_file():
            continue
        if f.stem != processed.stem:
            continue
        if f.suffix.lower() == processed.suffix.lower():
            continue
        if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            f.unlink()
            log.info("   删除兄弟文件 %s（只保留 %s 格式）", f.name, processed.suffix)
            count += 1
        except Exception as e:
            log.warning("   兄弟文件 %s 删除失败: %s", f.name, e)
    return count


def _cleanup_empty_dirs():
    for d in sorted(INBOX_DIR.rglob("*"), reverse=True):
        if not d.is_dir() or d == INBOX_ERROR_DIR:
            continue
        try:
            if not any(d.iterdir()):
                d.rmdir()
                log.info("🧹 删除空文件夹: %s", d)
        except OSError:
            pass


def _move_failed_group(src: Path, reason: str = "") -> None:
    """处理失败后，将主文件（若仍在收件箱）与同 stem 兄弟文件一并移入 error/ 目录。
    v2.36：主文件可能已被 pipeline 内部 move_to_error 移走（已写日志），
    此处仅处理仍留在收件箱的主文件与兄弟文件——否则失败后兄弟文件会被
    下次扫描当作最优格式处理，违背 FR-001-MULTI"只处理最优格式"原则。"""
    from src.archive import move_to_error
    # 主文件：pipeline 未移动时才处理（已移动则其 .error.txt 已由 pipeline 写入）
    if src.exists():
        move_to_error(src, reason)
    # 兄弟文件：一并移入 error/（不重复写 .error.txt 日志）
    for f in sorted(src.parent.rglob("*")):
        if not f.is_file() or f == src:
            continue
        if f.stem != src.stem or f.suffix.lower() == src.suffix.lower():
            continue
        if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            move_to_error(f)
            log.info("   失败组兄弟文件 %s 已移入 error/", f.name)
        except Exception as e:
            log.warning("   兄弟文件 %s 移入 error/ 失败: %s", f.name, e)


def _archive_old_errors() -> int:
    """每次处理前，将 error/ 目录中的旧错误文件（.error.txt 日志 + 失败音频）移入 archived/ 子文件夹。
    这样 error/ 根目录只保留当前批次的错误，方便用户一眼看到最新问题。
    v2.17：归档时在文件名中附加原文件的创建时间戳，避免重名冲突；v2.36 起归档含失败音频（复用 src.archive.archive_error_files）。
    返回归档的文件数。"""
    from src.archive import archive_error_files
    count = archive_error_files()
    if count:
        log.info("📁 已归档 %d 个旧错误文件到 error/archived/", count)
    return count


# ── 信号处理器：优雅退出，清理锁文件和状态 ──
_shutdown_requested = False


def _signal_handler(signum, frame):
    global _shutdown_requested
    sig_name = signal.Signals(signum).name
    log.warning("收到信号 %s，正在优雅退出...", sig_name)
    _shutdown_requested = True


def _register_signal_handlers():
    """注册 SIGTERM / SIGINT 处理器，确保进程被终止时自动清理状态"""
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    log.info("[signal] 已注册 SIGTERM/SIGINT 处理器")


def _cleanup_on_shutdown():
    """清理锁文件和状态，防止残留'处理中'状态"""
    log.info("[signal] 执行退出清理：重置状态为 idle，释放锁文件")
    try:
        _status(state="idle", current_file=None, stage=None, pending_count=0,
                last_result="failed",
                last_completed_at=datetime.now().isoformat(),
                last_completed_file="进程被信号终止（SIGTERM/SIGINT）")
    except Exception as e:
        log.warning("[signal] 状态清理失败: %s", e)
    _release_lock()


def main():
    _setup_logger()
    _register_signal_handlers()
    if not _acquire_lock():
        log.warning("已有处理任务在进行（锁文件存在），本次跳过")
        sys.exit(3)

    try:
        # 每次处理前，将上一轮的旧错误文件归档
        _archive_old_errors()

        # 立即写入处理中状态，让 WebUI 第一时间感知（即使后续初始化耗时较长）
        _status(state="processing", current_file=None, stage="初始化", pending_count=0)
        # 确保锁文件和 status.json 写入完成后，WebUI 的 derive_state() 能正确感知
        # 否则 WebUI 在极短时间内可能读到旧状态"idle"
        import time as _time
        _time.sleep(0.5)

        token = os.environ.get("HF_TOKEN")
        if not token:
            log.error("缺少环境变量 HF_TOKEN")
            _status(state="idle", current_file=None, stage=None, pending_count=0,
                    last_result="failed",
                    last_completed_at=datetime.now().isoformat(),
                    last_completed_file="初始化失败: 缺少环境变量 HF_TOKEN")
            sys.exit(2)

        files = scan_inbox()
        if not files:
            log.info("收件箱为空，无待处理文件")
            _status(state="idle", current_file=None, stage=None, pending_count=0,
                    last_result="success",
                    last_completed_at=datetime.now().isoformat(),
                    last_completed_file="（无待处理文件）")
            sys.exit(0)

        log.info("发现 %d 个待处理文件", len(files))

        # 初始化流水线——若失败（如模型加载），在 except 中统一处理
        try:
            pipe = AsrPipeline(hf_token=token)
        except Exception as e:
            log.exception("流水线初始化失败: %s", e)
            err_msg = f"初始化失败: {e}"
            _status(state="idle", current_file=None, stage=None, pending_count=0,
                    last_result="failed",
                    last_completed_at=datetime.now().isoformat(),
                    last_completed_file=err_msg)
            sys.exit(4)

        ok, fail = 0, 0
        for i, p in enumerate(files, 1):
            # 检查是否收到退出信号
            if _shutdown_requested:
                log.warning("收到退出信号，停止处理剩余文件（已处理 %d/%d）", i - 1, len(files))
                break

            rel = p.relative_to(INBOX_DIR)
            log.info("📥 [%d/%d] 开始处理 %s", i, len(files), rel)
            _status(state="processing", current_file=str(rel), stage="开始处理",
                    pending_count=len(files) - i)
            last_ok = False
            try:
                result = pipe.process_file(
                    p,
                    confirm_time_cb=lambda ext: ext,  # 非交互
                    status_cb=lambda stage: _status(stage=stage),
                )
                last_ok = result.success
                if result.success:
                    ok += 1
                    log.info("✅ [%d/%d] 完成 %s，%d 段 → %s", i, len(files), rel,
                             result.rows_written, result.archive_path)
                    # 删除兄弟文件（只保留已处理的格式）
                    n_bro = _delete_brother_files(p)
                    if n_bro:
                        log.info("   已删除 %d 个兄弟文件，只保留 %s 格式", n_bro, p.suffix)
                else:
                    fail += 1
                    log.warning("⚠️  [%d/%d] 未处理 %s: %s", i, len(files), rel, result.error_msg)
                    # 失败文件（主文件 + 兄弟文件）统一移入 error/，收件箱只保留待处理文件
                    _move_failed_group(p, result.error_msg or "")
            except Exception as e:
                fail += 1
                log.exception("处理 %s 失败: %s", rel, e)
                _move_failed_group(p, f"处理异常: {e}")

            # 注意：无论是否最后文件，状态都保持 "processing"，
            # 由循环外的 _status(state="idle") 统一切换到空闲，避免锁文件尚存时 WebUI 读到混乱状态
            _status(state="processing",
                    current_file=None if i == len(files) else str(rel),
                    stage=None,
                    pending_count=len(files) - i,
                    last_completed_file=p.name,
                    last_completed_at=datetime.now().isoformat(),
                    last_result="success" if last_ok else "failed")

        if not _shutdown_requested:
            _cleanup_empty_dirs()
            log.info("全部完成：成功 %d，失败 %d", ok, fail)
            _status(state="idle", current_file=None, stage=None, pending_count=0)
        sys.exit(0 if fail == 0 and not _shutdown_requested else 1)
    except SystemExit:
        # 正常退出（sys.exit），不捕获
        raise
    except Exception as e:
        # 未知异常（如 AsrPipeline 初始化失败），兜底处理
        log.exception("处理过程中发生未知异常: %s", e)
        _status(state="idle", current_file=None, stage=None, pending_count=0,
                last_result="failed",
                last_completed_at=datetime.now().isoformat(),
                last_completed_file=f"异常: {e}")
        sys.exit(9)
    finally:
        if _shutdown_requested:
            _cleanup_on_shutdown()
        _release_lock()


if __name__ == "__main__":
    main()
