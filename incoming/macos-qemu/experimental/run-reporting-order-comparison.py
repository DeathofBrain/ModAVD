#!/usr/bin/env python3
"""Run the authorized private A9/B6 comparison; default mode only checks inputs."""
import argparse
import datetime
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
RUNTIME_ROOT = REPO / ".macos-work/reclaim-runtime"
DIST = REPO / ".macos-work/reclaim-audit-objects/distribution/emulator"
INSTALLED = HERE / "evidence/20261101-v7-budget-tail-installed/installed.json"
PROBE = REPO / ".macos-work/qa/reclaim-memory-probe-arm64"
PARAM = "/sys/module/page_reporting/parameters/page_reporting_order"
EXPECTED_INSTALLED_SHA = "2f5b53c262ead736a635be2ddd7e28b19f0abb99cc3cc5b723bb085d22fce4f3"
EXPECTED_FILES = {
    "qemu/darwin-aarch64/qemu-system-aarch64-headless": "f02215e01baf1b31e83e91987549aa0ebeb95669abc268e5ab0fd5ec9e9dac24",
    "lib64/libemugl_common.dylib": "21e6f074cc985fd74c07655bc3a727fcae18d479fec69a5a159fec7db8168929",
    "lib64/libgfxstream_backend.dylib": "a246f2a57c3ab4c50e9850f828e5f277ff88e24a613c17592d037c0f7af6f471",
    "emulator": "fc31b3adf31c7667a2d8119a443b16d84f8c50f584b6f7e985283b2462c28229",
}
EXPECTED_PROBE_SHA = "9ea596d0de18e350c5398719e96d870ebbd4ab23a8888766a96bfcbb8b880e04"
RUN_ENV = {
    "MODAVD_HVF_RAM_AUDIT": "1", "MODAVD_HVF_RAM_PAUSE_AUDIT": "1",
    "MODAVD_HVF_RAM_RECLAIM": "1", "MODAVD_HVF_RAM_RECLAIM_RESIDENCY": "1",
    "MODAVD_HVF_RAM_RECLAIM_TAIL_DIAG": "0",
}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    if path.exists():
        raise RuntimeError("refusing to overwrite evidence: " + str(path))
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def read(path):
    return json.loads(path.read_text())


def progress(stage, **fields):
    print(json.dumps({"time": now(), "stage": stage, **fields}), flush=True)


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def environment():
    env = {k: v for k, v in os.environ.items() if not k.startswith("MODAVD_HVF_RAM")}
    for key in ("DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "DYLD_INSERT_LIBRARIES",
                "DYLD_FALLBACK_LIBRARY_PATH", "LD_LIBRARY_PATH"):
        env.pop(key, None)
    env.update(RUN_ENV)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def command_receipt(argv, *, env=None, timeout=30):
    started = now()
    monotonic = time.monotonic_ns()
    try:
        result = subprocess.run([str(x) for x in argv], cwd=REPO, env=env,
                                capture_output=True, text=True, timeout=timeout)
        code, stdout, stderr, timed_out = result.returncode, result.stdout, result.stderr, False
    except subprocess.TimeoutExpired as error:
        def decoded(value):
            return value.decode(errors="replace") if isinstance(value, bytes) else value or ""
        code, stdout, stderr, timed_out = 124, decoded(error.stdout), decoded(error.stderr), True
    return {"started_utc": started, "captured_utc": now(),
            "started_monotonic_ns": monotonic, "ended_monotonic_ns": time.monotonic_ns(),
            "capture_kind": "subprocess.run", "argv": [str(x) for x in argv],
            "exit_code": code, "stdout": stdout, "stderr": stderr, "timed_out": timed_out}


def guests():
    result = subprocess.run(["ps", "-axo", "pid=,comm="], check=True,
                            capture_output=True, text=True)
    return [line.strip() for line in result.stdout.splitlines()
            if "qemu-system-" in line.split(None, 1)[-1]]


