"""macOS privacy-permission checks.

Two *separate* TCC permissions matter here, and confusing them wastes a lot of
time:

* **Accessibility** (`AXIsProcessTrusted`) -- lets a tap *modify* events.
* **Input Monitoring** (`IOHIDCheckAccess(kIOHIDRequestTypeListenEvent)`) --
  lets a tap *observe key content* at all.

Having only Accessibility produces a genuinely baffling symptom, measured on
macOS 15.7.3: `CGEventTapCreate` **succeeds** for an event mask that includes
`flagsChanged`, and **fails** (returns NULL) for a mask of only
`keyDown`/`keyUp`. Modifier-state changes carry no typed characters, so they are
not treated as keystroke capture; actual key content is. The practical trap is
that a tap requesting keyDown|keyUp|flagsChanged is created successfully but
then never delivers a single keyDown -- which looks exactly like "the events I
want aren't reaching the tap" and invites the wrong conclusion.

So: always check Input Monitoring explicitly rather than inferring it from tap
creation.
"""

from __future__ import annotations

import ctypes
import ctypes.util

# IOHIDRequestType
_REQUEST_POST_EVENT = 0
_REQUEST_LISTEN_EVENT = 1

# IOHIDAccessType
ACCESS_GRANTED = 0
ACCESS_DENIED = 1
ACCESS_UNKNOWN = 2

ACCESS_NAMES = {
    ACCESS_GRANTED: "granted",
    ACCESS_DENIED: "denied",
    ACCESS_UNKNOWN: "not yet requested",
}


def _load_iokit():
    path = (
        ctypes.util.find_library("IOKit")
        or "/System/Library/Frameworks/IOKit.framework/IOKit"
    )
    try:
        lib = ctypes.CDLL(path)
        lib.IOHIDCheckAccess.argtypes = [ctypes.c_uint32]
        lib.IOHIDCheckAccess.restype = ctypes.c_uint32
        lib.IOHIDRequestAccess.argtypes = [ctypes.c_uint32]
        lib.IOHIDRequestAccess.restype = ctypes.c_bool
    except (OSError, AttributeError):
        return None
    return lib


_iokit = _load_iokit()


def input_monitoring_access() -> int:
    """One of ACCESS_GRANTED / ACCESS_DENIED / ACCESS_UNKNOWN."""
    if _iokit is None:
        return ACCESS_UNKNOWN
    return int(_iokit.IOHIDCheckAccess(_REQUEST_LISTEN_EVENT))


def post_event_access() -> int:
    """Permission to synthesise events. Not required by this tool, but useful
    context when diagnosing: it is usually granted alongside Accessibility."""
    if _iokit is None:
        return ACCESS_UNKNOWN
    return int(_iokit.IOHIDCheckAccess(_REQUEST_POST_EVENT))


def request_input_monitoring() -> bool:
    """Trigger the Input Monitoring prompt. Only does anything when the current
    state is ACCESS_UNKNOWN; once denied, macOS will not re-prompt and the user
    must enable it by hand in System Settings."""
    if _iokit is None:
        return False
    return bool(_iokit.IOHIDRequestAccess(_REQUEST_LISTEN_EVENT))


def accessibility_trusted() -> bool:
    """Whether this process may modify events (Accessibility)."""
    try:
        from ApplicationServices import AXIsProcessTrusted
    except ImportError:
        return False
    return bool(AXIsProcessTrusted())


def responsible_app_hint() -> str:
    """Best guess at which app the user must grant permission to.

    TCC attributes a permission to the *responsible* process, which for a script
    run from a terminal is the terminal application, not python. Walking up the
    process tree to the topmost .app is a good approximation.
    """
    import subprocess

    try:
        import os

        pid = os.getpid()
        last_app = None
        for _ in range(24):
            out = subprocess.run(
                ["ps", "-p", str(pid), "-o", "ppid=,comm="],
                capture_output=True,
                text=True,
                timeout=2,
            ).stdout.strip()
            if not out:
                break
            ppid_str, _, comm = out.partition(" ")
            comm = comm.strip()
            if ".app/Contents/MacOS/" in comm:
                last_app = comm.split(".app/")[0].rsplit("/", 1)[-1] + ".app"
            try:
                pid = int(ppid_str)
            except ValueError:
                break
            if pid <= 1:
                break
        return last_app or "your terminal application"
    except (OSError, subprocess.SubprocessError):
        return "your terminal application"


def describe() -> list[str]:
    """Human-readable permission report lines."""
    im = input_monitoring_access()
    lines = [
        f"Accessibility     : {'GRANTED' if accessibility_trusted() else 'NOT GRANTED'}"
        "   (needed to modify events)",
        f"Input Monitoring  : {ACCESS_NAMES.get(im, '?').upper()}"
        "   (needed to see keyDown/keyUp at all)",
    ]
    return lines


def blocking_problem() -> str | None:
    """Return a message describing what will stop the tool working, or None."""
    app = responsible_app_hint()
    if input_monitoring_access() != ACCESS_GRANTED:
        return (
            "Input Monitoring is not granted.\n"
            f"  A tap can still be created, but it will NEVER receive keyDown or\n"
            f"  keyUp events -- so nothing can be remapped, and a probe would\n"
            f"  wrongly look like 'NoMachine keys are invisible'.\n\n"
            f"  Fix: System Settings -> Privacy & Security -> Input Monitoring,\n"
            f"  enable {app}, then FULLY QUIT and reopen it (a running process\n"
            f"  does not pick up a new grant).\n"
        )
    if not accessibility_trusted():
        return (
            "Accessibility is not granted.\n"
            "  Events can be observed but not modified, so --dry-run will work\n"
            "  while a real run will not.\n\n"
            f"  Fix: System Settings -> Privacy & Security -> Accessibility,\n"
            f"  enable {app}, then fully quit and reopen it.\n"
        )
    return None
