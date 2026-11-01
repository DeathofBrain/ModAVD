#!/usr/bin/env python3
"""Bounded private-guest memory check with independent Darwin process samples."""
import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import threading
import time
import uuid


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--churn-mib", type=int, choices=(128, 256, 512), default=256)
    parser.add_argument("--rounds", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--release-seconds", type=int, choices=range(2, 16), default=8)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    root = here.parents[2]
    private = module("private_runtime", here / "run-private-runtime.py")
    usage = module("host_rusage", root / "ports/macos/qa/host_rusage.py")
    runtime = private.runtime.Runtime(root / ".macos-work/reclaim-runtime")
    state = json.loads((runtime.root / "emulator.json").read_text())
    if state["pid"] != args.expected_pid or not private.runtime.same_process(state, runtime.name()):
        raise RuntimeError("private emulator process identity mismatch")
    serial = runtime.owned_device()
    initial_usage = usage.snapshot(args.expected_pid)
    boot_id = runtime.adb("-s", serial, "shell", "cat", "/proc/sys/kernel/random/boot_id").stdout.strip()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("refusing to overwrite existing evidence")
    output.mkdir(parents=True)
    guest_dir = "/data/local/tmp/modavd-reclaim-" + uuid.uuid4().hex
    guest_binary = guest_dir + "/probe"
    binary = args.binary.resolve(strict=True)
    log = runtime.root / "logs/emulator.log"
    log_offset = log.stat().st_size
    events, samples = [], [initial_usage]
    started = time.monotonic()
    process = None
    cleanup = False
    try:
        runtime.adb("-s", serial, "shell", "mkdir", "-m", "700", guest_dir)
        runtime.adb("-s", serial, "push", str(binary), guest_binary)
        runtime.adb("-s", serial, "shell", "chmod", "700", guest_binary)
        _, platform = runtime.component("platform-tools")
        argv = [str(platform / "adb"), "-P", "20150", "-s", serial, "shell",
                guest_binary, str(args.churn_mib), str(args.rounds), str(args.release_seconds)]
        process = subprocess.Popen(argv, env=runtime.env(), text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)

        def collect():
            for line in process.stdout:
                events.append({"host_monotonic_ns": time.monotonic_ns(), "line": line.rstrip("\n")})

        reader = threading.Thread(target=collect)
        reader.start()
        while process.poll() is None:
            if time.monotonic() - started > 120:
                process.terminate()
                raise RuntimeError("bounded memory probe timed out")
            sample = usage.snapshot(args.expected_pid)
            if sample["process_start_abstime"] != initial_usage["process_start_abstime"]:
                raise RuntimeError("host process identity changed")
            samples.append(sample)
            time.sleep(0.25)
        reader.join(timeout=5)
        if reader.is_alive():
            raise RuntimeError("probe output reader did not finish")
        samples.append(usage.snapshot(args.expected_pid))
        final_boot = runtime.adb("-s", serial, "shell", "cat", "/proc/sys/kernel/random/boot_id").stdout.strip()
        if final_boot != boot_id:
            raise RuntimeError("guest rebooted during probe")
        parsed = [json.loads(event["line"]) for event in events if event["line"].startswith("{")]
        passed = process.returncode == 0 and any(e.get("event") == "done" and e.get("pass") is True for e in parsed)
    finally:
        if process and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        # Delete only the exact UUID-scoped paths created by this invocation.
        if private.runtime.same_process(state, runtime.name()):
            runtime.adb("-s", serial, "shell", "rm", "-f", guest_binary)
            runtime.adb("-s", serial, "shell", "rmdir", guest_dir)
            cleanup = True
        (output / "probe.jsonl").write_text("\n".join(e["line"] for e in events) + "\n")
        (output / "host-samples.json").write_text(json.dumps(samples, indent=2) + "\n")
        (output / "emulator-segment.log").write_bytes(log.read_bytes()[log_offset:])
    record = {"captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "pid": args.expected_pid, "process_start_abstime": initial_usage["process_start_abstime"],
              "boot_id": boot_id, "serial": serial, "adb_port": 20150,
              "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "churn_mib": args.churn_mib, "rounds": args.rounds,
              "release_seconds": args.release_seconds, "probe_exit": process.returncode,
              "pass": passed, "guest_directory_cleaned": cleanup,
              "elapsed_seconds": time.monotonic() - started, "timed_events": events,
              "interpretation": "RSS/footprint are observations; cumulative commits alone do not prove physical benefit."}
    (output / "capture.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({k: record[k] for k in ("pid", "pass", "probe_exit", "elapsed_seconds", "guest_directory_cleaned")}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
