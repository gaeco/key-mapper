"""Interruptible CFRunLoop driver.

`CFRunLoopRun()` blocks in `mach_msg` indefinitely. A Python signal handler set
with `signal.signal` only executes when the interpreter next regains control, so
while the run loop is parked with no events arriving, Ctrl-C and SIGALRM appear
to do nothing at all -- the process has to be SIGKILLed. This was observed
directly: `probe --duration 3` had to be killed by an external watchdog.

Driving the loop in short slices with `CFRunLoopRunInMode` returns control to
the interpreter regularly, so signal handlers run and a stop flag can be checked.
Taps registered against `kCFRunLoopCommonModes` are serviced in the default mode,
so slicing does not cost any events.
"""

from __future__ import annotations

import signal
import time

from collections.abc import Callable

from CoreFoundation import (
    CFRunLoopRunInMode,
    kCFRunLoopDefaultMode,
    kCFRunLoopRunStopped,
)

#: How long each slice parks for. Small enough that Ctrl-C feels instant, large
#: enough that the wakeups are irrelevant next to keyboard event rates.
SLICE_SECONDS = 0.25

#: How often `on_tick` is invoked. Measured in slices rather than seconds so the
#: loop needs no second clock.
TICK_EVERY_SLICES = 20  # ~5s at the default slice length


def run_until_interrupted(
    duration: float | None = None,
    on_tick: Callable[[], bool] | None = None,
) -> str:
    """Drive the current run loop until SIGINT/SIGTERM, or `duration` elapses.

    Args:
        duration: stop after this many seconds; None runs until signalled.
        on_tick: called roughly every 5s. Return False to stop the loop -- used
            to bail out if the event tap has died.

    Returns the reason for stopping: "interrupted", "duration", "stopped" or
    "unhealthy".

    Note: with no sources attached, `CFRunLoopRunInMode` returns
    `kCFRunLoopRunTimedOut` (not `...Finished`), so an absent tap cannot be
    detected from the return value -- that is what `on_tick` is for.
    """
    stop_reason: list[str] = []

    def on_signal(signum, _frame):
        stop_reason.append("interrupted")

    previous = {
        signal.SIGINT: signal.signal(signal.SIGINT, on_signal),
        signal.SIGTERM: signal.signal(signal.SIGTERM, on_signal),
    }
    deadline = time.monotonic() + duration if duration else None
    slices = 0
    try:
        while not stop_reason:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stop_reason.append("duration")
                    break
                slice_len = min(SLICE_SECONDS, remaining)
            else:
                slice_len = SLICE_SECONDS
            if CFRunLoopRunInMode(kCFRunLoopDefaultMode, slice_len, False) == (
                kCFRunLoopRunStopped
            ):
                stop_reason.append("stopped")
                break
            slices += 1
            if on_tick is not None and slices % TICK_EVERY_SLICES == 0:
                if not on_tick():
                    stop_reason.append("unhealthy")
                    break
    finally:
        for sig, handler in previous.items():
            if handler is not None:
                signal.signal(sig, handler)
    return stop_reason[0] if stop_reason else "stopped"
