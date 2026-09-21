"""A floating notepad to dictate into when there's no text field to paste at.

Wispr Flow's Scratchpad. Open it from the menu bar, and while it's open every
dictation is appended here instead of being pasted into the frontmost app —
useful for drafting, and for dictating while a terminal or a password prompt has
focus. Plain text, autosaved to scratchpad.md next to the app.

AppKit objects may only be touched on the main thread, and dictations finish on
a worker thread, so append() hops threads via the main NSOperationQueue. Every
AppKit symbol is resolved at import time (the pyobjc lazy-attribute race that
bit fn_key/mouse_trigger) — do not delete _WARMED.
"""

import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SCRATCHPAD_PATH = os.path.join(HERE, "scratchpad.md")

try:
    import AppKit
    import Foundation

    _WARMED = (
        AppKit.NSPanel, AppKit.NSTextView, AppKit.NSScrollView, AppKit.NSFont,
        AppKit.NSMakeRect, AppKit.NSBackingStoreBuffered, AppKit.NSFloatingWindowLevel,
        AppKit.NSWindowStyleMaskTitled, AppKit.NSWindowStyleMaskClosable,
        AppKit.NSWindowStyleMaskResizable, AppKit.NSWindowStyleMaskUtilityWindow,
        AppKit.NSViewWidthSizable, AppKit.NSViewHeightSizable,
        Foundation.NSOperationQueue,
    )
    _HAVE_APPKIT = True
except Exception:  # pragma: no cover — headless tests
    AppKit = Foundation = None
    _WARMED, _HAVE_APPKIT = (), False


def _on_main(fn):
    """Run fn on the main thread, now if we're already there."""
    if not _HAVE_APPKIT or threading.current_thread() is threading.main_thread():
        fn()
        return
    Foundation.NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


class Scratchpad:
    def __init__(self, path=SCRATCHPAD_PATH):
        self.path = path
        self._panel = None
        self._textview = None
        self._lock = threading.Lock()

    # ---------- file ----------

    def read(self):
        try:
            with open(self.path) as f:
                return f.read()
        except OSError:
            return ""

    def _write(self, text):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w") as f:
                f.write(text)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def save(self):
        """Persist whatever is in the window (called on append and on close)."""
        if self._textview is None:
            return

        def do():
            try:
                self._write(str(self._textview.string()))
            except Exception:
                pass
        _on_main(do)

    # ---------- window ----------

    @property
    def is_open(self):
        try:
            return self._panel is not None and bool(self._panel.isVisible())
        except Exception:
            return False

    def _build(self):
        rect = AppKit.NSMakeRect(0, 0, 520, 420)
        mask = (AppKit.NSWindowStyleMaskTitled | AppKit.NSWindowStyleMaskClosable
                | AppKit.NSWindowStyleMaskResizable | AppKit.NSWindowStyleMaskUtilityWindow)
        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, mask, AppKit.NSBackingStoreBuffered, False
        )
        panel.setTitle_("LocalFlow Scratchpad")
        panel.setFloatingPanel_(True)
        panel.setLevel_(AppKit.NSFloatingWindowLevel)
        # Closing the panel must not free it — the menu reopens the same one.
        panel.setReleasedWhenClosed_(False)
        panel.setHidesOnDeactivate_(False)

        scroll = AppKit.NSScrollView.alloc().initWithFrame_(rect)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)

        tv = AppKit.NSTextView.alloc().initWithFrame_(rect)
        tv.setRichText_(False)
        tv.setFont_(AppKit.NSFont.userFixedPitchFontOfSize_(13))
        tv.setAutomaticQuoteSubstitutionEnabled_(False)
        tv.setAutomaticDashSubstitutionEnabled_(False)
        tv.setAutomaticSpellingCorrectionEnabled_(False)
        tv.setAutoresizingMask_(AppKit.NSViewWidthSizable)
        tv.setString_(self.read())
        scroll.setDocumentView_(tv)
        panel.setContentView_(scroll)
        panel.center()

        self._panel, self._textview = panel, tv

    def open(self):
        if not _HAVE_APPKIT:
            return False

        def do():
            try:
                if self._panel is None:
                    self._build()
                self._panel.makeKeyAndOrderFront_(None)
            except Exception:
                self._panel = self._textview = None
        _on_main(do)
        return True

    def close(self):
        def do():
            try:
                if self._panel is not None:
                    self._write(str(self._textview.string()))
                    self._panel.orderOut_(None)
            except Exception:
                pass
        _on_main(do)

    def toggle(self):
        if self.is_open:
            self.close()
            return False
        self.open()
        return True

    # ---------- dictation sink ----------

    def append(self, text):
        """Add a finished dictation. Works whether or not the window is built."""
        text = (text or "").strip()
        if not text:
            return
        if not _HAVE_APPKIT or self._textview is None:
            with self._lock:  # no window yet: straight to the file
                existing = self.read()
                sep = "" if not existing or existing.endswith("\n") else "\n"
                self._write(f"{existing}{sep}{text}\n")
            return

        def do():
            try:
                current = str(self._textview.string())
                sep = "" if not current or current.endswith("\n") else "\n"
                self._textview.setString_(f"{current}{sep}{text}\n")
                self._textview.scrollRangeToVisible_(
                    (len(self._textview.string()), 0)
                )
                self._write(str(self._textview.string()))
            except Exception:
                pass
        _on_main(do)
