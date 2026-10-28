# Web UI Implementation Plan

## 0. [VERIFY] Answers (Recon)

- **Interface mapping:** `WebUI` must implement `say(text)` and `confirm(question)`. `Session.handle()` is the main entry point. It is synchronous and blocking.
- **Confirm Gate:** `ui.confirm()` blocks the execution thread. In the Web UI, this will be handled by a `threading.Event` per session. The worker thread will wait on the event, and the `/api/confirm` endpoint will set the result and trigger the event.
- **Seams:** 
    - STT: `stt.transcribe(wav_bytes)` already exists and is perfect.
    - TTS: Need to add `tts.synthesize_wav_bytes(text)` which returns `_piper` / `_kokoro` bytes without calling `play_wav()`.
- **Markdown/Summary:** `tts.clean_for_speech` exists and will be used for all audio.

## 1. Technical Design

### The WebUI Adapter
The `WebUI` class will act as a bridge between the asynchronous FastAPI requests and the synchronous `Session` object.

- **`say(text)`**: Appends the text to the current turn's `reply_text` buffer.
- **`confirm(question)`**: 
    1. Sets the session status to `"confirm_required"`.
    2. Stores the `question` and a `threading.Event`.
    3. Waits on the event with a 60s timeout.
    4. Returns the boolean result set by `/api/confirm`.

### API Implementation
- **`/api/turn`**: 
    1. Validates Token/Host.
    2. Spawns/Retrieves a `Session` in a `ThreadPoolExecutor` worker.
    3. Calls `session.handle(transcript)`.
    4. Returns the result (or `confirm_required`).
- **`/api/confirm`**: 
    1. Finds the active session.
    2. Sets the result (True/False) on the session's confirm event.
    3. Triggers the event to wake up the worker thread.

## 2. File Map

### Backend
- `voiceagent/web.py`: FastAPI app, `WebUI` adapter, Session manager, and `--web` CLI entry point.
- `voiceagent/tts.py`: Add `synthesize_wav_bytes(text)`.

### Frontend (`voiceagent/web/`)
- `index.html`: Main layout.
- `css/style.css`: Lean styling.
- `js/main.js`: App logic, state management, and API calls.
- `js/audio-utils.js`: `downsample()` and `encodeWav()` (pure functions).

## 3. Pre-flight Checklist (Section 10)

| Item | Status | Note |
|------|--------|------|
| 1. Terminal voice loop | ✅ | Verified in README/Daily notes. |
| 2. `--text` mode personas | ✅ | Verified in README. |
| 3. Confirm reliability | ⚠️ | Not explicitly benched; will rely on `parse_yes_no`. |
| 4. Whisper port | ✅ | Confirmed 8080 in README/Pipeline test. |
| 5. VRAM check | ✅ | gpt-oss:20b fits; others spill. |
| 6. Context Length | ✅ | `OLLAMA_CONTEXT_LENGTH=16384` noted. |

## 4. Risks & Mitigations
- **Thread Leak:** Use a `ThreadPoolExecutor` with a fixed max and implement a strict timeout in `WebUI.confirm` to prevent deadlocked workers.
- **VRAM Contention:** The server will only call existing endpoints (Ollama/Whisper); no new models will be loaded by the web layer.
