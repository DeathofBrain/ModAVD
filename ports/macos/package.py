#!/usr/bin/env python3
"""Assemble a local macOS ARM64 directory; never overwrite a running installation."""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / ".macos-work"
RESERVE = 20 * 1024**3
MARKER = ".modavd-macos-package.json"
MANIFEST = "PACKAGE-MANIFEST.json"
PAYLOAD_DIRS = ("sdk", "tools", "ModAVD.app", "components", "provenance", "licenses")
PAYLOAD_FILES = (MARKER, "README.zh-CN.txt", "modavd-manifest.json")
LIBC = ctypes.CDLL(None, use_errno=True)
IMAGE = "system-images/android-34/google_apis/arm64-v8a"
APK_LOCK_PATH = ROOT / "ports/macos/components.lock.json"
APK_LOCK = json.loads(APK_LOCK_PATH.read_text())
IME = APK_LOCK["components"]["ime"]
LAUNCHER = APK_LOCK["components"]["launcher"]
MAINTENANCE = ROOT / "ports/macos/maintenance"
MAINTENANCE_FILES = ("maintenance.py", "components.py", "diagnostics.py", "adb_readonly.py",
                     "components.lock.json", "README.md", "licenses/android-sdk-license.txt",
                     "licenses/android-sdk-arm-dbt-license.txt")


def fail(message):
    raise RuntimeError(message)


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def space(path, additional=0):
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    if free - additional < RESERVE:
        fail(f"空间停线：剩余 {free / 1024**3:.1f} GiB，下一步约需 {additional / 1024**3:.1f} GiB；须保留 20 GiB。")
    return free


def relative_name(name):
    value = PurePosixPath(name)
    if not name or value.as_posix() != name or value.is_absolute() or ".." in value.parts or "\\" in name or name == ".":
        fail(f"清单路径不安全：{name!r}")
    return Path(*value.parts)


def entries(root, boundary=None):
    """Do not follow directory symlinks or silently omit special files."""
    boundary = (boundary or root).resolve()
    for parent, directories, files in os.walk(root, followlinks=False,
                                            onerror=lambda error: fail(f"无法读取输入目录：{error}")):
        for name in sorted(directories + files):
            path = Path(parent) / name
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode):
                target = os.readlink(path)
                if os.path.isabs(target) or not path.resolve(strict=True).is_relative_to(boundary):
                    fail(f"拒绝外部或绝对符号链接：{path} -> {target}")
            elif not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                fail(f"不支持的输入文件类型：{path}")
            yield path


def check_apk(path, config, label):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != config["size"] or digest(path) != config["sha256"]:
        fail(f"{label} APK 不匹配固定 v{config['version']} 大小或 SHA-256：{path}")


def check_ime(path):
    check_apk(path, IME, "IME")


def check_launcher(path):
    check_apk(path, LAUNCHER, "µLauncher")
    for file_key, hash_key in (("licenseFile", "licenseSha256"), ("embeddedLicenseFile", "embeddedLicenseSha256")):
        if digest(ROOT / "ports/macos/licenses" / LAUNCHER[file_key]) != LAUNCHER[hash_key]:
            fail("µLauncher 随附许可文本与锁文件不符。")
    with zipfile.ZipFile(path) as apk:
        if "classes.dex" not in apk.namelist():
            fail("µLauncher APK 缺少 DEX 字节码。")
        for member in apk.infolist():
            if member.filename.startswith("lib/") or member.filename.lower().endswith((".so", ".dll", ".dylib")):
                fail("µLauncher APK 出现原生库，须重新核实 ARM64 兼容性。")
            if not member.is_dir():
                with apk.open(member) as source:
                    if source.read(4) == b"\x7fELF":
                        fail("µLauncher APK 出现 ELF，须重新核实 ARM64 兼容性。")
        if hashlib.sha256(apk.read(LAUNCHER["embeddedLicenseMember"])).hexdigest() != LAUNCHER["embeddedLicenseSha256"]:
            fail("µLauncher APK 内嵌许可与随附文本不符。")


