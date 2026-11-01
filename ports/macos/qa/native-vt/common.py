"""Shared native VideoToolbox QA plumbing. No source or backend mutation."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess

HERE = Path(__file__).resolve().parent
LEGACY_SOURCE = Path('android/emu/media/src/android/emulation/MediaH264DecoderVideoToolBox.cpp')
GOOGLE_BASELINE = 'f0c183f1cc7456ecd6f3607f2f47893768ae4334'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def native_host():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('This QA requires Apple Silicon macOS, Xcode command line tools and VideoToolbox')


def source_paths(source_root, qemu_source):
    paths = [
        source_root / 'hardware/google/aemu/host-common/include',
        source_root / 'hardware/google/aemu/base/include',
        qemu_source / 'android/emu/media/src',
        source_root / 'prebuilts/android-emulator-build/common/ffmpeg/darwin-aarch64/include',
        source_root / 'external/qemu/android/android-emu',
        source_root / 'hardware/google/gfxstream/host/include',
        source_root / 'hardware/google/gfxstream/host/features/include',
        source_root / 'hardware/google/gfxstream/host/common/include',
        source_root / 'hardware/google/gfxstream/common/base/include',
        source_root / 'external/qemu/android/emu/base/include',
    ]
    for path in paths:
        if not path.is_dir():
            raise ValueError('Missing required include directory: ' + str(path))
    return paths


def compiler():
    sdk = subprocess.check_output(['xcrun', '--show-sdk-path'], text=True).strip()
    return ['xcrun', 'clang++', '-std=c++17', '-O1', '-g', '-isysroot', sdk,
            '-Wno-deprecated-declarations', '-Wno-extern-c-compat', '-Wno-return-type-c-linkage']


def link_options(backend):
    return [str(backend), '-framework', 'Foundation', '-framework', 'VideoToolbox',
            '-framework', 'CoreMedia', '-framework', 'CoreVideo',
            '-Wl,-rpath,' + str(backend.parent)]


def add_verifier_arguments(parser, legacy=False):
    parser.add_argument('--backend', type=Path, required=True,
                        help='Exact existing libgfxstream_backend.dylib; never rebuilt or modified')
    parser.add_argument('--output', type=Path, required=True,
                        help='New directory for executable, logs and report.json; must not exist')
    parser.add_argument('--fixtures', type=Path, required=True,
                        help='manifest.json produced by generate-fixtures.py')
    parser.add_argument('--source-root', type=Path, required=True,
                        help='AOSP source root containing hardware/, external/ and prebuilts/')
    parser.add_argument('--qemu-source', type=Path,
                        help='Actual QEMU source tree; defaults to SOURCE_ROOT/external/qemu')
    parser.add_argument('--build-root', type=Path, required=legacy,
                        help='Existing emulator build tree (required by legacy for actual Stream.cpp.o)')
    parser.add_argument('--expected-backend-sha256',
                        help='Optional expected backend identity; current hash is always recorded')
    parser.add_argument('--asan', action='store_true', help='Enable ASan/UBSan for newly compiled QA code')
    parser.add_argument('--timeout', type=int, default=120, help='Native run timeout in seconds (default120)')


def validate_verifier(parser, args):
    try:
        native_host()
        for key in ['backend', 'output', 'fixtures', 'source_root', 'qemu_source', 'build_root']:
            value = getattr(args, key)
            if value is not None:
                setattr(args, key, value.expanduser().resolve())
        if args.output.exists():
            raise ValueError('Refusing existing output directory: ' + str(args.output))
        if args.timeout <= 0:
            raise ValueError('--timeout must be positive')
        if not args.backend.is_file() or not args.fixtures.is_file():
            raise ValueError('--backend and --fixtures must name existing files')
        if args.backend.name != 'libgfxstream_backend.dylib':
            raise ValueError('--backend must resolve to a file named libgfxstream_backend.dylib')
        args.qemu_source = args.qemu_source or args.source_root / 'external/qemu'
        includes = source_paths(args.source_root, args.qemu_source)
        if args.expected_backend_sha256 and digest(args.backend) != args.expected_backend_sha256:
            raise ValueError('Backend hash does not match --expected-backend-sha256')
        manifest = json.loads(args.fixtures.read_text())
        if (manifest.get('width'), manifest.get('height'), manifest.get('frame_count')) != (128, 96, 8):
            raise ValueError('These drivers require the generated 128x96, eight-frame fixtures')
        if {codec['codec'] for codec in manifest['codecs']} != {'h264', 'hevc'}:
            raise ValueError('Fixture manifest must contain H264 and HEVC')
        fixture_root = args.fixtures.parent / 'fixtures'
        fixture_files = []
        for relative, expected in manifest['sha256'].items():
            path = (fixture_root / relative).resolve()
            if not path.is_relative_to(fixture_root.resolve()) or not path.is_file() or digest(path) != expected:
                raise ValueError('Fixture hash mismatch or invalid path: ' + relative)
            fixture_files.append(path)
        for codec in manifest['codecs']:
            units = codec['access_units']
            if len(units) != 8 or [unit['index'] for unit in units] != list(range(8)):
                raise ValueError('Each codec must contain eight ordered access units')
            if len({unit['pts'] for unit in units}) != 8:
                raise ValueError('Each frame must have a distinct PTS')
            for unit in units:
                for key in ['annexb', 'reference_i420']:
                    if unit[key] not in manifest['sha256']:
                        raise ValueError('Access unit missing verified hash: ' + unit[key])
                if (fixture_root / unit['reference_i420']).stat().st_size != 18432:
                    raise ValueError('Reference is not tight 128x96 I420')
        return includes, fixture_files
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        parser.error(str(error))


def create_output(path):
    path.mkdir(parents=True, exist_ok=False)


def capture(argv, output, prefix, timeout):
    try:
        process = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
        result = {'exit': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}
    except subprocess.TimeoutExpired as error:
        def decode(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
        result = {'exit': None, 'timeout': True, 'stdout': decode(error.stdout), 'stderr': decode(error.stderr)}
    for key in ['stdout', 'stderr']:
        with (output / (prefix + '.' + key)).open('x') as stream:
            stream.write(result[key])
    return result


def headers(source_root, qemu_source):
    aemu = source_root / 'hardware/google/aemu/host-common/include/host-common'
    return [aemu / name for name in ['MediaVideoToolBoxVideoHelper.h',
            'MediaH264DecoderVideoToolBox.h', 'H264PingInfoParser.h', 'MediaH264DecoderPlugin.h']] + [
        qemu_source / 'android/emu/media/src/android/emulation/MediaVideoToolBoxHevcVideoHelper.h']


def runtime_environment(backend, asan):
    environment = dict(os.environ)
    removed = sorted(key for key in environment
                     if key.startswith(('DYLD_', '__XPC_DYLD_')))
    for key in removed:
        del environment[key]
    environment['DYLD_LIBRARY_PATH'] = str(backend.parent)
    environment['DYLD_PRINT_LIBRARIES'] = '1'
    policy = {
        'policy': 'Remove all inherited DYLD_* and __XPC_DYLD_* variables; select the backend directory explicitly and require dyld loaded-library confirmation.',
        'removed_inherited_variable_names': removed,
        'set': {'DYLD_LIBRARY_PATH': str(backend.parent), 'DYLD_PRINT_LIBRARIES': '1'},
    }
    if asan:
        options = {'ASAN_OPTIONS': 'alloc_dealloc_mismatch=1:detect_leaks=0',
                   'UBSAN_OPTIONS': 'halt_on_error=1'}
        environment.update(options)
        policy['set'].update(options)
    return environment, policy


def loaded_backend_identity(stderr, backend):
    # Match actual dyld loaded-image records, never paths mentioned by loader errors.
    record = re.compile(r'^dyld(?:\[\d+\])?:\s+(?:<[0-9a-fA-F-]+>\s+|loaded:\s+)(/[^\r\n]+)$')
    observed = []
    for line in stderr.splitlines():
        match = record.fullmatch(line)
        if match and Path(match[1]).name == 'libgfxstream_backend.dylib':
            observed.append(str(Path(match[1]).resolve()))
    paths = sorted(set(observed))
    expected = str(backend.resolve())
    return {'expected_resolved_path': expected, 'loaded_backend_paths': paths,
            'loaded_image_records': len(observed), 'confirmed': paths == [expected]}


def execute_verifier(args, command, tracked, prefix, mode, limits, extra=None, negative=False):
    tracked = sorted(set(path.resolve() for path in tracked))
    before = {str(path): digest(path) for path in tracked}
    if args.expected_backend_sha256 and before[str(args.backend)] != args.expected_backend_sha256:
        raise ValueError('Backend changed after initial validation')
    report = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'mode': mode,
              'backend': str(args.backend), 'backend_sha256': before[str(args.backend)],
              'expected_backend_sha256': args.expected_backend_sha256,
              'compile_command': command, 'source_sha256': before, 'limits': limits}
    if extra:
        report.update(extra)
    compiled = capture(command, args.output, 'compile', 60)
    report['compile'] = compiled
    binary = args.output / 'verify'
    passed = False
    if compiled['exit'] == 0:
        environment, report['runtime_environment'] = runtime_environment(args.backend, args.asan)
        try:
            result = subprocess.run([str(binary), str(args.fixtures)], env=environment,
                                    text=True, capture_output=True, timeout=args.timeout)
            run = {'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        except subprocess.TimeoutExpired as error:
            def decode(value):
                return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
            run = {'exit': None, 'timeout': True, 'stdout': decode(error.stdout), 'stderr': decode(error.stderr)}
        for key in ['stdout', 'stderr']:
            with (args.output / ('run.' + key)).open('x') as stream:
                stream.write(run[key])
        report['run'] = run
        report['dyld_backend_identity'] = loaded_backend_identity(run['stderr'], args.backend)
        report['binary_sha256'] = digest(binary)
        report['linked_libraries'] = capture(['otool', '-L', str(binary)], args.output, 'linked', 30)
        matches = [line.removeprefix(prefix) for line in run['stdout'].splitlines() if line.startswith(prefix)]
        if matches:
            report['native'] = json.loads(matches[-1])
        if negative:
            report['expected_negative'] = run['exit'] not in (None, 0) and 'alloc-dealloc-mismatch' in run['stderr']
            passed = report['expected_negative'] and report['dyld_backend_identity']['confirmed']
        else:
            passed = (run['exit'] == 0 and report.get('native', {}).get('pass', False)
                      and report['dyld_backend_identity']['confirmed'])
    report['source_and_backend_unchanged'] = all(path.is_file() and digest(path) == before[str(path)] for path in tracked)
    report['pass'] = passed and report['source_and_backend_unchanged']
    with (args.output / 'report.json').open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'report': str(args.output / 'report.json'), 'pass': report['pass'],
                      'compile_exit': compiled['exit'], 'run_exit': report.get('run', {}).get('exit'),
                      'dyld_backend_confirmed': report.get('dyld_backend_identity', {}).get('confirmed', False),
                      'native': report.get('native')}, indent=2))
    return 0 if report['pass'] else 1
