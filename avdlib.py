"""Task-owned Google Android Emulator baseline runtime and evidence logging."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent
SDK = ROOT / "sdk"
ADB_PORT = 22150
CONSOLE_PORT = 22160
GRPC_PORT = 22162
SERIAL = f"emulator-{CONSOLE_PORT}"
MIN_FREE = 20 * 1024**3


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(4 * 1024**2), b""):
            h.update(block)
    return h.hexdigest()


@dataclass
class Result:
    args: list[str]
    returncode: int
    stdout: str | bytes
    stderr: str | bytes
    duration: float
    log: str


class Runtime:
    def __init__(self, avd_name="official_clean"):
        self.root = ROOT
        self.sdk = SDK
        self.emulator = SDK / "emulator/emulator"
        self.adb_path = SDK / "platform-tools/adb"
        self.avd_root = ROOT / "avd"
        self.logs = ROOT / "logs"
        self.results = ROOT / "results"
        self.adb_port = ADB_PORT
        self.console_port = CONSOLE_PORT
        self.serial = SERIAL
        self.avd_name = avd_name
        self.state_path = ROOT / "state.json"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("ANDROID_", "ADB_", "QEMU_", "QT_", "VK_"))
                    and k not in ("DYLD_LIBRARY_PATH", "DYLD_INSERT_LIBRARIES")}
        self.env.update({
            "ANDROID_HOME": str(SDK), "ANDROID_SDK_ROOT": str(SDK),
            "ANDROID_AVD_HOME": str(self.avd_root),
            "ANDROID_USER_HOME": str(ROOT / "user"),
            "ANDROID_EMULATOR_HOME": str(ROOT / "user"),
            "ANDROID_ADB_SERVER_PORT": str(ADB_PORT),
            "ADB_SERVER_SOCKET": f"tcp:127.0.0.1:{ADB_PORT}",
            "ADB_EMULATOR_SCAN_PORTS": str(CONSOLE_PORT + 1),
            "ADB_MDNS_AUTO_CONNECT": "", "ADB_MDNS_OPENSCREEN": "0",
            "ADB_VENDOR_KEYS": str(ROOT / "keys"),
        })
        for p in (self.avd_root, self.logs, self.results, ROOT / "user", ROOT / "keys"):
            p.mkdir(parents=True, exist_ok=True)

    @property
    def avd_dir(self):
        return self.avd_root / (self.avd_name + ".avd")

    @property
    def config_path(self):
        return self.avd_dir / "config.ini"

    @property
    def pid(self):
        return self.state().get("pid")

    def state(self):
        return json.loads(self.state_path.read_text()) if self.state_path.exists() else {}

    def space(self, additional=0):
        free = shutil.disk_usage(ROOT).free
        if free - additional < MIN_FREE:
            raise RuntimeError(f"disk space stop: {free / 1024**3:.2f} GiB free; reserve 20 GiB")
        return free

    def run(self, cmd, label="command", timeout=120, check=True, binary=False, env=None, input=None):
        cmd = [str(s) for s in cmd]
        prefix = f"{time.time_ns()}-{re.sub('[^A-Za-z0-9_.-]', '_', label)[:65]}"
        start = time.monotonic()
        rc, out, err = -1, b"", b""
        timed_out = False
        try:
            cp = subprocess.run(cmd, cwd=ROOT, env=env or self.env, input=input,
                                capture_output=True, timeout=timeout)
            rc, out, err = cp.returncode, cp.stdout, cp.stderr
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            rc, out, err = 124, exc.stdout or b"", exc.stderr or b""
        duration = time.monotonic() - start
        path = self.logs / prefix
        path.with_suffix(".stdout").write_bytes(out)
        path.with_suffix(".stderr").write_bytes(err)
        record = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "cmd": cmd,
                  "returncode": rc, "duration_s": duration, "timeout": timed_out,
                  "stdout": str(path.with_suffix(".stdout").relative_to(ROOT)),
                  "stderr": str(path.with_suffix(".stderr").relative_to(ROOT))}
        with (self.logs / "commands.jsonl").open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        res = Result(cmd, rc, out if binary else out.decode("utf-8", "replace"),
                     err if binary else err.decode("utf-8", "replace"), duration, str(path))
        if check and rc:
            raise RuntimeError(f"command failed ({rc}): {cmd!r}; log={path}; stderr={err[-1200:]!r}")
        return res

    def adb(self, args, label="adb", timeout=60, check=True, binary=False, input=None):
        return self.run([self.adb_path, "-H", "127.0.0.1", "-P", str(ADB_PORT),
                         "-s", SERIAL, *args], label, timeout, check, binary, input=input)

    def shell(self, command, **kwargs):
        return self.adb(["shell", command], **kwargs)

    def ensure_server(self):
        info_path = ROOT / "adb-server.json"
        if self.port_used(ADB_PORT):
            if not info_path.exists():
                raise RuntimeError("private ADB port already occupied by an unowned process")
            previous = json.loads(info_path.read_text())
            ps = self.run(["ps", "-p", str(previous["pid"]), "-o", "command="],
                          "verify-adb-server", check=False)
            if str(self.adb_path) not in ps.stdout:
                raise RuntimeError("private ADB server identity no longer matches")
            return
        server_env = dict(self.env)
        server_env.pop("ADB_SERVER_SOCKET", None)
        command = [str(self.adb_path), "-L", f"tcp:{ADB_PORT}",
                   "--one-device", SERIAL, "server", "nodaemon"]
        log = self.logs / "private-adb-server.log"
        with log.open("ab") as f:
            proc = subprocess.Popen(command, env=server_env, cwd=ROOT, stdout=f, stderr=f,
                                    start_new_session=True)
        deadline = time.monotonic() + 15
        while not self.port_used(ADB_PORT) and time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"private ADB exited {proc.returncode}; see {log}")
            time.sleep(0.1)
        if not self.port_used(ADB_PORT):
            proc.terminate()
            raise RuntimeError(f"private ADB did not listen; see {log}")
        save_json(info_path, {"pid": proc.pid, "port": ADB_PORT, "path": str(self.adb_path),
                              "command": command, "log": str(log)})

    @staticmethod
    def port_used(port):
        with socket.socket() as sock:
            return sock.connect_ex(("127.0.0.1", port)) == 0

    def owned(self):
        state = self.state()
        if not state.get("pid"):
            return False
        ps = self.run(["ps", "-p", str(state["pid"]), "-o", "lstart=", "-o", "command="],
                      "verify-emulator", check=False)
        return (ps.returncode == 0 and state["identity"] == ps.stdout.strip()
                and str(self.sdk / "emulator") in ps.stdout)

    def create_avd(self, name=None, size_gib=8):
        name = name or self.avd_name
        directory = self.avd_root / (name + ".avd")
        if directory.exists():
            raise RuntimeError(f"AVD already exists: {directory}")
        self.space()
        directory.mkdir()
        config = {
            "AvdId": name, "avd.ini.displayname": name, "avd.ini.encoding": "UTF-8",
            "PlayStore.enabled": "false", "abi.type": "arm64-v8a", "tag.id": "google_apis",
            "tag.display": "Google APIs", "image.sysdir.1": "system-images/android-34/google_apis/arm64-v8a/",
            "hw.cpu.arch": "arm64", "hw.cpu.ncore": "4", "hw.ramSize": "4096",
            "hw.lcd.width": "1280", "hw.lcd.height": "720", "hw.lcd.density": "240",
            "hw.initialOrientation": "landscape", "hw.keyboard": "yes",
            "hw.gpu.enabled": "yes", "hw.gpu.mode": "host", "hw.audioInput": "no",
            "hw.battery": "yes", "hw.gps": "yes", "hw.accelerometer": "yes",
            "disk.dataPartition.size": f"{size_gib}G", "disk.cachePartition": "yes",
            "disk.cachePartition.size": "66MB", "vm.heapSize": "512",
            "fastboot.forceColdBoot": "yes", "showDeviceFrame": "no",
        }
        (directory / "config.ini").write_text("".join(f"{k}={v}\n" for k, v in config.items()))
        self.write_avd_ini(name)
        return directory

    def write_avd_ini(self, name):
        directory = self.avd_root / (name + ".avd")
        (self.avd_root / (name + ".ini")).write_text(
            f"avd.ini.encoding=UTF-8\npath={directory}\ntarget=android-34\n")

    def clone_stopped(self, src_avd, dst_name):
        if self.owned():
            raise RuntimeError("clone requires task emulator to be stopped")
        src = Path(src_avd)
        if not src.is_absolute():
            src = self.avd_root / (str(src_avd).removesuffix(".avd") + ".avd")
        dst = self.avd_root / (dst_name + ".avd")
        if dst.exists():
            raise RuntimeError(f"clone destination exists: {dst}")
        self.space()
        self.run(["cp", "-cR", src, dst], "clone-stopped-avd", timeout=300)
        for p in dst.glob("*.lock"):
            if p.is_dir(): shutil.rmtree(p)
            else: p.unlink()
        text = (dst / "config.ini").read_text()
        text = re.sub(r"(?m)^AvdId=.*$", f"AvdId={dst_name}", text)
        text = re.sub(r"(?m)^avd.ini.displayname=.*$", f"avd.ini.displayname={dst_name}", text)
        (dst / "config.ini").write_text(text)
        self.write_avd_ini(dst_name)
        return dst

    def start(self, extra_args=None, avd_name=None, window=False):
        self.space()
        if self.owned():
            raise RuntimeError("a task emulator is already running")
        if avd_name: self.avd_name = avd_name
        self.ensure_server()
        for port in (CONSOLE_PORT, CONSOLE_PORT + 1, GRPC_PORT):
            if self.port_used(port):
                raise RuntimeError(f"emulator port occupied: {port}")
        args = [str(self.emulator), "-avd", self.avd_name, "-no-snapshot", "-gpu", "host",
                "-memory", "4096", "-cores", "4", "-ports", f"{CONSOLE_PORT},{CONSOLE_PORT+1}",
                "-grpc", str(GRPC_PORT), "-grpc-use-jwt", "-no-audio", "-no-boot-anim", "-no-metrics", "-verbose"]
        if not window: args.append("-no-window")
        args.extend(str(a) for a in (extra_args or []))
        logfile = self.logs / f"emulator-{time.time_ns()}-{self.avd_name}.log"
        with logfile.open("wb") as f:
            proc = subprocess.Popen(args, cwd=ROOT, env=self.env, stdout=f, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        time.sleep(0.4)
        ident = self.run(["ps", "-p", str(proc.pid), "-o", "lstart=", "-o", "command="],
                         "emulator-identity", check=False).stdout.strip()
        state = {"pid": proc.pid, "identity": ident, "args": args, "avd": self.avd_name,
                 "started_unix": time.time(), "boot_started_monotonic": time.monotonic(),
                 "log": str(logfile), "window": window, "serial": SERIAL,
                 "adb_port": ADB_PORT, "gpu": "host"}
        save_json(self.state_path, state)
        with (self.logs / "launches.jsonl").open("a") as f:
            f.write(json.dumps(state) + "\n")
        if proc.poll() is not None:
            raise RuntimeError(f"emulator exited {proc.returncode}; see {logfile}")
        subprocess.Popen([self.env.get("PYTHON", "python3"), str(ROOT / "watchdog.py"),
                          "--pid", str(proc.pid), "--identity", ident], cwd=ROOT,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return proc.pid

    def wait_boot(self, timeout=240):
        start = time.monotonic()
        deadline = start + timeout
        while time.monotonic() < deadline:
            if not self.owned():
                raise RuntimeError(f"emulator exited while booting; see {self.state().get('log')}")
            cp = self.adb(["shell", "getprop", "sys.boot_completed"], "wait-boot", timeout=10, check=False)
            if cp.returncode == 0 and cp.stdout.strip() == "1":
                self.shell("input keyevent 82", label="unlock", check=False)
                return time.monotonic() - self.state()["boot_started_monotonic"]
            time.sleep(2)
        raise TimeoutError(f"AVD did not boot in {timeout}s")

    def stop(self, sync=False):
        start = time.monotonic()
        if not self.owned():
            return {"already_stopped": True, "duration_s": 0}
        if sync: self.adb(["shell", "sync"], "shutdown-sync", timeout=60)
        kill_started = time.monotonic()
        result = self.adb(["emu", "kill"], "emu-kill", timeout=30, check=False)
        kill_finished = time.monotonic()
        deadline = time.monotonic() + 60
        # The process may change its command title during teardown before its
        # listeners close. Wait for both identity loss and port release.
        ports = (CONSOLE_PORT, CONSOLE_PORT + 1, GRPC_PORT)
        while (self.owned() or any(self.port_used(p) for p in ports)) and time.monotonic() < deadline:
            time.sleep(0.2)
        alive = self.owned() or any(self.port_used(p) for p in ports)
        state = self.state()
        state.update({"stopped_unix": time.time(), "stop_sync": sync,
                      "stop_duration_s": time.monotonic() - start, "stop_returncode": result.returncode,
                      "still_alive": alive})
        save_json(self.state_path, state)
        if alive: raise RuntimeError("emu kill did not stop the task-owned emulator")
        return {"duration_s": time.monotonic() - start, "returncode": result.returncode,
                "kill_started_monotonic": kill_started, "kill_finished_monotonic": kill_finished}

    def collect_environment(self):
        data = {"collected": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "source": "Google official packages", "avd": self.avd_name,
                "serial": SERIAL, "launch": self.state(), "free_bytes": self.space()}
        commands = {
            "emulator_version": [self.emulator, "-version"],
            "acceleration": [self.emulator, "-accel-check"],
            "host_arch": ["uname", "-m"], "macos": ["sw_vers"],
            "chip": ["sysctl", "-n", "machdep.cpu.brand_string"],
            "memory_bytes": ["sysctl", "-n", "hw.memsize"],
            "host_page_bytes": ["sysctl", "-n", "hw.pagesize"],
        }
        for key, cmd in commands.items():
            r = self.run(cmd, key, check=False)
            data[key] = {"stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}
        for key, cmd in {
            "guest": "getprop ro.build.version.sdk; getprop ro.product.cpu.abi; getconf PAGESIZE; uname -a",
            "disk": "df -k /data", "memory": "cat /proc/meminfo",
            "graphics": "dumpsys SurfaceFlinger | grep -E 'GLES:|Vulkan|RenderEngine'",
            "mounts": "cat /proc/mounts",
        }.items():
            r = self.shell(cmd, label="environment-" + key, check=False)
            data[key] = {"stdout": r.stdout, "stderr": r.stderr, "returncode": r.returncode}
        for p in self.sdk.glob("**/source.properties"):
            data[str(p.relative_to(self.root))] = p.read_text()
        save_json(self.results / "environment.json", data)
        return data


def main():
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["create", "start", "stop", "info", "environment"])
    p.add_argument("--avd", default="official_clean")
    p.add_argument("--window", action="store_true")
    p.add_argument("--size-gib", type=int, default=8)
    p.add_argument("--no-sync", action="store_true")
    args = p.parse_args()
    rt = Runtime(args.avd)
    if args.command == "create": print(rt.create_avd(size_gib=args.size_gib))
    elif args.command == "start":
        print(json.dumps({"pid": rt.start(window=args.window), "boot_s": rt.wait_boot()}))
    elif args.command == "stop": print(json.dumps(rt.stop(sync=not args.no_sync)))
    elif args.command == "environment": print(json.dumps(rt.collect_environment(), ensure_ascii=False))
    else: print(json.dumps(rt.state(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
