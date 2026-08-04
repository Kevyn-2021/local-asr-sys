#!/usr/bin/env bash
# 主菜单启动器（对应 PRD 中 CLI 交互示例）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "$SCRIPT_DIR" && pwd)"
VENV="$PROJ_ROOT/.venv"
PY="$VENV/bin/python"

# 加载生产环境变量（.env：HF_TOKEN / ASR_PROJ_ROOT / ASR_ARCHIVE / ASR_INBOX / HF_HOME 等），
# 使 CLI 与 WebUI 共用同一套生产路径。未加载 .env 时 settings.py 会走默认值
# （~/asr-local、~/audio_archive、model_cache），在 HOME 下制造残留目录（v2.21 修复）。
ENV_FILE="$PROJ_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
# run.sh 能确定工程根目录，强制注入——.env 未配置 ASR_PROJ_ROOT 时，
# settings.py 的 PROJ_ROOT 默认 ~/asr-local，会污染 SAMPLE_AUDIO_DIR 等派生路径。
export ASR_PROJ_ROOT="$PROJ_ROOT"

# 若仍未取得 HF_TOKEN（无 .env），读本地密钥文件兜底
TOKEN_FILE="$PROJ_ROOT/.hf_token"
if [ -z "${HF_TOKEN:-}" ] && [ -f "$TOKEN_FILE" ]; then
  export HF_TOKEN="$(cat "$TOKEN_FILE" | tr -d '\n')"
fi

# 确保 DB 已被初始化
"$PY" -c "import sys; sys.path.insert(0,'$PROJ_ROOT'); from src.db import init_db; init_db(); print('DB OK')" >/dev/null

clear
cat <<'BANNER'
╔══════════════════════════════════════════════════════════╗
║       🎙️  本地音频转录与声纹识别系统 — 主菜单          ║
║                                                          ║
║  🖥️   运行环境: ThinkPad Ubuntu 24.04 · i5-10210U       ║
║  🧠 模型组合: Silero VAD + PyAnnote + Qwen3-ASR-1.7B    ║
║  💾 数据位置: ~/asr_sys_local/audio_archive/           ║
║  📥 收件箱  : ~/asr_sys_local/audio_inbox/  (看板手动触发)║
╚══════════════════════════════════════════════════════════╝
BANNER

if [ -z "${HF_TOKEN:-}" ]; then
  echo "⚠️  未检测到 HuggingFace Token。请先在页面登录："
  echo "   https://huggingface.co/settings/tokens  (create new → read)"
  echo "   然后可以："
  echo "     方式A) export HF_TOKEN=hf_xxxx (只影响当前会话)"
  echo "     方式B) echo -n 'hf_xxxx' > $TOKEN_FILE && chmod 600 $TOKEN_FILE (永久保存)"
  echo ""
fi

while true; do
  echo ""
  echo "请选择运行模式:"
  echo "  1) 📝 单次处理音频文件"
  echo "  2) 🖥️   启动 Web 管理界面 (Streamlit)"
  echo "  3) 🎤 声纹库录入 (注册 '我' / 老婆 / 女儿)"
  echo "  4) 👥 查看声纹库"
  echo "  5) 📊 查看数据库统计"
  echo "  6) ⬇️  下载/验证模型权重"
  echo "  7) 🔑 设置/覆盖 HF Token"
  echo "  8) ❌ 退出"
  echo ""
  read -rp "输入选项 [1-8]: " opt

  case "$opt" in
    1)
      if [ -z "${HF_TOKEN:-}" ]; then echo "缺少 HF_TOKEN"; continue; fi
      read -rp "音频文件路径: " f
      read -rp "已知说话人数 (回车=自动检测): " ns
      args=()
      [ -n "$f"  ] && args+=("$f")
      [ -n "$ns" ] && args+=(--num-speakers "$ns")
      "$PY" "$PROJ_ROOT/scripts/run_pipeline.py" "${args[@]}"
      ;;
    2)
      echo "🖥️  启动 Web UI (0.0.0.0:$WEB_PORT)，MacBook/惠普经 Tailscale 访问"
      # Tailscale/局域网都能访问；请用 Tailscale ACL 限制访问设备
      exec "$VENV/bin/streamlit" run "$PROJ_ROOT/scripts/webui.py" \
        --server.address 0.0.0.0 \
        --server.port "${WEB_PORT:-8501}" \
        --server.headless true
      ;;
    3)
      if [ -z "${HF_TOKEN:-}" ]; then echo "缺少 HF_TOKEN"; continue; fi
      read -rp "注册人称呼 (例: 我/老婆/女儿): " name
      [ -z "$name" ] && continue
      read -rp "是用户本人吗？(is_owner 标记，仅一条) [y/N]: " own
      read -rp "音频文件路径 (留空=用麦克风录 $((90)) 秒): " audio
      args=(--name "$name")
      [ "${own,,}" = "y" ] && args+=(--is-owner)
      [ -n "$audio" ] && args+=("$audio")
      "$PY" "$PROJ_ROOT/scripts/enroll_voiceprint.py" "${args[@]}"
      ;;
    4)
      "$PY" "$PROJ_ROOT/scripts/enroll_voiceprint.py" --list
      ;;
    5)
      "$PY" - <<PY
import sys; sys.path.insert(0, "$PROJ_ROOT")
from src.db import connect
with connect() as c:
  r = c.execute("SELECT COUNT(*) segs, COUNT(DISTINCT file_hash) files, COUNT(DISTINCT speaker) spks, COALESCE((SELECT SUM(d) FROM (SELECT MAX(audio_duration) AS d FROM transcripts GROUP BY file_hash)),0)/3600.0 hrs FROM transcripts").fetchone()
  print(f"  总片段  : {r['segs']}")
  print(f"  文件数  : {r['files']}")
  print(f"  说话人  : {r['spks']}")
  print(f"  累计时长: {r['hrs']:.1f} 小时")
PY
      ;;
    6)
      if [ -z "${HF_TOKEN:-}" ]; then echo "缺少 HF_TOKEN"; continue; fi
      bash "$PROJ_ROOT/scripts/step2_download_models.sh" "$HF_TOKEN"
      ;;
    7)
      read -rsp "粘贴 HF Token (huggingface.co/settings/tokens): " tok
      echo ""
      if [ -n "$tok" ]; then
        echo -n "$tok" > "$TOKEN_FILE"
        chmod 600 "$TOKEN_FILE"
        export HF_TOKEN="$tok"
        echo "✅ 已保存至 $TOKEN_FILE (权限 600)"
      fi
      ;;
    8|q|quit|exit)
      echo "👋 Bye~"
      exit 0
      ;;
    *)
      echo "无效选项: $opt"
      ;;
  esac
done
