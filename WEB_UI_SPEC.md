# VoiceAgent Web UI - spec v2 (lean)

Read `README.md` first. It is the source of truth for the project. This file only
adds what the browser interface needs. Items marked **[VERIFY]** are my guesses:
check them in the code and tell me if they are wrong.

**Context budget (important, usage is limited):** read only the files you need.
Never print whole directories. Read this spec once, then re-read only the section
for the current phase. Prefer `grep` and targeted line ranges over opening big
files.

## 0. Rules

1. Work in phases (section 6). **Stop at the end of each phase**, post the report
   (section 8) and wait for me to say "continue".
2. Phase 0 is read-only, except creating `docs/web_ui_plan.md`.
3. Smallest possible diff. No refactors, no reformatting, no unrelated fixes. Put
   anything you notice in `docs/web_ui_plan.md` under "Noticed, not fixed".
4. Never claim success without running it. Paste trimmed real output.
5. Ask before: installing dependencies, `git init`, changing the signature or
   behaviour of an existing function, deleting files.
6. Do not stop, restart or reconfigure whisper, Ollama, Piper, or anything on
   Nanak. Only call them as a client.
7. If this is a git repo, use branch `web-ui`, one commit per phase, never push.
8. Record the baseline first: run `python -m unittest discover -s tests -t .` and
   note what passes and fails. Do not fix failures that were there before.
9. Terminal mode (`python -m voiceagent`, `--text`) must keep working unchanged.
10. If this spec contradicts the code or itself, stop and tell me.

## 1. Goal and non-goals

**Goal:** a browser page on Sauron where I hold a button, speak, then see my
transcript and the agent's text reply and hear the reply. It must behave exactly
like the terminal agent: personas, routing, confirm gate, memory notes.

**Not now:** streaming audio or WebSockets, wake word or voice activity detection,
accounts, a database, HTTPS, JS frameworks or any build step.

## 2. Design decisions (made; follow unless you find a concrete problem)

- **D1. Same brain, new front end.** Add a `WebUI` that fits the same interface as
  `VoiceUI`/`TextUI` in `ui.py` (speak, ask yes/no, etc.) and drive the existing
  `agent.py` session. Do not change the action loop, router, tools, vault or
  memory code. [VERIFY the interface and how `session.handle()` is called.]
- **D2. Browser sends a 16 kHz mono 16-bit WAV.** The page captures the mic
  (AudioWorklet), downsamples and encodes the WAV in JS. No ffmpeg, no codec
  differences between browsers.
- **D3. FastAPI + uvicorn**, one process. STT, LLM and TTS are blocking, so run
  them in a worker thread. **One global lock:** one active turn at a time (single
  GPU, and the notes must not be written by two sessions at once because
  Syncthing syncs them).
- **D4. Raw WAV request body**, JSON response. No multipart, no extra dependency.
- **D5. Confirm gate stays authoritative.** Changes still need a spoken or
  clicked "yes" and unclear means no. The existing gate may block inside
  `session.handle()` waiting for an answer. If so, the web layer must handle
  that with a two-step turn (section 3): the turn pauses with
  `status: "confirm_required"`, and the answer arrives on `/api/confirm`. A
  confirmation that gets no answer within 60 s counts as **no**.
- **D6. Persona can change mid-session** (voice switching and auto-routing exist).
  Every response carries the current persona and the UI shows it.
- **D7. Privacy rules are untouched.** The web layer must not route around the
  local-only guard or `ALLOW_CLOUD_PERSONAS`. Show a "cloud" badge on any reply
  that came from a cloud model.
- **D8. Memory.** Each browser session is one agent session. An **End session**
  button runs the same close path as saying "goodbye" (daily summary plus proposed
  updates, each needing a yes). Idle expiry (30 min) or closing the tab runs the
  same path as Ctrl+C (daily summary only). `--no-memory` still works.
