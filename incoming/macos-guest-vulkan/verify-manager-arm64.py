#!/usr/bin/env python3
"""Verify the staged pure-Go ARM64 patcher in an isolated Manager module."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


HARNESS = r'''package main

import (
    "bytes"
    "encoding/json"
    "fmt"
    "os"

    "modavd/internal/errs"
    "modavd/internal/vkfix"
)

func check(ok bool, message string) {
    if !ok { panic(message) }
}

func main() {
    original, err := os.ReadFile(os.Args[1]); if err != nil { panic(err) }
    expected, err := os.ReadFile(os.Args[2]); if err != nil { panic(err) }
    saved := bytes.Clone(original)
    output, err := vkfix.PatchARM64(original); if err != nil { panic(err) }
    check(bytes.Equal(output, expected), "Go output differs from independently built candidate")
    check(bytes.Equal(original, saved), "patch changed original input")
    check(len(output) == len(original), "patch changed library size")
    first := output[0]; output[0] ^= 1
    check(bytes.Equal(original, saved), "output aliases original input")
    output[0] = first
    again, err := vkfix.PatchARM64(original); if err != nil { panic(err) }
    check(bytes.Equal(output, again), "patch is not deterministic")
    corrupt := bytes.Clone(original); corrupt[0x213f24] ^= 1
    negative := map[string][]byte{
        "nil": nil,
        "empty": {},
        "truncated": original[:len(original)-1],
        "corrupt_original": corrupt,
        "already_fixed": expected,
    }
    rejected := make(map[string]bool)
    for name, input := range negative {
        before := bytes.Clone(input)
        result, err := vkfix.PatchARM64(input)
        check(err != nil && errs.CodeOf(err) == errs.Unsupported && result == nil,
            fmt.Sprintf("%s did not fail closed with unsupported", name))
        check(bytes.Equal(input, before), name + " input changed")
        rejected[name] = true
    }
    json.NewEncoder(os.Stdout).Encode(map[string]any{
        "ok": true, "bytes_equal_to_candidate": true, "input_immutable": true,
        "output_independent": true, "deterministic": true,
        "original_sha256": vkfix.SHA256Hex(original),
        "result_sha256": vkfix.SHA256Hex(output), "size": len(output),
        "cache_name": vkfix.ARM64CacheName, "negative_inputs_rejected": rejected,
    })
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("original", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--manager-src", type=Path, default=Path(".macos-work/manager"))
    parser.add_argument("--go", default="go")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    env = dict(os.environ)
    env["GOTOOLCHAIN"] = "go1.26.5"
    env["GOWORK"] = "off"
    with tempfile.TemporaryDirectory(prefix="modavd-vulkan-go-") as temporary:
        isolated = Path(temporary)
        for relative in ("go.mod", "internal/vkfix/vkfix.go", "internal/errs/errs.go"):
            target = isolated / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.manager_src / relative, target)
        shutil.copy2(here / "manager-arm64-profile.go", isolated / "internal/vkfix/arm64.go")
        (isolated / "main.go").write_text(HARNESS)
        version = subprocess.run([args.go, "version"], env=env, cwd=isolated, text=True,
                                 capture_output=True, timeout=60, check=True)
        result = subprocess.run([args.go, "run", ".", args.original.resolve(), args.candidate.resolve()],
                                env=env, cwd=isolated, text=True, capture_output=True, timeout=60)
        if result.returncode:
            raise RuntimeError(f"isolated Go verification failed:\n{result.stdout}\n{result.stderr}")
    checks = json.loads(result.stdout)
    if not checks.get("ok"):
        raise RuntimeError("isolated Go checks did not pass")
    report = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "toolchain": version.stdout.strip(),
        "profile_sha256": hashlib.sha256((here / "manager-arm64-profile.go").read_bytes()).hexdigest(),
        "verifier_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "checks": checks, "run_stderr": result.stderr,
        "limits": ["Copies Manager vkfix and errs into a temporary module; Manager source is unchanged.",
                   "Proves complete output byte equality with the independently assembled v2 candidate.",
                   "Does not test Manager architecture selection, cache, upload, mount or Android Vulkan behavior.",
                   "No emulator, guest, ADB, original library or candidate modified."],
    }
    with args.output.open("x") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
