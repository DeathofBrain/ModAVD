#!/usr/bin/env python3
"""Run bounded 2/4/8-vCPU native HVF transaction/rollback experiments.

Each subprocess maps at most 65.1 MiB into one guest and exits before the next.
This does not test QEMU, Android, renderer access, or production synchronization.
"""
import argparse
from pathlib import Path
import platform
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpus", choices=[2, 4, 8], type=int, nargs="+",
                        default=[2, 4, 8])
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise SystemExit("This probe requires native Apple Silicon macOS.")
    source = Path(__file__).with_name("experimental-hvf-transaction-probe.c")
    with tempfile.TemporaryDirectory(prefix="modavd-hvf-transaction-") as directory:
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
        for cpus in args.cpus:
            for partial in [False, True]:
                command = [str(binary), str(cpus)]
                if partial:
                    command.append("partial")
                subprocess.run(command, check=True, timeout=15)


if __name__ == "__main__":
    main()
