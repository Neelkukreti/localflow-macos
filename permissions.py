"""What macOS has and hasn't granted us, and how to go fix it.

LocalFlow fails in a specifically confusing way without these: the event taps
just never fire, so Fn or the mouse wheel does nothing at all and there is no
error anywhere. The hub window reads this module to say so out loud.
"""

import ctypes
import subprocess

# System Settings panes, so the hub can send you straight to the right list.
PANES = {
    "accessibility":
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "input_monitoring":
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    "microphone":
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
}

_IOHID_LISTEN_EVENT = 1  # kIOHIDRequestTypeListenEvent
_GRANTED, _DENIED = 0, 1


def accessibility():
    """Needed to post the Cmd+V that pastes, and to read selected text."""
    try:
        import ApplicationServices
        return bool(ApplicationServices.AXIsProcessTrusted())
    except Exception:
        return False


def input_monitoring():
    """Needed by every event tap: Fn, the mouse wheel, the global hotkeys."""
    try:
        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        iokit.IOHIDCheckAccess.restype = ctypes.c_int
        iokit.IOHIDCheckAccess.argtypes = [ctypes.c_int]
        return iokit.IOHIDCheckAccess(_IOHID_LISTEN_EVENT) == _GRANTED
    except Exception:
        return False


def microphone():
    """3 == authorized. 0 is 'not asked yet', which is fine until you record."""
    try:
        import AVFoundation
        status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
            AVFoundation.AVMediaTypeAudio
        )
        return int(status) == 3
    except Exception:
        return False


def open_pane(which):
    url = PANES.get(which)
    if not url:
        return False
    try:
        subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def snapshot():
    """[{key, label, ok, why}] — everything the hub needs to render the list."""
    return [
        {"key": "microphone", "label": "Microphone", "ok": microphone(),
         "why": "Required to record anything at all."},
        {"key": "input_monitoring", "label": "Input Monitoring", "ok": input_monitoring(),
         "why": "Required for the Fn key, the mouse-wheel trigger and the global shortcuts."},
        {"key": "accessibility", "label": "Accessibility", "ok": accessibility(),
         "why": "Required to paste into other apps and to read selected text for Command Mode."},
    ]
