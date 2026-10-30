#!/usr/bin/env python3
"""Read Brogue's rendered JSON or deliver exactly one local input event."""
import argparse
import json
import os
from pathlib import Path
import socket
import tempfile
import time

SPECIAL_KEYS = {"Escape": 27, "Enter": 10, "Tab": 9, "Backspace": 127,
                "Up": 63232, "Down": 63233, "Left": 63234, "Right": 63235,
                "Space": 32}
MOVES = {"nw": "y", "n": "k", "ne": "u", "w": "h", "e": "l",
         "sw": "b", "s": "j", "se": "n", "wait": "z"}


def read_state(directory):
    return json.loads((Path(directory) / "state.json").read_text())


def send_input(directory, command, observed, timeout=5, wait=True):
    """No automatic retry: a lost acknowledgment must never duplicate a move."""
    wire = f"{command} {observed['session']} {observed['sequence']}".encode("ascii")
    # A short private pathname is required by macOS Unix sockets.
    with tempfile.TemporaryDirectory(prefix="brogue-client-", dir="/tmp") as temp:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.bind(str(Path(temp) / "reply.sock"))
            client.settimeout(timeout)
            client.sendto(wire, str(Path(directory) / "input.sock"))
            result = json.loads(client.recv(1024))
    if not result["ok"]:
        raise RuntimeError(result)
    if not wait:
        return result
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = read_state(directory)
        if current["session"] != observed["session"]:
            raise RuntimeError("Game session changed while waiting for input result")
        if (current["sequence"] > result["sequence"]
                and current["last_input_id"] >= result["input_id"]):
            return current
        time.sleep(.02)
    raise TimeoutError(f"Input {result['input_id']} was accepted, but no next input boundary "
                       "arrived. Do not resend blindly (the game may have exited).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=f"/tmp/brogue-control-{os.getuid()}")
    parser.add_argument("--observed", type=Path, help="JSON observation used to decide this input (required for actions)")
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--no-wait", action="store_true", help="return acknowledgment only")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("state")
    sub.add_parser("screen")
    key = sub.add_parser("key")
    key.add_argument("key", help="one ASCII character, Escape/Enter/Space/Tab/Backspace or arrow")
    key.add_argument("--ctrl", action="store_true")
    key.add_argument("--shift", action="store_true")
    move = sub.add_parser("move")
    move.add_argument("direction", choices=MOVES)
    for name in ("hover", "click"):
        mouse = sub.add_parser(name)
        mouse.add_argument("x", type=int, help="zero-based SCREEN column, 0..99")
        mouse.add_argument("y", type=int, help="zero-based SCREEN row, 0..33")
    args = parser.parse_args()
    try:
        if args.action in ("state", "screen"):
            state = read_state(args.dir)
            if args.action == "screen":
                print("\n".join(state["lines"]))
                return
        else:
            if args.action in ("key", "move"):
                value = MOVES[args.direction] if args.action == "move" else args.key
                code = SPECIAL_KEYS.get(value)
                if code is None:
                    if len(value) != 1 or not 32 <= ord(value) <= 126:
                        parser.error("key must be one printable ASCII character or a named special key")
                    code = ord(value)
                command = f"key {code} {int(getattr(args, 'ctrl', False))} {int(getattr(args, 'shift', False))}"
            else:
                if not (0 <= args.x < 100 and 0 <= args.y < 34):
                    parser.error("screen coordinates must be x=0..99, y=0..33")
                command = f"{args.action} {args.x} {args.y}"
            if args.observed is None:
                parser.error("actions require --observed PATH with the JSON used to decide the action")
            observed = json.loads(args.observed.read_text())
            state = send_input(args.dir, command, observed, args.timeout, not args.no_wait)
        print(json.dumps(state, ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        parser.exit(1, f"External control: {error}\n")


if __name__ == "__main__":
    main()
