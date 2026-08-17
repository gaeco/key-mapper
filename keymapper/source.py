"""Work out where a CGEvent came from.

The remapper must only rewrite keys injected by NoMachine's `nxnode`, leaving
the physical MacBook keyboard native. Two discriminators are available on every
CGEvent, in decreasing order of precision:

1. `kCGEventSourceUnixProcessID` -- the pid of the process that posted the
   event. Zero (or the tapping process's own pid) for real hardware. This is
   exact, but needs a pid -> executable-path lookup.
2. `kCGEventSourceStateID` -- `hidSystem` (1) for events originating from real
   hardware, `combinedSession` (0) / `private` (-1) for synthesised ones. Cheap
   and needs no lookup, but cannot tell NoMachine apart from any other program
   posting events.

Which of these actually discriminates on this machine is an empirical question;
run `keymapper probe` to find out before trusting either.
"""

from __future__ import annotations

import ctypes
import subprocess
import time

from .keycodes import SOURCE_STATE_HID_SYSTEM

#: Substrings that mark a process as NoMachine. Matched case-insensitively
#: against the full executable path.
NOMACHINE_MARKERS = ("nxnode", "nxserver", "nxexec", "nomachine", "nxrunner")

_PROC_PIDPATHINFO_MAXSIZE = 4096


def _load_proc_pidpath():
    """Bind libproc's proc_pidpath(2), or return None if unavailable."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        fn = libc.proc_pidpath
    except (OSError, AttributeError):
        return None
    fn.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    fn.restype = ctypes.c_int
    return fn


_proc_pidpath = _load_proc_pidpath()


def executable_path(pid: int) -> str | None:
    """Absolute path of a pid's executable, or None if it can't be determined."""
    if pid <= 0:
        return None
    if _proc_pidpath is not None:
        buf = ctypes.create_string_buffer(_PROC_PIDPATHINFO_MAXSIZE)
        n = _proc_pidpath(pid, buf, _PROC_PIDPATHINFO_MAXSIZE)
        if n > 0:
            return buf.value.decode("utf-8", "replace")
    # Fall back to ps: proc_pidpath fails for processes we don't own.
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = out.stdout.strip()
    return name or None


def is_nomachine_path(path: str | None) -> bool:
    if not path:
        return False
    lowered = path.lower()
    return any(marker in lowered for marker in NOMACHINE_MARKERS)


class SourceClassifier:
    """Decides whether an event should be remapped, per the configured filter.

    `filter_mode` is one of:
        "nomachine" -- only events posted by a NoMachine process
        "synthetic" -- any event not originating from real hardware
        "all"       -- every event, regardless of origin

    Pid -> verdict results are cached with a TTL because the lookup runs on the
    event-tap hot path, and because nxnode's pid changes on every reconnect, so
    the cache must not be permanent.
    """

    def __init__(self, filter_mode: str = "nomachine", cache_ttl: float = 30.0) -> None:
        if filter_mode not in ("nomachine", "synthetic", "all"):
            raise ValueError(
                f"unknown source_filter {filter_mode!r};"
                " expected 'nomachine', 'synthetic' or 'all'"
            )
        self.filter_mode = filter_mode
        self.cache_ttl = cache_ttl
        # pid -> (is_nomachine, path, expires_at)
        self._cache: dict[int, tuple[bool, str | None, float]] = {}

    def describe_pid(self, pid: int) -> str | None:
        """Cached executable path for a pid, for log output."""
        return self._lookup(pid)[1]

    def _lookup(self, pid: int) -> tuple[bool, str | None]:
        now = time.monotonic()
        hit = self._cache.get(pid)
        if hit is not None and hit[2] > now:
            return hit[0], hit[1]
        path = executable_path(pid)
        verdict = is_nomachine_path(path)
        self._cache[pid] = (verdict, path, now + self.cache_ttl)
        return verdict, path

    def should_remap(self, source_pid: int, source_state: int) -> bool:
        if self.filter_mode == "all":
            return True
        if self.filter_mode == "synthetic":
            return source_state != SOURCE_STATE_HID_SYSTEM
        return self._lookup(source_pid)[0]
