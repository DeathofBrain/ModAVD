#!/usr/bin/env python3
"""Freeze one stopped telemetry-v7 launch without inferring net memory saving."""
import argparse
import collections
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", type=Path, required=True)
    parser.add_argument("--expected-pid", type=int, required=True)
    parser.add_argument("--mode", choices=("positive", "map-fault", "tail-diagnostic"), required=True)
    parser.add_argument("--stop-receipt", type=Path, required=True)
    parser.add_argument("--memory-evidence", type=Path, action="append", default=[])
    parser.add_argument("--network-evidence", type=Path)
    parser.add_argument("--simd-evidence", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    launch = args.launch.resolve(strict=True)
    assert read(launch / "emulator.json")["pid"] == args.expected_pid
    stop = read(args.stop_receipt)
    assert stop["capture_kind"] == "subprocess.run"
    assert stop["expected_pid"] == args.expected_pid == stop["before_state"]["pid"]
    assert stop["exit_code"] == 0 and stop["argv"][-1] == "stop"
    processes = subprocess.check_output(["ps", "-axo", "pid=,comm="], text=True).splitlines()
    assert not any(line.split(None, 1)[0] == str(args.expected_pid) and "qemu-system-" in line
                   for line in processes), "Selected guest still running"
    assert not (launch / "assessment.json").exists(), "Never overwrite a frozen assessment"
    source_log = root / ".macos-work/reclaim-runtime/logs/emulator.log"
    log = source_log.read_bytes()[int((launch / "log-offset.txt").read_text()):]
    (launch / "emulator.log").write_bytes(log)
    with (launch / "summary.json").open("w") as output:
        subprocess.run(["python3", str(Path(__file__).with_name("summarize-ram-audit.py")),
                        str(launch / "emulator.log")], stdout=output, check=True)
    summary = read(launch / "summary.json")
    current, pending = None, None
    timing_rows, resident_rows = [], []
    for line in log.decode(errors="replace").splitlines():
        fields = {key: int(value) if value.lstrip("-").isdigit() else value
                  for key, value in re.findall(r"([a-z_]+)=([^\s]+)", line)}
        if "hvf-ram-pause-begin:" in line:
            assert current is None
            current = {"sequence": fields["sequence"], "native_attempts": 0,
                       "inspections": 0, "sg_budgets": 0, "resident_commit_count": 0,
                       "committed_count": 0}
        elif "hvf-ram-budget:" in line:
            assert current
            current["native_attempts"] += fields["attempted_objects"]
            current["inspections"] += fields["inspected_objects"]
            current["sg_budgets"] += 1
        elif "hvf-ram-reclaim: result=" in line:
            pending = fields
        elif "hvf-ram-physical:" in line:
            assert current and pending
            assert all(pending[key] == fields[key] for key in ("offset", "length", "result"))
            current["committed_count"] += fields["result"] == 1
            if fields["old_present_pages"]:
                current["resident_commit_count"] += fields["result"] == 1
                resident_rows.append({
                    "bh_sequence": current["sequence"], "transaction": pending,
                    "physical": fields,
                    "free_delta": fields["free_pages_after"] - fields["free_pages_before"],
                    "speculative_delta": fields["speculative_pages_after"] - fields["speculative_pages_before"],
                })
            pending = None
        elif "hvf-ram-pause-timing:" in line:
            assert current and current["sequence"] == fields["sequence"]
            row = {**current, **fields}
            assert row["request_to_resume_return_us"] == sum(row[key] for key in
                    ("acquire_us", "quiescent_work_us", "resume_call_us"))
            assert row["bh_to_before_final_log_us"] == sum(row[key] for key in
                    ("pre_pause_us", "request_to_resume_return_us", "post_resume_us"))
            assert row["popped_elements"] == row["acked_elements"]
            timing_rows.append(row)
            current = None
    assert current is None
    timing = {
        "scope": "Instrumented BH span starts after initial snapshot/GArray construction and ends before final timing log. Pause request->resume return includes acquisition/drain/resume bookkeeping, not each vCPU's exact off-run duration. PerSG report_work_us excludes caller validation and otherSG work.",
        "rows": timing_rows, "count": len(timing_rows),
        "all_element_acks_match": True, "all_phase_sums_match": True,
        "observed_values_are_not_bounds": True,
        "budget_scope": "2attempt/32inspect perSG call. Caller permits up to32elements, each up to1024SG; no sharedBH 2/32budget is implemented.",
    }
    for label, field in (("native_attempts_per_bh", "native_attempts"),
                         ("input_sgs_per_bh", "input_sgs"), ("elements_per_bh", "popped_elements"),
                         ("request_to_resume_return_us", "request_to_resume_return_us"),
                         ("quiescent_work_us", "quiescent_work_us"),
                         ("instrumented_bh_us", "bh_to_before_final_log_us")):
        timing["max_" + label] = max((row[field] for row in timing_rows), default=0)
    write(launch / "timing-assessment.json", timing)
    probes, boots = {}, []
    last_probe = None
    for folder in args.memory_evidence:
        capture = read(folder / "capture.json")
        done = [event for event in map(json.loads, (folder / "probe.jsonl").read_text().splitlines())
                if event.get("event") == "done"][-1]
        probes[str(folder)] = {"pass": capture["pass"], "elapsed_seconds": capture["elapsed_seconds"],
                              "live_checks": done["live_checks"], "errors": done["errors"],
                              "cleaned": capture["guest_directory_cleaned"]}
        boots.append(capture["boot_id"])
        last_probe = capture
    network, simd = None, None
    if args.network_evidence:
        capture = read(args.network_evidence)
        network = {"pass": capture["passed"], "cases": len(capture["cases"]),
                   "cleanup": capture.get("cleanup")}
        boots.extend((capture["identity"]["boot_id"], capture["boot_id_after"]))
        if last_probe:
            start = datetime.datetime.fromisoformat(capture["collected_at"]).timestamp()
            end = start + capture["elapsed_seconds"]
            memory_end = datetime.datetime.fromisoformat(last_probe["captured_utc"]).timestamp()
            memory_start = memory_end - last_probe["elapsed_seconds"]
            network["overlap_with_memory_seconds"] = max(0, min(end, memory_end) - max(start, memory_start))
    if args.simd_evidence:
        capture = read(args.simd_evidence / "capture.json")
        simd = {"pass": capture["pass"], "pause_cycles": capture["pause_cycles"],
                "snapshot_cycles": capture["snapshot_cycles"],
                "live_boundaries": len(capture["live_boundaries"]), "checks": capture["done"]["checks"],
                "errors": capture["done"]["errors"], "cleanup": capture["cleanup_success"]}
        boots.append(capture["boot_id"])
    physical, transactions = summary["physical_observations"], summary["transactions"]
    generations = []
    for offset in sorted({row["physical"]["offset"] for row in resident_rows}):
        rows = [row for row in resident_rows if row["physical"]["offset"] == offset]
        if len(rows) > 1:
            generations.append({"offset": offset, "gpa": rows[0]["transaction"]["gpa"],
                                "old_object_ids": [row["physical"]["old_object_id"] for row in rows],
                                "mapping_generations": [row["transaction"]["mapping_generation"] for row in rows],
                                "bh_sequences": [row["bh_sequence"] for row in rows],
                                "distinct_backing_ids": len({row["physical"]["old_object_id"] for row in rows}),
                                "all_committed": all(row["physical"]["result"] == 1 for row in rows)})
    positive_rows = [row for row in resident_rows if row["physical"]["result"] == 1]
    rollback_rows = [row for row in resident_rows if row["physical"]["result"] == 2]
    cold_rows = [row for row in physical if not row["old_present_pages"]]
    all_positive_exact = bool(positive_rows) and all(
        row["physical"]["old_ref_min"] == row["physical"]["old_ref_max"] == 1
        and row["physical"]["old_present_pages"] == 16
        and row["physical"]["old_page_query_errors"] == row["physical"]["query_error"] == 0
        and row["free_delta"] == 16 and row["speculative_delta"] == 0
        and row["transaction"]["cleanup_failed"] == 0
        and row["transaction"]["target_resident_before"] == 262144
        and row["transaction"]["target_resident_after"] == 0 for row in positive_rows)
    assessment = {
        "captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "pid": args.expected_pid, "stopped_normally": True, "mode": args.mode,
        "stop_receipt": str(args.stop_receipt), "stop_receipt_sha256": sha(args.stop_receipt),
        "installed_receipt": "installed.json", "boot_identity_unchanged": len(set(boots)) == 1 if boots else None,
        "memory": probes, "network": network, "simd": simd,
        "transactions": {"count": len(transactions),
                         "results": dict(collections.Counter(row.get("result") for row in transactions)),
                         "cleanup_errors": sum(row.get("cleanup_failed", 0) for row in transactions),
                         "mincore_errors": sum(row.get("resident_before_error", 0) + row.get("resident_after_error", 0)
                                               for row in transactions)},
        "resident_evidence": {
            "count": len(resident_rows), "committed_count": len(positive_rows),
            "distinct_committed_offsets": len({row["physical"]["offset"] for row in positive_rows}),
            "cumulative_resident_object_bytes": sum(row["physical"]["old_present_pages"] * 16384 for row in positive_rows),
            "distinct_committed_address_range_bytes": len({row["physical"]["offset"] for row in positive_rows}) * 262144,
            "all_positive_object_windows_exact": all_positive_exact, "observations": resident_rows,
            "all_resident_rollback_windows_exact": bool(rollback_rows) and all(
                row["physical"]["old_ref_min"] == row["physical"]["old_ref_max"] == 3
                and row["free_delta"] == row["speculative_delta"] == 0
                and row["physical"]["old_page_query_errors"] == row["physical"]["query_error"] == 0
                and row["transaction"]["cleanup_failed"] == 0
                and row["transaction"]["target_resident_before"] == row["transaction"]["target_resident_after"]
                for row in rollback_rows),
            "same_offset_generations": generations,
            "same_offset_second_generation_demonstrated": any(row["all_committed"] and row["distinct_backing_ids"] > 1
                                                              for row in generations),
        },
        "physical_counter_scope": {
            "native_global_all_windows_exact": False,
            "all_guest_windows_exact": bool(physical) and all(
                row["free_pages_after"] - row["free_pages_before"] == (row["old_present_pages"] if row["result"] == 1 else 0)
                and row["speculative_pages_after"] == row["speculative_pages_before"]
                and row["query_error"] == row["old_page_query_errors"] == 0 for row in physical),
            "all_free_delta_counts": dict(collections.Counter(row["free_pages_after"] - row["free_pages_before"] for row in physical)),
            "cold_free_nonzero_windows": sum(row["free_pages_after"] != row["free_pages_before"] for row in cold_rows),
            "cold_joint_nonexact_windows": sum(
                row["free_pages_after"] != row["free_pages_before"] or row["speculative_pages_after"] != row["speculative_pages_before"]
                for row in cold_rows),
            "page_query_errors": sum(row["old_page_query_errors"] for row in physical),
            "counter_query_errors": sum(row["query_error"] for row in physical),
            "strict_global_physical_pass": False, "net_saving_measured": False, "kernel_metadata_cost_measured": False,
        },
        "timing": "timing-assessment.json",
        "timing_max_request_to_resume_return_us": timing["max_request_to_resume_return_us"],
        "timing_max_bh_instrumented_us": timing["max_instrumented_bh_us"],
        "production_decision": "Retain private opt-in; do not promote default. Small object-release and finite correctness evidence do not establish net saving/kernel metadata cost or a shared BH work budget.",
        "log_sha256": hashlib.sha256(log).hexdigest(), "analysis_script_sha256": sha(Path(__file__)),
    }
    shutil.copy2(Path(__file__), launch / "analysis-script.py")
    write(launch / "assessment.json", assessment)
    write(launch / "capture.json", {"captured_utc": assessment["captured_utc"], "pid": args.expected_pid,
                                    "stopped_normally": True, "assessment": "assessment.json", "timing": "timing-assessment.json",
                                    "sha256": {name: sha(launch / name) for name in
                                               ("assessment.json", "timing-assessment.json", "emulator.log")}})
    print(json.dumps({key: assessment[key] for key in ("pid", "boot_identity_unchanged", "network", "simd", "transactions",
                                                      "physical_counter_scope", "timing_max_request_to_resume_return_us",
                                                      "timing_max_bh_instrumented_us")}, indent=2))


if __name__ == "__main__":
    main()
