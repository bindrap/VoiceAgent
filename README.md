# VoiceAgent

Local-first voice assistant. I talk, it routes me to the right persona, does the work or talks back, and reads the result to me. Started 2026-10-26.

> [!info] Status (Oct 28): full voice loop works on Sauron through the Web UI (Whisper → persona + local LLM → Piper/Kokoro). Not yet proven with real use: routing, voice yes/no, Nanak SSH/SQL tools. See [Current state](#current-state-oct-28).

---

## Workflow

```
 mic -> [1 STT] -> [2 Router] -> [3 Persona + LLM] -> [4 Confirm gate] -> [5 Tools] -> [6 Summarize + TTS]
                                        ^                                                    |
                                        +---------- notes (read context / write state) <-----+
```

1. **STT** — whisper.cpp server. Silence-based end-of-speech. Blank/hallucinated output ("Thank you.") is dropped.
2. **Router** — picks the persona: explicit call, then keywords, then LLM classify only if ambiguous.
3. **Persona + LLM** — each persona has its own prompt, background notes, tools, and writable files.
4. **Confirm gate** — anything that changes something is read back, and runs only on a spoken "yes". Unclear = no.
5. **Tools** — shell (Nanak), read-only SQL (Perfin), read note, append note.
6. **TTS** — Markdown stripped, long output summarized first, then spoken.

---

## Personas

Defined in `config/personas.json`. Edit that file to change what each one reads, can write, and which tools it has.

| Persona | Good for | Reads (background) | Tools | Can write (after confirm) |
|---------|----------|--------------------|-------|---------------------------|
| **friend** | talking, venting, decisions | `Parteek.md`, `Rain/Rain.md`, `Rain/Rain+Me.md`, Goals, Thoughts, Therapy, Personal, last 7 dailies | chat only | `Rain+Me.md`, `Parteek.md`, Thoughts, Goals |
| **finance** | money, portfolio, spending | `Parteek.md`, `Investments.md`, Finances, Perfin, TFSA, money_owed, last 3 dailies | `sql` (read-only), `read_note`, `note_append` | `Investments.md`, Finances, money_owed |
| **work** | tickets, what to tackle | `Work/WorkAgent.md`, last 5 dailies | `read_note`, `note_append` | ServiceNow + AMANDA TASK notes |
| **server_ops** | Nanak, Docker, logs | Server guide, inventory, (`NanakAgent.md` only on Nanak, secrets redacted) | `shell`, `read_note`, `note_append` | `NanakServerGuide.md` |
| **notes** | dictate into today's note | last daily | `note_append` (daily, immediate) | daily note only |
| **coach** | gym, jits, health | `Parteek.md`, Health, Goals, last 6 gym logs, last 6 jits journals | chat only | Health, Goals |

### Switching personas

- **Start-up flag:** `python -m voiceagent --persona finance`
- **By voice (explicit):** "switch to finance", "hey nanak, is jellyfin running", "coach mode"
- **Automatic:** keywords/patterns in `config/triggers.json` (ticket IDs → work, "tfsa" → finance, "docker" → server_ops...). Only switches when the other persona scores higher than the current one, so it stays put in normal conversation.
- **Ambiguous:** a small LLM call classifies (`ROUTER_USE_LLM`, optionally a tiny model via `LLM_MODEL_ROUTER`).
- **Tuning:** this is the part that needs real-life tuning. Add my own call words to `aliases` and keywords to `triggers.json`, no code changes.

---

## Memory = the notes

It doesn't keep its own memory. State lives in the vault and gets re-read each time a persona starts (and on every switch).

- **Every session:** a summary is appended to today's daily note (`### HH:MM — Voice session (persona)`), inserted above `Notes for Claude`.
- **At "goodbye":** the LLM proposes up to 3 durable updates for that persona's writable files. Each is read back and saved only on a "yes".
- **Writes are append-only:** a dated bullet under `## Voice updates` at the end of the file. Nothing is rewritten or deleted. Fold them into the real note myself later.
- **Notes persona:** dictation to the daily note is immediate (my own note, append-only).
- `--no-memory` turns all of it off.

---

## Privacy + safety

- **All personas are local-only.** If the LLM endpoint isn't local/private/Tailscale, the persona refuses to start unless it's listed in `ALLOW_CLOUD_PERSONAS`. Decide per persona later after testing models.
- **Per-persona models:** `LLM_MODEL_FINANCE`, `LLM_BASE_URL_FRIEND`, etc. override the global values.
- **Finance DB is read-only:** single `SELECT`/`WITH` only, write keywords rejected, opened with `mode=ro` + `query_only`. Over SSH it uses `sqlite3 -readonly`. Writes can be added later behind the confirm gate if there's a real need.
- **Shell:** read-only allowlist runs immediately. Chains, pipes, and redirects never count as read-only. Everything else needs a yes. Denylist is always blocked.
- **Secrets:** key/password/token patterns are redacted from notes and command output before they reach the model. Secret-ish files (`NanakAgent.md`, API key files, PIN/SIN notes, `_memory/`) can't be read by tools.
- **Notes access is vault-only**, `.md`/`.txt` only, no path escapes.
- On Nanak, server changes still follow the production-data rules (read-only first, impact + rollback, verify after).

---

## Hardware plan

| Box | Role |
|-----|------|
| **Sauron** (32GB DDR5, RX 7800 XT 16GB) | Experiment + refine. whisper large-v3 (Vulkan), 7-14B local LLM via Ollama, Kokoro/Piper TTS. Reaches Nanak data over SSH |
| **Nanak** (home server, CPU) | Slim version. Small whisper, small/cloud LLM, Piper. Runs shell/SQL locally. Runs standalone, not wired to Sauron |

### Bench results — Oct 26, 110s audio, `bo=1` (runs from `~/Clone/whisper.cpp` on Sauron)

| Model | Backend | Total time | Realtime speed |
|-------|---------|-----------|----------------|
| large-v3 | Vulkan (RADV coopmat) | 8.8s | 12.5× realtime |
| large-v3 | HIP/ROCm | 12.1s | 9× realtime |
| **large-v3-turbo** | **Vulkan** | **2.85s** | **38× realtime** |

- **Vulkan beats HIP on this GPU** — build `build-vulkan` is the keeper. Model load ~370ms (turbo) / ~670ms (large)
- Whisper models live in `~/Clone/whisper.cpp/models/` (built with GGML_VULKAN from source; Arch's `whisper-cpp` pkg has no Vulkan backend)
- Realistic mode for the agent: **large-v3-turbo on Vulkan** — faster than speaking-speech, leaves VRAM free for the LLM
- Also built `whisper-server` in the same tree = the STT endpoint the agent expects

### LLM bench #1 — Oct 27, Sauron, `scripts/bench-llm.py` (RX 7800 XT 16GB, ollama 0.40.11)

Two calls per model: `TOOL` = ~3.9K-token server_ops system prompt + tool-task (max 256 out), `SPEED` = tiny prompt (64 out). temp=0 seed=42. All 5 pulled at default quants, none deleted.

| Model | Size | Decode (SPEED) | TOOL rate | TOOL JSON_OK | Notes |
|-------|------|----------------|-----------|--------------|-------|
| **gpt-oss:20b (13G)** | fits VRAM | **85.4 tok/s** | 7.4 tok/s, 7.3s | **✅** – returns `{"cmd":"docker ps -a --filter \"name=immich\""}` when prompted for a filtered command. | Fastest local model, fits VRAM, best overall. |
| qwen3.6:35b (22G) | spills | 58.4 tok/s | 15.2 tok/s | ❌ – empty response (`raw=""`). | Spills to CPU, no tool JSON. |
| **laguna-xs-2.1 (20G)** | spills | 48.4 tok/s | 8.4 tok/s | **✅** – returns correct filtered command. | Works but spills, slower than gpt-oss. |
| qwen3.6:27b (17G) | spills | 22.5 tok/s | 10.9 tok/s | ❌ – empty response. | Over VRAM, slow. |
| qwen3.8:27b (17G) | spills | 15.7 tok/s | 10.5 tok/s | ❌ – refused to act as agent ("I don't have shell access…"). | Spills, refuses tool JSON. |

**Read:**
- After fixing the harness (added `tools` param, extract `reasoning_content`), **gpt-oss:20b** and **laguna-xs-2.1** now pass the tool-call contract.
- **VRAM split confirmed:** `qwen3.8:27b` showed 24%/76% CPU/GPU in `ollama ps` — anything ≥17G spills and decode collapses to ~10–22 tok/s. On a 16G card the 27b/35b set is not worth forcing.
- Real agent prompts will be ~6K tokens (persona base) — worse than this bench, so 20b-in-VRAM speed matters more.

> [!question] Open question — quantize / slim the qwens?
> 17–22G models can't fit 16G VRAM at the quants shipped on Ollama. Options: lower-quant pulls (if smaller tags exist), custom llama.cpp quantization, or a smaller member of the qwen3.x family (14B-class). **Lean: not worth it** — gpt-oss:20b fits fully in VRAM at 85 tok/s and the spill models were slower *and* failed/refused the tool task. Revisit only if gpt-oss flunks the corrected harness.

Run STT, LLM, TTS one after another on Sauron so they don't fight over 16GB VRAM.

> [!warning] Ollama context window
> Each persona sends ~20K chars of background (~6K tokens). Ollama's default context is small and truncates silently. Set `OLLAMA_CONTEXT_LENGTH=16384` (or more) on the machine running Ollama. Tune `CONTEXT_TOTAL_CHARS` in `.env`.

---

## Config

- `_env.md` — the durable, tracked template of every variable (blank secrets). **Source of truth.**
- `.env` — gitignored, per machine. Rebuild any time: `python scripts/make_env.py` (`--force` to overwrite).
- Fill in per machine: `LLM_MODEL`, `PIPER_*`, `NANAK_SSH_*`, `PERFIN_DB_PATH` (Nanak) / remote path (Sauron), `VAULT_DIR`.


### Web UI
Browser interface for the agent. Needs the whisper-server running (`scripts/run_web.sh` starts it on 8085).

- **Start:** `bash scripts/run_web.sh` or `python -m voiceagent --web` (host/port from `WEB_HOST` / `WEB_PORT`)
- **URL:** `http://127.0.0.1:8090?token=YOUR_TOKEN` (the token is stored for the tab and removed from the address bar)
- **Mic:** only works on `http://localhost` or HTTPS. Hold the button or Space to talk. Pick the mic from the dropdown (prefers `AK-2`).
- **Also:** persona dropdown, typed input, replay per reply, Stop audio, live health dots (STT / LLM / TTS).
- **Confirm gate:** the turn pauses on the agent's worker thread. Answer with the Yes/No buttons or say yes/no. Same turn resumes after.
- **End session** saves the daily-note summary only. Proposed note updates are skipped over the web (they need a voice yes/no).
- **Tests:** `pip install httpx` to run `tests/test_web.py`.
- **Gotcha fixed Oct 28:** replies were read letter by letter because `clean_for_speech` had an empty regex that put a space between every character. Test in `test_core.py` guards it.

## Run

```
pip install -r requirements.txt
python scripts/make_env.py                    # once, then edit .env
whisper-server -m <model.bin> --port 8080     # separate terminal
python -m voiceagent                          # starts in friend
python -m voiceagent --persona server_ops
python -m voiceagent --text                   # type instead of speak (debug, no mic needed)
python -m voiceagent --list
python -m unittest discover -s tests -t .     # tests (no mic/LLM needed)
```

Say "goodbye" to end cleanly (triggers the note updates). Ctrl+C saves the daily summary only.

## Code layout

```
_env.md  .env  requirements.txt
config/    personas.json (who reads/writes what)  triggers.json (routing words)
prompts/   base, friend, finance, work, server_ops, notes, coach, _actions (JSON tool protocol)
scripts/   make_env.py
tests/     test_core.py (router, SQL guard, tool gates, vault)  test_flow.py (end-to-end, fake LLM)
voiceagent/
  config.py   env + per-persona LLM profile + local-only guard
  audio.py    mic record (silence stop) + playback       stt.py   whisper.cpp client
  tts.py      Piper / Kokoro + Markdown stripping        ui.py    VoiceUI / TextUI + yes/no parsing
  router.py   persona routing                            personas.py  persona + system prompt builder
  tools.py    shell, sql, read_note, note_append + gates vault.py  safe paths, redaction, append-only writes
  memory.py   session summary + proposed updates         agent.py  session, switching, action loop
  llm.py      OpenAI-compatible client                   main.py   CLI
```

---

## Open questions / to tune

- [ ] Push-to-talk vs wake word vs always listening
- [ ] Voice confirm reliability (misheard yes/no). Maybe a confirm word
- [ ] Which local model per persona (friend wants warmth, finance wants accuracy, server_ops wants tool-following)
- [ ] Router tuning with real speech (aliases, keywords, thresholds)
- [ ] Where mic/speaker lives for headless Nanak (phone? Tailscale from Sauron?)
- [ ] Perfin schema: confirm table/column names so the finance prompt can include them
- [ ] Syncthing: agent edits daily/notes while Syncthing syncs and I edit elsewhere (conflict risk, atomic writes help)
- [ ] Streaming STT + streaming TTS for lower latency
- [ ] Writes to Perfin DB (only if a real need shows up)

## Roadmap

- [x] 1. whisper.cpp on Sauron, measure large-v3 + Vulkan — **done Oct 26** (bench notes below)
- [x] 2. `--text` mode against local Ollama — **done Oct 28** (friend; other personas still to try)
- [x] 3. Piper TTS — **done Oct 28**. Kokoro added (`kokoro_local`), voice choice pending
- [x] 4. Full voice loop on Sauron — **done Oct 28** via Web UI
- [ ] 5. SSH to Nanak: server_ops read-only, then finance SQL
- [ ] 6. Tune routing + prompts with real use
- [ ] 7. Slim deploy to Nanak
- [ ] 8. Pick best model per persona, decide cloud/local

---
## Cloud benchmark results

- **gemma4:31b** – returned the expected JSON tool call instantly (wall ≈ 0.7 s, no token count). JSON_OK=True.
- **gpt‑oss:20b (cloud)** – after fixing the stream parser to scan for `tool_calls`, the model now passes the tool‑call contract:
  ```
  ===== gpt-oss:20b =====
  TOOL   tool_call=1  wall=  0.9s  out_tok=   0  rate=  0.0 tok/s  (prompt ~0 tok)  JSON_OK=True
          -> {"cmd": "docker ps -a --filter \"name=immich\""}
  SPEED  wall=  0.98s  out_tok=  0  rate=  0.0 tok/s
  ```
  Wall‑time ≈ 0.9 s, no token‑per‑second metric in the harness. A direct `curl` with the same prompt shows **eval_count=95 tokens in 1.06 s → ≈ 90 tok/s**, comparable to local gpt‑oss:20b (85 tok/s). This makes **gpt‑oss:20b a viable cloud alternative to gemma4:31b** (slightly slower but smaller model, 20 B vs 31 B).
- **laguna‑xs‑2.1 (cloud)** – not available on Ollama Cloud (404). It’s a local‑only 20 GB model.
- **deepseek‑v4.1‑flash** – returns **402 Payment Required** under the current plan.
- Models that gave **404 Not Found** (gpt‑4o‑mini, claude‑3.5‑sonnet) are not available in the current account.

These results confirm the API key and endpoint work; cloud models can provide immediate JSON tool calls without token‑per‑second metrics. The stream parser fix now allows gpt‑oss:20b to be used reliably as a cloud model.

**Model selection decision (local‑first with cloud fallback):**
- **Primary**: `gpt‑oss:20b` local (fits 16 GB VRAM, 85 tok/s, passes tool contract).
- **Cloud fallback**: `gemma4:31b` (instant JSON, larger reasoning) or `gpt‑oss:20b` cloud (~90 tok/s, same tool quality) when local GPU is busy or for lighter devices.
- Local qwen/laguna models spill VRAM → not used.

---

## Better voice (Kokoro, in-process)

Piper is fast but flat. Kokoro (82M, Apache 2.0) sounds much more natural and still runs near real-time on the CPU.

```bash
pip install -U kokoro-onnx soundfile
mkdir -p ~/kokoro && cd ~/kokoro
wget https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx
wget https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin
```

Then in `.env`:

```
TTS_BACKEND=kokoro_local
KOKORO_MODEL=/home/parteek/kokoro/kokoro-v1.0.onnx
KOKORO_VOICES=/home/parteek/kokoro/voices-v1.0.bin
KOKORO_VOICE=af_heart      # try af_bella, af_sarah, am_michael, bf_emma (list: Kokoro-82M VOICES.md)
```

Switch back any time with `TTS_BACKEND=piper`. The first reply after start is slower while the model loads.

---

## TTS benchmark (Piper v1.8.0)

Test sentence: *“The quick brown fox jumps over the lazy dog. This is a test of the Piper text-to-speech system.”*

| Voice | Model size | Synth time | Audio dur | RTF | Notes |
|-------|------------|------------|-----------|-----|-------|
| en_US-lessac-medium | 60 MB | 0.99 s | 5.3 s | 0.19 | Baseline, clear but slightly fast |
| en_US-lessac-high | 109 MB | 1.28 s | 5.5 s | 0.23 | Larger, slower, no clear quality gain |
| **en_US-amy-medium** | 60 MB | 0.94 s | 6.8 s | **0.14** | **Lowest RTF, natural pacing, good clarity** |
| en_GB-alan-medium | 60 MB | 0.92 s | 6.4 s | 0.14 | Similar RTF to Amy, British accent |

- **All RTF < 1** → real-time synthesis on CPU (Ryzen 7 7800X).
- **Amy (en_US-amy-medium)** gives the lowest RTF (fastest relative to audio length) and a slightly longer, more natural‑sounding audio.
- **Lessac‑high** is almost 2× larger and slower without a noticeable quality improvement for our use case.
- **Recommendation**: default to **`en_US-amy-medium`** (best balance of speed, size, naturalness); keep `lessac-medium` as fallback.

---

## Current state (Oct 28)

**Working on Sauron:** full voice loop through the Web UI. Hold to talk (AK-2) → Whisper → persona + `gpt-oss:20b` → spoken reply, with the confirm gate, persona switching and session summaries.

| Piece | State |
|-------|-------|
| STT | whisper-server `large-v3-turbo` (Vulkan) on port 8085, started by `scripts/run_web.sh` |
| LLM | `gpt-oss:20b` local via Ollama, `gemma4:31b` cloud as fallback |
| TTS | Piper `en_US-amy-medium` works. Kokoro (`kokoro_local`) added for a more natural voice |
| Web UI | Mic picker, typed input, persona dropdown, replay, health dots, voice or button confirms |
| Terminal modes | `--text` works. Voice-in-terminal not retested since the Web UI took over |
| Tests | 35 pass (`python -m unittest discover -s tests -t .`). `tests/test_web.py` needs `pip install httpx` |

**Persona upgrade (Oct 28):** every persona now uses the actions protocol and can see the vault. Prompts (`prompts/base.md`) say so explicitly. New read-only tools `list_notes` (newest notes in a folder) and `search_notes` (find lines across notes) join `read_note`. Secrets and `_memory`/`.git`/`repos` stay blocked. "Garbled plan" fix: fences stripped, one retry, plain prose spoken as-is, bad output logged as `bad_plan` in `sessions/*.jsonl`, and `LLM_REASONING_EFFORT` (default `low`) so `gpt-oss` doesn't burn its tokens thinking.

**Plan normalization (Oct 28):** tested every persona with text phrases against `gpt-oss:120b-cloud`. Models often return one bare action (`{"tool":...}`) instead of `{"say","actions":[]}`, which used to produce "Done." `llm.parse_json` now wraps it. Empty replies get one retry. Finance falls back to `Investments.md` and the daily-note portfolio lines when Perfin isn't reachable (set `PERFIN_DB_PATH` or `NANAK_SSH_HOST` for live numbers).

**Local vs cloud:** keep the local model (`gpt-oss:20b`, about 85 tok/s) as the default. Personas are `local_only` because their context (finance, therapy, health, Rain) would leave the machine on cloud. If the 20b proves too weak, allow cloud for the least sensitive ones with `ALLOW_CLOUD_PERSONAS=work,notes` plus a per-persona `LLM_BASE_URL_<NAME>`/`LLM_MODEL_<NAME>`. A second GPU isn't worth it until a week of real use shows the 20b is the bottleneck. Still to do: retest on Sauron with the local model.

**Setup notes:**
- Whisper binary: `/home/parteek/Clone/whisper.cpp/build-vulkan/bin/whisper-server`. Port is 8085 (8080 was a bind conflict).
- Python deps live in the local `.venv`. Activate it before `bash scripts/run_web.sh`.
- Piper uses the local binary, no TTS server port involved.

**Fixed Oct 28:**
- Replies read letter by letter: `clean_for_speech` had an empty regex that spaced every character. Regression test added.
- Web confirm flow rewritten: the turn pauses and resumes on the worker thread. Before, "yes" was sent as a new message and the pending action was lost.
- Static files weren't served, health dots were hardcoded green, and the auth/host checks now cover every endpoint.
- Mic: exact device selection, browser echo/noise/auto-gain off, native-rate recording resampled to 16 kHz, quiet audio boosted, clear errors when silent.
- STT request format and port conflicts sorted during deployment.

**Not done yet (fine for now):**
- Routing on real speech, voice yes/no reliability, and persona tuning need real use.
- Nanak SSH and finance SQL tools untested live. Tighten the shell allowlist (prefix match) before enabling `server_ops`.
- Web "End session" saves the daily summary only. Proposed note updates need a voice yes/no, so they're skipped there.
- Systemd services for whisper-server + VoiceAgent, then the slim Nanak deploy.

---

## Related

- [[Sauron-001]] · [[Sauron-002]]
- `Nanak/NanakServerGuide.md`
