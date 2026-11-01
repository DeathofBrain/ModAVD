"""Synthetic tests only: no SDK downloads, ADB server, emulator or real device."""
import ctypes
import hashlib
import importlib.util
import io
import json
import mmap
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

SOURCE = Path(__file__).resolve().parents[1] / "runtime/runtime.py"
SPEC = importlib.util.spec_from_file_location("modavd_runtime", SOURCE)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="modavd-runtime-test-")
        self.root = Path(self.temporary.name) / "runtime"
        self.instance = runtime.Runtime(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_no_download_before_explicit_license_acceptance(self):
        with patch.object(runtime.urllib.request, "urlopen") as request:
            with self.assertRaisesRegex(RuntimeError, "许可证"):
                self.instance.install("platform-tools")
            request.assert_not_called()

    def test_nonempty_foreign_root_is_not_adopted(self):
        self.root.mkdir()
        (self.root / "personal.txt").write_text("keep")
        with self.assertRaisesRegex(RuntimeError, "非空"):
            self.instance.initialize()
        self.assertEqual((self.root / "personal.txt").read_text(), "keep")

    def test_low_space_does_not_prevent_shutdown_environment(self):
        self.instance.initialize()
        usage = shutil._ntuple_diskusage(100, 90, 10)
        with patch.object(runtime.shutil, "disk_usage", return_value=usage), \
                patch.dict(os.environ, {"ANDROID_EMU_VK_HIDE_REBAR": "1"}):
            with self.assertRaisesRegex(RuntimeError, "空间停线"):
                runtime.space(self.root)
            env = self.instance.env()
        self.assertEqual(env["ANDROID_ADB_SERVER_PORT"], "20050")
        self.assertEqual(env["ADB_LOCAL_TRANSPORT_MAX_PORT"], "5553")
        self.assertEqual(env["ADB_MDNS_AUTO_CONNECT"], "0")
        self.assertEqual(env["ANDROID_EMU_VK_HIDE_REBAR"], "0")
        self.assertEqual(env["ANDROID_AVD_HOME"], str(self.root.resolve() / "avd"))

    def test_stale_pid_never_sends_adb_stop(self):
        self.instance.initialize()
        runtime.write_json(self.root / "emulator.json", {"pid": -1, "started": "old"})
        with patch.object(self.instance, "adb") as adb:
            with self.assertRaisesRegex(RuntimeError, "拒绝访问设备"):
                self.instance.stop()
            adb.assert_not_called()

    def test_no_private_server_record_never_sends_kill_server(self):
        self.instance.initialize()
        with patch.object(self.instance, "adb") as adb:
            with self.assertRaisesRegex(RuntimeError, "拒绝接管其他服务"):
                self.instance.stop_server()
            adb.assert_not_called()

    def test_server_start_uses_supported_listener_and_waits_for_protocol(self):
        self.instance.initialize()
        (self.root / "keys/adbkey").write_text("synthetic key; never used")
        record = self.root / "adb-server.json"
        clock = [0.0]
        process = Mock(pid=4242)
        process.poll.return_value = None
        identity = {"pid": process.pid, "started": "synthetic start"}

        def ready():
            # A live process alone, including one alive beyond 0.3 seconds,
            # must never create the success receipt before a handshake.
            self.assertFalse(record.exists())
            return clock[0] >= 0.5

        def advance(seconds):
            clock[0] += seconds

        with patch.object(self.instance, "adb") as adb, \
                patch.object(runtime, "require_free_ports"), \
                patch.object(runtime.subprocess, "Popen", return_value=process) as spawn, \
                patch.object(runtime, "adb_server_ready", side_effect=ready), \
                patch.object(runtime, "process_record", return_value=identity), \
                patch.object(runtime.time, "monotonic", side_effect=lambda: clock[0]), \
                patch.object(runtime.time, "sleep", side_effect=advance):
            self.instance.ensure_server()
        args = spawn.call_args.args[0]
        self.assertEqual(args[args.index("-L") + 1], "tcp:20050")
        self.assertNotIn("tcp:127.0.0.1:20050", args)
        self.assertGreaterEqual(clock[0], 0.5)
        self.assertEqual(json.loads(record.read_text()), identity)
        adb.assert_called_once_with("version")
        process.terminate.assert_not_called()

    def test_server_exit_or_missing_handshake_never_records_success(self):
        self.instance.initialize()
        (self.root / "keys/adbkey").write_text("synthetic key; never used")
        record = self.root / "adb-server.json"
        for scenario in ("delayed_exit", "no_handshake"):
            with self.subTest(scenario=scenario):
                record.unlink(missing_ok=True)
                previous = {"pid": 1234, "started": "stale synthetic start"}
                if scenario == "no_handshake":
                    runtime.write_json(record, previous)
                before = record.read_bytes() if record.exists() else None
                clock = [0.0]
                process = Mock(pid=4242)
                process.poll.side_effect = lambda: 1 if scenario == "delayed_exit" and clock[0] >= 0.6 else None

                def advance(seconds):
                    clock[0] += seconds

                with patch.object(self.instance, "adb"), \
                        patch.object(runtime, "same_process", return_value=False), \
                        patch.object(runtime, "require_free_ports"), \
                        patch.object(runtime.subprocess, "Popen", return_value=process), \
                        patch.object(runtime, "adb_server_ready", return_value=False), \
                        patch.object(runtime, "process_record") as identity, \
                        patch.object(runtime.time, "monotonic", side_effect=lambda: clock[0]), \
                        patch.object(runtime.time, "sleep", side_effect=advance):
                    with self.assertRaisesRegex(RuntimeError, "协议未就绪"):
                        self.instance.ensure_server()
                self.assertEqual(record.read_bytes() if record.exists() else None, before)
                identity.assert_not_called()
                if scenario == "no_handshake":
                    process.terminate.assert_called_once_with()
                    process.wait.assert_called_once_with(timeout=5)
                else:
                    self.assertGreaterEqual(clock[0], 0.6)
                    process.terminate.assert_not_called()

    def test_existing_owned_server_without_protocol_is_rejected(self):
        self.instance.initialize()
        record = self.root / "adb-server.json"
        runtime.write_json(record, {"pid": 4242, "started": "synthetic start"})
        before = record.read_bytes()
        with patch.object(runtime, "same_process", return_value=True), \
                patch.object(runtime, "adb_server_ready", return_value=False), \
                patch.object(runtime, "require_free_ports") as ports, \
                patch.object(self.instance, "adb") as adb, \
                patch.object(runtime.subprocess, "Popen") as spawn:
            with self.assertRaisesRegex(RuntimeError, "仍存在但协议未就绪"):
                self.instance.ensure_server()
        self.assertEqual(record.read_bytes(), before)
        ports.assert_not_called()
        adb.assert_not_called()
        spawn.assert_not_called()

    def install_fixture(self, member="fixture/tool", corrupt=False):
        self.instance.initialize()
        content = io.BytesIO()
        with zipfile.ZipFile(content, "w") as bundle:
            bundle.writestr("fixture/source.properties", "Pkg.Revision=1.0\n")
            info = zipfile.ZipInfo(member)
            info.external_attr = 0o100755 << 16
            bundle.writestr(info, b"synthetic binary\n")
        data = content.getvalue()
        config = {"package": "fixture", "revision": "1.0", "url": "https://dl.google.com/fixture.zip",
                  "size": len(data), "sha1": hashlib.sha1(data).hexdigest(), "archiveRoot": "fixture",
                  "destination": "fixture", "expandedBudgetBytes": 1024}
        (self.root / "downloads/fixture.zip").write_bytes(data + (b"bad" if corrupt else b""))
        with patch.dict(runtime.LOCK["components"], {"fixture": config}), \
                patch.object(self.instance, "require_license"), \
                patch.object(runtime.urllib.request, "urlopen") as request:
            self.instance.install("fixture")
            request.assert_not_called()

    def test_fixed_archive_install_preserves_executable_and_receipt(self):
        self.install_fixture()
        tool = self.root / "sdk/fixture/tool"
        self.assertTrue(tool.stat().st_mode & 0o111)
        receipt = json.loads((self.root / "receipts/fixture.json").read_text())
        self.assertEqual(receipt["files"]["tool"], hashlib.sha256(b"synthetic binary\n").hexdigest())
        self.assertFalse((self.root / "downloads/fixture.zip").exists())

    def test_archive_checksum_failure_does_not_install(self):
        with self.assertRaisesRegex(RuntimeError, "SHA-1"):
            self.install_fixture(corrupt=True)
        self.assertFalse((self.root / "sdk/fixture").exists())

    def test_archive_traversal_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "不安全路径"):
            self.install_fixture(member="fixture/../../outside")
        self.assertFalse((self.root / "outside").exists())
        self.assertFalse((self.root / "sdk/fixture").exists())


