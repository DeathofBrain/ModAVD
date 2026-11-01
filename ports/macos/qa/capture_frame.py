#!/usr/bin/env python3
"""Save one task-owned POSIX SHM frame as PNG; development QA only."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import datetime as dt
import hashlib
import importlib.util
import json
import mmap
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import time
import zlib

SOURCE = Path(__file__).resolve().parents[1] / "runtime/runtime.py"
SPEC = importlib.util.spec_from_file_location("modavd_capture_runtime", SOURCE)
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)
HEADER = struct.Struct("<IIIIQ")
MAX_MAPPING_BYTES = 512 * 1024**2
MAX_FRAME_BYTES = 64 * 1024**2
LIMITATION = ("The producer exposes no lock or seqlock. Matching headers before and after "
              "the copy do not prove that all pixels belong to one untorn frame.")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def copy_frame(region, attempts: int = 50) -> tuple[dict, bytes]:
    """Copy only a bounded published payload; a stable header is not a seqlock."""
    if not HEADER.size <= len(region) <= MAX_MAPPING_BYTES:
        raise RuntimeError("invalid SHM mapping size (24 B .. 512 MiB required)")
    for attempt in range(attempts):
        before = bytes(region[:HEADER.size])
        width, height, fps, number, timestamp = HEADER.unpack(before)
        if timestamp:
            if not (1 <= width <= 8192 and 1 <= height <= 8192 and 1 <= fps <= 1000):
                raise RuntimeError("invalid frame dimensions or fps")
            count = width * height * 4
            if count > MAX_FRAME_BYTES:
                raise RuntimeError("frame exceeds the 64 MiB capture limit")
            if count > len(region) - HEADER.size:
                raise RuntimeError("truncated BGRA payload")
            pixels = bytes(region[HEADER.size:HEADER.size + count])
            if before == bytes(region[:HEADER.size]):
                return {"width": width, "height": height, "fps": fps,
                        "frameNumber": number, "timestampUs": timestamp,
                        "headerBytes": HEADER.size, "byteOrder": "little",
                        "pixelFormat": "BGRA", "payloadBytes": count,
                        "payloadSha256": sha256(pixels), "mappingBytes": len(region),
                        "copyAttempts": attempt + 1, "unchangedHeaderDuringCopy": True,
                        "headerHex": before.hex()}, pixels
        if attempt + 1 < attempts:
            time.sleep(0.02)
    raise RuntimeError("no published frame with an unchanged header; retry while the display is quieter")


def read_shm(name: str, *, expected_console_port: int | None = None) -> tuple[dict, bytes]:
    port = runtime.CONSOLE_PORT if expected_console_port is None else expected_console_port
    if type(port) is not int or not 1 <= port <= 65535 or name != f"videmulator{port}":
        raise RuntimeError("unexpected POSIX SHM name; no Windows prefix or path is accepted")
    library = ctypes.CDLL(None, use_errno=True)
    # mode is variadic on Darwin arm64, so only the first two arguments are fixed.
    library.shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int]
    library.shm_open.restype = ctypes.c_int
    fd = library.shm_open(name.encode("ascii"), os.O_RDONLY, ctypes.c_uint(0))
    if fd < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), name)
    try:
        size = os.fstat(fd).st_size
        if not HEADER.size <= size <= MAX_MAPPING_BYTES:
            raise RuntimeError("invalid SHM mapping size (24 B .. 512 MiB required)")
        with mmap.mmap(fd, size, access=mmap.ACCESS_READ) as region:
            return copy_frame(region)
    finally:
        os.close(fd)


def png_bgra(width: int, height: int, pixels: bytes) -> bytes:
    """Encode top-to-bottom BGRA as lossless RGBA PNG using only the stdlib."""
    count = width * height * 4
    if not (1 <= width <= 8192 and 1 <= height <= 8192) or count > MAX_FRAME_BYTES or len(pixels) != count:
        raise RuntimeError("invalid PNG dimensions or BGRA payload length")

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)

    compressor = zlib.compressobj()
    compressed = []
    stride = width * 4
    for offset in range(0, len(pixels), stride):
        row = pixels[offset:offset + stride]
        rgba = bytearray(stride + 1)  # PNG filter 0; preserve row order and alpha.
        rgba[1::4], rgba[2::4], rgba[3::4], rgba[4::4] = row[2::4], row[1::4], row[0::4], row[3::4]
        compressed.append(compressor.compress(rgba))
    compressed.append(compressor.flush())
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", b"".join(compressed)) + chunk(b"IEND", b""))


def read_record(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
        raise RuntimeError(f"invalid task record: {path}")
    record = json.loads(path.read_text())
    if not isinstance(record, dict):
        raise RuntimeError(f"invalid task record object: {path}")
    return record


class FrameCapture:
    def __init__(self, instance):
        self.instance = instance
        self.identity = None

    def owned(self) -> str:
        root = self.instance.root
        marker = read_record(root / "runtime.json")
        if marker.get("schemaVersion") != 1 or not isinstance(marker.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", marker["id"]):
            raise RuntimeError("runtime.json is not an owned QA runtime")
        server = read_record(root / "adb-server.json")
        emulator = read_record(root / "emulator.json")
        name = "modavd_macos_qa_" + marker["id"][:8]
        for record, tag in ((server, "server nodaemon"), (server, f"tcp:{runtime.ADB_PORT}"),
                            (server, str(root / "sdk/platform-tools/adb")), (emulator, name)):
            if not runtime.same_process(record, tag):
                raise RuntimeError("recorded private ADB/emulator PID is stale or foreign; no ADB request sent")
        identity = {"runtimeId": marker["id"], "server": server, "emulator": emulator}
        if self.identity is not None and identity != self.identity:
            raise RuntimeError("task process identity changed during capture; no further guest access")
        if not runtime.adb_server_ready():
            raise RuntimeError("owned private ADB server protocol is not ready; no ADB request sent")
        serial = self.instance.owned_device()
        if serial != f"emulator-{runtime.CONSOLE_PORT}":
            raise RuntimeError("unexpected task serial")
        self.identity = identity
        return serial

    def capture(self) -> tuple[dict, bytes]:
        serial = self.owned()
        try:
            response = self.instance.adb("-s", serial, "emu", "screenrecord", "webrtc", "start", timeout=20).stdout
            if response.split() != [f"videmulator{runtime.CONSOLE_PORT}", "OK"]:
                raise RuntimeError(f"unexpected SHM start response: {response!r}")
            frame, pixels = read_shm(f"videmulator{runtime.CONSOLE_PORT}")
        finally:
            # Recheck ownership even after a failed/partial start. Never send a
            # cleanup command to a replacement process or another ADB service.
            cleanup_serial = self.owned()
            self.instance.adb("-s", cleanup_serial, "emu", "screenrecord", "webrtc", "stop", timeout=20)
        return frame, pixels


@contextmanager
def new_output_directory(path: Path):
    """Anchor fixed output names to a newly created directory; never replace."""
    path = path.expanduser()
    if path.name in ("", ".", ".."):
        raise RuntimeError("output must name a new directory")
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent_fd = os.open(parent, flags)
    directory_fd = None
    try:
        os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
        directory_fd = os.open(path.name, flags, dir_fd=parent_fd)
        yield target, directory_fd
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(parent_fd)


def write_exclusive(directory_fd: int, name: str, data: bytes):
    if name not in ("frame.png", "evidence.json"):
        raise RuntimeError("unexpected capture output filename")
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode=0o600, dir_fd=directory_fd)
    with os.fdopen(fd, "wb") as output:
        output.write(data)


def save_capture(instance, output: Path) -> dict:
    output = output.expanduser()
    output = output.parent.resolve(strict=True) / output.name
    if output.is_relative_to(instance.sdk.resolve()):
        raise RuntimeError("capture output must not be inside the immutable SDK")
    runtime.space(output.parent, MAX_FRAME_BYTES + 1024**2)
    with new_output_directory(output) as (directory, directory_fd):
        collector = FrameCapture(instance)
        frame, pixels = collector.capture()
        png = png_bgra(frame["width"], frame["height"], pixels)
        report = {"schema": 1, "capturedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "tool": "ModAVD development SHM frame capture", "root": str(instance.root),
                  "serial": f"emulator-{runtime.CONSOLE_PORT}", "adbServerPort": runtime.ADB_PORT,
                  "processIdentity": collector.identity, "shmName": f"videmulator{runtime.CONSOLE_PORT}",
                  "frame": frame, "image": {"file": "frame.png", "format": "PNG RGBA8",
                                              "bytes": len(png), "sha256": sha256(png)},
                  "cleanup": "SHM stop command completed after ownership recheck",
                  "noSeqlock": True, "consistencyLimitation": LIMITATION,
                  "adbScreencap": "not_collected"}
        evidence = (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        runtime.space(directory, len(png) + len(evidence))
        write_exclusive(directory_fd, "frame.png", png)
        write_exclusive(directory_fd, "evidence.json", evidence)
    return {"png": str(directory / "frame.png"), "evidence": str(directory / "evidence.json"), **report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=runtime.REPO / ".macos-work/runtime")
    parser.add_argument("--output", type=Path, required=True, help="new directory; parent must exist")
    args = parser.parse_args()
    result = save_capture(runtime.Runtime(args.root), args.output)
    print(json.dumps({key: result[key] for key in ("png", "evidence", "frame", "image", "consistencyLimitation")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"capture failed: {error}", file=sys.stderr)
        sys.exit(1)
