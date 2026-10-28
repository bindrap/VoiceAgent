"""Web UI backend. The agent runs in a worker thread per turn; a confirm gate pauses that thread
until the browser answers (button or spoken yes/no), then the same turn resumes."""
import base64
import hmac
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import agent, stt, tts
from .config import cfg, is_local_url, llm_profile
from .ui import parse_yes_no

WEB_DIR = Path(__file__).resolve().parent / "web"
CONFIRM_TIMEOUT = 120
TURN_TIMEOUT = 240
SESSION_TTL = 3600


class WebUI:
    """Bridge between the synchronous Session and the HTTP layer."""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.parts: list[str] = []
        self.events: queue.Queue = queue.Queue()
        self.pending: dict | None = None
        self._answer = False
        self._answered = threading.Event()

    def say(self, text: str) -> None:
        self.parts.append(text)

    def listen(self) -> str:
        return ""

    def take_reply(self) -> str:
        text = " ".join(self.parts).strip()
        self.parts = []
        return text

    def confirm(self, question: str, retries: int = 0) -> bool:
        self._answered.clear()
        self._answer = False
        self.pending = {"id": uuid.uuid4().hex[:8], "prompt": question}
        self.events.put(("confirm", self.pending))
        answered = self._answered.wait(CONFIRM_TIMEOUT)
        self.pending = None
        return bool(answered and self._answer)

    def answer(self, yes: bool) -> bool:
        if not self.pending:
            return False
        self._answer = yes
        self._answered.set()
        return True


class SessionState:
    def __init__(self, session_id: str):
        self.ui = WebUI(session_id)
        self.session = agent.Session(self.ui)
        self.last_activity = time.time()
        self.worker: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return bool(self.worker and self.worker.is_alive())

    def start(self, text: str) -> None:
        def run():
            try:
                self.session.handle(text)
                self.ui.events.put(("done", None))
            except Exception as e:
                self.ui.events.put(("error", f"{type(e).__name__}: {e}"))

        self.ui.parts = []
        self.ui.events = queue.Queue()
        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def wait(self) -> tuple:
        try:
            return self.ui.events.get(timeout=TURN_TIMEOUT)
        except queue.Empty:
            return ("error", "the agent timed out")


sessions: dict[str, SessionState] = {}
sessions_lock = threading.Lock()

app = FastAPI(title="VoiceAgent")
app.mount("/css", StaticFiles(directory=WEB_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=WEB_DIR / "js"), name="js")


def check_host(request: Request) -> None:
    host = urlparse("//" + request.headers.get("host", "")).hostname or ""
    if host not in {"localhost", "127.0.0.1", "::1", *cfg.web_allowed_hosts}:
        raise HTTPException(status_code=403, detail="Forbidden host")


def get_token(request: Request, authorization: str = Header(None)) -> str:
    check_host(request)
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), (cfg.web_token or "NONE").encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return supplied


def get_session(x_session_id: str = Header(None), _: str = Depends(get_token)) -> SessionState:
    if not x_session_id:
        raise HTTPException(status_code=400, detail="Missing X-Session-Id")
    with sessions_lock:
        now = time.time()
        for sid in [s for s, st in sessions.items() if not st.busy and now - st.last_activity > SESSION_TTL]:
            sessions.pop(sid, None)
        if x_session_id not in sessions:
            try:
                sessions[x_session_id] = SessionState(x_session_id)
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Could not start session: {e}")
        state = sessions[x_session_id]
    state.last_activity = time.time()
    return state


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def build_response(state: SessionState, transcript: str, event: tuple, timings: dict) -> JSONResponse | dict:
    kind, data = event
    reply = state.ui.take_reply()
    if kind == "error":
        return error(500, "internal", str(data))
    spoken = reply
    confirm = None
    if kind == "confirm":
        confirm = data
        spoken = f"{reply} {data['prompt']}. Say yes or no.".strip()
    t = time.time()
    wav = tts.synthesize_wav_bytes(spoken) if spoken else b""
    timings["tts"] = int((time.time() - t) * 1000)
    timings["total"] = sum(timings.values())
    return {
        "status": "confirm_required" if confirm else "done",
        "persona": state.session.current,
        "transcript": transcript,
        "reply_text": reply,
        "audio_wav_base64": base64.b64encode(wav).decode() if wav else "",
        "llm_backend": "local" if is_local_url(llm_profile(state.session.current).base_url) else "cloud",
        "confirm": confirm,
        "timings_ms": timings,
    }


