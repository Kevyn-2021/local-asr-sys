# 本地音频转录与声纹识别系统 — 产品需求文档 (PRD)

**版本**: v2.66  
**日期**: 2026-08-06  
**作者**: 用户 + Kimi  
**状态**: 已实现

> **===== 文档分工说明（请先阅读）=====**
> 
> **本文档的角色**：产品需求文档（PRD），聚焦"需求是什么"——功能需求、非功能需求、UI 设计、数据模型。它回答"系统要做什么"。
> 
> **配套文档**：工程实现细节（技术架构、模型选型、各模块实现细节、经验教训、配置参考、变更日志）见另一份文档 [TDD_local_asr_system.md](file:///Users/kevin/m02_Developer/TRAE_Work_CN/ASR-Local-Thinkpad/TDD_local_asr_system.md)。
> 
> **内容不重复原则**：PRD 与 TDD 的内容互不重复。同样的内容只会在一个文档中出现，不会同时出现在两份文档中。两文档通过相互索引引用，而非复制粘贴。这样做的目的是：避免同一内容在多处维护，因漏改某处而导致不一致。
> 
> **写入新内容前请确认**：先判断内容属于"需求定义"还是"工程实现"，分别写入对应文档。如需引用对方文档的内容，使用链接索引而非重复描述。

---

## 1. 文档信息

| 项目 | 内容 |
|------|------|
| 产品名称 | 本地音频转录与声纹识别系统 |
| 目标平台 | Linux (Ubuntu 24)，单机运行节点（硬件信息本机维护，不随仓库发布） |
| 硬件约束 | Intel i5-10210U, 16GB RAM, 无独显 |
| 访问设备 | 开发机（办公室）、家用电脑（家里），经 Tailscale 加密隧道访问运行节点的 Web UI（设备信息本机维护，不随仓库发布） |
| 数据隐私 | 音频与转录结果只存在 ThinkPad 本地，不上云；跨设备访问全程走 Tailscale 端到端加密，不经第三方服务器中转 |

---

## 2. 背景与动机

### 2.1 问题陈述
用户日常通过多种设备（录音笔、手机、会议系统等）采集带时间戳的音频文件，需要：
- **手动触发处理**：音频放入收件箱（支持子文件夹）后，在 Web 看板点「开始处理收件箱」一键转录、识别说话人、归档（含有机重命名）
- **声纹分离 + 声纹识别**：不仅区分不同说话人，还要识别出"谁是谁"（本人 / 家人 / 其他注册人 / 未识别）
- **时间精度保留**：**什么时间说的这句话很重要，必须精确保留**
- **可检索**：历史记录可搜索、可查阅
- **跨设备访问**：在办公室开发机和家用电脑上都能检索、回听运行节点上的结果
- **隐私优先**：所有处理必须在本地完成，数据不上云

### 2.2 技术选型

选用 Silero VAD + PyAnnote Diarization + 声纹向量匹配（Speaker Embedding）+ Qwen3-ASR-1.7B 自建流水线，**重点保障时间戳精度**。全部模型本地运行，数据不出设备。选型理由与工程实现细节见 [TDD §2](./TDD_local_asr_system.md#2-模型选型与部署)。

---

## 3. 目标用户与场景

### 3.1 目标用户
- 个人知识管理者
- 小型团队会议记录员
- 采访/访谈工作者
- 对隐私敏感的用户
- **需要精确时间追溯的用户**（如法律取证、学术研究）

### 3.2 核心场景

**场景 A：会议录音自动归档**
> 用户将 Zoom/腾讯会议录音放入 `/home/kevin/asr_sys_local/audio_inbox/`（支持子文件夹），在 Web 看板点「开始处理收件箱」，系统自动识别说话人、转录文字、按"年月日-起始时间-结束时间"重命名归档。通过 Web 界面搜索"预算"关键词，快速定位到财务总监在 **2026-07-31 14:23:15** 的发言片段。

**场景 B：采访素材管理**
> 记者将 3 小时采访录音分段处理，系统区分记者和受访者，生成带**绝对时间戳**的文本。后期写稿时直接引用："受访者于 **2026-07-28 10:15:32** 表示..."

**场景 C：个人语音笔记时间线**
> 用户用手机录音后同步到电脑，系统自动转录并归档。支持按**精确时间**检索。

**场景 D：时间戳的严谨性**
> 需要精确到秒的时间戳，系统必须保证时间戳的**不可篡改性**和**可追溯性**。

**场景 E：家庭声纹识别（标注学习，无需专门录入）**
> 家庭录音丢入收件箱处理后，系统自动把每个说话人记为 `unknown_0001`、`unknown_0002`…。用户在看板把 `unknown_0001` 标注为"本人"、`unknown_0002` 标注为"家人"……系统随即把这些声纹与姓名关联并持续学习。之后任何新录音，系统自动认出"本人 / 家人"或标为新的 unknown。无需专门的录声纹环节。

**场景 F：跨设备检索**
> 用户在办公室的开发机上打开浏览器，通过 Tailscale 地址访问运行节点的 Web UI 检索记录；回家后，在家用电脑上同样可以直接访问，数据始终只存在运行节点上。

**场景 G：多来源批量导入**
> 用户从不同设备（录音笔、手机、会议系统）导出的音频，可能以文件夹方式组织（如 `2026-08-01/meeting.wav`、`interview/guest.mp3`）。直接拖入收件箱，系统递归遍历子文件夹，自动提取所有有效音频并处理。若同一段录音同时存在 `.wav` 和 `.mp3` 两个版本，系统只处理质量更高的 `.wav`，处理完成后将 `.mp3` 删除。

---

## 4. 功能需求

### 4.1 核心功能 (MVP)

#### FR-001: 收件箱与手动触发处理
- **优先级**: P0
- **描述**: 用户把音频放入 `/home/kevin/asr_sys_local/audio_inbox/`（支持子文件夹），在 Web 看板点击「开始处理收件箱」按钮触发处理流水线。**采用手动触发而非自动监听**：避免拷贝过程中文件大小变化的竞态，也避免 watchdog 监听子目录的可靠性问题与系统服务依赖
- **输入**: `.wav`, `.mp3`, `.m4a`, `.flac`, `.ogg`, `.opus`, `.webm`
- **触发方式**: Web 看板手动按钮（见 FR-008-M）；用户点击即视为"拷贝已完成"，无需再做大小稳定判断
- **防重复**: 通过**文件内容 SHA-256 哈希**去重（同一份音频即使改名、改路径也不会重复处理；哈希值同时供 FR-013 审计使用）
- **并发保护**: 锁文件防止重复触发；处理中状态实时写入 `status.json` 供看板显示
- **时间戳提取**: 提取**录音开始时间**（见 FR-001-TS）
- **说明**: 原 watchdog 自动监听已移除，采用手动触发原因见 FR-008-M

#### FR-001-DIR: 子文件夹递归扫描
- **优先级**: P0
- **描述**: 用户放入收件箱的可能不是单个音频文件，而是包含子文件夹的目录结构（如按日期组织的 `20260802/`、按来源组织的 `interview/`）。系统需递归遍历所有子文件夹，提取其中的有效音频文件
- **扫描规则**: 递归遍历收件箱所有子目录，排除 `error/` 子目录，同名多格式文件按 FR-001-MULTI 合并处理
- **处理顺序**: 按文件路径排序，确保处理顺序可预测

#### FR-001-MULTI: 同名多格式文件处理
- **优先级**: P0
- **描述**: 同一段录音可能以多种格式存在（如 `meeting.wav`、`meeting.mp3`、`meeting.opus`），系统只处理其中最优格式，其余同名文件直接删除，归档目录只保留被处理的单一格式
- **格式优先级** (从高到低):
  `.wav` > `.flac` > `.m4a` > `.mp3` > `.opus` > `.ogg` > `.webm`
- **处理逻辑**:
  1. 扫描收件箱时，按 stem 分组收集所有同 stem 不同后缀的音频文件
  2. 每组按格式优先级选最优格式进行流水线处理，非最优格式不处理
  3. 处理成功后，将其他同名兄弟文件直接从收件箱删除，只保留被处理的格式
  4. 处理失败时，主文件与兄弟文件**一并移入 `error/` 目录**，并生成带时间戳的 `.error.txt` 日志说明失败原因（`error/` 文件名含产生错误的时间戳 `YYYYMMDD_HHMMSS`，防止新旧批次/同名文件重名冲突）；用户排查修复后可手动将文件移回收件箱重新处理（v2.36：失败文件不再留在收件箱，避免下次扫描把次优格式兄弟文件当主格式处理）
- **示例**:
  ```
  收件箱/2026-08-01/ 下有:
    meeting.wav  (最优格式，被处理)
    meeting.mp3  (兄弟文件，被删除)
    meeting.opus (兄弟文件，被删除)

  处理 meeting.wav 成功后:
    归档到 audio_archive/processed_audio/2026-08/2026-08-01-143052-153052.wav
    meeting.mp3 ✓ 已删除
    meeting.opus ✓ 已删除

  收件箱/2026-08-01/ 清空 ✅

  处理 meeting.wav 失败后:
    error/meeting.wav                              ← 原始文件移入 error/（重名时附加时间戳）
    error/meeting.mp3                              ← 兄弟文件一并移入（避免下次当主格式处理）
    error/meeting.opus                             ← 兄弟文件一并移入
    error/meeting_20260801_215502.error.txt        ← 失败原因（文件名含产生错误的时间戳 YYYYMMDD_HHMMSS，v2.17）
  ```
- **错误文件名规则 (v2.17)**: 每次产生的错误文件命名格式为 `{源文件名}_{产生错误时间YYYYMMDD_HHMMSS}.error.txt`，避免不同批次的错误因同名而混淆；用户查看 `error/` 根目录即可一眼区分新旧错误

#### FR-001-TS: 录音开始时间提取 (时间戳核心)
- **优先级**: P0
- **描述**: 系统必须准确知道每段音频的**录音开始绝对时间**，作为后续所有时间戳计算的基准。**采用文件名时间优先策略**：用户文件命名包含精确的时间戳，不受文件拷贝传输影响
- **提取策略** (按优先级排序)：
  1. **文件名解析** (默认主来源)：用户文件命名格式为 `YYYY-MM-DD_时_分_秒`（如 `2026-08-02_19_30_25`），同时支持多种常见格式做兼容。以文件名中提取的时间为准，不依赖文件系统元数据
  2. **文件系统创建时间 (birth time / statx btime)** (兜底)：当文件名无法解析出时间时，回退到文件创建时间。用户保证创建时间的正确性
  3. ~~音频内置元数据~~：ID3 标签、RIFF INFO 等，当前暂未实现
  4. ~~手动确认/修正~~：非交互模式下不再需要，文件名即为权威来源
- **支持的文件名格式** (按匹配顺序)：
  - `2026-08-02_19_30_25`（主格式：六个字段，分隔符横线/下划线**任意混用**；`meeting-2026-07-31-14-30-52`、`2026-07-31-14-30-52-meeting` 等同型均可识别）
  - `recording_20260731_143052` / `recording-20260731-143052` / `20260731_143052_recording` / `20260731-143052-recording`（紧凑式：`YYYYMMDD` 与 `HHMMSS` 之间横线/下划线均可，时间前后可带任意前缀/后缀）
  - `voice_note_20260731T143052Z`（ISO 风格 T 分隔，兼容保留）
  - **实现口径（v2.50）**：正则按上面顺序尝试（长格式 → 紧凑式 → ISO），`re.search` 不锚定——时间戳在文件名任何位置、带任意前后缀均可提取；详细正则见 [TDD §3.5](./TDD_local_asr_system.md#35-时间戳处理)
- **配置项**: 时间来源优先级和正则匹配模式可由用户自定义
- **时区**: 一律按北京时间 (Asia/Shanghai, UTC+8) 解释无时区信息的时间
- **输出**: `recording_start_time` (ISO 8601 格式，带 +08:00 时区)

#### FR-001-AR: 归档与有机重命名
- **优先级**: P0
- **描述**: 处理成功后，原音频文件移入归档目录并按统一规则重命名，保证文件名整齐有序、按时间可排序。同名多格式文件（见 FR-001-MULTI）同步删除，归档目录只保留被处理的单一格式
- **归档目录**: `/home/kevin/asr_sys_local/audio_archive/processed_audio/YYYY-MM/`
- **重命名格式**: `YYYY-MM-DD-HHMMSS-HHMMSS.扩展名`
  - 起始时间 = `recording_start_time`
  - 结束时间 = `recording_start_time + 音频总时长`
  - 一律使用北京时间，纯数字无冒号（Windows/macOS/Linux 文件名均合法）
  - 示例: `2026-07-31-143052-153052.wav`
- **冲突处理**: 同名文件追加 `_2`, `_3` 后缀
- **失败归档**: 处理失败时，原始音频文件**移入 `/home/kevin/asr_sys_local/audio_inbox/error/` 目录**（重名时附加产生错误的时间戳 `YYYYMMDD_HHMMSS`），同 stem 兄弟文件一并移入（避免下次扫描把次优格式当主格式处理）；同时在 `error/` 下生成带时间戳的 `.error.txt` 日志（`{源文件名}_{YYYYMMDD_HHMMSS}.error.txt`）记录失败原因。用户排查修复后可手动将文件移回收件箱重新处理。每次新一轮处理启动前（「准备处理收件箱」按钮或自动），旧错误文件（日志 + 音频）自动移入 `error/archived/` 子文件夹，`error/` 根目录只保留当前批次错误
- **空文件夹清理**: 处理完成后，自动删除收件箱下已清空的子文件夹（排除 `error/` 目录），保持收件箱整洁

#### FR-002: 语音活动检测 (VAD)
- **优先级**: P0
- **描述**: 去除静音片段，只保留有效语音。**时间戳必须保留原始偏移**
- **模型**: Silero VAD (snakers4/silero-vad)，~1MB，纯本地离线推理
- **参数配置**: 见 [TDD §3.1](./TDD_local_asr_system.md#31-vad--silero-vad)
- **时间戳保留机制**:
  ```
  原始音频: [静音0-5s] [语音5-20s] [静音20-25s] [语音25-40s]

  VAD 输出:
    Segment 1: start=5.0s, end=20.0s, audio=[语音数据]
    Segment 2: start=25.0s, end=40.0s, audio=[语音数据]

  → 原始偏移量 5.0s 和 25.0s 必须完整保留，不可丢失
  ```
- **输出**: 带 `start` / `end` 的语音片段列表

#### FR-003: 声纹分离 (Speaker Diarization)
- **优先级**: P0
- **描述**: 识别不同说话人及其在原始音频中的时间区间，输出作为 FR-003-VID 声纹匹配的输入
- **模型**: PyAnnote Speaker Diarization 3.1 模型（~11MB），运行于 PyAnnote 4.x 库
- **参数配置**: 见 [TDD §3.2](./TDD_local_asr_system.md#32-diarization--pyannote-speaker-diarization-31)
- **时间戳要求**: 所有时间戳必须是**相对于原始音频起点的偏移量**（秒），不可使用 VAD 后片段的相对时间
- **性能优化 (v2.31)**: 先按 Silero VAD 语音段**拼接切除静音**再分离（缩短 segmentation 滑窗输入，加速与静音占比成正比），分离结果时间戳**自动映射回原始时间轴**，对下游（声纹/ASR/入库）完全无感；`use_vad_concat` 开关可在连续访谈等几乎无静音场景关闭（见 [TDD §3.2](./TDD_local_asr_system.md#32-diarization--pyannote-speaker-diarization-31)）
- **精度目标**: DER (Diarization Error Rate) < 8%
- **优化**: 已知说话人数时 DER 目标 < 5%

#### FR-003-VID: 声纹库与说话人识别 (Speaker Identification)
- **优先级**: P0
- **描述**: 在 Diarization 分出"几个匿名说话人"之后，通过声纹向量匹配识别出"具体是谁"
- **声纹库** (`voiceprints` 表，见 7.1)：
  - 每条记录一个人：`person_id` 自增编号、`person_name` 姓名、`embedding` 声纹向量（BLOB 存储）、`is_owner` 是否用户本人、`sample_audio_path` 录入样本路径
  - **本人标记 `is_owner` 且仅允许一条**（CLI 校验：已存在本人则拒绝），不做"1 号必须为本人"的编号强约束
- **录入流程 (CLI `enroll_voiceprint.py`，可选补充)**:
  - **定位说明**：自 v2.3 起主流程为「标注学习」（FR-003-CLUSTER——处理音频即自动抓声纹、Web 标注姓名，无需专门录入）；CLI 录入保留为**可选补充**手段，用于需要高质量固定段落样本的场景
  - 两种方式：① 导入已有音频文件（路径参数）；② `--record-seconds` 麦克风录音（sounddevice，16kHz 单声道）
  - 规范提示：环境安静、单人、距麦克风约 30cm；时长下限校验 `VOICEPRINT_CONFIG.enroll_min_duration_s`（过短仅警告不阻断）
  - 录入后提取声纹向量入库（`--is-owner` 标记本人）
- **匹配机制**（`voiceprint.py`）:
  - 对每个 Diarization 输出的匿名说话人，聚合其全部片段提取声纹向量（`aggregate_speaker_embedding`）
  - 与**命名声纹库**和**已标注声纹簇**逐级计算**余弦相似度**（三级匹配逻辑见 [FR-003-CLUSTER](#fr-003-cluster-声纹簇持久化与标注学习)，阈值见 [TDD §3.3](./TDD_local_asr_system.md#33-声纹识别--pyannote-embedding)）
  - 三级都不中 → pipeline 新建全局编号声纹簇 `unknown_XXXX`（见 FR-003-CLUSTER）
- **模型**: PyAnnote Embedding（~98MB），复用 Diarization 生态
- **输出**: 每个片段的最终说话人标签（注册人姓名 / `unknown_XXXX` 编号）+ 匹配得分

#### FR-003-CLUSTER: 声纹簇持久化与标注学习
- **优先级**: P0
- **描述**: 未识别说话人不再只是一次性的 UNKNOWN 标签，而是以"声纹簇"持久化到 `speaker_clusters` 表；同一未知人跨文件再次出现时沿用其全局编号，用户在 Web 上标注姓名后系统持续学习、越用越准
- **动机**: 一次性 UNKNOWN 标签无法跨文件关联同一个人；人工标注一次后应能强化识别，而不是每次都从零匹配
- **声纹簇表** (`speaker_clusters`，见 7.1)：
  - `cluster_id` 主键；`label`（`unknown_0001` 式四位编号，全局递增、**不复用**）；`embedding`（聚合声纹向量 float32）；`assigned_name`（用户标注的姓名，NULL = 纯 unknown）；`sample_count`（累积样本数）
- **三级匹配逻辑**（`voiceprint.py`）：
  1. **命名声纹库**：≥ auto 阈值认出 / ≥ review 阈值疑似（同 FR-003-VID）
  2. **已标注簇**：认出该簇对应的人
  3. **纯 unknown 簇**：沿用其全局编号
  4. 三级都不中 → pipeline 调 `register_new_cluster` 全局递增新建簇
- **增量学习**: 匹配到老簇时顺手做"增量平均"更新簇向量（持续学习），`sample_count` 累积
- **编号规则**: `next_unknown_label = MAX(已用编号) + 1`，四位；已被指派姓名的编号也不复用（标注 0002=某人后，下次从 0003 继续）
- **Web 标注界面**: 声纹库·数据库页「声纹簇·标注学习」面板，**列出全部簇**（编号 / 标注为 / 学习样本数；标注列显示姓名、"（未标注）"或"🚫 不标注"），分三个操作区（`st.tabs`）：①「标注为某人」——把未标注编号标注为新人或已有姓名；②「校准已标注」——纠正自动标注（改标为他人 / 改回未知，v2.20）；③「不标注」（v2.43）——陌生人不值得标注时设为**不标注**：**保持原编号** `unknown_XXXX`，从「标注为某人」列表隐藏，不参与标注流程但**照常参与声纹匹配与学习**；可随时恢复标注。标注后系统自动更新显示层映射，同时提供「确认标注并回填」按钮——用户点击后，系统批量更新 `transcripts` 表 `speaker` 字段（大小写不敏感匹配，兼容旧版 `UNKNOWN_XX` 格式），并扫描 `text_backups/` 目录下所有 `.txt`/`.json` 文件中的说话人标签进行替换。标注即 `assign_cluster_name` + 自动建人物档案（见 FR-010）
- **标注即全局生效（显示层映射 + 存量数据回填）**:
  - **显示层映射（主机制，覆盖所有数据库记录）**：数据库 `transcripts.speaker` 始终存原始标签（注册人姓名 / `unknown_XXXX` 编号），不做回填；Web UI 显示层（webui.py `speaker_display_map()` / `disp_speaker()`）把 `unknown_XXXX` 经 `speaker_clusters.assigned_name` 映射为标注姓名展示。用户标注一次，处理记录页（最近处理 / 片段记录 / 片段详情）与搜索页结果中该编号的**所有历史片段**（无论标注前还是标注后入库）自动显示姓名，不再显示 unknown。这是最轻量、最彻底的方案——无需修改数据库，所有展示层统一生效（见 §8.1.4、§8.2 页 2/页 4）
  - **存量数据回填（辅助机制，用于外部文件）**：标注时同时调用 `update_transcripts_speaker()` 更新 `transcripts` 表 `speaker` 字段（大小写不敏感匹配，兼容旧版 `UNKNOWN_XX` 格式），并调用 `update_txt_files_speaker()` 扫描 `text_backups/` 目录下所有 `.txt`/`.json` 文件中的说话人标签。两种机制并存：显示层映射覆盖所有 Web UI 展示，存量回填确保外部文件（TXT/JSON）中的标签也同步更新。详见 §8.1.4
- **标注校准 (v2.20)**（应对自动标注可能出错，需手工纠正）：
  - **改标为他人**：已标注的簇可改标为另一个姓名——`assign_cluster_name` 覆盖标注，回填匹配串为**原姓名**（此前已回填到 transcripts / 文本备份）
  - **改回未知**：已标注的簇可改回 `unknown_XXXX`——`unassign_cluster_name` 清空 `assigned_name`，回填到簇的**原编号**（label）。**编号是簇的稳定身份、永不改变**，改回后沿用原编号（如 `unknown_0005`），不产生新编号；该簇重新出现在「标注为某人」列表，可随时再标注。标注→改回→再标注全程可逆，`unknown_XXXX` 始终代表同一个声音簇
  - 两种校准均提供**两步确认**（防误操作），回填规则与标注一致（同步 `transcripts` 表 + 文本备份）；人物档案保留已有资料不清空（见 FR-010）

#### FR-004: 语音识别 (ASR)
- **优先级**: P0
- **描述**: 将语音转录为文字
- **精度动态分配（v2.58 起，v2.65 调阈值，v2.66 解除语音时长限制）**: ASR 加载精度默认按**决策时刻可用内存**自动选择——可用内存 ≥ 12GB（v2.65 由 13.5GB 下调；16GB 机器实测决策时可用内存 12.7-13.3GB）→ **FP32**（快，oneDNN 优化）；否则 → **bf16**（稳，内存约减半）；**不再限制语音时长**（v2.66 解除 v2.49 遗留的"语音 ≤30 分钟"上限——语音裁剪 + 段长封顶后 ASR 峰值由模型本身决定、与语音总量弱相关，内存检查已是唯一护栏）；可用环境变量覆盖（`ASR_TORCH_DTYPE` / `ASR_FP32_MIN_AVAIL_MB`）
- **转录粒度（v2.53/2.54 原则）**: 转录行**不严格按说话人切分**——效率优先（处理时长短）优先于逐说话人粒度；ASR 前会把时间相近的相邻段合并（间隔 ≤1.5s、合并后段长 ≤15s（v2.56 由 60s 收紧），可配置），合并行归属起始段说话人。声纹身份由**声纹簇**承载、不受转录行粒度影响，声纹标注可通过单人录音等其他途径完成（单人录音 → 一个分离说话人 → 一个簇/ID，见 FR-003-CLUSTER）
- **模型**: Qwen3-ASR-1.7B（加载精度默认 **auto 动态分配**——决策时刻可用内存 ≥12GB 走 FP32（oneDNN 优化、峰值 ~12-13GB），否则 bf16（峰值 ~5.5GB），见上方"精度动态分配"；唯一 ASR 模型，无备用快速模型）
- **语言**: 中文为主，支持 30 种语言与 22 种中文方言自动识别
- **精度目标**: 中文 WER < 6%
- **推理后端**: Transformers (本地离线，FP32/bf16 按内存自动)；不依赖 GPU/vLLM
- **输出**: 文字 + 置信度
- **注意**: 模型加载与推理的工程实现细节见 [TDD §3.4](./TDD_local_asr_system.md#34-asr--qwen3-asr-17b)

#### FR-005: 时间戳计算与存储 (核心功能)
- **优先级**: P0
- **描述**: 将各阶段的时间偏移量计算为**绝对时间戳**，并持久化存储
- **计算逻辑**（在**应用层**计算后随 INSERT 写入数据库，不依赖数据库触发器）:
  ```
  absolute_start = recording_start_time + segment_start_offset
  absolute_end   = recording_start_time + segment_end_offset

  示例:
    recording_start_time = 2026-07-31 14:30:52 (+08:00)
    segment_start_offset = 125 秒
    segment_end_offset   = 142 秒

    absolute_start = 2026-07-31 14:32:57
    absolute_end   = 2026-07-31 14:33:14
  ```
- **精度要求**: 秒级 (保留 3 位小数)
- **时区处理**: **统一使用北京时间 (Asia/Shanghai, UTC+8)**，不随系统时区变化
- **存储格式**: ISO 8601 (`2026-07-31T14:32:57+08:00`)

#### FR-006: 数据持久化
- **优先级**: P0
- **描述**: 存储转录结果，支持按时间检索
- **数据库**: SQLite (`/home/kevin/asr_sys_local/audio_archive/transcripts.db`)
- **字段** (含时间戳相关)：
  | 字段 | 类型 | 说明 |
  |------|------|------|
  | `id` | INTEGER PK | 自增 ID |
  | `source_file` | TEXT | 原始音频文件名（归档前） |
  | `file_hash` | TEXT | 音频内容 SHA-256（去重 + 审计） |
  | `archive_name` | TEXT | 归档后的有机文件名 (FR-001-AR) |
  | `recording_start_time` | TIMESTAMP | **录音开始绝对时间** (ISO 8601) |
  | `processed_at` | TIMESTAMP | 处理完成时间 |
  | `segment_start_offset` | REAL | 片段开始偏移 (秒，相对于音频起点) |
  | `segment_end_offset` | REAL | 片段结束偏移 (秒) |
  | `absolute_start_time` | TIMESTAMP | **绝对开始时间** (应用层计算，见 7.1) |
  | `absolute_end_time` | TIMESTAMP | **绝对结束时间** (应用层计算) |
  | `speaker` | TEXT | 说话人标签（注册人姓名 / `unknown_XXXX` 编号，原文存储；UI 显示层映射为标注姓名；标注确认时同步回填存量记录，见 FR-003-CLUSTER） |
  | `speaker_match_score` | REAL | 声纹匹配得分 (0-1)，未匹配为空 |
  | `text` | TEXT | 转录文字 |
  | `audio_duration` | REAL | 音频总时长 (秒) |
  | `confidence` | REAL | 置信度 (0-1) |
  | `language` | TEXT | 语言代码 |
  | `audio_path` | TEXT | 音频文件路径（归档后） |
  | `transcript_path` | TEXT | 文本备份路径 |
- **索引**: `source_file`, `speaker`, `recording_start_time`, `absolute_start_time`, `text` (FTS)
- **约束**: `absolute_start_time` 和 `absolute_end_time` 由应用层计算写入，数据库触发器禁止后续 UPDATE（见 7.1）
- **文本备份**: `/home/kevin/asr_sys_local/audio_archive/text_backups/YYYY-MM/` (Organic / JSON)

#### FR-007: 时间戳输出格式 (Organic 格式)
- **优先级**: P0
- **描述**: 文本备份必须包含多层时间戳信息
- **ASR 文本清洗**: 去除 Qwen3-ASR 输出的特殊 token（如 `<|system|>`、`<|user|>`、`<|assistant|>` 等），保留纯文字内容。清洗逻辑见 [TDD §3.4](./TDD_local_asr_system.md#34-asr--qwen3-asr-17b)
- **Organic 格式规范**:
  ```
  # 转录记录
  # 源文件: meeting_20260731_143052.wav
  # 归档文件: 2026-07-31-143052-153052.wav
  # 录音开始时间: 2026-07-31 14:30:52 (+08:00)
  # 处理时间: 2026-07-31 21:45:00
  # 音频总时长: 1800.0秒
  # ==================================================

  [2026-07-31 14:30:57 - 2026-07-31 14:31:12] 我 (0.87):
  # ↑ 绝对时间戳 (file_time + offset)，括号内为声纹匹配得分
    大家好，我是项目经理小王...

  [00:00:05 - 00:00:20] 我 (0.87):
  # ↑ 相对偏移 (可选，用于快速定位音频播放器)
    大家好，我是项目经理小王...
  ```
- **格式选项** (用户可配置)：
  - `absolute_only`: 仅显示绝对时间 (默认)
  - `relative_only`: 仅显示相对偏移
  - `both`: 同时显示绝对时间和相对偏移
  - `human_readable`: "7月31日 14:32" 格式

#### FR-008: 系统看板 (Web Dashboard)
- **优先级**: P0
- **描述**: 用户通过浏览器访问 ThinkPad 的局域网 IP 或 Tailscale IP 即可查看系统实时状态、处理历史、声纹库和归档文件。设计原则：**打开即知全貌**；暖纸灰底、白底面板、信息分段清晰
- **技术**: Streamlit，监听 `0.0.0.0:8501`（systemd 用户服务 `asr-webui.service` 常驻运行、开机自启）
- **浏览器访问环境**:
  - 地址格式：`http://<ThinkPad当前IP>:8501`（端口 8501）
  - 当前示例：`http://<ThinkPad当前IP>:8501`；Tailscale `http://<ThinkPad-Tailscale-IP>:8501`
  - **注：以上均为示例地址——ThinkPad 随网络环境更换 IP，实际使用时请替换为 ThinkPad 当前的真实地址**
  - 建议加入浏览器书签，一键打开
  - **访问控制（v2.62）**: 端口 8501 由 ufw 限制为**仅放行 Tailscale 设备 IP（精确地址，本机维护）与白名单设备 IP**，其余来源一律拒绝（ufw `Default: deny (incoming)` 兜底）；白名单可在「访问控制」页直接增删、可带描述（见 FR-008-A）。新增设备：① 安装/开启 Tailscale（推荐，办公室/家里通用，访问 `http://<ThinkPad-Tailscale-IP>:8501`）；② 或在「访问控制」页添加设备 IP 与描述（手机随机 MAC 需关闭该 Wi-Fi 的"私有地址"才能在路由器侧稳定绑定）
- **部署环境（SSH）**:
  - SSH 地址：`ssh kevin@<ThinkPad当前IP>`（端口 22，默认）；部署脚本内置默认地址（本机维护，不随仓库发布）
  - 代码目录：`/home/kevin/asr_sys_local/asr-local/`；数据目录：`/home/kevin/asr_sys_local/audio_inbox/`（收件箱）、`/home/kevin/asr_sys_local/audio_archive/`（归档与数据库）
  - 部署命令：`bash deploy_webui.sh`；ThinkPad 更换网络后：`ASR_REMOTE_HOST=kevin@<新IP> bash deploy_webui.sh`
  - **注：IP 需替换为 ThinkPad 当前的真实地址**
- **状态机**: 看板顶部状态带固定显示 3 态（空闲/处理中/处理失败），当前态高亮；状态由处理流程写入 `status.json`、pipeline 通过 `status_cb` 上报阶段共同驱动，WebUI 预启动写入 `_write_status_prelaunch()` 防止窗口期误判，详见 §8.1.1
- **手动处理入口**: 概览页顶部「收件箱 · 手动处理」面板，见 FR-008-M
- **页面结构**: 5 个页面，顶部用分段控件（`st.segmented_control`）切换，每个页签是独立矩形区块、**宽度随文字自适应，窄窗口自动折行（v2.62）**；v2.38 起页首与页签**合并为顶部锁定导航条**，同排展示并整条吸顶（详见 §8.2）；v2.61 起北京时间从顶栏移出，改为首页第一个面板
  - 页 1 — 状态概览（默认首页；含北京时间面板）
  - 页 2 — 处理记录
  - 页 3 — 数据库
  - 页 4 — 文件归档
  - 页 5 — 访问控制（见 FR-008-A）
- **说话人筛选器**: 处理记录页（页 2）和搜索页（页 4）的说话人筛选器使用 `speaker_display_map()` 显示标注后的姓名（如"本人""家人"），而非原始 `unknown_XXXX` 编号，使用户可直观选择已标注的说话人进行筛选
- **数据刷新**: Streamlit 交互式刷新（切换页面 / 点控件即触发重跑），概览页每 15 秒自动刷新（处理中或处理失败时）或每 10 分钟自动刷新（空闲时），通过 `streamlit_autorefresh` 实现（可选依赖）
- **代码部署**: 修改代码后运行 `bash deploy_webui.sh` 一键部署（自动更新页脚部署时间戳 → 编译 → scp 上传 → 重启 `asr-webui.service` → 验证）。部署后浏览器强制刷新（Ctrl/Cmd+Shift+R），核对页脚「部署时间」+ 时间戳确认新代码已生效

#### FR-008-M: 手动触发收件箱处理
- **优先级**: P0
- **描述**: 看板概览页顶部设「收件箱 · 手动处理」面板，用户放入音频后**手动点击按钮**触发处理，取代不可靠的自动监听
- **动机**: ① 自动监听看不到收件箱子文件夹里的文件，导致放入的音频不被处理；② 拷贝大文件过程中文件大小持续变化，"一检测到就处理"存在竞态；③ 手动触发让"何时开始"完全由用户掌控，不依赖系统服务常驻
- **面板内容**:
  - 递归扫描收件箱（含子文件夹），列出待处理文件（相对路径 + 大小，同 stem 多格式合并为一行标注"多格式"）
  - 「🧹 准备处理收件箱」按钮（左侧）+「▶ 开始处理收件箱」主按钮（右侧），并排展示（v2.17）
- **准备处理收件箱 (v2.17)**:
  - 点击后执行两个动作，为新一轮处理做好准备：
    1. **归档旧错误**：将 `error/` 根目录下的错误文件（`.error.txt` 日志 + 失败音频）全部移入 `error/archived/`（文件名附加原文件创建时间戳，避免重名），使根目录只保留新一轮处理产生的错误，新旧不混淆
    2. **解锁**：若存在残留锁文件（上次异常退出遗留的陈旧锁）则删除，确保新一轮处理能正常获取锁；正在处理中（新鲜锁）时保留锁并提示
  - 逻辑顺序：先「准备处理收件箱」，再「开始处理收件箱」，动作更顺
- **处理执行**:
  - 逐个处理所有待处理文件，非交互模式（时间冲突自动接受系统建议）
  - 锁文件防止重复触发（6 小时陈旧自动失效），状态实时写入 `status.json` 供看板同步
  - 处理完成后清理收件箱空文件夹；失败时主文件与兄弟文件移入 `error/` 目录，并生成 `.error.txt` 日志说明原因（见 FR-001-AR 失败归档）
- **状态上报**: 详见 [TDD §1.3](./TDD_local_asr_system.md#13-状态机)
- **按钮禁用条件**: 锁文件存在、状态为处理中/处理失败、或收件箱无待处理文件时禁用
- **进度反馈**: 处理中概览页每 15 秒自动刷新，状态带、6 阶段进度条（已完成/进行中/未处理三态显示）、待处理数实时更新
  - 文件名显示已修复，不再出现问号乱码问题

#### FR-008-A: 访问控制页（IP 白名单与端口说明）
- **优先级**: P1
- **描述**: 第 5 个页签「访问控制」，含两个面板：
  1. **IP 白名单 · 网页访问**：展示 8501 端口当前放行来源（Tailscale 设备 IP 固定放行、设备 IP 可增删），用户可直接新增/移除设备 IP（可带描述），修改即时生效
  2. **端口说明**：列出 ThinkPad 对外服务端口与用途——SSH 22（开发机远程管理/部署）、Web UI 8501（浏览器访问）
- **实现约束**: 白名单操作经 sudoers NOPASSWD 限定的管理脚本（`/usr/local/sbin/asr-webui-fw.sh`）调用 ufw，仅允许操作 8501 端口、仅接受合法 IPv4、描述 ≤48 字符；固定放行项（Tailscale 设备 IP、回环地址，精确地址本机维护）不可删除
- **恢复手段**: 设备不在白名单时无法打开本页——使用 SSH 登录 ThinkPad 或改走 Tailscale 恢复（见 FR-008-T）

#### FR-008-T: 跨设备安全访问 (Tailscale)
- **优先级**: P0
- **描述**: 用户本人的多台设备（开发机 / 家用电脑 / 运行节点）组成 Tailscale 虚拟局域网 (WireGuard 加密)，通过运行节点的 Tailscale 地址直接访问 Web UI
- **要点**:
  - 数据**只存于 ThinkPad**，其他设备仅浏览器访问，不落地副本
  - 传输全程端到端加密，不经第三方中转服务器（NAT 打洞成功后为 P2P 直连）
  - ThinkPad 重启后 Tailscale 与流水线服务均以 systemd 自启
  - 使用 Tailscale ACL 限制只有用户本人的设备可访问
  - **端口 8501 防火墙策略（v2.60 起 / v2.62 精确 IP）**: ufw 仅放行 Tailscale 设备 IP（精确地址，本机维护）+ 白名单设备 IP；白名单在「访问控制」页直接增删、可带描述（FR-008-A），Tailscale 设备 IP 固定放行
- **备选**: 若未来需要离线本地副本，可叠加 Syncthing 同步文本备份（本版本不实现）

#### FR-008-S: 转录全文搜索（含中文分词修复）
- **优先级**: P0
- **描述**: 搜索·文件页提供关键词全文检索，可叠加说话人、时间范围筛选（界面见 §8.2 页 4）
- **中文分词修复**: 原 `transcripts_fts` 使用 FTS5 unicode61 分词器不识别中文词，改用 `transcripts_fts2` + jieba 分词方案（查询方式见 §8.1.4 关键词全文检索）

### 4.2 扩展功能 (v2.0)

#### FR-009: 说话人库管理（基于声纹簇机制）
- 声纹簇重命名（`unknown_XXXX` → 真实姓名）、合并（同一人被误分为多个簇时归并为一个，向量按样本数加权平均）、删除误识别簇
- 标注/合并后，处理记录与搜索页的说话人**经显示层映射自动更新**。已实现的**标注**同步回填 `transcripts.speaker` 存量记录（见 FR-003-CLUSTER）；**合并/删除误识别簇**（规划中）同样经显示层映射更新，数据库原文不回填
- **时间戳不受影响**

#### FR-010: 人物档案
- **优先级**: P1
- **描述**: 为识别出的人建立人物档案（姓名、性别、出生年、与我的关系、备注），与声纹解耦，通过姓名关联
- **动机**: 声纹只回答"声音是谁"，档案回答"这个人是谁"——关系、备注等人文信息不应混在声纹库里
- **人物档案表** (`persons`，见 7.1)：
  - `person_name` 主键（唯一，中文/英文/混杂均可、不含空格）、`gender`、`birth_year`、`relation`（与我的关系）、`note`
- **关联方式**: 与声纹解耦，通过 `speaker_clusters.assigned_name ↔ persons.person_name` 按姓名关联；标注声纹簇时自动 `upsert_person` 建档（见 FR-003-CLUSTER）
- **标注不覆盖已有档案 (v2.19)**: 「确认标注并回填」时，若 `persons` 表中已存在该姓名，**保留**已填写的性别/出生年/关系/备注，仅当档案不存在时才自动建档（避免"标注后档案只剩姓名"）
- **Web 界面**: 声纹库·数据库页「人物档案」面板，展示档案列表 + 新增/编辑表单（姓名/性别/出生年/与我的关系/备注）

#### FR-015: 实时流处理
- 从麦克风直接录入，实时转录
- 时间戳使用系统当前时间（北京时间）

#### FR-011: 批量导入历史文件
- 递归扫描目录，批量处理历史音频
- 支持断点续传
- **时间戳提取策略可批量配置**（批量模式跳过逐一确认）

#### FR-012: 导出集成
- 导出为 Markdown、Notion、Obsidian 格式
- 导出为字幕文件 (.srt, .vtt)
- **所有导出格式必须包含完整时间戳**

#### FR-013: 时间戳验证与审计
- **时间戳不可篡改**: `absolute_start_time` / `absolute_end_time` 在应用层计算写入后，数据库触发器禁止 UPDATE
- **审计日志**: 记录所有对时间戳相关字段的修改尝试
- **哈希校验**: 对原始音频文件计算 SHA-256（复用 FR-001 去重哈希），确保音频未被篡改后时间戳仍有效

---

## 5. 非功能需求

### 5.1 性能需求 (按 i5-10210U 纯 CPU 实测口径)

| 指标 | 目标 | 说明 |
|------|------|------|
| VAD 处理速度 | > 100× 实时 | 1 小时音频 < 36 秒 |
| Diarization 速度 | > 2× 实时 (CPU) | 1 小时音频约 20~30 分钟；指定说话人数可更快；v2.31 起按 VAD 段拼接切除静音，实际耗时随语音占比进一步缩短 |
| 声纹匹配 | < 10 秒/说话人 | 向量提取 + 余弦比对，开销极小 |
| ASR 速度 (Qwen3-1.7B, CPU) | FP32 约 1.11× 实时（v2.32 单段实测）；bf16 约 3.14× 实时 | 逐段转录有固定开销（FP32 约 25-33s/段、bf16 约 2.5-3 倍，v2.53 实测），实际速度 = 段数 × 每段开销 + 内容解码；段合并上限 15s 下，8 分钟语音 FP32 实测 ~15-25 分钟，bf16 ~55-60 分钟 |
| 整体流水线 | < 2.5 小时/小时音频 | 分离+识别+转录串行，可后台/夜间批处理 |
| 内存峰值 | < 12GB（FP32 实测 11.8-12.5GB；bf16 约 5.5GB） | 16GB 系统留 ~4GB 余量（v2.32 放宽，原 6GB）；决策时可用内存 <12GB 自动走 bf16 |
| 数据库查询 | < 100ms | 10 万条记录内 |
| 时间戳计算 | < 1ms/条 | 纯数学运算，无 I/O |

> 注: 官方 vLLM 高吞吐数据均为 GPU 场景，与本机 (无独显) 无关；上表为纯 CPU 推理的保守口径。

> **⚠️ 性能数据边界（v2.55）**：本表为 **CPU 部署实测（i5-10210U）**，仅作本机回归基线；GPU 部署需重新实测，绝对数值不跨硬件外推。
> **配置不跨硬件迁移**：当前系统受制于 CPU 和内存的限制，性能有限，当前的配置和使用方法，**不应该完整迁移到 GPU 系统**，而是要修改参数和适配（如加载精度、段合并/批处理、内存编排策略）；要根据实际的系统再调参数，达到最优效果。

### 5.1.1 内存编排要求 (P0)
- 流水线**按阶段串行加载、用完即卸**：Diarization 完成后释放其内存，再加载 ASR 模型
- 阶段卸载后**归还空闲内存给系统**（`malloc_trim`，v2.66）：torch CPU 内存池不主动归还峰值，不归还会让下一文件的精度决策看到虚低可用内存（实测 FP32 卸载后滞留 ~2GB、下一文件误走 bf16）
- 目标: 任意时刻内存峰值 < 12GB（v2.32 放宽，原 6GB；1.7B FP32 实测 11.8GB），为系统与其他应用留 ~4GB 余量
- 模型加载超时、超时处理等工程实现细节见 [TDD §2.3-2.4](./TDD_local_asr_system.md#23-模型加载超时机制)

### 5.2 可靠性需求

| 需求 | 说明 |
|------|------|
| 断点续传 | 处理中断后可从断点恢复 |
| 错误隔离 | 单文件处理失败不影响其他文件 |
| 失败归档 | 处理失败时，原始音频文件与同 stem 兄弟文件**移入 `error/` 目录**，并在 `error/` 下生成带时间戳的 `.error.txt` 日志文件说明原因；文件保留供排查，用户可手动移回收件箱重新处理 |
| 日志记录 | 完整日志保存到 `/home/kevin/asr_sys_local/audio_archive/pipeline.log` |
| **时间戳一致性** | 即使处理中断重试，同一音频的时间戳结果必须一致 |

### 5.3 安全与隐私

| 需求 | 说明 |
|------|------|
| 完全离线 | **v2.18 修正**：所有模型（Silero VAD / PyAnnote Diarization / 声纹 / Qwen3-ASR）首次下载至本地后，运行时**完全离线**，不与 GitHub/HuggingFace 交互：Silero VAD 用 `source='local'` 加载本地缓存，PyAnnote 用 `HF_HUB_OFFLINE=1`，Qwen3-ASR 直接加载本地自定义解压目录 `models/Qwen3-ASR-1.7B-hf/`（v2.18 修复：`local_files_only=True` 只认 hub 缓存格式，匹配不到自定义目录会误判"本地缺失"而回退联网失败）；仅当本地模型缺失时才联网下载一次（依赖 `HF_TOKEN`） |
| 数据不出个人设备 | 音频、转录结果均只存于 ThinkPad；跨设备访问走 Tailscale 加密隧道，无云端副本 |
| 模型权重本地化 | 所有模型权重存储在 `/home/kevin/asr_sys_local/asr-local/models/`（通过 `HF_HOME` 环境变量统一指向），避免写入 `~/.cache/huggingface/` |
| 访问控制 | 数据库文件权限 600；Tailscale ACL 仅放行用户本人设备；WebUI 端口 8501 仅放行 Tailscale 设备 IP + 白名单设备 IP——白名单在 WebUI「访问控制」页直接增删、可带描述（v2.62），Tailscale 设备 IP 固定放行 |
| **时间戳防篡改** | 绝对时间戳应用层写入后，数据库触发器禁止修改 |

### 5.4 可维护性

| 需求 | 说明 |
|------|------|
| 模块化设计 | VAD / Diarization / 声纹匹配 / ASR / DB / 时间戳计算 / 归档 独立模块 |
| 配置化 | 所有参数通过 `config/settings.py` 配置文件调整 |
| 日志分级 | DEBUG / INFO / WARNING / ERROR |
| **时间戳调试** | 单独的时间戳计算日志，便于排查偏移问题 |

---

## 6. 技术方案

### 6.1 技术栈

| 层级 | 技术选型 | 理由 |
|------|---------|------|
| 语言 | Python 3.12+ | 生态丰富，模型支持好 |
| VAD | Silero VAD (snakers4/silero-vad) | 模型轻量（~1MB），加载速度快、延迟低 |
| Diarization | PyAnnote 4.x + speaker-diarization-3.1 模型 | 开源 SOTA，中文会议 DER ~12% |
| 声纹向量 | PyAnnote Embedding | 与 Diarization 同生态，向量比对开销小 |
| ASR | Qwen3-ASR-1.7B | 中文最强梯队，长句/中英混合/口音场景表现可靠；唯一 ASR 模型 |
| 数据库 | SQLite | 零配置，足够单机使用 |
| Web UI | Streamlit | 快速开发，Python 原生 |
| 触发方式 | 手动触发（process_inbox.py） | Web 看板按钮触发，递归扫描收件箱含子文件夹 |
| 跨设备访问 | Tailscale (WireGuard) | 端到端加密组网，无云端副本 |
| 时间处理 | `python-dateutil` + `zoneinfo` | 时区、ISO 8601 解析 |
| 模型下载 | HuggingFace Hub（`step2_download_models.sh` 用 Python snapshot_download 拉取到与运行时一致的目录）；直连卡死时经代理或 `HF_ENDPOINT=https://hf-mirror.com` 兜底（v2.46 实测可用） | 解决国内网络访问问题 |



## 7. 数据模型

### 7.1 数据库表结构 (时间戳增强版)

```sql
-- ====== 转录表 ======
CREATE TABLE transcripts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file             TEXT NOT NULL,           -- 原始音频文件名（归档前）
    file_hash               TEXT NOT NULL,           -- 音频内容 SHA-256（去重 + 审计）

    -- ====== 时间戳核心字段 ======
    recording_start_time    TEXT NOT NULL,           -- 录音开始绝对时间 (ISO 8601, +08:00)
    processed_at            TEXT NOT NULL,           -- 处理完成时间 (ISO 8601)

    -- 片段偏移 (相对于音频起点，秒)
    segment_start_offset    REAL NOT NULL,           -- 片段开始偏移
    segment_end_offset      REAL NOT NULL,           -- 片段结束偏移

    -- 绝对时间（应用层计算后随 INSERT 写入；下方触发器禁止后续 UPDATE）
    absolute_start_time     TEXT NOT NULL,           -- 绝对开始时间
    absolute_end_time       TEXT NOT NULL,           -- 绝对结束时间

    -- ====== 业务字段 ======
    speaker                 TEXT NOT NULL,           -- 说话人标签（注册人姓名 / unknown_XXXX 编号）
    speaker_match_score     REAL,                    -- 声纹匹配得分 (0-1)，未匹配为空
    text                    TEXT NOT NULL,           -- 转录文字

    -- 元数据
    audio_duration          REAL,                    -- 音频总时长 (秒)
    confidence              REAL,                    -- 置信度 (0-1)
    language                TEXT DEFAULT 'zh',        -- 语言代码

    -- 文件路径
    archive_name            TEXT,                    -- 归档后的有机文件名 (FR-001-AR)
    audio_path              TEXT,                    -- 音频文件路径（归档后）
    transcript_path         TEXT,                    -- 文本备份路径

    -- 审计
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ====== 声纹库表 ======
CREATE TABLE voiceprints (
    person_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    person_name             TEXT NOT NULL UNIQUE,    -- 姓名/称呼（如：本人、家人；实际称呼由用户自行标注）
    is_owner                INTEGER DEFAULT 0,       -- 1 = 用户本人（仅一条，CLI 校验：已存在本人则拒绝）
    embedding               BLOB NOT NULL,           -- 声纹向量
    sample_audio_path       TEXT,                    -- 录入样本路径
    enrolled_at             TEXT NOT NULL,           -- 录入时间 (ISO 8601)
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ====== 声纹簇表 (FR-003-CLUSTER) ======
CREATE TABLE speaker_clusters (
    cluster_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    label                   TEXT NOT NULL UNIQUE,    -- unknown_0001 式四位编号，全局递增、不复用
    embedding               BLOB NOT NULL,           -- 聚合声纹向量 (float32)，匹配命中后增量平均持续学习
    assigned_name           TEXT,                    -- 用户标注的姓名，NULL = 纯 unknown
    skip_label              INTEGER DEFAULT 0,       -- 1 = 不标注（v2.43：保持原编号，不参与标注流程）
    sample_count            INTEGER DEFAULT 1        -- 累积样本数（新建簇默认 1，增量平均学习用）
);

-- ====== 人物档案表 (FR-010) ======
CREATE TABLE persons (
    person_name             TEXT PRIMARY KEY,        -- 唯一，中文/英文/混杂均可、不含空格
    gender                  TEXT,                    -- 性别
    birth_year              INTEGER,                 -- 出生年
    relation                TEXT,                    -- 与我的关系
    note                    TEXT                     -- 备注
);

-- ====== 索引 ======
CREATE INDEX idx_source              ON transcripts(source_file);
CREATE INDEX idx_file_hash           ON transcripts(file_hash);
CREATE INDEX idx_speaker             ON transcripts(speaker);
CREATE INDEX idx_recording_time      ON transcripts(recording_start_time);
CREATE INDEX idx_absolute_start      ON transcripts(absolute_start_time);
CREATE INDEX idx_time_range          ON transcripts(absolute_start_time, absolute_end_time);

-- 全文搜索索引 (用于关键词搜索)
CREATE VIRTUAL TABLE transcripts_fts USING fts5(
    text,
    content='transcripts',
    content_rowid='id'
);

-- 中文分词全文索引 (FR-008-S)：unicode61 分词器不识别中文词，
-- 故新增独立 FTS 表，内容由应用层 (src/fts.py) 用 jieba 分词后以空格连接写入；
-- 查询词同样 jieba 分词 (多 token AND)，MATCH 失败退回 LIKE 兜底
CREATE VIRTUAL TABLE transcripts_fts2 USING fts5(text);

-- ====== 触发器：禁止修改绝对时间戳 ======
-- 注意: absolute_start_time / absolute_end_time 由应用层计算后随 INSERT 写入，
--       不使用 BEFORE INSERT 触发器计算（BEFORE INSERT 时新行尚不存在，
--       在触发器内 UPDATE 自身无效且会与保护触发器冲突）。
CREATE TRIGGER trg_protect_timestamp
BEFORE UPDATE OF absolute_start_time, absolute_end_time ON transcripts
FOR EACH ROW
BEGIN
    SELECT RAISE(ABORT, '绝对时间戳不可手动修改');
END;

-- ====== 触发器：同步 FTS 索引 ======
CREATE TRIGGER trg_fts_insert
AFTER INSERT ON transcripts
BEGIN
    INSERT INTO transcripts_fts(rowid, text) VALUES (NEW.id, NEW.text);
END;

CREATE TRIGGER trg_fts_update
AFTER UPDATE OF text ON transcripts
BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, text)
    VALUES ('delete', OLD.id, OLD.text);
    INSERT INTO transcripts_fts(rowid, text) VALUES (NEW.id, NEW.text);
END;

CREATE TRIGGER trg_fts_delete
AFTER DELETE ON transcripts
BEGIN
    INSERT INTO transcripts_fts(transcripts_fts, rowid, text)
    VALUES ('delete', OLD.id, OLD.text);
END;
```

### 7.2 时间戳格式规范

| 场景 | 格式 | 示例 |
|------|------|------|
| 数据库存储 | ISO 8601 | `2026-07-31T14:30:57.000+08:00` |
| Organic 输出 | 人类可读 | `2026-07-31 14:30:57` |
| SRT 字幕 | 已移除，经评估非必要 | — |
| JSON 导出 | ISO 8601 | `"2026-07-31T14:30:57.000+08:00"` |
| 归档文件名 | 有机格式 | `2026-07-31-143052-153052.wav` |
| 文件名提取 | 多种模式 | `2026-08-02_19_30_25`, `recording_20260731_143052`, `recording-20260731-143052`, `meeting-2026-07-31-14-30-52`, `20260731-143052-recording`, `voice_note_20260731T143052Z` |

### 7.3 文本备份格式 (Organic - 绝对时间戳版)

```
# 转录记录
# 源文件: meeting_20260731_143052.wav
# 归档文件: 2026-07-31-143052-153052.wav
# 录音开始时间: 2026-07-31 14:30:52 (+08:00)
# 处理时间: 2026-07-31 21:45:00
# 音频总时长: 1800.0秒
# 时间戳格式: 绝对时间 (ISO 8601, 北京时间)
# ==================================================

[2026-07-31 14:30:57 - 2026-07-31 14:31:12] 我 (0.87):
  大家好，我是项目经理小王，今天我们讨论一下 Q3 的项目进度。

[2026-07-31 14:31:17 - 2026-07-31 14:31:34] 家人 (0.82):
  好的，我先汇报一下开发组的进展。目前前端部分已经完成了 80%。

[2026-07-31 14:31:34 - 2026-07-31 14:31:45] unknown_0001:
  进度比预期慢了一些，预计什么时候能完成联调？
```

---

## 8. 用户界面

### 8.1 看板信息分类与数据来源

系统看板需要从多个来源采集信息，按来源分为以下类别：

#### 8.1.1 系统状态信息（来源：操作系统 + 进程状态 + 状态机）

| 信息项 | 来源 | 刷新方式 |
|--------|------|---------|
| 当前时间（北京时间） | 系统时钟 | 页面加载时 |
| 服务运行时长 | `/proc/uptime` | 页面加载时 |
| **当前状态（3 态状态机）** | `systemctl is-active` + `status.json` 推导（见下） | 每次刷新 |
| CPU 使用率 | `/proc/stat`（读两次取差值） | 每次刷新 |
| 内存使用率 | `/proc/meminfo` | 每次刷新 |
| 磁盘使用率 | `os.statvfs("/")` | 每次刷新 |
| 当前处理中的文件与阶段 | `status.json` 的 `current_file` / `stage` | 每次刷新 |
| 排队文件数 | `status.json` 的 `pending_count` | 每次刷新 |
| 上次处理结果 | `status.json` 的 `last_completed_file/at/result` | 每次刷新 |
| 处理起始时间 | `status.json` 的 `processing_start_time`（进入处理中状态的时间戳） | 每次刷新 |
| 当前步骤起始时间 | `status.json` 的 `stage_start_time`（当前阶段开始时间戳） | 每次刷新 |

**状态机**: 看板顶部状态带固定显示 3 态（空闲/处理中/处理失败），当前态高亮。状态推导逻辑与详细实现见 [TDD §1.3](./TDD_local_asr_system.md#13-状态机)。

| 状态 | 用户看到的含义 |
|------|---------------|
| **空闲** | 无待处理或处理已完成，等待放入音频 |
| **处理中** | 正在转录：加载音频 → VAD → 说话人分离 → 声纹匹配 → ASR 转录 → 归档 |
| **处理失败** | 上次处理出错，用户可查看错误信息后重试 |

#### 8.1.2 处理统计信息（来源：SQLite 数据库 `transcripts` 表）

| 信息项 | SQL 查询 |
|--------|---------|
| 音频数量（首页"处理成果"） | `SELECT COUNT(DISTINCT file_hash) FROM transcripts`（按去重音频文件数，与归档音频数一致） |
| 累计转录片段数（处理记录页"共 N 条"） | `SELECT COUNT(*) FROM transcripts` |
| 累计音频总时长（秒，首页换算小时显示） | `SELECT COALESCE(SUM(d),0) FROM (SELECT MAX(audio_duration) AS d FROM transcripts GROUP BY file_hash)`（audio_duration 存的是整文件时长且每个片段行重复，须按文件去重后再求和） |
| 最近处理记录 | `SELECT ... ORDER BY processed_at DESC LIMIT 5` |

#### 8.1.3 声纹库信息（来源：SQLite 数据库 `voiceprints` / `speaker_clusters` / `persons` 表）

| 信息项 | SQL 查询 |
|--------|---------|
| 标注声纹数 | `SELECT COUNT(DISTINCT assigned_name) FROM speaker_clusters WHERE assigned_name IS NOT NULL`（按唯一姓名去重——如 unknown_0001/0002/0003 都标注为同一人，计数为 1 而非 3） |
| 录入人员数 | `SELECT COUNT(*) FROM persons`（人物档案人数） |
| 声纹列表 | `SELECT person_name, enrolled_at, ... FROM voiceprints` |
| 声纹簇列表（编号/标注为/学习样本数/不标注） | `SELECT label, assigned_name, skip_label, sample_count FROM speaker_clusters`（FR-003-CLUSTER） |
| 人物档案列表 | `SELECT p.person_name, gender, birth_year, relation, note, CASE WHEN EXISTS (SELECT 1 FROM speaker_clusters sc WHERE sc.assigned_name = p.person_name) THEN 1 ELSE 0 END AS has_voiceprint FROM persons p ORDER BY created_at`（FR-010；has_voiceprint 自动判断该人物是否已有声纹簇标注） |

#### 8.1.4 转录记录与搜索（来源：SQLite 数据库 `transcripts` 表 + FTS5 全文索引）

| 信息项 | 查询方式 |
|--------|---------|
| 全部处理记录 | `SELECT ... ORDER BY absolute_start_time DESC LIMIT 200`（默认取最近 200 条，无分页） |
| 按日期筛选 | `WHERE absolute_start_time BETWEEN ? AND ?` |
| 按说话人筛选 | `WHERE speaker = ?` |
| 关键词全文检索 | `WHERE id IN (SELECT rowid FROM transcripts_fts2 WHERE transcripts_fts2 MATCH ?)`——查询词 jieba 分词（多 token AND），MATCH 失败退回 LIKE（FR-008-S） |
| 按时间范围搜索 | `WHERE absolute_start_time BETWEEN ? AND ?` |
| 说话人显示名 | **显示层映射 + 存量数据回填**：`transcripts.speaker` 始终存原始标签；UI 渲染时经 `speaker_clusters.assigned_name` 把 `unknown_XXXX` 映射为标注姓名展示，未标注的仍显示原编号（见 FR-003-CLUSTER） |

#### 8.1.5 数据库结构可视化（来源：SQLite `sqlite_master` 表 + 文件系统）

| 信息项 | 展示方式 |
|--------|---------|
| 数据库表结构 | 展示 `CREATE TABLE` 语句，用户可看到 `transcripts`、`voiceprints`、`speaker_clusters`、`persons`、`transcripts_fts`/`transcripts_fts2` 表定义 |
| 数据库文件大小 | `os.path.getsize(DB_PATH)` |
| 数据库文件路径 | 显示绝对路径 `/home/kevin/asr_sys_local/audio_archive/transcripts.db` |
| 示例数据 | 展示最近 3 条记录的完整字段 |

#### 8.1.6 文件系统浏览（来源：归档目录）

| 信息项 | 展示方式 |
|--------|---------|
| 归档音频文件列表 | 按月份组织，展示目录 `processed_audio/YYYY-MM/` 下的文件 |
| 文本备份文件列表 | 按月份组织，展示目录 `text_backups/YYYY-MM/` 下的 .txt / .json 文件 |
| 错误日志 | `error/` 根目录存放当前批次错误，`error/archived/` 存放历史归档错误；`error/README.txt` 说明目录结构 |
| 数据位置统计 | 数据库大小（KB/MB）、归档音频文件数（按月分目录）、文本备份文件数（txt/json） |
| 文本备份在线预览 | 下拉框选择 `.txt` 文件，在限高滚动预览框中显示全文 |

### 8.2 系统看板布局（UI v2.0）

**设计基调**：KVI 视觉风格——灰阶为基、暖赭 `#b86a48` 作唯一强调色、区块用**带边框面板**（`st.container(border=True)`）分隔，面板头部 = 标题 + 说明 + 分隔线。Streamlit 顶部常驻 header 条整条隐藏；主题色经 `.streamlit/config.toml` 配置，消除 Streamlit 默认鲜红。标题在左上角，英文品牌名 **Local ASR System**（v2.40 起；无装饰色块，纯文字）。

**设计要点**（v2.0 关键决策与理由）：
1. **页签必须像页签**：改用原生 `st.segmented_control` 分段控件（Streamlit < 1.35 自动回退 `st.radio`），不再对 `st.tabs` 做随版本漂移易失效的 CSS 覆盖
2. **区块必须有边界**：每个区块是带边框面板，取代"白底 vs 灰底"的微弱色差，相邻区块边界一眼可见；面板内的统计数字用**纯文本大数字行**（数字 + 灰色标签横排），不用矩形色块——色块在 Streamlit 面板内会被 16px 元素间距顶出边界，且大字号本身就是视觉锚点，符合 KVI"分隔靠间距而非边框"
3. **行高必须锁定**：代码块 / 建表语句统一用单个自定义 `<pre>` 渲染，避免 markdown 段落默认间距叠加导致松散
4. **状态带只读不可点**：3 态状态带是"圆点 + 文字"纯指示器——当前态暖赭实心点 + 加粗黑字，其余灰字空心点，明确不可点击，避免用户误以为可交互切换
5. **布局必须顺应内容**：搜索与文件浏览上下堆叠全宽展示；"就地展开的长内容"一律改为"下拉选择 + 限高滚动预览框"，音频回放改为下拉选一条回放，页面不被撑长
6. **折叠块内字号降一级**：expander 内部文字统一小一级，保持信息层级、避免折叠内容喧宾夺主
7. **页脚部署时间戳**：页脚固定显示 `ASR WebUI · KVI 视觉风格 · 部署时间 YYYY-MM-DD-HH:MM:SS`（v2.62 加"部署时间"前缀，避免与当前时间混淆），部署脚本自动打时间戳，用于强制刷新后核对新代码是否已生效
8. **用户输入统一 HTML 转义**：所有动态文本（文件名、转录内容、SQL）经 `html.escape` 渲染，防注入
9. **面板样式交给原生渲染，CSS 只调底部留白**：面板的边框/背景/圆角全部由 Streamlit 原生 `border=True` 容器绘制，自定义 CSS 仅增加面板内部底部留白
10. **顶部锁定导航条（v2.38 / v2.62）**：页首（品牌标题）与 5 个页签合并为**同一行**（`st.columns` 左品牌右导航），整条 `position: sticky` 吸顶——滚动页面时导航始终可见，任意页可随时切换；页签**宽度随文字自适应、窄窗口自动折行（v2.62）**；吸顶条自带页底色 + 底部细分隔线，留白用 padding 实现（sticky 元素的 margin 区域透明，下层内容会从 margin 处透出）；**北京时间 v2.61 起移出顶栏**，改为首页「北京时间」面板（导航条下方第一个面板，仅首页展示）

用户在浏览器地址栏输入 IP 后直接看到看板首页。顶部导航为**原生分段控件**，5 个选项：状态概览 / 处理记录 / 数据库 / 文件归档 / 访问控制（宽度随文字自适应、窄窗口自动折行，v2.62），选中项高亮，每个选项边界清晰。页脚固定显示「部署时间」+ 时间戳，用于核对部署是否生效。
**v2.38 起**：页首与导航同排组成**顶部锁定导航条**，整条吸顶，滚动时始终可见；**v2.61 起**北京时间移到首页「北京时间」面板（导航条下方第一个面板）。

#### 页 1 — 状态概览（默认首页）

回答：**"现在处于什么状态？有待处理的音频吗？系统是怎么处理音频的？数据放在哪？"**

```
┌──────────────────────────────────────────────────────────────┐
│  Local ASR System                                           │  ← 顶部锁定导航条
│   ┌────────┐┌────────┐┌────────┐┌────────┐┌────────┐        │    （v2.61：品牌+5 个
│   │状态概览 ││处理记录 ││ 数据库 ││文件归档 ││访问控制 │        │     页签宽度自适应、整条吸顶；
│   └────────┘└────────┘└────────┘└────────┘└────────┘        │      时间已移出顶栏）
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 北京时间   2026-08-06 14:30:25 北京时间   ← 首页面板   │   │  ← 导航条下方第一个面板
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ○ 空闲  【● 处理中】  ○ 处理失败                      │  ← 状态带：圆点+文字指示器
│                                                              │     （只读，不可点击）
│  ┌──────────────────────────────────────────────────────┐   │
│  │ ▎正在处理 meeting.wav — ASR 转录　·　队列中还有 2 个文件 │   │  ← 白底+暖赭左边框
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 加载✓ VAD✓ 说话人分离● 声纹○ ASR○ 归档○（6 阶段进度条）│   │  ← 仅处理中时显示
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 处理进度   当前文件：20260802/meeting.wav  ← 面板头部  │   │  ← 仅处理中时显示
│  │ ─────────────────────────────────────────────        │   │
│  │ 任务            起始时间              耗时             │   │  ← 表头（v2.18）
│  │ 总任务          2026-08-02 21:55:00   5 分 32 秒       │   │  ← 总任务：起始黑色，耗时赭红
│  │ 当前步骤·ASR 转录 2026-08-02 21:57:00  3 分 12 秒       │   │  ← 当前步骤：起始黑色，耗时暖赭
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 收件箱·手动处理   1 个待处理              ← 面板头部   │   │
│  │ ─────────────────────────────────────────────        │   │
│  │ 20260802/02_54_03.opus  397 MB（多格式）              │   │  ← 待处理文件列表
│  │ ┌──────────┐┌──────────────┐                          │   │
│  │ │🧹 准备处理││▶ 开始处理收件箱│  两按钮并排（v2.17）     │   │
│  │ └──────────┘└──────────────┘                          │   │
│  │  ← 开始按钮：无待处理/处理中时禁用（type=primary）      │   │
│  │  ← 准备按钮：处理中时禁用；归档旧错误 + 清理残留锁      │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 处理成果   全部历史累计                    ← 面板头部   │   │
│  │ ─────────────────────────────────────────────        │   │
│  │  2 音频数量  1 标注声纹  1 录入人员  0.0 时长(h)        │   │  ← 带边框面板
│  │   （标注声纹=已标注的唯一姓名数——同一个人计 1 次，非按簇计数）  │   │
│  └──────────────────────────────────────────────────────┘   │     纯文本大数字行（无色块）
│                                                              │     大数字 2rem 为视觉锚点
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 系统负担   ThinkPad 实时资源占用                        │   │
│  │ ─────────────────────────────────────────────        │   │
│  │ CPU   ████████░░░░░░░░░░░░░░  30%                     │   │
│  │ 内存  ██████████████░░░░░░░░  35%（5.6/16 GB）         │   │
│  │ 磁盘  ████░░░░░░░░░░░░░░░░░░  18%（剩余 85 GB）        │   │
│  │ 系统已运行 3 天 12 小时                                 │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 数据位置   所有数据只存在 ThinkPad 本机                  │   │
│  │ ─────────────────────────────────────────────        │   │
│  │ 转录数据库  /.../audio_archive/transcripts.db（20 KB）   │   │
│  │ 归档音频    /.../processed_audio/（8 个文件，按月分目录） │   │
│  │ 文本备份    /.../text_backups/（12 个文件，txt/json）│   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ 音频处理流程   4 个模型分工                 ← 面板头部 │   │
│  │ ─────────────────────────────────────────────        │   │
│  │ 01 VAD语音检测→02 说话人分离→03 声纹识别→04 ASR转文字  │   │  ← KVI 横向框图：
│  │  Silero VAD  pyannote-3.1   pyannote   Qwen3-ASR    │   │     灰阶节点+暖赭编号
│  │  snakers4/               embedding    -1.7B          │   │     +细箭头
│  │  silero-vad                                        │   │
│  │ 输入：收件箱音频各格式                                 │   │
│  │ 产出：带时间戳转录文字·归档音频·txt/json·SQLite    │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                              │
│  ASR WebUI · KVI 视觉风格 · 部署时间 2026-08-01-23:05:12     │  ← 页脚部署时间戳
└──────────────────────────────────────────────────────────────┘
```

状态说明（状态带下方，白底 + 暖赭左边框，文字随状态变化）：

| 状态 | 说明文案 |
|------|---------|
| 空闲 | 有待处理时："收件箱有 N 个待处理文件，点击下方「开始处理收件箱」按钮。"；无待处理时："收件箱为空。上次处理：xxx.wav（今天 21:14，成功）" 或 "收件箱为空，等待音频放入…" |
| 处理中 | "正在处理 **文件名** — 当前阶段（加载音频/VAD/说话人分离/声纹匹配/ASR 转录/归档）" + 6 阶段进度条（已完成/进行中/未处理三态显示，当前阶段高亮，已完成阶段打勾标记） |
| 处理失败 | 显示错误信息（红色边框框出），附出错时间戳；若收件箱仍有待处理文件则提示"收件箱还有 N 个待处理文件"，用户调整后重试 |

页面从上到下的信息顺序：**状态 → 处理进度（仅处理中时显示，含 6 阶段进度条与 3×3 表格） → 收件箱·手动处理 → 处理成果 → 系统负担 → 数据位置 → 音频处理流程**，每个区块一个面板。「最近处理」已移至页 2「处理记录」顶部。

#### 页 2 — 处理记录

回答：**"过去处理了哪些文件？最近处理了啥？"**

- 面板 1「最近处理」：最新 5 条转录片段（时间、源文件、说话人、时长 + 摘要），自概览页迁入本页顶部
- 面板 2「筛选条件」：日期范围 + 说话人两个筛选器
- 面板 3「片段记录」：记录表格（ID、源文件、说话人、时长、开始时间、处理完成），面板说明实时显示条数
- 面板 4「片段详情」：选一条记录看详情——转录文本用灰底引用块突出，附绝对时间区间、音频回放
- **说话人列显示规则**：本页所有说话人（最近处理、片段记录、片段详情）显示**标注后的姓名**——`unknown_XXXX` 编号经 `speaker_clusters.assigned_name` 显示层映射为姓名，未标注的仍显示原编号；数据库 `transcripts.speaker` 原文不动（见 §8.1.4）

#### 页 3 — 数据库

回答：**"录了哪些人？未知说话人攒了多少、都是谁？数据库怎么组织的？"**

- 面板 1「声纹怎么来的」：用一段话讲清**不需要专门录入**——处理音频时系统自动从片段抓取每个说话人的声纹记为 `unknown_XXXX`，用户标注姓名后系统持续学习、越认越准（"你标注 → 系统学习 → 下次自动认出"）；不再引导命令行录入（`enroll_voiceprint.py` 不再作为主流程）
- 面板 2「声纹簇·标注学习」：列出**全部声纹簇**（编号 / 标注为 / 学习样本数），分三个操作区：「标注为某人」（未标注编号 → 姓名，不标注编号不出现）、「校准已标注」（改标为他人 / 改回未知，含两步确认）与「不标注」（陌生人设为不标注保持原编号 / 恢复标注，支持批量）；标注即写回 `speaker_clusters.assigned_name` 并自动建人物档案（FR-003-CLUSTER）
- 面板 3「人物档案」：档案列表 + 新增/编辑表单（姓名/性别/出生年/与我的关系/备注/是否已标注声纹），与声纹按姓名关联（FR-010）。"是否已标注声纹"列自动判断——若 `speaker_clusters.assigned_name` 中存在该姓名则显示"是"，否则"否"
- 面板 4「数据库怎么组织的」：先用三行自然语言讲清各表各存什么，再附两个折叠区——「查看建表语句」（自定义 `<pre>` 紧凑排版）和「查看示例数据」（最近 3 条 JSON）

#### 页 4 — 文件归档

回答：**"谁在什么时候说了什么？转写结果保存在哪？"**

- 面板 1「搜索转录文本」（全宽）：关键词 + 说话人 + 时间范围搜索；结果列表每条显示说话人、绝对时间、来源文件、文本（前 200 字）；结果下方用下拉框选择一条结果回放对应音频片段；结果中的说话人同样经显示层映射显示标注后的姓名（未标注仍显示 `unknown_XXXX` 编号，见 §8.1.4）
- 面板 2「浏览归档文件」（全宽）：文本备份/归档音频二选一，按月份折叠分组（`<details>`/`<summary>` 原生 HTML）；文本备份模式下用下拉框选择文件，在限高滚动预览框中查看全文

#### 页 5 — 访问控制

回答：**"谁能访问 Web 界面？ThinkPad 对外开了哪些端口？"**

- 面板 1「IP 白名单 · 网页访问」：列出当前 8501 端口放行来源（Tailscale 设备 IP 固定、设备 IP 可移除），支持新增/移除设备 IP 并填写描述（即时生效）；修改经 sudoers 限定的管理脚本调用 ufw（见 FR-008-A）
- 面板 2「端口说明」：SSH 22（开发机远程管理/部署）与 Web UI 8501（浏览器访问）；远程桌面端口已关闭

### 8.3 CLI 交互

```bash
$ bash run.sh

╔══════════════════════════════════════════════════════════╗
║       🎙️  本地音频转录与声纹识别系统 — 主菜单          ║
║                                                          ║
║  🖥️   运行环境: ThinkPad Ubuntu 24.04 · i5-10210U       ║
║  🧠 模型组合: Silero VAD + PyAnnote + Qwen3-ASR-1.7B    ║
║  💾 数据位置: ~/asr_sys_local/audio_archive/           ║
║  📥 收件箱  : ~/asr_sys_local/audio_inbox/  (看板手动触发)║
╚══════════════════════════════════════════════════════════╝

请选择运行模式:
  1) 📝 单次处理音频文件
  2) 🖥️   启动 Web 管理界面 (Streamlit)
  3) 🎤 声纹库录入 (注册 '我' / 家人…)
  4) 👥 查看声纹库
  5) 📊 查看数据库统计
  6) ⬇️  下载/验证模型权重
  7) 🔑 设置/覆盖 HF Token
  8) ❌ 退出

输入选项 [1-8]: 1
音频文件路径: 2026-08-02_19_30_25.wav
已知说话人数 (回车=自动检测): 2

⏱ 录音开始时间提取:
  文件名解析 (主来源): 2026-08-02 19:30:25 (+08:00)  ✅

✅ 处理完成!

📋 时间戳验证:
  源文件: 2026-08-02_19_30_25.wav
  归档文件: 2026-08-02-193025-203025.wav
  录音开始时间: 2026-08-02 19:30:25 (+08:00)  ← 来自文件名解析

  片段 1:
    相对偏移: 00:00:05 → 00:00:20
    绝对时间: 2026-08-02 19:30:30 → 2026-08-02 19:30:45  ← ✅ 正确
    说话人: 我 (声纹匹配 0.87)
    文字: 大家好，我是项目经理小王...

  片段 2:
    相对偏移: 00:00:25 → 00:00:40
    绝对时间: 2026-07-31 14:31:17 → 2026-07-31 14:31:32  ← ✅ 正确
    说话人: 家人 (声纹匹配 0.68, ⚠️ 待确认)
    文字: 好的，我先汇报一下...

💾 已保存:
  数据库: /home/kevin/asr_sys_local/audio_archive/transcripts.db
  文本备份: /home/kevin/asr_sys_local/audio_archive/text_backups/2026-08/2026-08-02-193025-203025.txt
```

> 注：`run.sh` 启动时自动加载生产环境变量（`.env`，含 HF_TOKEN / ASR_PROJ_ROOT / ASR_ARCHIVE / ASR_INBOX / HF_HOME 等），CLI 与 WebUI 共用同一套生产路径；`.hf_token` 文件仅作无 `.env` 时的兜底。CLI 选项对应单次处理/Web 管理/声纹管理等，推荐方式为 Web 看板手动触发。

---

## 9. 风险评估与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 模型首次下载失败 | 中 | 高 | 手动下载脚本 `step2_download_models.sh`（Python snapshot_download，目录与运行时一致）；HF 直连卡死时经代理或 `HF_ENDPOINT=https://hf-mirror.com`（v2.46 实测可用，覆盖 v2.31 "hf-mirror 不可用"的旧结论） |
| 内存不足 (OOM) | 中 | 高 | 1.7B FP32 峰值 11.8GB（< 12GB 红线），分阶段串行加载/卸载；内存紧张可回退 bf16（5.2GB/3.14× 实时，见 TDD §3.4） |
| PyAnnote Token 过期 | 低 | 中 | 文档明确说明申请流程 |
| 长音频处理超时 | 中 | 中 | 支持分段处理 + 断点续传 |
| 声纹分离精度不足 | 中 | 中 | 支持手动指定说话人数优化 |
| **声纹误匹配 (张冠李戴)** | 中 | 中 | 三档阈值 + "疑似待确认"队列 + 录入规范（1~3 分钟干净语音） |
| 声纹库样本质量差 | 中 | 中 | 录入流程引导（时长校验 `enroll_min_duration_s`）+ 支持重新录入 |
| CPU 过热/降频 | 低 | 低 | 处理间隔休眠，避免满负荷 |
| **文件名格式不匹配** | 低 | 中 | 支持多种常见格式 + 正则自定义 + 回退到文件创建时间 |
| **时间戳提取失败** | 中 | **高** | 提供多种提取策略 + 手动输入兜底 |
| **时间戳计算错误** | 低 | **高** | 应用层统一计算 + 验证日志 + 单元测试 |
| **时区处理错误** | 低 | **高** | 强制 ISO 8601 带 +08:00 存储，统一北京时间 |
| Tailscale 连接失败 (NAT 打洞失败) | 低 | 中 | 自动走官方中继保底；办公室局域网内可直接 IP 访问 |
| 模型版本 API 变更 (如 PyAnnote 3.x→4.x) | 中 | 中 | 锁定版本 + 升级时验证兼容性 |
| 同名多格式文件去重遗漏 | 低 | 中 | 固定格式优先级 + 端到端测试覆盖 |

---

## 10. 里程碑与排期

| 阶段 | 功能 | 预计工时 | 交付物 |
|------|------|---------|--------|
| **MVP** | VAD + Diarization + 声纹库与识别 + ASR + DB + 监控 + 归档有机重命名 + 时间戳系统 + 子文件夹递归扫描 + 同名多格式处理 | 4-5 天 | 可运行的流水线，端到端测试通过 |
| **v1.1** | 系统看板 (Tab 1-4) + Tailscale 跨设备访问 + 声纹确认队列 | 1.5-2 天 | Streamlit 看板，三端可访问，实时系统状态 + 搜索 |
| **v1.2** | 批量导入 + 多格式导出 | 0.5-1 天 | CLI 批量工具，导出含完整时间戳 |
| **v2.0** | 说话人库管理 + 实时流 + 时间戳审计 | 3-5 天 | 完整系统，时间戳不可篡改 |

---

## 11. 附录

### 11.1 模型信息

各模型的大小、内存占用、来源及性能数据见 [TDD §2.1](./TDD_local_asr_system.md#21-模型清单)。模型存储路径、加载超时机制、内存编排等工程实现细节见 [TDD §2](./TDD_local_asr_system.md#2-模型选型与部署)。

### 11.2 时间戳相关配置

完整配置项（路径、格式优先级、VAD 参数、声纹匹配阈值、内存编排等）见 [TDD §5](./TDD_local_asr_system.md#5-配置参考)。

### 11.3 参考资源

- Qwen3-ASR: https://huggingface.co/Qwen/Qwen3-ASR-1.7B-hf
- PyAnnote Diarization: https://huggingface.co/pyannote/speaker-diarization-3.1
- PyAnnote Segmentation: https://huggingface.co/pyannote/segmentation-3.0
- PyAnnote Wespeaker（3.1 默认声纹嵌入）: https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM
- PyAnnote Community（3.1 的 PLDA 打分依赖，v2.46 记录）: https://huggingface.co/pyannote/speaker-diarization-community-1
- PyAnnote Embedding: https://huggingface.co/pyannote/embedding
- ModelScope（模型镜像下载，v2.31 实测可用）: https://modelscope.cn
- Tailscale: https://tailscale.com
- ISO 8601: https://en.wikipedia.org/wiki/ISO_8601
- SQLite FTS5: https://www.sqlite.org/fts5.html

---

## 12. 变更日志

本版本的详细变更日志已移至 [TDD §6](./TDD_local_asr_system.md#6-变更日志)。PRD 仅记录需求层面的变更摘要，工程实现细节的变更见 TDD。

| 版本 | 日期 | 需求层面变更摘要 |
|------|------|----------------|
| v1.0 | 2026-07-31 | 初始版本 |
| v1.1 | 2026-07-31 | 强化时间戳系统，增加绝对时间计算、时区处理、防篡改机制 |
| v1.2 | 2026-08-01 | 新增声纹库与说话人识别 (FR-003-VID)、归档有机重命名 (FR-001-AR)、Tailscale 跨设备访问 (FR-008-T)；统一北京时间；去重改为 SHA-256；ASR 定稿 Qwen3-ASR-0.6B |
| v1.3 | 2026-08-01 | 新增 FR-001-DIR 子文件夹递归扫描、FR-001-MULTI 同名多格式处理；路径规范化 |
| v1.4 | 2026-08-01 | FR-008 重新设计为系统看板（4 Tab），优先级 P1→P0 |
| v1.5 | 2026-08-01 | FR-008 精简去重；新增数据存储面板、数据库结构可视化、归档文件浏览；FR-001-AR 新增空文件夹清理 |
| v1.6 | 2026-08-01 | 新增 4 态状态机；UI 重构白底简洁风格 |
| v1.7 | 2026-08-01 | §6.2/§6.3 去重；修复排队中误报；UI 区块化；阶段进度条 |
| v1.8 | 2026-08-01 | KVI 视觉风格（灰阶为基、暖赭作标点） |
| v1.9 | 2026-08-01 | WebUI 布局精修（隐藏顶部工具栏、文字导航式 Tab） |
| v2.0 | 2026-08-01 | KVI 深化（数字放大、白底圆角模块、自绘进度条） |
| v2.1 | 2026-08-02 | WebUI 导航定稿（`st.segmented_control`）；FR-001 改为手动触发；新增一键部署脚本 |
| v2.2 | 2026-08-02 | 新增 FR-003-CLUSTER 声纹簇、FR-010 人物档案、FR-008-S 中文搜索修复 |
| v2.3 | 2026-08-02 | 声纹工作方式定稿为标注学习，取消专门录声纹环节 |
| v2.4 | 2026-08-02 | 新增说话人显示名映射；全文一致性 review |
| v2.5 | 2026-08-02 | 状态机 5 分钟兜底；ASR 文本清洗；存量数据回填 |
| v2.6 | 2026-08-02 | 状态机升级至 5 态（新增处理失败）；WebUI 预启动机制 |
| v2.7 | 2026-08-02 | VAD 切换为 Silero VAD；输出格式移除 SRT；模型加载 300 秒超时 |
| v2.8 | 2026-08-02 | 兄弟文件删除（不再归档多格式）；清理错误路径 |
| v2.9 | 2026-08-02 | 错误处理逻辑修正：失败不移文件，仅生成日志 |
| v2.10 | 2026-08-02 | ① 标注声纹计数改为按唯一姓名去重（`COUNT(DISTINCT assigned_name)`）；② 处理中显示进度面板：处理起始时间/已耗时、当前步骤/已耗时 |
| v2.11 | 2026-08-02 | 人物档案面板新增"是否已标注声纹"列，自动关联 `speaker_clusters.assigned_name` 判断 |
| v2.12 | 2026-08-02 | 状态机精简为 3 态（空闲/处理中/处理失败），移除从未出现的"已停止"和"排队中" |
| v2.13 | 2026-08-02 | 错误文件归档：每次处理前旧错误自动移入 `error/archived/`，根目录只保留当前批次错误；`error/README.txt` 说明目录结构 |
| v2.14 | 2026-08-02 | 长音频（≥10 分钟）说话人分离改在独立子进程运行，防 OOM 拖垮主进程；异常退出通过退出码区分（OOM/段错误）；SIGTERM/SIGINT 优雅退出清理状态 |
| v2.15 | 2026-08-02 | PyAnnote 4.x API 兼容修复（`DiarizeOutput` 包装对象）；模型加载离线优先；补齐 Silero VAD 本地权重（消除假网络错误） |
| v2.16 | 2026-08-02 | **说话人分离超时口径修正（FR-003）**：动态超时基准 600s+300s/10min → 1200s+900s/10min（短超时会误杀接近 1 倍实时的正常长音频）；实测长音频（≥10 分钟）子进程隔离模式接近 1 倍实时；工程细节见 [TDD v2.16](./TDD_local_asr_system.md#6-变更日志) |
| v2.17 | 2026-08-02 | **错误文件时间戳 + 准备处理按钮 + 移除自动超时 + 完全离线**：① 错误文件名附加产生时间戳 `{源文件}_{YYYYMMDD_HHMMSS}.error.txt`（见 FR-001）；② WebUI 新增「准备处理收件箱」按钮（归档旧错误 + 解锁残留锁文件，见 FR-008-M）；③ 移除说话人分离自动超时（改由外部监控发现挂死，避免误杀/干等，见 FR-001）；④ 模型完全离线化（Silero VAD `source='local'`、PyAnnote `HF_HUB_OFFLINE=1`、Qwen3-ASR `local_files_only=True`，见 §5.3）；⑤ 修复 `.error.txt` 复合后缀判断 bug（`Path.suffix` 只返回 `.txt`，改用 `endswith` 匹配） |
| v2.18 | 2026-08-02 | **刷新频率放宽 + 进度面板表格化 + ASR 全链路修复 + VAD 单位修复**：① 概览页处理中自动刷新从 5 秒放宽为 15 秒（见 FR-008 数据刷新）；②「处理进度」面板改为 3 行 3 列表格（表头：任务/起始时间/耗时；总任务行 + 当前步骤·XXXX 行，起始时间黑色、总耗时赭红、当前步骤耗时暖赭，见 §8.2 线框图）；③ Qwen3-ASR 改为直接加载本地自定义解压目录 `models/Qwen3-ASR-0.6B-hf/`，修复 `local_files_only=True` 匹配不到自定义目录导致回退联网失败的问题（见 §5.3）；④ **Qwen3-ASR 调用方式重构**：改用官方 `apply_transcription_request()` 入口（模型类为 `AutoModelForMultimodalLM`），修复转录全链路报错；⑤ Silero VAD 时间戳单位修正（采样点→秒，见 FR-002） |
| v2.19 | 2026-08-03 | **进度面板总任务行修复 + 人物档案保留 + 工程组织梳理**：① 修复「处理进度」表格"总任务"行在任务启动后无起始时间/耗时的问题（进度时间戳写入判断修正，见 §8.2）；② 标注声纹后不再清空已填写的人物档案（性别/出生年/关系/备注保留，见 FR-010）；③ 工程代码 Review：修复 CLI 配套工具（`run_pipeline.py`/`enroll_voiceprint.py`）错误导入与语法错误、清理遗留别名与重复实现、部署脚本纳入 CLI 文件避免暂存区与生产区漂移、删除一次性过程稿 `step1_setup.sh`；工程细节见 [TDD v2.19](./TDD_local_asr_system.md#6-变更日志) |
| v2.20 | 2026-08-03 | **声纹标注校准**：①「声纹簇·标注学习」面板重构——列出全部簇（含已标注），拆为「标注为某人」与「校准已标注」两个操作区（`st.tabs`）；② 新增**改标为他人**：已标注簇可改标为另一姓名（覆盖标注 + 回填）；③ 新增**改回未知**：已标注簇可改回 `unknown_XXXX`（沿用簇原编号，不产生新编号；标注→改回→再标注全程可逆），含两步确认防误操作；④ 三种操作（标注/改标/改回）统一走 `apply_cluster_label` 回填逻辑，人物档案保留已有资料（见 FR-003-CLUSTER、FR-010） |
| v2.21 | 2026-08-03 | **CLI 环境变量根治**：`run.sh` 启动时自动加载 `.env`（生产环境变量）并强制注入 `ASR_PROJ_ROOT`，CLI 与 WebUI 共用同一套生产路径——根治未加载 `.env` 时 settings 走默认值（`~/asr-local`、`~/audio_archive`、`model_cache`）在 HOME 下制造残留目录的问题（清理 `/home/kevin/asr-local` 残留） |
| v2.22 | 2026-08-03 | **工程组织与版本管理**：工程平铺重构（部署源与仓库根合一，与运行节点布局一致）、修复 CLI 主菜单（`run.sh`）工程根路径 bug、代码托管至公开 GitHub 仓库 `Kevyn-2021/local-asr-sys`（`.gitignore` 排除机密与个人数据、README 强调本地运行与数据不出本机）；需求功能无变化，工程细节见 [TDD v2.22](./TDD_local_asr_system.md#6-变更日志) |
| v2.23 | 2026-08-03 | **PRD 与实现对齐 + 部署地址可配置**：① §8.2 页 1 线框图与 WebUI 实际对齐——页面信息顺序（处理成果 → 系统负担 → 数据位置 → 音频处理流程）、补「准备处理收件箱」按钮、补 6 阶段进度条示意；② §8.1 数据来源表对齐实现（处理记录默认取最近 200 条、删除实现中不存在的"成功/失败统计"、数据位置统计项修正）；③ 部署脚本支持 `ASR_REMOTE_HOST` 环境变量覆盖运行节点地址（ThinkPad 随网络环境更换 IP） |
| v2.24 | 2026-08-03 | **部署与访问环境说明**：FR-008 补充**浏览器访问环境**（地址格式 `http://<ThinkPad当前IP>:8501`、办公室/家里/Tailscale 示例地址）与**部署环境**（SSH 地址、代码/数据目录、`deploy_webui.sh` 与 `ASR_REMOTE_HOST` 部署命令），并注明所列为示例地址、实际使用前需替换为 ThinkPad 当前真实 IP |
| v2.25 | 2026-08-03 | **声纹阈值调低 + 流程面板输入/产出分行**：① 声纹匹配置信度阈值调低（自动标注 0.75→0.65、疑似待确认 0.60→0.50，配置见 [TDD §5](./TDD_local_asr_system.md#5-配置参考)）——提高同一声纹跨录音的自动关联成功率，误关联可由「校准已标注」手工改回（FR-003-CLUSTER）；② WebUI 首页「音频处理流程」面板底部输入/产出**各占一行**，输入格式列表按格式优先级（FR-001-MULTI）排序为 `wav / flac / m4a / mp3 / opus / ogg / webm` |
| v2.26 | 2026-08-03 | **目录结构对齐生产布局**：git 仓库与 MacBook 工程根由"代码平铺"调整为与运行节点一致的 `asr_sys_local/` 包裹结构——代码位于 `asr_sys_local/asr-local/`（= 部署源），数据目录 `audio_inbox/`、`audio_archive/` 以占位文件保留结构（内容属个人数据，不入库）；部署环境（FR-008）中的代码/数据目录路径不变（本就为 `/home/kevin/asr_sys_local/...`）；需求功能无变化，工程细节见 [TDD v2.26](./TDD_local_asr_system.md#6-变更日志) |
| v2.27 | 2026-08-03 | **文档组织调整**：PRD/TDD 上提至工程总目录 `asr_sys_local/` 一级（与 `asr-local`/`audio_archive`/`audio_inbox` 并列），重命名为 `PRD_local_asr_system.md` / `TDD_local_asr_system.md`；两文档互引索引、`file:///` 绝对路径已同步更新，指向不再失效；需求功能无变化，工程细节见 [TDD v2.27](./TDD_local_asr_system.md#6-变更日志) |
| v2.28 | 2026-08-03 | **README 英文化上提仓库根**：README 移至仓库根（`asr_sys_local/README.md`）并全文改写为英文，核心传达"本地运行、完全离线、数据不出本机"；仓库 Description（英文文案）需在 GitHub 网页 About 处手动设置（本机无 gh CLI/token）；需求功能无变化，工程细节见 [TDD v2.28](./TDD_local_asr_system.md#6-变更日志) |
| v2.29 | 2026-08-03 | **git 仓库根外置（README 落仓库根）**：修正 v2.28 的 README 未显示问题——git 仓库根为 `ASR-Local-Thinkpad/`（README.md/.gitignore 在此，GitHub 首页渲染 README），工程总目录 `asr_sys_local/` 保持与运行节点一致；需求功能无变化，工程细节见 [TDD v2.29](./TDD_local_asr_system.md#6-变更日志) |
| v2.30 | 2026-08-03 | **仓库扁平化（去除 asr_sys_local 包裹层）**：git 仓库根一级直接放置全部有效内容——代码目录 `asr-local/`、数据目录 `audio_archive/`/`audio_inbox/`（占位）、README.md、PRD/TDD、.gitignore，与运行节点 `/home/kevin/asr_sys_local/` 内容一一对应；ThinkPad 生产路径不变（`deploy_webui.sh` 两端逻辑零改动）；需求功能无变化，工程细节见 [TDD v2.30](./TDD_local_asr_system.md#6-变更日志) |
| v2.31 | 2026-08-03 | **ASR 升级 1.7B + VAD 静音切除加速说话人分离**：① **ASR 模型 0.6B → 1.7B**（FR-004）——长句/中英混合/带口音场景准确率明显提升；权重按模型默认精度加载（半精度存储 ~3.4GB，移除原 FP32 强转），内存峰值仍 < 6GB；耗时为 0.6B 的约 3 倍，实测校准中；② **说话人分离按 VAD 段拼接切除静音**（FR-003）——PyAnnote segmentation 滑窗按"总时长"遍历、静音也在白白计算，先按 Silero VAD 语音段拼接成连续音频再分离（输入压缩为语音总时长，加速与静音占比成正比），分离结果时间戳自动映射回原始时间轴，对下游完全无感；`DIARIZATION_CONFIG.use_vad_concat` 开关可在几乎无静音场景关闭；工程细节见 [TDD v2.31](./TDD_local_asr_system.md#6-变更日志) |
| v2.32 | 2026-08-03 | **ASR 加载精度定稿 FP32 + 内存红线放宽**：① **1.7B 改为显式 FP32 加载**（v2.31 默认精度实测为 bf16，CPU 无 AVX512-BF16 回退转换，3.14× 实时偏慢）——FP32 有 oneDNN 优化，实测 **1.11× 实时**（93s 语音转录 103s），转录质量不变（FR-004）；② **内存峰值红线 6GB → 12GB**（§5.1）：FP32 权重 ~6.8GB，实测峰值 **11.8GB**，16GB 系统留 ~4GB 余量；内存紧张可回退 bf16（5.2GB / 3.14× 实时，见 TDD §3.4）；③ 修复 bf16 默认精度下音频特征 float32 与权重类型不匹配问题（输入按模型 dtype 对齐，fp32 下不触发）；工程细节见 [TDD v2.32](./TDD_local_asr_system.md#6-变更日志) |
| v2.33 | 2026-08-03 | **文档/界面与实际对齐清理**：① 清理 VAD 死配置 `max_speech_len_s`（代码从未使用）；② **FR-003-VID 按实际实现重写**——声纹库字段（`is_owner` 本人唯一、非"1 号必须本人"）、录入流程（CLI 导入/录音、无朗读固定段落与试听功能）、匹配机制（三级匹配指向 FR-003-CLUSTER）；③ WebUI「音频处理流程」面板模型名 0.6B→1.7B、02/03 步骤描述补充 VAD 拼接与声纹簇机制；④ §5.3 完全离线目录名 0.6B-hf→1.7B-hf、风险评估去除"试听确认"；⑤ 文档头部版本号与变更日志对齐（PRD v2.33 / TDD v2.33）；工程细节见 [TDD v2.33](./TDD_local_asr_system.md#6-变更日志) |
| v2.34 | 2026-08-04 | **处理成果面板统计修正**：① 首页"转录片段"改为"音频数量"——数字按去重音频文件数（`COUNT(DISTINCT file_hash)`，与「搜索·文件」归档音频数一致）；② "累计时长(h)"数值修正——原 `SUM(audio_duration)` 将同一文件的整文件时长按片段数重复累加致虚高，改为按文件去重后求和再换算小时；③ 移除与"音频数量"重复的原"处理文件"格子；工程细节见 [TDD v2.34](./TDD_local_asr_system.md#6-变更日志) |
| v2.35 | 2026-08-04 | **全文一致性 Review 清理**：① 修复失效链接——FR-007 的 TDD §3.4 锚点 0.6B→1.7B、FR-008-S 原指向错误章节的链接改为自引用 §8.1.4；② 模型下载表述与实际对齐（hf-mirror 实测不可用：§6.1 技术栈、§9 风险表、§11.3 参考资源改为 huggingface-cli + 代理/ModelScope 兜底）；③ §8.3 CLI 示例与实际 8 项菜单对齐（移除已取消的"启动目录监控"项、序号/文案与 run.sh 一致）；④ §7.1 voiceprints 表 is_owner 注释去掉旧"声纹库 1 号"概念（对齐 FR-003-VID）；工程细节见 [TDD v2.35](./TDD_local_asr_system.md#6-变更日志) |
| v2.36 | 2026-08-04 | **失败文件处理回归"移入 error/"**：① FR-001-AR / FR-001-MULTI——处理失败时原始音频文件**移入 `error/` 目录**（重名时附加产生错误时间戳），同 stem 兄弟文件一并移入，`.error.txt` 日志带时间戳防重名（v2.9 曾改为"仅留日志不移文件"，本次按实际需求恢复移入，收件箱只保留待处理文件；用户排查后可手动移回重试）；② 旧错误归档（「准备处理收件箱」/自动）范围扩展为日志 + 失败音频一并归档到 `error/archived/`；③ §8.1.6 错误目录说明同步；工程细节见 [TDD v2.36](./TDD_local_asr_system.md#6-变更日志) |
| v2.37 | 2026-08-04 | **settings.py 版本管理口径修正**：① settings.py **纳入 git**（以 ThinkPad 生产版本为基准上传，`MODELS_DIR` 默认统一 `model_cache`，MacBook/ThinkPad 两端一致）——其设计例外是"**不随 `deploy_webui.sh` 部署**"而非"不入 git"；② `.gitignore` 移除 settings.py 排除、恢复 git 跟踪；工程细节见 [TDD v2.37](./TDD_local_asr_system.md#6-变更日志) |
| v2.38 | 2026-08-04 | **顶部锁定导航条（FR-008 UI）**：页首（标题 + 北京时间）与页签导航合并为同一行（左品牌右导航），整条 `position: sticky` 吸顶——滚动页面时导航始终可见，任意页可随时切换；吸顶条留白用 padding（sticky 元素 margin 区域透明、下层内容会透出）；工程细节见 [TDD v2.38](./TDD_local_asr_system.md#6-变更日志) |
| v2.39 | 2026-08-04 | **顶部锁定导航条修复（FR-008 UI）**：① 吸顶修复——Streamlit 1.60 的 `st.columns` 顶层容器为 `stLayoutWrapper`（非 `stElementContainer`），v2.38 选择器未命中导致吸顶与吸顶条样式整体未生效；② 页签与下方面板间距加大（吸顶条底部留白 0.9rem→1.15rem），各 tab 间距一致；③ 页签选中态适配 1.60 的 `role="radiogroup"`+button 渲染，恢复 KVI 暖赭高亮；工程细节见 [TDD v2.39](./TDD_local_asr_system.md#6-变更日志) |
| v2.40 | 2026-08-05 | **顶部品牌改版 + 页签选中态去背景（FR-008 UI）**：① 品牌名 **ASR 本地转录系统 → Local ASR System**（英文），删除左侧暖赭小方块（无意义装饰）；② 品牌块（标题 + 北京时间）整体右移 0.5rem，不再贴左边界；③ 页签选中态**去掉背景色**——只保留加粗暖赭文字，无背景块；④ 浏览器标签页标题同步改 Local ASR System；工程细节见 [TDD v2.40](./TDD_local_asr_system.md#6-变更日志) |
| v2.41 | 2026-08-05 | **顶部导航条单行布局（FR-008 UI）**：① 品牌名 **Local ASR System 字号缩小至与面板标题一致**（1.05rem / 600，同「收件箱 · 手动处理」）；② **北京时间移到第一行**，与品牌名同行显示（字号 0.82rem 不变）；③ **导航整体右移 12px**（radiogroup 左边距 0.75rem）；④ 顶栏单行放得下，品牌与导航垂直居中对齐；工程细节见 [TDD v2.41](./TDD_local_asr_system.md#6-变更日志) |
| v2.42 | 2026-08-05 | **移除 Qwen3-ASR-0.6B（FR-004）**：① 删除 ThinkPad 本地模型 `models/Qwen3-ASR-0.6B-hf`（1.5G）与 HF 缓存残留指针；② 清理当前状态描述中的 0.6B 对比（§6.1 技术栈模型条目、§8.2 模型说明），系统只保留 **Qwen3-ASR-1.7B** 唯一 ASR 模型；③ 变更日志历史条目保留（记录升级过程，不篡改历史）；工程细节见 [TDD v2.42](./TDD_local_asr_system.md#6-变更日志) |
| v2.43 | 2026-08-05 | **声纹簇「不标注」（FR-003-CLUSTER）**：① `speaker_clusters` 新增 `skip_label` 列（0/1，老库自动迁移）；② Web「声纹簇·标注学习」面板新增**第三个操作区「不标注」**——陌生人设为不标注后**保持原编号** `unknown_XXXX`，从「标注为某人」列表隐藏，**照常参与声纹匹配与学习**；③ 支持批量设为不标注 / 恢复标注，总览表格标注列显示"🚫 不标注"；④ 不标注不触发任何回填（编号未变）；工程细节见 [TDD v2.43](./TDD_local_asr_system.md#6-变更日志) |
| v2.44 | 2026-08-05 | **不标注操作区布局 + 全文一致性 Review（FR-003-CLUSTER / 文档）**：① 「不标注」tab 布局调整——「设为不标注」「恢复标注」按钮分别与各自多选框**同行垂直居中对齐**（列比 3:1 + `use_container_width`）；② 一致性修正——§7.1 `speaker_clusters.sample_count` 默认值 0→**1**（对齐代码 `db.py`）；§4.2 FR-009 标注/合并回填表述修正（**标注已实现同步回填**，合并/删除仍规划中且不回填）；③ 通篇审查：PRD/TDD/代码无其他不一致；工程细节见 [TDD v2.44](./TDD_local_asr_system.md#6-变更日志) |
| v2.45 | 2026-08-05 | **不标注操作区对齐修正（FR-003-CLUSTER）**：v2.44 的 `vertical_alignment="center"` 把按钮对齐到了「文字+下拉框」整体的中间（既没对齐文字也没对齐框）；实测按钮与下拉框**等高 40px**，改为 `vertical_alignment="bottom"` 后按钮与下拉框本身精确对齐（label 在框上方不参与定位）；工程细节见 [TDD v2.45](./TDD_local_asr_system.md#6-变更日志) |
| v2.46 | 2026-08-05 | **模型目录清理 + step2 下载口径重写（§5.3/§6.1/§9/§11.3）**：① 清理 ThinkPad 冗余模型约 145M（step2 旧版松散目录 ×3、顶层旧 hub 缓存重复 ×2、community-1 顶层残缺、silero-vad-ms、xet）；② **发现并记录 3.1 管线的 PLDA 依赖 `pyannote/speaker-diarization-community-1`**（误删后离线加载失败、已恢复，删除模型必须以实际离线加载验证为准）；③ step2 重写——废弃的 huggingface-cli 改为 Python `snapshot_download`，pyannote 全部入 hub 缓存（含 wespeaker 与 community-1），Silero 固定到 vad.py 实际目录，目录与运行时逐一对齐；④ 网络兜底结论更新——hf-mirror 实测可用（`HF_ENDPOINT`），覆盖 v2.31"不可用"旧结论；工程细节见 [TDD v2.46](./TDD_local_asr_system.md#6-变更日志) |
| v2.47 | 2026-08-05 | **代理用法记录（迁移 SEC）+ 残留目录清理（工程运维）**：① 记录本地代理 open_proxy 用法并迁移至 `SEC_local_asr_notes.md`（敏感信息不入库），供后续模型下载使用；② 清理错误路径残留目录 `/home/kevin/audio_archive`（0 字节空库，settings 默认路径 + 未加载 .env 所致），并加 `db.py` 告警防护防止复发；功能需求无变化，工程细节见 [TDD v2.47](./TDD_local_asr_system.md#6-变更日志) |
| v2.48 | 2026-08-05 | **ASR 加载精度可配置（FR-004）**：① `ASR_CONFIG.torch_dtype` 支持环境变量 `ASR_TORCH_DTYPE` 覆盖——默认 **FP32**（1.11× 实时，内存红线 <12GB 不变），大文件/低内存时切 **bf16** 兜底（内存约减半、3.14× 实时）；② 背景——58.6 分钟大文件两次 OOM（FP32 峰值 ~15GB 超出 16GB 机器），切 bf16 后正常处理；③ 精度开关仅影响加载精度，不影响转录结果；工程细节见 [TDD v2.48](./TDD_local_asr_system.md#6-变更日志) |
| v2.49 | 2026-08-05 | **ASR 精度动态分配（FR-004）**：① 默认 `auto`——按音频时长自动选择加载精度：**≥30 分钟 → bf16**（内存约减半、3.14× 实时），**<30 分钟 → FP32**（1.11× 实时）；保证大文件不再 OOM、小文件速度不降；② 阈值与精度均可配置（`ASR_TORCH_DTYPE` / `ASR_TORCH_DTYPE_BIG_S`）；③ 精度不影响转录结果；工程细节见 [TDD v2.49](./TDD_local_asr_system.md#6-变更日志) |
| v2.50 | 2026-08-05 | **文件名时间提取格式统一（FR-001-TS）**：① 紧凑式支持横线/下划线两种分隔（`recording_20260731_143052` 与 `recording-20260731-143052` 均可识别），时间前后可带任意前缀/后缀；② PRD「支持的文件名格式」与 TDD 正则/描述同步为同一清单（长格式混用分隔符 / 紧凑式 / ISO T 分隔），消除两处文档与实现的口径差异；工程细节见 [TDD v2.50](./TDD_local_asr_system.md#6-变更日志) |
| v2.51 | 2026-08-05 | **敏感/个人信息去敏迁移（文档工程）**：① 新建 `SEC_local_asr_notes.md` 集中存放网络环境规则（办公室直连 / 家网代理）、open_proxy 用法、网络地址、设备型号、家庭声纹标签、拾音设备特点，**加入 .gitignore 不推送 GitHub**；② PRD 中具体 IP、设备型号、家庭人物示例全部中性化（真实信息本机维护）；功能需求无变化，工程细节见 [TDD v2.51](./TDD_local_asr_system.md#6-变更日志) |
| v2.52 | 2026-08-05 | **消除对 SEC 的引用（GitHub 死链修复）**：移除 PRD 中所有 `SEC_local_asr_notes.md` 链接与"见 SEC 文档"引导，改为自洽中性表述（"本机维护、不随仓库发布"）；变更日志保留迁移历史（纯文本文件名，无链接）；功能需求无变化，工程细节见 [TDD v2.52](./TDD_local_asr_system.md#6-变更日志) |
| v2.53 | 2026-08-05 | **ASR 转录加速（FR-004）**：① **合并相邻短段再转录**——逐段固定开销（特征提取+解码启动）远大于内容转录，段数越多越慢（1.7B 下 ASR 时间 ≈ 段数 × 每段开销，VAD 收益被吃掉）；合并间隔 ≤1.5s、段长 ≤60s 的相邻段（可配置），140 段可合并到约 6 段，转录时间大幅下降；② 合并段长上限同时约束单段内存峰值，缓解大文件 OOM；③ 转录行粒度变为合并段起止（句子级时间戳留待后续细分）；工程细节见 [TDD v2.53](./TDD_local_asr_system.md#6-变更日志) |
| v2.54 | 2026-08-05 | **效率优先原则固化 + 错误归档修复（FR-004 / FR-001-AR）**：① 明确转录行**不严格按说话人切分**（效率优先），声纹身份由声纹簇承载、不受转录行粒度影响，声纹标注可用单人录音等其他途径；② 修复错误归档逻辑——只归档 `.error.txt` 日志与失败音频，不再误移 error/ 下的 README 等杂项文件；误归档的 README 已恢复为 `error/README.md`；工程细节见 [TDD v2.54](./TDD_local_asr_system.md#6-变更日志) |
| v2.55 | 2026-08-05 | **性能数据边界标注（§5.1）**：性能基准表明确——本表为 **CPU 部署实测（i5-10210U）**，仅作本机回归基线，GPU 部署需重新实测、绝对数值不跨硬件外推；当前配置/使用方法受制于 CPU+内存，**不应完整迁移到 GPU 系统**，需按实际硬件修改参数并适配后再用；工程细节见 [TDD v2.55](./TDD_local_asr_system.md#6-变更日志) |
| v2.56 | 2026-08-05 | **ASR 转录内存与耗时修正（FR-004）**：① 段长上限 60s→**15s**——实测长段（60s）在 bf16 CPU 下单段约 6 分钟，且把内存钉满 16GB 导致假死；收紧后单段耗时与峰值内存双降；② ASR 过程中定期把空闲堆内存归还系统，避免 RSS 长期占满；③ 5.2 分钟语音预计 10-20 分钟完成；工程细节见 [TDD v2.56](./TDD_local_asr_system.md#6-变更日志) |
| v2.57 | 2026-08-05 | **说话人分离段修复（FR-003，根因）**：① 定位——"ASR 内存满+耗时数小时"的根因是**分离段把静音一起包进来**（VAD 拼接把同一说话人的多个语音片断在拼接轴上并成一段，映射回原时间轴后横跨静音）：58.6 分钟文件 VAD 语音仅 5.2 分钟，分离段却合计 51.7 分钟、最长 22 分钟，ASR 被迫转录含静音的整段；② 修复——分离结果**裁剪回真实语音片断**（与 VAD 取交集），段总时长回到语音量级（5.4 分钟、最长 35.7s）；③ 效果——ASR 只转录真实语音，内存与耗时恢复可预期；工程细节见 [TDD v2.57](./TDD_local_asr_system.md#6-变更日志) |
| v2.58 | 2026-08-06 | **ASR 精度决策升级（FR-004）**：① 原规则按"音频总时长 ≥30 分钟"切 bf16；升级为按**决策时可用内存 + VAD 语音时长**——可用内存 ≥13.5GB 且语音 ≤30 分钟 → FP32（快），否则 bf16（稳）；② 理由：语音裁剪后 ASR 峰值主要由模型决定，与文件大小/总时长弱相关，内存+语音量更精准；③ 收件箱 11 个真实文件实战观测，阈值可按实测微调；工程细节见 [TDD v2.58](./TDD_local_asr_system.md#6-变更日志) |
| v2.59 | 2026-08-06 | **多格式音频加载修复（FR-001）**：① 实战发现——m4a（及 soundfile 不支持的 mp3）加载失败（pydub 回退分支的 `del` 未定义变量 bug）；② 修复后支持 m4a/mp3/wav 等全部格式；③ 11 文件实战批处理已重启；工程细节见 [TDD v2.59](./TDD_local_asr_system.md#6-变更日志) |
| v2.60 | 2026-08-06 | **WebUI 访问控制（FR-008 / FR-008-T）**: ① 端口 8501 不再对局域网所有人开放——ufw 删除 `allow 8501/tcp`（Anywhere），改为仅放行 Tailscale 网段（100.64.0.0/10）与白名单设备 IP（当前：开发机 MacBook）；② 新增设备推荐走 Tailscale（跨办公室/家里通用），或路由器 MAC 绑定固定 IP 后加入 ufw 白名单（手机随机 MAC 注意关闭"私有地址"）；③ `install_services.sh` 默认策略同步；工程细节见 [TDD v2.60](./TDD_local_asr_system.md#6-变更日志) |
| v2.61 | 2026-08-06 | **导航 5 页签 + 访问控制页（FR-008 / 新增 FR-008-A）**: ① 导航改为 5 个**等宽**页签：状态概览 / 处理记录 / 数据库 / 文件归档 / 访问控制；② **北京时间从顶栏移出**，改为首页导航条下方第一个面板（仅首页展示）；③ 新增「访问控制」页——IP 白名单管理（Tailscale 网段固定放行，设备 IP 可增删，即时生效）+ 端口说明（SSH 22 / Web 8501；远程桌面端口已关闭）；④ ThinkPad 卸载 RustDesk、关闭 3389 与 RustDesk 端口，对外仅保留 SSH 与 Web；工程细节见 [TDD v2.61](./TDD_local_asr_system.md#6-变更日志) |
| v2.62 | 2026-08-06 | **导航宽度回归 + 访问控制页描述栏 + 页脚部署时间（FR-008 / FR-008-A）**: ① 导航**不再等宽**——页签宽度随文字自适应（如「数据库」3 字更窄），窗口变窄时自动折行；② 访问控制页新增「描述」输入栏，「添加」按钮与输入栏同行垂直居中；③ Tailscale 白名单由"网段 100.64.0.0/10"改为**精确设备 IP**（固定放行、不可删除；精确地址只记本机 SEC）；④ 端口说明面板删除冗余说明；⑤ 页脚加「部署时间」前缀避免误当当前时间；工程细节见 [TDD v2.62](./TDD_local_asr_system.md#6-变更日志) |
| v2.63 | 2026-08-06 | **导航加宽/字距微调 + 白名单分组排序（FR-008 / FR-008-A）**: ① 导航页签加宽（水平 padding 1.5rem）并加大页签间隙（gap 6px）、字间距 0.02em；② 「访问控制」页 IP 白名单按**固定放行置顶、设备白名单（可新增/移除）置底**分组展示，新增表单保持面板底部；工程细节见 [TDD v2.63](./TDD_local_asr_system.md#6-变更日志) |
| v2.64 | 2026-08-06 | **导航去缝隙 + 白名单行对齐（FR-008 / FR-008-A）**: ① 移除页签之间 gap（恢复分段控件连续外观），tab 内部文字字距微调至 0.05em（此前 0.02em）；② 「访问控制」页白名单每行「移除」按钮与左侧 IP/描述垂直居中对齐；工程细节见 [TDD v2.64](./TDD_local_asr_system.md#6-变更日志) |
| v2.65 | 2026-08-06 | **ASR FP32 阈值按实测微调（FR-004）**: ① 实战发现——16GB 机器（仅跑本任务）决策时可用内存稳定 12.7-13.3GB，默认阈值 13.5GB **永不触发**，auto 全部走 bf16（8 分钟语音 ASR 实测 ~57 分钟、约 7-8× 实时）；② `fp32_min_avail_mb` 默认 **13500→12000MB**——FP32 峰值 ~12-13GB、留 ~1GB 余量，低于阈值仍自动回退 bf16（安全兜底不变）；③ 11 文件批处理实测：bf16 每段 91-126 秒（与 TDD v2.53 记录吻合），切 FP32 预期 2.5-3× 提速；工程细节见 [TDD v2.65](./TDD_local_asr_system.md#6-变更日志) |
| v2.66 | 2026-08-06 | **解除 ASR 语音时长限制 + 卸载归还内存（FR-004）**: ① **解除 v2.49 遗留的"语音 ≤30 分钟"上限**——语音裁剪（v2.57）+ 段长封顶（v2.56）后 ASR 峰值由模型本身决定、与语音总量弱相关，精度决策只保留内存护栏（可用内存 ≥12GB → FP32，否则 bf16）；② **阶段卸载归还内存**——`_unload_asr`/`_unload_diar` 增加 `malloc_trim`（实测 FP32 卸载后 torch CPU 池滞留 ~2GB，导致下一文件决策可用内存虚低而误走 bf16）；③ 部署验证：终止旧批重启，首文件 FP32 决策生效；工程细节见 [TDD v2.66](./TDD_local_asr_system.md#6-变更日志) |

---

**文档结束**
