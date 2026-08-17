"""Tests for config parsing and validation."""

from __future__ import annotations

import pytest

from keymapper.config import (
    DEFAULT_MAPPINGS,
    ConfigError,
    build_hotkey,
    build_ruleset,
    load,
)
from keymapper.keycodes import (
    FLAG_ALTERNATE,
    FLAG_COMMAND,
    FLAG_CONTROL,
    FLAG_SHIFT,
    VK_C,
    VK_ESCAPE,
    VK_GRAVE,
    VK_P,
)
from keymapper.remap import Ruleset


def test_default_mappings_cover_the_five_keys():
    rs = build_ruleset(DEFAULT_MAPPINGS)
    assert len(rs) == 5
    assert rs.keycodes() == frozenset({0x00, 0x06, 0x07, 0x08, 0x09})  # a z x c v
    for rule in rs.rules:
        assert rule.from_flags == FLAG_CONTROL
        assert rule.to_flags == FLAG_COMMAND


def test_modifier_aliases_accepted():
    rs = build_ruleset([{"key": "c", "from": "ctrl", "to": "cmd"}])
    assert rs.rules[0].from_flags == FLAG_CONTROL
    assert rs.rules[0].to_flags == FLAG_COMMAND


def test_numeric_keycode_accepted():
    rs = build_ruleset([{"key": "0x08", "from": "command", "to": "control"}])
    assert rs.rules[0].keycode == VK_C


def test_to_key_rewrites_the_key():
    rs = build_ruleset(
        [{"key": "esc", "to_key": "`", "from": "control", "to": "command"}]
    )
    assert rs.rules[0].keycode == VK_ESCAPE
    assert rs.rules[0].to_keycode == VK_GRAVE
    assert rs.rules[0].describe() == "ctrl+esc -> cmd+`"


def test_to_key_defaults_to_keeping_the_key():
    rs = build_ruleset([{"key": "c", "from": "control", "to": "command"}])
    assert rs.rules[0].to_keycode is None


def test_to_key_accepts_a_numeric_keycode():
    rs = build_ruleset(
        [{"key": "esc", "to_key": "0x32", "from": "control", "to": "command"}]
    )
    assert rs.rules[0].to_keycode == VK_GRAVE


def test_to_key_rejects_an_unknown_key():
    with pytest.raises(ConfigError, match="to_key: unknown key"):
        build_ruleset([{"key": "esc", "to_key": "nope", "from": "ctrl", "to": "cmd"}])


def test_strict_flag_per_mapping_overrides_global():
    rs = build_ruleset(
        [{"key": "c", "from": "command", "to": "control", "strict": True}], strict=False
    )
    assert rs.rules[0].strict is True


def test_global_strict_applies_to_all():
    rs = build_ruleset(DEFAULT_MAPPINGS, strict=True)
    assert all(r.strict for r in rs.rules)


# --- error cases ---------------------------------------------------------


def test_unknown_modifier_rejected():
    with pytest.raises(ConfigError, match="unknown modifier"):
        build_ruleset([{"key": "c", "from": "hyper", "to": "control"}])


def test_unknown_key_rejected():
    with pytest.raises(ConfigError, match="unknown key"):
        build_ruleset([{"key": "notakey", "from": "command", "to": "control"}])


def test_out_of_range_keycode_rejected():
    with pytest.raises(ConfigError, match="out of range"):
        build_ruleset([{"key": "999", "from": "command", "to": "control"}])


def test_missing_field_rejected():
    with pytest.raises(ConfigError, match="missing to"):
        build_ruleset([{"key": "c", "from": "command"}])


def test_identical_from_and_to_rejected():
    """A no-op rule is a config mistake worth reporting, not silently accepting."""
    with pytest.raises(ConfigError, match="same modifier"):
        build_ruleset([{"key": "c", "from": "command", "to": "cmd"}])


def test_empty_mapping_list_rejected():
    with pytest.raises(ConfigError, match="nothing to remap"):
        build_ruleset([])


def test_non_table_mapping_rejected():
    with pytest.raises(ConfigError, match="expected a table"):
        build_ruleset(["c"])


# --- file loading --------------------------------------------------------


def test_missing_file_falls_back_to_defaults(tmp_path):
    cfg = load(tmp_path / "nope.toml")
    assert len(cfg.ruleset) == 5
    assert cfg.source_filter == "nomachine"


