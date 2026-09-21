"""Per-app voices: which cleanup level and writing style to use where.

Wispr Flow calls these "voices" — Slack gets short work-speak, Gmail gets a
formal paragraph, a terminal gets code left alone. We resolve the frontmost
app name to one profile and hand its level/style to cleanup.

Profiles live in config.json under "voices" so they can be edited by hand.
Matching is case-insensitive substring, longest match first, so "Google Chrome"
can be listed as "chrome" and "Visual Studio Code" as "code".
"""

DEFAULT_VOICES = {
    "work": {
        "apps": ["Slack", "Microsoft Teams", "Discord", "Telegram", "Linear", "Notion"],
        "level": "light", "style": "formal", "press_enter": False,
    },
    "email": {
        "apps": ["Mail", "Superhuman", "Microsoft Outlook", "Spark"],
        "level": "standard", "style": "formal", "press_enter": False,
    },
    "messages": {
        "apps": ["Messages", "WhatsApp", "Messenger", "WeChat"],
        "level": "light", "style": "casual", "press_enter": False,
    },
    "code": {
        # Terminals, IDEs and the AI chats we dictate prompts into: keep
        # identifiers, paths and flags exactly as spoken.
        "apps": ["Terminal", "iTerm", "Visual Studio Code", "Cursor", "Windsurf",
                 "Xcode", "Claude", "ChatGPT", "Warp", "Ghostty"],
        "level": "light", "style": "code", "press_enter": False,
    },
    "other": {"apps": [], "level": "light", "style": "default", "press_enter": False},
}

FALLBACK = "other"


def profiles(cfg):
    """Merge the user's config over the built-ins so a partial edit still works."""
    merged = {k: dict(v) for k, v in DEFAULT_VOICES.items()}
    for name, over in (cfg.get("voices") or {}).items():
        merged.setdefault(name, {"apps": []}).update(over or {})
    return merged


def resolve(app_name, cfg):
    """(voice name, profile) for the frontmost app."""
    vs = profiles(cfg)
    if app_name:
        low = app_name.lower()
        best = None  # longest pattern wins: "code" must not beat "visual studio code"
        for name, prof in vs.items():
            for pattern in prof.get("apps") or []:
                p = pattern.lower()
                if p and p in low and (best is None or len(p) > best[0]):
                    best = (len(p), name, prof)
        if best:
            return best[1], best[2]
    return FALLBACK, vs.get(FALLBACK, DEFAULT_VOICES[FALLBACK])
