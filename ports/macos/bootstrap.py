#!/usr/bin/env python3
"""Reproducible local Apple Silicon build; never installs system-wide tools."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".macos-work"
MANIFEST_REV = "2692acc620f6563b21995540656674faeb536cdc"
DARWIN_PROJECTS = (
    ("platform/prebuilts/bazel/darwin-x86_64", "prebuilts/bazel/darwin-x86_64", "b1630d780be4131f5bac61c5a9f7c9ee0387c5aa"),
    ("platform/prebuilts/clang/host/darwin-x86", "prebuilts/clang/host/darwin-x86", "9bd561d93a9a848ed607a417857c13ac5a050285"),
    ("platform/prebuilts/cmake/darwin-x86", "prebuilts/cmake/darwin-x86", "d2cbb801268ab9d166f86f7c366a41ebea82b762"),
    ("platform/prebuilts/ninja/darwin-x86", "prebuilts/ninja/darwin-x86", "c33943dfc7058ac5ff4021d031dae4cbd926c743"),
    ("platform/prebuilts/python/darwin-x86", "prebuilts/python/darwin-x86", "8a7b323c92153acc4390bf71392dec2dbb10f4f5"),
    ("platform/external/flex", "external/flex", "104fbfc36067cd630eff3430ee9736febbe26956"),
    ("toolchain/m4", "external/m4", "4c63838f0ccd1a8850a3d17f31c3427668a78a23"),
)
ACTIVE_SERIES = ("qemu/mas36", "gfxstream/mas35", "crashpad/module-version22")
# These contain sources for rebuilding prebuilts and upstream E2E images.
# Neither is consumed by the native emulator build; runtime images are locked
# separately in runtime/components.lock.json.
DEFERRED_PROJECTS = {
    "prebuilts/android-emulator-build/archive",
    "prebuilts/android-emulator-build/system-images",
}
SPARSE_PREBUILTS = {
    "prebuilts/android-emulator-build/qt": ("darwin-aarch64",),
    "prebuilts/clang/host/darwin-x86": ("clang-r596125",),
    "prebuilts/android-emulator-build/common": (
        "ANGLE/darwin-aarch64", "e2fsprogs/darwin-aarch64", "ffmpeg/darwin-aarch64",
        "libusb/darwin-aarch64", "libvpx/darwin-aarch64", "libxml2/darwin-aarch64",
        "netsim/darwin-aarch64", "virglrenderer/darwin-aarch64", "vulkan/darwin-aarch64",
        "x264/darwin-aarch64", "swiftshader/darwin-aarch64", "automation/macros",
        "automation/macroPreviews", "skins/arm64-v8a", "virtualscene", "testdata", "epoxy",
    ),
}


def free_gib(path):
    return shutil.disk_usage(path).free / 1024**3


def require_space(path, reserve):
    free = free_gib(path)
    if free < reserve:
        raise RuntimeError(f"Only {free:.1f} GiB free; preserving {reserve} GiB. Resume after freeing space.")


def stop_process(proc):
    if proc.poll() is not None:
        return
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
            proc.wait(timeout=10)
            return
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            continue


def run(args, *, cwd=ROOT, env=None, reserve=20, cancelled=None):
    """Monitor disk while a resumable sync/build runs in its own process group."""
    require_space(cwd, reserve)
    if cancelled is not None and cancelled.is_set():
        raise RuntimeError("Download cancelled; completed objects are retained")
    args = list(map(str, args))
    print("+", " ".join(args), flush=True)
    proc = subprocess.Popen(args, cwd=cwd, env=env, start_new_session=True)
    try:
        while proc.poll() is None:
            if free_gib(cwd) < reserve:
                stop_process(proc)
                raise RuntimeError(f"Paused at the {reserve} GiB disk reserve. Existing downloads/build objects are retained.")
            if cancelled is not None and cancelled.is_set():
                stop_process(proc)
                raise RuntimeError("Stopped sibling download after another fetch failed; completed objects are retained")
            time.sleep(1)
    except KeyboardInterrupt:
        stop_process(proc)
        raise
    if proc.returncode:
        raise subprocess.CalledProcessError(proc.returncode, args)


def git_output(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def manifest_bytes():
    tree = ET.parse(ROOT / "manifests/manifest-aosp-base.xml")
    manifest = tree.getroot()
    for project in list(manifest.findall("project")):
        if "windows" in project.get("path", "") or project.get("path") in DEFERRED_PROJECTS:
            manifest.remove(project)
    present = {p.get("path") for p in manifest.findall("project")}
    for name, path, revision in DARWIN_PROJECTS:
        if path not in present:
            ET.SubElement(manifest, "project", name=name, path=path, revision=revision,
                          upstream="emu-main-dev", **{"clone-depth": "1"})
    for project in manifest.findall("project"):
        project.set("clone-depth", "1")
    ET.indent(tree, space="  ")
    return ET.tostring(manifest, encoding="utf-8", xml_declaration=True) + b"\n"


def repo_launcher(reserve):
    tools = WORK / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    launcher = tools / "repo"
    if not launcher.exists():
        require_space(tools, reserve)
        with urllib.request.urlopen("https://storage.googleapis.com/git-repo-downloads/repo", timeout=60) as response:
            data = response.read()
        compile(data, str(launcher), "exec")
        launcher.write_bytes(data)
    return [sys.executable, str(launcher)]


def fetch_project(project, *, source, reserve_gib=20, cancelled=None):
    src = source
    path, revision = project.get("path"), project.get("revision")
    if not re.fullmatch(r"[0-9a-f]{40}", revision or ""):
        raise RuntimeError(f"Unpinned project: {path}")
    gitdir = src / ".repo/projects" / (path + ".git")
    sparse = SPARSE_PREBUILTS.get(path)
    if sparse:
        for key, value in (("remote.aosp.promisor", "true"),
                           ("remote.aosp.partialclonefilter", "blob:none"),
                           ("core.sparseCheckout", "true"), ("core.sparseCheckoutCone", "true")):
            subprocess.run(["git", f"--git-dir={gitdir}", "config", key, value], check=True)
        patterns = ["/*", "!/*/"]
        parents = {str(parent) for item in sparse for parent in Path(item).parents if str(parent) != "."}
        for parent in sorted(parents, key=lambda p: (p.count("/"), p)):
            patterns += [f"/{parent}/", f"!/{parent}/*/"]
        patterns += [f"/{item}/" for item in sparse]
        (gitdir / "info/sparse-checkout").write_text("\n".join(patterns) + "\n")
    missing_mode = "allow-promisor" if sparse else "error"
    check = ["git", "--no-lazy-fetch", f"--git-dir={gitdir}", "rev-list", "--no-walk", "--objects", f"--missing={missing_mode}", revision]
    inspect_env = dict(os.environ, GIT_NO_LAZY_FETCH="1")
    # allow-promisor alone also tolerates a missing tip. Verify the pinned
    # commit itself before treating its promised blobs as intentionally absent.
    tip = subprocess.run(["git", "--no-lazy-fetch", f"--git-dir={gitdir}", "cat-file", "-e", f"{revision}^{{commit}}"],
                         env=inspect_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    trees = ["git", "--no-lazy-fetch", f"--git-dir={gitdir}", "ls-tree", "-r", revision]
    complete = tip.returncode == 0 and all(subprocess.run(
        command, env=inspect_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        for command in (trees, check))
    if complete:
        print(f"Already fetched: {path}", flush=True)
        return
    command = ["git", f"--git-dir={gitdir}", "-c", "gc.auto=0", "fetch", "--depth=1", "--no-tags",
               "--no-recurse-submodules", "--no-auto-gc"]
    if sparse:
        command += ["--filter=blob:none"]
    command += ["aosp", f"{revision}:refs/modavd/pinned"]
    run(command,
        cwd=src, reserve=reserve_gib, cancelled=cancelled)
    subprocess.run(["git", "--no-lazy-fetch", f"--git-dir={gitdir}", "cat-file", "-e", f"{revision}^{{commit}}"],
                   env=inspect_env, check=True)
    subprocess.run(trees, env=inspect_env, stdout=subprocess.DEVNULL, check=True)
    subprocess.run(check, env=inspect_env, stdout=subprocess.DEVNULL, check=True)
    print(f"Fetched fixed commit: {path}", flush=True)



def materialize_projects(projects, *, source, launcher, jobs=4, reserve_gib=20):
    """Materialize only absent checkouts and retain every existing local edit."""
    missing = []
    for project in projects:
        path = project.get("path")
        worktree = source / path
        if not (worktree / ".git").exists():
            missing.append(path)
            continue
        head = subprocess.run(["git", "--no-lazy-fetch", "-C", str(worktree),
                               "rev-parse", "--verify", "HEAD"], capture_output=True, text=True)
        if head.returncode or not (source / ".repo/projects" / (path + ".git") / "index").is_file():
            raise RuntimeError(f"Incomplete checkout: {path}; existing files are retained for inspection")
        deleted = subprocess.check_output(["git", "-C", str(worktree), "ls-files", "--deleted", "-z"])
        if deleted:
            raise RuntimeError(f"Missing tracked files in {path}; local deletions or incomplete checkout retained")
        if head.stdout.strip() != project.get("revision"):
            print(f"Retaining local revision: {path}", flush=True)
    if missing:
        # Partial-clone checkout fetches selected blobs on demand. Keep this
        # phase under the same disk guard even though repo calls it local-only.
        run([*launcher, "sync", "--local-only", f"-j{jobs}", *missing],
            cwd=source, reserve=reserve_gib)
    receipts = {}
    for project in projects:
        path = project.get("path")
        head = git_output(source / path, "rev-parse", "HEAD")
        deleted = subprocess.check_output(["git", "-C", str(source / path), "ls-files", "--deleted", "-z"])
        if deleted:
            raise RuntimeError(f"Checkout did not finish: {path}; downloaded objects retained")
        receipts[path] = {"pinned_commit": project.get("revision"), "checkout_head": head,
                          "sparse": list(SPARSE_PREBUILTS.get(path, ()))}
    receipt = source / ".repo/modavd-checkouts.json"
    staged = receipt.with_suffix(".json.tmp")
    staged.write_text(json.dumps(receipts, indent=2) + "\n")
    staged.replace(receipt)


def sync(args):
    # The option must be supported; old Git may silently ignore the env form.
    subprocess.run(["git", "--no-lazy-fetch", "version"], check=True)
    src = args.source
    src.mkdir(parents=True, exist_ok=True)
    launcher = repo_launcher(args.reserve_gib)
    if not (src / ".repo").exists():
        run([*launcher, "init", "-u", "https://android.googlesource.com/platform/manifest",
             "-b", MANIFEST_REV, "--depth=1", "-g", "default,platform-darwin"],
            cwd=src, reserve=args.reserve_gib)
    manifest = src / ".repo/manifests/modavd-darwin.xml"
    manifest.write_bytes(manifest_bytes())
    run([*launcher, "init", "-m", manifest.name, "--depth=1", "-g", "default,platform-darwin"],
        cwd=src, reserve=args.reserve_gib)
    # Let repo initialize its own object/ref layout, but fetch the immutable
    # commit ourselves. repo's ordinary shallow sync silently retries without
    # depth on some failures, which can download years of binary history.
    init_code = """
