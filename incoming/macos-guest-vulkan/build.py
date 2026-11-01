#!/usr/bin/env python3
"""Build an Android ARM64 Vulkan GPU/readback probe; never deploy or run a guest."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent
NDK_SHA1 = "2970926d705988f79baa9b04c51b4f7914dd8c56"


def run(argv):
    print("+", " ".join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), check=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ndk-zip", type=Path, default=ROOT / ".macos-work/android-ndk-r27d-darwin.zip")
    p.add_argument("--ndk", type=Path, default=ROOT / ".macos-work/android-ndk-r27d")
    p.add_argument("--output", type=Path, default=ROOT / ".macos-work/qa/guest-vulkan-probe")
    args = p.parse_args()
    if not args.ndk.is_dir():
        with args.ndk_zip.open("rb") as f:
            digest = hashlib.file_digest(f, "sha1").hexdigest()
        if digest != NDK_SHA1:
            raise RuntimeError("NDK archive does not match Google's repository checksum")
        # Extract only the compiler/sysroot/CRT needed for this C binary. ZIP paths
        # are checked even though the complete official archive digest is pinned.
        with zipfile.ZipFile(args.ndk_zip) as archive:
            for entry in archive.infolist():
                name = entry.filename
                if not name.startswith("android-ndk-r27d/"):
                    raise RuntimeError(f"Unexpected NDK archive path: {name}")
                relative = Path(name)
                if ".." in relative.parts or relative.is_absolute():
                    raise RuntimeError(f"Unsafe NDK archive path: {name}")
                if name.endswith("/"):
                    continue
                if "/toolchains/llvm/prebuilt/darwin-x86_64/" not in name and name != "android-ndk-r27d/source.properties":
                    continue
                target = args.ndk.parent / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                mode = entry.external_attr >> 16
                if mode & 0o170000 == 0o120000:
                    target.symlink_to(archive.read(entry).decode())
                else:
                    with archive.open(entry) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(mode & 0o777)
    toolchain = args.ndk / "toolchains/llvm/prebuilt/darwin-x86_64"
    clang = toolchain / "bin/aarch64-linux-android34-clang"
    validator = ROOT / ".macos-work/aosp/prebuilts/android-emulator-build/common/vulkan/darwin-aarch64/glslangValidator"
    headers = ROOT / ".macos-work/aosp/hardware/google/gfxstream/third_party/vulkan/include"
    args.output.mkdir(parents=True, exist_ok=True)
    declarations = ["#include <stdint.h>\n"]
    for name in ("buffer", "image", "view"):
        spv = args.output / f"{name}.spv"
        run([validator, "-V", "--target-env", "vulkan1.1", SOURCE / f"{name}.comp", "-o", spv])
        data = spv.read_bytes()
        words = struct.unpack(f"<{len(data) // 4}I", data)
        declarations.append(f"static const uint32_t {name}_spv[] = {{\n" +
                            ",".join(f"0x{n:08x}" for n in words) + "\n};\n")
    (args.output / "shader_spv.h").write_text("".join(declarations))
    binary = args.output / "modavd-guest-vulkan-probe"
    run([clang, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-fPIE", "-pie",
         "-I", headers, "-I", args.output, SOURCE / "probe.c", "-lvulkan", "-o", binary])
    run([toolchain / "bin/llvm-readelf", "-h", binary])
    receipt = {"schema": 1, "ndk": "r27d", "ndk_official_sha1": NDK_SHA1,
               "ndk_url": "https://dl.google.com/android/repository/android-ndk-r27d-darwin.zip",
               "abi": "arm64-v8a", "min_api": 34,
               "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
               "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in [SOURCE / "probe.c", *(SOURCE / f"{n}.comp" for n in ("buffer", "image", "view"))]}}
    (args.output / "build-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(binary)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
