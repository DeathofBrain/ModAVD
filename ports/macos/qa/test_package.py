"""Synthetic packaging checks: no downloads, real SDK, emulator or devices."""

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SPEC = importlib.util.spec_from_file_location("modavd_package", Path(__file__).resolve().parents[1] / "package.py")
package = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(package)


class PackageTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="modavd-package-test-")
        self.root = Path(self.temporary.name).resolve()
        self.emulator = self.root / "emulator"
        self.manager = self.root / "manager"
        self.runtime = self.root / "runtime"
        self.output = self.root / "output/ModAVD"
        self.maintenance = self.root / "maintenance-source"
        self.maintenance.mkdir()
        (self.maintenance / "maintenance.py").write_text(
            'import json,sys\nprint(json.dumps(sys.argv[1:],ensure_ascii=False))\n')
        for name in package.MAINTENANCE_FILES:
            if name != "maintenance.py":
                resource = self.maintenance / name
                resource.parent.mkdir(parents=True, exist_ok=True)
                resource.write_text("Synthetic maintenance resources\n")
        (self.maintenance / "test_development.py").write_text("development tests must not ship\n")
        cache = self.maintenance / "__pycache__"
        cache.mkdir()
        (cache / "development.pyc").write_bytes(b"development cache, must not ship")
        self.ime = self.root / "fixture.apk"
        self.ime.write_bytes(b"synthetic IME; never a real APK")
        self.ime_config = {**package.IME, "size": self.ime.stat().st_size, "sha256": package.digest(self.ime)}
        self.launcher = self.root / "launcher.apk"
        with zipfile.ZipFile(self.launcher, "w") as apk:
            apk.writestr("classes.dex", b"synthetic DEX; never executable")
            apk.writestr(package.LAUNCHER["embeddedLicenseMember"],
                         (package.ROOT / "ports/macos/licenses" / package.LAUNCHER["embeddedLicenseFile"]).read_bytes())
        self.launcher_config = {**package.LAUNCHER, "size": self.launcher.stat().st_size, "sha256": package.digest(self.launcher)}
        self.args = argparse.Namespace(emulator=self.emulator, manager=self.manager, runtime_root=self.runtime,
                                       output=self.output, ime_apk=self.ime, aosp_source=self.root / "absent-aosp",
                                       manager_source=self.root / "absent-manager-source", launcher_apk=self.launcher,
                                       without_launcher=False)

        for name in ("emulator", "qemu/darwin-aarch64/qemu-system-aarch64", "qemu/darwin-aarch64/qemu-system-aarch64-headless"):
            self.binary(self.emulator / name)
        (self.emulator / "source.properties").write_text("Pkg.BuildId=mas-36\nPkg.Revision=37.2.10\n")
        (self.emulator / "NOTICE.txt").write_text("Synthetic notice\n")
        (self.emulator / "NOTICE.csv").write_text("Synthetic notice index\n")
        library = self.emulator / "lib64/libfixture.1.dylib"
        library.parent.mkdir()
        library.write_bytes(b"synthetic library")
        (library.parent / "libfixture.dylib").symlink_to(library.name)
        for name in ("tools/modavd", "tools/modavd-manager", "tools/modavd-webview",
                     "ModAVD.app/Contents/MacOS/ModAVD", "ModAVD.app/Contents/MacOS/modavd-webview"):
            self.binary(self.manager / name)
        (self.manager / "LICENSE").write_text("Synthetic license\n")
        (self.manager / "NOTICE").write_text("Synthetic notice\n")
        package.write_json(self.manager / ".modavd-manager-build.json", {
            "upstream": "https://github.com/ModAVD/ModAVDManager.git",
            "revision": "21c2a3ffa22eb37a5e39e61b1b53f2258d0f3e7a",
            "patch_sha256": package.digest(package.ROOT / "incoming/macos-manager/0001-darwin-arm64.patch"),
        })
        package.write_json(self.manager / "SHA256SUMS.json", self.hashes(self.manager))
        lock = json.loads((package.ROOT / "ports/macos/runtime/components.lock.json").read_text())
        for name in ("platform-tools", "system-image"):
            config = lock["components"][name]
            destination = self.runtime / "sdk" / config["destination"]
            destination.mkdir(parents=True)
            if name == "platform-tools":
                self.binary(destination / "adb")
            else:
                for image in ("system.img", "vendor.img", "kernel-ranchu", "ramdisk.img"):
                    (destination / image).write_bytes(b"synthetic image")
            package.write_json(self.runtime / "receipts" / (name + ".json"), {
                "revision": config["revision"], "archiveSha1": config["sha1"],
                "archiveSha256": "synthetic-test-only", "files": self.hashes(destination),
            })
            package.write_json(self.runtime / "receipts" / (config["license"] + ".json"), {
                "id": config["license"], "sha256": config["licenseTextSha256"], "acceptedAt": "synthetic-test-only",
            })

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def binary(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(struct.pack("<IIIIIIII", 0xFEEDFACF, 0x0100000C, 0, 2, 0, 0, 0, 0) + b"free-page-reporting")
        path.chmod(0o755)

    @staticmethod
    def hashes(root):
        return {path.relative_to(root).as_posix(): package.digest(path) for path in root.rglob("*") if path.is_file()}

    def assemble(self):
        with patch.dict(package.IME, self.ime_config), patch.dict(package.LAUNCHER, self.launcher_config), \
                patch.object(package, "MAINTENANCE", self.maintenance), \
                patch.object(package.urllib.request, "urlopen") as request, \
                patch.object(package, "space", return_value=100 * 1024**3), contextlib.redirect_stdout(io.StringIO()):
            package.assemble(self.args)
            request.assert_not_called()

    def test_complete_assembly_preserves_links_modes_and_independent_files(self):
        self.assemble()
        manifest = package.verify(self.output)
        source = self.emulator / "lib64/libfixture.1.dylib"
        copied = self.output / "sdk/emulator/lib64/libfixture.1.dylib"
        self.assertNotEqual(source.stat().st_ino, copied.stat().st_ino)
        self.assertEqual(os.readlink(copied.with_name("libfixture.dylib")), "libfixture.1.dylib")
        self.assertEqual((self.output / "tools/modavd").stat().st_mode & 0o777, 0o755)
        source.write_bytes(b"later source change")
        self.assertEqual(copied.read_bytes(), b"synthetic library")
        self.assertEqual(manifest["validation"]["Arknights"], "not_run_by_assembler")
        self.assertEqual(manifest["launcher"]["version"], "0.2.12")
        self.assertEqual((self.output / "components" / package.LAUNCHER["filename"]).read_bytes(), self.launcher.read_bytes())
        self.assertTrue((self.output / "licenses" / package.LAUNCHER["licenseFile"]).is_file())
        self.assertFalse(list(self.output.parent.glob("*.zip")))
        package.verify(self.output)

    def test_aemu_patch_receipt_and_source_state_are_packaged(self):
        receipt = {"base_tree": "fixture-base", "overlay_tree": "fixture-aemu",
                   "patches": [{"path": "incoming/macos-aemu/0001-media-dispatcher-cleanup-declaration.patch",
                                "sha256": "fixture-only"}]}
        package.write_json(self.args.aosp_source / ".repo/modavd-aemu-overlays.json", receipt)
        with patch.object(package, "git_state", side_effect=lambda path: {"testPath": str(path)}):
            self.assemble()
        manifest = package.verify(self.output)
        self.assertEqual(json.loads((self.output / "provenance/core/modavd-aemu-overlays.json").read_text()), receipt)
        self.assertEqual(manifest["sourceSnapshots"]["aemu"]["testPath"],
                         str(self.args.aosp_source / "hardware/google/aemu"))
        self.assertIn("aemu", manifest["sourceCorrelation"])
        name = "incoming/macos-aemu/0001-media-dispatcher-cleanup-declaration.patch"
        self.assertEqual(manifest["patches"]["files"][name], package.digest(package.ROOT / name))

    def test_shipped_maintenance_launchers_handle_spaces_and_keep_payload_clean(self):
        self.output = self.root / "output/Mod AVD 中文"
        self.args.output = self.output
        self.assemble()
        self.assertFalse((self.output / "tools/maintenance/__pycache__").exists())
        self.assertFalse((self.output / "tools/maintenance/test_development.py").exists())
        for name, command in (("report-config", ["report"]), ("collect-info", ["collect"]),
                              ("fetch-components", ["components", "repair"]), ("modavd-maintenance", [])):
            launcher = self.output / "tools" / name
            arguments = json.loads(subprocess.check_output([str(launcher), "--help"], text=True))
            self.assertEqual(arguments[0], "--root")
            self.assertEqual(Path(arguments[1]).resolve(), self.output)
            self.assertEqual(arguments[2:], command + ["--help"])
        self.assertFalse((self.output / "tools/maintenance/__pycache__").exists())
        package.verify(self.output)

    def test_verify_ignores_mutable_user_state(self):
        self.assemble()
        avd = self.output / "avd"
        avd.mkdir()
        (avd / "outside").symlink_to("../../../../")
        logs = self.output / "logs"
        logs.mkdir()
        os.mkfifo(logs / "user-owned-pipe")
        (self.output / "mas-avd.json").write_text("user state")
        package.verify(self.output)

    def test_payload_corruption_and_unlisted_files_are_rejected(self):
        self.assemble()
        extra = self.output / "sdk/unlisted"
        extra.write_text("unexpected")
        with self.assertRaisesRegex(RuntimeError, "清单之外"):
            package.verify(self.output)
        extra.unlink()
        (self.output / "tools/modavd").write_bytes(b"corrupted")
        with self.assertRaisesRegex(RuntimeError, "不匹配清单"):
            package.verify(self.output)

    def test_existing_user_output_never_reaches_input_validation_or_download(self):
        self.output.mkdir(parents=True)
        (self.output / "personal.txt").write_text("preserve")
        with patch.object(package, "validate_inputs") as validate, patch.object(package, "fetch_ime") as download:
            with self.assertRaisesRegex(RuntimeError, "拒绝覆盖"):
                package.assemble(self.args)
            validate.assert_not_called()
            download.assert_not_called()
        self.assertEqual((self.output / "personal.txt").read_text(), "preserve")

    def test_atomic_publish_refuses_existing_empty_directory_and_symlink(self):
        source = self.root / "stage"
        source.mkdir()
        destination = self.root / "concurrent-output"
        destination.mkdir()
        with self.assertRaises(FileExistsError):
            package.rename_exclusive(source, destination)
        self.assertTrue(source.is_dir())
        destination.rmdir()
        destination.symlink_to(source.name)
        with self.assertRaises(FileExistsError):
            package.rename_exclusive(source, destination)
        self.assertTrue(destination.is_symlink())

    def test_external_input_symlink_fails_and_cleans_only_owned_stage(self):
        (self.emulator / "external").symlink_to("../../")
        with self.assertRaisesRegex(RuntimeError, "外部或绝对符号链接"):
            self.assemble()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.output.parent.glob(".ModAVD.assembling-*")))
        self.assertTrue(self.emulator.is_dir())

    def test_missing_license_receipt_prevents_assembly(self):
        receipt = self.runtime / "receipts/android-sdk-arm-dbt-license.json"
        record = json.loads(receipt.read_text())
        del record["acceptedAt"]
        package.write_json(receipt, record)
        with self.assertRaisesRegex(RuntimeError, "明确接受"):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_low_disk_space_is_a_stop_condition(self):
        with patch.object(package.shutil, "disk_usage", return_value=shutil._ntuple_diskusage(100, 90, 10)):
            with self.assertRaisesRegex(RuntimeError, "空间停线"):
                package.space(self.root)

    def test_optional_launcher_can_be_omitted(self):
        self.args.without_launcher = True
        self.launcher.unlink()
        self.assemble()
        manifest = package.verify(self.output)
        self.assertFalse(manifest["launcher"]["included"])
        self.assertFalse((self.output / "components" / package.LAUNCHER["filename"]).exists())

    def test_launcher_with_native_code_is_rejected_even_with_matching_hash(self):
        with zipfile.ZipFile(self.launcher, "a") as apk:
            apk.writestr("lib/x86_64/libfixture.so", b"\x7fELFsynthetic native code")
        config = {**self.launcher_config, "size": self.launcher.stat().st_size, "sha256": package.digest(self.launcher)}
        with patch.dict(package.LAUNCHER, config):
            with self.assertRaisesRegex(RuntimeError, "原生库"):
                package.check_launcher(self.launcher)

    def test_corrupt_launcher_download_is_not_published(self):
        destination = self.root / "download/launcher.apk"
        with patch.dict(package.LAUNCHER, self.launcher_config), \
                patch.object(package.urllib.request, "urlopen", return_value=io.BytesIO(b"X" * self.launcher_config["size"])), \
                patch.object(package, "space", return_value=100 * 1024**3):
            with self.assertRaisesRegex(RuntimeError, "大小或 SHA-256"):
                package.fetch_launcher(destination)
        self.assertFalse(destination.exists())
        self.assertFalse(list(destination.parent.glob(".modavd-apk-*")))


if __name__ == "__main__":
    unittest.main()
