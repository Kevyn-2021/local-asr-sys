"""
Streamlit Web Dashboard — PRD FR-008
5 个页面：状态概览 / 处理记录 / 数据库 / 文件归档 / 访问控制

UI v2.0 — KVI 视觉风格重构：
- 导航：st.segmented_control 分段控件，每个页签是独立区块，不再依赖脆弱的 CSS 覆盖
- 顶部锁定导航条（v2.41 定稿 / v2.62 调整）：页首单行（Local ASR System 品牌）与 5 个页签同排，
  整条吸顶，滚动时始终可见；页签宽度随文字自适应、窄窗口自动折行；
  北京时间移到首页「北京时间」面板（导航条下方第一个面板）
- 布局：st.container(border=True) 面板，面板头部 = 标题 + 分隔线，区块边界明确
- 排版：代码块行高锁定 1.5；搜索/文件浏览上下堆叠；文本预览限高滚动
- 色彩：灰阶为基（85%），暖赭 #b86a48 作唯一强调色（5%）

启动方式:
  streamlit run scripts/webui.py --server.address 0.0.0.0 --server.port 8501
"""
from __future__ import annotations

import html
import ipaddress
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
    VOICEPRINT_CONFIG,
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

UI_VERSION = "2026-08-07-13:35:07"

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
    display: flex; flex-direction: row; align-items: baseline;
    padding-left: 0.5rem;   /* 品牌块整体右移，避免贴着左边界（v2.40） */
    white-space: nowrap;     /* 单行布局：仅品牌名（北京时间 v2.61 起移到首页面板） */
}
.topbar-brand {
    font-size: 1.05rem; font-weight: 600; color: var(--fg-0);  /* 与面板标题字号一致（v2.41） */
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

/* ── 分段导航（5 个页签，文字自适应宽度，v2.62）──
   Streamlit 1.60 渲染为 div[role="radiogroup"] + button（stSegmentedControl label 结构已不存在）；
   老版本 stSegmentedControl label 规则保留在下方 */
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) div[role="radiogroup"] {
    margin-left: 0.75rem;   /* 导航整体往右一点点（v2.41） */
    display: flex; flex-wrap: wrap;   /* v2.62：文字自适应宽度；窄窗口自动折行 */
}
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) div[role="radiogroup"] button {
    cursor: pointer; flex: 0 0 auto; min-width: 0;   /* v2.62：宽度随文字内容，不拉伸等宽 */
    padding-left: 1.5rem !important;
    padding-right: 1.5rem !important;   /* v2.63：页签整体加宽 */
    letter-spacing: 0.05em;   /* v2.64：tab 内部文字字符间距（页签之间保持无缝，不设 gap） */
}
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) div[role="radiogroup"] button[aria-checked="true"],
.block-container > div[data-testid="stVerticalBlock"] > div[data-testid="stLayoutWrapper"]:has(.topbar-title) div[role="radiogroup"] button[aria-checked="true"] * {
    background: transparent !important;   /* 选中态只保留加粗红色文字，不加背景（v2.40） */
    color: var(--accent) !important;
    font-weight: 600;
}
div[data-testid="stSegmentedControl"] label {
    min-width: 0;
}

/* 北京时间面板（v2.61：首页导航条下方第一个面板） */
.clock-line { font-size: 1.55rem; font-weight: 600; color: var(--fg-0); letter-spacing: 0.02em; }
.clock-tz { font-size: 0.85rem; font-weight: 400; color: var(--fg-3); }

/* 访问控制页 · 端口说明表格 */
.port-table { width: 100%; border-collapse: collapse; }
.port-table th, .port-table td {
    text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line-subtle);
    font-size: 0.92rem; vertical-align: top;
}
.port-table th { color: var(--fg-2); font-weight: 600; }
.port-table code { background: var(--bg-subtle); padding: 1px 6px; border-radius: 4px; }

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


