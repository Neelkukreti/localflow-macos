"""Headless checks for the features added on top of plain dictation:
voices, snippets, language modes/romanisation, Command Mode parsing,
cleanup levels and the microphone fallback order.

No mic, no Ollama, no clicks. Run: ./.venv/bin/python test_features.py
"""

import os
import tempfile

import cleanup
import commands
import languages
import voices
from dictionary import Dictionary
from snippets import Snippets

fails = []
def check(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, repr(got), "" if ok else f"(want {want!r})")
    if not ok: fails.append(name)


# ---- voices: the frontmost app picks the cleanup profile
check("slack is work", voices.resolve("Slack", {})[0], "work")
check("terminal is code", voices.resolve("iTerm2", {})[0], "code")
check("longest pattern wins", voices.resolve("Visual Studio Code", {})[0], "code")
check("unknown app falls back", voices.resolve("Preview", {})[0], "other")
check("no app falls back", voices.resolve(None, {})[0], "other")
check("config overrides a built-in",
      voices.resolve("Preview", {"voices": {"other": {"style": "casual"}}})[1]["style"], "casual")
check("config can add a voice",
      voices.resolve("Figma", {"voices": {"design": {"apps": ["Figma"], "style": "casual"}}})[0],
      "design")

# ---- snippets: a spoken phrase expands into saved text
def fresh_snips():
    return Snippets(path=os.path.join(tempfile.mkdtemp(), "snippets.json"))

s = fresh_snips()
s.add("my calendar link", "https://cal.com/you")
check("expands mid-sentence", s.expand("Here you go: my calendar link."),
      "Here you go: https://cal.com/you.")
check("case-insensitive", s.expand("MY CALENDAR LINK"), "https://cal.com/you")
check("punctuation between words is fine", s.expand("my, calendar link"), "https://cal.com/you")
check("leaves other text alone", s.expand("my calendar is full"), "my calendar is full")
s.add("sign off", "Thanks,\nAlex")
s.add("sign off formal", "Kind regards,\nAlex R")
check("longest trigger wins", s.expand("sign off formal"), "Kind regards,\nAlex R")
key = [k for k, _ in s.listing() if k == "my calendar link"][0]
s.set_state(key, "off")
check("disabled snippet is inert", s.expand("my calendar link"), "my calendar link")
s.set_state(key, "on")
again = Snippets(path=s.path)
check("reloaded from disk", again.expand("my calendar link"), "https://cal.com/you")
check("use counted", again.items["sign off formal"]["count"], 1)

# ---- language modes
check("en mode", languages.resolve({"language_mode": "en"})[0], "en")
check("hinglish auto-detects", languages.resolve({"language_mode": "hinglish"})[0], None)
check("hinglish seeds the script",
      "market" in languages.resolve({"language_mode": "hinglish"})[1], True)
check("hinglish romanises", languages.resolve({"language_mode": "hinglish"})[2], True)
check("romanize can be switched off",
      languages.resolve({"language_mode": "hi", "romanize": False})[2], False)
check("old config still works", languages.resolve({"language": "en"})[0], "en")

# ---- romanisation: Devanagari -> how Hinglish is actually typed
for src, want in [("नमस्ते", "namaste"), ("करना", "karna"), ("क्या", "kya"),
                  ("हिंदी", "hindi"), ("बंबई", "bambai"), ("काम", "kaam"),
                  ("मैं", "main"), ("बिटकॉइन", "bitkoin"), ("१२३", "123")]:
    check(f"romanise {src}", languages.romanise(src), want)
check("latin passes through", languages.romanise("BTC price"), "BTC price")
check("mixed script", languages.romanise("BTC का price"), "BTC ka price")

# ---- Command Mode: search vs rewrite
check("engine named", commands.parse_web("ask perplexity about ETF flows")[1], "perplexity")
check("query kept", commands.parse_web("hey claude what is a perp")[2], "what is a perp")
check("defaults to google", commands.parse_web("search for funding rate")[1], "google")
check("an edit is not a search", commands.parse_web("make this shorter"), None)
check("empty is not a search", commands.parse_web(""), None)

# ---- cleanup levels
check("off pastes raw", cleanup.clean("um so like hello", {"enabled": True}, level="off"),
      "um so like hello")
check("disabled pastes raw", cleanup.clean("um hello", {"enabled": False}), "um hello")
check("unknown level falls back to the default",
      cleanup.clean("", {"enabled": True}, level="nonsense"), "")
check("aggressive tolerates more loss",
      cleanup.NOVEL_MAX["aggressive"] > cleanup.NOVEL_MAX["light"], True)
check("code style exists", "camelCase" in cleanup.STYLES["code"], True)

# ---- casing: a 3B model won't do this reliably, so we do it in code
for src, want in [
    ("so i think we should push the launch to friday",
     "So I think we should push the launch to Friday"),
    ("i'm going on monday. i'll call you in march.",
     "I'm going on Monday. I'll call you in March."),
    ("hello there. how are you?", "Hello there. How are you?"),
    ("e.g. friday works", "E.g. Friday works"),
    ("", ""),
]:
    check(f"casing {src[:28]!r}", cleanup.fix_casing(src), want)
