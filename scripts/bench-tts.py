#!/usr/bin/env python3
"""Benchmark Piper TTS voices."""
import subprocess, time, os, json, wave
from pathlib import Path

VOICES_DIR = Path.home() / "piper_models"
TEST_TEXT = "The quick brown fox jumps over the lazy dog. This is a test of the Piper text-to-speech system."

def get_audio_duration(wav_path):
    with wave.open(str(wav_path), 'rb') as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / float(rate)

def bench_voice(voice_name, model_path):
    out_wav = Path(f"/tmp/tts_{voice_name}.wav")
    cmd = [
        "/home/parteek/.local/bin/piper",
        "--model", str(model_path),
        "--output_file", str(out_wav)
    ]
    start = time.time()
    result = subprocess.run(cmd, input=TEST_TEXT.encode(), capture_output=True)
    elapsed = time.time() - start
    if result.returncode != 0:
        return {"voice": voice_name, "error": result.stderr.decode()[:200]}
    if not out_wav.exists():
        return {"voice": voice_name, "error": "no output file"}
    duration = get_audio_duration(out_wav)
    rtf = elapsed / duration if duration > 0 else None
    return {
        "voice": voice_name,
        "model_size_mb": round(model_path.stat().st_size / 1_048_576, 1),
        "synthesis_time_s": round(elapsed, 3),
        "audio_duration_s": round(duration, 3),
        "rtf": round(rtf, 3) if rtf else None,
        "output_file": str(out_wav)
    }

def main():
    voices = {
        "en_US-lessac-medium": VOICES_DIR / "en_US-lessac-medium" / "model.onnx",
        "en_US-lessac-high": VOICES_DIR / "en_US-lessac-high" / "model.onnx",
        "en_US-amy-medium": VOICES_DIR / "en_US-amy-medium" / "model.onnx",
        "en_GB-alan-medium": VOICES_DIR / "en_GB-alan-medium" / "model.onnx",
    }
    results = []
    for name, path in voices.items():
        if not path.exists():
            print(f"Skipping {name}: model not found")
            continue
        print(f"Benchmarking {name}...")
        res = bench_voice(name, path)
        results.append(res)
        print(f"  {res}")
    # Save results
    out_path = Path("/tmp/tts_bench_results.json")
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved to {out_path}")
    # Print summary table
    print("\nSummary:")
    print(f"{'Voice':<25} {'Size(MB)':>10} {'Synth(s)':>10} {'Audio(s)':>10} {'RTF':>8}")
    for r in results:
        if "error" in r:
            print(f"{r['voice']:<25} ERROR: {r['error']}")
        else:
            print(f"{r['voice']:<25} {r['model_size_mb']:>10} {r['synthesis_time_s']:>10} {r['audio_duration_s']:>10} {r['rtf']:>8}")

if __name__ == "__main__":
    main()