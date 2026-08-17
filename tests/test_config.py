"""Tests for config parsing and validation."""

from __future__ import annotations

import pytest

from keymapper.config import DEFAULT_MAPPINGS, ConfigError, build_ruleset, load
from keymapper.keycodes import FLAG_COMMAND, FLAG_CONTROL, FLAG_SHIFT, VK_C
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
    """The config.toml we ship must parse and produce the intended 7 rules."""
    cfg = load()
    assert len(cfg.ruleset) == 7
    assert cfg.source_filter == "nomachine"
    assert isinstance(cfg.ruleset, Ruleset)
    assert {rule.describe() for rule in cfg.ruleset.rules} == {
        "ctrl+c -> cmd+c",
        "ctrl+v -> cmd+v",
        "ctrl+a -> cmd+a",
        "ctrl+z -> cmd+z",
        "ctrl+x -> cmd+x",
        "ctrl+s -> cmd+s",
        "ctrl+space -> cmd+space",
    }
