#!/usr/bin/env bash
# 管理 WebUI 端口 8501 的 ufw 访问白名单（仅 WebUI 通过 sudoers NOPASSWD 调用，v2.61）
# 用法: asr-webui-fw.sh {list|add <ipv4>|remove <ipv4>}
# 约束:
#   - 只允许操作端口 8501 的放行规则
#   - 固定放行项（Tailscale 设备 IP、127.0.0.1 等）由 /etc/asr-webui-fw.conf 的 FIXED_IPS 配置，不可删除
#   - add/remove 仅接受合法 IPv4 地址
# 安装（由 install_services.sh 自动执行）:
#   1) 复制到 /usr/local/sbin/asr-webui-fw.sh（root:root 0755）
#   2) /etc/sudoers.d/asr-webui-fw 写入:
#        kevin ALL=(root) NOPASSWD: /usr/local/sbin/asr-webui-fw.sh
set -euo pipefail

PORT=8501
FW_CONF=/etc/asr-webui-fw.conf
FIXED_IPS="127.0.0.1"
if [ -f "$FW_CONF" ]; then
  # shellcheck disable=SC1090
  . "$FW_CONF"   # 本机配置：FIXED_IPS="ip1 ip2 ..."（Tailscale 设备 IP 等，精确地址见 SEC，不入库）
fi

is_ipv4() {
  local ip="$1" octet
  [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
  for octet in ${ip//./ }; do
    (( octet >= 0 && octet <= 255 )) || return 1
  done
  return 0
}

is_fixed() {
  local ip="$1" f
  for f in ${FIXED_IPS}; do
    [ "$ip" = "$f" ] && return 0
  done
  return 1
}

exists_rule() {
  local ip="$1"
  ufw status | grep -qE "^${PORT}/tcp[[:space:]]+ALLOW([[:space:]]+IN)?[[:space:]]+${ip}([[:space:]]|$)"
}

cmd="${1:-}"
case "$cmd" in
  list)
    # 输出每行: <IP>|<comment>|<fixed:0|1>（机器可读，供 WebUI 解析）
    ufw status | grep -E "^${PORT}/tcp[[:space:]]+ALLOW([[:space:]]+IN)?[[:space:]]+" | while read -r _ _ src rest; do
      comment=""
      case "$rest" in
        \#*) comment="${rest#\# }" ;;
      esac
      if is_fixed "$src"; then
        echo "${src}|${comment}|1"
      else
        echo "${src}|${comment}|0"
      fi
    done
    ;;
  add)
    [ $# -ge 2 ] || { echo "用法: $0 add <ipv4> [comment]" >&2; exit 2; }
    is_ipv4 "$2" || { echo "非法 IPv4 地址: $2" >&2; exit 2; }
    comment="${3:-ASR WebUI (allowlisted device)}"
    comment="${comment//\'/}"
    comment="${comment:0:48}"
    if exists_rule "$2"; then
      echo "已存在: $2"
    else
      ufw allow from "$2" to any port "$PORT" proto tcp comment "$comment" >/dev/null
      echo "已添加: $2"
    fi
    ;;
  remove)
    [ $# -eq 2 ] || { echo "用法: $0 remove <ipv4>" >&2; exit 2; }
    is_ipv4 "$2" || { echo "非法 IPv4 地址: $2" >&2; exit 2; }
    if is_fixed "$2"; then
      echo "固定放行项（Tailscale/回环），不可删除: $2" >&2
      exit 1
    fi
    if exists_rule "$2"; then
      ufw delete allow from "$2" to any port "$PORT" proto tcp >/dev/null
      echo "已删除: $2"
    else
      echo "白名单中不存在: $2"
    fi
    ;;
  *)
    echo "用法: $0 {list|add <ipv4> [comment]|remove <ipv4>}" >&2
    exit 2
    ;;
esac
