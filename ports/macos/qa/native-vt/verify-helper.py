#!/usr/bin/env python3
"""Check actual existing H264/HEVC helper methods against native VT fixtures."""
import argparse
from pathlib import Path

from common import (HERE, add_verifier_arguments, compiler, create_output,
                    execute_verifier, headers, link_options, validate_verifier)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_verifier_arguments(parser)
    args = parser.parse_args()
    includes, fixture_files = validate_verifier(parser, args)
    sources = [args.qemu_source / 'android/emu/media/src/android/emulation' / name for name in [
        'MediaVideoToolBoxVideoHelper.cpp', 'MediaVideoToolBoxHevcVideoHelper.cpp']]
    tracked = [HERE / 'common.py', Path(__file__), HERE / 'helper.mm', args.backend, args.fixtures]
    tracked += sources + headers(args.source_root, args.qemu_source) + fixture_files
    for path in tracked:
        if not path.is_file():
            parser.error('Missing provenance input: ' + str(path))
    metadata = None
    if args.build_root:
        path = args.build_root / 'ram-audit-build/binary-gfxstream_backend.json'
        if path.is_file():
            import json
            metadata = json.loads(path.read_text())
            tracked.append(path)
    create_output(args.output)
    command = compiler()
    if args.asan:
        command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    command += ['-I' + str(path) for path in includes]
    command += [str(HERE / 'helper.mm')] + link_options(args.backend) + ['-o', str(args.output / 'verify')]
    limits = [
        'Links and invokes actual existing helper implementation, FFmpeg metadata probe and Apple VideoToolbox; no codec/session/decoder substitutions.',
        'Recorded source/header hashes identify the supplied source tree. Matching it to the binary remains a build provenance obligation; optional build-root metadata is recorded when present.',
        'Uses 128x96 H264/HEVC I/P byte-buffer fixtures. Does not test GPU texture output, Android service/MediaContext path, damaged/large/reordered streams or guest reclaim.',
        'Caller input has 64 readable zero-padding bytes and guard pages; after synchronous decode returns its page remains PROT_NONE until helper teardown.',
        'ASan/UBSan mode instruments the harness and intercepts allocations; backend and Apple framework instrumentation is unchanged.',
        'Private-field inspection changes no object layout or implementation. State cleanup does not prove all Apple internal allocations leak-free.',
        'No source, backend, package, guest, game or ADB service is modified.',
    ]
    return execute_verifier(args, command, tracked, 'VT_RESULT_JSON=',
        'ASan/UBSan harness with existing backend' if args.asan else 'Actual backend helpers',
        limits, {'backend_build_metadata': metadata})


if __name__ == '__main__':
    raise SystemExit(main())
