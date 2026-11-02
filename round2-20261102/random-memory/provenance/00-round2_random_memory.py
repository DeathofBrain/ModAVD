#!/usr/bin/env python3
"""Auditable full-random memory round 2 against an already-running clean AVD.

This runner never launches/stops an emulator or an ADB server. The output must
be a new directory. A 16 MiB independently regenerated dump must pass before
the fixed 120 s baseline and three 1 GiB / 60 s / 600 s rounds may begin.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import threading
import time
import traceback
import zlib

from soak_tests import Adb, MemoryMonitor, utc

ROOT = Path(__file__).resolve().parent
REMOTE = "/data/local/tmp/official_avd_probe_round2"
SMOKE_BYTES = 16 * 1024**2
ALLOCATION_BYTES = 1024**3
ALGORITHM = "chacha20-ietf"
METRICS = ("guest_MemAvailable_bytes", "host_phys_footprint_bytes", "host_rss_bytes")


def save_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command_result(command, timeout=30):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    return {"command": list(map(str, command)), "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


class LoggedAdb(Adb):
    """Reuse the shared ADB interface while retaining every command receipt."""
    def __init__(self, args, out):
        super().__init__(args)
        self.prefix = [args.adb, "-H", "127.0.0.1", "-P", str(args.adb_port), "-s", args.serial]
        self.out = Path(out)
        self.adb_port = args.adb_port
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("ANDROID_", "ADB_"))}
        self.env.update(ADB_SERVER_SOCKET=f"tcp:127.0.0.1:{args.adb_port}",
                        ADB_VENDOR_KEYS=str(ROOT / "keys"),
                        ADB_MDNS_AUTO_CONNECT="", ADB_MDNS_OPENSCREEN="0")
        self.lock = threading.Lock()
        self.command_count = 0

    def run(self, args, timeout=30, check=True):
        # ADB normally auto-starts its server. Refuse to issue a command unless
        # the private server is already listening, including during sampling.
        with socket.create_connection(("127.0.0.1", self.adb_port), timeout=2):
            pass
        command = self.prefix + list(map(str, args))
        with self.lock:
            self.command_count += 1
            number = self.command_count
        label = f"adb-{number:05d}"
        began = time.monotonic_ns()
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                                    timeout=timeout, env=self.env)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
            (self.out / f"{label}.stdout").write_bytes(stdout if isinstance(stdout, bytes) else stdout.encode())
            (self.out / f"{label}.stderr").write_bytes(stderr if isinstance(stderr, bytes) else stderr.encode())
            save_json(self.out / f"{label}.json", {"command": command, "timeout": timeout,
                      "status": "timeout", "host_start_monotonic_ns": began,
                      "host_end_monotonic_ns": time.monotonic_ns()})
            raise
        (self.out / f"{label}.stdout").write_text(result.stdout)
        (self.out / f"{label}.stderr").write_text(result.stderr)
        save_json(self.out / f"{label}.json", {"command": command, "returncode": result.returncode,
                  "host_start_monotonic_ns": began, "host_end_monotonic_ns": time.monotonic_ns()})
        if check and result.returncode:
            raise subprocess.CalledProcessError(result.returncode, command, result.stdout, result.stderr)
        return result


class CheckedMemoryMonitor(MemoryMonitor):
    """The shared collector with fail-closed treatment of missing samples."""
    def sample(self, host):
        row = super().sample(host)
        require(not row.get("guest_memory_error") and not row.get("host_memory_error"),
                "Memory sample failed: " + repr(row))
        require(all(row.get(key) is not None for key in METRICS),
                "Memory sample lacks a required metric: " + repr(row))
        require(row["host_disk_free_bytes"] >= 20 * 1024**3,
                "Disk free space fell below 20 GiB")
        return row


def validate_identity(args):
    state = json.loads((ROOT / "state.json").read_text())
    require(state.get("pid") == args.emulator_pid, "Specified PID differs from workspace state.json")
    require(state.get("avd") == "official_clean", "Round 2 requires workspace official_clean")
    require(state.get("serial") == args.serial and state.get("adb_port") == args.adb_port,
            "ADB endpoint differs from workspace emulator state")
    identity = command_result(["ps", "-p", str(args.emulator_pid), "-o", "lstart=", "-o", "command="])
    require(identity["returncode"] == 0 and identity["stdout"].strip() == state.get("identity"),
            "Emulator process start time / command identity no longer matches")
    process = command_result(["ps", "-p", str(args.emulator_pid), "-o", "command="])
    argv = shlex.split(process["stdout"].strip())
    require(argv and Path(argv[0]).resolve().is_relative_to((ROOT / "sdk/emulator/qemu").resolve()),
            "PID is not the workspace SDK QEMU process")
    require(Path(argv[0]).name in ("qemu-system-aarch64", "qemu-system-aarch64-headless"),
            "PID is not an allowed qemu-system-aarch64 executable")
    require("-avd" in argv and argv[argv.index("-avd") + 1] == "official_clean",
            "QEMU command does not select official_clean")
    require("-no-window" in argv, "Round 2 requires the clean emulator's headless launch")
    require(Path(args.adb).resolve() == (ROOT / "sdk/platform-tools/adb").resolve(),
            "ADB binary must belong to this workspace")
    server = json.loads((ROOT / "adb-server.json").read_text())
    require(server.get("port") == args.adb_port and Path(server.get("path", "")).resolve() == Path(args.adb).resolve(),
            "Private ADB server metadata does not match")
    server_process = command_result(["ps", "-p", str(server["pid"]), "-o", "command="])
    server_argv = shlex.split(server_process["stdout"].strip())
    require(server_process["returncode"] == 0 and server_argv and
            Path(server_argv[0]).resolve() == Path(args.adb).resolve(), "Private ADB server PID mismatch")
    with socket.create_connection(("127.0.0.1", args.adb_port), timeout=2):
        pass
    return {"state": state, "ps_identity": identity, "ps_command": process,
            "adb_server": server, "adb_server_process": server_process}


def provenance(args, out, identity):
    directory = out / "provenance"
    directory.mkdir()
    sources = [Path(__file__).resolve(), ROOT / "soak_tests.py", Path(args.source).resolve(),
               ROOT / "probes/build_probe.py", Path(args.probe).resolve(),
               ROOT / "avd/official_clean.avd/config.ini"]
    manifest_path = Path(args.probe).resolve().parent / "build_manifest.json"
    require(manifest_path.is_file(), "Probe build manifest is missing")
    build_manifest = json.loads(manifest_path.read_text())
    require(build_manifest.get("sha256") == sha256(args.probe), "Probe build-manifest SHA-256 differs from binary")
    sources.append(manifest_path)
    files = []
    for index, source in enumerate(sources):
        require(source.is_file(), f"Missing provenance input: {source}")
        copied = directory / f"{index:02d}-{source.name}"
        shutil.copyfile(source, copied)
        files.append({"path": str(source), "snapshot": str(copied.relative_to(out)),
                      "size_bytes": source.stat().st_size, "sha256": sha256(source)})
    host = {}
    for key, command in {
        "uname": ["uname", "-a"], "macos": ["sw_vers"],
        "memory": ["sysctl", "hw.memsize", "hw.pagesize", "hw.ncpu", "machdep.cpu.brand_string"],
        "swap": ["sysctl", "vm.swapusage"], "vm_stat": ["vm_stat"],
        "python": [sys.executable, "--version"], "adb_version": [args.adb, "version"],
    }.items():
        host[key] = command_result(command)
    receipt = {"timestamp_utc": utc(), "files": files, "host": host,
               "identity": identity, "host_disk_free_bytes": shutil.disk_usage(out).free,
               "argv": sys.argv, "arguments": vars(args), "python_executable": sys.executable,
               "method": {"algorithm": ALGORITHM, "smoke_bytes": SMOKE_BYTES,
                          "allocation_bytes": ALLOCATION_BYTES, "baseline_seconds": 120,
                          "hold_seconds": 60, "observe_seconds": 600,
                          "rounds": 3, "sample_interval_seconds": 5}}
    save_json(out / "provenance.json", receipt)
    return receipt


def chacha20_block(key, nonce, counter):
    """Independent RFC 8439 block implementation; no probe code is executed."""
    initial = list(struct.unpack("<4I", b"expand 32-byte k"))
    initial += list(struct.unpack("<8I", key)) + [counter] + list(struct.unpack("<3I", nonce))
    state = initial.copy()

    def quarter(a, b, c, d):
        state[a] = (state[a] + state[b]) & 0xffffffff
        state[d] ^= state[a]; state[d] = ((state[d] << 16) | (state[d] >> 16)) & 0xffffffff
        state[c] = (state[c] + state[d]) & 0xffffffff
        state[b] ^= state[c]; state[b] = ((state[b] << 12) | (state[b] >> 20)) & 0xffffffff
        state[a] = (state[a] + state[b]) & 0xffffffff
        state[d] ^= state[a]; state[d] = ((state[d] << 8) | (state[d] >> 24)) & 0xffffffff
        state[c] = (state[c] + state[d]) & 0xffffffff
        state[b] ^= state[c]; state[b] = ((state[b] << 7) | (state[b] >> 25)) & 0xffffffff

    for _ in range(10):
        quarter(0, 4, 8, 12); quarter(1, 5, 9, 13)
        quarter(2, 6, 10, 14); quarter(3, 7, 11, 15)
        quarter(0, 5, 10, 15); quarter(1, 6, 11, 12)
        quarter(2, 7, 8, 13); quarter(3, 4, 9, 14)
    return struct.pack("<16I", *((a + b) & 0xffffffff for a, b in zip(initial, state)))


def reference_self_check():
    # RFC 8439 section 2.3.2. This catches host reference endian/counter errors.
    expected = bytes.fromhex(
        "10f1e7e4d13b5915500fdd1fa32071c4c7d1f4c733c068030422aa9ac3d46c4e"
        "d2826446079faa0914c2d705d98b02a2b5129cd1de164eb9cbd083e8a2503c4e")
    actual = chacha20_block(bytes(range(32)), bytes.fromhex("000000090000004a00000000"), 1)
    require(actual == expected, "Independent Python ChaCha20 RFC self-check failed")


def verify_smoke_dump(path, protocol):
    began = time.monotonic()
    data = path.read_bytes()
    require(len(data) == SMOKE_BYTES, f"Smoke dump has {len(data)} bytes, expected {SMOKE_BYTES}")
    key = bytes.fromhex(protocol["seed_hex"])
    nonce = bytes.fromhex(protocol["nonce_hex"])
    expected_digest = hashlib.sha256()
    for offset in range(0, len(data), 64):
        expected = chacha20_block(key, nonce, protocol["initial_counter"] + offset // 64)
        actual = data[offset:offset + 64]
        require(actual == expected[:len(actual)], f"Independent random content mismatch at offset {offset}")
        expected_digest.update(expected[:len(actual)])
    compression_bytes = len(zlib.compress(data, level=9))
    ratio = compression_bytes / len(data)
    page_hashes = [hashlib.sha256(data[i:i + 4096]).hexdigest() for i in range(0, len(data), 4096)]
    require(ratio >= 0.99, f"Smoke data compresses unexpectedly: ratio={ratio}")
    require(len(set(page_hashes)) == len(page_hashes), "Duplicate 4 KiB page in smoke data")
    return {"status": "passed", "independent_implementation": "Python RFC 8439 ChaCha20, RFC block-vector self-check",
            "bytes_compared": len(data), "dump_sha256": hashlib.sha256(data).hexdigest(),
            "expected_sha256": expected_digest.hexdigest(), "zlib_level": 9,
            "zlib_compressed_bytes": compression_bytes, "compression_ratio": ratio,
            "compression_ratio_minimum": 0.99, "page_bytes": 4096,
            "page_count": len(page_hashes), "unique_page_sha256_count": len(set(page_hashes)),
            "page_sha256": page_hashes, "verification_elapsed_seconds": time.monotonic() - began}


def run_payload(adb, out, label, arguments, timeout, monitor=None):
    """Stream raw output and host receive anchors; keep partial logs on errors."""
    with socket.create_connection(("127.0.0.1", adb.adb_port), timeout=2):
        pass
    command = adb.prefix + ["shell", REMOTE, *map(str, arguments)]
    receipt = {"command": command, "started_utc": utc(),
               "host_start_monotonic_ns": time.monotonic_ns(), "timeout_seconds": timeout}
    save_json(out / f"{label}.execution.json", receipt)
    anchors = []
    reader_failures = []
    with (out / f"{label}.stdout.jsonl").open("wb") as stdout_file, \
         (out / f"{label}.stderr.log").open("wb") as stderr_file, \
         (out / f"{label}.host-events.jsonl").open("w") as anchors_file:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_file, env=adb.env)

        def read_stdout():
            try:
                for line in iter(process.stdout.readline, b""):
                    timestamp = time.monotonic_ns()
                    stdout_file.write(line); stdout_file.flush()
                    anchor = {"host_received_utc": utc(), "host_received_monotonic_ns": timestamp,
                              "line": line.decode("utf-8", "replace").rstrip("\r\n")}
                    anchors.append(anchor)
                    anchors_file.write(json.dumps(anchor) + "\n"); anchors_file.flush()
            except BaseException as exc:
                reader_failures.append(repr(exc))

        reader = threading.Thread(target=read_stdout, name=f"{label}-stdout", daemon=True)
        reader.start()
        try:
            deadline = time.monotonic() + timeout
            while process.poll() is None:
                if monitor:
                    monitor.raise_if_failed()
                require(time.monotonic() < deadline, f"{label} exceeded {timeout} seconds")
                time.sleep(0.2)
        except BaseException:
            # Only the ADB client created by this function is terminated. The
            # runner never sends kill/stop commands to any guest or emulator.
            process.kill(); process.wait(timeout=10)
            raise
        finally:
            reader.join(timeout=10)
            receipt.update(finished_utc=utc(), host_end_monotonic_ns=time.monotonic_ns(),
                           returncode=process.poll(), reader_failures=reader_failures,
                           reader_stopped=not reader.is_alive(), received_event_lines=len(anchors))
            save_json(out / f"{label}.execution.json", receipt)
        require(not reader.is_alive() and not reader_failures, f"{label} stdout reader failed")
    require(receipt["returncode"] == 0, f"{label} exited {receipt['returncode']}; raw logs retained")
    return anchors, receipt


def parse_protocol(anchors, expected_bytes, hold_seconds, dump_expected=False):
    """The accepted JSON schema is intentionally exact for this test version."""
    events = []
    for number, anchor in enumerate(anchors, 1):
        try:
            event = json.loads(anchor["line"])
        except (ValueError, TypeError) as exc:
            raise RuntimeError(f"Invalid JSON on probe line {number}") from exc
        require(isinstance(event, dict), f"Probe line {number} is not a JSON object")
        event["host_received_monotonic_ns"] = anchor["host_received_monotonic_ns"]
        events.append(event)
    return validate_random_events(events, expected_bytes, hold_seconds, dump_expected)


def validate_random_events(events, expected_bytes, hold_seconds, dump_expected):
    expected = ["start", "filled", "verified"]
    if dump_expected:
        expected.append("dumped")
    expected += ["hold_start", "released"]
    names = [event.get("event") for event in events]
    require(names == ["random_memory_" + name for name in expected],
            f"Unexpected random-memory event sequence: {names!r}")
    seed = events[0].get("seed_hex")
    require(isinstance(seed, str) and re.fullmatch(r"[0-9a-f]{64}", seed) is not None,
            "Missing or malformed 32-byte random seed")
    previous = -1
    for event, kind in zip(events, expected):
        require(event.get("algorithm") == ALGORITHM, "Probe algorithm differs from expected ChaCha20-IETF")
        require(event.get("seed_hex") == seed, "Probe seed changed within one allocation")
        require(event.get("nonce_hex") == "000000000000000000000000", "Probe nonce is not the specified 12 zero bytes")
        require(type(event.get("initial_counter")) is int and event["initial_counter"] == 0,
                "Probe stream does not start at counter 0")
        require(type(event.get("bytes")) is int and event["bytes"] == expected_bytes,
                f"{kind} allocation byte count differs")
        timestamp = event.get("monotonic_ns")
        require(type(timestamp) is int and timestamp > previous, "Probe event time is missing or non-increasing")
        previous = timestamp
        verified = 0 if kind in ("start", "filled") else expected_bytes
        require(type(event.get("verified_bytes")) is int and event["verified_bytes"] == verified,
                f"{kind} does not confirm the expected full read-verification count")
        filled = 0 if kind == "start" else expected_bytes
        require(type(event.get("filled_bytes")) is int and event["filled_bytes"] == filled,
                f"{kind} does not confirm the expected full write count")
    require(events[0].get("hold_seconds") == hold_seconds,
            "Probe did not accept the requested hold duration")
    require(events[0].get("seed_source") == "getrandom",
            "Measured probe must use a fresh guest getrandom seed")
    released = events[-1]
    require(type(released.get("munmap_returncode")) is int and released["munmap_returncode"] == 0,
            "Probe did not explicitly confirm successful munmap")
    if dump_expected:
        dump = events[3]
        require(dump.get("dumped_bytes") == expected_bytes, "Smoke dump byte count differs")
        require(dump.get("dump_path") == dump_expected, "Smoke dump path differs from requested path")
    hold_start = events[-2]
    duration = (released["monotonic_ns"] - hold_start["monotonic_ns"]) / 1e9
    require(hold_seconds - 0.01 <= duration <= hold_seconds + max(5, hold_seconds * .2),
            f"Probe hold duration is outside tolerance: {duration} seconds")
    # Each receipt is guest time plus host/guest clock offset plus nonnegative
    # transport delay. The minimum observed offset is the least delayed anchor,
    # not a proven exact clock offset. Exclude a one-second guard at both ends.
    offsets = [event["host_received_monotonic_ns"] - event["monotonic_ns"] for event in events]
    offset = min(offsets)
    guard = 1_000_000_000
    return {"valid": True, "algorithm": ALGORITHM, "seed_hex": seed,
            "nonce_hex": events[0]["nonce_hex"], "initial_counter": 0,
            "allocation_bytes": expected_bytes, "hold_duration_seconds": duration,
            "events": events, "hold_start_host_estimate_ns": hold_start["monotonic_ns"] + offset + guard,
            "hold_end_host_estimate_ns": released["monotonic_ns"] + offset - guard,
            "clock_mapping": {"method": "minimum(host receive monotonic ns minus guest event monotonic ns)",
                              "offset_ns": offset, "observed_offset_spread_ns": max(offsets) - min(offsets),
                              "hold_endpoint_guard_ns": guard,
                              "limitation": "One-way event transport has unknown absolute delay. Host hold bounds are estimates with a 1-second endpoint guard; guest hold duration itself uses only the guest monotonic clock."}}


def median_metric(rows, key):
    values = [int(row[key]) for row in rows if row.get(key)]
    return statistics.median(values) if values else None


def summarize(out, progress):
    source = out / "memory.csv"
    rows = list(csv.DictReader(source.open())) if source.exists() else []
    origin = progress.get("monitor_start_monotonic_ns")
    rounds = []
    for entry in progress["rounds"]:
        result = {"round": entry["round"], "protocol_valid": entry.get("protocol_valid", False),
                  "observation_completed": entry.get("observation_completed", False)}
        timing = entry.get("timing")
        if timing and origin:
            baseline_rows = [r for r in rows if timing["baseline_start_ns"] <= origin + round(float(r["elapsed_seconds"]) * 1e9) < timing["baseline_end_ns"]]
            hold_rows = [r for r in rows if timing["hold_start_host_estimate_ns"] <= origin + round(float(r["elapsed_seconds"]) * 1e9) and
                         origin + round((float(r["elapsed_seconds"]) + float(r["sample_duration_ms"]) / 1000) * 1e9) <= timing["hold_end_host_estimate_ns"]]
            released_rows = [r for r in rows if timing["observe_start_ns"] <= origin + round(float(r["elapsed_seconds"]) * 1e9) <= timing.get("observe_end_ns", time.monotonic_ns())]
            baseline_rows = baseline_rows[-12:]
            hold_rows = hold_rows[-3:]
            released_rows = released_rows[-12:]
            result["sample_counts"] = {"baseline_last_12": len(baseline_rows), "hold_last_3": len(hold_rows), "release_last_12": len(released_rows)}
            result["metrics"] = {}
            for key in METRICS:
                baseline = median_metric(baseline_rows, key)
                held = median_metric(hold_rows, key)
                released = median_metric(released_rows, key)
                metric = {"baseline_median_bytes": baseline, "hold_median_last_3_bytes": held,
                          "release_median_last_12_bytes": released,
                          "hold_minus_baseline_bytes": None if baseline is None or held is None else held - baseline,
                          "release_minus_hold_bytes": None if held is None or released is None else released - held,
                          "release_minus_baseline_bytes": None if baseline is None or released is None else released - baseline,
                          "reclaimed_fraction": None}
                if baseline is not None and held is not None and released is not None:
                    allocation_increment = baseline - held if key.startswith("guest_") else held - baseline
                    # Predeclared threshold avoids unstable percentages for
                    # negligible allocations / host accounting variation.
                    threshold = max(128 * 1024**2, ALLOCATION_BYTES * 0.1)
                    metric["allocation_increment_bytes"] = allocation_increment
                    metric["reclamation_fraction_minimum_increment_bytes"] = threshold
                    if allocation_increment >= threshold and entry.get("observation_completed"):
                        reclaimed = released - held if key.startswith("guest_") else held - released
                        metric["reclaimed_fraction"] = reclaimed / allocation_increment
                    else:
                        metric["reclaimed_fraction_reason"] = "allocation increment below declared threshold, or observation incomplete"
                result["metrics"][key] = metric
            result["timing"] = timing
        rounds.append(result)
    summary = {"status": progress["status"], "updated_utc": utc(), "sample_count": len(rows),
               "host_error_samples": sum(bool(r.get("host_memory_error")) for r in rows),
               "guest_error_samples": sum(bool(r.get("guest_memory_error")) for r in rows),
               "rounds": rounds, "completed_rounds": sum(bool(r.get("observation_completed")) for r in progress["rounds"]),
               "time_semantics": "CSV elapsed_seconds is sample start relative to monitor start; sample_duration_ms includes host and guest reads. CSV phase combines allocate/fill/verify/hold. Metric hold samples are restricted to the guest-event-derived hold interval with host receive anchors; observation begins after the ADB client exits with validated munmap. Each later baseline uses the preceding observation's last 60 seconds.",
               "metric_scope": "phys_footprint is primary emulator-process memory; RSS is supplemental. Host compressor and swap in CSV describe the entire host, not solely this emulator.",
               "reclamation_note": "Successful protocol completion does not imply host reclamation. Fractions are raw, unclamped, and null unless the allocation increment is at least max(128 MiB, 10% of allocation) and the full observation completed."}
    save_json(out / "summary.json", summary)
    return summary


def update_progress(out, progress, **changes):
    progress.update(changes, updated_utc=utc())
    save_json(out / "progress.json", progress)
    summarize(out, progress)
    print(json.dumps({"updated_utc": progress["updated_utc"], "status": progress["status"],
                      "stage": progress.get("stage"), "completed_rounds": sum(bool(r.get("observation_completed")) for r in progress["rounds"])}), flush=True)


def monitor_wait(monitor, seconds, args, validate_every=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        monitor.wait(min(validate_every, deadline - time.monotonic()))
        validate_identity(args)
        require(shutil.disk_usage(monitor.out).free >= 20 * 1024**3, "Disk free space fell below 20 GiB")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=str(ROOT / "sdk/platform-tools/adb"))
    parser.add_argument("--adb-port", type=int, default=22150)
    parser.add_argument("--serial", default="emulator-22160")
    parser.add_argument("--emulator-pid", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe", default=str(ROOT / "probes/build-round2-random/avd_probe"))
    parser.add_argument("--source", default=str(ROOT / "probes/avd_probe.c"))
    parser.add_argument("--smoke-only", action="store_true")
    args = parser.parse_args()
    args.sample_interval = 5
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    began = time.monotonic()
    progress = {"status": "running", "stage": "preflight", "started_utc": utc(), "rounds": [], "smoke_only": args.smoke_only}
    update_progress(out, progress)
    try:
        reference_self_check()
        identity = validate_identity(args)
        require(shutil.disk_usage(out).free >= 20 * 1024**3, "Need at least 20 GiB host disk free")
        provenance(args, out, identity)
        adb_logs = out / "adb-logs"
        adb_logs.mkdir()
        adb = LoggedAdb(args, adb_logs)
        require(adb.run(["get-state"]).stdout.strip() == "device", "ADB target is not online")
        require(adb.run(["shell", "getprop", "sys.boot_completed"]).stdout.strip() == "1", "Guest boot is incomplete")
        guest = {}
        for key, command in {"properties": ["shell", "getprop"], "kernel": ["shell", "uname", "-a"],
                             "meminfo": ["shell", "cat", "/proc/meminfo"],
                             "page_size": ["shell", "getconf", "PAGESIZE"],
                             "disk_free": ["shell", "df", "-k", "/data/local/tmp"]}.items():
            result = adb.run(command)
            guest[key] = {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
        save_json(out / "guest-environment.json", guest)
        adb.run(["push", str(Path(args.probe).resolve()), REMOTE], timeout=60)
        adb.run(["shell", "chmod", "755", REMOTE])
        remote_sha = adb.run(["shell", "sha256sum", REMOTE]).stdout.split()[0]
        require(remote_sha == sha256(args.probe), "Deployed probe SHA-256 differs from local binary")
        save_json(out / "deployment.json", {"remote": REMOTE, "sha256": remote_sha, "verified_utc": utc()})
        dump_remote = f"/data/local/tmp/official_avd_round2_smoke_{time.time_ns()}.bin"
        update_progress(out, progress, stage="smoke_probe")
        anchors, execution = run_payload(adb, out, "smoke", ["memory-random", SMOKE_BYTES, 1, dump_remote], timeout=180)
        smoke = parse_protocol(anchors, SMOKE_BYTES, 1, dump_expected=dump_remote)
        save_json(out / "smoke.protocol.json", smoke)
        dump_local = out / "smoke-16MiB.bin"
        adb.run(["pull", dump_remote, str(dump_local)], timeout=120)
        update_progress(out, progress, stage="smoke_independent_verification")
        receipt = verify_smoke_dump(dump_local, smoke)
        receipt.update(seed_hex=smoke["seed_hex"], nonce_hex=smoke["nonce_hex"], algorithm=ALGORITHM,
                       local_dump=str(dump_local), remote_dump=dump_remote)
        save_json(out / "smoke-verification.json", receipt)
        progress["smoke_passed"] = True
        if args.smoke_only:
            update_progress(out, progress, status="smoke_passed", stage="finished")
            return
        seeds = {smoke["seed_hex"]}
        with CheckedMemoryMonitor(args, out, adb) as monitor:
            progress["monitor_start_monotonic_ns"] = round(monitor.start * 1e9)
            baseline_start = time.monotonic_ns()
            update_progress(out, progress, stage="baseline_120s")
            monitor_wait(monitor, 120, args)
            for number in range(1, 4):
                validate_identity(args)
                entry = {"round": number, "started_utc": utc(), "protocol_valid": False,
                         "observation_completed": False}
                progress["rounds"].append(entry)
                update_progress(out, progress, stage=f"round_{number}_allocate_fill_verify_hold")
                baseline_end = time.monotonic_ns()
                monitor.set_phase(f"round_{number}_allocate_hold")
                anchors, execution = run_payload(adb, out, f"round_{number}",
                                                 ["memory-random", ALLOCATION_BYTES, 60],
                                                 timeout=360, monitor=monitor)
                protocol = parse_protocol(anchors, ALLOCATION_BYTES, 60)
                require(protocol["seed_hex"] not in seeds, "Duplicate seed across smoke or measured rounds")
                seeds.add(protocol["seed_hex"])
                save_json(out / f"round_{number}.protocol.json", protocol)
                observe_start = time.monotonic_ns()
                entry.update(protocol_valid=True, seed_hex=protocol["seed_hex"],
                             timing={"baseline_start_ns": baseline_start, "baseline_end_ns": baseline_end,
                                     "hold_start_host_estimate_ns": protocol["hold_start_host_estimate_ns"],
                                     "hold_end_host_estimate_ns": protocol["hold_end_host_estimate_ns"],
                                     "observe_start_ns": observe_start,
                                     "guest_hold_duration_seconds": protocol["hold_duration_seconds"],
                                     "clock_mapping": protocol["clock_mapping"]})
                monitor.set_phase(f"round_{number}_released_observe")
                update_progress(out, progress, stage=f"round_{number}_observe_600s")
                try:
                    monitor_wait(monitor, 600, args)
                    entry["observation_completed"] = True
                finally:
                    entry["timing"]["observe_end_ns"] = time.monotonic_ns()
                    entry["actual_observe_seconds"] = (entry["timing"]["observe_end_ns"] - observe_start) / 1e9
                    update_progress(out, progress)
                current = summarize(out, progress)["rounds"][-1]
                counts = current.get("sample_counts", {})
                require(counts.get("baseline_last_12") == 12 and counts.get("hold_last_3") == 3
                        and counts.get("release_last_12") == 12,
                        f"Round {number} lacks required metric window coverage: {counts}")
                require(current["metrics"] and all(
                    metric["baseline_median_bytes"] is not None and metric["hold_median_last_3_bytes"] is not None
                    and metric["release_median_last_12_bytes"] is not None
                    for metric in current["metrics"].values()),
                    f"Round {number} metric window contains missing values")
                # The next round's baseline is the stable final minute of the
                # preceding complete 600-second observation.
                baseline_start = max(observe_start, time.monotonic_ns() - 60_000_000_000)
        update_progress(out, progress, status="completed", stage="finished")
    except BaseException as exc:
        progress.update(error=repr(exc), traceback=traceback.format_exc())
        update_progress(out, progress, status="partial", stage=progress.get("stage"))
        raise
    finally:
        progress.update(finished_utc=utc(), elapsed_seconds=time.monotonic() - began)
        save_json(out / "progress.json", progress)
        summarize(out, progress)


if __name__ == "__main__":
    main()