def run_turn(state: SessionState, transcript: str, timings: dict) -> JSONResponse | dict:
    """Route a transcript: answer a pending confirm, or start a new agent turn."""
    ui = state.ui
    if ui.pending:
        yes = parse_yes_no(transcript)
        if yes is None:
            ui.say("I didn't catch that.")
            return build_response(state, transcript, ("confirm", ui.pending), timings)
        ui.answer(yes)
        return build_response(state, transcript, state.wait(), timings)
    if state.busy:
        return error(429, "busy", "Another turn is running")
    t = time.time()
    state.start(transcript)
    event = state.wait()
    timings["agent"] = int((time.time() - t) * 1000)
    return build_response(state, transcript, event, timings)


@app.get("/")
async def index():
    return FileResponse(WEB_DIR / "index.html")


def _probe(url: str, headers: dict | None = None) -> str:
    try:
        return "ok" if requests.get(url, headers=headers, timeout=1.5).status_code < 500 else "err"
    except requests.RequestException:
        return "err"


def _tts_status() -> str:
    backend = (cfg.tts_backend or "none").lower()
    if backend == "none":
        return "off"
    if backend == "piper":
        found = shutil.which(cfg.piper_bin) or Path(cfg.piper_bin).exists()
        return "ok" if found and Path(cfg.piper_voice_model).exists() else "err"
    if backend == "kokoro_local":
        ok = Path(cfg.kokoro_model).is_file() and Path(cfg.kokoro_voices).is_file()
        return "ok" if ok else "err"
    return _probe(cfg.kokoro_url) if cfg.kokoro_url else "err"


def _health() -> dict:
    prof = llm_profile()
    services = {
        "stt": _probe(cfg.whisper_url),
        "llm": _probe(f"{prof.base_url.rstrip('/')}/models", {"Authorization": f"Bearer {prof.api_key}"}),
        "tts": _tts_status(),
    }
    ok = all(v in ("ok", "off") for v in services.values())
    return {"status": "ok" if ok else "degraded", "services": services}


@app.get("/api/health")
async def health(_: str = Depends(get_token)):
    return await run_in_threadpool(_health)


@app.get("/api/personas")
async def personas(state: SessionState = Depends(get_session)):
    return {
        "current": state.session.current,
        "personas": [{"name": n, "description": p.description} for n, p in state.session.personas.items()],
    }


@app.post("/api/persona")
async def set_persona(payload: dict, state: SessionState = Depends(get_session)):
    if state.busy:
        return error(429, "busy", "Another turn is running")
    try:
        await run_in_threadpool(state.session.switch, str(payload.get("name", "")), False)
    except (KeyError, PermissionError) as e:
        return error(400, "bad_persona", str(e))
    return {"persona": state.session.current}


@app.post("/api/turn")
async def turn(request: Request, state: SessionState = Depends(get_session)):
    audio = await request.body()
    if not audio:
        return error(415, "empty_body", "Empty audio")
    timings: dict = {}
    t = time.time()
    try:
        transcript = await run_in_threadpool(stt.transcribe, audio)
    except Exception as e:
        return error(502, "stt_failed", str(e))
    timings["stt"] = int((time.time() - t) * 1000)
    if not transcript:
        return error(422, "empty_transcript", "Whisper heard nothing")
    return await run_in_threadpool(run_turn, state, transcript, timings)


@app.post("/api/text")
async def text_turn(payload: dict, state: SessionState = Depends(get_session)):
    text = str(payload.get("text", "")).strip()
    if not text:
        return error(415, "empty_body", "Empty message")
    return await run_in_threadpool(run_turn, state, text, {})


@app.post("/api/confirm")
async def confirm(payload: dict, state: SessionState = Depends(get_session)):
    if not state.ui.pending:
        return error(409, "no_pending", "Nothing is waiting for confirmation")
    yes = payload.get("answer") == "yes"
    state.ui.answer(yes)
    event = await run_in_threadpool(state.wait)
    return await run_in_threadpool(build_response, state, "yes" if yes else "no", event, {})


@app.post("/api/end")
async def end_session(state: SessionState = Depends(get_session)):
    if state.busy:
        return error(429, "busy", "Another turn is running")
    try:
        await run_in_threadpool(state.session.finalize, False)  # no voice-confirmed note proposals over the web
    except Exception as e:
        return error(500, "internal", str(e))
    with sessions_lock:
        sessions.pop(state.ui.session_id, None)
    return {"summary_saved": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=cfg.web_host, port=cfg.web_port)
