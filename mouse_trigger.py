"""Middle-mouse (scroll-wheel click) trigger.

Three gestures, the same ones the hold key has:

  press and hold      talk while held, transcribes on release
  double-click        hands-free; one more click finishes
  plain single click  not ours — replayed to the app underneath

A plain single click has to keep belonging to whatever is under the cursor
(middle-click still opens and closes browser tabs), so the tap swallows every
middle click and replays a lone one once the double-click window lapses. Clicks
that drive dictation are never replayed, so starting or stopping a dictation
leaves no stray tab behind.
"""

import os
import threading
import time

import Quartz

# Opt-in diagnostics. The trigger fails invisibly when it fails at all — the tap
# either never gets created or quietly decides a click isn't ours — so there has
# to be somewhere to look. Enabled by trigger.debug_log in config.json.
LOG_PATH = os.path.expanduser("~/Library/Logs/LocalFlow-trigger.log")
_log_enabled = False


def set_logging(on):
    global _log_enabled
    _log_enabled = bool(on)


def log(message):
    if not _log_enabled:
        return
    try:
        with open(LOG_PATH, "a") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {message}\n")
    except OSError:
        pass

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
                 clock=time.monotonic, timer=threading.Timer, hold_seconds=0.35,
                 drop=None):
        self.on_start = on_start      # start listening
        self.on_stop = on_stop        # finish and transcribe
        self.is_active = is_active    # () -> False while a dictation is being processed
        self.replay = replay          # send a held-back single click on to the system
        self.drop = drop or (lambda: None)  # forget a held-back click; it was ours
        self.double_seconds = double_seconds
        self.hold_seconds = hold_seconds
        self._clock = clock
        self._timer = timer
        self.listening = False
        self._lock = threading.RLock()
        self._pending = None          # timer running while a first click is held back
        self._hold = None             # timer that turns a long press into hold-to-talk
        self._holding = False         # recording because the button is still down
        self._down = False
        self._first_down_at = 0.0
        self._swallow_up = False      # the release of a click we acted on

    def down(self):
        with self._lock:
            self._down = True
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
            # Held down past the threshold and it's push-to-talk, not a click.
            self._hold = self._timer(self.hold_seconds, self._fire_hold)
            self._hold.daemon = True
            self._hold.start()
            return DEFER

    def up(self):
        with self._lock:
            self._down = False
            self._cancel_hold()
            if self._holding:       # push-to-talk: releasing finishes the dictation
                self._holding = False
                self.listening = False
                log("release -> transcribing")
                self.on_stop()
                return SWALLOW
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

    def _fire_hold(self):
        """The button is still down past hold_seconds: talk while it's held."""
        with self._lock:
            self._hold = None
            if not self._down or self.listening or not self.is_active():
                log(f"hold not taken (down={self._down}, listening={self.listening}, "
                    f"active={self.is_active()})")
                return
            log("hold taken -> push-to-talk")
            self._cancel_pending()
            self.drop()          # the held-back press was ours, don't replay it
            self._holding = True
            self.listening = True
            self.on_start()

    def _cancel_hold(self):
        if self._hold is not None:
            self._hold.cancel()
            self._hold = None

    def _cancel_pending(self):
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None

    def reset(self):
        """Forget any in-flight click state (used when a dictation is cancelled)."""
        with self._lock:
            self._cancel_pending()
            self._cancel_hold()
            self.listening = False
            self._holding = False
            self._down = False
            self._swallow_up = False


class MiddleClickListener(threading.Thread):
    def __init__(self, trigger_factory, pass_through=True):
        super().__init__(daemon=True)
        self.pass_through = pass_through
        self.trigger = trigger_factory(self.replay)
        # The grammar needs to discard a held-back press once it decides the
        # press was push-to-talk rather than a click meant for another app.
        self.trigger.drop = self._drop_held
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
            log("tap was disabled by macOS — re-enabling")
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
                log(f"down -> {verdict} (active={self.trigger.is_active()}, "
                    f"listening={self.trigger.listening})")
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
            log("TAP CREATION FAILED — no Accessibility/Input Monitoring")
            if getattr(self, "on_error", None):
                self.on_error("mouse wheel")
            return
        log(f"tap created (pass_through={self.pass_through})")
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
