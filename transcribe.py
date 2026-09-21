"""Local speech-to-text using MLX Whisper (Apple Silicon GPU).

The model is loaded lazily on first use and cached for the process lifetime.
"""

import numpy as np

_loaded_model = None
_loaded_name = None


def _ensure_model(name: str):
    """MLX Whisper loads/caches weights internally per path, so we just import."""
    global _loaded_model, _loaded_name
    import mlx_whisper  # noqa: F401  (import cost only)
    _loaded_name = name
    _loaded_model = mlx_whisper
    return _loaded_model


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

    if prompt:
        text = (mlx_whisper.transcribe(audio, initial_prompt=prompt, **kwargs).get("text") or "").strip()
        # On near-silence Whisper can echo the prompt back; retry without it.
        if not (len(text) > 15 and text.lower() in prompt.lower()):
            return text
    result = mlx_whisper.transcribe(audio, **kwargs)
    return (result.get("text") or "").strip()
