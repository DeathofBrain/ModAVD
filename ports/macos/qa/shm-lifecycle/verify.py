#!/usr/bin/env python3
"""Compile production SHM lifecycle code in an isolated host contract harness."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent
QFILES = [
    'android/android-ui/modules/aemu-recording/src/android/recording/screen-recorder.cpp',
    'android/android-ui/modules/aemu-recording/src/android/recording/video/VideoFrameSharer.cpp',
    'android/android-ui/modules/aemu-recording/src/android/recording/video/VideoFrameSharer.h',
    'android/android-emu/android/base/async/CallbackRegistry.cpp',
    'android/android-ui/modules/aemu-gl-bridge/src/android/gpu_frame.cpp',
]
MEMORY = 'android/emu/base/memory/src/android/base/memory/SharedMemory_posix.cpp'
CASES = ['lifecycle', 'no_frames', 'multi_owner', 'destructors', 'failed_init',
         'concurrent', 'sharer_concurrent', 'stop_barrier', 'destructor_barrier',
         'registry_order', 'registry_cycles', 'registry_overlap']
NEGATIVE = ['lifecycle', 'no_frames', 'multi_owner', 'destructors', 'failed_init', 'registry_cycles']
SHIMS = {
    'aemu/base/Log.h': '#pragma once\n#include <iostream>\n#define LOG(level) std::cerr\n',
    'android/base/system/System.h': '''#pragma once
#include <chrono>
#include <cstdint>
namespace android { namespace base { class System { public:
static System* get() { static System s; return &s; }
uint64_t getUnixTimeUs() { return std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::system_clock::now().time_since_epoch()).count(); }
}; }}
''',
    'host-common/opengles.h': '''#pragma once
#include <cstdint>
typedef void (*ReadPixelsFunc)(void*, uint32_t, uint32_t);
ReadPixelsFunc android_getReadPixelsFunc();
''',
    'android/recording/Producer.h': '#pragma once\nnamespace android { namespace recording { class Producer {}; }}\n',
    'android/gpu_frame.h': '''#pragma once
#include <cstdint>
#include "host-common/opengles.h"
using FrameAvailableCallback = void (*)(void*);
void gpu_frame_set_record_mode(bool, uint32_t displayId = 0);
void gpu_register_shared_memory_callback(FrameAvailableCallback, void*);
void gpu_unregister_shared_memory_callback(void*);
''',
}

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(command, *, cwd=None, env=None, timeout=60):
    p = subprocess.run([str(x) for x in command], cwd=cwd, env=env,
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return {'command': [str(x) for x in command], 'exit': p.returncode,
            'stdout': p.stdout, 'stderr': p.stderr}


def function(text, signature):
    """Copy a complete source function verbatim; no rewriting of its body."""
    start = text.index(signature)
    opening = text.index('{', start)
    depth = 1
    pos = opening + 1
    while depth:
        if text[pos] == '{': depth += 1
        elif text[pos] == '}': depth -= 1
        pos += 1
    return text[start:pos] + '\n'


def case(binary, name, env, timeout):
    started = time.monotonic()
    p = subprocess.Popen([str(binary), name], env=env, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    timed_out = False
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        p.kill()
        out, err = p.communicate(timeout=5)
    finally:
        # This name is generated only from this child PID, far outside real ports.
        # A killed negative control must not leave its private POSIX SHM behind.
        libc = ctypes.CDLL(None)
        libc.shm_unlink.argtypes = [ctypes.c_char_p]
        libc.shm_unlink(f'videmulator{900000 + p.pid}'.encode())
    return {'case': name, 'pid': p.pid, 'exit': p.returncode, 'timeout': timed_out,
            'seconds': time.monotonic()-started, 'stdout': out, 'stderr': err}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--qemu', type=Path, required=True)
    ap.add_argument('--aemu', type=Path, required=True)
    ap.add_argument('--build', type=Path, required=True, help='Read-only configured Ninja build (for SDK selection)')
    ap.add_argument('--patch', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True, help='New output directory only')
    args = ap.parse_args()
    for field in ('qemu', 'aemu', 'build', 'patch'):
        setattr(args, field, getattr(args, field).resolve(strict=True))
    args.output = args.output.absolute()
    args.output.mkdir()  # Refuse existing output, including failed earlier runs.
    report = {'scope': 'host production-function/TU contract QA, not wholeGPU or guest validation',
              'production_tree_modified': False, 'guest_commands': False,
              'patch': {'path': str(args.patch), 'sha256': sha(args.patch)},
              'inputs': {}, 'variants': {}, 'ok': False}
    try:
        shutil.copyfile(__file__, args.output/'verify-at-run.py')
        shutil.copyfile(HERE/'lifecycle.cpp', args.output/'lifecycle.cpp')
        shutil.copyfile(args.patch, args.output/'reviewed.patch')
        common = args.output/'common'
        common.mkdir()
        memory = common/'SharedMemory_posix.cpp'
        channel = common/'MessageChannel.cpp'
        shutil.copyfile(args.qemu/MEMORY, memory)
        shutil.copyfile(args.aemu/'base/MessageChannel.cpp', channel)
        inputs = [args.qemu/p for p in QFILES] + [args.qemu/MEMORY, args.aemu/'base/MessageChannel.cpp',
                  args.aemu/'base/include/aemu/base/async/CallbackRegistry.h',
                  args.aemu/'base/include/aemu/base/synchronization/MessageChannel.h',
                  args.aemu/'base/include/aemu/base/synchronization/Lock.h',
                  args.aemu/'base/include/aemu/base/synchronization/ConditionVariable.h',
                  args.aemu/'base/include/aemu/base/memory/SharedMemory.h']
        report['inputs'] = {str(p):sha(p) for p in inputs}
        shims = common/'shims'
        for rel, text in SHIMS.items():
            p = shims/rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        ninja = (args.build/'build.ninja').read_text()
        sdk = next(Path(shlex.split(line.split('=',1)[1])[shlex.split(line.split('=',1)[1]).index('-isysroot')+1])
                   for line in ninja.splitlines() if line.strip().startswith('FLAGS =') and '-isysroot' in line)
        developer = next(p for p in sdk.parents if (p/'Toolchains/XcodeDefault.xctoolchain').is_dir())
        tools_env = dict(os.environ, DEVELOPER_DIR=str(developer))
        compiler = subprocess.check_output(['xcrun','--find','clang++'], env=tools_env, text=True).strip()
        linker = subprocess.check_output(['xcrun','--find','ld'], env=tools_env, text=True).strip()
        report['toolchain'] = {'compiler':compiler,'linker':linker,'sdk':str(sdk),'developer':str(developer)}
        for variant in ['baseline', 'patched']:
            d = args.output/variant
            d.mkdir()
            source = d/'source'
            source.mkdir()
            for rel in QFILES:
                p = source/rel
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.qemu/rel, p)
            # Isolate git apply from the containing workspace and main build tree.
            subprocess.run(['git','init','-q',str(source)],check=True)
            if variant == 'patched':
                checked = run(['git','apply','--check',args.patch],cwd=source)
                if checked['exit']: raise RuntimeError(checked)
                applied = run(['git','apply',args.patch],cwd=source)
                if applied['exit']: raise RuntimeError(applied)
            gpu = (source/QFILES[4]).read_text()
            screen = (source/QFILES[0]).read_text()
            extracted = {
                'gpu-production.inc': '\n'.join(function(gpu,s) for s in [
                    'void gpu_frame_set_record_mode(', 'void gpu_register_shared_memory_callback(',
                    'void gpu_unregister_shared_memory_callback(']),
                'screen-production.inc': '\n'.join(function(screen,s) for s in [
                    'const char* start_shared_memory_module(', 'bool stop_shared_memory_module(']),
            }
            for name,text in extracted.items(): (d/name).write_text(text)
            evidence = {'production_snapshot_hashes':{rel:sha(source/rel) for rel in QFILES},
                        'exact_extraction_hashes':{name:sha(d/name) for name in extracted},
                        'runs':{},'compiles':{}}
            report['variants'][variant] = evidence
            recording = source/'android/android-ui/modules/aemu-recording/src'
            command = [compiler, f'--ld-path={linker}', '-isysroot', str(sdk), '-std=c++17',
                       '-O1','-g','-pthread','-DEINTR_WRAPPER_DEBUG=0','-Wno-reorder-ctor','-Wno-unused-parameter',
                       '-I'+str(shims),'-I'+str(d),'-I'+str(recording),
                       '-I'+str(args.aemu/'base/include'),
                       str(args.output/'lifecycle.cpp'), str(source/QFILES[1]), str(source/QFILES[3]),
                       str(channel),str(memory)]
            modes = ['normal'] if variant == 'baseline' else ['normal','asan-ubsan']
            for mode in modes:
                binary = d/('verify-'+mode)
                actual = command + (['-fsanitize=address,undefined','-fno-omit-frame-pointer'] if mode != 'normal' else []) + ['-o',str(binary)]
                compiled = run(actual, timeout=60)
                evidence['compiles'][mode] = compiled
                if compiled['exit']: raise RuntimeError(f'{variant}/{mode} compile failed: {compiled["stderr"]}')
                env = dict(os.environ, ASAN_OPTIONS='detect_leaks=0:abort_on_error=1', UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
                names = NEGATIVE if variant == 'baseline' else CASES
                evidence['runs'][mode] = [case(binary,n,env,2 if variant=='baseline' else 8) for n in names]
                evidence.setdefault('binary_hashes',{})[mode]=sha(binary)
        report['inputs_unchanged'] = all(sha(p)==digest for p,digest in report['inputs'].items())
        report['patched_all_pass'] = all(r['exit']==0 and not r['timeout'] for mode in report['variants']['patched']['runs'].values() for r in mode)
        report['negative_controls_fail'] = all(r['exit']!=0 or r['timeout'] for r in report['variants']['baseline']['runs']['normal'])
        report['ok'] = report['inputs_unchanged'] and report['patched_all_pass'] and report['negative_controls_fail']
        report['limits'] = [
            'Real production TUs: VideoFrameSharer.cpp, CallbackRegistry.cpp, MessageChannel.cpp, SharedMemory_posix.cpp; real lock/condition/channel/map headers.',
            'Exact verbatim production functions: SHM exported start/stop and GPU register/unregister/set_record_mode.',
            'Renderer/GpuFrameBridge/post setup, globals container, readpixels, logging, wall clock and unused Producer dependency are host shims. No whole GPU, guest, native engine binary, packaging or pixel parity is validated.',
            'Actual POSIX mapping is tested. Initialization failure is induced with a temporary child-only RLIMIT_NOFILE=0 and restored before checking results.',
            'Callback-internal register deferral is tested. Callback-internal unregister/self-destruction remains unsupported, as in the original registry.',
            'The 24-byte shared-memory ABI and stale header after stop are retained. Mapping existence is not stream activity.',
            'Separate bounded child processes give deadlock cases hard timeouts. ASan/UBSan instrument every compiled TU in the patched harness; these are not TSan or whole-emulator sanitizer runs. LeakSanitizer is unavailable on this macOS runtime and is explicitly disabled.'
        ]
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        (args.output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'ok':report['ok'],'report':str(args.output/'report.json')}))
    return 0 if report['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