- **D9. Config via the repo's conventions.** Add new variables to `_env.md` and
  `scripts/make_env.py`, not a new config system: `WEB_HOST` (127.0.0.1),
  `WEB_PORT` (8090), `WEB_TOKEN`, `WEB_ALLOWED_HOSTS`, `WEB_STT_TIMEOUT` (30),
  `WEB_LLM_TIMEOUT` (90), `WEB_TTS_TIMEOUT` (30). Start with
  `python -m voiceagent --web`. The whisper port comes from `.env`: my notes
  disagree (8080 vs 8081), so never hard-code it.
- **D10. Security (the shell persona can run commands on Nanak, so treat this
  page like a remote shell).**
  - Bind to 127.0.0.1 only unless `WEB_HOST` says otherwise.
  - **A token is always required, even on loopback.** Generate a random one at
    startup if `WEB_TOKEN` is empty and print a URL containing it. Compare with
    `hmac.compare_digest`. The page keeps it in `sessionStorage`.
  - Check the Host header against an allowlist (localhost, 127.0.0.1, [::1], plus
    `WEB_ALLOWED_HOSTS`) and reject other `Origin` headers. No CORS middleware.
  - CSP: `default-src 'self'; media-src 'self' blob: data:; connect-src 'self'`.
    Also `X-Content-Type-Options: nosniff` and `Cache-Control: no-store` on the API.
  - **Render all transcript and model text with `textContent`, never `innerHTML`.**
  - Never log transcripts, replies or audio.

## 3. API

Every request except `GET /` and static files needs `Authorization: Bearer <token>`.
Every API request except `/api/health` also needs `X-Session-Id` matching
`^[A-Za-z0-9-]{16,64}$` (created by the page with `crypto.randomUUID()`).

**POST /api/turn.** Body: WAV bytes, `Content-Type: audio/wav`. Response 200:
```json
{
  "status": "done",
  "persona": "friend",
  "transcript": "what you said",
  "reply_text": "what the agent said",
  "audio_wav_base64": "UklGR...",
  "llm_backend": "local",
  "confirm": null,
  "tool_calls": [{"name": "string", "summary": "short"}],
  "timings_ms": {"stt": 410, "llm": 1900, "tts": 350, "total": 2700}
}
```
When the gate needs an answer, `status` is `"confirm_required"`, `reply_text` and
the audio read the proposed action aloud, and `confirm` is
`{"id": "string", "prompt": "string"}`.

**POST /api/confirm.** JSON `{"id": "...", "answer": "yes" | "no"}` (the page's
Confirm and Deny buttons) or a WAV body with `X-Confirm-Id` (spoken answer, parsed
with the existing yes/no logic). Returns the same shape as `/api/turn`.

**POST /api/end.** Runs the close path from D8, returns `{"summary_saved": true}`.

**GET /api/health.** Needs the token but not a session id. Returns `{"status": "ok|degraded|down", "services": {"stt":..., "llm":...,
"tts":...}}`, each with `ok`, `latency_ms`, `detail`. Cheap checks only (no
inference), cached 2 s.

**Errors:** `{"error": {"code": "...", "message": "...", "stage": "...", "retryable": true}}`

| code | HTTP | meaning |
|---|---|---|
| bad_audio | 415 | not RIFF/WAVE PCM16 mono 16 kHz, or corrupt |
| too_short / too_long | 422 / 413 | under 0.4 s / over 60 s or 4 MB |
| silent | 422 | effectively silence (RMS under 0.005) |
| empty_transcript | 422 | whisper returned nothing usable |
| bad_session | 400 | missing or malformed session id |
| unauthorized / forbidden_host | 401 / 403 | token or Host/Origin check failed |
| busy | 429 | another turn is running (`Retry-After: 2`) |
| confirm_expired | 410 | confirmation timed out (treated as "no") |
| persona_blocked | 403 | privacy guard refused (cloud not allowed for this persona) |
| stt_/llm_/tts_ unavailable or timeout | 503 / 504 | backend down or too slow |
| internal | 500 | unexpected; never send a stack trace |

