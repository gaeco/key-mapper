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
    VK_ESCAPE,
    VK_GRAVE,
    VK_P,
    VK_Q,
    VK_V,
    VK_X,
    VK_Z,
)
from keymapper.remap import Change, Hotkey, Rule, Ruleset

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
    assert ctrl_to_cmd().decide(event_type, keycode, FLAG_CONTROL) == Change(
        flags=FLAG_COMMAND, keycode=keycode
    )


@pytest.mark.parametrize("keycode", MAPPED_KEYS)
def test_control_bit_is_cleared_not_just_ored(keycode):
    result = ctrl_to_cmd().decide(EVENT_KEY_DOWN, keycode, FLAG_CONTROL)
    assert result is not None
    assert not result.flags & FLAG_CONTROL, "control bit must be removed, not left set"
    assert result.flags & FLAG_COMMAND


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
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL | FLAG_SHIFT) == Change(
        flags=FLAG_COMMAND | FLAG_SHIFT, keycode=VK_C
    ), "the extra shift must survive the rewrite"
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL | FLAG_ALTERNATE) == Change(
        flags=FLAG_COMMAND | FLAG_ALTERNATE, keycode=VK_C
    )


def test_strict_rejects_extra_modifiers():
    rs = ctrl_to_cmd(strict=True)
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL) == Change(FLAG_COMMAND, VK_C)
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL | FLAG_SHIFT) is None


@pytest.mark.parametrize("noise", [FLAG_ALPHA_SHIFT, FLAG_SECONDARY_FN])
def test_strict_ignores_insignificant_flags(noise):
    """Caps lock and fn are incidental and must not defeat a strict match."""
    rs = ctrl_to_cmd(strict=True)
    result = rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL | noise)
    assert result == Change(
        flags=FLAG_COMMAND | noise, keycode=VK_C
    ), "incidental bit should be preserved"


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
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL) == Change(FLAG_COMMAND, VK_C)


def test_matching_rule_reports_the_rule():
    rs = ctrl_to_cmd()
    rule = rs.matching_rule(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL)
    assert rule is not None and rule.keycode == VK_C
    assert rs.matching_rule(EVENT_KEY_DOWN, VK_B, FLAG_CONTROL) is None


def test_describe_is_readable():
    assert Rule(VK_C, FLAG_CONTROL, FLAG_COMMAND).describe() == "ctrl+c -> cmd+c"


def test_empty_ruleset_is_inert():
    assert Ruleset([]).decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL) is None


# --- rewriting the key as well as the modifier ----------------------------


def esc_to_grave() -> Ruleset:
    """The shipped ctrl+esc -> cmd+` rule."""
    return Ruleset(
        [
            Rule(
                keycode=VK_ESCAPE,
                from_flags=FLAG_CONTROL,
                to_flags=FLAG_COMMAND,
                to_keycode=VK_GRAVE,
            )
        ]
    )


@pytest.mark.parametrize("event_type", [EVENT_KEY_DOWN, EVENT_KEY_UP])
def test_key_is_rewritten_with_the_modifier(event_type):
    assert esc_to_grave().decide(event_type, VK_ESCAPE, FLAG_CONTROL) == Change(
        flags=FLAG_COMMAND, keycode=VK_GRAVE
    )


def test_key_rewrite_keeps_extra_modifiers():
    assert esc_to_grave().decide(EVENT_KEY_DOWN, VK_ESCAPE, FLAG_CONTROL | FLAG_SHIFT) == (
        Change(flags=FLAG_COMMAND | FLAG_SHIFT, keycode=VK_GRAVE)
    )


def test_key_rewrite_still_matches_on_the_source_key():
    """The rule fires on esc, not on the ` it produces."""
    rs = esc_to_grave()
    assert rs.decide(EVENT_KEY_DOWN, VK_GRAVE, FLAG_CONTROL) is None
    assert rs.keycodes() == frozenset({VK_ESCAPE})


def test_unset_to_keycode_keeps_the_key():
    rule = Rule(VK_C, FLAG_CONTROL, FLAG_COMMAND)
    assert rule.to_keycode is None
    assert rule.apply(VK_C, FLAG_CONTROL) == Change(flags=FLAG_COMMAND, keycode=VK_C)


def test_key_rewrite_fires_even_when_target_modifier_is_held():
    """Cmd already down does not mean the job is done -- the key is still esc."""
    assert esc_to_grave().decide(
        EVENT_KEY_DOWN, VK_ESCAPE, FLAG_CONTROL | FLAG_COMMAND
    ) == Change(flags=FLAG_COMMAND, keycode=VK_GRAVE)


def test_key_rewrite_describe_shows_both_keys():
    assert esc_to_grave().rules[0].describe() == "ctrl+esc -> cmd+`"


def test_key_only_rewrite_is_not_a_no_op():
    """Same modifier, different key: the change must survive the no-op guard."""
    rs = Ruleset(
        [
            Rule(
                keycode=VK_ESCAPE,
                from_flags=FLAG_CONTROL,
                to_flags=FLAG_CONTROL,
                to_keycode=VK_GRAVE,
            )
        ]
    )
    assert rs.decide(EVENT_KEY_DOWN, VK_ESCAPE, FLAG_CONTROL) == Change(
        flags=FLAG_CONTROL, keycode=VK_GRAVE
    )


# --- the wildcard rule: convert the whole Control layer --------------------


def any_ctrl_to_cmd(strict: bool = False) -> Ruleset:
    """The shipped catch-all: ctrl+<anything> -> cmd+<same>."""
    return Ruleset(
        [Rule(None, FLAG_CONTROL, FLAG_COMMAND, strict=strict)]
    )


