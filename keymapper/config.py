"""Load and validate config.toml into a Ruleset."""

from __future__ import annotations

import tomllib
from pathlib import Path

from .keycodes import FLAG_NAMES, KEY_NAMES, SIGNIFICANT_FLAGS
from .remap import Hotkey, Rule, Ruleset

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"

#: Used when no config file exists, so the tool works out of the box.
DEFAULT_MAPPINGS = [
    {"key": key, "from": "control", "to": "command"} for key in ("c", "v", "a", "z", "x")
]

#: The pause toggle, used when config.toml has no [pause_hotkey] section.
DEFAULT_PAUSE_HOTKEY = {
    "key": "p",
    "modifiers": ["control", "option", "shift"],
}


class ConfigError(Exception):
    """Raised for a malformed config file, with a message aimed at the user."""


def _parse_modifier(value: str, where: str) -> int:
    key = value.strip().lower()
    if key not in FLAG_NAMES:
        raise ConfigError(
            f"{where}: unknown modifier {value!r}."
            f" Valid: {', '.join(sorted(set(FLAG_NAMES)))}"
        )
    return FLAG_NAMES[key]


def _parse_key(value: str, where: str) -> int:
    key = value.strip().lower()
    if key in KEY_NAMES:
        return KEY_NAMES[key]
    # Allow a raw keycode for keys not in the name table.
    try:
        code = int(key, 0)
    except ValueError as exc:
        raise ConfigError(
            f"{where}: unknown key {value!r}."
            f" Use a letter ({', '.join(sorted(KEY_NAMES))}) or a numeric keycode."
        ) from exc
    if not 0 <= code <= 0x7F:
        raise ConfigError(f"{where}: keycode {code} out of range 0-127")
    return code


def build_hotkey(raw: object, where: str = "[pause_hotkey]") -> Hotkey | None:
    """Build the pause hotkey, or None when it is disabled.

    Requires at least one modifier: the tap swallows this chord, so a bare
    letter would silently eat that key everywhere.
    """
    if raw is None:
        raw = DEFAULT_PAUSE_HOTKEY
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: expected a table")
    if not raw.get("enabled", True):
        return None
    if "key" not in raw:
        raise ConfigError(f"{where}: missing key")
    modifiers = raw.get("modifiers", [])
    if isinstance(modifiers, str):
        modifiers = [modifiers]
    if not isinstance(modifiers, list):
        raise ConfigError(f"{where}: modifiers must be a list of strings")
    flags = 0
    for name in modifiers:
        if not isinstance(name, str):
            raise ConfigError(f"{where}: modifiers must be a list of strings")
        flags |= _parse_modifier(name, where)
    if not flags & SIGNIFICANT_FLAGS:
        raise ConfigError(
            f"{where}: needs at least one of shift/control/option/command --"
            " the hotkey is swallowed, so an unmodified key would be eaten"
            " every time you typed it. Use enabled = false to switch it off."
        )
    return Hotkey(keycode=_parse_key(raw["key"], where), flags=flags)


class Config:
    def __init__(
        self,
        ruleset: Ruleset,
        source_filter: str = "nomachine",
        tap_location: str = "session",
        cache_ttl: float = 30.0,
        pause_hotkey: Hotkey | None = None,
    ) -> None:
        self.ruleset = ruleset
        self.source_filter = source_filter
        self.tap_location = tap_location
        self.cache_ttl = cache_ttl
        self.pause_hotkey = pause_hotkey


def build_ruleset(mappings: list[dict], strict: bool = False) -> Ruleset:
    rules = []
    for i, entry in enumerate(mappings):
        where = f"[[mapping]] #{i + 1}"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where}: expected a table")
        missing = {"key", "from", "to"} - entry.keys()
        if missing:
            raise ConfigError(f"{where}: missing {', '.join(sorted(missing))}")
        from_flags = _parse_modifier(entry["from"], where)
        to_flags = _parse_modifier(entry["to"], where)
        if from_flags == to_flags:
            raise ConfigError(f"{where}: 'from' and 'to' are the same modifier")
        to_key = entry.get("to_key")
        rules.append(
            Rule(
                keycode=_parse_key(entry["key"], where),
                from_flags=from_flags,
                to_flags=to_flags,
                strict=bool(entry.get("strict", strict)),
                to_keycode=(
                    None if to_key is None else _parse_key(to_key, f"{where} to_key")
                ),
            )
        )
    if not rules:
        raise ConfigError("no [[mapping]] entries -- nothing to remap")
    return Ruleset(rules)


def load(path: Path | None = None) -> Config:
    """Read config from `path`, falling back to built-in defaults if absent."""
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return Config(
            ruleset=build_ruleset(DEFAULT_MAPPINGS),
            pause_hotkey=build_hotkey(None),
        )
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML -- {exc}") from exc

    strict = bool(raw.get("strict_modifiers", False))
    mappings = raw.get("mapping") or DEFAULT_MAPPINGS
    source_filter = str(raw.get("source_filter", "nomachine"))
    if source_filter not in ("nomachine", "synthetic", "all"):
        raise ConfigError(
            f"{path}: source_filter must be 'nomachine', 'synthetic' or 'all',"
            f" got {source_filter!r}"
        )
    tap_location = str(raw.get("tap_location", "session"))
    return Config(
        ruleset=build_ruleset(mappings, strict=strict),
        source_filter=source_filter,
        tap_location=tap_location,
        cache_ttl=float(raw.get("pid_cache_ttl_seconds", 30.0)),
        pause_hotkey=build_hotkey(raw.get("pause_hotkey")),
    )
