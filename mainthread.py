"""Every AppKit write goes through the main thread. No exceptions.

AppKit is not thread-safe. Setting a menu title, the status-bar icon or a menu
item's checkmark from a background thread takes AppKit's view-hierarchy lock
off the main thread; when that races the main thread's display cycle the lock
is never released and the whole app freezes — menu gone, triggers dead, "it
hangs". A sample of the frozen app showed the main thread parked in
-[NSViewHierarchyLock _lockForWriting:] for its entire run.

LocalFlow writes UI from the transcription worker, both key listeners and
several timers — about 80 call sites. Rather than audit them one by one (and
have the next one reintroduce the bug), install() patches the rumps setters so
a call from any other thread is queued onto the main thread instead.

Reads stay consistent: a title set from a worker is readable back immediately
from Python, even before the main thread has applied it.
"""

import threading

import rumps
from Foundation import NSOperationQueue

_WARMED = (NSOperationQueue,)
_installed = False


def on_main(fn, *args, **kwargs):
    """Run fn now if we're on the main thread, otherwise queue it there."""
    if threading.current_thread() is threading.main_thread():
        return fn(*args, **kwargs)
    NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: fn(*args, **kwargs))
    return None


def _wrap_setter(cls, name):
    prop = getattr(cls, name)
    shadow = f"_lf_{name}"

    def fget(self):
        if hasattr(self, shadow):
            return getattr(self, shadow)
        return prop.fget(self)

    def fset(self, value):
        setattr(self, shadow, value)       # readable immediately, from any thread
        on_main(prop.fset, self, value)

    setattr(cls, name, property(fget, fset, prop.fdel, prop.__doc__))


def _wrap_method(cls, name):
    original = getattr(cls, name)

    def wrapper(self, *args, **kwargs):
        return on_main(original, self, *args, **kwargs)

    wrapper.__name__ = name
    wrapper.__doc__ = original.__doc__
    setattr(cls, name, wrapper)


def install():
    """Patch rumps once, before any menu or status item is built."""
    global _installed
    if _installed:
        return
    _wrap_setter(rumps.MenuItem, "title")
    _wrap_setter(rumps.MenuItem, "state")
    _wrap_method(rumps.MenuItem, "set_icon")
    _wrap_setter(rumps.App, "title")
    _wrap_setter(rumps.App, "icon")
    _wrap_setter(rumps.App, "template")
    if hasattr(rumps.App, "set_icon"):
        _wrap_method(rumps.App, "set_icon")
    _notify = rumps.notification
    rumps.notification = lambda *a, **k: on_main(_notify, *a, **k)
    _installed = True
