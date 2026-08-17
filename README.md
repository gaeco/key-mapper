# key-mapper

Remap `Ctrl+any key` to `Cmd+any key` on macOS, for keystrokes arriving from
a **NoMachine** client — leaving the Mac's own keyboard untouched.

So pressing `Ctrl+C` on the client keyboard produces a `Cmd+C` event on macOS,
and copy/paste work with the muscle memory the client keyboard was built for.

Setup this was built for:

| | |
|---|---|
| NoMachine server | macOS 15.7.3 (this machine) |
| NoMachine client | Galaxy S22 Ultra, Android 16 / One UI 8.0, Samsung DeX |

## Why Karabiner-Elements can't do this

Karabiner is the obvious tool and it *cannot work here*, which is worth
understanding before reaching for it again:

| Layer | Sees physical keyboards | Sees NoMachine's keys |
|---|---|---|
| Karabiner-Elements (IOKit virtual HID driver) | yes | **no** |
| Quartz event tap (this tool) | yes | yes |

`nxnode` injects remote keystrokes as *synthetic CGEvents* — that is why
NoMachine asks for Accessibility permission on macOS. Synthetic events never pass
through Karabiner's HID driver, so a Karabiner rule can never match them. An
event tap sits downstream of injection, where those events do appear.

## Install

```sh
python3 -m venv .venv                 # Homebrew python is PEP 668 managed
./.venv/bin/python -m pip install -e '.[dev]'
```

## Permissions — read this first

**Two separate permissions are required, and they are easy to confuse.** Grant
both to the app that *runs* the command (your terminal — `keymapper doctor`
prints which one it detected), then **fully quit and reopen it**. A running
process does not pick up a new grant.

System Settings → Privacy & Security →

| Permission | Why | Symptom when missing |
|---|---|---|
| **Input Monitoring** | see `keyDown`/`keyUp` at all | tap is created but **no keystroke ever arrives** |
| **Accessibility** | *modify* events | events observed but rewriting silently does nothing |

The Input Monitoring failure mode is genuinely deceptive, and worth spelling out
because it cost time during development: with Accessibility but not Input
Monitoring, `CGEventTapCreate` **succeeds** for an event mask containing
`flagsChanged` and **fails** for a mask of only `keyDown|keyUp`. Modifier changes
carry no typed characters so they aren't treated as keystroke capture; real keys
are. A tap asking for all three is therefore created happily and then never
delivers a single key — which looks exactly like "NoMachine's keys are invisible
to event taps" and invites entirely the wrong conclusion. `keymapper doctor`
checks Input Monitoring directly via `IOHIDCheckAccess` rather than inferring it,
and both `probe` and `run` refuse to start without it.

```sh
./.venv/bin/keymapper doctor
```

## Usage

```sh
./.venv/bin/keymapper doctor                # permissions, NoMachine processes, config, conflicts
./.venv/bin/keymapper probe                 # see raw events and where they came from
./.venv/bin/keymapper run --dry-run         # log what would change, modify nothing
./.venv/bin/keymapper run                   # the real thing
```

### `probe` — do this before trusting anything

`probe` installs listen-only taps at all three tap locations and prints every key
event with the fields revealing its origin:

```
[session] keyDown  key=  9 (   v) flags=0x00040000 (ctrl) srcPid=67161 srcState=combinedSession proc=nxnode NOMACHINE
```

Press `Ctrl+V` on the MacBook keyboard, then press it from DeX, then stop with
Ctrl-C. (Use `Ctrl+V` rather than `Ctrl+C` for the test — `Ctrl+C` in the
terminal running `probe` just interrupts it.) The summary groups what it saw by
source. Two things to confirm:

1. **NoMachine keys appear at all** (if not, see Fallbacks below).
2. **`srcPid` / `srcState` differ between the two keyboards** — this is what
   makes `source_filter = "nomachine"` possible.

### `run`

Logs each rewrite:

```
  remap       keyDown  ctrl+c -> cmd+c
```

Stop with Ctrl-C.

## Configuration

`config.toml`; every field is documented inline there.

- `source_filter` — `"nomachine"` (default), `"synthetic"`, or `"all"`
- `tap_location` — `"session"` (default), `"hid"`, `"annotatedSession"`
- `strict_modifiers` — `false` (default) lets `Ctrl+Shift+C` convert and keep its
  Shift, matching Karabiner's `"optional": ["any"]`
- `[[mapping]]` — the rules, in priority order; first match wins
- `[pause_hotkey]` — the pause toggle, `Ctrl+Opt+Shift+P` by default; set
  `enabled = false` to remove it

A mapping's `key` may be `"any"`, which matches every keycode. That is how the
shipped config converts the whole Control layer in one rule rather than listing
keys one at a time. Because the first matching rule wins, an exception to the
catch-all is just a rule placed above it — which is how `Ctrl+Esc → Cmd+\``
survives.

Mappings are not restricted to Ctrl→Cmd: any modifier pair works, and swapping
`from` and `to` reverses the direction. `to_key` additionally rewrites the key
itself, so a chord with no counterpart on the other side can still be expressed.

