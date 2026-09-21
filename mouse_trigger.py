"""Middle-mouse (scroll-wheel click) trigger.

Double-click the wheel to start listening; click once to finish. A plain single
click still belongs to whatever app is underneath — middle-click must keep
opening and closing Chrome tabs — so the tap swallows every middle click and
replays a lone one to the system once the double-click window lapses. The clicks
that drive dictation are never replayed, so starting or stopping a dictation
leaves no stray tab behind.
"""

import threading
import time

import Quartz

MIDDLE_BUTTON = 2
# Marks the clicks we post ourselves, so the tap ignores its own replays.
REPLAY_TAG = 0x10CA1F10

PASS = "pass"        # hand the click straight to the app under the cursor
SWALLOW = "swallow"  # this click drove dictation; the app must not see it
DEFER = "defer"      # hold it back until we know it isn't half of a double-click


class ClickTrigger:
    """The click grammar: double-click starts, a single click stops.

    Kept free of rumps and Quartz so it can be tested on its own. Every method
    returns what the listener should do with the event.
    """

    def __init__(self, on_start, on_stop, is_active, replay, double_seconds=0.35,
                 clock=time.monotonic, timer=threading.Timer):
        self.on_start = on_start      # start listening (hands-free)
        self.on_stop = on_stop        # finish and transcribe
        self.is_active = is_active    # () -> False while a dictation is being processed
        self.replay = replay          # send a held-back single click on to the system
        self.double_seconds = double_seconds
        self._clock = clock
        self._timer = timer
        self.listening = False
        self._lock = threading.RLock()
        self._pending = None          # timer running while a first click is held back
        self._first_down_at = 0.0
        self._swallow_up = False      # the release of a click we acted on

    def down(self):
        with self._lock:
            if self.listening:  # a click while listening ends the dictation
                self.listening = False
                self._swallow_up = True
                self.on_stop()
                return SWALLOW
            if self._pending is not None and self._clock() - self._first_down_at <= self.double_seconds:
                self._cancel_pending()  # second click of a double-click: don't replay the first
                self._swallow_up = True
                self.listening = True
                self.on_start()
                return SWALLOW
            if not self.is_active():
                return PASS  # busy transcribing — the app may as well have the click
            self._first_down_at = self._clock()
            self._pending = self._timer(self.double_seconds, self._fire_pending)
            self._pending.daemon = True
            self._pending.start()
            return DEFER

    def up(self):
        with self._lock:
            if self._swallow_up:
                self._swallow_up = False
                return SWALLOW
            if self._pending is not None:
                return DEFER  # replayed together with its press, if no second click lands
            return PASS

    def _fire_pending(self):
        with self._lock:
            self._pending = None
            if not self.listening:
                self.replay()  # it was a plain middle click after all

    def _cancel_pending(self):
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None

    def reset(self):
        """Forget any in-flight click state (used when a dictation is cancelled)."""
        with self._lock:
            self._cancel_pending()
            self.listening = False
            self._swallow_up = False


class MiddleClickListener(threading.Thread):
    def __init__(self, trigger_factory, pass_through=True):
        super().__init__(daemon=True)
        self.pass_through = pass_through
        self.trigger = trigger_factory(self.replay)
        self._held = []
        self._held_lock = threading.Lock()
        self._replay_until = 0.0  # events echoed back to us right after a replay
        self._tap = None

    # --- holding back and replaying a plain middle click ---

    def _hold(self, event):
        if not self.pass_through:
            return
        with self._held_lock:
            self._held.append(Quartz.CGEventCreateCopy(event))

    def replay(self):
        with self._held_lock:
            events, self._held = self._held, []
        if not events:
            return
        # The tag below marks our own clicks, but if it ever failed to survive
        # CGEventPost we would re-defer and re-post forever, so also ignore
        # middle clicks for a moment after posting.
        self._replay_until = time.monotonic() + 0.25
        for e in events:
            Quartz.CGEventSetIntegerValueField(e, Quartz.kCGEventSourceUserData, REPLAY_TAG)
            Quartz.CGEventPost(Quartz.kCGSessionEventTap, e)
            time.sleep(0.008)  # keep the press and release in order

    def _drop_held(self):
        with self._held_lock:
            self._held = []

    # --- the tap ---

    def _callback(self, _proxy, event_type, event, _refcon):
        if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self._tap, True)
            return event
        try:
            if (Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUserData) == REPLAY_TAG
                    or time.monotonic() < self._replay_until):
                return event  # our own replay on its way to the app
            if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventButtonNumber) != MIDDLE_BUTTON:
                return event
            if event_type == Quartz.kCGEventOtherMouseDown:
                verdict = self.trigger.down()
            elif event_type == Quartz.kCGEventOtherMouseUp:
                verdict = self.trigger.up()
            else:
                return event
            if verdict == DEFER:
                self._hold(event)
                return None if self.pass_through else event
            if verdict == SWALLOW:
                self._drop_held()
                return None
            return event
        except Exception as e:  # never let a callback error kill the tap
            print(f"mouse_trigger: {e}")
            return event

    def run(self):
        mask = (
            Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseDown)
            | Quartz.CGEventMaskBit(Quartz.kCGEventOtherMouseUp)
        )
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault, mask, self._callback, None,
        )
        if self._tap is None:
            # No Accessibility grant: watch without swallowing. Dictation still
            # works, but the clicks that drive it also reach the app underneath.
            print("mouse_trigger: no suppressing tap (grant Accessibility); listening only")
            self.pass_through = False
            self._tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly, mask, self._callback, None,
            )
        if self._tap is None:
            if getattr(self, "on_error", None):
                self.on_error("mouse wheel")
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        Quartz.CFRunLoopRun()


# pyobjc resolves Quartz attributes lazily and races when two tap threads touch
# the same symbol at once (KeyError in objc._lazyimport). Resolve everything the
# thread needs here, at import time, on the main thread.
_WARMED = (
    Quartz.CFRunLoopRun, Quartz.CFRunLoopGetCurrent, Quartz.CFRunLoopAddSource,
    Quartz.CFMachPortCreateRunLoopSource, Quartz.CGEventTapCreate, Quartz.CGEventTapEnable,
    Quartz.CGEventMaskBit, Quartz.CGEventGetIntegerValueField, Quartz.CGEventSetIntegerValueField,
    Quartz.CGEventCreateCopy, Quartz.CGEventPost,
    Quartz.kCFRunLoopCommonModes, Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
    Quartz.kCGEventTapOptionDefault, Quartz.kCGEventTapOptionListenOnly,
    Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput,
    Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp,
    Quartz.kCGMouseEventButtonNumber, Quartz.kCGEventSourceUserData,
)
