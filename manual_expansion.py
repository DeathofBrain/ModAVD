"""Optional preserved-data expansion using only official SDK and guest tools.

Import and --help have no emulator side effects. Execution requires an exclusive
idle task slot, creates a fresh APFS clone, and never writes the source backup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import time
import traceback

from avdlib import GRPC_PORT, Runtime, save_json
from basic_tests import _df, _pull_status
from expansion_retests import (MARKER, EXPECTED_MARKER_BYTES, boot_clone,
                               disk_headers, hardware_capacity, idle_slot,
                               source_fingerprint)


GIB = 1024**3
TARGET_BYTES = 32 * GIB
EXPECTED_SHA256 = "861a2e19cddb688fb25f2e4e4643f6a625903da083bf99d0030ad550da83257a"


class Blocked(RuntimeError):
    """A prerequisite is missing or a safe operation cannot be established."""


def record(result):
    return {"args": result.args, "returncode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
            "duration_s": result.duration, "log": result.log}


def persist(rt, report):
    save_json(rt.results / "manual_expansion.json", report)


def inspect_chain(rt, destination, label, expected_bytes=None):
    """Prove exactly the intended two clone-owned qcow layers before writing."""
    destination = Path(destination)
    root = destination.resolve(strict=True)
    if destination != root or destination.is_symlink():
        raise Blocked("clone path contains a symlink or is not canonical")
    top = root / "userdata-qemu.img.qcow2"
    base = root / "userdata-qemu.img"
    for image in (top, base):
        if image.is_symlink() or image.resolve(strict=True) != image:
            raise Blocked(f"userdata image contains a symlink: {image}")
        with image.open("rb") as f:
            if f.read(4) != b"QFI\xfb":
                raise Blocked(f"userdata image is not qcow2: {image}")
    result = rt.run([rt.sdk / "emulator/qemu-img", "info", "--output=json",
                     "--backing-chain", top], label=label, check=False)
    if result.returncode:
        raise Blocked(f"official qemu-img cannot inspect backing chain; log={result.log}")
    try:
        chain = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise Blocked(f"invalid qemu-img chain JSON; log={result.log}") from exc
    if not isinstance(chain, list) or len(chain) != 2:
        raise Blocked(f"expected exactly two userdata qcow layers; log={result.log}")
    for entry, expected in zip(chain, (top, base)):
        filename = Path(entry.get("filename", ""))
        if not filename.is_absolute() or filename.resolve(strict=True) != expected:
            raise Blocked(f"unknown or external userdata backing chain path: {filename}")
        if entry.get("format") != "qcow2":
            raise Blocked("userdata backing chain contains a non-qcow2 image")
        if entry.get("dirty-flag") or entry.get("snapshots"):
            raise Blocked("userdata image is dirty or contains internal snapshots")
        if entry.get("format-specific", {}).get("data", {}).get("corrupt"):
            raise Blocked("qemu-img reports userdata image corruption")
        if expected_bytes is not None and entry.get("virtual-size") != expected_bytes:
            raise Blocked(f"userdata virtual capacity differs from expected {expected_bytes}")
    backing = chain[0].get("backing-filename")
    if not backing:
        raise Blocked("userdata overlay has no declared backing image")
    resolved = (top.parent / backing).resolve(strict=True)
    if resolved != base:
        raise Blocked("userdata overlay backing does not resolve to clone base")
    full_backing = chain[0].get("full-backing-filename")
    if not full_backing or Path(full_backing).resolve(strict=True) != base:
        raise Blocked("qemu-img full backing filename does not prove clone ownership")
    if chain[1].get("backing-filename") or chain[1].get("full-backing-filename"):
        raise Blocked("userdata base unexpectedly has an additional backing layer")
    return {"command": record(result), "chain": chain,
            "resize_order": [str(base), str(top)]}


def topology(rt, label):
    """Identify the real ext4 /data mount before considering resize2fs."""
    result = rt.shell("cat /proc/mounts", label=label + "-mounts")
    mounts = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[1] == "/data":
            mounts.append({"device": fields[0], "mount": fields[1],
                           "filesystem": fields[2], "options": fields[3]})
    row = {"mounts_command": record(result), "data_mounts": mounts}
    if len(mounts) != 1 or mounts[0]["filesystem"] != "ext4":
        raise Blocked(f"cannot prove one ext4 /data mount; log={result.log}")
    device = mounts[0]["device"]
    if not re.fullmatch(r"/dev/block/[A-Za-z0-9_./-]+", device) or ".." in device.split("/"):
        raise Blocked(f"unrecognized mounted data device: {device!r}")
    resolved = rt.shell("readlink -f " + shlex.quote(device), label=label + "-device-realpath")
    canonical = resolved.stdout.strip()
    if not re.fullmatch(r"/dev/block/[A-Za-z0-9_-]+", canonical):
        raise Blocked(f"unproven canonical mounted device; log={resolved.log}")
    size = rt.shell("blockdev --getsize64 " + shlex.quote(canonical),
                    label=label + "-mounted-block-size", check=False)
    row.update({"device_realpath_command": record(resolved), "mounted_device": canonical,
                "mounted_block_size_command": record(size)})
    if size.returncode or not size.stdout.strip().isdigit():
        raise Blocked(f"cannot obtain actual mounted block device capacity; log={size.log}")
    row["mounted_block_bytes"] = int(size.stdout.strip())
    # Diagnostic only: never alter a device-mapper table or a backing device.
    physical = rt.shell(
        "for p in /dev/block/by-name/userdata /dev/block/platform/*/by-name/userdata; do "
        "if [ -e \"$p\" ]; then readlink -f \"$p\"; blockdev --getsize64 \"$p\"; fi; done",
        label=label + "-underlying-userdata", check=False)
    row["underlying_userdata_command"] = record(physical)
    slaves = rt.shell("ls -l /sys/class/block/" + shlex.quote(Path(canonical).name) + "/slaves",
                      label=label + "-dm-slaves", check=False)
    row["slaves_command"] = record(slaves)
    return row


def guest_failure_diagnostics(rt, mounted_device, label):
    """Read-only kernel/filesystem evidence after an unsuccessful resize."""
    evidence = {}
    try:
        evidence["dmesg_last_120"] = record(rt.shell("dmesg | tail -n 120", label=label + "-dmesg", check=False))
    except Exception as exc:
        evidence["dmesg_error"] = str(exc)
    try:
        tool = rt.shell("command -v tune2fs", label=label + "-tune2fs-probe", check=False)
        evidence["tune2fs_probe"] = record(tool)
        path = tool.stdout.strip()
        if tool.returncode == 0 and re.fullmatch(r"/(?:system|vendor|product|apex)/[A-Za-z0-9_./-]+", path) and ".." not in path.split("/"):
            evidence["tune2fs_list"] = record(rt.shell(
                shlex.quote(path) + " -l " + shlex.quote(mounted_device),
                label=label + "-tune2fs-list", check=False))
    except Exception as exc:
        evidence["tune2fs_error"] = str(exc)
    return evidence


def run_manual(rt, source="official_expansion_backup", clone="official_expand_manual32"):
    report = {
        "test": "official_qemu_img_and_guest_resize2fs_preserved_data_expansion",
        "status": "running", "phase": "prerequisites", "source_backup": source,
        "clone": clone, "started_unix": time.time(), "target_bytes": TARGET_BYTES,
        "reserve_bytes": 20 * GIB,
        "method": "Fresh stopped APFS clone; baseline marker and df; sync shutdown; official qemu-img resize of clone base then overlay from 8 to 32 GiB; config 32G and -partition-size 32768; inspect guest ext4 mounted device, use guest-bundled resize2fs online only if that device itself grew; sync cold-boot verification. No wipe, conversion, overlay deletion, rebase, dm table changes, or host resize2fs on qcow.",
    }
    previous = rt.results / "manual_expansion.json"
    if previous.exists():
        history = rt.results / "history"
        history.mkdir(exist_ok=True)
        archive = history / f"manual_expansion-{time.time_ns()}.json"
        archive.write_bytes(previous.read_bytes())
        report["previous_report_preserved"] = str(archive)
    persist(rt, report)
    original = rt.avd_name
    source_dir = rt.avd_root / (source + ".avd")
    source_before = None
    try:
        idle_slot(rt)
        report["free_bytes_before"] = rt.space()
        if not (source_dir / "config.ini").is_file():
            raise Blocked(f"immutable source backup not found: {source_dir}")
        if not (rt.sdk / "emulator/qemu-img").is_file():
            raise Blocked("official SDK qemu-img is unavailable")
        marker = rt.results / "expansion-marker.bin"
        contents = marker.read_bytes()
        expected = {"size": len(contents), "sha256": hashlib.sha256(contents).hexdigest()}
        if expected != {"size": EXPECTED_MARKER_BYTES, "sha256": EXPECTED_SHA256}:
            raise Blocked("host reference marker differs from preserved initial trial artifact")
        report["expected_marker"] = {"host_path": str(marker), "guest_path": MARKER, **expected}
        source_before = source_fingerprint(source_dir)
        report["source_before"] = source_before
        destination = rt.avd_root / (clone + ".avd")
        if destination.exists():
            raise Blocked(f"clone already exists; preserving previous evidence: {destination}")
        report["phase"] = "clone_and_baseline"
        report["clone_path"] = str(rt.clone_stopped(source, clone))
        report["fresh_clone_chain"] = inspect_chain(rt, destination, "manual-expand-fresh-clone-chain", 8 * GIB)
        persist(rt, report)
        report["baseline_launch"] = boot_clone(rt, clone)
        report["before"] = _df(rt, "manual-expand-df-before")
        before, log = _pull_status(rt, [MARKER], "manual-expand-marker-before")
        report.update({"before_marker": before[MARKER], "before_marker_log": log,
                       "before_marker_matches_host": before[MARKER] == expected,
                       "baseline_hardware": hardware_capacity(rt)})
        report["baseline_shutdown"] = rt.stop(sync=True)
        persist(rt, report)
        if before[MARKER] != expected:
            raise Blocked("fresh clone marker differs from host; resize skipped")
        if not 7 * GIB <= report["before"]["total_bytes"] <= 9 * GIB:
            raise Blocked("fresh clone data filesystem is outside expected 8 GiB class")
        idle_slot(rt)
        report["chain_before"] = inspect_chain(rt, destination, "manual-expand-chain-before", 8 * GIB)
        report["headers_before"] = disk_headers(destination)
        report["phase"] = "resize_clone_qcow_layers"
        report["qemu_img_resize"] = []
        persist(rt, report)
        # This bundled qemu-img resize syntax uses format autodetection and does
        # not advertise a -f option. Both inputs have been proven qcow2 above.
        for path in report["chain_before"]["resize_order"]:
            idle_slot(rt)
            rt.space()
            result = rt.run([rt.sdk / "emulator/qemu-img", "resize", path, "32G"],
                            label="manual-expand-qemu-img-resize", timeout=120, check=False)
            report["qemu_img_resize"].append(record(result))
            persist(rt, report)
            if result.returncode:
                raise RuntimeError(f"official qemu-img resize failed; log={result.log}")
        report["chain_after_resize"] = inspect_chain(rt, destination, "manual-expand-chain-after", TARGET_BYTES)
        config = destination / "config.ini"
        report["config_before"] = config.read_text()
        if not re.search(r"(?m)^disk\.dataPartition\.size=.*$", report["config_before"]):
            raise Blocked("clone configuration lacks disk.dataPartition.size")
        config.write_text(re.sub(r"(?m)^disk\.dataPartition\.size=.*$", "disk.dataPartition.size=32G",
                                 report["config_before"]))
        report["config_after"] = config.read_text()
        report["phase"] = "expanded_device_boot"
        persist(rt, report)
        report["growth_launch"] = boot_clone(rt, clone, ["-partition-size", "32768"])
        report["after_image_resize"] = _df(rt, "manual-expand-df-after-image-resize")
        report["growth_hardware"] = hardware_capacity(rt)
        if report["after_image_resize"]["total_bytes"] < 30 * GIB:
            # Root applies only to this owned, disposable clone. Reconnect is
            # needed because adb root restarts adbd.
            report["adb_root"] = record(rt.adb(["root"], label="manual-expand-adb-root", check=False))
            report["adb_wait_for_root"] = record(rt.adb(["wait-for-device"], label="manual-expand-wait-root", timeout=60))
            uid = rt.shell("id -u", label="manual-expand-root-id", check=False)
            report["root_id"] = record(uid)
            if uid.returncode or uid.stdout.strip() != "0":
                raise Blocked("guest root unavailable; no filesystem resize attempted")
            report["topology_before_filesystem_resize"] = topology(rt, "manual-expand-topology")
            persist(rt, report)
            block = report["topology_before_filesystem_resize"]
            if block["mounted_block_bytes"] < 30 * GIB:
                raise Blocked("qcow capacity grew but actual mounted data device remains below 32 GiB class; mapped block device is not changed by this test")
            tool = rt.shell("command -v resize2fs", label="manual-expand-guest-resize2fs", check=False)
            report["guest_resize2fs_probe"] = record(tool)
            toolpath = tool.stdout.strip()
            if tool.returncode or not re.fullmatch(r"/(?:system|vendor|product|apex)/[A-Za-z0-9_./-]+", toolpath) or ".." in toolpath.split("/"):
                raise Blocked("no proven guest-bundled resize2fs path; filesystem resize skipped")
            report["phase"] = "guest_online_ext4_resize"
            persist(rt, report)
            resize = rt.shell(shlex.quote(toolpath) + " " + shlex.quote(block["mounted_device"]),
                              label="manual-expand-guest-resize2fs-run", timeout=180, check=False)
            report["guest_resize2fs"] = record(resize)
            report["after_guest_resize"] = _df(rt, "manual-expand-df-after-guest-resize")
            persist(rt, report)
            if resize.returncode:
                report["guest_failure_diagnostics"] = guest_failure_diagnostics(
                    rt, block["mounted_device"], "manual-expand-failure")
                persist(rt, report)
                raise RuntimeError(f"guest online resize2fs failed; log={resize.log}")
        else:
            # Always record the device evidence even if the filesystem grew
            # automatically. Root may be necessary for blockdev on this image.
            report["adb_root"] = record(rt.adb(["root"], label="manual-expand-root-observe", check=False))
            rt.adb(["wait-for-device"], label="manual-expand-wait-observe", timeout=60)
            report["topology_after_automatic_growth"] = topology(rt, "manual-expand-auto-topology")
        report["after"] = _df(rt, "manual-expand-df-final")
        after, log = _pull_status(rt, [MARKER], "manual-expand-marker-after")
        report.update({"after_marker": after[MARKER], "after_marker_log": log,
                       "marker_three_way_match": before[MARKER] == expected == after[MARKER]})
        report["growth_shutdown"] = rt.stop(sync=True)
        report["headers_after_growth"] = disk_headers(destination)
        report["phase"] = "cold_boot_verification"
        persist(rt, report)
        report["verification_launch"] = boot_clone(rt, clone, ["-partition-size", "32768"])
        report["verified_df"] = _df(rt, "manual-expand-df-cold-verify")
        verified, log = _pull_status(rt, [MARKER], "manual-expand-marker-cold-verify")
        report.update({"verified_marker": verified[MARKER], "verified_marker_log": log,
                       "cold_marker_matches_host": verified[MARKER] == expected})
        report["verification_shutdown"] = rt.stop(sync=True)
        capacity_ok = report["after"]["total_bytes"] >= 30 * GIB and report["verified_df"]["total_bytes"] >= 30 * GIB
        integrity_ok = report["marker_three_way_match"] and report["cold_marker_matches_host"]
        report["guest_capacity_grew_to_32_gib_class"] = capacity_ok
        report["status"] = "passed" if capacity_ok and integrity_ok else "failed"
        report["finding"] = "32 GiB class filesystem and preserved marker verified across cold boot" if report["status"] == "passed" else "guest capacity or marker integrity did not meet success criteria"
        report["phase"] = "complete"
    except Exception as exc:
        report.update({"status": "blocked" if isinstance(exc, Blocked) or "disk space" in str(exc) or "occupied" in str(exc) or "running" in str(exc) else "failed",
                       "error": str(exc), "traceback": traceback.format_exc()})
    finally:
        # Only a clone that this script launched may be shut down.
        if rt.state().get("avd") == clone and rt.owned():
            try:
                if report["status"] != "passed":
                    # Preserve evidence even when a mapped-device or missing
                    # tool prerequisite blocks filesystem growth.
                    report["cleanup_observed_df"] = _df(rt, "manual-expand-df-on-stop")
                    observed, log = _pull_status(rt, [MARKER], "manual-expand-marker-on-stop")
                    report["cleanup_observed_marker"] = observed[MARKER]
                    report["cleanup_observed_marker_log"] = log
            except Exception as exc:
                report["cleanup_observation_error"] = str(exc)
            try:
                report["cleanup_shutdown"] = rt.stop(sync=True)
            except Exception as exc:
                report["cleanup_error"] = str(exc)
                report["status"] = "failed"
        if source_before is not None:
            try:
                report["source_after"] = source_fingerprint(source_dir)
                report["source_backup_unchanged"] = source_before == report["source_after"]
                if not report["source_backup_unchanged"]:
                    report["status"] = "failed"
            except Exception as exc:
                report["source_verification_error"] = str(exc)
                report["status"] = "failed"
        report["task_owned_emulator_still_running"] = rt.owned()
        report["ports_still_occupied"] = [p for p in (rt.console_port, rt.console_port + 1, GRPC_PORT) if rt.port_used(p)]
        report["free_bytes_after"] = shutil.disk_usage(rt.root).free
        rt.avd_name = original
        report["restored_runtime_avd_name"] = original
        report["source_avd_was_started"] = False
        report["finished_unix"] = time.time()
        persist(rt, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="official_expansion_backup")
    parser.add_argument("--clone", default="official_expand_manual32")
    args = parser.parse_args()
    if args.source == args.clone or any(not re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in (args.source, args.clone)):
        parser.error("source and clone must be distinct simple AVD names")
    report = run_manual(Runtime(), args.source, args.clone)
    print(json.dumps({key: report.get(key) for key in ("test", "status", "phase", "error", "finding", "source_backup_unchanged")}, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