def preflight():
    if sys.platform != "darwin":
        raise RuntimeError("this frozen comparison is Darwin-only")
    if sha(INSTALLED) != EXPECTED_INSTALLED_SHA or read(INSTALLED)["files"] != EXPECTED_FILES:
        raise RuntimeError("frozen D9/f022 installation identity changed")
    checks = []
    for rel, expected in EXPECTED_FILES.items():
        path = DIST / rel
        actual = sha(path)
        if actual != expected:
            raise RuntimeError("private binary mismatch: " + rel)
        signed = command_receipt(["/usr/bin/codesign", "--verify", "--strict", path])
        if signed["exit_code"]:
            raise RuntimeError("private strict signature failure: " + rel)
        checks.append({"path": str(path), "sha256": actual, "strict_signature": signed})
    if sha(PROBE) != EXPECTED_PROBE_SHA:
        raise RuntimeError("existing Android memory probe hash changed")
    active = guests()
    return {"captured_utc": now(), "mode": "read-only preflight", "files": checks,
            "installed_receipt_sha256": EXPECTED_INSTALLED_SHA,
            "probe": {"path": str(PROBE), "sha256": sha(PROBE)},
            "environment": RUN_ENV, "fault_environment": "unset",
            "dyld_injection_and_search_overrides": "removed for both guest launches",
            "private_runtime": str(RUNTIME_ROOT), "active_android_guests": active,
            "execution_ready_no_android_guest": not active,
            "free_disk_bytes": shutil.disk_usage(RUNTIME_ROOT).free,
            "binary_source_layer": "private D9, independent of main G2 backend",
            "full_bh_body_available": False,
            "timing_fields": ["request_to_resume_return_us", "bh_to_before_final_log_us"],
            "runner_sha256": sha(Path(__file__)),
            "helpers": {name: sha(HERE / name) for name in
                        ("run-private-runtime.py", "run-guest-reclaim-probe.py",
                         "freeze-v7-launch.py", "analyze-reporting-order-run.py")},
            "runtime_source_sha256": sha(REPO / "ports/macos/runtime/runtime.py")}


