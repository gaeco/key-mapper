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
        keycode: virtual keycode the rule applies to, or None to match every
            key -- which is how "convert the whole Control layer" is expressed
            as one rule rather than one per key.
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
        allow_all_sources: when True this rule ignores `source_filter` entirely
            and fires whatever posted the event -- the local MacBook keyboard as
            well as the NoMachine client. Per-rule rather than global because the
            two kinds of rule want opposite answers: the Control catch-all must
            stay remote-only or the local shell loses Ctrl+C, while a chord you
            want everywhere should not be hostage to that.
    """

    keycode: int | None
    from_flags: int
    to_flags: int
    strict: bool = False
    to_keycode: int | None = None
    allow_all_sources: bool = False

    @property
    def matches_any_key(self) -> bool:
        return self.keycode is None

    def matches(self, keycode: int, flags: int) -> bool:
        if self.keycode is not None and keycode != self.keycode:
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

    def apply(self, keycode: int, flags: int) -> Change:
        """The rewrite for an event that matched on `keycode`.

        The incoming keycode has to be passed in rather than read off the rule:
        a wildcard rule has no keycode of its own, and passes through whichever
        key it matched.
        """
        return Change(
            flags=(flags & ~self.from_flags) | self.to_flags,
            keycode=keycode if self.to_keycode is None else self.to_keycode,
        )

    def describe(self) -> str:
        source = "any" if self.keycode is None else key_name(self.keycode)
        target = source if self.to_keycode is None else key_name(self.to_keycode)
        # The source scope is part of what the rule does, so it belongs in the
        # one-line description the startup banner and `doctor` both print --
        # otherwise two rules that behave very differently look identical there.
        scope = " (any source)" if self.allow_all_sources else ""
        return (
            f"{describe_flags(self.from_flags)}+{source}"
            f" -> {describe_flags(self.to_flags)}+{target}{scope}"
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

    def resolve(
        self, event_type: int, keycode: int, flags: int
    ) -> tuple[Rule, Change] | None:
        """The winning rule and its rewrite, or None to leave the event alone.

        Returns both halves because the tap needs both: the change to apply, and
        the rule itself to know whether `allow_all_sources` exempts it from the
        source filter. One scan of the rules rather than two on the hot path.

        `flagsChanged` events are never rewritten. Converting the Control key's
        own press/release would turn Control into Command globally rather than
        just for the mapped keys.
        """
        if event_type not in REMAPPABLE_EVENTS:
            return None
        for rule in self.rules:
            if rule.matches(keycode, flags):
                change = rule.apply(keycode, flags)
                # A rule that would change nothing is not worth a log line.
                # First match wins, so a no-op match stops the search rather
                # than falling through to a later rule.
                if change == Change(flags, keycode):
                    return None
                return rule, change
        return None

    def decide(self, event_type: int, keycode: int, flags: int) -> Change | None:
        """Return the rewrite to apply, or None to leave the event untouched."""
        resolved = self.resolve(event_type, keycode, flags)
        return None if resolved is None else resolved[1]

    def matching_rule(self, event_type: int, keycode: int, flags: int) -> Rule | None:
        """Like `decide`, but returns the rule -- used for log messages."""
        if event_type not in REMAPPABLE_EVENTS:
            return None
        for rule in self.rules:
            if rule.matches(keycode, flags):
                return rule
        return None

    def keycodes(self) -> frozenset[int]:
        """Keycodes the specific rules name; lets the tap bail out early.

        Wildcard rules contribute nothing here, so this set is only a valid
        early-out when `watches_all_keys()` is False.
        """
        return frozenset(
            rule.keycode for rule in self.rules if rule.keycode is not None
        )

    def watches_all_keys(self) -> bool:
        """Whether any rule is a wildcard, which defeats the keycode early-out."""
        return any(rule.matches_any_key for rule in self.rules)

    def __len__(self) -> int:
        return len(self.rules)
