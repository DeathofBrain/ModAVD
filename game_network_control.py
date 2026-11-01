#!/usr/bin/env python3
"""Bounded host/guest remote echo control; no emulator/server or memory sampler.

The caller deploys the framing-aware probe and keeps the emulator running.
SIGINT/SIGTERM preserve partial evidence; they never count as a completed soak.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import threading
import time

import soak_tests as s
from run_soak import validate_preflight


def paired(args, directory, duration, cancel, reason):
    directory.mkdir(exist_ok=False)
    group = argparse.Namespace(**{**vars(args), 'duration': duration,
        'host_endpoint': args.wan_ipv4, 'guest_endpoint': args.wan_ipv4,
        'payload_bytes': 64, 'bulk_bytes': 0, 'heartbeat_ms': 1000,
        'payload_framing': 'line'})
    metadata = {**vars(group), 'output': str(directory), 'started_utc': s.utc(),
        'guest_payload': s.REMOTE, 'memory_monitor_count': 0,
        'network_scope': f'Paired remote {args.wan_hostname}:{args.wan_port} at {args.wan_ipv4}; current host routing may include FakeIP/TUN/proxy; direct Internet routing not established.',
        'phase': 'download_network_control', 'status': 'running'}
    s.write_json(directory / 'metadata.json', metadata)
    stop = threading.Event()
    host_error = []
    reader_error = []
    pid = []
    adb = s.Adb(args)
    guest_argv = [s.REMOTE, 'tcp', args.wan_ipv4, str(args.wan_port),
                  str(duration), '1000', '64', '0', 'line64']
    command = 'echo __AVD_PROBE_PID__ $$; exec ' + ' '.join(guest_argv)
    metadata['guest_command'] = adb.prefix + ['shell', command]
    started = time.monotonic()
    proc = None
    reader = None
    host = None
    interrupted = False
    with (directory / 'guest_network.jsonl').open('x') as guest_log, \
         (directory / 'guest_network.stderr.log').open('x') as guest_err:
        def host_run():
            try:
                s.host_tcp(group, args.wan_port, directory / 'host_network.jsonl', stop)
            except BaseException as exc:
                host_error.append(repr(exc))

        def read_guest():
            try:
                for line in proc.stdout:
                    match = re.fullmatch(r'__AVD_PROBE_PID__ (\d+)\s*', line)
                    if match and not pid:
                        pid.append(int(match.group(1)))
                    else:
                        guest_log.write(line)
                        guest_log.flush()
            except BaseException as exc:
                reader_error.append(repr(exc))

        try:
            host = threading.Thread(target=host_run, name='download-host-echo', daemon=True)
            host.start()
            proc = subprocess.Popen(metadata['guest_command'], stdout=subprocess.PIPE,
                                    stderr=guest_err, text=True)
            reader = threading.Thread(target=read_guest, name='download-guest-log', daemon=True)
            reader.start()
            while True:
                elapsed = time.monotonic() - started
                free = shutil.disk_usage(directory).free
                metadata['minimum_host_disk_free_bytes'] = min(
                    free, metadata.get('minimum_host_disk_free_bytes', free))
                if free < args.reserve_gib * 1024**3:
                    reason['reason'] = 'disk_reserve_below_threshold'
                    cancel.set()
                if cancel.is_set():
                    interrupted = True
                    break
                if proc.poll() is not None and not host.is_alive():
                    break
                if elapsed > duration + 45:
                    reason['reason'] = 'clients_exceeded_duration_grace'
                    interrupted = True
                    break
                cancel.wait(.25)
        finally:
            stop.set()
            # Kill only the exact probe PID after checking its current cmdline.
            # Never pkill by name and never stop the emulator or ADB server.
            if proc is not None and proc.poll() is None and pid:
                try:
                    identity = adb.run(['shell', f'cat /proc/{pid[0]}/cmdline'], timeout=5, check=False)
                    actual = identity.stdout.rstrip('\r\n').split('\x00')
                    while actual and not actual[-1]: actual.pop()
                    metadata['guest_identity_matched_for_cleanup'] = identity.returncode == 0 and actual == guest_argv
                    if metadata['guest_identity_matched_for_cleanup']:
                        killed = adb.run(['shell', 'kill', '-TERM', str(pid[0])], timeout=5, check=False)
                        metadata['guest_probe_sigterm_returncode'] = killed.returncode
                    else:
                        metadata['guest_cleanup_warning'] = 'PID identity differed or was unavailable; no guest kill sent'
                except Exception as exc:
                    metadata['guest_cleanup_error'] = repr(exc)
            if proc is not None:
                try: proc.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired: proc.kill(); proc.wait()
            if reader is not None: reader.join(timeout=5)
            if host is not None: host.join(timeout=15)
            metadata.update(finished_utc=s.utc(), elapsed_seconds=time.monotonic() - started,
                guest_returncode=proc.returncode if proc is not None else None,
                guest_probe_pid=pid[0] if pid else None, interrupted=interrupted,
                stop_reason=reason['reason'] if interrupted else 'duration_complete',
                host_thread_errors=host_error, guest_reader_errors=reader_error,
                host_thread_still_alive=host.is_alive() if host is not None else False)
            s.write_json(directory / 'metadata.json', metadata)
    result = s.summarize_network(directory)
    status = json.loads((directory / 'network_summary.json').read_text())['status']
    status = 'partial' if interrupted else 'failed' if host_error or reader_error else status
    receipt = {'status': status, 'interrupted': interrupted,
        'elapsed_seconds': metadata['elapsed_seconds'], 'duration_seconds': duration,
        'stop_reason': metadata['stop_reason'], 'clients': result,
        'measurement_scope': 'Network-only control during windowed download/login; not a 2-hour game observation and not game UID traffic.'}
    s.write_json(directory / 'receipt.json', receipt)
    return receipt


def run(args):
    args.wan_ipv4 = str(ipaddress.IPv4Address(args.wan_ipv4 or socket.gethostbyname(args.wan_hostname)))
    out = Path(args.output).resolve()
    if shutil.disk_usage(out.parent).free < args.reserve_gib * 1024**3:
        raise RuntimeError('Free space is below the configured reserve; no probe started')
    out.mkdir(exist_ok=False)
    cancel = threading.Event()
    reason = {'reason': 'none'}
    def stop(signum, _frame):
        reason['reason'] = signal.Signals(signum).name
        cancel.set()
    old = {sig: signal.signal(sig, stop) for sig in (signal.SIGINT, signal.SIGTERM)}
    report = {'started_utc': s.utc(), 'status': 'running', 'preflight_rounds': [],
              'duration_seconds': args.duration, 'memory_monitor_count': 0,
              'scope': 'Auxiliary paired remote connection during download, maximum 3600 seconds; not a full 2-hour test.',
              'disk_reserve_gib': args.reserve_gib,
              'disk_guard_note': 'Stops only this helper probes below reserve; emulator lifecycle/20 GiB sync-stop remains the caller Runtime watchdog responsibility.'}
    s.write_json(out / 'control.json', report)
    try:
        selected = None
        for number in range(1, args.preflight_rounds + 1):
            directory = out / f'preflight-{number}'
            receipt = paired(args, directory, 10, cancel, reason)
            validation = {}
            if not cancel.is_set():
                for who in ('host', 'guest'):
                    try:
                        validation[who] = {'status': 'passed', 'receipt': validate_preflight(
                            directory / f'{who}_network.jsonl', expected_ipv4=args.wan_ipv4,
                            expected_port=args.wan_port)}
                    except Exception as exc:
                        validation[who] = {'status': 'failed', 'error': str(exc)}
            report['preflight_rounds'].append({'directory': str(directory),
                'execution_status': receipt['status'], 'validation': validation})
            if selected is None and validation and all(v['status'] == 'passed' for v in validation.values()):
                selected = str(directory)
            s.write_json(out / 'control.json', report)
            if cancel.is_set(): break
        report['selected_preflight'] = selected
        if cancel.is_set(): report['status'] = 'partial'
        elif selected:
            report['observation'] = paired(args, out / 'observation', args.duration, cancel, reason)
            report['status'] = report['observation']['status']
        else:
            report.update(status='blocked', error='No clean paired preflight; all raw attempts retained')
    finally:
        for sig, handler in old.items(): signal.signal(sig, handler)
        report.update(finished_utc=s.utc(), stop_reason=reason['reason'])
        s.write_json(out / 'control.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb', default='${WORKSPACE}/sdk/platform-tools/adb')
    p.add_argument('--adb-port', type=int, default=22150)
    p.add_argument('--serial', default='emulator-22160')
    p.add_argument('--output', required=True)
    p.add_argument('--duration', type=int, default=3600)
    p.add_argument('--preflight-rounds', type=int, default=1)
    p.add_argument('--wan-hostname', default='tcpbin.com')
    p.add_argument('--wan-ipv4')
    p.add_argument('--wan-port', type=int, default=4242)
    p.add_argument('--reserve-gib', type=float, default=20)
    args = p.parse_args()
    if not 1 <= args.duration <= 3600: p.error('duration must be 1..3600 seconds')
    if not 1 <= args.preflight_rounds <= 3: p.error('preflight rounds must be 1..3')
    if not 1 <= args.wan_port <= 65535: p.error('port must be 1..65535')
    if args.reserve_gib < 20: p.error('reserve must be at least 20 GiB')
    run(args)


if __name__ == '__main__':
    main()
