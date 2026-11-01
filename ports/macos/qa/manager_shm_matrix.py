#!/usr/bin/env python3
"""Collect package-owned SHM/ADB observations against the fixed-size SHM ABI.

Exit success covers collection and metadata checks, not ADB pixel parity. The
two captures are independent and SHM has no frame-atomic publication protocol.
No pixel tolerance is applied. Rotations are lossless diagnostic hypotheses,
not a best-match orientation oracle.
"""
import array
import argparse
import collections
import concurrent.futures
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import time

import capture_manager as capture


def parse_screencap(raw):
    if len(raw) < 12:
        raise RuntimeError('truncated raw screencap header')
    width, height, fmt = struct.unpack_from('<III', raw)
    if not (1 <= width <= 8192 and 1 <= height <= 8192):
        raise RuntimeError(f'invalid screencap dimensions: {width}x{height}')
    payload_size = width * height * 4
    header = len(raw) - payload_size
    if fmt != 1 or header not in (12, 16) or payload_size > capture.frames.MAX_FRAME_BYTES:
        raise RuntimeError(f'unsupported raw screencap: {width}x{height} format {fmt} header {header}')
    rgba = raw[header:]
    bgra = bytearray(payload_size)
    bgra[0::4], bgra[1::4], bgra[2::4], bgra[3::4] = rgba[2::4], rgba[1::4], rgba[0::4], rgba[3::4]
    return {'width': width, 'height': height, 'format': fmt, 'headerBytes': header,
            'rawHeaderHex': raw[:header].hex(),
            # Android14 screencap writes dataSpaceToInt(), not the Dataspace enum.
            'rawColorSpaceCode': struct.unpack_from('<I', raw, 12)[0] if header == 16 else None,
            'bgraSha256': hashlib.sha256(bgra).hexdigest()}, bytes(bgra)


def rotate_bgra(width, height, pixels, clockwise_quarter_turns):
    """Reorder complete pixels only: no interpolation or color conversion."""
    if len(pixels) != width * height * 4 or clockwise_quarter_turns not in range(4):
        raise ValueError('invalid BGRA rotation input')
    turns = clockwise_quarter_turns
    if turns == 0:
        return width, height, pixels
    source = array.array('I')
    source.frombytes(pixels)
    if source.itemsize != 4:
        raise RuntimeError('four-byte array elements required')
    if turns == 2:
        return width, height, source[::-1].tobytes()
    result = array.array('I')
    if turns == 1:
        for x in range(width):
            result.extend(source[(height - 1) * width + x:x - 1 if x else None:-width])
    else:
        for x in range(width - 1, -1, -1):
            result.extend(source[x::width])
    return height, width, result.tobytes()


def pixel_difference(first, second):
    if len(first) != len(second) or len(first) % 4:
        raise ValueError('pixel comparison requires equal-size BGRA buffers')
    channels = []
    for index, name in enumerate(('B', 'G', 'R', 'A')):
        histogram = collections.Counter(a - b for a, b in zip(first[index::4], second[index::4]))
        count = sum(histogram.values())
        channels.append({'channel': name, 'samples': count, 'equalSamples': histogram.get(0, 0),
                         'maxAbsoluteDifference': max((abs(x) for x in histogram), default=0),
                         'meanAbsoluteDifference': sum(abs(x) * n for x, n in histogram.items()) / count if count else 0,
                         'signedDifferenceHistogram': {str(k): histogram[k] for k in sorted(histogram)}})
    return {'exactlyEqual': first == second, 'mismatchedBytes': sum(a != b for a, b in zip(first, second)),
            'differenceSign': 'ADB candidate minus SHM', 'channels': channels,
            'toleranceApplied': False, 'parityVerdict': 'not_established_by_independent_captures'}


