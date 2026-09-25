"""Headless checks of the hold-key gestures (hold_grammar.py).

Run: ./.venv/bin/python test_hold.py   — no keys, no mic, fake clock.
"""

from hold_grammar import HoldGrammar

fails = []
def check(name, got, want):
    ok = got == want
    print(("PASS " if ok else "FAIL ") + name, got, "" if ok else f"(want {want})")
    if not ok: fails.append(name)


class Clock:
    t = 0.0
    def __call__(self): return Clock.t


class FakeTimer:
    live = []
    def __init__(self, delay, fn): self.delay, self.fn, self.cancelled = delay, fn, False
    def start(self): FakeTimer.live.append(self)
    def cancel(self): self.cancelled = True
    @classmethod
    def fire(cls):
        for t in list(cls.live):
            cls.live.remove(t)
            if not t.cancelled: t.fn()


def build():
    Clock.t = 0.0
    FakeTimer.live.clear()
    st = {"rec": False, "busy": False, "log": []}
    def start(): st["rec"] = True; st["log"].append("start")
    def finish(): st["rec"] = False; st["log"].append("finish")
    def lock(): st["log"].append("lock")
    g = HoldGrammar(start, finish, lock, lambda: st["rec"], lambda: st["busy"],
                    hold_seconds=0.35, double_seconds=0.35, clock=Clock(), timer=FakeTimer)
    return g, st

def at(t): Clock.t = t


# plain hold
g, st = build()
at(0.0); g.down(); at(1.2); g.up()
check("hold: starts and finishes on release", st["log"], ["start", "finish"])

# single quick tap: finishes once the double-tap window passes
g, st = build()
at(0.0); g.down(); at(0.1); g.up()
check("tap: still listening inside the window", st["rec"], True)
FakeTimer.fire()
check("tap: finishes when no second tap comes", st["log"], ["start", "finish"])

# genuine double-tap locks hands-free; the next tap finishes
g, st = build()
at(0.0); g.down(); at(0.08); g.up(); at(0.2); g.down(); at(0.28); g.up()
check("double-tap: locks", g.locked, True)
check("double-tap: keeps listening after release", st["rec"], True)
FakeTimer.fire()
check("double-tap: the window lapsing doesn't stop a lock", st["rec"], True)
at(5.0); g.down(); at(5.1); g.up()
check("double-tap: next tap finishes", st["log"], ["start", "lock", "finish"])
check("double-tap: not recording afterwards", st["rec"], False)

# THE BUG: false-start tap, then a real press-and-hold
g, st = build()
at(0.0); g.down(); at(0.08); g.up()          # accidental tap
at(0.20); g.down()                            # real press, inside the window
at(2.50); g.up()                              # held for 2.3s, then released
check("tap-then-hold: release FINISHES (used to lock and never stop)", st["rec"], False)
check("tap-then-hold: not left locked", g.locked, False)
check("tap-then-hold: log", st["log"], ["start", "lock", "finish"])

# a stale lock (dictation ended elsewhere, e.g. Stream Deck or the cap) doesn't eat a press
g, st = build()
at(0.0); g.down(); at(0.08); g.up(); at(0.2); g.down(); at(0.28); g.up()   # locked
st["rec"] = False                                                         # ended elsewhere
at(3.0); g.down()
check("stale lock: next press starts a fresh dictation", st["rec"], True)
at(4.0); g.up()
check("stale lock: and its release finishes it", st["rec"], False)

# busy transcribing: presses are ignored, nothing is started
g, st = build()
st["busy"] = True
g.down(); at(1.0); g.up()
check("busy: nothing happens", st["log"], [])

# a missed release is recovered by the watchdog calling up()
g, st = build()
at(0.0); g.down()
at(1.5); g.up()        # what the watchdog does when the physical key is found up
check("watchdog up(): finishes a hold whose release was missed", st["rec"], False)

print("\n" + ("ALL PASS" if not fails else f"FAILURES: {fails}"))
raise SystemExit(1 if fails else 0)
