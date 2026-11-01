#!/usr/bin/env python3
"""Verify QEMU's actual SIMD mapping and round-trip all Q registers through HVF.

The production function is extracted verbatim. The signed temporary process owns
one vCPU and two pages, executes only STR/LDR Q0..Q31 plus HVC, and destroys its
own VM. It does not inspect or connect to Android, ADB, QEMU, or any other VM.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile


def mapping(text):
    signature = "static hv_simd_fp_reg_t regno_to_hv_simd_fp_reg_type(int i)"
    start = text.index(signature)
    opening = text.index("{", start)
    depth, end = 1, opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    function = text[start:end]
    cases = [int(i) for i in re.findall(r"case (\d+):", function)]
    if sorted(cases) != list(range(32)):
        raise RuntimeError("Production mapping must contain exactly cases 0..31")
    return function


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    parser.add_argument("--output", type=Path, help="Create a new JSON evidence file; never overwrite")
    parser.add_argument("--developer-dir", type=Path,
                        default=Path("/Applications/Xcode.app/Contents/Developer"))
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("This verification requires native Apple Silicon macOS")
    source = args.qemu.resolve() / "target/arm/hvf.c"
    production = mapping(source.read_text())
    probe = Path(__file__).with_name("hvf-simd-roundtrip.c")
    env = os.environ.copy()
    env["DEVELOPER_DIR"] = str(args.developer_dir)
    with tempfile.TemporaryDirectory(prefix="modavd-hvf-simd-") as directory:
        work = Path(directory)
        header = work / "hvf-simd-map.inc"
        binary = work / "probe"
        entitlements = work / "entitlements.plist"
        entitlements.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>
''')

        def build(function, signed):
            header.write_text(function + "\n")
            subprocess.run(["xcrun", "clang", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-Wno-return-type", "-mmacosx-version-min=11.0", "-I", str(work),
                            str(probe), "-framework", "Hypervisor", "-o", str(binary)],
                           env=env, check=True, capture_output=True, text=True)
            if signed:
                subprocess.run(["codesign", "--force", "--sign", "-", "--entitlements",
                                str(entitlements), str(binary)], env=env, check=True,
                               capture_output=True, text=True)

        # A negative control reproduces the original defect in a temporary
        # header only. It must fail before creating any VM.
        negative = re.sub(r"(case 30:\s*return HV_SIMD_FP_REG_)Q30;", r"\g<1>Q20;", production)
        if negative == production:
            raise RuntimeError("Expected fixed production Q30 mapping before running verification")
        build(negative, False)
        result = subprocess.run([str(binary), "--map-only"], env=env,
                                capture_output=True, text=True, timeout=5)
        control = json.loads(result.stdout)
        if result.returncode != 1 or control != {
            "ok": False, "phase": "mapping", "register": 30, "actual": 20, "expected": 30
        }:
            raise RuntimeError(f"Negative control did not reject the original Q30 alias: {result}")
        build(production, True)
        result = subprocess.run([str(binary)], env=env, capture_output=True,
                                text=True, timeout=20)
        if result.returncode:
            raise RuntimeError(f"HVF SIMD verification failed ({result.returncode}): {result.stderr} {result.stdout}")
        actual = json.loads(result.stdout)
    report = {
        "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "probe_sha256": hashlib.sha256(probe.read_bytes()).hexdigest(),
        "scope": "Extracted production mapping, real private HVF API and guest SIMD execution; no existing VM touched",
        "negative_control_rejected": control,
        "actual_hvf": actual,
        "limits": "Does not boot Android or execute the complete QEMU hvf_put/get_registers functions, snapshot or sleep lifecycle",
    }
    if args.output:
        with args.output.open("x") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