import sys
from pathlib import Path
source = Path(sys.argv[1])
sys.path.insert(0, str(source / '.repo/repo'))
from manifest_xml import XmlManifest
manifest = XmlManifest(str(source / '.repo'), str(source / '.repo/manifest.xml'))
for project in manifest.projects:
    if not Path(project.gitdir).exists():
        project._InitGitDir(quiet=True)
    project._InitRemote()
"""
    run([sys.executable, "-c", init_code, src], reserve=args.reserve_gib)
    projects = ET.fromstring(manifest_bytes()).findall("project")
    cancelled = threading.Event()


    pool = ThreadPoolExecutor(max_workers=args.jobs)
    futures = []
    try:
        futures = [pool.submit(fetch_project, project, source=src, reserve_gib=args.reserve_gib,
                               cancelled=cancelled) for project in projects]
        for future in as_completed(futures):
            future.result()
    except BaseException:
        cancelled.set()
        for future in futures:
            future.cancel()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    materialize_projects(projects, source=src, launcher=launcher, jobs=args.jobs,
                         reserve_gib=args.reserve_gib)
    print(f"Source sync complete; {free_gib(src):.1f} GiB free", flush=True)


def patch(args):
    series = json.loads((ROOT / "patches/series.json").read_text())
    for key in ACTIVE_SERIES:
        item = next(s for s in series if s["dir"] == key)
        repo = args.source / item["repo"]
        current_tree = git_output(repo, "rev-parse", "HEAD^{tree}")
        if current_tree == item["tree"]:
            print(f"Already replayed: {key}")
            continue
        if git_output(repo, "status", "--porcelain"):
            raise RuntimeError(f"Refusing to replay onto dirty repository: {repo}")
        if git_output(repo, "rev-parse", "HEAD") != item["base"]:
            raise RuntimeError(f"Unexpected base in {repo}; inspect it without resetting local work")
        files = sorted((ROOT / "patches" / key).glob("*.patch"))
        if len(files) != item["count"]:
            raise RuntimeError(f"Wrong patch count: {key}")
        run(["git", "-c", "core.autocrlf=false", "-C", repo, "am", "--keep-cr", *files],
            reserve=args.reserve_gib)
        if git_output(repo, "rev-parse", "HEAD^{tree}") != item["tree"]:
            raise RuntimeError(f"Replayed source tree mismatch: {key}")
    apply_overlays(args.source, args.reserve_gib)


def apply_overlays(source, reserve_gib=20):
    qemu_files = [file for folder in ("macos-build", "macos-qemu")
                  for file in sorted((ROOT / "incoming" / folder).glob("*.patch"))]
    apply_repo_overlays(source / "external/qemu", qemu_files,
                        source / ".repo/modavd-patches.json", reserve_gib)
    gfx_files = sorted((ROOT / "incoming/macos-gfxstream").glob("*.patch"))
    if gfx_files:
        apply_repo_overlays(source / "hardware/google/gfxstream", gfx_files,
                            source / ".repo/modavd-gfxstream-overlays.json", reserve_gib)
    aemu_files = sorted((ROOT / "incoming/macos-aemu").glob("*.patch"))
    if aemu_files:
        apply_repo_overlays(source / "hardware/google/aemu", aemu_files,
                            source / ".repo/modavd-aemu-overlays.json", reserve_gib)


def apply_repo_overlays(repo, files, receipt_path, reserve_gib=20):
    """Compare whole overlay trees: later patches may change earlier context."""
    with tempfile.TemporaryDirectory(prefix="modavd-overlay-index-") as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"))

        def index_git(*arguments, quiet=False):
            return subprocess.run(["git", "-C", str(repo), *map(str, arguments)], env=env,
                                  stdout=subprocess.DEVNULL if quiet else subprocess.PIPE,
                                  stderr=subprocess.DEVNULL if quiet else None, check=not quiet)

        def matches():
            return index_git("diff", "--quiet", "--no-ext-diff", quiet=True).returncode == 0

        index_git("read-tree", "HEAD")
        prefix = 0 if matches() else None
        for number, file in enumerate(files, 1):
            index_git("apply", "--cached", file)
            if matches():
                prefix = number
        expected_tree = index_git("write-tree").stdout.decode().strip()
        if prefix != len(files):
            if subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet"]).returncode:
                raise RuntimeError("Staged source changes retained; inspect before applying Darwin overlays")
            if prefix is None and receipt_path.exists():
                previous = json.loads(receipt_path.read_text())
                if previous.get("base_tree") == git_output(repo, "rev-parse", "HEAD^{tree}"):
                    index_git("read-tree", previous["overlay_tree"])
                    if matches():
                        old = [ROOT / item["path"] for item in previous["patches"]]
                        if any(hashlib.sha256(file.read_bytes()).hexdigest() != item["sha256"]
                               for file, item in zip(old, previous["patches"])):
                            raise RuntimeError("Previously applied patch files changed; local source retained")
                        for file in reversed(old):
                            run(["git", "-C", repo, "apply", "--reverse", file], reserve=reserve_gib)
                        prefix = 0
            if prefix is None:
                raise RuntimeError("Source differs from every known overlay state; local edits retained")
            for file in files[prefix:]:
                run(["git", "-C", repo, "apply", "--check", file], reserve=reserve_gib)
                run(["git", "-C", repo, "apply", file], reserve=reserve_gib)
            index_git("read-tree", expected_tree)
            if not matches():
                raise RuntimeError("Darwin overlay verification failed; inspect retained source")
        receipt = {"base_tree": git_output(repo, "rev-parse", "HEAD^{tree}"),
                   "overlay_tree": expected_tree,
                   "patches": [{"path": str(file.relative_to(ROOT)),
                                "sha256": hashlib.sha256(file.read_bytes()).hexdigest()} for file in files]}
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        print(f"Verified complete Darwin overlay in {repo.name}: "
              f"{len(files)} patches, tree {expected_tree}", flush=True)


def build(args):
    src = args.source
    env = os.environ.copy()
    env["DEVELOPER_DIR"] = str(args.developer_dir)
    env["CMAKE_BUILD_PARALLEL_LEVEL"] = str(args.jobs)
    # ExternalProject configure steps launch a separate CMake process, which
    # cannot inherit the parent CMAKE_MAKE_PROGRAM cache entry. Keep those
    # nested builds on the same pinned tools instead of requiring Homebrew.
    tool_dirs = [src / "prebuilts/ninja/darwin-x86",
                 src / "prebuilts/cmake/darwin-x86/bin",
                 src / "prebuilts/python/darwin-x86/bin"]
    env["PATH"] = os.pathsep.join([*(str(path) for path in tool_dirs),
                                   env.get("PATH", os.defpath)])
    python = src / "prebuilts/python/darwin-x86/bin/python3"
    out = src / "objs"
    cmd = [python, "android/build/python/cmake.py", "--target", "darwin_aarch64",
           "--qtwebengine", "--config", "release", "--crash", "none", "--out", out,
           "--sdk_build_number", "mas-36",
           "--cmake_option", "OPTION_MINBUILD=ON",
           "--cmake_option", "CMAKE_POLICY_DEFAULT_CMP0087=NEW",
           "--task", "configure"]
    run(cmd, cwd=src / "external/qemu", env=env, reserve=args.reserve_gib)
    # CompileTask also builds the separate fishtank developer application.
    # Install the full emulator directly, without that unrelated distribution.
    cmake = src / "prebuilts/cmake/darwin-x86/bin/cmake"
    run([cmake, "--build", out, "--target", "install/strip", "--parallel", str(args.jobs)],
        cwd=src, env=env, reserve=args.reserve_gib)
    installed = out / "distribution/emulator"
    # These imported universal tools have an unsigned arm64 slice upstream.
    for name in ("e2fsck", "fsck.ext4", "mkfs.ext4", "resize2fs", "tune2fs"):
        run(["codesign", "--force", "--sign", "-", installed / "bin64" / name],
            env=env, reserve=args.reserve_gib)
    # codesign recognizes emulator/{emulator,resources} as a flat bundle.
    # Seal it only after every library/resource and helper has been installed.
    run(["codesign", "--force", "--deep", "--sign", "-", "--entitlements",
         src / "external/qemu/entitlements.plist", installed],
        env=env, reserve=args.reserve_gib)
    run(["codesign", "--verify", "--deep", "--strict", installed],
        env=env, reserve=args.reserve_gib)


def preflight(args):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("This port targets native Apple Silicon macOS")
    env = os.environ.copy()
    env["DEVELOPER_DIR"] = str(args.developer_dir)
    report = {"source": str(args.source), "free_gib": round(free_gib(ROOT), 2),
              "reserve_gib": args.reserve_gib, "manifest_revision": MANIFEST_REV,
              "manifest_sha256": hashlib.sha256(manifest_bytes()).hexdigest()}
    for name, command in (("xcode", ["xcodebuild", "-version"]),
                          ("sdk", ["xcrun", "--sdk", "macosx", "--show-sdk-version"]),
                          ("clang", ["xcrun", "clang", "--version"])):
        report[name] = subprocess.check_output(command, env=env, text=True).strip()
    print(json.dumps(report, indent=2))
    require_space(ROOT, args.reserve_gib)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "manifest", "sync", "patch", "build"))
    parser.add_argument("--source", type=Path, default=WORK / "aosp")
    parser.add_argument("--developer-dir", type=Path, default=Path("/Applications/Xcode.app/Contents/Developer"))
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--reserve-gib", type=float, default=20)
    args = parser.parse_args()
    args.source = args.source.expanduser().resolve()
    if args.jobs < 1 or args.reserve_gib < 20:
        parser.error("jobs must be positive and reserve must be at least 20 GiB")
    if args.command == "manifest":
        sys.stdout.buffer.write(manifest_bytes())
    else:
        {"preflight": preflight, "sync": sync, "patch": patch, "build": build}[args.command](args)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
