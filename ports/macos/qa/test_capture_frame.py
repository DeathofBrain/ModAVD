"""Synthetic screenshot tests; no ADB, emulator, guest or existing SHM access."""
import ctypes
import hashlib
import importlib.util
import json
import mmap
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import zlib

SOURCE = Path(__file__).with_name("capture_frame.py")
SPEC = importlib.util.spec_from_file_location("modavd_capture_frame", SOURCE)
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="modavd-capture-test-")
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "runtime"
        self.root.mkdir()
        (self.root / "sdk").mkdir()
        self.instance = Mock(root=self.root, sdk=self.root / "sdk")
        self.instance.owned_device.return_value = "emulator-20060"
        self.instance.adb.return_value = subprocess.CompletedProcess([], 0, "videmulator20060\nOK\n", "")
        for name, value in {
            "runtime.json": {"schemaVersion": 1, "id": "a" * 32},
            "adb-server.json": {"pid": 4242, "started": "synthetic server"},
            "emulator.json": {"pid": 4243, "started": "synthetic emulator"},
        }.items():
            (self.root / name).write_text(json.dumps(value))
        self.pixels = bytes([1, 2, 3, 4, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120])
        self.region = struct.pack("<IIIIQ", 2, 2, 60, 7, 123456) + self.pixels

    def tearDown(self):
        self.temporary.cleanup()

    def test_png_preserves_channel_order_alpha_rows_and_checksums(self):
        frame, pixels = capture.copy_frame(self.region)
        png = capture.png_bgra(frame["width"], frame["height"], pixels)
        self.assertEqual(frame["payloadSha256"], hashlib.sha256(self.pixels).hexdigest())
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        chunks = {}
        offset = 8
        while offset < len(png):
            count = struct.unpack_from(">I", png, offset)[0]
            kind = png[offset + 4:offset + 8]
            data = png[offset + 8:offset + 8 + count]
            crc = struct.unpack_from(">I", png, offset + 8 + count)[0]
            self.assertEqual(crc, zlib.crc32(kind + data) & 0xffffffff)
            chunks[kind] = data
            offset += count + 12
        self.assertEqual(struct.unpack(">IIBBBBB", chunks[b"IHDR"]), (2, 2, 8, 6, 0, 0, 0))
        self.assertEqual(zlib.decompress(chunks[b"IDAT"]),
                         bytes([0, 3, 2, 1, 4, 30, 20, 10, 40, 0, 70, 60, 50, 80, 110, 100, 90, 120]))
        self.assertEqual(chunks[b"IEND"], b"")

    def test_frame_boundary_and_publication_checks(self):
        cases = [
            (b"x" * 23, "mapping size"),
            (struct.pack("<IIIIQ", 0, 2, 60, 7, 1), "dimensions"),
            (struct.pack("<IIIIQ", 2, 2, 1001, 7, 1), "dimensions or fps"),
            (struct.pack("<IIIIQ", 8192, 8192, 60, 7, 1), "64 MiB"),
            (struct.pack("<IIIIQ", 2, 2, 60, 7, 1) + b"short", "truncated"),
            (struct.pack("<IIIIQ", 2, 2, 60, 0, 0) + self.pixels, "no published frame"),
        ]
        for region, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(RuntimeError, error):
                capture.copy_frame(region, attempts=1)
        with self.assertRaisesRegex(RuntimeError, "unexpected POSIX SHM"):
            capture.read_shm("SHM_videmulator20060")
        with self.assertRaisesRegex(RuntimeError, "payload length"):
            capture.png_bgra(2, 2, b"short")

    def test_changed_header_retries_and_returns_the_later_pixels(self):
        first = struct.pack("<IIIIQ", 1, 1, 60, 7, 1)
        second = struct.pack("<IIIIQ", 1, 1, 60, 8, 2)

        class ChangingRegion:
            calls = 0
            headers = iter((first, second, second, second))

            def __len__(self):
                return 28

            def __getitem__(self, key):
                if key.start is None:
                    self.calls += 1
                    return next(self.headers)
                return b"old!" if self.calls == 1 else b"new!"

        with patch.object(capture.time, "sleep"):
            frame, pixels = capture.copy_frame(ChangingRegion(), attempts=2)
        self.assertEqual((frame["frameNumber"], frame["copyAttempts"], pixels), (8, 2, b"new!"))

    def test_reads_only_a_self_created_posix_mapping(self):
        # A random exclusive name cannot adopt the running emulator's SHM.
        number = 10000 + int.from_bytes(os.urandom(4), "little") % 55000
        name = f"videmulator{number}"
        library = ctypes.CDLL(None, use_errno=True)
        library.shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int]
        library.shm_open.restype = ctypes.c_int
        library.shm_unlink.argtypes = [ctypes.c_char_p]
        fd = library.shm_open(name.encode(), os.O_CREAT | os.O_EXCL | os.O_RDWR, ctypes.c_uint(0o600))
        self.assertGreaterEqual(fd, 0, ctypes.get_errno())
        try:
            os.ftruncate(fd, len(self.region))
            with mmap.mmap(fd, len(self.region), access=mmap.ACCESS_WRITE) as region:
                region[:] = self.region
                with patch.object(capture.runtime, "CONSOLE_PORT", number):
                    frame, pixels = capture.read_shm(name)
            self.assertEqual((frame["frameNumber"], pixels), (7, self.pixels))
        finally:
            os.close(fd)
            library.shm_unlink(name.encode())

    def test_capture_always_stops_stream_after_read_or_start_response_failure(self):
        for response, read_error in (("videmulator20060\nOK\n", True), ("foreign_name\nOK\n", False)):
            with self.subTest(response=response):
                self.instance.adb.reset_mock()
                self.instance.adb.return_value.stdout = response
                with patch.object(capture.runtime, "same_process", return_value=True), \
                        patch.object(capture.runtime, "adb_server_ready", return_value=True), \
                        patch.object(capture, "read_shm", side_effect=RuntimeError("synthetic read failure")) as read:
                    with self.assertRaises(RuntimeError):
                        capture.FrameCapture(self.instance).capture()
                self.assertEqual(self.instance.adb.call_count, 2)
                self.assertEqual(self.instance.adb.call_args.args[-1], "stop")
                self.assertEqual(read.called, read_error)

    def test_foreign_or_unready_server_never_sends_guest_commands(self):
        for live, ready in ((False, True), (True, False)):
            with self.subTest(live=live, ready=ready):
                self.instance.reset_mock()
                with patch.object(capture.runtime, "same_process", return_value=live), \
                        patch.object(capture.runtime, "adb_server_ready", return_value=ready):
                    with self.assertRaisesRegex(RuntimeError, "no ADB request sent"):
                        capture.FrameCapture(self.instance).capture()
                self.instance.owned_device.assert_not_called()
                self.instance.adb.assert_not_called()

    def test_partial_start_failure_still_attempts_stop(self):
        self.instance.adb.side_effect = [subprocess.CalledProcessError(1, ["synthetic start"]),
                                         subprocess.CompletedProcess([], 0, "OK\n", "")]
        with patch.object(capture.runtime, "same_process", return_value=True), \
                patch.object(capture.runtime, "adb_server_ready", return_value=True):
            with self.assertRaises(subprocess.CalledProcessError):
                capture.FrameCapture(self.instance).capture()
        self.assertEqual(self.instance.adb.call_count, 2)
        self.assertEqual(self.instance.adb.call_args.args[-1], "stop")

    def test_changed_process_identity_refuses_cleanup_of_replacement(self):
        def replace_emulator(_):
            (self.root / "emulator.json").write_text(json.dumps({"pid": 9999, "started": "replacement"}))
            return capture.copy_frame(self.region)

        with patch.object(capture.runtime, "same_process", return_value=True), \
                patch.object(capture.runtime, "adb_server_ready", return_value=True), \
                patch.object(capture, "read_shm", side_effect=replace_emulator):
            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                capture.FrameCapture(self.instance).capture()
        self.assertEqual(self.instance.adb.call_count, 1)
        self.assertEqual(self.instance.adb.call_args.args[-1], "start")

    def test_output_refuses_existing_paths_symlinks_and_sdk(self):
        existing = self.directory / "existing"
        existing.mkdir()
        (existing / "frame.png").write_bytes(b"keep")
        file = self.directory / "file"
        file.write_bytes(b"keep file")
        link = self.directory / "link"
        link.symlink_to(existing)
        for output in (existing, file, link, self.instance.sdk / "captures"):
            with self.subTest(output=output), patch.object(capture.FrameCapture, "capture") as collect:
                with self.assertRaises((OSError, RuntimeError)):
                    capture.save_capture(self.instance, output)
                collect.assert_not_called()
        self.assertEqual((existing / "frame.png").read_bytes(), b"keep")
        self.assertEqual(file.read_bytes(), b"keep file")
        with capture.new_output_directory(self.directory / "new") as (directory, fd):
            (directory / "frame.png").symlink_to(file)
            with self.assertRaises(FileExistsError):
                capture.write_exclusive(fd, "frame.png", b"replace")
            with self.assertRaisesRegex(RuntimeError, "filename"):
                capture.write_exclusive(fd, "../file", b"replace")
        self.assertEqual(file.read_bytes(), b"keep file")

    def test_synthetic_capture_saves_png_and_traceable_evidence(self):
        output = self.directory / "saved"
        with patch.object(capture.runtime, "same_process", return_value=True), \
                patch.object(capture.runtime, "adb_server_ready", return_value=True), \
                patch.object(capture, "read_shm", side_effect=lambda _: capture.copy_frame(self.region)):
            report = capture.save_capture(self.instance, output)
        saved = json.loads((output / "evidence.json").read_text())
        self.assertEqual(saved["frame"]["frameNumber"], 7)
        self.assertEqual(saved["frame"]["payloadSha256"], hashlib.sha256(self.pixels).hexdigest())
        self.assertEqual(saved["image"]["sha256"], hashlib.sha256((output / "frame.png").read_bytes()).hexdigest())
        self.assertTrue(saved["noSeqlock"])
        self.assertIn("do not prove", saved["consistencyLimitation"])
        self.assertEqual(saved["processIdentity"]["server"]["pid"], 4242)
        self.assertEqual(set(p.name for p in output.iterdir()), {"frame.png", "evidence.json"})
        self.assertEqual(self.instance.adb.call_args.args[-1], "stop")
        self.assertEqual(report["png"], str(output.resolve() / "frame.png"))


if __name__ == "__main__":
    unittest.main()