def test_load_reads_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'source_filter = "all"\n'
        'tap_location = "hid"\n'
        "pid_cache_ttl_seconds = 5.0\n"
        "strict_modifiers = true\n"
        "[[mapping]]\n"
        'key = "z"\n'
        'from = "command"\n'
        'to = "shift"\n'
    )
    cfg = load(path)
    assert cfg.source_filter == "all"
    assert cfg.tap_location == "hid"
    assert cfg.cache_ttl == 5.0
    assert len(cfg.ruleset) == 1
    assert cfg.ruleset.rules[0].to_flags == FLAG_SHIFT
    assert cfg.ruleset.rules[0].strict is True


def test_bad_source_filter_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('source_filter = "wishful"\n')
    with pytest.raises(ConfigError, match="source_filter must be"):
        load(path)


def test_invalid_toml_reports_path(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is not = = toml\n")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load(path)


def test_shipped_config_is_valid():
    """The config.toml we ship must parse and produce the intended 11 rules."""
    cfg = load()
    assert len(cfg.ruleset) == 11
    assert cfg.source_filter == "nomachine"
    assert isinstance(cfg.ruleset, Ruleset)
    assert {rule.describe() for rule in cfg.ruleset.rules} == {
        "ctrl+c -> cmd+c",
        "ctrl+v -> cmd+v",
        "ctrl+a -> cmd+a",
        "ctrl+z -> cmd+z",
        "ctrl+x -> cmd+x",
        "ctrl+s -> cmd+s",
        "ctrl+n -> cmd+n",
        "ctrl+t -> cmd+t",
        "shift+space -> cmd+space",
        "ctrl+tab -> cmd+tab",
        "ctrl+esc -> cmd+`",
    }
    assert cfg.pause_hotkey is not None
    assert cfg.pause_hotkey.describe() == "shift+ctrl+opt+p"


# --- the pause hotkey -----------------------------------------------------


def test_default_hotkey_is_ctrl_opt_shift_p():
    hotkey = build_hotkey(None)
    assert hotkey is not None
    assert hotkey.keycode == VK_P
    assert hotkey.flags == FLAG_CONTROL | FLAG_ALTERNATE | FLAG_SHIFT


def test_hotkey_can_be_disabled():
    assert build_hotkey({"enabled": False, "key": "p", "modifiers": ["control"]}) is None


def test_hotkey_accepts_a_bare_modifier_string():
    hotkey = build_hotkey({"key": "p", "modifiers": "cmd"})
    assert hotkey is not None and hotkey.flags == FLAG_COMMAND


def test_hotkey_requires_a_modifier():
    """A swallowed bare letter would silently eat that key everywhere."""
    with pytest.raises(ConfigError, match="at least one"):
        build_hotkey({"key": "p"})
    with pytest.raises(ConfigError, match="at least one"):
        build_hotkey({"key": "p", "modifiers": ["capslock"]})


def test_hotkey_rejects_unknown_modifier():
    with pytest.raises(ConfigError, match="unknown modifier"):
        build_hotkey({"key": "p", "modifiers": ["hyper"]})


def test_hotkey_rejects_missing_key():
    with pytest.raises(ConfigError, match="missing key"):
        build_hotkey({"modifiers": ["control"]})


def test_hotkey_rejects_wrong_shape():
    with pytest.raises(ConfigError, match="expected a table"):
        build_hotkey(["control", "p"])
    with pytest.raises(ConfigError, match="modifiers must be"):
        build_hotkey({"key": "p", "modifiers": 3})


def test_hotkey_from_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[[mapping]]\nkey = "c"\nfrom = "control"\nto = "command"\n'
        '[pause_hotkey]\nkey = "q"\nmodifiers = ["command", "shift"]\n'
    )
    cfg = load(path)
    assert cfg.pause_hotkey is not None
    assert cfg.pause_hotkey.describe() == "shift+cmd+q"


def test_missing_hotkey_section_falls_back_to_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[[mapping]]\nkey = "c"\nfrom = "control"\nto = "command"\n')
    cfg = load(path)
    assert cfg.pause_hotkey is not None
    assert cfg.pause_hotkey.keycode == VK_P