check("casing leaves internal capitals alone",
      cleanup.fix_casing("BTC is up. camelCase stays put. OpenSearch too."),
      "BTC is up. camelCase stays put. OpenSearch too.")
check("casing leaves a trademarked lowercase start alone",
      cleanup.fix_casing("iPhone sales rose"), "iPhone sales rose")
check("code style skips casing entirely",
      cleanup.clean("friday branch", {"enabled": True}, level="off", style="code"), "friday branch")

d_case = Dictionary(path=os.path.join(tempfile.mkdtemp(), "dictionary.json"))
d_case.add("Supabase"); d_case.add("OpenSearch")
check("dictionary restores its own spelling",
      d_case.apply_casing("i pushed supabase and opensearch"),
      "i pushed Supabase and OpenSearch")
check("dictionary casing leaves correct text untouched",
      d_case.apply_casing("Supabase is fine"), "Supabase is fine")
check("dictionary casing respects word boundaries",
      d_case.apply_casing("supabased"), "supabased")

# ---- Whisper repetition loops (regression: 25 Sep, every dictation looped)
import transcribe
fallback = transcribe.DECODE_DEFAULTS.get("temperature")
check("decoder keeps a temperature fallback (never a single pinned value)",
      isinstance(fallback, (tuple, list)) and len(fallback) > 1, True)
check("the fallback is bounded so it can't hang for minutes",
      len(fallback) <= 3, True)
check("repetition-loop detection is on",
      transcribe.DECODE_DEFAULTS.get("compression_ratio_threshold") is not None, True)
for src, want in [
    ("Smithson Smithson Smithson Smithson Smithson Smithson Smithson", "Smithson"),
    ("Energetic The The The The The The The The The The", "Energetic The"),
    ("The first time you have X. The first time you have X. The first time you have X. "
     "The first time you have X.", "The first time you have X."),
    ("So I think we should push the launch to Friday.", "So I think we should push the launch to Friday."),
    ("no no no", "no no no"),                                      # real emphasis survives
    ("Make B-rolls. Make B-rolls. More.", "Make B-rolls. Make B-rolls. More."),
    ("Combine both studies.\n\nAnd then tell me.", "Combine both studies.\n\nAnd then tell me."),
]:
    check(f"collapse {src[:26]!r}", transcribe._collapse_loops(src), want)
check("loop detected", transcribe.looks_like_loop("The The The The The The The The"), True)
check("normal sentence isn't a loop",
      transcribe.looks_like_loop("So I think we should push the launch to Friday."), False)
check("punctuation-only output is dropped", transcribe._only_if_words("!"), "")
check("real short output is kept", transcribe._only_if_words("Hi!"), "Hi!")

# ---- microphone fallback order (no PortAudio needed)
import recorder as rec_mod

class FakeSD:
    devices = [{"name": "BlackHole 2ch", "max_input_channels": 2},
               {"name": "MacBook Pro Microphone", "max_input_channels": 1},
               {"name": "Studio Display Speakers", "max_input_channels": 0}]
    @staticmethod
    def query_devices(*a, **k): return FakeSD.devices

real_sd, rec_mod.sd = rec_mod.sd, FakeSD
try:
    r = rec_mod.Recorder(device="FDUCE SL40", preferred=["FDUCE SL40", "MacBook Pro Microphone"])
    check("unplugged mic falls back to the next choice", r._resolve_device()[1],
          "MacBook Pro Microphone")
    r2 = rec_mod.Recorder(device="MacBook Pro Microphone")
    check("plugged mic is used", r2._resolve_device(), (1, "MacBook Pro Microphone"))
    r3 = rec_mod.Recorder(device="Nothing Here", preferred=["Also Missing"])
    check("all missing falls to the system default", r3._resolve_device()[0], None)
finally:
    rec_mod.sd = real_sd

# ---- trigger resolution: the wheel and the hold key are independent, and an
# older config (key / keep_fn) must still resolve to the same behaviour.
try:
    import app as _app
except Exception as e:                      # pragma: no cover
    print(f"SKIP trigger tests (no AppKit here: {e})")
else:
    class _Cfg(_app.LocalFlowApp):
        def __init__(self, cfg): self.cfg = cfg
    for trig, want in [
        ({"wheel": True, "hold_key": "fn"}, (True, "fn")),
        ({"wheel": False, "hold_key": "alt_r"}, (False, "alt_r")),
        ({"wheel": True, "hold_key": "none"}, (True, "none")),
        ({"key": "mouse_middle", "keep_fn": True}, (True, "fn")),
        ({"key": "mouse_middle", "keep_fn": False}, (True, "none")),
        ({"key": "fn"}, (False, "fn")),
        ({"key": "alt_r"}, (False, "alt_r")),
        ({}, (True, "fn")),
    ]:
        check(f"trigger {trig or 'defaults'}", _Cfg({"trigger": trig})._trigger_setup(), want)

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
raise SystemExit(1 if fails else 0)
