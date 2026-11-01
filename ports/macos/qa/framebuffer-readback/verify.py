#!/usr/bin/env python3
"""Compile a native driver with the existing FrameBuffer object's ABI flags."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / 'framebuffer_readback.cpp'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(command, output, prefix, cwd=None, env=None, timeout=60):
    try:
        result = subprocess.run(command, cwd=cwd, env=env, text=True,
                                capture_output=True, timeout=timeout)
        record = {'exit': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
    except subprocess.TimeoutExpired as error:
        def decode(value):
            return value.decode(errors='replace') if isinstance(value, bytes) else (value or '')
        record = {'exit': None, 'timeout': True,
                  'stdout': decode(error.stdout), 'stderr': decode(error.stderr)}
    for key in ('stdout', 'stderr'):
        with (output / (prefix + '.' + key)).open('x') as stream:
            stream.write(record[key])
    return record


def flags_from_ninja(args, report):
    cache = (args.build_root / 'CMakeCache.txt').read_text().splitlines()
    ninja = next(line.split('=', 1)[1] for line in cache
                 if line.startswith('CMAKE_MAKE_PROGRAM:'))
    targets = [line.split(':', 1)[0].removeprefix('build ')
               for line in (args.build_root / 'build.ninja').read_text().splitlines()
               if line.startswith('build ') and 'frame_buffer.cpp.o:' in line]
    if len(targets) != 1:
        raise ValueError('Expected exactly one FrameBuffer object target in build.ninja')
    report['ninja_query_command'] = [ninja, '-t', 'commands', targets[0]]
    query = capture(report['ninja_query_command'], args.output, 'ninja', cwd=args.build_root)
    report['ninja_query_exit'] = query['exit']
    if query['exit'] != 0:
        raise ValueError('Ninja command query failed')
    source = args.source_root / 'hardware/google/gfxstream/host/frame_buffer.cpp'
    matches = [shlex.split(line) for line in query['stdout'].splitlines()
               if str(source) in line and str(source) in shlex.split(line)]
    if len(matches) != 1:
        raise ValueError('Expected exactly one compile command for the supplied source root')
    original = matches[0]
    if any(value in original for value in ('&&', ';', '||')):
        raise ValueError('Unsupported shell operators in compile command')
    filtered = []
    index = 0
    while index < len(original):
        value = original[index]
        if value in ('-o', '-MF', '-MT', '-MQ'):
            index += 2
        elif value in ('-c', '-MD', '-MMD', '-MP') or value == str(source):
            index += 1
        else:
            filtered.append(value)
            index += 1
    report['framebuffer_object_target'] = targets[0]
    report['framebuffer_compile_command'] = original
    report['inherited_abi_flags'] = filtered
    return list(filtered)


def loaded_identity(stderr, backend):
    record = re.compile(r'^dyld(?:\[\d+\])?:\s+(?:<[0-9a-fA-F-]+>\s+|loaded:\s+)(/[^\r\n]+)$')
    observed = []
    for line in stderr.splitlines():
        match = record.fullmatch(line)
        if match and Path(match[1]).name == backend.name:
            observed.append(str(Path(match[1]).resolve()))
    paths = sorted(set(observed))
    return {'expected_resolved_path': str(backend), 'loaded_image_paths': paths,
            'loaded_image_records': len(observed), 'confirmed': paths == [str(backend)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('backend', 'build-root', 'source-root', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--expected-backend-sha256')
    parser.add_argument('--asan', action='store_true', help='Instrument the driver only with ASan/UBSan')
    parser.add_argument('--timeout', type=int, default=60, help='Timeout per native mode, default60 seconds')
    args = parser.parse_args()
    try:
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise ValueError('Requires Apple Silicon macOS and an existing gfxstream build')
        for name in ('backend', 'build_root', 'source_root', 'output'):
            setattr(args, name, getattr(args, name).expanduser().resolve())
        if args.output.exists():
            raise ValueError('Refusing existing output directory: ' + str(args.output))
        if args.timeout <= 0 or args.backend.name != 'libgfxstream_backend.dylib' or not args.backend.is_file():
            raise ValueError('Require a positive timeout and an existing libgfxstream_backend.dylib')
        if args.expected_backend_sha256 and digest(args.backend) != args.expected_backend_sha256:
            raise ValueError('Backend SHA-256 differs from the expected identity')
        for name in ('build.ninja', 'CMakeCache.txt'):
            if not (args.build_root / name).is_file():
                raise ValueError('Missing build metadata: ' + name)
        runtime = args.backend.parent / 'vulkan'
        for name in ('libvulkan.dylib', 'MoltenVK_icd.json', 'libMoltenVK.dylib'):
            if not (runtime / name).is_file():
                raise ValueError('Missing selected backend Vulkan runtime: ' + name)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    args.output.mkdir(parents=True, exist_ok=False)
    tracked = [SOURCE, Path(__file__).resolve(), args.backend,
               args.build_root / 'build.ninja', args.build_root / 'CMakeCache.txt',
               runtime / 'libvulkan.dylib', runtime / 'MoltenVK_icd.json', runtime / 'libMoltenVK.dylib',
               args.source_root / 'hardware/google/gfxstream/host/frame_buffer.cpp',
               args.source_root / 'hardware/google/gfxstream/host/frame_buffer.h']
    report = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'asan_driver_only': args.asan, 'backend': str(args.backend),
              'expected_backend_sha256': args.expected_backend_sha256,
              'limits': 'Native FrameBuffer GPU upload/readback; no guest, Android codec service or shared-memory integration.',
              'pass': False}
    before = {}
    try:
        before = {str(path): digest(path) for path in tracked}
        report['source_and_backend_sha256'] = before
        report['backend_sha256'] = before[str(args.backend)]
        (args.output / SOURCE.name).write_bytes(SOURCE.read_bytes())
        (args.output / 'verify.py').write_bytes(Path(__file__).read_bytes())
        command = flags_from_ninja(args, report)
        if args.asan:
            # The AOSP prebuilt sanitizer runtime may not support the running macOS.
            # Retain the Ninja ABI/include/define flags; use Xcode's host runtime.
            sdk = Path(command[command.index('-isysroot') + 1])
            developer = next(parent for parent in sdk.parents
                             if (parent / 'Toolchains/XcodeDefault.xctoolchain').is_dir())
            host_tools = dict(os.environ, DEVELOPER_DIR=str(developer))
            selected = capture(['xcrun', '--find', 'clang++'], args.output, 'sanitizer-compiler', env=host_tools)
            if selected['exit'] != 0:
                raise ValueError('Cannot locate the Xcode sanitizer compiler')
            command[0] = selected['stdout'].strip()
            linker = capture(['xcrun', '--find', 'ld'], args.output, 'sanitizer-linker', env=host_tools)
            if linker['exit'] != 0:
                raise ValueError('Cannot locate the matching Xcode linker')
            command += ['--ld-path=' + linker['stdout'].strip()]
            report['sanitizer_compiler_policy'] = 'Xcode host compiler/linker/runtime selected from the inherited SDK; inherited Ninja ABI/define/include flags; driver instrumentation only'
        binary = args.output / 'verify'
        command += ['-O1', '-g', str(args.output / SOURCE.name), str(args.backend),
                    '-framework', 'Foundation', '-Wl,-rpath,' + str(args.backend.parent), '-o', str(binary)]
        if args.asan:
            command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
        report['compile_command'] = command
        report['compile'] = capture(command, args.output, 'compile', cwd=args.build_root)
        if report['compile']['exit'] != 0:
            raise ValueError('Driver compilation failed')
        report['binary_sha256'] = digest(binary)
        report['linked_libraries'] = capture(['otool', '-L', str(binary)], args.output, 'linked')
        environment = dict(os.environ)
        removed = sorted(key for key in environment if key.startswith(('DYLD_', '__XPC_DYLD_')))
        for key in removed:
            del environment[key]
        settings = {'DYLD_LIBRARY_PATH': str(args.backend.parent), 'DYLD_PRINT_LIBRARIES': '1',
                    'ANDROID_EMU_VK_LOADER_PATH': str(runtime / 'libvulkan.dylib'),
                    'ANDROID_EMULATOR_LAUNCHER_DIR': str(args.backend.parent.parent),
                    'ANDROID_EMU_VK_ICD': 'moltenvk', 'ANDROID_EMU_HEADLESS': '1',
                    'ANDROID_EMU_VK_HIDE_REBAR': '0',
                    'VK_DRIVER_FILES': str(runtime / 'MoltenVK_icd.json'),
                    'VK_ICD_FILENAMES': str(runtime / 'MoltenVK_icd.json')}
        environment.update(settings)
        if args.asan:
            settings.update(ASAN_OPTIONS='alloc_dealloc_mismatch=1:detect_leaks=0', UBSAN_OPTIONS='halt_on_error=1')
            environment.update(settings)
        report['runtime_environment'] = {'removed_inherited_variable_names': removed, 'set': settings}
        report['modes'] = {}
        for mode in ('vulkan-only', 'gl'):
            run = capture([str(binary), mode], args.output, mode, cwd=args.build_root,
                          env=environment, timeout=args.timeout)
            identity = loaded_identity(run['stderr'], args.backend)
            driver_identity = loaded_identity(run['stderr'], runtime / 'libMoltenVK.dylib')
            loader_identity = loaded_identity(run['stderr'], runtime / 'libvulkan.dylib')
            native = None
            for line in run['stdout'].splitlines():
                if line.startswith('{'):
                    try:
                        native = json.loads(line)
                    except json.JSONDecodeError:
                        pass
            report['modes'][mode] = {'run': run, 'native': native, 'dyld_backend_identity': identity,
                                     'dyld_vulkan_loader_identity': loader_identity,
                                     'dyld_moltenvk_identity': driver_identity,
                                     'pass': run['exit'] == 0 and bool(native and native.get('pass'))
                                     and identity['confirmed'] and driver_identity['confirmed'] and loader_identity['confirmed']}
        report['pass'] = all(value['pass'] for value in report['modes'].values())
    except (ValueError, OSError, StopIteration) as error:
        report['error'] = str(error)
    finally:
        report['source_and_backend_unchanged'] = bool(before) and all(
            path.is_file() and digest(path) == before[str(path)] for path in tracked)
        report['pass'] = report['pass'] and report['source_and_backend_unchanged']
        with (args.output / 'report.json').open('x') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    print(json.dumps({'report': str(args.output / 'report.json'), 'pass': report['pass'],
                      'error': report.get('error'), 'modes': {key: {'pass': value['pass'], 'native': value['native']}
                      for key, value in report.get('modes', {}).items()}}, indent=2))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
