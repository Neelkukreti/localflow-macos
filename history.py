"""Rolling memory of past dictations, used as context so Whisper and the
cleanup model spell names/jargon the way you do and keep continuity.

Stored locally in history.jsonl next to the app; nothing leaves the Mac.
"""

import os
import json
import time
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY_PATH = os.path.join(HERE, "history.jsonl")
KEEP = 500  # entries kept on disk


class History:
    def __init__(self, path=HISTORY_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.entries = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                return [json.loads(line) for line in f if line.strip()][-KEEP:]
        except (OSError, ValueError):
            return []

    def add(self, text, app=None):
        entry = {"t": time.time(), "text": text, "app": app}
        with self._lock:
            self.entries.append(entry)
            if len(self.entries) > KEEP:
                self.entries = self.entries[-KEEP:]
                self._rewrite()
            else:
                with open(self.path, "a") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _rewrite(self):
        with open(self.path, "w") as f:
            for e in self.entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def clear(self):
        with self._lock:
            self.entries = []
            self._rewrite()

    def recent(self, max_chars, app=None):
        """Most recent dictations (oldest first) fitting in max_chars.
        Dictations into the same app come first when app is given."""
        pool = list(reversed(self.entries))
        if app:
            pool = [e for e in pool if e.get("app") == app] + [e for e in pool if e.get("app") != app]
        picked, used = [], 0
        for e in pool:
            n = len(e["text"]) + 1
            if used + n > max_chars:
                break
            picked.append(e)
            used += n
        picked.sort(key=lambda e: e["t"])
        return [e["text"] for e in picked]
