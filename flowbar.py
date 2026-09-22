"""The floating status pill, like Wispr Flow's flow bar.

A small always-on-top capsule near the bottom of the screen that says what
LocalFlow is doing. Two things matter and both are easy to get wrong:

  * it must NOT take focus — a normal window would steal key focus from the app
    you're dictating into, and the Cmd+V paste would land in the wrong place.
    Hence a non-activating panel.
  * it must NOT eat clicks — ignoresMouseEvents, so it's scenery, not a target.

Hidden while idle unless flow_bar.always_visible is set.
"""

import threading

try:
    import objc
    import AppKit
    from Foundation import NSObject, NSOperationQueue

    _WARMED = (
        AppKit.NSPanel, AppKit.NSView, AppKit.NSTextField, AppKit.NSColor, AppKit.NSFont,
        AppKit.NSMakeRect, AppKit.NSScreen, AppKit.NSBackingStoreBuffered,
        AppKit.NSWindowStyleMaskBorderless, AppKit.NSWindowStyleMaskNonactivatingPanel,
        AppKit.NSStatusWindowLevel, NSOperationQueue,
    )
    _HAVE = True
except Exception:  # pragma: no cover — headless
    objc = AppKit = NSObject = NSOperationQueue = None
    NSObject = object
    _WARMED, _HAVE = (), False

# state -> (label, dot colour). Matches menubar_icon.COLORS.
STATES = {
    "idle":       ("Ready",          (0.42, 0.45, 0.55)),
    "recording":  ("Listening",      (0.90, 0.22, 0.24)),
    "hands_free": ("Listening",      (0.96, 0.60, 0.10)),
    "working":    ("Transcribing",   (0.35, 0.45, 0.95)),
    "command":    ("Command",        (0.55, 0.35, 0.90)),
}

W, H, BOTTOM = 212.0, 34.0, 96.0
SEGMENTS = 11          # meter bars
SEG_W, SEG_GAP = 4.0, 3.0

# What counts as a usable input level. Below QUIET Whisper starts inventing
# "Thank you." on the silence; above HOT the mic is clipping and consonants mush.
QUIET, HOT = 0.035, 0.80
GREEN = (0.30, 0.78, 0.50)
AMBER = (0.96, 0.66, 0.16)
RED   = (0.92, 0.28, 0.26)
OFF   = (1.0, 1.0, 1.0, 0.13)


def _on_main(fn):
    if not _HAVE or threading.current_thread() is threading.main_thread():
        fn()
    else:
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


class _PanelWatcher(NSObject):
    """Remembers where you dragged the bar to."""

    def initWithBar_(self, bar):
        self = objc.super(_PanelWatcher, self).init()
        if self is None:
            return None
        self._bar = bar
        return self

    def windowDidMove_(self, _notification):
        self._bar._remember_position()


