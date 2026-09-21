"""Command Mode: speak an instruction instead of typing it.

Two things one spoken instruction can do:

  "make this shorter"      -> rewrite whatever text is selected, in place
  "ask perplexity about X" -> open that search in the browser

This is the one place a model IS allowed to follow what you said, which is the
exact opposite of cleanup.py. The two never share a prompt: cleanup must never
obey a transcript, and a command must never be pasted as literal text.
"""

import re
import json
import urllib.parse
import urllib.request
import urllib.error
import subprocess

SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={q}",
    "perplexity": "https://www.perplexity.ai/search?q={q}",
    "chatgpt": "https://chatgpt.com/?q={q}",
    "claude": "https://claude.ai/new?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "tradingview": "https://www.tradingview.com/search/?query={q}",
}

# "ask google what the funding rate is" / "search perplexity for BTC ETF flows"
_WEB = re.compile(
    r"^\s*(?:ask|search|google|look\s+up|hey)\b\s*"
    r"(?P<engine>" + "|".join(SEARCH_ENGINES) + r")?\s*"
    r"(?:for|about)?\s*(?P<query>.+?)\s*$",
    re.IGNORECASE,
)

SYSTEM_PROMPT = (
    "You rewrite text. The user gives you a piece of text inside <text> tags and an "
    "instruction inside <instruction> tags. Apply the instruction to the text and "
    "return ONLY the rewritten text — no preamble, no explanation, no quotes, no tags. "
    "Preserve the original language, names, numbers, URLs and code exactly unless the "
    "instruction says to change them. If the instruction does not apply, return the "
    "text unchanged."
)


def parse_web(instruction):
    """(url, engine, query) if this is a search command, else None."""
    text = (instruction or "").strip().rstrip(".!?")
    if not text:
        return None
    m = _WEB.match(text)
    if not m:
        return None
    engine = (m.group("engine") or "google").lower()
    query = (m.group("query") or "").strip(" ,")
    if not query:
        return None
    # A bare "search" with no engine and a long sentence is usually a real search;
    # an instruction like "search and replace the name" is not.
    url = SEARCH_ENGINES[engine].format(q=urllib.parse.quote_plus(query))
    return url, engine, query


def open_url(url):
    try:
        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _ollama(payload, url, timeout):
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def transform(selection, instruction, cfg):
    """Rewrite `selection` per `instruction`. Returns "" if the model gave nothing
    usable, so the caller can leave the user's text alone."""
    selection = (selection or "").strip()
    instruction = (instruction or "").strip()
    if not selection or not instruction:
        return ""
    payload = {
        "model": cfg.get("model") or "llama3.2:3b",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": f"<text>{selection}</text>\n<instruction>{instruction}</instruction>"},
        ],
        "stream": False,
        "options": {"temperature": 0.2},
    }
    try:
        data = _ollama(payload, cfg.get("ollama_url", "http://localhost:11434"),
                       cfg.get("timeout", 60))
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return ""
    out = (data.get("message", {}).get("content") or "").strip()
    out = re.sub(r"</?(?:text|instruction)>", "", out).strip()
    out = re.sub(r'^"(.*)"$', r"\1", out, flags=re.DOTALL).strip()
    # A 3B model sometimes answers instead of editing; a wild size change is the tell.
    if not out or len(out) > max(len(selection) * 6, len(selection) + 600):
        return ""
    return out
