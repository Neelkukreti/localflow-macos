"""Headless check of the wheel-click grammar (double-click starts, click stops,
a lone click is replayed to the app underneath).

Run: ./.venv/bin/python test_click.py   — no mic, no menu bar, no real clicks.
"""

from mouse_trigger import ClickTrigger, PASS, SWALLOW, DEFER


class FakeTimer:
    live = []
    def __init__(self, delay, fn): self.delay, self.fn, self.cancelled = delay, fn, False
    def start(self): FakeTimer.live.append(self)
    def cancel(self): self.cancelled = True
    @classmethod
    def fire_all(cls):
        for t in list(cls.live):
            cls.live.remove(t)
            if not t.cancelled: t.fn()


class Clock:
    t = 0.0
    def __call__(self): return Clock.t


def build(active=True):
    log = []
    Clock.t = 0.0
    FakeTimer.live.clear()
    trig = ClickTrigger(
        on_start=lambda: log.append("start"), on_stop=lambda: log.append("stop"),
        is_active=lambda: active, replay=lambda: log.append("replay"),
        double_seconds=0.35, clock=Clock(), timer=FakeTimer,
    )
    return trig, log


fails = []
def check(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, got, "" if ok else f"(want {want})")
    if not ok: fails.append(name)


# 1. a lone middle click is held back, then handed to the app under the cursor
t, log = build()
check("single click: press deferred", t.down(), DEFER)
Clock.t = 0.08
check("single click: release deferred", t.up(), DEFER)
check("single click: nothing started", log, [])
FakeTimer.fire_all()
check("single click: replayed to the app", log, ["replay"])

# 2. double-click starts listening and never reaches the app
t, log = build()
t.down(); Clock.t = 0.08; t.up(); Clock.t = 0.2
check("double-click: second press swallowed", t.down(), SWALLOW)
check("double-click: starts listening", log, ["start"])
check("double-click: its release swallowed", t.up(), SWALLOW)
FakeTimer.fire_all()
check("double-click: first click not replayed", log, ["start"])

# 3. one click while listening finishes the dictation, and is swallowed too
Clock.t = 6.0
check("stop click swallowed", t.down(), SWALLOW)
check("stop click transcribes", log, ["start", "stop"])
check("stop release swallowed", t.up(), SWALLOW)

# 4. while transcribing, clicks belong to the app
t, log = build(active=False)
check("busy: press passes through", t.down(), PASS)
check("busy: release passes through", t.up(), PASS)
check("busy: nothing started", log, [])

# 5. a slow second click is just another plain click
t, log = build()
t.down(); Clock.t = 0.08; t.up(); FakeTimer.fire_all()
check("slow double: first click replayed", log, ["replay"])
Clock.t = 0.9
check("slow double: second press deferred too", t.down(), DEFER)
check("slow double: still not listening", log, ["replay"])

# 6. cancelling a dictation clears the state
t, log = build()
t.down(); Clock.t = 0.08; t.up(); t.reset(); FakeTimer.fire_all()
check("reset: no replay, no start", log, [])
Clock.t = 2.0
check("reset: next click starts fresh", t.down(), DEFER)

# 7. the replay plumbing itself: copying the CGEvent, tagging it, posting it in
#    order. CGEventPost is stubbed so no click reaches any app.
import Quartz
import mouse_trigger

posted = []
real_post = Quartz.CGEventPost
mouse_trigger.Quartz.CGEventPost = lambda tap, e: posted.append(e)
try:
    lis = mouse_trigger.MiddleClickListener(lambda replay: build()[0], pass_through=True)
    where = Quartz.CGPointMake(400, 400)
    for kind in (Quartz.kCGEventOtherMouseDown, Quartz.kCGEventOtherMouseUp):
        lis._hold(Quartz.CGEventCreateMouseEvent(None, kind, where, 2))
    lis.replay()
    check("replay: press and release posted in order", len(posted), 2)
    check("replay: both tagged as ours", [
        Quartz.CGEventGetIntegerValueField(e, Quartz.kCGEventSourceUserData) for e in posted
    ], [mouse_trigger.REPLAY_TAG, mouse_trigger.REPLAY_TAG])
    check("replay: kept the original button", [
        Quartz.CGEventGetIntegerValueField(e, Quartz.kCGMouseEventButtonNumber) for e in posted
    ], [2, 2])
    check("replay: echo guard armed", lis._replay_until > 0, True)
    check("replay: nothing left held", lis._held, [])
    posted.clear()
    lis.replay()
    check("replay: nothing to replay twice", posted, [])
finally:
    mouse_trigger.Quartz.CGEventPost = real_post

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
raise SystemExit(1 if fails else 0)
