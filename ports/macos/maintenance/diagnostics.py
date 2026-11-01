"""Privacy-filtered package diagnostics and compatibility reports.

SPDX-License-Identifier: AGPL-3.0-or-later
No broad recursive log/config collection, image reads or process environments.
"""
from __future__ import annotations

import datetime as dt
import getpass
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import tempfile
import time
import zipfile

from components import read_json, safe_path

MAX_TEXT = 2 * 1024**2
MAX_TOTAL = 20 * 1024**2
CONFIG_KEYS = frozenset((
    "AvdId", "abi.type", "hw.cpu.arch", "tag.id", "PlayStore.enabled", "hw.cpu.ncore", "hw.ramSize",
    "hw.lcd.width", "hw.lcd.height", "hw.lcd.density", "hw.gpu.enabled", "hw.gpu.mode", "hw.keyboard",
    "hw.mainKeys", "hw.initialOrientation", "disk.dataPartition.size", "showDeviceFrame", "skin.dynamic",
    "fastboot.forceColdBoot", "fastboot.forceFastBoot", "disk.cachePartition.size", "hw.audioInput", "hw.audioOutput",
))
INSTANCE_KEYS = frozenset(("memoryMb", "initialized", "bridge", "softwareRenderer", "launcher"))
SECRET = re.compile(r"token|password|passwd|secret|credential|authorization|cookie|api.?key|adbkey|private.?key|cdk|account|username|email|login|access.?key|(?<!\w)(?:key|user|uid|serial|device.?id)\s*[:=]|账号|账户|密码|口令|兑换码|密钥|用户名|手机号", re.I)
GAMES = {
    "com.hypergryph.arknights": "明日方舟（官服）",
    "com.hypergryph.arknights.bilibili": "明日方舟（B服）",
    "com.miHoYo.bh3": "崩坏3", "com.miHoYo.Yuanshen": "原神",
    "com.miHoYo.hkrpg": "崩坏：星穹铁道", "com.miHoYo.Nap": "绝区零",
    "com.sunborn.girlsfrontline.cn": "少女前线",
}
NON_GAMES = frozenset(("org.modavd.ime", "de.jrpie.android.launcher"))


def sanitize(text, root):
    """Fail closed on credential/account lines, then remove common identifiers."""
    result = []
    identifiers = [str(Path(root).resolve()), str(Path.home()), platform.node(), getpass.getuser()]
    for line in text.splitlines():
        if SECRET.search(line):
            result.append("[含账号或凭据字段的整行已移除]")
            continue
        for identifier in identifiers:
            if len(identifier) >= 3:
                line = line.replace(identifier, "[本机标识]")
        line = re.sub(r'''(["'])(?:[A-Z]:[\\/]|/)[^"']*\1''', "[路径]", line, flags=re.I)
        line = re.sub(r"[A-Z]:[\\/][^\s\"'<>]*|(?<![\w:])/[^\s\"'<>]+", "[路径]", line, flags=re.I)
        line = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[邮箱]", line)
        line = re.sub(r"https?://\S+", "[URL]", line)
        line = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b", "[IP]", line)
        line = re.sub(r"(?<![\w])(?:[A-Fa-f0-9]{2}:){5}[A-Fa-f0-9]{2}(?![\w])", "[MAC]", line)
        line = re.sub(r"\b[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\b", "[UUID]", line)
        line = re.sub(r"\b(?:[A-Fa-f0-9]{16,}|[A-Za-z0-9_+/=-]{32,})\b", "[长标识]", line)
        line = re.sub(r"\b1[3-9]\d{9}\b", "[手机号]", line)
        line = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", line)
        result.append(line[:2048])
    return "\n".join(result)


def bounded_text(path, root, tail=False):
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise RuntimeError("跳过链接或特殊文件")
    with path.open("rb") as source:
        if tail:
            source.seek(max(0, path.stat().st_size - MAX_TEXT))
        raw = source.read(MAX_TEXT)
    if b"\x00" in raw:
        raise RuntimeError("跳过二进制文件")
    return sanitize(raw.decode("utf-8", errors="replace"), root)


