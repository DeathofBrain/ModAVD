"""Synthetic Manager capture tests; never access ADB, guests or existing SHM."""
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("modavd_manager_capture", Path(__file__).with_name("capture_manager.py"))
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class ManagerCaptureTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="modavd-manager-capture-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / "package"
        self.avd = self.root / "avd/mas_0.avd"
        self.avd.mkdir(parents=True)
        self.cli = self.root / "tools/modavd"
        self.adb = self.root / "sdk/platform-tools/adb"
        self.qemu_path = self.root / "sdk/emulator/qemu/darwin-aarch64/qemu-system-aarch64"
        for file in (self.cli, self.adb, self.qemu_path):
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(b"synthetic executable, never run: " + file.name.encode())
            file.chmod(0o700)
        (self.root / ".modavd-macos-package.json").write_text(json.dumps({
            "schema": 1, "tool": "ModAVD macOS local assembler", "owner": "synthetic-owner"}))
        (self.root / "PACKAGE-MANIFEST.json").write_text(json.dumps({
            "owner": "synthetic-owner", "platform": "mac-arm64",
            "files": {file.relative_to(self.root).as_posix(): {"sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
                      for file in (self.cli, self.adb)}}))
        (self.root / "mas-avd.json").write_text("{}")
        self.qemu = {"pid": 4243, "startedAtUs": 1600000000123456,
                     "executable": str(self.qemu_path),
                     "argv": [str(self.qemu_path), "-avd", "mas_0", "-ports", "20000,20001"],
                     "discoveryEnvironment": {"HOME": str(self.directory / "home")}}
        self.server = {"pid": 4242, "startedAtUs": 1600000000000000,
                       "executable": str(self.adb), "argv": [], "discoveryEnvironment": {}}
        self.record = self.directory / "home/Library/Caches/TemporaryItems/avd/running/pid_4243.ini"
        self.record.parent.mkdir(parents=True)
        self.record.write_text(f"avd.name=mas_0\navd.id=mas_0\navd.dir={self.avd}\nport.serial=20000\n")
        self.info = {"index": 0, "name": "mas_0", "status": "online", "running": True,
                     "booted": True, "busy": False, "pid": 4243, "serial": "emulator-20000",
                     "adb_address": "127.0.0.1:20001", "ports": {"console": 20000, "adb": 20001, "grpc": 20002},
                     "screencap": {"shm": "videmulator20000", "shm_ready": False}, "path": str(self.avd)}
        self.sync_start_time()
        self.status = {key: self.info[key] for key in ("index", "name", "status", "running", "booted", "busy", "pid")}
        self.runs = []
        self.start_response = "videmulator20000\nOK\n"
        self.start_failure = None
        pixels = bytes((1, 2, 3, 255, 4, 5, 6, 255))
        self.region = struct.pack("<IIIIQ", 2, 1, 60, 9, 987654) + pixels
        self.frame = capture.frames.copy_frame(self.region)
        self.patch(capture.subprocess, "run", side_effect=self.fake_run)
        self.patch(capture, "private_server", side_effect=lambda: copy.deepcopy(self.server))
        self.patch(capture, "process_snapshot", side_effect=self.snapshot)
        self.patch(capture.frames.runtime, "adb_server_ready", return_value=True)
        self.patch(capture.frames.runtime, "space")
        self.read = self.patch(capture.frames, "read_shm", return_value=self.frame)

    def patch(self, target, name, **kwargs):
        patcher = patch.object(target, name, **kwargs)
        result = patcher.start()
        self.addCleanup(patcher.stop)
        return result

    def sync_start_time(self):
        self.info["started_at_ms"] = self.qemu["startedAtUs"] // 1000
        self.info["started_at"] = dt.datetime.fromtimestamp(
            self.qemu["startedAtUs"] // 1000000, tz=dt.timezone.utc).isoformat()

    def snapshot(self, pid):
        self.assertEqual(pid, self.qemu["pid"])
        return copy.deepcopy(self.qemu)

    def fake_run(self, command, **kwargs):
        self.runs.append((command, kwargs))
        self.assertTrue(kwargs["check"])
        self.assertEqual(kwargs["env"]["ANDROID_ADB_SERVER_PORT"], "20050")
        self.assertEqual(kwargs["env"]["ADB_SERVER_SOCKET"], "tcp:127.0.0.1:20050")
        if command[0] == str(self.cli):
            self.assertEqual(command[:3], [str(self.cli), "--root", str(self.root)])
            self.assertEqual(command[4:], ["0", "--json"])
            self.assertIn(command[3], ("info", "status"))
            data = self.info if command[3] == "info" else self.status
            return subprocess.CompletedProcess(command, 0, json.dumps({"ok": True, "data": data}), "")
        self.assertEqual(command[:7], [str(self.adb), "-H", "127.0.0.1", "-P", "20050", "-s", "emulator-20000"])
        args = command[7:]
        if args == ["emu", "avd", "id"]:
            output = "mas_0\nOK\n"
        elif args == ["emu", "screenrecord", "webrtc", "start"]:
            if self.start_failure:
                raise self.start_failure
            output = self.start_response
        else:
            self.assertEqual(args, ["emu", "screenrecord", "webrtc", "stop"])
            output = "OK\n"
        return subprocess.CompletedProcess(command, 0, output, "")

    def emu_commands(self):
        return [command[7:] for command, _ in self.runs
                if command[0] == str(self.adb) and command[7:10] == ["emu", "screenrecord", "webrtc"]]

    def test_capture_outputs_png_with_package_process_and_frame_evidence(self):
        output = self.directory / "captured"
        result = capture.save_capture(self.root, 0, output)
        evidence = json.loads((output / "evidence.json").read_text())
        self.assertEqual(result["png"], str(output / "frame.png"))
        self.assertEqual(evidence["image"]["sha256"], hashlib.sha256((output / "frame.png").read_bytes()).hexdigest())
        self.assertEqual(evidence["processIdentity"]["qemu"]["startedAtUs"], self.qemu["startedAtUs"])
        self.assertEqual(evidence["processIdentity"]["avdPath"], str(self.avd))
        self.assertEqual(evidence["managerObservations"][0]["runtimeRecord"]["path"], str(self.record))
        self.assertEqual(evidence["streamOwnership"], "started_for_capture")
        self.assertEqual(evidence["frame"]["frameNumber"], 9)
        self.assertTrue(evidence["noSeqlock"])
        self.assertEqual(self.emu_commands(), [["emu", "screenrecord", "webrtc", "start"],
                                              ["emu", "screenrecord", "webrtc", "stop"]])
        self.read.assert_called_once_with("videmulator20000", expected_console_port=20000)

    def test_reuse_leaves_borrowed_stream_running_and_records_ownership(self):
        self.info["screencap"]["shm_ready"] = True
        output = self.directory / "borrowed"
        capture.save_capture(self.root, 0, output, reuse_stream=True)
        evidence = json.loads((output / "evidence.json").read_text())
        self.assertEqual(evidence["streamOwnership"], "borrowed_existing")
        self.assertIn("left running", evidence["cleanup"])
        self.assertEqual(len(evidence["managerObservations"]), 2)
        self.assertEqual(self.emu_commands(), [])

    def test_reuse_read_failure_never_starts_or_stops_stream(self):
        self.info["screencap"]["shm_ready"] = True
        self.read.side_effect = RuntimeError("synthetic read failure")
        collector = capture.ManagerCapture(self.root)
        with self.assertRaisesRegex(RuntimeError, "synthetic read failure"):
            collector.capture(reuse_stream=True)
        self.assertEqual(len(collector.observations), 2)
        self.assertEqual(self.emu_commands(), [])

    def test_default_refuses_existing_or_unknown_stream(self):
        for ready in (True, None):
            with self.subTest(ready=ready):
                self.info["screencap"]["shm_ready"] = ready
                with self.assertRaisesRegex(RuntimeError, "refusing to take it over"):
                    capture.ManagerCapture(self.root).capture()
        self.read.assert_not_called()
        self.assertEqual(self.emu_commands(), [])

    def test_reuse_requires_explicit_ready_state(self):
        for ready in (False, None):
            with self.subTest(ready=ready):
                self.info["screencap"]["shm_ready"] = ready
                with self.assertRaisesRegex(RuntimeError, "already ready"):
                    capture.ManagerCapture(self.root).capture(reuse_stream=True)
        self.read.assert_not_called()
        self.assertEqual(self.emu_commands(), [])

    def test_new_stream_read_failure_still_stops_owned_stream(self):
        self.read.side_effect = RuntimeError("synthetic read failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic read failure"):
            capture.ManagerCapture(self.root).capture()
        self.assertEqual(self.emu_commands()[-1][-1], "stop")

    def test_failed_or_partial_start_still_attempts_owned_stop(self):
        for failure, response in ((subprocess.CalledProcessError(1, ["synthetic start"]), ""),
                                  (None, "foreign-shm\nOK\n")):
            with self.subTest(failure=failure):
                self.runs.clear()
                self.start_failure, self.start_response = failure, response
                with self.assertRaises((subprocess.CalledProcessError, RuntimeError)):
                    capture.ManagerCapture(self.root).capture()
                self.assertEqual(self.emu_commands()[-1][-1], "stop")
        self.read.assert_not_called()

    def test_replaced_qemu_receives_no_stop(self):
        def replace(*args, **kwargs):
            self.qemu["startedAtUs"] += 1000000
            self.sync_start_time()
            return self.frame
        self.read.side_effect = replace
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            capture.ManagerCapture(self.root).capture()
        self.assertEqual([command[-1] for command in self.emu_commands()], ["start"])

    def test_replaced_private_server_receives_no_stop(self):
        def replace(*args, **kwargs):
            self.server["pid"] += 100
            return self.frame
        self.read.side_effect = replace
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            capture.ManagerCapture(self.root).capture()
        self.assertEqual([command[-1] for command in self.emu_commands()], ["start"])

    def test_changed_package_receives_no_stop(self):
        def replace(*args, **kwargs):
            self.cli.write_bytes(b"synthetic replacement")
            return self.frame
        self.read.side_effect = replace
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            capture.ManagerCapture(self.root).capture()
        self.assertEqual([command[-1] for command in self.emu_commands()], ["start"])

    def test_cli_identity_mismatch_rejected_before_guest_or_stream_commands(self):
        base = copy.deepcopy(self.info)
        other = self.root / "avd/other.avd"
        other.mkdir()
        replacements = {"serial": "emulator-20060", "path": str(other), "started_at_ms": 1,
                        "ports": {"console": 20000, "adb": 20001, "grpc": 9999}, "pid": 5555}
        for key, value in replacements.items():
            with self.subTest(key=key):
                self.info = copy.deepcopy(base)
                self.info[key] = value
                self.runs.clear()
                with self.assertRaises(RuntimeError):
                    capture.ManagerCapture(self.root).capture()
                self.assertFalse(any(command[0] == str(self.adb) for command, _ in self.runs))
        self.read.assert_not_called()

    def test_runtime_avd_identity_and_stale_records_are_rejected(self):
        original = self.record.read_text()
        for content, stale in ((original.replace("mas_0", "mas_1"), False), (original, True)):
            with self.subTest(stale=stale):
                self.record.write_text(content)
                if stale:
                    os.utime(self.record, ns=(1000000000, 1000000000))
                self.runs.clear()
                with self.assertRaises((OSError, RuntimeError)):
                    capture.ManagerCapture(self.root).capture()
                self.assertFalse(any(command[0] == str(self.adb) for command, _ in self.runs))

    def test_display_title_is_not_id_and_runtime_evidence_excludes_secrets(self):
        original = self.record.read_text()
        self.record.write_text(original.replace("avd.name=mas_0", "avd.name=macOS B服回归") +
                               "grpc.token=synthetic-secret-never-export\nunknown.key=synthetic-private-value\n" +
                               "port.adb=20001\nemulator.version=36.3.1\nemulator.build=synthetic\n")
        self.info["title"] = "macOS B服回归"
        self.info["screencap"]["shm_ready"] = True
        output = self.directory / "display-title"
        capture.save_capture(self.root, 0, output, reuse_stream=True)
        raw = (output / "evidence.json").read_text()
        evidence = json.loads(raw)
        record = evidence["managerObservations"][0]["runtimeRecord"]
        self.assertEqual(record["properties"]["avd.id"], "mas_0")
        self.assertEqual(record["properties"]["avd.name"], "macOS B服回归")
        self.assertEqual(record["sha256"], hashlib.sha256(self.record.read_bytes()).hexdigest())
        self.assertEqual(set(record["properties"]), capture.RUNTIME_PROPERTIES)
        for forbidden in ("grpc.token", "synthetic-secret-never-export", "unknown.key", "synthetic-private-value"):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(self.emu_commands(), [])

    def test_matching_display_title_cannot_substitute_for_wrong_avd_id(self):
        self.record.write_text(self.record.read_text().replace("avd.id=mas_0", "avd.id=mas_1"))
        with self.assertRaisesRegex(RuntimeError, "AVD/id/port"):
            capture.ManagerCapture(self.root).capture()
        self.assertFalse(any(command[0] == str(self.adb) for command, _ in self.runs))

    def test_real_macos_registration_path_rule(self):
        environment = {"HOME": "/Users/azuki", "ANDROID_EMULATOR_HOME": "/ignored/emulator-home",
                       "ANDROID_PREFS_ROOT": "/ignored/preferences", "TMPDIR": "/ignored/tmp"}
        self.assertEqual(capture.discovery_directory(environment) / "pid_54065.ini",
                         Path("/Users/azuki/Library/Caches/TemporaryItems/avd/running/pid_54065.ini"))
        self.qemu["discoveryEnvironment"].update(environment)
        self.qemu["discoveryEnvironment"]["HOME"] = str(self.directory / "home")
        capture.ManagerCapture(self.root).capture()
        self.assertEqual([command[-1] for command in self.emu_commands()], ["start", "stop"])

    def test_missing_home_uses_only_configdirs_user_directory_fallback(self):
        home = self.directory / "explicit-emulator-home"
        self.qemu["discoveryEnvironment"] = {"ANDROID_EMULATOR_HOME": str(home)}
        fallback = home / "avd/running/pid_4243.ini"
        fallback.parent.mkdir(parents=True)
        self.record.rename(fallback)
        collector = capture.ManagerCapture(self.root)
        collector.capture()
        self.assertEqual(collector.observations[0]["runtimeRecord"]["path"], str(fallback))

    def test_present_home_never_searches_legacy_registration_path(self):
        legacy = self.directory / "home/.android/avd/running/pid_4243.ini"
        legacy.parent.mkdir(parents=True)
        self.record.rename(legacy)
        with self.assertRaises(FileNotFoundError):
            capture.ManagerCapture(self.root).capture()
        self.assertFalse(any(command[0] == str(self.adb) for command, _ in self.runs))
        self.read.assert_not_called()

    def test_foreign_or_unready_private_server_rejected_before_manager_cli(self):
        for foreign in (True, False):
            with self.subTest(foreign=foreign):
                self.server["executable"] = "/foreign/adb" if foreign else str(self.adb)
                with patch.object(capture.frames.runtime, "adb_server_ready", return_value=foreign):
                    with self.assertRaisesRegex(RuntimeError, "private ADB"):
                        capture.ManagerCapture(self.root).capture()
                self.assertEqual(self.runs, [])

    def test_inventory_hash_mismatch_rejected_before_any_command(self):
        self.cli.write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "SHA-256"):
            capture.ManagerCapture(self.root)
        self.assertEqual(self.runs, [])

    def test_existing_output_and_sdk_output_are_preserved_without_guest_commands(self):
        existing = self.directory / "existing"
        existing.mkdir()
        keep = existing / "frame.png"
        keep.write_bytes(b"keep")
        link = self.directory / "link"
        link.symlink_to(existing)
        for output in (existing, link, self.root / "sdk/capture"):
            with self.subTest(output=output):
                with self.assertRaises((OSError, RuntimeError)):
                    capture.save_capture(self.root, 0, output)
        self.assertEqual(keep.read_bytes(), b"keep")
        self.assertEqual(self.runs, [])


if __name__ == "__main__":
    unittest.main()