## 4. Backend notes

- New seams: `tts.synthesize_wav_bytes(text)` (same voice and sanitiser as
  `speak()`, returns bytes) and `stt.transcribe_wav_bytes(data)`. Existing
  functions stay unchanged. [VERIFY the Markdown stripping and long-output
  summarising already exist; reuse them.]
- Validate audio with the stdlib `wave` module before calling any backend.
  **`audioop` was removed in Python 3.13; this machine runs 3.14**, so use
  `array` or `struct`.
- Cap the text sent to TTS (1,500 chars). Reasoning text from gpt-oss must never
  reach `reply_text` or the speaker.
- Sessions: dict keyed by id, 20 max (evict least recently used), clock
  injectable for tests. Sessions must not share state.
- Temp files go in a private temp dir and are removed in `finally`.
- Logging: request id, status, per-stage milliseconds only.

## 5. Front end

Plain HTML/CSS/JS served from `voiceagent/web/`, works offline, no CDN.

- Capture at the browser's native sample rate, then downsample with a box filter.
  Do not request a 16 kHz AudioContext (some browsers refuse). Put `downsample()`
  and `encodeWav()` in `audio-utils.js` as pure functions (`node --test` if Node
  exists).
- Hold-to-talk button (also click-to-toggle and the Space key). Disabled while the
  agent is thinking or speaking, so the mic does not hear the speaker. Cap at 60 s;
  discard under 0.4 s with a friendly message.
- Visible states (idle, recording with level meter, transcribing/thinking,
  speaking, error), persona badge, local/cloud badge, health dots, per-turn
  timings, conversation log with copy buttons, Stop, **End session**.
- A pending confirmation shows a clear banner with **Confirm** and **Deny**.
- Autoplay the reply; on `NotAllowedError` show a Play button. Replay button on
  every agent message.
