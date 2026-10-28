import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ImportError:  # tests / minimal installs
    def load_dotenv(*_a, **_k):
        return False

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _s(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _b(key: str, default: str = "false") -> bool:
    return _s(key, default).lower() in ("1", "true", "yes")


def _list(key: str) -> tuple:
    return tuple(x.strip() for x in _s(key).split(",") if x.strip())


@dataclass(frozen=True)
class Config:
    host: str = _s("VOICEAGENT_HOST", "sauron")
    default_persona: str = _s("VOICEAGENT_PERSONA", "friend")
    data_dir: Path = ROOT / _s("VOICEAGENT_DATA_DIR", "./sessions")
    history_turns: int = int(_s("HISTORY_TURNS", "20"))
    max_action_steps: int = int(_s("MAX_ACTION_STEPS", "5"))

    input_device: str = _s("AUDIO_INPUT_DEVICE")
    output_device: str = _s("AUDIO_OUTPUT_DEVICE")
    sample_rate: int = int(_s("AUDIO_SAMPLE_RATE", "16000"))
    push_to_talk: bool = _b("PUSH_TO_TALK", "true")
    silence_threshold: float = float(_s("SILENCE_THRESHOLD", "0.01"))
    silence_seconds: float = float(_s("SILENCE_SECONDS", "1.2"))
    max_record_seconds: int = int(_s("MAX_RECORD_SECONDS", "60"))

    whisper_url: str = _s("WHISPER_SERVER_URL", "http://127.0.0.1:8085")
    whisper_language: str = _s("WHISPER_LANGUAGE", "en")

    tts_backend: str = _s("TTS_BACKEND", "piper")
    piper_bin: str = _s("PIPER_BIN")
    piper_voice_model: str = _s("PIPER_VOICE_MODEL")
    kokoro_url: str = _s("KOKORO_URL")
    kokoro_voice: str = _s("KOKORO_VOICE") or "af_heart"
    kokoro_api_key: str = _s("KOKORO_API_KEY")
    kokoro_model: str = _s("KOKORO_MODEL")  # kokoro_local: path to kokoro-v1.0.onnx
    kokoro_voices: str = _s("KOKORO_VOICES")  # kokoro_local: path to voices-v1.0.bin
    kokoro_speed: float = float(_s("KOKORO_SPEED", "1.0"))

    # Web UI settings
    web_token: str = _s("WEB_TOKEN", "NONE")
    web_host: str = _s("WEB_HOST", "127.0.0.1")
    web_port: int = int(_s("WEB_PORT", "8090"))
    web_allowed_hosts: tuple = _list("WEB_ALLOWED_HOSTS")

    # Personas listed here may use a non-local LLM even if marked local_only.
    allow_cloud_personas: tuple = _list("ALLOW_CLOUD_PERSONAS")

    router_use_llm: bool = _b("ROUTER_USE_LLM", "true")
    router_min_score: int = int(_s("ROUTER_MIN_SCORE", "2"))

    vault_dir: Path = Path(_s("VAULT_DIR") or ROOT.parent.parent)
    daily_notes_dir: str = _s("DAILY_NOTES_DIR", "daily")
    context_max_chars: int = int(_s("CONTEXT_MAX_CHARS", "6000"))
    context_total_chars: int = int(_s("CONTEXT_TOTAL_CHARS", "24000"))
    summary_to_daily: bool = _b("SESSION_SUMMARY_TO_DAILY", "true")
    propose_note_updates: bool = _b("PROPOSE_NOTE_UPDATES", "true")

    ssh_host: str = _s("NANAK_SSH_HOST")
    ssh_user: str = _s("NANAK_SSH_USER")
    ssh_port: str = _s("NANAK_SSH_PORT", "22")
    ssh_key: str = _s("NANAK_SSH_KEY_PATH")
    readonly_allowlist: tuple = _list("READONLY_ALLOWLIST")
    command_denylist: tuple = _list("COMMAND_DENYLIST")

    perfin_db_path: str = _s("PERFIN_DB_PATH")
    perfin_remote_db_path: str = _s("PERFIN_REMOTE_DB_PATH", "/opt/docker/perfin/finance_tracker.db")
    perfin_max_rows: int = int(_s("PERFIN_MAX_ROWS", "100"))


cfg = Config()


@dataclass(frozen=True)
class LLMProfile:
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    reasoning_effort: str = ""


def llm_profile(name: str | None = None) -> LLMProfile:
    """Global LLM_* settings, overridden per persona by LLM_*_<NAME> (e.g. LLM_MODEL_FINANCE)."""
    sfx = f"_{name.upper()}" if name else ""

    def g(key: str, default: str = "") -> str:
        return _s(key + sfx) or _s(key, default)

    return LLMProfile(
        base_url=g("LLM_BASE_URL", "http://127.0.0.1:11434/v1"),
        api_key=g("LLM_API_KEY", "ollama"),
        model=g("LLM_MODEL"),
        temperature=float(g("LLM_TEMPERATURE", "0.6")),
        max_tokens=int(g("LLM_MAX_TOKENS", "1024")),
        reasoning_effort=g("LLM_REASONING_EFFORT", "low"),
    )


def is_local_url(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host == "localhost" or host.endswith((".local", ".lan", ".ts.net")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip in ipaddress.ip_network("100.64.0.0/10")


def check_llm_allowed(persona: str, local_only: bool, profile: LLMProfile) -> None:
    if local_only and not is_local_url(profile.base_url) and persona not in cfg.allow_cloud_personas:
        raise PermissionError(
            f"'{persona}' is local-only but its LLM endpoint ({profile.base_url}) isn't local. "
            f"Point LLM_BASE_URL_{persona.upper()} at a local server, or add '{persona}' "
            f"to ALLOW_CLOUD_PERSONAS if you really mean it."
        )
