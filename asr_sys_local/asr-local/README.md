# Local ASR & Voiceprint System

本地音频转写与说话人识别系统 —— **纯本地运行、完全离线推理，所有数据只保存在运行设备本机，不依赖、不上传任何云端服务。**

## 设计原则

- **本地优先**：全部推理在设备 CPU 上完成（VAD / 说话人分离 / 声纹识别 / 语音转写），无需网络
- **数据不出本机**：转写结果、声纹特征、文本备份一律存储在本地 SQLite 与本地目录，可由用户完全掌控
- **离线可运行**：模型权重下载一次后即完全离线使用，无任何外部依赖

## 功能特性

- **语音转写**：Qwen3-ASR-0.6B 多模态模型，离线 CPU 推理，中文及多语言/方言支持
- **说话人分离**：pyannote diarization，自动切分不同说话人
- **声纹识别与持续学习**：pyannote embedding，识别已知说话人；未识别对象以 `unknown_XXXX` 编号持久化，用户标注姓名后系统持续学习、越用越准
- **语音活动检测**：Silero VAD，只转写有效语音段，跳过静音
- **Web 管理界面**（Streamlit）：处理状态看板、转写记录检索、说话人标注校准、人物档案、归档浏览
- **多种入口**：Web 看板（推荐） + CLI 主菜单（`run.sh`）

## 处理流程

```
音频 → 语音活动检测(VAD) → 说话人分离(Diarization) → 声纹匹配 → 语音转写(ASR) → 归档入库
```

处理全程在内存中进行，原始音频文件不被修改；成功后按统一规则归档并重命名，转写文本分段存入数据库与文本备份（TXT / JSON）。

## 目录结构

仓库目录与运行节点（ThinkPad）生产布局**完全一致**：一级目录 `asr_sys_local/`（工程总目录）下，代码集中在 `asr-local/`，数据目录 `audio_inbox/` / `audio_archive/` 以占位文件保留结构（内容属个人数据，不入库）。

```
asr_sys_local/                # 工程总目录（= git 仓库根，与运行节点 /home/kevin/asr_sys_local 一致）
├── asr-local/                # 代码目录（= 部署源）
│   ├── config/               # 全局配置（路径、阈值、模型参数）
│   ├── scripts/              # 入口程序（Web 界面、批量处理、CLI 工具、模型下载）
│   ├── src/                  # 核心模块（VAD / 说话人分离 / 声纹 / ASR / 数据库 / 归档）
│   ├── src/utils/            # 通用工具（音频 IO、时间戳、哈希）
│   ├── systemd/              # 系统服务单元（可选，常驻运行）
│   ├── run.sh                # CLI 主菜单启动器
│   ├── deploy_webui.sh       # 部署脚本：同步代码到运行节点并重启服务
│   └── requirements.txt
├── audio_inbox/              # 数据：收件箱（放入待处理音频；内容不入库）
├── audio_archive/            # 数据：归档音频 / 文本备份 / 数据库（内容不入库）
├── PRD_local_asr_system.md   # 需求文档（功能需求 / 数据模型 / UI 设计）
└── TDD_local_asr_system.md   # 技术设计文档（架构 / 实现细节 / 配置参考 / 变更日志）
```

## 快速开始

1. 准备 Python 3.12 环境并安装依赖：`pip install -r requirements.txt`
2. 下载模型权重（体积较大且各模型有独立许可协议，**不随本仓库分发**）：
   `bash scripts/step2_download_models.sh <HF_TOKEN>`
3. 启动 Web 界面：`streamlit run scripts/webui.py`（或运行 `run.sh` 进入主菜单）

## 部署到运行节点

`deploy_webui.sh` 将代码同步到运行节点（例如局域网内的低功耗主机）并自动重启服务：

```bash
bash deploy_webui.sh
```

运行节点通过 `.env` 文件配置本地路径与模型目录；代码本身不包含机器特定路径，`.env` 等本地配置不会进入版本库。

## 本地配置（不入库）

| 文件/目录 | 用途 | 说明 |
|---|---|---|
| `.env` | 运行节点环境变量（Token、路径） | 本地机密，禁止提交 |
| `models/` | 模型权重缓存 | 体积大 + 许可限制，自行下载 |
| `sample_audio/` | 示例音频 | 个人数据，禁止提交 |

## 许可

- 本仓库代码：MIT
- 模型权重：Qwen3-ASR、pyannote 系列、Silero VAD 各有其许可证，请在使用前确认条款；权重需自行下载
