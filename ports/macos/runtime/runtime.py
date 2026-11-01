#!/usr/bin/env python3
"""Isolated development runtime for the macOS port; never imported by Maa Verge."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LOCK = json.loads((HERE / "components.lock.json").read_text())
RESERVE = 20 * 1024**3
ADB_PORT = 20050
CONSOLE_PORT = 20060


def fail(message: str) -> None:
    raise RuntimeError(message)


def digest(path: Path, algorithm: str = "sha256") -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, algorithm).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def space(root: Path, additional: int = 0) -> int:
    probe = root
    while not probe.exists():
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free - additional < RESERVE:
        fail(f"空间停线：剩余 {free / 1024**3:.1f} GiB，下一步约需 "
             f"{additional / 1024**3:.1f} GiB，须保留 20 GiB；保留现有断点。")
    return free


class Runtime:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.sdk = self.root / "sdk"

    def initialize(self) -> dict:
        marker = self.root / "runtime.json"
        if not marker.exists():
            space(self.root)
            if self.root.exists() and any(self.root.iterdir()):
                fail("运行目录非空且没有 runtime.json；请选择本任务的独立目录。")
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_json(marker, {"schemaVersion": 1, "id": uuid.uuid4().hex})
        data = json.loads(marker.read_text())
        if data.get("schemaVersion") != 1 or not re.fullmatch(r"[a-f0-9]{32}", data.get("id", "")):
            fail("运行目录标记无效。")
        for name in ["sdk", "avd", "user", "keys", "logs", "receipts", "downloads", "tools"]:
            (self.root / name).mkdir(exist_ok=True, mode=0o700)
        return data

    def name(self) -> str:
        return "modavd_macos_qa_" + self.initialize()["id"][:8]

    def env(self) -> dict:
        self.initialize()
        env = os.environ.copy()
        env.update({
            "ANDROID_HOME": str(self.sdk), "ANDROID_SDK_ROOT": str(self.sdk),
            "ANDROID_USER_HOME": str(self.root / "user"),
            "ANDROID_EMULATOR_HOME": str(self.root / "user"),
            "ANDROID_AVD_HOME": str(self.root / "avd"),
            "ANDROID_ADB_SERVER_PORT": str(ADB_PORT),
            "ADB_SERVER_SOCKET": f"tcp:127.0.0.1:{ADB_PORT}",
            "ADB_VENDOR_KEYS": str(self.root / "keys"),
            # The emulator registers itself with our server. Do not scan other emulators.
            "ADB_LOCAL_TRANSPORT_MAX_PORT": "5553",
            "ADB_MDNS_AUTO_CONNECT": "0",
            # Apple unified memory also exposes DEVICE_LOCAL|HOST_VISIBLE.
            # Hiding it as NVIDIA ReBAR would remove required guest mappings.
            "ANDROID_EMU_VK_HIDE_REBAR": "0",
        })
        return env

    def component(self, name: str) -> tuple[dict, Path]:
        config = LOCK["components"][name]
        return config, self.sdk / config["destination"]

    def license(self, component: str) -> tuple[dict, str]:
        config, _ = self.component(component)
        text = (HERE / "licenses" / (config["license"] + ".txt")).read_text()
        if hashlib.sha256(text.encode()).hexdigest() != config["licenseTextSha256"]:
            fail("本地许可证文本与锁文件不符。")
        return config, text

    def require_license(self, component: str) -> None:
        config, _ = self.license(component)
        receipt = self.root / "receipts" / (config["license"] + ".json")
        if not receipt.exists() or json.loads(receipt.read_text()).get("sha256") != config["licenseTextSha256"]:
            fail(f"尚未明确接受 {config['license']}。先运行 licenses --id {config['license']} 阅读；"
                 "本工具不自动接受许可证。")

    def installed(self, name: str) -> bool:
        config, dest = self.component(name)
        receipt = self.root / "receipts" / (name + ".json")
        if not dest.exists() or not receipt.exists():
            return False
        record = json.loads(receipt.read_text())
        return record.get("archiveSha1") == config["sha1"] and bool(record.get("files"))

    def install(self, name: str, core_ready: Path | None = None) -> None:
        self.initialize()
        if self.installed(name):
            print(f"已安装锁定版本：{name}")
            return
        config, destination = self.component(name)
        self.require_license(name)
        if name == "system-image":
            if core_ready is None or not core_ready.is_file() or not os.access(core_ready, os.X_OK):
                fail("镜像下载尚未解锁：须提供已构建的 macOS emulator 路径 --core-ready。")
            version = subprocess.run([str(core_ready.resolve()), "-version"], env=self.env(), check=True,
                                     capture_output=True, text=True, timeout=30)
            if "Android emulator version" not in version.stdout + version.stderr:
                fail("--core-ready 未返回 Android Emulator 版本信息，拒绝开始镜像下载。")
        if destination.exists():
            fail(f"存在未经本工具验证的组件目录，拒绝覆盖：{destination}")
        archive = self.root / "downloads" / Path(config["url"]).name
        partial = archive.with_suffix(archive.suffix + ".part")
        if not archive.exists():
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > config["size"]:
                fail(f"下载断点大于锁定归档，请单独检查：{partial}")
            space(self.root, config["size"] - offset + config["expandedBudgetBytes"])
            if offset < config["size"]:
                request = urllib.request.Request(config["url"], headers={"Range": f"bytes={offset}-"} if offset else {})
                with urllib.request.urlopen(request, timeout=30) as response:
                    if urllib.parse.urlparse(response.url).hostname != "dl.google.com":
                        fail("下载被重定向到锁定官方域名以外。")
                    if offset and response.status == 206:
                        if not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                            fail("下载断点响应范围不符。")
                        mode = "ab"
                    else:
                        mode = "wb"
                    with partial.open(mode) as output:
                        while chunk := response.read(1024 * 1024):
                            if output.tell() + len(chunk) > config["size"]:
                                fail("下载响应超过锁定归档大小，已停止。")
                            space(self.root, len(chunk))
                            output.write(chunk)
            partial.replace(archive)
        if archive.stat().st_size != config["size"] or digest(archive, "sha1") != config["sha1"]:
            fail(f"归档大小或官方 SHA-1 不符，拒绝解压：{archive}")
        archive_sha256 = digest(archive)
        stage = self.sdk / (".install-" + name + "-" + uuid.uuid4().hex)
        stage.mkdir()
        try:
            with zipfile.ZipFile(archive) as bundle:
                members = bundle.infolist()
                space(self.root, sum(member.file_size for member in members))
                for member in members:
                    relative = PurePosixPath(member.filename)
                    if relative.is_absolute() or ".." in relative.parts or "\\" in member.filename:
                        fail("归档包含不安全路径。")
                    if not relative.parts or relative.parts[0] != config["archiveRoot"]:
                        fail("归档根目录与锁文件不符。")
                    if stat.S_ISLNK(member.external_attr >> 16):
                        fail("归档包含符号链接；须先审查后再支持。")
                    target = stage.joinpath(*relative.parts[1:])
                    if member.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    space(self.root, member.file_size)
                    with bundle.open(member) as source, target.open("wb") as output:
                        while chunk := source.read(1024 * 1024):
                            space(self.root, len(chunk))
                            output.write(chunk)
                    target.chmod((member.external_attr >> 16) & 0o777 or 0o644)
            properties = dict(line.split("=", 1) for line in (stage / "source.properties").read_text().splitlines()
                              if "=" in line and not line.startswith("#"))
            normalize = lambda value: tuple(int(p) for p in value.strip().split(".")) + (0,) * (3 - len(value.strip().split(".")))
            if normalize(properties.get("Pkg.Revision", "0")) != normalize(config["revision"]):
                fail("解压后的 Pkg.Revision 与锁文件不符。")
            files = {str(p.relative_to(stage)): digest(p) for p in stage.rglob("*") if p.is_file()}
            destination.parent.mkdir(parents=True, exist_ok=True)
            stage.replace(destination)
            write_json(self.root / "receipts" / (name + ".json"), {
                "package": config["package"], "revision": config["revision"],
                "archiveSha1": config["sha1"], "archiveSha256": archive_sha256, "files": files,
            })
            archive.unlink()
            print(f"已安装并校验：{name} {config['revision']}")
        finally:
            if stage.exists():
                shutil.rmtree(stage)

    def adb(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        config, directory = self.component("platform-tools")
        if not self.installed("platform-tools"):
            fail("先安装锁定的 platform-tools。")
        receipt = json.loads((self.root / "receipts/platform-tools.json").read_text())
        executable = directory / "adb"
        if digest(executable) != receipt["files"].get("adb"):
            fail("adb 文件校验失败。")
        return subprocess.run([str(executable), "-H", "127.0.0.1", "-P", str(ADB_PORT), *args],
                              env=self.env(), check=True, capture_output=True, text=True, timeout=timeout)

    def create_avd(self) -> Path:
        if not self.installed("system-image"):
            fail("先准备锁定的 API 34 Google APIs arm64 镜像。")
        name = self.name()
        directory = self.root / "avd" / (name + ".avd")
        if directory.exists():
            if not (directory / ".modavd-qa-owner").is_file():
                fail("实例目录没有本任务标记，拒绝修改。")
            return directory
        space(self.root, 6 * 1024**3)
        directory.mkdir()
        (directory / ".modavd-qa-owner").write_text(self.initialize()["id"] + "\n")
        settings = {
            "AvdId": name, "avd.ini.displayname": "ModAVD macOS QA", "abi.type": "arm64-v8a",
            "hw.cpu.arch": "arm64", "image.sysdir.1": "system-images/android-34/google_apis/arm64-v8a/",
            "tag.id": "google_apis", "tag.display": "Google APIs", "PlayStore.enabled": "false",
            "hw.lcd.width": "1280", "hw.lcd.height": "720", "hw.lcd.density": "240",
            "hw.ramSize": "4096", "hw.cpu.ncore": "4", "hw.gpu.enabled": "yes", "hw.gpu.mode": "host",
            "hw.keyboard": "yes", "hw.mainKeys": "yes", "hw.initialOrientation": "landscape",
            "disk.dataPartition.size": "6G", "showDeviceFrame": "no", "skin.dynamic": "yes",
            "fastboot.forceColdBoot": "yes", "fastboot.forceFastBoot": "no",
        }
        (directory / "config.ini").write_text("".join(f"{key}={value}\n" for key, value in settings.items()))
        (self.root / "avd" / (name + ".ini")).write_text(f"avd.ini.encoding=UTF-8\npath={directory}\ntarget=android-34\n")
        return directory

    def ensure_server(self) -> None:
        record = self.root / "adb-server.json"
        if record.exists() and same_process(json.loads(record.read_text()), "server nodaemon"):
            if not adb_server_ready():
                fail("本任务 ADB server 仍存在但协议未就绪；请检查 logs/adb-server.log。")
            return
        require_free_ports([ADB_PORT])
        _, directory = self.component("platform-tools")
        self.adb("version")
        key = self.root / "keys/adbkey"
        if not key.exists():
            subprocess.run([str(directory / "adb"), "keygen", str(key)], env=self.env(), check=True)
        with (self.root / "logs/adb-server.log").open("ab") as log:
            # No USB device has this task-owned emulator serial; leave USB devices alone.
            # ADB 37 rejects an explicit listen hostname. tcp:<port> binds
            # loopback unless -a is supplied; clients still use 127.0.0.1.
            process = subprocess.Popen([str(directory / "adb"), "--one-device", f"emulator-{CONSOLE_PORT}",
                                        "-L", f"tcp:{ADB_PORT}", "server", "nodaemon"],
                                       env=self.env(), stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + 10
        while process.poll() is None and time.monotonic() < deadline:
            if adb_server_ready() and process.poll() is None:
                write_json(record, process_record(process.pid))
                return
            time.sleep(0.1)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        fail("隔离 ADB server 协议未就绪，请检查 logs/adb-server.log。")

    def start(self, emulator: Path, headless: bool, memory: int) -> None:
        self.initialize()
        state_path = self.root / "emulator.json"
        if state_path.exists() and same_process(json.loads(state_path.read_text()), self.name()):
            fail("本任务实例已在运行。")
        space(self.root, 6 * 1024**3)
        emulator = emulator.expanduser().resolve(strict=True)
        if not emulator.is_file() or not os.access(emulator, os.X_OK):
            fail("emulator 必须是可执行文件。")
        self.create_avd()
        require_free_ports([CONSOLE_PORT, CONSOLE_PORT + 1, CONSOLE_PORT + 2])
        subprocess.run([str(emulator), "-accel-check"], env=self.env(), check=True, timeout=30)
        self.ensure_server()
        args = [str(emulator), "-avd", self.name(), "-no-snapshot", "-gpu", "host", "-ports",
                f"{CONSOLE_PORT},{CONSOLE_PORT + 1}", "-grpc", str(CONSOLE_PORT + 2), "-grpc-use-token",
                "-audio", "none", "-no-metrics", "-memory", str(memory), "-feature", "-QuickbootFileBacked",
                "-feature", "-VirtioSndCard", "-feature", "VulkanBatchedDescriptorSetUpdate"]
        if headless:
            args += ["-no-window", "-no-boot-anim"]
        args += ["-qemu", "-global", "virtio-blk-device.write-cache=off", "-global",
                 "virtio-blk-device.config-wce=off", "-device", "virtio-balloon-pci,free-page-reporting=on"]
        with (self.root / "logs/emulator.log").open("ab") as log:
            process = subprocess.Popen(args, env=self.env(), stdout=log, stderr=log, start_new_session=True)
        time.sleep(0.3)
        if process.poll() is not None:
            fail("模拟器进程提前退出，请检查 logs/emulator.log。")
        write_json(state_path, {**process_record(process.pid), "name": self.name(), "args": args,
                               "serial": f"emulator-{CONSOLE_PORT}", "headless": headless})
        with (self.root / "logs/space-watch.log").open("ab") as log:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--root", str(self.root), "watch"],
                             stdout=log, stderr=log, start_new_session=True)
        print(json.dumps({"pid": process.pid, "serial": f"emulator-{CONSOLE_PORT}",
                          "adbServerPort": ADB_PORT, "log": str(self.root / "logs/emulator.log")}, indent=2))

    def owned_device(self) -> str:
        record = self.root / "emulator.json"
        if not record.is_file() or not same_process(json.loads(record.read_text()), self.name()):
            fail("没有仍属于本任务的模拟器进程；拒绝访问设备。")
        serial = f"emulator-{CONSOLE_PORT}"
        observed = self.adb("-s", serial, "shell", "getprop", "ro.boot.qemu.avd_name").stdout.strip()
        if observed != self.name():
            fail(f"设备身份不符，拒绝操作：{observed!r}")
        return serial

    def stop(self) -> None:
        serial = self.owned_device()
        state = json.loads((self.root / "emulator.json").read_text())
        self.adb("-s", serial, "shell", "sync")
        print(self.adb("-s", serial, "emu", "kill").stdout.strip())
        deadline = time.monotonic() + 30
        while same_process(state, self.name()):
            if time.monotonic() >= deadline:
                fail("模拟器已接受正常关机，但进程尚未退出；保留记录以便检查。")
            time.sleep(0.1)

    def stop_server(self) -> None:
        state = self.root / "emulator.json"
        if state.exists() and same_process(json.loads(state.read_text()), self.name()):
            fail("先正常停止本任务的模拟器，再关闭其私有 ADB server。")
        record = self.root / "adb-server.json"
        if not record.exists() or not same_process(json.loads(record.read_text()), "server nodaemon"):
            fail("没有仍属于本任务的 ADB server；拒绝接管其他服务。")
        self.adb("kill-server")

    def watch(self) -> None:
        """Stop only this recorded QA instance if storage crosses the reserve."""
        state = json.loads((self.root / "emulator.json").read_text())
        while same_process(state, self.name()):
            try:
                space(self.root)
            except RuntimeError as error:
                print(error, file=sys.stderr, flush=True)
                try:
                    self.stop()
                except (RuntimeError, OSError, subprocess.SubprocessError) as stop_error:
                    print(f"空间紧急停机；guest sync 不可用：{stop_error}", file=sys.stderr, flush=True)
                    if same_process(state, self.name()):
                        os.kill(state["pid"], signal.SIGTERM)
                return
            time.sleep(2)

    def wait_boot(self, seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            space(self.root)
            try:
                serial = self.owned_device()
                if self.adb("-s", serial, "shell", "getprop", "sys.boot_completed").stdout.strip() == "1":
                    print(json.dumps({"serial": serial, "bootCompleted": True, "avd": self.name()}))
                    return
            except (RuntimeError, subprocess.SubprocessError):
                pass
            time.sleep(2)
        fail("客体未在限定时间内完成开机。")

    def screenshot(self, samples: int) -> None:
        serial = self.owned_device()
        output = self.adb("-s", serial, "emu", "screenrecord", "webrtc", "start").stdout
        fields = output.split()
        if len(fields) != 2 or fields[1] != "OK" or fields[0] != f"videmulator{CONSOLE_PORT}":
            fail(f"共享内存启动响应不符：{output!r}")
        source = HERE.parent / "qa/shm-probe.cpp"
        probe = self.root / "tools/shm-probe"
        try:
            if not probe.exists() or source.stat().st_mtime > probe.stat().st_mtime:
                subprocess.run(["clang++", "-std=c++20", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(probe)],
                               check=True, timeout=60)
            subprocess.run([str(probe), "--name", fields[0], "--samples", str(samples)], check=True, timeout=30)
        finally:
            self.adb("-s", serial, "emu", "screenrecord", "webrtc", "stop")


def adb_server_ready() -> bool:
    """Probe our loopback endpoint without an adb client auto-starting a daemon."""
    try:
        with socket.create_connection(("127.0.0.1", ADB_PORT), timeout=0.5) as connection:
            connection.sendall(b"000chost:version")
            response = b""
            while len(response) < 4:
                chunk = connection.recv(4 - len(response))
                if not chunk:
                    return False
                response += chunk
            return response == b"OKAY"
    except OSError:
        return False


def process_record(pid: int) -> dict:
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="], capture_output=True, text=True, check=True)
    if not result.stdout.strip():
        fail("进程未就绪。")
    return {"pid": pid, "started": result.stdout.strip()}


def same_process(record: dict, marker: str) -> bool:
    try:
        pid = record["pid"]
        if not isinstance(pid, int) or pid <= 1 or process_record(pid)["started"] != record["started"]:
            return False
        result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=True)
        return marker in result.stdout
    except (KeyError, RuntimeError, subprocess.CalledProcessError):
        return False


def require_free_ports(ports: list[int]) -> None:
    held = []
    try:
        for port in ports:
            listener = socket.socket()
            held.append(listener)
            try:
                # A stopped emulator may leave TIME_WAIT sockets. This does
                # not permit binding over an existing listener (no REUSEPORT).
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind(("127.0.0.1", port))
            except OSError:
                fail(f"端口 {port} 已占用，拒绝接管。")
    finally:
        for listener in held:
            listener.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO / ".macos-work/runtime")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("info")
    licenses = commands.add_parser("licenses")
    licenses.add_argument("--id", choices=sorted({c["license"] for c in LOCK["components"].values()}), required=True)
    accept = commands.add_parser("accept-license", help="Only run after the human has read and accepted these exact terms")
    accept.add_argument("--id", required=True)
    accept.add_argument("--sha256", required=True)
    install = commands.add_parser("install")
    install.add_argument("component", choices=LOCK["components"])
    install.add_argument("--core-ready", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("component", choices=LOCK["components"])
    commands.add_parser("create-avd")
    start = commands.add_parser("start")
    start.add_argument("--emulator", type=Path, required=True)
    start.add_argument("--headless", action="store_true")
    start.add_argument("--memory", type=int, choices=[2048, 4096, 6144, 8192], default=4096)
    commands.add_parser("stop")
    commands.add_parser("stop-server", help="Stop only the private ADB server created by this runtime")
    commands.add_parser("watch", help="Internal 20 GiB storage watchdog; started automatically")
    boot = commands.add_parser("wait-boot")
    boot.add_argument("--seconds", type=int, choices=range(1, 601), default=180)
    shot = commands.add_parser("shm-check")
    shot.add_argument("--samples", type=int, choices=range(1, 101), default=5)
    for name in ["sdkmanager-list", "sdkmanager-licenses"]:
        command = commands.add_parser(name)
        command.add_argument("--java-home", type=Path)
    args = parser.parse_args()
    runtime = Runtime(args.root)
    if args.command == "licenses":
        component = next(n for n, c in LOCK["components"].items() if c["license"] == args.id)
        config, text = runtime.license(component)
        print(text)
        print(f"\n许可证：{args.id}\n文本 SHA-256：{config['licenseTextSha256']}")
    elif args.command == "accept-license":
        matches = [c for c in LOCK["components"].values() if c["license"] == args.id and c["licenseTextSha256"] == args.sha256]
        if not matches:
            fail("许可证 ID 或文本指纹与锁文件不符。")
        runtime.initialize()
        write_json(runtime.root / "receipts" / (args.id + ".json"), {
            "id": args.id, "sha256": args.sha256, "acceptedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
    elif args.command == "install":
        runtime.install(args.component, args.core_ready)
    elif args.command == "verify":
        if not runtime.installed(args.component):
            fail("组件尚未安装或不匹配当前锁文件。")
        _, directory = runtime.component(args.component)
        receipt = json.loads((runtime.root / "receipts" / (args.component + ".json")).read_text())
        for name, expected in receipt["files"].items():
            if digest(directory / name) != expected:
                fail(f"组件文件校验失败：{name}")
        print("全部已安装文件 SHA-256 校验通过。")
    elif args.command == "info":
        print(json.dumps({"root": str(runtime.root), "freeGiB": round(space(runtime.root) / 1024**3, 1),
                          "reserveGiB": 20, "sdk": str(runtime.sdk), "avdHome": str(runtime.root / "avd"),
                          "adbServerPort": ADB_PORT, "consolePort": CONSOLE_PORT,
                          "installed": {name: runtime.installed(name) for name in LOCK["components"]}}, indent=2))
    elif args.command == "create-avd":
        print(runtime.create_avd())
    elif args.command == "start":
        runtime.start(args.emulator, args.headless, args.memory)
    elif args.command == "stop":
        runtime.stop()
    elif args.command == "stop-server":
        runtime.stop_server()
    elif args.command == "watch":
        runtime.watch()
    elif args.command == "wait-boot":
        runtime.wait_boot(args.seconds)
    elif args.command == "shm-check":
        runtime.screenshot(args.samples)
    elif args.command.startswith("sdkmanager-"):
        if not runtime.installed("commandline-tools"):
            fail("先准备锁定的 commandline-tools。")
        _, directory = runtime.component("commandline-tools")
        env = runtime.env()
        if args.java_home:
            env["JAVA_HOME"] = str(args.java_home.resolve(strict=True))
        option = "--licenses" if args.command.endswith("licenses") else "--list_installed"
        # stdin remains attached: the official manager, not this wrapper, asks about terms.
        subprocess.run([str(directory / "bin/sdkmanager"), f"--sdk_root={runtime.sdk}", option], env=env, check=True)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as error:
        print(f"失败：{error}", file=sys.stderr)
        sys.exit(1)