class Run:
    def __init__(self, order, output, env):
        self.order, self.output, self.env = order, output, env
        self.private = module("private_order_runtime", HERE / "run-private-runtime.py")
        self.runtime = self.private.runtime.Runtime(RUNTIME_ROOT)
        self.usage = module("private_order_usage", REPO / "ports/macos/qa/host_rusage.py")
        self.log = RUNTIME_ROOT / "logs/emulator.log"
        self.state = self.boot = self.start_identity = None
        self.original_order = None
        self.setting_attempted = False
        self.serial = "emulator-20160"

    def identity(self, require_boot=True):
        if not self.state or not self.private.runtime.same_process(self.state, self.runtime.name()):
            raise RuntimeError("private PID/start/AVD process identity changed")
        usage = self.usage.snapshot(self.state["pid"])
        if self.start_identity is not None and usage["process_start_abstime"] != self.start_identity:
            raise RuntimeError("private native process start changed")
        if self.runtime.owned_device() != self.serial:
            raise RuntimeError("private ADB/AVD ownership changed")
        boot = self.runtime.adb("-s", self.serial, "shell", "cat",
                                "/proc/sys/kernel/random/boot_id").stdout.strip()
        if not re.fullmatch(r"[0-9a-f-]{36}", boot):
            raise RuntimeError("invalid guest boot UUID")
        if require_boot and self.boot is not None and boot != self.boot:
            raise RuntimeError("guest rebooted")
        return {"captured_utc": now(), "pid": self.state["pid"], "boot_id": boot,
                "serial": self.serial, "usage": usage}

    def root_command(self, script):
        before = self.identity()
        _, platform = self.runtime.component("platform-tools")
        record = command_receipt([platform / "adb", "-H", "127.0.0.1", "-P", "20150",
                                  "-s", self.serial, "shell", "su", "0", "sh", "-c",
                                  shlex.quote(script)], env=self.runtime.env(), timeout=20)
        record["before_identity"] = before
        record["after_identity"] = self.identity()
        return record

    def metadata(self, label, expected_order=None):
        before_offset = self.log.stat().st_size
        records = []
        try:
            for path in (PARAM, "/proc/buddyinfo", "/proc/pagetypeinfo", "/proc/zoneinfo",
                         "/proc/meminfo", "/proc/vmstat"):
                record = self.root_command("cat " + shlex.quote(path))
                record["guest_path"] = path
                records.append(record)
                if record["exit_code"]:
                    raise RuntimeError("required metadata read failed: " + path)
            value = records[0]["stdout"].strip()
            if not value.isdigit() or not 0 <= int(value) <= 10:
                raise RuntimeError("unexpected original/current reporting order")
            if expected_order is not None and int(value) != expected_order:
                raise RuntimeError("reporting order changed during comparison")
        except BaseException as error:
            write(self.output / (label + "-metadata-error.json"), {
                "label": label, "captured_utc": now(), "records": records,
                "error": str(error), "start_absolute_log_offset": before_offset,
                "end_absolute_log_offset": self.log.stat().st_size})
            raise
        record = {"label": label, "captured_utc": now(), "pid": self.state["pid"],
                  "boot_id": self.boot, "order": int(value), "records": records,
                  "start_absolute_log_offset": before_offset,
                  "end_absolute_log_offset": self.log.stat().st_size}
        write(self.output / (label + "-metadata.json"), record)
        return record

    def idle(self, label, seconds=10):
        progress(label, order=self.order, pid=self.state["pid"], seconds=seconds)
        begin = {"started_utc": now(), "start_absolute_log_offset": self.log.stat().st_size}
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if not self.private.runtime.same_process(self.state, self.runtime.name()):
                raise RuntimeError("private guest exited during idle")
            if shutil.disk_usage(RUNTIME_ROOT).free < 20 * 1024**3:
                raise RuntimeError("private runtime 20GiB floor crossed")
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        write(self.output / (label + ".json"), {**begin, "ended_utc": now(),
              "end_absolute_log_offset": self.log.stat().st_size, "seconds": seconds,
              "after_identity": self.identity()})

    def execute(self):
        if guests():
            raise RuntimeError("another Android guest exists; quota has not been released")
        self.output.mkdir()
        shutil.copy2(Path(__file__), self.output / "runner-script.py")
        shutil.copy2(INSTALLED, self.output / "installed.json")
        write(self.output / "preflight.json", preflight())
        log_offset = self.log.stat().st_size if self.log.exists() else 0
        (self.output / "log-offset.txt").write_text(str(log_offset) + "\n")
        primary_error = None
        stopped = False
        try:
            progress("starting-private-guest", order=self.order)
            start = command_receipt([sys.executable, HERE / "run-private-runtime.py", "--root",
                                     RUNTIME_ROOT, "start", "--emulator", DIST / "emulator",
                                     "--headless", "--memory", "2048"], env=self.env, timeout=75)
            write(self.output / "start.json", start)
            # A failed start may still leave a newly recorded, owned process.
            if (RUNTIME_ROOT / "emulator.json").exists():
                candidate = read(RUNTIME_ROOT / "emulator.json")
                if self.private.runtime.same_process(candidate, self.runtime.name()):
                    self.state = candidate
                    write(self.output / "emulator.json", self.state)
            if start["exit_code"] or self.state is None:
                raise RuntimeError("private guest start failed")
            self.start_identity = self.usage.snapshot(self.state["pid"])["process_start_abstime"]
            deadline = time.monotonic() + 180
            while True:
                if not self.private.runtime.same_process(self.state, self.runtime.name()):
                    raise RuntimeError("guest exited before boot")
                try:
                    ready = self.runtime.adb("-s", self.serial, "shell", "getprop",
                                             "sys.boot_completed", timeout=5).stdout.strip() == "1"
                    if ready:
                        self.boot = self.identity(require_boot=False)["boot_id"]
                        break
                except (subprocess.SubprocessError, RuntimeError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("bounded boot wait expired")
                progress("waiting-for-private-boot", order=self.order, pid=self.state["pid"])
                time.sleep(2)
            identity = self.identity()
            kernel = self.runtime.adb("-s", self.serial, "shell", "uname", "-r").stdout.strip()
            page = self.runtime.adb("-s", self.serial, "shell", "getconf", "PAGESIZE").stdout.strip()
            if "7e35917775b8" not in kernel or page != "4096":
                raise RuntimeError("fixed guest kernel or actual 4KiB page size mismatch")
            maps = command_receipt(["/usr/sbin/lsof", "-a", "-p", str(self.state["pid"]), "-Fn"])
            paths = [line[1:] for line in maps["stdout"].splitlines()
                     if line.startswith("n") and line.endswith(tuple(EXPECTED_FILES)[:3])]
            # Check the loaded core/backend/common paths without exporting unrelated open files.
            expected_loaded = [str(DIST / rel) for rel in EXPECTED_FILES if rel != "emulator"]
            if maps["exit_code"] or not all(path in paths for path in expected_loaded):
                raise RuntimeError("loaded private D9 core/backend/common identity mismatch")
            write(self.output / "boot-identity.json", {**identity, "kernel": kernel,
                  "guest_page_bytes": int(page), "loaded_private_paths": paths,
                  "four_file_hashes": EXPECTED_FILES, "environment": RUN_ENV})
            initial = self.metadata("before-setting")
            self.original_order = initial["order"]
            features = self.root_command(
                'for d in /sys/bus/virtio/devices/*; do '
                '[ "$(cat "$d/device")" = 0x0005 ] || continue; '
                'printf "DEVICE=%s\\n" "$d"; printf "FEATURES="; cat "$d/features"; '
                'printf "DRIVER="; readlink "$d/driver"; done')
            write(self.output / "reporter-features.json", features)
            match = re.search(r"^FEATURES=([01]{6,64})$", features["stdout"], re.M)
            if features["exit_code"] or not match or match[1][5] != "1" or "virtio_balloon" not in features["stdout"]:
                raise RuntimeError("reporting feature/driver not confirmed")
            script = ("set -eu; before=$(cat /proc/sys/kernel/random/boot_id); "
                      f"[ \"$before\" = {shlex.quote(self.boot)} ]; "
                      "printf 'BOOT_BEFORE=%s\\n' \"$before\"; "
                      f"printf 'ORDER_BEFORE='; cat {PARAM}; "
                      f"echo '{self.order}' > {PARAM}; "
                      f"printf 'ORDER_AFTER='; cat {PARAM}; "
                      "after=$(cat /proc/sys/kernel/random/boot_id); "
                      "printf 'BOOT_AFTER=%s\\n' \"$after\"; [ \"$before\" = \"$after\" ]")
            self.setting_attempted = True
            setting = self.root_command(script)
            write(self.output / "explicit-order-setting.json", setting)
            expected = re.search(r"^ORDER_AFTER=(\d+)$", setting["stdout"], re.M)
            if setting["exit_code"] or not expected or int(expected[1]) != self.order:
                raise RuntimeError("explicit reporting-order write/readback failed")
            window = {"order": self.order, "original_order": initial["order"],
                      "pid": self.state["pid"], "boot_id": self.boot,
                      "started_utc": now(), "start_absolute_log_offset": self.log.stat().st_size,
                      "setting_receipt_sha256": sha(self.output / "explicit-order-setting.json")}
            self.metadata("after-setting", self.order)
            self.idle("idle-before-probes")
            for size in (256, 512):
                self.identity()
                progress("memory-probe", order=self.order, pid=self.state["pid"], churn_mib=size)
                probe = command_receipt([sys.executable, HERE / "run-guest-reclaim-probe.py",
                                        "--expected-pid", str(self.state["pid"]), "--output",
                                        self.output / ("memory" + str(size)), "--binary", PROBE,
                                        "--churn-mib", str(size), "--rounds", "3",
                                        "--release-seconds", "8"], env=self.env, timeout=150)
                write(self.output / ("memory" + str(size) + "-command.json"), probe)
                if probe["exit_code"]:
                    raise RuntimeError("bounded live/zero/reuse memory probe failed")
                capture = read(self.output / ("memory" + str(size)) / "capture.json")
                if not capture["pass"] or not capture["guest_directory_cleaned"] or capture["boot_id"] != self.boot:
                    raise RuntimeError("memory probe identity/correctness/cleanup failed")
                self.metadata("after-memory" + str(size), self.order)
            self.idle("idle-after-probes")
            self.metadata("final", self.order)
            window.update(ended_utc=now(), end_absolute_log_offset=self.log.stat().st_size,
                          final_identity=self.identity())
            write(self.output / "comparison-window.json", window)
        except BaseException as error:
            primary_error = error
            write(self.output / "run-error.json", {"captured_utc": now(), "type": type(error).__name__,
                  "error": str(error), "order": self.order})
        finally:
            cleanup_error = None
            if self.state is not None:
                try:
                    before_state = read(RUNTIME_ROOT / "emulator.json")
                    if any(before_state[key] != self.state[key] for key in ("pid", "started")):
                        raise RuntimeError("refusing to stop a different private guest")
                    if self.private.runtime.same_process(self.state, self.runtime.name()):
                        if self.start_identity is not None:
                            self.identity(require_boot=self.boot is not None)
                        progress("stopping-private-guest", order=self.order, pid=self.state["pid"])
                        stop = command_receipt([sys.executable, HERE / "run-private-runtime.py", "--root",
                                                RUNTIME_ROOT, "stop"], env=self.env, timeout=45)
                        stop.update(expected_pid=self.state["pid"], before_state=before_state,
                                    runtime_source_sha256=sha(REPO / "ports/macos/runtime/runtime.py"))
                        write(self.output / "stop-receipt.json", stop)
                        # same_process can become false before ps removes the dying QEMU.
                        deadline = time.monotonic() + 5
                        teardown = []
                        while True:
                            selected = [line for line in guests()
                                        if line.split(None, 1)[0] == str(self.state["pid"])]
                            teardown.append({"captured_utc": now(), "selected_processes": selected})
                            if not selected or time.monotonic() >= deadline:
                                break
                            time.sleep(.1)
                        write(self.output / "post-stop-process-checks.json", teardown)
                        stopped = stop["exit_code"] == 0 and not self.private.runtime.same_process(self.state, self.runtime.name())
                        stopped = stopped and not selected
                    if not stopped and self.setting_attempted and self.original_order is not None:
                        # Restore only this still-owned boot after a failed normal stop.
                        script = ("set -eu; before=$(cat /proc/sys/kernel/random/boot_id); "
                                  f"[ \"$before\" = {shlex.quote(self.boot)} ]; "
                                  f"echo '{self.original_order}' > {PARAM}; "
                                  f"printf 'ORDER_RESTORED='; cat {PARAM}; "
                                  "after=$(cat /proc/sys/kernel/random/boot_id); "
                                  "[ \"$before\" = \"$after\" ]")
                        restore = self.root_command(script)
                        write(self.output / "failed-stop-order-restoration.json", restore)
                        match = re.search(r"^ORDER_RESTORED=(\d+)$", restore["stdout"], re.M)
                        if restore["exit_code"] or not match or int(match[1]) != self.original_order:
                            raise RuntimeError("retained guest original-order restoration failed")
                except BaseException as error:
                    cleanup_error = str(error)
                    write(self.output / "cleanup-error.json", {"captured_utc": now(), "error": cleanup_error})
            if self.log.exists():
                (self.output / "emulator.log").write_bytes(self.log.read_bytes()[log_offset:])
            write(self.output / "outcome.json", {"captured_utc": now(), "order": self.order,
                  "pid": self.state["pid"] if self.state else None, "stopped_normally": stopped,
                  "workload_completed": primary_error is None, "cleanup_error": cleanup_error,
                  "full_bh_body_available": False})
        if not stopped:
            raise RuntimeError("private guest lacks successful normal stop; do not start next run")
        if primary_error is not None:
            raise primary_error
        argv = [sys.executable, HERE / "freeze-v7-launch.py", "--launch", self.output,
                "--expected-pid", str(self.state["pid"]), "--mode", "positive",
                "--stop-receipt", self.output / "stop-receipt.json"]
        for size in (256, 512):
            argv += ["--memory-evidence", self.output / ("memory" + str(size))]
        frozen = command_receipt(argv, env=self.env, timeout=45)
        write(self.output / "freeze-command.json", frozen)
        if frozen["exit_code"]:
            raise RuntimeError("stopped launch freezer failed")
        analyzed = command_receipt([sys.executable, HERE / "analyze-reporting-order-run.py",
                                    "--launch", self.output], env=self.env, timeout=45)
        write(self.output / "analyze-command.json", analyzed)
        if analyzed["exit_code"]:
            raise RuntimeError("reporting-order scoped analysis failed")
        progress("run-completed", order=self.order, pid=self.state["pid"], output=str(self.output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute", action="store_true", help="Only invoke after root explicitly transfers guest quota")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise RuntimeError("refusing to overwrite evidence: " + str(output))
    output.mkdir(parents=True)
    write(output / "preflight.json", preflight())
    if not args.execute:
        progress("preflight-only-no-guest-start", output=str(output))
        return
    if guests():
        raise RuntimeError("another guest is running; root still owns the quota")
    if shutil.disk_usage(RUNTIME_ROOT).free < 26 * 1024**3:
        raise RuntimeError("launch reserve requires26GiB to preserve20GiB floor")
    with (RUNTIME_ROOT / "reporting-order-comparison.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for order in (9, 6):
            Run(order, output / ("order" + str(order)), environment()).execute()
    write(output / "comparison-completed.json", {"captured_utc": now(), "orders": [9, 6],
          "all_guests_stopped": True, "same_four_file_hashes": EXPECTED_FILES,
          "full_bh_body_available": False, "no_production_default_promotion": True})


if __name__ == "__main__":
    main()
