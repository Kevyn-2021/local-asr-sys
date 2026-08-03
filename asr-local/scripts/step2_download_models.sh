#!/usr/bin/env bash
# 一键模型下载脚本 (PRD 部署 Step 2)
# 用法: bash scripts/step2_download_models.sh <HF_TOKEN>
# 说明: 该脚本使用 huggingface-cli 将 Qwen3-ASR + PyAnnote + Silero VAD 的权重
#       拉取到 $PROJ_ROOT/models/，供离线运行。
#       在运行脚本之前，请先在 HuggingFace 页面点 "Access repository" 同意条款：
#         - https://huggingface.co/pyannote/speaker-diarization-3.1
#         - https://huggingface.co/pyannote/segmentation-3.0
#         - https://huggingface.co/pyannote/embedding
#       否则 PyAnnote 组件会下载失败。
set -euo pipefail

HF_TOKEN="${1:-${HF_TOKEN:-}}"
if [ -z "$HF_TOKEN" ]; then
  echo "用法: bash scripts/step2_download_models.sh <HF_TOKEN>" >&2
  echo "  或者 export HF_TOKEN=xxx; bash scripts/step2_download_models.sh" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$PROJ_ROOT/.venv"
MODELS="$PROJ_ROOT/models"
mkdir -p "$MODELS"

export HF_HOME="$MODELS"
export HUGGINGFACE_HUB_CACHE="$MODELS/hub"
export HF_HUB_CACHE="$MODELS/hub"
export TRANSFORMERS_CACHE="$MODELS/hub"
export TORCH_HOME="$MODELS/torch_hub"
mkdir -p "$TRANSFORMERS_CACHE" "$MODELS/torch_hub"

HUGGINGFACE_CLI="$VENV/bin/huggingface-cli"
PY="$VENV/bin/python"

if [ ! -x "$HUGGINGFACE_CLI" ]; then
  echo "✗ huggingface-cli 不在 venv，尝试 pip install..." >&2
  "$PY" -m pip install -q "huggingface_hub[cli]>=0.24"
fi

login_ok=false
if "$HUGGINGFACE_CLI" whoami --token "$HF_TOKEN" >/dev/null 2>&1; then
  login_ok=true
else
  echo "→ 尝试登录 HF..."
  echo "$HF_TOKEN" | "$HUGGINGFACE_CLI" login --token
  if "$HUGGINGFACE_CLI" whoami >/dev/null 2>&1; then login_ok=true; fi
fi

if [ "$login_ok" != "true" ]; then
  echo "✗ HF Token 无效或无法登录。请检查 token 并确认账号可访问所需模型。" >&2
  exit 3
fi

echo "============================================="
echo "[1/4] 下载 Qwen3-ASR-0.6B-hf (~1.2GB)"
echo "============================================="
"$HUGGINGFACE_CLI" download --repo-type model Qwen/Qwen3-ASR-0.6B-hf --local-dir "$MODELS/Qwen3-ASR-0.6B-hf" --local-dir-use-symlinks False

echo ""
echo "============================================="
echo "[2/4] 下载 PyAnnote Diarization 3.1 及其依赖 segmentation-3.0"
echo "============================================="
"$HUGGINGFACE_CLI" download --repo-type model pyannote/speaker-diarization-3.1 --local-dir "$MODELS/pyannote-speaker-diarization-3.1" --local-dir-use-symlinks False || {
  echo "⚠️  pyannote/speaker-diarization-3.1 下载失败" >&2
  echo "⚠️  请先在浏览器中打开:" >&2
  echo "   https://huggingface.co/pyannote/speaker-diarization-3.1" >&2
  echo "   https://huggingface.co/pyannote/segmentation-3.0" >&2
  echo "   登录你的 HF 账号，点 'Access repository' 同意条款后重试。" >&2
  exit 4
}
# Diarization 运行时会自动再抓 segmentation-3.0；这里手动也抓一份
"$HUGGINGFACE_CLI" download --repo-type model pyannote/segmentation-3.0 --local-dir "$MODELS/pyannote-segmentation-3.0" --local-dir-use-symlinks False || true

echo ""
echo "============================================="
echo "[3/4] 下载 PyAnnote Embedding (声纹识别)"
echo "============================================="
"$HUGGINGFACE_CLI" download --repo-type model pyannote/embedding --local-dir "$MODELS/pyannote-embedding" --local-dir-use-symlinks False || {
  echo "⚠️  pyannote/embedding 下载失败；请在 HuggingFace 同意该模型的访问条款后重试" >&2
  exit 5
}

echo ""
echo "============================================="
echo "[4/4] 预热下载 Silero VAD (~1MB，torch.hub)"
echo "============================================="
"$PY" - <<PYEOF
import os
os.environ["HF_HOME"] = os.environ["HF_HOME"] = "$MODELS"
import torch
print("torch hub dir:", torch.hub.get_dir())
m, utils = torch.hub.load(repo_or_dir="snakers4/silero-vad", model="silero_vad", onnx=False, trust_repo=True, source="github")
print("✓ Silero VAD 加载成功")
PYEOF

echo ""
echo "============================================="
echo "  全部模型下载完成 ✓"
echo "============================================="
du -sh "$MODELS"/* 2>/dev/null
echo ""
echo "下一步：录入你本人声纹（声纹库 1 号，必须）"
echo "  准备一段 1~3 分钟干净语音 WAV：/path/to/owner.wav"
echo "  $PY scripts/enroll_voiceprint.py --name '我' --is-owner /path/to/owner.wav"
echo ""
echo "之后：处理单个文件试试 →"
echo "  $PY scripts/run_pipeline.py ~/audio_inbox/test.wav"
echo "  → 然后在 Mac/惠普 Tailscale 浏览器打开 http://<ThinkPad-Tailscale-IP>:8501"
