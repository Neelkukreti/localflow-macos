"""Snippets: a spoken trigger phrase that expands into saved text.

"my calendar link" -> https://cal.com/…   ·   "sign off" -> a whole signature.

Deliberately separate from dictionary fixes: a fix repairs a word Whisper got
wrong, a snippet replaces something you said correctly with something longer.
Expansion runs after cleanup and after fixes, so the cleanup model never sees
(and so can never rewrite) the payload.
"""

import os
import json
import re
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SNIPPETS_PATH = os.path.join(HERE, "snippets.json")


def _norm(trigger):
    """Triggers match on words only, so punctuation from cleanup can't block them."""
    return re.sub(r"[^\w\s]", "", (trigger or "").strip().lower())


class Snippets:
    def __init__(self, path=SNIPPETS_PATH):
        self.path = path
        self._lock = threading.Lock()
        self.items = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return {}
        out = {}
        for key, e in (data.get("snippets") or {}).items():
            if isinstance(e, str):  # tolerate a hand-written {"trigger": "text"} file
                e = {"text": e, "trigger": key, "state": "on"}
            out[_norm(key)] = e
        return out

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"snippets": self.items}, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, self.path)

    def add(self, trigger, text):
        key = _norm(trigger)
        if not key or not (text or "").strip():
            return False
        with self._lock:
            self.items[key] = {"trigger": trigger.strip(), "text": text,
                               "state": "on", "count": 0}
            self._save()
        return True

    def remove(self, key):
        with self._lock:
            if self.items.pop(key, None) is None:
                return False
            self._save()
        return True

    def set_state(self, key, state):
        with self._lock:
            if key in self.items:
                self.items[key]["state"] = state
                self._save()

    def listing(self):
        return sorted(self.items.items(), key=lambda kv: kv[1].get("trigger", kv[0]).lower())

    def expand(self, text):
        """Replace every enabled trigger phrase found in text. Longest first, so
        'sign off formal' wins over 'sign off'."""
        if not text or not self.items:
            return text
        with self._lock:
            active = [(k, e) for k, e in self.items.items() if e.get("state", "on") == "on"]
        used = []
        for key, entry in sorted(active, key=lambda kv: -len(kv[0])):
            # Allow any punctuation/whitespace between the spoken words.
            pattern = r"(?<!\w)" + r"[\W_]+".join(re.escape(w) for w in key.split()) + r"(?!\w)"
            new, n = re.subn(pattern, lambda _m, t=entry["text"]: t, text,
                             flags=re.IGNORECASE)
            if n:
                text, _ = new, used.append(key)
        if used:
            with self._lock:
                for key in used:
                    if key in self.items:
                        self.items[key]["count"] = (self.items[key].get("count") or 0) + 1
                self._save()
        return text
