"""Behavioral tests use only temporary packages, fake HTTP and fake ADB peers."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import zipfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import components
import diagnostics
import adb_readonly
import maintenance


def archive(entries=None, revision="1.0"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr("platform-tools/source.properties", "Pkg.Revision=" + revision + "\n")
        member = zipfile.ZipInfo("platform-tools/adb")
        member.external_attr = (stat.S_IFREG | 0o755) << 16
        bundle.writestr(member, b"fixture-adb")
        for name, value in entries or []:
            bundle.writestr(name, value)
    return output.getvalue()


class Response(io.BytesIO):
    def __init__(self, body, url, status=200, headers=None):
        super().__init__(body)
        self.url, self.status, self.headers = url, status, headers or {}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root, self.resources = self.base / "package", self.base / "resources"
        self.root.mkdir()
        (self.root / ".modavd-macos-package.json").write_text("{}")
        (self.root / "mas-avd.json").write_text(json.dumps({"components": {"emulator": "37.2.10"}, "instances": {"0": {"memoryMb": 3072}}}))
        (self.resources / "licenses").mkdir(parents=True)
        self.license_text = b"Fixture license text; no production terms.\n"
        (self.resources / "licenses/fixture-license.txt").write_bytes(self.license_text)
        self.body = archive()
        self.config = {"package": "platform-tools", "revision": "1.0", "url": "https://dl.google.com/android/repository/fixture.zip", "size": len(self.body),
                       "sha1": hashlib.sha1(self.body).hexdigest(), "sha256": hashlib.sha256(self.body).hexdigest(), "license": "fixture-license",
                       "licenseTextSha256": hashlib.sha256(self.license_text).hexdigest(), "archiveRoot": "platform-tools", "destination": "platform-tools", "expandedBudgetBytes": 10000}
        self.save_lock()
        self.component = components.Components(self.root, self.resources)
        self.space = mock.patch.object(components, "available_space")
        self.stopped = mock.patch.object(components, "require_stopped")
        self.processes = mock.patch.object(components, "process_paths", return_value=[])
        self.space.start()
        self.stopped.start()
        self.processes.start()
        self.addCleanup(self.space.stop)
        self.addCleanup(self.stopped.stop)
        self.addCleanup(self.processes.stop)
        self.addCleanup(self.temporary.cleanup)

    def save_lock(self):
        (self.resources / "components.lock.json").write_text(json.dumps({"components": {"platform-tools": self.config}}))

    def accept(self, directory="provenance/runtime"):
        path = self.root / directory
        path.mkdir(parents=True, exist_ok=True)
        (path / "fixture-license.json").write_text(json.dumps({"id": "fixture-license", "sha256": self.config["licenseTextSha256"], "acceptedAt": "2026-10-31T00:00:00Z"}))

    def opener(self, request, **kwargs):
        return Response(self.body, request.full_url)

    def diagnostic(self, **kwargs):
        kwargs.setdefault("crash_directory", self.base / "no-crashes")
        kwargs.setdefault("crashpad_directories", [])
        return diagnostics.Diagnostics(self.root, **kwargs)

    def partial(self, mirror="googledownloads_cn"):
        return self.root / ".modavd-maintenance/downloads" / ("platform-tools-" + self.config["sha256"] + ".zip." + mirror + ".part")


class ComponentTests(Fixture):
    def test_unaccepted_license_blocks_before_any_writes_or_download(self):
        opener = mock.Mock()
        with self.assertRaisesRegex(RuntimeError, "licenses"):
            self.component.repair(opener=opener)
        opener.assert_not_called()
        self.assertFalse((self.root / ".modavd-maintenance").exists())

    def test_same_text_existing_acceptance_is_reused_without_rewriting(self):
        self.accept()
        path = self.root / "provenance/runtime/fixture-license.json"
        before = path.read_bytes()
        self.assertTrue(self.component.accepted("fixture-license"))
        self.component.repair(opener=self.opener)
        self.assertEqual(path.read_bytes(), before)

    def test_accept_requires_explicit_flag_and_exact_hash(self):
        for checksum, explicit in (("0" * 64, True), (self.config["licenseTextSha256"], False)):
            with self.assertRaises(RuntimeError):
                self.component.accept("fixture-license", checksum, explicit)
        self.component.accept("fixture-license", self.config["licenseTextSha256"], True)
        self.assertTrue(self.component.accepted("fixture-license"))
        self.assertTrue((self.root / ".modavd-maintenance/receipts/fixture-license.json").is_file())

    def test_changed_terms_cannot_reuse_receipt(self):
        self.accept()
        (self.resources / "licenses/fixture-license.txt").write_text("different terms")
        with self.assertRaisesRegex(RuntimeError, "哈希"):
            self.component.accepted("fixture-license")

    def test_install_then_damage_then_atomic_repair(self):
        self.accept()
        self.assertEqual(self.component.repair(opener=self.opener)[0]["state"], "verified")
        adb = self.root / "sdk/platform-tools/adb"
        self.assertEqual(adb.stat().st_mode & 0o777, 0o755)
        adb.write_text("damaged")
        self.assertEqual(self.component.status("platform-tools")["state"], "damaged")
        self.assertEqual(self.component.repair(opener=mock.Mock(side_effect=AssertionError("cached archive should be reused")))[0]["state"], "verified")
        self.assertEqual(adb.read_bytes(), b"fixture-adb")

    def test_extra_files_are_not_silently_trusted(self):
        self.accept()
        self.component.repair(opener=self.opener)
        (self.root / "sdk/platform-tools/extra").write_text("untrusted")
        self.assertEqual(self.component.status("platform-tools")["state"], "damaged")

    def test_repair_restores_original_manifest_modes_without_rewriting_it(self):
        self.accept()
        self.component.repair(opener=self.opener)
        tree = self.root / "sdk/platform-tools"
        records = {}
        for path in [tree] + list(tree.rglob("*")):
            record = {"mode": stat.S_IMODE(path.stat().st_mode), "type": "directory" if path.is_dir() else "file"}
            if path.is_file():
                record.update(sha256=components.digest(path), bytes=path.stat().st_size)
            records[path.relative_to(self.root).as_posix()] = record
        records["tools/untouched"] = {"type": "file", "sha256": "do-not-retrust"}
        manifest = self.root / "PACKAGE-MANIFEST.json"
        manifest.write_text(json.dumps({"files": records}))
        original_manifest = manifest.read_bytes()
        (tree / "adb").chmod(0o644)
        self.assertEqual(self.component.status("platform-tools")["state"], "damaged")
        self.component.repair(opener=self.opener)
        self.assertEqual((tree / "adb").stat().st_mode & 0o777, 0o755)
        self.assertEqual(tree.stat().st_mode & 0o777, 0o755)
        self.assertEqual(manifest.read_bytes(), original_manifest)

    def test_range_resume_and_identity_encoding(self):
        self.accept()
        part = self.partial()
        part.parent.mkdir(parents=True)
        part.write_bytes(self.body[:40])
        requests = []
        def opener(request, **kwargs):
            requests.append(request)
            self.assertEqual(request.get_header("Range"), "bytes=40-")
            self.assertEqual(request.get_header("Accept-encoding"), "identity")
            return Response(self.body[40:], request.full_url, 206, {"Content-Range": "bytes 40-%d/%d" % (len(self.body) - 1, len(self.body))})
        self.component.repair(opener=opener)
        self.assertEqual(len(requests), 1)

    def test_range_ignored_restarts_instead_of_appending(self):
        self.accept()
        part = self.partial()
        part.parent.mkdir(parents=True)
        part.write_bytes(self.body[:40])
        self.assertEqual(self.component.repair(opener=self.opener)[0]["state"], "verified")

    def test_bad_range_and_bad_hash_fall_back_in_order(self):
        self.accept()
        calls = []
        def opener(request, **kwargs):
            calls.append(request.full_url)
            if len(calls) == 1:
                return Response(self.body, request.full_url, 206, {"Content-Range": "bytes 9-99/100"})
            if len(calls) == 2:
                return Response(b"x" * len(self.body), request.full_url)
            return Response(self.body, request.full_url)
        self.component.repair(opener=opener)
        self.assertEqual([__import__("urllib.parse").parse.urlparse(u).hostname for u in calls], ["googledownloads.cn", "mirrors.cloud.tencent.com", "dl.google.com"])

    def test_short_download_preserves_separate_mirror_breakpoints(self):
        self.accept()
        with self.assertRaisesRegex(RuntimeError, "镜像均失败"):
            self.component.repair(opener=lambda request, **_: Response(self.body[:30], request.full_url))
        for name, _, _ in components.MIRRORS:
            self.assertEqual(self.partial(name).read_bytes(), self.body[:30])
        self.assertFalse((self.root / "sdk/platform-tools").exists())

    def test_foreign_redirect_and_compressed_response_never_publish(self):
        self.accept()
        for mode in ("redirect", "encoding"):
            def opener(request, **kwargs):
                return Response(self.body, "https://example.org/fixture.zip" if mode == "redirect" else request.full_url,
                                headers={"Content-Encoding": "gzip"} if mode == "encoding" else {})
            with self.assertRaises(RuntimeError):
                self.component.repair(opener=opener)
        self.assertFalse((self.root / "sdk/platform-tools").exists())

    def test_running_package_refuses_repair_without_stopping_it(self):
        self.accept()
        with mock.patch.object(components, "require_stopped", side_effect=RuntimeError("still running")), mock.patch.object(self.component, "download") as download:
            with self.assertRaisesRegex(RuntimeError, "still running"):
                self.component.repair()
        download.assert_not_called()

    def test_staging_failure_preserves_existing_component(self):
        self.accept()
        destination = self.root / "sdk/platform-tools"
        destination.mkdir(parents=True)
        (destination / "preserved").write_text("old data")
        with mock.patch.object(self.component, "extract", side_effect=RuntimeError("bad ZIP")):
            with self.assertRaises(RuntimeError):
                self.component.repair(opener=self.opener)
        self.assertEqual((destination / "preserved").read_text(), "old data")
        self.assertEqual(list((self.root / ".modavd-maintenance").glob("stage-*")), [])

    def test_atomic_publish_error_preserves_previous_component(self):
        self.accept()
        destination = self.root / "sdk/platform-tools"
        destination.mkdir(parents=True)
        (destination / "preserved").write_text("old data")
        with mock.patch.object(components, "atomic_publish", side_effect=OSError("exchange failed")):
            with self.assertRaises(OSError):
                self.component.repair(opener=self.opener)
        self.assertEqual((destination / "preserved").read_text(), "old data")

    def test_unsafe_archive_members_are_rejected(self):
        bad_entries = [[("../escape", b"bad")], [("platform-tools/../../escape", b"bad")], [("platform-tools\\escape", b"bad")], [("other-root/file", b"bad")], [("platform-tools/ADB", b"duplicate")]]
        for entries in bad_entries:
            path = self.base / "unsafe.zip"
            path.write_bytes(archive(entries))
            stage = self.base / ("stage" + str(len(list(self.base.glob("stage*")))))
            stage.mkdir()
            with self.assertRaises((RuntimeError, FileExistsError)):
                self.component.extract("platform-tools", path, stage)
        self.assertFalse((self.base / "escape").exists())

    def test_symlink_zip_and_revision_mismatch_rejected(self):
        for symlink in (True, False):
            path = self.base / "invalid.zip"
            if symlink:
                with zipfile.ZipFile(path, "w") as bundle:
                    member = zipfile.ZipInfo("platform-tools/link")
                    member.external_attr = (stat.S_IFLNK | 0o777) << 16
                    bundle.writestr(member, "/etc/passwd")
            else:
                path.write_bytes(archive(revision="9.9"))
            stage = self.base / ("stage-" + str(symlink))
            stage.mkdir()
            with self.assertRaises(RuntimeError):
                self.component.extract("platform-tools", path, stage)

    def test_destination_symlink_does_not_escape_package(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "sdk").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "符号链接"):
            self.component.status("platform-tools")

    def test_exclusive_maintenance_lock(self):
        with self.component.exclusive():
            with self.assertRaisesRegex(RuntimeError, "另一个"):
                with self.component.exclusive():
                    pass

    def test_production_lock_retains_official_runtime_versions_and_terms(self):
        lock = json.loads((HERE / "components.lock.json").read_text())
        runtime = json.loads((HERE.parent / "runtime/components.lock.json").read_text())
        for name, config in lock["components"].items():
            for key in ("url", "size", "sha1", "revision", "license", "licenseTextSha256", "destination", "archiveRoot"):
                self.assertEqual(config[key], runtime["components"][name][key])
            self.assertEqual(components.digest(HERE / "licenses" / (config["license"] + ".txt")), config["licenseTextSha256"])


class DiagnosticTests(Fixture):
    def setup_private(self):
        private = "CDK-PRIVATE-1234567890"
        (self.root / "mas-avd.json").write_text(json.dumps({"components": {"emulator": "37.2.10"}, "instances": {"0": {"memoryMb": 3072, "account": private, "token": private}}, "sdkRoot": "/Users/alice/SDK", "CDK": private}))
        avd = self.root / "avd/mas_0.avd"
        avd.mkdir(parents=True)
        (avd / "config.ini").write_text("hw.ramSize=3072\nhw.cpu.ncore=4\ngrpc.token=" + private + "\nimage.sysdir.1=/Users/alice/private\n")
        (avd / "userdata-qemu.img").write_bytes(private.encode())
        (self.root / "keys").mkdir()
        (self.root / "keys/adbkey").write_text(private)
        (self.root / "logs").mkdir()
        (self.root / "logs/emulator.log").write_text("HVF ready\naccount=Alice token=" + private + "\nCDK=" + private + "\nPath /Users/alice/private/file\nemail alice@example.com\nexternal 123.42.4.1\n")
        (self.root / "logs/account.log").write_text(private)
        (self.root / "logs/ignored.dmp").write_bytes(private.encode())
        return private

    def test_default_zip_excludes_secrets_images_unknown_config_and_keys(self):
        private = self.setup_private()
        output = self.base / "report.zip"
        tool = self.diagnostic(adb_factory=mock.Mock(side_effect=AssertionError("no live access")))
        tool.collect(output)
        with zipfile.ZipFile(output) as bundle:
            self.assertIn("logs/emulator-00.txt", bundle.namelist())
            text = "\n".join(bundle.read(n).decode() for n in bundle.namelist())
            for secret in (private, "Alice", "alice@example.com", "123.42.4.1", "/Users/alice", "grpc.token", "userdata-qemu.img", "adbkey"):
                self.assertNotIn(secret, text)
            summary = json.loads(bundle.read("summary.json"))
            self.assertEqual(summary["configuration"]["instances"]["0"]["files"]["config.ini"], {"hw.ramSize": "3072", "hw.cpu.ncore": "4"})
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_optional_live_games_queries_only_whitelisted_package_names(self):
        class FakeAdb:
            def __init__(self, root, index):
                self.index = index
            def shell(self, command):
                return {"getprop sys.boot_completed": "1\n", "getprop ro.build.version.sdk": "34\n", "getprop ro.product.cpu.abi": "arm64-v8a\n",
                        "pm list packages -3": "package:com.hypergryph.arknights.bilibili\npackage:org.modavd.ime\npackage:bad;rm -rf /\n",
                        "dumpsys package com.hypergryph.arknights.bilibili": "  versionName=2.7.81\n  account=SECRET\n"}[command]
        tool = self.diagnostic(adb_factory=FakeAdb)
        text = tool.report(live=True)
        self.assertIn("明日方舟（B服） 2.7.81", text)
        self.assertNotIn("SECRET", text)

    def test_screenshot_requires_explicit_live_and_is_separate_entry(self):
        with self.assertRaisesRegex(RuntimeError, "--live"):
            self.diagnostic().collect(self.base / "bad.zip", screenshots=True)
        class FakeAdb:
            def __init__(self, *args):
                pass
            def shell(self, command):
                return "1" if command == "getprop sys.boot_completed" else ""
            def screenshot(self):
                return b"fixture-pixels"
        output = self.base / "screenshot.zip"
        self.diagnostic(adb_factory=FakeAdb).collect(output, live=True, screenshots=True)
        with zipfile.ZipFile(output) as bundle:
            self.assertEqual(bundle.read("live/instance-0.png"), b"fixture-pixels")
            self.assertIn("未经像素脱敏", bundle.read("README-privacy.txt").decode())

    def test_crashes_only_recent_package_owned_text_summaries(self):
        crash = self.base / "crashes"
        crash.mkdir()
        def make(name, path, old=False):
            p = crash / name
            p.write_text(json.dumps({"bug_type": "309", "userId": "SECRET-ACCOUNT"}) + "\n" + json.dumps({"procPath": str(path), "procName": "emulator", "exception": {"type": "EXC_BAD_ACCESS", "account": "SECRET-ACCOUNT"}, "memory": "SECRET-MEMORY"}))
            if old:
                os.utime(p, (time.time() - 9 * 86400,) * 2)
        make("emulator_2026.ips", self.root / "sdk/emulator/emulator")
        make("emulator_foreign.ips", self.base / "foreign/emulator")
        make("emulator_old.ips", self.root / "sdk/emulator/emulator", True)
        (crash / "emulator_current.dmp").write_bytes(b"SECRET-MEMORY")
        result = self.diagnostic(crash_directory=crash).crashes()
        self.assertEqual(len(result), 1)
        value = json.loads(next(iter(result.values())))
        self.assertEqual(value["exception"]["type"], "EXC_BAD_ACCESS")
        self.assertNotIn("SECRET", str(result))
        self.assertNotIn(str(self.root), str(result))

    def test_foreign_or_changed_live_instance_does_not_abort_other_collection(self):
        tool = self.diagnostic(adb_factory=mock.Mock(side_effect=RuntimeError("foreign")))
        output = self.base / "offline.zip"
        tool.collect(output, live=True)
        with zipfile.ZipFile(output) as bundle:
            self.assertIn("unavailable", json.loads(bundle.read("summary.json"))["live"]["0"]["status"])

    def test_crashpad_uses_only_fixed_mas_database_metadata_without_opening_dump(self):
        base = self.base / "android-fixture"
        pending = base / "emu-crash-37.2.10-mas.db/pending"
        pending.mkdir(parents=True)
        dump = pending / "00000000-0000-0000-0000-000000000001.dmp"
        dump.write_bytes(b"SECRET-ACCOUNT-IN-MEMORY")
        old = pending / "00000000-0000-0000-0000-000000000002.dmp"
        old.write_bytes(b"old")
        os.utime(old, (time.time() - 8 * 86400,) * 2)
        foreign = base / "emu-crash-37.2.10.db/pending"
        foreign.mkdir(parents=True)
        (foreign / dump.name).write_bytes(b"official database must not be collected")
        tool = self.diagnostic(crashpad_directories=[base])
        with mock.patch.object(Path, "open", side_effect=AssertionError("no dump bytes may be read")):
            inventory = tool.crashpad_inventory()
        self.assertEqual(len(inventory["records"]), 1)
        self.assertEqual(inventory["records"][0]["bytes"], dump.stat().st_size)
        self.assertNotIn(dump.name, str(inventory))
        self.assertNotIn("SECRET", str(inventory))

    def test_problem_zip_never_overwrites_and_rejects_sdk_output(self):
        tool = self.diagnostic()
        output = self.base / "exists.zip"
        output.write_bytes(b"preserve")
        with self.assertRaises(RuntimeError):
            tool.collect(output)
        self.assertEqual(output.read_bytes(), b"preserve")
        with self.assertRaises(RuntimeError):
            tool.collect(self.root / "sdk/forbidden.zip")

    def test_symlink_logs_and_avd_are_not_followed(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "emulator.log").write_text("SECRET")
        (self.root / "logs").symlink_to(outside, target_is_directory=True)
        (self.root / "avd").symlink_to(outside, target_is_directory=True)
        output = self.base / "safe.zip"
        self.diagnostic().collect(output)
        with zipfile.ZipFile(output) as bundle:
            self.assertFalse(any(n.startswith("logs/") for n in bundle.namelist()))


class ReadOnlyAdbTests(unittest.TestCase):
    def test_5037_rejected_without_opening_a_socket_or_starting_adb(self):
        with mock.patch.object(adb_readonly.subprocess, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "5037"):
                adb_readonly.listening_process(5037)
        run.assert_not_called()

    def test_mutating_commands_cannot_enter_read_service(self):
        client = object.__new__(adb_readonly.ReadOnlyAdb)
        with mock.patch.object(client, "_read") as read:
            for command in ("reboot", "input tap 1 2", "pm install /tmp/x.apk", "dumpsys package foo; reboot", "settings put global x 1"):
                with self.assertRaises(RuntimeError):
                    client.shell(command)
        read.assert_not_called()

    def test_wire_protocol_uses_only_selected_transport_and_read_service(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        requests = []
        def server():
            try:
                connection, _ = listener.accept()
                with connection:
                    for _ in range(2):
                        length = int(adb_readonly.receive_exact(connection, 4), 16)
                        requests.append(adb_readonly.receive_exact(connection, length).decode())
                        connection.sendall(b"OKAY")
                    connection.sendall(b"34\n")
            finally:
                listener.close()
        thread = threading.Thread(target=server, daemon=True)
        thread.start()
        client = object.__new__(adb_readonly.ReadOnlyAdb)
        client.port, client.serial = listener.getsockname()[1], "emulator-20000"
        with mock.patch.object(client, "owned") as owned:
            self.assertEqual(client.shell("getprop ro.build.version.sdk"), "34\n")
            self.assertEqual(owned.call_count, 2)
        thread.join(timeout=2)
        self.assertEqual(requests, ["host:transport:emulator-20000", "shell:getprop ro.build.version.sdk"])

    def test_recheck_failure_discards_response(self):
        client = object.__new__(adb_readonly.ReadOnlyAdb)
        client.port, client.serial = 20050, "emulator-20000"
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.recv.side_effect = [b"OKAY", b"OKAY", b"1", b""]
        with mock.patch.object(client, "owned", side_effect=[None, RuntimeError("changed")]), mock.patch.object(adb_readonly.socket, "create_connection", return_value=fake):
            with self.assertRaisesRegex(RuntimeError, "changed"):
                client.shell("getprop sys.boot_completed")

    def test_screenshot_signature_checked(self):
        client = object.__new__(adb_readonly.ReadOnlyAdb)
        with mock.patch.object(client, "_read", return_value=b"not png"):
            with self.assertRaisesRegex(RuntimeError, "PNG"):
                client.screenshot()

    def test_clipboard_is_explicit(self):
        with mock.patch.object(maintenance, "Diagnostics") as factory, mock.patch.object(maintenance, "copy_to_clipboard") as clipboard, contextlib.redirect_stdout(io.StringIO()):
            factory.return_value.report.return_value = "safe report\n"
            maintenance.main(["--root", "/fixture", "report"])
            clipboard.assert_not_called()
            maintenance.main(["--root", "/fixture", "report", "--clipboard"])
            clipboard.assert_called_once_with("safe report\n")


if __name__ == "__main__":
    unittest.main()
