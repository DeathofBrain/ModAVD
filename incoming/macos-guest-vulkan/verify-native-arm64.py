#!/usr/bin/env python3
"""Execute exact candidate ARM code with private synthetic trackers and checked PLT callbacks."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ORIGINAL_SHA256 = "d6188011ed2ed58a1d69adfb04246abaa2595c0f15565a22da83f7751a3d6828"
CANDIDATE_SHA256 = "73ddfe0935826feef1f1a78d5a70af3ee7fa83584d2efaf5bad6ee91f36eb613"


def run(command, env):
    result = subprocess.run(list(map(str, command)), env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if result.returncode:
        raise RuntimeError(f"command failed ({result.returncode}): {command}\n"
                           f"{result.stdout}\n{result.stderr}")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path, expected in ((args.original, ORIGINAL_SHA256), (args.candidate, CANDIDATE_SHA256)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("unexpected library hash: " + str(path))
    here = Path(__file__).resolve().parent
    env = dict(os.environ)
    env.setdefault("DEVELOPER_DIR", "/Applications/Xcode.app/Contents/Developer")
    with tempfile.TemporaryDirectory(prefix="modavd-vulkan-native-") as temporary:
        binary = Path(temporary) / "verify-arm"
        entitlements = Path(temporary) / "jit.plist"
        entitlements.write_text('<?xml version="1.0"?><plist version="1.0"><dict>'
                                '<key>com.apple.security.cs.allow-jit</key><true/></dict></plist>')
        compiled = run(["xcrun", "--sdk", "macosx", "clang", "-arch", "arm64", "-O2", "-std=c11",
                        "-Wall", "-Wextra", here / "native-arm64-test.c", "-o", binary], env)
        run(["codesign", "--force", "--sign", "-", "--entitlements", entitlements, binary], env)
        actual = run([binary, args.original.resolve(), args.candidate.resolve()], env)
    checks = json.loads(actual.stdout)
    if not checks.get("ok"):
        raise RuntimeError("native ARM checks did not pass")
    report = {"timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "original_sha256": ORIGINAL_SHA256, "candidate_sha256": CANDIDATE_SHA256,
              "test_source_sha256": hashlib.sha256((here / "native-arm64-test.c").read_bytes()).hexdigest(),
              "checks": checks, "compile_stderr": compiled.stderr, "run_stderr": actual.stderr,
              "limits": ["Private native ARM instruction, argument, validator, lock-scope and registered-FDE unwind test only.",
                         "Synthetic fixed-layout maps and native callback PLT replacements; not Android bionic, real guest maps or host Vulkan.",
                         "KHR check proves entry routing and argument preservation; it bypasses Android TLS and does not execute descriptor linearization.",
                         "Android unwinder/loader, Vulkan protocol, rendering and full multithreaded app compatibility remain unverified.",
                         "No existing emulator, guest, ADB or package modified."]}
    with args.output.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