def reader_observation(samples, natural):
    # A static screen need not post another frame. Unix timestamps are not a
    # monotonic clock; record them without requiring growth. uint32 wraps.
    ordered = all(((b['frameNumber'] - a['frameNumber']) & 0xffffffff) < 0x80000000
                  for a, b in zip(samples, samples[1:]))
    return {'naturalDimensionsMatch': all([s['width'], s['height']] == natural for s in samples),
            'frameNumbersOrderedModulo32': ordered,
            'frameAdvanceObserved': any(a['frameNumber'] != b['frameNumber'] for a, b in zip(samples, samples[1:])),
            'stationaryFramesAllowed': True}


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
    collector = capture.ManagerCapture(args.root, args.index)
    report = {'schema': 2, 'scope': 'existing Manager stream; independent capture observations; QA guest rotations restored',
              'noSeqlock': True, 'consistencyLimitation': capture.frames.LIMITATION,
              'okMeaning': 'collection completed, SHM fixed-size metadata checks passed, retention/settings restored; not ADB pixel parity',
              'pixelParityEstablished': False, 'pixelToleranceApplied': False,
              'capturePairing': 'ADB and SHM are independent; even unchanged SHM headers do not identify a matching ADB composition',
              'rotationComparison': 'record all dimension-compatible cardinal rotations losslessly; no hypothesis selected as correct',
              'stages': [], 'collectionCompleted': False, 'ok': False,
              'runnerSha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    with (args.output / 'runner.py').open('xb') as stream:
        stream.write(Path(__file__).read_bytes())
    saved = {}
    try:
        info = collector.owned()
        report['identity'] = collector.identity
        report['initialInfo'] = info
        natural = [info['config']['width'], info['config']['height']]
        report['naturalDimensions'] = natural
        report['producerContract'] = 'fixed startup framebuffer dimensions; BGRA; frames update on posted-frame callbacks; fps is a target/suggestion'
        commands = {
            'sentinel': ('shell', 'sha256sum', '/data/local/tmp/modavd-gapfix-disk-20261101/sentinel.bin'),
            'disk': ('shell', 'df', '-k', '/data'),
            'home': ('shell', 'cmd', 'package', 'resolve-activity', '--brief', '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.HOME'),
            'mounts': ('shell', 'cat', '/proc/mounts'),
        }
        report['retention'] = {k: collector.adb_command(*v).stdout.strip() for k, v in commands.items()}
        for key in ('accelerometer_rotation', 'user_rotation'):
            saved[key] = collector.adb_command('shell', 'settings', 'get', 'system', key).stdout.strip()
        report['savedSettings'] = saved.copy()
        collector.owned()
        collector.adb_command('shell', 'input', 'keyevent', 'KEYCODE_HOME')
        collector.adb_command('shell', 'settings', 'put', 'system', 'accelerometer_rotation', '0')
        for rotation in (0, 1, 2, 3):
            collector.owned()
            collector.adb_command('shell', 'settings', 'put', 'system', 'user_rotation', str(rotation))
            time.sleep(1)
            stage = {'requestedUserRotation': rotation, 'pixelAttempts': []}
            for attempt in range(5):
                collector.owned()
                before, _ = capture.frames.read_shm(f'videmulator{collector.console}', expected_console_port=collector.console)
                started = time.monotonic_ns()
                command = [str(collector.adb), '-H', '127.0.0.1', '-P', str(args.adb_port), '-s', collector.serial, 'exec-out', 'screencap']
                raw = subprocess.run(command, env=collector.env(), capture_output=True, check=True, timeout=20).stdout
                adb_finished = time.monotonic_ns()
                adb, converted = parse_screencap(raw)
                width, height = adb['width'], adb['height']
                collector.owned()
                frame, pixels = capture.frames.read_shm(f'videmulator{collector.console}', expected_console_port=collector.console)
                result = {'attempt': attempt, 'adb': adb, 'shmBeforeAdb': before, 'shmAfterAdb': frame,
                          'monotonicTimingNs': {'adbStart': started, 'adbEnd': adb_finished, 'shmCopyEnd': time.monotonic_ns()},
                          'shmNaturalDimensionsMatch': [frame['width'], frame['height']] == natural,
                          'shmHeaderUnchangedAcrossAdb': before['headerHex'] == frame['headerHex'],
                          'logicalDimensionsCompatible': [width, height] in (natural, natural[::-1]),
                          'normalizationHypotheses': []}
                for turns in range(4):
                    rw, rh = (width, height) if turns % 2 == 0 else (height, width)
                    if [rw, rh] != [frame['width'], frame['height']]:
                        continue
                    _, _, candidate = rotate_bgra(width, height, converted, turns)
                    result['normalizationHypotheses'].append({'clockwiseQuarterTurns': turns,
                                                             'comparison': pixel_difference(candidate, pixels)})
                # Always retain the first pair, including a mismatch. PNG has
                # no added color profile; raw header metadata is kept above.
                if attempt == 0:
                    for kind, w, h, data in (('adb', width, height, converted),
                                             ('shm', frame['width'], frame['height'], pixels)):
                        name = f'rotation-{rotation}-{kind}.png'
                        with (args.output / name).open('xb') as stream:
                            stream.write(capture.frames.png_bgra(w, h, data))
                        result[kind + 'Image'] = name
                stage['pixelAttempts'].append(result)
                time.sleep(0.2)
            def reader(number):
                samples = []
                for _ in range(12):
                    item, _ = capture.frames.read_shm(f'videmulator{collector.console}', expected_console_port=collector.console)
                    samples.append({k: item[k] for k in ('width', 'height', 'frameNumber', 'timestampUs', 'payloadSha256')})
                    time.sleep(0.025)
                return {'reader': number, 'samples': samples, 'observation': reader_observation(samples, natural)}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                stage['readers'] = list(pool.map(reader, range(3)))
            stage['metadataContractChecksPass'] = all(
                p['shmNaturalDimensionsMatch'] and p['logicalDimensionsCompatible'] for p in stage['pixelAttempts']) and all(
                r['observation']['naturalDimensionsMatch'] and r['observation']['frameNumbersOrderedModulo32'] for r in stage['readers'])
            stage['exactPixelObservations'] = sum(h['comparison']['exactlyEqual'] for p in stage['pixelAttempts'] for h in p['normalizationHypotheses'])
            report['stages'].append(stage)
            collector.owned()
        report['collectionCompleted'] = True
        report['metadataContractChecksPass'] = all(s['metadataContractChecksPass'] for s in report['stages'])
        report['ok'] = report['metadataContractChecksPass'] and report['retention']['sentinel'].startswith('87b20f4f88d708b0fa57a64ce807dfe87fe608428bbab4eb8832eaec43852d64')
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        try:
            if saved:
                collector.owned()
                for key, value in saved.items():
                    if value == 'null':
                        collector.adb_command('shell', 'settings', 'delete', 'system', key)
                    else:
                        collector.adb_command('shell', 'settings', 'put', 'system', key, value)
                report['restoredSettings'] = {key: collector.adb_command('shell', 'settings', 'get', 'system', key).stdout.strip() for key in saved}
                report['settingsRestored'] = report['restoredSettings'] == saved
                report['finalInfo'] = collector.owned()
                report['ok'] = report['ok'] and report['settingsRestored']
        except Exception as error:
            report['cleanupError'] = f'{type(error).__name__}: {error}'
            report['ok'] = False
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'ok': report['ok'], 'okMeaning': report['okMeaning'],
                      'pixelParityEstablished': False, 'output': str(args.output),
                      'error': report.get('error')}, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