- Specific messages from error codes ("Microphone blocked", "Whisper not
  reachable", "Busy, try again").
- Keyboard operable, `aria-live` on status and log, works at phone width.
  The microphone only works on `http://localhost` or HTTPS; opening the page over a
  plain LAN IP will not work. Document it, do not work around it.

## 6. Phases

- **Phase 0, recon (read-only).** Baseline tests. Find how `session.handle()` and
  the confirm gate work (blocking or not), the `UI` interface, how personas switch,
  how memory close runs, and the tool list with side-effect flags. Check section 10
  (pre-flight): record which items I have confirmed and flag the open ones. Write
  `docs/web_ui_plan.md` with answers to every [VERIFY], the planned files, risks
  and questions. Stop.
- **Phase 1, backend core.** Seams, `web.py`, `--web`, `/api/health`, `/api/turn`
  happy path, `WebUI`, token and Host checks. Unit tests with the fake LLM
  harness. Prove terminal mode still works. Stop.
- **Phase 2, robustness.** Validation, error table, timeouts, lock, sessions,
  the confirm two-step, `/api/confirm`, `/api/end`, memory close paths. Tests for
  every error code. Stop.
- **Phase 3, front end.** Everything in section 5, plus `docs/web_ui_manual_test.md`
  written from section 11 part C so I can run the manual checks step by step. Stop.
- **Phase 4, integration.** Skipped-unless-`VOICEAGENT_INTEGRATION=1` round trip:
  Piper says "what is two plus two", resample to 16 kHz, POST it, expect the
  transcript to contain most of the words and the reply to be non-empty. Measure
  cold vs warm latency and report (no tuning). Also turn section 11 parts A and B
  into unit tests. Stop.
- **Phase 5, docs.** README section and `scripts/run_web.sh`. Final report.

Tests use `unittest` (the repo's choice), not pytest. Reuse the fake LLM from
`tests/test_flow.py`. Mock STT and TTS. Required coverage: every error code,
concurrency (second turn gets `busy`, lock released after errors and timeouts),
session isolation and expiry, token/Host/Origin checks, the confirm flow (yes, no,
timeout), and that a persona blocked by the privacy guard stays blocked.

## 7. Pitfalls to check

- VRAM: gpt-oss:20b (about 13 GB) plus whisper plus Piper. Run one at a time as
  the README says, never load extra models, report what whisper uses.
- Ollama context: the README says set `OLLAMA_CONTEXT_LENGTH` to 16384 or more.
- Piper reads symbols aloud: sanitise before TTS.
- Syncthing: only one session may write notes at a time (the global lock).
- Browsers: autoplay rules, mic only on a secure context.

## 8. Report format (end of every phase, keep it short)

Done (bullets) / Evidence (commands + trimmed output) / Files changed /
[VERIFY] answers / Open questions / Next phase (one line). Then stop.

## 9. Done means

- `python -m voiceagent --web` starts, prints the token URL, serves the page.
- Speak, see my transcript and the reply, hear it; persona switching works.
- A change requiring confirmation can be approved or denied by click or voice.
- Terminal mode unchanged; all unit tests pass; every error code tested.
- Token, Host/Origin checks and CSP in place; nothing sensitive logged.
- Cold and warm latency reported; README section written.
- Section 11 parts A and B pass as unit tests; `docs/web_ui_manual_test.md` exists
  and I have run part C.
- Pre-flight results (section 10) are recorded in `docs/web_ui_plan.md`.

## 10. Pre-flight (I do these before Phase 1)

The agent cannot hear or speak, so it never does these itself. In Phase 0 it
records which are done and flags the rest. Items 1 and 4 block Phase 1; the
others are recommendations.

1. **Terminal voice loop works:** `python -m voiceagent --persona friend`, speak,
   hear a reply. (Blocking.)
2. `--text` mode tried with every persona: routing, the confirm gate and the
   "goodbye" note updates behave.
3. Confirm reliability: say yes and no ten times each, note how many were misheard.
4. The whisper port is confirmed in `.env` (README says 8080, an earlier note said
   8081). (Blocking.)
5. VRAM: whisper, gpt-oss:20b and Piper run one after another with no CPU spill
   (`ollama ps`).
6. `OLLAMA_CONTEXT_LENGTH` is 16384 or more, and the ~6K-token persona prompts are
   not being truncated.

## 11. Acceptance checks

**A. Security (automated unit tests).** No token gives 401. A wrong token gives
401. A Host header outside the allowlist gives 403. A foreign `Origin` gives 403.
No CORS headers are sent. The CSP header is present. Text like `<script>` in a
transcript or reply is displayed as text, never run (frontend test or code review).

**B. Failure drills (automated with mocks).** STT, LLM and TTS each down and each
timing out return their matching error code. A confirmation left unanswered for
60 s counts as "no" and nothing executes. A second turn during a running one gets
`busy`, and the lock is released afterwards. Malformed audio is rejected. After
every failure the server keeps serving the next request.

**C. Manual checks (I run these; the agent writes the steps into
`docs/web_ui_manual_test.md` in Phase 3).**
1. The mic permission prompt appears, and a denied mic gives a clear message.
2. Real speech with real speakers: no echo loop, and the button is disabled while
   the agent speaks.
3. It works in Chromium and in Firefox.
4. The page is usable at phone width.
5. Confirm and Deny buttons, and a spoken yes or no, each resolve a pending
   confirmation. No answer means nothing runs.
6. Switching persona by voice ("switch to finance") updates the badge.
7. End session writes the expected daily-note summary (check the vault; writes are
   append-only).
8. Real failure drills: stop whisper mid-session, then stop Ollama. The page shows
   a clear error and recovers when the service comes back.
9. Open the URL without the token, and with a wrong Host header. Both are refused.
10. The cloud badge shows when a cloud model answers, and a persona not listed in
    `ALLOW_CLOUD_PERSONAS` refuses cloud.
