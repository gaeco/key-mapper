"""Unit tests for the pure remap logic.

Runs without pyobjc, Accessibility permission, or a window session.
"""

from __future__ import annotations

import pytest

from keymapper.keycodes import (
    EVENT_FLAGS_CHANGED,
    EVENT_KEY_DOWN,
    EVENT_KEY_UP,
    FLAG_ALPHA_SHIFT,
    FLAG_ALTERNATE,
    FLAG_COMMAND,
    FLAG_CONTROL,
    FLAG_SECONDARY_FN,
    FLAG_SHIFT,
    VK_A,
    VK_B,
    VK_C,
    VK_Q,
    VK_V,
    VK_X,
    VK_Z,
)
from keymapper.remap import Rule, Ruleset

MAPPED_KEYS = [VK_C, VK_V, VK_A, VK_Z, VK_X]


def ctrl_to_cmd(strict: bool = False) -> Ruleset:
    """The shipped default: ctrl+{c,v,a,z,x} -> cmd+same."""
    return Ruleset(
        [
            Rule(keycode=kc, from_flags=FLAG_CONTROL, to_flags=FLAG_COMMAND, strict=strict)
            for kc in MAPPED_KEYS
        ]
    )


# --- the happy path -------------------------------------------------------


@pytest.mark.parametrize("keycode", MAPPED_KEYS)
@pytest.mark.parametrize("event_type", [EVENT_KEY_DOWN, EVENT_KEY_UP])
def test_control_becomes_command(keycode, event_type):
    """All five combos convert, on both press and release."""
    assert ctrl_to_cmd().decide(event_type, keycode, FLAG_CONTROL) == FLAG_COMMAND


@pytest.mark.parametrize("keycode", MAPPED_KEYS)
def test_control_bit_is_cleared_not_just_ored(keycode):
    result = ctrl_to_cmd().decide(EVENT_KEY_DOWN, keycode, FLAG_CONTROL)
    assert result is not None
    assert not result & FLAG_CONTROL, "control bit must be removed, not left set"
    assert result & FLAG_COMMAND


# --- pass-through cases ---------------------------------------------------


def test_unmapped_key_untouched():
    for keycode in (VK_B, VK_Q):
        assert ctrl_to_cmd().decide(EVENT_KEY_DOWN, keycode, FLAG_CONTROL) is None


def test_no_control_modifier_untouched():
    """Plain 'c' and shift+c must not be rewritten."""
    rs = ctrl_to_cmd()
    assert rs.decide(EVENT_KEY_DOWN, VK_C, 0) is None
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_SHIFT) is None


def test_command_already_held_untouched():
    """cmd+c arriving as cmd+c is already correct; don't touch it."""
    rs = ctrl_to_cmd()
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_COMMAND) is None
    # ctrl+cmd+c: command already present, so leave it for the app to interpret
    # rather than silently dropping the control bit.
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL | FLAG_COMMAND) is None


def test_flags_changed_never_rewritten():
    """Rewriting flagsChanged would make Control into Command globally."""
    assert ctrl_to_cmd().decide(EVENT_FLAGS_CHANGED, VK_C, FLAG_CONTROL) is None


def test_unknown_event_type_untouched():
    assert ctrl_to_cmd().decide(999, VK_C, FLAG_CONTROL) is None


# --- extra modifiers: lenient (default) vs strict -------------------------


def test_lenient_allows_extra_modifiers():
    """Mirrors Karabiner's optional:[any] -- ctrl+shift+c still converts."""
    rs = ctrl_to_cmd(strict=False)
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL | FLAG_SHIFT) == (
        FLAG_COMMAND | FLAG_SHIFT
    ), "the extra shift must survive the rewrite"
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL | FLAG_ALTERNATE) == (
        FLAG_COMMAND | FLAG_ALTERNATE
    )


def test_strict_rejects_extra_modifiers():
    rs = ctrl_to_cmd(strict=True)
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL) == FLAG_COMMAND
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL | FLAG_SHIFT) is None


@pytest.mark.parametrize("noise", [FLAG_ALPHA_SHIFT, FLAG_SECONDARY_FN])
def test_strict_ignores_insignificant_flags(noise):
    """Caps lock and fn are incidental and must not defeat a strict match."""
    rs = ctrl_to_cmd(strict=True)
    result = rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL | noise)
    assert result == FLAG_COMMAND | noise, "incidental bit should be preserved"


# --- ruleset plumbing ----------------------------------------------------


def test_keycodes_covers_all_rules():
    assert ctrl_to_cmd().keycodes() == frozenset(MAPPED_KEYS)


def test_first_match_wins():
    rs = Ruleset(
        [
            Rule(VK_C, FLAG_CONTROL, FLAG_COMMAND),
            Rule(VK_C, FLAG_CONTROL, FLAG_ALTERNATE),
        ]
    )
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL) == FLAG_COMMAND


def test_matching_rule_reports_the_rule():
    rs = ctrl_to_cmd()
    rule = rs.matching_rule(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL)
    assert rule is not None and rule.keycode == VK_C
    assert rs.matching_rule(EVENT_KEY_DOWN, VK_B, FLAG_CONTROL) is None


def test_describe_is_readable():
    assert Rule(VK_C, FLAG_CONTROL, FLAG_COMMAND).describe() == "ctrl+c -> cmd+c"


def test_empty_ruleset_is_inert():
    assert Ruleset([]).decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL) is None
