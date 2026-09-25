"""The floating status pill — a tiny black capsule, like Wispr Flow's.

Deliberately almost invisible. Idle it's a small black sliver (only if
flow_bar_always is on; otherwise it's hidden); while you dictate it widens just
enough to hold a row of level bars, and that's all there is to it. No text, no
dot: the menu-bar logo already says what state you're in.

Three things still matter and are easy to get wrong:

  * it must NOT take focus — a normal window would steal key focus from the app
    you're dictating into and the paste would land in the wrong place. Hence a
    non-activating panel.
  * it must NOT eat clicks — ignoresMouseEvents, except during an explicit
    20-second "move" from Settings.
  * AppKit on the main thread only — everything goes through _on_main.
"""

import threading

try:
    import objc
    import AppKit
    from Foundation import NSObject, NSOperationQueue

    _WARMED = (
        AppKit.NSPanel, AppKit.NSView, AppKit.NSColor, AppKit.NSMakeRect,
        AppKit.NSScreen, AppKit.NSBackingStoreBuffered,
        AppKit.NSWindowStyleMaskBorderless, AppKit.NSWindowStyleMaskNonactivatingPanel,
        AppKit.NSStatusWindowLevel, NSOperationQueue,
    )
    _HAVE = True
except Exception:  # pragma: no cover — headless
    objc = AppKit = NSOperationQueue = None
    NSObject = object
    _WARMED, _HAVE = (), False

STATES = ("idle", "recording", "hands_free", "working", "command")

# Sizes in points. Idle is a sliver; active is just wide enough for the meter.
IDLE_W, IDLE_H = 34.0, 7.0
ACTIVE_W, ACTIVE_H = 58.0, 16.0
BOTTOM = 14.0              # distance above the bottom of the visible screen

SEGMENTS = 9
SEG_W, SEG_GAP = 2.5, 2.5
SEG_MIN_H, SEG_MAX_H = 2.5, 9.0

# Below QUIET Whisper starts inventing "Thank you." on the silence; above HOT
# the mic is clipping and consonants mush.
QUIET, HOT = 0.035, 0.80
GOOD = (1.0, 1.0, 1.0, 0.92)       # white bars: level is fine
LOW = (1.0, 1.0, 1.0, 0.30)        # dim: too quiet to transcribe well
RED = (1.0, 0.33, 0.30, 1.0)       # clipping
OFF = (1.0, 1.0, 1.0, 0.14)

# A tint on the border while busy, so "transcribing" is distinguishable from
# "listening" without any text.
BORDER = {
    "recording": (1.0, 1.0, 1.0, 0.16),
    "hands_free": (1.0, 0.62, 0.18, 0.55),
    "command": (0.62, 0.45, 1.0, 0.60),
    "working": (0.45, 0.58, 1.0, 0.55),
    "idle": (1.0, 1.0, 1.0, 0.10),
}


def _on_main(fn):
    if not _HAVE or threading.current_thread() is threading.main_thread():
        fn()
    else:
        NSOperationQueue.mainQueue().addOperationWithBlock_(fn)


