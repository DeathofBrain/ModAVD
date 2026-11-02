#!/usr/bin/env python3
"""Read-only game liveness evidence for an already-owned official AVD.

Uses the existing private ADB server's smart socket directly, so a missing server
can never cause this helper to start ADB. No app start, input, root, state change,
process kill, memory sampling, or emulator lifecycle operation is performed.
Screenshots and detailed activity/process evidence remain in a mode-0700 folder.
A passing result establishes sampled presence/foreground, not game connectivity.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import struct
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parent


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def private_write(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)


def save_json(path, value):
    data = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    temp = path.with_suffix(path.suffix + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
    temp.replace(path)


def exact_read(sock, size):
    data = bytearray()
    while len(data) < size:
        block = sock.recv(size - len(data))
        if not block:
            raise RuntimeError('Private ADB socket closed before its complete response')
        data.extend(block)
    return bytes(data)


def adb_request(sock, service):
    raw = service.encode('utf-8')
    sock.sendall(f'{len(raw):04x}'.encode('ascii') + raw)
    status = exact_read(sock, 4)
    if status == b'FAIL':
        size = int(exact_read(sock, 4), 16)
        raise RuntimeError('ADB FAIL: ' + exact_read(sock, size).decode('utf-8', 'replace'))
    if status != b'OKAY':
        raise RuntimeError(f'Unexpected ADB status {status!r}')


def shell_v2(port, serial, command, timeout=15):
    """Read a bounded command through an already-running server; never autostart."""
    stdout, stderr = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    with socket.create_connection(('127.0.0.1', port), timeout=min(timeout, 5)) as sock:
        adb_request(sock, 'host:transport:' + serial)
        adb_request(sock, 'shell,v2,raw:' + command)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('ADB shell total deadline exceeded')
            sock.settimeout(remaining)
            channel, size = struct.unpack('<BI', exact_read(sock, 5))
            if size > 16 * 1024**2:
                raise RuntimeError('Unbounded ADB shell frame')
            data = exact_read(sock, size)
            if channel == 1:
                stdout.extend(data)
            elif channel == 2:
                stderr.extend(data)
            elif channel == 3:
                if len(data) != 1:
                    raise RuntimeError('Unexpected ADB shell exit payload')
                return data[0], bytes(stdout), bytes(stderr)
            else:
                raise RuntimeError(f'Unexpected ADB shell channel {channel}')
            if len(stdout) + len(stderr) > 16 * 1024**2:
                raise RuntimeError('ADB shell output exceeds 16 MiB')


def ps_identity(pid):
    cp = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'command='],
                        capture_output=True, text=True, timeout=5, check=False)
    if cp.returncode or not cp.stdout.strip():
        raise RuntimeError(f'Owned process {pid} is no longer alive')
    return cp.stdout.strip()


def validate_ownership(args, state, adb_state, emulator_identity, server_identity):
    current = json.loads(args.state.read_text())
    current_adb = json.loads(args.adb_server_state.read_text())
    if current != state or current_adb != adb_state:
        raise RuntimeError('Ownership receipt changed; refusing to observe another AVD/server')
    if state.get('pid') != args.emulator_pid or state.get('avd') != args.expected_avd:
        raise RuntimeError('PID/AVD differs from caller-requested game instance')
    if state.get('serial') != args.serial or state.get('adb_port') != args.adb_port:
        raise RuntimeError('Private ADB transport differs from the owned emulator receipt')
    qemu = str(ROOT / 'sdk/emulator/qemu/darwin-aarch64/')
    if qemu not in emulator_identity or 'qemu-system-aarch64' not in emulator_identity:
        raise RuntimeError('Emulator is not the task-owned official arm64 runtime')
    argv = state.get('args', [])
    avd_index = argv.index('-avd') if '-avd' in argv else -1
    if avd_index < 0 or argv[avd_index + 1:avd_index + 2] != [args.expected_avd]:
        raise RuntimeError('Owned emulator command line lacks the exact requested AVD')
    if state.get('identity') != emulator_identity or ps_identity(args.emulator_pid) != emulator_identity:
        raise RuntimeError('Emulator PID/start time/command line identity changed')
    adb_path = str(ROOT / 'sdk/platform-tools/adb')
    expected = [adb_path, '-L', f'tcp:{args.adb_port}', '--one-device', args.serial, 'server', 'nodaemon']
    if adb_state.get('path') != adb_path or adb_state.get('port') != args.adb_port or adb_state.get('command') != expected:
        raise RuntimeError('Private ADB server receipt is not task-owned and restricted to this serial')
    if not server_identity.endswith(' '.join(expected)) or ps_identity(adb_state['pid']) != server_identity:
        raise RuntimeError('Private ADB server PID/start time/command line identity changed')


def collect(args, state, adb_state, emulator_identity, server_identity):
    validate_ownership(args, state, adb_state, emulator_identity, server_identity)
    began = time.monotonic()
    record = {'event': 'game_sample', 'timestamp_utc': utc(), 'monotonic_ns': time.monotonic_ns()}
    rc, raw, err = shell_v2(args.adb_port, args.serial, 'pidof ' + args.package)
    if rc not in (0, 1) or err:
        raise RuntimeError('pidof failed: ' + err.decode('utf-8', 'replace'))
    pids = raw.decode('ascii').split()
    if any(not re.fullmatch(r'[1-9][0-9]*', pid) for pid in pids):
        raise RuntimeError('pidof returned an invalid PID')
    processes = []
    for pid in pids:
        rc, cmd, err = shell_v2(args.adb_port, args.serial, f'cat /proc/{pid}/cmdline', timeout=8)
        # The main process must match exactly. Child/service processes alone do not establish the game is alive.
        cmdline = cmd.split(b'\0')[0].decode('utf-8', 'replace')
        processes.append({'pid': int(pid), 'cmdline': cmdline,
                          'exact_main_process': rc == 0 and not err and cmdline == args.package,
                          'read_returncode': rc, 'read_error': err.decode('utf-8', 'replace')})
    rc, raw, err = shell_v2(args.adb_port, args.serial, 'dumpsys activity activities')
    if rc or err:
        raise RuntimeError('dumpsys activity failed: ' + err.decode('utf-8', 'replace'))
    lines = [line.strip() for line in raw.decode('utf-8', 'replace').splitlines()
             if re.search(r'(?:mResumedActivity|topResumedActivity|ResumedActivity)\s*[:=]', line)]
    foreground = any(re.search(r'(?<![\w.])' + re.escape(args.package) + r'/', line) for line in lines)
    record.update(processes=processes, main_process_alive=any(p['exact_main_process'] for p in processes),
                  foreground=foreground, resumed_activity_lines=lines,
                  foreground_observable=bool(lines), sample_duration_ms=(time.monotonic() - began) * 1000)
    validate_ownership(args, state, adb_state, emulator_identity, server_identity)
    return record


def run(args):
    if args.output.exists():
        raise FileExistsError('Choose a fresh observer --output directory')
    state = json.loads(args.state.read_text())
    adb_state = json.loads(args.adb_server_state.read_text())
    emulator_identity = ps_identity(args.emulator_pid)
    server_identity = ps_identity(adb_state['pid'])
    validate_ownership(args, state, adb_state, emulator_identity, server_identity)
    args.output.mkdir(parents=True, mode=0o700)
    args.output.chmod(0o700)
    cancel = threading.Event()
    stop_reason = {'value': 'duration_complete'}
    def stop(signum, _frame):
        stop_reason['value'] = signal.Signals(signum).name
        cancel.set()
    previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    started = time.monotonic()
    deadline = started + args.duration
    next_sample, next_screen = started, started
    samples, errors = [], []
    report = {'status': 'running', 'started_utc': utc(), 'started_monotonic_ns': time.monotonic_ns(),
              'duration_seconds': args.duration, 'sample_interval_seconds': args.interval,
              'screenshot_interval_seconds': args.screenshot_interval, 'package': args.package,
              'expected_avd': args.expected_avd, 'emulator_pid': args.emulator_pid,
              'emulator_identity': emulator_identity, 'adb_server_identity': server_identity,
              'private_screenshots': True,
              'scope': 'Sampled game main-process and resumed-activity presence. No proof of logged-in state, server reachability, gameplay, or uninterrupted presence between samples.'}
    save_json(args.output / 'observer.json', report)
    event_fd = os.open(args.output / 'events.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(event_fd, 'w') as log:
            while not cancel.is_set():
                now = time.monotonic()
                if now < next_sample:
                    cancel.wait(min(next_sample - now, 1))
                    continue
                try:
                    sample = collect(args, state, adb_state, emulator_identity, server_identity)
                    sample['elapsed_seconds'] = (sample['monotonic_ns'] / 1e9) - started
                    samples.append(sample)
                    log.write(json.dumps(sample, ensure_ascii=False) + '\n'); log.flush()
                    if now >= next_screen or now >= deadline:
                        validate_ownership(args, state, adb_state, emulator_identity, server_identity)
                        rc, raw, err = shell_v2(args.adb_port, args.serial, 'screencap -p', timeout=20)
                        if rc or err or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
                            raise RuntimeError('screencap did not return a valid PNG')
                        image = args.output / f'screen-{sample["monotonic_ns"]}.png'
                        private_write(image, raw)
                        validate_ownership(args, state, adb_state, emulator_identity, server_identity)
                        log.write(json.dumps({'event': 'private_screenshot', 'timestamp_utc': utc(),
                            'monotonic_ns': time.monotonic_ns(), 'path': image.name,
                            'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}) + '\n'); log.flush()
                        next_screen = started + (int((now - started) // args.screenshot_interval) + 1) * args.screenshot_interval
                except Exception as exc:
                    error = {'event': 'observer_error', 'timestamp_utc': utc(),
                             'monotonic_ns': time.monotonic_ns(), 'error': repr(exc)}
                    errors.append(error); log.write(json.dumps(error) + '\n'); log.flush()
                    stop_reason['value'] = 'observation_error'
                    break
                if now >= deadline:
                    break
                next_sample = min(deadline, started + (int((time.monotonic() - started) // args.interval) + 1) * args.interval)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        elapsed = time.monotonic() - started
        gaps = [(b['monotonic_ns'] - a['monotonic_ns']) / 1e9 for a, b in zip(samples, samples[1:])]
        coverage = bool(samples) and samples[0]['elapsed_seconds'] <= 10 and samples[-1]['elapsed_seconds'] >= args.duration and max(gaps, default=0) <= args.interval + 20
        all_present = bool(samples) and all(e['main_process_alive'] and e['foreground'] for e in samples)
        report.update(finished_utc=utc(), elapsed_seconds=elapsed, stop_reason=stop_reason['value'],
                      sample_count=len(samples), maximum_sample_gap_seconds=max(gaps, default=None),
                      full_observation_window=coverage, main_process_missing_samples=sum(not e['main_process_alive'] for e in samples),
                      not_foreground_samples=sum(not e['foreground'] for e in samples), errors=errors)
        report['status'] = 'passed' if coverage and all_present and not errors and not cancel.is_set() else 'failed' if errors or not all_present else 'partial'
        save_json(args.output / 'observer.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT / 'state.json')
    parser.add_argument('--adb-server-state', type=Path, default=ROOT / 'adb-server.json')
    parser.add_argument('--emulator-pid', type=int, required=True)
    parser.add_argument('--expected-avd', required=True)
    parser.add_argument('--package', default='com.hypergryph.arknights.bilibili')
    parser.add_argument('--adb-port', type=int, default=22150)
    parser.add_argument('--serial', default='emulator-22160')
    parser.add_argument('--duration', type=float, default=7200)
    parser.add_argument('--interval', type=float, default=60)
    parser.add_argument('--screenshot-interval', type=float, default=300)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', args.package):
        parser.error('Invalid Android package name')
    if min(args.duration, args.interval, args.screenshot_interval) <= 0:
        parser.error('Duration and intervals must be positive')
    if args.emulator_pid <= 0 or not 1 <= args.adb_port <= 65535:
        parser.error('Invalid PID or port')
    print(json.dumps(run(args), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
