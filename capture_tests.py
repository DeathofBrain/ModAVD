"""Official AVD PNG screenshot and source-verified shared-video probes."""
from __future__ import annotations

import argparse
import base64
import csv
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import struct
import threading
import time
import traceback
import urllib.request
import zlib

from avdlib import Runtime, save_json


def _save(rt, name, report):
    save_json(rt.results / f"{name}.json", report)


def _begin(rt, name, report):
    previous = rt.results / f"{name}.json"
    if previous.exists():
        history = rt.results / "history"
        history.mkdir(exist_ok=True)
        archive = history / f"{name}-{time.time_ns()}.json"
        archive.write_bytes(previous.read_bytes())
        report["previous_report_preserved"] = str(archive)
    _save(rt, name, report)


def _percentile(values, q):
    values = sorted(values)
    if not values:
        return None
    pos = (len(values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def validate_png(blob):
    """Validate signature, every chunk CRC, complete IEND, and actual inflation."""
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("missing PNG signature")
    pos = 8
    width = height = bitdepth = colortype = None
    idat = bytearray()
    ended = False
    while pos + 12 <= len(blob):
        size = struct.unpack_from(">I", blob, pos)[0]
        kind = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + size]
        if pos + 12 + size > len(blob):
            raise ValueError("truncated PNG chunk")
        crc = struct.unpack_from(">I", blob, pos + 8 + size)[0]
        if zlib.crc32(kind + data) & 0xFFFFFFFF != crc:
            raise ValueError(f"PNG CRC mismatch for {kind!r}")
        if kind == b"IHDR":
            width, height, bitdepth, colortype, compression, filtering, interlace = struct.unpack(">IIBBBBB", data)
            if not width or not height or width > 16384 or height > 16384:
                raise ValueError("invalid PNG dimensions")
            if compression or filtering or interlace:
                raise ValueError("unsupported screenshot PNG encoding")
        elif kind == b"IDAT":
            idat.extend(data)
        elif kind == b"IEND":
            ended = True
            break
        pos += size + 12
    if not ended or not idat or width is None:
        raise ValueError("incomplete PNG")
    decoded = zlib.decompress(idat)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colortype)
    if channels is None:
        raise ValueError("invalid PNG color type")
    expected = height * (1 + (width * channels * bitdepth + 7) // 8)
    if len(decoded) != expected:
        raise ValueError(f"PNG inflated length {len(decoded)} != {expected}")
    return {"width": width, "height": height, "bitdepth": bitdepth,
            "color_type": colortype, "inflated_bytes": len(decoded),
            "pixel_sha256": hashlib.sha256(decoded).hexdigest()}


def validate_raw(blob):
    """Check official screencap dimensions/format against 12/16-byte layouts.

    Recent Android versions append a uint32 color-space field.  Do not guess
    which layout is present: the exact pixel payload size must match.
    """
    if len(blob) < 12:
        raise ValueError("raw screencap shorter than minimum header")
    width, height, pixel_format = struct.unpack_from("<III", blob)
    if not (0 < width <= 16384 and 0 < height <= 16384):
        raise ValueError(f"invalid raw dimensions: {width}x{height}")
    bytes_per_pixel = {1: 4, 2: 4, 3: 3, 4: 2, 0x16: 8, 0x2B: 4}.get(pixel_format)
    if bytes_per_pixel is None:
        raise ValueError(f"unrecognized Android pixel format: {pixel_format}")
    matching_headers = [size for size in (12, 16)
                        if len(blob) == size + width * height * bytes_per_pixel]
    if len(matching_headers) != 1:
        raise ValueError(f"raw length {len(blob)} does not uniquely match dimensions/format {(width, height, pixel_format)}")
    header_bytes = matching_headers[0]
    result = {"width": width, "height": height, "pixel_format": pixel_format,
              "bytes_per_pixel": bytes_per_pixel, "header_bytes": header_bytes,
              "pixel_sha256": hashlib.sha256(blob[header_bytes:]).hexdigest()}
    if header_bytes == 16:
        result["color_space"] = struct.unpack_from("<I", blob, 12)[0]
    return result


def _ensure_boot(rt):
    if not rt.owned():
        rt.start()
    return rt.wait_boot()


def _start_dynamic(rt):
    """Use official Android Settings scrolling as a reproducible dynamic fixture."""
    rt.shell("am start -a android.settings.SETTINGS", label="dynamic-settings-start", timeout=30)
    time.sleep(1)
    stop = threading.Event()
    evidence = {"fixture": "Android Settings; alternating input swipe 600,590 to 600,180 (250ms)",
                "commands": 0, "errors": []}

    def animate():
        index = 0
        while not stop.is_set():
            command = ("input swipe 600 590 600 180 250" if index % 2 == 0 else
                       "input swipe 600 180 600 590 250")
            try:
                res = rt.shell(command, label="dynamic-settings-swipe", timeout=10, check=False)
                evidence["commands"] += 1
                if res.returncode:
                    evidence["errors"].append({"returncode": res.returncode, "log": res.log})
            except Exception as exc:
                evidence["errors"].append({"error": str(exc)})
            index += 1
            stop.wait(0.10)

    thread = threading.Thread(target=animate, name="capture-dynamic-fixture", daemon=True)
    thread.start()
    return stop, thread, evidence


def screenshots(rt, count=100):
    report = {"test": "adb_exec_out_screencap_raw_and_png", "status": "running",
              "count_per_format_per_scene": count,
              "commands": {"raw": "adb exec-out screencap", "png": "adb exec-out screencap -p"},
              "started_unix": time.time(),
              "latency": "host monotonic subprocess wall time, including ADB transport; PNG validation excluded",
              "percentile_method": "linear interpolation at (N-1)*q", "scenes": {}}
    _begin(rt, "screenshots", report)
    try:
        report["boot_s"] = _ensure_boot(rt)
        for scene in ("static", "dynamic"):
            scene_report = {"status": "running", "formats": {}}
            report["scenes"][scene] = scene_report
            stop = thread = None
            if scene == "static":
                rt.shell("input keyevent HOME", label="static-home")
                time.sleep(2)
                scene_report["fixture"] = "Android Launcher at rest; system clock may update"
            else:
                stop, thread, animation = _start_dynamic(rt)
                scene_report["fixture"] = animation
            try:
                for image_format in ("raw", "png"):
                    format_report = {"status": "running", "samples": []}
                    scene_report["formats"][image_format] = format_report
                    for i in range(1, count + 1):
                        row = {"sample": i, "format": image_format}
                        try:
                            rt.space(5 * 1024**2)
                            command = ["exec-out", "screencap"] + (["-p"] if image_format == "png" else [])
                            res = rt.adb(command, label=f"screencap-{scene}-{image_format}-{i:03d}",
                                         timeout=30, check=False, binary=True)
                            row.update({"duration_ms": res.duration * 1000, "returncode": res.returncode,
                                        "bytes": len(res.stdout), "log": res.log})
                            if res.returncode:
                                raise RuntimeError(f"screencap returned {res.returncode}")
                            row.update(validate_png(res.stdout) if image_format == "png" else validate_raw(res.stdout))
                            row["valid"] = True
                            if i in (1, count // 2, count):
                                extension = "png" if image_format == "png" else "raw"
                                image = rt.results / f"screenshot-{scene}-{image_format}-{i:03d}.{extension}"
                                image.write_bytes(res.stdout)
                                row["image"] = str(image)
                        except Exception as exc:
                            row.update({"valid": False, "error": str(exc)})
                        format_report["samples"].append(row)
                        if i % 10 == 0:
                            _save(rt, "screenshots", report)
                            print(json.dumps({"suite": "screenshots", "scene": scene,
                                              "format": image_format, "completed": i,
                                              "requested": count}, ensure_ascii=False), flush=True)
                        if "disk space" in row.get("error", ""):
                            break
                    latencies = [r["duration_ms"] for r in format_report["samples"] if "duration_ms" in r]
                    valid = [r for r in format_report["samples"] if r.get("valid")]
                    unique = len({r["pixel_sha256"] for r in valid})
                    format_report.update({"valid_count": len(valid), "unique_pixel_hashes": unique,
                                          "latency_ms_p50": _percentile(latencies, 0.5),
                                          "latency_ms_p95": _percentile(latencies, 0.95),
                                          "latency_ms_min": min(latencies) if latencies else None,
                                          "latency_ms_max": max(latencies) if latencies else None,
                                          "status": "passed" if len(valid) == count else "failed"})
                    if any("disk space" in r.get("error", "") for r in format_report["samples"]):
                        format_report["status"] = "blocked"
                    if scene == "dynamic" and unique < 2:
                        format_report.update({"status": "blocked", "finding": "Dynamic fixture did not yield distinct screenshots; dynamic capture result is inconclusive."})
                    csv_path = rt.results / f"screenshots-{scene}-{image_format}.csv"
                    with csv_path.open("w", newline="") as f:
                        fields = ["sample", "format", "duration_ms", "returncode", "bytes", "valid", "width", "height", "pixel_format", "header_bytes", "pixel_sha256", "log", "error"]
                        writer = csv.DictWriter(f, fields, extrasaction="ignore")
                        writer.writeheader()
                        writer.writerows(format_report["samples"])
                    format_report["csv"] = str(csv_path)
                    _save(rt, "screenshots", report)
            finally:
                if stop:
                    stop.set()
                    thread.join(timeout=12)
            scene_report["status"] = ("passed" if all(s["status"] == "passed" for s in scene_report["formats"].values())
                                      else "blocked" if any(s["status"] == "blocked" for s in scene_report["formats"].values()) else "failed")
            _save(rt, "screenshots", report)
        report["status"] = ("passed" if all(s["status"] == "passed" for s in report["scenes"].values())
                            else "blocked" if any(s["status"] == "blocked" for s in report["scenes"].values()) else "failed")
    except Exception as exc:
        report.update({"status": "blocked" if "disk space" in str(exc) else "failed",
                       "error": str(exc), "traceback": traceback.format_exc()})
    report["finished_unix"] = time.time()
    _save(rt, "screenshots", report)
    return report


def official_protocol_evidence(rt):
    """Archive pinned official source; never infer a protocol from ModAVD."""
    out = rt.results / "share-vid-official-source"
    out.mkdir(exist_ok=True)
    cached = out / "manifest.json"
    if cached.exists():
        manifest = json.loads(cached.read_text())
        if manifest.get("sources") and all(
            s["url"].startswith("https://android.googlesource.com/")
            and Path(s["path"]).exists()
            and hashlib.sha256(Path(s["path"]).read_bytes()).hexdigest() == s["sha256"]
            for s in manifest["sources"]
        ):
            return manifest
    repositories = {
        "qemu": ("platform/external/qemu", "refs/heads/emu-master-dev", [
            "android/emu/cmdline/include/android/cmdline-options.h",
            "android/android-ui/modules/aemu-recording/src/android/recording/screen-recorder.cpp",
            "android/android-ui/modules/aemu-recording/src/android/recording/video/VideoFrameSharer.h",
            "android/android-ui/modules/aemu-recording/src/android/recording/video/VideoFrameSharer.cpp",
            "android/android-webrtc/videobridge/emulator/webrtc/capture/VideoShareCapturer.h",
            "android/android-webrtc/videobridge/emulator/webrtc/capture/VideoShareCapturer.cpp",
        ]),
        "aemu": ("platform/hardware/google/aemu", "refs/heads/main", [
            "base/SharedMemory_posix.cpp", "base/include/aemu/base/memory/SharedMemory.h",
        ]),
    }
    manifest = {"retrieved_unix": time.time(), "sources": [],
                "limitation": "Pinned public official source establishes a protocol reference; it is not asserted to be the exact source revision of the downloaded emulator binary. Runtime header and frame updates are validated separately."}
    for short, (repository, ref, paths) in repositories.items():
        root_url = f"https://android.googlesource.com/{repository}/+/{ref}?format=JSON"
        commit = json.loads(urllib.request.urlopen(root_url, timeout=30).read().decode()[5:])["commit"]
        for path in paths:
            url = f"https://android.googlesource.com/{repository}/+/{commit}/{path}?format=TEXT"
            with urllib.request.urlopen(url, timeout=30) as response:
                data = base64.b64decode(response.read())
                status = response.status
            dest = out / (short + "-" + Path(path).name)
            dest.write_bytes(data)
            manifest["sources"].append({"url": url, "commit": commit, "path": str(dest),
                                        "http_status": status, "sha256": hashlib.sha256(data).hexdigest()})
    manifest["layout"] = {"handle": "videmulator<console_port>", "header": "uint32 width,height,fps,frameNumber; uint64 tsUs",
                          "header_bytes": 24, "pixel_bytes": "width*height*4", "pixel_format": "ARGB8888 (official source description)",
                          "mapping": "POSIX shm_open(handle, O_RDONLY), mmap(PROT_READ, MAP_SHARED)",
                          "concurrency": "Producer overwrites pixels before publishing frameNumber; source documents possible tearing. Header stability checks do not guarantee tear-free pixels."}
    save_json(out / "manifest.json", manifest)
    return manifest


def shared_video(rt, seconds=60):
    report = {"test": "official_share_vid", "status": "running", "requested_seconds": seconds,
              "started_unix": time.time()}
    _begin(rt, "shared_video", report)
    mapped = None
    fd = None
    stop = thread = None
    launched_probe = False
    try:
        help_result = rt.run([rt.emulator, "-help"], label="emulator-help-share-vid", timeout=30, check=False)
        detail = rt.run([rt.emulator, "-help-share-vid"], label="emulator-help-share-vid-detail", timeout=30, check=False)
        report["help"] = {"log": help_result.log, "detail_log": detail.log,
                          "listed": "share-vid" in help_result.stdout + help_result.stderr}
        try:
            report["official_protocol"] = official_protocol_evidence(rt)
        except Exception as exc:
            report.update({"status": "blocked", "error": f"cannot verify official SHM protocol source: {exc}"})
            return report
        rt.stop(sync=True)
        try:
            rt.start(extra_args=["-share-vid"])
            launched_probe = True
            report["boot_s"] = rt.wait_boot()
        except Exception as exc:
            state = rt.state()
            launch_log = Path(state.get("log", "/nonexistent"))
            output = launch_log.read_text(errors="replace") if launch_log.exists() else ""
            unsupported = any(x in output.lower() for x in ("unknown option", "unknown parameter", "unrecognized option")) and "share" in output.lower()
            report.update({"status": "failed",
                           "capability": "parameter_rejected" if unsupported else "startup_failed",
                           "error": str(exc), "launch_log": str(launch_log)})
            return report
        report["launch_log"] = rt.state().get("log")
        name = f"videmulator{rt.console_port}"
        report["handle"] = name
        libc = ctypes.CDLL(None, use_errno=True)
        libc.shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_uint]
        libc.shm_open.restype = ctypes.c_int
        fd = libc.shm_open(name.encode(), os.O_RDONLY, 0)
        if fd < 0:
            error = ctypes.get_errno()
            report.update({"status": "failed", "capability": "accepted_but_no_shared_region",
                           "errno": error, "error": os.strerror(error)})
            return report
        size = os.fstat(fd).st_size
        report["region_bytes"] = size
        if size < 24:
            raise ValueError(f"shared region shorter than official header: {size}")
        mapped = mmap.mmap(fd, size, access=mmap.ACCESS_READ)
        header = struct.unpack_from("<IIIIQ", mapped, 0)
        width, height, target_fps, frame, ts = header
        expected_bytes = 24 + width * height * 4
        report["initial_header"] = {"width": width, "height": height, "target_fps": target_fps,
                                    "frame_number": frame, "timestamp_us": ts}
        if not (0 < width <= 16384 and 0 < height <= 16384 and 0 < target_fps <= 1000 and expected_bytes <= size):
            raise ValueError(f"runtime header does not match official reference: {header}, region={size}")
        stop, thread, animation = _start_dynamic(rt)
        report["fixture"] = animation
        csv_path = rt.results / "shared-video-frames.csv"
        start = time.monotonic()
        start_frame = struct.unpack_from("<I", mapped, 12)[0]
        previous_frame = start_frame
        updates = counter_advance = backward_events = 0
        samples = []
        next_pixels = 0
        payload_hashes = set()
        with csv_path.open("w", newline="") as f:
            fields = ["elapsed_s", "width", "height", "target_fps", "frame_number", "timestamp_us", "delta", "pixel_sha256", "stable_header"]
            writer = csv.DictWriter(f, fields)
            writer.writeheader()
            while time.monotonic() - start < seconds:
                now = time.monotonic() - start
                w, h, fps, number, timestamp = struct.unpack_from("<IIIIQ", mapped, 0)
                delta = (number - previous_frame) & 0xFFFFFFFF
                if delta > 1000000:
                    backward_events += 1
                    delta = 0
                if number != previous_frame:
                    updates += 1
                    counter_advance += delta
                row = {"elapsed_s": now, "width": w, "height": h, "target_fps": fps,
                       "frame_number": number, "timestamp_us": timestamp, "delta": delta}
                if now >= next_pixels:
                    pixels = mapped[24:expected_bytes]
                    after_frame = struct.unpack_from("<I", mapped, 12)[0]
                    row["stable_header"] = after_frame == number
                    row["pixel_sha256"] = hashlib.sha256(pixels).hexdigest()
                    payload_hashes.add(row["pixel_sha256"])
                    if not samples:
                        raw_path = rt.results / "shared-video-first-frame-argb8888.bin"
                        raw_path.write_bytes(pixels)
                        report["raw_frame"] = str(raw_path)
                    samples.append(row.copy())
                    next_pixels += 1
                    f.flush()
                writer.writerow(row)
                previous_frame = number
                time.sleep(1 / 120)
        elapsed = time.monotonic() - start
        report.update({"observed_seconds": elapsed, "start_frame_number": start_frame,
                       "end_frame_number": previous_frame, "observed_counter_advance": counter_advance,
                       "observed_header_changes": updates, "counter_backwards_events": backward_events,
                       "measured_produced_frames_per_second": counter_advance / elapsed,
                       "measured_distinct_observations_per_second": updates / elapsed,
                       "unique_pixel_hashes_at_1hz": len(payload_hashes), "one_hz_samples": samples,
                       "csv": str(csv_path)})
        effective = counter_advance > 0 and len(payload_hashes) >= 2 and backward_events == 0
        report.update({"status": "passed" if effective else "failed",
                       "capability": "valid_changing_frames" if effective else "accepted_but_no_valid_changing_frames"})
    except Exception as exc:
        report.update({"status": "blocked" if "disk space" in str(exc) else "failed",
                       "error": str(exc), "traceback": traceback.format_exc()})
    finally:
        if stop:
            stop.set()
            thread.join(timeout=12)
        if mapped is not None:
            mapped.close()
        if fd is not None and fd >= 0:
            os.close(fd)
        if launched_probe:
            try:
                report["stop_after_probe"] = rt.stop(sync=True)
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        report["finished_unix"] = time.time()
        _save(rt, "shared_video", report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("suite", choices=("screenshots", "share-vid", "all"))
    p.add_argument("--avd", default="official_clean")
    p.add_argument("--count", type=int, default=100)
    p.add_argument("--seconds", type=float, default=60)
    args = p.parse_args()
    if args.count < 1 or args.seconds <= 0:
        p.error("count and seconds must be positive")
    rt = Runtime(args.avd)
    if args.suite in ("screenshots", "all"):
        print(json.dumps(screenshots(rt, args.count), ensure_ascii=False), flush=True)
    if args.suite in ("share-vid", "all"):
        print(json.dumps(shared_video(rt, args.seconds), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