def host_info():
    result = {"os": "macOS" if platform.system() == "Darwin" else platform.system(),
              "version": platform.mac_ver()[0] if platform.system() == "Darwin" else platform.release(),
              "architecture": platform.machine(), "logicalCpus": os.cpu_count()}
    if platform.system() == "Darwin":
        for key, name in (("hw.memsize", "memoryBytes"), ("hw.physicalcpu", "physicalCpus"), ("machdep.cpu.brand_string", "cpuModel")):
            try:
                data = subprocess.run(["/usr/sbin/sysctl", "-n", key], capture_output=True, text=True, check=True, timeout=5).stdout.strip()
                result[name] = int(data) if data.isdigit() else data[:128]
            except (OSError, subprocess.SubprocessError):
                result[name] = "unavailable"
    return result


def local_process_summary(root):
    from components import process_paths
    try:
        paths = process_paths()
        return {"status": "read-only executable inventory", "qemuCount": sum(
            p.is_relative_to(root / "sdk/emulator/qemu") and p.name.startswith("qemu-system-") for p in paths),
            "privateAdbCount": sum(p == (root / "sdk/platform-tools/adb").resolve() for p in paths)}
    except (OSError, ValueError, RuntimeError):
        return {"status": "unavailable"}


def properties(path, root):
    # Read raw values only for allowlisted keys; do not retain other fields.
    text = bounded_text(path, root)
    result = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in CONFIG_KEYS:
            result[key.strip()] = value.strip()[:256]
    return result


