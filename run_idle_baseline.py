"""Complete stopped-clone retests and a real 7200 s non-game AVD control.

Requires the foundation runner to have finished and released the emulator slot.
Does not install a game or interact with accounts.
"""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import traceback

from avdlib import ROOT, Runtime, save_json, sha256


def paired_remote_preflight(rt):
    """Retain three paired attempts; a clean pair permits the full WAN soak."""
    from run_soak import validate_preflight
    from soak_tests import host_tcp

    base = rt.results / "remote-preflight-retests"
    summary_path = rt.results / "remote_echo_preflight.json"
    if base.exists() or summary_path.exists():
        raise FileExistsError("Prior remote preflight evidence exists; archive it before retrying")
    base.mkdir()
    preflight = {"hostname": "tcpbin.com", "port": 4242, "started_unix": time.time(),
                 "payload_framing": "line64", "payload_bytes": 64, "bulk_bytes": 0,
                 "duration_seconds_per_round": 10, "planned_rounds": 3, "rounds": [],
                 "routing_note": "DNS may be FakeIP/TUN; direct Internet routing is not claimed",
                 "selection_rule": "First round with both host and guest clean; all three rounds run and failures remain evidence"}
    selected = None
    for number in range(1, 4):
        directory = base / f"round-{number}"
        directory.mkdir()
        host_log = directory / "host_network.jsonl"
        guest_log = directory / "guest_network.jsonl"
        # Empty evidence files distinguish a blocked attempt from a missing round.
        host_log.touch(exist_ok=False)
        guest_log.touch(exist_ok=False)
        receipt = {"round": number, "started_unix": time.time(), "host_log": str(host_log),
                   "guest_log": str(guest_log), "host": {}, "guest": {}}
        stop = threading.Event()
        host = None
        try:
            endpoint = socket.gethostbyname(preflight["hostname"])
            receipt["resolved_ipv4"] = endpoint
            arguments = argparse.Namespace(duration=10, payload_bytes=64, bulk_bytes=0,
                heartbeat_ms=1000, payload_framing="line", host_endpoint=endpoint)
            def host_control():
                try:
                    host_tcp(arguments, preflight["port"], host_log, stop)
                except BaseException as exc:
                    receipt["host"]["execution_error"] = repr(exc)
            host = threading.Thread(target=host_control, name=f"wan-preflight-host-{number}", daemon=True)
            command = ["shell", "/data/local/tmp/official_avd_probe", "tcp", endpoint,
                       str(preflight["port"]), "10", "1000", "64", "0", "line64"]
            receipt["guest_command"] = command
            host.start()
            try:
                result = rt.adb(command, label=f"wan-guest-preflight-round-{number}", timeout=40, check=False)
                guest_log.write_text(result.stdout)
                (directory / "guest_network.stderr.log").write_text(result.stderr)
                receipt["guest"].update(returncode=result.returncode, command_log=result.log)
            except subprocess.TimeoutExpired as exc:
                def decoded(value):
                    return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
                guest_log.write_text(decoded(exc.stdout))
                (directory / "guest_network.stderr.log").write_text(decoded(exc.stderr))
                receipt["guest"]["execution_error"] = repr(exc)
            except Exception as exc:
                receipt["guest"]["execution_error"] = repr(exc)
            host.join(timeout=30)
            if host.is_alive():
                stop.set()
                host.join(timeout=12)
                receipt["host"]["execution_error"] = "Host preflight exceeded its bounded wait"
            for label, log in (("host", host_log), ("guest", guest_log)):
                try:
                    if receipt[label].get("execution_error") or (label == "guest" and receipt[label].get("returncode") != 0):
                        raise RuntimeError("Preflight execution did not complete successfully")
                    receipt[label]["validation"] = validate_preflight(
                        log, expected_ipv4=endpoint, expected_port=preflight["port"])
                    receipt[label]["status"] = "passed"
                except Exception as exc:
                    receipt[label].update(status="failed", error=str(exc))
            receipt["status"] = "passed" if all(receipt[label].get("status") == "passed" for label in ("host", "guest")) else "failed"
            if receipt["status"] == "passed" and selected is None:
                selected = {"round": number, "resolved_ipv4": endpoint, "guest_log": str(guest_log),
                            "validation": receipt["guest"]["validation"], "receipt": str(directory / "receipt.json")}
        except Exception as exc:
            receipt.update(status="blocked", error=str(exc))
        finally:
            stop.set()
            if host is not None and host.is_alive():
                host.join(timeout=12)
            receipt["finished_unix"] = time.time()
            receipt["attribution_note"] = "Paired public-service observations; endpoint, host routing, and emulator paths all require consideration before attributing a reset."
            save_json(directory / "receipt.json", receipt)
            preflight["rounds"].append(receipt)
            save_json(summary_path, preflight)
    preflight.update(finished_unix=time.time(), completed_rounds=len(preflight["rounds"]),
                     clean_paired_rounds=sum(r["status"] == "passed" for r in preflight["rounds"]),
                     status="passed" if selected else "failed" if any(r["status"] == "failed" for r in preflight["rounds"]) else "blocked",
                     selected=selected,
                     observation_scope="Three approximately 10-second paired attempts; a clean selected pair permits long observation and does not erase earlier resets.")
    if selected:
        preflight.update(resolved_ipv4=selected["resolved_ipv4"], guest_log=selected["guest_log"], validation=selected["validation"])
    save_json(summary_path, preflight)
    return preflight


