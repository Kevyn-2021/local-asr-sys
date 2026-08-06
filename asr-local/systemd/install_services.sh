#!/usr/bin/env bash
# 把 systemd 单元安装到 ~/.config/systemd/user/ 并启用
set -euo pipefail
SUDO_PW="${1:-}"
ASR_WEB_ALLOW_IPS="${2:-}"
ASR_WEB_TAILSCALE_IPS="${3:-}"
if [ -z "$SUDO_PW" ]; then
  echo "用法: bash systemd/install_services.sh <sudo_password> [\"ip1 ip2 ...\"] [\"tailscale-ip1 ...\"]" >&2
  echo "  需要 sudo 以便 enable-linger（用户退出登录后服务也能运行）和设置 firewall。" >&2
  echo "  第 2 个参数（可选）：WebUI 访问白名单设备 IP（空格分隔；推荐设备接入 Tailscale 则无需）" >&2
  echo "  第 3 个参数（可选）：Tailscale 设备 IP（空格分隔；精确地址不入库、见 SEC，写入 /etc/asr-webui-fw.conf 作为固定放行项）" >&2
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

# ufw: 8501 仅放行 Tailscale 设备 IP（第 3 个参数）+ 可选白名单设备 IP（第 2 个参数）（v2.62）
if [ -n "${ASR_WEB_TAILSCALE_IPS}" ]; then
  ( echo "$SUDO_PW" | sudo -S bash -c "printf 'FIXED_IPS=\"%s 127.0.0.1\"\n' \"${ASR_WEB_TAILSCALE_IPS}\" > /etc/asr-webui-fw.conf" ) || true
  for ip in ${ASR_WEB_TAILSCALE_IPS}; do
    ( echo "$SUDO_PW" | sudo -S ufw allow from "$ip" to any port 8501 proto tcp comment "Tailscale IP" ) || true
  done
fi
for ip in ${ASR_WEB_ALLOW_IPS}; do
  ( echo "$SUDO_PW" | sudo -S ufw allow from "$ip" to any port 8501 proto tcp comment "ASR WebUI (allowlisted device)" ) || true
done
# v2.61: 安装 WebUI 白名单管理脚本 + sudoers NOPASSWD（仅限该脚本；WebUI「访问控制」页增删 IP 用）
FW_HELPER_SRC="$HERE/asr-webui-fw.sh"
if [ -f "$FW_HELPER_SRC" ]; then
  ( echo "$SUDO_PW" | sudo -S install -m 0755 -o root -g root "$FW_HELPER_SRC" /usr/local/sbin/asr-webui-fw.sh ) || true
  ( echo "$SUDO_PW" | sudo -S bash -c 'grep -q asr-webui-fw.sh /etc/sudoers.d/asr-webui-fw 2>/dev/null || printf "kevin ALL=(root) NOPASSWD: /usr/local/sbin/asr-webui-fw.sh\n" > /etc/sudoers.d/asr-webui-fw; chmod 440 /etc/sudoers.d/asr-webui-fw; visudo -c' ) || true
fi
# SSH 22 放行：ufw 默认 deny incoming，必须显式放行，否则部署/管理会断连（v2.61 明确）
( echo "$SUDO_PW" | sudo -S ufw allow 22/tcp comment "SSH" ) || true
echo "$SUDO_PW" | sudo -S ufw --force enable 2>/dev/null || true

echo ""
echo "✅ 安装完成"
echo "   填好 $ENV_FILE 的 HF_TOKEN 后即可："
echo "     systemctl --user start asr-webui"
echo "   查看日志: journalctl --user -u asr-webui -f"
