#!/usr/bin/env python3
"""Capture one already-running, package-owned Manager instance; development QA."""
from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys

SPEC = importlib.util.spec_from_file_location("modavd_manager_capture_frames", Path(__file__).with_name("capture_frame.py"))
frames = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(frames)
SERVER_PORT = 20050
RUNTIME_PROPERTIES = frozenset(("avd.dir", "avd.id", "avd.name", "port.serial", "port.adb",
                                "emulator.version", "emulator.build"))


class BsdInfo(ctypes.Structure):
    # Darwin sys/proc_info.h, PROC_PIDTBSDINFO. Native alignment is required.
    _fields_ = [(name, ctypes.c_uint32) for name in (
        "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved")]
    _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
    _fields_ += [(name, ctypes.c_uint32) for name in ("nfiles", "pgid", "jobc", "tdev", "tpgid")]
    _fields_ += [("nice", ctypes.c_int32), ("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64)]


def process_snapshot(pid: int) -> dict:
    """Read executable, argv and precise creation time without ps text parsing."""
    if sys.platform != "darwin" or type(pid) is not int or pid <= 1:
        raise RuntimeError("a live Darwin process PID is required")
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    library.proc_pidinfo.restype = ctypes.c_int
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_pidpath.restype = ctypes.c_int
    info = BsdInfo()
    if library.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info) or info.status == 5:
        raise RuntimeError("process is absent, unreadable or a zombie")
    executable = ctypes.create_string_buffer(4096)
    if library.proc_pidpath(pid, executable, len(executable)) <= 0:
        raise RuntimeError("cannot read actual process executable")
    libc = ctypes.CDLL(None, use_errno=True)
    libc.sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
    libc.sysctl.restype = ctypes.c_int
    limit = ctypes.c_int()
    limit_size = ctypes.c_size_t(ctypes.sizeof(limit))
    limit_mib = (ctypes.c_int * 2)(1, 8)  # CTL_KERN, KERN_ARGMAX.
    if libc.sysctl(limit_mib, 2, ctypes.byref(limit), ctypes.byref(limit_size), None, 0) or not 4096 <= limit.value <= 2 * 1024**2:
        raise RuntimeError("cannot read a valid bounded process argument limit")
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2, PID.
    arguments = ctypes.create_string_buffer(limit.value)
    count = ctypes.c_size_t(len(arguments))
    if libc.sysctl(mib, 3, arguments, ctypes.byref(count), None, 0):
        raise RuntimeError("cannot read bounded process arguments")
    data = arguments.raw[:count.value]
    argc = struct.unpack_from("=i", data)[0] if len(data) >= 4 else 0
    if not 1 <= argc <= 4096:
        raise RuntimeError("invalid process argument count")
    position = data.find(b"\0", 4)
    if position < 0:
        raise RuntimeError("invalid executable in process arguments")
    while position < len(data) and data[position] == 0:
        position += 1
    argv = []
    for _ in range(argc):
        end = data.find(b"\0", position)
        if end < 0:
            raise RuntimeError("truncated process arguments")
        argv.append(os.fsdecode(data[position:end]))
        position = end + 1
    # Keep only discovery-directory inputs, never other process environment data.
    wanted = {b"ANDROID_EMULATOR_HOME", b"ANDROID_PREFS_ROOT", b"ANDROID_SDK_HOME", b"HOME"}
    environment = {}
    for item in data[position:].split(b"\0"):
        key, separator, value = item.partition(b"=")
        if separator and key in wanted:
            environment[key.decode("ascii")] = os.fsdecode(value)
    after = BsdInfo()
    if library.proc_pidinfo(pid, 3, 0, ctypes.byref(after), ctypes.sizeof(after)) != ctypes.sizeof(after) or (
            info.start_seconds, info.start_microseconds) != (after.start_seconds, after.start_microseconds):
        raise RuntimeError("process identity changed while reading it")
    return {"pid": pid, "startedAtUs": info.start_seconds * 1000000 + info.start_microseconds,
            "executable": str(Path(os.fsdecode(executable.value)).resolve(strict=True)),
            "argv": argv, "discoveryEnvironment": environment}


def private_server() -> dict:
    completed = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", f"-iTCP:{SERVER_PORT}",
                                "-sTCP:LISTEN", "-Fp"], capture_output=True, text=True, timeout=10, check=True)
    pids = {int(line[1:]) for line in completed.stdout.splitlines() if line.startswith("p") and line[1:].isdigit()}
    if len(pids) != 1:
        raise RuntimeError("private ADB port must have exactly one identifiable listening process")
    return process_snapshot(pids.pop())


