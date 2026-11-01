#!/usr/bin/env python3
"""Synthetic bounded HVF object-release experiment; never operates an emulator."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(command, output, prefix, timeout=60):
    environment = dict(os.environ)
    for name in list(environment):
        if name.startswith(('DYLD_', '__XPC_DYLD_')):
            del environment[name]
    try:
        result = subprocess.run(command, env=environment, text=True,
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


def assess(native):
    failures, physical_failures, rows = [], [], []
    assertions = 0

    def check(condition, label, physical=False):
        nonlocal assertions
        assertions += 1
        if not condition:
            (physical_failures if physical else failures).append(label)

    check(native.get('pass') is True, 'Native system calls completed')
    check(native.get('page_size') == 16384, '16KiB host pages')
    cases = native.get('cases', [])
    check(len(cases) == 12, '12 paired cases')
    seen, positives, controls = set(), 0, 0
    neighbor_keys = ('pages', 'present', 'dirty', 'reusable', 'paged_out', 'query_errors',
                     'mincore_exit', 'mincore_incore', 'first_object_id', 'last_object_id',
                     'first_offset', 'last_offset', 'first_depth')

    def empty(state, label, expected):
        check(state.get('pages') == expected, label + ' page count')
        for key in ('present', 'paged_out', 'query_errors', 'mincore_exit', 'mincore_incore', 'reusable'):
            check(state.get(key) == 0, label + ' ' + key + '=0')

    for case in cases:
        independent = case['independent_initial_objects']
        size = case['target_bytes']
        pages = size // 16384
        key = (case['round'], size, independent)
        label = str(key)
        check(key not in seen, label + ' unique')
        seen.add(key)
        check(size in (64 * 1024**2, 128 * 1024**2), label + ' bounded target')
        check(case['round'] in (0, 1, 2), label + ' round')
        check(case['chunks'] == size // (2 * 1024**2), label + ' 2MiB objects')
        generations = case['generations']
        check(len(generations) == (2 if independent else 1), label + ' generation count')
        for number, generation in enumerate(generations):
            glabel = label + '/generation' + str(number)
            positives += int(independent)
            controls += int(not independent)
            check(generation['generation'] == number, glabel + ' ordered')
            check(generation['synthetic_vcpu_4K_writes'] == size // 4096, glabel + ' actual vCPU sweep')
            before = generation['target_before']
            check(before['pages'] == pages and before['present'] == pages and before['query_errors'] == 0,
                  glabel + ' target fully resident before replacement')
            check(generation['old_present_before_cleanup'] == pages, glabel + ' old pages retained')
            check(generation['old_pagedout_before_cleanup'] == 0, glabel + ' old pages not paged out')
            old = generation['old_states_after_all_commits']
            check(len(old) == case['chunks'], glabel + ' old aliases count')
            object_ids = []
            for index, state in enumerate(old):
                slabel = glabel + '/old' + str(index)
                check(state['pages'] == 128 and state['present'] == 128 and state['mincore_incore'] == 128,
                      slabel + ' old chunk resident')
                check(state['query_errors'] == 0 and state['mincore_exit'] == 0 and state['paged_out'] == 0,
                      slabel + ' metadata valid')
                check(state['first_ref_count'] == 1 if independent else state['first_ref_count'] > 1,
                      slabel + ' last reference' if independent else slabel + ' shared reference control')
                if independent:
                    check(state['first_object_id'] != 0 and state['first_object_id'] == state['last_object_id'],
                          slabel + ' one nonzero object identity')
                    object_ids.append(state['first_object_id'])
            if independent:
                check(len(set(object_ids)) == len(old), glabel + ' distinct independent objects')
            check(generation['old_ref_min'] == generation['old_ref_max'] == 1 if independent
                  else generation['old_ref_min'] > 1, glabel + ' old reference range')
            empty(generation['new_after_all_commits'], glabel + '/new-before-cleanup', pages)
            empty(generation['new_after_cleanup'], glabel + '/new-after-cleanup', pages)
            for side in ('left', 'right'):
                initial = case[side + '_initial']
                check(initial['pages'] == 128 and initial['present'] == 128, glabel + '/' + side + ' resident')
                for phase in ('before_cleanup', 'after_cleanup'):
                    state = generation[side + '_' + phase]
                    for field in neighbor_keys:
                        check(state[field] == initial[field], glabel + '/' + side + '/' + phase + '/' + field)
            direct = generation['direct_free_page_delta']
            combined = generation['direct_free_plus_speculative_delta']
            if independent:
                check(direct == pages, glabel + ' exact direct physical free delta', physical=True)
                check(combined == pages, glabel + ' no speculative substitute', physical=True)
            else:
                check(abs(direct) <= 2 and abs(combined) <= 2,
                      glabel + ' control has no comparable page release', physical=True)
            rows.append({'round': case['round'], 'target_MiB': size // 1024**2,
                         'independent_objects': independent, 'generation': number,
                         'old_reference_min': generation['old_ref_min'],
                         'old_reference_max': generation['old_ref_max'],
                         'old_resident_pages': generation['old_present_before_cleanup'],
                         'expected_release_pages': pages if independent else 0,
                         'observed_direct_free_pages': direct,
                         'observed_free_plus_speculative_pages': combined,
                         'cached_host_free_delta': generation['free_page_delta'],
                         'process_footprint_delta': generation['task_footprint_delta']})
    check(positives == 12 and controls == 6, '12 positive generations and6 controls')
    return {'correctness_pass': not failures, 'physical_observation_pass': not physical_failures and positives == 12 and controls == 6,
            'pass': not failures and not physical_failures, 'assertions': assertions,
            'positive_generations': positives, 'control_generations': controls,
            'correctness_failures': failures, 'physical_observation_failures': physical_failures,
            'rows': rows,
            'limits': [
                'Synthetic own-VM native test, not production Android memory benefit or authorization to enable reclaim.',
                'System-wide direct free/speculative sysctls can include host noise; an inexact physical observation is distinct from a mapping or ownership failure.',
                'host_statistics64 may be cached/rate-limited; process footprint can debit before physical release. Neither is a primary release metric.',
                'Neighbors are verified through identity/residency/state metadata, not content reads; no guest content loads occur after commit.',
                'No madvise, memory pressure, production source/runtime helper mutation or guest operation. Peak touched synthetic RAM132MiB.'
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory; existing paths are refused')
    args = parser.parse_args()
    args.output = args.output.expanduser().resolve()
    if platform.system() != 'Darwin' or platform.machine() != 'arm64' or os.sysconf('SC_PAGESIZE') != 16384:
        parser.error('Requires Apple Silicon macOS with16KiB host pages')
    if args.output.exists():
        parser.error('Refusing existing output directory: ' + str(args.output))
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'pass': False,
              'scope': 'Synthetic HVF ownVM; max132MiB touched; no emulator operation or pressure',
              'source_sha256': {name: digest(HERE / name) for name in ('probe.mm', 'entitlements.plist', 'verify.py')}}
    assessment = {'pass': False, 'correctness_pass': False, 'physical_observation_pass': False,
                  'error': 'Native probe did not complete'}
    try:
        for name in ('probe.mm', 'entitlements.plist', 'verify.py'):
            with (args.output / name).open('xb') as stream:
                stream.write((HERE / name).read_bytes())
        binary = args.output / 'probe'
        command = ['xcrun', 'clang++', '-std=c++17', '-O2', '-arch', 'arm64', str(args.output / 'probe.mm'),
                   '-framework', 'Foundation', '-framework', 'Hypervisor', '-o', str(binary)]
        report['compile_command'] = command
        report['compile'] = capture(command, args.output, 'compile')
        if report['compile']['exit'] != 0:
            raise ValueError('Native probe compilation failed')
        command = ['codesign', '--force', '--sign', '-', '--entitlements', str(args.output / 'entitlements.plist'), str(binary)]
        report['sign_command'] = command
        report['sign'] = capture(command, args.output, 'sign')
        if report['sign']['exit'] != 0:
            raise ValueError('Output executable ad-hoc signing failed')
        report['binary_sha256'] = digest(binary)
        report['run'] = capture([str(binary)], args.output, 'run', timeout=60)
        if report['run']['exit'] != 0:
            raise ValueError('Native probe failed or timed out')
        native = json.loads(report['run']['stdout'])
        with (args.output / 'native-report.json').open('x') as stream:
            json.dump(native, stream, indent=2)
            stream.write('\n')
        assessment = assess(native)
        assessment['raw_report_sha256'] = digest(args.output / 'native-report.json')
        report['pass'] = assessment['pass']
    except (ValueError, OSError, KeyError, TypeError) as error:
        report['error'] = str(error)
        assessment['error'] = str(error)
    finally:
        report['source_unchanged'] = all(digest(HERE / name) == value for name, value in report['source_sha256'].items())
        report['pass'] = report['pass'] and report['source_unchanged']
        for name, value in (('report.json', report), ('assessment.json', assessment)):
            with (args.output / name).open('x') as stream:
                json.dump(value, stream, indent=2)
                stream.write('\n')
    print(json.dumps({'report': str(args.output / 'report.json'), 'assessment': str(args.output / 'assessment.json'),
                      'pass': report['pass'], 'correctness_pass': assessment['correctness_pass'],
                      'physical_observation_pass': assessment['physical_observation_pass'], 'error': report.get('error')}, indent=2))
    return 0 if report['pass'] else 1


if __name__ == '__main__':
    sys.exit(main())
