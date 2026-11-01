#!/usr/bin/env python3
"""Compile and run the actual pixel-store scope on native accelerated macOS GL."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parents[3]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / ".macos-work/qa/dma-native-pixel-store-rgba8-20261101.json")
    parser.add_argument("--build", type=Path,
                        default=ROOT / ".macos-work/qa/dma-native-pixel-store-build-20261101")
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() != "arm64":
        parser.error("this native probe requires an Apple Silicon Mac")
    if args.output.exists():
        parser.error("output already exists; choose a fresh evidence path")
    args.build.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    gfxstream = ROOT / ".macos-work/aosp/hardware/google/gfxstream/host"
    inputs = [SOURCE / "native-pixel-store.mm", SOURCE / "host/common/dma_device.cpp",
              SOURCE / "include/modavd-dma-pixel-store.h", SOURCE / "include/modavd-dma-sized-borrow.h",
              gfxstream / "common/include/gfxstream/host/dma_device.h",
              gfxstream / "include/render-utils/dma_device.h", Path(__file__).resolve()]
    hashes = {str(path.relative_to(ROOT)): sha(path) for path in inputs}
    binary = args.build / "native-pixel-store"
    command = ["xcrun", "clang++", "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
               "-Wno-deprecated-declarations", "-I" + str(SOURCE / "include"),
               "-I" + str(gfxstream / "common/include"), "-I" + str(gfxstream / "include"),
               str(SOURCE / "native-pixel-store.mm"), str(SOURCE / "host/common/dma_device.cpp"),
               "-framework", "Cocoa", "-framework", "OpenGL", "-o", str(binary)]
    receipt = {"schema": 1, "at": datetime.now(timezone.utc).isoformat(),
               "scope": "Native NSOpenGL/CGL RGBA8 pixel-store test; no guest operations or mocks.",
               "host": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
               "compile_command": command, "source_sha256": hashes, "pass": False}
    try:
        compile_result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (args.build / "compile.stdout.txt").write_text(compile_result.stdout)
        (args.build / "compile.stderr.txt").write_text(compile_result.stderr)
        receipt["compile_exit_code"] = compile_result.returncode
        if compile_result.returncode:
            raise RuntimeError("native Objective-C++ compile failed; see compile.stderr.txt")
        receipt["binary_sha256"] = sha(binary)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        (args.build / "native.stdout.json").write_text(result.stdout)
        (args.build / "native.stderr.txt").write_text(result.stderr)
        receipt["native_exit_code"] = result.returncode
        receipt["native"] = json.loads(result.stdout)
        receipt["source_unchanged_during_run"] = hashes == {
            str(path.relative_to(ROOT)): sha(path) for path in inputs}
        receipt["pass"] = result.returncode == 0 and receipt["native"].get("pass") is True and \
            receipt["source_unchanged_during_run"]
    except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        receipt["failure"] = str(error)
    with args.output.open("x") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(json.dumps(receipt, indent=2))
    return 0 if receipt["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
