#!/usr/bin/env python3
"""Analyze frozen A9/B6 logs with explicit BH boundaries and honest timing scope."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import re
import shutil


def fields(line):
    return {key: int(value) if value.lstrip("-").isdigit() else value
            for key, value in re.findall(r"([a-z_]+)=([^\s]+)", line)}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_log(raw, absolute_base=0):
    rows, current, pending = [], None, None
    offset = absolute_base
    for line_number, encoded in enumerate(raw.splitlines(keepends=True), 1):
        start, end = offset, offset + len(encoded)
        offset = end
        line = encoded.decode(errors="replace")
        item = fields(line)
        if "hvf-ram-tail-begin:" in line or "hvf-ram-tail-end:" in line:
            raise RuntimeError("TAIL_DIAG must be off in both comparison runs")
        if "hvf-ram-pause-begin:" in line:
            if current is not None:
                raise RuntimeError("nested/unclosed BH")
            current = {"sequence": item["sequence"], "start_absolute_log_offset": start,
                       "start_line": line_number, "reports": [], "budgets": [],
                       "transactions": [], "eligibility": []}
            pending = None
        elif "hvf-ram-audit: event=report " in line:
            if current is None:
                raise RuntimeError("report outside observed BH")
            match = re.fullmatch(r"(\d+)\+(\d+)", item["range"])
            if not match:
                raise RuntimeError("invalid audit report range")
            report_offset, length = map(int, match.groups())
            pages = length // 4096
            derived_order = pages.bit_length() - 1 if length % 4096 == 0 and pages and not pages & (pages - 1) else None
            current["reports"].append({"block": item["block"], "ram_offset": report_offset,
                                       "length": length, "derived_guest_order": derived_order,
                                       "candidate_bytes": item["range_upper_bound"], "line": line_number})
        elif "hvf-ram-budget:" in line:
            if current is None:
                raise RuntimeError("budget outside observed BH")
            if item["chunk_size"] != 262144 or item["attempt_limit"] != 2 or item["inspect_limit"] != 32:
                raise RuntimeError("frozen granule/per-SG cap mismatch")
            if item["attempted_objects"] > 2 or item["inspected_objects"] > 32:
                raise RuntimeError("per-SG cap exceeded")
            current["budgets"].append(item)
        elif "hvf-ram-eligibility:" in line:
            if current is None:
                raise RuntimeError("eligibility outside observed BH")
            current["eligibility"].append(item)
        elif "hvf-ram-reclaim: result=" in line:
            if current is None:
                raise RuntimeError("transaction outside observed BH")
            if pending is not None:
                raise RuntimeError("transaction lacks its physical record")
            pending = {"transaction": item, "line": line_number}
            current["transactions"].append(pending)
        elif "hvf-ram-physical:" in line:
            if current is None or pending is None:
                raise RuntimeError("physical record without transaction")
            if not all(pending["transaction"][key] == item[key] for key in ("offset", "length", "result")):
                raise RuntimeError("physical/transaction identity mismatch")
            pending["physical"] = item
            pending["free_delta"] = item["free_pages_after"] - item["free_pages_before"]
            pending["speculative_delta"] = item["speculative_pages_after"] - item["speculative_pages_before"]
            pending = None
        elif "hvf-ram-pause-timing:" in line:
            if current is None or current["sequence"] != item["sequence"]:
                raise RuntimeError("BH end identity mismatch")
            if pending is not None:
                raise RuntimeError("BH ends with an unpaired transaction")
            if item["popped_elements"] != item["acked_elements"]:
                raise RuntimeError("report acknowledgement count mismatch")
            if item["request_to_resume_return_us"] != sum(item[key] for key in
                    ("acquire_us", "quiescent_work_us", "resume_call_us")):
                raise RuntimeError("pause phase sum mismatch")
            if item["bh_to_before_final_log_us"] != sum(item[key] for key in
                    ("pre_pause_us", "request_to_resume_return_us", "post_resume_us")):
                raise RuntimeError("instrumented BH phase sum mismatch")
            current.update(timing=item, end_absolute_log_offset=end, end_line=line_number)
            rows.append(current)
            current, pending = None, None
    if pending is not None:
        raise RuntimeError("final transaction lacks its physical record")
    if current is not None:
        raise RuntimeError("final BH is not closed")
    return rows


def union_bytes(ranges):
    total = 0
    by_block = collections.defaultdict(list)
    for block, offset, length in ranges:
        by_block[block].append((offset, offset + length))
    for intervals in by_block.values():
        current = None
        for start, end in sorted(intervals):
            if current is None:
                current = [start, end]
            elif start <= current[1]:
                current[1] = max(current[1], end)
            else:
                total += current[1] - current[0]
                current = [start, end]
        if current is not None:
            total += current[1] - current[0]
    return total


def distribution(values):
    values = sorted(values)
    if not values:
        return {"count": 0, "sum": 0, "min": None, "max": None, "p50": None, "p95": None}
    return {"count": len(values), "sum": sum(values), "min": values[0], "max": values[-1],
            "p50": values[math.ceil(len(values) * .5) - 1],
            "p95": values[math.ceil(len(values) * .95) - 1],
            "percentile_method": "empirical nearest rank; finite observed sample, not bound"}


def summarize(rows):
    reports = [item for row in rows for item in row["reports"]]
    budgets = [item for row in rows for item in row["budgets"]]
    transactions = [item for row in rows for item in row["transactions"]]
    physical = [item for item in transactions if "physical" in item]
    resident = [item for item in physical if item["physical"]["old_present_pages"]]
    resident_commits = [item for item in resident if item["transaction"]["result"] == 1]
    cold = [item for item in physical if not item["physical"]["old_present_pages"]]
    ranges = [(item["block"], item["ram_offset"], item["length"]) for item in reports]
    released_ranges = [(item["transaction"]["block"], item["transaction"]["offset"],
                        item["transaction"]["length"]) for item in resident_commits]
    order_hist = collections.Counter(str(item["derived_guest_order"]) for item in reports)
    length_hist = collections.Counter(item["length"] for item in reports)
    per_bh = []
    for row in rows:
        work = {key: sum(item[key] for item in row["budgets"]) for key in
                ("attempted_objects", "inspected_objects", "attempted_bytes", "inspected_bytes", "report_work_us")}
        per_bh.append({"sequence": row["sequence"], "timing": row["timing"],
                       "budget_sg_count": len(row["budgets"]), "work": work,
                       "audit_report_sg_count": len(row["reports"]),
                       "transaction_count": len(row["transactions"]),
                       "resident_commit_count": sum(item.get("physical", {}).get("old_present_pages", 0) > 0
                                                    and item["transaction"]["result"] == 1
                                                    for item in row["transactions"])})
    repeated = []
    for offset in sorted({item["transaction"]["offset"] for item in resident_commits}):
        group = [item for item in resident_commits if item["transaction"]["offset"] == offset]
        ids = [item["physical"]["old_object_id"] for item in group]
        if len(group) > 1:
            repeated.append({"offset": offset, "old_object_ids": ids,
                             "distinct_old_object_ids": len(set(ids)),
                             "mapping_generations": [item["transaction"]["mapping_generation"] for item in group]})
    metrics = {
        "bh_count": len(rows), "input_sgs": sum(row["timing"]["input_sgs"] for row in rows),
        "tracked_audit_report_sgs": len(reports), "sg_length_histogram": dict(length_hist),
        "derived_guest_order_histogram": dict(order_hist), "distinct_report_ranges": len(set(ranges)),
        "cumulative_report_bytes": sum(item["length"] for item in reports),
        "union_report_address_bytes": union_bytes(ranges),
        "cumulative_candidate_bytes": sum(item["candidate_bytes"] for item in reports),
        "native_attempted_objects": sum(item["attempted_objects"] for item in budgets),
        "native_inspected_objects": sum(item["inspected_objects"] for item in budgets),
        "transaction_results": dict(collections.Counter(item["transaction"]["result"] for item in transactions)),
        "cold_commit_count": sum(item["transaction"]["result"] == 1 for item in cold),
        "resident_window_count": len(resident), "resident_commit_count": len(resident_commits),
        "resident_observations": resident, "resident_unique_address_bytes": union_bytes(released_ranges),
        "resident_cumulative_object_window_bytes": sum(item["transaction"]["length"] for item in resident_commits),
        "resident_cumulative_present_page_bytes": sum(item["physical"]["old_present_pages"] * 16384 for item in resident_commits),
        "same_offset_generations": repeated,
        "cleanup_errors": sum(item["transaction"]["cleanup_failed"] for item in transactions),
        "mincore_errors": sum(item["transaction"][key] for item in transactions for key in
                              ("resident_before_error", "resident_after_error")),
        "page_query_errors": sum(item["physical"]["old_page_query_errors"] for item in physical),
        "counter_query_errors": sum(item["physical"]["query_error"] for item in physical),
        "cold_joint_nonexact_windows": sum(item["free_delta"] != 0 or item["speculative_delta"] != 0 for item in cold),
        "all_observed_physical_windows_exact": bool(physical) and all(
            item["free_delta"] == (item["physical"]["old_present_pages"] if item["transaction"]["result"] == 1 else 0)
            and item["speculative_delta"] == 0 and item["physical"]["old_page_query_errors"] == 0
            and item["physical"]["query_error"] == 0 for item in physical),
        "eligibility_reasons": dict(collections.Counter(str(item.get("reason", item.get("skip", "unknown")))
                                      for row in rows for item in row["eligibility"])),
        "per_bh": per_bh,
    }
    metrics["bh_distributions"] = {
        key: distribution([row["timing"][key] for row in rows]) for key in
        ("input_sgs", "popped_elements", "reclaim_calls", "acquire_us", "quiescent_work_us",
         "resume_call_us", "request_to_resume_return_us", "bh_to_before_final_log_us")}
    metrics["bh_work_distributions"] = {
        key: distribution([row["work"][key] for row in per_bh]) for key in
        ("attempted_objects", "inspected_objects", "attempted_bytes", "inspected_bytes", "report_work_us")}
    return metrics


def metadata_summaries(launch):
    result = []
    for path in sorted(launch.glob("*-metadata.json")):
        capture = json.loads(path.read_text())
        text = next(row["stdout"] for row in capture["records"] if row["guest_path"] == "/proc/pagetypeinfo")
        counts = collections.defaultdict(int)
        for line in text.splitlines():
            match = re.match(r"Node\s+\d+, zone\s+\w+, type\s+(\w+)\s+([\d ]+)$", line)
            if match and match[1] != "Isolate":
                for order, number in enumerate(map(int, match[2].split())):
                    counts[order] += number
        coverage = {str(minimum): sum(number * (1 << order) * 4096 for order, number in counts.items()
                                     if order >= minimum) for minimum in range(6, 11)}
        result.append({"path": path.name, "sha256": sha(path), "label": capture["label"],
                       "pid": capture["pid"], "boot_id": capture["boot_id"], "order": capture["order"],
                       "counts_by_order": dict(counts), "structural_buddy_bytes_at_or_above_order": coverage,
                       "scope": "Free-list structure only; includes already-reported/CMA blocks; no host PRESENT correlation."})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launch", type=Path, required=True)
    args = parser.parse_args()
    launch = args.launch.resolve(strict=True)
    output = launch / "reporting-order-assessment.json"
    if output.exists():
        raise RuntimeError("refusing to overwrite frozen scoped assessment")
    window = json.loads((launch / "comparison-window.json").read_text())
    outcome = json.loads((launch / "outcome.json").read_text())
    if not outcome["stopped_normally"] or not outcome["workload_completed"]:
        raise RuntimeError("comparison did not complete/stop normally")
    absolute_base = int((launch / "log-offset.txt").read_text())
    rows = parse_log((launch / "emulator.log").read_bytes(), absolute_base)
    start, end = window["start_absolute_log_offset"], window["end_absolute_log_offset"]
    scopes = {"before_setting_window": [], "comparison_wholly_inside": [],
              "boundary_crossing_excluded": [], "after_comparison_window": []}
    for row in rows:
        if row["end_absolute_log_offset"] <= start:
            scope = "before_setting_window"
        elif row["start_absolute_log_offset"] >= end:
            scope = "after_comparison_window"
        elif row["start_absolute_log_offset"] >= start and row["end_absolute_log_offset"] <= end:
            scope = "comparison_wholly_inside"
        else:
            scope = "boundary_crossing_excluded"
        scopes[scope].append(row)
    result = {"order": window["order"], "pid": window["pid"], "boot_id": window["boot_id"],
              "log_sha256": sha(launch / "emulator.log"), "window": window,
              "scopes": {key: summarize(value) for key, value in scopes.items()},
              "all_launch": summarize(rows), "bh_rows": rows,
              "phase_buddy_structure": metadata_summaries(launch),
              "full_bh_body_available": False, "tail_diagnostic_records": 0,
              "timing_scope": "request_to_resume_return_us includes pause acquisition/drain/resume bookkeeping. bh_to_before_final_log_us excludes initial snapshot/GArray and final timing log; neither is complete BH body, exact per-vCPU off-run or a bound.",
              "strict_global_physical_pass": False, "net_saving_measured": False,
              "kernel_metadata_cost_measured": False, "production_default_promoted": False,
              "analysis_script_sha256": sha(Path(__file__))}
    shutil.copy2(Path(__file__), launch / "order-analysis-script.py")
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    selected = result["scopes"]["comparison_wholly_inside"]
    print(json.dumps({"order": window["order"], "pid": window["pid"],
                      "bh_count": selected["bh_count"], "sg_orders": selected["derived_guest_order_histogram"],
                      "resident_commits": selected["resident_commit_count"],
                      "full_bh_body_available": False}, indent=2))


if __name__ == "__main__":
    main()
