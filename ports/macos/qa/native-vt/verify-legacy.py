#!/usr/bin/env python3
"""Compile the full actual legacy H264 TU and exercise real native VideoToolbox."""
import argparse
from pathlib import Path
import subprocess

from common import (GOOGLE_BASELINE, HERE, LEGACY_SOURCE, add_verifier_arguments,
                    compiler, create_output, execute_verifier, headers, link_options,
                    validate_verifier)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_verifier_arguments(parser, legacy=True)
    parser.add_argument('--original-negative', action='store_true',
                        help='Compile Google baseline full TU and require ASan alloc-dealloc-mismatch')
    parser.add_argument('--baseline-revision', default=GOOGLE_BASELINE)
    parser.add_argument('--baseline-repository', type=Path,
                        help='Git repository for negative control (default SOURCE_ROOT/external/qemu)')
    args = parser.parse_args()
    if args.original_negative and not args.asan:
        parser.error('--original-negative requires --asan')
    includes, fixture_files = validate_verifier(parser, args)
    actual = args.qemu_source / LEGACY_SOURCE
    archive = args.build_root / 'archives/libandroid-emu-base.a'
    tracked = [HERE / 'common.py', Path(__file__), HERE / 'legacy.mm', actual,
               args.backend, args.fixtures, archive] + headers(args.source_root, args.qemu_source) + fixture_files
    for path in tracked:
        if not path.is_file():
            parser.error('Missing provenance input: ' + str(path))
    original = None
    repository = (args.baseline_repository or args.source_root / 'external/qemu').expanduser().resolve()
    if args.original_negative:
        original = subprocess.check_output(['git', '-C', str(repository), 'show',
            args.baseline_revision + ':' + str(LEGACY_SOURCE)], text=True)
        old = 'std::unique_ptr<uint8_t> idr(new uint8_t[dataSz + 4]);'
        new = 'std::unique_ptr<uint8_t[]> idr(new uint8_t[dataSz + 4]);'
        if original.count(old) != 1 or original.replace(old, new) != actual.read_text():
            parser.error('Baseline-to-actual delta must be exactly the single array-owner fix')
    create_output(args.output)
    stream_object = args.output / 'Stream.cpp.o'
    with stream_object.open('xb') as stream:
        stream.write(subprocess.check_output(['xcrun', 'ar', '-p', str(archive), 'Stream.cpp.o']))
    tracked.append(stream_object)
    source = actual
    if original is not None:
        source = args.output / 'google-base-MediaH264DecoderVideoToolBox.cpp'
        with source.open('x') as stream:
            stream.write(original)
        tracked.append(source)
    command = compiler() + ['-include', 'memory', '-include', 'arpa/inet.h']
    if args.asan:
        command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    command += ['-I' + str(path) for path in includes]
    command += [str(HERE / 'legacy.mm'), str(source), str(stream_object)] + link_options(args.backend)
    command += ['-o', str(args.output / 'verify')]
    limits = [
        'Compiles and invokes the entire actual legacy TU, actual exported parser/renderer dependencies, actual Stream.cpp.o and system VideoToolbox; no codec/session replacements.',
        'Legacy is absent from backend exports and is not selected by the current default factory. This independent TU test does not prove packaged legacy integration.',
        'ASan/UBSan instruments driver and complete legacy TU. Dependency objects, backend and Apple frameworks retain their original instrumentation.',
        'Version100 CPU-copy protocol, small fixed H264 I/P fixtures, exact native VT references and PROT_NONE caller requests until object destruction.',
        'No GPU output, Android service/guest/MediaContext, malformed/large/reordered streams, snapshot load, Apple internal allocator leak proof or reclaim coverage.',
        'No source, backend, package, guest, game or ADB service is modified.',
    ]
    return execute_verifier(args, command, tracked, 'VT_LEGACY_JSON=',
        'Google baseline expected ASan failure' if args.original_negative else
        ('ASan/UBSan complete legacy TU' if args.asan else 'Actual complete legacy TU'),
        limits, {'baseline_revision': args.baseline_revision if args.original_negative else None,
                 'baseline_repository': str(repository) if args.original_negative else None},
        negative=args.original_negative)


if __name__ == '__main__':
    raise SystemExit(main())
