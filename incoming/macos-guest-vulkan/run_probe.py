#!/usr/bin/env python3
"""Run the GPU probe only against a named disposable guest with a pinned driver."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
DRIVER = "/vendor/lib64/libvulkan_enc.so"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--adb-port", type=int, required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--qemu-pid", type=int, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expected-driver-sha256", required=True)
    parser.add_argument("--binary", type=Path, default=ROOT / ".macos-work/qa/guest-vulkan-probe/modavd-guest-vulkan-probe")
    parser.add_argument("--iterations", type=int, default=128)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.qemu_pid == 54065 or args.adb_port == 20050 or args.serial == "emulator-20050":
        parser.error("the session's validated game guest is excluded")
    if args.adb_port == 5037 or not 1024 <= args.adb_port <= 65535:
        parser.error("an explicit private ADB server port is required")
    if not re.fullmatch(r"emulator-[0-9]+", args.serial):
        parser.error("serial must be emulator-CONSOLEPORT")
    if not re.fullmatch(r"[0-9a-f]{64}", args.expected_driver_sha256):
        parser.error("expected driver SHA256 must be 64 lowercase hex digits")
    if not 1 <= args.iterations <= 4096:
        parser.error("iterations must be 1..4096")
    args.root = args.root.resolve(strict=True)
    args.adb = args.adb.resolve(strict=True)
    args.binary = args.binary.resolve(strict=True)

    def qemu_identity():
        result = subprocess.run(["/bin/ps", "-ww", "-p", str(args.qemu_pid), "-o", "lstart=,command="],
                                check=True, text=True, capture_output=True)
        # macOS lstart is five whitespace-separated fields. Keep it in the
        # receipt and compare again after the run to reject process replacement.
        fields = result.stdout.strip().split(None, 5)
        if len(fields) != 6:
            raise RuntimeError("QEMU PID does not have a readable process identity")
        start, command = " ".join(fields[:5]), fields[5]
        argv = shlex.split(command)
        executable = Path(argv[0]).resolve(strict=True)
        if not executable.is_relative_to(args.root / "sdk/emulator/qemu") or "qemu-system-aarch64" not in executable.name:
            raise RuntimeError("QEMU PID is not the specified root's ARM64 emulator")
        try:
            ports = argv[argv.index("-ports") + 1].split(",")
        except (ValueError, IndexError):
            raise RuntimeError("QEMU must expose an explicit -ports pair") from None
        if args.serial != "emulator-" + ports[0]:
            raise RuntimeError("ADB serial does not match QEMU's console port")
        return {"pid": args.qemu_pid, "started": start, "executable": str(executable), "console_port": int(ports[0])}

    identity = qemu_identity()
    adb = [str(args.adb), "-H", "127.0.0.1", "-P", str(args.adb_port), "-s", args.serial]

    def remote(*argv, timeout=30):
        return subprocess.run([*adb, "exec-out", *map(str, argv)], check=True,
                              text=True, capture_output=True, timeout=timeout).stdout.strip()

    if remote("getprop", "ro.product.cpu.abi") != "arm64-v8a":
        raise RuntimeError("selected guest is not arm64-v8a")
    if remote("getprop", "sys.boot_completed") != "1":
        raise RuntimeError("selected guest has not completed boot")
    boot_id = remote("cat", "/proc/sys/kernel/random/boot_id")
    before_hash = remote("sha256sum", DRIVER).split()[0]
    if before_hash != args.expected_driver_sha256:
        raise RuntimeError("guest driver differs from the pinned expected SHA256")
    binary_hash = hashlib.sha256(args.binary.read_bytes()).hexdigest()
    guest_binary = "/data/local/tmp/modavd-gapfix-vulkan-probe-" + binary_hash[:16]
    args.output.mkdir(parents=True, exist_ok=False)
    receipt = {"schema": 1, "utc": datetime.now(timezone.utc).isoformat(), "qemu": identity,
               "adb_server": {"host": "127.0.0.1", "port": args.adb_port}, "serial": args.serial,
               "boot_id": boot_id, "guest_driver": DRIVER, "driver_sha256": before_hash,
               "probe_sha256": binary_hash, "iterations_per_thread": args.iterations,
               "probe_transport": "adb-shell-v2-no-pty"}
    (args.output / "identity.json").write_text(json.dumps(receipt, indent=2) + "\n")
    subprocess.run([*adb, "push", str(args.binary), guest_binary], check=True, timeout=30,
                   stdout=subprocess.DEVNULL)
    remote("chmod", "700", guest_binary)
    if remote("sha256sum", guest_binary).split()[0] != binary_hash:
        raise RuntimeError("pushed probe SHA256 does not match the host binary")
    # Guest-side timeout remains effective even if the ADB client disconnects.
    result = subprocess.run([*adb, "shell", "-T", "timeout", "-s", "KILL", "240", guest_binary, str(args.iterations)],
                            text=True, capture_output=True, timeout=260)
    (args.output / "stdout.json").write_text(result.stdout)
    (args.output / "stderr.txt").write_text(result.stderr)
    receipt["exit_code"] = result.returncode
    receipt["qemu_unchanged"] = qemu_identity() == identity
    receipt["boot_unchanged"] = remote("cat", "/proc/sys/kernel/random/boot_id") == boot_id
    receipt["driver_unchanged"] = remote("sha256sum", DRIVER).split()[0] == before_hash
    try:
        observation = json.loads(result.stdout)
        receipt["semantic_pass"] = observation.get("pass") is True
    except json.JSONDecodeError:
        receipt["semantic_pass"] = False
        receipt["output_is_json"] = False
    else:
        receipt["output_is_json"] = True
    (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))
    if not all(receipt[key] for key in ("qemu_unchanged", "boot_unchanged", "driver_unchanged", "output_is_json")):
        return 2
    return 0 if result.returncode == 0 and receipt["semantic_pass"] else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
