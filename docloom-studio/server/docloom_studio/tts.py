"""Text-to-speech for podcast audio.

Kokoro backend: local, no API key, high quality. It runs only when `kokoro`
and `soundfile` are installed and the model weights have been fetched (the
first synthesis downloads them from Hugging Face). Output is a single WAV, so
no ffmpeg is required. The backend is selected by the `provider.tts` setting so
other engines can slot in later."""

from __future__ import annotations

import asyncio
from pathlib import Path

SAMPLE_RATE = 24000  # Kokoro native rate
_GAP_SECONDS = 0.35  # pause between speakers


class TtsError(RuntimeError):
    """Raised when synthesis can't run (backend missing, model unavailable, …).
    Callers treat it as 'audio not produced' — the transcript still ships."""


async def synthesize_podcast(script: dict, out_path: Path, cfg: dict | None) -> float:
    """Synthesize a two-host script to a WAV at out_path. Returns duration (s).
    Runs the blocking TTS off the event loop."""
    cfg = cfg or {}
    kind = cfg.get("kind", "kokoro")
    if kind != "kokoro":
        raise TtsError(f"unsupported TTS backend {kind!r}")
    return await asyncio.to_thread(_kokoro_synthesize, script, Path(out_path), cfg)


def _kokoro_synthesize(script: dict, out_path: Path, cfg: dict) -> float:
    try:
        import numpy as np
        import soundfile as sf
        from kokoro import KPipeline
    except ImportError as e:
        raise TtsError(
            "Kokoro TTS is not installed. Run `pip install kokoro soundfile` "
            "(the first run downloads the model weights)."
        ) from e

    lang = cfg.get("lang", "a")  # 'a' = American English
    voices = {"A": cfg.get("voice_a", "af_heart"),
              "B": cfg.get("voice_b", "am_michael")}
    pipeline = KPipeline(lang_code=lang)
    gap = np.zeros(int(SAMPLE_RATE * _GAP_SECONDS), dtype=np.float32)

    parts: list = []
    for turn in script.get("turns", []):
        text = (turn.get("text") or "").strip()
        if not text:
            continue
        voice = voices.get(turn.get("speaker", "A"), voices["A"])
        for _, _, audio in pipeline(text, voice=voice):
            parts.append(np.asarray(audio, dtype=np.float32))
        parts.append(gap)

    if not parts:
        raise TtsError("no speech was synthesized")
    full = np.concatenate(parts)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), full, SAMPLE_RATE)
    return float(len(full)) / SAMPLE_RATE