def _cg(rgba):
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgba).CGColor()


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
        self.position = position        # saved centre [x, y] from a previous drag
        self.on_move = on_move
        self._drag_mode = False
        self._watcher = None
        self._panel = None
        self._pill = None
        self._segs = []
        self._state = "idle"
        self._lit = -1
        self._centre = None             # (x, y) the pill stays centred on as it resizes

    # ---------- geometry ----------

    def _default_centre(self):
        f = AppKit.NSScreen.mainScreen().visibleFrame()
        return (f.origin.x + f.size.width / 2.0, f.origin.y + BOTTOM + ACTIVE_H / 2.0)

    def _saved_centre(self):
        if not self.position:
            return None
        try:
            x, y = float(self.position[0]), float(self.position[1])
        except (TypeError, ValueError, IndexError):
            return None
        # Only on a screen that still exists — monitors come and go.
        for sc in (AppKit.NSScreen.screens() or []):
            f = sc.frame()
            if f.origin.x <= x <= f.origin.x + f.size.width and \
               f.origin.y <= y <= f.origin.y + f.size.height:
                return (x, y)
        return None

    def _frame_for(self, w, h):
        cx, cy = self._centre
        return AppKit.NSMakeRect(cx - w / 2.0, cy - h / 2.0, w, h)

    # ---------- build ----------

    def _build(self):
        self._centre = self._saved_centre() or self._default_centre()
        rect = self._frame_for(ACTIVE_W, ACTIVE_H)

        panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False,
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setLevel_(AppKit.NSStatusWindowLevel)
        panel.setIgnoresMouseEvents_(not self._drag_mode)
        panel.setMovableByWindowBackground_(True)
        panel.setReleasedWhenClosed_(False)
        panel.setHidesOnDeactivate_(False)
        panel.setCollectionBehavior_(1 << 0 | 1 << 8)   # every Space, over full-screen apps

        content = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, ACTIVE_W, ACTIVE_H))
        content.setWantsLayer_(True)
        pill = content.layer()
        pill.setBackgroundColor_(_cg((0.0, 0.0, 0.0, 0.92)))
        pill.setBorderWidth_(0.75)
        pill.setBorderColor_(_cg(BORDER["idle"]))

        self._segs = []
        for _ in range(SEGMENTS):
            seg = AppKit.NSView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, SEG_W, SEG_MIN_H))
            seg.setWantsLayer_(True)
            seg.layer().setCornerRadius_(SEG_W / 2.0)
            seg.layer().setBackgroundColor_(_cg(OFF))
            content.addSubview_(seg)
            self._segs.append(seg)

        panel.setContentView_(content)
        self._watcher = _PanelWatcher.alloc().initWithBar_(self)
        panel.setDelegate_(self._watcher)
        self._panel, self._pill = panel, pill

    def _apply_size(self, active):
        w, h = (ACTIVE_W, ACTIVE_H) if active else (IDLE_W, IDLE_H)
        self._panel.setFrame_display_(self._frame_for(w, h), True)
        self._panel.contentView().setFrame_(AppKit.NSMakeRect(0, 0, w, h))
        self._pill.setCornerRadius_(h / 2.0)
        for seg in self._segs:
            seg.setHidden_(not active)
        if active:
            self._layout_segs([SEG_MIN_H] * SEGMENTS)

    def _layout_segs(self, heights):
        total = SEGMENTS * SEG_W + (SEGMENTS - 1) * SEG_GAP
        x0 = (ACTIVE_W - total) / 2.0
        for i, (seg, h) in enumerate(zip(self._segs, heights)):
            seg.setFrame_(AppKit.NSMakeRect(x0 + i * (SEG_W + SEG_GAP), (ACTIVE_H - h) / 2.0, SEG_W, h))

    # ---------- state ----------

    def set_state(self, state):
        if not self.enabled:
            return
        self._state = state

        def do():
            try:
                if self._panel is None:
                    self._build()
                active = state != "idle"
                self._apply_size(active)
                self._pill.setBorderColor_(_cg(BORDER.get(state, BORDER["idle"])))
                if active or self.always_visible or self._drag_mode:
                    self._panel.orderFrontRegardless()   # never activates the app
                else:
                    self._panel.orderOut_(None)
            except Exception:
                self._panel = None
        _on_main(do)

    def set_level(self, level):
        """Bars rise with the input: white when the level is usable, dim when
        you're too quiet to transcribe cleanly, red when you're clipping."""
        if not self.enabled or self._panel is None or not self._segs:
            return
        level = max(0.0, min(1.0, float(level)))
        lit = int(round(level * SEGMENTS))
        if level > 0 and lit == 0:
            lit = 1
        if lit == self._lit:
            return          # nothing would change; don't wake the main thread
        self._lit = lit
        colour = RED if level >= HOT else (LOW if level < QUIET else GOOD)
        # A little bump in the middle, so it reads as a voice and not a gauge.
        shape = [0.55, 0.7, 0.85, 0.95, 1.0, 0.95, 0.85, 0.7, 0.55]

        def do():
            try:
                heights = []
                for i, seg in enumerate(self._segs):
                    on = i < lit
                    seg.layer().setBackgroundColor_(_cg(colour if on else OFF))
                    heights.append(SEG_MIN_H + (SEG_MAX_H - SEG_MIN_H) * shape[i] * level
                                   if on else SEG_MIN_H)
                self._layout_segs(heights)
            except Exception:
                pass
        _on_main(do)

    def clear_level(self):
        self._lit = -1
        self.set_level(0.0)

    # ---------- moving it ----------

    def set_draggable(self, on):
        """Let the pill be dragged, at the cost of swallowing clicks — only ever
        on while the user is deliberately repositioning it."""
        self._drag_mode = bool(on)

        def do():
            try:
                if self._panel is None:
                    self._build()
                self._panel.setIgnoresMouseEvents_(not self._drag_mode)
                if self._drag_mode:
                    self._apply_size(True)
                    self._pill.setBorderColor_(_cg((1.0, 1.0, 1.0, 0.6)))
                    self._panel.orderFrontRegardless()
                else:
                    self.set_state(self._state)
            except Exception:
                pass
        _on_main(do)

    def _remember_position(self):
        try:
            f = self._panel.frame()
            self._centre = (float(f.origin.x + f.size.width / 2.0),
                            float(f.origin.y + f.size.height / 2.0))
        except Exception:
            return
        self.position = list(self._centre)
        if self.on_move:
            try:
                self.on_move(self.position)
            except Exception:
                pass

    def hide(self):
        def do():
            if self._panel is not None:
                self._panel.orderOut_(None)
        _on_main(do)
