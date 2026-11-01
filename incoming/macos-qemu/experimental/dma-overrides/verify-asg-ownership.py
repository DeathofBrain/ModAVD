#!/usr/bin/env python3
"""Native QA for exact renderer certificate/CreateASG and QEMU ASG lifecycle bodies."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess


def block(source, anchor, body_anchor=None):
    start = source.index(anchor)
    opening = source.index(body_anchor, start) if body_anchor else start
    opening = source.index("{", opening)
    depth = 0
    for end in range(opening, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if not depth:
                return source[start:end + 1]
    raise ValueError("unterminated block: " + anchor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("choose fresh evidence; historical output is never replaced")
    here = Path(__file__).resolve().parent
    root = here.parents[3]
    private = root / ".macos-work/qemu-patch-dev"
    original = root / ".macos-work/aosp/hardware/google/gfxstream"
    paths = {
        "dma": here / "host/common/dma_device.cpp",
        "ownership_header": here / "include/modavd-asg-external-ownership.h",
        "dma_header": here / "include/modavd-dma-sized-borrow.h",
        "renderer": here / "host/virtio_gpu_context.cpp",
        "qemu_asg": private / "android/android-emu/android/emulation/address_space_graphics.cpp",
        "qemu_header": private / "include/host-common/address_space_graphics.h",
        "qemu_dma": private / "android/android-emu/android/emulation/DmaMap.cpp",
        "qemu_dma_header": private / "include/sysemu/dma-sized.h",
        "template": here / "asg-ownership-test.cpp.in",
    }
    contents = {name: path.read_text() for name, path in paths.items()}
    qemu_dma = contents["qemu_dma"]
    # Keep the actual query wrapper and complete identity registry together.
    registry = qemu_dma[qemu_dma.index("static std::atomic<ModavdDmaAuditBorrow>"):
                        qemu_dma.index("\nnamespace android {")]
    ctor = block(contents["qemu_asg"],
                 "AddressSpaceGraphicsContext::AddressSpaceGraphicsContext(",
                 "mConsumerInterface(sGlobals->getConsumerInterface()) {")
    replacements = {
        "@@REGISTRY@@": registry,
        "@@CREATE_ASG@@": block(contents["renderer"],
                                "int VirtioGpuContext::CreateAddressSpaceGraphicsInstance("),
        "@@QEMU_CTOR@@": ctor,
        "@@QEMU_DTOR@@": block(contents["qemu_asg"],
                               "AddressSpaceGraphicsContext::~AddressSpaceGraphicsContext()"),
        "@@GUARD_MEMBER@@": "    " + next(line.strip() for line in contents["qemu_header"].splitlines()
                                          if "bool mExternalReclaimGuard" in line),
    }
    source = contents["template"]
    for marker, replacement in replacements.items():
        if source.count(marker) != 1:
            raise ValueError("expected unique template marker: " + marker)
        source = source.replace(marker, replacement)
    build = args.output.parent / (args.output.stem + "-build")
    build.mkdir(parents=True, exist_ok=False)
    cpp = build / "asg-ownership.cpp"
    cpp.write_text(source)
    binary = build / "asg-ownership"
    clang = subprocess.check_output(["xcrun", "-f", "clang++"], text=True).strip()
    sdk = subprocess.check_output(["xcrun", "--show-sdk-path"], text=True).strip()
    command = [clang, "-std=c++17", "-O1", "-g", "-isysroot", sdk,
               "-fsanitize=address,undefined", "-fno-omit-frame-pointer", "-pthread",
               "-I" + str(here / "include"),
               "-I" + str(private / "include"),
               "-I" + str(original / "host/common/include"),
               "-I" + str(original / "host/include"),
               str(cpp), str(paths["dma"]), "-o", str(binary)]
    compiled = subprocess.run(command, text=True, capture_output=True, timeout=60)
    (build / "compile.log").write_text(compiled.stdout + compiled.stderr)
    if compiled.returncode:
        raise RuntimeError("native compile failed: " + compiled.stderr)
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0", UBSAN_OPTIONS="halt_on_error=1")
    results = []
    for mode in ["normal", "out-of-order-scope"]:
        result = subprocess.run([str(binary), mode], text=True, capture_output=True,
                                timeout=45, env=env)
        results.append({"mode": mode, "returncode": result.returncode,
                        "stdout": result.stdout, "stderr": result.stderr})
    unchanged = all(path.read_text() == contents[name] for name, path in paths.items())
    passed = (results[0]["returncode"] == 0 and
              results[1]["returncode"] == -signal.SIGABRT and unchanged)
    report = {
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pass": passed, "asan_ubsan": True,
        "source_unchanged_during_run": unchanged,
        "checks": json.loads(results[0]["stdout"]) if results[0]["returncode"] == 0 else None,
        "results": results, "compile_command": command,
        "sha256": {str(path): hashlib.sha256(contents[name].encode()).hexdigest()
                   for name, path in paths.items()},
        "extracted_sha256": {name: hashlib.sha256(value.encode()).hexdigest()
                             for name, value in replacements.items()},
        "limits": [
            "Compiles the complete modified renderer dma_device.cpp with its actual headers.",
            "Executes exact renderer CreateASG, QEMU constructor/destructor, guard member initializer, DmaMap query wrapper and identity registry text.",
            "Resource, allocation, console and consumer interfaces are test shims; the consumer shim starts a real native thread and joins it in destroy().",
            "Checks that production destructor calls destroy before all three free interfaces and leaves its guard afterward; real renderer join implementation is not executed.",
            "Does not exercise QEMU/renderer dynamic loading, real RingBlob allocation, actual memory discard, GPU output, snapshot loading or guest workloads.",
            "No runtime source, package, emulator or guest was modified.",
        ],
    }
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"pass": passed, "checks": report["checks"],
                      "negative_returncode": results[1]["returncode"]}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
