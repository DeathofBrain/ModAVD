#!/usr/bin/env python3
"""Create a checked macOS update payload without SDK components or user state."""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import zipfile

SPEC = importlib.util.spec_from_file_location("modavd_assembler", Path(__file__).with_name("package.py"))
assembler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assembler)

TRANSPORT = "modavd-update-manifest.json"
DIRECTORIES = ("sdk/emulator", "ModAVD.app", "tools", "components", "licenses", "provenance")
ROOT_FILES = ("modavd-manifest.json", "README.zh-CN.txt", assembler.MARKER, assembler.MANIFEST)
SIGNATURE_XATTRS = frozenset(("com.apple.cs.CodeDirectory", "com.apple.cs.CodeRequirements",
                             "com.apple.cs.CodeSignature", "com.apple.cs.CodeEntitlements"))


def signature_xattrs(path):
    """ZIP lacks generic-code signature attributes; retain only these keys."""
    if sys.platform != "darwin":
        return {}
    library = ctypes.CDLL(None, use_errno=True)
    library.listxattr.argtypes = (ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
    library.listxattr.restype = ctypes.c_ssize_t
    library.getxattr.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p,
                                 ctypes.c_size_t, ctypes.c_uint32, ctypes.c_int)
    library.getxattr.restype = ctypes.c_ssize_t
    encoded = os.fsencode(path)
    count = library.listxattr(encoded, None, 0, 1)  # XATTR_NOFOLLOW
    if count < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(path))
    if count > 65536:
        raise RuntimeError("扩展属性名称列表过长：" + path.name)
    names = ctypes.create_string_buffer(max(1, count))
    actual = library.listxattr(encoded, names, count, 1)
    if actual < 0 or actual > count:
        raise RuntimeError("读取时扩展属性发生变化：" + path.name)
    attributes = {os.fsdecode(name) for name in names.raw[:actual].split(b"\x00") if name}
    unsupported = {name for name in attributes if name.startswith("com.apple.cs.")} - SIGNATURE_XATTRS
    if unsupported:
        raise RuntimeError("发现尚未支持的代码签名属性：" + ", ".join(sorted(unsupported)))
    result = {}
    for name in sorted(attributes & SIGNATURE_XATTRS):
        size = library.getxattr(encoded, os.fsencode(name), None, 0, 0, 1)
        if size < 0 or size > 1024**2:
            raise RuntimeError("代码签名属性不可读或超过1MiB：" + path.name)
        value = ctypes.create_string_buffer(max(1, size))
        read = library.getxattr(encoded, os.fsencode(name), value, size, 0, 1)
        if read != size:
            raise RuntimeError("读取时代码签名属性发生变化：" + path.name)
        result[name] = base64.b64encode(value.raw[:size]).decode("ascii")
    return result


def record(path, unit):
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if mode & ~0o777:
        raise RuntimeError("更新负载不能含特殊权限：" + path.name)
    if path.is_symlink():
        target = os.readlink(path)
        if os.path.isabs(target) or "\\" in target or "\x00" in target:
            raise RuntimeError("更新负载含不安全链接：" + path.name)
        try:
            resolved = path.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise RuntimeError("更新负载含悬空或循环链接：" + path.name) from error
        if not resolved.is_relative_to(unit):
            raise RuntimeError("更新链接不能跨越目录单元：" + path.name)
        return {"type": "symlink", "target": target,
                "sha256": hashlib.sha256(target.encode("utf-8")).hexdigest()}
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError("更新负载仅接受普通文件和内部链接：" + path.name)
    result = {"type": "file", "mode": mode, "size": info.st_size,
              "sha256": assembler.digest(path)}
    attributes = signature_xattrs(path)
    if attributes:
        result["xattrs"] = attributes
    return result


def collect(source):
    units, files = list(DIRECTORIES) + list(ROOT_FILES), {}
    for name in DIRECTORIES:
        unit = source / name
        if unit.is_symlink() or not unit.is_dir():
            raise RuntimeError("缺少更新目录单元：" + name)
        for path in assembler.entries(unit):
            if path.is_dir() and not path.is_symlink():
                continue
            files[path.relative_to(source).as_posix()] = record(path, unit)
    for name in ROOT_FILES:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("缺少更新根文件：" + name)
        files[name] = record(path, path.parent)
    return units, dict(sorted(files.items()))


def create(source, output):
    source = Path(source).expanduser().resolve(strict=True)
    output = Path(output).expanduser().absolute()
    if output.is_symlink() or output.exists():
        raise RuntimeError("更新归档已存在，拒绝覆盖")
    if output.resolve().is_relative_to(source):
        raise RuntimeError("更新归档必须放在源包目录之外")
    assembler.verify(source)
    version = json.loads((source / "modavd-manifest.json").read_text())
    if version.get("platform") != "mac-arm64" or not isinstance(version.get("version"), str) or type(version.get("build")) is not int or version["build"] <= 0:
        raise RuntimeError("源包版本/平台清单无效")
    units, files = collect(source)
    manifest = {"schema": 1, "format": "modavd-macos-update-v1", "platform": "mac-arm64",
                "version": version["version"], "build": version["build"], "units": units, "files": files}
    output.parent.mkdir(parents=True, exist_ok=True)
    assembler.space(output.parent)
    fd, name = tempfile.mkstemp(prefix=".modavd-update-", suffix=".zip", dir=output.parent)
    temporary = Path(name)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            archive.writestr(TRANSPORT, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            for relative, expected in files.items():
                path = source / relative
                before = path.lstat()
                entry = zipfile.ZipInfo(relative)
                entry.create_system = 3
                entry.compress_type = zipfile.ZIP_DEFLATED
                if expected["type"] == "symlink":
                    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                    data = os.readlink(path).encode("utf-8")
                    if hashlib.sha256(data).hexdigest() != expected["sha256"]:
                        raise RuntimeError("生成归档时链接发生变化：" + relative)
                    archive.writestr(entry, data)
                else:
                    entry.external_attr = (stat.S_IFREG | expected["mode"]) << 16
                    checksum = hashlib.sha256()
                    with path.open("rb") as incoming, archive.open(entry, "w", force_zip64=True) as outgoing:
                        while chunk := incoming.read(1024 * 1024):
                            assembler.space(output.parent, len(chunk))
                            checksum.update(chunk)
                            outgoing.write(chunk)
                    if checksum.hexdigest() != expected["sha256"]:
                        raise RuntimeError("生成归档时文件发生变化：" + relative)
                    if signature_xattrs(path) != expected.get("xattrs", {}):
                        raise RuntimeError("生成归档时代码签名属性发生变化：" + relative)
                after = path.lstat()
                if (before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns):
                    raise RuntimeError("生成归档时负载发生变化：" + relative)
        # Preserve the source's owner and package inventory as one update unit;
        # transport metadata is deliberately never installed in the target root.
        assembler.verify(source)
        checksum = assembler.digest(temporary)
        os.link(temporary, output)
        return {"output": str(output), "sha256": checksum, "bytes": output.stat().st_size,
                "files": len(files), "units": units, "version": version["version"], "build": version["build"]}
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(create(args.source, args.output), ensure_ascii=False, indent=2))
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(1, "更新归档生成失败：" + str(error) + "\n")


if __name__ == "__main__":
    main()
