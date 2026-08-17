"""Pure modifier-remapping logic.

Deliberately free of any macOS/pyobjc import so it can be unit-tested on any
platform. The tap layer (`tap.py`) reads fields off a real CGEvent and hands the
plain integers to `Ruleset.decide`.

A rule rewrites the modifier bitmask and, optionally, the keycode. Both are
plain fields on a CGEvent, so the tap can mutate the event in place and return
it rather than synthesising a replacement with CGEventPost -- which would feed
straight back into our own tap and require loop-breaking sentinels. What no rule
does is turn one event into a different *number* of events; that is the line
that keeps the tap stateless.
"""

from __future__ import annotations

from dataclasses import dataclass

from .keycodes import (
    EVENT_KEY_DOWN,
    EVENT_KEY_UP,
    SIGNIFICANT_FLAGS,
    describe_flags,
    key_name,
)

#: Event types a rule may apply to. Both press and release are rewritten: if
#: only keyDown were remapped, an app tracking key release would see a Control
#: chord open and a Command chord close.
REMAPPABLE_EVENTS = (EVENT_KEY_DOWN, EVENT_KEY_UP)


@dataclass(frozen=True)
class Change:
    """The rewrite to apply to an event: modifier bits, and which key to send.

    `keycode` is always populated, even when the rule leaves the key alone, so
    the tap has one shape to apply rather than two optional halves.
    """

    flags: int
    keycode: int


@dataclass(frozen=True)
class Rule:
    """Rewrite `from_flags` to `to_flags` on a given key.

    Args:
        keycode: virtual keycode the rule applies to.
        from_flags: modifier bits that must be present to match.
        to_flags: modifier bits to substitute them with.
        strict: when True the chord must carry *exactly* `from_flags` (ignoring
            caps lock / numeric pad / fn). When False any extra modifier is
            tolerated, matching the `"optional": ["any"]` semantics of the
            equivalent Karabiner-Elements rule.
        to_keycode: virtual keycode to send instead, or None to keep the key.
            Most rules keep it: a modifier swap is all that Ctrl+C -> Cmd+C
            needs. Rewriting the key as well is for chords with no counterpart
            on the other side, such as Ctrl+Esc -> Cmd+`.
    """

    keycode: int
    from_flags: int
    to_flags: int
    strict: bool = False
    to_keycode: int | None = None

    def matches(self, keycode: int, flags: int) -> bool:
        if keycode != self.keycode:
            return False
        if flags & self.from_flags != self.from_flags:
            return False
        # Already in the target state -- nothing to do, and rewriting would be
        # a no-op that still costs a log line. A rule that also rewrites the key
        # is exempt: the modifier may be right while the key is still wrong, so
        # there is real work left to do.
        if flags & self.to_flags and self.to_keycode is None:
            return False
        if self.strict:
            extra = flags & SIGNIFICANT_FLAGS & ~self.from_flags
            if extra:
                return False
        return True

    @property
    def target_keycode(self) -> int:
        """The keycode this rule sends -- the original unless overridden."""
        return self.keycode if self.to_keycode is None else self.to_keycode

    def apply(self, flags: int) -> Change:
        return Change(
            flags=(flags & ~self.from_flags) | self.to_flags,
            keycode=self.target_keycode,
        )

    def describe(self) -> str:
        return (
            f"{describe_flags(self.from_flags)}+{key_name(self.keycode)}"
            f" -> {describe_flags(self.to_flags)}+{key_name(self.target_keycode)}"
        )


@dataclass(frozen=True)
class Hotkey:
    """A chord that toggles remapping off and on.

    Matched strictly: the significant modifiers must be *exactly* `flags`, so
    Ctrl+Opt+Shift+P fires while Ctrl+Opt+Shift+Cmd+P does not. Lenient matching
    would be wrong here for a reason it is not wrong for a `Rule` -- the tap
    swallows this chord rather than passing it on, so a hotkey that fired on any
    superset of itself would make every such chord untypeable.

    Caps lock, fn and numeric-pad bits are ignored, as everywhere else.
    """

    keycode: int
    flags: int

    def matches(self, event_type: int, keycode: int, flags: int) -> bool:
        if event_type not in REMAPPABLE_EVENTS:
            return False
        if keycode != self.keycode:
            return False
        return flags & SIGNIFICANT_FLAGS == self.flags & SIGNIFICANT_FLAGS

    def describe(self) -> str:
        return f"{describe_flags(self.flags)}+{key_name(self.keycode)}"


class Ruleset:
    """An ordered collection of rules; first match wins."""

    def __init__(self, rules: list[Rule]) -> None:
        self.rules = rules

    def decide(self, event_type: int, keycode: int, flags: int) -> Change | None:
        """Return the rewrite to apply, or None to leave the event untouched.

        `flagsChanged` events are never rewritten. Converting the Control key's
        own press/release would turn Control into Command globally rather than
        just for the mapped keys.
        """
        if event_type not in REMAPPABLE_EVENTS:
            return None
        for rule in self.rules:
            if rule.matches(keycode, flags):
                change = rule.apply(flags)
                # A rule that would change nothing is not worth a log line.
                return change if change != Change(flags, keycode) else None
        return None

    def matching_rule(self, event_type: int, keycode: int, flags: int) -> Rule | None:
        """Like `decide`, but returns the rule -- used for log messages."""
        if event_type not in REMAPPABLE_EVENTS:
            return None
        for rule in self.rules:
            if rule.matches(keycode, flags):
                return rule
        return None

    def keycodes(self) -> frozenset[int]:
        """Every keycode any rule cares about; lets the tap bail out early."""
        return frozenset(rule.keycode for rule in self.rules)

    def __len__(self) -> int:
        return len(self.rules)
