#!/usr/bin/env bash
# 部署运行时到 ThinkPad 并重启 Web 服务（v2.19：纳入 CLI 配套文件，避免再漂移）
# 用法: bash deploy_webui.sh
#       ASR_REMOTE_HOST=kevin@<IP> bash deploy_webui.sh   # ThinkPad 换了网络/地址时覆盖
set -euo pipefail

REMOTE_HOST="${ASR_REMOTE_HOST:-kevin@10.44.21.23}"
REMOTE_ROOT="/home/kevin/asr_sys_local/asr-local"
LOCAL_ROOT="$(cd "$(dirname "$0")" && pwd)"

echo "==> 1/5 更新版本时间戳"
STAMP="$(date '+%Y-%m-%d-%H:%M:%S')"
sed -i '' "s/^UI_VERSION = \".*\"/UI_VERSION = \"${STAMP}\"/" "${LOCAL_ROOT}/scripts/webui.py"
echo "    UI_VERSION = ${STAMP}"

echo "==> 2/5 语法校验"
python3 -m py_compile "${LOCAL_ROOT}/scripts/webui.py" "${LOCAL_ROOT}/scripts/process_inbox.py" \
    "${LOCAL_ROOT}/scripts/run_pipeline.py" "${LOCAL_ROOT}/scripts/enroll_voiceprint.py" \
    "${LOCAL_ROOT}/src/db.py" "${LOCAL_ROOT}/src/voiceprint.py" \
    "${LOCAL_ROOT}/src/pipeline.py" "${LOCAL_ROOT}/src/fts.py" \
    "${LOCAL_ROOT}/src/archive.py" "${LOCAL_ROOT}/src/diarization.py" \
    "${LOCAL_ROOT}/src/asr.py" "${LOCAL_ROOT}/src/vad.py" \
    "${LOCAL_ROOT}/src/utils/time_utils.py" "${LOCAL_ROOT}/src/utils/hash_utils.py" \
    "${LOCAL_ROOT}/src/utils/audio_utils.py"
echo "    OK"

echo "==> 3/5 上传 Web 与 CLI 运行时 + Streamlit 主题配置"
# 注意: config/settings.py 不部署（设计约定）——ThinkPad 保留自己的生产配置（运行时由 .env 的 HF_HOME/ASR_PROJ_ROOT 等覆盖）。
#       settings.py 已纳入 git 版本管理（v2.37 起以 ThinkPad 生产版本为基准，MacBook 与 ThinkPad 两端文件一致），
#       不在部署清单内：部署覆盖会冲掉 ThinkPad 上手工调整的配置，故由手动 scp 同步。
# 其余文件不含机器特定路径（路径都从 settings 导入），可安全覆盖。
scp -q "${LOCAL_ROOT}/scripts/webui.py" "${REMOTE_HOST}:${REMOTE_ROOT}/scripts/webui.py"
scp -q "${LOCAL_ROOT}/scripts/process_inbox.py" "${REMOTE_HOST}:${REMOTE_ROOT}/scripts/process_inbox.py"
scp -q "${LOCAL_ROOT}/scripts/run_pipeline.py" "${REMOTE_HOST}:${REMOTE_ROOT}/scripts/run_pipeline.py"
scp -q "${LOCAL_ROOT}/scripts/enroll_voiceprint.py" "${REMOTE_HOST}:${REMOTE_ROOT}/scripts/enroll_voiceprint.py"
scp -q "${LOCAL_ROOT}/scripts/step2_download_models.sh" "${REMOTE_HOST}:${REMOTE_ROOT}/scripts/step2_download_models.sh"
scp -q "${LOCAL_ROOT}/run.sh" "${REMOTE_HOST}:${REMOTE_ROOT}/run.sh"
scp -q "${LOCAL_ROOT}/src/db.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/db.py"
scp -q "${LOCAL_ROOT}/src/voiceprint.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/voiceprint.py"
scp -q "${LOCAL_ROOT}/src/pipeline.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/pipeline.py"
scp -q "${LOCAL_ROOT}/src/fts.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/fts.py"
scp -q "${LOCAL_ROOT}/src/archive.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/archive.py"
scp -q "${LOCAL_ROOT}/src/diarization.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/diarization.py"
scp -q "${LOCAL_ROOT}/src/asr.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/asr.py"
scp -q "${LOCAL_ROOT}/src/vad.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/vad.py"
scp -q "${LOCAL_ROOT}/src/utils/time_utils.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/utils/time_utils.py"
scp -q "${LOCAL_ROOT}/src/utils/hash_utils.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/utils/hash_utils.py"
scp -q "${LOCAL_ROOT}/src/utils/audio_utils.py" "${REMOTE_HOST}:${REMOTE_ROOT}/src/utils/audio_utils.py"
ssh "${REMOTE_HOST}" "mkdir -p ${REMOTE_ROOT}/.streamlit"
scp -q "${LOCAL_ROOT}/.streamlit/config.toml" "${REMOTE_HOST}:${REMOTE_ROOT}/.streamlit/config.toml"
echo "    OK"

echo "==> 4/5 远端编译校验 + 重启服务 + 验证"
ssh "${REMOTE_HOST}" "
    ${REMOTE_ROOT}/.venv/bin/python -m py_compile \
        ${REMOTE_ROOT}/scripts/webui.py ${REMOTE_ROOT}/scripts/process_inbox.py \
        ${REMOTE_ROOT}/scripts/run_pipeline.py ${REMOTE_ROOT}/scripts/enroll_voiceprint.py \
        ${REMOTE_ROOT}/src/db.py ${REMOTE_ROOT}/src/voiceprint.py \
        ${REMOTE_ROOT}/src/pipeline.py ${REMOTE_ROOT}/src/fts.py \
        ${REMOTE_ROOT}/src/archive.py ${REMOTE_ROOT}/src/diarization.py \
        ${REMOTE_ROOT}/src/asr.py ${REMOTE_ROOT}/src/vad.py \
        ${REMOTE_ROOT}/src/utils/time_utils.py ${REMOTE_ROOT}/src/utils/hash_utils.py \
        ${REMOTE_ROOT}/src/utils/audio_utils.py &&
    systemctl --user restart asr-webui.service &&
    sleep 3 &&
    test \"\$(systemctl --user is-active asr-webui.service)\" = active &&
    grep -q '${STAMP}' ${REMOTE_ROOT}/scripts/webui.py &&
    echo '    服务 active，版本 ${STAMP} 已部署'
"

echo ""
echo "==> 5/5 校验 CLI 配套可导入（run_pipeline / enroll_voiceprint）"
ssh "${REMOTE_HOST}" "
    set -a && source ${REMOTE_ROOT}/.env && set +a &&
    PYTHONPATH=${REMOTE_ROOT} ${REMOTE_ROOT}/.venv/bin/python -c 'import sys; sys.path.insert(0,\"${REMOTE_ROOT}/scripts\"); import run_pipeline, enroll_voiceprint; print(\"    CLI 导入 OK\")'
"

echo ""
echo "部署完成。浏览器强制刷新查看（Mac: Cmd+Shift+R / Win: Ctrl+Shift+R）"
echo "页脚应显示: ASR WebUI · KVI 视觉风格 · ${STAMP}"
