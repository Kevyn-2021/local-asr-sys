# 本地音频转录与声纹识别系统 — 技术设计文档 (TDD)

**版本**: v2.86  
**日期**: 2026-08-06  
**状态**: 持续更新

> **===== 文档分工说明（请先阅读）=====**
> 
> **本文档的角色**：技术设计文档（TDD），聚焦"如何实现"——技术架构、模型选型、工程实现细节、踩坑记录、配置参考、变更日志。它回答"系统怎么建"。
> 
> **配套文档**：产品的需求定义（功能需求、非功能需求、UI 设计、数据模型）见另一份文档 [PRD_local_asr_system.md](file:///Users/kevin/m02_Developer/TRAE_Work_CN/ASR-Local-Thinkpad/PRD_local_asr_system.md)。
> 
> **内容不重复原则**：TDD 与 PRD 的内容互不重复。同样的内容只会在一个文档中出现，不会同时出现在两份文档中。两文档通过相互索引引用，而非复制粘贴。这样做的目的是：避免同一内容在多处维护，因漏改某处而导致不一致。
> 
> **写入新内容前请确认**：先判断内容属于"需求定义"还是"工程实现"，分别写入对应文档。如需引用对方文档的内容，使用链接索引而非重复描述。

---

## 1. 技术架构

### 1.1 架构总览

用户交互层 → 处理流水线 → 数据层，状态机在交互层与流水线之间串联。

```
┌─────────────────────────────────────────────────────────────┐
│                     用户交互层                               │
│   手动处理 (process_inbox.py)   CLI (run.sh)   Web 看板      │
│   └─ 看板按钮触发·递归扫描       └─ 单次处理      └─ 5 页    │
│            │                            │            ▲      │
│            │  触发 process_file()        │            │      │
│            └──────────────┬─────────────┘            │      │
│                           │                          │      │
│            状态上报 (status.json) ────────────────────┘      │
│            process_inbox 写 state/stage/pending；webui 读取  │
├───────────────────────────┼─────────────────────────────────┤
│                     处理流水线 (AsrPipeline)                  │
│  输入预处理 → VAD → 说话人分离 → 声纹匹配 → ASR → 时间戳    │
│  各阶段串行加载/卸载模型 (§3.2 内存编排)                      │
├───────────────────────────┼─────────────────────────────────┤
│                     数据层                                    │
│   SQLite transcripts.db                                      │
│   ├─ transcripts        片段：时间戳 + 说话人 + 文字          │
│   ├─ voiceprints        命名声纹库（声纹向量）                │
│   ├─ speaker_clusters   声纹簇·标注学习                       │
│   ├─ persons            人物档案                              │
│   └─ transcripts_fts/fts2  全文索引                           │
│   文本备份 text_backups/   音频归档 processed_audio/          │
│   status.json             日志 pipeline.log                  │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 处理流程（串行）

每个音频文件依次经过 6 个阶段，全部完成后才处理下一个文件：

1. **加载音频** → 2. **VAD 语音检测** → 3. **说话人分离** → 4. **声纹匹配** → 5. **ASR 转录** → 6. **归档与入库**

> 串行原因：模型按阶段加载/卸载，避免同时加载多个模型导致内存超限。

### 1.3 状态机

WebUI 顶部状态带显示 3 态，由 `derive_state()` 按以下优先级推导：

| 优先级 | 条件 | 判定状态 |
|--------|------|---------|
| 1 | 锁文件存在 | **处理中** |
| 2 | `last_launched_at` 在 5 分钟内 + `state=processing` | **处理中** |
| 3 | `last_launched_at` 在 5 分钟内 + `state=idle` + `last_result=failed` | **处理失败** |
| 4 | `last_launched_at` 在 5 分钟内 + `state=idle` | **空闲** |
| 5 | `state=processing` 且更新在 5 分钟内 | **处理中**（兜底） |
| 6 | `state=idle` + `last_result=failed` | **处理失败** |
| 7 | `state=idle` | **空闲** |
| 8 | 其余按 `status.json` 返回 | — |

> v2.12 精简说明：原 5 态（已停止/空闲/排队中/处理中/处理失败）中的"已停止"和"排队中"从未实际出现——手动触发模式下 `_write_status_prelaunch()` 和 `main()` 始终直接写 `state=processing`，无代码路径产生 `queued`；`已停止` 原为异常退出残留兜底，当前状态机已通过锁文件 + 5 分钟超时覆盖该场景。

**状态数据流转**：
- `process_inbox.py`：获取锁 → 写 `state=processing` → 0.5s 延迟确保持久化 → 循环内全程保持 processing → 循环外统一写 idle → finally 释放锁
- WebUI 预启动（`_write_status_prelaunch()`）：启动子进程前立即写 `state=processing` + `last_launched_at`，防止子进程启动延迟期间 WebUI 误判为"空闲"

### 1.4 进度时间戳追踪

`process_inbox.py` 的 `_status()` 函数在处理过程中自动记录两个时间戳，供 WebUI 进度面板展示：

| 字段 | 写入时机 | 清除时机 |
|------|---------|---------|
| `processing_start_time` | 首次写入 `state=processing` 时（当前次处理中尚未有此字段） | `state=idle` 时设为 `None` |
| `stage_start_time` | `stage` 字段变化时（新值与 `status.json` 中已有值不同） | `state=idle` 时设为 `None` |

**实现逻辑**（`process_inbox.py` 的 `_status()` 函数）：

```python
def _status(**kw):
    data = {}
    try:
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass

    # 进度时间戳追踪
    new_state = kw.get("state")
    if new_state == "processing" and not data.get("processing_start_time"):
        kw["processing_start_time"] = datetime.now().isoformat()
    elif new_state == "idle":
        kw["processing_start_time"] = None
        kw["stage_start_time"] = None

    new_stage = kw.get("stage")
    if new_stage and new_stage != data.get("stage"):
        kw["stage_start_time"] = datetime.now().isoformat()

    data.update(kw)
    data["updated_at"] = datetime.now().isoformat()
    STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
```

> **坑（v2.19）**：写入判断不能写成 `"processing_start_time" not in data`——空闲清理时该键**已存在但值为 `None`**，下次任务启动时 `not in data` 为 False 导致不写入，WebUI 进度表格"总任务"行永远空白。必须判断**值为空**（`not data.get(...)`）才写入。

WebUI 通过 `read_status()` 读取这些字段，计算已耗时并渲染进度面板。

**进度面板（v2.18 起为 3 行 3 列表格）**，仅处理中显示：

| 任务 | 起始时间 | 耗时 |
|------|---------|------|
| 总任务 | `processing_start_time`（黑色） | 总耗时（赭红） |
| 当前步骤·{stage} | `stage_start_time`（黑色） | 当前步骤耗时（暖赭） |

- 起始时间用 `fmt_full_time()` 显示完整 `YYYY-MM-DD HH:MM:SS`
- 耗时用 `fmt_elapsed()` 计算「现在 − 起始时间」
- 概览页处理中/失败时每 **15 秒**自动刷新（v2.18 由 5 秒放宽），空闲时每 10 分钟

详见 [PRD §8.2 页 1 线框图](./PRD_local_asr_system.md#82-系统看板布局ui-v20)。

### 1.5 标注声纹计数逻辑

WebUI「处理成果」面板中"标注声纹"数字的统计口径：

```sql
SELECT COUNT(DISTINCT assigned_name) FROM speaker_clusters WHERE assigned_name IS NOT NULL
```

**为什么用 `DISTINCT`**：多个 `unknown_XXXX` 编号可能标注为同一个人（如 unknown_0001/0002/0003 都标注为同一人），此时标注声纹数应为 1（按唯一姓名），而非 3（按簇数）。

### 1.6 人物档案与声纹关联

`list_persons()` 查询自动判断每个人物是否已有声纹簇标注，通过 `EXISTS` 子查询关联 `speaker_clusters.assigned_name`：

```sql
SELECT p.person_name, p.gender, p.birth_year, p.relation, p.note, p.created_at,
       CASE WHEN EXISTS (SELECT 1 FROM speaker_clusters sc
                         WHERE sc.assigned_name = p.person_name)
            THEN 1 ELSE 0 END AS has_voiceprint
FROM persons p ORDER BY p.created_at
```

WebUI 人物档案面板将 `has_voiceprint` 映射为"是/否"显示，用户可一眼看出哪些人已标注声纹、哪些尚未标注。

**标注回填不覆盖已有档案（v2.19）**：声纹簇面板「标注并回填」时，若 `persons` 表中已存在该姓名（用户填过资料），**不能**再调用只传姓名的 `upsert_person()`——其 `ON CONFLICT DO UPDATE` 会把未传字段覆盖为 NULL，导致"标注后档案只剩姓名"。必须先 `get_person()` 判断：仅当档案不存在时才自动建档。

### 1.7 声纹簇标注校准（v2.20）

自动标注可能出错，用户需能手工纠正。三种操作（标注为某人 / 改标他人 / 改回未知）统一走 webui.py 的 `apply_cluster_label(cluster_id, target)`：

```python
def apply_cluster_label(cluster_id, target):
    old_label = get_cluster_label(cluster_id) or ""          # 簇身份，永不改变
    old_name = (list_clusters_view 中该簇的 assigned_name) or None
    match_from = old_name if old_name else old_label         # transcripts 中当前存在形式
    if target:                                               # 标注 / 改标
        assign_cluster_name(cluster_id, target)
        if get_person(target) is None: upsert_person(target) # 已有档案不清空
        target_text = target
    else:                                                    # 改回未知
        unassign_cluster_name(cluster_id)                    # assigned_name=NULL
        target_text = old_label                              # 回填到原编号
    update_transcripts_speaker(match_from, target_text)
    update_txt_files_speaker(match_from, target_text)
```

**关键设计（可逆性）**：
- **`label`（unknown_XXXX）是簇的稳定身份，永不改变**。改回未知时沿用原编号，不产生新编号——因此"标注 → 改回 → 再标注"全程可逆，无需新编号，也不会编号错乱（编号全局递增不复用，见 §4.5 与 PRD FR-003-CLUSTER）。
- **回填匹配串的选择**：簇从未标注过 → transcripts 中记录是 `label`（pipeline 写入），匹配 `label`；已标注过 → 记录已被回填为姓名，匹配**姓名**。改回时用 `old_name` 匹配、回填到 `label`。
- **边界说明**：若同一姓名被标到多个簇，改回/改标会按姓名全局替换（UI 已提示"该姓名的记录将改回编号"）——属合理近似，用户可随后逐个再标注。

**UI（「声纹簇·标注学习」面板，v2.20 起 / v2.68 重构为单流程 / v2.85 轻量化）**：
布局按「筛选说话人 → 查看发言（仅未标注簇）→ 直接标注」组织（见 [PRD §8.2 页 3](./PRD_local_asr_system.md#82-系统看板布局ui-v20)），此处仅记录实现要点：
- **说话人下拉（v2.78 优化）**：已标注人员按 `assigned_name` **合并为一行**（同名多簇：`raws = [姓名] + 全部 label`，发言聚合展示）；未标注/不标注的 unknown 各自一行；**无发言样本的簇默认隐藏**（`sp_utt` 按 transcripts.speaker 计数判断，勾选「显示无发言样本的说话人」可显示，供清理/改名）；排序 = 未标注（按编号）→ 已标注（按姓名）→ 不标注（按编号）→ 声纹库命名；标注操作区对合并姓名展示簇清单，改标/改回**批量作用于全部同名簇**并汇总回填数（与 `apply_cluster_label` 按姓名全局回填一致）
- **发言列表**：`get_speaker_utterances(raws, limit=100)` 按 `speaker IN (label[, assigned_name])` 查询，绝对时间倒序
- **声纹匹配 · 学习看板（v2.79 起 / v2.80 按人展示）**：数据库页底部只读面板——顶部阈值行（认名 ≥0.65 ｜ 疑似 0.50–0.65 ｜ 学习 ≥0.75，读 `VOICEPRINT_CONFIG`）；主表**按已标注人员**逐行展示其片段 `speaker_match_score` 分档（≥0.75 高置信 / 0.65–0.75 认名未学习 / 0.50–0.65 疑似 / <0.50 未识别 / 无得分 / 合计 / 待重置簇），每行聚合"姓名 + 该人全部簇 label"（`get_voiceprint_dashboard()` 一次性加载全部 (speaker, score) 后 Python 分档），末行（全部）合计；底部小字概况（已标注人数/未标注/不标注/无发言样本簇/待重置簇，`list_clusters_view` 含 `reset_on_next_match`）；标注流程不展示置信度
- **发言列表仅未标注簇渲染（v2.85）**：`unlabeled = clusters 非空且无 assigned_name 且非 skip_label` 才调 `get_speaker_utterances()`；已标注/不标注显示提示「请到文件归档回听整段」、不再加载任何音频片段
- **「🎧 试听发言」已移除（v2.69 加 → v2.85 删）**：原按 `segment_start_offset/end_offset` 从 `audio_path` 切段播放（`render_segment_audio()` 用 soundfile **整文件读入再切片**——大音频加载重/易失败，是「无法加载音频片段」根因）；v2.85 起删除该函数及全部调用（数据库页试听 + 文件归档搜索片段回放），听音频统一到「文件归档」整段回放（`st.audio` 直接喂路径，不做段偏移）
- **标注操作区**（按簇渲染）：未标注 →「标注并回填」+「🚫 设为不标注」；已标注 →「改标并回填」+「改回未知（沿用编号 {label}）」；不标注中 →「↩️ 恢复标注」；改回未知为**两步确认**（`st.session_state[f"unassign_{cluster_id}"]`），操作统一走 `apply_cluster_label()` 与 `set_cluster_skip()`
- **改标即重置（v2.76 / v2.82 补清除）**：`apply_cluster_label` 的标注/改标分支统一走 `assign_cluster_name()`——"改标为他人"或"给 `sample_count>1` 的簇指派姓名"时置 `reset_on_next_match=1`，下次处理命中时重置向量（实现见 §3.3）；`unassign_cluster_name`（改回未知）**同时清 `reset_on_next_match=0`**——休眠标记不再让看板「待重置簇」误计未标注簇，再标注时按规则重新置位
- **不标注语义（v2.43 / v2.75 修正）**：`skip_label=1` 是独立于标注的标记——不写 `assigned_name`、不改 `label`/`embedding`，**不触发任何 transcripts 回填**（编号未变）；匹配层（`voiceprint.py`）照常参与匹配（`load_all_clusters` 仍返回全部簇、沿用编号）但 **v2.75 起不参与向量学习**（与纯 unknown 一致）；不标注簇在说话人下拉显示「🚫 不标注」且标注入口隐藏，恢复标注即 `set_cluster_skip(cid, False)`、入口重新出现（标注→改回→再标注仍全程可逆）

---

## 2. 模型选型与部署

### 2.1 模型清单

| 模型 | 大小 | 运行时内存 | 来源 | 本机 (CPU) 速度 |
|------|------|------|------|----------------|
| Silero VAD (snakers4/silero-vad) | ~1MB | ~200MB | GitHub | >100× 实时 |
| PyAnnote Diarization 3.1 (PyAnnote 4.x 库) | ~11MB | ~1-2GB | HuggingFace | ~2-3× 实时 |
| PyAnnote Embedding (声纹) | ~98MB | ~200MB | HuggingFace | 秒级 |
| Qwen3-ASR-1.7B | ~6.8GB (FP32) / ~3.4GB (bf16) | FP32 ~12-13GB；bf16 ~5.5GB | HuggingFace / ModelScope | FP32 约 1.11× 实时、bf16 约 3.14× 实时（v2.32 单段实测）；实际含逐段固定开销（v2.53，见 §3.4） |

> **精度口径（v2.32 实测 / v2.66 现行）**：现行默认 **`auto` 动态分配**——决策时刻可用内存 ≥ `fp32_min_avail_mb`（默认 12000MB）→ FP32，否则 bf16（详见 §3.4）。历史实测（v2.32）：1.7B 显式 **FP32** 加载（`torch_dtype=torch.float32`）有 oneDNN 优化，速度 **1.11× 实时**（93s 语音转录 103s）、峰值内存 **11.8GB**；v2.31 曾用默认精度（实测为 **bfloat16**，CPU 无 AVX512-BF16 指令回退转换计算），速度 3.14× 实时、内存 5.2GB。内存红线已放宽至 **<12GB**（原 6GB）。

> **⚠️ 性能数据边界（v2.55）**：本表为 **CPU 部署实测（i5-10210U）**，仅作本机回归基线；GPU 部署需重新实测，绝对数值不跨硬件外推。
> **配置不跨硬件迁移**：当前系统受制于 CPU 和内存的限制，性能有限，当前的配置和使用方法，**不应该完整迁移到 GPU 系统**，而是要修改参数和适配（如加载精度、段合并/批处理、内存编排策略）；要根据实际的系统再调参数，达到最优效果。

### 2.2 模型存储路径

所有模型权重存储在 `/home/kevin/asr_sys_local/asr-local/models/`，通过 `HF_HOME` 环境变量统一指向，避免写入 `~/.cache/huggingface/`。

模型加载优先使用 `local_files_only=True` 检查本地目录，缺失时才允许联网下载。

### 2.3 模型加载超时机制

**模型加载阶段**设置 300 秒（5 分钟）超时。v2.17 起模型完全离线（本地缓存），加载耗时一般数秒至数分钟，300 秒足够宽裕、不会误杀正常加载；同时防止本地缓存损坏/磁盘异常时无限等待（避免干等）。超时后抛出 `PipelineError`，被 `process_file` 的异常处理捕获，写入 `state="idle"` + `last_result="failed"` 后退出。

> **超时 Review 结论（v2.17）**：自动超时只保留在「模型加载」阶段（耗时可预期，300s 不会误杀）；「处理执行」阶段（VAD / 说话人分离 / 声纹 / ASR 推理）**不设任何自动超时**——处理耗时与音频时长成正比，超时设短误杀正常长音频、设长等于干等。处理阶段挂死由外部监控（每 10 分钟状态检查）与人工介入兜底，见 §3.2。

### 2.4 内存编排

流水线按阶段串行加载、用完即卸，峰值内存控制在 <12GB（v2.32 放宽：1.7B FP32 实测 11.8GB，16GB 系统留 ~4GB 余量；原 <6GB）：

- 完成 Diarization 后卸载其模型，再加载 ASR 模型
- 每个音频处理结束后强制 GC（`gc.collect()`）
- **卸载时归还空闲内存（v2.66）**：`_unload_asr`/`_unload_diar` 在 `gc.collect()` 后调用 `malloc_trim(0)`——torch CPU 内存池不主动归还峰值，不归还会让下一文件精度决策看到虚低可用内存（实测 FP32 卸载后滞留 ~2GB、下一文件误走 bf16）
- 模型常驻/卸载策略可在 `MEMORY_CONFIG` 中调整

---

## 3. 各模块实现细节

### 3.1 VAD — Silero VAD

#### 选型背景
从 PyAnnote segmentation-3.0 VAD 模式切换为 Silero VAD，原因：
- Silero VAD 更轻量（~1MB vs ~80MB），加载速度更快
- 纯 VAD 任务上效果优于 PyAnnote 的 VAD 模式
- 通过代理可正常从 GitHub 下载

#### 加载方式
通过 `torch.hub.load()` 加载，缓存目录设为 `MODELS_DIR / "silero-vad"`。

```python
torch.hub.set_dir(str(SILERO_CACHE_DIR))
self._model, self._utils = torch.hub.load(
    repo_or_dir="snakers4/silero-vad",
    model="silero_vad",
    source="github",
    trust_repo=True,
)
(self._get_speech_ts, self._get_speech_ts_adaptive) = (
    self._utils[0], self._utils[1]
)
```

#### ⚠️ 关键踩坑：`get_speech_timestamps` 参数顺序

```python
# Silero VAD 的 get_speech_timestamps 函数签名：
def get_speech_timestamps(
    audio,                    # 位置参数 #1
    model,                    # 位置参数 #2
    threshold=0.5,            # 位置参数 #3 ← 注意！
    sampling_rate=16000,      # 关键字参数
    min_speech_duration_ms=250,
    ...
)
```

**错误用法**（第三个位置传 `sr`，被当作 `threshold=16000`，与后面 `threshold=0.5` 冲突）：

```python
self._get_speech_ts(wav, self._model, sr, threshold=0.5, ...)
#                                               ↑
#                          "multiple values for argument 'threshold'"
```

**正确用法**（`sr` 必须作为 `sampling_rate` 关键字参数传入）：

```python
self._get_speech_ts(
    wav,
    self._model,
    threshold=0.5,
    sampling_rate=sr,         # ← 关键字参数，不占 threshold 位置
    min_speech_duration_ms=...,
    ...
)
```

#### 输出格式

Silero VAD 返回字典列表，**`start`/`end` 是采样点（sample），不是毫秒**（`return_seconds=False` 为默认）：

```python
[{"start": 7040, "end": 54400}, ...]  # 采样点（16kHz 下 1 秒 = 16000 采样点）
```

在 `detect()` 中转为秒——**必须除以采样率**：

```python
start_s = seg["start"] / sr   # 采样点 → 秒（v2.18 修复：除以采样率，不是 /1000）
end_s = seg["end"] / sr
```

> **踩坑（v2.18）**：旧代码误将采样点当毫秒（`/ 1000.0`），16kHz 下时间被放大 16 倍——6 秒音频检测出 0~100 秒的语音段，导致 VAD 与 Diarization 段**永远交集失败**，报"无有效语音段，已跳过"。

#### 离线加载（v2.17）
- 加载逻辑改为**离线优先**：本地缓存仓库 `MODELS_DIR/silero-vad/snakers4_silero-vad_master` 存在时，用 `torch.hub.load(repo_or_dir=<本地路径>, source="local")` **完全离线加载**，不与 GitHub 交互
- 仅当本地缓存缺失时才回退 `source="github"` 联网下载一次
- 依赖的权重文件 `src/silero_vad/data/silero_vad.jit` 已下载到本地缓存，运行时无需联网

#### 踩坑记录（v2.16）

- **模型权重缺失导致假网络错误**：Silero VAD 的 `files/` 目录缺少 `silero_vad.jit`（约 1.8MB）时，`torch.hub.load` 每次都会联网下载，网络不通时报 `Remote end closed connection without response`，误以为只是网络问题。修复：带代理下载补齐模型权重（实际路径为 `MODELS_DIR/silero-vad/snakers4_silero-vad_master/src/silero_vad/data/silero_vad.jit`）。
- **torch 2.13 hub 下载 bug**：`torch.hub.load(source='github')` 在 `_validate_not_a_forked_repo()` 中对无 `Authorization` 头的请求执行 `del headers["Authorization"]` 抛 `KeyError`。绕过方式：仓库已在本地时改用 `source='local'` 传入本地路径，或在有代理时直接 `hf_hub`/手动下载权重文件。
- **缓存命中无需联网**：仓库（`snakers4_silero-vad_master`）与权重均缓存完整后，`source='github' + force_reload=False` 直接命中缓存（日志显示 `Using cache found in ...`），无需联网。

### 3.2 Diarization — PyAnnote Speaker Diarization 3.1

#### 版本兼容（v2.16 修复）
- PyAnnote 4.x 与 3.x 存在 API 差异：`use_auth_token` → `token`、返回 `DiarizeOutput` 对象需取其 `.speaker_diarization` 属性
- **踩坑（v2.16）**：pyannote 4.0.7 的 `pipeline()` 返回 `DiarizeOutput` dataclass（字段含 `speaker_diarization: Annotation`），不是 Annotation 对象，直接调用 `.itertracks()` 会报 `'DiarizeOutput' object has no attribute 'itertracks'`。代码中已做兼容：`if hasattr(anno, "speaker_diarization"): anno = anno.speaker_diarization`
- **踩坑（v2.16）**：pyannote 4.x 的 `Pipeline.from_pretrained()` **不支持 `local_files_only` 参数**（会抛 TypeError）。离线优先需改用 `HF_HUB_OFFLINE=1` 环境变量
- **踩坑（v2.16）**：`pyannote/wespeaker-voxceleb-resnet34-LM` 的 `pytorch_model.bin` 必须在本地缓存（约 26MB），缺失时 pipeline 加载会联网下载，网络不通则加载失败

#### 性能
i5-10210U CPU 环境下实际约 2~3 倍实时，1 小时音频约 20~30 分钟。
> 实测修正（v2.16）：长音频（>= 10 分钟）在子进程隔离模式下实际接近 **1 倍实时**（15.3 分钟音频耗时 > 15 分钟），短音频在主进程内约 2~3 倍实时。

#### VAD 静音切除加速（v2.31）
PyAnnote Diarization 内部耗时分三段：**segmentation 滑窗**（256ms 步长遍历全音频，与**总时长**成正比，静音也在白白计算）、**speaker embedding**（对语音段提向量，pipeline 内部 VAD head 本就跳过静音）、**clustering**（量小）。因此切静音收益集中在 segmentation 阶段，收益上限 = 静音占比 × segmentation 耗时占比。

实现（对调用方完全无感）：
1. pipeline 将 Silero VAD 语音段传入 `Diarizer.run(audio, vad_segments=...)`
2. `src/utils/audio_utils.py::build_speech_concatenation()` 按 VAD 段拼接：先合并重叠/紧邻段（Silero 输出含 `speech_pad_ms` 边界，gap < 0.1s 视为同一次说话），再逐段拼接，段内保留原始波形
3. Diarization 跑在拼接音频上，输出段时间戳经映射表**线性映射回原始时间轴**（`map_back`：拼接轴时间 → 原轴偏移）
4. 子进程隔离模式（≥10 分钟）：拼接音频无对应文件，先写临时 wav（`_write_temp_wav`）供子进程按路径加载，完成后清理
5. 开关 `DIARIZATION_CONFIG["use_vad_concat"]`（默认 True）；几乎无静音的连续访谈场景可置 False 规避拼接边界风险

> **风险提示**：拼接处若 VAD 边界截断词句，会影响边界段的 DER；`speech_pad_ms=300` 已提供边界余量。实测对比需关注 DER 与耗时两项指标（PRD FR-003）。

> **⚠️ 坑（v2.57 根治）**：拼接轴上**同一说话人的多个语音片断**会被分离并成一段，映射回原时间轴后**把中间的静音一起包进来**——实测 58.6 分钟文件（VAD 语音仅 5.2 分钟）分离出 17 段、最大一段 **22 分钟**、段总时长 **51.7 分钟**；ASR 因此把含静音的整段送去转录（内存钉满 ~14GB、耗时数小时假死）。v2.57 修复：分离结果映射回原轴后，**与 VAD 语音片断取交集裁剪**（再合并相邻同说话人短段，间隔 ≤ `merge_gap_s`），段总时长回到真实语音量级（5.4 分钟、最大 35.7s）。此修复同时让声纹聚合只使用真实语音。

#### 安全机制（v2.15 + v2.17 修订）
针对说话人分离阶段曾出现的进程崩溃/挂死问题，实施防护：

1. **子进程隔离**：长音频（>= 10 分钟）的 pipeline 推理在独立子进程中运行，OOM 或段错误只杀死子进程，主进程存活并上报错误。短音频（< 10 分钟）仍在主进程内直接运行以降低开销。
2. **崩溃检测**：检查子进程退出码，`-9`（SIGKILL）报告 OOM，`-11`（SIGSEGV）报告段错误，提供针对性建议。
3. **离线优先加载**：`_load_pipeline()` 先设 `HF_HUB_OFFLINE=1` 离线加载本地缓存，失败才回退联网，避免网络波动导致加载卡住。
4. **不设自动超时（v2.17 决策）**：曾尝试动态超时（v2.15: 600s+300s/10min → v2.16: 1200s+900s/10min），实测均不合理——超时设短会**误杀**接近 1 倍实时的正常长音频，设长则等于**干等**。最终决定移除全部自动超时（子进程无限 `join()`），改为**外部监控兜底**：每 10 分钟状态检查任务 + 用户主动发现异常后由 AI 检查进程。真·挂死不再依赖自动化超时终止。

实现细节：
- 子进程通过 `multiprocessing.Process` + `spawn` 上下文启动
- 音频通过文件路径（而非 tensor）传递，避免大 tensor 序列化开销
- 子进程内独立加载 PyAnnote Pipeline 模型，执行推理后将结果通过 `Queue` 传回
- 子进程异常退出（退出码非 0）由主进程感知并上报；挂死由外部监控发现

详见 [diarization.py](file:///Users/kevin/m02_Developer/TRAE_Work_CN/ASR-Local-Thinkpad/asr-local/src/diarization.py)。

### 3.3 声纹识别 — PyAnnote Embedding

#### 匹配机制
- 对每个 Diarization 输出的说话人，聚合全部片段提取声纹向量
- 与声纹库逐一计算余弦相似度（阈值配置于 `config/settings.py` `VOICEPRINT_CONFIG`；自动 0.65 / 疑似 0.50（v2.25 自 0.75/0.60 调低）/ 学习 0.75（v2.79 认名与学习解耦））：
  - `score >= 0.65` → 自动认名（命中已标注簇且 `>= 0.75` 才学习，见下方「标注学习与向量更新」；`0.65–0.75` 只认名不学习）
  - `0.50 <= score < 0.65` → 疑似待确认
  - `score < 0.50` → 未识别，进入声纹簇流程

声纹簇的匹配逻辑、编号规则、标注学习机制见 [PRD FR-003-CLUSTER](./PRD_local_asr_system.md#fr-003-cluster-声纹簇持久化与标注学习)。

#### 标注学习与向量更新（v2.75/v2.76 定稿，用户视角场景表见 [PRD FR-003-CLUSTER](./PRD_local_asr_system.md#fr-003-cluster-声纹簇持久化与标注学习)）
- **认名/学习阈值解耦（v2.79）**：命中已标注簇时，`score >= learn_threshold`（默认 0.75，`VOICEPRINT_CONFIG.learn_threshold`，可改）才增量学习；`[threshold_auto, learn_threshold)`（0.65–0.75）只返回姓名、不学习；改标重置（`reset_on_next_match`）**不受学习阈值限制**——用户已人工确认身份，`>= threshold_auto` 即重新播种。
- **只学已标注簇（v2.75）**：`match_speaker` 命中声纹簇且 `score >= 0.65` 时，**仅当簇已标注（`assigned_name` 非空）才调用 `_learn_into_cluster` 做增量平均**（`new_vec = (旧vec×n + 新vec)/(n+1)`，`sample_count+1`）；纯 unknown / 已取消标注 / skip_label（不标注）簇只返回编号、不更新向量。原因：低质量音源（16kHz 但 32kbps mp3 实测）分离/嵌入不准，3 个说话人会被并成一个簇，若照常学习会把多人向量平均进同一簇造成污染（v2.74 实测 unknown_0044 被 1.5h 低码率音频污染 1 次）。用户标注后该簇重新进入学习。
- **改标即重置（v2.76 / v2.82 补清除）**：`speaker_clusters` 新增 `reset_on_next_match INTEGER DEFAULT 0`（SCHEMA_SQL + `init_db()` 老库 ALTER 迁移），`load_all_clusters()` 读取该字段。`assign_cluster_name()` 在"原已标注且改标为他人"或"给 `sample_count>1` 的簇指派姓名"时置位（同名重指派不重置）；`match_speaker` 命中带标记的已标注簇时调 `reset_cluster_vector()`——embedding **直接替换**、`sample_count=1`、清标记——否则走 v2.75 增量学习。纯新建簇（`sample_count=1`）首次标注不重置，保留该文件作为身份依据。`unassign_cluster_name()`（改回未知）同时清 `reset_on_next_match=0`（v2.82），再标注时按规则重新置位。原因：改标前旧姓名期间累积的样本无法逐条撤销，不重置就只能靠稀释，混合向量会持续带偏匹配、越用越不准。
- **无发言样本的声纹簇（v2.75 记录）**：簇在声纹匹配阶段创建（`register_new_cluster` 立即持久化），早于 ASR 转录入库；转录未产出行（ASR 无文本 / 文件失败 / 批次中断）时该簇无关联 transcript → WebUI「声纹簇·标注学习」中显示 0 条发言（v2.85 起不提供片段试听，如 unknown_0046）。身份追踪与转录成功解耦属预期，不影响匹配；若干扰可后续增加"清理无样本簇"运维功能。
- **行为测试（v2.76 通过，临时库）**：① 新建簇首次标注 → 不重置；② 改标为他人 → 置位；③ 同名重指派 → 不置位；④ 累积簇（`sample_count>1`）标注 → 置位；⑤ 命中待重置簇 → 向量替换、`sample_count=1`、清标记、不学习；⑥ 下次命中 → 恢复正常增量学习（`sample_count=2`）。

### 3.4 ASR — Qwen3-ASR-1.7B

#### 模型类与调用入口（v2.18 重构）
- **模型类是 `AutoModelForMultimodalLM`**，不是 `AutoModelForSpeechSeq2Seq`——Qwen3-ASR 是语音-文本多模态模型（音频编码器 + Qwen LLM）
- **输入必须通过 `processor.apply_transcription_request(audio=...)` 构建**（官方推荐入口，自动处理 chat-template 格式化）。手动拼 `input_features` 会缺文本侧 `input_ids`，报 `Audio features and audio tokens do not match`
- **解码**：`processor.decode(generated_ids, return_format="transcription_only")` 直接得到纯转录文本（自动去掉 `language ...` 标签和 `<asr_text>` 标记）
- 生成时 `max_new_tokens=512`；`generated_ids = generated[:, inputs["input_ids"].shape[1]:]` 截取新生成部分
- 语言：`apply_transcription_request(audio=..., language=...)` 传 `zh` 强制中文，或 `None` 自动识别
- `sampling_rate` 需放进 `processor_kwargs={"sampling_rate": sr}`（直接传会触发 warning）
- 推理后端：Transformers（本地离线，**FP32/bf16 按决策自动**），不依赖 GPU/vLLM

#### 精度决策与输入对齐（v2.31 踩坑 → v2.32 定稿）
- **v2.31 曾用默认精度**：1.7B 权重默认 bf16，而 `apply_transcription_request` 产出的音频特征为 float32，直接推理报 `Input type (float) and bias type (c10::BFloat16) should be the same`。当时修复为按模型首层参数 dtype 自动对齐浮点输入：
  ```python
  model_dtype = next(self.model.parameters()).dtype
  if model_dtype in (torch.float16, torch.bfloat16):
      inputs = {
          k: (v.to(model_dtype) if v.dtype.is_floating_point else v)
          for k, v in inputs.items()
      }
  ```
- **v2.32 定稿 FP32**：CPU 无 AVX512-BF16，bf16 回退转换计算慢（3.14× 实时）；FP32 有 oneDNN 优化（1.11× 实时），代价是权重 ~6.8GB、峰值内存 11.8GB（红线放宽至 <12GB）。上述对齐代码保留，fp32 下不触发，兼容将来默认精度模型。
- **v2.58 精度动态分配（默认 auto，现行口径 v2.66 为"仅按可用内存"）**：`ASR_CONFIG["torch_dtype"]` 默认 **`auto`**——在 `pipeline.process_file` 第 (7) 步决策：**可用内存（`/proc/meminfo MemAvailable`）≥ `fp32_min_avail_mb`（默认 12000MB，v2.65 由 13500 下调）→ `float32`**（oneDNN 优化 1.11× 实时，峰值 ~12-13GB）；否则 → `bfloat16`（内存约减半、3.14× 实时，稳定兜底）。依据：v2.57 语音裁剪后 ASR 峰值主要取决于模型本身（与文件大小/总时长相关性弱），故用"决策时刻可用内存"比 v2.49 的"音频总时长 ≥30 分钟"更精准；`MemAvailable` 读取失败时保守走 bf16。环境变量可覆盖：`ASR_TORCH_DTYPE=float32|bfloat16|auto`、`ASR_FP32_MIN_AVAIL_MB=<MB>`。背景：16GB 机器 FP32 峰值 ~15GB 曾两次 OOM（v2.48，当时分离段含静音）；v2.57 修复后峰值已可控，阈值可在实战中观测微调。**v2.65 实测微调**：16GB 机器（仅跑本任务）决策时可用内存稳定 12.7-13.3GB，13.5GB 阈值永不触发 → auto 全部 bf16（8 分钟语音 ASR 实测 ~57 分钟、约 7-8× 实时；bf16 每段 91-126s，与 v2.53 记录吻合）；下调至 12000MB 后稳定触发 FP32（峰值 ~12-13GB，留 ~1GB 余量），低于阈值仍回退 bf16。
- **v2.66 卸载归还内存（实战发现，pipeline.py §2.4）**：`_unload_asr`/`_unload_diar` 原只有 `del + gc.collect()`，torch CPU 内存池不主动归还峰值——实测 FP32 文件跑完后滞留 ~2GB（进程 RSS 7.4GB，旧 bf16 全程仅 ~5GB），下一文件决策时可用内存被压到 10.7GB（<12000）误走 bf16。修复：两处卸载统一走模块级 `_malloc_trim()`（`ctypes.CDLL("libc.so.6").malloc_trim(0)`，非 Linux 静默跳过；ASR 循环内每 8 段的既有调用同步改为该助手）。验证：终止批处理重启后，首文件决策恢复 float32（可用内存 12908MB）。
- **v2.53 段合并根治（段开销 + 内存峰值）**：ASR 是**逐段转录**，每段固定开销（特征提取 + `generate` 启动 + 最多 512 token 解码）远大于内容转录——实测 FP32 ≈ **25-33s/段**（历史 ~5 分钟语音、20-32 段需 12-13 分钟，并非文档的 1.11× 实时），bf16 再乘 2.5-3×；且单文件 ASR 期间 torch CPU 内存池不归还峰值（实测从 5.3GB 涨到 14GB，逼近 OOM）。v2.53 修复：① **合并相邻短段**——`ASR_CONFIG.segment_merge_gap_s`（默认 1.5s）内间隔合并、合并后段长不超过 `segment_max_s`（默认 60s），把 140 段连续短语音合并到 ~6 段（实测），直接砍掉"段数 × 每段开销"这个主乘数，恢复 VAD 对 ASR 的收益（只花语音时长）；② 段长上限同时约束单段峰值内存；③ 每 8 段 `gc.collect()` 缓解高水位堆积；④ 合并段说话人取"起始分离段"的说话人；合并后每行时间戳为合并段起止（粒度变粗，如需句子级时间戳可后续启用 `return_timestamps` 细分）。环境变量：`ASR_SEGMENT_MERGE_GAP_S` / `ASR_SEGMENT_MAX_S`。
- **v2.56 段长上限收紧 60s→15s + 内存归还 OS（关键修正）**：v2.53 实测发现**段长才是内存与耗时的主因**——58.6 分钟文件分离后仅 17 段、合并成 15 段，但 bf16 下 60s 长段**单段约 6 分钟**（0.7s/token 解码），总 ASR 92 分钟；且每段的大工作集（特征+激活）让 RSS **从开始就钉在 ~14.7GB**，16GB 机器满内存 + swap 假死。修正：① `segment_max_s` 默认 **60s → 15s**（单段生成 token 骤减，单段耗时与峰值内存都大幅下降；5.2 分钟语音预计 10-20 分钟完成）；② 每 8 段在 `gc.collect()` 后调用 **`malloc_trim(0)`**（Linux glibc）把空闲堆归还 OS，避免 RSS 长期钉在高水位；③ 仍可用环境变量 `ASR_SEGMENT_MAX_S` 调整（如大内存机器可放宽）。
- **v2.54 设计原则（用户确认）**：**转录行不严格按说话人切分**——效率优先（系统处理时长短）优先于逐说话人粒度；声纹身份由 `speaker_clusters` 簇承载、不受转录行粒度影响（声纹匹配发生在 ASR 之前、按分离说话人聚合，一个说话人一个簇）；声纹标注可通过单人录音等其他途径（单人录音 → 一个分离说话人 → 一个簇/ID）。转录行粗粒度（合并段归属起始段说话人）是**刻意取舍**，不是缺陷。

#### 离线加载（v2.18 修复 + v2.66 精度动态口径）
Qwen3-ASR 模型以**自定义解压目录**存放于 `MODELS_DIR/Qwen3-ASR-1.7B-hf/`（含 `config.json` / `model.safetensors` / `tokenizer.json` 等）。加载精度由调用方（pipeline auto 决策，见上节）传入；下例为显式 FP32 时的写法。

```python
local_dir = MODELS_DIR / "Qwen3-ASR-1.7B-hf"
if local_dir.exists():
    # 完全离线：直接用本地目录加载（不再走 hub 缓存 / 联网）
    self.processor = AutoProcessor.from_pretrained(str(local_dir), token=hf_token)
    self.model = AutoModelForMultimodalLM.from_pretrained(
        str(local_dir), torch_dtype=torch_dtype, low_cpu_mem_usage=True, token=hf_token)
else:
    # 兜底：hub 缓存(local_files_only) → 联网
    ...
```

> **踩坑（v2.18）**：`local_files_only=True + cache_dir` **只认 HF hub 缓存格式**（`models--Qwen--Qwen3-ASR-1.7B-hf/snapshots/...`），匹配不到自定义解压目录时会误判"本地缺失"→ 回退联网下载 → 无外网环境下失败（`Cannot send a request, as the client has been closed.` / `Network is unreachable`）。必须**先检查自定义目录是否存在**，存在则直接按本地路径加载。

> **踩坑（v2.18）**：processor 返回的掩码键名是 `input_features_mask` 而非 `attention_mask`；generate 不传 `return_timestamps`/`language`（模型不支持会警告 "not used by the model"）。改为官方 `apply_transcription_request` 入口后这些细节由 processor 自动处理，无需手工拼参。

#### ASR 文本清洗
Qwen3-ASR 输出可能包含特殊 token（`<|system|>`、`<|user|>`、`<|assistant|>`、`<|endoftext|>` 等），在写入 TXT/JSON 前统一经 `_clean_asr_text()` 清洗：

```python
# 处理层次：
# 1. 去除所有 <|...|> 和 |...| 格式的特殊 token
t = re.sub(r'<\|?[a-z_]+\|?>', '', t)
t = re.sub(r'\|[a-z_]+\|', '', t)
# 2. 按行检测 role label，去除 system prompt 内容
# 3. 去除行内残留的 role label
# 4. 去除语言前缀（如 "language Chinese"）
t = re.sub(r'\blanguage\s+[a-zA-Z]+', '', t, flags=re.IGNORECASE)
# 5. 合并多余空白
```

> 注意：`\b` 词边界断言在中文前后不生效，`language Chinese` 中 `Chinese` 后面不能跟 `\b`。

### 3.5 时间戳处理

提取策略与计算公式见 [PRD FR-001-TS](./PRD_local_asr_system.md#fr-001-ts-录音开始时间提取-时间戳核心) 和 [PRD FR-005](./PRD_local_asr_system.md#fr-005-时间戳计算与存储-核心功能)；时区规则见 [PRD §7.2](./PRD_local_asr_system.md#72-时间戳格式规范)。

#### 正则表达式（v2.50 定稿，与 settings.py `FILENAME_TIME_PATTERNS` 完全一致）
```python
# 1) 长格式：YYYY-MM-DD_时_分_秒，六个字段分隔符横线/下划线任意混用（可带前后缀）
r"(?P<Y>\d{4})[-_](?P<M>\d{2})[-_](?P<D>\d{2})[-_](?P<h>\d{2})[-_](?P<m>\d{2})[-_](?P<s>\d{2})"
# 2) 紧凑式：YYYYMMDD[-_]HHMMSS（横线/下划线均可，可带前后缀）
r"(?P<Y>\d{4})(?P<M>\d{2})(?P<D>\d{2})[-_](?P<h>\d{2})(?P<m>\d{2})(?P<s>\d{2})"
# 3) ISO 风格：YYYYMMDDTHHMMSS（可带前后缀）
r"(?P<Y>\d{4})(?P<M>\d{2})(?P<D>\d{2})T(?P<h>\d{2})(?P<m>\d{2})(?P<s>\d{2})"
# 4) 全紧凑式：YYYYMMDDHHMMSS 14 位无分隔（v2.74 新增，前后加数字边界防截取）
r"(?<!\d)(?P<Y>\d{4})(?P<M>\d{2})(?P<D>\d{2})(?P<h>\d{2})(?P<m>\d{2})(?P<s>\d{2})(?!\d)"
```
`parse_filename_time()` 按列表顺序 `re.search`（不锚定），时间戳在文件名任意位置均可提取。
实测样例：`Note-20260806152345` → 2026-08-06 15:23:45；`voice_20260806152345` 等同型均可；`20260806152345123`（15 位数字串）因前后数字边界不匹配而不被截取。

### 3.6 错误处理

失败处理逻辑与兄弟文件规则见 [PRD FR-001-AR](./PRD_local_asr_system.md#fr-001-ar-归档与有机重命名) 和 [PRD FR-001-MULTI](./PRD_local_asr_system.md#fr-001-multi-同名多格式文件处理)。

```python
def move_to_error(src: Path, reason: str = "") -> None:
    """处理失败时，将原始音频文件移入 error/ 目录，并生成带时间戳的 .error.txt 日志。
    v2.36：失败文件统一移入 error/（不再留在收件箱），收件箱只保留待处理文件；
    移入文件名与 .error.txt 均附加产生错误的时间戳（YYYYMMDD_HHMMSS），
    防止不同批次 / 不同来源同名文件重名冲突；原始文件保留在 error/ 供排查，
    用户可手动移回收件箱重新处理。"""
    INBOX_ERROR_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # 1) 原始音频文件移入 error/（保留原名；重名时附加时间戳与序号）
    if src.exists():
        dest = INBOX_ERROR_DIR / src.name
        if dest.exists() and dest != src:
            dest = INBOX_ERROR_DIR / f"{src.stem}_{ts}{src.suffix}"
            i = 2
            while dest.exists():
                dest = INBOX_ERROR_DIR / f"{src.stem}_{ts}_{i}{src.suffix}"
                i += 1
        shutil.move(str(src), str(dest))
    # 2) .error.txt 日志（带时间戳，防重名冲突）
    if reason:
        base = src.stem
        note = INBOX_ERROR_DIR / f"{base}_{ts}.error.txt"
        i = 2
        while note.exists():
            note = INBOX_ERROR_DIR / f"{base}_{ts}_{i}.error.txt"
            i += 1
        note.write_text(reason, encoding="utf-8")
```

#### 错误归档（v2.17 重构）

- **共享函数** `src/archive.py::archive_error_files()`：将 `error/` 根目录**全部错误文件**（`.error.txt` 日志 + 失败音频）移入 `error/archived/`，文件名附加**原文件创建时间戳**（优先 statx btime，回退 mtime），彻底避免重名（v2.36 起含失败音频）
- **两个调用方复用同一函数**（避免逻辑重复）：
  1. `process_inbox.py::_archive_old_errors()` — 每次处理开始前自动归档上一轮错误
  2. `webui.py::prepare_inbox()` — 「准备处理收件箱」按钮，用户手动触发归档 + 解锁
- **解锁逻辑（v2.84 起 PID 感知）**：仅当锁文件存在且**非处理中**（陈旧锁 = 超过 6 小时 **或持有 PID 已死**）时删除/接管；正在处理（PID 存活的新鲜锁）保留并提示——崩溃/被杀后无需再等 6 小时
- **失败文件重新入队（v2.84）**：收件箱面板新增「↩️ 失败文件重新入队并处理」按钮——把 `error/` 根目录当前批次失败音频移回收件箱并立即启动处理（`.error.txt` 日志留待下轮归档），替代手动 SSH 移动；`error/archived/` 历史归档不在范围内

---

## 4. 工程经验与教训

### 4.1 VAD 相关
- **Silero VAD 参数顺序**（v2.9）：`get_speech_timestamps` 的第三个位置参数是 `threshold`，不是 `sampling_rate`。`sr` 必须作为 `sampling_rate=16000` 关键字参数传入，详见 §3.1。
- **无有效语音段处理**：静音或噪音文件通过 VAD 后无有效语音段，**移入 `error/` 目录**并生成带时间戳的 `.error.txt` 日志说明原因（v2.36：失败文件统一移入 error/，见 §3.6）。
- **Silero 返回采样点而非毫秒（v2.18）**：`get_speech_timestamps` 默认 `return_seconds=False`，返回的 `start`/`end` 是采样点。16kHz 下必须 `/ sr` 转秒；误 `/ 1000` 会把时间放大 16 倍，导致 VAD 与 Diarization 交集永远为空 → "无有效语音段"。详见 §3.1。

### 4.2 模型加载相关
- **PyAnnote 版本兼容**：PyAnnote 4.x 与 3.x API 不同，需注意 `use_auth_token` → `token` 的变化。
- **模型加载超时**：300 秒超时机制防止下载卡死，超时后强制终止并清理进程。
- **网络问题**：模型加载可能因网络问题卡住，需通过超时机制强制终止。
- **自定义目录 vs hub 缓存（v2.18）**：`local_files_only=True + cache_dir` 只认 HF hub 缓存格式（`models--org--name/snapshots/...`）。若模型以自定义解压目录存放（如 `models/Qwen3-ASR-1.7B-hf/`），会误判"本地缺失"→ 回退联网 → 无外网时失败。**必须先检查自定义目录是否存在，存在则直接按本地路径加载**（详见 §3.4）。
- **Qwen3-ASR 是多模态模型（v2.18）**：模型类必须用 `AutoModelForMultimodalLM`（非 `AutoModelForSpeechSeq2Seq`），输入必须走 `processor.apply_transcription_request()` 官方入口——手动拼 `input_features` 会缺 `input_ids` 文本侧，报 `Audio features and audio tokens do not match`；`processor` 返回的掩码键是 `input_features_mask` 而非 `attention_mask`；generate 不支持 `return_timestamps`/`language` 参数。详见 §3.4。

### 4.3 文本清洗相关
- **正则陷阱**：Python 默认 UNICODE 模式下 `\w+` 匹配中文字符，`\b` 词边界在中文前后不生效。清洗 `language Chinese` 时不能使用 `\b` 作为尾部边界。

### 4.4 文件处理相关
- **文件创建时间**：跨平台拷贝后文件创建时间可能变为拷贝时刻，因此时间戳提取以文件名优先，不依赖文件系统元数据。
- **pydub 回退分支 `del` 未定义变量（v2.59 实战修复）**：`load_audio()` 优先 soundfile，失败走 pydub/ffmpeg 回退——回退分支不产生 `data`，但函数结尾 `del mono, data` 直接引用了未定义的 `data`，抛 `UnboundLocalError: cannot access local variable 'data'`，导致 **m4a（及 soundfile 不支持的 mp3）全部加载失败**。修复：`data = None` 初始化 + `if data is not None: del data`。
- **文件名时间戳格式（v2.50 定稿 / v2.74 扩展，PRD FR-001-TS / §3.5 / settings.py 三处一致）**：按顺序尝试 ① 长格式 `YYYY-MM-DD_时_分_秒`（六个字段分隔符 `[-_]` 任意混用，如 `meeting-2026-07-31-14-30-52`）② 紧凑式 `YYYYMMDD[-_]HHMMSS`（如 `recording_20260731_143052`、`recording-20260731-143052`、`20260731-143052-recording`）③ ISO `YYYYMMDDTHHMMSS`（如 `voice_note_20260731T143052Z`）④ 全紧凑式 `YYYYMMDDHHMMSS`（14 位无分隔，如 `Note-20260806152345`，v2.74 新增；前后加数字边界防截取）；时间前后可带任意前缀/后缀。
- **watchdog 已禁用**：因无法可靠检测子文件夹和拷贝过程中的竞态，改为手动触发处理。
- **`Path.suffix` 陷阱（v2.17）**：`Path("a.error.txt").suffix` 只返回最后一个后缀 `.txt`，**不等于** `.error.txt`。用 `f.suffix != ".error.txt"` 判断永远为真，导致匹配不到任何文件。匹配复合后缀必须用 `f.name.endswith(".error.txt")`。`archive_error_files()` 与 `count_error_files()` 均因此失效过一次。

### 4.5 数据库相关
- **表结构初始化**：使用 `CREATE TABLE IF NOT EXISTS` 确保表结构存在而不覆盖数据。
- **说话人标签更新**：标注后需同步更新 `transcripts` 表及 `text_backups/` 目录中的 TXT/JSON 文件。
- **`upsert_person()` 覆盖语义（v2.19）**：`ON CONFLICT DO UPDATE` 会把**未传字段**覆盖为 NULL。标注回填若只传姓名，会清空已填的性别/出生年/关系/备注。必须先 `get_person()` 判断存在性，仅新建时才调用。详见 §1.6。
- **`next_unknown_label()` 取全表 MAX（v2.19）**：不能基于"最后插入行"（`ORDER BY cluster_id DESC LIMIT 1`）——删除过编号较大的簇后，最后插入行的编号可能已被占用，INSERT 时 UNIQUE 冲突。改为扫描全部行取最大编号 +1。

### 4.6 模型下载与代理配置
- **网络可达性**：能否直连海外（含 huggingface.co/Google）取决于运行节点所处网络环境（办公室可直连、家网需本地代理）；**hf-mirror.com 兜底可用**——`HF_ENDPOINT=https://hf-mirror.com bash step2_download_models.sh`（覆盖 v2.31/v2.35 "hf-mirror 不可用"的旧结论，网络环境变化所致）。代理工具与网络地址等敏感细节在本机另行维护，不随仓库发布。
- **huggingface-cli 已废弃（v2.46）**：新版 huggingface_hub 中 `huggingface-cli` 只打印提示、不再执行下载；step2 已改用 Python `snapshot_download`。
- **模型目录组织（v2.46 定稿，与运行时逐一对齐——路径配合，缺一不可）**：
  ```
  models/
  ├── Qwen3-ASR-1.7B-hf/                 # asr.py 自定义目录直接加载（不经过 hub 缓存）
  ├── silero-vad/snakers4_silero-vad_master/  # vad.py torch.hub.set_dir 本地缓存
  └── hub/                               # HF hub 缓存（HF_HOME=models ⇒ $HF_HOME/hub）
      ├── models--pyannote--speaker-diarization-3.1/          # 管线配置 + handler
      ├── models--pyannote--segmentation-3.0/                 # 分段模型
      ├── models--pyannote--wespeaker-voxceleb-resnet34-LM/   # 3.1 默认声纹嵌入（config.yaml 引用）
      ├── models--pyannote--speaker-diarization-community-1/  # 3.1 的 PLDA 打分依赖（plda/xvec_transform.npz 等）
      └── models--pyannote--embedding/                        # 声纹匹配（voiceprint.py）
  ```
  **坑（v2.46）**：3.1 管线离线加载依赖 `speaker-diarization-community-1`（PLDA），曾因"看名字像没用"误删导致离线加载失败（`OfflineModeIsEnabled` 拉取 `plda/xvec_transform.npz`），已恢复。**核对/删除模型目录必须以实际离线加载（`HF_HUB_OFFLINE=1` 跑 pipeline）为准，不能只凭目录名判断**。step2 旧版下载的松散目录（`pyannote-speaker-diarization-3.1` 等）运行时并不读取（PyAnnote 4.x 走 hub 缓存），纯冗余。
- **本地代理（open_proxy）**：开启/关闭/状态命令、内核位置、端口与环境变量口径属敏感信息，在本机另行维护、不随仓库发布。**脚本化/无 sudo 场景**：优先 `HF_ENDPOINT=https://hf-mirror.com` 或开发机中转；代理已开启的 shell 中设置 `HTTPS_PROXY` 指向本地代理端口即可让下载走代理。
- **默认路径残留目录防护（v2.47 → v2.83 升级为硬拒绝）**：`db.py::connect()` 在「无显式 `db_path` + 未设 `ASR_ARCHIVE`」时**直接抛 `RuntimeError`**，不执行 `ensure_parent_dir`、不创建默认路径——v2.47 只打 stderr 告警仍复发（2026-08-07 11:01 临时查询未 source .env 再次制造 `/home/kevin/audio_archive` 0 字节空库，已清理），升级为硬失败；正确路径 `/home/kevin/asr_sys_local/audio_archive` 不受影响。需要临时查询请先 `source <工程根>/.env`，或显式传 `db_path`。

### 4.7 WebUI 样式踩坑
- **CSS 选择器精准命中**：面板底部留白的选择器必须精准命中单个面板（`stVerticalBlock:has(> [data-testid="stElementContainer"] .panel-head)`）。先用 `stVerticalBlockBorderWrapper`（当前版本不存在，样式整体失效），再试 `stVerticalBlock:has(.panel-head)`（误命中祖先容器，形成"整片大白块"），最终定为现在的精准选择器。
- **顶部锁定导航条（v2.38 实现 / v2.39 修复生效 / v2.41 单行定稿 / v2.61 五页签 + 时间移出 / v2.62 宽度回归折行 / v2.63 加宽分组 / v2.64 去缝隙对齐）**：页首 + 页签同排（`st.columns`）后整条吸顶。关键坑：① **不能用 `:first-child` 定位吸顶行**——CSS 注入的 `st.markdown` 才是主 vertical block 的第一个元素，必须用 `:has(.topbar-title)` 精确定位页首行；② **吸顶目标必须是 `stLayoutWrapper` 而非 `stElementContainer`**——Streamlit 1.60 的 `st.columns` 顶层容器是 `stLayoutWrapper`（实测 sticky 有效），`stElementContainer` 只是列内部元素的包装（v2.38 因此整块样式未命中：不吸顶、无底边框、无留白）；③ **吸顶条留白不能用元素 margin**——sticky 元素的 margin 区域透明，下层内容滚动时会从 margin 处透出，留白一律用 padding；④ **分段控件 1.60 渲染为 `div[role="radiogroup"]`+button**（`stSegmentedControl label` 结构已不存在），选中态用 `button[aria-checked="true"]`，老版本规则保留兜底；⑤ **v2.41 单行布局**：`.topbar-title` 改 `flex-direction: row` + `align-items: baseline` + `white-space: nowrap`（品牌 1.05rem/600 与北京时间 0.82rem 同行不换行），导航右移用 `div[role="radiogroup"] { margin-left: 0.75rem }`（+12px，列比保持 1.2:1.8）；⑥ **v2.61 五页签 + 时间移出**：`NAV_OPTIONS` 增为 5 个短标签（状态概览/处理记录/数据库/文件归档/访问控制），`.topbar-time` 移除、首页新增「北京时间」面板；⑦ **v2.62 宽度回归 + 折行**：`div[role="radiogroup"]` 去掉 `width:100%`、`button` 去掉 `flex:1 1 0`，改回 `flex:0 0 auto; min-width:0`（宽度随文字内容，实测 状态概览/处理记录/文件归档/访问控制 90px、数据库 76px），容器加 `flex-wrap: wrap`——窄窗口自动折行（实测 480px 视口折成两行）；⑧ **v2.63 加宽 + 分组**：nav button 加 `padding-left/right: 1.5rem !important`（实测 4 字页签 90→107px、数据库 76→93px，仍按文字自适应）；「访问控制」页白名单按固定性分组——固定放行置顶、设备白名单置底，新增表单保持面板底部；⑨ **v2.64 去缝隙 + 行对齐**：移除 radiogroup 的 `gap:6px`（页签恢复无缝连续外观），tab 内文字 `letter-spacing` 0.02em→0.05em（实测 0.7px）；白名单每行 `st.columns([1.2, 3.2, 1], vertical_alignment="center")`——实测「移除」按钮与左侧 IP 中心 Y 一致；⑩ **v2.71 水平对齐**：「固定」由 `st.caption` 改为全宽居中 `<div>`、「移除」按钮加 `use_container_width=True`——两者同宽居中，消除按钮偏向右侧的错位感。

### 4.8 工程组织与部署
- **部署清单：每次部署必改 `UI_VERSION`（v2.79 记录）**：`scripts/webui.py` 顶部 `UI_VERSION = "YYYY-MM-DD-HH:MM:SS"` 是页脚「部署时间」的唯一来源，**每次向 ThinkPad 部署 webui.py 前必须更新为 ThinkPad 当前时间**（远端 `date "+%Y-%m-%d-%H:%M:%S"`），否则页脚显示旧部署时间、无法核对线上版本（历史部署验证条目均以 UI_VERSION 落款）。提交类型：chore，不升版本号。

- **目录结构扁平对齐生产（v2.26 起，v2.30 最终扁平）**：所有有效内容位于 **git 仓库根一级**——README.md、.gitignore、代码目录 `asr-local/`、数据目录 `audio_archive/`、`audio_inbox/`（.gitkeep 占位，内容不入库）、PRD/TDD 文档。仓库根内容与运行节点 `/home/kevin/asr_sys_local/` **一一对应**（代码目录 ↔ 运行节点 `asr_sys_local/asr-local/`，数据目录 ↔ 运行节点同名数据目录）。GitHub 仅在仓库根渲染 README，README 位于根一级（v2.29/v2.30）：

  ```
  ASR-Local-Thinkpad/               （= git 仓库根，GitHub 首页渲染 README；内容与运行节点 /home/kevin/asr_sys_local 一致）
  ├── README.md                     # 项目说明（英文）
  ├── .gitignore                    # 排除机密/数据/模型
  ├── asr-local/                    （= 部署源，含全部代码）
  │   ├── config/        # 全局配置
  │   ├── scripts/       # 入口程序（webui / process_inbox / CLI 工具 / 模型下载）
  │   ├── src/           # 核心模块（VAD / 说话人分离 / 声纹 / ASR / 数据库 / 归档）
  │   ├── src/utils/     # 通用工具（音频 IO / 时间戳 / 哈希）
  │   ├── systemd/       # 系统服务单元
  │   ├── run.sh         # CLI 主菜单（PROJ_ROOT = asr-local）
  │   ├── deploy_webui.sh# 部署脚本（LOCAL_ROOT = asr-local）
  │   └── requirements.txt
  ├── audio_inbox/       # 数据：收件箱（.gitkeep 占位，内容不入库）
  ├── audio_archive/     # 数据：归档与数据库（.gitkeep 占位，内容不入库）
  ├── PRD_local_asr_system.md    # 需求文档
  └── TDD_local_asr_system.md    # 技术设计文档
  ```

  `deploy_webui.sh` 的 `LOCAL_ROOT` 基于脚本自身位置（`$(dirname "$0")`），位于 `asr-local/` 内自动指向部署源根；`REMOTE_ROOT` 硬编码 `/home/kevin/asr_sys_local/asr-local`，两端目标一致，部署逻辑无需改动。
- **run.sh 工程根路径 bug（v2.22 修复）**：`PROJ_ROOT` 原用 `$(cd "$SCRIPT_DIR/.." && pwd)` 多退一层——run.sh 位于工程根时 `..` 指向父目录（ThinkPad 上为 `asr_sys_local` 而非 `asr-local`），导致 `.venv`/`.hf_token` 定位错误、run.sh 实际不可用。改为 `$(cd "$SCRIPT_DIR" && pwd)`（run.sh 与工程根同层）。
- **GitHub 版本管理（v2.22）**：工程已托管至公开仓库 `Kevyn-2021/local-asr-sys`。MacBook 为**唯一 git 源**；`.gitignore` 排除机密（`.env`/`.hf_token`）与个人数据（音频/数据库/模型权重/`sample_audio`）；**ThinkPad 不纳入 git**（含机密与运行资产），继续由 `deploy_webui.sh` 同步代码，两者各司其职。
- **部署地址可配置（v2.23）**：ThinkPad 常随网络环境切换地址，`deploy_webui.sh` 的 `REMOTE_HOST` 支持 `ASR_REMOTE_HOST=kevin@<当前IP>` 环境变量覆盖（默认地址本机维护，不随仓库发布）。v2.23 部署验证：平铺重构后开发机与运行节点 **18 个运行时文件 md5 全量一致**，`run.sh` 的 `PROJ_ROOT` 修复在运行节点生效（`/home/kevin/asr_sys_local/asr-local`）。
- **webui 重启不杀批处理（v2.84）**：`asr-webui.service` 增加 `KillMode=process`——systemd 重启/停止服务只终止 webui 主进程，不再对同 cgroup 的 `process_inbox.py` 子进程发 SIGTERM（2026-08-07 16:05 实战：v2.83 部署重启把 webui 启动的批处理杀成 Diarization 失败、文件移入 error/）。注意：`deploy_webui.sh` 的 4/5 步会 `systemctl --user restart`，修改单元后必须同步单元文件 + `daemon-reload` 才能生效。
- **默认路径制造残留目录（v2.21 根治）**：`settings.py` 的默认值（`PROJ_ROOT=~/asr-local`、`ARCHIVE_DIR=~/audio_archive`、`MODELS_DIR=model_cache`）只在 `.env` 未加载时生效；而代码里多处 `Path.mkdir(parents=True, exist_ok=True)` 会自动创建这些默认目录——`~/audio_archive`、`~/asr-local/model_cache` 因此各出现过一次并被清理。根源是 CLI 入口（`run.sh`）此前只读 `.hf_token`、不加载 `.env`。v2.21 起 `run.sh` 启动时 `source .env`（`set -a` 导出）并强制注入 `ASR_PROJ_ROOT`，CLI 与 WebUI 共用生产路径。**v2.83 起 db 打开路径被硬拦截**：`connect()` 未设 `ASR_ARCHIVE` 时直接报错（见上文防护条目），不再自动 mkdir 制造 DB 残留；settings 默认值本身保留，其余模块（如收件箱/日志）的 mkdir 仍可能随脚本创建，排查此类残留时看目录名是否为 settings 默认值 + 目录 mtime。
- **暂存区与生产区漂移**：MacBook 工程根目录（`ASR-Local-Thinkpad/`）是部署源，但 `deploy_webui.sh` 原先只部署 Web 相关文件，CLI 配套（`run_pipeline.py`/`enroll_voiceprint.py`/`step2_download_models.sh`/`run.sh`）未纳入部署，导致暂存区被改动后与 ThinkPad 生产版本漂移（`src.config.settings` 错误导入、`enroll_voiceprint.py` 中文引号 SyntaxError 等）。v2.19 起部署脚本纳入全部运行时（`config/settings.py` 除外），并新增远端 CLI 导入校验。
- **`config/settings.py` 为设计例外（v2.33 明确；v2.37 修正"入 git"口径）**：settings.py 的特殊之处是**不随 `deploy_webui.sh` 部署**（脚本注释）——ThinkPad 保留自己的生产配置，运行时由 `.env`（`HF_HOME`/`ASR_PROJ_ROOT`/`ASR_ARCHIVE`/`ASR_INBOX` 等）覆盖；但它**纳入 git 版本管理**（不是"不入 git"）。v2.37 起以 **ThinkPad 生产版本为基准**上传（`MODELS_DIR` 默认值统一为 `PROJ_ROOT / "model_cache"`），MacBook 与 ThinkPad **两端文件完全一致**，原"本地 models / 生产 model_cache"的默认值差异不再存在（MacBook 开发机无模型权重、无本地 .env，统一无副作用）。部署脚本不推送 settings.py 属**设计约定**（部署覆盖会冲掉 ThinkPad 上手工调整的配置），而非内容差异。修改 settings.py 后：① 手动 `scp config/settings.py` 同步到 ThinkPad（两端内容相同，无需 sed）；② 提交 git 保持版本管理。
- **一次性过程稿不进部署源**：`step1_setup.sh` 是初装期一次性脚本，路径停留在旧布局（`~/asr-local`、`~/audio_archive`、`model_cache`），不再匹配当前 `~/asr_sys_local/` + `models/` 布局，v2.19 删除；`systemd/asr-webui.service` 与 `install_services.sh` 的 .env 模板同步对齐生产路径（含"用户级 service 不含 User/Group 行"约束）。
- **命名残留清理**：VAD 切换为 Silero 后遗留的 `PyAnnoteVad` 别名、`pipeline.py` 中混用的相对/绝对导入、`webui.py` 复制的 `AUDIO_EXTS`、`process_inbox.py` 重复定义的 `BJT`、`voiceprint.py` 未用的 `db_conn` 参数等一次性清理，保持单一事实来源（统一从 `config.settings` 导入、统一 `src.*` 绝对导入）。

---

## 5. 配置参考

### 5.1 路径配置

```python
PROJ_ROOT = ~/asr-local                     # 代码目录
INBOX_DIR = ~/audio_inbox                    # 收件箱
ARCHIVE_DIR = ~/audio_archive                # 归档根目录
MODELS_DIR = Path(os.environ.get("HF_HOME", PROJ_ROOT / "model_cache"))
DB_PATH = ARCHIVE_DIR / "transcripts.db"     # 数据库
```

> 注：`model_cache` 为 settings.py 默认兜底（MacBook 无 .env 时用）；ThinkPad 生产环境由 `.env` 的 `HF_HOME=/home/kevin/asr_sys_local/asr-local/models` 覆盖（v2.37 口径：两端文件内容一致，差异仅来自 .env）。

### 5.2 音频格式

```python
SUPPORTED_EXTENSIONS = {'.wav', '.mp3', '.m4a', '.flac', '.ogg', '.opus', '.webm'}
FORMAT_PRIORITY = {
    ".wav": 0, ".flac": 1, ".m4a": 2, ".mp3": 3,
    ".opus": 4, ".ogg": 5, ".webm": 6,
}
```

### 5.3 时间戳

```python
TIME_SOURCE_PRIORITY = ["filename", "file_birthtime"]
TIME_SOURCE_MISMATCH_THRESHOLD_SECONDS = 300  # 5 分钟
ORGANIC_OUTPUT_FORMAT = "absolute"     # absolute / relative / both
TIMEZONE = "Asia/Shanghai"
```

### 5.4 VAD 配置

```python
VAD_CONFIG = {
    "threshold":         0.5,
    "min_speech_len_s":  0.25,
    "min_silence_len_s": 0.1,
    "speech_pad_ms":     300,
    "sample_rate":       16000,
}
```

> v2.33：移除 `max_speech_len_s`（30.0）——原为"单段语音最大时长"预留配置，代码从未读取（Silero 默认不限制），属残留死配置。

### 5.5 内存编排

```python
MEMORY_CONFIG = {
    "stage_unload":      True,    # 阶段完成后卸载模型内存
    "force_gc_each":     1,       # 每个音频结束后强制 GC
}
```

---

## 6. 变更日志

| 版本 | 日期 | 变更内容 |
|------|------|---------|
| v1.0 | 2026-07-31 | 初始版本 |
| v1.1 | 2026-07-31 | **重大更新**: 强化时间戳系统，增加绝对时间计算、时区处理、防篡改机制、时间戳验证 |
| v1.2 | 2026-08-01 | **需求对齐更新**: ① 新增声纹库与说话人识别 (FR-003-VID)；② 新增归档有机重命名 (FR-001-AR)；③ 时间戳来源改为**创建时间优先 + 文件名校验 + 手动确认兜底**；④ 新增 Tailscale 跨设备安全访问 (FR-008-T)；⑤ 统一北京时间 (UTC+8)；⑥ 去重改为内容 SHA-256 哈希；⑦ ASR 仅保留 Qwen3-ASR-0.6B |
| v1.3 | 2026-08-01 | **工程实现对齐**: ① 新增 FR-001-DIR 子文件夹递归扫描；② 新增 FR-001-MULTI 同名多格式处理；③ VAD 模型更新；④ PyAnnote 库版本确认 4.0.7；⑤ 路径规范化 `/home/kevin/asr_sys_local/`；⑥ 配置对齐 `settings.py`；⑦ Qwen3-ASR 需设 `bos_token_id`、模型加载优先 `local_files_only=True` |
| v1.4 | 2026-08-01 | **系统看板 + 精简**: ① FR-008 重新设计：4 个 Tab（系统状态/处理记录/声纹库/搜索），优先级从 P1 提升至 P0；② 访问方式明确；③ 删除 2.2 方案评估表格；④ 里程碑更新 |
| v1.5 | 2026-08-01 | **UX 细化 + 信息可视化**: ① FR-008 精简合并与 §8 重复内容；② 新增系统已运行时长和上次处理完成时间；③ 新增数据存储面板；④ 第 8 节重构为先分类再布局；⑤ 新增数据库结构可视化；⑥ 新增归档文件浏览；⑦ FR-001-AR 新增空文件夹清理 |
| v1.6 | 2026-08-01 | **看板全面重构**: ① 新增 4 态状态机；② 状态带 UI；③ UI 基调白底简洁；④ Tab 重命名；⑤ 修复 watchdog 事件漏采 bug；⑥ 新增状态机定义表 |
| v1.7 | 2026-08-01 | **PRD 去重 + UI 增强**: ① §6.2/§6.3 去重；② 修复排队中误报；③ UI 区块化；④ 处理中显示阶段进度条；⑤ 概览页 10 分钟自动刷新 |
| v1.8 | 2026-08-01 | **KVI 视觉风格**: ① 灰阶为基；② 暖赭作标点；③ 克制层级；④ 状态说明统一；⑤ 卡片内部紧凑 |
| v1.9 | 2026-08-01 | **WebUI 布局精修**: ① 隐藏 Streamlit 顶部工具栏；② 标题移到左上角；③ Tab 改成文字导航式；④ 区块间隔加大 |
| v2.0 | 2026-08-01 | **KVI 深化 + 减少边框卡片**: ① 数字放大至 3rem；② 边框卡片→白底圆角模块；③ 区块间距加大至 3rem；④ 系统负担条自绘；⑤ 统计数字 flex 布局；⑥ 归档文件浏览改用原生 HTML；⑦ 隐藏汉堡菜单；⑧ 标题增大 |
| v2.1 | 2026-08-02 | **WebUI 实现再对齐**: ① 导航定稿 `st.segmented_control`；② 状态带改为圆点+文字纯指示器；③ 隐藏 Streamlit header 条；④ 页脚改为版本时间戳；⑤ 新增 `.streamlit/config.toml` 主题配置；⑥ 新增一键部署脚本 `deploy_webui.sh`；⑦ 统计数字从矩形色块改为纯文本大数字行；⑧ FR-001 改为手动触发处理，watchdog 移除；⑨ 面板内部底部留白修复 |
| v2.2 | 2026-08-02 | **声纹簇 + 人物档案 + 中文搜索修复**: ① 界面调整：最近处理移至处理记录页顶部，新增音频处理流程面板；② 中文搜索修复 FR-008-S：新增 `src/fts.py` + jieba 分词 FTS 表；③ 新增 FR-003-CLUSTER 声纹簇；④ 新增 FR-010 人物档案；⑤ 导入统一修复为顶层导入；⑥ VAD 实现对齐 PRD；⑦ 设置变量名统一 |
| v2.3 | 2026-08-02 | **声纹标注学习定稿**: ① 取消专门录声纹环节，标注学习主流程；② 处理成果统计调整；③ 人物档案表单体感优化；④ 删除测试音频及其产物；⑤ 存量编号统一为 `unknown_XXXX` 四位小写；⑥ 记录 ThinkPad 代理 |
| v2.4 | 2026-08-02 | **说话人显示名映射 + 全文 review**: ① 新增 `speaker_display_map()` 显示层映射；② 全文一致性 review 修正编号写法、架构图、FR 描述 |
| v2.5 | 2026-08-02 | **状态机修正 + 异常兜底 + ASR 文本清洗**: ① 状态机 5 分钟兜底；② 模型加载失败兜底；③ `_clean_asr_text()` 清洗特殊 token；④ 存量数据回填 transcripts 表 + text_backups 文件 |
| v2.6 | 2026-08-02 | **状态机升级至 5 态**: 新增处理失败状态，derive_state() 7 步优先级精确化，WebUI 预启动机制，按钮繁忙逻辑 |
| v2.7 | 2026-08-02 | **VAD 切换 + 格式精简 + 加载超时**: ① VAD 从 PyAnnote VAD 切换为 Silero VAD；② 输出格式移除 SRT；③ 模型加载 300 秒超时；④ 6 阶段进度条三态显示；⑤ 首页文件名显示修复 |
| v2.8 | 2026-08-02 | **兄弟文件删除**: ① `_archive_brother_files()` → `_delete_brother_files()`；② 清理错误路径；③ 已归档冗余格式清理 |
| v2.9 | 2026-08-02 | **错误处理逻辑修正**: ① `move_to_error()` 重写为仅生成 `.error.txt` 日志，不移文件；② 失败时兄弟文件保留；③ `count_error_files()` 改为统计 `.error.txt` 日志数量 |
| v2.10 | 2026-08-02 | **VAD 参数顺序修复**: `get_speech_timestamps()` 第三个位置参数是 `threshold` 而非 `sampling_rate`，`sr` 必须作为 `sampling_rate=sr` 关键字参数传入。修复前报 `"multiple values for argument 'threshold'"`。详见 §3.1 |
| v2.11 | 2026-08-02 | **进度时间戳 + 标注计数修复**: ① `_status()` 新增 `processing_start_time` 和 `stage_start_time` 自动追踪，处理中时 WebUI 展示处理起始时间/已耗时、当前步骤起始时间/已耗时；② 标注声纹计数从 `COUNT(*)` 改为 `COUNT(DISTINCT assigned_name)`，按唯一姓名去重。详见 §1.4、§1.5 |
| v2.12 | 2026-08-02 | **人物档案声纹关联**: `list_persons()` 新增 `has_voiceprint` 字段，通过 `EXISTS` 子查询自动判断人物是否已有声纹簇标注；WebUI 人物档案面板新增"是否已标注声纹"列。详见 §1.6 |
| v2.13 | 2026-08-02 | **状态机精简为 3 态**: 移除从未出现的"已停止"和"排队中"状态，`derive_state()` 优先级从 9 步精简为 8 步。详见 §1.3 |
| v2.14 | 2026-08-02 | **错误文件归档**: `process_inbox.py` 新增 `_archive_old_errors()`，每次处理前将旧 `.error.txt` 移入 `error/archived/`；error/ 根目录只保留当前批次错误 |
| v2.15 | 2026-08-02 | **说话人分离安全机制**: ① 子进程隔离：长音频(>=10分钟)在独立子进程中运行，OOM 不拖垮主进程；② 动态超时：基准 600s + 每 10 分钟 300s，上限 3600s；③ 退出码检测：区分 OOM(SIGKILL/-9) 和段错误(SIGSEGV/-11)；④ 信号处理器：SIGTERM/SIGINT 自动清理锁文件和状态。详见 §3.2 |
| v2.16 | 2026-08-02 | **Diarization 兼容修复 + 离线加载 + 超时放宽**: ① pyannote 4.x 返回 `DiarizeOutput` 需取 `.speaker_diarization`（修复 `'DiarizeOutput' object has no attribute 'itertracks'`）；② `_load_pipeline()` 改用 `HF_HUB_OFFLINE=1` 离线优先（pyannote 4.x 不支持 `local_files_only` 参数）；③ 超时放宽为基准 1200s + 每 10 分钟 900s、上限 7200s（原策略误杀 1 倍实时的长音频）；④ 补齐 Silero VAD `silero_vad.jit` 权重（缺失时假网络错误）；⑤ 实测长音频子进程模式接近 1 倍实时。详见 §3.1、§3.2 |
| v2.17 | 2026-08-02 | **错误时间戳 + 准备处理按钮 + 移除自动超时 + 完全离线**: ① 错误文件命名附加产生时间戳 `{源文件}_{YYYYMMDD_HHMMSS}.error.txt`（§3.6）；② 归档逻辑重构为共享函数 `src/archive.py::archive_error_files()`，归档时附加原文件创建时间戳；③ WebUI 新增「准备处理收件箱」按钮（归档旧错误 + 解锁残留锁文件），与「开始处理收件箱」并排（§3.6）；④ **移除说话人分离全部自动超时**（v2.15/v2.16 策略实测会误杀或干等），子进程无限 `join()` + 崩溃检测，挂死由外部每 10 分钟监控发现（§3.2）；⑤ **模型完全离线**：Silero VAD `source='local'` 本地加载（§3.1）、Qwen3-ASR `local_files_only=True` 优先（§3.4）、声纹 `HF_HUB_OFFLINE=1` 优先（§3.3）；⑥ **修复 `.error.txt` 后缀判断 bug**：`Path.suffix` 只返回最后一个后缀（`.txt`），`f.suffix != ".error.txt"` 永远为真导致 `archive_error_files()` 匹配不到任何文件，改为 `f.name.endswith(".error.txt")`（§3.6）；⑦ **超时 Review 结论**：自动超时仅保留在模型加载阶段（300s，不误杀），处理执行阶段一律不设超时（§2.3） |
| v2.18 | 2026-08-02 | **刷新频率放宽 + 进度面板表格化 + ASR 全链路修复 + VAD 单位修复**: ① 概览页处理中自动刷新 5s→15s（§1.4）；②「处理进度」面板改为 3 行 3 列表格（表头：任务/起始时间/耗时；总任务行 + 当前步骤·XXXX 行，起始时间黑色、总耗时赭红、当前步骤耗时暖赭），新增 `fmt_full_time()` 显示完整时间（§1.4）；③ **Qwen3-ASR 离线加载修复**：模型以自定义解压目录 `MODELS_DIR/Qwen3-ASR-0.6B-hf/` 存放，`local_files_only=True` 只认 hub 缓存格式导致误判"本地缺失"回退联网失败，改为**自定义目录存在则直接按本地路径加载**（§3.4）；④ **Qwen3-ASR 调用方式重构**：模型类从 `AutoModelForSpeechSeq2Seq` 改为 **`AutoModelForMultimodalLM`**，输入改用官方 `processor.apply_transcription_request()` 入口，解码用 `return_format="transcription_only"`——修复手工拼参导致的 `missing 'audio'`、`input_features_mask` 缺失、`bos_token_id` 未定义、`Audio features and audio tokens do not match` 一系列错误（§3.4）；⑤ **Silero VAD 时间戳单位修复**：返回的是采样点非毫秒，`/1000` 改为 `/ sr`，修复 6 秒音频检测出 0~100 秒语音段导致"无有效语音段"（§3.1）；⑥ **VadSegment 字段名修复**：`vad_has_overlap()` 误用 `.start/.end`，改为 `.start_offset_s/.end_offset_s`（§3.1）；⑦ 修掉 `apply_transcription_request` 的 `sampling_rate` warning（改用 `processor_kwargs`） |
| v2.19 | 2026-08-03 | **总任务行空白修复 + 人物档案保留 + 编号健壮性 + 工程代码 Review 清理**: ① `_status()` 写入判断 `"processing_start_time" not in data` → `not data.get(...)`，修复空闲清理后下次任务"总任务"行永远空白（§1.4）；② 标注回填改为 `get_person()` 判断、仅新建时才 `upsert_person()`，不再清空已填的性别/出生年/关系/备注（§1.6）；③ `next_unknown_label()` 改为扫描全表取 MAX(编号)+1，删除大编号簇后不再 UNIQUE 冲突（§4.5）；④ `clean_text()` 复用 `archive._clean_asr_text` 消除展示层重复实现；⑤ **工程 Review 清理**：修复 `enroll_voiceprint.py` 中文引号 SyntaxError 与 `src.config.settings` 错误导入、移除 `PyAnnoteVad` 遗留别名、pipeline 统一绝对导入、函数内 import 上提、`AUDIO_EXTS`/`BJT`/`db_conn` 去重、`run.sh` banner 旧路径、systemd 单元与 `install_services.sh` 对齐生产路径、`step2_download_models.sh` 对齐 `models/` 目录（§4.8）；⑥ `deploy_webui.sh` 纳入 CLI 配套文件并新增远端导入校验，删除一次性过程稿 `step1_setup.sh`（§4.8） |
| v2.20 | 2026-08-03 | **声纹标注校准**: ① `db.py` 新增 `unassign_cluster_name()`（清空 assigned_name，编号保留）；② `webui.py` 新增 `apply_cluster_label()` 统一「标注为某人 / 改标他人 / 改回未知」三种操作的回填逻辑（§1.7）；③「声纹簇·标注学习」面板重构——列出全部簇、`st.tabs` 双操作区、改回未知两步确认防误操作（§1.7）；④ 改回未知沿用簇原编号（label 为稳定身份，标注→改回→再标注全程可逆，不产生新编号）（§1.7）；⑤ 数据层实测通过：标注回填、档案保护、改回可逆三路径 PASS |
| v2.21 | 2026-08-03 | **CLI 环境变量根治（默认路径残留）**: ① `run.sh` 启动时自动 `source .env`（`set -a` 导出生产环境变量）并**强制注入 `ASR_PROJ_ROOT`**，CLI 与 WebUI 共用同一套生产路径（§4.8）；② 根治未加载 `.env` 时 settings 走默认值（`PROJ_ROOT=~/asr-local`、`ARCHIVE_DIR=~/audio_archive`、`MODELS_DIR=model_cache`）在 HOME 下自动 `mkdir` 制造残留目录的问题——`~/audio_archive`、`~/asr-local/model_cache` 均已出现过并被清理（§4.8）；③ `.hf_token` 降级为无 `.env` 时的兜底 |
| v2.22 | 2026-08-03 | **工程平铺重构 + GitHub 版本管理**: ① 目录平铺——`scripts/pkg_staging/` 套壳上提为单层工程根（config/scripts/src/systemd/run.sh/deploy_webui.sh），工程根 = git 仓库根 = 部署源，与 ThinkPad 生产布局一致（§4.8）；② 修复 `run.sh` 的 `PROJ_ROOT` 多退一层 bug（`SCRIPT_DIR/..` → `SCRIPT_DIR`，run.sh 与工程根同层），此前该 bug 使 run.sh 实际不可用（§4.8）；③ 修复 `deploy_webui.sh` 的 `LOCAL_ROOT`（去掉 `pkg_staging` 段）（§4.8）；④ 新增 GitHub 公开仓库 `Kevyn-2021/local-asr-sys`，`.gitignore` 排除机密（`.env`/`.hf_token`）与个人数据（音频/数据库/模型/`sample_audio`），MacBook 为唯一 git 源、ThinkPad 不纳入 git 继续 deploy 同步（§4.8）；⑤ 新增 README（强调本地运行、完全离线、数据不出本机） |
| v2.23 | 2026-08-03 | **部署地址可配置 + 部署验证**: ① `deploy_webui.sh` 的 `REMOTE_HOST` 支持 `ASR_REMOTE_HOST=kevin@<IP>` 环境变量覆盖（默认当前地址），ThinkPad 随网络环境切换时无需改脚本（§4.8）；② 平铺重构后重新部署验证——开发机与运行节点 18 个运行时文件 md5 全量一致、服务 active、`run.sh` PROJ_ROOT 修复在运行节点生效（§4.8） |
| v2.24 | 2026-08-03 | **部署与访问环境说明（PRD FR-008）**: ① FR-008 补充**浏览器访问环境**（地址格式 `http://<ThinkPad当前IP>:8501`、办公室/家里/Tailscale 示例地址）与**部署环境**（SSH 地址、代码/数据目录、`deploy_webui.sh` 与 `ASR_REMOTE_HOST` 部署命令）；② 注明所列为**示例地址**，实际使用前需替换为 ThinkPad 当前真实 IP（敏感细节本机维护）；需求功能无变化 |
| v2.25 | 2026-08-03 | **声纹阈值调低 + WebUI 流程面板输入/产出分行**: ① `VOICEPRINT_CONFIG` 三档阈值自 0.75/0.60 调低为 **0.65/0.50**（§3.3）——同一声纹跨录音相似度可能略低于 0.75 导致未自动关联，调低后提高自动关联成功率，误关联可由「校准已标注」（PRD FR-003-CLUSTER v2.20）手工改回；② WebUI「音频处理流程」面板 `.pipe-io` 改为纵向布局（输入/产出各占一行），输入格式列表按 `_FORMAT_PRIORITY` 优先级排序为 `wav / flac / m4a / mp3 / opus / ogg / webm`（§1.5） |
| v2.26 | 2026-08-03 | **目录结构对齐生产布局**: ① git 仓库根由"代码平铺根"调整为与运行节点 `/home/kevin/asr_sys_local` **完全一致**的包裹结构——代码整体移入 `asr_sys_local/asr-local/`（= 部署源），数据目录 `audio_inbox/`、`audio_archive/` 以 `.gitkeep` 占位随仓库保留（内容不入库；`.gitignore` 原整目录忽略改为 `目录/*` + `!目录/.gitkeep` negate 规则）（§4.8）；② `deploy_webui.sh` 的 `LOCAL_ROOT` 随脚本自定位自动指向新根、`REMOTE_ROOT` 硬编码不变，部署逻辑无需改动（§4.8）；③ README 目录树同步为包裹结构；④ ThinkPad 生产布局本就如此，无物理改动，仅重新部署验证 |
| v2.27 | 2026-08-03 | **文档上提一级目录 + 重命名**: ① PRD/TDD 由 `asr_sys_local/asr-local/` 移出至一级目录 `asr_sys_local/`，与 `asr-local`/`audio_archive`/`audio_inbox` 并列，并重命名为 **`PRD_local_asr_system.md` / `TDD_local_asr_system.md`**（英文文件名，避免中文文件名跨平台/链接转义问题）；② 全文索引同步——PRD↔TDD 互引链接、`file:///` 绝对路径（含 diarization.py 代码引用路径补 `asr_sys_local/asr-local` 段）全部更新为新文件名与新路径（§4.8）；③ README 目录树同步（§4.8） |
| v2.28 | 2026-08-03 | **README 上提仓库根 + 英文化**: ① README 由 `asr_sys_local/asr-local/` 移至仓库根 `asr_sys_local/README.md`（GitHub 仅在仓库根展示 README）；② 全文改写为英文（本地运行、数据不出本机、离线推理为核心理念）；③ 目录树同步——README/PRD/TDD 并列一级目录（§4.8）；④ GitHub 仓库 **Description（英文）需在网页 About 设置**——本机无 gh CLI/GitHub token，无法命令行设置，提供文案待用户粘贴 |
| v2.29 | 2026-08-03 | **git 仓库根外置，README 落仓库根**: ① v2.28 将 README 放在 `asr_sys_local/README.md`，但 GitHub 只在 **git 仓库根**渲染 README，故仍不显示——本次将 git 仓库根与工程总目录解耦：**git 仓库根 = `ASR-Local-Thinkpad/`**（README.md/.gitignore 在此，GitHub 首页渲染 README），**工程总目录 = `asr_sys_local/`**（与运行节点一致，含 asr-local/audio_archive/audio_inbox/PRD/TDD）（§4.8）；② README 目录树改为以仓库根为根绘制 |
| v2.30 | 2026-08-03 | **仓库扁平化（去除 asr_sys_local 包裹层）**: ① git 仓库根由"ASR-Local-Thinkpad + asr_sys_local 两级"扁平为**一级**——README.md/.gitignore/代码目录 `asr-local/`/数据目录 `audio_archive|audio_inbox`（.gitkeep 占位）/PRD/TDD 全部位于仓库根，与运行节点 `/home/kevin/asr_sys_local/` 内容一一对应（§4.8）；② `deploy_webui.sh` LOCAL_ROOT（脚本自定位）与 `REMOTE_ROOT` 硬编码均不受影响，ThinkPad 生产路径零改动；③ README 目录树、§4.8 结构图同步为扁平结构 |
| v2.31 | 2026-08-03 | **ASR 升级 1.7B + VAD 静音切除加速说话人分离**: ① **ASR 0.6B→1.7B**（§2.1、§3.4）——`ASR_CONFIG.model_repo` 改为 `Qwen/Qwen3-ASR-1.7B-hf`、本地目录 `MODELS_DIR/Qwen3-ASR-1.7B-hf/`；**移除 `torch_dtype=torch.float32` 显式强转**，跟随模型默认精度（safetensors 半精度存储 ~3.4GB），CPU 推理由 PyTorch 自动处理半精度算子，峰值内存仍 < 6GB；② **VAD 拼接切除静音**（§3.2）——`src/utils/audio_utils.py` 新增 `build_speech_concatenation()`（合并重叠段→逐段拼接→`map_back` 映射表把拼接轴时间线性映射回原始轴），`diarization.py` 的 `Diarizer.run()` 新增 `vad_segments` 参数，子进程隔离模式经 `_write_temp_wav()` 写临时 wav 供子进程加载；`DIARIZATION_CONFIG.use_vad_concat` 开关（默认 True）；③ pipeline 传入 `vad_segments=vad_segs`（对下游时间戳无感） |
| v2.32 | 2026-08-03 | **ASR 精度定稿 FP32 + 内存红线放宽 6GB→12GB**: ① **显式 `torch_dtype=torch.float32`**（§3.4）——v2.31 默认精度实测为 bf16，CPU 无 AVX512-BF16 回退转换计算慢（3.14× 实时 / 5.2GB）；FP32 有 oneDNN 优化，实测 **1.11× 实时**（93s 语音 103s）、峰值内存 **11.8GB**；② **内存红线 <6GB → <12GB**（PRD §5.1）：FP32 权重 ~6.8GB + 运行时 ~11.8GB，16GB 系统留 ~4GB 余量；若内存紧张可回退 bf16（移除 torch_dtype 参数）；③ bf16 输入对齐代码保留（fp32 下不触发），§3.4 记录两精度实测数据 |
| v2.33 | 2026-08-03 | **死配置清理 + 文档/界面与实际对齐**: ① **移除 `VAD_CONFIG.max_speech_len_s`（30.0）**（§5.4）——该键原为 Silero "单段语音最大时长"预留，代码从未读取（Silero 默认不限制），属残留死配置；② WebUI「音频处理流程」面板模型名 0.6B→**1.7B**、02 步骤描述补充"VAD 拼接切除静音加速"、03 步骤描述补充"声纹库/已标注声纹簇比对 + 新建 unknown 编号"（§1.5）；③ PRD FR-003-VID 重写为与实际一致（§3.3 索引不变）；④ 文档头部版本号与变更日志对齐（v1.7/v3.7 遗留 → v2.33）；⑤ **§4.8 明确 `config/settings.py` 设计例外**：不随部署、改动需手动 scp + sed 恢复生产 `MODELS_DIR=model_cache` 默认值、两端该差异属预期 |
| v2.34 | 2026-08-04 | **处理成果统计口径修正（webui.py / run.sh）**: ① `get_stats()`（scripts/webui.py）——首页"转录片段"（`COUNT(*)` 片段数，数字偏大）改为**"音频数量"**（`COUNT(DISTINCT file_hash)`，与归档音频数对齐），并移除口径重复的原"处理文件"格子；② **累计时长虚高根因修复**——pipeline 为每个片段行写入**整文件时长** `duration_s`（src/pipeline.py 逐行同一值），原 `SUM(audio_duration)` 会把同一文件按片段数重复累加；改为子查询按 `file_hash` 分组取 `MAX(audio_duration)` 求和后再 /3600 显示小时（SQL 见 [PRD §8.1.2](./PRD_local_asr_system.md#812-处理统计信息来源-sqlite-数据库-transcripts-表)）；③ `run.sh` 菜单 5 CLI 摘要同步同一修复（§4.8 部署含 run.sh） |
| v2.35 | 2026-08-04 | **全文一致性 Review 清理（run.sh / step2 / 文档）**: ① `run.sh` banner 模型组合 0.6B→**1.7B**、收件箱提示"选 1/2 处理"改为"看板手动触发"、声纹录入提示"声纹库 1 号仅一条"改为"is_owner 标记，仅一条"（对齐 PRD FR-003-VID）；② `step2_download_models.sh` 下载模型 0.6B→**1.7B**（`Qwen/Qwen3-ASR-1.7B-hf`，~4.1GB）、末段"录入本人声纹（1 号必须）"改为"标注学习为主流程、CLI 录入可选补充"（§4.8）；③ 修复 §3.2 [diarization.py] `file:///` 链接缺 `asr-local/` 段；④ §4.1"无有效语音段移入 error/"更正为"不移文件、仅生成 .error.txt"（与 §3.6/PRD FR-001-AR 一致）；⑤ §5.3 配置名补 `_SECONDS`（`TIME_SOURCE_MISMATCH_THRESHOLD_SECONDS`）对齐 settings.py；⑥ PRD §6.1/§9/§11.3 模型下载表述修正（hf-mirror 实测不可用 → huggingface-cli + 代理/ModelScope 兜底，v2.31 经验） |
| v2.36 | 2026-08-04 | **失败文件处理恢复"移入 error/"（archive.py / process_inbox.py）**: ① `move_to_error()`（src/archive.py §3.6）——处理失败时把**原始音频文件移入 error/ 目录**（保留原名，重名时附加产生错误时间戳与序号），`.error.txt` 仍带时间戳防重名（v2.9 曾改为"仅写日志不移文件"，v2.36 恢复移入，收件箱只保留待处理文件；用户可手动移回重试）；② `archive_error_files()`——归档范围由仅 `.error.txt` 扩展为 **`.error.txt` 日志 + 失败音频**一并移入 `error/archived/`（命名附原文件创建时间戳防重名）；③ `process_inbox.py` 新增 `_move_failed_group()`（§3.6）——失败分支把仍留在收件箱的主文件 + 同 stem 兄弟文件一并移入 error/，避免下次扫描把次优格式兄弟文件当主格式处理（FR-001-MULTI）；④ §4.1 无有效语音段处理同步（移入 error/ + 日志）；⑤ pipeline 各失败分支（重复文件/加载/VAD/Diarization/ASR/无有效语音段）经 `move_to_error` 统一生效 |
| v2.37 | 2026-08-04 | **settings.py 入 git 口径修正（v2.33 设计例外的口径修正）**: ① settings.py **纳入 git 版本管理**——以 **ThinkPad 生产版本为基准**上传（`MODELS_DIR` 默认值统一为 `PROJ_ROOT / "model_cache"`，MacBook 本地已替换为生产版本，两端文件完全一致；原"本地 models / 生产 model_cache"差异消除，MacBook 无模型权重/无 .env，统一无副作用）；② 设计例外口径修正为"**不随 `deploy_webui.sh` 部署**（设计约定：部署覆盖会冲掉 ThinkPad 上手工调整的配置，运行时由 `.env` 覆盖）而非'不入 git'"——撤销上一轮的 `git rm --cached` 与 `.gitignore` 排除，恢复跟踪（§4.8）；③ `deploy_webui.sh` 注释同步；④ 此后修改 settings.py：手动 `scp` 同步到 ThinkPad + 提交 git（两端内容相同，无需 sed 恢复默认值） |
| v2.38 | 2026-08-04 | **顶部锁定导航条（webui.py §4.7 / PRD §8.2）**: ① 页首（标题 + 北京时间）与页签导航**合并为同一行**——`st.columns([1.2, 1.8])` 左品牌右导航，品牌块 = 暖赭方块 + 标题 + 时间小字副标（原 `.page-header`/`.page-title`/`.page-time` 样式移除，改为 `.topbar-*`）；② 整条 `position: sticky; top: 0` 吸顶（页底色 + 底部分隔线 + 轻阴影），滚动时导航始终可见；③ **两个关键坑**：吸顶行用 `:has(.topbar-title)` 定位（`:first-child` 会命中 CSS 注入的 st.markdown）；吸顶条留白用 padding 不用 margin（sticky 元素 margin 透明、下层内容透出）；④ 分段控件 label min-width 9em→8.5em 适配同排布局；⑤ 文档头部版本号 + PRD §8.2 设计要点/线框图 + 变更日志同步 |
| v2.39 | 2026-08-04 | **顶部锁定导航条修复（webui.py §4.7 / PRD §8.2）**: ① **吸顶选择器修正**——Streamlit 1.60 实测 `st.columns` 顶层容器为 **`stLayoutWrapper`**（`stElementContainer` 只是列内元素包装），v2.38 用 `stElementContainer` 导致吸顶/底边框/留白全部未生效（无头 Chrome 实测 DOM + 注入 sticky 验证）；② **页签与下方面板间距加大**——吸顶条 `padding-bottom` 0.9rem→**1.15rem**，四个 tab 间距一致；③ **分段控件适配 1.60 新渲染**——`stSegmentedControl label` 结构不存在，改为 `div[role="radiogroup"] button`（`aria-checked="true"` 选中态恢复 KVI 暖赭高亮 + min-width 8.5em），老版本 label 规则保留兜底；④ 部署验证：无头 Chrome 滚动后 topbarTop=0、`cssHit=1` |
| v2.40 | 2026-08-05 | **顶部品牌改版 + 页签选中态去背景（webui.py §4.7 / PRD §8.2）**: ① 品牌名 `ASR 本地转录系统` → **`Local ASR System`**，删除 `.title-dot` 暖赭小方块（HTML span + CSS 一并移除），`st.set_page_config(page_title=...)` 同步改英文；② 品牌块右移——`.topbar-title` 增加 `padding-left: 0.5rem`（标题与北京时间整体右移，不贴左边界）；③ 页签选中态去背景——`button[aria-checked="true"]` 的 `background` 改 `transparent !important`（移除 `--accent-soft` 背景与 accent 边框色），保留加粗暖赭文字（`color: var(--accent); font-weight: 600`）；④ 部署验证：无头 Chrome 实测选中按钮 `backgroundColor=rgba(0,0,0,0)`、品牌文本为 Local ASR System、吸顶回归正常 |
| v2.41 | 2026-08-05 | **顶部导航条单行布局（webui.py §4.7 / PRD §8.2）**: ① **品牌字号与面板标题一致**——`.topbar-brand` 1.45rem/700 → **1.05rem/600**（同 `.panel-title`「收件箱 · 手动处理」）；② **北京时间移到第一行**——`.topbar-title` 由 `flex-direction: column` 改 **`row` + `align-items: baseline` + `gap: 12px` + `white-space: nowrap`**，时间字号 0.82rem 不变；③ **导航整体右移 12px**——`div[role="radiogroup"]` 加 `margin-left: 0.75rem`（列比保持 1.2:1.8）；④ 单行可行性实测：品牌文字 1.05rem≈135px + 时间≈194px ≈375px < 品牌列 360px、导航组 481px < 导航列 548px，放得下不换行；⑤ 部署验证：品牌/时间同行、导航未溢出列宽、无横向滚动、吸顶正常 |
| v2.42 | 2026-08-05 | **移除 Qwen3-ASR-0.6B（ThinkPad 模型清理 / settings.py / PRD §6.1、§8.2）**: ① **删除 ThinkPad 本地模型**——`models/Qwen3-ASR-0.6B-hf`（1.5G，`HF_HOME`=models 由 .env 覆盖）+ `~/.cache/huggingface/hub/models--Qwen--Qwen3-ASR-0.6B-hf` 残留指针（12K），删除后全盘 `find -iname "*0.6B*"` 无残留，`models/Qwen3-ASR-1.7B-hf`（4.1G）完好；② **settings.py 注释清理**——"v2.31 升级 0.6B → 1.7B"改"v2.31 定稿 1.7B（v2.42 起移除 0.6B）"，并手动 scp 同步到 ThinkPad（settings.py 不随 deploy 部署，v2.37 约定）；③ **PRD 当前状态描述清理**——§6.1 模型条目移除 0.6B 对比、§8.2 技术栈表"优于 0.6B"改为"表现可靠"；④ 变更日志历史条目保留（v1.2/v2.18/v2.31 等为升级过程记录，不篡改历史） |
| v2.43 | 2026-08-05 | **声纹簇「不标注」（db.py §1.7 / webui.py / PRD FR-003-CLUSTER）**: ① **数据层**——`speaker_clusters` 新增 `skip_label INTEGER DEFAULT 0`（SCHEMA_SQL + `init_db()` 内 PRAGMA 检查 + `ALTER TABLE` 老库迁移），新增 `set_cluster_skip(cluster_id, skip)`（不写 assigned_name/label/embedding，不触发回填），`load_all_clusters()`/`list_clusters_view()` 查询补 `skip_label`；② **UI**——「声纹簇·标注学习」新增第三个 tab「🚫 不标注」：两个多选框（设为不标注 / 恢复标注）+ 按钮批量执行，总览表格标注列显示"🚫 不标注"；「标注为某人」列表按 `NOT assigned_name AND NOT skip_label` 过滤；③ **语义**——不标注簇保持原编号、照常参与匹配学习（匹配层不过滤），恢复标注即回到标注列表，全程可逆；④ **启动迁移**——webui.py 顶部调用 `init_db()`（幂等），老库重启即补列；⑤ 部署验证：服务 active + `PRAGMA table_info` 确认 `skip_label` 存在 + 无头 Chrome 渲染新 tab |
| v2.44 | 2026-08-05 | **不标注操作区布局 + 全文一致性 Review（webui.py / db.py / voiceprint.py / PRD）**: ① **布局**——「🚫 不标注」tab 改为两组 `st.columns([3, 1], vertical_alignment="center")`：「设为不标注」「恢复标注」按钮分别与各自多选框**同行垂直居中对齐**，`use_container_width=True` 铺满窄列；② **一致性修正**——PRD §7.1 `sample_count DEFAULT 0`→**1**（对齐 `db.py` SCHEMA_SQL 实际默认值）；PRD §4.2 FR-009 回填表述修正（标注已实现回填、合并/删除规划中不回填，消除与 FR-003-CLUSTER 的矛盾）；`voiceprint.py::register_new_cluster` 新建簇字典补 `skip_label: 0`（与 `load_all_clusters` 返回形状一致，匹配层不消费该字段）；③ 通篇审查——版本号、变更日志、锚点、界面描述、SQL、脚本模型名（1.7B）均一致；④ 部署验证：18 个运行时文件 md5 全量一致 + 无头 Chrome 实测按钮与多选框同行对齐 |
| v2.45 | 2026-08-05 | **不标注操作区对齐修正（webui.py §1.7）**: ① **问题**——v2.44 的 `vertical_alignment="center"` 使按钮对齐到「label 文字 + 下拉框」整体的垂直中间：实测 label 24px + 间隙 4px + 框 40px，按钮中心落在 label 与框之间（既不对齐文字也不对齐框）；② **修正**——无头 Chrome 实测按钮与下拉框**等高（均 40px）**，改用 `vertical_alignment="bottom"`：按钮底边对齐列底边（= 下拉框底边），顶部随之精确对齐下拉框；label 保持可见位于框上方、不参与按钮定位；③ 部署验证：按钮 top == 下拉框 top（941px）、left 位于框右侧，四端 md5 一致 |
| v2.46 | 2026-08-05 | **模型目录清理 + step2 下载口径重写（§4.6 / PRD §5.3、§6.1、§9、§11.3）**: ① **ThinkPad 模型目录清理**——删除 9 项冗余（step2 旧版松散目录 pyannote-speaker-diarization-3.1/pyannote-segmentation-3.0/pyannote-embedding、顶层旧 hub 缓存 models--pyannote--segmentation-3.0/wespeaker/community-1 残缺、silero-vad-ms、xet、hub 内 community-1）约 178M；② **PLDA 依赖事故与恢复**——删除 hub 内 community-1 后离线加载管线失败（`get_plda` 需 `pyannote/speaker-diarization-community-1/plda/xvec_transform.npz`），经 MacBook + `HF_ENDPOINT=https://hf-mirror.com` 的 `snapshot_download` 下载 33M 并用 tar 原样传回（保留 refs/snapshots 符号链接）恢复；离线加载验证通过（pipeline 3.1 / embedding / Silero VAD 全 OK）；③ **step2_download_models.sh 重写**——废弃 `huggingface-cli` 改 Python `snapshot_download`；pyannote 全部入 hub 缓存（speaker-diarization-3.1 / segmentation-3.0 / wespeaker / community-1 / embedding）；Qwen 保持自定义目录；Silero 预热固定 `torch.hub.set_dir(models/silero-vad)`（修掉旧版 TORCH_HOME 与 vad.py 目录不一致的隐患）；环境变量与 .env/settings.py 唯一口径（HF_HOME=models、HF_HUB_CACHE=models/hub）；④ 网络结论更新——hf-mirror 实测可用（HF_ENDPOINT），覆盖 v2.31/v2.35 旧结论；⑤ 部署验证：step2 同步 ThinkPad + 19 文件 md5 一致 + 离线加载实测通过 |
| v2.47 | 2026-08-05 | **open_proxy 用法记录（迁移 SEC）+ 残留目录清理与防护（§4.6 / db.py）**: ① **open_proxy 机制记录并迁移**——开启/关闭/状态命令、内核位置、端口与环境变量口径移入 `SEC_local_asr_notes.md`（敏感信息不入库）；脚本化无 sudo 场景继续用 hf-mirror/开发机中转；② **清理错误路径残留**——`/home/kevin/audio_archive`（settings 默认路径 + 未加载 .env 时 connect 制造，含 0 字节 transcripts.db）已删除，确认无其他默认路径残留（`~/audio_inbox`/`~/asr-local` 等均不存在），正确路径 `asr_sys_local/audio_archive` 不受影响；③ **复发防护**——`db.py::connect()` 在"无显式 db_path + 默认 `~/audio_archive` + 未设 ASR_ARCHIVE"时向 stderr 告警，替代静默制造残留；④ 部署验证：db.py 同步运行节点 + 19 文件 md5 一致 |
| v2.48 | 2026-08-05 | **ASR 加载精度可配置 + 大文件 OOM 兜底（settings.py §3.4 / asr.py / 运维）**: ① **事件**——运行节点处理 58.6 分钟大文件 `2026-08-04_14_07_26.wav` 两次被内核 OOM 杀死（WebUI 启动 11:02:59 峰值 14.6GB；standalone 13:30:54 峰值 14.96GB），均死在 ASR 转录阶段（FP32 模型 ~11.8GB + 波形/运行缓冲超出 16GB）；② **修复**——`ASR_CONFIG["torch_dtype"]` 支持环境变量 `ASR_TORCH_DTYPE` 覆盖（默认 `float32`，`bfloat16` 内存约减半），`asr.py` 三处加载统一走 `model_kwargs`；③ **恢复**——重置残留 status/锁，以 `ASR_TORCH_DTYPE=bfloat16` standalone 重启，VAD 拼接 58.6→5.2 分钟语音，内存可用 ~12GB 正常推进；④ **备注**——办公室网络可直连 huggingface.co（302），家网需代理/hf-mirror 兜底（网络环境规则本机维护）；定时脚本已改多 IP 尝试；⑤ 部署验证：asr.py/settings.py 两端 md5 一致，UI_VERSION 2026-08-05-15:15:05 |
| v2.49 | 2026-08-05 | **ASR 精度默认改为 auto 动态分配（settings.py §3.4 / asr.py / pipeline.py）**: ① **默认 `auto`**——`pipeline.process_file` 按音频时长决策：≥1800s（30 分钟，可配 `ASR_TORCH_DTYPE_BIG_S`）→ bf16，否则 FP32；以 `torch_dtype` 参数传入 `QwenAsr`（不再只依赖环境变量）；② **asr.py**——构造函数新增 `torch_dtype` 参数（None 时读 settings，`auto` 兜底为 float32）；③ **pipeline.py**——第 (7) 步决策并记录 `[pipeline] ASR 精度决策：X（音频 Y 分钟）`；④ 阈值依据：FP32 成功最大 23.6 分钟、OOM 最小 58.6 分钟，30 分钟留余量；⑤ 部署验证：asr.py/pipeline.py/settings.py 两端 md5 一致 |
| v2.50 | 2026-08-05 | **文件名时间提取格式三处统一（settings.py §3.5 / PRD FR-001-TS / TDD §3.5、§4.4）**: ① **实现修正**——紧凑式正则 `YYYYMMDD_HHMMSS` 的日期-时间分隔符由 `_` 放宽为 `[-_]`：新增识别 `recording-20260731-143052`、`20260731-143052-recording`（此前只能下划线）；② **文档统一**——PRD「支持的文件名格式」与 TDD §3.5 正则/§4.4 描述改为同一清单：长格式（六个字段 `[-_]` 任意混用，可带前后缀）/ 紧凑式（`[-_]` 均可，可带前后缀）/ ISO `YYYYMMDDTHHMMSS`；`re.search` 不锚定；③ 验证：8 种格式（含 7 种建议 + ISO）全部正确提取，无匹配样例（无时间数字串）正确返回 None |
| v2.51 | 2026-08-05 | **敏感/个人信息去敏迁移（PRD/TDD → SEC_local_asr_notes.md，不入库）**: ① 新建 `SEC_local_asr_notes.md`（3 大写字母前缀，与 PRD/TDD 同风格），收录：ThinkPad 网络环境规则（办公室免代理直连 / 家网需 open_proxy）、open_proxy 用法、网络地址清单（办公室/家里/Tailscale IP）、访问设备型号、家庭声纹标注标签、3 款拾音设备特点与总结；② `.gitignore` 加入 `SEC_local_asr_notes.md`，不推送 GitHub；③ PRD/TDD 中性化——具体 IP/代理端口/设备型号/家庭人物称呼示例全部替换为中性描述并注明本机维护；④ 部署脚本默认地址、open_proxy 命令等敏感细节同步迁移；⑤ 校验：git 跟踪文件不再含上述具体 IP/代理端口/家庭称呼 |
| v2.52 | 2026-08-05 | **消除 PRD/TDD 对 SEC 的引用（GitHub 死链修复，文档自洽）**: ① 移除 PRD/TDD 中所有 `SEC_local_asr_notes.md` 链接与"见 SEC 文档"引导（该文件不入库，GitHub 上不存在，避免读者点死链）；② 相关表述改为自洽中性文案——"本机维护、不随仓库发布"；③ 变更日志中保留对本次迁移的历史记录（纯文本文件名，无链接）；④ README 补充说明：敏感运维细节（网络地址/代理/个人标签）本地维护、不随仓库发布；⑤ 校验：PRD/TDD 中无任何指向不入库文件的链接 |
| v2.53 | 2026-08-05 | **ASR 段合并根治（pipeline.py §3.4 / settings.py）**: ① **问题**——58.6 分钟文件（VAD 140 段、语音 5.2 分钟）bf16 ASR 跑了 89 分钟仍未完成：逐段固定开销（~25-33s/段 FP32）被 bf16 放大（~2.5-3×），时间 ≈ 段数 × 每段开销；且单文件 ASR 内存从 5.3GB 涨到 14GB（torch CPU 内存池不还峰值），逼近 OOM；② **修复**——ASR 前合并相邻短段（间隔 ≤ `segment_merge_gap_s` 默认 1.5s，合并后段长 ≤ `segment_max_s` 默认 60s）：140 段连续短语音实测合并到 6 段，直接砍掉主乘数，恢复 VAD 对 ASR 的收益；段长上限同时约束单段峰值内存；每 8 段 `gc.collect()`；③ **行为变化**——合并段每行时间戳为合并段起止、说话人取起始分离段（粒度变粗，句子级时间戳留待 `return_timestamps` 细分）；④ **终止与恢复**——终止了 89 分钟的 bf16 运行（SIGTERM 未响应→SIGKILL+手动重置 status/锁），收件箱文件保留待用户手动重跑；⑤ 验证：合并逻辑本地单测通过（连续短段 140→6、分散长段不合并、超上限不合并）；部署验证：pipeline.py/settings.py 两端 md5 一致 |
| v2.54 | 2026-08-05 | **效率优先原则固化 + 错误归档误移 README 修复（PRD FR-004 / TDD §3.4 / archive.py）**: ① **设计原则固化（用户确认）**——转录行不严格按说话人切分（效率优先）；声纹身份由簇承载、不受转录行粒度影响；声纹标注可通过单人录音等其他途径（单人录音→一个分离说话人→一个簇/ID）；② **archive_error_files() 修复**——原实现无差别移动 error/ 根目录**所有文件**，曾把 `README.txt` 误当错误文件搬入 archived/ 并附加时间戳改名（`README_20260802_220637_20260802_220637.txt`）；修复为只归档 `.error.txt` 日志 + `SUPPORTED_EXTENSIONS` 失败音频（注意复合后缀判断用 `endswith(".error.txt")`，v2.17 坑）；③ **现场恢复**——误归档 README 改回 `error/README.md`（内容为错误目录说明，516B）；④ 部署验证：archive.py 同步运行节点 + md5 一致 |
| v2.55 | 2026-08-05 | **性能数据边界标注（PRD §5.1 / TDD §2.1）**: ① 性能基准表新增标注——本表为 **CPU 部署实测（i5-10210U）**、仅作本机回归基线、GPU 部署需重新实测、绝对数值不跨硬件外推；② 明确**配置不跨硬件迁移**——当前配置/使用方法受制于 CPU+内存，迁移 GPU 时应修改参数并适配（加载精度、段合并/批处理、内存编排），按实际系统再调参；③ 背景：系统本应部署于 GPU，受电脑限制目前只能 CPU+内存运行，故明确 CPU 数据的边界，避免误当系统能力上限 |
| v2.56 | 2026-08-05 | **ASR 段长上限收紧 60s→15s + malloc_trim 内存归还（settings.py §3.4 / pipeline.py）**: ① **实测教训**——58.6 分钟文件（分离仅 17 段、合并 15 段）bf16 长段解码极慢（60s 段 ≈ 6 分钟/段，总 ASR 92 分钟），且长段工作集使 RSS 从一开始就钉在 ~14.7GB，16GB 机器满内存+swap 假死（第三次终止）；② **修正**——`segment_max_s` 默认 60s→**15s**（单段生成 token 骤减、峰值内存与耗时双降）；每 8 段 `gc.collect()` 后调 **`malloc_trim(0)`** 归还空闲堆给 OS；③ 预期：5.2 分钟语音 10-20 分钟完成、RSS 峰值显著下降；④ 终止恢复：第三次终止假死进程（TERM 未响应→KILL+重置 status/锁），收件箱文件保留；⑤ 部署验证：pipeline.py/settings.py 两端 md5 一致 |
| v2.57 | 2026-08-05 | **分离段裁剪回真实语音（根因修复，diarization.py §3.2）**: ① **根因定位（受控实验）**——ASR 循环本身零内存增长（50+ 段实测 RSS 稳定 4.7GB、无泄漏）；真凶是**分离段横跨静音**：VAD 拼接轴上同一说话人的多个语音片断被并成一段，映射回原时间轴后把中间静音一起包进来（58.6 分钟文件：VAD 语音 5.2 分钟，但分离 17 段、最大 1336.7s、段总时长 3104.8s）；ASR 把含静音的整段送去转录 → 内存钉满 14GB + 耗时数小时；② **修复**——`Diarizer.run()` 映射回原轴后，与 VAD 语音片断**取交集裁剪**（>0.05s 保留），再合并相邻同说话人短段（间隔 ≤ `merge_gap_s`）；③ **验证**——同文件重跑：17 段 → 72 段（真实片断）、最长段 1336.7s → **35.7s**、段总时长 3104.8s → **325.7s**（≈VAD 语音量级）；④ 附带收益——声纹聚合只使用真实语音；⑤ 部署验证：diarization.py 同步 + md5 一致，UI_VERSION 2026-08-05-22:43:53 |
| v2.58 | 2026-08-06 | **ASR 精度决策升级：按"可用内存 + 语音时长"（settings.py §3.4 / pipeline.py）**: ① 原规则（v2.49）按**音频总时长 ≥30 分钟**切 bf16——粗糙代理；升级为**决策时刻 `/proc/meminfo MemAvailable` ≥ `fp32_min_avail_mb`（默认 13500MB）且 VAD 语音总量 ≤ `fp32_max_speech_s`（默认 1800s）→ FP32**，否则 bf16；② 依据——v2.57 语音裁剪后 ASR 峰值主要由模型决定（FP32 ~12-13GB / bf16 ~5.5GB），与文件大小/总时长弱相关，故"可用内存 + 已知语音量"更精准（内存足、语音少 → 快跑 FP32；内存紧或语音超长 → bf16 保稳）；③ 新增 `_available_mem_mb()`（Linux MemAvailable 读取，失败保守走 bf16），决策日志带可用内存与语音/音频分钟数；④ 决策逻辑本地单测 4 组合全过；⑤ 五端协同——PRD/TDD/SEC/代码/运行节点同步（SEC 无敏感信息变化无需改）；⑥ 生产实测：收件箱 11 个真实文件直接跑，观测每文件精度选择/内存/耗时后微调阈值 |
| v2.59 | 2026-08-06 | **m4a 音频加载修复（audio_utils.py §4.4，实战发现）**: ① **现象**——11 文件实战批处理第一个 m4a 即失败：`加载失败: cannot access local variable 'data'`；② **根因**——`load_audio()` 的 pydub/ffmpeg 回退分支不产生 `data`，函数结尾 `del mono, data` 引用未定义变量 → UnboundLocalError，影响所有 soundfile 不支持的格式（m4a 必中，部分 mp3 可能）；③ **修复**——`data = None` 初始化 + 判空释放；④ 处理——终止旧批、恢复失败文件回收件箱、修复后重启全部 11 个；⑤ 部署验证：audio_utils.py 远端含修复 + md5 一致，UI_VERSION 2026-08-06-12:18:16 |
| v2.60 | 2026-08-06 | **WebUI 访问控制（§4.8 / PRD FR-008、FR-008-T）**: ① **问题**——8501 原 `ufw allow 8501/tcp`（Anywhere），局域网任何人拿到 `http://<ThinkPad当前IP>:8501` 即可进入管理界面；② **修复**——ThinkPad ufw 删除全局放行，改为仅放行 **Tailscale 网段（100.64.0.0/10）** 与**白名单设备 IP**（当前：开发机 MacBook，办公室 DHCP 地址仅记入 SEC；新设备由用户提供 IP 或接入 Tailscale）；`Default: deny (incoming)` 兜底；③ **为什么不用 MAC 过滤**——MAC 是二层地址、到主机即被剥掉且可伪造；手机普遍启用随机 MAC（ThinkPad 邻居表实测同一随机 MAC 出现在多个 IP），服务器端无法可靠按 MAC 限流；实际可行方案 = 路由器 MAC→固定 IP 绑定 + ufw IP 白名单，或 Tailscale 身份（推荐，跨网络通用）；④ **install_services.sh 同步**——安装脚本默认只放行 Tailscale 网段 + 可选白名单 IP（第 2 个参数空格分隔传入）；⑤ 验证：白名单内开发机 curl 8501 → 200；`ufw status verbose` 确认 8501 无 Anywhere 规则；SSH(22)/远程桌面（3389/RustDesk 21115-21119）当时保持原策略（v2.61 起远程桌面已关闭） |
| v2.61 | 2026-08-06 | **导航改 5 页签 + 访问控制页 + 时间面板 + 端口收敛（webui.py / systemd/asr-webui-fw.sh / install_services.sh / PRD FR-008、FR-008-A、FR-008-T）**: ① **导航 4→5 等宽**——`NAV_OPTIONS` 改为 状态概览/处理记录/数据库/文件归档/访问控制，`div[role="radiogroup"]` 改 `display:flex; width:100%` + `button { flex:1 1 0; min-width:0 }` 等宽（原 min-width 8.5em 移除），列比 1.2:1.8→1.0:2.4；② **北京时间移出顶栏**——`.topbar-time` 样式与用法移除，首页新增「北京时间」面板（导航条下方第一个面板，仅首页展示）；③ **页 5 访问控制**——面板 1 白名单管理（list/add/remove 经 `sudo -n /usr/local/sbin/asr-webui-fw.sh`，helper 只允许操作 8501、仅接受合法 IPv4、Tailscale 网段/回环固定不可删），面板 2 端口说明（22 SSH 开发机管理/部署、8501 Web UI，远程桌面已关闭）；④ **RustDesk 卸载清理**——ThinkPad 无 dpkg 包/无进程，残留目录（配置/日志/空目录）移至 `~/rustdesk_remove_20260806/`（可恢复后自行删除），ufw 关闭 3389/tcp 与 21115:21119/tcp、21116/udp（v4+v6）；⑤ **helper 部署**——install_services.sh 安装 `/usr/local/sbin/asr-webui-fw.sh`（root 0755）+ `/etc/sudoers.d/asr-webui-fw`（NOPASSWD 仅限该脚本）+ 明确放行 SSH 22；⑥ 验证：helper list/add/remove/固定项保护/非法 IP 拒绝全部通过，ufw 最终仅 22 + 8501（Tailscale+白名单设备），白名单内 curl 8501 → 200，UI_VERSION 已更新 |
| v2.62 | 2026-08-06 | **导航宽度回归 + 访问控制页描述 + 精确 Tailscale IP + 页脚部署时间（webui.py / asr-webui-fw.sh / install_services.sh / PRD FR-008、FR-008-A）**: ① **导航不再等宽**——移除 v2.61 的 `width:100%`+`flex:1`，`div[role="radiogroup"]` 改 `display:flex; flex-wrap:wrap`、`button` 改 `flex:0 0 auto; min-width:0`（宽度随文字，实测 状态概览/处理记录/文件归档/访问控制 90px、数据库 76px；480px 视口折成两行）；② **访问控制页**——新增「描述」输入栏（≤48 字符，随 IP 写入 ufw comment），「添加」按钮与输入栏同行垂直居中（`st.columns(..., vertical_alignment="center")` + label 折叠，实测按钮/输入框中心 Y 一致）；helper `add <ip> [comment]` 支持描述（去单引号、截断 48 字符）；③ **Tailscale 白名单改精确 IP**——废弃 `100.64.0.0/10` 网段，改为固定放行 Tailscale 设备 IP（`/etc/asr-webui-fw.conf` 存 `FIXED_IPS`，helper 读取；精确地址只记 SEC，仓库保持中性）；helper `list` 输出改 `ip|comment|fixed` 机器格式；④ 端口说明面板删除冗余文案；⑤ 页脚改「部署时间」前缀；⑥ 验证：headless Chrome 实测宽度/折行/对齐/文案，helper list/add(comment)/remove/固定保护全过，ufw 现状 22 + 8501（Tailscale #1/#2 + Macbook Air Office） |
| v2.63 | 2026-08-06 | **导航加宽/字距 + 白名单分组（webui.py §4.7 / PRD FR-008-A）**: ① `div[role="radiogroup"]` 加 `gap:6px`，nav button 加 `padding: 0 1.5rem`（!important）+ `letter-spacing: 0.02em`——实测 4 字页签 90→107px、数据库 76→93px（仍按文字自适应）；② 「访问控制」页白名单分组：**固定放行（不可删除）置顶、设备白名单（可新增/移除）置底**，两组间留白，新增表单保持面板底部；③ 验证：headless Chrome 实测宽度/padding/gap/字距 + 页面元素顺序（固定组在设备组之前、新增表单最后） |
| v2.64 | 2026-08-06 | **导航去缝隙 + 白名单行对齐（webui.py §4.7 / PRD FR-008-A）**: ① 移除 radiogroup 的 `gap:6px`（页签恢复无缝连续外观，实测相邻按钮间距 -1px≈0）；tab 内文字 `letter-spacing` 0.02em→**0.05em**（实测 0.7px），宽度仍 109/94px；② 白名单每行改 `st.columns([1.2, 3.2, 1], vertical_alignment="center")`——实测「移除」按钮与左侧 IP 中心 Y 完全一致（diff=0）；③ 验证：headless Chrome 实测宽度/缝隙/字距/按钮对齐，UI_VERSION 2026-08-06-16:01:55 |
| v2.65 | 2026-08-06 | **ASR FP32 阈值按实测微调（settings.py §3.4 / PRD FR-004）**: ① 实战发现——16GB 机器（仅跑本任务）决策时可用内存稳定 12.7-13.3GB，`fp32_min_avail_mb` 默认 13500MB **永不触发**，auto 全部走 bf16；11 文件批处理实测：ASR 阶段 8 分钟语音文件 ~57 分钟、18 分钟语音文件 ~107 分钟（约 7-8× 实时），bf16 每段 91-126 秒（与 v2.53 记录 62-99s/段同量级）；② `fp32_min_avail_mb` 默认 **13500→12000MB**——FP32 峰值 ~12-13GB（v2.32/v2.58 实测）、决策时可用内存稳定 ≥12.7GB，留 ~1GB 余量；可用内存低于阈值仍自动回退 bf16；③ 决策时机说明——精度决策发生在音频加载/VAD/分离/声纹引擎之后，MemAvailable 已扣掉该音频的波形占用，音频越大天然越保守；④ 部署验证：settings.py 两端 md5 一致，终止旧批重启生效 |
| v2.66 | 2026-08-06 | **解除 ASR 语音时长限制 + 卸载归还内存（settings.py §3.4 / pipeline.py §2.4 / PRD FR-004）**: ① **解除"VAD 语音总量 ≤1800s"限制**——删除 `fp32_max_speech_s` 配置与决策条件（v2.57 裁剪 + v2.56 段长封顶后 ASR 峰值由模型决定、与语音总量弱相关，内存检查即唯一护栏；语音总量只影响耗时）；② **卸载归还内存**——新增模块级 `_malloc_trim()` 并在 `_unload_asr`/`_unload_diar` 卸载后调用（ASR 循环内每 8 段既有调用改为复用）；实测 FP32 卸载后 torch CPU 池滞留 ~2GB、下一文件决策可用内存虚低（10.7GB）误走 bf16，修复后重启首文件恢复 float32（12908MB）；③ 五端协同——PRD/TDD/SEC/代码/运行节点同步（SEC 无敏感信息变化无需改）；④ 部署验证：pipeline.py/settings.py 两端 md5 一致，终止旧批重启生效 |
| v2.67 | 2026-08-06 | **「处理记录」页重构为按音频维度（db.py §PRD7.1 / pipeline.py / webui.py §PRD8.2 页2）**: ① **db.py**——`transcripts` 新增 `processing_started_at`/`processing_completed_at` 两列（SCHEMA_SQL + init_db ALTER TABLE 老库迁移），`SegmentRow` 与 `insert_segments` 同步；② **pipeline.py**——`process_file` 开头记录开始处理时间，归档+文本备份成功后、入库前回填完成处理时间；③ **webui.py**——页 2 移除「最近处理」「筛选条件」「片段记录/片段详情」四面板，改为两面板：「音频处理记录」（`get_audio_records()` 按 file_hash 聚合、按源音频时间从远到近，显示层编号 1..N，列：编号/源文件/时长(min)/源音频起止时间/开始处理/完成处理时间，`fmt_dt_no_sec` 不含秒；旧记录完成时间回退 processed_at、开始时间显示 —）与「音频处理详情」（`get_audio_segments()` 取该音频全部片段，按绝对时间升序渲染 `[起 - 止] 说话人：文本`，说话人经显示层映射；`render_full_audio()` 整段回放替代按片段切分）；④ 删除页 2 专用的 `get_all_records()`（搜索页的 `render_segment_audio` 保留）；⑤ 部署验证：webui.py/db.py/pipeline.py 三端 md5 一致，webui 重启后老库自动迁移 |
| v2.68 | 2026-08-06 | **处理记录显示归档名 + 标注学习重构（webui.py §PRD8.2 页2/页3 / PRD FR-003-CLUSTER）**: ① 页 2 两面板的音频名称改用 `archive_name`（回退 `source_file`），「音频处理记录」列头改「归档音频」，「音频处理详情」下拉与标题同步；② 新增 `get_speaker_utterances()`（按原始标签 `IN` 查询发言，绝对时间倒序限 100 条）；③ 「声纹簇·标注学习」面板重写——说话人下拉（全部声纹簇 + 声纹库命名的无簇说话人，选项带标注态标识：已标注/（未标注）/🚫 不标注）→ 发言列表（绝对时间 + 源文件 + 文本）→ 标注区（未标注：标注并回填/设为不标注；已标注：改标并回填/改回未知两步确认；不标注中：恢复标注），删除旧 ID/编号/标注为/样本数表格与三段式 tab；④ 五端协同——PRD/TDD/SEC/代码/运行节点同步（SEC 无敏感信息变化无需改）；⑤ 部署验证：webui.py 两端 md5 一致，页 2/页 3 渲染验证 |
| v2.69 | 2026-08-06 | **声纹标注面板试听发言（webui.py §PRD8.2 页3）**: ① `get_speaker_utterances()` SELECT 增加 `segment_start_offset`/`segment_end_offset`/`audio_path`（发言切片信息本就在 transcripts，仅补取）；② 「声纹簇·标注学习」发言列表下方新增 **「🎧 试听发言」**下拉（选项 = 时间 + 文字预览，内部以 `transcripts.id` 为键但不展示任何 ID）+ 复用 `render_segment_audio()` 播放（soundfile 读归档音频后按偏移切段）；③ 五端协同——PRD/TDD/SEC/代码/运行节点同步（SEC 无敏感信息变化无需改）；④ 部署验证：webui.py 两端 md5 一致，AppTest 无头渲染（含数据库页新控件）通过 |
| v2.70 | 2026-08-06 | **两处 UI 微调（webui.py §PRD8.2 页2/页3）**: ① 「音频处理记录」表格改为倒序显示（`rows` 构建按源音频时间升序保证编号 1 = 最远，展示时 `list(reversed(rows))` 让最大编号在最上方）；② 「声纹怎么来的」面板移除结尾句前的 `<br>`，标注学习循环说明并入同一段落；③ 部署验证：webui.py 两端 md5 一致 |
| v2.71 | 2026-08-06 | **三处 UI 微调（webui.py §PRD8.2 页2/页3/页5）**: ① 「访问控制」白名单行的「固定」由 `st.caption` 改为全宽居中 `<div>`，「移除」按钮加 `use_container_width=True`——两者同宽居中，消除按钮偏向右侧的错位感；② 「音频处理记录」面板说明去掉「，不再拆分片段」；③ 「声纹簇·标注学习」面板说明去掉「（原「处理记录」页说话人筛选已并入）」；④ 部署验证：webui.py 两端 md5 一致 |
| v2.72 | 2026-08-06 | **五端一致性 Review（文档工程 / ThinkPad 同步）**: ① §1.7 UI 描述改写为 v2.68/69 单流程（说话人下拉-发言列表-试听-标注操作区），收敛与 PRD §8.2 页 3 的重复、只留实现要点；「确认标注并回填」按钮名统一为「标注并回填」；§4.7 补 v2.71 白名单水平对齐要点；② **ThinkPad 工程文件全量 md5 比对**——部署脚本 18 个运行时文件 + settings.py 全部一致；systemd/asr-webui.service 与 install_services.sh 存在历史漂移（旧 User/Group 行、旧路径 HF_HOME=asr-local/model_cache、旧 ufw 逻辑、缺白名单参数），已从仓库同步覆盖；/usr/local/sbin/asr-webui-fw.sh 与仓库仅注释措辞差异、行为一致（均读 /etc/asr-webui-fw.conf 的 FIXED_IPS）；deploy_webui.sh 为 Mac 侧工具按设计不同步；③ 版本头部与变更日志两两核对一致（v1.0→v2.72） |
| v2.73 | 2026-08-06 | **清理 RustDesk 残留目录（ThinkPad 运维）**: ① 复核——`~/rustdesk_remove_20260806/` 仅含 RustDesk 运行日志（约 496KB，tray/password/check-hwcodec-config 等子目录），无进程/无 dpkg 包/无 systemd 服务/无常见残留配置目录/21115-21119 与 3389 端口未监听；② 确认无用后删除该目录，复核通过；③ 五端协同：SEC 与交接文档同步更新（本地维护），PRD/TDD changelog 记录 |
| v2.74 | 2026-08-07 | **文件名全紧凑时间格式 + FTS 索引修复（settings.py / fts.py / 数据修正）**: ① `FILENAME_TIME_PATTERNS` 新增第 4 条全紧凑式 `YYYYMMDDHHMMSS`（14 位无分隔，`(?<!\d)`/`(?!\d)` 边界，如 `Note-20260806152345`）——v2.74 前此类文件名无法解析、回退到文件创建时间，实测把 2026-08-06 15:23:45 的录音识别为 22:44:15；② **修复 `src/fts.py::sync_segments`**——原实现按 `(file_hash, text)` 反查 id 取最新一条，同文件两条相同文本命中同一 id，第二次 `INSERT INTO transcripts_fts2(rowid, ...)` 触发 FTS5 rowid 唯一约束抛 `constraint failed`（仅告警、转录成功但该文件索引缺行，中文搜索漏匹配）；改为按 `file_hash` 取最新 `len(rows)` 个 id 与插入行一一对应 + `INSERT OR REPLACE` 幂等；③ **存量数据修正**（仅最后处理的 `Note-20260806152345.mp3`）：录音开始时间 22:44:15 → 15:23:45，归档音频/文本/JSON 文件名、数据库 `recording_start_time`/`absolute_*_time`/`archive_name`/`audio_path`/`transcript_path` 及 WebUI 源音频开始/结束时间同步修正，`transcripts_fts2` 全量重建；④ 部署验证：settings.py 与 fts.py 两端 md5 一致 |
| v2.75 | 2026-08-07 | **声纹向量学习限定为已标注簇（voiceprint.py §3.3 / PRD FR-003-CLUSTER）**: ① `match_speaker` 命中簇且 `score >= 0.65` 时，仅对 `assigned_name` 非空的簇调用 `_learn_into_cluster`；纯 unknown / 取消标注 / skip_label 簇只沿用编号、不更新向量（v2.74 实测：16kHz/32kbps 低码率音频 3 个说话人并成 1 簇、污染 unknown_0044 一次）；② 记录"无样本簇"机制（声纹匹配阶段即建簇、ASR 无文本或文件失败时簇无 transcript 片段，标注队列显示 0 条发言无音频，属预期，如 unknown_0046）；③ 部署验证：voiceprint.py 两端 md5 一致 |
| v2.76 | 2026-08-07 | **改标即重置声纹向量（db.py / voiceprint.py §3.3 / PRD FR-003-CLUSTER）**: ① `speaker_clusters` 新增 `reset_on_next_match INTEGER DEFAULT 0` 列（含老库 ALTER 迁移），`load_all_clusters` 同步读取；② `assign_cluster_name` 在"原已标注且改标为他人"或"给 `sample_count>1` 的簇指派姓名"时置 `reset_on_next_match=1`（同名重指派不重置；纯新建簇 `sample_count=1` 首次标注不重置）；③ `match_speaker` 命中待重置的已标注簇时调用 `reset_cluster_vector`（embedding 替换、`sample_count=1`、清标记），否则按 v2.75 增量学习；④ 部署验证：db.py / voiceprint.py 两端 md5 一致，行为测试通过（改标→命中→重置→再命中→正常学习） |
| v2.77 | 2026-08-07 | **标注学习文档落位 + 冲突修正（TDD §1.7 / §3.3 / PRD FR-003-CLUSTER）**: ① §3.3 三条零散记录（v2.75 学习策略 / v2.76 改标重置 / 无样本簇）合并为「标注学习与向量更新」小节，补齐增量平均公式、重置规则与 v2.76 六步行为测试结果；② §1.7「不标注语义」修正——v2.43 原文"照常参与匹配学习"与 v2.75 冲突，改为"照常参与匹配（沿用编号）但不参与向量学习"，并补「改标即重置」实现要点；③ 五端协同：本版仅文档改动，代码保持 v2.76 已同步状态（db.py / voiceprint.py 两端 md5 一致，无需部署） |
| v2.78 | 2026-08-07 | **声纹标注说话人下拉优化（webui.py §1.7 / PRD FR-003-CLUSTER、§8.2 页3）**: ① 选项构建重构——已标注按 `assigned_name` 合并为一行（`named_groups` + `raws=[姓名]+全部 label`），未标注/不标注 unknown 各自一行；新增「显示无发言样本的说话人」复选框（默认隐藏，`sp_utt` 统计 transcripts.speaker 计数判断）；排序：未标注 → 已标注（按姓名）→ 不标注 → 声纹库命名；② 标注操作区对合并姓名展示簇清单（对应 N 个声纹簇），改标/改回**批量作用于全部同名簇**并汇总回填数；单簇标注/不标注逻辑不变；③ 部署验证：webui.py 两端 md5 一致，服务重启 active + HTTP 200 + 日志无异常；实测下拉 46 → 20 行（KevinZH 18 簇合并为 1 行） |
| v2.79 | 2026-08-07 | **认名/学习阈值解耦 + 声纹看板（settings.py / voiceprint.py / db.py / webui.py §3.3 / §1.7）**: ① `VOICEPRINT_CONFIG` 新增 `learn_threshold: 0.75`（可改）；`match_speaker` 命中已标注簇时 `>= learn_threshold` 才 `_learn_into_cluster`，`[threshold_auto, learn_threshold)` 只认名不学；改标重置（`reset_on_next_match`）≥ auto 即重新播种、不受学习阈值限制；② `list_clusters_view` 补 `reset_on_next_match`（看板待重置计数）；③ 新增 `get_voiceprint_dashboard()` + 数据库页底部「声纹匹配 · 学习看板」面板（片段得分分布 / 簇概况 / 人员明细）；④ 部署验证：四文件两端 md5 一致，服务重启 active + HTTP 200，行为测试（0.70 不学 / 0.80 学 / 重置 0.70 重播种）通过 |
| v2.80 | 2026-08-07 | **声纹看板按人员展示（webui.py §1.7 / PRD FR-003-CLUSTER、§8.2 页3）**: ① `get_voiceprint_dashboard()` 重写——按已标注人员聚合（姓名 + 全部簇 label）统计片段得分分档（高置信/认名未学习/疑似/未识别/无得分/合计/待重置簇），附（全部）合计行；② 面板改为阈值行 + 按人员主表 + 概况小字（移除总声纹簇表）；③ 修复合计行待重置簇混入字符串导致 st.dataframe Arrow 序列化失败、表格被裁的问题（统一 int）；④ 部署验证：webui.py 两端 md5 一致，服务重启 active，AppTest 数据库页 0 异常、看板 10 行（9 人 + 合计）完整渲染；UI_VERSION 2026-08-07-13:35:07 |
| v2.81 | 2026-08-07 | **五端一致性 Review（文档工程）**: ① TDD §3.3 匹配机制"三档阈值 0.65/0.50"补 v2.79 学习阈值 0.75，改为"自动 0.65 / 疑似 0.50 / 学习 0.75（认名与学习解耦）"，并指明 `0.65–0.75` 只认名不学习；② 全量运行时文件 md5 复核——26 个文件两端一致（deploy_webui.sh 为 Mac 侧工具按设计不同步）；③ SEC（gitignored）白名单更新为当前 MacBook 办公室地址；交接文档（gitignored）刷新至 v2.80 口径；④ PRD/TDD 版本头与 changelog 核对一致（v1.0→v2.81） |
| v2.82 | 2026-08-07 | **改回未知清除待重置标记（db.py §1.7 / §3.3 / PRD FR-003-CLUSTER）**: ① `unassign_cluster_name` UPDATE 增加 `reset_on_next_match=0`——改回未知后休眠标记不再让看板「待重置簇」误计未标注簇（v2.80 起按人员看板 + v2.76 重置标记的联动细节）；② 存量数据清理：`UPDATE speaker_clusters SET reset_on_next_match=0 WHERE assigned_name IS NULL`（幂等，unknown_0044 等）；③ 行为验证：标注→置位 / 改回→清零 / 再标注→重新置位 / 命中→重播种，临时库六步通过；④ 部署验证：db.py 两端 md5 一致，服务重启 active + HTTP 200；UI_VERSION 无需改（未动 webui.py） |
| v2.83 | 2026-08-07 | **未加载 .env 时 connect() 直接报错（db.py §4.8）**: ① `connect()` 判定改为「无显式 `db_path` + 未设 `ASR_ARCHIVE`」即抛 `RuntimeError`（指引 source .env / run.sh / systemd / 显式 db_path），不执行 `ensure_parent_dir`、不创建 `~/audio_archive` 默认路径空库——v2.47 告警实测复发（2026-08-07 11:01 临时查询制造 0 字节空库）后升级；② 行为验证：无 env 调用抛错且不建目录，`source .env` 后正常连接；③ 部署验证：db.py/run.sh 两端 md5 一致，服务重启 active + HTTP 200；UI_VERSION 无需改（未动 webui.py） |
| v2.84 | 2026-08-07 | **批处理可恢复性 + 一键重新入队（webui.py / process_inbox.py / systemd §3.6 / §4.8 / PRD FR-008-M）**: ① `asr-webui.service` 加 `KillMode=process`——重启服务只杀 webui 主进程，webui 启动的 `process_inbox.py` 不再被 SIGTERM 连坐（2026-08-07 16:05 实战：v2.83 部署重启把运行中批处理杀成 Diarization 失败、文件移入 error/）；② 锁文件 PID 感知——webui `inbox_processing()` 与 `process_inbox._acquire_lock()` 均按「PID 存活且 cmdline 含 process_inbox.py」判定有效锁，否则视为陈旧立即接管（崩溃/SIGKILL 后无需等 6 小时）；③ 新增 `requeue_failed_files()` + 收件箱面板「↩️ 失败文件重新入队并处理」按钮——error/ 当前批次失败音频移回收件箱并启动（.error.txt 留待归档）；④ 行为验证：死 PID 锁立即接管、活 PID 锁拒绝双开、重新入队按钮移动文件并触发处理；⑤ 部署验证：webui.py/process_inbox.py/unit 两端一致，daemon-reload + 服务重启 active + HTTP 200；UI_VERSION 更新（v2.84 部署） |
| v2.85 | 2026-08-07 | **WebUI 轻量化——移除片段试听（webui.py §1.7 / §4.7 / PRD §8.2 页3/页4）**: ① 删除 `render_segment_audio()` 及其两处调用（数据库页「试听发言」+ 文件归档搜索片段回放）——soundfile 整文件读入再切片，大音频加载重/易失败（「无法加载音频片段」根因）；② 「声纹簇·标注学习」发言列表仅对未标注簇渲染（`unlabeled = clusters 非空且无 assigned_name 且非 skip_label`），已标注/不标注显示提示、不再加载音频；③ 文件归档「浏览归档文件」归档音频新增整段回放（`st.audio` 直接喂路径，浏览器拖动进度，不做段偏移）+ 滚动字幕（`audio_path`/`archive_name` 匹配 transcripts，说话人经显示层映射）；④ 部署验证：webui.py 两端 md5 一致，AppTest 页 3/页 4 渲染 0 异常，HTTP 200；UI_VERSION 更新（v2.85 部署） |
| v2.86 | 2026-08-07 | **文件归档体验增强（webui.py §4.7 / PRD §8.2 页4）**: ① 浏览归档文件月份 `<details>` 展开内容包一层限高 div（`max-height:300px; overflow-y:auto`）——文本备份/归档音频按月折叠后不再撑开页面；② 归档音频字幕**随播放自动滚动 + 当前句高亮**——转录行带 `data-start/data-end`（`segment_start_offset/end_offset`），客户端 JS 监听 `<audio>.timeupdate` 命中当前行 `scrollIntoView({block:'center',behavior:'smooth'})` + `.active` 高亮，纯前端、零服务端开销（SQL 仅补两列偏移字段）；③ 部署验证：webui.py 两端 md5 一致，AppTest 页 4 渲染 0 异常，HTTP 200；UI_VERSION 更新（v2.86 部署） |

---

**文档结束**
