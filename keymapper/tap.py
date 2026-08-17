"""Event-tap lifecycle for the remapper.

Installs one active tap and rewrites matching events in place. Because every
rule preserves the keycode and only changes modifier bits, the callback can call
`CGEventSetFlags` and return the same event -- no `CGEventPost`, therefore no
risk of our own synthetic events re-entering our own tap.
"""

from __future__ import annotations

import sys
import time

import Quartz
from CoreFoundation import (
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    kCFRunLoopCommonModes,
)

from . import permissions
from .keycodes import describe_flags, event_name, key_name
from .remap import Hotkey, Ruleset
from .runloop import run_until_interrupted
from .source import SourceClassifier

TAP_LOCATIONS = {
    "session": Quartz.kCGSessionEventTap,
    "hid": Quartz.kCGHIDEventTap,
    "annotatedSession": Quartz.kCGAnnotatedSessionEventTap,
}

EVENT_MASK = (
    Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
    | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
    | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged)
)


class Remapper:
    """Owns the tap, the run loop, and the hot-path callback."""

    def __init__(
        self,
        ruleset: Ruleset,
        classifier: SourceClassifier,
        tap_location: str = "session",
        dry_run: bool = False,
        verbose: bool = False,
        pause_hotkey: Hotkey | None = None,
    ) -> None:
        if tap_location not in TAP_LOCATIONS:
            raise ValueError(
                f"unknown tap_location {tap_location!r};"
                f" expected one of {', '.join(TAP_LOCATIONS)}"
            )
        self.ruleset = ruleset
        self.classifier = classifier
        self.tap_location = tap_location
        self.dry_run = dry_run
        self.verbose = verbose
        self.pause_hotkey = pause_hotkey

        # Precomputed so the hot path can reject most events with one set lookup.
        self._watched_keycodes = ruleset.keycodes()

        self.paused = False
        self.remapped = 0
        self.skipped_source = 0
        self.skipped_paused = 0
        self.toggles = 0
        self.tap_reenables = 0

        self._tap = None
        self._source = None
        self._handler = None  # pyobjc does not retain the callable
        self._loop = None

    # -- pause toggle -----------------------------------------------------
    def _toggle(self, event_type, event):
        """Flip the pause flag and swallow the hotkey.

        Returning None deletes the event, so the frontmost app never sees the
        chord -- otherwise the toggle would double as whatever Ctrl+Opt+Shift+P
        means in that app. Both keyDown and keyUp are swallowed, so no app is
        left with a half-open chord.

        Only keyDown flips the state: acting on keyUp as well would toggle twice
        per press and land back where it started. Auto-repeat is swallowed but
        ignored, so holding the chord down does not thrash the flag.

        A listen-only tap (`--dry-run`) cannot delete events, so there the chord
        still reaches the app. The toggle itself works either way.
        """
        if event_type == Quartz.kCGEventKeyDown:
            repeat = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventAutorepeat
            )
            if not repeat:
                self.paused = not self.paused
                self.toggles += 1
                state = "PAUSED -- passing every key through" if self.paused else "RESUMED"
                print(f"\n  {state}\n", file=sys.stderr, flush=True)
        return None

    # -- hot path ---------------------------------------------------------
    def _handle(self, proxy, event_type, event, refcon):
        # macOS disables a tap whose callback is too slow, and delivers this
        # instead of a key event. Without re-enabling, the app goes silently
        # deaf -- the classic event-tap failure mode.
        if event_type in (
            Quartz.kCGEventTapDisabledByTimeout,
            Quartz.kCGEventTapDisabledByUserInput,
        ):
            self.tap_reenables += 1
            print(
                f"tap disabled ({event_name(event_type)}) -- re-enabling",
                file=sys.stderr,
                flush=True,
            )
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, True)
            return event

        # flagsChanged is passed through untouched by design; see remap.py.
        if event_type == Quartz.kCGEventFlagsChanged:
            return event

        keycode = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGKeyboardEventKeycode
        )

        # The toggle is checked before the watched-keycode reject (its key is not
        # one we remap) and before the pause check (otherwise pausing could never
        # be undone). Deliberately not subject to the source filter: it is a
        # control surface for whoever is at a keyboard, local or remote.
        if self.pause_hotkey is not None and keycode == self.pause_hotkey.keycode:
            flags = Quartz.CGEventGetFlags(event)
            if self.pause_hotkey.matches(event_type, keycode, flags):
                return self._toggle(event_type, event)

        if self.paused:
            if keycode in self._watched_keycodes:
                self.skipped_paused += 1
            return event

        if keycode not in self._watched_keycodes:
            return event

        flags = Quartz.CGEventGetFlags(event)
        new_flags = self.ruleset.decide(event_type, keycode, flags)
        if new_flags is None:
            return event

        # Origin check is deferred until after the cheap checks pass, so the pid
        # lookup only ever runs for the handful of keys we might rewrite.
        source_pid = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGEventSourceUnixProcessID
        )
        source_state = Quartz.CGEventGetIntegerValueField(
            event, Quartz.kCGEventSourceStateID
        )
        if not self.classifier.should_remap(source_pid, source_state):
            self.skipped_source += 1
            if self.verbose:
                proc = self.classifier.describe_pid(source_pid) or "-"
                print(
                    f"  skip  {event_name(event_type):8} "
                    f"{describe_flags(flags)}+{key_name(keycode)} "
                    f"(source pid={source_pid} {proc.rsplit('/', 1)[-1]})",
                    file=sys.stderr,
                    flush=True,
                )
            return event

        self.remapped += 1
        arrow = "would remap" if self.dry_run else "remap"
        print(
            f"  {arrow:11} {event_name(event_type):8} "
            f"{describe_flags(flags)}+{key_name(keycode)}"
            f" -> {describe_flags(new_flags)}+{key_name(keycode)}",
            file=sys.stderr,
            flush=True,
        )
        if not self.dry_run:
            Quartz.CGEventSetFlags(event, new_flags)
        return event

    # -- lifecycle --------------------------------------------------------
    def start(self) -> bool:
        """Create and arm the tap. Returns False if it could not be created."""
        self._handler = self._handle
        option = (
            Quartz.kCGEventTapOptionListenOnly
            if self.dry_run
            else Quartz.kCGEventTapOptionDefault
        )
        self._tap = Quartz.CGEventTapCreate(
            TAP_LOCATIONS[self.tap_location],
            Quartz.kCGHeadInsertEventTap,
            option,
            EVENT_MASK,
            self._handler,
            None,
        )
        if self._tap is None:
            return False
        self._source = CFMachPortCreateRunLoopSource(None, self._tap, 0)
        CFRunLoopAddSource(CFRunLoopGetCurrent(), self._source, kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        return True

    def _health_check(self) -> bool:
        """Periodic guard against becoming silently deaf.

        The in-callback re-enable handles the normal timeout case, but it can only
        fire if the callback is still being invoked. If the tap is disabled and no
        events are being delivered, nothing would ever notice. Returning False
        stops the run loop so the process exits visibly instead of pretending to
        work.
        """
        if self._tap is None:
            return False
        if not Quartz.CGEventTapIsEnabled(self._tap):
            print(
                "tap found disabled outside the callback -- re-enabling",
                file=sys.stderr,
                flush=True,
            )
            self.tap_reenables += 1
            Quartz.CGEventTapEnable(self._tap, True)
            if not Quartz.CGEventTapIsEnabled(self._tap):
                print(
                    "tap could not be re-enabled; stopping so this is not silent.",
                    file=sys.stderr,
                    flush=True,
                )
                return False
        return True

    def run(self) -> int:
        # Input Monitoring is checked up front because its absence does not stop
        # the tap being created -- it just stops keyDown/keyUp ever arriving,
        # which looks like the app silently doing nothing.
        problem = permissions.blocking_problem()
        if problem:
            print("\n".join(permissions.describe()), file=sys.stderr)
            print(f"\n{problem}", file=sys.stderr)
            if permissions.input_monitoring_access() == permissions.ACCESS_UNKNOWN:
                permissions.request_input_monitoring()
            return 2

        if not self.start():
            print(
                "Could not create the event tap despite permissions looking correct.\n"
                "Run `keymapper doctor` for details.",
                file=sys.stderr,
            )
            return 1

        mode = "DRY RUN (no events modified)" if self.dry_run else "active"
        print(
            f"keymapper running -- tap={self.tap_location} mode={mode}\n"
            f"source filter: {self.classifier.filter_mode}\n"
            f"rules ({len(self.ruleset)}):",
            file=sys.stderr,
        )
        for rule in self.ruleset.rules:
            print(f"  {rule.describe()}", file=sys.stderr)
        if self.pause_hotkey is not None:
            print(
                f"pause toggle: {self.pause_hotkey.describe()}"
                " (swallowed, works from any keyboard)",
                file=sys.stderr,
            )
        print("Press Ctrl-C to stop.\n", file=sys.stderr)

        started = time.monotonic()
        reason = run_until_interrupted(on_tick=self._health_check)
        elapsed = time.monotonic() - started
        print(
            f"\nstopped ({reason}) after {elapsed:.0f}s -- "
            f"remapped={self.remapped} "
            f"skipped_wrong_source={self.skipped_source} "
            f"skipped_while_paused={self.skipped_paused} "
            f"pause_toggles={self.toggles} "
            f"tap_reenables={self.tap_reenables}",
            file=sys.stderr,
        )
        return 0
