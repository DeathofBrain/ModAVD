#!/usr/bin/env python3
"""Analyze saved observer CSV snapshots; never invoke ADB or inspect accounts.

Totals cover the guest interface, not a game UID. Completion is an explicit
caller observation and cannot be inferred from byte counters or a quiet link.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import statistics


def stamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None: raise ValueError('UTC boundaries require an explicit timezone')
    return parsed.timestamp()


def snapshot_csv(path, receipts):
    raw = path.read_bytes()
    # Concurrent writers may have an unfinished last line: retain its hash but
    # analyze only complete CSV records. Normal observer records contain no LF.
    complete = raw if raw.endswith(b'\n') else raw[:raw.rfind(b'\n') + 1]
    receipts[str(path)] = {'sha256': hashlib.sha256(raw).hexdigest(),
        'snapshot_bytes': len(raw), 'excluded_unfinished_tail_bytes': len(raw) - len(complete)}
    return list(csv.DictReader(io.StringIO(complete.decode('utf-8'))))


def statistics_for(values):
    return {'min': min(values), 'median': statistics.median(values), 'max': max(values)} if values else None


def analyze(args):
    source = Path(args.network).resolve()
    receipts = {}
    samples = snapshot_csv(source / 'samples.csv', receipts)
    interfaces = snapshot_csv(source / 'guest_interfaces.csv', receipts)
    metadata_path = source / 'metadata.json'
    metadata = json.loads(metadata_path.read_text())
    receipts[str(metadata_path)] = {'sha256': hashlib.sha256(metadata_path.read_bytes()).hexdigest()}
    observer_summary_path = source / 'summary.json'
    observer_summary = {}
    if observer_summary_path.exists():
        summary_raw = observer_summary_path.read_bytes()
        observer_summary = json.loads(summary_raw)
        receipts[str(observer_summary_path)] = {'sha256': hashlib.sha256(summary_raw).hexdigest(),
            'snapshot_bytes': len(summary_raw)}
    completed = bool(observer_summary and observer_summary.get('stop_reason') != 'running')
    if not completed and not args.provisional:
        raise RuntimeError('Observer has not saved a final summary; use --provisional for a live snapshot')
    lower = stamp(args.start_utc) if args.start_utc else -math.inf
    upper = stamp(args.end_utc) if args.end_utc else math.inf
    sample_by_id = {int(row['sample']): row for row in samples}
    prior = {}
    series = []
    exclusions = {}
    rx_total = tx_total = 0
    for row in interfaces:
        name = row['interface']
        old = prior.get(name)
        prior[name] = row
        why = None
        if old is None: continue
        sid = int(row['sample'])
        current_sample = sample_by_id.get(sid)
        previous_sample = sample_by_id.get(sid - 1)
        if current_sample is None or previous_sample is None:
            why = 'sample_snapshot_missing_endpoint'
        elif int(old['sample']) != sid - 1:
            why = 'nonadjacent_samples'
        elif any(interface_row.get('timestamp_utc') != sample_row.get('timestamp_utc') or
                 interface_row.get('host_monotonic_ns') != sample_row.get('host_monotonic_ns')
                 for interface_row, sample_row in ((old, previous_sample), (row, current_sample))):
            why = 'sample_snapshot_timestamp_mismatch'
        elif (row.get('is_primary_route_interface') != 'True' or
              old.get('is_primary_route_interface') != 'True' or
              current_sample.get('route_verified') != 'True' or
              previous_sample.get('route_verified') != 'True' or
              any(record.get('primary_route_interface') != name
                  for record in (old, row, previous_sample, current_sample))):
            continue
        elif previous_sample.get('error') or current_sample.get('error'):
            why = 'sample_error'
        elif row.get('counter_reset') == 'True':
            why = 'counter_reset'
        if why:
            exclusions[why] = exclusions.get(why, 0) + 1
            continue
        start = stamp(old['timestamp_utc'])
        end = stamp(row['timestamp_utc'])
        if start < lower or end > upper:
            continue  # Never prorate byte counters across a selected boundary.
        seconds = (int(row['host_monotonic_ns']) - int(old['host_monotonic_ns'])) / 1e9
        rx = int(row['rx_bytes']) - int(old['rx_bytes'])
        tx = int(row['tx_bytes']) - int(old['tx_bytes'])
        if seconds <= 0 or rx < 0 or tx < 0:
            exclusions['invalid_counter_or_clock'] = exclusions.get('invalid_counter_or_clock', 0) + 1
            continue
        if not row.get('rx_delta_bytes') or int(row['rx_delta_bytes']) != rx or int(row['tx_delta_bytes']) != tx:
            exclusions['missing_or_inconsistent_recorded_delta'] = exclusions.get('missing_or_inconsistent_recorded_delta', 0) + 1
            continue
        rx_total += rx
        tx_total += tx
        series.append({'sample': sid, 'timestamp_utc': row['timestamp_utc'],
            'interval_start_utc': old['timestamp_utc'], 'interface': name,
            'elapsed_seconds': float(row['elapsed_seconds']), 'interval_seconds': seconds,
            'rx_bytes': rx, 'tx_bytes': tx, 'rx_bytes_per_second': rx / seconds,
            'tx_bytes_per_second': tx / seconds, 'cumulative_rx_bytes': rx_total,
            'cumulative_tx_bytes': tx_total})
    observed = sum(row['interval_seconds'] for row in series)
    selected_samples = [r for r in samples if lower <= stamp(r['timestamp_utc']) <= upper]
    sample_gaps = [(int(b['host_monotonic_ns']) - int(a['host_monotonic_ns'])) / 1e9
                   for a, b in zip(selected_samples, selected_samples[1:])]
    evidence = []
    for value in args.completion_evidence:
        p = Path(value).resolve()
        evidence.append({'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()})
    report = {'generated_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'status': 'final_observer_data' if completed else 'provisional_live_snapshot',
        'network_source': str(source), 'observer_stop_reason': observer_summary.get('stop_reason'),
        'selection': {'start_utc': args.start_utc, 'end_utc': args.end_utc,
            'boundary_rule': 'Include only complete adjacent intervals within boundaries; no interpolation.'},
        'caller_observed_game_state': args.completion_state,
        'caller_completion_evidence': evidence,
        'scope': 'Total guest traffic on the interface selected by the 1.1.1.1 (or configured target) route query. Not UID-specific; not proof that every game destination uses this route.',
        'attribution_limit': 'Game, updater, Google services, and background traffic can contribute. Download size and success must be verified from game state; zero-rate samples cannot establish a disconnect.',
        'selected_samples': len(selected_samples),
        'sample_error_count': sum(bool(r.get('error')) for r in selected_samples),
        'sample_interval_seconds': statistics_for(sample_gaps),
        'measured_primary_intervals': len(series), 'measured_primary_interval_seconds': observed,
        'interfaces_used': sorted({r['interface'] for r in series}),
        'verified_route_interval_rx_bytes': rx_total,
        'verified_route_interval_tx_bytes': tx_total,
        'verified_route_interval_rx_gib': rx_total / 1024**3,
        'mean_rx_bytes_per_second': rx_total / observed if observed else None,
        'mean_tx_bytes_per_second': tx_total / observed if observed else None,
        'interval_rx_bytes_per_second': statistics_for([r['rx_bytes_per_second'] for r in series]),
        'excluded_intervals': exclusions, 'memory': None,
        'privacy': 'Reads only saved numeric interface/memory CSV and hashes caller-selected evidence. Does not read host_netstat snapshots, packets, app private files, credentials, or session contents.',
        'source_snapshots': receipts}
    memory_rows = []
    if args.memory:
        mp = Path(args.memory).resolve()
        if mp.is_dir(): mp = mp / 'memory.csv'
        memory_rows = [r for r in snapshot_csv(mp, receipts) if lower <= stamp(r['timestamp_utc']) <= upper]
        metrics = {}
        for key in ('host_phys_footprint_bytes', 'host_rss_bytes', 'guest_MemAvailable_bytes',
                    'host_compressor_physical_bytes', 'host_swap_used_bytes', 'host_disk_free_bytes'):
            values = [float(r[key]) for r in memory_rows if r.get(key)]
            if values:
                metrics[key] = {'first': values[0], 'last': values[-1],
                    'change': values[-1] - values[0], 'min': min(values), 'max': max(values)}
        report['memory'] = {'samples': len(memory_rows), 'metrics_bytes': metrics,
            'host_error_samples': sum(bool(r.get('host_memory_error')) for r in memory_rows),
            'guest_error_samples': sum(bool(r.get('guest_memory_error')) for r in memory_rows),
            'disk_reserve_bytes': 20 * 1024**3,
            'scope': 'Compression/swap are whole-host metrics; RSS/phys_footprint are the emulator process. No causal attribution to this game download alone.'}
    out = Path(args.output).resolve()
    out.mkdir(exist_ok=False)
    if series:
        with (out / 'primary-traffic-series.csv').open('x', newline='') as f:
            writer = csv.DictWriter(f, list(series[0]))
            writer.writeheader()
            writer.writerows(series)
    if args.plot and series:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        panels = 3 if memory_rows else 2
        fig, axes = plt.subplots(panels, 1, figsize=(10, 3 * panels), constrained_layout=True)
        xs = [r['elapsed_seconds'] / 60 for r in series]
        axes[0].plot(xs, [r['rx_bytes_per_second'] / 1024**2 for r in series], label='Guest route-interface RX')
        axes[0].plot(xs, [r['tx_bytes_per_second'] / 1024**2 for r in series], label='TX')
        axes[0].set_ylabel('MiB/s');axes[0].legend()
        axes[1].plot(xs, [r['cumulative_rx_bytes'] / 1024**3 for r in series])
        axes[1].set_ylabel('Cumulative RX GiB')
        if memory_rows:
            for key, label in [('host_phys_footprint_bytes', 'Emulator phys_footprint'), ('guest_MemAvailable_bytes', 'Guest MemAvailable')]:
                points = [(float(r['elapsed_seconds']) / 60, float(r[key]) / 1024**2) for r in memory_rows if r.get(key)]
                if points: axes[2].plot(*zip(*points), label=label)
            axes[2].set_ylabel('MiB');axes[2].legend()
        for ax in axes:
            ax.set_xlabel('Minutes since observer start');ax.grid(alpha=.2)
        fig.suptitle('Windowed game update: whole-guest interface traffic\nCompletion requires game-state evidence')
        for suffix in ('png', 'svg'): fig.savefig(out / f'download-trend.{suffix}', dpi=160)
        plt.close(fig)
    (out / 'analysis.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--network', required=True)
    p.add_argument('--memory')
    p.add_argument('--output', required=True)
    p.add_argument('--start-utc')
    p.add_argument('--end-utc')
    p.add_argument('--completion-state', choices=('unknown', 'update_completed', 'entered_game'), default='unknown')
    p.add_argument('--completion-evidence', action='append', default=[])
    p.add_argument('--provisional', action='store_true')
    p.add_argument('--plot', action='store_true')
    analyze(p.parse_args())


if __name__ == '__main__': main()
