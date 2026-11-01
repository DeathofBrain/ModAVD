"""Small read-only ADB client; never invokes adb or creates a server.

SPDX-License-Identifier: AGPL-3.0-or-later
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

from components import read_json, safe_path

MAX_OUTPUT = 16 * 1024**2


class BsdInfo(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in (
        "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved")]
    _fields_ += [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
    _fields_ += [(n, ctypes.c_uint32) for n in ("nfiles", "pgid", "jobc", "tdev", "tpgid")]
    _fields_ += [("nice", ctypes.c_int32), ("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64)]


def process_identity(pid):
    if sys.platform != "darwin" or type(pid) is not int or pid <= 1:
        raise RuntimeError("只接受可核对的 macOS 进程")
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    library.proc_pidinfo.restype = ctypes.c_int
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_pidpath.restype = ctypes.c_int
    before, after = BsdInfo(), BsdInfo()
    buffer = ctypes.create_string_buffer(4096)
    if library.proc_pidinfo(pid, 3, 0, ctypes.byref(before), ctypes.sizeof(before)) != ctypes.sizeof(before) or before.status == 5 or library.proc_pidpath(pid, buffer, len(buffer)) <= 0:
        raise RuntimeError("进程已退出或身份不可读")
    if library.proc_pidinfo(pid, 3, 0, ctypes.byref(after), ctypes.sizeof(after)) != ctypes.sizeof(after) or (before.start_seconds, before.start_microseconds) != (after.start_seconds, after.start_microseconds):
        raise RuntimeError("读取时进程身份变化")
    return {"pid": pid, "startedAtUs": before.start_seconds * 1000000 + before.start_microseconds,
            "executable": str(Path(os.fsdecode(buffer.value)).resolve(strict=True))}


def listening_process(port):
    if type(port) is not int or port == 5037 or not 1024 <= port <= 65535:
        raise RuntimeError("拒绝默认 5037 或无效 ADB 端口")
    result = subprocess.run(["/usr/sbin/lsof", "-nP", "-a", "-iTCP:%d" % port, "-sTCP:LISTEN", "-Fp"],
                            capture_output=True, text=True, check=True, timeout=10)
    pids = {int(line[1:]) for line in result.stdout.splitlines() if re.fullmatch(r"p\d+", line)}
    if len(pids) != 1:
        raise RuntimeError("私有 ADB 端口未运行或无法唯一归属")
    return process_identity(pids.pop())


def receive_exact(connection, count):
    result = b""
    while len(result) < count:
        part = connection.recv(count - len(result))
        if not part:
            raise RuntimeError("ADB 响应被截断")
        result += part
    return result


def send_service(connection, service):
    raw = service.encode("utf-8")
    if len(raw) > 65535:
        raise RuntimeError("ADB 服务请求过长")
    connection.sendall(("%04x" % len(raw)).encode("ascii") + raw)
    if receive_exact(connection, 4) != b"OKAY":
        # Never print the server's arbitrary failure text.
        raise RuntimeError("ADB 拒绝只读服务")


class ReadOnlyAdb:
    SHELL = frozenset(("getprop sys.boot_completed", "getprop ro.build.version.sdk", "getprop ro.product.cpu.abi",
                       "pm list packages -3"))

    def __init__(self, root, index):
        self.root = Path(root).resolve(strict=True)
        if type(index) is not int or not 0 <= index <= 9 or index == 5:
            raise RuntimeError("实例索引无效")
        self.index, self.serial = index, "emulator-%d" % (20000 + index * 10)
        metadata = read_json(safe_path(self.root, "mas-avd.json"))
        self.port = metadata.get("adbServerPort", 20050)
        if metadata.get("sdkRoot") or type(self.port) is not int or self.port == 5037 or not 1024 <= self.port <= 65535:
            raise RuntimeError("仅支持包内 SDK 的已存在私有 ADB")
        self.server, self.qemu = None, None

    def manager_info(self):
        cli = safe_path(self.root, "tools/modavd")
        manifest = read_json(safe_path(self.root, "PACKAGE-MANIFEST.json"))
        from components import digest
        if not cli.is_file() or digest(cli) != manifest.get("files", {}).get("tools/modavd", {}).get("sha256"):
            raise RuntimeError("Manager CLI 与包清单不同")
        result = subprocess.run([str(cli), "--root", str(self.root), "info", str(self.index), "--json"],
                                capture_output=True, text=True, check=True, timeout=20)
        data = json.loads(result.stdout)
        if data.get("ok") is not True or not isinstance(data.get("data"), dict):
            raise RuntimeError("Manager 无法报告实例身份")
        info = data["data"]
        avd = safe_path(self.root, "avd/mas_%d.avd" % self.index)
        if info.get("index") != self.index or info.get("name") != "mas_%d" % self.index or info.get("serial") != self.serial or info.get("running") is not True or info.get("booted") is not True or info.get("busy") or info.get("creation") or Path(info.get("path", "")).resolve() != avd:
            raise RuntimeError("实例未启动完成或不属于此包")
        process = process_identity(info.get("pid"))
        path = Path(process["executable"])
        if not path.is_relative_to(self.root / "sdk/emulator/qemu/darwin-aarch64") or path.name not in ("qemu-system-aarch64", "qemu-system-aarch64-headless") or info.get("started_at_ms") != process["startedAtUs"] // 1000:
            raise RuntimeError("QEMU 可执行文件或启动时间与实例不符")
        return process

    def owned(self):
        server = listening_process(self.port)
        if Path(server["executable"]) != safe_path(self.root, "sdk/platform-tools/adb"):
            raise RuntimeError("私有 ADB 服务不是本包的 adb")
        qemu = self.manager_info()
        if (self.server is not None and self.server != server) or (self.qemu is not None and self.qemu != qemu):
            raise RuntimeError("服务或实例身份已变化")
        self.server, self.qemu = server, qemu

    def _read(self, service, limit=MAX_OUTPUT):
        self.owned()
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=10) as connection:
                connection.settimeout(15)
                send_service(connection, "host:transport:" + self.serial)
                send_service(connection, service)
                output = bytearray()
                while True:
                    part = connection.recv(min(65536, limit + 1 - len(output)))
                    if not part:
                        break
                    output.extend(part)
                    if len(output) > limit:
                        raise RuntimeError("ADB 只读响应超过限额")
                return bytes(output)
        finally:
            self.owned()

    def shell(self, command):
        if command not in self.SHELL and not re.fullmatch(r"dumpsys package [a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+", command):
            raise RuntimeError("此 ADB 客户端只支持固定只读查询")
        return self._read("shell:" + command).decode("utf-8", errors="replace")

    def screenshot(self):
        raw = self._read("exec:screencap -p")
        if len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
            raise RuntimeError("截图不是有效 PNG")
        return raw
