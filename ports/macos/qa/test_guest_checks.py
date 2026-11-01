"""Guest helper safety/fixture tests; no ADB, emulator, SDK or real device."""
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

SPEC = importlib.util.spec_from_file_location("guest_checks_fixture", Path(__file__).with_name("guest_checks.py"))
guest = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(guest)


class GuestChecksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="modavd-guest-helper-test-")
        self.root = Path(self.temp.name)
        self.instance = SimpleNamespace(root=self.root, owned_device=Mock(return_value="emulator-20060"),
                                        adb=Mock(return_value=subprocess.CompletedProcess([], 0, "fixture output", "")))
        self.checks = guest.GuestChecks(self.instance)
        self.write("runtime.json", {"schemaVersion": 1, "id": "a" * 32})
        self.write("adb-server.json", {"pid": 100, "started": "fixture-server"})
        self.write("emulator.json", {"pid": 101, "started": "fixture-emulator"})

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        (self.root / name).write_text(json.dumps(data))

    def test_plan_never_initializes_or_calls_adb(self):
        missing = self.root / "missing"
        with patch.object(guest, "Runtime") as runtime, patch.object(guest.runtime_module, "same_process") as processes, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(guest.main(["--root", str(missing), "plan"]), 0)
        runtime.assert_not_called()
        processes.assert_not_called()
        self.assertFalse(missing.exists())
        self.assertEqual(json.loads(output.getvalue())["adbServerPort"], 20050)

    def test_stale_private_server_refuses_before_owned_device_or_adb(self):
        with patch.object(guest.runtime_module, "same_process", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "no ADB request"):
                self.checks.shell("getprop")
        self.instance.owned_device.assert_not_called()
        self.instance.adb.assert_not_called()

    def test_changed_process_does_not_access_replacement_guest(self):
        with patch.object(guest.runtime_module, "same_process", return_value=True) as processes:
            self.assertEqual(self.checks.shell("getprop"), "fixture output")
            self.assertIn(unittest.mock.call({"pid": 100, "started": "fixture-server"}, "tcp:20050"), processes.call_args_list)
            self.instance.adb.assert_called_once_with("-s", "emulator-20060", "shell", "getprop", timeout=20)
            self.write("emulator.json", {"pid": 102, "started": "different-instance"})
            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                self.checks.shell("uname -a")
        self.assertEqual(self.instance.owned_device.call_count, 1)
        self.assertEqual(self.instance.adb.call_count, 1)

    def test_permission_and_missing_command_are_unavailable(self):
        self.instance.adb.return_value = subprocess.CompletedProcess([], 0, "partial evidence\n", "cat: Permission denied\n")
        with patch.object(guest.runtime_module, "same_process", return_value=True):
            item = self.checks.read("fixture", "partial read")
        self.assertEqual(item["status"], "unavailable")
        self.assertIn("partial evidence", item["evidence"]["text"])
        self.assertIn("Permission denied", item["evidence"]["text"])
        with patch.object(self.checks, "shell", return_value="Permission Denial: denied"):
            self.assertEqual(self.checks.read("x", "fixture")["status"], "unavailable")
        with patch.object(self.checks, "shell", side_effect=subprocess.CalledProcessError(127, "x", stderr="not found")):
            self.assertEqual(self.checks.read("x", "fixture")["status"], "unavailable")

    def test_loopback_server_serves_only_fixed_generated_payload(self):
        token = "b" * 32
        with guest.payload_server(token) as (port, path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
                data = response.read()
            self.assertEqual(data, guest.PAYLOAD)
            self.assertEqual(hashlib.sha256(data).hexdigest(), guest.PAYLOAD_SHA256)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/other", timeout=3)
            self.assertEqual(error.exception.code, 404)
        with self.assertRaises(ValueError):
            with guest.payload_server("../foreign"):
                pass

    def test_network_hash_and_cleanup_are_scoped_to_random_task_directory(self):
        commands = []
        def shell(script):
            commands.append(script)
            if script == "toybox":
                return "nc sha256sum tail head wc"
            if "toybox nc -w" in script:
                return f"HTTP/1.0 200 OK\n{guest.PAYLOAD_SHA256}  payload.bin\n{len(guest.PAYLOAD)}\n"
            return ""
        with patch.object(self.checks, "shell", side_effect=shell), patch.object(guest.runtime_module, "space"):
            record = self.checks.network()
        self.assertEqual(record["status"], "pass")
        self.assertEqual(record["cleanup"], "completed")
        self.assertTrue(any("/data/local/tmp/modavd-qa-" in cmd for cmd in commands))
        self.assertFalse(any("rm -r" in cmd or "5037" in cmd for cmd in commands))
        self.assertIn("rmdir /data/local/tmp/modavd-qa-", commands[-1])

    def test_evidence_retains_hash_when_truncated(self):
        raw = "x" * (guest.MAX_EVIDENCE + 20)
        record = guest.evidence(raw)
        self.assertTrue(record["truncated"])
        self.assertEqual(len(record["text"]), guest.MAX_EVIDENCE)
        self.assertEqual(record["sha256"], hashlib.sha256(raw.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