@pytest.mark.parametrize("keycode", [VK_C, VK_Q, VK_ESCAPE, VK_GRAVE, 0x7F, 0x00])
@pytest.mark.parametrize("event_type", [EVENT_KEY_DOWN, EVENT_KEY_UP])
def test_wildcard_converts_every_key(keycode, event_type):
    """Including keys with no entry in the name table."""
    assert any_ctrl_to_cmd().decide(event_type, keycode, FLAG_CONTROL) == Change(
        flags=FLAG_COMMAND, keycode=keycode
    )


def test_wildcard_passes_the_matched_key_through_unchanged():
    """The rule has no keycode of its own, so it must not invent one."""
    for keycode in (VK_B, VK_Q, VK_ESCAPE):
        change = any_ctrl_to_cmd().decide(EVENT_KEY_DOWN, keycode, FLAG_CONTROL)
        assert change is not None and change.keycode == keycode


def test_wildcard_still_requires_its_modifier():
    """A blanket key match is not a blanket modifier match."""
    rs = any_ctrl_to_cmd()
    assert rs.decide(EVENT_KEY_DOWN, VK_C, 0) is None
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_SHIFT) is None
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_COMMAND) is None


def test_wildcard_ignores_flags_changed():
    assert any_ctrl_to_cmd().decide(EVENT_FLAGS_CHANGED, VK_C, FLAG_CONTROL) is None


def test_wildcard_honours_strict():
    rs = any_ctrl_to_cmd(strict=True)
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL) == Change(FLAG_COMMAND, VK_C)
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL | FLAG_SHIFT) is None


def test_wildcard_defeats_the_keycode_early_out():
    rs = any_ctrl_to_cmd()
    assert rs.watches_all_keys()
    assert rs.keycodes() == frozenset(), "nothing specific to fast-path on"


def test_specific_rules_win_over_a_wildcard_placed_after_them():
    """Ordering is the whole mechanism by which exceptions to the catch-all work."""
    rs = Ruleset(
        [
            Rule(VK_ESCAPE, FLAG_CONTROL, FLAG_COMMAND, to_keycode=VK_GRAVE),
            Rule(None, FLAG_CONTROL, FLAG_COMMAND),
        ]
    )
    assert rs.decide(EVENT_KEY_DOWN, VK_ESCAPE, FLAG_CONTROL) == Change(
        flags=FLAG_COMMAND, keycode=VK_GRAVE
    ), "the esc rule must win, not the catch-all"
    assert rs.decide(EVENT_KEY_DOWN, VK_C, FLAG_CONTROL) == Change(FLAG_COMMAND, VK_C)


def test_wildcard_placed_first_shadows_everything_after_it():
    """The failure mode the shipped config's ordering avoids."""
    rs = Ruleset(
        [
            Rule(None, FLAG_CONTROL, FLAG_COMMAND),
            Rule(VK_ESCAPE, FLAG_CONTROL, FLAG_COMMAND, to_keycode=VK_GRAVE),
        ]
    )
    assert rs.decide(EVENT_KEY_DOWN, VK_ESCAPE, FLAG_CONTROL) == Change(
        flags=FLAG_COMMAND, keycode=VK_ESCAPE
    ), "esc stays esc: the catch-all matched first"


def test_wildcard_describe_is_readable():
    assert any_ctrl_to_cmd().rules[0].describe() == "ctrl+any -> cmd+any"
    assert any_ctrl_to_cmd().rules[0].matches_any_key


def test_specific_rule_does_not_match_any_key():
    assert not Rule(VK_C, FLAG_CONTROL, FLAG_COMMAND).matches_any_key


# --- the pause hotkey -----------------------------------------------------

PAUSE_FLAGS = FLAG_CONTROL | FLAG_ALTERNATE | FLAG_SHIFT


def pause_hotkey() -> Hotkey:
    """The shipped default: ctrl+opt+shift+p."""
    return Hotkey(keycode=VK_P, flags=PAUSE_FLAGS)


@pytest.mark.parametrize("event_type", [EVENT_KEY_DOWN, EVENT_KEY_UP])
def test_hotkey_matches_its_chord(event_type):
    """Both halves match, so the tap can swallow the press and the release."""
    assert pause_hotkey().matches(event_type, VK_P, PAUSE_FLAGS)


def test_hotkey_ignores_other_keys():
    assert not pause_hotkey().matches(EVENT_KEY_DOWN, VK_C, PAUSE_FLAGS)


@pytest.mark.parametrize(
    "flags",
    [
        0,
        FLAG_CONTROL,
        FLAG_CONTROL | FLAG_SHIFT,
        FLAG_CONTROL | FLAG_ALTERNATE,
        FLAG_ALTERNATE | FLAG_SHIFT,
    ],
)
def test_hotkey_rejects_partial_chords(flags):
    assert not pause_hotkey().matches(EVENT_KEY_DOWN, VK_P, flags)


def test_hotkey_rejects_supersets():
    """Strict by design: a swallowed chord must not eat cmd+ctrl+opt+shift+p."""
    assert not pause_hotkey().matches(EVENT_KEY_DOWN, VK_P, PAUSE_FLAGS | FLAG_COMMAND)


@pytest.mark.parametrize("noise", [FLAG_ALPHA_SHIFT, FLAG_SECONDARY_FN])
def test_hotkey_ignores_insignificant_flags(noise):
    """Caps lock or fn being set must not stop the toggle firing."""
    assert pause_hotkey().matches(EVENT_KEY_DOWN, VK_P, PAUSE_FLAGS | noise)


def test_hotkey_ignores_flags_changed():
    assert not pause_hotkey().matches(EVENT_FLAGS_CHANGED, VK_P, PAUSE_FLAGS)


def test_hotkey_describe_is_readable():
    assert pause_hotkey().describe() == "shift+ctrl+opt+p"
