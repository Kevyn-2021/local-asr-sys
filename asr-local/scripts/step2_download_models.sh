#!/usr/bin/env bash
# 一键模型下载脚本 (PRD 部署 Step 2) —— v2.46 重写
# 用法: bash scripts/step2_download_models.sh <HF_TOKEN>
#       或 export HF_TOKEN=xxx; bash scripts/step2_download_models.sh
# 网络兜底: 直连 huggingface.co 卡死时，用镜像或代理后重跑：
#       HF_ENDPOINT=https://hf-mirror.com bash scripts/step2_download_models.sh
#       （v2.46 实测 hf-mirror 可用；也可 export HTTPS_PROXY=... 走代理）
#
# 说明: 全部用 Python snapshot_download 下载（huggingface-cli 已废弃、不再工作），
#       目录布局与运行时逐一对齐（路径配合，缺一不可）：
#         models/Qwen3-ASR-1.7B-hf/                 ← asr.py 自定义目录直接加载
#         models/silero-vad/snakers4_silero-vad_master ← vad.py torch.hub 本地缓存
#         models/hub/models--pyannote--*            ← PyAnnote HF hub 缓存（HF_HOME/hub）
#   运行时一致性（.env / settings.py / 本脚本同一套）：
#       HF_HOME=models  ⇒ PyAnnote 缓存根 = models/hub；asr.py 自定义目录 = models/Qwen3-ASR-1.7B-hf；
#       vad.py torch.hub.set_dir = models/silero-vad
#
# 前置: 在 HuggingFace 网页登录并同意以下 gated 模型访问条款：
#   - https://huggingface.co/pyannote/speaker-diarization-3.1
#   - https://huggingface.co/pyannote/segmentation-3.0
#   - https://huggingface.co/pyannote/embedding
# 否则对应组件下载会失败。
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

# 与运行时完全一致的路径（唯一口径，见 settings.py MODELS_DIR = HF_HOME）
export HF_HOME="$MODELS"
export HF_HUB_CACHE="$MODELS/hub"
export HUGGINGFACE_HUB_CACHE="$MODELS/hub"
export HF_ENDPOINT="${HF_ENDPOINT:-https://huggingface.co}"
export HF_TOKEN
export MODELS_DIR="$MODELS"

PY="$VENV/bin/python"

if [ ! -x "$PY" ]; then
  echo "✗ 未找到 venv Python：$PY" >&2
  exit 3
fi

# 确保 huggingface_hub 可用（提供 snapshot_download）
"$PY" -m pip show huggingface_hub >/dev/null 2>&1 \
  || "$PY" -m pip install -q "huggingface_hub>=0.24"

echo "============================================="
echo "[1/5] 校验 HF Token"
echo "============================================="
"$PY" - <<'PYEOF'
import os
from huggingface_hub import HfApi
try:
    HfApi(token=os.environ["HF_TOKEN"]).whoami()
    print("✓ HF 登录校验通过")
except Exception as e:
    raise SystemExit(f"✗ HF Token 无效或网络不通（可试 HF_ENDPOINT=https://hf-mirror.com）: {e}")
PYEOF

download_hub() {
  # 下载到 HF hub 缓存（$HF_HOME/hub/models--<org>--<name>），运行时离线命中同一路径
  "$PY" - "$1" <<'PYEOF'
import os, sys
from huggingface_hub import snapshot_download
repo = sys.argv[1]
print(f"[hub] {repo} → {os.environ['HF_HUB_CACHE']}")
snapshot_download(repo, token=os.environ["HF_TOKEN"])
print(f"✓ {repo} 已入 hub 缓存")
PYEOF
}

echo ""
echo "============================================="
echo "[2/5] Qwen3-ASR-1.7B-hf（自定义目录，asr.py 直接加载）"
echo "============================================="
"$PY" - <<'PYEOF'
import os
from huggingface_hub import snapshot_download
dest = os.path.join(os.environ["MODELS_DIR"], "Qwen3-ASR-1.7B-hf")
print(f"[local-dir] Qwen/Qwen3-ASR-1.7B-hf → {dest}")
snapshot_download("Qwen/Qwen3-ASR-1.7B-hf", local_dir=dest,
                  token=os.environ["HF_TOKEN"])
if not os.path.exists(os.path.join(dest, "config.json")):
    raise SystemExit("✗ Qwen3-ASR-1.7B-hf 下载不完整（缺 config.json）")
print("✓ Qwen3-ASR-1.7B-hf")
PYEOF

echo ""
echo "============================================="
echo "[3/5] PyAnnote Diarization 3.1 + 依赖（全部入 hub 缓存）"
echo "============================================="
download_hub pyannote/speaker-diarization-3.1
download_hub pyannote/segmentation-3.0
download_hub pyannote/wespeaker-voxceleb-resnet34-LM   # 3.1 默认声纹嵌入
download_hub pyannote/speaker-diarization-community-1  # 3.1 的 PLDA 打分依赖（v2.46 记录）

echo ""
echo "============================================="
echo "[4/5] PyAnnote Embedding（声纹识别，hub 缓存）"
echo "============================================="
download_hub pyannote/embedding

echo ""
echo "============================================="
echo "[5/5] Silero VAD（torch.hub 缓存，与 vad.py 目录一致）"
echo "============================================="
"$PY" - <<'PYEOF'
import os
import torch
hub_dir = os.path.join(os.environ["MODELS_DIR"], "silero-vad")
torch.hub.set_dir(hub_dir)
torch.hub.load(repo_or_dir="snakers4/silero-vad", model="silero_vad",
               onnx=False, trust_repo=True, source="github")
if not os.path.isdir(os.path.join(hub_dir, "snakers4_silero-vad_master")):
    raise SystemExit("✗ Silero VAD 下载不完整（缺 snakers4_silero-vad_master）")
print("✓ Silero VAD")
PYEOF

echo ""
echo "============================================="
echo "  全部模型下载完成 ✓"
echo "  运行时验证：bash run.sh → 选 6 再跑一次（或直接处理音频），"
echo "  PyAnnote/声纹会以 HF_HUB_OFFLINE=1 命中 models/hub 缓存。"
echo "============================================="
