#!/usr/bin/env bash
# 把 systemd 单元安装到 ~/.config/systemd/user/ 并启用
set -euo pipefail
SUDO_PW="${1:-}"
if [ -z "$SUDO_PW" ]; then
  echo "用法: bash systemd/install_services.sh <sudo_password>" >&2
  echo "  需要 sudo 以便 enable-linger（用户退出登录后服务也能运行）和设置 firewall。" >&2
  exit 2
fi

HERE="$(cd "$(dirname "$BASH_SOURCE")" && pwd)"
PROJ_ROOT="$(cd "$HERE/.." && pwd)"
USER_UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$USER_UNIT_DIR"

cp -v "$HERE/asr-webui.service"   "$USER_UNIT_DIR/"

# 默认 .env 模板：填 HF_TOKEN
ENV_FILE="$PROJ_ROOT/.env"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<'EOF'
# 复制自模板：请把下面的 HF Token 换成你自己的（read 权限即可）
HF_TOKEN=hf_paste_your_token_here
# 可选：Web UI 端口 & 绑定地址
ASR_WEB_PORT=8501
ASR_WEB_HOST=0.0.0.0
# 可选：模型缓存目录
HF_HOME=/home/kevin/asr_sys_local/asr-local/models
EOF
  chmod 600 "$ENV_FILE"
  echo "✓ 已生成 .env 模板: $ENV_FILE  (请用编辑器修改 HF_TOKEN)"
fi

systemctl --user daemon-reload
# 开机自启
systemctl --user enable asr-webui.service   || true

# linger：用户登出后服务也继续跑
echo "$SUDO_PW" | sudo -S loginctl enable-linger "$USER"

# ufw: 放行 8501 (局域网 + Tailscale；真正鉴权交给 Tailscale ACL)
( echo "$SUDO_PW" | sudo -S ufw allow 8501/tcp comment "ASR webui (via Tailscale)" ) || true
echo "$SUDO_PW" | sudo -S ufw --force enable 2>/dev/null || true

echo ""
echo "✅ 安装完成"
echo "   填好 $ENV_FILE 的 HF_TOKEN 后即可："
echo "     systemctl --user start asr-webui"
echo "   查看日志: journalctl --user -u asr-webui -f"