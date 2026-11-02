#!/usr/bin/env python3
"""Validate existing game-soak evidence without sampling or changing source files.

Writes an independent validation.json. Network summaries, raw event windows,
memory CSV coverage and per-sample metrics, and game observer ownership/liveness
must all agree. This validates evidence acquisition, not absence of memory leaks
or the game's own server connection.
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
import time

ROOT = Path(__file__).resolve().parent
DURATION = 7200.0
MEMORY_INTERVAL = 5.0
MEMORY_MIN_SPAN = 7195.0
MEMORY_MAX_GAP = 10.0
FIRST_BOUNDARY_TOLERANCE = 5.0
LAST_BOUNDARY_TOLERANCE = 6.0
NETWORK_WINDOW_TOLERANCE = 0.1
ELAPSED_ROUNDING_TOLERANCE = 0.001
CORE_METRICS = ('host_rss_bytes', 'host_phys_footprint_bytes',
                'host_compressor_physical_bytes', 'host_compressor_uncompressed_bytes',
                'host_swap_used_bytes', 'guest_MemTotal_bytes', 'guest_MemAvailable_bytes',
                'host_disk_free_bytes')


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('UTC timestamp lacks an offset')
    return parsed.timestamp()


def number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite numeric value')
    return result


def clock_residuals(wall, monotonic):
    """Describe wall-clock adjustments; they never establish elapsed coverage."""
    values = [(w - wall[0]) - (m - monotonic[0]) for w, m in zip(wall, monotonic)]
    return {'utc_minus_monotonic_delta_min_seconds': min(values, default=None),
            'utc_minus_monotonic_delta_max_seconds': max(values, default=None),
            'over_one_second_residual_indices_first_20':
                [i for i, value in enumerate(values) if abs(value) > 1][:20]}


def load_json(path):
    return json.loads(path.read_text())


def load_events(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def validate(soak, observer, *, expected_avd, package='com.hypergryph.arknights.bilibili'):
    soak, observer = Path(soak).resolve(), Path(observer).resolve()
    reasons, checks, evidence = [], {}, {}
    host_windows, clock_reference = {}, {}
    def require(condition, message):
        if not condition:
            reasons.append(message)
    def record_file(path):
        raw = path.read_bytes()
        evidence[str(path)] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    def section(name, fn):
        before = len(reasons)
        try:
            result = fn()
        except Exception as exc:
            reasons.append(f'{name}: {type(exc).__name__}: {exc}')
            result = {}
        checks[name] = {'status': 'passed' if len(reasons) == before else 'failed', **result}
    orchestration, local_metadata = {}, {}
    try:
        orchestration = load_json(soak / 'orchestration.json')
        local_metadata = load_json(soak / 'metadata.json')
        start = timestamp(orchestration['started_utc'])
        finish = timestamp(orchestration['finished_utc'])
    except Exception as exc:
        reasons.append(f'Missing/invalid orchestration metadata: {exc}')
        start, finish = None, None
    def orchestration_check():
        for name in ('orchestration.json', 'metadata.json'):
            record_file(soak / name)
        require(orchestration.get('status') == 'passed', 'Original orchestration did not pass')
        require(orchestration.get('execution_completed') is True, 'Original orchestration did not complete execution')
        require(orchestration.get('duration_seconds') == DURATION, 'Original run duration is not exactly 7200 seconds')
        require(number(orchestration['elapsed_seconds']) >= DURATION, 'Original run elapsed time is shorter than 7200 seconds')
        require(orchestration.get('memory_monitor_count') == 1, 'Expected exactly one memory monitor')
        require(orchestration.get('network_statuses') == {'local': 'passed', 'wan': 'passed'}, 'Original network groups did not both pass')
        require(local_metadata.get('duration') == DURATION, 'Local metadata duration is not 7200 seconds')
        require(local_metadata.get('sample_interval') == MEMORY_INTERVAL, 'Memory sample interval is not 5 seconds')
        label = orchestration.get('workload_label', '')
        require(bool(label) and 'idle' not in label.lower(), 'Workload label is missing or denotes idle')
        require(local_metadata.get('workload_label') == label, 'Metadata and orchestration workload labels differ')
        require(local_metadata.get('guest_returncode') == 0, 'Local guest probe return code is not zero')
        require(not local_metadata.get('memory_monitor_error'), 'Memory monitor reported failure')
        return {'utc_span_seconds': finish - start, 'elapsed_seconds': orchestration.get('elapsed_seconds'),
                'utc_minus_monotonic_elapsed_seconds': (finish - start) - number(orchestration['elapsed_seconds']),
                'workload_label': label}
    section('orchestration', orchestration_check)
    def network_check():
        result = {}
        for group, directory in (('local', soak), ('wan', soak / 'wan')):
            meta = load_json(directory / 'metadata.json')
            summary = load_json(directory / 'network_summary.json')
            record_file(directory / 'metadata.json'); record_file(directory / 'network_summary.json')
            require(meta.get('duration') == DURATION, f'{group}: metadata duration is not 7200 seconds')
            require(meta.get('emulator_pid') == local_metadata.get('emulator_pid'), f'{group}: emulator PID differs')
            require(meta.get('guest_returncode') == 0, f'{group}: guest probe did not exit zero')
            require(summary.get('status') == 'passed', f'{group}: original network summary did not pass')
            require(set(summary.get('results', {})) == {'host', 'guest'}, f'{group}: summary lacks exactly host and guest')
            for who in ('host', 'guest'):
                label = f'{group}.{who}'
                original = summary['results'][who]
                path = directory / f'{who}_network.jsonl'
                events = load_events(path); record_file(path)
                require(original.get('status') == 'passed', f'{label}: original result did not pass')
                require(original.get('expected_duration_seconds') == DURATION, f'{label}: summary expected duration differs')
                for flag in ('duration_ok', 'heartbeat_coverage_ok', 'heartbeat_ack_count_ok', 'heartbeat_ack_gaps_ok', 'completed'):
                    require(original.get(flag) is True, f'{label}: {flag} is not true')
                require(len(events) >= 3, f'{label}: missing raw event window')
                times = [int(e['monotonic_ns']) for e in events]
                require(all(b >= a for a, b in zip(times, times[1:])), f'{label}: raw event clock regresses')
                span = (times[-1] - times[0]) / 1e9
                require(span >= DURATION - NETWORK_WINDOW_TOLERANCE,
                        f'{label}: raw event window {span:g}s is shorter than a complete 7200s run')
                require(abs(number(original['window_seconds']) - span) <= 0.001, f'{label}: raw window and summary differ')
                config = [e for e in events if e.get('event') == 'network_config']
                require(len(config) == 1, f'{label}: expected one network config')
                if config:
                    required = {'duration_seconds': 7200, 'interval_ms': 1000,
                                'payload_bytes': 65536 if group == 'local' else 64,
                                'bulk_bytes': 32 * 1024**2 if group == 'local' else 0,
                                'payload_framing': 'binary' if group == 'local' else 'line64',
                                'endpoint_ipv4': meta.get('host_endpoint' if who == 'host' else 'guest_endpoint'),
                                'endpoint_port': meta.get('echo_port')}
                    for key, value in required.items():
                        require(config[0].get(key) == value, f'{label}: raw network_config {key} differs')
                    require(events[0] is config[0], f'{label}: raw stream does not begin with network_config')
                require(events[-1].get('event') == 'network_complete', f'{label}: raw stream does not finish with network_complete')
                errors = [e for e in events if e.get('event') in ('heartbeat_error', 'bulk_error', 'connect_error', 'socket_error')]
                require(not errors, f'{label}: raw TCP errors exist')
                acks = [e for e in events if e.get('event') == 'heartbeat_ack']
                require(len(acks) == original.get('acks'), f'{label}: raw ACK count differs from summary')
                require(bool(acks), f'{label}: raw heartbeat ACKs missing')
                expected_size = 65536 if group == 'local' else 64
                require(all(e.get('sent_bytes') == expected_size and e.get('received_bytes') == expected_size and not e.get('errno') for e in acks),
                        f'{label}: incomplete/failed heartbeat payload claimed as ACK')
                ack_gaps = [(b['monotonic_ns'] - a['monotonic_ns']) / 1e9 for a, b in zip(acks, acks[1:])]
                require(bool(ack_gaps) and all(0 <= gap <= 3 for gap in ack_gaps), f'{label}: raw ACK gap is invalid or over 3 seconds')
                result[label] = {'window_seconds': span, 'acks': len(acks), 'maximum_ack_gap_seconds': max(ack_gaps, default=None)}
                if who == 'host' and len(config) == 1 and events[-1].get('event') == 'network_complete':
                    host_windows[group] = (int(config[0]['monotonic_ns']) / 1e9, times[-1] / 1e9)
                    wall_events = [e for e in events if e.get('timestamp_utc')]
                    result[label]['clock_residuals'] = clock_residuals(
                        [timestamp(e['timestamp_utc']) for e in wall_events],
                        [int(e['monotonic_ns']) / 1e9 for e in wall_events])
        return {'clients': result}
    section('network', network_check)
    def reference_check():
        # run_soak creates MemoryMonitor after orchestration starts and before
        # either host client, then records elapsed after both clients complete.
        # These ordering facts bound the missing absolute monitor/orchestrator
        # origins without aligning either stream through adjustable UTC labels.
        require(set(host_windows) == {'local', 'wan'}, 'Both host monotonic network anchors are required')
        first = min(window[0] for window in host_windows.values())
        last = max(window[1] for window in host_windows.values())
        elapsed = number(orchestration['elapsed_seconds'])
        overhead = elapsed - (last - first)
        require(overhead >= -ELAPSED_ROUNDING_TOLERANCE,
                'Host monotonic network envelope exceeds orchestration elapsed time')
        overhead = max(0, overhead)
        clock_reference.update(host_window_start=first, host_window_finish=last,
                               earliest_orchestration_start=first - overhead,
                               latest_orchestration_finish=last + overhead,
                               monitor_origin_offset_upper_bound=overhead)
        return {**clock_reference, 'timebase': 'Host monotonic seconds; guest monotonic clocks are not aligned',
                'basis': 'Source lifecycle ordering bounds the unrecorded orchestration and memory-monitor origins. UTC is diagnostic only.'}
    section('monotonic_reference', reference_check)
    def memory_check():
        path = soak / 'memory.csv'
        with path.open(newline='') as stream:
            rows = list(csv.DictReader(stream))
        record_file(path)
        require(len(rows) >= math.floor(DURATION / MEMORY_INTERVAL * 0.99), 'Memory sample count is below 99% of the expected 5-second schedule')
        times = [number(row['elapsed_seconds']) for row in rows]
        wall = [timestamp(row['timestamp_utc']) for row in rows]
        gaps = [b - a for a, b in zip(times, times[1:])]
        require(bool(gaps) and all(0 < gap <= MEMORY_MAX_GAP + 1e-6 for gap in gaps), 'Memory timestamps regress/duplicate or sample gap exceeds 10 seconds')
        span = times[-1] - times[0] if times else 0
        require(span >= MEMORY_MIN_SPAN, 'Memory sample span is below 7195 seconds')
        elapsed = number(orchestration['elapsed_seconds'])
        overhead = clock_reference['monitor_origin_offset_upper_bound']
        first_upper = times[0] + overhead if times else None
        tail_upper = elapsed - times[-1] if times else None
        require(first_upper is not None and times[0] >= -ELAPSED_ROUNDING_TOLERANCE
                and first_upper <= FIRST_BOUNDARY_TOLERANCE + ELAPSED_ROUNDING_TOLERANCE,
                'First memory sample cannot be proven within 5 seconds after orchestration start by monotonic bounds')
        require(tail_upper is not None and -ELAPSED_ROUNDING_TOLERANCE <= tail_upper
                <= LAST_BOUNDARY_TOLERANCE + ELAPSED_ROUNDING_TOLERANCE,
                'Last memory sample cannot be proven within 6 seconds of orchestration finish by monotonic bounds')
        missing, errors, wrong_phase = [], [], []
        for index, row in enumerate(rows, 2):
            if row.get('host_memory_error') or row.get('guest_memory_error'):
                errors.append(index)
            if row.get('phase') != orchestration.get('workload_label'):
                wrong_phase.append(index)
            for key in CORE_METRICS:
                try:
                    value = number(row.get(key, ''))
                    if value < 0 or (key in ('host_rss_bytes', 'host_phys_footprint_bytes', 'guest_MemTotal_bytes') and value <= 0):
                        raise ValueError('Invalid sign/value')
                except (ValueError, TypeError):
                    missing.append({'line': index, 'metric': key})
        require(not missing, f'Memory key metric absent/invalid in {len(missing)} row-fields')
        require(not errors, f'Memory host/guest errors in {len(errors)} rows')
        require(not wrong_phase, f'Memory phase differs from game workload in {len(wrong_phase)} rows')
        return {'sample_count': len(rows), 'span_seconds': span, 'maximum_gap_seconds': max(gaps, default=None),
                'first_sample_after_orchestration_start_upper_bound_seconds': first_upper,
                'orchestration_finish_after_last_sample_upper_bound_seconds': tail_upper,
                'monitor_origin_offset_upper_bound_seconds': overhead,
                'boundary_basis': 'Relative monitor elapsed plus conservative host-network lifecycle bounds; no absolute memory timestamp was recorded',
                'utc_first_sample_after_orchestration_start_seconds': wall[0] - start if wall else None,
                'utc_orchestration_finish_after_last_sample_seconds': finish - wall[-1] if wall else None,
                'missing_or_invalid_metrics_first_20': missing[:20], 'error_rows_first_20': errors[:20],
                'wrong_phase_rows_first_20': wrong_phase[:20], 'clock_residuals': clock_residuals(wall, times)}
    section('memory', memory_check)
    def workload_check():
        report = load_json(observer / 'observer.json')
        events = load_events(observer / 'events.jsonl')
        record_file(observer / 'observer.json'); record_file(observer / 'events.jsonl')
        samples = [e for e in events if e.get('event') == 'game_sample']
        require(report.get('status') == 'passed', 'Game observer did not pass')
        observer_duration = number(report.get('duration_seconds', 0))
        require(observer_duration >= DURATION, 'Game observer declared duration is shorter than 7200 seconds')
        require(report.get('sample_interval_seconds') == 60, 'Game observer interval is not 60 seconds')
        require(report.get('screenshot_interval_seconds') == 300, 'Game screenshot interval is not 300 seconds')
        require(report.get('full_observation_window') is True and report.get('stop_reason') == 'duration_complete', 'Game observer did not complete its full window')
        require(not report.get('errors') and not any(e.get('event') == 'observer_error' for e in events), 'Game observer contains errors')
        require(report.get('package') == package, 'Game observer package differs from expected package')
        require(report.get('expected_avd') == expected_avd, 'Game observer AVD differs from expected game instance')
        require(report.get('emulator_pid') == local_metadata.get('emulator_pid') and isinstance(report.get('emulator_pid'), int), 'Observer and soak emulator PID differ')
        identity = report.get('emulator_identity', '')
        require(str(ROOT / 'sdk/emulator/qemu/darwin-aarch64/') in identity and 'qemu-system-aarch64' in identity,
                'Observer lacks task-owned official arm64 emulator command line')
        require(bool(re.search(r'(?:^|\s)-avd\s+' + re.escape(expected_avd) + r'(?:\s|$)', identity)), 'Observer emulator command line has the wrong AVD')
        require(bool(re.search(r'(?:^|\s)-no-window(?:\s|$)', identity)), 'Game soak was not observed on a headless emulator')
        server_command = ' '.join([str(ROOT / 'sdk/platform-tools/adb'), '-L', f'tcp:{local_metadata["adb_port"]}',
                                   '--one-device', local_metadata['serial'], 'server', 'nodaemon'])
        require(report.get('adb_server_identity', '').endswith(server_command), 'Observer private ADB server identity differs from the soak transport')
        require(len(samples) == report.get('sample_count') and len(samples) >= math.ceil(observer_duration / 60) + 1, 'Game sample count is inconsistent or below its declared 60-second schedule')
        points = [int(e['monotonic_ns']) / 1e9 for e in samples]
        wall = [timestamp(e['timestamp_utc']) for e in samples]
        gaps = [b - a for a, b in zip(points, points[1:])]
        require(bool(gaps) and all(0 < gap <= 80 for gap in gaps), 'Game observer gap exceeds 80 seconds or clock is invalid')
        require(bool(points) and points[-1] - points[0] >= observer_duration - 0.1, 'Game samples do not span the full declared observation duration')
        first_offset = points[0] - clock_reference['earliest_orchestration_start'] if points else None
        tail_offset = clock_reference['latest_orchestration_finish'] - points[-1] if points else None
        require(first_offset is not None and first_offset <= FIRST_BOUNDARY_TOLERANCE,
                'Game samples begin over 5 seconds after the conservative monotonic orchestration start')
        require(tail_offset is not None and tail_offset <= LAST_BOUNDARY_TOLERANCE,
                'Game samples end over 6 seconds before the conservative monotonic orchestration finish')
        for index, event in enumerate(samples):
            require(event.get('main_process_alive') is True and event.get('foreground') is True and event.get('foreground_observable') is True,
                    f'Game sample {index}: main process missing or game not observably foreground')
            processes = event.get('processes', [])
            require(any(p.get('exact_main_process') is True and p.get('cmdline') == package and p.get('read_returncode') == 0
                        and not p.get('read_error') and isinstance(p.get('pid'), int) and p['pid'] > 0 for p in processes),
                    f'Game sample {index}: no exact package/cmdline/PID evidence')
            require(any(re.search(r'(?<![\w.])' + re.escape(package) + r'/', line) for line in event.get('resumed_activity_lines', [])),
                    f'Game sample {index}: foreground flag lacks resumed-activity evidence')
        screens = [e for e in events if e.get('event') == 'private_screenshot']
        screen_times = []
        for event in screens:
            filename = event.get('path', '')
            require(Path(filename).name == filename, 'Private screenshot path escapes observer directory')
            if Path(filename).name != filename:
                continue
            path = observer / filename
            raw = path.read_bytes(); record_file(path)
            require(raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw) == event.get('bytes') and hashlib.sha256(raw).hexdigest() == event.get('sha256'),
                    'Private screenshot bytes/hash do not match receipt')
            screen_times.append(int(event['monotonic_ns']) / 1e9)
        require(len(screens) >= 25, 'Fewer than the 25 expected private start/300-second/end screenshots')
        require(bool(screen_times) and points and -1 <= screen_times[0] - points[0] <= 30
                and -1 <= screen_times[-1] - points[-1] <= 30, 'Private screenshots do not cover start and end game samples')
        require(all(0 < b - a <= 330 for a, b in zip(screen_times, screen_times[1:])), 'Private screenshot gap exceeds 330 seconds or timestamps regress')
        return {'sample_count': len(samples), 'sample_span_seconds': points[-1] - points[0] if points else 0,
                'maximum_gap_seconds': max(gaps, default=None), 'private_screenshot_count': len(screens),
                'first_sample_after_orchestration_start_upper_bound_seconds': first_offset,
                'orchestration_finish_after_last_sample_upper_bound_seconds': tail_offset,
                'utc_first_sample_after_orchestration_start_seconds': wall[0] - start if wall else None,
                'utc_orchestration_finish_after_last_sample_seconds': finish - wall[-1] if wall else None,
                'clock_residuals': clock_residuals(wall, points)}
    section('game_workload', workload_check)
    return {'status': 'passed' if not reasons else 'failed', 'validated_utc': utc(),
            'soak_directory': str(soak), 'observer_directory': str(observer), 'expected_avd': expected_avd,
            'expected_package': package, 'duration_seconds': DURATION, 'checks': checks, 'failure_reasons': reasons,
            'criteria': {'memory_minimum_span_seconds': MEMORY_MIN_SPAN, 'memory_maximum_gap_seconds': MEMORY_MAX_GAP,
                         'memory_interval_seconds': MEMORY_INTERVAL, 'memory_core_metrics': list(CORE_METRICS),
                         'first_memory_boundary_tolerance_seconds': FIRST_BOUNDARY_TOLERANCE,
                         'last_memory_boundary_tolerance_seconds': LAST_BOUNDARY_TOLERANCE,
                         'network_window_tolerance_seconds': NETWORK_WINDOW_TOLERANCE,
                         'memory_elapsed_serialization_tolerance_seconds': ELAPSED_ROUNDING_TOLERANCE,
                         'utc_policy': 'Labels and residual diagnostics only; all elapsed, gap, and cross-stream coverage gates use monotonic evidence'},
            'evidence_files': evidence,
            'interpretation': 'Evidence acquisition and sampled game presence/foreground validation. TCP clients are auxiliary controls, not game UID traffic. No memory-leak threshold or game server continuity is established. Private screenshots require separate game-state interpretation.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--soak', type=Path, required=True)
    parser.add_argument('--observer', type=Path, required=True)
    parser.add_argument('--expected-avd', required=True)
    parser.add_argument('--package', default='com.hypergryph.arknights.bilibili')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = validate(args.soak, args.observer, expected_avd=args.expected_avd, package=args.package)
    output = args.output or args.soak / 'validation.json'
    protected = {args.soak.resolve() / name for name in ('orchestration.json', 'metadata.json', 'memory.csv', 'network_summary.json')}
    if output.resolve() in protected or output.name != 'validation.json':
        parser.error('Output must be an independent validation.json; source evidence cannot be overwritten')
    # Every run gets a new immutable validation result; choose a fresh output folder to repeat.
    with output.open('x') as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'validation': str(output.resolve()), 'failure_reasons': report['failure_reasons']}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
