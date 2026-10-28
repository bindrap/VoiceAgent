import re
import subprocess
import tempfile
from pathlib import Path

import requests

from .audio import play_wav
from .config import cfg


def clean_for_speech(text: str) -> str:
    """Strip markdown and symbols so the TTS doesn't read them aloud."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)  # fenced code blocks
    text = re.sub(r"`([^`]*)`", r"\1", text)  # inline code
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> text
    text = re.sub(r"https?://\S+", " link ", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}|>|[-*+]|\d+[.)])\s+", "", text)  # headings, quotes, list markers
    text = re.sub(r"[*_~|#]+", "", text)  # emphasis, table pipes, stray hashes
    text = re.sub(r"[^\w\s.,!?;:'\"()%$&/@+-]", " ", text)  # emoji and other symbols
    return re.sub(r"\s+", " ", text).strip()


def _piper(text: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.wav"
        
        # We use a pipe to write the string directly to stdin.
        # Some versions of subprocess.run with 'input=' can be buggy on certain platforms
        # and treat the input as a sequence of bytes/chars.
        # Using Popen with write is the most stable way to feed Piper.
        
        process = subprocess.Popen(
            [cfg.piper_bin, "--model", cfg.piper_voice_model, "--output_file", str(out)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False
        )
        
        stdout, stderr = process.communicate(input=text.encode("utf-8"))
        
        if process.returncode != 0:
            print(f"Piper Error: {stderr.decode(errors='replace')}")
            # Try returning empty bytes rather than crashing
            if not out.exists():
                return b""

        return out.read_bytes()


_kokoro_engine = None


def _kokoro_local(text: str) -> bytes:
    """In-process Kokoro (kokoro-onnx). The model loads once, on first use."""
    global _kokoro_engine
    import io

    import soundfile as sf

    if _kokoro_engine is None:
        from kokoro_onnx import Kokoro

        _kokoro_engine = Kokoro(cfg.kokoro_model, cfg.kokoro_voices)
    samples, rate = _kokoro_engine.create(text, voice=cfg.kokoro_voice, speed=cfg.kokoro_speed, lang="en-us")
    buf = io.BytesIO()
    sf.write(buf, samples, rate, format="WAV")
    return buf.getvalue()


def _kokoro(text: str) -> bytes:
    resp = requests.post(
        f"{cfg.kokoro_url}/v1/audio/speech",
        headers={"Authorization": f"Bearer {cfg.kokoro_api_key}"} if cfg.kokoro_api_key else {},
        json={"model": "kokoro", "voice": cfg.kokoro_voice, "input": text, "response_format": "wav"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.content


def speak(text: str) -> None:
    text = clean_for_speech(text)
    if not text:
        return
    print(f"[agent] {text}")
    backend = cfg.tts_backend
    if backend == "none":
        return
    wav = synthesize_wav_bytes(text)
    play_wav(wav)


def synthesize_wav_bytes(text: str) -> bytes:
    """Direct access to TTS bytes without playback."""
    text = clean_for_speech(text)
    if not text:
        return b""
    
    backend = (cfg.tts_backend or "none").lower().strip()
    
    if backend == "none":
        return b""
    
    if backend == "piper":
        try:
            return _piper(text)
        except Exception as e:
            print(f"Piper Error: {e}")
            return b""
            
    if backend == "kokoro_local":
        try:
            return _kokoro_local(text)
        except Exception as e:
            print(f"Kokoro (local) Error: {e}")
            return b""

    if backend == "kokoro":
        try:
            return _kokoro(text)
        except Exception as e:
            print(f"Kokoro Error: {e}")
            return b""
            
    print(f"Unknown TTS backend: {backend}")
    return b""
