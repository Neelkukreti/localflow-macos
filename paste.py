"""Insert text into the frontmost app.

Writes to the pasteboard and sends Cmd+V. Both are done through native APIs
rather than by shelling out to pbcopy/pbpaste/osascript: a dictation used to
fork four processes just to deliver its text, which cost more than the paste
itself. AppleScript is kept as a fallback for the keystroke in case the
Accessibility grant is missing, since that path reports its own error.
"""

import os
import subprocess
import time

# pbcopy/pbpaste mangle non-ASCII text without a UTF-8 locale (launchd/Bash have none).
# Still needed by the fallback paths and by callers that shell out.
UTF8_ENV = {**os.environ, "LANG": "en_US.UTF-8"}

_V_KEYCODE = 9  # 'v'

try:
    from AppKit import NSPasteboard, NSPasteboardTypeString
    import Quartz

    _WARMED = (
        NSPasteboard, NSPasteboardTypeString,
        Quartz.CGEventCreateKeyboardEvent, Quartz.CGEventPost, Quartz.CGEventSetFlags,
        Quartz.CGEventSourceCreate, Quartz.kCGEventFlagMaskCommand,
        Quartz.kCGHIDEventTap, Quartz.kCGEventSourceStateHIDSystemState,
    )
    _NATIVE = True
except Exception:  # pragma: no cover — headless
    NSPasteboard = NSPasteboardTypeString = Quartz = None
    _WARMED, _NATIVE = (), False


def _read_clipboard() -> str:
    if _NATIVE:
        try:
            value = NSPasteboard.generalPasteboard().stringForType_(NSPasteboardTypeString)
            return str(value) if value else ""
        except Exception:
            pass
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, env=UTF8_ENV).stdout
    except Exception:
        return ""


def _write_clipboard(text: str):
    if _NATIVE:
        try:
            pb = NSPasteboard.generalPasteboard()
            pb.clearContents()
            if pb.setString_forType_(text, NSPasteboardTypeString):
                return
        except Exception:
            pass
    subprocess.run(["pbcopy"], input=text, text=True, env=UTF8_ENV)


def _key(keycode, command=False):
    """Post one key press+release. Returns False if we couldn't."""
    if not _NATIVE:
        return False
    try:
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
        for down in (True, False):
            e = Quartz.CGEventCreateKeyboardEvent(src, keycode, down)
            if command:
                Quartz.CGEventSetFlags(e, Quartz.kCGEventFlagMaskCommand)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        return True
    except Exception:
        return False


def _cmd_v():
    if _key(_V_KEYCODE, command=True):
        return
    subprocess.run([
        "osascript", "-e",
        'tell application "System Events" to keystroke "v" using command down',
    ])


def _press_return():
    if _key(36):  # Return
        return
    subprocess.run([
        "osascript", "-e",
        'tell application "System Events" to key code 36',
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
