"""Every dictation's audio is saved before it's processed.

If transcription hangs or fails, what you said isn't lost: the WAV stays in
recordings/failed/ with a small JSON note, and the menu can retry it. A
successful dictation deletes its file straight away, so nothing piles up.
Failed ones are pruned after a week (and capped at 50).
"""

import json
import os
import shutil
import time
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "recordings")
PENDING = os.path.join(ROOT, "pending")
FAILED = os.path.join(ROOT, "failed")
KEEP_DAYS = 7
KEEP_MAX = 50


def save(audio, samplerate=16000, root=ROOT):
    pending = os.path.join(root, "pending")
    os.makedirs(pending, exist_ok=True)
    path = os.path.join(pending, time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time()*1000)%1000:03d}.wav")
    pcm = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(pcm.tobytes())
    return path


def done(path):
    """Processed fine (or deliberately dropped): the audio isn't needed."""
    try:
        os.remove(path)
    except OSError:
        pass


def fail(path, reason, app=None, root=ROOT):
    """Keep the audio for a retry, with a note of what went wrong."""
    failed = os.path.join(root, "failed")
    os.makedirs(failed, exist_ok=True)
    dest = os.path.join(failed, os.path.basename(path))
    try:
        shutil.move(path, dest)
    except OSError:
        return None
    with open(dest[:-4] + ".json", "w") as f:
        json.dump({"reason": str(reason), "app": app, "failed_at": time.time()}, f)
    prune(root)
    return dest


def listing(root=ROOT):
    """Failed dictations, newest first: [{path, when, seconds, reason, app}]."""
    failed = os.path.join(root, "failed")
    out = []
    if not os.path.isdir(failed):
        return out
    for name in os.listdir(failed):
        if not name.endswith(".wav"):
            continue
        path = os.path.join(failed, name)
        meta = {}
        try:
            with open(path[:-4] + ".json") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            pass
        try:
            with wave.open(path) as w:
                secs = w.getnframes() / float(w.getframerate())
        except Exception:
            secs = 0.0
        out.append({"path": path, "when": os.path.getmtime(path), "seconds": secs,
                    "reason": meta.get("reason", ""), "app": meta.get("app")})
    return sorted(out, key=lambda e: -e["when"])


def discard(path):
    for p in (path, path[:-4] + ".json"):
        try:
            os.remove(p)
        except OSError:
            pass


def prune(root=ROOT):
    items = listing(root)
    cutoff = time.time() - KEEP_DAYS * 86400
    for i, e in enumerate(items):
        if e["when"] < cutoff or i >= KEEP_MAX:
            discard(e["path"])


def clear_pending(root=ROOT):
    """At launch: anything left in pending/ was interrupted by a crash or quit."""
    pending = os.path.join(root, "pending")
    if not os.path.isdir(pending):
        return 0
    n = 0
    for name in os.listdir(pending):
        if name.endswith(".wav"):
            fail(os.path.join(pending, name), "LocalFlow quit or crashed mid-dictation", root=root)
            n += 1
    return n