def main(soak_only=False):
    rt = Runtime("official_clean")
    path = ROOT / "results" / ("idle_soak_progress.json" if soak_only else "idle_baseline_progress.json")
    if path.exists():
        raise RuntimeError("Prior idle baseline evidence exists; review it before choosing a new output")
    if (rt.results / "remote-preflight-retests").exists() or (rt.results / "remote_echo_preflight.json").exists():
        raise RuntimeError("Prior remote preflight evidence exists; archive it before starting a new run")
    foundation = json.loads((rt.results / "progress.json").read_text())
    if foundation.get("status") != "foundation_complete" or rt.owned():
        raise RuntimeError("Foundation is not complete or its emulator slot is still in use")
    if any(rt.port_used(p) for p in (22160, 22161, 22162)):
        raise RuntimeError("Emulator ports are not idle")
    report = {"status": "running", "started_unix": time.time(),
              "workload": "idle official_clean AVD; no game installed or running",
              "game_test": "deferred until all independent baseline work finishes, per user"}
    def stage(value):
        report["stage"] = value
        save_json(path, report)
        print(json.dumps({"stage": value, "unix": time.time()}), flush=True)
    def child(args, label, timeout, allowed=(0,)):
        result = rt.run([sys.executable, *args], label=label, timeout=timeout, check=False)
        report.setdefault("children", []).append({"args": args, "returncode": result.returncode,
                                                  "seconds": result.duration, "log": result.log})
        save_json(path, report)
        if result.returncode not in allowed:
            raise RuntimeError(f"{label} failed; see {result.log}")
        return result
    try:
        if not soak_only:
            stage("expansion_three_retests")
            child(["expansion_retests.py"], "expansion-three-retests", 1200)
            expansion = json.loads((rt.results / "expansion_retests.json").read_text())
            report["expansion_status"] = expansion["status"]
            if not expansion.get("source_backup_unchanged"):
                raise RuntimeError("Expansion source backup protection could not be verified")
            if any(r.get("task_owned_emulator_still_running") or r.get("ports_still_occupied") for r in expansion["rounds"]):
                raise RuntimeError("Expansion retests did not release the emulator slot")
            stage("manual_official_tool_expansion")
            child(["manual_expansion.py"], "manual-official-expansion", 1200, allowed=(0, 2))
            manual = json.loads((rt.results / "manual_expansion.json").read_text())
            report["manual_expansion_status"] = manual["status"]
            if not manual.get("source_backup_unchanged"):
                raise RuntimeError("Manual expansion source backup protection could not be verified")
            if manual["status"] == "failed":
                raise RuntimeError("Manual expansion failed; first evidence preserved for review before long observation")
            if rt.owned() or any(rt.port_used(p) for p in (22160, 22161, 22162)):
                raise RuntimeError("Manual expansion did not release the emulator slot")
        else:
            report["prior_retest_reports"] = [str(rt.results / name) for name in
                ("expansion_retests.json", "manual_expansion_retests.json", "idle_baseline_progress.json")]
        stage("start_clean_headless_control")
        rt.start()
        report["boot_s"] = rt.wait_boot()
        report["launch"] = rt.state()
        report["guest_game_package_query"] = rt.shell(
            "pm list packages com.hypergryph.arknights", label="idle-no-game-installed").stdout
        if "package:" in report["guest_game_package_query"]:
            raise RuntimeError("Non-game control contains a game package")
        rt.shell("input keyevent HOME", label="idle-control-launcher")
        shot = rt.adb(["exec-out", "screencap", "-p"], label="idle-control-screen", binary=True)
        screenshot = rt.results / "idle-control-screen.png"
        screenshot.write_bytes(shot.stdout)
        report["screen"] = {"path": str(screenshot), "sha256": sha256(screenshot)}
        child(["soak_tests.py", "--output", "results/idle_probe_prepare", "prepare"], "idle-probe-prepare", 120)
        stage("remote_echo_preflight")
        preflight = paired_remote_preflight(rt)
        report["remote_preflight"] = preflight
        stage("7200_second_idle_memory_and_network_control")
        if preflight["status"] == "passed":
            child(["run_soak.py", "--emulator-pid", str(rt.pid), "--wan-ipv4", preflight["resolved_ipv4"],
                   "--guest-wan-preflight", preflight["guest_log"],
                   "--workload-label", "idle_non_game_control"], "idle-dual-network-soak", 7380)
        else:
            child(["soak_tests.py", "--emulator-pid", str(rt.pid), "--output", "results/soak",
                   "soak", "--duration", "7200", "--workload-label", "idle_non_game_control"],
                  "idle-local-network-soak", 7380)
        report["execution_status"] = "completed"
        report["status"] = "measurements_complete"
    except Exception as exc:
        report.update(status="incomplete", error=str(exc), traceback=traceback.format_exc())
    finally:
        if rt.owned() and rt.state().get("avd") == "official_clean":
            try: report["cleanup_sync_stop"] = rt.stop(sync=True)
            except Exception as exc: report["cleanup_error"] = str(exc)
        report["finished_unix"] = time.time()
        save_json(path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--soak-only", action="store_true", help="Run the independent long control after separately completed retests")
    args = parser.parse_args()
    main(soak_only=args.soak_only)
