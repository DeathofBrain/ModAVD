"""Three independent, non-destructive official AVD capacity-growth retests.

This module has no emulator side effects on import. The CLI requires an idle
task emulator slot and never starts or writes the source backup AVD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import time
import traceback

from avdlib import GRPC_PORT, Runtime, save_json
from basic_tests import _df, _pull_status


MARKER = "/data/local/tmp/official-baseline/expansion-marker.bin"
EXPECTED_MARKER_BYTES = 1048576


def disk_headers(directory):
    """Read qcow metadata without opening an image for write or using resize."""
    directory = Path(directory)
    result = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as f:
            header = f.read(4096)
            if header[:4] != b"QFI\xfb":
                continue
            if len(header) < 72:
                raise ValueError(f"truncated qcow header: {path}")
            backing_offset = struct.unpack_from(">Q", header, 8)[0]
            backing_length = struct.unpack_from(">I", header, 16)[0]
            if backing_length > 1024 * 1024:
                raise ValueError(f"implausible qcow backing filename length: {path}")
            f.seek(backing_offset)
            backing = f.read(backing_length)
        result[str(path.relative_to(directory))] = {
            "version": struct.unpack_from(">I", header, 4)[0],
            "virtual_size_bytes": struct.unpack_from(">Q", header, 24)[0],
            "cluster_bits": struct.unpack_from(">I", header, 20)[0],
            "backing_file": backing.decode("utf-8", "replace"),
            "backing_file_hex": backing.hex(),
            "file_size_bytes": path.stat().st_size,
            "header_sha256_first_4096": hashlib.sha256(header).hexdigest(),
        }
    return result


def source_fingerprint(directory):
    """Fingerprint source configuration and metadata, without modifying it."""
    directory = Path(directory)
    config = directory / "config.ini"
    # Full source-disk hashes would read several GiB repeatedly. File mtimes,
    # sizes, and qcow-header hashes detect accidental source writes by this CLI.
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            files[str(path.relative_to(directory))] = {
                "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            }
    return {"config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
            "files": files, "qcow_headers": disk_headers(directory)}


def persist(rt, report):
    save_json(rt.results / "expansion_retests.json", report)


def idle_slot(rt):
    if rt.owned():
        raise RuntimeError("task emulator is running; retests require an exclusive idle slot")
    busy = [p for p in (rt.console_port, rt.console_port + 1, GRPC_PORT) if rt.port_used(p)]
    if busy:
        raise RuntimeError(f"task emulator ports are still occupied: {busy}")


def hardware_capacity(rt):
    path = rt.avd_dir / "hardware-qemu.ini"
    text = path.read_text() if path.exists() else ""
    fields = re.findall(
        r"(?m)^(disk\.(?:data|system|vendor|cache|encryptionKey)Partition\.[^=]+)\s*=\s*(.*)$", text)
    return {"path": str(path), "text": text,
            "partition_fields": {key.strip(): value.strip() for key, value in fields}}


def boot_clone(rt, name, extra_args=None):
    idle_slot(rt)
    pid = rt.start(avd_name=name, extra_args=extra_args)
    elapsed = rt.wait_boot()
    state = rt.state()
    if state.get("avd") != name:
        raise RuntimeError(f"AVD identity mismatch: {state.get('avd')} != {name}")
    return {"pid": pid, "boot_s": elapsed, "args": state["args"],
            "log": state["log"], "state": state}


def run_retests(rt, source="official_expansion_backup", prefix="official_expand_config32", rounds=3):
    report = {
        "test": "three_fresh_clone_config32_and_official_partition_flag_retests",
        "status": "running", "source_backup": source, "requested_rounds": rounds,
        "started_unix": time.time(), "rounds": [],
        "method": "Each stopped APFS clone is first booted at the preserved 8 GiB configuration to verify marker and df, stopped with sync, configured to disk.dataPartition.size=32G, then launched with official -partition-size 32768. No wipe, image conversion, image resize, overlay deletion, or manual guest filesystem resizing.",
        "source_protection": "Source backup is only read and copied. It is never launched or opened for write. Configuration, file sizes/mtimes, and qcow-header fingerprints are compared before and after.",
        "reserve_bytes": 20 * 1024**3,
    }
    previous = rt.results / "expansion_retests.json"
    if previous.exists():
        history = rt.results / "history"
        history.mkdir(exist_ok=True)
        archived = history / f"expansion_retests-{time.time_ns()}.json"
        archived.write_bytes(previous.read_bytes())
        report["previous_report_preserved"] = str(archived)
    persist(rt, report)
    original_name = rt.avd_name
    source_dir = rt.avd_root / (source + ".avd")
    expected = None
    try:
        idle_slot(rt)
        rt.space()
        if not (source_dir / "config.ini").is_file():
            raise RuntimeError(f"source backup not found: {source_dir}")
        marker = rt.results / "expansion-marker.bin"
        data = marker.read_bytes()
        if len(data) != EXPECTED_MARKER_BYTES:
            raise ValueError(f"host marker has {len(data)} bytes, expected {EXPECTED_MARKER_BYTES}")
        expected = {"size": EXPECTED_MARKER_BYTES, "sha256": hashlib.sha256(data).hexdigest()}
        report["expected_marker"] = {"host_path": str(marker), "guest_path": MARKER, **expected}
        report["source_before"] = source_fingerprint(source_dir)
        report["source_config"] = (source_dir / "config.ini").read_text()
        original_result = rt.results / "partition_expansion.json"
        if original_result.exists():
            prior = json.loads(original_result.read_text())
            report["first_trial_reference"] = str(original_result)
            report["first_trial_marker_matches_host"] = (
                prior.get("before_files", {}).get(MARKER) == expected and
                prior.get("after_files", {}).get(MARKER) == expected)
        persist(rt, report)
        for index in range(1, rounds + 1):
            row = {"round": index, "status": "running", "clone": f"{prefix}_{index}",
                   "started_unix": time.time(), "phase": "clone"}
            report["rounds"].append(row)
            persist(rt, report)
            clone = row["clone"]
            try:
                idle_slot(rt)
                row["free_bytes_before"] = rt.space()
                destination = rt.avd_root / (clone + ".avd")
                if destination.exists():
                    raise RuntimeError(f"clone destination exists; preserving prior evidence: {destination}")
                row["clone_path"] = str(rt.clone_stopped(source, clone))
                row["headers_after_copy"] = disk_headers(destination)
                config = destination / "config.ini"
                row["config_before"] = config.read_text()
                row["phase"] = "baseline_boot"
                persist(rt, report)
                row["baseline_launch"] = boot_clone(rt, clone)
                row["before"] = _df(rt, f"expansion-retest-{index}-df-before")
                before, before_log = _pull_status(rt, [MARKER], f"expansion-retest-{index}-marker-before")
                row["before_marker"] = before[MARKER]
                row["before_marker_log"] = before_log
                row["before_marker_matches_host"] = before[MARKER] == expected
                row["baseline_hardware"] = hardware_capacity(rt)
                row["baseline_shutdown"] = rt.stop(sync=True)
                persist(rt, report)
                if not row["before_marker_matches_host"]:
                    raise RuntimeError("fresh clone marker does not match host artifact; growth attempt skipped")
                if not 7 * 1024**3 <= row["before"]["total_bytes"] <= 9 * 1024**3:
                    raise RuntimeError("fresh clone guest capacity is outside the expected 8 GiB class; growth attempt skipped")
                row["headers_before_growth"] = disk_headers(destination)
                if not re.search(r"(?m)^disk\.dataPartition\.size=.*$", row["config_before"]):
                    raise ValueError("source configuration lacks disk.dataPartition.size")
                configured = re.sub(r"(?m)^disk\.dataPartition\.size=.*$",
                                    "disk.dataPartition.size=32G", row["config_before"])
                rt.space()
                config.write_text(configured)
                row["config_after"] = config.read_text()
                row["phase"] = "growth_boot"
                persist(rt, report)
                row["growth_launch"] = boot_clone(rt, clone, ["-partition-size", "32768"])
                row["after"] = _df(rt, f"expansion-retest-{index}-df-after")
                after, after_log = _pull_status(rt, [MARKER], f"expansion-retest-{index}-marker-after")
                row["after_marker"] = after[MARKER]
                row["after_marker_log"] = after_log
                row["after_marker_matches_host"] = after[MARKER] == expected
                row["marker_three_way_match"] = row["before_marker_matches_host"] and row["after_marker_matches_host"]
                row["growth_hardware"] = hardware_capacity(rt)
                row["phase"] = "growth_shutdown"
                persist(rt, report)
                row["growth_shutdown"] = rt.stop(sync=True)
                row["headers_after_growth"] = disk_headers(destination)
                row["free_bytes_after"] = rt.space()
                row["guest_capacity_grew_to_32_gib_class"] = (
                    row["after"]["total_bytes"] >= 30 * 1024**3 and
                    row["after"]["total_bytes"] > row["before"]["total_bytes"])
                row["status"] = ("passed" if row["guest_capacity_grew_to_32_gib_class"]
                                 and row["marker_three_way_match"] else "failed")
                row["finding"] = ("guest capacity grew and marker remained intact" if row["status"] == "passed" else
                                  "configured capacity did not yield a 32 GiB guest filesystem" if row["marker_three_way_match"] else
                                  "marker integrity failure")
                row["phase"] = "complete"
            except Exception as exc:
                row.update({"status": "blocked" if "disk space" in str(exc) or "occupied" in str(exc)
                            or "running" in str(exc) or "destination exists" in str(exc) else "failed",
                            "error": str(exc), "traceback": traceback.format_exc()})
            finally:
                # Only a clone launched by this round may be stopped here.
                if rt.state().get("avd") == clone and rt.owned():
                    try:
                        row["cleanup_shutdown"] = rt.stop(sync=True)
                    except Exception as exc:
                        row["cleanup_error"] = str(exc)
                row["task_owned_emulator_still_running"] = rt.owned()
                row["ports_still_occupied"] = [p for p in (rt.console_port, rt.console_port + 1, GRPC_PORT)
                                                if rt.port_used(p)]
                row["finished_unix"] = time.time()
                rt.avd_name = original_name
                persist(rt, report)
                print(json.dumps({"suite": "expansion_retests", "round": index,
                                  "status": row["status"], "before": row.get("before", {}).get("total_bytes"),
                                  "after": row.get("after", {}).get("total_bytes"),
                                  "marker_three_way_match": row.get("marker_three_way_match"),
                                  "error": row.get("error")}, ensure_ascii=False), flush=True)
            if row.get("task_owned_emulator_still_running") or row.get("ports_still_occupied"):
                report["abort_reason"] = "task slot not idle after trial; remaining trials skipped"
                break
        report["source_after"] = source_fingerprint(source_dir)
        report["source_backup_unchanged"] = report["source_before"] == report["source_after"]
        complete = len(report["rounds"]) == rounds
        report["status"] = (
            "passed" if complete and report["source_backup_unchanged"] and all(r["status"] == "passed" for r in report["rounds"])
            else "blocked" if not complete or any(r["status"] == "blocked" for r in report["rounds"])
            else "failed")
    except Exception as exc:
        report.update({"status": "blocked", "error": str(exc), "traceback": traceback.format_exc()})
    finally:
        rt.avd_name = original_name
        report["restored_runtime_avd_name"] = rt.avd_name
        report["source_avd_was_started"] = False
        report["finished_unix"] = time.time()
        persist(rt, report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default="official_expansion_backup")
    p.add_argument("--prefix", default="official_expand_config32")
    p.add_argument("--rounds", type=int, default=3)
    args = p.parse_args()
    if args.rounds < 1:
        p.error("rounds must be positive")
    if args.source == args.prefix or any(not re.fullmatch(r"[A-Za-z0-9_-]+", s) for s in [args.source, args.prefix]):
        p.error("source/prefix must be distinct simple AVD names")
    rt = Runtime()
    result = run_retests(rt, args.source, args.prefix, args.rounds)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
