"""Durability and official data-partition growth tests for a task-owned AVD.

All emulator/ADB invocations go through avdlib so commands and both output
streams remain available as evidence.  Nothing here uses SIGKILL, wipes data,
or edits a disk image with qemu-img.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shlex
import time
import traceback

from avdlib import Runtime, save_json


GUEST = "/data/local/tmp/official-baseline"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def _persist(rt, name, report):
    save_json(rt.results / f"{name}.json", report)


def _begin(rt, name, report):
    previous = rt.results / f"{name}.json"
    if previous.exists():
        history = rt.results / "history"
        history.mkdir(exist_ok=True)
        archive = history / f"{name}-{time.time_ns()}.json"
        archive.write_bytes(previous.read_bytes())
        report["previous_report_preserved"] = str(archive)
    _persist(rt, name, report)


def _boot(rt, extra_args=None, avd_name=None):
    if not rt.owned():
        rt.start(extra_args=extra_args, avd_name=avd_name)
    return rt.wait_boot()


def _pull_status(rt, files, label):
    """One guest read command; do not treat missing and empty as equivalent."""
    commands = []
    for filename in files:
        q = shlex.quote(filename)
        commands.append(
            f"if [ -e {q} ]; then printf 'FILE %s ' {q}; "
            f"stat -c %s {q} | tr '\\n' ' '; sha256sum {q}; "
            f"else printf 'MISSING %s\\n' {q}; fi"
        )
    result = rt.shell("\n".join(commands), label=label, timeout=120)
    statuses = {}
    for line in result.stdout.splitlines():
        if line.startswith("MISSING "):
            statuses[line[8:]] = {"state": "missing"}
        elif line.startswith("FILE "):
            parts = line.split()
            if len(parts) >= 4 and parts[2].isdigit():
                statuses[parts[1]] = {"size": int(parts[2]), "sha256": parts[3]}
    for filename in files:
        if filename not in statuses:
            statuses[filename] = {"state": "unreadable", "log": result.log}
    return statuses, result.log


def _classify(actual, new_hash, new_size, old_hash=None):
    if actual.get("state"):
        return actual["state"]
    if actual["size"] == 0:
        return "zero"
    if actual["sha256"] == new_hash and actual["size"] == new_size:
        return "new"
    if old_hash and actual["sha256"] == old_hash:
        return "old"
    return "corrupt"


def sync_shutdown(rt, rounds=10):
    report = {
        "test": "sync_then_emu_kill", "status": "running", "requested_rounds": rounds,
        "shutdown_semantics": "adb shell sync, then adb emu kill; orderly emulator console exit, not SIGKILL",
        "avd": rt.avd_name, "rounds": [], "started_unix": time.time(),
    }
    _begin(rt, "sync_shutdown", report)
    for index in range(1, rounds + 1):
        row = {"round": index, "status": "running"}
        report["rounds"].append(row)
        try:
            row["boot_before_s"] = _boot(rt)
            data = hashlib.sha256(f"sync-{index}-{time.time_ns()}".encode()).digest() * 32768
            expected = {"size": len(data), "sha256": digest(data)}
            host = rt.results / f"sync-payload-{index:02d}.bin"
            host.write_bytes(data)
            guest = f"{GUEST}/sync/round-{index:02d}.bin"
            rt.shell(f"mkdir -p {GUEST}/sync", label="sync-mkdir")
            pushed = rt.adb(["push", host, guest], label=f"sync-push-{index}")
            row.update({"file": guest, "expected": expected, "push_log": pushed.log})
            synced = rt.shell("sync", label=f"sync-before-kill-{index}", timeout=120)
            row["sync_log"] = synced.log
            row["shutdown"] = rt.stop(sync=False)
            row["boot_after_s"] = _boot(rt)
            actual, log = _pull_status(rt, [guest], f"sync-verify-{index}")
            row.update({"actual": actual[guest], "verify_log": log})
            row["status"] = "passed" if actual[guest] == expected else "failed"
        except Exception as exc:
            row.update({"status": "blocked" if "disk space" in str(exc) else "failed",
                        "error": str(exc), "traceback": traceback.format_exc()})
        _persist(rt, "sync_shutdown", report)
        print(json.dumps({"suite": "sync_shutdown", **row}, ensure_ascii=False), flush=True)
        if row.get("status") == "blocked":
            break
    report["completed_rounds"] = len(report["rounds"])
    report["passed_rounds"] = sum(r["status"] == "passed" for r in report["rounds"])
    report["status"] = ("passed" if report["passed_rounds"] == rounds else
                        "blocked" if any(r["status"] == "blocked" for r in report["rounds"]) else "failed")
    report["finished_unix"] = time.time()
    _persist(rt, "sync_shutdown", report)
    return report


def no_sync_shutdown(rt, rounds=10, delays_ms=(0, 100, 1000), files_per_kind=16,
                     avd_name="official_nosync"):
    report = {
        "test": "closed_writes_then_emu_kill_without_sync", "status": "running",
        "requested_rounds_per_delay": rounds, "delays_ms": list(delays_ms),
        "files_per_kind_per_round": files_per_kind, "bytes_per_file": 65536,
        "shutdown_semantics": "adb emu kill is orderly emulator console exit, not host SIGKILL or power loss",
        "timing_semantics": "Delay starts when host receives the final CLOSED marker; actual file close precedes that by ADB transport latency. Host elapsed time to kill invocation is measured; exact guest-close-to-kill latency is not claimed.",
        "workload": "Synthetic create + overwrite of small files. Does not establish actual game-cache durability.",
        "avd": avd_name, "rounds": [], "started_unix": time.time(),
    }
    _begin(rt, "no_sync_shutdown", report)
    source_name = rt.avd_name
    try:
        rt.stop(sync=True)
        destination = rt.avd_root / f"{avd_name}.avd"
        if destination.exists():
            raise RuntimeError(f"disposable AVD already exists; preserving it: {destination}")
        rt.clone_stopped(source_name, avd_name)
        _boot(rt, avd_name=avd_name)
        rt.shell(f"mkdir -p {GUEST}/nosync", label="nosync-mkdir")
        for delay in delays_ms:
            for index in range(1, rounds + 1):
                row = {"delay_requested_ms": delay, "round": index, "status": "running"}
                report["rounds"].append(row)
                try:
                    row["boot_before_s"] = _boot(rt)
                    key = f"{delay:04d}-{index:02d}"
                    new_data = hashlib.sha256(f"new-{key}".encode()).digest() * 2048
                    old_data = hashlib.sha256(f"old-{key}".encode()).digest() * 2048
                    new_host = rt.results / "nosync-new.bin"
                    old_host = rt.results / "nosync-old.bin"
                    new_host.write_bytes(new_data)
                    old_host.write_bytes(old_data)
                    rt.adb(["push", new_host, f"{GUEST}/nosync/new-source.bin"], label=f"nosync-new-{key}")
                    rt.adb(["push", old_host, f"{GUEST}/nosync/old-source.bin"], label=f"nosync-old-{key}")
                    folder = f"{GUEST}/nosync/{key}"
                    created = [f"{folder}/create-{n:02d}.bin" for n in range(files_per_kind)]
                    overwritten = [f"{folder}/overwrite-{n:02d}.bin" for n in range(files_per_kind)]
                    prep = [f"mkdir -p {folder}"]
                    prep += [f"cat {GUEST}/nosync/old-source.bin > {f}" for f in overwritten]
                    prep.append("sync")
                    rt.shell("set -e\n" + "\n".join(prep), label=f"nosync-seed-sync-{key}", timeout=120)
                    # Each shell redirection closes before the next command. No fsync/sync is requested.
                    commands = [f"cat {GUEST}/nosync/new-source.bin > {f}" for f in created + overwritten]
                    commands.append("printf 'CLOSED '; cat /proc/uptime")
                    written = rt.shell("set -e\n" + "\n".join(commands), label=f"nosync-write-close-{key}", timeout=120)
                    closed_received = time.monotonic()
                    if "CLOSED " not in written.stdout:
                        raise RuntimeError("workload did not return final file-close marker")
                    if delay:
                        time.sleep(delay / 1000)
                    kill_invoked = time.monotonic()
                    shutdown = rt.stop(sync=False)
                    row.update({"workload_log": written.log, "workload_duration_s": written.duration,
                                "guest_close_marker": written.stdout.strip(),
                                "marker_received_to_stop_call_ms": (kill_invoked - closed_received) * 1000,
                                "shutdown": shutdown})
                    if "kill_started_monotonic" in shutdown:
                        row["marker_received_to_emu_kill_invocation_ms"] = (
                            shutdown["kill_started_monotonic"] - closed_received) * 1000
                        row["marker_received_to_emu_kill_completion_ms"] = (
                            shutdown["kill_finished_monotonic"] - closed_received) * 1000
                    row["boot_after_s"] = _boot(rt)
                    actual, log = _pull_status(rt, created + overwritten, f"nosync-verify-{key}")
                    for filename, state in actual.items():
                        kind = "create" if filename in created else "overwrite"
                        state["kind"] = kind
                        state["classification"] = _classify(state, digest(new_data), len(new_data),
                                                            digest(old_data) if kind == "overwrite" else None)
                    counts = Counter(v["classification"] for v in actual.values())
                    row.update({"files": actual, "counts": dict(counts), "verify_log": log,
                                "new_sha256": digest(new_data), "old_sha256": digest(old_data),
                                "status": "passed" if counts["new"] == len(actual) else "failed"})
                except Exception as exc:
                    row.update({"status": "blocked" if "disk space" in str(exc) else "failed",
                                "error": str(exc), "traceback": traceback.format_exc()})
                _persist(rt, "no_sync_shutdown", report)
                print(json.dumps({"suite": "no_sync_shutdown", "delay_ms": delay,
                                  "round": index, "status": row["status"],
                                  "counts": row.get("counts"), "error": row.get("error")}, ensure_ascii=False), flush=True)
                if row["status"] == "blocked":
                    break
            if report["rounds"][-1]["status"] == "blocked":
                break
    except Exception as exc:
        report["setup_error"] = str(exc)
        report["status"] = "blocked"
    finally:
        if rt.avd_name == avd_name:
            try:
                rt.stop(sync=True)
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        rt.avd_name = source_name
    counts = Counter()
    for row in report["rounds"]:
        counts.update(row.get("counts", {}))
    report["counts"] = dict(counts)
    report["completed_rounds"] = len(report["rounds"])
    if report["status"] == "running":
        report["status"] = ("passed" if len(report["rounds"]) == rounds * len(delays_ms)
                            and all(r["status"] == "passed" for r in report["rounds"]) else "failed")
    report["finished_unix"] = time.time()
    _persist(rt, "no_sync_shutdown", report)
    return report


def _df(rt, label):
    result = rt.shell("df -k /data; stat -f -c '%T %s %b %a' /data", label=label)
    data_rows = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 6 and all(p.isdigit() for p in parts[1:4]) and parts[-1].startswith("/"):
            data_rows.append(parts)
    if len(data_rows) != 1:
        raise RuntimeError(f"could not parse guest df; log={result.log}")
    return {"total_bytes": int(data_rows[0][1]) * 1024, "reported_mount": data_rows[0][-1],
            "stdout": result.stdout, "log": result.log}


def partition_expansion(rt, clone_name="official_expansion", backup_name="official_expansion_backup"):
    report = {
        "test": "official_partition_size_8_to_32_gib", "status": "running",
        "method": "APFS cp -cR of stopped AVD; preserve an original backup; launch clone with official -partition-size 32768; no wipe-data, qemu-img resize, or manual filesystem resize",
        "source_avd": rt.avd_name, "clone_avd": clone_name, "backup_avd": backup_name,
        "started_unix": time.time(),
    }
    _begin(rt, "partition_expansion", report)
    source_name = rt.avd_name
    try:
        _boot(rt)
        marker = rt.results / "expansion-marker.bin"
        marker.write_bytes(hashlib.sha256(f"expansion-{time.time_ns()}".encode()).digest() * 32768)
        guest = f"{GUEST}/expansion-marker.bin"
        rt.shell(f"mkdir -p {GUEST}", label="expansion-mkdir")
        rt.adb(["push", marker, guest], label="expansion-marker-push")
        rt.shell("sync", label="expansion-marker-sync")
        report["before"] = _df(rt, "expansion-df-before")
        report["before_files"], _ = _pull_status(rt, [guest], "expansion-hash-before")
        report["stop_source"] = rt.stop(sync=True)
        report["backup_path"] = str(rt.clone_stopped(source_name, backup_name))
        report["clone_path"] = str(rt.clone_stopped(source_name, clone_name))
        report["clone_config_before"] = (Path(report["clone_path"]) / "config.ini").read_text()
        report["boot_after_s"] = _boot(rt, extra_args=["-partition-size", "32768"], avd_name=clone_name)
        report["after"] = _df(rt, "expansion-df-after")
        report["after_files"], _ = _pull_status(rt, [guest], "expansion-hash-after")
        report["clone_config_after"] = rt.config_path.read_text()
        # ext4 metadata uses some capacity, so 30 GiB is a conservative observable threshold.
        grew = (report["after"]["total_bytes"] >= 30 * 1024**3 and
                report["after"]["total_bytes"] > report["before"]["total_bytes"])
        intact = report["before_files"] == report["after_files"]
        report.update({"capacity_grew_to_32_gib_class": grew, "marker_intact": intact,
                       "status": "passed" if grew and intact else "failed"})
        if not grew:
            report["finding"] = "Existing guest /data did not grow to 32 GiB using the official partition-size option. No destructive workaround was attempted."
    except Exception as exc:
        report.update({"status": "blocked" if "disk space" in str(exc) else "failed",
                       "error": str(exc), "traceback": traceback.format_exc()})
    finally:
        if rt.avd_name == clone_name:
            try:
                report["stop_clone"] = rt.stop(sync=True)
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        rt.avd_name = source_name
    report["finished_unix"] = time.time()
    _persist(rt, "partition_expansion", report)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("suite", choices=("sync", "nosync", "expansion", "all"))
    p.add_argument("--avd", default="official_clean")
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--files-per-kind", type=int, default=16)
    p.add_argument("--clone-prefix", default="official")
    args = p.parse_args()
    if args.rounds < 1 or args.files_per_kind < 1:
        p.error("rounds and files-per-kind must be positive")
    rt = Runtime(args.avd)
    if args.suite in ("sync", "all"):
        sync_shutdown(rt, args.rounds)
    if args.suite in ("nosync", "all"):
        no_sync_shutdown(rt, args.rounds, files_per_kind=args.files_per_kind,
                         avd_name=args.clone_prefix + "_nosync")
    if args.suite in ("expansion", "all"):
        partition_expansion(rt, args.clone_prefix + "_expansion", args.clone_prefix + "_expansion_backup")


if __name__ == "__main__":
    main()
