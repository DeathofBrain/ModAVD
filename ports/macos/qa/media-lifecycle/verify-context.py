#!/usr/bin/env python3
"""Compile actual complete MediaContext TUs with counted codec/control shims.

Positive runs use the supplied patched source. Three separate baseline controls
must detect destructor ordering, load ordering and the HEVC snapshot count.
No source checkout, backend, package or guest is modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
CONTEXT = Path('android/android-emu/android/emulation/address_space_host_media.cpp')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, text):
    with path.open('x') as stream:
        stream.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ['qemu-source', 'aemu-source', 'baseline-qemu-source',
                 'baseline-aemu-source', 'gfxstream-source', 'output']:
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args()
    for name, value in vars(args).items():
        setattr(args, name, value.expanduser().resolve())
    if args.output.exists():
        parser.error('Output must be a new directory; historical evidence is never replaced')
    variants = [('patched', args.qemu_source, args.aemu_source),
                ('baseline', args.baseline_qemu_source, args.baseline_aemu_source)]
    tracked = [Path(__file__).resolve(), HERE / 'context.cpp', HERE / 'Stream.h.in',
               args.gfxstream_source / 'host/include/render-utils/address_space_operations.h']
    for _, qemu, aemu in variants:
        tracked += [qemu / CONTEXT]
        tracked += list((aemu / 'host-common/include/host-common').glob('*.h'))
        tracked += [aemu / 'base/include/aemu/base/AlignedBuf.h',
                    aemu / 'base/include/aemu/base/c_header.h']
    for path in tracked:
        if not path.is_file():
            parser.error('Missing required source: ' + str(path))
    before = {str(path): digest(path) for path in tracked}
    args.output.mkdir(parents=True, exist_ok=False)
    shim = args.output / 'shim/aemu/base/files'
    shim.mkdir(parents=True)
    write(shim / 'Stream.h', (HERE / 'Stream.h.in').read_text())
    clang = subprocess.check_output(['xcrun', '-f', 'clang++'], text=True).strip()
    sdk = subprocess.check_output(['xcrun', '--show-sdk-path'], text=True).strip()
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(('DYLD_', '__XPC_DYLD_'))}
    environment.update(ASAN_OPTIONS='detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
    report = {'scope': 'Actual complete context TU and actual public declarations; codec factories/implementations, allocation, control ops and byte-oriented big-endian Stream are counted shims. No native decode, HVF, RAM reclaim or guest.',
              'source_sha256': before, 'variants': [], 'inputs': {k: str(v) for k, v in vars(args).items()}}
    for name, qemu, aemu in variants:
        command = [clang, '-std=c++17', '-g', '-O1', '-fsanitize=address,undefined',
                   '-fno-omit-frame-pointer', '-isysroot', sdk]
        command += ['-I' + str(path) for path in [args.output / 'shim',
            qemu / 'android/emu/media/src', aemu / 'host-common/include',
            aemu / 'base/include', args.gfxstream_source / 'host/include']]
        binary = args.output / name
        command += [str(qemu / CONTEXT), str(HERE / 'context.cpp'), '-o', str(binary)]
        compiled = subprocess.run(command, text=True, capture_output=True, timeout=60, env=environment)
        write(args.output / (name + '.compile.stdout'), compiled.stdout)
        write(args.output / (name + '.compile.stderr'), compiled.stderr)
        result = {'variant': name, 'compile_command': command, 'compile_exit': compiled.returncode, 'runs': []}
        if compiled.returncode == 0:
            result['binary_sha256'] = digest(binary)
            for scenario in (['all'] if name == 'patched' else ['destructor', 'load', 'snapshot']):
                process = subprocess.run([str(binary), scenario], text=True, capture_output=True,
                                         timeout=30, env=environment)
                run = {'scenario': scenario, 'exit': process.returncode,
                       'stdout': process.stdout, 'stderr': process.stderr}
                write(args.output / (name + '-' + scenario + '.stdout'), process.stdout)
                write(args.output / (name + '-' + scenario + '.stderr'), process.stderr)
                if name == 'patched' and process.returncode == 0:
                    run['native'] = json.loads(process.stdout)
                    run['pass'] = run['native'].get('pass') is True
                elif name == 'baseline':
                    expected = ('snapshot active count must include HEVC' if scenario == 'snapshot'
                                else 'mapping removed with live decoders')
                    run['expected_failure'] = expected
                    run['pass'] = process.returncode == 1 and expected in process.stderr
                else:
                    run['pass'] = False
                result['runs'].append(run)
        report['variants'].append(result)
    report['source_unchanged'] = all(path.is_file() and digest(path) == before[str(path)] for path in tracked)
    report['pass'] = report['source_unchanged'] and all(
        variant['compile_exit'] == 0 and variant['runs'] and all(run['pass'] for run in variant['runs'])
        for variant in report['variants'])
    write(args.output / 'report.json', json.dumps(report, indent=2) + '\n')
    print(json.dumps({'report': str(args.output / 'report.json'), 'pass': report['pass'],
                      'variants': [{'variant': v['variant'], 'compile_exit': v['compile_exit'],
                                    'runs': [{'scenario': r['scenario'], 'exit': r['exit'],
                                              'pass': r['pass'], 'native': r.get('native')}
                                             for r in v['runs']]} for v in report['variants']]}, indent=2))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
