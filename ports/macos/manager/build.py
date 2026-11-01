#!/usr/bin/env python3
"""Pin and build the Darwin manager without changing system tools or existing SDKs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
UPSTREAM = "https://github.com/ModAVD/ModAVDManager.git"
REVISION = "21c2a3ffa22eb37a5e39e61b1b53f2258d0f3e7a"
PATCH = ROOT / "incoming/macos-manager/0001-darwin-arm64.patch"
TOOLCHAIN = "go1.26.5"


def run(argv, *, cwd=ROOT, env=None, capture=False):
    print("+", " ".join(map(str, argv)), flush=True)
    return subprocess.run(list(map(str, argv)), cwd=cwd, env=env, check=True,
                          stdout=subprocess.PIPE if capture else None, text=capture)


def prepare(source):
    if not PATCH.is_file():
        raise RuntimeError(f"Missing patch: {PATCH}")
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--no-checkout", UPSTREAM, source])
        run(["git", "fetch", "--depth=1", "origin", REVISION], cwd=source)
        run(["git", "checkout", "--detach", REVISION], cwd=source)
    if not (source / ".git").exists():
        raise RuntimeError(f"Refusing to overwrite a non-repository: {source}")
    head = run(["git", "rev-parse", "HEAD"], cwd=source, capture=True).stdout.strip()
    if head != REVISION:
        raise RuntimeError(f"Unexpected upstream revision in {source}: {head}; existing work was retained")
    applied = subprocess.run(["git", "apply", "--reverse", "--check", str(PATCH)], cwd=source,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if applied.returncode == 0:
        print("Darwin patch is already present", flush=True)
        verify_source(source)
        return
    dirty = run(["git", "status", "--porcelain"], cwd=source, capture=True).stdout.strip()
    if dirty:
        raise RuntimeError(f"Refusing to apply onto local changes in {source}; existing work was retained")
    run(["git", "apply", "--check", PATCH], cwd=source)
    run(["git", "apply", PATCH], cwd=source)
    verify_source(source)


def verify_source(source):
    # A private temporary index compares the working files with exactly base + patch. The user's index stays intact.
    with tempfile.TemporaryDirectory(prefix="modavd-manager-index-") as directory:
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(Path(directory) / "index")
        run(["git", "read-tree", REVISION], cwd=source, env=env)
        run(["git", "apply", "--cached", PATCH], cwd=source, env=env)
        differences = subprocess.run(["git", "diff", "--quiet", "--no-ext-diff"], cwd=source, env=env)
        extra = run(["git", "ls-files", "--others", "--exclude-standard"], cwd=source, env=env, capture=True).stdout.strip()
        if differences.returncode or extra:
            raise RuntimeError(f"Source differs from the pinned revision plus Darwin patch: {source}; local work was retained")


def build(args):
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise RuntimeError("This build needs an Apple Silicon Mac and the macOS SDK")
    prepare(args.source)
    output = args.output
    marker = output / ".modavd-manager-build.json"
    if output.exists() and any(output.iterdir()) and not marker.is_file():
        raise RuntimeError(f"Refusing to overwrite an unmarked output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"upstream": UPSTREAM, "revision": REVISION, "toolchain": TOOLCHAIN,
                                  "patch_sha256": hashlib.sha256(PATCH.read_bytes()).hexdigest()}, indent=2)+"\n")
    env = os.environ.copy()
    env.update(GOTOOLCHAIN=TOOLCHAIN, GOOS="darwin", GOARCH="arm64", CGO_ENABLED="1",
               MACOSX_DEPLOYMENT_TARGET="13.0")
    developer = args.developer_dir
    if developer is None and Path("/Applications/Xcode.app/Contents/Developer").is_dir():
        developer = Path("/Applications/Xcode.app/Contents/Developer")
    if developer is not None:
        env["DEVELOPER_DIR"] = str(developer)
    if not args.skip_tests:
        run(["go", "test", "./..."], cwd=args.source, env=env)
        run(["go", "vet", "./..."], cwd=args.source, env=env)
    tools = output / "tools"
    tools.mkdir(exist_ok=True)
    for name, package in (("modavd", "."), ("modavd-manager", "./cmd/modavd-gui")):
        run(["go", "build", "-trimpath", "-buildvcs=false", "-ldflags=-s -w", "-o", tools / name, package],
            cwd=args.source, env=env)
    helper = tools / "modavd-webview"
    run(["xcrun", "swiftc", "-O", "-swift-version", "5", "-target", "arm64-apple-macos13.0",
         ROOT / "ports/macos/manager/native/main.swift", "-o", helper], env=env)
    app = output / "ModAVD.app"
    contents = app / "Contents"
    binaries = contents / "MacOS"
    binaries.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tools / "modavd-manager", binaries / "ModAVD")
    shutil.copy2(helper, binaries / "modavd-webview")
    info = {"CFBundleName": "ModAVD", "CFBundleDisplayName": "ModAVD", "CFBundleExecutable": "ModAVD",
            "CFBundleIdentifier": "org.modavd.manager", "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "0.2.0", "CFBundleVersion": "2",
            "LSMinimumSystemVersion": "13.0", "NSHighResolutionCapable": True,
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True}}
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    for target in (binaries / "modavd-webview", binaries / "ModAVD", app):
        run(["/usr/bin/codesign", "--force", "--sign", "-", target], env=env)
    run(["/usr/bin/codesign", "--verify", "--deep", "--strict", app], env=env)
    for name in ("LICENSE", "NOTICE"):
        if (args.source / name).is_file():
            shutil.copy2(args.source / name, output / name)
    checksums = {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in sorted(output.rglob("*")) if path.is_file() and path.name != "SHA256SUMS.json"}
    (output / "SHA256SUMS.json").write_text(json.dumps(checksums, indent=2, ensure_ascii=False)+"\n")
    print(f"Built CLI and native manager: {output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "build"), nargs="?", default="build")
    parser.add_argument("--source", type=Path, default=ROOT / ".macos-work/manager")
    parser.add_argument("--output", type=Path, default=ROOT / ".macos-work/manager-dist")
    parser.add_argument("--developer-dir", type=Path)
    parser.add_argument("--skip-tests", action="store_true", help="Reuse an already validated source build")
    args = parser.parse_args()
    args.source = args.source.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    if args.action == "prepare":
        prepare(args.source)
    else:
        build(args)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