@unittest.skipUnless(os.uname().sysname == "Darwin", "macOS POSIX SHM integration test")
class ShmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="modavd-shm-test-")
        cls.probe = Path(cls.temporary.name) / "shm-probe"
        subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror",
                        str(Path(__file__).with_name("shm-probe.cpp")), "-o", str(cls.probe)], check=True)
        cls.libc = ctypes.CDLL(None, use_errno=True)
        # Darwin shm_open declares mode as a variadic argument. arm64 puts it
        # on the stack, unlike the two fixed arguments passed in registers.
        cls.libc.shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int]
        cls.libc.shm_open.restype = ctypes.c_int
        cls.libc.shm_unlink.argtypes = [ctypes.c_char_p]

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def probe_fixture(self, header, pixels=b"\x10\x20\x30\xff" * 4, *extra):
        name = f"videmulator{os.getpid()}"
        fd = self.libc.shm_open(name.encode(), os.O_CREAT | os.O_EXCL | os.O_RDWR, ctypes.c_uint(0o600))
        self.assertGreaterEqual(fd, 0, ctypes.get_errno())
        try:
            data = header + pixels
            os.ftruncate(fd, len(data))
            with mmap.mmap(fd, len(data)) as memory:
                memory[:] = data
                return subprocess.run([str(self.probe), "--name", name, "--samples", "2",
                                       "--interval-ms", "0", *extra], capture_output=True, text=True)
        finally:
            os.close(fd)
            self.libc.shm_unlink(name.encode())

    def test_reads_exact_posix_name_24_byte_header_and_bgra_payload(self):
        result = self.probe_fixture(struct.pack("<IIIIQ", 2, 2, 60, 7, 123456))
        self.assertEqual(result.returncode, 0, result.stderr)
        record = json.loads(result.stdout)
        self.assertEqual(record["headerBytes"], 24)
        self.assertEqual(record["pixelFormat"], "BGRA")
        self.assertEqual(record["samples"][0]["payloadBytes"], 16)
        self.assertEqual(record["samples"][0]["timestampUs"], 123456)
        self.assertFalse(record["frameCounterChanged"])

    def test_truncated_frame_is_rejected_without_reading_outside_mapping(self):
        result = self.probe_fixture(struct.pack("<IIIIQ", 2000, 2000, 60, 7, 123456))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("truncated BGRA", result.stderr)

    def test_invalid_dimensions_are_rejected(self):
        result = self.probe_fixture(struct.pack("<IIIIQ", 0, 2, 60, 7, 123456))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dimensions", result.stderr)

    def test_unpublished_frame_is_rejected(self):
        result = self.probe_fixture(struct.pack("<IIIIQ", 2, 2, 60, 0, 0))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not been published", result.stderr)

    def test_static_screen_only_fails_when_progress_is_requested(self):
        result = self.probe_fixture(struct.pack("<IIIIQ", 2, 2, 60, 7, 123456),
                                    b"\x10\x20\x30\xff" * 4, "--require-progress")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no frame progress", result.stderr)

    def test_windows_prefix_is_rejected(self):
        result = subprocess.run([str(self.probe), "--name", "SHM_videmulator20060"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without SHM_", result.stderr)


if __name__ == "__main__":
    unittest.main()
