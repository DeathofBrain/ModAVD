#!/usr/bin/env python3
"""Extract actual DmaMap lifetime registry and exec counter for native shim QA."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess


def function(source, anchor):
    start = source.index(anchor)
    opening = source.index("{", start)
    depth = 0
    for end in range(opening, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if not depth:
                return source[start:end + 1]
    raise ValueError("unterminated function: " + anchor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose fresh evidence; historical output is never replaced")
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    private = root / ".macos-work/qemu-patch-dev"
    paths = {"registry": private / "android/android-emu/android/emulation/DmaMap.cpp",
             "counter": private / "exec.c",
             "template": here / "external-lifetime-test.cpp.in"}
    contents = {key: path.read_text() for key, path in paths.items()}
    registry = contents["registry"]
    start = registry.index("static std::atomic<bool> sExternalTrackingReady")
    end = registry.index('extern "C" void modavd_dma_set_audit_borrow', start)
    exact_registry = registry[start:end]
    exact_counter = function(contents["counter"], "void qemu_ram_reclaim_external_lifetime(")
    source = contents["template"].replace("@@REGISTRY@@", exact_registry).replace("@@COUNTER@@", exact_counter)
    build = args.output.parent / (args.output.stem + "-build")
    build.mkdir(parents=True, exist_ok=False)
    cpp = build / "external-lifetime.cpp"
    cpp.write_text(source)
    binary = build / "external-lifetime"
    clang = subprocess.check_output(["xcrun", "-f", "clang++"], text=True).strip()
    sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
    command = [clang, "-std=c++17", "-O1", "-g", "-isysroot", sdk,
               "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-pthread",
               str(cpp), "-o", str(binary)]
    compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
    (build / "compile.log").write_text(compiled.stdout + compiled.stderr)
    if compiled.returncode:
        raise RuntimeError("native compile failed: " + compiled.stderr)
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0", UBSAN_OPTIONS="halt_on_error=1")
    results = []
    for mode in ["normal", "duplicate-enter", "missing-leave"]:
        result = subprocess.run([str(binary), mode], capture_output=True, text=True, timeout=45, env=env)
        results.append({"mode": mode, "returncode": result.returncode,
                        "stdout": result.stdout, "stderr": result.stderr})
    unchanged = all(path.read_text() == contents[key] for key, path in paths.items())
    passed = (results[0]["returncode"] == 0 and
              all(x["returncode"] == -signal.SIGABRT for x in results[1:]) and unchanged)
    report = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "pass": passed, "asan_ubsan": True, "source_unchanged_during_run": unchanged,
              "checks": json.loads(results[0]["stdout"]) if results[0]["returncode"] == 0 else None,
              "results": results, "compile_command": command,
              "sha256": {str(path): hashlib.sha256(contents[key].encode()).hexdigest() for key, path in paths.items()},
              "extracted_registry_sha256": hashlib.sha256(exact_registry.encode()).hexdigest(),
              "extracted_counter_sha256": hashlib.sha256(exact_counter.encode()).hexdigest(),
              "limits": ["Executes exact registry and counter function text with std::mutex QEMU synchronization and no-op logging shims.",
                         "Observer checks ready/counter under the audit mutex against independently marked completed enters.",
                         "Does not exercise real QEMU lock initialization, actual memory discard, guest workloads, native codec playback or all pointer ingress.",
                         "No runtime source, package, emulator or guest was modified."]}
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"pass": passed, "checks": report["checks"],
                      "negative_returncodes": [x["returncode"] for x in results[1:]]}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
