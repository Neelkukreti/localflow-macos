"""Duck other audio while dictating: pause a playing music app, resume after.

Wispr Flow calls this "mute music". We pause rather than mute so a podcast
doesn't run on silently while you talk over it. Only apps that are already
running AND already playing are touched, and only those get resumed.
"""

import subprocess

PLAYERS = ("Spotify", "Music")


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
    for app in PLAYERS:
        # `running` check first: touching `player state` would launch the app.
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
