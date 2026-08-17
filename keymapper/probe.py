"""Step 0 feasibility probe.

Installs a *listen-only* tap at each of the three tap locations and dumps one
line per key event, including the fields that reveal where the event came from.

It answers two questions that the remapper's design depends on:

1. Are NoMachine-injected keys visible to a userspace event tap at all, and at
   which tap location?
2. Do they carry source fields that distinguish them from the local keyboard?
   If not, "remap NoMachine only" is not achievable.

Listen-only taps cannot alter events, so this is safe to run at any time.
"""

from __future__ import annotations

import collections
import sys

import Quartz
from CoreFoundation import (
    CFMachPortCreateRunLoopSource,
    CFRunLoopAddSource,
    CFRunLoopGetCurrent,
    kCFRunLoopCommonModes,
)

from . import permissions
from .keycodes import describe_flags, event_name, key_name, source_state_name
from .runloop import run_until_interrupted
from .source import executable_path, is_nomachine_path

TAP_LOCATIONS = (
    ("hid", Quartz.kCGHIDEventTap),
    ("session", Quartz.kCGSessionEventTap),
    ("annotatedSession", Quartz.kCGAnnotatedSessionEventTap),
)

WATCHED_EVENTS = (
    Quartz.kCGEventKeyDown,
    Quartz.kCGEventKeyUp,
    Quartz.kCGEventFlagsChanged,
)

EVENT_MASK = 0
for _t in WATCHED_EVENTS:
    EVENT_MASK |= Quartz.CGEventMaskBit(_t)


