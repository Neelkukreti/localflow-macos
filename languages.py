"""Language modes, including Hinglish, plus Devanagari -> Roman transliteration.

Whisper decides the *script* it writes in, not just the language. Dictate
code-switched Hindi/English and it will happily hand back देवनागरी, which is
not how Hinglish gets typed. Two levers fix that:

  1. a Roman-script seed in the initial_prompt, which biases the decoder
     towards Roman output for the whole clip (same trick the dictionary uses);
  2. romanise(), a fallback transliteration for anything that slips through.

Modes: "en" | "hi" | "hinglish" | "auto".
"""

import re

# A Roman-Hinglish seed: short, everyday, and in the register we actually dictate.
HINGLISH_SEED = (
    "Haan yaar, market abhi sideways hai, funding rate positive hai, "
    "thoda wait karte hain phir entry lenge."
)

MODES = {
    # mode: (whisper language, seed prompt, romanise output?)
    "en": ("en", "", False),
    "hi": ("hi", "", True),
    "hinglish": (None, HINGLISH_SEED, True),
    "auto": (None, "", False),
}


def resolve(whisper_cfg):
    """(language, seed prompt, romanise) for the configured mode.

    Falls back to the older `whisper.language` key so an existing config keeps
    behaving exactly as before.
    """
    mode = (whisper_cfg.get("language_mode") or "").strip().lower()
    if mode in MODES:
        lang, seed, roman = MODES[mode]
        if not whisper_cfg.get("romanize", True):
            roman = False
        return lang, seed, roman
    return whisper_cfg.get("language") or None, "", False


# ---------------------------------------------------------------- Devanagari

_VOWELS = {
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo",
    "ऋ": "ri", "ॠ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
    "ऍ": "e", "ऎ": "e", "ऑ": "o", "ऒ": "o",
}

_MATRAS = {
    "ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri", "ॄ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ॅ": "e", "ॆ": "e", "ॊ": "o",
}

_CONSONANTS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "ळ": "l",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
    # Nukta forms, both precomposed and as consonant + U+093C.
    "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f", "य़": "y",
}

_NUKTA = {"क": "q", "ख": "kh", "ग": "g", "ज": "z", "ड": "r", "ढ": "rh", "फ": "f", "य": "y"}

_DIGITS = {chr(0x0966 + i): str(i) for i in range(10)}
_SIGNS = {"।": ".", "॥": ".", "ॐ": "om", "ऽ": ""}

_VIRAMA, _ANUSVARA, _CHANDRABINDU, _VISARGA, _NUKTA_MARK = "्", "ं", "ँ", "ः", "़"

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
# Nasal before a labial reads as 'm' (हिंदी -> hindi, but बंबई -> bambai).
_LABIALS = set("पफबभम")


def _tokenise(text):
    """Devanagari -> a flat list of tokens.

    ("lit", s)                 anything we pass through untouched
    ("syl", cons, vowel, nas)  a consonant; vowel None means the inherent schwa,
                               "" means a dead consonant (virama)
    ("vow", vowel, nas)        an independent vowel
    """
    tokens, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]

        if ch in _DIGITS or ch in _SIGNS:
            tokens.append(("lit", _DIGITS.get(ch) or _SIGNS[ch]))
            i += 1
            continue

        if ch in _VOWELS:
            i += 1
            nas, i = _read_nasal(text, i)
            tokens.append(("vow", _VOWELS[ch], nas))
            continue

        nukta = text[i + 1:i + 2] == _NUKTA_MARK
        if ch in _CONSONANTS or (nukta and ch in _NUKTA):
            if nukta and ch in _NUKTA:
                cons, i = _NUKTA[ch], i + 2
            else:
                cons, i = _CONSONANTS[ch], i + 1
            nxt = text[i] if i < n else ""
            if nxt == _VIRAMA:
                vowel, i = "", i + 1
            elif nxt in _MATRAS:
                vowel, i = _MATRAS[nxt], i + 1
            else:
                vowel = None  # inherent schwa, may be deleted later
            nas, i = _read_nasal(text, i)
            tokens.append(("syl", cons, vowel, nas))
            continue

        if ch in (_ANUSVARA, _CHANDRABINDU, _VISARGA, _VIRAMA, _NUKTA_MARK):
            i += 1  # stray mark with nothing to attach to
            continue

        tokens.append(("lit", ch))
        i += 1
    return tokens


def _read_nasal(text, i):
    """Consume a nasal/visarga sitting on the syllable we just read."""
    ch = text[i:i + 1]
    if ch in (_ANUSVARA, _CHANDRABINDU):
        nxt = text[i + 1:i + 2]
        return ("m" if nxt in _LABIALS else "n"), i + 1
    if ch == _VISARGA:
        return "h", i + 1
    return "", i


def _delete_schwa(tokens):
    """Hindi drops the inherent 'a' word-finally and between syllables.

    करना is karna, not karanaa. The rule we apply: a schwa is dropped at the end
    of a word, or mid-word when the next syllable carries a real vowel of its
    own. A schwa before a dead consonant (नमस्ते) or an independent vowel
    (बंबई) stays, which is what keeps namaste and bambai readable.
    """
    words, run = [], []
    for tok in tokens:  # a "word" is a run of Devanagari syllables
        if tok[0] == "lit":
            words.append(run)
            words.append([tok])
            run = []
        else:
            run.append(tok)
    words.append(run)

    out = []
    for word in words:
        syls = [t for t in word if t[0] in ("syl", "vow")]
        for idx, tok in enumerate(word):
            if tok[0] != "syl" or tok[2] is not None:
                out.append(tok)
                continue
            last = idx == len(word) - 1
            nxt = word[idx + 1] if not last else None
            drop = last or (
                idx > 0 and nxt is not None and nxt[0] == "syl"
                and nxt[2] not in (None, "")
            )
            out.append(("syl", tok[1], "" if drop else "a", tok[3]))
        del syls
    return out


# Word-final long vowels are written short in Hinglish (abhi, hindi, kya),
# and a schwa meeting an independent vowel makes a diphthong (bambai).
_FINAL = ((re.compile(r"aee(?![a-z])"), "ai"), (re.compile(r"aoo(?![a-z])"), "au"),
          (re.compile(r"aa(?![a-z])"), "a"), (re.compile(r"ee(?![a-z])"), "i"),
          (re.compile(r"oo(?![a-z])"), "u"))


def romanise(text):
    """Transliterate Devanagari into the Roman spelling Hinglish is typed in.

    Every non-Devanagari character is passed through, so mixed-script output
    ("BTC का price") comes back as one readable line.
    """
    if not text or not _DEVANAGARI.search(text):
        return text

    parts = []
    for tok in _delete_schwa(_tokenise(text)):
        if tok[0] == "lit":
            parts.append(tok[1])
        elif tok[0] == "vow":
            parts.append(tok[1] + tok[2])
        else:
            parts.append(tok[1] + (tok[2] or "") + tok[3])
    out = "".join(parts)

    out = re.sub(r"aee(?=[^a-z]|$)", "ai", out)
    out = re.sub(r"aoo(?=[^a-z]|$)", "au", out)
    for pattern, repl in _FINAL:
        out = pattern.sub(repl, out)
    return re.sub(r"([bcdfghjklmnpqrstvwxyz])\1{2,}", r"\1\1", out)
