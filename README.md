# Local ASR & Voiceprint System

A fully **local, offline** audio transcription and speaker identification system. All inference runs on-device (CPU); all data stays on the machine — no cloud service, no data leaving the device.

## Design Principles

- **Local-first**: All inference runs on the device CPU (VAD / speaker diarization / voiceprint identification / speech-to-text), no network required
- **Data stays on-device**: Transcripts, voiceprints, and text backups are stored in a local SQLite database and local directories, fully under your control
- **Runs offline**: After model weights are downloaded once, the system works completely offline with no external dependencies
- **Sensitive operational details stay local**: Network addresses, proxy setup, device info, and personal labels are maintained in a local-only notes file (not in this repository), so nothing private is published here

## Features

- **Speech-to-text**: Qwen3-ASR-1.7B multimodal model, offline CPU inference (precision auto-selected: FP32 when memory allows, bf16 otherwise), Chinese and multi-language/dialect support
- **Speaker diarization**: pyannote diarization, automatically splits audio into different speakers
- **Voiceprint identification & continuous learning**: pyannote embedding identifies known speakers; unrecognized speakers are persisted as `unknown_XXXX` labels — annotate the name once in the Web UI and the system keeps learning, getting more accurate over time
- **Voice activity detection**: Silero VAD, transcribes only speech segments and skips silence
- **Web management UI** (Streamlit): processing status dashboard, transcript search, speaker labeling/calibration, speaker profiles, archive browsing
- **Multiple entry points**: Web dashboard (recommended) + CLI main menu (`run.sh`)

## Processing Pipeline

```
audio → voice activity detection (VAD) → speaker diarization → voiceprint matching → speech-to-text (ASR) → archive
```

Processing happens entirely in memory; the source audio files are never modified. On success, files are archived with a unified naming rule, and transcripts are stored per-segment in the database and in text backups (TXT / JSON).

## Repository Layout

This README sits at the git repository root (so GitHub renders it on the repo home page). All effective content lives at the top level, mirroring the run node layout (`/home/kevin/asr_sys_local/`): the code lives in `asr-local/` (= deployment source), while the data directories are kept as placeholders (their contents are personal data and never committed).

```
local-asr-sys/                # git repository root (this README is rendered here)
├── README.md                 # project overview (this file)
├── .gitignore                # excludes secrets, audio data, models, etc.
├── asr-local/                # code (= deployment source)
│   ├── config/               # global configuration (paths, thresholds, model parameters)
│   ├── scripts/              # entry points (Web UI, batch processor, CLI tools, model download)
│   ├── src/                  # core modules (VAD / diarization / voiceprint / ASR / database / archive)
│   ├── src/utils/            # shared utilities (audio I/O, timestamps, hashing)
│   ├── systemd/              # systemd units (optional, for running as a service)
│   ├── run.sh                # CLI main menu launcher
│   ├── deploy_webui.sh       # deploy script: syncs code to the run node and restarts the service
│   └── requirements.txt
├── audio_inbox/              # data: inbox (drop audio files here; contents not committed)
├── audio_archive/            # data: archived audio / text backups / database (contents not committed)
├── PRD_local_asr_system.md   # product requirements document
└── TDD_local_asr_system.md   # technical design document
```

## Quick Start

1. Set up a Python 3.12 environment and install dependencies: `pip install -r requirements.txt`
2. Download model weights (large in size, and each model has its own license — **not distributed with this repository**):
   `bash scripts/step2_download_models.sh <HF_TOKEN>`
3. Launch the Web UI: `streamlit run scripts/webui.py` (or run `run.sh` to enter the main menu)

## Deployment to a Run Node

`deploy_webui.sh` syncs the code to a run node (e.g., a low-power machine on your LAN) and restarts the service:

```bash
bash deploy_webui.sh
```

The run node configures local paths and the model directory via a `.env` file; the code itself contains no machine-specific paths, and local configuration files are never committed.

## Local Configuration (not committed)

| File/Directory | Purpose | Notes |
|---|---|---|
| `.env` | Run-node environment variables (token, paths) | Local secret, never commit |
| `models/` | Model weight cache | Large + license restrictions, download yourself |
| `sample_audio/` | Sample audio | Personal data, never commit |

## License

- Repository code: MIT
- Model weights (Qwen3-ASR, pyannote series, Silero VAD) each have their own license; check the terms before use. Weights must be downloaded by yourself.
