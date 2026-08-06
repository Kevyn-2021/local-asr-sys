"""
ASR 本地系统全局配置
目录、阈值、时区、说话人匹配阈值、VAD 参数等
所有值可在本文件直接修改，或通过环境变量 ASR_CONFIG_OVERRIDE 指向的 YAML 覆盖
"""
from __future__ import annotations

import os
from pathlib import Path

HOME = Path(os.path.expanduser("~"))

# ---------- 路径 ----------
PROJ_ROOT            = Path(os.environ.get("ASR_PROJ_ROOT",            HOME / "asr-local"))
INBOX_DIR            = Path(os.environ.get("ASR_INBOX",                 HOME / "audio_inbox"))
INBOX_ERROR_DIR      = INBOX_DIR / "error"
ARCHIVE_DIR          = Path(os.environ.get("ASR_ARCHIVE",               HOME / "audio_archive"))
ARCHIVE_AUDIO_DIR    = ARCHIVE_DIR / "processed_audio"
ARCHIVE_TEXT_DIR     = ARCHIVE_DIR / "text_backups"
DB_PATH              = ARCHIVE_DIR / "transcripts.db"
LOG_PATH             = ARCHIVE_DIR / "pipeline.log"
MODELS_DIR      = Path(os.environ.get("HF_HOME",                   PROJ_ROOT / "model_cache"))
SAMPLE_AUDIO_DIR     = PROJ_ROOT / "sample_audio"

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".webm"}

# ---------- 时间戳 ----------
# 统一使用北京时间，不跟随系统
TIMEZONE             = "Asia/Shanghai"

# 时间来源优先级（文件名优先，用户文件命名含精确时间戳，不受拷贝影响）
TIME_SOURCE_PRIORITY = ["filename", "file_birthtime"]
TIME_SOURCE_MISMATCH_THRESHOLD_SECONDS = 300   # 5 分钟
# 文件名时间提取正则（按列表顺序尝试；re.search 不锚定，时间前后可带任意前缀/后缀）
FILENAME_TIME_PATTERNS = [
    # 1) 长格式：六个字段，分隔符横线/下划线任意混用（v2.50 定稿）
    #    2026-08-02_19_30_25 / meeting-2026-07-31-14-30-52 / 2026-07-31-14-30-52-meeting
    r"(?P<Y>\d{4})[-_](?P<M>\d{2})[-_](?P<D>\d{2})[-_](?P<h>\d{2})[-_](?P<m>\d{2})[-_](?P<s>\d{2})",
    # 2) 紧凑式：YYYYMMDD 与 HHMMSS 之间横线/下划线均可（v2.50 由 `_` 放宽为 `[-_]`）
    #    recording_20260731_143052 / recording-20260731-143052 / 20260731_143052_recording
    r"(?P<Y>\d{4})(?P<M>\d{2})(?P<D>\d{2})[-_](?P<h>\d{2})(?P<m>\d{2})(?P<s>\d{2})",
    # 3) ISO 风格 T 分隔（兼容保留）：voice_note_20260731T143052Z
    r"(?P<Y>\d{4})(?P<M>\d{2})(?P<D>\d{2})T(?P<h>\d{2})(?P<m>\d{2})(?P<s>\d{2})",
]
ORGANIC_OUTPUT_FORMAT = "absolute"  # absolute | relative | both

# ---------- 归档重命名 ----------
# YYYY-MM-DD-HHMMSS-HHMMSS.扩展名
ARCHIVE_RENAME_PATTERN = "{date}-{start}-{end}"

# ---------- VAD (Silero VAD, 原 PyAnnote VAD 于 v2.7 切换) ----------
# v2.33：移除 max_speech_len_s 死配置（代码从未使用；Silero 默认不限制单段最大时长）
VAD_CONFIG = {
    "threshold":         0.5,
    "min_speech_len_s":  0.25,
    "min_silence_len_s": 0.1,
    "speech_pad_ms":     300,
    "sample_rate":       16000,
}

# ---------- Diarization (PyAnnote) ----------
DIARIZATION_CONFIG = {
    "model_repo":        "pyannote/speaker-diarization-3.1",
    "min_segment_s":     0.5,
    "merge_gap_s":       2.0,
    # 若为 None 自动检测；已知人数时请传整数（精度 & 速度都更好）
    "num_speakers":      None,
    # v2.31：VAD 静音切除加速。先按 Silero VAD 段拼接（切除静音）再做说话人分离，
    # 分离结果时间戳映射回原始时间轴。收益 = 静音占比 × segmentation 耗时占比；
    # 若录音几乎无静音（连续访谈）可置 False 关闭以规避拼接边界风险。
    "use_vad_concat":    True,
}