def fetch_apk(path, config, checker):
    if os.path.lexists(path):
        checker(path)
        return path
    space(path.parent, config["size"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".modavd-apk-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as output, urllib.request.urlopen(config["url"], timeout=30) as response:
            while chunk := response.read(65536):
                if output.tell() + len(chunk) > config["size"]:
                    fail("APK 下载超过固定大小，已停止。")
                space(path.parent, len(chunk))
                output.write(chunk)
        checker(temporary)
        # link is atomic and refuses an existing destination, including a symlink.
        os.link(temporary, path)
        return path
    finally:
        temporary.unlink(missing_ok=True)


def fetch_ime(path):
    return fetch_apk(path, IME, check_ime)


def fetch_launcher(path):
    return fetch_apk(path, LAUNCHER, check_launcher)


def arm64_executable(path):
    if not path.is_file() or not path.stat().st_mode & 0o111:
        fail(f"缺少可执行文件：{path}")
    with path.open("rb") as source:
        header = source.read(4096)
    arm64 = 0x0100000C
    architectures = []
    if header[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf") and len(header) >= 8:
        endian = "<" if header[:4] == b"\xcf\xfa\xed\xfe" else ">"
        architectures.append(struct.unpack(endian + "I", header[4:8])[0])
    elif header[:4] in (b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf", b"\xbe\xba\xfe\xca", b"\xbf\xba\xfe\xca") and len(header) >= 8:
        endian = ">" if header[0] == 0xCA else "<"
        stride = 32 if header[:4] in (b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca") else 20
        count = struct.unpack(endian + "I", header[4:8])[0]
        if count > 64 or 8 + stride * count > len(header):
            fail(f"Mach-O 架构头无效：{path}")
        architectures = [struct.unpack_from(endian + "I", header, 8 + stride * i)[0] for i in range(count)]
    if arm64 not in architectures:
        fail(f"文件不是含 ARM64 的 Mach-O：{path}")


def contains(path, needle):
    tail = b""
    with path.open("rb") as source:
        while data := source.read(1024 * 1024):
            block = tail + data
            if needle in block:
                return True
            tail = block[-len(needle):]
    return False


def properties(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))


def verify_receipt(directory, files):
    actual = {p.relative_to(directory).as_posix() for p in entries(directory) if not p.is_dir() or p.is_symlink()}
    if actual != set(files):
        fail(f"组件文件集合不匹配校验收据：{directory}")
    for name, expected in files.items():
        path = directory / relative_name(name)
        if path.is_symlink() or digest(path) != expected:
            fail(f"组件 SHA-256 校验失败：{path}")


def validate_inputs(emulator, manager, runtime):
    for directory in (emulator, manager, runtime):
        if not directory.is_dir():
            fail(f"输入目录尚未准备：{directory}")
    for name in ("emulator", "qemu/darwin-aarch64/qemu-system-aarch64", "qemu/darwin-aarch64/qemu-system-aarch64-headless"):
        arm64_executable(emulator / name)
    props = properties(emulator / "source.properties")
    if props.get("Pkg.BuildId", "").strip() != "mas-36" or props.get("Pkg.Revision", "").strip() != "37.2.10":
        fail("核心 source.properties 必须来自锁定 mas-36 / 37.2.10 构建；不能在装配时改写版本。")
    if not contains(emulator / "qemu/darwin-aarch64/qemu-system-aarch64-headless", b"free-page-reporting"):
        fail("核心缺少 ModAVD free-page-reporting 标记。")
    for name in ("NOTICE.txt", "NOTICE.csv"):
        if not (emulator / name).is_file() or not (emulator / name).stat().st_size:
            fail(f"核心缺少 CMake 安装生成的许可清单：{name}")
    for name in ("tools/modavd", "tools/modavd-manager", "tools/modavd-webview", "ModAVD.app/Contents/MacOS/ModAVD", "ModAVD.app/Contents/MacOS/modavd-webview"):
        arm64_executable(manager / name)
    manager_receipt = json.loads((manager / ".modavd-manager-build.json").read_text())
    if manager_receipt.get("upstream") != "https://github.com/ModAVD/ModAVDManager.git" or manager_receipt.get("revision") != "21c2a3ffa22eb37a5e39e61b1b53f2258d0f3e7a":
        fail("管理器来源不匹配锁定仓库与提交。")
    if manager_receipt.get("patch_sha256") != digest(ROOT / "incoming/macos-manager/0001-darwin-arm64.patch"):
        fail("管理器产物与当前 Darwin 补丁指纹不符，请先重新构建。")
    checksums = json.loads((manager / "SHA256SUMS.json").read_text())
    verify_receipt(manager, {**checksums, "SHA256SUMS.json": digest(manager / "SHA256SUMS.json")})
    lock = json.loads((ROOT / "ports/macos/runtime/components.lock.json").read_text())
    receipts = {}
    for name in ("platform-tools", "system-image"):
        config = lock["components"][name]
        receipt = json.loads((runtime / "receipts" / (name + ".json")).read_text())
        if receipt.get("archiveSha1") != config["sha1"] or receipt.get("revision") != config["revision"] or not receipt.get("files"):
            fail(f"组件收据与锁定版本不符：{name}")
        license_receipt = json.loads((runtime / "receipts" / (config["license"] + ".json")).read_text())
        if license_receipt.get("id") != config["license"] or not license_receipt.get("acceptedAt") or license_receipt.get("sha256") != config["licenseTextSha256"]:
            fail(f"缺少完整条款的明确接受记录：{config['license']}")
        license_file = ROOT / "ports/macos/runtime/licenses" / (config["license"] + ".txt")
        if digest(license_file) != config["licenseTextSha256"]:
            fail("本地许可证文本不匹配组件锁文件。")
        verify_receipt(runtime / "sdk" / config["destination"], receipt["files"])
        receipts[name] = receipt
    arm64_executable(runtime / "sdk/platform-tools/adb")
    for name in ("system.img", "vendor.img", "kernel-ranchu", "ramdisk.img"):
        if not (runtime / "sdk" / IMAGE / name).is_file():
            fail(f"镜像缺少 {name}")
    return props, manager_receipt, lock, receipts


def copy_file(source, destination, stats):
    before = source.stat()
    space(destination.parent)
    cloned = False
    if sys.platform == "darwin":
        clonefile = LIBC.clonefile
        clonefile.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int)
        clonefile.restype = ctypes.c_int
        cloned = clonefile(os.fsencode(source), os.fsencode(destination), 0) == 0
    if cloned:
        stats["clonedFiles"] += 1
    else:
        if os.path.lexists(destination):
            fail(f"文件复制目标已存在，拒绝覆盖：{destination}")
        space(destination.parent, before.st_size)
        with source.open("rb") as src, destination.open("xb") as dst:
            while chunk := src.read(1024 * 1024):
                space(destination.parent, len(chunk))
                dst.write(chunk)
        stats["copiedFiles"] += 1
    shutil.copystat(source, destination)
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or digest(source) != digest(destination):
        fail(f"复制期间输入发生变化或校验失败：{source}")
    space(destination.parent)


def copy_tree(source, destination, stats):
    destination.mkdir(parents=True)
    directories = [source]
    for path in entries(source):
        target = destination / path.relative_to(source)
        if path.is_symlink():
            target.symlink_to(os.readlink(path))
        elif path.is_dir():
            target.mkdir()
            directories.append(path)
        else:
            copy_file(path, target, stats)
    for path in reversed(directories):
        shutil.copystat(path, destination / path.relative_to(source))


def install_maintenance(stage, stats):
    """Ship the user tools and their fixed resources without development caches."""
    if not (MAINTENANCE / "maintenance.py").is_file():
        fail("随包维护工具尚未准备：ports/macos/maintenance/maintenance.py")
    destination = stage / "tools/maintenance"
    destination.mkdir()
    for name in MAINTENANCE_FILES:
        relative = relative_name(name)
        path = MAINTENANCE / relative
        target = destination / relative
        if not path.is_file() or any((MAINTENANCE / Path(*relative.parts[:i])).is_symlink()
                                     for i in range(1, len(relative.parts) + 1)):
            fail(f"维护工具资源缺失或是符号链接：{relative}")
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_file(path, target, stats)
    commands = {"modavd-maintenance": "", "fetch-components": "components repair",
                "collect-info": "collect", "report-config": "report"}
    for name, command in commands.items():
        launcher = stage / "tools" / name
        launcher.write_text(
            '#!/bin/sh\nset -eu\n'
            'tools_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"\n'
            'exec python3 -B "$tools_dir/maintenance/maintenance.py" '
            '--root "$tools_dir/.." ' + (command + " " if command else "") + '"$@"\n')
        launcher.chmod(0o755)


def git_state(path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(path), *args], stderr=subprocess.DEVNULL)
    try:
        return {"head": git("rev-parse", "HEAD").decode().strip(),
                "headTree": git("rev-parse", "HEAD^{tree}").decode().strip(),
                "workingDiffSha256": hashlib.sha256(git("diff", "--binary", "HEAD")).hexdigest(),
                "status": git("status", "--porcelain").decode().splitlines()}
    except (OSError, subprocess.CalledProcessError):
        return {"status": "unavailable"}


def patch_manifest():
    series = json.loads((ROOT / "patches/series.json").read_text())
    active = ("qemu/mas36", "gfxstream/mas35", "crashpad/module-version22")
    record = {"baseSeries": [s for s in series if s["dir"] in active], "files": {}}
    folders = [ROOT / "patches" / name for name in active]
    folders += [ROOT / "incoming" / name for name in
                ("macos-build", "macos-qemu", "macos-gfxstream", "macos-aemu", "macos-manager")]
    for folder in folders:
        for path in sorted(folder.glob("*.patch")):
            record["files"][path.relative_to(ROOT).as_posix()] = digest(path)
    return record


def inventory(root, immutable_only=False):
    def payload_paths():
        for name in PAYLOAD_FILES:
            path = root / name
            if path.is_symlink() or not path.is_file():
                fail(f"装配元数据缺失或是符号链接：{name}")
            yield path
        for name in PAYLOAD_DIRS:
            path = root / name
            if path.is_symlink() or not path.is_dir():
                fail(f"装配负载目录缺失或是符号链接：{name}")
            yield path
            yield from entries(path, boundary=root)

    result = {}
    for path in payload_paths() if immutable_only else entries(root):
        name = path.relative_to(root).as_posix()
        if name == MANIFEST:
            continue
        info = path.lstat()
        record = {"mode": stat.S_IMODE(info.st_mode)}
        if path.is_symlink():
            record.update(type="symlink", target=os.readlink(path))
        elif path.is_dir():
            record.update(type="directory")
        else:
            record.update(type="file", bytes=info.st_size, sha256=digest(path))
        result[name] = record
    return result


def verify(output):
    if output.is_symlink() or (output / MARKER).is_symlink() or not (output / MARKER).is_file():
        fail("输出目录没有本工具的 owned 标记。")
    if (output / MANIFEST).is_symlink() or not (output / MANIFEST).is_file():
        fail("装配清单缺失或是符号链接。")
    marker = json.loads((output / MARKER).read_text())
    manifest = json.loads((output / MANIFEST).read_text())
    if marker.get("schema") != 1 or marker.get("tool") != "ModAVD macOS local assembler" or manifest.get("owner") != marker.get("owner"):
        fail("装配标记与清单不符。")
    # Running the manager creates user state. Only verify the immutable payload,
    # while permitting newly created avd/logs/mas-avd.json files outside it.
    actual = inventory(output, immutable_only=True)
    for name, expected in manifest["files"].items():
        relative_name(name)
        if actual.get(name) != expected:
            fail(f"装配文件不匹配清单：{name}")
    if actual.keys() != manifest["files"].keys():
        fail("装配负载目录出现清单之外的文件，请单独检查。")
    return manifest


def rename_exclusive(source, destination):
    """Publish atomically without replacing even a concurrently created empty directory."""
    if sys.platform == "darwin":
        rename = LIBC.renamex_np
        rename.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        result = rename(os.fsencode(source), os.fsencode(destination), 0x4)  # RENAME_EXCL
    elif sys.platform == "linux" and hasattr(LIBC, "renameat2"):
        rename = LIBC.renameat2
        rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        rename.restype = ctypes.c_int
        result = rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1)  # RENAME_NOREPLACE
    else:
        fail("宿主缺少原子 no-replace 目录发布接口。")
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


def assemble(args):
    output = args.output
    if os.path.lexists(output):
        kind = "本工具已有输出" if (output / MARKER).is_file() else "用户目录或未标记路径"
        fail(f"拒绝覆盖{kind}：{output}；请 verify 或选择新的 --output，保留现有实例和文件。")
    for source in (args.emulator, args.manager, args.runtime_root):
        if output.is_relative_to(source) or source.is_relative_to(output):
            fail("输出与输入目录不能互相嵌套。")
    print("校验核心、管理器与已获许可的固定 SDK 组件……", flush=True)
    props, manager_receipt, lock, receipts = validate_inputs(args.emulator, args.manager, args.runtime_root)
    ime = fetch_ime(args.ime_apk)
    launcher = None if args.without_launcher else fetch_launcher(args.launcher_apk)
    space(output.parent)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}.assembling-", dir=output.parent))
    owner = uuid.uuid4().hex
    write_json(stage / MARKER, {"schema": 1, "tool": "ModAVD macOS local assembler", "owner": owner})
    stats = {"clonedFiles": 0, "copiedFiles": 0}
    try:
        for source, relative in ((args.emulator, "sdk/emulator"), (args.manager / "tools", "tools"),
                                 (args.manager / "ModAVD.app", "ModAVD.app"),
                                 (args.runtime_root / "sdk/platform-tools", "sdk/platform-tools"),
                                 (args.runtime_root / "sdk" / IMAGE, "sdk/" + IMAGE)):
            print(f"装配 {relative}……", flush=True)
            copy_tree(source, stage / relative, stats)
        install_maintenance(stage, stats)
        extras = [(ime, "components/modavd-ime.apk"), (ROOT / "LICENSE", "licenses/port-LICENSE")]
        extras.append((APK_LOCK_PATH, "provenance/components.lock.json"))
        if launcher:
            extras.append((launcher, "components/" + LAUNCHER["filename"]))
            for key in ("licenseFile", "embeddedLicenseFile"):
                extras.append((ROOT / "ports/macos/licenses" / LAUNCHER[key], "licenses/" + LAUNCHER[key]))
        for name in ("LICENSE", "NOTICE", "SHA256SUMS.json", ".modavd-manager-build.json"):
            if (args.manager / name).is_file():
                extras.append((args.manager / name, "provenance/manager/" + name))
        for name in ("modavd-checkouts.json", "modavd-patches.json", "modavd-gfxstream-overlays.json", "modavd-aemu-overlays.json"):
            receipt = args.aosp_source / ".repo" / name
            if receipt.is_file() and not receipt.is_symlink():
                extras.append((receipt, "provenance/core/" + name))
        extras.append((ROOT / "ports/macos/runtime/components.lock.json", "provenance/runtime/components.lock.json"))
        for name in ("platform-tools", "system-image", "android-sdk-license", "android-sdk-arm-dbt-license"):
            extras.append((args.runtime_root / "receipts" / (name + ".json"), "provenance/runtime/" + name + ".json"))
        for name in ("android-sdk-license", "android-sdk-arm-dbt-license"):
            extras.append((ROOT / "ports/macos/runtime/licenses" / (name + ".txt"), "licenses/" + name + ".txt"))
        for source, relative in extras:
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            copy_file(source, destination, stats)
        (stage / "README.zh-CN.txt").write_text(
            "ModAVD macOS ARM64 本地开发装配\n\n"
            "双击 ModAVD.app，或在终端执行 tools/modavd --root <本目录绝对路径> check --json。\n"
            "保留整个目录结构。应用向上发现 sdk/；实例与日志会写入本目录。\n"
            "组件检查与补全：tools/modavd-maintenance components status；tools/fetch-components。\n"
            "问题包：tools/collect-info --output <新ZIP路径>；脱敏报告：tools/report-config。\n"
            "维护命令需要系统 Python 3.11+；条款、数据范围及可选截图见 tools/maintenance/README.md。\n"
            "本地更新：tools/modavd --root <本目录绝对路径> update prepare --package <ZIP> --sha256 <SHA256>；\n"
            "准备成功后执行同根 update install；中断后执行同根 update recover。更新不替换实例和 Google SDK 组件。\n"
            "核心启动、HVF、窗口、IME 和明日方舟兼容性以真实验收记录为准，装配成功不代表已通过。\n"
            "本轮游戏只验收B服明日方舟，登录后实战暂缓；MAA截图增强消费端尚未确认。未包含游戏安装包。\n"
            "轻量桌面 µLauncher 的来源、版本与是否随包提供见 PACKAGE-MANIFEST.json；许可全文见 licenses/。\n"
            "这是本地 ad-hoc 开发产物，未完成发行签名、公证或在线更新验收。\n"
            "版本、补丁与 SHA-256 见 PACKAGE-MANIFEST.json；许可和构建收据见 licenses/ 与 provenance/。\n"
        )
        write_json(stage / "modavd-manifest.json", {"schema": 1, "version": "0.36.0", "build": 36,
                                                    "platform": "mac-arm64", "channel": "local-development"})
        manifest = {"schema": 1, "owner": owner, "createdAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "platform": "mac-arm64", "channel": "local-development", "version": "0.36.0",
                    "emulatorProperties": props, "manager": manager_receipt, "ime": IME,
                    "launcher": LAUNCHER if launcher else {"included": False, "optional": True},
                    "maintenance": {"included": True, "python": "3.11+",
                                    "commands": ["modavd-maintenance", "fetch-components", "collect-info", "report-config"]},
                    "sdkComponents": {n: {"revision": c["revision"], "archiveSha1": c["sha1"],
                                            "archiveSha256": receipts[n]["archiveSha256"]}
                                      for n, c in lock["components"].items() if n in receipts},
                    "patches": patch_manifest(),
                    "sourceSnapshots": {"modavd": git_state(ROOT), "qemu": git_state(args.aosp_source / "external/qemu"),
                                        "gfxstream": git_state(args.aosp_source / "hardware/google/gfxstream"),
                                        "aemu": git_state(args.aosp_source / "hardware/google/aemu"),
                                        "manager": git_state(args.manager_source)},
                    "sourceCorrelation": "Current modavd/qemu/gfxstream/aemu/manager source state recorded; correspondence with compiled objects is not independently verified.",
                    "validation": {name: "not_run_by_assembler" for name in
                                   ("HVF_boot", "Qt_window_show_hide", "SHM_real_frame", "IME_guest_roundtrip", "Launcher_guest_install_and_home", "Arknights", "Developer_ID_notarization", "online_update")},
                    "copyStatistics": stats, "files": inventory(stage)}
        write_json(stage / MANIFEST, manifest)
        verify(stage)
        if os.path.lexists(output):
            fail("装配期间输出路径被其他进程创建；拒绝覆盖。")
        # This is a new local directory, never an update of a user's installation.
        rename_exclusive(stage, output)
        print(json.dumps({"output": str(output), "manifest": str(output / MANIFEST), **stats,
                          "runtimeValidation": "pending"}, indent=2, ensure_ascii=False))
    finally:
        if stage.exists() and json.loads((stage / MARKER).read_text()).get("owner") == owner:
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("assemble", "verify", "fetch-ime", "fetch-launcher"))
    parser.add_argument("--emulator", type=Path, default=WORK / "aosp/objs/distribution/emulator")
    parser.add_argument("--manager", type=Path, default=WORK / "manager-dist")
    parser.add_argument("--runtime-root", type=Path, default=WORK / "runtime")
    parser.add_argument("--output", type=Path, default=WORK / "package/ModAVD")
    parser.add_argument("--ime-apk", type=Path, default=WORK / "package-cache/modavd-ime-v1.0.0.apk")
    parser.add_argument("--launcher-apk", type=Path, default=WORK / "package-cache/de.jrpie.android.launcher_57.apk")
    parser.add_argument("--without-launcher", action="store_true", help="省略可选轻量桌面；默认装配固定 µLauncher 0.2.12")
    parser.add_argument("--aosp-source", type=Path, default=WORK / "aosp")
    parser.add_argument("--manager-source", type=Path, default=WORK / "manager")
    args = parser.parse_args()
    for key, value in vars(args).items():
        if isinstance(value, Path):
            path = Path(os.path.abspath(value.expanduser()))
            # Resolve parent aliases for overlap checks while retaining a final
            # symlink so output/IME ownership checks can explicitly reject it.
            setattr(args, key, path.parent.resolve() / path.name if key in ("output", "ime_apk", "launcher_apk") else path.resolve())
    if args.command == "assemble":
        assemble(args)
    elif args.command == "fetch-ime":
        print(fetch_ime(args.ime_apk))
    elif args.command == "fetch-launcher":
        print(fetch_launcher(args.launcher_apk))
    else:
        manifest = verify(args.output)
        print(json.dumps({"ok": True, "version": manifest["version"], "payloadEntries": len(manifest["files"]),
                          "runtimeValidation": "pending"}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print(f"失败：{error}", file=sys.stderr)
        sys.exit(1)
