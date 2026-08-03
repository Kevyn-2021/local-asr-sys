"""
命令行入口：处理单个音频
用法:
  PYTHONPATH=/home/kevin/asr-local /home/kevin/asr-local/.venv/bin/python scripts/run_pipeline.py /path/to/audio.wav
  # 指定说话人数（提升精度）:
    --num-speakers 2
  # 手动覆盖录音开始时间（格式: 2026-07-31T14:30:52+08:00 或 2026-07-31 14:30:52）:
    --start-time "2026-07-31 14:30:52"
  # 非交互模式（遇到时间需要确认时直接接受系统建议，不阻塞）:
    --non-interactive
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ_ROOT))

from config.settings import LOG_PATH, TIME_SOURCE_PRIORITY  # noqa: E402
from src.pipeline import AsrPipeline, PipelineError  # noqa: E402
from src.utils.time_utils import BJT, ExtractedTime, extract_recording_start_time  # noqa: E402

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


def _parse_start(s: str) -> datetime:
    s = s.strip().replace("Z", "+08:00")
    try:
        return datetime.fromisoformat(s).astimezone(BJT)
    except Exception:
        pass
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJT)
    except Exception as e:
        raise argparse.ArgumentTypeError(f"时间格式不支持: {s}") from e


def _cli_confirm(ext: ExtractedTime) -> ExtractedTime:
    alt_lines = []
    for src, dt in ext.alternatives.items():
        if dt is None: continue
        alt_lines.append(f"  - {src}: {dt.isoformat()}")
    print("\n" + "=" * 60)
    print("⏱ 录音开始时间提取结果：")
    print(f"  主来源 [{ext.source}]: {ext.recording_start.isoformat()}")
    if alt_lines:
        print("  其他来源:")
        print("\n".join(alt_lines))
    if ext.needs_confirmation:
        print("⚠️  需要确认: " + ext.note)
        print("   Y = 接受, 或直接粘贴新的时间 (YYYY-MM-DD HH:MM:SS)")
        ans = input("请确认 [Y/新时间]: ").strip()
        if ans.lower() in ("", "y", "yes"):
            return ext
        try:
            ext2 = ExtractedTime(
                recording_start=_parse_start(ans),
                source="manual",
                alternatives=ext.alternatives,
                needs_confirmation=False,
                note="manual override via CLI",
            )
            return ext2
        except Exception as e:
            print(f"  解析失败，使用系统建议。原因: {e}")
    else:
        print(f"✅ 自动接受 (优先级 {' > '.join(TIME_SOURCE_PRIORITY)})")
    return ext


def main():
    parser = argparse.ArgumentParser(description="ASR 本地转录：VAD + Diarization + 声纹识别 + Qwen3-ASR + 入库归档")
    parser.add_argument("audio", type=Path, help="音频文件路径")
    parser.add_argument("--num-speakers", type=int, default=None, help="已知说话人数（提升分离精度与速度）")
    parser.add_argument("--start-time", type=_parse_start, default=None, help="强制覆盖录音开始绝对时间 (北京时间)")
    parser.add_argument("--non-interactive", action="store_true", help="不交互，遇到需要确认的时间直接接受系统建议")
    parser.add_argument("--hf-token", type=str, default=os.environ.get("HF_TOKEN"), help="HuggingFace Token（可用环境变量 HF_TOKEN）")
    args = parser.parse_args()
    _setup_logger()

    if not args.audio.exists():
        print(f"❌ 文件不存在: {args.audio}", file=sys.stderr)
        sys.exit(2)
    if not args.hf_token:
        print("❌ 缺少 HuggingFace Token：请通过 --hf-token 或环境变量 HF_TOKEN 提供", file=sys.stderr)
        sys.exit(2)

    pipe = AsrPipeline(hf_token=args.hf_token)

    def cb(ext: ExtractedTime) -> ExtractedTime:
        if args.non_interactive:
            return ext
        return _cli_confirm(ext)

    try:
        result = pipe.process_file(
            args.audio,
            time_override=args.start_time,
            num_speakers=args.num_speakers,
            confirm_time_cb=cb,
        )
    except PipelineError as e:
        print(f"❌ 流水线错误: {e}")
        sys.exit(1)

    if result.success:
        print(f"\n✅ 处理完成，写入 {result.rows_written} 条记录")
        print(f"   归档: {result.archive_path}")
        if result.text_backups:
            print(f"   文本备份 (TXT/JSON): {result.text_backups[0].parent}/")
        sys.exit(0)
    else:
        print(f"❌ 失败: {result.error_msg}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
