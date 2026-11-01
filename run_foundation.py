"""Run the real official-AVD foundational suites with persistent stage status."""
import json
from pathlib import Path
import subprocess
import time
import traceback

from avdlib import Runtime, ROOT, save_json
import basic_tests
import capture_tests


def main():
    rt = Runtime()
    status = {"started_unix": time.time(), "stages": [], "status": "running"}
    status_file = ROOT / "results/progress.json"
    def stage(name, fn):
        row = {"stage": name, "started_unix": time.time(), "status": "running"}
        status["current_stage"] = name
        status["stages"].append(row)
        save_json(status_file, status)
        print(json.dumps(row), flush=True)
        try:
            value = fn()
            row["status"] = value.get("status", "completed") if isinstance(value, dict) else "completed"
        except Exception as exc:
            row.update(status="error", error=str(exc), traceback=traceback.format_exc())
        row["finished_unix"] = time.time()
        save_json(status_file, status)
        print(json.dumps(row), flush=True)
        return row
    def headless():
        if rt.owned(): raise RuntimeError("unanticipated running task emulator")
        pid = rt.start()
        boot = rt.wait_boot()
        environment = rt.collect_environment()
        result = {"status": "passed", "pid": pid, "boot_s": boot,
                  "launch": rt.state(), "environment": environment}
        save_json(rt.results / "headless_smoke.json", result)
        return result
    if stage("headless_smoke", headless)["status"] != "passed":
        status["status"] = "blocked"
        save_json(status_file, status)
        return
    stage("sync_shutdown", lambda: basic_tests.sync_shutdown(rt, 10))
    stage("no_sync_shutdown", lambda: basic_tests.no_sync_shutdown(rt, 10, avd_name="official_nosync_verified"))
    stage("partition_expansion", lambda: basic_tests.partition_expansion(rt))
    stage("screenshots", lambda: capture_tests.screenshots(rt, 100))
    stage("shared_video", lambda: capture_tests.shared_video(rt, 60))
    # A separate, ordinary launch avoids measuring the -share-vid capture overhead.
    if rt.owned(): rt.stop(sync=True)
    rt.avd_name = "official_clean"
    rt.start()
    rt.wait_boot()
    stage("probe_smoke", lambda: subprocess.run(
        ["python3", "soak_tests.py", "--output", "results/probe_prepare", "prepare"],
        cwd=ROOT, env=rt.env, check=True))
    stage("memory_reclamation_3_rounds", lambda: subprocess.run(
        ["python3", "soak_tests.py", "--emulator-pid", str(rt.pid),
         "--output", "results/memory", "memory"], cwd=ROOT, env=rt.env, check=True))
    rt.stop(sync=True)
    status.update(status="foundation_complete", current_stage="ready_for_game", finished_unix=time.time())
    save_json(status_file, status)
    print(json.dumps(status), flush=True)


if __name__ == "__main__":
    main()