## Pausing

`Ctrl+Opt+Shift+P` pauses all remapping; pressing it again resumes. While paused
every key passes through untouched, so you get native behaviour back — useful for
a shell session that needs its real `Ctrl+C` — without stopping the process and
re-granting anything.

The chord is swallowed, so the frontmost app never sees it, and it is *not*
subject to `source_filter`: it works from the MacBook's own keyboard as well as
from the NoMachine client. It matches strictly, so `Cmd+Ctrl+Opt+Shift+P` is left
alone. Each toggle logs `PAUSED` / `RESUMED` to stderr, which is the only
indication of state — there is no menu bar item.

## Behaviour and limitations

**The entire Control layer loses its terminal meaning for the remote client.**
This is the cost of the catch-all, and it is bigger than it was when only five
keys converted. Since macOS now sees `Cmd+<key>` for every Control chord, a shell
inside the session loses `Ctrl+C` (SIGINT), `Ctrl+D` (EOF), `Ctrl+Z` (suspend),
and `Ctrl+A`/`E`/`K`/`L`/`R`/`U`/`W` (readline), along with every tmux prefix and
every `Ctrl` binding in vim, nano and less. In exchange every `Ctrl` shortcut you
know from Windows or Linux works in every Mac app.

The pause hotkey is the intended escape hatch: `Ctrl+Opt+Shift+P` gives the whole
Control layer back for as long as you need it. For a permanent exception, add a
`[[mapping]]` above the catch-all. Local typing on the MacBook's own keyboard is
unaffected while `source_filter = "nomachine"`.

**`flagsChanged` events are passed through unmodified, by design.** Rewriting the
Control key's own press/release would turn Control into Command *globally*,
including for the local keyboard. Consequence: an app that reads modifier state
from `flagsChanged` rather than from the `keyDown`'s own flags may still see
Control. This is why a shortcut that needs Command genuinely *held* — the
`Cmd+Tab` app switcher staying open to cycle through apps — does not behave as it
would with a real Cmd.
Most apps read the `keyDown` flags. If one app misbehaves while others are fine,
this is the first thing to suspect.

**Only the modifier is rewritten, never the keycode.** That is what lets the tap
mutate the event in place and return it, with no `CGEventPost` re-injection —
so there is no feedback loop and no need for loop-breaking sentinel tags.

**Going deaf.** macOS disables an event tap whose callback is too slow. The
callback re-enables itself on `kCGEventTapDisabledByTimeout`, and a health check
every ~5s catches a tap disabled while no events are flowing; if it can't be
revived the process exits rather than pretending to work.

## Karabiner interaction

Karabiner-Elements has been uninstalled, so nothing currently competes with this
tool. Two leftovers are worth knowing about:

- `~/.config/karabiner/karabiner.json` survives an uninstall, still holding the
  old *"Command+C/V/A to Control+C/V/A"* rule and a `left_control →
  left_command` simple modification. Config alone rewrites nothing — only
  `karabiner_grabber` intercepts keys — so this is inert. Delete the file if you
  don't want it reapplied should Karabiner ever be reinstalled.
- `Karabiner-Menu`, `Karabiner-NotificationWindow`, and
  `Karabiner-VirtualHIDDevice-Daemon` may still be running from
  `/Library/Application Support/org.pqrs/`. They are UI and driver helpers, not
  the key interceptor, and don't affect keystrokes.

`keymapper doctor` checks whether `karabiner_grabber` is actually running: it
reports a leftover config as an informational note and only warns (failing the
run) when a live Karabiner profile overlaps. Two kinds of overlap count — a
complex rule mentioning Command, and any simple modification involving Control or
Command, since a `left_control → left_command` mapping does at the HID layer what
this tool does at the session tap.

If Karabiner is ever reinstalled, note that a rule pointing the opposite way
(`Cmd→Ctrl`) is harmless while `source_filter = "nomachine"` — neither sees the
other's events — but under `source_filter = "all"` the two fight: Karabiner
rewrites a local `Cmd+C` to `Ctrl+C` at the HID layer and this tool rewrites it
back at the session tap.

## Fallbacks

If `probe` shows no NoMachine events at any tap location, an event tap is the
wrong layer and the options are:

1. `keymapper run --tap hid` — earlier in the pipeline; generally needs root.
2. NoMachine **client-side** keyboard/modifier settings in the Android app.
3. NoMachine **server-side** `node.cfg`
   (`/Applications/NoMachine.app/Contents/Frameworks/etc/node.cfg`).
4. Bind at the application layer — tmux `bind`, vim `map`, terminal key profiles.

## Development

```sh
./.venv/bin/python -m pytest         # 73 tests
```

`remap.py` holds the transform logic and imports nothing from macOS, so it is
tested directly. `keycodes.py` duplicates the Carbon/CoreGraphics constants for
the same reason; the values were verified against pyobjc's own rather than
transcribed from memory. `runloop.py` exists because `CFRunLoopRun()` parks in
`mach_msg` and Python signal handlers never run, so Ctrl-C could not stop the
process — it slices the loop instead, and `tests/test_runloop.py` guards that.
