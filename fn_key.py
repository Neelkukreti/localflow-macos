"""Listen-only Quartz event tap for holding the Fn (🌐) key.

pynput can't tell an Fn press from a release on macOS, so we read the
SecondaryFn flag straight off each flags-changed event instead.
"""

import threading

import Quartz

FN_KEYCODES = {63, 179}
FN_FLAG = Quartz.kCGEventFlagMaskSecondaryFn


class FnListener(threading.Thread):
    def __init__(self, on_down, on_up, on_other_key, on_error=None):
        super().__init__(daemon=True)
        self.on_error = on_error
        self.on_down = on_down
        self.on_up = on_up
        self.on_other_key = on_other_key  # a key pressed while Fn is held
        self.fn_down = False
        self._tap = None

    def _callback(self, _proxy, event_type, event, _refcon):
        if event_type in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
            Quartz.CGEventTapEnable(self._tap, True)
            return event
        try:
            if event_type == Quartz.kCGEventFlagsChanged:
                code = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                if code in FN_KEYCODES:
                    down = bool(Quartz.CGEventGetFlags(event) & FN_FLAG)
                    if down != self.fn_down:
                        self.fn_down = down
                        (self.on_down if down else self.on_up)()
            elif event_type == Quartz.kCGEventKeyDown and self.fn_down:
                self.on_other_key()
        except Exception as e:  # never let a callback error kill the tap
            print(f"fn_key: {e}")
        return event

    def run(self):
        mask = Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            self._callback,
            None,
        )
        if self._tap is None:
            # Silent failure here is what makes "Fn does nothing" so confusing:
            # tell the app so it can say which permission is missing.
            if self.on_error:
                self.on_error("Fn key")
            return
        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        Quartz.CFRunLoopRun()


# pyobjc resolves Quartz attributes lazily and races when two tap threads touch
# the same symbol at once (KeyError in objc._lazyimport). Resolve everything the
# thread needs here, at import time, on the main thread.
_WARMED = (
    Quartz.CFRunLoopRun, Quartz.CFRunLoopGetCurrent, Quartz.CFRunLoopAddSource,
    Quartz.CFMachPortCreateRunLoopSource, Quartz.CGEventTapCreate, Quartz.CGEventTapEnable,
    Quartz.CGEventMaskBit, Quartz.CGEventGetIntegerValueField, Quartz.CGEventGetFlags,
    Quartz.kCFRunLoopCommonModes, Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
    Quartz.kCGEventTapOptionDefault, Quartz.kCGEventTapOptionListenOnly,
    Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput,
    Quartz.kCGEventFlagsChanged, Quartz.kCGEventKeyDown, Quartz.kCGKeyboardEventKeycode,
    Quartz.kCGEventFlagMaskSecondaryFn, Quartz.kCGEventOtherMouseDown,
    Quartz.kCGEventOtherMouseUp, Quartz.kCGEventOtherMouseDragged,
    Quartz.kCGMouseEventButtonNumber,
)
