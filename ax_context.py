"""What's already on screen: the text around the cursor, and whether it is safe
to paste there at all.

Two independent jobs, both read-only:

  surrounding_text()  the contents of the focused text field, fed to Whisper as
                      extra prompt so a reply matches the thread it's in.
  is_secure_input()   macOS is in secure keyboard entry (a password field, or
                      sudo in a terminal). We must not paste or even record.
  selected_text()     what the user has highlighted, for Command Mode.

pyobjc resolves framework attributes lazily and is NOT thread-safe about it, and
everything here gets called from the worker thread. Every symbol is therefore
pulled onto the main thread at import time, exactly like fn_key.
Do not delete _WARMED.
"""

import ctypes

try:
    import ApplicationServices as _AS

    _WARMED = (
        _AS.AXUIElementCreateSystemWide,
        _AS.AXUIElementCopyAttributeValue,
        _AS.AXUIElementCopyParameterizedAttributeValue,
        _AS.kAXFocusedUIElementAttribute,
        _AS.kAXValueAttribute,
        _AS.kAXSelectedTextAttribute,
        _AS.kAXSelectedTextRangeAttribute,
        _AS.kAXRoleAttribute,
        _AS.kAXNumberOfCharactersAttribute,
    )
    _SYSTEM = _AS.AXUIElementCreateSystemWide()
except Exception:  # pragma: no cover — no pyobjc (tests run headless)
    _AS, _WARMED, _SYSTEM = None, (), None

# IsSecureEventInputEnabled lives in Carbon and has no pyobjc binding.
try:
    _carbon = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/Carbon.framework/Carbon"
    )
    _carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
    _carbon.IsSecureEventInputEnabled.argtypes = []
except Exception:  # pragma: no cover
    _carbon = None

# Roles whose contents are a password or otherwise none of our business.
_SECRET_ROLES = {"AXSecureTextField"}


def is_secure_input():
    """True when macOS has secure keyboard entry on (password field, sudo prompt).

    Errs on the side of False: a missing Carbon symbol must not stop dictation.
    """
    if _carbon is None:
        return False
    try:
        return bool(_carbon.IsSecureEventInputEnabled())
    except Exception:
        return False


def _attr(element, name):
    if element is None or _AS is None:
        return None
    try:
        err, value = _AS.AXUIElementCopyAttributeValue(element, name, None)
        return value if err == 0 else None
    except Exception:
        return None


def _focused():
    return _attr(_SYSTEM, _AS.kAXFocusedUIElementAttribute) if _AS else None


def focused_role():
    el = _focused()
    role = _attr(el, _AS.kAXRoleAttribute) if el is not None else None
    return str(role) if role else None


def is_secret_field():
    """A password field specifically, even if secure input isn't flagged yet."""
    return focused_role() in _SECRET_ROLES


def selected_text():
    """The highlighted text in the focused field, or "" — used by Command Mode."""
    el = _focused()
    if el is None:
        return ""
    value = _attr(el, _AS.kAXSelectedTextAttribute)
    return str(value) if value else ""


def surrounding_text(max_chars=400):
    """The tail of the focused text field's contents, as Whisper prompt context.

    Returns "" for anything that isn't a readable text field, for a password
    field, or when Accessibility can't see the app (Electron apps often can't).
    """
    if _AS is None or max_chars <= 0:
        return ""
    el = _focused()
    if el is None:
        return ""
    role = _attr(el, _AS.kAXRoleAttribute)
    if role and str(role) in _SECRET_ROLES:
        return ""
    value = _attr(el, _AS.kAXValueAttribute)
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text
