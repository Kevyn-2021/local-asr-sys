"""
声纹录入 CLI — PRD FR-003-VID
用法:
  python scripts/enroll_voiceprint.py --name "我"   --is-owner          ~/Desktop/my_voice.wav
  python scripts/enroll_voiceprint.py --name "老婆"  /mnt/usb/wife.wav
  # 直接从麦克风录制（系统需装有 sounddevice）:
    python scripts/enroll_voiceprint.py --name "女儿" --record-seconds 90
  python scripts/enroll_voiceprint.py --list   # 查看所有已注册
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ_ROOT))

from src.db import list_voiceprints  # noqa: E402
from src.voiceprint import VoiceprintEngine  # noqa: E402
from src.utils.audio_utils import load_audio  # noqa: E402
from config.settings import VOICEPRINT_CONFIG  # noqa: E402


def _record(duration_s: int, save_path: Path) -> Path:
    """用 sounddevice 录音 16kHz 单声道；失败给出清晰提示"""
    try:
        import sounddevice as sd
        import soundfile as sf
    except Exception as e:
        print(f"录音需要 sounddevice 库: {e}\n请用已有音频文件，或 `pip install sounddevice`", file=sys.stderr)
        sys.exit(3)
    sr = 16000
    print(f"🎙 即将录音 {duration_s} 秒...请按 Enter 开始")
    input()
    print("  开始录音，请安静、距离麦克风 30cm...")
    recording = sd.rec(int(duration_s * sr), samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(save_path), recording, sr)
    print(f"  ✅ 录音完成 → {save_path}")
    return save_path


def main():
    parser = argparse.ArgumentParser(description="声纹库录入与查看")
    parser.add_argument("audio", nargs="?", type=Path, default=None, help="已有的音频文件路径")
    parser.add_argument("--name", type=str, help="注册人姓名/称呼 (例: 我、老婆、女儿)")
    parser.add_argument("--is-owner", action="store_true", help="这是用户本人（声纹库 1 号，仅允许注册 1 条）")
    parser.add_argument("--record-seconds", type=int, default=90, help="使用麦克风录音时长（秒）；不给定 audio 文件时启用")
    parser.add_argument("--list", action="store_true", help="列出已注册声纹")
    parser.add_argument("--hf-token", type=str, default=os.environ.get("HF_TOKEN"), help="HuggingFace Token")
    args = parser.parse_args()

    if args.list:
        rows = list_voiceprints()
        if not rows:
            print("（声纹库为空）")
        else:
            print("ID 姓名             本人  向量字节  录入时间              样本")
            for r in rows:
                owner = "是" if int(r["is_owner"]) else " "
                print(f"{int(r['person_id']):<3} {r['person_name']:<14} {owner:<3}   {int(r['emb_bytes']):<7} {r['enrolled_at']:<22} {r['sample_audio_path'] or '-'}")
        return

    if not args.name:
        print("❌ 请提供 --name", file=sys.stderr)
        sys.exit(2)
    if not args.hf_token:
        print("❌ 缺少 HF_TOKEN", file=sys.stderr)
        sys.exit(2)

    if args.audio is None:
        save = PROJ_ROOT / "sample_audio" / f"enroll_{args.name}.wav"
        audio_path = _record(args.record_seconds, save)
    else:
        audio_path = args.audio
    audio = load_audio(audio_path)
    min_d = VOICEPRINT_CONFIG["enroll_min_duration_s"]
    if audio.duration_s < min_d:
        print(f"⚠️ 音频太短 ({audio.duration_s:.0f}s < {min_d}s)，建议至少 {min_d} 秒")

    vp = VoiceprintEngine(args.hf_token)
    # 本人唯一性校验（每人只能注册一条本人声纹）
    if args.is_owner:
        if any(int(r["is_owner"]) for r in list_voiceprints()):
            print("❌ 声纹库中已存在「本人」条目（is_owner=1），每人只能一条本人。", file=sys.stderr)
            sys.exit(4)
    pid = vp.enroll_from_audio(audio, args.name, is_owner=args.is_owner,
                                sample_audio_path=str(audio_path))
    print(f"✅ 已注册：person_id={pid}, name={args.name}，样本={audio_path}")


if __name__ == "__main__":
    main()
