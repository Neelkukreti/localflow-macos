"""The hold key's gestures, as a small state machine with no AppKit in it.

    hold                  talk while held, transcribe on release
    tap, tap              lock hands-free; the next tap finishes
    tap, then HOLD        just a hold — NOT a lock

That last line is the bug this exists to fix. A second press inside the
double-tap window used to lock hands-free unconditionally, so a false-start
tap followed by a real press-and-hold locked the dictation, and releasing did
nothing: "it starts listening and never stops". Now the second press only
locks if it is itself a *tap*; if it's held past hold_seconds, releasing it
finishes the dictation like any other hold.

Kept free of Quartz and rumps (like the old click grammar) so every sequence
can be tested with a fake clock — see test_features.py.
"""

import threading
import time


class HoldGrammar:
    def __init__(self, on_start, on_finish, on_lock, is_recording, is_busy,
                 hold_seconds=0.35, double_seconds=0.35,
                 clock=time.monotonic, timer=threading.Timer):
        self.on_start = on_start          # begin recording
        self.on_finish = on_finish        # stop and transcribe
        self.on_lock = on_lock            # switch the running recording to hands-free
        self.is_recording = is_recording
        self.is_busy = is_busy
        self.hold_seconds = hold_seconds
        self.double_seconds = double_seconds
        self._clock = clock
        self._timer = timer
        self._lock = threading.RLock()
        self.held = False                 # the key is down, as far as we know
        self.locked = False               # hands-free
        self._down_at = 0.0
        self._second_at = None            # when the would-be second tap went down
        self._tap_timer = None

    # ---------- events ----------

    def down(self):
        with self._lock:
            if self.held:
                return                    # a repeat down; ignore
            self.held = True
            if self.is_busy():
                return
            if self.locked:
                self.locked = False
                if self.is_recording():
                    self._cancel_tap()
                    self.on_finish()      # the tap that ends a hands-free dictation
                    return
                # A stale lock (the dictation ended some other way): fresh press.
            if self._tap_timer is not None:
                self._cancel_tap()
                if self.is_recording():
                    # Second press inside the window. Lock now, so a genuine
                    # double-tap keeps listening — but remember when, because if
                    # this press turns out to be a hold, it isn't a lock at all.
                    self.locked = True
                    self._second_at = self._clock()
                    self.on_lock()
                    return
            if not self.is_recording():
                self._down_at = self._clock()
                self.on_start()

    def up(self):
        with self._lock:
            if not self.held:
                return
            self.held = False
            if self._second_at is not None:
                held_for = self._clock() - self._second_at
                self._second_at = None
                if held_for >= self.hold_seconds:
                    # tap, then HOLD: a hold after all. Release finishes it.
                    self.locked = False
                    if self.is_recording():
                        self.on_finish()
                return                    # a genuine double-tap: stay locked
            if self.locked or not self.is_recording():
                return
            if self._clock() - self._down_at >= self.hold_seconds:
                self.on_finish()          # an ordinary hold
                return
            # A short tap: wait to see whether a second one follows.
            self._tap_timer = self._timer(self.double_seconds, self._settle)
            self._tap_timer.daemon = True
            self._tap_timer.start()

    def _settle(self):
        with self._lock:
            self._tap_timer = None
            if self.is_recording() and not self.locked:
                self.on_finish()          # a lone tap, nothing followed

    def _cancel_tap(self):
        if self._tap_timer is not None:
            self._tap_timer.cancel()
            self._tap_timer = None

    def reset(self):
        """Forget everything — the dictation was cancelled or ended elsewhere."""
        with self._lock:
            self._cancel_tap()
            self.locked = False
            self._second_at = None
            # `held` is left alone: the key may genuinely still be down.