class FlowBar:
    def __init__(self, enabled=True, always_visible=False, position=None, on_move=None):
        self.enabled = enabled and _HAVE
        self.always_visible = always_visible
        self.position = position        # [x, y] saved from a previous drag
        self.on_move = on_move          # called with (x, y) to persist it
        self._drag_mode = False         # see set_draggable()
        self._watcher = None
        self._panel = None
        self._dot = None
        self._label = None
        self._segs = []
        self._state = "idle"

    def _build(self):
        screen = AppKit.NSScreen.mainScreen().visibleFrame()
        x = screen.origin.x + (screen.size.width - W) / 2.0
        y = screen.origin.y + BOTTOM
        if self.position:
            try:
                px, py = float(self.position[0]), float(self.position[1])
                # Honour a saved spot only if it's still on SOME screen — check
                # every display, not just the main one, or a bar parked on an
                # external monitor snaps back every launch. Monitors come and go,
                # and a bar off in dead space looks like a broken app.
                for sc in (AppKit.NSScreen.screens() or [AppKit.NSScreen.mainScreen()]):
                    f = sc.frame()
                    if (f.origin.x - W / 2 < px < f.origin.x + f.size.width
                            and f.origin.y - H / 2 < py < f.origin.y + f.size.height):
                        x, y = px, py
                        break
            except (TypeError, ValueError, IndexError, AttributeError):
                pass
        rect = AppKit.NSMakeRect(x, y, W, H)

        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False,
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        # Click-through by default. It has to be: the bar floats over whatever
        # you're working in, and an interactive panel swallows clicks meant for
        # the app underneath — including the middle click the wheel trigger
        # replays, which is what makes the wheel look broken.
        # set_draggable() turns this off only while you're repositioning it.
        panel.setIgnoresMouseEvents_(not self._drag_mode)
        panel.setMovableByWindowBackground_(True)
        panel.setReleasedWhenClosed_(False)
        panel.setHidesOnDeactivate_(False)
        # Visible on every Space, and over full-screen apps.
        panel.setCollectionBehavior_(1 << 0 | 1 << 8)

        content = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, W, H))
        content.setWantsLayer_(True)
        layer = content.layer()
        layer.setCornerRadius_(H / 2.0)
        layer.setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.055, 0.065, 0.105, 0.94).CGColor()
        )
        layer.setBorderWidth_(1.0)
        layer.setBorderColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(1, 1, 1, 0.09).CGColor()
        )
        layer.setShadowOpacity_(0.45)
        layer.setShadowRadius_(12.0)
        layer.setShadowOffset_((0, -2))

        dot = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(15, H / 2 - 4, 8, 8))
        dot.setWantsLayer_(True)
        dot.layer().setCornerRadius_(4.0)
        content.addSubview_(dot)

        label = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(31, H / 2 - 9, 88, 18)
        )
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setFont_(AppKit.NSFont.fontWithName_size_("Seravek", 12.5)
                       or AppKit.NSFont.systemFontOfSize_(12.5))
        label.setTextColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.95, 0.93, 0.88, 1)
        )
        content.addSubview_(label)

        # Level meter: a row of segments that light up with the input.
        meter_w = SEGMENTS * SEG_W + (SEGMENTS - 1) * SEG_GAP
        x0 = W - 15 - meter_w
        self._segs = []
        for i in range(SEGMENTS):
            seg = AppKit.NSView.alloc().initWithFrame_(
                AppKit.NSMakeRect(x0 + i * (SEG_W + SEG_GAP), H / 2 - 6, SEG_W, 12)
            )
            seg.setWantsLayer_(True)
            seg.layer().setCornerRadius_(SEG_W / 2.0)
            seg.layer().setBackgroundColor_(
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*OFF).CGColor()
            )
            content.addSubview_(seg)
            self._segs.append(seg)

        panel.setContentView_(content)
        self._watcher = _PanelWatcher.alloc().initWithBar_(self)
        panel.setDelegate_(self._watcher)
        self._panel, self._dot, self._label = panel, dot, label

    def set_state(self, state):
        """Update the pill, showing or hiding it to match."""
        if not self.enabled:
            return
        self._state = state
        label, color = STATES.get(state, STATES["idle"])

        def do():
            try:
                if self._panel is None:
                    self._build()
                r, g, b = color
                self._dot.layer().setBackgroundColor_(
                    AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1).CGColor()
                )
                self._label.setStringValue_(label)
                if state == "idle" and not self.always_visible:
                    self._panel.orderOut_(None)
                else:
                    # orderFrontRegardless: never activate the app to show it.
                    self._panel.orderFrontRegardless()
            except Exception:
                self._panel = None
        _on_main(do)

    def set_draggable(self, on):
        """Let the bar be dragged, at the cost of swallowing clicks.

        Only ever on while the user is deliberately repositioning it.
        """
        self._drag_mode = bool(on)

        def do():
            try:
                if self._panel is None:
                    self._build()
                self._panel.setIgnoresMouseEvents_(not self._drag_mode)
                if self._drag_mode:
                    self._panel.orderFrontRegardless()
                elif self._state == "idle" and not self.always_visible:
                    self._panel.orderOut_(None)
            except Exception:
                pass
        _on_main(do)

    def _remember_position(self):
        try:
            frame = self._panel.frame()
            pos = [float(frame.origin.x), float(frame.origin.y)]
        except Exception:
            return
        self.position = pos
        if self.on_move:
            try:
                self.on_move(pos)
            except Exception:
                pass

    def set_level(self, level):
        """Light the meter to match the mic input.

        Green in the usable band, amber when you're too quiet to transcribe
        cleanly, red when you're clipping.
        """
        if not self.enabled or self._panel is None or not self._segs:
            return
        level = max(0.0, min(1.0, float(level)))
        lit = int(round(level * SEGMENTS))
        if level > 0 and lit == 0:
            lit = 1
        if level >= HOT:
            color = RED
        elif level < QUIET:
            color = AMBER
        else:
            color = GREEN

        def do():
            try:
                for i, seg in enumerate(self._segs):
                    if i < lit:
                        # The top of the range always reads red, even mid-phrase.
                        c = RED if (i / SEGMENTS) >= HOT else color
                        rgba = (c[0], c[1], c[2], 1.0)
                    else:
                        rgba = OFF
                    seg.layer().setBackgroundColor_(
                        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgba).CGColor()
                    )
            except Exception:
                pass
        _on_main(do)

    def clear_level(self):
        self.set_level(0.0)

    def hide(self):
        def do():
            if self._panel is not None:
                self._panel.orderOut_(None)
        _on_main(do)