def discovery_directory(environment: dict) -> Path:
    # Pinned ConfigDirs::getDiscoveryDirectory on Darwin does not use TMPDIR.
    # A nonempty process HOME selects this registration root even when absent;
    # only missing HOME falls back to ConfigDirs::getUserDirectory.
    if environment.get("HOME"):
        return Path(environment["HOME"]) / "Library/Caches/TemporaryItems/avd/running"
    home = environment.get("ANDROID_EMULATOR_HOME")
    if not home:
        for key in ("ANDROID_PREFS_ROOT", "ANDROID_SDK_HOME"):
            if environment.get(key):
                path = Path(environment[key])
                home = str(path / ".android" if (path / ".android").is_dir() else path)
                break
    if not home:
        home = str(Path(environment.get("HOME") or Path.home()) / ".android")
    return Path(home) / "avd/running"


def runtime_record(process: dict, expected_avd: Path, name: str, console: int) -> dict:
    path = discovery_directory(process["discoveryEnvironment"]) / f"pid_{process['pid']}.ini"
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 65536 or info.st_mtime_ns // 1000 < process["startedAtUs"]:
        raise RuntimeError("missing, stale or invalid emulator runtime identity file")
    raw = path.read_bytes()
    properties = {}
    for line in raw.decode("utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in RUNTIME_PROPERTIES:
            properties[key.strip()] = value.strip()
    if Path(properties.get("avd.dir", "")).resolve(strict=True) != expected_avd or properties.get("avd.id") != name or properties.get("port.serial") != str(console):
        raise RuntimeError("actual emulator runtime AVD/id/port differs from the requested package instance")
    return {"path": str(path.resolve()), "sha256": frames.sha256(raw), "properties": properties}


class ManagerCapture:
    def __init__(self, root: Path, index: int = 0):
        if type(index) is not int or index not in (0, 1, 2, 3, 4, 6, 7, 8, 9):
            raise RuntimeError("invalid Manager instance index")
        self.root = root.expanduser().resolve(strict=True)
        self.sdk = self.root / "sdk"
        self.index, self.console = index, 20000 + 10 * index
        self.name, self.serial = f"mas_{index}", f"emulator-{20000 + 10 * index}"
        self.avd = (self.root / "avd" / (self.name + ".avd")).resolve(strict=True)
        if not self.avd.is_relative_to(self.root / "avd"):
            raise RuntimeError("AVD path escapes the requested package")
        self.cli, self.adb = self.root / "tools/modavd", self.sdk / "platform-tools/adb"
        self.identity = None
        self.observations = []
        self.package = self.check_package()

    def check_package(self) -> dict:
        marker = frames.read_record(self.root / ".modavd-macos-package.json")
        # The full inventory is larger than a small process receipt.
        manifest_path = self.root / "PACKAGE-MANIFEST.json"
        info = manifest_path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024**2:
            raise RuntimeError("invalid package manifest")
        raw = manifest_path.read_bytes()
        manifest = json.loads(raw)
        if marker.get("schema") != 1 or marker.get("tool") != "ModAVD macOS local assembler" or manifest.get("owner") != marker.get("owner") or manifest.get("platform") != "mac-arm64":
            raise RuntimeError("package ownership marker and manifest differ")
        metadata = frames.read_record(self.root / "mas-avd.json")
        if metadata.get("adbServerPort", SERVER_PORT) != SERVER_PORT or metadata.get("sdkRoot"):
            raise RuntimeError("this capture mode requires the package SDK and private ADB 20050")
        for file in (self.cli, self.adb):
            if file.is_symlink() or not file.is_file() or not os.access(file, os.X_OK):
                raise RuntimeError("package CLI/ADB executable is missing or a symlink")
            expected = manifest["files"][file.relative_to(self.root).as_posix()]["sha256"]
            if frames.runtime.digest(file) != expected:
                raise RuntimeError("package CLI/ADB SHA-256 differs from its inventory")
        return {"owner": marker["owner"], "manifestSha256": frames.sha256(raw)}

    def env(self):
        environment = os.environ.copy()
        environment.update(ANDROID_ADB_SERVER_PORT=str(SERVER_PORT), ADB_SERVER_SOCKET=f"tcp:127.0.0.1:{SERVER_PORT}")
        return environment

    def cli_json(self, command: str) -> dict:
        completed = subprocess.run([str(self.cli), "--root", str(self.root), command, str(self.index), "--json"],
                                   env=self.env(), capture_output=True, text=True, check=True, timeout=30)
        envelope = json.loads(completed.stdout)
        if envelope.get("ok") is not True or not isinstance(envelope.get("data"), dict):
            raise RuntimeError(f"Manager {command} did not return a successful JSON envelope")
        return envelope["data"]

    def adb_command(self, *args):
        return subprocess.run([str(self.adb), "-H", "127.0.0.1", "-P", str(SERVER_PORT), "-s", self.serial, *args],
                              env=self.env(), capture_output=True, text=True, check=True, timeout=20)

    def owned(self) -> dict:
        if self.check_package() != self.package:
            raise RuntimeError("package identity changed during capture")
        server = private_server()
        if Path(server["executable"]) != self.adb.resolve(strict=True) or not frames.runtime.adb_server_ready():
            raise RuntimeError("private ADB 20050 is absent, foreign or not protocol-ready")
        info, status = self.cli_json("info"), self.cli_json("status")
        for observation in (info, status):
            if observation.get("index") != self.index or observation.get("name") != self.name or observation.get("status") != "online" or observation.get("running") is not True or observation.get("booted") is not True or observation.get("busy") or observation.get("creation"):
                raise RuntimeError("Manager instance is not the requested idle, booted instance")
        if type(info.get("pid")) is not int or info["pid"] <= 1 or status.get("pid") != info["pid"]:
            raise RuntimeError("Manager info/status process identity differs")
        if info.get("serial") != self.serial or info.get("adb_address") != f"127.0.0.1:{self.console + 1}" or info.get("ports") != {"console": self.console, "adb": self.console + 1, "grpc": self.console + 2} or info.get("screencap", {}).get("shm") != f"videmulator{self.console}" or Path(info.get("path", "")).resolve(strict=True) != self.avd:
            raise RuntimeError("Manager instance paths, serial, ports or SHM name differ")
        process = process_snapshot(info["pid"])
        executable = Path(process["executable"])
        if not executable.is_relative_to(self.sdk / "emulator/qemu/darwin-aarch64") or executable.name not in ("qemu-system-aarch64", "qemu-system-aarch64-headless"):
            raise RuntimeError("actual emulator executable is outside this package")
        argv = process["argv"]
        def value(flag):
            return argv[argv.index(flag) + 1] if argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv) else None
        if value("-avd") != self.name or value("-ports") != f"{self.console},{self.console + 1}":
            raise RuntimeError("actual emulator arguments differ from Manager identity")
        if info.get("started_at_ms") != process["startedAtUs"] // 1000 or int(dt.datetime.fromisoformat(info["started_at"]).timestamp()) != process["startedAtUs"] // 1000000:
            raise RuntimeError("actual process start time differs from Manager identity")
        discovery = runtime_record(process, self.avd, self.name, self.console)
        again = private_server()
        identity = {"package": self.package, "index": self.index, "avdPath": str(self.avd),
                    "qemu": {key: process[key] for key in ("pid", "startedAtUs", "executable", "argv")},
                    "server": {key: server[key] for key in ("pid", "startedAtUs", "executable")}}
        if any(again[key] != server[key] for key in ("pid", "startedAtUs", "executable")) or (self.identity is not None and identity != self.identity):
            raise RuntimeError("package/server/emulator identity changed; no further guest command sent")
        # avd.name and ro.boot.qemu.avd_name are display titles; avd id is stable.
        if self.adb_command("emu", "avd", "id").stdout.split() != [self.name, "OK"]:
            raise RuntimeError("guest console AVD id differs from the verified Manager process")
        self.identity = identity
        self.observations.append({"info": info, "status": status, "runtimeRecord": discovery})
        return info

    def capture(self, *, reuse_stream: bool = False):
        info = self.owned()
        if reuse_stream:
            if info["screencap"].get("shm_ready") is not True:
                raise RuntimeError("--reuse-stream requires an already ready, verified Manager SHM stream")
            self.stream_ownership = "borrowed_existing"
            try:
                return frames.read_shm(f"videmulator{self.console}", expected_console_port=self.console)
            finally:
                # This stream belongs to Manager. Recheck it, but never start or stop it.
                self.owned()
        if info["screencap"].get("shm_ready") is not False:
            raise RuntimeError("SHM stream already exists or readiness is unknown; refusing to take it over")
        self.stream_ownership = "started_for_capture"
        try:
            response = self.adb_command("emu", "screenrecord", "webrtc", "start").stdout
            if response.split() != [f"videmulator{self.console}", "OK"]:
                raise RuntimeError(f"unexpected Manager SHM start response: {response!r}")
            frame, pixels = frames.read_shm(f"videmulator{self.console}", expected_console_port=self.console)
        finally:
            self.owned()  # A replacement root/process/serial must never receive our stop.
            self.adb_command("emu", "screenrecord", "webrtc", "stop")
        return frame, pixels


