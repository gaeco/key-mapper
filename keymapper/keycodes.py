"""macOS virtual keycodes and CGEvent flag masks.

These are plain integer constants copied from Carbon's <HIToolbox/Events.h>
(kVK_ANSI_*) and CoreGraphics' <CGEventTypes.h> (kCGEventFlagMask*). They are
duplicated here rather than imported from Quartz so that the pure remap logic
and its tests can run without pyobjc or a macOS window session.
"""

# --- Virtual keycodes (kVK_ANSI_*) -----------------------------------------
# Only the keys we care about, plus a few used in tests as negative cases.
VK_A = 0x00
VK_S = 0x01
VK_D = 0x02
VK_Z = 0x06
VK_X = 0x07
VK_C = 0x08
VK_V = 0x09
VK_B = 0x0B
VK_Q = 0x0C

#: Human-readable name -> virtual keycode, for parsing config.toml.
KEY_NAMES: dict[str, int] = {
    "a": VK_A,
    "s": VK_S,
    "d": VK_D,
    "z": VK_Z,
    "x": VK_X,
    "c": VK_C,
    "v": VK_V,
    "b": VK_B,
    "q": VK_Q,
}

#: Virtual keycode -> name, for log output.
KEY_CODES: dict[int, str] = {code: name for name, code in KEY_NAMES.items()}


def key_name(keycode: int) -> str:
    """Best-effort display name for a keycode."""
    return KEY_CODES.get(keycode, f"0x{keycode:02x}")


# --- CGEvent modifier flag masks (kCGEventFlagMask*) ----------------------
FLAG_ALPHA_SHIFT = 0x00010000  # caps lock
FLAG_SHIFT = 0x00020000
FLAG_CONTROL = 0x00040000
FLAG_ALTERNATE = 0x00080000  # option
FLAG_COMMAND = 0x00100000
FLAG_NUMERIC_PAD = 0x00200000
FLAG_HELP = 0x00400000
FLAG_SECONDARY_FN = 0x00800000

#: Name -> mask, for parsing config.toml.
FLAG_NAMES: dict[str, int] = {
    "capslock": FLAG_ALPHA_SHIFT,
    "shift": FLAG_SHIFT,
    "control": FLAG_CONTROL,
    "ctrl": FLAG_CONTROL,
    "option": FLAG_ALTERNATE,
    "alt": FLAG_ALTERNATE,
    "command": FLAG_COMMAND,
    "cmd": FLAG_COMMAND,
    "fn": FLAG_SECONDARY_FN,
}

#: The modifiers that matter when deciding whether a chord matches. Caps lock,
#: numeric pad and fn are excluded deliberately: numeric-pad and fn bits are set
#: incidentally by many keys (arrows, function row) and would otherwise defeat
#: strict matching, and caps lock state is orthogonal to a chord.
SIGNIFICANT_FLAGS = FLAG_SHIFT | FLAG_CONTROL | FLAG_ALTERNATE | FLAG_COMMAND


def describe_flags(flags: int) -> str:
    """Render a flags bitmask as e.g. 'cmd+shift'. Returns '-' when empty."""
    parts = []
    for name, mask in (
        ("caps", FLAG_ALPHA_SHIFT),
        ("shift", FLAG_SHIFT),
        ("ctrl", FLAG_CONTROL),
        ("opt", FLAG_ALTERNATE),
        ("cmd", FLAG_COMMAND),
        ("pad", FLAG_NUMERIC_PAD),
        ("fn", FLAG_SECONDARY_FN),
    ):
        if flags & mask:
            parts.append(name)
    return "+".join(parts) if parts else "-"


# --- CGEventType values ---------------------------------------------------
# From <CGEventTypes.h>. Duplicated for the same reason as above.
EVENT_KEY_DOWN = 10
EVENT_KEY_UP = 11
EVENT_FLAGS_CHANGED = 12
EVENT_TAP_DISABLED_BY_TIMEOUT = 0xFFFFFFFE
EVENT_TAP_DISABLED_BY_USER_INPUT = 0xFFFFFFFF

EVENT_NAMES: dict[int, str] = {
    EVENT_KEY_DOWN: "keyDown",
    EVENT_KEY_UP: "keyUp",
    EVENT_FLAGS_CHANGED: "flagsChanged",
    EVENT_TAP_DISABLED_BY_TIMEOUT: "tapDisabledByTimeout",
    EVENT_TAP_DISABLED_BY_USER_INPUT: "tapDisabledByUserInput",
}


def event_name(event_type: int) -> str:
    return EVENT_NAMES.get(event_type, f"type{event_type}")


# --- CGEventSourceStateID -------------------------------------------------
# Verified against pyobjc's Quartz constants, not guessed: Private really is -1.
SOURCE_STATE_PRIVATE = -1  # kCGEventSourceStatePrivate
SOURCE_STATE_COMBINED_SESSION = 0  # kCGEventSourceStateCombinedSessionState
SOURCE_STATE_HID_SYSTEM = 1  # kCGEventSourceStateHIDSystemState

SOURCE_STATE_NAMES: dict[int, str] = {
    SOURCE_STATE_PRIVATE: "private",
    SOURCE_STATE_COMBINED_SESSION: "combinedSession",
    SOURCE_STATE_HID_SYSTEM: "hidSystem",
}


def source_state_name(state_id: int) -> str:
    return SOURCE_STATE_NAMES.get(state_id, f"state{state_id}")
