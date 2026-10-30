#!/usr/bin/env python3
"""Integration tests: make bin/brogue, then python3 test/test_visible_bridge.py."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bridge", ROOT / "tools/visible_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class VisibleBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="brogue-build-", dir="/tmp")
        cls.harness = Path(cls.build.name) / "harness"
        subprocess.run(["cc", "-std=c99", "-Isrc/brogue", "-Isrc/platform", "-Isrc/variants",
                        "test/visible_bridge_harness.c", "src/platform/visible-bridge.c",
                        "-o", str(cls.harness)], cwd=ROOT, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brogue-test-", dir="/tmp")
        self.directory = Path(self.temp.name) / "bridge"
        self.process = None

    def tearDown(self):
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=5)
            self.process.stderr.close()
        self.temp.cleanup()

    def start(self, harness=False):
        env = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy")
        command = ([str(self.harness), str(self.directory)] if harness else
                   [str(ROOT / "bin/brogue"), "--no-gpu", "-s", "12345", "--data-dir",
                    str(ROOT / "bin"), "--bridge-dir", str(self.directory)])
        self.process = subprocess.Popen(command, cwd=self.temp.name, env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(250):
            if (self.directory / "state.json").exists():
                return bridge.read_state(self.directory)
            if self.process.poll() is not None:
                self.fail(self.process.stderr.read().decode())
            time.sleep(.02)
        self.fail("No initial snapshot")

    def raw(self, message):
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.bind(str(Path(self.temp.name) / "client"))
            client.settimeout(2)
            client.sendto(message.encode(), str(self.directory / "input.sock"))
            try:
                return json.loads(client.recv(1024))
            finally:
                (Path(self.temp.name) / "client").unlink()

    def test_rendered_only_unicode_and_invisible_foreground(self):
        state = self.start(harness=True)
        self.assertEqual(state["viewport"]["lines"][0][:5], '@"\\\U0001f780 ')
        self.assertEqual(state["cells"][3][25][0], ' ')
        self.assertNotIn('X', ''.join(state["lines"]))
        self.assertEqual(state["cells"][3][24][0], '\U0001f780')
        self.assertEqual(len(state["lines"]), 34)
        self.assertTrue(all(len(row) == 100 for row in state["lines"]))
        self.assertEqual(state["scope"], "rendered_screen_only")
        self.assertEqual(set(state), {'schema_version', 'scope', 'sequence', 'last_input_id',
            'text_input', 'width', 'height', 'lines', 'sidebar', 'messages', 'viewport',
            'cell_format', 'cells'})

    def test_reject_stale_malformed_and_out_of_bounds_inputs(self):
        initial = self.start(harness=True)
        for request in ['key 122 0 0 0', 'key 122 0 0 999']:
            self.assertEqual(self.raw(request)["error"], "stale_sequence")
        for request in ['bad', 'key 122 2 0 1', 'key 999 0 0 1', 'key 122 0 0 1 garbage',
                        'click -1 0 1', 'click 100 0 1', 'hover 0 34 1',
                        'key 122 0 0 1\x00hidden', 'x' * 300]:
            self.assertEqual(self.raw(request)["error"], "invalid_command")
        self.assertEqual(bridge.read_state(self.directory), initial)
        next_state = bridge.send_input(self.directory, 'key 122 0 0')
        self.assertEqual(next_state["last_input_id"], 1)
        self.assertGreater(next_state["sequence"], initial["sequence"])

    def test_duplicate_delivery_cannot_repeat_an_action(self):
        initial = self.start(harness=True)
        request = f"key 122 0 0 {initial['sequence']}"
        self.assertTrue(self.raw(request)["ok"])
        self.assertEqual(self.raw(request)["error"], "stale_sequence")
        self.assertEqual(bridge.read_state(self.directory)["last_input_id"], 1)

    def test_real_game_inventory_item_details_and_wait(self):
        initial = self.start()
        self.assertIn('Hello and welcome', ''.join(initial['messages']))
        self.assertTrue(any('@' in row for row in initial['viewport']['lines']))
        inventory = bridge.send_input(self.directory, 'key 105 0 0')
        self.assertIn('dagger', ''.join(inventory['lines']))
        detail = bridge.send_input(self.directory, 'key 98 0 0')
        self.assertNotEqual(detail['lines'], inventory['lines'])
        bridge.send_input(self.directory, 'key 27 0 0')
        # Escape may close the detail and inventory together; both are valid.
        state = bridge.read_state(self.directory)
        for _ in range(2):
            if 'Inventory' in state['lines'][-1]:
                break
            state = bridge.send_input(self.directory, 'key 27 0 0')
        waited = bridge.send_input(self.directory, 'key 122 0 0')
        self.assertGreater(waited['last_input_id'], initial['last_input_id'])
        self.assertIn('Depth: 1', waited['lines'][-1])
        def player_position(screen):
            return next((x, y) for y, row in enumerate(screen['viewport']['lines'])
                        for x, char in enumerate(row) if char == '@')
        before = player_position(waited)
        moved = bridge.send_input(self.directory, 'key 104 0 0')
        self.assertEqual(player_position(moved), (before[0] - 1, before[1]))
        searched = bridge.send_input(self.directory, 'key 115 0 0')
        self.assertEqual(player_position(searched), player_position(moved))
        self.assertEqual((self.directory.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((self.directory / 'state.json').stat().st_mode & 0o777), 0o600)

    def test_private_directory_required_and_existing_socket_preserved(self):
        self.directory.mkdir(mode=0o755)
        result = subprocess.run([str(self.harness), str(self.directory)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.directory.chmod(0o700)
        self.start(harness=True)
        result = subprocess.run([str(self.harness), str(self.directory)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.directory / 'input.sock').exists())
        self.assertTrue(bridge.send_input(self.directory, 'key 122 0 0')['last_input_id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
