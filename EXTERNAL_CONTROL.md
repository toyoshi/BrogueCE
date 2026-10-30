# External Control API

An opt-in API for ordinary play: read the rendered UI as JSON and submit normal
keyboard/mouse input over a local Unix socket. There is no debug API, hidden-state
endpoint, arbitrary state mutation, or HTTP dependency. The game implementation
is C; the optional reference client and Mac packager use Python's standard library.

Based on upstream Brogue CE v1.15.1. SDL graphical backend on macOS/Linux only.
Game rules and save/recording formats are unchanged. Enable explicitly with
`--control-dir /absolute/private/directory`; otherwise the API is disabled.

## Build and start

On macOS:

```sh
brew install sdl2 sdl2_image
make -j4 RELEASE=YES bin/brogue
cd bin
./brogue --control-dir /tmp/brogue-control-$(id -u)
```

The game creates the control directory with mode 0700. An existing directory
must already be owned by you, mode 0700, and not itself a symlink. Use a short
path under `/tmp` because macOS Unix socket paths have a small length limit.
One running game per directory. A second game refuses an existing socket.
After a crash, verify the old process is gone before removing its stale socket.

## Python reference API

From the repository root:

```python
from tools.external_control import read_state, send_input
import os

control_dir = f"/tmp/brogue-control-{os.getuid()}"
observed = read_state(control_dir)
print("\n".join(observed["lines"]))
# Decide the action from THIS observation, then pass that exact observation.
result = send_input(control_dir, "key 105 0 0", observed)  # i: inventory
```

`send_input(directory, command, observed, timeout=5, wait=True)` requires the
observation used to decide the command. It never substitutes a newer observation
or automatically retries a command. If another input changes the screen before
submission, the game rejects `stale_sequence`. After a restart it rejects
`stale_session`, even if the new game happens to have the same sequence number.

CLI examples:

```sh
python3 tools/external_control.py screen
python3 tools/external_control.py state > /tmp/observed.json
# Inspect the observation and choose exactly one command:
python3 tools/external_control.py --observed /tmp/observed.json key i
# Capture and inspect a NEW observation before the next command.
python3 tools/external_control.py state > /tmp/observed.json
python3 tools/external_control.py --observed /tmp/observed.json key Escape
```

Other actions are `move nw|n|ne|w|e|sw|s|se|wait`, `key s` (search),
`hover X Y`, and `click X Y`; each requires `--observed`. Coordinates are zero-based
SCREEN columns/rows. A click emits a press followed by release, so it works on
buttons as well as the map. Map clicks can begin multi-turn pathfinding. Prefer
individual movement keys for careful control. Keyboard/mouse use remains normal.
The controller must inspect menus: a key can select an item instead of moving.

## Read contract

`state.json` is atomically replaced at input-wait boundaries. It contains:

- `schema_version: 1`, `scope: "rendered_screen_only"`
- `session`: random per-process ID from OS entropy, independent of the game RNG
- `sequence`: published observation counter, **not game turn number**
- `last_input_id`: latest API input delivered completely before this observation
- `input_pending`: a click press has been delivered but its release is pending
- `text_input`: the game's input function is currently in text-entry mode
- `width: 100`, `height: 34`, `lines`: complete displayed Unicode character grid
- `sidebar`: left 20 columns; `messages`: upper-right three lines
- `viewport`: normal map rectangle at screen `(21,3)`, width 79, height 29
- `cells[y][x]`: `[glyph, fg_r, fg_g, fg_b, bg_r, bg_g, bg_b]`, RGB in 0..100

Only cells sent to the renderer are captured. No dungeon map, monster/item lists,
identification tables, seed, RNG state, or save contents are read. Unexplored tiles
stay blank. Previously explored terrain remains as displayed memory, not current
hidden terrain. Unidentified descriptions remain unidentified. Matching foreground
and background colors suppress the character. Tile mode exports text equivalents.
Menus/dialogs covering the map are exported as overlays, never the map underneath.

This is a rendered character/color API, not a semantic entity API: HP bars retain
their displayed label/colors, without adding hidden numeric values. Use the normal
inventory/hover/help UI to obtain descriptions visible to the player.

Snapshots and remote inputs are processed at input-wait boundaries. During
animations, automatic exploration, or pathfinding, the last observation can
remain unchanged. Remote input does not interrupt these operations mid-animation;
physical keys retain their normal interruption behavior. The JSON left after exit
is historical. Discard observations from previous sessions. If snapshot publication
fails, new input is rejected with `state_unavailable`; publication retries once per
second without advancing the sequence until successful.

## Socket protocol

Bind a short client path in a private temporary directory and send one ASCII
Unix datagram to `input.sock`:

```text
key CODE CTRL SHIFT SESSION SEQUENCE
hover SCREEN_X SCREEN_Y SESSION SEQUENCE
click SCREEN_X SCREEN_Y SESSION SEQUENCE
```

`SESSION` and `SEQUENCE` must come from the observation used for the decision.
`CODE` is printable ASCII or Escape=27, Enter=10, Tab=9, Backspace=127,
Up=63232, Down=63233, Left=63234, Right=63235. CTRL/SHIFT are 0 or 1.
The JSON reply contains `ok`, `session`, `input_id`, and the accepted `sequence`.
Errors are `invalid_command`, `stale_session`, `stale_sequence`, `state_unavailable`.
Socket and snapshots use mode 0600. There is no network listener.

Acknowledgment means accepted input, not successful gameplay. Inspect a later
observation in the same session with a higher sequence and `last_input_id >= input_id`.
For a click, the intermediate press snapshot does not count as completion. A
subsequent confirmation prompt still requires a new explicit decision. Python
waits for the later observation by default; `--no-wait` returns just acknowledgment
(useful for exit commands with no next observation). Do not blindly resend after
timeout: the action might already have been executed.

## Separate Mac app

```sh
python3 tools/package_external_control.py /absolute/output/folder
```

Open `Brogue External Control.app`. It enables the default control directory
`/tmp/brogue-control-UID` and stores game files in `Session/` next to the app.
It does not use the original app's save directory. The local CPU build bundles
installed SDL dependencies and is ad-hoc signed, not notarized.

## Verification

```sh
make -j4 RELEASE=YES bin/brogue
python3 test/test_external_control.py
```

Tests cover real-game inventory BUTTON clicks, item details, rest, movement and
search; rendered-only/Unicode JSON; stale observations and restarts; malformed,
out-of-bounds and duplicate commands; private paths; and recovery after failed
publication. Tests use SDL's dummy video driver and isolated temporary game files.
