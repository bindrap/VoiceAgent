import re
import requests

from .config import cfg

# whisper tends to emit these on silence/noise
HALLUCINATIONS = {"", "you", "thank you.", "thanks for watching!", "thanks for watching.", "bye."}


def clean_transcript(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", text)  # [BLANK_AUDIO], (music), etc.
    text = " ".join(text.split())
    return "" if text.lower() in HALLUCINATIONS else text


def transcribe(wav_bytes: bytes) -> str:
    """Send WAV to a running whisper.cpp server (whisper-server) and return text."""
    
    # Based on the server's --convert option, it prefers raw audio data 
    # that it can transcode itself via ffmpeg, OR a strictly valid WAV.
    # Let's try the most reliable a-synchronous request format:
    # a simple multipart upload with just the 'file' field.
    
    try:
        files = {
            'file': ('audio.wav', wav_bytes, 'audio/wav')
        }
        
        # We remove all extra data fields as some versions of the server 
        # treat extra form fields as a malformed request.
        resp = requests.post(
            f"{cfg.whisper_url}/inference",
            files=files,
            timeout=120,
        )
        
        if resp.status_code != 200:
            print(f"STT Server Error ({resp.status_code}): {resp.text}")
            resp.raise_for_status()
            
        return clean_transcript(resp.json().get("text", ""))
    except Exception as e:
        # Fallback: send raw binary without any multipart wrapping
        try:
            resp = requests.post(
                f"{cfg.whisper_url}/inference",
                data=wav_bytes,
                headers={"Content-Type": "audio/wav"},
                timeout=120,
            )
            resp.raise_for_status()
            return clean_transcript(resp.json().get("text", ""))
        except Exception as e2:
            raise RuntimeError(f"STT request failed all modes: {e2}")
