#!/usr/bin/env python3
"""Run a bounded 64 MiB single-vCPU HVF reclamation experiment.

This is a native API probe, not an Android emulator or release qualification.
It builds and ad-hoc signs only a temporary executable. All mappings belong to
that process. Results are observations; footprint alone is not a success test.
"""
import argparse
from pathlib import Path
import platform
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-advice", action="store_true",
                        help="also compare DONTNEED, FREE, REUSABLE, and ZERO")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("This probe requires native Apple Silicon macOS.")
    source = Path(__file__).with_name("experimental-hvf-discard-probe.c")
    with tempfile.TemporaryDirectory(prefix="modavd-hvf-discard-") as directory:
        work = Path(directory)
        binary = work / "probe"
        entitlements = work / "entitlements.plist"
        entitlements.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict><key>com.apple.security.hypervisor</key><true/></dict></plist>
''')
        subprocess.run(["xcrun", "clang", "-Wall", "-Wextra", "-Werror",
                        str(source), "-framework", "Hypervisor", "-o", str(binary)], check=True)
        subprocess.run(["codesign", "--force", "--sign", "-", "--entitlements",
                        str(entitlements), str(binary)], check=True)
        if args.all_advice:
            for advice, label in [(4, "MADV_DONTNEED"), (5, "MADV_FREE"),
                                  (7, "MADV_FREE_REUSABLE"), (11, "MADV_ZERO")]:
                print(f"\n{label}: current mapping", flush=True)
                subprocess.run([str(binary), str(advice), "0"], check=True, timeout=10)
        for partial in [False, True]:
            print("\nExperimental unmap/replace/remap: " +
                  ("2 MiB interior" if partial else "64 MiB full region"), flush=True)
            command = [str(binary), "400", "0"]
            if partial:
                command.append("partial")
            subprocess.run(command, check=True, timeout=10)


if __name__ == "__main__":
    main()
