import io
import time

from .config import cfg


def _device(value: str):
    if not value:
        return None
    return int(value) if value.isdigit() else value


def record_utterance() -> bytes:
    """Record until silence after speech. Returns WAV bytes (mono, 16-bit)."""
    import numpy as np
    import sounddevice as sd
    import soundfile as sf

    sr =  cfg.sample_rate
    block = int(sr * 0.1)
    chunks: list[np.ndarray] = []
    heard_speech = False
    silent_for = 0.0
    start = time.time()

    with sd.InputStream(samplerate=sr, channels=1, dtype="float32",
                        blocksize=block, device=_device(cfg.input_device)) as stream:
        while time.time() - start < cfg.max_record_seconds:
            data, _ = stream.read(block)
            chunks.append(data.copy())
            rms = float(np.sqrt(np.mean(data ** 2)))
            if rms >= cfg.silence_threshold:
                heard_speech = True
                silent_for = 0.0
            elif heard_speech:
                silent_for += 0.1
                if silent_for >= cfg.silence_seconds:
                    break

    audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")
    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def play_wav(wav_bytes: bytes) -> None:
    import sounddevice as sd
    import soundfile as sf

    data, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32")
    sd.play(data, sr, device=_device(cfg.output_device))
    sd.wait()