def get_audio_records() -> list[dict]:
    """按音频（file_hash 去重）聚合的处理记录（v2.67「音频处理记录」）。
    按源音频开始时间从远到近排序（显示层编号 1 = 最远）。
    旧记录（v2.67 前入库，无起止时间字段）：完成时间回退 processed_at，开始时间显示 —。"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT file_hash, source_file, archive_name, "
            "MIN(recording_start_time) AS recording_start_time, "
            "MAX(audio_duration) AS audio_duration, "
            "MIN(processed_at) AS processed_at, "
            "MIN(COALESCE(processing_started_at, '')) AS processing_started_at, "
            "MAX(COALESCE(processing_completed_at, processed_at)) AS processing_completed_at, "
            "MAX(audio_path) AS audio_path "
            "FROM transcripts GROUP BY file_hash "
            "ORDER BY MIN(recording_start_time) ASC").fetchall()
    return [dict(r) for r in rows]


def get_audio_segments(file_hash: str) -> list[dict]:
    """某音频（file_hash）的全部转写片段，按绝对开始时间升序（v2.67 详情页）"""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, absolute_start_time, absolute_end_time, speaker, text "
            "FROM transcripts WHERE file_hash=? ORDER BY absolute_start_time ASC",
            (file_hash,)).fetchall()
    return [dict(r) for r in rows]


def get_voiceprint_dashboard() -> dict:
    """数据库页底部「声纹匹配 · 学习看板」数据（只读统计，v2.80 按人展示）。
    每个已标注人员 = 姓名 + 其全部声纹簇 label，聚合这些片段按 speaker_match_score 分档。"""
    with connect() as conn:
        sp_scores = [(str(r["speaker"]), r["speaker_match_score"]) for r in conn.execute(
            "SELECT speaker, speaker_match_score FROM transcripts")]
        sp_utt = {str(r["speaker"]): int(r["c"]) for r in conn.execute(
            "SELECT speaker, COUNT(*) AS c FROM transcripts GROUP BY speaker")}
    clusters = list_clusters_view()
    named: dict[str, list[dict]] = {}
    unknown = skip = 0
    for cl in clusters:
        if cl.get("assigned_name"):
            named.setdefault(str(cl["assigned_name"]), []).append(cl)
        elif cl.get("skip_label"):
            skip += 1
        else:
            unknown += 1

    label_sets: dict[str, set[str]] = {}
    for name, cls in named.items():
        s = label_sets.setdefault(name, set())
        s.add(str(name))
        for c in cls:
            s.add(str(c["label"]))

    BAND_HIGH = "高置信(≥0.75)"
    BAND_MATCH = "认名未学习(0.65–0.75)"
    BAND_SUSPECT = "疑似(0.50–0.65)"
    BAND_LOW = "未识别(<0.50)"
    BAND_NONE = "无得分"

    def band(score):
        if score is None:
            return BAND_NONE
        if score >= 0.75:
            return BAND_HIGH
        if score >= 0.65:
            return BAND_MATCH
        if score >= 0.50:
            return BAND_SUSPECT
        return BAND_LOW

    persons = []
    for name, cls in sorted(named.items()):
        counts = {BAND_HIGH: 0, BAND_MATCH: 0, BAND_SUSPECT: 0, BAND_LOW: 0, BAND_NONE: 0}
        spks = label_sets[name]
        for spk, sc in sp_scores:
            if spk in spks:
                counts[band(sc)] += 1
        persons.append({
            "姓名": name,
            **counts,
            "合计": sum(counts.values()),
            "待重置簇": sum(1 for c in cls if c.get("reset_on_next_match")),
        })

    total = {BAND_HIGH: 0, BAND_MATCH: 0, BAND_SUSPECT: 0, BAND_LOW: 0, BAND_NONE: 0}
    for _spk, sc in sp_scores:
        total[band(sc)] += 1

    def cluster_utts(cl: dict) -> int:
        n = sp_utt.get(str(cl["label"]), 0)
        if cl.get("assigned_name"):
            n += sp_utt.get(str(cl["assigned_name"]), 0)
        return n

    return {
        "persons": persons,
        "total": total,
        "named_persons": len(named),
        "unknown": unknown,
        "skip": skip,
        "no_sample": sum(1 for c in clusters if cluster_utts(c) == 0),
        "pending_reset": sum(1 for c in clusters if c.get("reset_on_next_match")),
    }


def get_speaker_utterances(speakers: list[str], limit: int = 100) -> list[dict]:
    """某说话人（一个或多个原始标签）的发言，按绝对时间倒序（v2.68 标注学习用）"""
    if not speakers:
        return []
    with connect() as conn:
        ph = ",".join("?" * len(speakers))
        rows = conn.execute(
            f"SELECT id, source_file, absolute_start_time, absolute_end_time, "
            f"speaker, text, segment_start_offset, segment_end_offset, audio_path "
            f"FROM transcripts WHERE speaker IN ({ph}) "
            f"ORDER BY absolute_start_time DESC LIMIT ?",
            (*speakers, limit)).fetchall()
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
    """是否有手动处理任务正在进行（锁文件存在且持有 PID 存活；v2.84 起进程已死即视为陈旧）"""
    try:
        if not INBOX_LOCK.exists():
            return False
        return not _lock_is_stale(INBOX_LOCK)
    except Exception:
        return False


def _lock_is_stale(path: Path) -> bool:
    """锁文件陈旧判定：超过 6 小时，或持有 PID 已不存在（崩溃/SIGKILL 等）即视为陈旧。
    v2.84：PID 存活且确为 process_inbox 进程才算有效锁，否则可立即接管/删除。"""
    try:
        if not path.exists():
            return False
        if (time.time() - path.stat().st_mtime) >= 6 * 3600:
            return True
        pid_txt = path.read_text(encoding="utf-8").strip()
        if not pid_txt.isdigit():
            return True
        with open(f"/proc/{pid_txt}/cmdline", "rb") as f:
            cmd = f.read().decode("utf-8", errors="ignore")
        return "process_inbox.py" not in cmd
    except FileNotFoundError:
        return True  # 持有进程已不存在 → 陈旧
    except Exception:
        return False  # /proc 不可读等异常 → 保守视为运行中


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

    # 2. 解锁：只有"非处理中"时才能删锁（PID 存活的新鲜锁视为任务运行中，不碰；
    #    v2.84 起持有进程已死的锁视为陈旧，无需等 6 小时即可删除）
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


def requeue_failed_files() -> tuple[int, list[str]]:
    """把 error/ 根目录当前批次的失败音频移回收件箱根目录（v2.84）。
    返回 (成功数, 失败明细)；.error.txt 日志留在 error/，由下轮归档。"""
    moved = 0
    failed = []
    if not INBOX_ERROR_DIR.exists():
        return 0, failed
    for p in sorted(INBOX_ERROR_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        dest = INBOX_DIR / p.name
        try:
            if dest.exists():
                dest = INBOX_DIR / f"{p.stem}-{datetime.now().strftime('%Y%m%d_%H%M%S')}{p.suffix}"
            p.rename(dest)
            moved += 1
        except Exception as e:
            failed.append(f"{p.name}: {e}")
    return moved, failed


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


def fmt_dt_no_sec(iso: str | None) -> str:
    """`YYYY-MM-DD HH:MM`（不含秒），供「音频处理记录」时间列使用（v2.67）"""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso[:16]


def _audio_end_time(start_iso: str | None, duration_s: float | None) -> str:
    """源音频结束时间 = 录音开始时间 + 音频总时长（v2.67）"""
    if not start_iso or duration_s is None:
        return ""
    try:
        return (datetime.fromisoformat(start_iso) + timedelta(seconds=duration_s)).isoformat()
    except Exception:
        return ""


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


def render_full_audio(a: dict):
    """整段音频回放（v2.67 详情页）：不按片段切分，用户按文本时间戳自行拖动"""
    p = a.get("audio_path")
    if p and Path(p).exists():
        try:
            st.audio(str(p))
        except Exception:
            st.warning("无法加载音频")
    else:
        st.info("该音频无对应文件")


# ========== 访问控制辅助 ==========

FW_HELPER = "/usr/local/sbin/asr-webui-fw.sh"


def _valid_ipv4(text: str) -> bool:
    try:
        ipaddress.IPv4Address(text)
        return True
    except ValueError:
        return False


def _fw_list():
    """返回 8501 端口白名单规则列表；helper 不可用时返回 None。"""
    try:
        out = subprocess.run(
            ["sudo", "-n", FW_HELPER, "list"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    rules = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        rules.append({"ip": parts[0], "comment": parts[1], "fixed": parts[2] == "1"})
    return rules


def _fw_apply(action: str, ip: str, comment: str = ""):
    """调用特权 helper 增/删白名单（可带描述），返回 (ok, message)。"""
    try:
        args = ["sudo", "-n", FW_HELPER, action, ip]
        if comment:
            args.append(comment[:48])
        out = subprocess.run(
            args,
            capture_output=True, text=True, timeout=15,
        )
        return out.returncode == 0, (out.stdout or out.stderr).strip()
    except Exception as exc:
        return False, f"执行失败：{exc}"


def render_access_control():
    """页 5「访问控制」：IP 白名单管理 + 端口说明（PRD FR-008-A，v2.61）。"""
    c = panel("IP 白名单 · 网页访问", "端口 8501 仅放行以下来源；Tailscale 设备 IP 固定放行、不可删除")
    with c:
        rules = _fw_list()
        if rules is None:
            st.warning("无法读取防火墙白名单：ThinkPad 未安装管理脚本（/usr/local/sbin/asr-webui-fw.sh + "
                       "sudoers NOPASSWD）。详见 TDD v2.61「访问控制」；安装后刷新本页即可。")
        else:
            if not rules:
                st.info("当前白名单为空（仅 Tailscale 设备可访问）。")
            else:
                # 固定放行项置顶、设备白名单（可新增/移除）置底（v2.63）
                for group_label, group_rules in (
                    ("固定放行（不可删除）", [r for r in rules if r["fixed"]]),
                    ("设备白名单（可新增/移除）", [r for r in rules if not r["fixed"]]),
                ):
                    if not group_rules:
                        continue
                    st.markdown(f"**{group_label}**")
                    for r in group_rules:
                        col_ip, col_desc, col_op = st.columns([1.2, 3.2, 1], vertical_alignment="center")
                        with col_ip:
                            st.markdown(f"<code>{html.escape(r['ip'])}</code>", unsafe_allow_html=True)
                        with col_desc:
                            st.markdown(html.escape(r["comment"]) or "—")
                        with col_op:
                            if r["fixed"]:
                                # v2.71：与「移除」按钮同宽居中，避免纵向错位（按钮偏向右侧）
                                st.markdown(
                                    "<div style='text-align:center;color:var(--fg-3);font-size:0.8rem;'>固定</div>",
                                    unsafe_allow_html=True,
                                )
                            elif st.button("移除", key=f"fw_rm_{r['ip']}", use_container_width=True):
                                ok, msg = _fw_apply("remove", r["ip"])
                                if ok:
                                    st.success(msg)
                                else:
                                    st.error(msg)
                                st.rerun()
                    st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)
            st.divider()
            st.markdown("**新增白名单 IP**")
            col_ip, col_desc, col_btn = st.columns([1.4, 2.2, 1], vertical_alignment="center")
            new_ip = col_ip.text_input("IP", label_visibility="collapsed",
                                       placeholder="例如 192.168.3.20", key="fw_new_ip")
            new_desc = col_desc.text_input("描述", label_visibility="collapsed",
                                           placeholder="描述（可选，≤48 字符）", key="fw_new_desc")
            if col_btn.button("添加", type="primary", use_container_width=True, key="fw_add_btn"):
                ip = new_ip.strip()
                if not _valid_ipv4(ip):
                    st.error("请输入合法的 IPv4 地址")
                else:
                    ok, msg = _fw_apply("add", ip, new_desc.strip())
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)
                    st.rerun()
        st.caption("提示：常用设备接入 Tailscale 后无需加白名单；若误删当前设备 IP 导致无法打开本页，"
                   "请用 SSH 登录 ThinkPad 或改走 Tailscale 恢复。")

    c = panel("端口说明", "ThinkPad 对外服务的端口与用途")
    with c:
        st.markdown(
            "<table class='port-table'>"
            "<thead><tr><th>端口</th><th>服务</th><th>用途</th></tr></thead>"
            "<tbody>"
            "<tr><td><code>22/tcp</code></td><td>SSH</td>"
            "<td>开发机（MacBook）远程管理 ThinkPad、部署代码："
            "<code>ssh kevin@&lt;ThinkPad当前IP&gt;</code></td></tr>"
            "<tr><td><code>8501/tcp</code></td><td>Web UI</td>"
            "<td>浏览器访问本管理界面：<code>http://&lt;ThinkPad当前IP&gt;:8501</code>，"
            "或 Tailscale 地址 <code>http://&lt;ThinkPad-Tailscale-IP&gt;:8501</code></td></tr>"
            "</tbody></table>",
            unsafe_allow_html=True,
        )


# ========== 页面 ==========

NAV_OPTIONS = ["状态概览", "处理记录", "数据库", "文件归档", "访问控制"]

# ── 顶部锁定导航条：品牌 + 5 个等宽页签同排，整条吸顶（CSS） ──
#   北京时间 v2.61 起移到首页「北京时间」面板，顶栏只保留品牌名
col_brand, col_nav = st.columns([1.0, 2.4], gap="medium")
with col_brand:
    st.markdown(
        f"<div class='topbar-title'>"
        f"<span class='topbar-brand'>Local ASR System</span>"
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
# 页 1 — 状态概览
# ================================================================
if page == "状态概览":
    state, st_dict = derive_state()

    # 处理中或刚失败时 15 秒自动刷新，平时 10 分钟（v2.18 由 5 秒放宽，避免过于频繁）
    try:
        from streamlit_autorefresh import st_autorefresh
        busy_now = (state in ("processing", "failed")) or inbox_processing()
        st_autorefresh(interval=15_000 if busy_now else 600_000, key="overview_refresh")
    except ImportError:
        pass

    # 北京时间面板：导航条下方第一个面板（v2.61 起从顶栏移入，仅首页展示）
    c = panel("北京时间", "系统时钟 · Asia/Shanghai (UTC+8)")
    with c:
        now = datetime.now()
        st.markdown(
            f"<div class='clock-line'>{now.strftime('%Y-%m-%d %H:%M:%S')}　"
            f"<span class='clock-tz'>北京时间</span></div>",
            unsafe_allow_html=True,
        )

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
        # v2.84：当前批次失败音频一键重新入队并处理（替代手动 SSH 移动）
        err_audio = []
        if INBOX_ERROR_DIR.exists():
            err_audio = [p for p in sorted(INBOX_ERROR_DIR.iterdir())
                         if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
        if err_audio:
            names = "、".join(html.escape(p.name) for p in err_audio[:3])
            if len(err_audio) > 3:
                names += f" 等 {len(err_audio)} 个"
            st.markdown(
                f"<div class='inbox-empty'>error/ 中有失败音频：{names}。"
                "排查后可一键重新入队处理。</div>", unsafe_allow_html=True)
            if st.button("↩️ 失败文件重新入队并处理", key="btn_requeue",
                         use_container_width=True, disabled=busy):
                moved, failed = requeue_failed_files()
                if moved:
                    st.success(f"已移回 {moved} 个失败文件到收件箱，正在启动处理…")
                    ok, msg = trigger_process_inbox()
                    if not ok:
                        st.warning(msg)
                    time.sleep(1.2)
                else:
                    st.warning("没有可重新入队的失败文件"
                               + (f"（{failed[0]}）" if failed else ""))
                st.rerun()

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
    audios = get_audio_records()    # 按音频聚合，按源音频时间从远到近

    c = panel("音频处理记录", "按处理过的音频罗列；编号按源音频时间从远到近（最远 = 1）")
    with c:
        if not audios:
            st.info("暂无处理记录")
        else:
            rows = []
            for i, a in enumerate(audios, 1):
                end_iso = _audio_end_time(a["recording_start_time"], a.get("audio_duration"))
                rows.append({
                    "编号": i,
                    "归档音频": a["archive_name"] or a["source_file"],
                    "时长(min)": f"{a['audio_duration'] / 60:.1f}" if a.get("audio_duration") else "—",
                    "源音频开始时间": fmt_dt_no_sec(a["recording_start_time"]),
                    "源音频结束时间": fmt_dt_no_sec(end_iso),
                    "开始处理时间": fmt_dt_no_sec(a["processing_started_at"]),
                    "处理完成时间": fmt_dt_no_sec(a["processing_completed_at"]),
                })
            # v2.70：编号语义不变（1 = 最远），表格倒序显示（最大编号在最上方）
            st.dataframe(pd.DataFrame(list(reversed(rows))), width="stretch", hide_index=True)

    c = panel("音频处理详情", "选一个音频编号，查看该音频的完整转写文本并回放；已标注的直接显示姓名")
    with c:
        if not audios:
            st.info("暂无处理记录")
        else:
            options = {i: a for i, a in enumerate(audios, 1)}
            sel = st.selectbox(
                "选择音频编号",
                list(options),
                format_func=lambda i: f"{i} — {options[i]['archive_name'] or options[i]['source_file']}",
            )
            a = options[sel]
            end_iso = _audio_end_time(a["recording_start_time"], a.get("audio_duration"))
            st.markdown(
                f"<div style='font-size:0.95rem;'><strong>{html.escape(str(a['archive_name'] or a['source_file']))}</strong>"
                f"<span style='color:var(--fg-2);font-size:0.85rem;margin-left:12px;'>"
                f"{fmt_dt_no_sec(a['recording_start_time'])} → {fmt_dt_no_sec(end_iso)}</span></div>",
                unsafe_allow_html=True,
            )
            render_full_audio(a)
            segs = get_audio_segments(a["file_hash"])
            if not segs:
                st.info("该音频暂无转写片段")
            else:
                lines = []
                for s in segs:
                    who = disp_speaker(str(s["speaker"]), sp_map)
                    lines.append(
                        f"<div class='seg-line'>"
                        f"<span style='color:var(--fg-2);'>{fmt_full_time(s['absolute_start_time'])}"
                        f" - {fmt_full_time(s['absolute_end_time'])}</span> "
                        f"<strong>{html.escape(who)}</strong>：{clean_text(s['text'])}</div>"
                    )
                st.markdown(
                    "<div style='margin-top:10px;padding:12px 16px;background:var(--bg-subtle);"
                    "border-radius:6px;font-size:0.95rem;line-height:1.9;"
                    "max-height:540px;overflow-y:auto;'>" + "".join(lines) + "</div>",
                    unsafe_allow_html=True,
                )

# ================================================================
# 页 3 — 数据库
# ================================================================
elif page == "数据库":
    c = panel("声纹怎么来的", "不需要专门录入——处理音频时自动抓取，你负责标注")
    with c:
        st.markdown(
            "<div style='font-size:0.95rem;color:var(--fg-2);line-height:1.8;'>"
            "这套系统<strong>不需要单独录入声纹</strong>。每次处理音频时，"
            "系统会自动从声音片段里抓取每个说话人的声纹，记为一个 <code>unknown_XXXX</code> 编号（见下方「声纹簇·标注学习」）。"
            "你只要把认出的人<strong>标注上姓名</strong>，系统就会把这个声纹和姓名关联起来，"
            "并在后续每次出现时不断学习、越认越准。"
            "也就是说：<strong>你标注 → 系统学习 → 下次自动认出</strong>，如此循环配合。"
            "</div>",
            unsafe_allow_html=True,
        )


    # ── 声纹簇标注（v2.68 重构）：筛选说话人 → 查看发言 → 直接标注 ──
    # 原「处理记录」页的说话人筛选功能并入此处；替代旧的三段式 tab（ID/编号/样本数对用户无意义）。
    c = panel("声纹簇 · 标注学习", "筛选说话人 → 查看他说过的话 → 直接标注")
    with c:
        sp_map = speaker_display_map()
        clusters = list_clusters_view()
        persons = list_persons()
        known_names = [p["person_name"] for p in persons]

        # 说话人选项（v2.78 优化）：
        #  - 已标注人员按姓名合并为一行（一个姓名可能对应多个声纹簇，不再重复出现多行）
        #  - 无发言样本的簇（无法试听确认）默认隐藏，可勾选显示
        #  - 排序：未标注 unknown（按编号）→ 已标注姓名（按姓名）→ 不标注（按编号）
        with connect() as conn:
            sp_utt = {str(r["speaker"]): int(r["c"]) for r in conn.execute(
                "SELECT speaker, COUNT(*) AS c FROM transcripts GROUP BY speaker")}

        def _cluster_utts(cl: dict) -> int:
            n = sp_utt.get(str(cl["label"]), 0)
            if cl.get("assigned_name"):
                n += sp_utt.get(str(cl["assigned_name"]), 0)
            return n

        unknown_opts: list[dict] = []
        named_groups: dict[str, list[dict]] = {}
        skip_opts: list[dict] = []
        seen_labels: set[str] = set()
        for cl in clusters:
            label = str(cl["label"])
            if label in seen_labels:
                continue
            seen_labels.add(label)
            name = cl.get("assigned_name")
            if name:
                named_groups.setdefault(str(name), []).append(cl)
            elif cl.get("skip_label"):
                skip_opts.append({"disp": f"{label} 🚫 不标注", "raws": [label], "clusters": [cl]})
            else:
                unknown_opts.append({"disp": f"{label}（未标注）", "raws": [label], "clusters": [cl]})

        show_empty = st.checkbox(
            "显示无发言样本的说话人", value=False,
            help="无发言样本的簇无法试听确认，默认隐藏；勾选后可一并显示（供清理/改名等操作）",
            key="clu_show_empty")

        def _keep(opt: dict) -> bool:
            return show_empty or any(_cluster_utts(c) > 0 for c in opt["clusters"])

        named_opts = [
            {"disp": f"{name} 已标注",
             "raws": [str(name)] + [str(c["label"]) for c in cls],
             "clusters": cls}
            for name, cls in sorted(named_groups.items(), key=lambda kv: kv[0])
        ]
        options = [o for o in unknown_opts if _keep(o)] \
                + [o for o in named_opts if _keep(o)] \
                + [o for o in skip_opts if _keep(o)]
        hidden_n = sum(1 for o in unknown_opts + named_opts + skip_opts if not _keep(o))
        if hidden_n:
            st.caption(f"已隐藏 {hidden_n} 个无发言样本的说话人（勾选上方可显示）")

        # 无簇的已命名说话人（声纹库匹配，v2.68 起保留）
        seen_names = {str(c["label"]) for c in clusters} | {str(n) for n in named_groups}
        for s in get_speaker_list():
            if s in seen_names:
                continue
            seen_names.add(s)
            options.append({"disp": f"{disp_speaker(s, sp_map)}（声纹库命名）",
                            "raws": [s], "clusters": []})

        if not options:
            if clusters and not show_empty:
                st.info("所有说话人都没有发言样本（已隐藏）。勾选「显示无发言样本的说话人」可查看。")
            else:
                st.markdown(
                    "<div style='font-size:0.95rem;color:var(--fg-2);'>"
                    "还没有任何说话人。处理音频后，未识别说话人会以 "
                    "<code>unknown_0001</code>、<code>unknown_0002</code>… 的形式出现在这里。"
                    "</div>",
                    unsafe_allow_html=True,
                )
        else:
            opt_disps = [o["disp"] for o in options]
            sel_disp = st.selectbox("说话人", opt_disps, key="clu_sp_filter")
            sel = options[opt_disps.index(sel_disp)]

            # ── 该说话人的发言 ──
            utts = get_speaker_utterances(sel["raws"], limit=100)
            st.markdown(
                f"<div style='font-size:0.85rem;color:var(--fg-2);margin:2px 0 6px 0;'>"
                f"该说话人共 {len(utts)} 条发言（最新 100 条）</div>",
                unsafe_allow_html=True,
            )
            if not utts:
                st.info("该说话人在现有转录里没有发言记录")
            else:
                lines = []
                for r in utts:
                    who = disp_speaker(str(r["speaker"]), sp_map)
                    lines.append(
                        f"<div class='seg-line'>"
                        f"<span style='color:var(--fg-2);'>{fmt_full_time(r['absolute_start_time'])}"
                        f" - {fmt_full_time(r['absolute_end_time'])}</span> "
                        f"<span style='color:var(--fg-3);font-size:0.82rem;'>{html.escape(who)}"
                        f" · {html.escape(str(r['source_file']))}</span><br>"
                        f"{clean_text(r['text'])}</div>"
                    )
                st.markdown(
                    "<div style='padding:12px 16px;background:var(--bg-subtle);border-radius:6px;"
                    "font-size:0.95rem;line-height:1.8;max-height:420px;overflow-y:auto;'>"
                    + "".join(lines) + "</div>",
                    unsafe_allow_html=True,
                )

            # ── 试听发言（v2.69）：标注前先听声音确认是谁，不用靠文字猜 ──
            if utts:
                utt_opts = {r["id"]: f"[{fmt_full_time(r['absolute_start_time'])}] {clean_text(r['text'], 24)}"
                            for r in utts}
                sel_utt = st.selectbox(
                    "🎧 试听发言（听声音确认说话人；无需记 ID，按时间与文字预览选即可）",
                    list(utt_opts),
                    format_func=lambda k: utt_opts[k],
                    key="utt_listen",
                )
                row_utt = next(r for r in utts if r["id"] == sel_utt)
                render_segment_audio(row_utt)

            # ── 标注区（命中声纹簇才可标注） ──
            st.divider()
            if not sel["clusters"]:
                st.info("该说话人来自已命名声纹库，无需标注。")
            else:
                cls = sel["clusters"]
                group_note = ""
                if len(cls) > 1:
                    group_note = (f"（对应 {len(cls)} 个声纹簇："
                                  + "、".join(html.escape(str(c["label"])) for c in cls)
                                  + "；标注/改回将作用于全部同名簇）")
                if cls[0].get("assigned_name"):
                    assigned = str(cls[0]["assigned_name"])
                    st.markdown(
                        f"<div style='font-size:0.9rem;'><strong>标注 {html.escape(assigned)}"
                        f"{group_note}</strong></div>",
                        unsafe_allow_html=True,
                    )
                    others = [n for n in known_names if n != assigned]
                    name_choice = st.selectbox(
                        "改标为", ["（输入新姓名）"] + others, key="cal_name_grp")
                    new_name = ""
                    if name_choice == "（输入新姓名）":
                        new_name = st.text_input("新姓名（唯一，不含空格）", key="cal_new_grp")
                    target = new_name.strip() if name_choice == "（输入新姓名）" else name_choice
                    col_a, col_b = st.columns(2)
                    with col_a:
                        if st.button("改标并回填", key="btn_reassign_grp"):
                            if not target or " " in target:
                                st.warning("请输入不含空格的姓名")
                            else:
                                tot_db = tot_txt = 0
                                for cl in cls:
                                    d, t = apply_cluster_label(cl["cluster_id"], target)
                                    tot_db += d
                                    tot_txt += t
                                st.success(f"已把 {assigned} 改标为 {target}（{len(cls)} 个簇），"
                                           f"更新 {tot_db} 条转录、{tot_txt} 个文本备份。")
                                st.rerun()
                    with col_b:
                        if st.button("改回未知", key="btn_unassign_grp"):
                            st.session_state["unassign_grp"] = True
                    if st.session_state.get("unassign_grp"):
                        st.warning(f"确认把「{assigned}」改回未知？将作用于 {len(cls)} 个簇，"
                                   f"并把转录与文本备份中的 {assigned} 改回编号。")
                        if st.button("确认改回", key="btn_unassign_ok_grp"):
                            tot_db = tot_txt = 0
                            for cl in cls:
                                d, t = apply_cluster_label(cl["cluster_id"], None)
                                tot_db += d
                                tot_txt += t
                            st.success(f"已改回未知（{len(cls)} 个簇，"
                                       f"更新 {tot_db} 条转录、{tot_txt} 个文本备份）。")
                            st.session_state.pop("unassign_grp", None)
                            st.rerun()
                else:
                    cl = cls[0]
                    cid = cl["cluster_id"]
                    label = str(cl["label"])
                    skipped = bool(cl.get("skip_label"))
                    st.markdown(
                        f"<div style='font-size:0.9rem;'><strong>标注 {html.escape(label)}</strong></div>",
                        unsafe_allow_html=True,
                    )
                    if skipped:
                        if st.button("↩️ 恢复标注", key=f"btn_unskip_{cid}"):
                            set_cluster_skip(cid, False)
                            st.success(f"已恢复 {label} 的标注入口。")
                            st.rerun()
                    else:
                        name_choice = st.selectbox(
                            "标注为", ["（输入新姓名）"] + known_names, key=f"cal_name_{cid}")
                        new_name = ""
                        if name_choice == "（输入新姓名）":
                            new_name = st.text_input("新姓名（唯一，不含空格）", key=f"cal_new_{cid}")
                        target = new_name.strip() if name_choice == "（输入新姓名）" else name_choice
                        col_a, col_b = st.columns(2)
                        with col_a:
                            if st.button("标注并回填", key=f"btn_assign_{cid}"):
                                if not target or " " in target:
                                    st.warning("请输入不含空格的姓名")
                                else:
                                    n_db, n_txt = apply_cluster_label(cid, target)
                                    st.success(f"已把 {label} 标注为 {target}，系统会持续学习该声纹；"
                                               f"更新 {n_db} 条转录、{n_txt} 个文本备份。")
                                    st.rerun()
                        with col_b:
                            if st.button("🚫 设为不标注", key=f"btn_skip_{cid}"):
                                set_cluster_skip(cid, True)
                                st.success(f"已把 {label} 设为不标注（保持原编号）。")
                                st.rerun()


    # ── 人物档案（需求 4)：姓名/性别/出生年/关系/备注 ──
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

    # ── 声纹匹配 · 学习看板（v2.79 起 / v2.80 按人展示：只读统计，不参与标注流程） ──
    c = panel("声纹匹配 · 学习看板", "按人员展示系统自动认名与学习的只读统计；阈值只决定自动认不认/学不学，不改变标注操作")
    with c:
        ta = float(VOICEPRINT_CONFIG.get("threshold_auto", 0.65))
        tr = float(VOICEPRINT_CONFIG.get("threshold_review", 0.50))
        tl = float(VOICEPRINT_CONFIG.get("learn_threshold", 0.75))
        st.markdown(
            f"<div style='font-size:0.95rem;color:var(--fg-1);margin:2px 0 6px 0;'>"
            f"认名 ≥ {ta} ｜ 疑似 {tr}–{ta} ｜ 学习 ≥ {tl}"
            f"<span style='color:var(--fg-2);font-size:0.82rem;'>（v2.79 解耦：{ta}–{tl} 只认名、不学习，防低置信/低质量样本污染）</span></div>",
            unsafe_allow_html=True,
        )
        dash = get_voiceprint_dashboard()
        if dash["persons"]:
            st.markdown("**按人员 · 片段匹配得分分布**")
            rows = []
            for p in dash["persons"]:
                rows.append({
                    "姓名": p["姓名"],
                    "高置信(≥0.75)": p["高置信(≥0.75)"],
                    "认名未学习(0.65–0.75)": p["认名未学习(0.65–0.75)"],
                    "疑似(0.50–0.65)": p["疑似(0.50–0.65)"],
                    "未识别(<0.50)": p["未识别(<0.50)"],
                    "无得分": p["无得分"],
                    "合计": p["合计"],
                    "待重置簇": p["待重置簇"],
                })
            t = dash["total"]
            rows.append({
                "姓名": "（全部）",
                "高置信(≥0.75)": t["高置信(≥0.75)"],
                "认名未学习(0.65–0.75)": t["认名未学习(0.65–0.75)"],
                "疑似(0.50–0.65)": t["疑似(0.50–0.65)"],
                "未识别(<0.50)": t["未识别(<0.50)"],
                "无得分": t["无得分"],
                "合计": sum(t.values()),
                "待重置簇": 0,
            })
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        else:
            st.caption("暂无已标注人员")
        st.caption(
            f"已标注 {dash['named_persons']} 人 ｜ 未标注 unknown {dash['unknown']} ｜ "
            f"不标注 {dash['skip']} ｜ 无发言样本簇 {dash['no_sample']} ｜ 待重置簇 {dash['pending_reset']}"
        )

# ================================================================
# 页 4 — 文件归档
# ================================================================
elif page == "文件归档":
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

# ================================================================
# 页 5 — 访问控制
# ================================================================
elif page == "访问控制":
    render_access_control()

# ── 页脚（部署时间戳：核对 ThinkPad 部署代码版本；加"部署时间"前缀避免误当当前时间，v2.62） ──
st.markdown(
    f"<div class='footer-note'>ASR WebUI · KVI 视觉风格 · 部署时间 {UI_VERSION}</div>",
    unsafe_allow_html=True,
)
