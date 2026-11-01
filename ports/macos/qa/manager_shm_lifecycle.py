#!/usr/bin/env python3
"""Exercise SHM start/stop on a dedicated, package-owned QA guest.

This intentionally controls that QA instance's Manager stream. It restores a
started stream and the HOME activity. No MAA or game instance is accessed.
Native contract QA observes internal reference counts; this live runner only
observes the public console and shared-memory behavior.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

import capture_manager as capture
from manager_shm_matrix import parse_screencap


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--index', type=int, required=True)
    parser.add_argument('--adb-port', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    capture.SERVER_PORT = args.adb_port
    capture.frames.runtime.ADB_PORT = args.adb_port
    owner = capture.ManagerCapture(args.root, args.index)
    report = {'schema': 1, 'ok': False, 'internalRefcountObserved': False,
              'scope': 'dedicated QA guest console and SHM lifecycle; no pixel parity or tear-free claim',
              'shmReadinessMeaning': 'Manager shm_ready only tests mapping existence, including after stop',
              'runnerSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'checks': {}}
    (args.output / 'runner.py').write_bytes(Path(__file__).read_bytes())
    checks = report['checks']

    def adb(*argv):
        owner.owned()
        return owner.adb_command(*argv)

    def command(action):
        result = adb('emu', 'screenrecord', 'webrtc', action).stdout.split()
        if result != ([f'videmulator{owner.console}', 'OK'] if action == 'start' else ['OK']):
            raise RuntimeError(f'unexpected {action} response: {result!r}')

    def read():
        return capture.frames.read_shm(f'videmulator{owner.console}', expected_console_port=owner.console)

    def adb_image_digest():
        owner.owned()
        argv = [str(owner.adb), '-H', '127.0.0.1', '-P', str(args.adb_port), '-s', owner.serial, 'exec-out', 'screencap']
        data = subprocess.run(argv, env=owner.env(), capture_output=True, check=True, timeout=20).stdout
        metadata, _ = parse_screencap(data)
        return metadata['bgraSha256']

    def boot_id():
        value = adb('shell', 'cat', '/proc/sys/kernel/random/boot_id').stdout.strip()
        if str(uuid.UUID(value)) != value:
            raise RuntimeError('invalid guest boot ID')
        return value

    def new_frame(previous, previous_pixels):
        deadline = time.monotonic() + 8
        while True:
            frame, pixels = read()
            if frame['frameNumber'] != previous['frameNumber'] and pixels != previous_pixels:
                return frame, pixels
            if time.monotonic() >= deadline:
                raise RuntimeError('no new SHM frame observed after changing the guest display')
            time.sleep(0.1)

    try:
        report['initialInfo'] = owner.owned()
        report['identity'] = owner.identity
        report['bootIdBefore'] = boot_id()
        adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
        time.sleep(0.3)
        report['initialFrame'], _ = read()
        report['adbBeforeStopSha256'] = adb_image_digest()
        started = time.monotonic()
        for i in range(80):
            if i % 10 == 0:
                owner.owned()
            command('start')
        report['repeatedStartSeconds'] = time.monotonic() - started
        checks['eightyRepeatedStartsReturned'] = True
        owner.owned()
        command('stop')
        stopped, stopped_pixels = read()
        report['stoppedFrame'] = stopped
        report['settingsLaunch'] = adb('shell', 'am', 'start', '-a', 'android.settings.SETTINGS').stdout.strip()
        time.sleep(0.4)
        report['adbAfterStopSha256'] = adb_image_digest()
        checks['guestDisplayChangedWhileStopped'] = report['adbBeforeStopSha256'] != report['adbAfterStopSha256']
        report['stoppedObservations'] = []
        for _ in range(8):
            frame, pixels = read()
            report['stoppedObservations'].append(frame)
            if frame['headerHex'] != stopped['headerHex'] or pixels != stopped_pixels:
                raise RuntimeError('SHM changed after successful stop while the guest display changed')
            time.sleep(0.1)
        checks['stoppedStreamRemainedUnchanged'] = True
        owner.owned()
        for i in range(80):
            if i % 10 == 0:
                owner.owned()
            command('stop')
        checks['eightyRepeatedStopsReturned'] = True
        command('start')
        # Keep Settings visible until its new frame is observed. Returning HOME
        # first could correctly reproduce the same pixels as the stopped HOME.
        resumed, pixels = new_frame(stopped, stopped_pixels)
        report['resumedFrame'] = resumed
        checks['restartPublishedNewFrame'] = True
        (args.output / 'resumed.png').write_bytes(capture.frames.png_bgra(resumed['width'], resumed['height'], pixels))
        adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
        started = time.monotonic()
        for i in range(40):
            if i % 5 == 0:
                owner.owned()
            command('stop')
            command('start')
        report['fortyCyclesSeconds'] = time.monotonic() - started
        checks['fortyStartStopCyclesReturned'] = True
        report['bootIdAfter'] = boot_id()
        checks['sameGuestBoot'] = report['bootIdBefore'] == report['bootIdAfter']
        report['finalInfo'] = owner.owned()
        report['ok'] = all(checks.values())
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        try:
            owner.owned()
            command('start')
            adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
            time.sleep(0.4)
            before_settings, before_settings_pixels = read()
            try:
                adb('shell', 'am', 'start', '-a', 'android.settings.SETTINGS')
                settings_frame, settings_pixels = new_frame(before_settings, before_settings_pixels)
            finally:
                # A failed frame check must still return this owned QA guest
                # HOME. The adb helper refuses a replacement process.
                adb('shell', 'input', 'keyevent', 'KEYCODE_HOME')
                report['restoreHomeCommandReturned'] = True
            report['restoredFrame'], _ = new_frame(settings_frame, settings_pixels)
            report['restoreInfo'] = owner.owned()
            report['bootIdRestored'] = boot_id()
            if report.get('bootIdBefore') and report['bootIdRestored'] != report['bootIdBefore']:
                raise RuntimeError('guest rebooted during stream restoration')
            report['restoredStream'] = True
        except Exception as error:
            report['restoreError'] = f'{type(error).__name__}: {error}'
            report['ok'] = False
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'ok': report['ok'], 'output': str(args.output), 'error': report.get('error')}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
