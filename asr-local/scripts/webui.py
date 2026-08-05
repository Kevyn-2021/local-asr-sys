"""
Streamlit Web Dashboard — PRD FR-008
4 个页面：概览 / 处理记录 / 声纹库·数据库 / 搜索·文件

UI v2.0 — KVI 视觉风格重构：
- 导航：st.segmented_control 分段控件，每个页签是独立区块，不再依赖脆弱的 CSS 覆盖
- 顶部锁定导航条（v2.41 定稿）：页首单行（Local ASR System + 北京时间）与页签同排，整条吸顶，滚动时始终可见
- 布局：st.container(border=True) 面板，面板头部 = 标题 + 分隔线，区块边界明确
- 排版：代码块行高锁定 1.5；搜索/文件浏览上下堆叠；文本预览限高滚动
- 色彩：灰阶为基（85%），暖赭 #b86a48 作唯一强调色（5%）

启动方式:
  streamlit run scripts/webui.py --server.address 0.0.0.0 --server.port 8501
"""
from __future__ import annotations

import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ_ROOT))

import pandas as pd
import streamlit as st

from config.settings import (
    ARCHIVE_AUDIO_DIR,
    ARCHIVE_DIR,
    ARCHIVE_TEXT_DIR,
    DB_PATH,
    INBOX_DIR,
    INBOX_ERROR_DIR,
    LOG_PATH,
    SUPPORTED_EXTENSIONS,
)
from src.archive import update_txt_files_speaker
from src.db import (
    assign_cluster_name,
    connect,
    get_cluster_label,
    get_person,
    init_db,
    list_clusters_view,
    list_persons,
    list_voiceprints,
    set_cluster_skip,
    unassign_cluster_name,
    update_transcripts_speaker,
    upsert_person,
)
from src.fts import init_fts, search_ids

UI_VERSION = "2026-08-05-22:43:53"

st.set_page_config(page_title="Local ASR System", page_icon="🎙️", layout="wide")
init_db()  # 幂等：建表 + v2.43 skip_label 老库迁移（pipeline 也会调用）

