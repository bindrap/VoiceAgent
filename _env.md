# _env — VoiceAgent config template

This note is the **durable, version-controlled copy** of the config. `.env` is gitignored and can get lost,
so this file holds every variable with blank secrets. Never put real keys in here.

Rebuild `.env` from it (won't overwrite an existing `.env` unless `--force`):

```
python scripts/make_env.py
```

Then fill in the blanks per machine (Sauron / Nanak). Per-persona override: add `_<PERSONA>` to
`LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`
(e.g. `LLM_MODEL_FINANCE=qwen2.5:14b`, `LLM_MODEL_FRIEND=...`, `LLM_MODEL_ROUTER=...`).

```env
# ---- Machine ----
# sauron | nanak
VOICEAGENT_HOST=sauron
# friend | finance | work | server_ops | notes | coach  (starting persona; switch by voice)
VOICEAGENT_PERSONA=friend
VOICEAGENT_DATA_DIR=./sessions
HISTORY_TURNS=20
MAX_ACTION_STEPS=5

# ---- Audio I/O ----
# blank = system default (sounddevice index or name)
AUDIO_INPUT_DEVICE=
AUDIO_OUTPUT_DEVICE=
AUDIO_SAMPLE_RATE=16000
# true = press Enter then speak; auto-stops on silence
PUSH_TO_TALK=true
# RMS below this counts as silence
SILENCE_THRESHOLD=0.01
SILENCE_SECONDS=1.2
MAX_RECORD_SECONDS=60

# ---- STT: whisper.cpp server (whisper-server -m <model> --port 8080) ----
WHISPER_SERVER_URL=http://127.0.0.1:8080
WHISPER_LANGUAGE=en

# ---- LLM: any OpenAI-compatible endpoint (Ollama = http://127.0.0.1:11434/v1) ----
LLM_BASE_URL=http://127.0.0.1:11434/v1
# Ollama local: anything non-empty, e.g. ollama
LLM_API_KEY=
# e.g. qwen2.5:14b on Sauron
LLM_MODEL=
LLM_TEMPERATURE=0.6
LLM_MAX_TOKENS=1024

# ---- Privacy: every persona is local-only by default ----
# Comma-separated personas allowed to use a non-local LLM endpoint. Leave blank to keep everything local.
ALLOW_CLOUD_PERSONAS=

# ---- Routing (which persona handles what I said) ----
# Use the LLM to classify only when keywords are ambiguous
ROUTER_USE_LLM=true
ROUTER_MIN_SCORE=2
# optional: LLM_BASE_URL_ROUTER= / LLM_MODEL_ROUTER= (a small fast model is fine)

# ---- TTS ----
# piper | kokoro | none
TTS_BACKEND=piper
PIPER_BIN=
PIPER_VOICE_MODEL=
# TTS_BACKEND=kokoro_local (best quality, in-process): needs `pip install kokoro-onnx` + the two files below
KOKORO_MODEL=
KOKORO_VOICES=
KOKORO_SPEED=1.0
# TTS_BACKEND=kokoro (OpenAI-compatible server) uses KOKORO_URL
KOKORO_URL=
KOKORO_VOICE=af_heart
KOKORO_API_KEY=

# ---- Vault + memory (notes are the memory) ----
VAULT_DIR=C:/Users/bindrap/Documents/Obsidian Vault
DAILY_NOTES_DIR=daily
CONTEXT_MAX_CHARS=6000
# total background context per persona (~4 chars per token). Ollama's default context window is small:
# set OLLAMA_CONTEXT_LENGTH=16384 (or more) on the machine running Ollama or the notes get truncated silently.
CONTEXT_TOTAL_CHARS=24000
# append a summary of each session to today's daily note
SESSION_SUMMARY_TO_DAILY=true
# at goodbye, propose durable updates to persona-writable notes (each needs a spoken yes)
PROPOSE_NOTE_UPDATES=true

# ---- Nanak access (used from Sauron over SSH; on Nanak itself commands run locally) ----
NANAK_SSH_HOST=
NANAK_SSH_USER=
NANAK_SSH_PORT=22
NANAK_SSH_KEY_PATH=
# Commands starting with these run WITHOUT confirmation (read-only only)
READONLY_ALLOWLIST=docker ps,docker logs,docker inspect,docker stats --no-stream,df -h,free -m,uptime,systemctl status,git status,ls,cat,tail,journalctl
# Always blocked, even if confirmed
COMMAND_DENYLIST=rm -rf,mkfs,dd if=,shutdown,reboot,docker volume rm,docker system prune,chmod -R,chown -R

# ---- Perfin finance DB (read-only) ----
# Local path when running on Nanak (blank = go over SSH using the remote path)
PERFIN_DB_PATH=
PERFIN_REMOTE_DB_PATH=/opt/docker/perfin/finance_tracker.db
PERFIN_MAX_ROWS=100
```
