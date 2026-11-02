#!/usr/bin/env python3
"""Audit a completed round-2 random-memory run and create a new Chinese report.

Read-only with respect to the input run. No ADB, subprocess, emulator operations,
or imports from the acquisition runner. matplotlib is imported only after all
completion, protocol, source, sample, timing and numerical checks pass.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys
from zoneinfo import ZoneInfo
import zlib


MIB = 1024**2
GIB = 1024**3
ALLOCATION = GIB
SMOKE = 16 * MIB
THRESHOLD = 128 * MIB
METRICS = ("guest_MemAvailable_bytes", "host_phys_footprint_bytes", "host_rss_bytes")
INTEGER_FIELDS = (
    "host_rss_bytes", "host_phys_footprint_bytes", "host_compressor_physical_bytes",
    "host_compressor_uncompressed_bytes", "host_free_bytes", "host_swap_total_bytes",
    "host_swap_used_bytes", "host_swap_free_bytes", "host_swapins_pages", "host_swapouts_pages",
    "guest_MemTotal_bytes", "guest_MemAvailable_bytes", "guest_MemFree_bytes", "guest_Cached_bytes",
    "guest_SwapTotal_bytes", "guest_SwapFree_bytes", "host_disk_free_bytes")
KEY_LABELS = {"guest_MemAvailable_bytes": "Guest MemAvailable",
              "host_phys_footprint_bytes": "宿主 phys_footprint",
              "host_rss_bytes": "宿主 RSS"}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON object field: {key}")
        result[key] = value
    return result


def parse_json(text):
    return json.loads(text, object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


class Audit:
    def __init__(self, source):
        self.source = source.resolve()
        self.inputs = {}

    def path(self, relative):
        path = (self.source / relative).resolve()
        require(path.is_relative_to(self.source), f"Input path escapes run directory: {relative}")
        require(path.is_file(), f"Missing input: {path}")
        return path

    def record(self, relative):
        path = self.path(relative)
        self.inputs[str(path)] = {"size_bytes": path.stat().st_size, "sha256": file_sha(path)}
        return path

    def json(self, relative):
        return parse_json(self.record(relative).read_text())

    def jsonl(self, relative):
        lines = self.record(relative).read_text().splitlines()
        require(lines and all(line.strip() for line in lines), f"Empty JSONL or blank event: {relative}")
        values = [parse_json(line) for line in lines]
        require(all(isinstance(value, dict) for value in values), f"Non-object JSONL event: {relative}")
        return values

    def sources(self):
        provenance = self.json("provenance.json")
        require(provenance.get("method") == {
            "algorithm": "chacha20-ietf", "smoke_bytes": SMOKE, "allocation_bytes": ALLOCATION,
            "baseline_seconds": 120, "hold_seconds": 60, "observe_seconds": 600,
            "rounds": 3, "sample_interval_seconds": 5}, "Acquisition method differs from fixed round-2 protocol")
        require(provenance["arguments"].get("smoke_only") is False, "Input is smoke-only")
        files = provenance.get("files", [])
        require(len(files) == 7, "Unexpected source snapshot manifest")
        by_name = {}
        checks = []
        for entry in files:
            snapshot = self.record(entry["snapshot"])
            digest = file_sha(snapshot)
            require(digest == entry["sha256"] and snapshot.stat().st_size == entry["size_bytes"],
                    f"Snapshot SHA/size differs: {snapshot}")
            original = Path(entry["path"])
            require(original.is_file() and file_sha(original) == digest,
                    f"Current source/input SHA differs from acquisition snapshot: {original}")
            require(original.name not in by_name, "Duplicate source manifest basename")
            by_name[original.name] = entry
            checks.append({"source": str(original), "snapshot": str(snapshot), "sha256": digest,
                           "current_source_sha_matches": True})
        require(set(by_name) == {"round2_random_memory.py", "soak_tests.py", "avd_probe.c", "build_probe.py",
                                 "avd_probe", "config.ini", "build_manifest.json"}, "Unexpected provenance source names")
        binary_sha = by_name["avd_probe"]["sha256"]
        build = self.json(by_name["build_manifest.json"]["snapshot"])
        deployment = self.json("deployment.json")
        require(build.get("sha256") == binary_sha == deployment.get("sha256"),
                "Build, binary snapshot, and deployed binary SHA differ")
        require(deployment.get("remote") == "/data/local/tmp/official_avd_probe_round2", "Unexpected deployed probe path")
        state = provenance["identity"]["state"]
        require(state.get("avd") == "official_clean" and state.get("window") is False
                and "-no-window" in state.get("args", []), "Recorded AVD is not the clean headless instance")
        require(state.get("pid") == provenance["arguments"]["emulator_pid"], "Recorded emulator PID differs")
        require(provenance["identity"]["ps_identity"].get("returncode") == 0 and
                provenance["identity"]["ps_identity"]["stdout"].strip() == state["identity"],
                "Recorded process identity check failed")
        for name, result in provenance["host"].items():
            require(result.get("returncode") == 0 and result.get("stdout"), f"Incomplete host metadata: {name}")
        self.json("guest-environment.json")
        return provenance, checks, binary_sha

    def protocol(self, label, allocation, hold_seconds, dump_path=None):
        raw = self.jsonl(f"{label}.stdout.jsonl")
        anchors = self.jsonl(f"{label}.host-events.jsonl")
        protocol = self.json(f"{label}.protocol.json")
        execution = self.json(f"{label}.execution.json")
        require(execution.get("returncode") == 0 and execution.get("reader_stopped") is True
                and execution.get("reader_failures") == [], f"{label}: execution did not finish cleanly")
        require(execution.get("received_event_lines") == len(raw) == len(anchors), f"{label}: event count differs")
        self.record(f"{label}.stderr.log")
        command = execution.get("command", [])
        require(command[-4:] == ["memory-random", str(allocation), str(hold_seconds), dump_path]
                if dump_path else command[-3:] == ["memory-random", str(allocation), str(hold_seconds)],
                f"{label}: workload command differs or formal run unexpectedly dumps data")
        kinds = ["start", "filled", "verified"] + (["dumped"] if dump_path else []) + ["hold_start", "released"]
        require([event.get("event") for event in raw] == ["random_memory_" + kind for kind in kinds],
                f"{label}: unexpected event order")
        seed = raw[0].get("seed_hex")
        require(isinstance(seed, str) and re.fullmatch(r"[0-9a-f]{64}", seed), f"{label}: invalid seed")
        combined, last_guest, last_host = [], -1, -1
        for event, anchor, kind in zip(raw, anchors, kinds):
            require(parse_json(anchor["line"]) == event, f"{label}: anchored raw event differs")
            timestamp, received = event.get("monotonic_ns"), anchor.get("host_received_monotonic_ns")
            require(type(timestamp) is int and timestamp > last_guest and type(received) is int
                    and received > last_host, f"{label}: non-increasing event/receipt time")
            require(execution["host_start_monotonic_ns"] <= received <= execution["host_end_monotonic_ns"],
                    f"{label}: event outside execution host interval")
            last_guest, last_host = timestamp, received
            required = {"algorithm": "chacha20-ietf", "seed_hex": seed, "nonce_hex": "0" * 24,
                        "initial_counter": 0, "bytes": allocation,
                        "filled_bytes": 0 if kind == "start" else allocation,
                        "verified_bytes": 0 if kind in ("start", "filled") else allocation}
            require(all(event.get(key) == value and type(event.get(key)) is type(value)
                        for key, value in required.items()), f"{label}: {kind} full-fill/verify or stream fields differ")
            combined.append({**event, "host_received_monotonic_ns": received})
        require(raw[0].get("seed_source") == "getrandom" and raw[0].get("hold_seconds") == hold_seconds,
                f"{label}: fresh seed / requested hold not confirmed")
        require(type(raw[-1].get("munmap_returncode")) is int and raw[-1]["munmap_returncode"] == 0,
                f"{label}: successful munmap not confirmed")
        if dump_path:
            require(raw[3].get("dump_path") == dump_path and raw[3].get("dumped_bytes") == allocation,
                    f"{label}: dump receipt differs")
        duration = (raw[-1]["monotonic_ns"] - raw[-2]["monotonic_ns"]) / 1e9
        require(hold_seconds - .01 <= duration <= hold_seconds + max(5, hold_seconds * .2),
                f"{label}: actual guest hold differs")
        offsets = [event["host_received_monotonic_ns"] - event["monotonic_ns"] for event in combined]
        mapping = {"offset_ns": min(offsets), "observed_offset_spread_ns": max(offsets) - min(offsets),
                   "hold_endpoint_guard_ns": 1_000_000_000}
        hold_start = raw[-2]["monotonic_ns"] + min(offsets) + 1_000_000_000
        hold_end = raw[-1]["monotonic_ns"] + min(offsets) - 1_000_000_000
        require(protocol.get("valid") is True and protocol.get("events") == combined,
                f"{label}: saved protocol differs from raw events")
        require(protocol.get("seed_hex") == seed and protocol.get("algorithm") == "chacha20-ietf"
                and protocol.get("nonce_hex") == "0" * 24 and protocol.get("initial_counter") == 0
                and protocol.get("allocation_bytes") == allocation
                and protocol.get("hold_duration_seconds") == duration
                and protocol.get("hold_start_host_estimate_ns") == hold_start
                and protocol.get("hold_end_host_estimate_ns") == hold_end,
                f"{label}: saved protocol calculations differ")
        require(all(protocol["clock_mapping"].get(key) == value for key, value in mapping.items()),
                f"{label}: clock mapping differs from receipt anchors")
        return {"seed_hex": seed, "hold_duration_seconds": duration, "events": combined,
                "hold_start_host_estimate_ns": hold_start, "hold_end_host_estimate_ns": hold_end,
                "clock_mapping": mapping, "execution": execution}

    def smoke(self):
        receipt = self.json("smoke-verification.json")
        require(receipt.get("status") == "passed" and receipt.get("bytes_compared") == SMOKE
                and receipt.get("algorithm") == "chacha20-ietf", "Smoke independent verification did not pass")
        protocol = self.protocol("smoke", SMOKE, 1, receipt["remote_dump"])
        require(receipt.get("seed_hex") == protocol["seed_hex"], "Smoke receipt seed differs")
        dump = self.record("smoke-16MiB.bin")
        require(dump.stat().st_size == SMOKE, "Smoke dump byte count differs")
        digest = file_sha(dump)
        require(receipt.get("dump_sha256") == digest == receipt.get("expected_sha256"), "Smoke dump SHA differs")
        # Bounded-memory recheck of compression and every 4 KiB page hash.
        compressor = zlib.compressobj(level=9)
        compressed_bytes, page_hashes = 0, []
        with dump.open("rb") as stream:
            for page in iter(lambda: stream.read(4096), b""):
                page_hashes.append(hashlib.sha256(page).hexdigest())
                compressed_bytes += len(compressor.compress(page))
        compressed_bytes += len(compressor.flush())
        require(compressed_bytes == receipt.get("zlib_compressed_bytes") and
                compressed_bytes / SMOKE == receipt.get("compression_ratio") >= .99,
                "Smoke compression audit differs or is below threshold")
        require(receipt.get("page_sha256") == page_hashes and len(set(page_hashes)) == len(page_hashes) == 4096
                and receipt.get("page_count") == receipt.get("unique_page_sha256_count") == 4096,
                "Smoke page hashes differ or contain duplicates")
        return {"seed_hex": protocol["seed_hex"], "dump_sha256": digest,
                "compression_ratio": compressed_bytes / SMOKE, "unique_pages": 4096,
                "bytes_compared_during_acquisition": SMOKE,
                "guarded_hold_interval_valid": protocol["hold_start_host_estimate_ns"] < protocol["hold_end_host_estimate_ns"],
                "timing_limitation": "Acquisition applies the same 1 s endpoint guard to the 1 s smoke hold, creating an empty/reversed estimated hold interval. Smoke timing is not used for metric windows; the raw smoke hold duration is independently validated."}

    def samples(self, origin):
        path = self.record("memory.csv")
        with path.open(newline="") as stream:
            reader = csv.DictReader(stream)
            required = set(INTEGER_FIELDS) | {"timestamp_utc", "elapsed_seconds", "phase", "sample_duration_ms",
                                             "guest_memory_error", "host_memory_error"}
            require(reader.fieldnames and required <= set(reader.fieldnames)
                    and len(set(reader.fieldnames)) == len(reader.fieldnames), "Incomplete/duplicate CSV header")
            rows = list(reader)
        require(len(rows) >= 420, "Too few samples for complete fixed protocol")
        allowed_phases = {"baseline"} | {f"round_{r}_{phase}" for r in range(1, 4)
                                         for phase in ("allocate_hold", "released_observe")}
        for index, row in enumerate(rows, 1):
            require(None not in row and all(row.get(key) is not None for key in required), f"CSV row {index} is incomplete")
            require(not row["guest_memory_error"] and not row["host_memory_error"], f"CSV row {index} has a sample error")
            require(row["phase"] in allowed_phases, f"CSV row {index} has an unknown phase")
            for key in INTEGER_FIELDS:
                require(re.fullmatch(r"[0-9]+", row[key]) is not None, f"CSV row {index}: invalid {key}")
                row[key] = int(row[key])
            for key in ("elapsed_seconds", "sample_duration_ms"):
                row[key] = float(row[key])
                require(math.isfinite(row[key]) and row[key] >= 0, f"CSV row {index}: invalid {key}")
            require(row["sample_duration_ms"] < 5000, f"CSV row {index} sample took >=5 s")
            require(row["guest_MemTotal_bytes"] > 0 and row["host_phys_footprint_bytes"] > 0,
                    f"CSV row {index}: zero guest total or emulator footprint")
            require(row["host_disk_free_bytes"] >= 20 * GIB, f"CSV row {index}: disk reserve breached")
            require(row["guest_MemAvailable_bytes"] <= row["guest_MemTotal_bytes"]
                    and row["guest_MemFree_bytes"] <= row["guest_MemTotal_bytes"]
                    and row["guest_SwapFree_bytes"] <= row["guest_SwapTotal_bytes"], f"CSV row {index}: guest totals inconsistent")
            stamp = dt.datetime.fromisoformat(row["timestamp_utc"])
            require(stamp.tzinfo is not None and stamp.utcoffset() == dt.timedelta(0),
                    f"CSV row {index}: timestamp is not valid UTC")
            row["utc_epoch"] = stamp.timestamp()
            row["start_ns"] = origin + round(row["elapsed_seconds"] * 1e9)
            row["end_ns"] = origin + round((row["elapsed_seconds"] + row["sample_duration_ms"] / 1000) * 1e9)
        gaps = [b["elapsed_seconds"] - a["elapsed_seconds"] for a, b in zip(rows, rows[1:])]
        require(all(4 <= gap <= 6 for gap in gaps), "Sample interval outside declared 5 +/- 1 seconds")
        require(abs(statistics.median(gaps) - 5) <= .05, "Median sample interval differs from 5 seconds")
        require(all(a["end_ns"] <= b["start_ns"] for a, b in zip(rows, rows[1:])),
                "Sample duration overlaps the next monotonic sample start")
        # UTC labels can be adjusted independently of monotonic clocks. Preserve
        # discrepancies instead of treating a wall-clock correction as a short
        # observation. Cadence, ordering, windows and >=600 s coverage continue
        # to be enforced exclusively on independent monotonic measurements.
        wall_clock_changes = []
        for current_sample, (a, b, gap) in enumerate(zip(rows, rows[1:], gaps), 2):
            wall_gap = b["utc_epoch"] - a["utc_epoch"]
            residual = wall_gap - gap
            if wall_gap <= 0 or abs(residual) > .05:
                wall_clock_changes.append({"csv_line": current_sample + 1,
                    "previous_timestamp_utc": a["timestamp_utc"], "timestamp_utc": b["timestamp_utc"],
                    "previous_elapsed_seconds": a["elapsed_seconds"], "elapsed_seconds": b["elapsed_seconds"],
                    "utc_interval_seconds": wall_gap, "monotonic_interval_seconds": gap,
                    "utc_minus_monotonic_interval_seconds": residual,
                    "previous_sample_duration_ms": a["sample_duration_ms"],
                    "sample_duration_ms": b["sample_duration_ms"]})
        phase_order = {name: number for number, name in enumerate(
            ["baseline"] + [f"round_{r}_{phase}" for r in range(1, 4)
                            for phase in ("allocate_hold", "released_observe")])}
        require(set(row["phase"] for row in rows) == allowed_phases and
                all(phase_order[b["phase"]] >= phase_order[a["phase"]] for a, b in zip(rows, rows[1:])),
                "Sample phases are missing or go backwards")
        require(len({row["guest_MemTotal_bytes"] for row in rows}) == 1, "Guest total RAM changed during test")
        for key in ("host_swapins_pages", "host_swapouts_pages"):
            require(all(b[key] >= a[key] for a, b in zip(rows, rows[1:])), f"Host cumulative counter went backwards: {key}")
        return rows, {"count": len(rows), "interval_target_seconds": 5, "interval_allowed_seconds": [4, 6],
                      "interval_min_seconds": min(gaps), "interval_median_seconds": statistics.median(gaps),
                      "interval_max_seconds": max(gaps), "max_sample_duration_ms": max(row["sample_duration_ms"] for row in rows),
                      "host_error_samples": 0, "guest_error_samples": 0,
                      "wall_clock_changes": wall_clock_changes,
                      "csv_utc_minus_monotonic_span_seconds": (rows[-1]["utc_epoch"] - rows[0]["utc_epoch"])
                          - (rows[-1]["elapsed_seconds"] - rows[0]["elapsed_seconds"]),
                      "clock_policy": "UTC is retained as a calendar label and its discontinuities are disclosed. All cadence, duration and metric-window integrity checks use monotonic clocks; UTC need not advance at the same rate."}


def recalculate_round(entry, protocol, rows, previous):
    number, timing = entry["round"], entry["timing"]
    for key in ("hold_start_host_estimate_ns", "hold_end_host_estimate_ns"):
        require(timing.get(key) == protocol[key], f"Round {number}: host hold bound differs from events")
    require(timing.get("guest_hold_duration_seconds") == protocol["hold_duration_seconds"], f"Round {number}: guest hold differs")
    require(all(timing["clock_mapping"].get(key) == value for key, value in protocol["clock_mapping"].items()),
            f"Round {number}: progress clock mapping differs")
    duration = (timing["observe_end_ns"] - timing["observe_start_ns"]) / 1e9
    require(600 <= duration <= 605 and duration == entry.get("actual_observe_seconds"),
            f"Round {number}: actual observation is not a complete 600 s")
    require(protocol["execution"]["host_end_monotonic_ns"] <= timing["observe_start_ns"]
            and timing["baseline_end_ns"] <= protocol["execution"]["host_start_monotonic_ns"],
            f"Round {number}: baseline/execution/observation order differs")
    baseline_duration = (timing["baseline_end_ns"] - timing["baseline_start_ns"]) / 1e9
    if previous:
        require(60 <= baseline_duration <= 65 and
                previous["timing"]["observe_start_ns"] <= timing["baseline_start_ns"]
                <= previous["timing"]["observe_end_ns"] and
                0 <= timing["baseline_end_ns"] - previous["timing"]["observe_end_ns"] < 5_000_000_000,
                f"Round {number}: baseline is not previous observation's final minute")
    else:
        require(120 <= baseline_duration <= 125, "Initial baseline is not a complete 120 s")
    baseline = [row for row in rows if timing["baseline_start_ns"] <= row["start_ns"] < timing["baseline_end_ns"]][-12:]
    held = [row for row in rows if protocol["hold_start_host_estimate_ns"] <= row["start_ns"]
            and row["end_ns"] <= protocol["hold_end_host_estimate_ns"]][-3:]
    observed = [row for row in rows if timing["observe_start_ns"] <= row["start_ns"] <= timing["observe_end_ns"]]
    released = observed[-12:]
    require(len(baseline) == 12 and len(held) == 3 and len(released) == 12 and len(observed) >= 119,
            f"Round {number}: incomplete metric windows")
    require(observed[0]["start_ns"] - timing["observe_start_ns"] <= 6_000_000_000 and
            timing["observe_end_ns"] - observed[-1]["start_ns"] <= 6_000_000_000,
            f"Round {number}: observation samples do not cover full interval")
    require(all(row["phase"] == f"round_{number}_allocate_hold" for row in held)
            and all(row["phase"] == f"round_{number}_released_observe" for row in released),
            f"Round {number}: sample phases disagree with event-derived windows")
    metrics = {}
    for key in METRICS:
        before = statistics.median(row[key] for row in baseline)
        during = statistics.median(row[key] for row in held)
        after = statistics.median(row[key] for row in released)
        increment = before - during if key.startswith("guest_") else during - before
        recovered = after - during if key.startswith("guest_") else during - after
        fraction = recovered / increment if increment >= THRESHOLD else None
        metrics[key] = {"baseline_median_bytes": before, "hold_median_last_3_bytes": during,
                        "release_median_last_12_bytes": after, "hold_minus_baseline_bytes": during - before,
                        "release_minus_hold_bytes": after - during, "release_minus_baseline_bytes": after - before,
                        "allocation_increment_bytes": increment,
                        "reclamation_fraction_minimum_increment_bytes": THRESHOLD, "reclaimed_fraction": fraction}
    return {"round": number, "protocol_valid": True, "observation_completed": True,
            "sample_counts": {"baseline_last_12": 12, "hold_last_3": 3, "release_last_12": 12},
            "metrics": metrics, "timing": timing,
            "windows": {name: {"sample_count": len(values), "first_elapsed_seconds": values[0]["elapsed_seconds"],
                                 "last_elapsed_seconds": values[-1]["elapsed_seconds"]}
                        for name, values in (("baseline", baseline), ("hold", held), ("released", released))},
            "actual_observe_seconds": duration, "seed_hex": protocol["seed_hex"],
            "fill_duration_seconds": (protocol["events"][1]["monotonic_ns"] - protocol["events"][0]["monotonic_ns"]) / 1e9,
            "verify_duration_seconds": (protocol["events"][2]["monotonic_ns"] - protocol["events"][1]["monotonic_ns"]) / 1e9}


def compare_summary(saved, calculated, sample_count):
    require(saved.get("status") == "completed" and saved.get("completed_rounds") == 3
            and saved.get("sample_count") == sample_count
            and saved.get("host_error_samples") == saved.get("guest_error_samples") == 0,
            "Summary completion/sample counters disagree")
    require(len(saved.get("rounds", [])) == 3, "Summary must contain exactly three rounds")
    for recorded, actual in zip(saved["rounds"], calculated):
        number = actual["round"]
        require(all(recorded.get(key) == actual[key] for key in
                    ("round", "protocol_valid", "observation_completed", "sample_counts", "timing")),
                f"Round {number}: summary windows/status/timing differ")
        require(set(recorded.get("metrics", {})) == set(METRICS), f"Round {number}: summary metric keys differ")
        for key in METRICS:
            for name, value in actual["metrics"][key].items():
                other = recorded["metrics"][key].get(name)
                if name == "reclaimed_fraction" and value is not None:
                    require(isinstance(other, (float, int)) and math.isclose(other, value, rel_tol=1e-12, abs_tol=1e-12),
                            f"Round {number}: {key}/{name} differs from independent recomputation")
                else:
                    require(other == value, f"Round {number}: {key}/{name} differs from independent recomputation")


def audit_run(source):
    audit = Audit(source)
    progress = audit.json("progress.json")
    require(progress.get("status") == "completed" and progress.get("stage") == "finished"
            and progress.get("smoke_passed") is True and progress.get("smoke_only") is False
            and "error" not in progress and "traceback" not in progress,
            "Run is not completed; no report or plots will be produced")
    require("finished_utc" in progress and len(progress.get("rounds", [])) == 3
            and [entry.get("round") for entry in progress["rounds"]] == [1, 2, 3]
            and all(entry.get("protocol_valid") is True and entry.get("observation_completed") is True
                    for entry in progress["rounds"]), "All three rounds must be fully completed")
    provenance, source_checks, binary_sha = audit.sources()
    smoke = audit.smoke()
    origin = progress.get("monitor_start_monotonic_ns")
    require(type(origin) is int and origin > 0, "Missing monitor host monotonic origin")
    rows, sample_checks = audit.samples(origin)
    sample_checks["run_utc_minus_monotonic_seconds"] = (
        dt.datetime.fromisoformat(progress["finished_utc"]) - dt.datetime.fromisoformat(progress["started_utc"])
    ).total_seconds() - progress["elapsed_seconds"]
    rounds, seeds, previous = [], {smoke["seed_hex"]}, None
    for entry in progress["rounds"]:
        protocol = audit.protocol(f"round_{entry['round']}", ALLOCATION, 60)
        require(protocol["seed_hex"] not in seeds and protocol["seed_hex"] == entry.get("seed_hex"),
                "Repeated or inconsistent round seed")
        seeds.add(protocol["seed_hex"])
        rounds.append(recalculate_round(entry, protocol, rows, previous))
        previous = entry
    saved = audit.json("summary.json")
    compare_summary(saved, rounds, len(rows))
    require(rows[-1]["start_ns"] >= progress["rounds"][-1]["timing"]["observe_end_ns"] - 6_000_000_000,
            "CSV ends before final observation")
    background = {}
    for key in ("host_compressor_physical_bytes", "host_compressor_uncompressed_bytes", "host_swap_used_bytes",
                "host_swapins_pages", "host_swapouts_pages"):
        values = [row[key] for row in rows]
        background[key] = {"first": values[0], "last": values[-1], "min": min(values), "max": max(values),
                           "last_minus_first": values[-1] - values[0]}
    result = {"status": "passed", "input_directory": str(source.resolve()),
              "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "source_checks": source_checks, "binary_sha256": binary_sha,
              "smoke": smoke, "samples": sample_checks, "rounds": rounds,
              "background_whole_host": background, "summary_independent_recomputation_matches": True,
              "independent_seed_count_including_smoke": len(seeds), "inputs": audit.inputs,
              "time_semantics": "CSV sample start is rounded to milliseconds relative to host monotonic origin. Hold selection uses raw guest event times plus the least-delayed receipt anchor and a 1 s guard at both ends. One-way transport delay is not independently bounded. Release observation starts only after validated probe exit.",
              "scope": "Protocol completion and process-memory reclamation are separate results; system compressor and swap are whole-host background. This test alone cannot establish a memory leak."}
    return result, progress, provenance, rows


def mib(value, signed=False):
    return f"{value / MIB:+.2f}" if signed else f"{value / MIB:.2f}"


def percentage(value):
    return "null（增量低于门槛）" if value is None else f"{value * 100:.2f}%"


def render_report(audit, progress, provenance):
    local_started = dt.datetime.fromisoformat(progress["started_utc"]).astimezone(ZoneInfo("Asia/Shanghai"))
    local_finished = dt.datetime.fromisoformat(progress["finished_utc"]).astimezone(ZoneInfo("Asia/Shanghai"))
    host = provenance["host"]
    lines = ["# 第二轮：全量随机内存填充与释放", "",
             "三轮负载协议均完成，独立重算结果与采集汇总一致。协议完成表示每轮完整写入并逐字节读验 1 GiB、持有约 60 秒、成功 `munmap` 并继续观察至少 600 秒；宿主内存回收另按实际占用增量和释放后的测量值解释。", "",
             f"测量时间：{local_started:%Y-%m-%d %H:%M:%S} 至 {local_finished:%H:%M:%S}（Asia/Shanghai）。", "",
             f"输入目录：`{audit['input_directory']}`。样本 {audit['samples']['count']} 个，目标间隔 5 秒，实测间隔最小／中位／最大为 {audit['samples']['interval_min_seconds']:.3f}／{audit['samples']['interval_median_seconds']:.3f}／{audit['samples']['interval_max_seconds']:.3f} 秒；guest 与 host 错误样本均为 0。", "",
             "## 采集与验收", "",
             "使用官方 SDK 的 `official_clean` 无头实例。首次基线至少 120 秒；后续每轮基线取上一轮释放观察的最后一分钟。ChaCha20-IETF 使用 guest `getrandom` 的独立 32 字节种子、全零 nonce 和从 0 连续递增的块计数器；三轮种子与烟测种子互不相同。正式 1 GiB 负载不导出全量内容。", "",
             f"16 MiB 烟测在采集时由独立 Python 实现完整重生成比较，且通过 RFC 8439 已知答案自检。本次复核其 dump SHA-256、zlib 压缩率 {audit['smoke']['compression_ratio']:.6f}（预设下限 0.99）与 4,096 个互不重复的 4 KiB 页哈希。压缩阈值是本次工程验收条件。", "",
             "烟测元数据有一项已知限制：1 秒持有也套用了两端各 1 秒的映射 guard，导致其估算窗口为空或倒置。该窗口不参与任何指标计算；烟测真实持有时长另由 guest 原始单调时间核验。三轮正式 60 秒持有区间分别审计。", "",
             "以下数值由原始 CSV 与 JSONL 事件独立重算，逐项核对 `summary.json`。基线取末 12 点，持有取真实持有窗口内的末 3 点，释放取完整观察窗口末 12 点，各窗口均计算中位数。CSV 的 `allocate_hold` 大阶段包含分配、填充、校验和持有；真实持有边界由 guest 事件与 host 接收锚点计算，两端各排除 1 秒。单向传输绝对延迟未知，不能将这些映射边界当成精确同步的时钟。", "",
             "| 轮次 | 填充 / 读验（秒） | guest 持有（秒） | 释放观察（秒） | 锚点 offset 跨度（毫秒） |", "| --- | ---: | ---: | ---: | ---: |"]
    for entry in audit["rounds"]:
        lines.append(f"| {entry['round']} | {entry['fill_duration_seconds']:.3f} / {entry['verify_duration_seconds']:.3f} | {entry['timing']['guest_hold_duration_seconds']:.3f} | {entry['actual_observe_seconds']:.3f} | {entry['timing']['clock_mapping']['observed_offset_spread_ns'] / 1e6:.3f} |")
    if audit["samples"]["wall_clock_changes"]:
        lines += ["", "时间审计保留了一处墙钟限制：UTC 是日历标签，采样节拍、窗口和持续时间均以单调时钟核验。以下 UTC 偏移支持墙钟发生调整，但采集证据未记录校时来源；原始时间戳未修改，三轮 600 秒观察不依赖 UTC 差值。", "",
                  "| CSV 行 | UTC 时间（Asia/Shanghai） | UTC 间隔（秒） | 单调间隔（秒） | 两者之差（毫秒） |",
                  "| --- | --- | ---: | ---: | ---: |"]
        for change in audit["samples"]["wall_clock_changes"]:
            stamp = dt.datetime.fromisoformat(change["timestamp_utc"]).astimezone(ZoneInfo("Asia/Shanghai"))
            lines.append(f"| {change['csv_line']} | {stamp:%Y-%m-%d %H:%M:%S.%f} | {change['utc_interval_seconds']:.6f} | {change['monotonic_interval_seconds']:.6f} | {change['utc_minus_monotonic_interval_seconds'] * 1000:+.3f} |")
        lines += ["", f"CSV 全跨度 UTC−单调时钟差为 {audit['samples']['csv_utc_minus_monotonic_span_seconds'] * 1000:+.3f} 毫秒；整个采集任务起止 UTC−单调 elapsed 差为 {audit['samples']['run_utc_minus_monotonic_seconds'] * 1000:+.3f} 毫秒。首次报告因错误要求两种时钟每点等速而拒绝生成；该版脚本与诊断记录保存在相邻 `random-memory-report-diagnostics/` 目录。修订后保留全部单调时钟、事件顺序、样本完整性与窗口覆盖检查。"]
    lines += ["", "## 三轮关键指标", "", "单位均为 MiB；Δ 为后一个窗口减前一个窗口。Guest 可用内存的分配增量使用“基线 − 持有”，宿主占用使用“持有 − 基线”。回收比例只有分配增量至少达到预设 128 MiB 门槛时才计算，数值不裁剪到 0–100%。", "",
              "| 轮次 | 指标 | 基线 | 持有 | 释放 | Δ 持有−基线 | Δ 释放−持有 | Δ 释放−基线 | 回收比例 |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for entry in audit["rounds"]:
        for key, value in entry["metrics"].items():
            lines.append(f"| {entry['round']} | {KEY_LABELS[key]} | {mib(value['baseline_median_bytes'])} | {mib(value['hold_median_last_3_bytes'])} | {mib(value['release_median_last_12_bytes'])} | {mib(value['hold_minus_baseline_bytes'], True)} | {mib(value['release_minus_hold_bytes'], True)} | {mib(value['release_minus_baseline_bytes'], True)} | {percentage(value['reclaimed_fraction'])} |")
    primary = [entry["metrics"]["host_phys_footprint_bytes"] for entry in audit["rounds"]]
    valid = [value for value in primary if value["reclaimed_fraction"] is not None]
    if not valid:
        explanation = "三轮 phys_footprint 的持有增量均低于 128 MiB，因此宿主主要口径的回收比例均为 `null`；本次数据不足以可靠估计宿主回收比例。"
    else:
        explanation = f"三轮中有 {len(valid)} 轮 phys_footprint 的持有增量达到 128 MiB，表中按对应实测增量报告回收比例；其他轮次为 `null`。"
    lines += ["", explanation + " RSS 为补充指标，应与 phys_footprint 分开阅读。Guest `MemAvailable` 的恢复表明 guest 可用内存变化，不能替代宿主占用的回收证据。本次曲线、差值与三轮重复测试不能单独判定内存泄漏。", "",
              "![第二轮完整时间序列](memory-timeline.png)", "",
              "图中橙色区域为两端各排除 1 秒后的持有窗口，虚线为释放观察开始；底部压缩与交换曲线覆盖整个宿主。", "",
              "## 全宿主内存压力背景", "",
              "| 全宿主字段 | 起始 | 末尾 | 最小 | 最大 | 末尾−起始 |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for key in ("host_compressor_physical_bytes", "host_compressor_uncompressed_bytes", "host_swap_used_bytes"):
        value = audit["background_whole_host"][key]
        lines.append(f"| {key}（MiB） | {mib(value['first'])} | {mib(value['last'])} | {mib(value['min'])} | {mib(value['max'])} | {mib(value['last_minus_first'], True)} |")
    lines += ["", "以上压缩和交换指标包含其他进程及系统活动，不能全部归因于模拟器。宿主起始已经存在的内存压力、已经建立的虚拟机内存映射以及并发活动，均限制了以一次负载推算宿主新增占用及回收量的解释。", "",
              "## 环境与溯源", "", "```text", host["macos"]["stdout"].strip(), host["memory"]["stdout"].strip(), "```", "",
              f"探针 binary／构建 manifest／部署 SHA-256 一致：`{audit['binary_sha256']}`。源码、调度器、共享采样器、构建脚本与 AVD 配置的留档 SHA-256 均与采集清单和当前文件一致。", "",
              "| 文件 | SHA-256 |", "| --- | --- |"]
    for item in audit["source_checks"]:
        lines.append(f"| `{Path(item['source']).name}` | `{item['sha256']}` |")
    lines += ["", "逐项审计回执、重新计算的窗口与完整输入文件哈希见 [audit.json](audit.json)；矢量图见 [memory-timeline.svg](memory-timeline.svg)。历史结果与原始采集目录均保留。", ""]
    return "\n".join(lines)


def plot(audit, progress, rows, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.alpha": .18, "axes.axisbelow": True,
                         "svg.fonttype": "none", "savefig.dpi": 180})
    panels = [
        ("Guest available memory (GiB)", [("guest_MemAvailable_bytes", "MemAvailable", "#23836a", GIB)]),
        ("Emulator phys_footprint (GiB)", [("host_phys_footprint_bytes", "phys_footprint", "#1762a3", GIB)]),
        ("Emulator RSS (MiB)", [("host_rss_bytes", "RSS", "#ca7625", MIB)]),
        ("Whole-host pressure (GiB)", [("host_compressor_physical_bytes", "Compressor physical", "#7762a6", GIB),
                                      ("host_swap_used_bytes", "Swap used", "#b84f73", GIB)])]
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
    minutes = [row["elapsed_seconds"] / 60 for row in rows]
    origin = progress["monitor_start_monotonic_ns"]
    for axis, (label, definitions) in zip(axes, panels):
        for key, legend, color, scale in definitions:
            axis.plot(minutes, [row[key] / scale for row in rows], label=legend, color=color, linewidth=1.25)
        axis.set_ylabel(label)
        axis.legend(loc="upper left", fontsize=8, framealpha=.85)
        for entry in audit["rounds"]:
            timing = entry["timing"]
            start = (timing["hold_start_host_estimate_ns"] - origin) / 60e9
            end = (timing["hold_end_host_estimate_ns"] - origin) / 60e9
            release = (timing["observe_start_ns"] - origin) / 60e9
            axis.axvspan(start, end, color="#f3ac4b", alpha=.22, linewidth=0)
            axis.axvline(release, color="#6a727b", linestyle="--", linewidth=.8)
            if axis is axes[0]:
                axis.text((start + end) / 2, 1.025, f"R{entry['round']}", transform=axis.get_xaxis_transform(),
                          horizontalalignment="center", fontsize=9)
    axes[-1].set_xlabel("Minutes since baseline monitor start")
    axes[-1].set_xlim(0, minutes[-1])
    fig.suptitle("Round 2: 3 x 1 GiB full-random guest allocation / verification / release", fontsize=13, y=.992)
    fig.text(.08, .013, "Orange: guarded 60 s hold. Dashed: validated release observation start.\n"
             "Host compressor/swap cover the whole machine; protocol completion does not imply process-memory reclamation.",
             fontsize=8, color="#535c67")
    fig.tight_layout(rect=(0, .05, 1, .975), h_pad=1.8)
    for extension in ("png", "svg"):
        fig.savefig(output / f"memory-timeline.{extension}", facecolor="white",
                    metadata={"Creator": "round2_memory_report.py / matplotlib"})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Explicit completed random-memory acquisition directory")
    parser.add_argument("--output", required=True, help="New report directory; an existing path is refused")
    args = parser.parse_args()
    source, output = Path(args.input).resolve(), Path(args.output).resolve()
    require(source.is_dir(), f"Input directory does not exist: {source}")
    require(not output.exists(), f"Refusing to overwrite existing output: {output}")
    require(not output.is_relative_to(source), "Report output must be outside the raw acquisition directory")
    audit, progress, provenance, rows = audit_run(source)
    # Only validated complete data reaches output creation or matplotlib.
    output.mkdir(parents=True, exist_ok=False)
    audit["report_script"] = {"path": str(Path(__file__).resolve()), "sha256": file_sha(__file__)}
    plot(audit, progress, rows, output)
    (output / "report.md").write_text(render_report(audit, progress, provenance))
    audit["outputs"] = {name: {"sha256": file_sha(output / name), "size_bytes": (output / name).stat().st_size}
                        for name in ("report.md", "memory-timeline.png", "memory-timeline.svg")}
    (output / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "completed", "report": str(output / "report.md"),
                      "audit": str(output / "audit.json"), "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Audit/report refused: {error}", file=sys.stderr)
        raise SystemExit(1)
