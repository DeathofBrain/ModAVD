"""Pinned Google component repair for an existing ModAVD macOS package.

SPDX-License-Identifier: AGPL-3.0-or-later
Only the SDK component directories, downloads and receipts are writable here.
"""
from __future__ import annotations

import contextlib
import ctypes
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import urllib.parse
import urllib.request
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
MIRRORS = (
    ("googledownloads_cn", "https://googledownloads.cn/android/repository/", "Google 中国下载"),
    ("tencent", "https://mirrors.cloud.tencent.com/AndroidSDK/", "腾讯云镜像（第三方镜像）"),
    ("google", "https://dl.google.com/android/repository/", "Google 官方"),
)
RESERVE = 20 * 1024**3


def digest(path, algorithm="sha256"):
    value = hashlib.new(algorithm)
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def relative_path(name):
    path = PurePosixPath(name)
    if not name or path.as_posix() != name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name or name == ".":
        raise RuntimeError("不安全的相对路径")
    return Path(*path.parts)


def safe_path(root, name):
    path = Path(root)
    for part in relative_path(name).parts:
        path /= part
        if path.is_symlink():
            raise RuntimeError("目标路径含符号链接，拒绝读写")
    return path


def read_json(path, limit=16 * 1024**2):
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size > limit:
        raise RuntimeError("JSON 文件类型或大小不安全")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="utf-8") as output:
            os.chmod(temporary, 0o600)
            json.dump(value, output, indent=2, ensure_ascii=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def available_space(root, required=0):
    if shutil.disk_usage(root).free < RESERVE + required:
        raise RuntimeError("空间不足：完成操作后须保留 20 GiB；下载断点已保留")


def process_paths():
    """Executable paths only; never read process environments or account data."""
    if sys.platform != "darwin":
        raise RuntimeError("组件修复的运行进程保护仅支持 macOS")
    library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    library.proc_listallpids.argtypes = [ctypes.c_void_p, ctypes.c_int]
    library.proc_listallpids.restype = ctypes.c_int
    library.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    library.proc_pidpath.restype = ctypes.c_int
    count = library.proc_listallpids(None, 0)
    if count <= 0:
        raise RuntimeError("无法确认包内进程是否停止")
    pids = (ctypes.c_int * (count + 1024))()
    count = library.proc_listallpids(pids, ctypes.sizeof(pids))
    if count <= 0 or count >= len(pids):
        raise RuntimeError("进程列表读取失败或已溢出")
    result = []
    for pid in pids[:count]:
        buffer = ctypes.create_string_buffer(4096)
        if pid > 1 and library.proc_pidpath(pid, buffer, len(buffer)) > 0:
            result.append(Path(os.fsdecode(buffer.value)).resolve())
    return result


def require_stopped(root):
    sdk = root / "sdk"
    if any(path == (sdk / "platform-tools/adb").resolve() or path.is_relative_to((sdk / "emulator").resolve()) for path in process_paths()):
        raise RuntimeError("包内模拟器或私有 adb 仍在运行；请自行关闭后再修复，本工具不会停止进程")


def atomic_publish(stage, destination):
    """Publish a whole directory, with an atomic exchange for a repair."""
    if not destination.exists():
        os.rename(stage, destination)
        return
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        exchange = library.renamex_np
        exchange.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = exchange(os.fsencode(stage), os.fsencode(destination), 2)  # RENAME_SWAP
    elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
        exchange = library.renameat2
        exchange.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        result = exchange(-100, os.fsencode(stage), -100, os.fsencode(destination), 2)
    else:
        raise RuntimeError("宿主不支持目录原子交换；保留原组件")
    if result:
        raise OSError(ctypes.get_errno(), "目录原子交换失败；保留原组件")


class Components:
    def __init__(self, root, resources=HERE):
        self.root = Path(root).expanduser().resolve(strict=True)
        self.resources = Path(resources)
        self.lock = read_json(self.resources / "components.lock.json")
        if not safe_path(self.root, ".modavd-macos-package.json").is_file():
            raise RuntimeError("请选择已装配的 ModAVD macOS 包根目录")
        for config in self.lock["components"].values():
            relative_path(config["destination"])
            if not config["destination"].startswith(("platform-tools", "system-images/")):
                raise RuntimeError("组件目标不在维护范围内")
            if not re.fullmatch(r"[a-f0-9]{40}", config["sha1"]) or not re.fullmatch(r"[a-f0-9]{64}", config["sha256"]):
                raise RuntimeError("锁文件缺少固定归档哈希")

    def license(self, identifier):
        configs = [c for c in self.lock["components"].values() if c["license"] == identifier]
        if not configs:
            raise RuntimeError("未知许可 ID")
        path = safe_path(self.resources, "licenses/" + identifier + ".txt")
        raw = path.read_bytes()
        expected = configs[0]["licenseTextSha256"]
        if hashlib.sha256(raw).hexdigest() != expected:
            raise RuntimeError("随包许可全文与锁定哈希不符")
        return raw.decode("utf-8"), expected

    def receipt(self, name):
        for directory in (".modavd-maintenance/receipts", "receipts", "provenance/runtime"):
            path = safe_path(self.root, directory + "/" + name + ".json")
            if path.is_file():
                try:
                    yield read_json(path)
                except (ValueError, OSError, RuntimeError):
                    continue

    def accepted(self, identifier):
        _, expected = self.license(identifier)
        return any(r.get("id") == identifier and r.get("sha256") == expected and isinstance(r.get("acceptedAt"), str) and bool(r["acceptedAt"]) for r in self.receipt(identifier))

    def accept(self, identifier, confirmation, explicitly_accepted):
        _, expected = self.license(identifier)
        if not explicitly_accepted or confirmation != expected:
            raise RuntimeError("须先阅读全文，再明确传入 --i-have-read-and-accept 与 --confirm 完整许可 SHA-256")
        write_json(safe_path(self.root, ".modavd-maintenance/receipts/" + identifier + ".json"), {
            "id": identifier, "sha256": expected, "acceptedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "method": "explicit-maintenance-cli",
        })

    def status(self, name):
        config = self.lock["components"][name]
        destination = safe_path(self.root, "sdk/" + config["destination"])
        result = {"component": name, "revision": config["revision"], "license": config["license"],
                  "licenseAccepted": self.accepted(config["license"]), "state": "missing"}
        if not destination.exists():
            return result
        for receipt in self.receipt(name):
            if receipt.get("archiveSha1") != config["sha1"] or receipt.get("archiveSha256") != config["sha256"] or receipt.get("revision") != config["revision"] or not isinstance(receipt.get("files"), dict) or not receipt["files"]:
                continue
            try:
                for filename, expected in receipt["files"].items():
                    path = safe_path(destination, filename)
                    if not re.fullmatch(r"[a-f0-9]{64}", expected) or not path.is_file() or digest(path) != expected:
                        raise RuntimeError("文件哈希不符")
                actual = set()
                for parent, directories, files in os.walk(destination, followlinks=False):
                    for entry in directories + files:
                        path = Path(parent) / entry
                        if path.is_symlink() or not (path.is_file() or path.is_dir()):
                            raise RuntimeError("组件内有链接或特殊文件")
                    actual.update((Path(parent) / f).relative_to(destination).as_posix() for f in files)
                if actual != set(receipt["files"]):
                    raise RuntimeError("组件文件集合不符")
                self.package_modes(name, destination, receipt["files"])
            except (OSError, ValueError, TypeError, RuntimeError):
                result["state"] = "damaged"
                continue
            result["state"] = "verified"
            return result
        if result["state"] != "damaged":
            result["state"] = "unverified"
        return result

    def package_modes(self, name, tree, files, restore=False):
        """Compare only this fixed SDK subtree; never re-inventory/retrust the package."""
        manifest_path = safe_path(self.root, "PACKAGE-MANIFEST.json")
        if not manifest_path.exists():
            if restore:
                tree.chmod(0o755)
                for path in tree.rglob("*"):
                    if path.is_dir():
                        path.chmod(0o755)
            elif name == "platform-tools" and not os.access(tree / "adb", os.X_OK):
                raise RuntimeError("adb 执行权限缺失")
            return
        manifest = read_json(manifest_path)
        prefix = "sdk/" + self.lock["components"][name]["destination"]
        expected = {key: value for key, value in manifest.get("files", {}).items() if key == prefix or key.startswith(prefix + "/")}
        actual = {prefix: tree}
        actual.update((prefix + "/" + p.relative_to(tree).as_posix(), p) for p in tree.rglob("*"))
        if set(actual) != set(expected):
            raise RuntimeError("原包SDK清单与固定归档不同；请重新装配相符的包，拒绝重写全包清单")
        for key, path in actual.items():
            record = expected[key]
            mode = record.get("mode")
            if type(mode) is not int or not 0 <= mode <= 0o777:
                raise RuntimeError("原包SDK权限记录不安全")
            if path.is_dir():
                correct = record.get("type") == "directory"
            else:
                relative = path.relative_to(tree).as_posix()
                correct = record.get("type") == "file" and record.get("sha256") == files.get(relative) and record.get("bytes") == path.stat().st_size
            if not correct:
                raise RuntimeError("原包SDK哈希与固定归档不同；请重新装配相符的包")
            if restore:
                path.chmod(mode)
            elif stat.S_IMODE(path.stat().st_mode) != mode:
                raise RuntimeError("组件权限与原包SDK清单不同")

    def urls(self, config):
        official = urllib.parse.urlparse(config["url"])
        prefix = "/android/repository/"
        if official.scheme != "https" or official.netloc != "dl.google.com" or not official.path.startswith(prefix) or official.query or official.fragment:
            raise RuntimeError("锁文件来源不是 Google 官方固定归档")
        relative = official.path[len(prefix):]
        return [(identifier, base + relative, label) for identifier, base, label in MIRRORS]

    def download(self, name, opener=urllib.request.urlopen):
        config = self.lock["components"][name]
        directory = safe_path(self.root, ".modavd-maintenance/downloads")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        archive = safe_path(directory, name + "-" + config["sha256"] + ".zip")
        valid = lambda p: p.is_file() and p.stat().st_size == config["size"] and digest(p, "sha1") == config["sha1"] and digest(p) == config["sha256"]
        if archive.exists():
            if valid(archive):
                return archive
            raise RuntimeError("缓存归档哈希错误；请移走该归档后重试")
        failures = []
        for mirror, url, label in self.urls(config):
            partial = safe_path(directory, archive.name + "." + mirror + ".part")
            try:
                offset = partial.stat().st_size if partial.exists() else 0
                if offset > config["size"]:
                    partial.unlink()
                    offset = 0
                available_space(self.root, config["size"] - offset + config["expandedBudgetBytes"])
                if offset < config["size"]:
                    headers = {"Accept-Encoding": "identity", "User-Agent": "ModAVD-macOS-maintenance/1"}
                    if offset:
                        headers["Range"] = "bytes=%d-" % offset
                    with opener(urllib.request.Request(url, headers=headers), timeout=30) as response:
                        if urllib.parse.urlparse(response.url).scheme != "https" or urllib.parse.urlparse(response.url).hostname not in {urllib.parse.urlparse(m[1]).hostname for m in MIRRORS}:
                            raise RuntimeError("镜像重定向到未批准的域名")
                        if response.headers.get("Content-Encoding", "identity").lower() not in ("identity", ""):
                            raise RuntimeError("拒绝压缩的 HTTP 响应，以免破坏 Range 字节范围")
                        if response.status == 206:
                            expected_range = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                            if not expected_range or int(expected_range[1]) != offset or int(expected_range[3]) != config["size"] or int(expected_range[2]) != config["size"] - 1:
                                raise RuntimeError("Range 响应与锁定归档不符")
                            mode = "ab"
                        elif response.status == 200:
                            mode = "wb"  # Server ignored Range: restart this mirror safely.
                        else:
                            raise RuntimeError("HTTP 响应不可用于归档下载")
                        with partial.open(mode) as output:
                            os.chmod(partial, 0o600)
                            while True:
                                chunk = response.read(1024 * 1024)
                                if not chunk:
                                    break
                                if output.tell() + len(chunk) > config["size"]:
                                    raise RuntimeError("响应超过锁定大小")
                                available_space(self.root, len(chunk))
                                output.write(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                if not valid(partial):
                    # Complete wrong bytes must not poison a future Range resume.
                    if partial.exists() and partial.stat().st_size >= config["size"]:
                        partial.unlink()
                    raise RuntimeError("归档大小、官方 SHA-1 或锁定 SHA-256 不符")
                os.replace(partial, archive)
                return archive
            except (OSError, ValueError, RuntimeError) as error:
                failures.append({"mirror": mirror, "source": label, "error": type(error).__name__})
        raise RuntimeError("所有固定镜像均失败；断点保留。" + json.dumps(failures, ensure_ascii=False))

    def extract(self, name, archive, stage):
        config = self.lock["components"][name]
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            if len(members) > 100000 or sum(m.file_size for m in members) > config["expandedBudgetBytes"]:
                raise RuntimeError("ZIP 展开超出锁定预算")
            seen = set()
            for member in members:
                raw = member.filename[:-1] if member.is_dir() else member.filename
                relative = relative_path(raw)
                key = relative.as_posix().casefold()
                if relative.parts[0] != config["archiveRoot"] or key in seen:
                    raise RuntimeError("归档根目录错误或有重名路径")
                seen.add(key)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                    raise RuntimeError("归档包含链接或特殊文件")
                if member.flag_bits & 1:
                    raise RuntimeError("不支持加密归档")
                target = stage.joinpath(*relative.parts[1:])
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if len(relative.parts) < 2:
                    raise RuntimeError("归档根节点不是目录")
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                target.chmod((mode & 0o777) or 0o644)
        properties = dict(line.split("=", 1) for line in (stage / "source.properties").read_text().splitlines() if "=" in line and not line.startswith("#"))
        normalize = lambda s: tuple(int(p) for p in s.strip().split(".")) + (0,) * (3 - len(s.strip().split(".")))
        if normalize(properties.get("Pkg.Revision", "0")) != normalize(config["revision"]):
            raise RuntimeError("归档内部 Pkg.Revision 与锁定版本不同")
        return {p.relative_to(stage).as_posix(): digest(p) for p in stage.rglob("*") if p.is_file()}

    @contextlib.contextmanager
    def exclusive(self):
        directory = safe_path(self.root, ".modavd-maintenance")
        directory.mkdir(mode=0o700, exist_ok=True)
        lock = safe_path(directory, "maintenance.lock")
        with lock.open("a") as output:
            try:
                fcntl.flock(output, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("另一个组件维护任务正在运行")
            yield

    def repair(self, names=None, opener=urllib.request.urlopen):
        names = list(names or self.lock["components"])
        if any(n not in self.lock["components"] for n in names):
            raise RuntimeError("未知组件")
        # No writes/downloads before all selected license gates have passed.
        for name in names:
            if not self.accepted(self.lock["components"][name]["license"]):
                raise RuntimeError("尚未明确接受许可；先运行 licenses --id " + self.lock["components"][name]["license"] + " 阅读全文，再按提示 accept-license")
        with self.exclusive():
            result = []
            for name in names:
                state = self.status(name)
                if state["state"] == "verified":
                    result.append(state)
                    continue
                require_stopped(self.root)
                archive = self.download(name, opener)
                config = self.lock["components"][name]
                destination = safe_path(self.root, "sdk/" + config["destination"])
                if destination.exists() and not destination.is_dir():
                    raise RuntimeError("组件目标不是目录，拒绝交换或覆盖")
                destination.parent.mkdir(parents=True, exist_ok=True)
                available_space(self.root, config["expandedBudgetBytes"])
                stage = safe_path(self.root, ".modavd-maintenance/stage-" + name + "-" + uuid.uuid4().hex)
                stage.mkdir(mode=0o700)
                try:
                    files = self.extract(name, archive, stage)
                    self.package_modes(name, stage, files, restore=True)
                    require_stopped(self.root)  # Recheck after a potentially long download.
                    atomic_publish(stage, destination)
                    write_json(safe_path(self.root, ".modavd-maintenance/receipts/" + name + ".json"), {
                        "package": config["package"], "revision": config["revision"],
                        "archiveSha1": config["sha1"], "archiveSha256": config["sha256"], "files": files,
                        "installedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                    })
                    result.append(self.status(name))
                finally:
                    if stage.exists():
                        shutil.rmtree(stage)
            return result
