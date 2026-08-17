"""Command-line entry point: run | probe | doctor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _cmd_probe(args) -> int:
    from .probe import Probe

    return Probe(include_flags_changed=args.flags_changed).run(duration=args.duration)


def _cmd_run(args) -> int:
    from .config import ConfigError, load
    from .source import SourceClassifier
    from .tap import Remapper

    try:
        cfg = load(Path(args.config) if args.config else None)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    source_filter = args.source_filter or cfg.source_filter
    remapper = Remapper(
        ruleset=cfg.ruleset,
        classifier=SourceClassifier(source_filter, cache_ttl=cfg.cache_ttl),
        tap_location=args.tap or cfg.tap_location,
        dry_run=args.dry_run,
        verbose=args.verbose,
        pause_hotkey=cfg.pause_hotkey,
    )
    return remapper.run()


def _simple_desc(entry: dict) -> str:
    frm = entry.get("from", {}).get("key_code", "?")
    to = ", ".join(str(t.get("key_code", "?")) for t in (entry.get("to") or [])) or "?"
    return f"{frm} -> {to}"


def _touches_modifier(entry: dict) -> bool:
    """True for a simple_modification involving Control or Command.

    A `left_control -> left_command` modification does at the HID layer what this
    tool does at the session tap, so the two would both fire on a local key.
    """
    text = _simple_desc(entry)
    return "control" in text or "command" in text


def _cmd_doctor(args) -> int:
    import platform
    import subprocess

    from . import permissions
    from .config import ConfigError, load
    from .source import is_nomachine_path

    ok = True
    print(f"python            : {platform.python_version()} ({sys.executable})")
    print(f"macOS             : {platform.mac_ver()[0]}")

    try:
        import Quartz as Q
    except ImportError as exc:
        print(f"pyobjc Quartz     : MISSING ({exc})")
        return 1
    print("pyobjc Quartz     : importable")

    print(f"grant permission to: {permissions.responsible_app_hint()}")
    for line in permissions.describe():
        print(line)

    # Empirical cross-check. A keyDown-only mask is the honest test of Input
    # Monitoring: a mask containing flagsChanged is created even when Input
    # Monitoring is denied, so testing with one would give a false pass.
    keydown_mask = Q.CGEventMaskBit(Q.kCGEventKeyDown)
    with_flags_mask = keydown_mask | Q.CGEventMaskBit(Q.kCGEventFlagsChanged)

    def _cb(p, t, e, r):
        return e

    keep = []

    def can_create(mask, option):
        tap = Q.CGEventTapCreate(
            Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap, option, mask, _cb, None
        )
        keep.append(tap)
        return tap is not None

    key_tap = can_create(keydown_mask, Q.kCGEventTapOptionListenOnly)
    active_tap = can_create(keydown_mask, Q.kCGEventTapOptionDefault)
    flags_tap = can_create(with_flags_mask, Q.kCGEventTapOptionListenOnly)
    print(f"keyDown tap       : {'ok' if key_tap else 'CANNOT CREATE'}")
    print(f"keyDown tap (mod) : {'ok' if active_tap else 'CANNOT CREATE'}")
    if flags_tap and not key_tap:
        print(
            "                    note: a tap including flagsChanged IS creatable.\n"
            "                    That is the missing-Input-Monitoring signature --\n"
            "                    such a tap runs but never sees a keystroke."
        )
    if not key_tap:
        ok = False

    problem = permissions.blocking_problem()
    if problem:
        ok = False
        print()
        print(problem.rstrip())
        print()

    # NoMachine presence
    try:
        out = subprocess.run(
            ["ps", "ax", "-o", "pid=,comm="], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        out = ""
    nx = [ln.strip() for ln in out.splitlines() if is_nomachine_path(ln)]
    if nx:
        print(f"NoMachine procs   : {len(nx)} running")
        for line in nx[:6]:
            pid, _, comm = line.partition(" ")
            print(f"                    pid {pid:>7}  {comm.strip()}")
    else:
        print("NoMachine procs   : none found (is a session connected?)")

    try:
        cfg = load(Path(args.config) if args.config else None)
        print(f"config            : {len(cfg.ruleset)} rules, filter={cfg.source_filter}")
        for rule in cfg.ruleset.rules:
            print(f"                    {rule.describe()}")
        hotkey = cfg.pause_hotkey
        print(
            f"pause toggle      : {hotkey.describe() if hotkey else 'disabled'}"
        )
    except ConfigError as exc:
        print(f"config            : ERROR {exc}")
        ok = False

    kb = Path.home() / ".config/karabiner/karabiner.json"
    # `karabiner_grabber` is the component that actually intercepts keys. Its
    # config file outlives an uninstall, so a leftover karabiner.json on its own
    # changes no keystroke and must not be reported as a conflict.
    kb_running = "karabiner_grabber" in out
    if kb.exists():
        import json

        try:
            data = json.loads(kb.read_text())
            for prof in data.get("profiles", []):
                if not prof.get("selected"):
                    continue
                rules = prof.get("complex_modifications", {}).get("rules", [])
                simple = prof.get("simple_modifications", [])
                overlap = [
                    r.get("description", "?")
                    for r in rules
                    if "ommand" in str(r.get("description", ""))
                ]
                overlap += [
                    f"simple: {_simple_desc(m)}" for m in simple if _touches_modifier(m)
                ]
                if not kb_running:
                    print(
                        f"karabiner         : not running -- leftover config"
                        f" ({len(rules)} complex / {len(simple)} simple in"
                        f" {prof.get('name')!r}) has no effect"
                    )
                    if overlap:
                        print(f"                    delete {kb} to keep it that way")
                    continue
                print(
                    f"karabiner         : active profile {prof.get('name')!r},"
                    f" {len(rules)} complex / {len(simple)} simple"
                )
                for desc in overlap:
                    print(f"                    WARNING overlapping rule: {desc}")
                    ok = False
        except (json.JSONDecodeError, OSError) as exc:
            print(f"karabiner         : could not read ({exc})")

    print()
    print("all checks passed" if ok else "some checks need attention (see above)")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="keymapper",
        description="Remap Ctrl+<key> to Cmd+<key> for keys injected by NoMachine.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run the remapper")
    p_run.add_argument("--config", help="path to config.toml")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        help="log what would be remapped without modifying any event",
    )
    p_run.add_argument(
        "--verbose", action="store_true", help="also log events skipped by source filter"
    )
    p_run.add_argument(
        "--source-filter",
        choices=["nomachine", "synthetic", "all"],
        help="override the configured source filter",
    )
    p_run.add_argument(
        "--tap",
        choices=["session", "hid", "annotatedSession"],
        help="override the tap location",
    )
    p_run.set_defaults(func=_cmd_run)

    p_probe = sub.add_parser(
        "probe", help="diagnose which taps see NoMachine keys (safe, read-only)"
    )
    p_probe.add_argument(
        "--duration", type=float, default=None, help="stop automatically after N seconds"
    )
    p_probe.add_argument(
        "--flags-changed", action="store_true", help="also log flagsChanged events"
    )
    p_probe.set_defaults(func=_cmd_probe)

    p_doc = sub.add_parser("doctor", help="check permissions and environment")
    p_doc.add_argument("--config", help="path to config.toml")
    p_doc.set_defaults(func=_cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
