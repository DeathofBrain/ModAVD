"""Offline regressions using real Git object stores and file:// remotes.

No AOSP download, repo launcher, SDK, emulator, or personal Git configuration is
used. Fetch and checkout commands execute unchanged through bootstrap helpers.
"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "bootstrap.py"
SPEC = importlib.util.spec_from_file_location("modavd_bootstrap", SOURCE)
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class BootstrapGitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which("git"):
            raise unittest.SkipTest("Git is required for the offline integration tests")
        probe = subprocess.run(["git", "--no-lazy-fetch", "version"], capture_output=True)
        if probe.returncode:
            raise unittest.SkipTest("Git with --no-lazy-fetch support is required")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="modavd-bootstrap-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "aosp"
        self.source.mkdir()
        self.trace = self.root / "git-trace.jsonl"
        self.environment = patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ALLOW_PROTOCOL": "file",
            "GIT_TRACE2_EVENT": str(self.trace),
            "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.origin_work = self.root / "origin-work"
        self.git("init", "-q", "--initial-branch=main", str(self.origin_work))
        self.git("-C", self.origin_work, "config", "user.name", "Local verification")
        self.git("-C", self.origin_work, "config", "user.email", "local-verification@example.invalid")
        self.files = {
            "README": "pinned source\n",
            "LICENSE": "shared root license\n",
            "darwin-aarch64/bin/tool": "native Qt\n",
            "windows-x86_64/bin/tool": "Windows Qt\n",
            "linux-x86_64/bin/tool": "Linux Qt\n",
            "ANGLE/README": "shared ANGLE metadata\n",
            "ANGLE/darwin-aarch64/libANGLE.dylib": "native ANGLE\n",
            "ANGLE/windows-x86_64/ANGLE.dll": "Windows ANGLE\n",
            "ANGLE/linux-x86_64/libANGLE.so": "Linux ANGLE\n",
            "skins/arm64-v8a/skin.ini": "arm64 skin\n",
            "skins/x86_64/skin.ini": "x86 skin\n",
            "automation/macros/default.pb": "common macro\n",
            "virtualscene/posters/default.png": "common scene\n",
            "testdata/runtime.txt": "common test data\n",
            "epoxy/include/epoxy.h": "shared headers\n",
        }
        for name, content in self.files.items():
            self.write(self.origin_work / name, content)
        self.commit(self.origin_work, "initial fixture")
        self.ancestor = self.git("-C", self.origin_work, "rev-parse", "HEAD")
        self.write(self.origin_work / "historical-only.txt", "old revision payload\n")
        self.commit(self.origin_work, "old payload")
        self.historical_blob = self.git("-C", self.origin_work, "rev-parse", "HEAD:historical-only.txt")
        (self.origin_work / "historical-only.txt").unlink()
        self.commit(self.origin_work, "pinned fixture")
        self.pinned = self.git("-C", self.origin_work, "rev-parse", "HEAD")
        self.blobs = {
            name: self.git("-C", self.origin_work, "rev-parse", f"HEAD:{name}")
            for name in self.files
        }
        self.git("-C", self.origin_work, "tag", "unrequested-tag")
        self.git("-C", self.origin_work, "checkout", "-q", "--orphan", "unrelated")
        self.git("-C", self.origin_work, "rm", "-q", "-r", "-f", ".")
        self.write(self.origin_work / "unrelated.bin", "another branch payload\n")
        self.commit(self.origin_work, "unrelated branch")
        self.unrelated = self.git("-C", self.origin_work, "rev-parse", "HEAD")
        self.unrelated_blob = self.git("-C", self.origin_work, "rev-parse", "HEAD:unrelated.bin")
        self.remote = self.root / "origin.git"
        self.git("clone", "-q", "--bare", self.origin_work, self.remote)
        self.git("--git-dir", self.remote, "config", "uploadpack.allowFilter", "true")
        self.git("--git-dir", self.remote, "config", "uploadpack.allowAnySHA1InWant", "true")
        self.trace.write_text("")

    @staticmethod
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    @staticmethod
    def git(*args, check=True):
        result = subprocess.run(["git", *map(str, args)], capture_output=True, text=True, check=check)
        return result.stdout.strip() if check else result

    def commit(self, worktree, message):
        self.git("-C", worktree, "add", "--all")
        self.git("-C", worktree, "commit", "-q", "-m", message)

    def project(self, path):
        gitdir = self.source / ".repo/projects" / (path + ".git")
        gitdir.parent.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "--bare", str(gitdir))
        self.git("--git-dir", gitdir, "remote", "add", "aosp", self.remote.as_uri())
        return {"path": path, "revision": self.pinned}, gitdir

    def fetch(self, project):
        bootstrap.fetch_project(project, source=self.source, reserve_gib=0)

    def checkout(self, project, gitdir):
        worktree = self.source / project["path"]
        worktree.mkdir(parents=True, exist_ok=True)
        (worktree / ".git").write_text(f"gitdir: {gitdir}\n")
        self.git("--git-dir", gitdir, "config", "core.bare", "false")
        self.git("--git-dir", gitdir, "config", "core.worktree", str(worktree))
        self.git("-C", worktree, "checkout", "-q", "--detach", self.pinned)
        self.git("-C", worktree, "config", "user.name", "Local verification")
        self.git("-C", worktree, "config", "user.email", "local-verification@example.invalid")
        return worktree

    def fetch_commands(self):
        records = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return [record["argv"] for record in records
                if record.get("event") == "start" and "fetch" in record.get("argv", [])]

    def has_object(self, gitdir, object_id):
        return self.git("--no-lazy-fetch", "--git-dir", gitdir,
                        "cat-file", "-e", object_id, check=False).returncode == 0

    def test_missing_promisor_tip_uses_one_explicit_shallow_fetch(self):
        project, gitdir = self.project("prebuilts/android-emulator-build/qt")
        self.git("--git-dir", gitdir, "config", "remote.aosp.promisor", "true")
        self.git("--git-dir", gitdir, "config", "remote.aosp.partialclonefilter", "blob:none")
        self.assertFalse(self.has_object(gitdir, self.pinned))
        self.trace.write_text("")

        self.fetch(project)

        commands = self.fetch_commands()
        self.assertEqual(len(commands), 1, commands)
        self.assertIn("--depth=1", commands[0])
        self.assertIn("--filter=blob:none", commands[0])
        self.assertTrue(self.has_object(gitdir, self.pinned))
        self.assertFalse(self.has_object(gitdir, self.ancestor))
        # A second inspection must retain promised blobs without fetching them.
        self.trace.write_text("")
        self.fetch(project)
        self.assertEqual(self.fetch_commands(), [])

    def test_fixed_fetch_has_one_commit_and_no_other_branch_or_tag(self):
        project, gitdir = self.project("external/fixture")
        self.fetch(project)

        self.assertEqual(self.git("--git-dir", gitdir, "rev-list", "--count", "refs/modavd/pinned"), "1")
        self.assertEqual(self.git("--git-dir", gitdir, "for-each-ref", "--format=%(refname)"),
                         "refs/modavd/pinned")
        self.assertEqual((gitdir / "shallow").read_text().strip(), self.pinned)
        for unwanted in (self.ancestor, self.historical_blob, self.unrelated, self.unrelated_blob):
            self.assertFalse(self.has_object(gitdir, unwanted), unwanted)
        self.assertTrue(self.has_object(gitdir, self.blobs["README"]))

    def test_sparse_checkout_materializes_native_and_shared_resources_only(self):
        cases = (
            ("prebuilts/android-emulator-build/qt",
             ("LICENSE", "darwin-aarch64/bin/tool"),
             ("windows-x86_64/bin/tool", "linux-x86_64/bin/tool")),
            ("prebuilts/android-emulator-build/common",
             ("LICENSE", "ANGLE/README", "ANGLE/darwin-aarch64/libANGLE.dylib",
              "skins/arm64-v8a/skin.ini", "automation/macros/default.pb",
              "virtualscene/posters/default.png", "testdata/runtime.txt", "epoxy/include/epoxy.h"),
             ("ANGLE/windows-x86_64/ANGLE.dll", "ANGLE/linux-x86_64/libANGLE.so", "skins/x86_64/skin.ini")),
        )
        for path, included, excluded in cases:
            with self.subTest(project=path):
                project, gitdir = self.project(path)
                self.fetch(project)
                worktree = self.checkout(project, gitdir)
                for name in included:
                    self.assertEqual((worktree / name).read_text(), self.files[name], name)
                for name in excluded:
                    self.assertFalse((worktree / name).exists(), name)
                    self.assertFalse(self.has_object(gitdir, self.blobs[name]), name)

    def test_resume_retains_patch_commit_and_uncommitted_user_edits(self):
        project, gitdir = self.project("external/fixture")
        self.fetch(project)
        worktree = self.checkout(project, gitdir)
        self.write(worktree / "local-patch.txt", "applied macOS patch\n")
        self.commit(worktree, "local macOS patch")
        patched = self.git("-C", worktree, "rev-parse", "HEAD")
        self.write(worktree / "README", "uncommitted user edit\n")
        self.write(worktree / "untracked.txt", "user scratch file\n")
        before = self.git("-C", worktree, "status", "--porcelain")

        self.fetch(project)
        bootstrap.materialize_projects(
            [project], source=self.source, reserve_gib=0,
            launcher=[sys.executable, "-c", "raise SystemExit('unexpected checkout during resume')"])

        self.assertEqual(self.git("-C", worktree, "rev-parse", "HEAD"), patched)
        self.assertEqual(self.git("-C", worktree, "status", "--porcelain"), before)
        self.assertEqual((worktree / "README").read_text(), "uncommitted user edit\n")
        self.assertEqual((worktree / "local-patch.txt").read_text(), "applied macOS patch\n")
        self.assertEqual((worktree / "untracked.txt").read_text(), "user scratch file\n")
        receipt = json.loads((self.source / ".repo/modavd-checkouts.json").read_text())
        self.assertEqual(receipt[project["path"]]["pinned_commit"], self.pinned)
        self.assertEqual(receipt[project["path"]]["checkout_head"], patched)

    def test_checkout_with_missing_index_is_not_reported_complete(self):
        project, gitdir = self.project("external/fixture")
        self.fetch(project)
        worktree = self.source / project["path"]
        worktree.mkdir(parents=True)
        (worktree / ".git").write_text(f"gitdir: {gitdir}\n")
        self.write(worktree / "keep.txt", "interrupted checkout contents\n")
        with self.assertRaisesRegex(RuntimeError, "Incomplete checkout"):
            bootstrap.materialize_projects([project], source=self.source, launcher=[], reserve_gib=0)
        self.assertEqual((worktree / "keep.txt").read_text(), "interrupted checkout contents\n")
        self.assertFalse((self.source / ".repo/modavd-checkouts.json").exists())

    def test_resume_retains_missing_tracked_file_for_inspection(self):
        project, gitdir = self.project("external/fixture")
        self.fetch(project)
        worktree = self.checkout(project, gitdir)
        (worktree / "README").unlink()
        with self.assertRaisesRegex(RuntimeError, "Missing tracked files"):
            bootstrap.materialize_projects([project], source=self.source, launcher=[], reserve_gib=0)
        self.assertFalse((worktree / "README").exists())
        self.assertEqual(self.git("-C", worktree, "rev-parse", "HEAD"), self.pinned)
        self.assertFalse((self.source / ".repo/modavd-checkouts.json").exists())


@unittest.skipUnless(shutil.which("git"), "Git is required for overlay integration tests")
class OverlayGitTest(unittest.TestCase):
    git = staticmethod(BootstrapGitTest.git)
    write = staticmethod(BootstrapGitTest.write)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="modavd-overlay-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "aosp"
        self.repo = self.source / "external/qemu"
        self.repo.mkdir(parents=True)
        (self.source / ".repo").mkdir()
        self.overlays = self.root / "port"
        (self.overlays / "incoming/macos-build").mkdir(parents=True)
        (self.overlays / "incoming/macos-qemu").mkdir()
        environment = patch.dict(os.environ, {
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "0", "GIT_TERMINAL_PROMPT": "0", "GIT_ALLOW_PROTOCOL": "file",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.root_patch = patch.object(bootstrap, "ROOT", self.overlays)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.git("init", "-q", "--initial-branch=main", self.repo)
        self.git("-C", self.repo, "config", "user.name", "Local verification")
        self.git("-C", self.repo, "config", "user.email", "local-verification@example.invalid")
        self.target = self.repo / "overlap.txt"
        self.base = "".join(f"original line {number}\n" for number in range(1, 21))
        self.write(self.target, self.base)
        self.write(self.repo / "user.txt", "unchanged user source\n")
        self.git("-C", self.repo, "add", ".")
        self.git("-C", self.repo, "commit", "-q", "-m", "overlay fixture base")
        self.head = self.git("-C", self.repo, "rev-parse", "HEAD")

        # Generate genuine adjacent-context diffs. The second patch changes a
        # context line of the first, so its individual reverse check must fail.
        self.write(self.target, self.base.replace("original line 15", "new build requirement"))
        self.inserted_patch = self.diff_bytes()
        first = self.base.replace("original line 3", "Darwin first fix")
        self.write(self.target, first)
        self.first = self.overlays / "incoming/macos-qemu/0001-first.patch"
        self.first.write_bytes(self.diff_bytes())
        self.git("-C", self.repo, "add", "overlap.txt")
        self.complete = first.replace("original line 4", "Darwin adjacent fix")
        self.write(self.target, self.complete)
        (self.overlays / "incoming/macos-qemu/0002-adjacent.patch").write_bytes(self.diff_bytes())
        self.git("-C", self.repo, "read-tree", "HEAD")
        self.write(self.target, self.base)

    def diff_bytes(self):
        return subprocess.check_output(["git", "-C", str(self.repo), "diff", "--binary", "--no-ext-diff"])

    def apply(self):
        bootstrap.apply_overlays(self.source, reserve_gib=0)

    def test_complete_overlapping_series_is_recognized_as_already_applied(self):
        self.apply()
        self.assertEqual(self.target.read_text(), self.complete)
        reverse = self.git("-C", self.repo, "apply", "--reverse", "--check", self.first, check=False)
        self.assertNotEqual(reverse.returncode, 0, "fixture must reproduce overlapping patch context")
        self.write(self.repo / "personal-untracked.txt", "keep local scratch work\n")

        self.apply()

        self.assertEqual(self.target.read_text(), self.complete)
        self.assertEqual(self.git("-C", self.repo, "rev-parse", "HEAD"), self.head)
        self.assertEqual((self.repo / "personal-untracked.txt").read_text(), "keep local scratch work\n")
        self.assertEqual(self.git("-C", self.repo, "diff", "--cached", "--name-only"), "")

    def test_new_earlier_patch_recovers_known_overlay_and_retains_unknown_edits(self):
        self.apply()
        self.write(self.repo / "personal-untracked.txt", "keep local scratch work\n")
        (self.overlays / "incoming/macos-build/0001-new-build.patch").write_bytes(self.inserted_patch)

        self.apply()

        expected = self.complete.replace("original line 15", "new build requirement")
        self.assertEqual(self.target.read_text(), expected)
        self.assertEqual((self.repo / "personal-untracked.txt").read_text(), "keep local scratch work\n")
        self.assertEqual(self.git("-C", self.repo, "rev-parse", "HEAD"), self.head)
        receipt = self.source / ".repo/modavd-patches.json"
        known_receipt = receipt.read_bytes()
        self.write(self.repo / "user.txt", "unrecognized user edit\n")
        before = self.git("-C", self.repo, "status", "--porcelain")
        with self.assertRaisesRegex(RuntimeError, "local edits retained"):
            self.apply()
        self.assertEqual((self.repo / "user.txt").read_text(), "unrecognized user edit\n")
        self.assertEqual(self.target.read_text(), expected)
        self.assertEqual(self.git("-C", self.repo, "status", "--porcelain"), before)
        self.assertEqual(receipt.read_bytes(), known_receipt)

    def test_gfxstream_overlay_has_independent_tree_and_receipt(self):
        repo = self.source / "hardware/google/gfxstream"
        repo.mkdir(parents=True)
        self.git("init", "-q", "--initial-branch=main", repo)
        self.git("-C", repo, "config", "user.name", "Local verification")
        self.git("-C", repo, "config", "user.email", "local-verification@example.invalid")
        target = repo / "native.txt"
        self.write(target, "original native binding\n")
        self.git("-C", repo, "add", ".")
        self.git("-C", repo, "commit", "-q", "-m", "gfx fixture base")
        head = self.git("-C", repo, "rev-parse", "HEAD")
        self.write(target, "verified native binding\n")
        patches = self.overlays / "incoming/macos-gfxstream"
        patches.mkdir()
        (patches / "0001-native.patch").write_bytes(subprocess.check_output(
            ["git", "-C", str(repo), "diff", "--binary", "--no-ext-diff"]))
        self.write(target, "original native binding\n")

        self.apply()
        self.apply()

        self.assertEqual(target.read_text(), "verified native binding\n")
        self.assertEqual(self.git("-C", repo, "rev-parse", "HEAD"), head)
        qemu_receipt = json.loads((self.source / ".repo/modavd-patches.json").read_text())
        gfx_receipt = json.loads((self.source / ".repo/modavd-gfxstream-overlays.json").read_text())
        self.assertNotEqual(qemu_receipt["overlay_tree"], gfx_receipt["overlay_tree"])
        self.assertEqual(len(qemu_receipt["patches"]), 2)
        self.assertEqual(len(gfx_receipt["patches"]), 1)

    def test_aemu_overlay_has_independent_receipt_and_preserves_unknown_edits(self):
        repo = self.source / "hardware/google/aemu"
        repo.mkdir(parents=True)
        self.git("init", "-q", "--initial-branch=main", repo)
        self.git("-C", repo, "config", "user.name", "Local verification")
        self.git("-C", repo, "config", "user.email", "local-verification@example.invalid")
        target = repo / "declaration.txt"
        self.write(target, "default destructor\n")
        self.git("-C", repo, "add", ".")
        self.git("-C", repo, "commit", "-q", "-m", "aemu fixture base")
        head = self.git("-C", repo, "rev-parse", "HEAD")
        self.write(target, "out-of-line destructor\n")
        patches = self.overlays / "incoming/macos-aemu"
        patches.mkdir()
        (patches / "0001-declaration.patch").write_bytes(subprocess.check_output(
            ["git", "-C", str(repo), "diff", "--binary", "--no-ext-diff"]))
        self.write(target, "default destructor\n")

        self.apply()
        self.apply()

        receipt = self.source / ".repo/modavd-aemu-overlays.json"
        recorded = receipt.read_bytes()
        aemu = json.loads(recorded)
        qemu = json.loads((self.source / ".repo/modavd-patches.json").read_text())
        self.assertEqual(target.read_text(), "out-of-line destructor\n")
        self.assertEqual(self.git("-C", repo, "rev-parse", "HEAD"), head)
        self.assertEqual(len(aemu["patches"]), 1)
        self.assertEqual(aemu["patches"][0]["path"], "incoming/macos-aemu/0001-declaration.patch")
        self.assertNotEqual(aemu["overlay_tree"], qemu["overlay_tree"])
        self.write(target, "unrecognized user declaration\n")
        with self.assertRaisesRegex(RuntimeError, "local edits retained"):
            self.apply()
        self.assertEqual(target.read_text(), "unrecognized user declaration\n")
        self.assertEqual(receipt.read_bytes(), recorded)


if __name__ == "__main__":
    unittest.main()
