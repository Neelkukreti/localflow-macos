"""Insert text into the frontmost app by writing to the clipboard and
issuing Cmd+V via AppleScript. Optionally restores the previous clipboard.
"""

import os
import subprocess
import time

# pbcopy/pbpaste mangle non-ASCII text without a UTF-8 locale (launchd/Bash have none).
UTF8_ENV = {**os.environ, "LANG": "en_US.UTF-8"}


def _read_clipboard() -> str:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, env=UTF8_ENV).stdout
    except Exception:
        return ""


def _write_clipboard(text: str):
    subprocess.run(["pbcopy"], input=text, text=True, env=UTF8_ENV)


def _cmd_v():
    subprocess.run([
        "osascript", "-e",
        'tell application "System Events" to keystroke "v" using command down',
    ])


def _press_return():
    subprocess.run([
        "osascript", "-e",
        'tell application "System Events" to key code 36',  # Return
    ])


def insert(text: str, cfg: dict, press_enter: bool = False):
    """Paste `text` at the cursor. With press_enter, hit Return afterwards so a
    chat message sends itself (Wispr Flow's "press enter" command)."""
    if not text:
        return
    prev = _read_clipboard() if cfg.get("restore_clipboard", True) else None
    _write_clipboard(text)
    if cfg.get("auto_paste", True):
        time.sleep(0.05)
        _cmd_v()
        if press_enter:
            # After the paste lands, not before: Return on an empty field sends nothing.
            time.sleep(0.15)
            _press_return()
        if prev is not None:
            # Give the paste a beat to consume the clipboard before restoring.
            time.sleep(0.4)
            _write_clipboard(prev)