# ---------- 声纹库 & 识别 ----------
VOICEPRINT_CONFIG = {
    "embedding_model":   "pyannote/embedding",
    "sample_rate":       16000,
    # 三档阈值（余弦相似度，0-1）
    # v2.25 调低：原 0.75/0.60，实测同一声纹跨录音相似度可能略低于 0.75，
    # 调低后提高自动关联成功率；误关联可由 Web「校准已标注」手工改回
    "threshold_auto":    0.65,   # >= 自动标注
    "threshold_review":  0.50,   # >= 0.50 且 < 0.65 → "疑似待确认"；< 0.50 → UNKNOWN
    # 录入规范
    "enroll_min_duration_s":  60,
    "enroll_max_duration_s":  180,
}

# ---------- ASR (Qwen3-ASR-1.7B) ----------
# v2.31 定稿 Qwen3-ASR-1.7B；v2.32 加载精度定稿为显式 FP32（CPU 有 oneDNN 优化，速度快于 bf16），
# 实测：1.11× 实时、峰值内存 11.8GB（16GB 系统留 ~4GB 余量）；内存红线放宽至 <12GB（PRD §5.1）。
# 若内存紧张可回退默认精度（bf16：3.14× 实时 / 5.2GB）——移除下方 torch_dtype 即可（见 TDD §3.4）。
ASR_CONFIG = {
    "model_repo":        "Qwen/Qwen3-ASR-1.7B-hf",
    "use_flash_attn":    False,   # CPU 环境关闭
    "language":          "zh",
    "return_timestamps": True,
    # v2.58：加载精度默认 "auto"——按"决策时可用内存 + 语音时长"动态分配：
    #   可用内存 >= fp32_min_avail_mb（默认 13500MB = 13.5GB）且 语音时长 <= fp32_max_speech_s（默认 1800s = 30 分钟）
    #       → float32（oneDNN 优化 1.11× 实时，峰值 ~12-13GB）
    #   否则 → bfloat16（内存约减半、3.14× 实时，稳定兜底）
    # 判断依据：v2.57 语音裁剪后单段工作集已压小，ASR 峰值主要取决于模型本身（与文件大小/总时长相关性弱），
    # 所以用"决策时刻的 MemAvailable + VAD 语音总量"比 v2.49 的"音频总时长 ≥30 分钟"更精准。
    # 可用环境变量覆盖：
    #   ASR_TORCH_DTYPE=float32|bfloat16|auto
    #   ASR_FP32_MIN_AVAIL_MB=<MB>      （FP32 所需最小可用内存）
    #   ASR_FP32_MAX_SPEECH_S=<秒>      （FP32 允许的最大语音时长）
    "torch_dtype":          os.environ.get("ASR_TORCH_DTYPE", "auto"),
    "fp32_min_avail_mb":    float(os.environ.get("ASR_FP32_MIN_AVAIL_MB", "13500")),
    "fp32_max_speech_s":    float(os.environ.get("ASR_FP32_MAX_SPEECH_S", "1800")),
    # v2.53/v2.56：ASR 段合并——逐段固定开销远大于内容转录，段数越多越慢，合并相邻短段砍段数；
    # 段长上限是关键：单段越长，生成 token 越多、解码越慢、内存工作集越大（实测 bf16 下 60s 段
    # 单段约 6 分钟，进程 RSS 钉在 ~14.7GB，16GB 机器满内存+swap 假死）。
    # v2.56 默认上限 15s：峰值内存与单段耗时都大幅下降，5.2 分钟语音可 10-20 分钟完成。
    #   间隔 ≤ segment_merge_gap_s 的相邻段合并；合并后段长不超过 segment_max_s。
    #   环境变量可覆盖：ASR_SEGMENT_MERGE_GAP_S / ASR_SEGMENT_MAX_S
    "segment_merge_gap_s": float(os.environ.get("ASR_SEGMENT_MERGE_GAP_S", "1.5")),
    "segment_max_s":       float(os.environ.get("ASR_SEGMENT_MAX_S", "15.0")),
}

# ---------- 内存编排 (PRD 5.1.1) ----------
MEMORY_CONFIG = {
    "stage_unload":      True,   # 阶段完成后卸载模型内存
    "force_gc_each":     1,      # 每个音频结束后强制 GC
}

# ---------- 安全 / 访问 ----------
# Streamlit Web UI 绑定地址
# 用 Tailscale 内网 IP + 127.0.0.1；如果你想让同局域网任何设备都能访问，改成 0.0.0.0
WEB_BIND_HOST          = os.environ.get("ASR_WEB_HOST", "0.0.0.0")
WEB_PORT               = int(os.environ.get("ASR_WEB_PORT", "8501"))
# 监听的网卡名（可选），如果只想 Tailscale 访问可填 "tailscale0"
WEB_BIND_INTERFACE     = os.environ.get("ASR_WEB_IFACE", "")
