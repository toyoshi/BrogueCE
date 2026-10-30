#!/usr/bin/env python3
"""Integration tests: make bin/brogue, then python3 test/test_external_control.py."""
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
spec = importlib.util.spec_from_file_location("control", ROOT / "tools/external_control.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


class ExternalControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="brogue-build-", dir="/tmp")
        cls.harness = Path(cls.build.name) / "harness"
        subprocess.run(["cc", "-std=c99", "-Isrc/brogue", "-Isrc/platform", "-Isrc/variants",
                        "test/external_control_harness.c", "src/platform/external-control.c",
                        "-o", str(cls.harness)], cwd=ROOT, check=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="brogue-test-", dir="/tmp")
        self.directory = Path(self.temp.name) / "control"
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
                    str(ROOT / "bin"), "--control-dir", str(self.directory)])
        self.process = subprocess.Popen(command, cwd=self.temp.name, env=env,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(250):
            if (self.directory / "state.json").exists():
                return control.read_state(self.directory)
            if self.process.poll() is not None:
                self.fail(self.process.stderr.read().decode())
            time.sleep(.02)
        self.fail("No initial snapshot")

    def send(self, command, observed=None, **options):
        observation = control.read_state(self.directory) if observed is None else observed
        return control.send_input(self.directory, command, observation, **options)

    def await_state(self, predicate):
        for _ in range(200):
            state = control.read_state(self.directory)
            if predicate(state): return state
            time.sleep(.02)
        self.fail("Expected observation did not arrive")

    def raw(self, message, session=None):
        # Tests use compact strings; insert the session field into the wire command.
        if ' ' in message:
            command, sequence = message.rsplit(' ', 1)
            session = session or control.read_state(self.directory)['session']
            message = f"{command} {session} {sequence}"
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
            'text_input', 'session', 'input_pending', 'width', 'height', 'lines', 'sidebar', 'messages', 'viewport',
            'cell_format', 'cells'})

    def test_reject_stale_malformed_and_out_of_bounds_inputs(self):
        initial = self.start(harness=True)
        for request in ['key 122 0 0 0', 'key 122 0 0 999']:
            self.assertEqual(self.raw(request)["error"], "stale_sequence")
        for request in ['bad', 'key 122 2 0 1', 'key 999 0 0 1', 'key 122 0 0 1 garbage',
                        'click -1 0 1', 'click 100 0 1', 'hover 0 34 1',
                        'key 122 0 0 1\x00hidden', 'key ' + '9'*80 + ' 0 0 1',
                        'key 122 0 0 ' + '9'*80, 'x' * 300]:
            self.assertEqual(self.raw(request)["error"], "invalid_command")
        self.assertEqual(control.read_state(self.directory), initial)
        next_state = self.send( 'key 122 0 0')
        self.assertEqual(next_state["last_input_id"], 1)
        self.assertGreater(next_state["sequence"], initial["sequence"])

    def test_duplicate_delivery_cannot_repeat_an_action(self):
        initial = self.start(harness=True)
        request = f"key 122 0 0 {initial['sequence']}"
        self.assertTrue(self.raw(request)["ok"])
        self.assertEqual(self.raw(request)["error"], "stale_sequence")
        self.assertEqual(control.read_state(self.directory)["last_input_id"], 1)

    def test_real_game_inventory_item_details_and_wait(self):
        initial = self.start()
        self.assertIn('Hello and welcome', ''.join(initial['messages']))
        self.assertTrue(any('@' in row for row in initial['viewport']['lines']))
        x = initial['lines'][-1].index('Inventory') + 2
        inventory = self.send(f'click {x} 33')
        self.assertFalse(inventory['input_pending'])
        self.assertIn('dagger', ''.join(inventory['lines']))
        detail = self.send( 'key 98 0 0')
        self.assertNotEqual(detail['lines'], inventory['lines'])
        self.send( 'key 27 0 0')
        # Escape may close the detail and inventory together; both are valid.
        state = control.read_state(self.directory)
        for _ in range(2):
            if 'Inventory' in state['lines'][-1]:
                break
            state = self.send( 'key 27 0 0')
        waited = self.send( 'key 122 0 0')
        self.assertGreater(waited['last_input_id'], initial['last_input_id'])
        self.assertIn('Depth: 1', waited['lines'][-1])
        def player_position(screen):
            return next((x, y) for y, row in enumerate(screen['viewport']['lines'])
                        for x, char in enumerate(row) if char == '@')
        before = player_position(waited)
        moved = self.send( 'key 104 0 0')
        self.assertEqual(player_position(moved), (before[0] - 1, before[1]))
        searched = self.send( 'key 115 0 0')
        self.assertEqual(player_position(searched), player_position(moved))
        self.assertEqual((self.directory.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((self.directory / 'state.json').stat().st_mode & 0o777), 0o600)

    def test_client_preserves_observation_precondition(self):
        observed = self.start(harness=True)
        current = self.send('key 122 0 0', observed)
        with self.assertRaisesRegex(RuntimeError, 'stale_sequence'):
            self.send('key 122 0 0', observed)
        self.assertEqual(control.read_state(self.directory), current)

    def test_restart_rejects_old_session_at_same_sequence(self):
        observed = self.start(harness=True)
        self.send('key 113 0 0', observed, wait=False)
        self.process.wait(timeout=2)
        self.process.stderr.close()
        self.process = None
        (self.directory / 'state.json').unlink()
        current = self.start(harness=True)
        self.assertEqual(current['sequence'], observed['sequence'])
        self.assertNotEqual(current['session'], observed['session'])
        with self.assertRaisesRegex(RuntimeError, 'stale_session'):
            self.send('key 122 0 0', observed)
        self.assertEqual(control.read_state(self.directory)['last_input_id'], 0)

    def test_publish_failure_rejects_input_then_recovers(self):
        observed = self.start(harness=True)
        self.directory.chmod(0o500)
        try:
            self.assertTrue(self.raw(f"key 122 0 0 {observed['sequence']}")['ok'])
            time.sleep(.05)
            self.assertEqual(control.read_state(self.directory)['sequence'], observed['sequence'])
            self.assertEqual(self.raw(f"key 122 0 0 {observed['sequence']}")['error'], 'state_unavailable')
        finally:
            self.directory.chmod(0o700)
        current = self.await_state(lambda state: state['sequence'] > observed['sequence'])
        result = self.send('key 122 0 0', current)
        self.assertEqual(result['last_input_id'], 2)

    def test_private_directory_required_and_existing_socket_preserved(self):
        self.directory.mkdir(mode=0o755)
        result = subprocess.run([str(self.harness), str(self.directory)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.directory.chmod(0o700)
        self.start(harness=True)
        result = subprocess.run([str(self.harness), str(self.directory)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.directory / 'input.sock').exists())
        self.assertTrue(self.send( 'key 122 0 0')['last_input_id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
