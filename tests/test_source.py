"""Tests for event-origin classification."""

from __future__ import annotations

import os

import pytest

from keymapper.keycodes import (
    SOURCE_STATE_COMBINED_SESSION,
    SOURCE_STATE_HID_SYSTEM,
    SOURCE_STATE_PRIVATE,
)
from keymapper.source import SourceClassifier, executable_path, is_nomachine_path


@pytest.mark.parametrize(
    "path",
    [
        "/Applications/NoMachine.app/Contents/MacOS/nxnode",
        "/etc/NX/nxserver",
        "/Applications/NoMachine.app/Contents/Frameworks/bin/nxexec",
        "nxnode",
        "/some/path/NOMACHINE/thing",  # case-insensitive
    ],
)
def test_recognises_nomachine_paths(path):
    assert is_nomachine_path(path)


@pytest.mark.parametrize(
    "path",
    ["/usr/bin/python3", "/Applications/iTerm.app/Contents/MacOS/iTerm2", "", None, "loginwindow"],
)
def test_rejects_other_paths(path):
    assert not is_nomachine_path(path)


def test_executable_path_resolves_own_pid():
    path = executable_path(os.getpid())
    assert path and "python" in path.lower()


def test_executable_path_handles_bogus_pid():
    assert executable_path(0) is None
    assert executable_path(-1) is None
    # A pid that almost certainly doesn't exist should not raise.
    assert executable_path(999_999) in (None, "")


# --- filter modes --------------------------------------------------------


def test_all_mode_accepts_everything():
    c = SourceClassifier("all")
    assert c.should_remap(0, SOURCE_STATE_HID_SYSTEM)
    assert c.should_remap(12345, SOURCE_STATE_PRIVATE)


@pytest.mark.parametrize(
    "state,expected",
    [
        (SOURCE_STATE_HID_SYSTEM, False),  # real hardware -> leave alone
        (SOURCE_STATE_COMBINED_SESSION, True),
        (SOURCE_STATE_PRIVATE, True),
    ],
)
def test_synthetic_mode_keys_off_state(state, expected):
    assert SourceClassifier("synthetic").should_remap(0, state) is expected


def test_nomachine_mode_rejects_our_own_process():
    """This test process is not NoMachine, so its pid must not match."""
    c = SourceClassifier("nomachine")
    assert not c.should_remap(os.getpid(), SOURCE_STATE_HID_SYSTEM)


def test_nomachine_mode_accepts_a_faked_nomachine_pid(monkeypatch):
    c = SourceClassifier("nomachine")
    monkeypatch.setattr(
        "keymapper.source.executable_path",
        lambda pid: "/Applications/NoMachine.app/Contents/MacOS/nxnode",
    )
    assert c.should_remap(4178, SOURCE_STATE_COMBINED_SESSION)


def test_bad_filter_mode_rejected():
    with pytest.raises(ValueError, match="unknown source_filter"):
        SourceClassifier("magic")


# --- caching -------------------------------------------------------------


def test_lookup_is_cached(monkeypatch):
    calls = []

    def fake(pid):
        calls.append(pid)
        return "/Applications/NoMachine.app/Contents/MacOS/nxnode"

    monkeypatch.setattr("keymapper.source.executable_path", fake)
    c = SourceClassifier("nomachine", cache_ttl=60.0)
    for _ in range(5):
        assert c.should_remap(4178, SOURCE_STATE_COMBINED_SESSION)
    assert len(calls) == 1, "pid lookup must not repeat on the hot path"


def test_cache_expires(monkeypatch):
    """nxnode's pid changes on reconnect, so the cache must not be permanent."""
    calls = []
    monkeypatch.setattr(
        "keymapper.source.executable_path",
        lambda pid: (calls.append(pid), "/etc/NX/nxserver")[1],
    )
    clock = [1000.0]
    monkeypatch.setattr("keymapper.source.time.monotonic", lambda: clock[0])

    c = SourceClassifier("nomachine", cache_ttl=30.0)
    assert c.should_remap(4178, 0)
    assert len(calls) == 1
    clock[0] += 31.0
    assert c.should_remap(4178, 0)
    assert len(calls) == 2, "entry should have expired and been re-resolved"


def test_describe_pid_returns_path(monkeypatch):
    monkeypatch.setattr("keymapper.source.executable_path", lambda pid: "/etc/NX/nxserver")
    assert SourceClassifier("nomachine").describe_pid(1) == "/etc/NX/nxserver"
