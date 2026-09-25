"""Local speech-to-text using MLX Whisper (Apple Silicon GPU).

The model is loaded lazily on first use and cached for the process lifetime.

Decoding is bounded, not pinned. Whisper's temperature fallback does two jobs:

  * the bad one — it re-decodes the same audio up to six times, at rising
    temperatures, whenever its heuristics dislike the output. On a quiet clip
    that turned a 5s job into a 113s one, which reads as the app hanging.
  * the essential one — it is the ONLY thing that rescues a repetition loop.
    When a pass comes back as "The The The The…" its compression ratio trips,
    and a warmer retry breaks out of the loop.

temperature=0 with no fallback kills the hang and the rescue together, and
every dictation came back as a loop (23 Sep, reverted). So we keep the
fallback but cap it at three temperatures: worst case three passes, not six.
_collapse_loops() is the backstop for anything that still gets through.

condition_on_previous_text=False stops the model feeding on its own output
across 30s windows. Everything is overridable via whisper.decode in config.json.
"""

import re

import numpy as np

# Whisper only reads roughly the last 224 tokens of initial_prompt, so a longer
# one costs tokens for nothing and biases the decoder harder towards rambling.
PROMPT_MAX_CHARS = 420

DECODE_DEFAULTS = {
    "temperature": (0.0, 0.2, 0.4),       # bounded fallback: ≤3 passes, never 6
    "compression_ratio_threshold": 2.4,   # what trips a retry on a repetition loop
    "condition_on_previous_text": False,
}


def _norm(tok):
    return re.sub(r"[^\w']", "", tok.lower())


def _collapse_loops(text: str) -> str:
    """Backstop for a repetition loop that survived the fallback.

    Collapses a word repeated 4+ times in a row, or a 2-6 word phrase repeated
    3+ times, to a single occurrence — so a stuck decode can't paste a wall of
    "The The The". Token-based rather than a regex, because a backreference
    trips over the missing trailing space on the last repetition. Three of the
    same word is left alone ("no no no" is real speech); four isn't.
    """
    original = (text or "").strip()
    toks = original.split()
    if len(toks) < 4:
        return original
    norm = [_norm(t) for t in toks]
    collapsed_any = False
    changed = True
    while changed:
        changed = False
        for n in range(1, 7):
            need = 4 if n == 1 else 3
            out_t, out_n, i = [], [], 0
            while i < len(toks):
                gram = norm[i:i + n]
                if len(gram) == n and all(gram):
                    reps, j = 1, i + n
                    while norm[j:j + n] == gram:
                        reps, j = reps + 1, j + n
                    if reps >= need:
                        out_t += toks[i:i + n]
                        out_n += gram
                        i, changed, collapsed_any = j, True, True
                        continue
                out_t.append(toks[i])
                out_n.append(norm[i])
                i += 1
            toks, norm = out_t, out_n
    # Nothing looped: hand back the text untouched. Rejoining tokens would
    # flatten the paragraph breaks "new paragraph" puts in.
    return " ".join(toks).strip() if collapsed_any else original


def looks_like_loop(text: str) -> bool:
    """True when one token dominates the text — a decode stuck on repeat."""
    words = [w for w in (_norm(t) for t in (text or "").split()) if w]
    if len(words) < 5:
        return False
    top = max(words.count(w) for w in set(words))
    return top / len(words) > 0.45


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
            language="en", temperature=0.0,
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
        # A prompt can pull Whisper into echoing it or looping on it; if either
        # happened, try once more without the prompt before giving up on it.
        echoed = len(text) > 15 and text.lower() in prompt.lower()
        if not echoed and not looks_like_loop(text):
            return _only_if_words(_collapse_loops(text))
    result = mlx_whisper.transcribe(audio, **kwargs)
    text = (result.get("text") or "").strip()
    if looks_like_loop(text) and len(set(text.split())) <= 3:
        return ""
    return _only_if_words(_collapse_loops(text))


def _only_if_words(text: str) -> str:
    """Noise and near-silence come back as a bare "!" or "..." — never pasted."""
    return text if re.search(r"[^\W_]", text or "") else ""
