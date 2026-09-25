"""The words you actually say.

Two halves, both local, both in dictionary.json next to the app:

* **Words** — names and jargon fed to Whisper as its initial prompt so they come
  back spelled your way. You add some by hand; the rest LocalFlow picks up by
  noticing which uncommon words you keep saying. Anything it learns on its own
  is flagged until you look at it, so the menu can ask "keep these?".
* **Fixes** — literal replacements applied to the finished text, for the times
  Whisper reliably mishears something ("super base" → "Supabase").
"""

import json
import os
import re
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DICT_PATH = os.path.join(HERE, "dictionary.json")

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’]*(?:-[A-Za-z0-9'’]+)*")
SENTENCE_END = ".!?…\n"

# macOS ships a word list; anything in it is ordinary English that Whisper
# already knows, so only shouted (BTC), CamelCase (OpenSearch) or
# capitalised-mid-sentence (…the Radar bot) forms of those are worth learning.
SYSTEM_WORDS_PATH = "/usr/share/dict/words"
_system_words = None


def ordinary_words():
    global _system_words
    if _system_words is None:
        try:
            with open(SYSTEM_WORDS_PATH) as f:
                _system_words = frozenset(w.strip().lower() for w in f if w.strip())
        except OSError:  # no word list on this machine — lean on STOPWORDS alone
            _system_words = frozenset()
    return _system_words


def _stems(w):
    """Crude de-inflection — the system list holds base words ("fund", not "funding")."""
    yield w
    if len(w) > 3 and w.endswith("s"):
        yield w[:-1]
        if w.endswith("es"):
            yield w[:-2]
        if w.endswith("ies"):
            yield w[:-3] + "y"
    if len(w) > 3 and w.endswith("ed"):
        yield w[:-1]
        yield w[:-2]
        if len(w) > 4 and w[-3] == w[-4]:
            yield w[:-3]
    if len(w) > 4 and w.endswith("ing"):
        yield w[:-3]
        yield w[:-3] + "e"
        if len(w) > 5 and w[-4] == w[-5]:
            yield w[:-4]
    if len(w) > 4 and w.endswith(("ly", "er")):
        yield w[:-2]
        yield w[:-1]
    if len(w) > 5 and w.endswith("est"):
        yield w[:-3]
        yield w[:-2]


def is_ordinary(word):
    """Plain English (in any obvious inflection), as opposed to a name or jargon."""
    known = ordinary_words()
    return any(stem in known for stem in _stems(word.lower()))


def _sentence_initial(text, start):
    """Is the word at this offset the first one of a sentence?"""
    for ch in reversed(text[:start]):
        if ch.isspace() or ch in "\"'(“‘":
            continue
        return ch in SENTENCE_END
    return True

# Filler and the commonest words, dropped before the word list above is even
# consulted — Whisper knows them, and they would crowd the prompt out of its
# ~220-token window.
STOPWORDS = set("""
a about above after again against all almost also always am an and another any anybody anyone anything are
aren't around as at away back be because been before being below best better between big both but by call
came can can't cannot come could couldn't did didn't do does doesn't doing don't done down during each
either else enough even ever every everybody everyone everything few find first for found from get gets
getting give given go goes going gone good got great had hadn't has hasn't have haven't having he her here
hers herself him himself his how however i i'm i've if in into is isn't it it's its itself just keep kind
know known last least less let let's like little long look looking made make makes making many may maybe me
mean means might mine more most much must my myself need needs never new next no nobody none nor not nothing
now of off often oh ok okay old on once one only or other others otherwise ought our ours ourselves out over
own part people perhaps please put quite rather really right said same saw say says see seen set shall she
should shouldn't since so some somebody someone something sometimes soon still such sure take taken tell than
that that's the their theirs them themselves then there these they thing things think this those though
thought three through thus time to today together too took two under until up upon us use used using very
want was wasn't way we well went were weren't what when where whether which while who whole whom why will
with within without won't would wouldn't yeah yes yet you you're your yours yourself
um uh umm uhh hmm mmm like basically actually literally anyway alright yep nope gonna wanna kinda sorta
""".split())


def _key(word):
    return word.lower()


class Dictionary:
    def __init__(self, path=DICT_PATH, promote_at=3, prompt_limit=40):
        self.path = path
        self.promote_at = promote_at      # times a word must be said before it's learned
        self.prompt_limit = prompt_limit  # words handed to Whisper
        self._lock = threading.RLock()
        self.words = {}   # key -> {display, count, state, source, reviewed, added, last}
        self.fixes = {}   # lowercased heard text -> replacement
        self.meta = {}
        self._load()

    # ---------- storage ----------

    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.words = data.get("words", {})
        self.fixes = data.get("fixes", {})
        self.meta = data.get("meta", {})

    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"version": 1, "words": self.words, "fixes": self.fixes, "meta": self.meta},
                          f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, self.path)

    # ---------- building the vocabulary ----------

    def add(self, word, source="manual"):
        """Add (or re-enable) a word you typed in yourself."""
        word = word.strip()
        if not word:
            return False
        with self._lock:
            e = self.words.get(_key(word))
            if e is None:
                self.words[_key(word)] = {
                    "display": word, "count": 0, "state": "on", "source": source,
                    "reviewed": True, "added": time.time(), "last": time.time(),
                }
            else:
                e.update(display=word, state="on", reviewed=True, source=source)
            self.save()
            return True

    def set_state(self, key, state):
        with self._lock:
            e = self.words.get(key)
            if not e:
                return
            e["state"] = state
            e["reviewed"] = True
            self.save()

    def forget_learned(self):
        """Drop everything LocalFlow taught itself, keeping what you added."""
        with self._lock:
            self.words = {k: e for k, e in self.words.items() if e.get("source") == "manual"}
            self.save()

    def _candidate(self, token, sentence_initial=False):
        """Is this token worth learning? Everyday words and stray letters aren't."""
        if _key(token) in STOPWORDS or not any(c.isalpha() for c in token) or len(token) < 2:
            return False
        shouted = token.isupper()                          # BTC, CJ
        camel = token[1:] != token[1:].lower()             # OpenSearch, MacBook
        named = token[:1].isupper() and not sentence_initial   # …the Radar bot
        if is_ordinary(token):
            return shouted or camel or named
        return len(token) >= 3 or shouted

    def learn(self, text, save=True):
        """Count the words in a finished dictation; promote the ones that stick.

        Returns the words promoted by this call (newly learned).
        """
        promoted = []
        with self._lock:
            text = text or ""
            # A word counts once per dictation, however often it appears in it.
            # "Said 3+ times" means across three dictations; counting occurrences
            # let one looped transcript ("Smithson" x111) learn junk outright.
            seen = set()
            for m in WORD_RE.finditer(text):
                token = m.group(0)
                if not self._candidate(token, _sentence_initial(text, m.start())):
                    continue
                k = _key(token)
                if k in seen:
                    continue
                seen.add(k)
                e = self.words.get(k)
                if e is None:
                    e = self.words[k] = {
                        "display": token, "count": 0, "state": "counting", "source": "learned",
                        "reviewed": False, "added": time.time(), "last": time.time(),
                    }
                e["count"] += 1
                e["last"] = time.time()
                if token[:1].isupper():
                    e["display"] = token  # prefer the capitalised spelling
                if e["state"] == "counting" and e["count"] >= self.promote_at:
                    e["state"] = "on"
                    promoted.append(e["display"])
            if save:
                self.save()
        return promoted

    def seed(self, texts):
        """First run: mine the dictations already on disk."""
        with self._lock:
            for t in texts:
                self.learn(t, save=False)
            self.meta["seeded"] = time.time()
            self.save()

    def import_words(self, words):
        """One-off import of the old config.json vocabulary list."""
        with self._lock:
            if self.meta.get("imported_config_vocab"):
                return
            for w in words:
                self.add(w)
            self.meta["imported_config_vocab"] = True
            self.save()

    # ---------- using it ----------

    def prompt_words(self, limit=None):
        """Words for Whisper's initial prompt: yours first, then most-said."""
        limit = self.prompt_limit if limit is None else limit
        with self._lock:
            on = [e for e in self.words.values() if e["state"] == "on"]
        on.sort(key=lambda e: (e.get("source") != "manual", -e.get("count", 0), e["display"].lower()))
        return [e["display"] for e in on[:limit]]

    def apply_casing(self, text):
        """Restore the casing of words we already know how to spell.

        Whisper and the cleanup model both hand back "supabase" or "opensearch"
        from time to time. The dictionary already holds the form you use, so
        this puts it back. Only entries that actually carry capitals are used,
        and only on whole words.
        """
        if not text:
            return text
        with self._lock:
            forms = [e["display"] for e in self.words.values()
                     if e.get("state") == "on" and any(c.isupper() for c in e["display"])]
        for display in sorted(forms, key=len, reverse=True):
            if display in text:
                continue  # already spelled your way
            pattern = rf"(?<!\w){re.escape(display)}(?!\w)"
            text = re.sub(pattern, lambda _m, d=display: d, text, flags=re.IGNORECASE)
        return text

    def listing(self, limit=None):
        """(key, entry) for the menu — on-words first, most-said at the top."""
        with self._lock:
            rows = [(k, e) for k, e in self.words.items() if e["state"] in ("on", "off")]
        rows.sort(key=lambda kv: (kv[1]["state"] != "on", kv[1].get("source") != "manual",
                                  -kv[1].get("count", 0), kv[1]["display"].lower()))
        return rows[:limit] if limit else rows

    def unreviewed(self):
        """Learned words you haven't looked at yet."""
        with self._lock:
            rows = [e for e in self.words.values()
                    if e["state"] == "on" and not e.get("reviewed") and e.get("source") == "learned"]
        rows.sort(key=lambda e: -e.get("count", 0))
        return rows

    def review(self, keep_displays):
        """Answer to 'keep these?': the ones you kept stay, the rest go quiet."""
        keep = {_key(w) for w in keep_displays}
        with self._lock:
            for e in self.unreviewed():
                e["state"] = "on" if _key(e["display"]) in keep else "off"
                e["reviewed"] = True
            self.save()

    # ---------- fixes ----------

    def add_fix(self, heard, write):
        heard, write = heard.strip(), write.strip()
        if not heard or not write:
            return False
        with self._lock:
            self.fixes[heard.lower()] = write
            self.save()
        self.add(write)
        return True

    def remove_fix(self, heard):
        with self._lock:
            self.fixes.pop(heard.lower(), None)
            self.save()

    def apply_fixes(self, text):
        """Replace misheard phrases, matching whole words and ignoring case."""
        if not text:
            return text
        with self._lock:
            fixes = sorted(self.fixes.items(), key=lambda kv: -len(kv[0]))
        for heard, write in fixes:
            # lambda, not a template string — a replacement is literal text
            text = re.sub(rf"(?<!\w){re.escape(heard)}(?!\w)", lambda _m, w=write: w,
                          text, flags=re.IGNORECASE)
        return text
