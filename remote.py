"""Remote control over Darwin notifications — for the Stream Deck, or anything.

    notifyutil -p com.localflow.toggle     start / finish a hands-free dictation
    notifyutil -p com.localflow.stop       abandon whatever is in flight

Why this and not a hotkey: a Stream Deck "Hotkey" key is an injected
keystroke, which pynput's GlobalHotKeys silently drops, and its settings format
is private. A Darwin notification needs no Accessibility or Automation grant,
can't be mistaken for typing, and `notifyutil` ships with macOS.

It costs nothing while idle: notify_register_file_descriptor hands us a pipe
that macOS writes a 4-byte token into on each post, so the listener thread
simply blocks on read() — no polling.
"""

import ctypes
import os
import struct
import threading

PREFIX = "com.localflow."
NOTIFY_STATUS_OK = 0
NOTIFY_REUSE = 0x00000001

try:
    _libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    _register = _libc.notify_register_file_descriptor
    _register.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int),
                          ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    _register.restype = ctypes.c_uint32
except Exception:  # pragma: no cover
    _register = None


class Remote(threading.Thread):
    def __init__(self, handlers, on_error=None):
        """handlers: {"toggle": fn, "stop": fn} — names get PREFIX added."""
        super().__init__(daemon=True, name="localflow-remote")
        self.handlers = dict(handlers)
        self.on_error = on_error
        self.registered = []

    def run(self):
        if _register is None:
            return
        fd = ctypes.c_int(-1)
        by_token = {}
        for i, (name, fn) in enumerate(self.handlers.items()):
            token = ctypes.c_int(0)
            # First registration creates the pipe; the rest reuse it.
            flags = NOTIFY_REUSE if i else 0
            status = _register((PREFIX + name).encode(), ctypes.byref(fd), flags,
                               ctypes.byref(token))
            if status != NOTIFY_STATUS_OK:
                if self.on_error:
                    self.on_error(f"notify register {name}: status {status}")
                continue
            by_token[token.value] = fn
            self.registered.append(PREFIX + name)
        if fd.value < 0 or not by_token:
            return
        while True:
            try:
                data = os.read(fd.value, 4)
            except OSError:
                return
            if len(data) < 4:
                return
            token = struct.unpack("!i", data)[0]    # network byte order
            fn = by_token.get(token)
            if fn is not None:
                try:
                    fn()
                except Exception as e:
                    if self.on_error:
                        self.on_error(str(e))
