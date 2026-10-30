# Visible JSON + local input bridge

Based on upstream Brogue CE **v1.15.1**. The bridge is off unless started with
`--bridge-dir /absolute/private/directory`. Supported: the SDL graphical backend
on macOS/Linux. Game rules and save/recording formats are unchanged.

## Start on macOS

```sh
brew install sdl2 sdl2_image
make -j4 RELEASE=YES bin/brogue
mkdir -p /tmp/brogue-bridge-$(id -u)
chmod 700 /tmp/brogue-bridge-$(id -u)
cd bin
./brogue --bridge-dir /tmp/brogue-bridge-$(id -u)
```

From the repository root, in another terminal:

```sh
python3 tools/visible_bridge.py screen             # readable current screen
python3 tools/visible_bridge.py state              # complete JSON
python3 tools/visible_bridge.py key n              # New Game on title screen
python3 tools/visible_bridge.py move ne            # one diagonal step
python3 tools/visible_bridge.py move wait          # rest one turn
python3 tools/visible_bridge.py key s              # search
python3 tools/visible_bridge.py key i              # inventory
python3 tools/visible_bridge.py key Escape
python3 tools/visible_bridge.py hover 50 15         # inspect screen cell
python3 tools/visible_bridge.py click 50 15         # normal in-game click
```

A click on the map can start multi-turn pathfinding, exactly as a physical click
would. Prefer one movement key per request when controlling a game carefully.
The keyboard/mouse keep working normally. A controller must interpret the current
screen: a key can mean an item choice in a menu instead of movement.

To create a separate app on this Mac:

```sh
python3 tools/package_visible_bridge.py /absolute/path/to/output-folder
```

Open `Brogue Bridge.app` in that folder. It enables the default bridge, keeps its
own game files in `Session/` next to the app, and bundles the installed SDL
libraries. It does not use the original app's save directory. The app is built
for the local Mac's CPU and is ad-hoc signed (not notarized).

## What is exported

`state.json` is replaced atomically at each call to the game's input-wait function.
Readers see a complete old or new snapshot, never a half-written file. It includes:

- `schema_version`, `scope`, `sequence`, `last_input_id`, `text_input`
- `width`, `height`: the 100 × 34 character screen
- `lines`: the full displayed screen as 34 Unicode strings
- `sidebar`: leftmost 20 columns; `messages`: upper-right 3 lines
- `viewport`: normal map rectangle at screen `(21,3)`, width 79, height 29
- `cells[y][x]`: `[glyph, fg_r, fg_g, fg_b, bg_r, bg_g, bg_b]`, RGB in 0..100

Coordinates are **zero-based screen coordinates**. Normal dungeon coordinates
are `screen_x - 21`, `screen_y - 3`. Menus and dialogs can cover the map rectangle;
`viewport` then contains that overlay, not the map beneath it.

Only final cells sent to the renderer are captured. There is **no access to the
internal dungeon map, monster/item lists, identification tables, seed, RNG, or
save contents**. Unexplored tiles stay blank. Previously explored terrain remains
as displayed memory. Unidentified item descriptions remain unidentified. The
only route to extra detail is the same inventory/hover/help UI available to the
player. Foreground glyphs matching the background color are exported as spaces.
In tile mode, glyphs are their text equivalents, not sprite images.

This is a lossless character/color representation of the rendered UI, **not a
semantic entity API**. HP bars retain their label and cell colors rather than
inventing a precise numeric HP value absent from the UI. Enemy/item descriptions
can be read by opening their normal UI. No hidden engine metadata is exported.

Snapshots are input-boundary observations, not a video stream. During animations,
automatic exploration, or pathfinding, the last snapshot can remain unchanged.
Remote input is consumed only at the next input-wait loop, not in the middle of
an animation. Physical keys retain their native interruption behavior. A snapshot
remaining on disk after exit is historical; socket existence alone is not proof
of a live process.

## Input protocol / Python API

Python uses only the standard library. You can import `read_state(directory)` and
`send_input(directory, command, timeout=5, wait=True)` from `tools/visible_bridge.py`.

The transport is a local Unix datagram socket at `input.sock`. Bind a short client
socket path in a private temporary directory, then send one ASCII datagram:

```text
key CODE CTRL SHIFT EXPECTED_SEQUENCE
hover SCREEN_X SCREEN_Y EXPECTED_SEQUENCE
click SCREEN_X SCREEN_Y EXPECTED_SEQUENCE
```

`CODE` is one printable ASCII code or Escape=27, Enter=10, Tab=9, Backspace=127,
Up=63232, Down=63233, Left=63234, Right=63235. CTRL/SHIFT are 0 or 1.
The reply is JSON, for example:

```json
{"ok":true,"input_id":1,"sequence":4}
```

An acknowledgment means the event was delivered to the game, not that its action
succeeded. Inspect a later snapshot with `last_input_id >= input_id` and a higher
`sequence`. `sequence` counts input boundaries, **not game turns**. The client waits
for this by default. `--no-wait` returns just the acknowledgment, useful for exit
commands that have no next snapshot. A timeout after acceptance must not cause a
blind retry. Errors are `invalid_command` or `stale_sequence`. Read a fresh screen
before deciding whether to submit a new action. Restarting the game starts a new
session; discard outstanding commands/snapshots from the old session.

The directory must be owned by the current user, mode 0700, and not itself a
symlink. Socket and snapshots are mode 0600. There is no network listener or HTTP
server. Use a directory under `/tmp` because macOS socket paths are short. One
running game per directory. A second process refuses an existing socket. After
a crash, verify the old process has exited before removing its stale `input.sock`.

## Verification

```sh
make -j4 RELEASE=YES bin/brogue
python3 test/test_visible_bridge.py
```

The tests run the real game with SDL's dummy video driver and an isolated renderer
fixture. They cover JSON/Unicode escaping, invisible foreground suppression,
private directories, stale/duplicate/malformed/out-of-bounds input rejection,
and actual inventory/item-detail/rest interaction. Test game files live in
separate temporary directories. The fixture links no engine globals, so it can
only export the cells explicitly drawn into it.
