#!/usr/bin/env python3
"""Observe genuine game-file writes on a disposable AVD and compare after emu kill.

Never runs on the original game AVD. Never synthesizes, overwrites or deletes a
game file. Public evidence contains sizes/digests and opaque path IDs only.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import time
import uuid

from avdlib import GRPC_PORT, Runtime, ROOT, save_json

PACKAGE = "com.hypergryph.arknights.bilibili"
SOURCE_AVD = "official_game"
THROWAWAY_PREFIX = "official_game_cache_disposable_"
APP_ROOT = "/data/user/0/" + PACKAGE
ROOTS = tuple(APP_ROOT + "/" + kind for kind in ("cache", "files", "shared_prefs"))
SENSITIVE = re.compile(
    r"auth|token|account|login|cookie|credential|password|passwd|session|sdk|bilibili|"
    r"device.?id|(?:^|[/_.-])uid(?:$|[/_.-])|user", re.IGNORECASE)


def public_file(value):
    return {key: value[key] for key in ("size", "mtime_unix_seconds", "inode", "sha256")
            if key in value}


def linux_device_number(value):
    """Decode /proc/maps major:minor using Linux's dev_t layout, even on macOS."""
    if not re.fullmatch(r"[0-9a-fA-F]+:[0-9a-fA-F]+", value):
        raise RuntimeError("Invalid opaque maps device identity")
    major, minor = (int(part, 16) for part in value.split(":"))
    if major > 0xffffffff or minor > 0xffffffff:
        raise RuntimeError("Out-of-range opaque maps device identity")
    return ((minor & 0xff) | ((major & 0xfff) << 8)
            | ((minor & ~0xff) << 12) | ((major & ~0xfff) << 32))


def parse_handle_snapshot(output):
    """Parse only process numbers and file identities; no paths are returned."""
    fds, writable_maps, completed = set(), set(), set()
    current = None
    complete = False
    for line in output.splitlines():
        fields = line.split("|")
        if fields == ["COMPLETE"] and current is None and completed and not complete:
            complete = True
            continue
        if complete:
            raise RuntimeError("Unexpected data after opaque handle snapshot")
        if len(fields) == 2 and fields[0] == "P" and fields[1].isdigit():
            if current is not None or fields[1] in completed:
                raise RuntimeError("Incomplete opaque process observation")
            current = fields[1]
        elif len(fields) == 2 and fields[0] == "E" and fields[1] == current:
            completed.add(current)
            current = None
        elif len(fields) == 4 and fields[0] in ("F", "M") and fields[1] == current:
            if not fields[3].isdigit():
                raise RuntimeError("Invalid opaque inode identity")
            if fields[0] == "F":
                if not fields[2].isdigit():
                    raise RuntimeError("Invalid opaque FD device identity")
                fds.add((int(fields[2]), int(fields[3])))
            else:
                writable_maps.add((linux_device_number(fields[2]), int(fields[3])))
        else:
            raise RuntimeError("Incomplete or invalid opaque handle observation")
    if not complete or current is not None:
        raise RuntimeError("Opaque FD/maps enumeration was not completed")
    return {"fd_identities": fds, "writable_map_identities": writable_maps,
            "same_uid_processes": len(completed)}


def parse_package_uid(listing, details):
    """Use an exact user-0 package UID, accepting both dumpsys field names."""
    matches = re.findall(r"(?m)^package:" + re.escape(PACKAGE) + r"\s+uid:(\d+)\s*$", listing)
    if len(matches) != 1:
        raise RuntimeError("Exactly one installed Bilibili user-0 package UID was not established")
    uid = int(matches[0])
    if not 10000 <= uid < 100000:
        raise RuntimeError("Installed Bilibili UID is outside the user-0 application range")
    metadata = {int(value) for value in re.findall(r"(?m)^\s+(?:userId|appId)=(\d+)\s*$", details)}
    if metadata and metadata != {uid}:
        raise RuntimeError("Package-list UID and dumpsys application identity disagree")
    return uid


