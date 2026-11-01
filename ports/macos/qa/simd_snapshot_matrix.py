#!/usr/bin/env python3
"""Check live ARM64 Q0..Q31 across owned private-guest pauses and snapshots."""
import argparse
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import time
import uuid


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def events(text):
    lines = text.splitlines()
    if text and not text.endswith("\n"):
        lines = lines[:-1]  # A concurrent writer may not yet have completed it.
    return [json.loads(line) for line in lines if line.startswith("{")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-pid", type=int, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    base = Path(__file__).resolve().parents[3]
    private = load("simd_private_runtime", base / "incoming/macos-qemu/experimental/run-private-runtime.py")
    runtime = private.runtime.Runtime(base / ".macos-work/reclaim-runtime")
    state = json.loads((runtime.root / "emulator.json").read_text())
    if state["pid"] != args.expected_pid or not private.runtime.same_process(state, runtime.name()):
        raise RuntimeError("private emulator process identity mismatch")
    profile = json.loads((runtime.root / "reclaim-profile.json").read_text())
    if profile != {"schema": 1, "adb": 20150, "console": 20160, "kind": "disposable-reclaim-audit"}:
        raise RuntimeError("private runtime port/ownership profile mismatch")
    serial = runtime.owned_device()
    binary = args.binary.resolve(strict=True)
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("refusing to overwrite evidence")
    private.runtime.space(runtime.root, 20 * 1024 ** 3)
    output.mkdir(parents=True)
    identity = uuid.uuid4().hex
    guest_dir = "/data/local/tmp/modavd-simd-" + identity
    guest_binary = guest_dir + "/probe"
    out_file, err_file, exit_file = (guest_dir + "/" + name for name in ("out.jsonl", "err", "exit"))
    snapshot_names = []
    transcript, observations, cleanup = [], [], []
    started = time.monotonic()
    log = runtime.root / "logs/emulator.log"
    log_offset = log.stat().st_size
    record = {"captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "pid": args.expected_pid, "serial": serial, "adb_port": 20150,
              "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "source_sha256": {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                                for name in ("simd_register_probe.c", "simd_register_probe.S")},
              "pass": False, "negative_is_probe_sensitivity_only": True,
              "limits": ["Samples one busy worker on guest CPU0; does not cover every vCPU or every guest SIMD instruction.",
                         "Successful pin/FIFO requests improve the chance that Q registers are live at each host pause; interrupt handlers can still run.",
                         "Negative injection changes Q30 inside the probe; it is not an old-emulator regression test."],
              "commands": transcript, "observations": observations, "cleanup": cleanup}

    def command(*parts, timeout=30, allow_failure=False):
        before = time.monotonic_ns()
        try:
            result = runtime.adb("-s", serial, *parts, timeout=timeout)
        except subprocess.CalledProcessError as error:
            result = error
            if not allow_failure:
                transcript.append({"argv": list(parts), "host_monotonic_ns": before,
                                   "returncode": error.returncode, "stdout": error.stdout, "stderr": error.stderr})
                raise
        except subprocess.TimeoutExpired as error:
            def decoded(value):
                return value.decode(errors="replace") if isinstance(value, bytes) else (value or "")
            result = subprocess.CompletedProcess(list(parts), 124, decoded(error.stdout), decoded(error.stderr))
            transcript.append({"argv": list(parts), "host_monotonic_ns": before,
                               "elapsed_seconds": (time.monotonic_ns() - before) / 1e9,
                               "returncode": 124, "stdout": result.stdout, "stderr": result.stderr,
                               "timeout": True})
            if not allow_failure:
                raise
            return result
        transcript.append({"argv": list(parts), "host_monotonic_ns": before,
                           "elapsed_seconds": (time.monotonic_ns() - before) / 1e9,
                           "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        return result

    def console(*parts):
        result = command("emu", "avd", *parts, timeout=45)
        if "OK" not in result.stdout.splitlines() or any(line.startswith("KO") for line in result.stdout.splitlines()):
            raise RuntimeError("emulator console rejected " + " ".join(parts))
        return result.stdout

    def read_output():
        try:
            result = runtime.adb("-s", serial, "shell", "cat", out_file)
        except subprocess.CalledProcessError as error:
            if "No such file" not in error.stderr:
                raise
            return "", []
        parsed = events(result.stdout)
        observations.append({"host_monotonic_ns": time.monotonic_ns(), "events": parsed})
        return result.stdout, parsed

    guest_pid = None
    launcher = None
    positive_text = ""
    boundaries = []
    record["live_boundaries"] = boundaries

    def live_boundary(label, previous_checks=None, allow_done=False):
        nonlocal positive_text
        boundary_deadline = time.monotonic() + 5
        while True:
            positive_text, parsed = read_output()
            done = [event for event in parsed if event.get("event") == "done"]
            checks = max((event.get("checks", 0) for event in parsed), default=0)
            if any(event.get("errors", 0) for event in parsed) or (done and not allow_done):
                raise RuntimeError("probe was not live at " + label)
            if checks > 0 and (previous_checks is None or checks > previous_checks):
                boundaries.append({"label": label, "host_monotonic_ns": time.monotonic_ns(),
                                   "checks": checks, "done": bool(done)})
                return checks
            if time.monotonic() >= boundary_deadline:
                raise RuntimeError("probe made no progress at " + label)
            time.sleep(0.1)
    try:
        command("root")
        command("wait-for-device")
        if runtime.owned_device() != serial:
            raise RuntimeError("guest identity changed after root")
        record["guest_uid"] = command("shell", "id", "-u").stdout.strip()
        boot_id = command("shell", "cat", "/proc/sys/kernel/random/boot_id").stdout.strip()
        record["boot_id"] = boot_id
        command("shell", "mkdir", "-m", "700", guest_dir)
        command("push", str(binary), guest_binary)
        command("shell", "chmod", "700", guest_binary)
        negative = command("shell", guest_binary, "--seconds", "1", "--bad-q30", "--fifo", allow_failure=True)
        (output / "negative.jsonl").write_text(negative.stdout)
        negative_done = [event for event in events(negative.stdout) if event.get("event") == "done"]
        record["negative_sensitivity_pass"] = (negative.returncode == 1 and len(negative_done) == 1 and
                                               negative_done[0].get("errors") == 1 and
                                               negative_done[0].get("mismatch_word") == 60 and
                                               negative_done[0].get("pass") is False)
        if not record["negative_sensitivity_pass"]:
            raise RuntimeError("deliberate Q30 mismatch was not detected")
        script = (shlex.quote(guest_binary) + " --seconds 45 --fifo > " + shlex.quote(out_file) +
                  " 2> " + shlex.quote(err_file) + "; echo $? > " + shlex.quote(exit_file))
        background = "nohup sh -c " + shlex.quote(script) + " < /dev/null > /dev/null 2>&1 & echo $!"
        # Some adbd versions keep a shell service open until all descendants exit.
        # Launch that bounded service asynchronously; results come from UUID files.
        _, platform = runtime.component("platform-tools")
        launch_argv = [str(platform / "adb"), "-H", "127.0.0.1", "-P", "20150", "-s", serial, "shell", background]
        launcher = subprocess.Popen(launch_argv, env=runtime.env(), text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        record["launcher_argv"] = launch_argv
        deadline = time.monotonic() + 140
        while True:
            positive_text, parsed = read_output()
            ready = [event for event in parsed if event.get("event") == "ready"]
            progress = [event for event in parsed if event.get("event") == "progress" and event.get("checks", 0) > 0]
            if ready and progress:
                record["ready"] = ready[-1]
                break
            if time.monotonic() >= deadline or any(event.get("event") == "done" for event in parsed):
                raise RuntimeError("positive probe did not reach live register checking")
            time.sleep(0.2)
        for cycle in range(10):
            checks = live_boundary("pause_" + str(cycle) + "_before")
            console("stop")
            if "virtual device is stopped" not in console("status"):
                raise RuntimeError("guest stop status mismatch")
            console("start")
            if "virtual device is running" not in console("status"):
                raise RuntimeError("guest start status mismatch")
            live_boundary("pause_" + str(cycle) + "_after", checks)
        record["pause_cycles"] = 10
        for cycle in range(3):
            checks = live_boundary("snapshot_" + str(cycle) + "_before")
            private.runtime.space(runtime.root, 20 * 1024 ** 3)
            name = "simd_" + identity + "_" + str(cycle)
            snapshot_names.append(name)
            console("snapshot", "save", name)
            console("snapshot", "load", name)
            command("wait-for-device", timeout=30)
            if runtime.owned_device() != serial:
                raise RuntimeError("private AVD identity changed after snapshot")
            console("snapshot", "delete", name)
            snapshot_names.remove(name)
            live_boundary("snapshot_" + str(cycle) + "_after", checks, allow_done=cycle == 2)
        record["snapshot_cycles"] = 3
        while True:
            positive_text, parsed = read_output()
            done = [event for event in parsed if event.get("event") == "done"]
            if done:
                record["done"] = done[-1]
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("bounded positive probe timed out")
            time.sleep(0.5)
        positive_exit = command("shell", "cat", exit_file).stdout.strip()
        record["positive_exit"] = positive_exit
        record["positive_stderr"] = command("shell", "cat", err_file).stdout
        record["boot_id_unchanged"] = command("shell", "cat", "/proc/sys/kernel/random/boot_id").stdout.strip() == boot_id
        record["host_process_unchanged"] = private.runtime.same_process(state, runtime.name())
        record["pass"] = bool(record["negative_sensitivity_pass"] and positive_exit == "0" and
                              record["done"].get("pass") is True and record["done"].get("checks", 0) > 0 and
                              record["done"].get("errors") == 0 and record["boot_id_unchanged"] and
                              record["host_process_unchanged"] and record["ready"].get("pin_error") == 0)
    except Exception as error:
        record["error"] = str(error)
        raise
    finally:
        try:
            if launcher is not None:
                if launcher.poll() is None:
                    launcher.terminate()
                try:
                    launch_out, launch_err = launcher.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    launcher.kill()
                    launch_out, launch_err = launcher.communicate(timeout=5)
                record["launcher_result"] = {"returncode": launcher.returncode, "stdout": launch_out, "stderr": launch_err}
                if launch_out.strip().isdecimal():
                    guest_pid = int(launch_out.strip())
                    record["guest_background_pid"] = guest_pid
            if private.runtime.same_process(state, runtime.name()):
                command("wait-for-device", timeout=30, allow_failure=True)
                for name in list(snapshot_names):
                    result = command("emu", "avd", "snapshot", "delete", name, allow_failure=True)
                    removed = result.returncode == 0 and result.stdout.splitlines() == ["OK"]
                    cleanup.append({"snapshot": name, "removed": removed, "returncode": result.returncode, "stdout": result.stdout})
                    if removed:
                        snapshot_names.remove(name)
                command("emu", "avd", "start", allow_failure=True)
                for pid in {guest_pid, record.get("ready", {}).get("pid")} - {None}:
                    # Recheck the UUID path before signaling either created PID.
                    result = command("shell", "cat", "/proc/" + str(pid) + "/cmdline", allow_failure=True)
                    if guest_binary in result.stdout:
                        result = command("shell", "kill", str(pid), allow_failure=True)
                        cleanup.append({"verified_guest_pid": pid, "returncode": result.returncode})
                result = command("shell", "rm", "-f", guest_binary, out_file, err_file, exit_file, allow_failure=True)
                cleanup.append({"exact_files": result.returncode == 0})
                result = command("shell", "rmdir", guest_dir, allow_failure=True)
                cleanup.append({"exact_directory": result.returncode == 0})
        except Exception as error:
            record["cleanup_error"] = str(error)
        finally:
            record["cleanup_success"] = (not snapshot_names and not record.get("cleanup_error") and
                                           any(item.get("exact_files") for item in cleanup) and
                                           any(item.get("exact_directory") for item in cleanup))
            record["pass"] = record["pass"] and record["cleanup_success"]
            (output / "positive.jsonl").write_text(positive_text)
            (output / "emulator-segment.log").write_bytes(log.read_bytes()[log_offset:])
            record["elapsed_seconds"] = time.monotonic() - started
            (output / "capture.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({key: record.get(key) for key in ("pid", "pass", "pause_cycles", "snapshot_cycles", "ready", "done", "elapsed_seconds")}))
    if not record["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