# ── KVI 风格系统 ──────────────────────────────────────────────
st.markdown("""
<style>
:root {
    --accent: #b86a48;
    --accent-soft: rgba(184, 106, 72, 0.10);
    --bg-page: #f7f5ef;
    --bg-panel: #ffffff;
    --bg-subtle: #f8f7f4;
    --fg-0: #1a1a1a;
    --fg-1: #333333;
    --fg-2: #666666;
    --fg-3: #999999;
    --line: #e6e2da;
    --line-subtle: #f0ede7;
}

/* 隐藏 Streamlit 顶部常驻条（整条移除，含工具栏与汉堡菜单） */
header[data-testid="stHeader"] { display: none !important; }
.stAppToolbar { display: none !important; }
#MainMenu { display: none !important; }
[data-testid="stDecoration"] { display: none !important; }

.stApp {
    background: var(--bg-page);
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display",
        "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
        "Noto Sans SC", sans-serif;
}
.block-container { padding-top: 1rem; max-width: 1100px; }

/* ── KVI 配色：消除 Streamlit 默认鲜红，主色统一为暖赭 ── */
a, a:visited { color: var(--accent); }
.stButton > button[kind="primary"],
.stButton > button[data-testid="stBaseButton-primary"] {
    background: var(--accent) !important;
    border-color: var(--accent) !important; color: #fff !important;
}
.stButton > button[kind="primary"]:hover,
.stButton > button[data-testid="stBaseButton-primary"]:hover {
    background: #a55c3c !important; border-color: #a55c3c !important;
}
/* 分段控件 / 单选：选中态暖赭，可点击项明确 cursor */
div[data-testid="stSegmentedControl"] label { cursor: pointer; }
div[data-testid="stSegmentedControl"] label:has(input:checked) {
    background: var(--accent-soft) !important;
    border-color: var(--accent) !important;
}
div[data-testid="stSegmentedControl"] label:has(input:checked) p {
    color: var(--accent) !important; font-weight: 600;
}
div[data-testid="stRadio"] label { cursor: pointer; }
div[data-testid="stRadio"] label:has(input:checked) p { color: var(--fg-0); font-weight: 500; }
input[type="radio"]:checked, input[type="checkbox"]:checked { accent-color: var(--accent); }
/* 输入框聚焦描边 */
[data-baseweb="input"]:focus-within, [data-baseweb="select"]:focus-within {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 1px var(--accent) !important;
}
/* 提示框：中性灰阶，不用蓝/橙功能色抢眼 */
div[data-testid="stAlert"] {
    background: var(--bg-subtle) !important;
    border: 1px solid var(--line) !important;
    color: var(--fg-1) !important; border-radius: 8px;
}
div[data-testid="stAlert"] p { color: var(--fg-1) !important; }

/* ── 顶部锁定导航条（v2.38 实现 / v2.41 单行定稿）：页首 + 导航同排，整条吸顶 ──
   结构：第一行 st.columns = [品牌标题块 | 分段导航]。
   关键：sticky 元素的 margin 区域是透明的，下层内容滚动时会从 margin 处透出，
   所以吸顶条的留白一律用 padding；标题/导航自身的 margin 在此置 0。 */
.topbar-title {
    display: flex; flex-direction: row; align-items: baseline; gap: 12px;
    padding-left: 0.5rem;   /* 品牌块整体右移，避免贴着左边界（v2.40） */
    white-space: nowrap;     /* 单行布局：标题与北京时间不换行（v2.41） */
}
.topbar-brand {
    font-size: 1.05rem; font-weight: 600; color: var(--fg-0);  /* 与面板标题字号一致（v2.41） */
}
.topbar-time {
    font-size: 0.82rem; color: var(--fg-3); font-weight: 400;
}

/* 吸顶条：用 :has(.topbar-title) 定位"页首+导航"所在的行容器
   （不能用 :first-child——CSS 注入的 st.markdown 才是页面第一个元素；
    也不能用 stElementContainer——Streamlit 1.60 的 st.columns 顶层容器是 stLayoutWrapper，
    stElementContainer 只是列内部元素各自的包装，实测 sticky 无效） */
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) {
    position: sticky; top: 0; z-index: 100;
    background: var(--bg-page);
    border-bottom: 1px solid var(--line);
    box-shadow: 0 2px 8px rgba(31, 27, 23, 0.05);
    padding: 0.8rem 0 1.15rem 0;   /* 底部留白：页签与下方面板拉开距离 */
}
/* 行内垂直居中：导航与品牌块对齐 */
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) [data-testid="stHorizontalBlock"] {
    align-items: center;
}
/* 吸顶条内部：标题 markdown margin 压掉（防透底） */
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) div[data-testid="stMarkdownContainer"] {
    margin-bottom: 0 !important;
}

/* ── 分段导航 ──
   Streamlit 1.60 渲染为 div[role="radiogroup"] + button（stSegmentedControl label 结构已不存在）；
   老版本 stSegmentedControl label 规则保留在下方 */
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) div[role="radiogroup"] button {
    cursor: pointer; min-width: 8.5em;
}
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) div[role="radiogroup"] {
    margin-left: 0.75rem;   /* 导航整体往右一点点（v2.41） */
}
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) div[role="radiogroup"] button[aria-checked="true"],
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) div[role="radiogroup"] button[aria-checked="true"] * {
    background: transparent !important;   /* 选中态只保留加粗红色文字，不加背景（v2.40） */
    color: var(--accent) !important;
    font-weight: 600;
}
div[data-testid="stSegmentedControl"] label {
    min-width: 8.5em;
}

/* ── 面板（border 容器） ──
   边框/背景/圆角全部交给 Streamlit 原生 border=True 容器渲染（上一版视觉正常）。
   这里 ONLY 做一件事：给单个面板加大内部底部留白。
   关键：用 :has(> [data-testid="stLayoutWrapper"] > [data-testid="stVerticalBlock"] .panel-head)
   已证仍会命中祖先；改为只命中"直接包含 panel-head 的那个 stVerticalBlock"——
   即 panel-head 向上最近的 stVerticalBlock，用 :has 限定其直接 layoutWrapper 子链。 */
div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .panel-head) {
    padding-bottom: 22px !important;  /* 仅加大底部留白，其余一律不动 */
}
div[data-testid="stVerticalBlock"]:has(> [data-testid="stElementContainer"] .panel-head) p { line-height: 1.65; }

.panel-head {
    display: flex; align-items: baseline; gap: 12px;
    border-bottom: 1px solid var(--line-subtle);
    padding-bottom: 10px; margin-bottom: 16px;
}
.panel-title { font-size: 1.05rem; font-weight: 600; color: var(--fg-0); }
.panel-desc { font-size: 0.82rem; color: var(--fg-3); }

/* ── 状态带（纯指示器：圆点+文字，明确不可点击） ── */
.state-bar {
    display: flex; gap: 24px; margin: 0 0 10px 0; font-size: 0.9rem;
    cursor: default; user-select: none;
}
.state-item {
    display: inline-flex; align-items: center; gap: 7px;
    color: var(--fg-3); font-weight: 400; cursor: default;
}
.state-item::before {
    content: ""; width: 7px; height: 7px; border-radius: 50%;
    border: 1.5px solid var(--fg-3); box-sizing: border-box;
}
.state-item.active { color: var(--fg-0); font-weight: 600; }
.state-item.active::before {
    background: var(--accent); border-color: var(--accent);
}

/* ── 状态说明 ── */
.state-note {
    padding: 14px 20px; border-radius: 8px; margin: 0 0 20px 0;
    background: var(--bg-panel); border: 1px solid var(--line);
    border-left: 3px solid var(--accent);
    color: var(--fg-1); font-size: 0.95rem; line-height: 1.6;
}
.state-note strong { color: var(--fg-0); }
.state-note code {
    background: rgba(0,0,0,0.06); padding: 2px 6px; border-radius: 3px;
    font-size: 0.88rem;
}

/* ── 统计数字（KVI Headline 视觉锚点） ──
   实测：Streamlit 面板内 verticalBlock gap=16px、stat-grid 仅 96px。
   纯文本 inline-flex 布局（无矩形色块），视觉重心靠字号而非边框。 */
.stat-grid {
    display: flex; flex-wrap: wrap; align-items: baseline;
    gap: 2px 40px; margin: 4px 0 2px 0;
}
.stat-cell { display: inline-flex; align-items: baseline; gap: 8px; }
.stat-num {
    font-size: 2rem; font-weight: 700; color: var(--fg-0);
    line-height: 1.1; letter-spacing: -0.02em;
}
.stat-lbl {
    font-size: 0.85rem; color: var(--fg-3); font-weight: 400;
}

/* ── 最近处理 / 搜索结果条目 ── */
.list-item {
    padding: 12px 0; border-bottom: 1px solid var(--line-subtle);
    font-size: 0.95rem; line-height: 1.55;
}
.list-item:last-child { border-bottom: none; }
.list-item .t { color: var(--fg-3); font-size: 0.85rem; white-space: nowrap; }
.list-item .f { color: var(--fg-0); font-weight: 510; }
.list-item .m { color: var(--fg-2); }
.list-item .x { color: var(--fg-1); margin-top: 3px; font-size: 0.9rem; }

/* ── 系统负担条 ── */
.load-row {
    display: flex; align-items: center; gap: 12px;
    padding: 6px 0; font-size: 0.9rem; color: var(--fg-1);
}
.load-bar-bg {
    flex: 1; height: 6px; background: var(--bg-subtle);
    border-radius: 3px; overflow: hidden;
}
.load-bar-fg {
    height: 100%; border-radius: 3px;
    background: var(--fg-0); transition: width 0.3s;
}
.load-label { min-width: 10ch; color: var(--fg-2); }
.load-value { min-width: 14ch; text-align: right; color: var(--fg-1); font-weight: 500; }

/* ── 数据路径块 ── */
.path-block { font-size: 0.92rem; line-height: 1.9; color: var(--fg-1); }
.path-block code {
    background: rgba(0,0,0,0.04); padding: 2px 8px; border-radius: 3px;
    font-size: 0.88rem; color: var(--fg-0);
}

/* ── 建表语句 / 代码块（行高锁定 1.5） ── */
pre, code { line-height: 1.5 !important; }
.schema-item { margin-bottom: 14px; }
.schema-item:last-child { margin-bottom: 0; }
.schema-name {
    font-size: 0.85rem; font-weight: 600; color: var(--accent);
    margin-bottom: 4px;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
.schema-pre {
    background: var(--bg-subtle); border: 1px solid var(--line-subtle);
    border-radius: 6px; padding: 10px 14px; margin: 0;
    font-size: 0.78rem; line-height: 1.5; color: var(--fg-2);
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    overflow-x: auto; white-space: pre-wrap; word-break: break-word;
}

/* ── 归档文件浏览 ── */
.archive-month { margin: 6px 0; }
.archive-month summary {
    cursor: pointer; color: var(--fg-0); font-weight: 500;
    padding: 4px 0; font-size: 0.95rem;
}
.archive-file {
    padding: 3px 0 3px 20px; font-size: 0.88rem; color: var(--fg-1);
    line-height: 1.6;
}
.archive-file code {
    background: rgba(0,0,0,0.04); padding: 1px 6px; border-radius: 3px;
    font-size: 0.84rem;
}

/* ── 文本预览（限高滚动） ── */
.preview-box {
    max-height: 320px; overflow-y: auto;
    background: var(--bg-subtle); border: 1px solid var(--line-subtle);
    border-radius: 6px; padding: 12px 16px; margin-top: 10px;
    font-size: 0.88rem; line-height: 1.6; color: var(--fg-1);
    white-space: pre-wrap; word-break: break-word;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}

/* ── 页脚 ── */
.footer-note {
    margin-top: 32px; padding-top: 14px;
    border-top: 1px solid var(--line-subtle);
    font-size: 0.78rem; color: var(--fg-3); line-height: 1.6;
}

/* ── 音频处理流程框图（KVI：灰阶节点 + 暖赭编号 + 细箭头） ── */
.pipe-flow {
    display: flex; align-items: stretch; gap: 0;
    margin: 8px 0 4px 0; overflow-x: auto;
}
.pipe-node {
    flex: 1; min-width: 0; text-align: left;
    background: var(--bg-subtle); border: 1px solid var(--line-subtle);
    border-radius: 8px; padding: 12px 14px;
}
.pipe-num {
    display: inline-block; font-size: 0.78rem; font-weight: 700;
    color: var(--accent); letter-spacing: 0.04em;
}
.pipe-name {
    font-size: 0.92rem; font-weight: 600; color: var(--fg-0);
    margin-top: 2px;
}
.pipe-model {
    font-size: 0.75rem; color: var(--fg-3); margin-top: 2px;
    font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
.pipe-desc {
    font-size: 0.8rem; color: var(--fg-2); margin-top: 6px; line-height: 1.5;
}
.pipe-arrow {
    align-self: center; color: var(--fg-3); font-size: 1rem;
    padding: 0 8px; flex-shrink: 0; user-select: none;
}
.pipe-io {
    display: flex; flex-direction: column; align-items: flex-start;
    gap: 6px; margin-top: 10px; font-size: 0.78rem; color: var(--fg-3);
}
.pipe-io span {
    background: var(--bg-subtle); border: 1px solid var(--line-subtle);
    border-radius: 4px; padding: 2px 8px;
}

/* ── 收件箱待处理文件 ── */
.inbox-item {
    padding: 6px 0; font-size: 0.92rem; color: var(--fg-1);
    border-bottom: 1px solid var(--line-subtle); line-height: 1.5;
}
.inbox-item:last-of-type { border-bottom: none; }
.inbox-item code {
    background: rgba(0,0,0,0.04); padding: 1px 6px; border-radius: 3px;
    font-size: 0.88rem; color: var(--fg-0);
}
.inbox-item .sz { color: var(--fg-3); font-size: 0.85rem; }
.inbox-empty { color: var(--fg-3); font-size: 0.92rem; padding: 4px 0; }

/* ── 处理进度信息（v2.18：3 行 3 列表格） ── */
.progress-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
}
.progress-table th {
    text-align: left;
    font-size: 0.78rem;
    color: var(--fg-3);
    font-weight: 500;
    padding: 6px 12px;
    border-bottom: 1px solid var(--fg-3);
}
.progress-table td {
    padding: 8px 12px;
    border-bottom: 1px solid var(--bg-subtle);
}
.progress-table .task-name {
    color: var(--fg-1);
    font-weight: 500;
    white-space: nowrap;
}
.progress-table .task-time {
    color: var(--fg-0);           /* 黑色：起始时间 */
    font-weight: 510;
}
.progress-table .elapsed-total {
    color: #8a2f12;               /* 赭红：总耗时 */
    font-weight: 600;
}
.progress-table .elapsed-stage {
    color: var(--accent);         /* 暖赭：当前步骤耗时 */
    font-weight: 600;
}

/* ── Streamlit 组件覆盖 ── */
.stProgress > div > div > div > div { background-color: var(--fg-0) !important; }
.stProgress > div > div { background-color: var(--bg-subtle) !important; }
div[data-testid="stDataFrame"] { font-size: 0.85rem; }
.stSelectbox label, .stDateInput label, .stTextInput label, .stRadio label {
    font-size: 0.85rem !important; color: var(--fg-2) !important;
}
.stButton > button { font-size: 0.9rem !important; }
/* 折叠块：标题即可点击（cursor），内部内容字号降一级 */
div[data-testid="stExpander"] {
    border: 1px solid var(--line-subtle); border-radius: 8px;
    background: var(--bg-panel);
}
div[data-testid="stExpander"] summary { cursor: pointer; }
div[data-testid="stExpander"] summary p {
    font-size: 0.88rem !important; color: var(--fg-2) !important;
}
div[data-testid="stExpander"] [data-testid="stMarkdownContainer"] p,
div[data-testid="stExpander"] [data-testid="stMarkdownContainer"] li {
    font-size: 0.82rem !important;
}
div[data-testid="stExpander"] [data-testid="stJson"] { font-size: 0.78rem; }
/* expander 展开后的表单内容区：加内边距 + 顶部分隔线，形成有边界的表单卡片 */
div[data-testid="stExpander"] details[open] > div,
div[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
    padding: 16px 18px 18px 18px !important;
    border-top: 1px solid var(--line-subtle);
    background: var(--bg-subtle);
    border-radius: 0 0 8px 8px;
}
/* 表单输入控件边框加深一点，在浅色底上更清晰 */
div[data-testid="stExpander"] input,
div[data-testid="stExpander"] textarea,
div[data-testid="stExpander"] [data-baseweb="select"] > div {
    border-color: var(--line) !important;
    background: var(--bg-panel) !important;
}
</style>
""", unsafe_allow_html=True)

