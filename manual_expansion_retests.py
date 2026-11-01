"""Run three fresh-clone manual-growth retests and read-only cold rechecks.

No emulator operations occur on import. Original reports and logs are preserved;
this runner does not repair, wipe, convert, or change the expansion method.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
import traceback

from avdlib import GRPC_PORT, Runtime, save_json
from basic_tests import _df, _pull_status
from expansion_retests import MARKER, boot_clone, idle_slot, source_fingerprint
from manual_expansion import EXPECTED_MARKER_BYTES, EXPECTED_SHA256, record, run_manual


EXPECTED = {"size": EXPECTED_MARKER_BYTES, "sha256": EXPECTED_SHA256}


def cold_recheck(rt, clone, index):
    """Cold boot once, observe df/marker/kernel log, then sync and stop."""
    original = rt.avd_name
    row = {"clone": clone, "trial": index, "status": "running", "started_unix": time.time(),
           "method": "One cold boot with the existing 32G configuration and official partition flag; read-only df, marker SHA/size and last 120 kernel log lines; sync shutdown. No second resize or filesystem repair."}
    try:
        idle_slot(rt)
        rt.space()
        row["launch"] = boot_clone(rt, clone, ["-partition-size", "32768"])
        row["df"] = _df(rt, f"manual-retest-{index}-cold-df")
        files, log = _pull_status(rt, [MARKER], f"manual-retest-{index}-cold-marker")
        row["marker"] = files[MARKER]
        row["marker_log"] = log
        row["marker_matches_host"] = files[MARKER] == EXPECTED
        row["adb_root_for_dmesg"] = record(rt.adb(["root"], label=f"manual-retest-{index}-cold-root", check=False))
        row["adb_wait_for_root"] = record(rt.adb(["wait-for-device"], label=f"manual-retest-{index}-cold-root-wait", timeout=60))
        row["dmesg_last_120"] = record(rt.shell("dmesg | tail -n 120", label=f"manual-retest-{index}-cold-dmesg", check=False))
        row["shutdown"] = rt.stop(sync=True)
        row["status"] = "passed" if row["marker_matches_host"] else "failed"
        row["capacity_32_gib_class"] = row["df"]["total_bytes"] >= 30 * 1024**3
    except Exception as exc:
        row.update({"status": "failed", "error": str(exc), "traceback": traceback.format_exc()})
    finally:
        if rt.state().get("avd") == clone and rt.owned():
            try:
                row["cleanup_shutdown"] = rt.stop(sync=True)
            except Exception as exc:
                row["cleanup_error"] = str(exc)
        row["task_owned_emulator_still_running"] = rt.owned()
        row["ports_still_occupied"] = [p for p in (rt.console_port, rt.console_port + 1, GRPC_PORT) if rt.port_used(p)]
        rt.avd_name = original
        row["finished_unix"] = time.time()
    return row


def main():
    rt = Runtime()
    directory = rt.results / "manual-expansion-retests"
    directory.mkdir(exist_ok=True)
    summary_path = rt.results / "manual_expansion_retests.json"
    report = {"test": "three_fresh_clone_manual_growth_retests_and_four_cold_rechecks",
              "status": "running", "started_unix": time.time(), "trials": [],
              "requested_fresh_retests": 3, "source": "official_expansion_backup",
              "scope": "Original manual trial plus three fresh clones; after each failed expansion one read-only cold boot. Same official qemu-img + guest resize2fs method; no repair attempts."}
    source_dir = rt.avd_root / "official_expansion_backup.avd"
    try:
        idle_slot(rt)
        rt.space()
        report["source_before"] = source_fingerprint(source_dir)
        original_path = rt.results / "manual_expansion.json"
        original = json.loads(original_path.read_text())
        if original.get("clone") != "official_expand_manual32":
            raise RuntimeError("current original manual result is not the expected first trial")
        preserved = directory / "trial-0.json"
        if preserved.exists():
            raise RuntimeError("trial-0.json already exists; preserve prior retest evidence")
        preserved.write_bytes(original_path.read_bytes())
        first = {"trial": 0, "clone": original["clone"], "report": str(preserved),
                 "status": original["status"], "error": original.get("error")}
        report["trials"].append(first)
        save_json(summary_path, report)
        first["cold_recheck"] = cold_recheck(rt, first["clone"], 0)
        cold_path = directory / "trial-0-cold.json"
        save_json(cold_path, first["cold_recheck"])
        first["cold_recheck_report"] = str(cold_path)
        save_json(summary_path, report)
        idle_slot(rt)
        for index in range(1, 4):
            idle_slot(rt)
            clone = f"official_expand_manual32_retest{index}"
            row = {"trial": index, "clone": clone, "status": "running"}
            report["trials"].append(row)
            save_json(summary_path, report)
            full = run_manual(rt, clone=clone)
            trial_path = directory / f"trial-{index}.json"
            if trial_path.exists():
                raise RuntimeError(f"existing trial report must not be replaced: {trial_path}")
            save_json(trial_path, full)
            row.update({"report": str(trial_path), "status": full["status"],
                        "error": full.get("error"), "source_backup_unchanged": full.get("source_backup_unchanged"),
                        "before_bytes": full.get("before", {}).get("total_bytes"),
                        "after_image_resize_bytes": full.get("after_image_resize", {}).get("total_bytes"),
                        "after_guest_resize_bytes": full.get("after_guest_resize", {}).get("total_bytes"),
                        "marker_after": full.get("cleanup_observed_marker", full.get("after_marker")),
                        "resize_returncode": full.get("guest_resize2fs", {}).get("returncode")})
            save_json(summary_path, report)
            idle_slot(rt)
            if full["status"] != "passed":
                row["cold_recheck"] = cold_recheck(rt, clone, index)
                cold_path = directory / f"trial-{index}-cold.json"
                save_json(cold_path, row["cold_recheck"])
                row["cold_recheck_report"] = str(cold_path)
            save_json(summary_path, report)
            print(json.dumps({"trial": index, "status": row["status"],
                              "after_guest_resize_bytes": row.get("after_guest_resize_bytes"),
                              "cold_bytes": row.get("cold_recheck", {}).get("df", {}).get("total_bytes"),
                              "cold_marker_matches": row.get("cold_recheck", {}).get("marker_matches_host")}), flush=True)
            idle_slot(rt)
        report["source_after"] = source_fingerprint(source_dir)
        report["source_backup_unchanged"] = report["source_before"] == report["source_after"]
        report["fresh_retests_completed"] = len(report["trials"]) - 1
        report["status"] = "passed" if report["source_backup_unchanged"] and all(r["status"] == "passed" for r in report["trials"]) else "failed"
    except Exception as exc:
        report.update({"status": "blocked", "error": str(exc), "traceback": traceback.format_exc()})
    finally:
        if "source_before" in report:
            report["source_after"] = source_fingerprint(source_dir)
            report["source_backup_unchanged"] = report["source_before"] == report["source_after"]
            if not report["source_backup_unchanged"]:
                report["status"] = "failed"
        report["task_owned_emulator_still_running"] = rt.owned()
        report["ports_still_occupied"] = [p for p in (rt.console_port, rt.console_port + 1, GRPC_PORT) if rt.port_used(p)]
        report["finished_unix"] = time.time()
        save_json(summary_path, report)
    print(json.dumps({key: report.get(key) for key in ("status", "fresh_retests_completed", "source_backup_unchanged", "task_owned_emulator_still_running", "ports_still_occupied", "error")}), flush=True)
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
