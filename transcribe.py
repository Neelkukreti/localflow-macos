"""Local speech-to-text using MLX Whisper (Apple Silicon GPU).

The model is loaded lazily on first use and cached for the process lifetime.

Decoding is deliberately pinned down. Whisper's defaults are tuned for offline
batch transcription, not for a dictation box you're waiting on:

  * temperature fallback re-decodes the same audio up to six times whenever the
    output looks poor by its own heuristics. On a quiet clip or a noisy room
    that turns a 2-second job into a 20-second one, which reads as the app
    hanging. temperature=0 does one pass, full stop.
  * condition_on_previous_text feeds the model its own output, which is how it
    ends up repeating a phrase forever on near-silence.

Both are overridable per install via whisper.decode in config.json.
"""

import numpy as np

# Whisper only reads roughly the last 224 tokens of initial_prompt, so a longer
# one costs tokens for nothing and biases the decoder harder towards rambling.
PROMPT_MAX_CHARS = 420

DECODE_DEFAULTS = {
    "temperature": 0.0,
    "condition_on_previous_text": False,
}

_loaded_model = None
_loaded_name = None


def _ensure_model(name: str):
    """MLX Whisper loads/caches weights internally per path, so we just import."""
    global _loaded_model, _loaded_name
    import mlx_whisper  # noqa: F401  (import cost only)
    _loaded_name = name
    _loaded_model = mlx_whisper
    return _loaded_model


def preload(cfg: dict):
    """Load the weights now, so the first dictation doesn't pay ~5s for it."""
    try:
        model_name = cfg.get("model", "mlx-community/whisper-large-v3-turbo")
        mlx_whisper = _ensure_model(model_name)
        mlx_whisper.transcribe(
            np.zeros(1600, dtype=np.float32), path_or_hf_repo=model_name,
            language="en", **DECODE_DEFAULTS,
        )
        return True
    except Exception:
        return False


def transcribe(audio: np.ndarray, cfg: dict, prompt: str = "") -> str:
    """audio: float32 mono PCM in [-1, 1] at 16 kHz.
    prompt: recent text/vocabulary that biases spelling of names and jargon."""
    model_name = cfg.get("model", "mlx-community/whisper-large-v3-turbo")
    language = cfg.get("language") or None

    mlx_whisper = _ensure_model(model_name)
    audio = np.asarray(audio, dtype=np.float32).flatten()

    kwargs = {"path_or_hf_repo": model_name}
    if language:
        kwargs["language"] = language
    kwargs.update(DECODE_DEFAULTS)
    kwargs.update(cfg.get("decode") or {})

    if prompt:
        prompt = prompt[-PROMPT_MAX_CHARS:]
        text = (mlx_whisper.transcribe(audio, initial_prompt=prompt, **kwargs).get("text") or "").strip()
        # On near-silence Whisper can echo the prompt back; retry without it.
        if not (len(text) > 15 and text.lower() in prompt.lower()):
            return text
    result = mlx_whisper.transcribe(audio, **kwargs)
    return (result.get("text") or "").strip()
