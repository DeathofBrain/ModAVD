"""Update archive integration checks; no download, live application or SDK use."""

import importlib.util
import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile

from ports.macos.qa import test_package as package_fixture

SPEC = importlib.util.spec_from_file_location("modavd_release", Path(__file__).resolve().parents[1] / "release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        self.fixture = package_fixture.PackageTest("test_complete_assembly_preserves_links_modes_and_independent_files")
        self.fixture.setUp()
        self.fixture.assemble()
        self.source = self.fixture.output
        self.output = self.fixture.root / "releases/local-update.zip"

    def tearDown(self):
        self.fixture.tearDown()

    def create(self):
        with patch.object(release.assembler, "space", return_value=100 * 1024**3):
            return release.create(self.source, self.output)

    def test_archive_keeps_links_modes_and_original_package_metadata_excludes_state_and_google(self):
        (self.source / "avd").mkdir()
        (self.source / "avd/user-secret").write_text("must never ship")
        (self.source / "mas-avd.json").write_text("must never ship")
        result = self.create()
        self.assertEqual(result["sha256"], release.assembler.digest(self.output))
        with zipfile.ZipFile(self.output) as archive:
            names = set(archive.namelist())
            transport = json.loads(archive.read(release.TRANSPORT))
            self.assertEqual(names - {release.TRANSPORT}, set(transport["files"]))
            self.assertEqual(transport["format"], "modavd-macos-update-v1")
            self.assertFalse(any(n.startswith(("avd/", "logs/", "sdk/platform-tools/", "sdk/system-images/")) for n in names))
            self.assertNotIn("mas-avd.json", names)
            self.assertEqual(archive.read("modavd-manifest.json"), (self.source / "modavd-manifest.json").read_bytes())
            self.assertEqual(archive.read(release.assembler.MANIFEST), (self.source / release.assembler.MANIFEST).read_bytes())
            link = archive.getinfo("sdk/emulator/lib64/libfixture.dylib")
            self.assertTrue(stat.S_ISLNK(link.external_attr >> 16))
            self.assertEqual(archive.read(link), b"libfixture.1.dylib")
            self.assertEqual(stat.S_IMODE(archive.getinfo("tools/modavd").external_attr >> 16), 0o755)

    def test_rejects_external_cross_unit_dangling_and_special_mode(self):
        unit = self.fixture.root / "unit"
        unit.mkdir()
        outside = self.fixture.root / "outside"
        outside.write_text("outside this update unit")
        for number, target in enumerate(("../outside", "/tmp", "missing", "loop")):
            link = unit / ("loop" if target == "loop" else "link%d" % number)
            link.symlink_to(target)
            with self.assertRaises(RuntimeError):
                release.record(link, unit)
        special = unit / "special"
        special.write_text("setuid must not ship")
        special.chmod(0o4755)
        with self.assertRaisesRegex(RuntimeError, "特殊权限"):
            release.record(special, unit)

    def test_existing_output_and_source_nested_output_never_overwrite(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"keep existing archive")
        with self.assertRaisesRegex(RuntimeError, "拒绝覆盖"):
            self.create()
        self.assertEqual(self.output.read_bytes(), b"keep existing archive")
        with self.assertRaisesRegex(RuntimeError, "源包目录之外"):
            release.create(self.source, self.source / "inside.zip")

    def test_failed_final_source_validation_does_not_publish_or_keep_archive(self):
        manifest = release.assembler.verify(self.source)
        with patch.object(release.assembler, "verify", side_effect=[manifest, RuntimeError("changed input")]):
            with self.assertRaisesRegex(RuntimeError, "changed input"):
                self.create()
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.output.parent.glob(".modavd-update-*")))

    @unittest.skipUnless(sys.platform == "darwin", "Darwin signature attributes")
    def test_archive_preserves_all_signature_attributes_and_excludes_unrelated_metadata(self):
        path = self.source / "sdk/emulator/source.properties"
        expected = {}
        for index, name in enumerate(sorted(release.SIGNATURE_XATTRS)):
            value = b"\x00\xfa\xde" + bytes([index]) + b"signature fixture"
            subprocess.run(["/usr/bin/xattr", "-wx", name, value.hex(), str(path)], check=True)
            expected[name] = base64.b64encode(value).decode("ascii")
        subprocess.run(["/usr/bin/xattr", "-w", "org.modavd.test.private", "must not ship", str(path)], check=True)
        self.create()
        with zipfile.ZipFile(self.output) as archive:
            transport = json.loads(archive.read(release.TRANSPORT))
            self.assertEqual(transport["files"]["sdk/emulator/source.properties"]["xattrs"], expected)
            self.assertEqual(archive.read("sdk/emulator/source.properties"), path.read_bytes())


if __name__ == "__main__":
    unittest.main()
