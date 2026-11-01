#!/usr/bin/env python3
"""Generate tiny H264/HEVC Annex-B fixtures and independent native VT I420 references."""
import argparse
import datetime
import json
from pathlib import Path

from common import HERE, capture, compiler, create_output, digest, native_host


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True,
                        help='New output directory; existing paths are always refused')
    args = parser.parse_args()
    args.output = args.output.expanduser().resolve()
    try:
        native_host()
        if args.output.exists():
            raise ValueError('Refusing existing output directory: ' + str(args.output))
    except ValueError as error:
        parser.error(str(error))
    create_output(args.output)
    fixture_root = args.output / 'fixtures'
    fixture_root.mkdir(exist_ok=False)
    binary = args.output / 'generate'
    command = compiler() + [str(HERE / 'generate.mm'), '-framework', 'VideoToolbox',
        '-framework', 'CoreMedia', '-framework', 'CoreVideo', '-framework', 'CoreFoundation', '-o', str(binary)]
    tracked = [Path(__file__), HERE / 'generate.mm', HERE / 'common.py']
    before = {str(path): digest(path) for path in tracked}
    report = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'compile_command': command, 'source_sha256': before}
    compiled = capture(command, args.output, 'compile', 60)
    report['compile'] = compiled
    passed = False
    if compiled['exit'] == 0:
        run = capture([str(binary), str(fixture_root)], args.output, 'run', 60)
        report['run'] = run
        if run['exit'] == 0:
            manifest = json.loads(run['stdout'])
            manifest.update({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'pass': True, 'generator_sha256': before[str(HERE / 'generate.mm')],
                'sha256': {str(path.relative_to(fixture_root)): digest(path)
                           for path in sorted(fixture_root.rglob('*')) if path.is_file()},
                'limits': [
                    'Deterministic 128x96 I420 input is hardware encoded by this host; encoded bytes may differ across OS versions.',
                    'Independent native VT decode writes tight I420 references; it shares the platform decoder backend, not production helper parser/copy/lifetime code.',
                    'Does not invoke production helpers, guest, package, GPU-texture output or reclaim.']})
            for codec in manifest['codecs']:
                hashes = [manifest['sha256'][entry['reference_i420']] for entry in codec['access_units']]
                if len(set(hashes)) != manifest['frame_count']:
                    raise ValueError('Reference frames are not distinct')
            with (args.output / 'manifest.json').open('x') as stream:
                json.dump(manifest, stream, indent=2)
                stream.write('\n')
            report['binary_sha256'] = digest(binary)
            report['manifest_sha256'] = digest(args.output / 'manifest.json')
            passed = True
    report['source_unchanged'] = all(digest(path) == before[str(path)] for path in tracked)
    report['pass'] = passed and report['source_unchanged']
    with (args.output / 'report.json').open('x') as stream:
        json.dump(report, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'pass': report['pass'], 'report': str(args.output / 'report.json'),
                      'manifest': str(args.output / 'manifest.json') if passed else None}, indent=2))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