class Observer:
    def __init__(self, runtime, report, max_bytes):
        self.runtime = runtime
        self.report = report
        self.max_bytes = max_bytes
        self.salt = secrets.token_bytes(32)
        self.uid = None
        self.known_paths = set()

    def identifier(self, path):
        return hmac.new(self.salt, path.encode(), hashlib.sha256).hexdigest()

    def adb(self, args, purpose, timeout=60, allowed=(0,), binary=False):
        # Unlike Runtime.run, this deliberately never persists raw stdout/stderr
        # or filename-bearing command arguments from the private game directory.
        start = time.monotonic()
        try:
            result = subprocess.run([str(self.runtime.adb_path), "-H", "127.0.0.1", "-P", "22150",
                                     "-s", "emulator-22160", *args], env=self.runtime.env,
                                    capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            # TimeoutExpired.__str__ contains argv, which can include a private
            # game filename. Keep the public exception and operation log opaque.
            self.report.setdefault("private_read_operations", []).append({
                "purpose": purpose, "duration_seconds": time.monotonic() - start,
                "timed_out": True,
            })
            raise RuntimeError(f"Private read operation {purpose} timed out; raw data withheld") from None
        self.report.setdefault("private_read_operations", []).append({
            "purpose": purpose, "duration_seconds": time.monotonic() - start,
            "returncode": result.returncode,
        })
        if result.returncode not in allowed:
            raise RuntimeError(f"Private read operation {purpose} failed ({result.returncode}); raw data withheld")
        return result.stdout if binary else result.stdout.decode("utf-8", "replace")

    def shell(self, command, purpose, **kwargs):
        return self.adb(["shell", command], purpose, **kwargs)

    def enable_root(self):
        if (not self.runtime.avd_name.startswith(THROWAWAY_PREFIX)
                or self.runtime.state().get("avd") != self.runtime.avd_name
                or not self.runtime.owned()):
            raise RuntimeError("adb root is permitted only on this test's disposable clone")
        message = self.adb(["root"], "root-disposable-clone").strip()
        self.adb(["wait-for-device"], "wait-after-root")
        actual = self.shell("id -u", "verify-root").strip()
        if actual != "0":
            raise RuntimeError("Official system image did not provide root on the disposable clone")
        self.report["root_enabled_only_on_disposable_clone"] = True
        self.report["root_response_class"] = ("already_root" if "already" in message else "restart_requested")

    def package_uid(self):
        details = self.shell("dumpsys package " + PACKAGE, "package-metadata")
        listing = self.shell("cmd package list packages -U --user 0 " + PACKAGE, "package-uid-user-zero")
        self.uid = parse_package_uid(listing, details)
        self.report["package_uid"] = self.uid
        self.report["package_uid_source"] = "Exact package from cmd package list packages -U --user 0, cross-checked against dumpsys appId/userId when present"
        self.report["client_version_name"] = re.findall(r"versionName=(\S+)", details)
        self.report["client_version_code"] = re.findall(r"versionCode=(\d+)", details)

    def scan(self):
        commands = [f"if [ -d {shlex.quote(root)} ]; then find {shlex.quote(root)} -type f "
                    "-exec stat -c '%s|%Y|%i|%u|%d|%n' {} +; fi" for root in ROOTS]
        output = self.shell("\n".join(commands), "scan-file-metadata")
        self.known_paths = set()
        found, counts = {}, {"eligible": 0, "sensitive_name_excluded": 0,
                             "too_large": 0, "other_uid": 0, "unparseable": 0}
        for line in output.splitlines():
            parts = line.split("|", 5)
            if len(parts) != 6 or not all(value.isdecimal() for value in parts[:5]):
                counts["unparseable"] += 1
                continue
            size, mtime, inode, uid, device = map(int, parts[:5])
            path = parts[5]
            if not any(path.startswith(root + "/") for root in ROOTS):
                counts["unparseable"] += 1
                continue
            self.known_paths.add(path)
            relative = path.removeprefix(APP_ROOT + "/")
            if SENSITIVE.search(relative):
                counts["sensitive_name_excluded"] += 1
            elif size > self.max_bytes:
                counts["too_large"] += 1
            elif uid != self.uid:
                counts["other_uid"] += 1
            else:
                found[path] = {"size": size, "mtime_unix_seconds": mtime, "inode": inode,
                               "device": device}
                counts["eligible"] += 1
        return found, counts

    def stat(self, path):
        output = self.shell("stat -c '%s|%Y|%i|%u|%d' -- " + shlex.quote(path),
                            "stat-candidate", allowed=(0, 1)).strip()
        fields = output.split("|")
        if len(fields) != 5 or not all(value.isdecimal() for value in fields):
            return None
        size, mtime, inode, uid, device = map(int, fields)
        if uid != self.uid:
            return None
        return {"size": size, "mtime_unix_seconds": mtime, "inode": inode, "device": device}

    def file_hash(self, path):
        output = self.shell("sha256sum -- " + shlex.quote(path), "hash-candidate").strip()
        digest = output.split(" ", 1)[0]
        if not re.fullmatch("[a-f0-9]{64}", digest):
            raise RuntimeError("Guest sha256sum returned no valid digest")
        return digest

    def handles_by_game_uid(self):
        # All process identities must be readable, so a denied live process is
        # never silently skipped as if it could not belong to the game UID.
        # Only numeric dev/inode records leave this private read operation.
        script = f"""
count=0
for p in /proc/[0-9]*; do
  identity=$(stat -c '%i|%u' "$p" 2>/dev/null) || exit 21
  status=$(cat "$p/status" 2>/dev/null) || exit 22
  uid_line=$(printf '%s\\n' "$status" | grep '^Uid:') || exit 23
  [ -n "$uid_line" ] || exit 23
  set -- $uid_line
  [ "$#" -eq 5 ] || exit 23
  case "$2$3$4$5" in *[!0-9]*) exit 23;; esac
  if [ "$2" != {self.uid} ] && [ "$3" != {self.uid} ] && [ "$4" != {self.uid} ] && [ "$5" != {self.uid} ]; then
    continue
  fi
  count=$((count + 1))
  pid=${{p##*/}}
  printf 'P|%s\\n' "$pid"
  fd_numbers=$(ls -1 "$p/fd" 2>/dev/null) || exit 24
  for fd in $fd_numbers; do
    case "$fd" in ''|*[!0-9]*) exit 25;; esac
    key=$(stat -L -c '%d|%i' "$p/fd/$fd" 2>/dev/null) || exit 26
    printf 'F|%s|%s\\n' "$pid" "$key"
  done
  maps=$(cat "$p/maps" 2>/dev/null) || exit 27
  printf '%s\\n' "$maps" | while IFS=' ' read -r span permissions offset device inode remainder; do
    [ -n "$span" ] || continue
    case "$permissions" in
      [r-][w-][x-][ps]) ;;
      *) exit 28;;
    esac
    case "$permissions" in ?w??) printf 'M|%s|%s|%s\\n' "$pid" "$device" "$inode";; esac
  done || exit 28
  after_identity=$(stat -c '%i|%u' "$p" 2>/dev/null) || exit 29
  after_status=$(cat "$p/status" 2>/dev/null) || exit 29
  after_uid_line=$(printf '%s\\n' "$after_status" | grep '^Uid:') || exit 29
  [ "$after_identity" = "$identity" ] && [ "$after_uid_line" = "$uid_line" ] || exit 29
  printf 'E|%s\\n' "$pid"
done
[ "$count" -gt 0 ] || exit 8
printf 'COMPLETE\\n'
"""
        return parse_handle_snapshot(self.shell(script, "check-game-uid-fds-and-writable-maps"))

    def closed_snapshot(self, identity):
        snapshot = self.handles_by_game_uid()
        reason = ("open_fd" if identity in snapshot["fd_identities"] else
                  "writable_mmap" if identity in snapshot["writable_map_identities"] else None)
        if reason:
            counts = self.report.setdefault("closure_observation_rejections", {})
            counts[reason] = counts.get(reason, 0) + 1
            return None
        return {"same_uid_processes": snapshot["same_uid_processes"],
                "fd_identities_checked": len(snapshot["fd_identities"]),
                "writable_map_identities_checked": len(snapshot["writable_map_identities"])}

    def stable_hash(self, path, require_closed=False):
        before = self.stat(path)
        if before is None or before["size"] > self.max_bytes:
            return None
        closure_checks = []
        try:
            digest = self.file_hash(path)
            if require_closed:
                evidence = self.closed_snapshot((before["device"], before["inode"]))
                if evidence is None:
                    return None
                closure_checks.append(evidence)
        except RuntimeError:
            # A disappearing file/FD or denied read cannot establish closure.
            # Continue observing other candidates; never infer a successful read.
            counts = self.report.setdefault("closure_observation_rejections", {})
            counts["unreadable_or_racy"] = counts.get("unreadable_or_racy", 0) + 1
            return None
        after = self.stat(path)
        if after != before:
            return None
        if require_closed:
            try:
                if self.file_hash(path) != digest or self.stat(path) != before:
                    return None
                evidence = self.closed_snapshot((before["device"], before["inode"]))
                if evidence is None:
                    return None
                closure_checks.append(evidence)
                if self.stat(path) != before:
                    return None
            except RuntimeError:
                counts = self.report.setdefault("closure_observation_rejections", {})
                counts["unreadable_or_racy"] = counts.get("unreadable_or_racy", 0) + 1
                return None
        return {**after, "sha256": digest, "closed_observed_monotonic": time.monotonic(),
                "closure_evidence": closure_checks if require_closed else None}


def cleanup_disposable(rt, report, clone_name):
    """Record cleanup and refuse a completed result unless the slot is idle."""
    evidence = {"stop_attempted": False, "errors": []}
    try:
        evidence["state_avd_before"] = rt.state().get("avd")
        evidence["owned_before"] = rt.owned()
        if evidence["owned_before"] and evidence["state_avd_before"] == clone_name:
            evidence["stop_attempted"] = True
            report["cleanup_sync_stop_of_disposable_clone"] = rt.stop(sync=True)
            if report["cleanup_sync_stop_of_disposable_clone"].get("returncode") not in (None, 0):
                evidence["errors"].append({"operation": "stop", "error": "emu kill returned a nonzero status"})
    except Exception as error:
        evidence["errors"].append({"operation": "stop", "error": str(error)})
    try:
        evidence["state_avd_after"] = rt.state().get("avd")
        evidence["owned_after"] = rt.owned()
    except Exception as error:
        evidence["owned_after"] = None
        evidence["errors"].append({"operation": "verify_identity", "error": str(error)})
    try:
        evidence["ports_still_occupied"] = [port for port in (rt.console_port, rt.console_port + 1, GRPC_PORT)
                                            if rt.port_used(port)]
    except Exception as error:
        evidence["ports_still_occupied"] = None
        evidence["errors"].append({"operation": "verify_ports", "error": str(error)})
    evidence["slot_idle_verified"] = (not evidence["errors"] and evidence["owned_after"] is False
                                       and evidence["ports_still_occupied"] == [])
    report["cleanup"] = evidence
    if not evidence["slot_idle_verified"]:
        report["observation_status_before_cleanup_failure"] = report["status"]
        report["status"] = "blocked" if report["status"] == "blocked" else "failed"
        report["cleanup_error"] = "Cleanup or final slot-idle verification did not complete"


def run(args):
    case = uuid.uuid4().hex[:12]
    clone_name = THROWAWAY_PREFIX + case
    report_path = ROOT / "results/game-cache" / (case + ".json")
    report = {
        "case": case, "test": "genuine_game_write_no_sync", "status": "preparing",
        "source_avd": SOURCE_AVD, "disposable_avd": clone_name,
        "package": PACKAGE, "started_unix": time.time(),
        "file_scopes": ["cache", "files", "shared_prefs"],
        "max_file_bytes": args.max_file_mib * 1024**2,
        "max_existing_baseline_files": args.max_baseline_files,
        "sensitive_filename_exclusion_regex": SENSITIVE.pattern,
        "requested_observation_seconds": args.observe_seconds,
        "requested_delay_after_closed_observation_ms": args.delay_ms,
        "synthetic_game_writes_performed": False, "game_files_deleted_or_overwritten_by_test": False,
        "content_exported": False, "public_paths_are_opaque_hmac_ids": True,
        "closure_observation_method": "Two successful same-UID FD and writable-mmap snapshots, matched by Linux st_dev plus inode, around stable stat/hash checks; no pathname equivalence is assumed",
        "limitations": [
            "Closure means no matching device/inode was observed in readable same-UID process FDs or writable mappings at two non-atomic snapshots, with stable stat/hash; no close syscall timestamp or continued closure is claimed.",
            "Any unreadable or disappearing process identity, FD enumeration/stat, or maps read rejects the snapshot; uncertain candidates are skipped and can yield blocked rather than a closure claim.",
            "New processes, reopened handles, later writes and races between snapshots remain possible. Processes outside the package UID are outside this observation.",
            "Polling may miss brief or same-second same-size writes; no observed candidate means blocked, not no game writes.",
            "Only filtered files within the stated size limit are examined; account/authentication filenames are excluded.",
            "Existing files are sampled by most recent mtime up to max_existing_baseline_files; unhashable or unsampled existing files cannot be attributed as fresh writes.",
            "Files can be changed by boot receivers after reboot; any mismatch needs review and is not alone proof of emulator data loss.",
        ],
    }
    rt = Runtime(clone_name)
    observer = Observer(rt, report, report["max_file_bytes"])
    save_json(report_path, report)
    try:
        if not args.source_synced_stopped:
            raise RuntimeError("Source sync/shutdown was not explicitly confirmed")
        state = rt.state()
        if rt.owned() or any(rt.port_used(port) for port in (22160, 22161, 22162)):
            raise RuntimeError("A task emulator is running; cache test requires an exclusive stopped slot")
        if (state.get("avd") != SOURCE_AVD or state.get("stop_sync") is not True
                or state.get("still_alive") is not False):
            raise RuntimeError("Latest recorded state does not prove the original official_game was synced and stopped")
        source = ROOT / "avd" / (SOURCE_AVD + ".avd")
        if not source.is_dir() or source.is_symlink():
            raise RuntimeError("Original game AVD is missing or not a plain directory")
        report["source_stopped_state"] = {key: state.get(key) for key in
                                          ("avd", "stop_sync", "stopped_unix", "still_alive")}
        clone = rt.clone_stopped(source, clone_name)
        clone.chmod(0o700)
        report["clone_method"] = "APFS cp -cR of synced stopped original; original never booted or rooted by this script"
        rt.start()
        report["first_boot_seconds"] = rt.wait_boot()
        observer.enable_root()
        observer.package_uid()
        alive = observer.shell("pidof " + PACKAGE, "verify-game-not-started", allowed=(0, 1)).strip()
        if alive:
            raise RuntimeError("Game auto-started before baseline; fresh-write attribution is not established")
        initial, counts = observer.scan()
        initial_all_paths = set(observer.known_paths)
        report["baseline_filter_counts"] = counts
        baseline = {}
        selected_paths = sorted(initial, key=lambda path: initial[path]["mtime_unix_seconds"], reverse=True)
        for path in selected_paths[:args.max_baseline_files]:
            value = observer.stable_hash(path)
            if value is not None:
                baseline[path] = value
        report["baseline_hashed_files"] = len(baseline)
        launch_epoch = int(observer.shell("date +%s", "guest-clock-before-game-launch").strip())
        resolved = observer.shell("cmd package resolve-activity --brief -a android.intent.action.MAIN "
                                  "-c android.intent.category.LAUNCHER " + PACKAGE, "resolve-game-launcher")
        components = [line.strip() for line in resolved.splitlines()
                      if line.strip().startswith(PACKAGE + "/")]
        if len(components) != 1:
            raise RuntimeError("Actual Bilibili launcher activity could not be resolved uniquely")
        observer.shell("am start -W -n " + shlex.quote(components[0]), "launch-actual-game", timeout=90)
        report["actual_game_launch_requested"] = True
        report["write_trigger"] = "Launch installed real Bilibili game; no taps, login automation or synthetic file writes"
        report["guest_epoch_before_actual_launch"] = launch_epoch
        report["status"] = "observing_real_game_writes"
        save_json(report_path, report)
        deadline = time.monotonic() + args.observe_seconds
        chosen = None
        while time.monotonic() < deadline:
            alive = observer.shell("pidof " + PACKAGE, "verify-game-running", allowed=(0, 1)).strip()
            if not alive:
                raise RuntimeError("Actual game is not running; no recent-write attribution established")
            current, counts = observer.scan()
            report["last_filter_counts"] = counts
            for path, metadata in sorted(current.items(), key=lambda item: item[1]["mtime_unix_seconds"], reverse=True):
                if metadata["size"] <= 0:
                    report["empty_payload_candidates_skipped"] = report.get("empty_payload_candidates_skipped", 0) + 1
                    continue
                previous = baseline.get(path)
                if path in initial_all_paths and previous is None:
                    continue
                if metadata["mtime_unix_seconds"] < launch_epoch:
                    continue
                if previous is not None and all(metadata[key] == previous[key] for key in metadata):
                    continue
                value = observer.stable_hash(path, require_closed=True)
                if value is None or value["size"] <= 0 or (previous and value["sha256"] == previous["sha256"]):
                    continue
                chosen = (path, value, previous)
                break
            if chosen:
                break
            time.sleep(0.5)
        if chosen is None:
            raise RuntimeError("No eligible nonempty genuine recent game write with observed closed FD and stable digest was confirmed")
        path, before_kill, previous = chosen
        path_id = observer.identifier(path)
        report["path_id"] = path_id
        report["candidate_scope"] = path.removeprefix(APP_ROOT + "/").split("/", 1)[0]
        report["genuine_recent_game_write_confirmed"] = True
        report["baseline_file"] = public_file(previous) if previous else None
        report["before_kill"] = public_file(before_kill)
        report["candidate_closure_evidence"] = before_kill["closure_evidence"]
        report["closed_observed_monotonic"] = before_kill["closed_observed_monotonic"]
        # Private mapping is local, permission-restricted, and never included in
        # stdout or the public report. No file contents or credentials are saved.
        private = ROOT / "private/game-cache"
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        private.chmod(0o700)
        mapping = private / (case + "-paths.json")
        descriptor = os.open(mapping, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            json.dump({"case": case, "path_id": path_id, "relative_path": path.removeprefix(APP_ROOT + "/")}, file)
            file.write("\n")
        if args.delay_ms:
            time.sleep(args.delay_ms / 1000)
        stopped = rt.stop(sync=False)
        report["no_sync_shutdown"] = stopped
        report["closed_observed_to_kill_start_ms"] = 1000 * (
            stopped["kill_started_monotonic"] - before_kill["closed_observed_monotonic"])
        report["kill_command_duration_ms"] = 1000 * (
            stopped["kill_finished_monotonic"] - stopped["kill_started_monotonic"])
        report["status"] = "rebooting_for_comparison"
        save_json(report_path, report)
        rt.start()
        report["verification_boot_seconds"] = rt.wait_boot()
        observer.enable_root()
        observer.package_uid()
        report["game_process_present_before_verification"] = bool(observer.shell(
            "pidof " + PACKAGE, "check-postboot-game-process", allowed=(0, 1)).strip())
        metadata = observer.stat(path)
        after = observer.stable_hash(path) if metadata is not None else None
        report["after_reboot"] = public_file(after or metadata) if (after or metadata) else None
        if metadata is None:
            outcome = "missing_or_unreadable"
        elif after is None:
            outcome = "digest_unavailable_or_file_outside_size_limit"
        elif after["size"] == before_kill["size"] and after["sha256"] == before_kill["sha256"]:
            outcome = "unchanged"
        elif after["size"] == 0 and before_kill["size"] > 0:
            outcome = "zero_bytes"
        elif previous and after["size"] == previous["size"] and after["sha256"] == previous["sha256"]:
            outcome = "baseline_content"
        else:
            outcome = "changed_other"
        report["comparison"] = outcome
        report["status"] = "passed_in_observed_file" if outcome == "unchanged" else "changed_requires_review"
    except Exception as error:
        report["status"] = "blocked" if not report.get("genuine_recent_game_write_confirmed") else "failed"
        report["error"] = str(error)
    finally:
        cleanup_disposable(rt, report, clone_name)
        report["finished_unix"] = time.time()
        save_json(report_path, report)
    print(json.dumps({"report": str(report_path), "status": report["status"],
                      "comparison": report.get("comparison"), "error": report.get("error")},
                     ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-synced-stopped", action="store_true")
    parser.add_argument("--observe-seconds", type=float, default=30)
    parser.add_argument("--max-file-mib", type=int, default=8)
    parser.add_argument("--max-baseline-files", type=int, default=128)
    parser.add_argument("--delay-ms", type=int, default=0)
    args = parser.parse_args()
    if (not 0 < args.observe_seconds <= 300 or not 0 < args.max_file_mib <= 32
            or not 1 <= args.max_baseline_files <= 1024 or not 0 <= args.delay_ms <= 1000):
        parser.error("observe-seconds must be (0,300], max-file-mib 1..32, max-baseline-files 1..1024, delay-ms 0..1000")
    run(args)


if __name__ == "__main__":
    main()