STATUS_PATH = ARCHIVE_DIR / "status.json"


# ========== 状态读取 ==========

def read_status() -> dict:
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def derive_state() -> tuple[str, dict]:
    st_dict = read_status()
    now = datetime.now()

    # 1) 锁文件存在 → 正在处理中
    if inbox_processing():
        return "processing", st_dict

    # 2) last_launched_at 兜底：用户刚点击按钮（即使子进程启动失败覆盖了 state）
    launched = st_dict.get("last_launched_at")
    if launched:
        try:
            dt = datetime.fromisoformat(launched)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            if (now - dt).total_seconds() < 300:
                # 5 分钟内刚启动过 → 看 status.json 的实际 state
                state = st_dict.get("state", "idle")
                if state == "processing":
                    # 子进程正在运行中
                    return "processing", st_dict
                # state 为 idle → 检查上次结果
                if st_dict.get("last_result") == "failed":
                    return "failed", st_dict
                # state 为 idle（成功），说明子进程已正常退出
                return "idle", st_dict
        except Exception:
            pass

    # 3) status.json 中 state 为 processing 且更新在 5 分钟内
    state = st_dict.get("state", "idle")
    updated = st_dict.get("updated_at")
    if state == "processing" and updated:
        try:
            dt = datetime.fromisoformat(updated)
            if dt.tzinfo is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            if (now - dt).total_seconds() < 300:
                return "processing", st_dict
        except Exception:
            pass

    # 4) 检查是否有失败但未清理的状态
    if state == "idle" and st_dict.get("last_result") == "failed":
        return "failed", st_dict

    # 5) 空闲
    if state == "idle":
        return "idle", st_dict
    return state, st_dict


# ========== 系统指标 ==========

def get_uptime_str() -> str:
    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        days, rem = divmod(int(secs), 86400)
        hours, mins = divmod(rem, 3600)
        mins //= 60
        if days:
            return f"{days} 天 {hours} 小时"
        if hours:
            return f"{hours} 小时 {mins} 分钟"
        return f"{mins} 分钟"
    except Exception:
        return "—"


def get_cpu_pct() -> float:
    try:
        with open("/proc/stat") as f:
            l1 = [int(v) for v in f.readline().split()[1:]]
        time.sleep(0.5)
        with open("/proc/stat") as f:
            l2 = [int(v) for v in f.readline().split()[1:]]
        total = sum(l2) - sum(l1)
        idle = l2[3] - l1[3]
        return round(100.0 * (total - idle) / total, 0) if total else 0.0
    except Exception:
        return 0.0


def get_memory_info() -> tuple[float, float, float]:
    try:
        with open("/proc/meminfo") as f:
            d = {}
            for line in f:
                k, _, v = line.partition(":")
                d[k.strip()] = int(v.strip().replace(" kB", "")) * 1024
        total = d["MemTotal"]
        used = total - d["MemFree"] - d.get("Buffers", 0) - d.get("Cached", 0)
        pct = round(100.0 * used / total, 0) if total else 0
        return total / 1e9, used / 1e9, pct
    except Exception:
        return 16.0, 0.0, 0.0


def get_disk_info() -> tuple[float, float, float]:
    try:
        s = os.statvfs("/")
        total = s.f_frsize * s.f_blocks
        free = s.f_frsize * s.f_bavail
        used = total - free
        pct = round(100.0 * used / total, 0) if total else 0
        return total / 1e9, free / 1e9, pct
    except Exception:
        return 0, 0, 0


# ========== 数据库查询 ==========

def get_stats() -> dict:
    with connect() as conn:
        row = dict(conn.execute(
            # 音频数量 = 去重文件数（与归档音频数一致）；总时长按文件去重后求和
            # （audio_duration 存的是整文件时长，逐片段 SUM 会重复累加同一文件）
            "SELECT COUNT(DISTINCT file_hash) AS files, "
            "COALESCE((SELECT SUM(d) FROM (SELECT MAX(audio_duration) AS d "
            "FROM transcripts GROUP BY file_hash)), 0) AS total_seconds FROM transcripts"
        ).fetchone())
        # 标注声纹：已标注姓名的声纹簇数（按唯一姓名去重）
        row["labeled"] = conn.execute(
            "SELECT COUNT(DISTINCT assigned_name) AS c FROM speaker_clusters WHERE assigned_name IS NOT NULL"
        ).fetchone()["c"]
        # 录入人员：人物档案里的人数
        row["persons"] = conn.execute("SELECT COUNT(*) AS c FROM persons").fetchone()["c"]
    row["hours"] = round(row["total_seconds"] / 3600, 1)
    return row


