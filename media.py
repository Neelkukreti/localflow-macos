"""Duck other audio while dictating: pause a playing music app, resume after.

Wispr Flow calls this "mute music". We pause rather than mute so a podcast
doesn't run on silently while you talk over it. Only apps that are already
running AND already playing are touched, and only those get resumed.
"""

import subprocess

# name -> bundle id, so we can ask NSWorkspace whether it's running before
# paying for an AppleScript round-trip. Most dictations have neither open, and
# two osascript calls per dictation for a "no" is the wrong trade.
PLAYERS = {"Spotify": "com.spotify.client", "Music": "com.apple.Music"}

try:
    from AppKit import NSWorkspace
except Exception:  # pragma: no cover — headless
    NSWorkspace = None


def _running_players():
    if NSWorkspace is None:
        return list(PLAYERS)
    try:
        ids = {str(a.bundleIdentifier() or "")
               for a in NSWorkspace.sharedWorkspace().runningApplications()}
    except Exception:
        return list(PLAYERS)
    return [name for name, bundle in PLAYERS.items() if bundle in ids]


def _osa(script, timeout=2):
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True,
                             text=True, timeout=timeout)
        return out.stdout.strip()
    except Exception:
        return ""


def pause_players():
    """Pause whatever is playing; returns the apps to hand back to resume()."""
    paused = []
    for app in _running_players():
        # Still guard with `is running`: NSWorkspace can race a quitting app,
        # and touching `player state` would relaunch it.
        state = _osa(
            f'if application "{app}" is running then '
            f'tell application "{app}" to return player state as text'
        )
        if state.lower() == "playing":
            _osa(f'tell application "{app}" to pause')
            paused.append(app)
    return paused


def resume(apps):
    for app in apps or []:
        _osa(f'if application "{app}" is running then tell application "{app}" to play')
