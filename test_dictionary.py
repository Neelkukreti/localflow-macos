"""Headless check of the dictionary: learning, review, fixes, persistence.

Run: ./.venv/bin/python test_dictionary.py
"""

import os
import tempfile

from dictionary import Dictionary

fails = []
def check(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, got, "" if ok else f"(want {want})")
    if not ok: fails.append(name)


def fresh():
    path = os.path.join(tempfile.mkdtemp(), "dictionary.json")
    return Dictionary(path=path, promote_at=3)


# learning: a word has to earn its place
d = fresh()
d.learn("Supabase funding is wild")
d.learn("the Supabase desk again")
check("said twice: not learned yet", d.prompt_words(), [])
d.learn("Supabase one more time")
check("said three times: learned", d.prompt_words(), ["Supabase"])

# everyday words, stray letters and short words are not worth teaching Whisper
d = fresh()
for _ in range(5):
    d.learn("and then the funding was really good so I went to see the ladder")
check("everyday words ignored", d.prompt_words(), [])
d = fresh()
for _ in range(3):
    d.learn("Slow progress beats no progress.")
check("word starting a sentence is not a name", d.prompt_words(), [])
d = fresh()
for _ in range(3):
    d.learn("I checked the Radar bot again")
check("ordinary word capitalised mid-sentence is a name", d.prompt_words(), ["Radar"])
d = fresh()
for _ in range(3):
    d.learn("BTC broke out")
check("acronyms learned", d.prompt_words(), ["BTC"])

# your own words come first and count from the start
d = fresh()
for _ in range(9):
    d.learn("Cloudflare Cloudflare")
d.add("Anthropic")
check("manual word leads the prompt", d.prompt_words(), ["Anthropic", "Cloudflare"])
check("manual word is reviewed", [e["reviewed"] for k, e in d.listing() if k == "anthropic"], [True])

# review: keep what you want, the rest goes quiet and stops biasing Whisper
d = fresh()
for _ in range(3):
    d.learn("Vercel ladder and Reykjavik rain")
check("both learned", sorted(d.prompt_words()), ["Reykjavik", "Vercel"])
check("both waiting for review", sorted(e["display"] for e in d.unreviewed()), ["Reykjavik", "Vercel"])
d.review(["Vercel"])
check("kept word stays", d.prompt_words(), ["Vercel"])
check("dropped word is off, not deleted", [e["state"] for k, e in d.listing() if k == "reykjavik"], ["off"])
check("review clears the queue", d.unreviewed(), [])

# fixes: whole words, any case, longest match first
d = fresh()
d.add_fix("super base", "Supabase")
d.add_fix("open search", "OpenSearch")
check("fix applied mid-sentence", d.apply_fixes("I sent it to Super Base today"), "I sent it to Supabase today")
check("fix is case-insensitive", d.apply_fixes("super base"), "Supabase")
check("fix leaves longer words alone", d.apply_fixes("supersuper based"), "supersuper based")
check("fixed spelling joins the dictionary", "Supabase" in d.prompt_words(), True)
d.remove_fix("super base")
check("removed fix stops applying", d.apply_fixes("super base"), "super base")

# forgetting keeps the words you added yourself
d = fresh()
for _ in range(3):
    d.learn("Remotion render")
d.add("Vercel")
d.forget_learned()
check("forget keeps manual words", d.prompt_words(), ["Vercel"])

# it survives a restart
d = fresh()
d.add("Netlify")
for _ in range(3):
    d.learn("Netlify funding Netlify")
again = Dictionary(path=d.path, promote_at=3)
check("reloaded from disk", again.prompt_words(), ["Netlify"])
check("counts kept", [e["count"] for k, e in again.listing() if k == "netlify"], [6])

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
raise SystemExit(1 if fails else 0)