def get_recent_records(limit: int = 5) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, source_file, archive_name, speaker, "
            "segment_start_offset, segment_end_offset, absolute_start_time, "
            "absolute_end_time, processed_at, audio_duration, text, confidence, "
            "audio_path, transcript_path "
            "FROM transcripts ORDER BY processed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def speaker_display_map() -> dict[str, str]:
    """unknown_XXXX → 标注姓名 的映射；未标注的不在表中（显示原编号）。
    处理记录/搜索等展示层用它把编号替换为姓名，数据库原文不动。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT label, assigned_name FROM speaker_clusters "
            "WHERE assigned_name IS NOT NULL").fetchall()
    return {r["label"]: r["assigned_name"] for r in rows}


def disp_speaker(speaker: str, mapping: dict[str, str]) -> str:
    """显示用说话人名：已标注的 unknown 显示姓名，否则显示原值"""
    return mapping.get(speaker, speaker)


def apply_cluster_label(cluster_id: int, target: str | None) -> tuple[int, int]:
    """声纹标注统一入口（v2.20：标注为某人 / 改标他人 / 改回未知 共用）：
    1. 更新 speaker_clusters 的 assigned_name（target=None 表示改回未知）
    2. 标注为某人且档案不存在时自动建档（已存在则保留已有资料，不清空）
    3. 回填 transcripts 表与文本备份文件中的说话人标签

    回填匹配串：簇从未标注过 → transcripts 里是原编号（label）；
                已标注过 → transcripts 里已是姓名（此前回填过）。
    改回未知 → 回填到簇的原编号（label 是簇的稳定身份，永不改变）。

    返回 (更新的转录记录数, 更新的文本备份文件数)。"""
    old_label = get_cluster_label(cluster_id) or ""
    cur = next((c for c in list_clusters_view() if c["cluster_id"] == cluster_id), {})
    old_name = cur.get("assigned_name") or None

    match_from = old_name if old_name else old_label  # transcripts 中当前存在形式
    if target:
        assign_cluster_name(cluster_id, target)
        if get_person(target) is None:
            upsert_person(target)
        target_text = target
    else:
        unassign_cluster_name(cluster_id)
        target_text = old_label  # 改回未知 → 回填到原编号

    n_db = update_transcripts_speaker(match_from, target_text) if match_from else 0
    n_txt = update_txt_files_speaker(match_from, target_text) if match_from else 0
    return n_db, n_txt


def get_all_records(limit: int = 200, date_from: str = "", date_to: str = "",
                    speakers: list[str] | None = None) -> list[dict]:
    with connect() as conn:
        sql = ("SELECT id, source_file, archive_name, speaker, "
               "segment_start_offset, segment_end_offset, absolute_start_time, "
               "absolute_end_time, processed_at, audio_duration, text, confidence, "
               "audio_path, transcript_path FROM transcripts WHERE 1=1")
        params: list = []
        if date_from:
            sql += " AND absolute_start_time >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND absolute_start_time <= ?"
            params.append(date_to)
        if speakers:
            ph = ",".join("?" * len(speakers))
            sql += f" AND speaker IN ({ph})"
            params.extend(speakers)
        sql += " ORDER BY absolute_start_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_speaker_list() -> list[str]:
    with connect() as conn:
        return [r["speaker"] for r in conn.execute(
            "SELECT DISTINCT speaker FROM transcripts ORDER BY speaker").fetchall()]


def search_records(keyword: str = "", speakers: list[str] | None = None,
                   date_from: str = "", date_to: str = "",
                   limit: int = 100) -> list[dict]:
    with connect() as conn:
        sql = ("SELECT t.id, t.source_file, t.archive_name, t.speaker, "
               "t.segment_start_offset, t.segment_end_offset, t.absolute_start_time, "
               "t.absolute_end_time, t.processed_at, t.audio_duration, t.text, "
               "t.confidence, t.audio_path, t.transcript_path "
               "FROM transcripts t WHERE 1=1")
        params: list = []
        if keyword.strip():
            ids = search_ids(keyword.strip())
            if not ids:
                return []  # 有关键词但无匹配，直接返回空（避免 IN () 语法错误）
            ph = ",".join("?" * len(ids))
            sql += f" AND t.id IN ({ph})"
            params.extend(ids)
        if speakers:
            ph = ",".join("?" * len(speakers))
            sql += f" AND t.speaker IN ({ph})"
            params.extend(speakers)
        if date_from:
            sql += " AND t.absolute_start_time >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND t.absolute_start_time <= ?"
            params.append(date_to)
        sql += " ORDER BY t.absolute_start_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_db_schema() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' "
            "AND name NOT LIKE '%_fts%' AND name NOT LIKE '%_config' "
            "AND name NOT LIKE '%_data' AND name NOT LIKE '%_docsize' "
            "AND name NOT LIKE '%_idx' ORDER BY type, name"
        ).fetchall()
    return [dict(r) for r in rows]


def get_db_size() -> str:
    try:
        sz = DB_PATH.stat().st_size
        if sz < 1024 * 1024:
            return f"{sz / 1024:.0f} KB"
        return f"{sz / 1024 / 1024:.1f} MB"
    except Exception:
        return "—"


def list_archive_files(dir_path: Path) -> list[dict]:
    files = []
    if not dir_path.exists():
        return files
    for month_dir in sorted(dir_path.iterdir(), reverse=True):
        if not month_dir.is_dir():
            continue
        for f in sorted(month_dir.iterdir(), reverse=True):
            files.append({
                "path": str(f), "name": f.name,
                "size": f.stat().st_size,
                "month": month_dir.name, "suffix": f.suffix,
            })
    return files


def count_error_files() -> int:
    """统计 error/ 目录下 .error.txt 日志文件数量"""
    try:
        return sum(1 for p in INBOX_ERROR_DIR.iterdir()
                   if p.name.lower().endswith(".error.txt")) if INBOX_ERROR_DIR.exists() else 0
    except Exception:
        return 0


# ========== 手动处理收件箱（FR-008-M） ==========

INBOX_LOCK = ARCHIVE_DIR / "process_inbox.lock"


def scan_inbox_files() -> list[dict]:
    """递归扫描收件箱（含子文件夹）的待处理音频；排除 error/；
    同 stem 多格式合并为一行显示"""
    seen: dict[str, dict] = {}
    if not INBOX_DIR.exists():
        return []
    for p in sorted(INBOX_DIR.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if INBOX_ERROR_DIR in p.parents:
            continue
        rel = str(p.relative_to(INBOX_DIR))
        size_mb = p.stat().st_size / 1024 / 1024
        if p.stem in seen:
            seen[p.stem]["size_mb"] += size_mb
            seen[p.stem]["multi"] = True
        else:
            seen[p.stem] = {"rel": rel, "size_mb": size_mb, "multi": False}
    return list(seen.values())


def inbox_processing() -> bool:
    """是否有手动处理任务正在进行（锁文件 6 小时内视为有效）"""
    try:
        if not INBOX_LOCK.exists():
            return False
        return (time.time() - INBOX_LOCK.stat().st_mtime) < 6 * 3600
    except Exception:
        return False


def trigger_process_inbox() -> tuple[bool, str]:
    """后台启动 process_inbox.py，返回 (是否成功, 提示信息)"""
    if inbox_processing():
        return False, "已有处理任务正在进行，请稍候"
    venv_py = PROJ_ROOT / ".venv" / "bin" / "python"
    script = PROJ_ROOT / "scripts" / "process_inbox.py"
    if not venv_py.exists():
        return False, f"Python 解释器不存在: {venv_py}"
    if not script.exists():
        return False, f"处理脚本不存在: {script}"
    try:
        # 在启动子进程前，立即写入 status.json 的 state="processing"，
        # 让 WebUI 的 derive_state() 能第一时间感知（即使子进程锁文件尚未创建）
        _write_status_prelaunch()
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJ_ROOT)
        logf = open(LOG_PATH, "a", encoding="utf-8")
        subprocess.Popen(
            [str(venv_py), str(script)],
            cwd=str(PROJ_ROOT), env=env,
            stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        return True, "已启动处理，状态带会实时更新进度"
    except Exception as e:
        return False, f"启动失败：{e}"


def prepare_inbox() -> tuple[bool, str]:
    """「准备处理收件箱」（v2.17）：
    1. 将 error/ 根目录下的旧错误文件归档到 error/archived/（文件名附创建时间戳）
    2. 解锁：若存在残留锁文件（陈旧锁，非处理中）则删除
    返回 (是否成功, 提示信息)。"""
    from src.archive import archive_error_files

    # 1. 归档错误文件
    archived = archive_error_files()

    # 2. 解锁：只有"非处理中"时才能删锁（新鲜锁视为任务运行中，不碰）
    unlocked = False
    if INBOX_LOCK.exists() and not inbox_processing():
        try:
            INBOX_LOCK.unlink()
            unlocked = True
        except Exception:
            pass

    parts = []
    if archived:
        parts.append(f"已归档 {archived} 个旧错误文件到 error/archived/")
    else:
        parts.append("error/ 根目录无旧错误文件")
    if unlocked:
        parts.append("已清除残留锁文件（上一次异常退出的锁）")
    elif INBOX_LOCK.exists():
        parts.append("检测到处理任务正在运行，锁文件保留")
    else:
        parts.append("锁文件不存在，无需解锁")
    return True, "；".join(parts)


def _write_status_prelaunch():
    """在启动子进程前，立即写入 'processing' 状态，防止窗口期 WebUI 显示 '空闲'。
    写入 last_launched_at 时间戳，即使子进程失败覆盖了 state，WebUI 也能据此判断有任务刚启动。"""
    try:
        data = {}
        try:
            data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
        data["state"] = "processing"
        data["current_file"] = None
        data["stage"] = "启动中"
        data["pending_count"] = 0
        data["last_launched_at"] = datetime.now().isoformat()
        data["updated_at"] = datetime.now().isoformat()
        STATUS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def fmt_time(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        today = datetime.now().date()
        if dt.date() == today:
            return f"今天 {dt.strftime('%H:%M')}"
        return dt.strftime("%m-%d %H:%M")
    except Exception:
        return iso[:16]


def fmt_full_time(iso: str | None) -> str:
    """完整时间格式（含秒），供处理进度表格的「起始时间」列使用（v2.18）"""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return iso[:19]


def fmt_elapsed(iso: str | None) -> str:
    """计算从 iso 时间到现在的耗时，返回人性化字符串"""
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        elapsed = (datetime.now() - dt).total_seconds()
        if elapsed < 0:
            return ""
        mins, secs = divmod(int(elapsed), 60)
        hours, mins = divmod(mins, 60)
        if hours:
            return f"{hours} 时 {mins} 分 {secs} 秒"
        if mins:
            return f"{mins} 分 {secs} 秒"
        return f"{secs} 秒"
    except Exception:
        return ""


def clean_text(text: str | None, limit: int = 0) -> str:
    """清洗 ASR 文本用于显示（复用 archive._clean_asr_text，避免两处维护同一逻辑）。
    数据库中的文本已在 pipeline 阶段清洗过，此函数仅作展示层安全兜底 + HTML 转义。"""
    from src.archive import _clean_asr_text
    t = _clean_asr_text(text or "")
    if limit and len(t) > limit:
        t = t[:limit] + "…"
    return html.escape(t)


# ========== 渲染辅助 ==========

STATES = [
    ("idle", "空闲"),
    ("processing", "处理中"),
    ("failed", "处理失败"),
]


def render_state_bar(current: str):
    items = []
    for key, label in STATES:
        cls = "active" if key == current else ""
        items.append(f"<span class='state-item {cls}'>{label}</span>")
    st.markdown(f"<div class='state-bar'>" + "".join(items) + "</div>",
                unsafe_allow_html=True)


def render_state_detail(state: str, st_dict: dict):
    if state == "processing":
        fname = st_dict.get("current_file") or "准备中..."
        stage = st_dict.get("stage") or "…"
        pending = st_dict.get("pending_count", 0)
        extra = f"　·　队列中还有 {pending} 个文件" if pending else ""
        html_body = f"正在处理 <strong>{html.escape(str(fname))}</strong> — {html.escape(str(stage))}{extra}"
    elif state == "failed":
        # 处理失败状态：显示错误信息，用户可点击重试
        last_file = st_dict.get("last_completed_file") or "未知错误"
        last_at = st_dict.get("last_completed_at")
        html_body = (f"<div style='padding:4px 0 8px 0;'>上次处理出错，请根据错误信息调整后重试：</div>"
                     f"<div style='padding:10px 14px;background:#fef2f2;"
                     f"border:1px solid #fecaca;border-radius:6px;color:#991b1b;font-size:0.9rem;'>"
                     f"⚠️ {html.escape(str(last_file))}</div>")
        if last_at:
            html_body += f"<div style='margin-top:6px;font-size:0.85rem;color:var(--fg-3);'>出错时间：{fmt_time(last_at)}</div>"
        # 同时显示收件箱状态
        n = len(scan_inbox_files())
        if n:
            html_body += f"<div style='margin-top:10px;'>收件箱还有 <strong>{n}</strong> 个待处理文件。</div>"
    else:
        # 空闲：以实际收件箱扫描为准
        n = len(scan_inbox_files())
        last_file = st_dict.get("last_completed_file")
        last_at = st_dict.get("last_completed_at")
        last_ok = st_dict.get("last_result") == "success"
        if n:
            html_body = f"收件箱有 <strong>{n}</strong> 个待处理文件，点击下方「开始处理收件箱」按钮。"
        elif last_file:
            mark = "成功" if last_ok else "失败"
            html_body = f"收件箱为空。上次处理：{html.escape(str(last_file))}（{fmt_time(last_at)}，{mark}）"
        else:
            html_body = "收件箱为空，等待音频放入…"
        err_n = count_error_files()
        if err_n:
            html_body += f"　<span style='color:#999;'>（error/ 中有 {err_n} 条错误日志）</span>"
    st.markdown(f"<div class='state-note'>{html_body}</div>", unsafe_allow_html=True)


def render_pipeline_diagram() -> str:
    """KVI 风格横向流程图：4 个模型的分工与产出（灰阶节点 + 暖赭编号）"""
    stages = [
        ("01", "VAD 语音检测", "Silero VAD", "找出音频里哪些片段有人在说话，过滤静音与噪声。"),
        ("02", "说话人分离", "pyannote diarization-3.1", "先按 VAD 语音段拼接（切除静音加速），再把语音按“谁在说”切成若干段，每段标记一个匿名说话人。"),
        ("03", "声纹识别", "pyannote embedding", "给每个说话人提取声纹向量，与声纹库/已标注声纹簇比对认出是谁；认不出则新建 unknown 编号待标注。"),
        ("04", "ASR 语音转文字", "Qwen3-ASR-1.7B", "把每一小段语音转成文字，得到带绝对时间戳的转录片段。"),
    ]
    nodes = []
    for i, (num, name, model, desc) in enumerate(stages):
        nodes.append(
            f"<div class='pipe-node'>"
            f"<span class='pipe-num'>{num}</span>"
            f"<div class='pipe-name'>{html.escape(name)}</div>"
            f"<div class='pipe-model'>{html.escape(model)}</div>"
            f"<div class='pipe-desc'>{html.escape(desc)}</div>"
            f"</div>"
        )
        if i < len(stages) - 1:
            nodes.append("<div class='pipe-arrow'>→</div>")
    flow = "<div class='pipe-flow'>" + "".join(nodes) + "</div>"
    io = (
        "<div class='pipe-io'>"
        "<span>输入：收件箱音频（wav / flac / m4a / mp3 / opus / ogg / webm，按格式优先级排序）</span>"
        "<span>产出：带时间戳的转录文字 · 归档音频 · txt/json 备份 · SQLite 入库</span>"
        "</div>"
    )
    return flow + io


def panel(title: str, desc: str = ""):
    """带边框的区块面板：头部 = 标题 + 说明 + 分隔线，内容为 Streamlit 上下文。"""
    c = st.container(border=True)
    head = f"<div class='panel-head'><span class='panel-title'>{title}</span>"
    if desc:
        head += f"<span class='panel-desc'>{desc}</span>"
    head += "</div>"
    with c:
        st.markdown(head, unsafe_allow_html=True)
    return c


def render_segment_audio(row: dict):
    p = row.get("audio_path")
    if p and Path(p).exists():
        try:
            import soundfile as sf
            y, sr = sf.read(p, always_2d=False)
            s = max(0, int(row["segment_start_offset"] * sr))
            e = min(len(y), int(row["segment_end_offset"] * sr))
            st.audio(y[s:e], sample_rate=sr, format="audio/wav")
        except Exception:
            st.warning("无法加载音频片段")
    else:
        st.info("该片段无对应音频文件")


# ========== 页面 ==========

NAV_OPTIONS = ["概览 · 状态", "处理记录", "声纹库 · 数据库", "搜索 · 文件"]

# ── 顶部锁定导航条：页首（标题+时间同排）与导航同排，整条吸顶（CSS） ──
col_brand, col_nav = st.columns([1.2, 1.8], gap="medium")
with col_brand:
    st.markdown(
        f"<div class='topbar-title'>"
        f"<span class='topbar-brand'>Local ASR System</span>"
        f"<span class='topbar-time'>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 北京时间</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
with col_nav:
    if hasattr(st, "segmented_control"):
        page = st.segmented_control(
            "导航", NAV_OPTIONS,
            default=NAV_OPTIONS[0], label_visibility="collapsed", key="nav",
        )
    else:
        page = st.radio(
            "导航", NAV_OPTIONS,
            horizontal=True, label_visibility="collapsed", key="nav",
        )
if page is None:
    page = NAV_OPTIONS[0]

# ================================================================
# 页 1 — 概览 · 状态
# ================================================================
if page == "概览 · 状态":
    state, st_dict = derive_state()

    # 处理中或刚失败时 15 秒自动刷新，平时 10 分钟（v2.18 由 5 秒放宽，避免过于频繁）
    try:
        from streamlit_autorefresh import st_autorefresh
        busy_now = (state in ("processing", "failed")) or inbox_processing()
        st_autorefresh(interval=15_000 if busy_now else 600_000, key="overview_refresh")
    except ImportError:
        pass

    render_state_bar(state)
    render_state_detail(state, st_dict)

    if state == "processing":
        stages = ["加载音频", "VAD 语音检测", "说话人分离", "声纹匹配", "ASR 转录", "归档与入库"]
        cur = st_dict.get("stage") or ""
        try:
            current_idx = stages.index(cur)
        except ValueError:
            current_idx = 0

        # 渲染阶段进度条
        stage_html = '<div style="display:flex;gap:4px;margin:8px 0 16px 0;flex-wrap:wrap;">'
        for i, s in enumerate(stages):
            if i < current_idx:
                # 已完成 - 绿色
                bg = "#d4edda"
                color = "#155724"
                border = "#c3e6cb"
                icon = "✓"
            elif i == current_idx:
                # 正在处理 - 暖赭色高亮
                bg = "#fef0e8"
                color = "#b86a48"
                border = "#e8c9b8"
                icon = "●"
            else:
                # 未处理 - 灰色
                bg = "#f5f5f5"
                color = "#cccccc"
                border = "#e8e8e8"
                icon = "○"
            stage_html += f'<div style="flex:1;min-width:80px;padding:8px 10px;border-radius:6px;background:{bg};border:1px solid {border};text-align:center;font-size:0.78rem;line-height:1.3;">'
            stage_html += f'<div style="font-weight:600;color:{color};margin-bottom:2px;">{icon} {s}</div>'
            stage_html += '</div>'
        stage_html += '</div>'
        st.markdown(stage_html, unsafe_allow_html=True)

        # ── 处理进度信息（v2.18：3 行 3 列表格：任务/起始时间/耗时） ──
        proc_start = st_dict.get("processing_start_time")
        stage_start = st_dict.get("stage_start_time")
        cur_stage = st_dict.get("stage") or "…"
        cur_file = st_dict.get("current_file") or ""
        if proc_start or stage_start:
            c = panel("处理进度", f"当前文件：{html.escape(str(cur_file))}" if cur_file else "进度详情")
            with c:
                elapsed_total = fmt_elapsed(proc_start) if proc_start else "—"
                elapsed_stage = fmt_elapsed(stage_start) if stage_start else "—"
                time_total = fmt_full_time(proc_start) if proc_start else "—"
                time_stage = fmt_full_time(stage_start) if stage_start else "—"
                st.markdown(
                    f"<table class='progress-table'>"
                    f"<thead><tr><th>任务</th><th>起始时间</th><th>耗时</th></tr></thead>"
                    f"<tbody>"
                    f"<tr><td class='task-name'>总任务</td>"
                    f"<td class='task-time'>{time_total}</td>"
                    f"<td class='elapsed-total'>{elapsed_total}</td></tr>"
                    f"<tr><td class='task-name'>当前步骤·{html.escape(cur_stage)}</td>"
                    f"<td class='task-time'>{time_stage}</td>"
                    f"<td class='elapsed-stage'>{elapsed_stage}</td></tr>"
                    f"</tbody></table>",
                    unsafe_allow_html=True,
                )

    # ── 收件箱 · 手动处理（FR-008-M） ──
    pending_files = scan_inbox_files()
    busy = inbox_processing()
    c = panel("收件箱 · 手动处理", f"{len(pending_files)} 个待处理" if pending_files else "放入音频后点此处理")
    with c:
        if pending_files:
            rows = []
            for f in pending_files:
                tag = " <span class='sz'>（多格式）</span>" if f["multi"] else ""
                rows.append(
                    f"<div class='inbox-item'><code>{html.escape(f['rel'])}</code>"
                    f" <span class='sz'>{f['size_mb']:.0f} MB</span>{tag}</div>"
                )
            st.markdown("".join(rows), unsafe_allow_html=True)
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
        else:
            st.markdown("<div class='inbox-empty'>收件箱为空。把音频文件（支持子文件夹）放入 "
                        f"<code>{INBOX_DIR}/</code> 后，点击下方按钮开始处理。</div>",
                        unsafe_allow_html=True)
        # 两列并排：左侧「准备处理收件箱」，右侧「开始处理收件箱」（v2.17）
        col_prep, col_start = st.columns([1, 2])
        with col_prep:
            prep_clicked = st.button(
                "🧹 准备处理收件箱" if not busy else "⏳ 处理中…",
                disabled=busy,
                use_container_width=True, key="btn_prepare",
            )
        with col_start:
            clicked = st.button(
                "▶ 开始处理收件箱" if not busy else "⏳ 正在处理中…",
                type="primary", disabled=busy or not pending_files,
                use_container_width=True, key="btn_process",
            )
        if prep_clicked:
            ok, msg = prepare_inbox()
            if ok:
                st.success(msg)
                time.sleep(0.8)
                st.rerun()
            else:
                st.warning(msg)
        if clicked:
            ok, msg = trigger_process_inbox()
            if ok:
                st.success(msg)
                time.sleep(1.5)
                st.rerun()
            else:
                st.warning(msg)

    c = panel("处理成果", "全部历史累计")
    with c:
        stats = get_stats()
        st.markdown(
            f"<div class='stat-grid'>"
            f"<div class='stat-cell'><span class='stat-num'>{stats['files']}</span><span class='stat-lbl'>音频数量</span></div>"
            f"<div class='stat-cell'><span class='stat-num'>{stats['labeled']}</span><span class='stat-lbl'>标注声纹</span></div>"
            f"<div class='stat-cell'><span class='stat-num'>{stats['persons']}</span><span class='stat-lbl'>录入人员</span></div>"
            f"<div class='stat-cell'><span class='stat-num'>{stats['hours']}</span><span class='stat-lbl'>累计时长 (h)</span></div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    c = panel("系统负担", "ThinkPad 实时资源占用")
    with c:
        cpu = get_cpu_pct()
        mem_total, mem_used, mem_pct = get_memory_info()
        disk_total, disk_free, disk_pct = get_disk_info()
        bars = []
        for label, pct, val in [
            ("CPU", cpu, f"{cpu:.0f}%"),
            ("内存", mem_pct, f"{mem_pct:.0f}%（{mem_used:.1f} / {mem_total:.0f} GB）"),
            ("磁盘", disk_pct, f"{disk_pct:.0f}%（剩余 {disk_free:.0f} GB）"),
        ]:
            bars.append(
                f"<div class='load-row'>"
                f"<span class='load-label'>{label}</span>"
                f"<div class='load-bar-bg'><div class='load-bar-fg' style='width:{min(pct,100)}%'></div></div>"
                f"<span class='load-value'>{val}</span>"
                f"</div>"
            )
        bars.append(
            f"<div style='font-size:0.85rem;color:var(--fg-3);margin-top:8px;'>"
            f"系统已运行 {get_uptime_str()}</div>"
        )
        st.markdown("".join(bars), unsafe_allow_html=True)

    c = panel("数据位置", "所有数据只存在 ThinkPad 本机")
    with c:
        audio_files = list_archive_files(ARCHIVE_AUDIO_DIR)
        text_files = list_archive_files(ARCHIVE_TEXT_DIR)
        st.markdown(
            f"<div class='path-block'>"
            f"<strong>转录数据库</strong>　<code>{DB_PATH}</code>（{get_db_size()}）<br>"
            f"<strong>归档音频</strong>　　<code>{ARCHIVE_AUDIO_DIR}/</code>（{len(audio_files)} 个文件，按月份分目录）<br>"
            f"<strong>文本备份</strong>　　<code>{ARCHIVE_TEXT_DIR}/</code>（{len(text_files)} 个文件，txt/json 两种格式）"
            f"</div>",
            unsafe_allow_html=True,
        )

    c = panel("音频处理流程", "每个文件经过的 4 个模型，分工与产出")
    with c:
        st.markdown(render_pipeline_diagram(), unsafe_allow_html=True)

# ================================================================
# 页 2 — 处理记录
# ================================================================
elif page == "处理记录":
    sp_map = speaker_display_map()  # unknown_XXXX → 标注姓名
    c = panel("最近处理", "最新 5 条转录片段")
    with c:
        recent = get_recent_records(5)
        if recent:
            items_html = []
            for r in recent:
                t = fmt_time(r["processed_at"])
                dur = f"{r['audio_duration']:.0f}s" if r.get("audio_duration") else "—"
                who = disp_speaker(str(r["speaker"]), sp_map)
                items_html.append(
                    f"<div class='list-item'>"
                    f"<span class='t'>{t}</span>&nbsp;&nbsp;<span class='f'>{html.escape(str(r['source_file']))}</span>"
                    f"<span class='m'>　{html.escape(who)}　{dur}</span>"
                    f"<div class='x'>{clean_text(r['text'], 60)}</div></div>"
                )
            st.markdown("".join(items_html), unsafe_allow_html=True)
        else:
            st.markdown("<span style='color:var(--fg-3);'>暂无记录</span>",
                        unsafe_allow_html=True)

    c = panel("筛选条件", "按日期范围和说话人过滤")
    with c:
        c1, c2 = st.columns([3, 1])
        with c1:
            dr = st.date_input("日期范围", value=(
                datetime.now() - timedelta(days=30), datetime.now() + timedelta(days=1)))
        with c2:
            sp_map = speaker_display_map()
            raw_speakers = get_speaker_list()
            display_to_raws: dict[str, list[str]] = {}
            seen: set[str] = set()
            display_speakers: list[str] = []
            for s in raw_speakers:
                d = disp_speaker(s, sp_map)
                display_to_raws.setdefault(d, []).append(s)
                if d not in seen:
                    seen.add(d)
                    display_speakers.append(d)
            sp_display = st.selectbox("说话人", [""] + display_speakers)
            sp_list = display_to_raws.get(sp_display) if sp_display else None
        date_from = dr[0].isoformat() if isinstance(dr, tuple) else ""
        date_to = dr[1].isoformat() if isinstance(dr, tuple) else ""

    records = get_all_records(date_from=date_from, date_to=date_to, speakers=sp_list)

    c = panel("片段记录", f"共 {len(records)} 条")
    with c:
        if not records:
            st.info("暂无处理记录")
        else:
            df = pd.DataFrame(records)
            df["说话人"] = df["speaker"].map(lambda s: disp_speaker(str(s), sp_map))
            view = df[["id", "source_file", "说话人", "audio_duration",
                       "absolute_start_time", "processed_at"]].copy()
            view.columns = ["ID", "源文件", "说话人", "时长(s)", "开始时间", "处理完成"]
            st.dataframe(view, width='stretch', hide_index=True)

    if records:
        c = panel("片段详情", "选一条记录查看文本并回放音频；已标注的直接显示姓名")
        with c:
            sel_id = st.selectbox("选择记录 ID", df["id"].tolist())
            row = df[df["id"] == sel_id].iloc[0]
            who = disp_speaker(str(row["speaker"]), sp_map)
            st.markdown(
                f"<div style='font-size:0.95rem;'><strong>{html.escape(str(row['source_file']))}</strong>"
                f" — <strong style='color:var(--accent);'>{html.escape(who)}</strong></div>"
                f"<div style='font-size:0.88rem;color:var(--fg-2);margin:6px 0;'>"
                f"绝对时间：{row['absolute_start_time']} → {row['absolute_end_time']}</div>"
                f"<div style='margin-top:8px;padding:12px 16px;background:var(--bg-subtle);"
                f"border-radius:6px;font-size:0.95rem;line-height:1.6;'>{clean_text(row['text'])}</div>",
                unsafe_allow_html=True,
            )
            render_segment_audio(row)

# ================================================================
# 页 3 — 声纹库 · 数据库
# ================================================================
elif page == "声纹库 · 数据库":
    c = panel("声纹怎么来的", "不需要专门录入——处理音频时自动抓取，你负责标注")
    with c:
        st.markdown(
            "<div style='font-size:0.95rem;color:var(--fg-2);line-height:1.8;'>"
            "这套系统<strong>不需要单独录入声纹</strong>。每次处理音频时，"
            "系统会自动从声音片段里抓取每个说话人的声纹，记为一个 <code>unknown_XXXX</code> 编号（见下方「声纹簇·标注学习」）。"
            "你只要把认出的人<strong>标注上姓名</strong>，系统就会把这个声纹和姓名关联起来，"
            "并在后续每次出现时不断学习、越认越准。<br>"
            "也就是说：<strong>你标注 → 系统学习 → 下次自动认出</strong>，如此循环配合。"
            "</div>",
            unsafe_allow_html=True,
        )

    # ── 声纹簇标注（FR-003-CLUSTER + v2.20 标注校准 + v2.43 不标注）：标注为某人 / 校准已标注 / 不标注 ──
    c = panel("声纹簇 · 标注学习", "自动识别 + 手工校准：认不出的标注为某人；标错的改标他人或改回未知；陌生人设为不标注保持原编号")
    with c:
        clusters = list_clusters_view()
        if not clusters:
            st.markdown(
                "<div style='font-size:0.95rem;color:var(--fg-2);'>"
                "还没有识别出任何说话人簇。处理音频后，每个匿名说话人会以 "
                "<code>unknown_0001</code>、<code>unknown_0002</code>… 的形式出现在这里。"
                "</div>",
                unsafe_allow_html=True,
            )
        else:
            # 全部簇一览（含已标注）：编号是簇的稳定身份，标注列显示姓名或"（未标注）"
            cl_df = pd.DataFrame(clusters)

            def _cell(r):
                if r["assigned_name"]:
                    return r["assigned_name"]
                return "🚫 不标注" if r.get("skip_label") else "（未标注）"

            cl_df["标注"] = cl_df.apply(_cell, axis=1)
            view = cl_df[["cluster_id", "label", "标注", "sample_count"]].copy()
            view.columns = ["ID", "编号", "标注为", "学习样本数"]
            st.dataframe(view, width='stretch', hide_index=True)

            unassigned = [c for c in clusters
                          if not c.get("assigned_name") and not c.get("skip_label")]
            assigned = [c for c in clusters if c.get("assigned_name")]
            persons = list_persons()
            known_names = [p["person_name"] for p in persons]

            tab_mark, tab_cal, tab_skip = st.tabs(
                ["✏️ 标注为某人", "🔧 校准已标注", "🚫 不标注"])

            # ── Tab 1：未标注 → 标注为某人 ──
            with tab_mark:
                st.markdown(
                    "<div style='font-size:0.85rem;color:var(--fg-3);margin:4px 0 2px 0;'>"
                    "系统没认出的说话人（未标注编号）→ 标注为某人，下次自动认出：</div>",
                    unsafe_allow_html=True,
                )
                if not unassigned:
                    st.info("当前没有待标注的编号")
                else:
                    ca, cb = st.columns([2, 2])
                    with ca:
                        options = {f"{c['label']}（未标注）": c["cluster_id"]
                                   for c in unassigned}
                        sel_cluster = st.selectbox("选择编号", list(options.keys()), key="cal_sel_un")
                    with cb:
                        name_choice = st.selectbox(
                            "标注为", ["（输入新姓名）"] + known_names, key="cal_name_un")
                    new_name = ""
                    if name_choice == "（输入新姓名）":
                        new_name = st.text_input("新姓名（唯一，不含空格）", key="cal_new_un")
                    target_name = new_name.strip() if name_choice == "（输入新姓名）" else name_choice
                    if st.button("确认标注并回填", key="btn_assign"):
                        if not target_name:
                            st.warning("请输入或选择一个姓名")
                        elif " " in target_name:
                            st.warning("姓名不能含空格")
                        else:
                            n_db, n_txt = apply_cluster_label(options[sel_cluster], target_name)
                            st.success(
                                f"已把 {get_cluster_label(options[sel_cluster])} 标注为 {target_name}，"
                                f"系统会持续学习该声纹。已更新 {n_db} 条转录记录、{n_txt} 个文本备份文件。")
                            st.rerun()

            # ── Tab 2：已标注 → 改标他人 / 改回未知（v2.20 标注校准） ──
            with tab_cal:
                st.markdown(
                    "<div style='font-size:0.85rem;color:var(--fg-3);margin:4px 0 2px 0;'>"
                    "纠正自动标注：改标为另一人，或改回未知（沿用原编号，可再标注）：</div>",
                    unsafe_allow_html=True,
                )
                if not assigned:
                    st.info("当前没有已标注的簇")
                else:
                    opt2 = {f"{c['label']} → {c['assigned_name']}": c["cluster_id"]
                            for c in assigned}
                    sel2 = st.selectbox("选择已标注簇", list(opt2.keys()), key="cal_sel_as")
                    cid2 = opt2[sel2]
                    cur_c = next(c for c in assigned if c["cluster_id"] == cid2)
                    cur_name = str(cur_c["assigned_name"])
                    cur_label = str(cur_c["label"])

                    others = [n for n in known_names if n != cur_name]
                    name_choice2 = st.selectbox(
                        "改标为", ["（输入新姓名）"] + others, key="cal_name_as")
                    new_name2 = ""
                    if name_choice2 == "（输入新姓名）":
                        new_name2 = st.text_input("新姓名（唯一，不含空格）", key="cal_new_as")
                    target2 = new_name2.strip() if name_choice2 == "（输入新姓名）" else name_choice2
                    if st.button("改标并回填", key="btn_reassign"):
                        if not target2:
                            st.warning("请输入或选择目标姓名")
                        elif " " in target2:
                            st.warning("姓名不能含空格")
                        else:
                            n_db, n_txt = apply_cluster_label(cid2, target2)
                            st.success(
                                f"已把 {cur_label} 从 {cur_name} 改标为 {target2}，"
                                f"更新 {n_db} 条转录记录、{n_txt} 个文本备份文件。")
                            st.rerun()

                    st.divider()
                    # 改回未知：两步确认，防误操作（编号沿用簇的原 label）
                    if st.button(f"改回未知（沿用编号 {cur_label}）", key="btn_unassign"):
                        st.session_state["unassign_cid"] = cid2
                    if st.session_state.get("unassign_cid") == cid2:
                        st.warning(
                            f"确认把「{cur_label} → {cur_name}」改回未知（编号 {cur_label}）？\n\n"
                            f"将把转录记录与文本备份中所有 {cur_name} 改回 {cur_label}。"
                            f"该编号会重新出现在「标注为某人」列表，可随时再标注。")
                        if st.button("确认改回", key="btn_unassign_ok"):
                            n_db, n_txt = apply_cluster_label(cid2, None)
                            st.success(
                                f"已改回未知：{cur_label}（原标注 {cur_name}），"
                                f"更新 {n_db} 条转录记录、{n_txt} 个文本备份文件。")
                            st.session_state.pop("unassign_cid", None)
                            st.rerun()

            # ── Tab 3：不标注（v2.43）——陌生人保持原编号，不参与标注流程；可恢复 ──
            with tab_skip:
                st.markdown(
                    "<div style='font-size:0.85rem;color:var(--fg-3);margin:4px 0 2px 0;'>"
                    "有些陌生人不值得标注：设为「不标注」后保持原编号（unknown_XXXX），"
                    "不再出现在「标注为某人」列表；随时可恢复标注。"
                    "（不标注只隐藏标注入口，不影响声纹匹配与学习）</div>",
                    unsafe_allow_html=True,
                )
                all_un = [c for c in clusters if not c.get("assigned_name")]
                normal_un = [c for c in all_un if not c.get("skip_label")]
                skipped = [c for c in all_un if c.get("skip_label")]
                if not all_un:
                    st.info("当前没有未标注编号")
                else:
                    # 每组：多选框与按钮同行；bottom 对齐使按钮与下拉框本身对齐
                    # （label 在框上方，不参与按钮定位；按钮与下拉框等高 40px，v2.45）
                    row_skip = st.columns([3, 1], vertical_alignment="bottom")
                    with row_skip[0]:
                        opt_skip = st.multiselect(
                            "设为不标注（保持原编号）",
                            [f"{c['label']}（未标注）" for c in normal_un],
                            key="skip_sel",
                        )
                    with row_skip[1]:
                        if st.button("🚫 设为不标注", key="btn_skip", use_container_width=True):
                            if not opt_skip:
                                st.warning("请先勾选要设为不标注的编号")
                            else:
                                id_by = {f"{c['label']}（未标注）": c["cluster_id"] for c in normal_un}
                                for o in opt_skip:
                                    set_cluster_skip(id_by[o], True)
                                st.success(f"已将 {len(opt_skip)} 个编号设为不标注（保持原编号）。")
                                st.rerun()
                    row_restore = st.columns([3, 1], vertical_alignment="bottom")
                    with row_restore[0]:
                        opt_restore = st.multiselect(
                            "恢复标注（回到「标注为某人」）",
                            [f"{c['label']}（不标注中）" for c in skipped],
                            key="skip_restore_sel",
                        )
                    with row_restore[1]:
                        if st.button("↩️ 恢复标注", key="btn_skip_restore", use_container_width=True):
                            if not opt_restore:
                                st.warning("请先勾选要恢复的编号")
                            else:
                                id_by2 = {f"{c['label']}（不标注中）": c["cluster_id"] for c in skipped}
                                for o in opt_restore:
                                    set_cluster_skip(id_by2[o], False)
                                st.success(f"已恢复 {len(opt_restore)} 个编号的标注。")
                                st.rerun()

    # ── 人物档案（需求 4）：姓名/性别/出生年/关系/备注 ──
    c = panel("人物档案", "记录每位已标注人物的基本信息与和你的关系")
    with c:
        persons = list_persons()
        if persons:
            p_df = pd.DataFrame(persons)
            view = p_df[["person_name", "gender", "birth_year", "relation", "note", "has_voiceprint"]].copy()
            view["has_voiceprint"] = view["has_voiceprint"].map({1: "是", 0: "否"})
            view.columns = ["姓名", "性别", "出生年", "与我的关系", "备注", "是否已标注声纹"]
            st.dataframe(view, width='stretch', hide_index=True)
        else:
            st.markdown(
                "<div style='font-size:0.95rem;color:var(--fg-2);'>"
                "暂无档案。在上方「声纹簇·标注学习」标注一个人后会自动建档，再回来补充资料。"
                "</div>",
                unsafe_allow_html=True,
            )
        with st.expander("新增 / 编辑人物资料"):
            persons_now = list_persons()
            names = [p["person_name"] for p in persons_now]
            mode = st.radio("操作", ["编辑已有", "新建人物"], horizontal=True, key="person_mode")
            if mode == "编辑已有" and names:
                pname = st.selectbox("姓名", names, key="edit_pname")
                cur = next((p for p in persons_now if p["person_name"] == pname), {})
            else:
                pname = st.text_input("姓名（唯一，不含空格）", key="new_pname2")
                cur = {}
            c1, c2 = st.columns(2)
            with c1:
                gender = st.selectbox("性别", ["", "男", "女", "其他"],
                                      index=["", "男", "女", "其他"].index(cur.get("gender") or ""),
                                      key="p_gender")
                birth_year = st.text_input("出生年", value=str(cur.get("birth_year") or ""),
                                           key="p_birth")
            with c2:
                relation = st.text_input("与我的关系", value=cur.get("relation") or "",
                                         key="p_relation")
            note = st.text_area("备注", value=cur.get("note") or "", key="p_note")
            if st.button("保存资料", key="btn_save_person"):
                if not pname.strip():
                    st.warning("请填写姓名")
                elif " " in pname.strip():
                    st.warning("姓名不能含空格")
                else:
                    by = birth_year.strip()
                    by_val = int(by) if by.isdigit() else None
                    upsert_person(pname.strip(), gender or None, by_val,
                                  relation.strip() or None, note.strip() or None)
                    st.success(f"已保存 {pname.strip()} 的资料")
                    st.rerun()

    c = panel("数据库怎么组织的", "四张业务表 + 全文索引")
    with c:
        st.markdown(
            f"<div class='path-block'>"
            f"SQLite 数据库 <code>{DB_PATH}</code>（{get_db_size()}），包含：<br><br>"
            f"• <strong>transcripts</strong> — 每行一条转录片段：谁说的、什么时间（绝对时间戳）、说了什么<br>"
            f"• <strong>voiceprints</strong> — 已录入的命名声纹：姓名 + 声纹向量<br>"
            f"• <strong>speaker_clusters</strong> — 声纹簇：unknown 全局编号 + 聚合向量 + 标注姓名，支撑持续学习<br>"
            f"• <strong>persons</strong> — 人物档案：姓名（唯一）+ 性别 + 出生年 + 关系 + 备注<br>"
            f"• <strong>transcripts_fts / transcripts_fts2</strong> — 全文索引（fts2 为 jieba 中文分词索引）"
            f"</div>",
            unsafe_allow_html=True,
        )
        with st.expander("查看建表语句"):
            parts = []
            for s in get_db_schema():
                parts.append(
                    f"<div class='schema-item'>"
                    f"<div class='schema-name'>{html.escape(s['name'])}</div>"
                    f"<pre class='schema-pre'>{html.escape(s['sql'] or '')}</pre>"
                    f"</div>"
                )
            st.markdown("".join(parts), unsafe_allow_html=True)
        with st.expander("查看示例数据（最近 3 条）"):
            for r in get_recent_records(3):
                st.json(r)

# ================================================================
# 页 4 — 搜索 · 文件
# ================================================================
elif page == "搜索 · 文件":
    init_fts()  # 首次自动重建中文分词索引（已有则秒回）
    c = panel("搜索转录文本", "全文检索 + 说话人 + 时间范围")
    with c:
        kw = st.text_input("关键词", placeholder="输入后按回车或点搜索…")
        c1, c2 = st.columns([1, 2])
        with c1:
            sp_map4_filter = speaker_display_map()
            raw_speakers4 = get_speaker_list()
            display_to_raws4: dict[str, list[str]] = {}
            seen4: set[str] = set()
            display_speakers4: list[str] = []
            for s in raw_speakers4:
                d = disp_speaker(s, sp_map4_filter)
                display_to_raws4.setdefault(d, []).append(s)
                if d not in seen4:
                    seen4.add(d)
                    display_speakers4.append(d)
            sp4_display = st.selectbox("说话人", ["全部"] + display_speakers4, key="sp4")
            sp4_list = display_to_raws4.get(sp4_display) if sp4_display and sp4_display != "全部" else None
        with c2:
            dr4 = st.date_input("时间范围", value=(
                datetime.now() - timedelta(days=30),
                datetime.now() + timedelta(days=1)), key="dr4")
        if st.button("搜索", type="primary"):
            st.session_state["did_search"] = True
        date_from = dr4[0].isoformat() if isinstance(dr4, tuple) else ""
        date_to = dr4[1].isoformat() if isinstance(dr4, tuple) else ""
        if kw.strip() or st.session_state.get("did_search"):
            results = search_records(keyword=kw, speakers=sp4_list,
                                     date_from=date_from, date_to=date_to)
            if not results:
                st.info("没有匹配的记录，试试调整关键词或时间范围")
            else:
                st.markdown(
                    f"<div style='font-size:0.9rem;color:var(--fg-2);margin:8px 0;'>"
                    f"共 <strong style='color:var(--fg-0);'>{len(results)}</strong> 条结果</div>",
                    unsafe_allow_html=True,
                )
                items = []
                sp_map4 = speaker_display_map()
                for r in results[:50]:
                    who = disp_speaker(str(r['speaker']), sp_map4)
                    items.append(
                        f"<div class='list-item'>"
                        f"<span class='f'>{html.escape(who)}</span>"
                        f"<span class='m'>　{r['absolute_start_time'][:19].replace('T', ' ')}"
                        f"　·　{html.escape(str(r['source_file']))}</span>"
                        f"<div class='x'>{clean_text(r['text'], 200)}</div>"
                        f"</div>"
                    )
                st.markdown("".join(items), unsafe_allow_html=True)

                st.markdown(
                    "<div style='font-size:0.85rem;color:var(--fg-3);margin:14px 0 4px 0;'>"
                    "回放某条结果对应的音频片段：</div>",
                    unsafe_allow_html=True,
                )
                idx = st.selectbox(
                    "选择结果",
                    range(min(len(results), 50)),
                    format_func=lambda i: (
                        f"#{results[i]['id']} · {results[i]['speaker']} · "
                        f"{results[i]['absolute_start_time'][:19].replace('T', ' ')}"
                    ),
                    label_visibility="collapsed",
                )
                render_segment_audio(results[idx])

    c = panel("浏览归档文件", "按月份分组的文本备份与归档音频")
    with c:
        browse = st.radio("类型", ["文本备份", "归档音频"], horizontal=True)
        target = ARCHIVE_TEXT_DIR if browse == "文本备份" else ARCHIVE_AUDIO_DIR
        files = list_archive_files(target)
        if not files:
            st.info("暂无文件")
        else:
            months = sorted({f["month"] for f in files}, reverse=True)
            groups = []
            for month in months:
                mfiles = [f for f in files if f["month"] == month]
                rows = []
                for f in mfiles:
                    size_kb = f["size"] / 1024
                    rows.append(
                        f"<div class='archive-file'>"
                        f"<code>{html.escape(f['name'])}</code>　{size_kb:.0f} KB"
                        f"</div>"
                    )
                groups.append(
                    f"<details class='archive-month'>"
                    f"<summary>{month}（{len(mfiles)} 个文件）</summary>"
                    + "".join(rows) + "</details>"
                )
            st.markdown("".join(groups), unsafe_allow_html=True)

            if browse == "文本备份":
                txt_files = [f for f in files if f["suffix"] == ".txt"]
                if txt_files:
                    st.markdown(
                        "<div style='font-size:0.85rem;color:var(--fg-3);margin:14px 0 4px 0;'>"
                        "选择文件预览全文（预览区域限高滚动，不会撑开页面）：</div>",
                        unsafe_allow_html=True,
                    )
                    sel = st.selectbox(
                        "预览文件",
                        range(len(txt_files)),
                        format_func=lambda i: f"{txt_files[i]['month']} / {txt_files[i]['name']}",
                        label_visibility="collapsed",
                    )
                    try:
                        content = Path(txt_files[sel]["path"]).read_text(encoding="utf-8")
                        st.markdown(
                            f"<div class='preview-box'>{html.escape(content)}</div>",
                            unsafe_allow_html=True,
                        )
                    except Exception:
                        st.warning("无法读取该文件")

# ── 页脚（版本时间戳：确认 ThinkPad 上部署的是否为最新代码） ──
st.markdown(
    f"<div class='footer-note'>ASR WebUI · KVI 视觉风格 · {UI_VERSION}</div>",
    unsafe_allow_html=True,
)
