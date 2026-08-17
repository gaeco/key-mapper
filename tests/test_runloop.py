"""Regression tests for the interruptible run loop.

The bug being guarded against: `CFRunLoopRun()` parks in `mach_msg`, so a Python
signal handler does not execute and neither Ctrl-C nor a timer can stop it -- the
process has to be SIGKILLed. `run_until_interrupted` slices the loop so signals
are serviced.

These need pyobjc and macOS; they skip elsewhere so the pure logic tests stay
portable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

pytest.importorskip("CoreFoundation", reason="requires pyobjc on macOS")

from CoreFoundation import (  # noqa: E402
    CFAbsoluteTimeGetCurrent,
    CFRunLoopAddTimer,
    CFRunLoopGetCurrent,
    CFRunLoopRemoveTimer,
    CFRunLoopTimerCreate,
    kCFRunLoopCommonModes,
)

from keymapper.runloop import run_until_interrupted  # noqa: E402


@pytest.fixture
def parked_loop():
    """Attach a source to the run loop, standing in for the event tap.

    The timer is removed on teardown: pytest runs every test on the same thread,
    hence the same run loop, so a leaked source changes the behaviour of later
    tests.
    """
    timer = CFRunLoopTimerCreate(
        None, CFAbsoluteTimeGetCurrent() + 9999, 9999, 0, 0, lambda *_: None, None
    )
    loop = CFRunLoopGetCurrent()
    CFRunLoopAddTimer(loop, timer, kCFRunLoopCommonModes)
    yield timer
    CFRunLoopRemoveTimer(loop, timer, kCFRunLoopCommonModes)


def test_duration_stops_the_loop(parked_loop):
    start = time.monotonic()
    reason = run_until_interrupted(duration=1.0)
    elapsed = time.monotonic() - start
    assert reason == "duration"
    assert 0.8 < elapsed < 2.5, f"expected ~1s, got {elapsed:.2f}s"


def test_sigint_breaks_a_parked_loop(parked_loop):
    """The actual regression: SIGINT must escape a loop with no events arriving."""
    subprocess.Popen(["/bin/sh", "-c", f"sleep 0.5; kill -INT {os.getpid()}"])
    start = time.monotonic()
    reason = run_until_interrupted(duration=15.0)
    elapsed = time.monotonic() - start
    assert reason == "interrupted", f"SIGINT did not break the loop (got {reason!r})"
    assert elapsed < 3.0, f"took {elapsed:.2f}s to notice SIGINT"


def test_on_tick_can_stop_the_loop():
    """The tap-health hook must be able to end the loop."""
    calls = []

    def unhealthy():
        calls.append(1)
        return False

    reason = run_until_interrupted(duration=30.0, on_tick=unhealthy)
    assert reason == "unhealthy"
    assert len(calls) == 1


def test_on_tick_true_keeps_running(parked_loop):
    reason = run_until_interrupted(duration=1.0, on_tick=lambda: True)
    assert reason == "duration"


def test_signal_handlers_are_restored(parked_loop):
    """The loop must not leave its own SIGINT handler installed afterwards."""
    import signal

    original = signal.getsignal(signal.SIGINT)
    run_until_interrupted(duration=0.3)
    assert signal.getsignal(signal.SIGINT) is original