def save_capture(root: Path, index: int, output: Path, *, reuse_stream: bool = False) -> dict:
    collector = ManagerCapture(root, index)
    output = output.expanduser()
    output = output.parent.resolve(strict=True) / output.name
    if output.is_relative_to(collector.sdk):
        raise RuntimeError("capture output must not be inside the immutable SDK")
    frames.runtime.space(output.parent, frames.MAX_FRAME_BYTES + 1024**2)
    with frames.new_output_directory(output) as (directory, fd):
        frame, pixels = collector.capture(reuse_stream=reuse_stream)
        png = frames.png_bgra(frame["width"], frame["height"], pixels)
        report = {"schema": 1, "capturedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "tool": "ModAVD Manager package SHM capture", "root": str(collector.root),
                  "index": index, "serial": collector.serial, "adbServerPort": SERVER_PORT,
                  "processIdentity": collector.identity, "managerObservations": collector.observations,
                  "shmName": f"videmulator{collector.console}", "frame": frame,
                  "image": {"file": "frame.png", "format": "PNG RGBA8", "bytes": len(png), "sha256": frames.sha256(png)},
                  "streamOwnership": collector.stream_ownership,
                  "cleanup": ("borrowed Manager stream left running after package/server/process/guest identity recheck"
                              if reuse_stream else "SHM stop completed after package/server/process/guest identity recheck"),
                  "noSeqlock": True, "consistencyLimitation": frames.LIMITATION}
        evidence = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode()
        frames.runtime.space(directory, len(png) + len(evidence))
        frames.write_exclusive(fd, "frame.png", png)
        frames.write_exclusive(fd, "evidence.json", evidence)
    return {"png": str(directory / "frame.png"), "evidence": str(directory / "evidence.json"), "frame": frame}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=frames.runtime.REPO / ".macos-work/package/ModAVD-validated")
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--reuse-stream", action="store_true",
                        help="read an already ready Manager stream without starting or stopping it")
    parser.add_argument("--output", type=Path, required=True, help="new evidence directory; parent must exist")
    args = parser.parse_args()
    print(json.dumps(save_capture(args.root, args.index, args.output, reuse_stream=args.reuse_stream), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        raise SystemExit(f"Manager capture failed: {error}")
