"""Whisper in its own process, so a hung transcription can be killed.

MLX inference can wedge (a GPU stall, a runaway decode). In a thread there is
no safe way to stop it: the app could only abandon it, and it kept running.
A process can be killed and started again, so LocalFlow runs Whisper here and
talks to it over pipes (worker_client.py).

Protocol, one JSON object per line:
  in : {"id": 7, "wav": "/path.wav", "cfg": {...}, "prompt": "..."}
       {"id": 8, "op": "sleep", "seconds": 60}          (tests: simulate a hang)
  out: {"ready": true}                                   once the model is loaded
       {"id": 7, "ok": true, "text": "..."} | {"id": 7, "ok": false, "error": "..."}

Libraries print to stdout (download progress and the like), which would corrupt
the protocol, so the real stdout is kept for replies and fd 1 is pointed at stderr.
"""

import json
import os
import sys
import time
import wave

import numpy as np

_proto = os.fdopen(os.dup(1), "w", buffering=1)
os.dup2(2, 1)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import transcribe  # noqa: E402  (after the fd juggling, on purpose)


def reply(obj):
    _proto.write(json.dumps(obj) + "\n")
    _proto.flush()


def read_wav(path):
    with wave.open(path) as w:
        frames = w.readframes(w.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def main():
    cfg = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    transcribe.preload(cfg)
    reply({"ready": True})
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid = req.get("id")
        if req.get("op") == "sleep":
            time.sleep(float(req.get("seconds", 1)))
            reply({"id": rid, "ok": True, "text": ""})
            continue
        try:
            text = transcribe.transcribe(read_wav(req["wav"]), req.get("cfg") or {},
                                         prompt=req.get("prompt") or "")
            reply({"id": rid, "ok": True, "text": text})
        except Exception as e:
            reply({"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    main()