class Probe:
    def __init__(self, include_flags_changed: bool = False) -> None:
        self.include_flags_changed = include_flags_changed
        # (tap_name, src_pid, src_state, proc_name) -> count
        self.summary: collections.Counter = collections.Counter()
        self._pid_names: dict[int, str | None] = {}
        # Strong refs: a GC'd tap or run-loop source silently stops working.
        self._keepalive: list = []
        self._taps: list = []
        self._event_count = 0

    def _pid_name(self, pid: int) -> str | None:
        if pid not in self._pid_names:
            self._pid_names[pid] = executable_path(pid)
        return self._pid_names[pid]

    def _callback(self, tap_name: str):
        def handler(proxy, event_type, event, refcon):
            # A listen-only tap can still be disabled by timeout; re-arm it.
            if event_type in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                print(
                    f"[{tap_name}] tap disabled ({event_name(event_type)}) -- re-enabling",
                    file=sys.stderr,
                )
                for tap in self._taps:
                    Quartz.CGEventTapEnable(tap, True)
                return event

            if event_type == Quartz.kCGEventFlagsChanged and not self.include_flags_changed:
                return event

            get = Quartz.CGEventGetIntegerValueField
            keycode = get(event, Quartz.kCGKeyboardEventKeycode)
            src_pid = get(event, Quartz.kCGEventSourceUnixProcessID)
            src_state = get(event, Quartz.kCGEventSourceStateID)
            user_data = get(event, Quartz.kCGEventSourceUserData)
            kb_type = get(event, Quartz.kCGKeyboardEventKeyboardType)
            flags = Quartz.CGEventGetFlags(event)

            proc = self._pid_name(src_pid) if src_pid else None
            short_proc = proc.rsplit("/", 1)[-1] if proc else "-"
            tag = "NOMACHINE" if is_nomachine_path(proc) else "local?"

            self.summary[(tap_name, src_pid, src_state, short_proc)] += 1
            self._event_count += 1

            print(
                f"[{tap_name:16}] {event_name(event_type):12} "
                f"key={keycode:3} ({key_name(keycode):>4}) "
                f"flags=0x{flags:08x} ({describe_flags(flags):<14}) "
                f"srcPid={src_pid:<7} srcState={source_state_name(src_state):<16} "
                f"kbType={kb_type:<4} userData={user_data:<4} "
                f"proc={short_proc:<12} {tag}",
                flush=True,
            )
            return event

        return handler

    def run(self, duration: float | None = None) -> int:
        # Check this first. Without Input Monitoring the taps below are created
        # successfully but silently never deliver keyDown/keyUp, which would make
        # this probe report "no NoMachine events" for entirely the wrong reason.
        problem = permissions.blocking_problem()
        if problem:
            print("\n".join(permissions.describe()), file=sys.stderr)
            print(f"\n{problem}", file=sys.stderr)
            if permissions.input_monitoring_access() == permissions.ACCESS_UNKNOWN:
                print(
                    "Requesting the Input Monitoring prompt now -- click Allow,"
                    " then re-run.\n",
                    file=sys.stderr,
                )
                permissions.request_input_monitoring()
            print(
                "Refusing to probe: the result would be misleading.\n"
                "Re-run once Input Monitoring is granted.",
                file=sys.stderr,
            )
            return 2

        print("Creating listen-only taps at all three locations:", file=sys.stderr)
        created = []
        for name, location in TAP_LOCATIONS:
            handler = self._callback(name)
            self._keepalive.append(handler)  # pyobjc does not retain the callable
            tap = Quartz.CGEventTapCreate(
                location,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionListenOnly,
                EVENT_MASK,
                handler,
                None,
            )
            if tap is None:
                print(
                    f"  {name:16} FAILED to create "
                    "(needs Accessibility or Input Monitoring; "
                    "the 'hid' location may additionally require root)",
                    file=sys.stderr,
                )
                continue
            source = CFMachPortCreateRunLoopSource(None, tap, 0)
            CFRunLoopAddSource(CFRunLoopGetCurrent(), source, kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            self._keepalive.extend([tap, source])
            self._taps.append(tap)
            created.append(name)
            print(f"  {name:16} tap active", file=sys.stderr)

        if not created:
            print(
                "\nNo taps could be created at all. Grant your terminal app\n"
                "Input Monitoring (and Accessibility) in System Settings ->\n"
                "Privacy & Security, then FULLY QUIT and relaunch the terminal.\n",
                file=sys.stderr,
            )
            return 1

        print(
            f"\nListening on: {', '.join(created)}\n"
            "Now do this:\n"
            "  1. On the MacBook's own keyboard, press Ctrl+V a few times.\n"
            "  2. From the NoMachine client (DeX), press Ctrl+V a few times.\n"
            "Then stop with Ctrl-C and compare the srcPid / srcState columns.\n"
            "(Ctrl+V, not Ctrl+C: pressing Ctrl+C here would interrupt probe.)\n",
            file=sys.stderr,
        )

        reason = run_until_interrupted(duration=duration)
        print(f"\nstopped ({reason})", file=sys.stderr)
        self.report()
        return 0

    def report(self) -> None:
        print("\n" + "=" * 78, file=sys.stderr)
        print("SUMMARY  (distinct event sources seen)", file=sys.stderr)
        print("=" * 78, file=sys.stderr)
        if not self.summary:
            print(
                "No key events were captured at all.\n"
                "Either nothing was typed, or the tap has no permission.",
                file=sys.stderr,
            )
            return
        print(
            f"{'tap':18} {'srcPid':>7} {'srcState':16} {'process':14} {'count':>6}",
            file=sys.stderr,
        )
        for (tap, pid, state, proc), count in sorted(
            self.summary.items(), key=lambda kv: -kv[1]
        ):
            mark = " <-- NoMachine" if is_nomachine_path(proc) else ""
            print(
                f"{tap:18} {pid:>7} {source_state_name(state):16} {proc:14} {count:>6}{mark}",
                file=sys.stderr,
            )

        pids = {pid for (_, pid, _, _) in self.summary}
        states = {state for (_, _, state, _) in self.summary}
        print("", file=sys.stderr)
        if len(pids) > 1 or len(states) > 1:
            print(
                "GOOD: more than one distinct source was seen, so local and\n"
                "NoMachine keys are very likely distinguishable.",
                file=sys.stderr,
            )
        else:
            print(
                "NOTE: only one distinct source was seen. Either you typed from\n"
                "just one of the two keyboards, or they are indistinguishable.",
                file=sys.stderr,
            )