class Diagnostics:
    def __init__(self, root, adb_factory=None, crash_directory=None, crashpad_directories=None):
        self.root = Path(root).expanduser().resolve(strict=True)
        if not safe_path(self.root, ".modavd-macos-package.json").is_file():
            raise RuntimeError("请选择 ModAVD macOS 包根目录")
        self.adb_factory = adb_factory
        self.crash_directory = Path(crash_directory) if crash_directory else Path.home() / "Library/Logs/DiagnosticReports"
        self.crashpad_directories = list(crashpad_directories) if crashpad_directories is not None else [Path("/tmp") / ("android-" + getpass.getuser())]
        self.omitted = []

    def config(self):
        result = {"components": {}, "instances": {}}
        try:
            metadata = read_json(safe_path(self.root, "mas-avd.json"), MAX_TEXT)
            for key in ("platform-tools", "emulator", "system-image", "launcher", "ime"):
                value = metadata.get("components", {}).get(key)
                if isinstance(value, str) and re.fullmatch(r"[\dA-Za-z._-]{1,40}", value):
                    result["components"][key] = value
            for index, instance in metadata.get("instances", {}).items():
                if not re.fullmatch(r"[0-9]", index) or not isinstance(instance, dict):
                    continue
                result["instances"][index] = {k: sanitize(str(v), self.root) for k, v in instance.items() if k in INSTANCE_KEYS and isinstance(v, (str, bool, int))}
        except (OSError, ValueError, TypeError, RuntimeError):
            self.omitted.append("mas-avd.json 不可读或格式不支持")
        for index in range(10):
            try:
                directory = safe_path(self.root, "avd/mas_%d.avd" % index)
                if not directory.is_dir():
                    continue
                entry = result["instances"].setdefault(str(index), {})
                entry["files"] = {}
                for name in ("config.ini", "hardware-qemu.ini", "emulator-user.ini"):
                    path = safe_path(directory, name)
                    if path.is_file():
                        entry["files"][name] = properties(path, self.root)
            except (OSError, ValueError, RuntimeError):
                self.omitted.append("实例 %d 配置不可读或存在链接" % index)
        return result

    def live(self, instances, screenshots=False):
        from adb_readonly import ReadOnlyAdb
        factory = self.adb_factory or ReadOnlyAdb
        states, images = {}, {}
        for index in sorted(instances, key=int):
            if int(index) == 5:
                continue
            try:
                client = factory(self.root, int(index))
                if client.shell("getprop sys.boot_completed").strip() != "1":
                    raise RuntimeError("not booted")
                api = client.shell("getprop ro.build.version.sdk").strip()
                abi = client.shell("getprop ro.product.cpu.abi").strip()
                state = {"status": "booted", "api": api if re.fullmatch(r"\d{1,3}", api) else "unknown",
                         "abi": abi if abi in ("arm64-v8a", "x86_64", "armeabi-v7a", "x86") else "unknown", "packages": []}
                packages = sorted(set(re.findall(r"^package:([a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+)$", client.shell("pm list packages -3"), re.M)))
                for package in packages[:100]:
                    if package in NON_GAMES:
                        continue
                    data = client.shell("dumpsys package " + package)
                    version = re.search(r"^\s*versionName=([\dA-Za-z._+()-]{1,80})\s*$", data, re.M)
                    state["packages"].append({"package": package, "name": GAMES.get(package, package),
                                              "version": version[1] if version else "unknown"})
                states[index] = state
                if screenshots:
                    images["live/instance-%s.png" % index] = client.screenshot()
            except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError):
                states[index] = {"status": "unavailable: stopped, foreign, changed or unreadable"}
        return states, images

    def logs(self):
        candidates = []
        try:
            path = safe_path(self.root, "logs")
            if path.is_dir():
                candidates.extend(p for p in path.iterdir() if not p.is_symlink() and p.is_file() and re.fullmatch(r"(?:emulator|qemu|mas[_-]?[0-9]|manager|modavd)[A-Za-z0-9_.-]*\.(?:log|txt)", p.name, re.I))
        except (OSError, RuntimeError):
            self.omitted.append("logs 目录不可读或存在链接")
        for index in range(10):
            for name in ("emulator.log", "qemu.log", "launch.log"):
                try:
                    path = safe_path(self.root, "avd/mas_%d.avd/%s" % (index, name))
                    if path.is_file():
                        candidates.append(path)
                except (OSError, RuntimeError):
                    continue
        result = {}
        for number, path in enumerate(sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:8]):
            try:
                result["logs/emulator-%02d.txt" % number] = bounded_text(path, self.root, tail=True)
            except (OSError, ValueError, RuntimeError):
                self.omitted.append("一个模拟器日志因类型或权限被跳过")
        return result

    def crashes(self, now=None):
        """Text summaries only; never include minidumps, memory or raw IPS JSON."""
        now = time.time() if now is None else now
        result, selected = {}, []
        if not self.crash_directory.is_dir() or self.crash_directory.is_symlink():
            return result
        try:
            paths = list(self.crash_directory.iterdir())
        except OSError:
            self.omitted.append("本地崩溃报告目录不可读")
            return result
        for path in paths:
            if not re.fullmatch(r"(?:qemu-system-aarch64(?:-headless)?|emulator|ModAVD|modavd-manager|modavd-webview)[_-].*\.(?:ips|crash)", path.name, re.I) or path.is_symlink():
                continue
            try:
                if stat.S_ISREG(path.stat().st_mode) and now - 7 * 86400 <= path.stat().st_mtime <= now and path.stat().st_size <= MAX_TEXT:
                    selected.append(path)
            except OSError:
                continue
        for path in sorted(selected, key=lambda p: p.stat().st_mtime, reverse=True):
            if len(result) >= 3:
                break
            try:
                raw = path.read_text(encoding="utf-8")
                summary = {}
                if path.suffix == ".ips":
                    decoder, values, position = json.JSONDecoder(), [], 0
                    while position < len(raw):
                        while position < len(raw) and raw[position].isspace():
                            position += 1
                        if position >= len(raw):
                            break
                        value, position = decoder.raw_decode(raw, position)
                        values.append(value)
                    data = next((v for v in values if isinstance(v, dict) and isinstance(v.get("procPath"), str)), None)
                    if not data or not self.owned_crash_path(data["procPath"]):
                        continue
                    summary = {k: data[k] for k in ("procName", "cpuType") if isinstance(data.get(k), str)}
                    for key, fields in (("exception", ("type", "signal", "codes")), ("termination", ("namespace", "code"))):
                        obj = data.get(key, {})
                        if isinstance(obj, dict):
                            summary[key] = {k: v for k, v in obj.items() if k in fields and isinstance(v, (str, int))}
                else:
                    match = re.search(r"^Path:\s+(.+)$", raw, re.M)
                    if not match or not self.owned_crash_path(match[1].strip()):
                        continue
                    for line in raw.splitlines():
                        if re.match(r"^(Process|Code Type|OS Version|Exception Type|Exception Codes|Termination Reason|Crashed Thread):", line):
                            key, value = line.split(":", 1)
                            summary[key] = value.strip()[:512]
                summary["format"] = "privacy-filtered text summary; no memory dump or raw crash file"
                def cleaned(value):
                    if isinstance(value, dict):
                        return {k: cleaned(v) for k, v in value.items()}
                    return sanitize(value, self.root) if isinstance(value, str) else value
                result["crash/crash-%02d.json" % (len(result) + 1)] = json.dumps(cleaned(summary), ensure_ascii=False, indent=2)
            except (OSError, ValueError, TypeError):
                self.omitted.append("一个近七日崩溃报告格式不支持，未收集原文件")
        return result

    def owned_crash_path(self, value):
        path = Path(value).resolve()
        return any(path.is_relative_to(self.root / directory) for directory in ("sdk/emulator", "ModAVD.app", "tools"))

    def crashpad_inventory(self, now=None):
        """Stat only the fixed ModAVD database; never open minidump bytes."""
        now = time.time() if now is None else now
        records = []
        for base in self.crashpad_directories:
            try:
                base = Path(base)
                if base.is_symlink() or not base.is_dir() or base.stat().st_uid != os.getuid():
                    continue
                database = safe_path(base, "emu-crash-37.2.10-mas.db")
                if not database.is_dir() or database.stat().st_uid != os.getuid():
                    continue
                for category in ("pending", "completed"):
                    directory = safe_path(database, category)
                    if not directory.is_dir():
                        continue
                    for path in directory.iterdir():
                        if not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\.dmp", path.name) or path.is_symlink():
                            continue
                        info = path.stat()
                        if stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and now - 7 * 86400 <= info.st_mtime <= now:
                            records.append({"modifiedAt": dt.datetime.fromtimestamp(info.st_mtime, dt.timezone.utc).isoformat(),
                                            "bytes": info.st_size, "state": category})
            except (OSError, ValueError, RuntimeError):
                self.omitted.append("固定 ModAVD 崩溃数据库不可读；未读取转储")
        return {"source": "default local ModAVD 37.2.10 crash database; same-version installation ownership is unknown",
                "scope": "last seven days; newest three; file metadata only; no filenames, UUIDs or minidump bytes",
                "records": sorted(records, key=lambda r: r["modifiedAt"], reverse=True)[:3]}

    def snapshot(self, live=False, screenshots=False):
        config = self.config()
        states, images = self.live(config["instances"], screenshots) if live else ({}, {})
        result = {"schema": 1, "host": host_info(), "configuration": config,
                  "runtimeProcesses": local_process_summary(self.root),
                  "live": states, "liveRequested": live, "screenshotRequested": screenshots}
        return result, images

    def report(self, live=False):
        state, _ = self.snapshot(live)
        host = state["host"]
        lines = ["ModAVD macOS 兼容性报告（脱敏）", "%s %s / %s" % (host["os"], host["version"], host["architecture"]),
                 "CPU：%s；逻辑核：%s；内存：%s GiB" % (host.get("cpuModel", "unknown"), host["logicalCpus"], round(host.get("memoryBytes", 0) / 1024**3, 1)),
                 "组件：" + json.dumps(state["configuration"]["components"], ensure_ascii=False)]
        for index, config in state["configuration"]["instances"].items():
            lines.append("实例 %s：内存 %s MiB" % (index, config.get("memoryMb", "unknown")))
            observed = state["live"].get(index)
            if observed and observed.get("status") == "booted":
                packages = observed["packages"]
                lines.append("  第三方应用（含游戏）：" + ("、".join(p["name"] + " " + p["version"] for p in packages) if packages else "无"))
            else:
                lines.append("  游戏包：未读取（可对已启动且归属验证通过的实例添加 --live）")
        lines.extend(("自动化工具及版本：", "运行效果或问题："))
        return sanitize("\n".join(lines), self.root) + "\n"

    def collect(self, output, live=False, screenshots=False):
        if screenshots and not live:
            raise RuntimeError("--screenshot 必须同时指定 --live；截图保留完整画面，可能含个人信息")
        output = Path(output).expanduser().absolute()
        if output.exists() or output.is_symlink():
            raise RuntimeError("输出文件已存在，拒绝覆盖")
        # Prevent accidental replacement of package files or placement in SDK/AVD.
        for protected in (self.root / "sdk", self.root / "avd", self.root / "tools", self.root / "provenance", self.root / "licenses", self.root / "ModAVD.app"):
            if output.resolve().is_relative_to(protected):
                raise RuntimeError("问题包输出不能放入程序、组件或实例目录")
        snapshot, images = self.snapshot(live, screenshots)
        files = self.logs()
        files.update(self.crashes())
        files["crash/crashpad-inventory.json"] = json.dumps(self.crashpad_inventory(), ensure_ascii=False, indent=2)
        snapshot["omitted"] = self.omitted
        files["summary.json"] = json.dumps(snapshot, ensure_ascii=False, indent=2)
        files["README-privacy.txt"] = (
            "本问题包只在本机生成，未上传。默认已过滤账号/凭据行、用户名、主机名、路径、邮箱、URL、IP 和长标识。\n"
            "范围：硬件型号/系统版本、白名单实例配置、最多8份模拟器文本日志尾部（每份2MiB）、近7日最多3份本包崩溃摘要。\n"
            "另含默认 ModAVD 同版本 Crashpad 数据库近7日最新3份转储的时间/大小摘要，不读转储字节；不能确定其来自哪份同版本安装。\n"
            "不含：磁盘镜像、userdata、快照、密钥、gRPC token、CDK、账户配置、MAS/MAA配置、环境变量、原始崩溃转储或内存。\n"
            "liveRequested=true 时只查询已存在且验证归属的私有ADB和本包实例，不启动服务，不使用5037。\n"
            "如 screenshotRequested=true，live/*.png 是用户显式选择的完整画面，未经像素脱敏，可能包含账号；公开前请逐张查看或删除。\n"
            "文本过滤无法识别任意自定义文本中的全部个人信息；公开前仍请检查问题包。\n"
        )
        encoded = {name: text.encode("utf-8") for name, text in files.items()}
        encoded.update(images)
        if sum(len(value) for value in encoded.values()) > MAX_TOTAL:
            raise RuntimeError("问题包超过20MiB限额；请减少日志或取消截图")
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".modavd-diagnostics-", dir=output.parent)
        temporary = Path(temporary)
        os.close(fd)
        try:
            os.chmod(temporary, 0o600)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
                for name, value in sorted(encoded.items()):
                    info = zipfile.ZipInfo(name)
                    info.external_attr = 0o600 << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    bundle.writestr(info, value)
            os.link(temporary, output)  # Atomic publication without overwriting.
        finally:
            temporary.unlink(missing_ok=True)
        return {"file": str(output), "entries": sorted(encoded), "bytes": output.stat().st_size}


def copy_to_clipboard(text):
    if platform.system() != "Darwin":
        raise RuntimeError("剪贴板仅支持 macOS pbcopy")
    subprocess.run(["/usr/bin/pbcopy"], input=text, text=True, check=True, timeout=10)
