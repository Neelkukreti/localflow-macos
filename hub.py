"""The LocalFlow window — the thing Spotlight opens.

A WKWebView showing hub/index.html, with a small JSON bridge back into the
running app. The UI is HTML because this window is a dashboard (status, history,
dictionary, snippets, settings) and hand-building that in AppKit would be a lot
of code for a worse result.

JS calls  window.webkit.messageHandlers.lf.postMessage({id, action, payload})
and we answer with window.lfResolve(id, data). Everything here runs on the main
thread, which is where WebKit insists on being touched.
"""

import json
import os
import threading

import objc
from AppKit import (
    NSWindow, NSWindowStyleMaskTitled, NSWindowStyleMaskClosable,
    NSWindowStyleMaskResizable, NSWindowStyleMaskMiniaturizable,
    NSBackingStoreBuffered, NSMakeRect, NSApplication, NSColor,
    NSWindowTitleHidden, NSFullSizeContentViewWindowMask,
    NSApplicationActivationPolicyRegular, NSApplicationActivationPolicyAccessory,
)
from Foundation import NSObject, NSURL, NSOperationQueue
import WebKit

HERE = os.path.dirname(os.path.abspath(__file__))
HUB_DIR = os.path.join(HERE, "hub")
INDEX = os.path.join(HUB_DIR, "index.html")

# Same lazy-attribute race as the event taps: resolve before any thread runs.
_WARMED = (
    WebKit.WKWebView, WebKit.WKWebViewConfiguration, WebKit.WKUserContentController,
    NSWindow, NSMakeRect, NSApplication, NSURL, NSOperationQueue,
)


def _on_main(fn):
    if threading.current_thread() is threading.main_thread():
        fn()
    else:
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


class _WindowWatcher(NSObject):
    """Drops the app back to accessory mode when the window closes."""

    def initWithHub_(self, owner):
        self = objc.super(_WindowWatcher, self).init()
        if self is None:
            return None
        self._hub = owner
        return self

    def windowWillClose_(self, _notification):
        self._hub._go_accessory()


class _Bridge(NSObject):
    """Receives postMessage from the page. Conforms to WKScriptMessageHandler."""

    def initWithHandler_(self, handler):
        self = objc.super(_Bridge, self).init()
        if self is None:
            return None
        self._handler = handler
        self._webview = None
        return self

    def userContentController_didReceiveScriptMessage_(self, _ucc, message):
        try:
            body = message.body()
            msg_id = int(body.get("id", 0))
            action = str(body.get("action", ""))
            payload = body.get("payload") or {}
            payload = {str(k): v for k, v in dict(payload).items()}
        except Exception:
            return
        try:
            result = self._handler(action, payload)
        except Exception as e:
            result = {"error": str(e)}
        if self._webview is None or not msg_id:
            return
        try:
            js = f"window.lfResolve({msg_id}, {json.dumps(result, default=str)})"
            self._webview.evaluateJavaScript_completionHandler_(js, None)
        except Exception:
            pass


class Hub:
    def __init__(self, handler):
        """handler(action, payload) -> JSON-serialisable result."""
        self.handler = handler
        self._window = None
        self._webview = None
        self._bridge = None
        self._watcher = None

    @property
    def is_open(self):
        try:
            return self._window is not None and bool(self._window.isVisible())
        except Exception:
            return False

    def _build(self):
        rect = NSMakeRect(0, 0, 940, 620)
        mask = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                | NSWindowStyleMaskResizable | NSWindowStyleMaskMiniaturizable
                | NSFullSizeContentViewWindowMask)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, mask, NSBackingStoreBuffered, False
        )
        win.setTitle_("LocalFlow")
        win.setTitlebarAppearsTransparent_(True)
        win.setTitleVisibility_(NSWindowTitleHidden)
        win.setBackgroundColor_(
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.969, 0.965, 0.953, 1.0)
        )
        win.setReleasedWhenClosed_(False)
        win.setMinSize_((780, 520))

        config = WebKit.WKWebViewConfiguration.alloc().init()
        bridge = _Bridge.alloc().initWithHandler_(self.handler)
        config.userContentController().addScriptMessageHandler_name_(bridge, "lf")
        # Let the page's own background show through rather than flashing white.
        try:
            config.preferences().setValue_forKey_(True, "developerExtrasEnabled")
        except Exception:
            pass

        web = WebKit.WKWebView.alloc().initWithFrame_configuration_(rect, config)
        web.setAutoresizingMask_(2 | 16)  # width | height sizable
        try:
            web.setValue_forKey_(False, "drawsBackground")
        except Exception:
            pass
        bridge._webview = web
        win.setContentView_(web)
        win.center()

        self._watcher = _WindowWatcher.alloc().initWithHub_(self)
        win.setDelegate_(self._watcher)
        self._window, self._webview, self._bridge = win, web, bridge
        web.loadFileURL_allowingReadAccessToURL_(
            NSURL.fileURLWithPath_(INDEX), NSURL.fileURLWithPath_(HUB_DIR)
        )

    def _go_accessory(self):
        """Back to menu-bar-only: no Dock icon, no Cmd+Tab entry."""
        def do():
            try:
                NSApplication.sharedApplication().setActivationPolicy_(
                    NSApplicationActivationPolicyAccessory
                )
            except Exception:
                pass
        _on_main(do)

    def open(self):
        def do():
            try:
                if self._window is None:
                    self._build()
                else:
                    self.refresh()
                # An LSUIElement app is an "accessory": it cannot take focus, so
                # activateIgnoringOtherApps_ alone leaves the window behind
                # everything. Become a regular app while the window is up, and
                # drop back to accessory when it closes.
                nsapp = NSApplication.sharedApplication()
                nsapp.setActivationPolicy_(NSApplicationActivationPolicyRegular)
                nsapp.activateIgnoringOtherApps_(True)
                self._window.makeKeyAndOrderFront_(None)
                self._window.orderFrontRegardless()
            except Exception:
                self._window = self._webview = None
        _on_main(do)

    def close(self):
        def do():
            if self._window is not None:
                self._window.orderOut_(None)
            self._go_accessory()
        _on_main(do)

    def toggle(self):
        if self.is_open:
            self.close()
            return False
        self.open()
        return True

    def refresh(self):
        """Re-render whatever tab is showing (after a dictation, say)."""
        if self._webview is None:
            return

        def do():
            try:
                self._webview.evaluateJavaScript_completionHandler_(
                    "window.lfRefresh && window.lfRefresh()", None
                )
            except Exception:
                pass
        _on_main(do)
