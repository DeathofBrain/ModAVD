#!/usr/bin/env python3
"""Read guest interface counters every 5 s; never inspect packets or accounts.

Run before game preparation and stop with SIGINT after visual verification.
Default maximum runtime is 3600 seconds. Traffic is per guest interface, not
per game UID, and background Google traffic cannot establish game-update success.
"""
import argparse
import collections
import ipaddress
import csv
import json
from pathlib import Path
import re
import signal
import subprocess
import threading
import time

from soak_tests import Adb,utc,write_json

INTERFACE_FIELDS=['sample','timestamp_utc','host_monotonic_ns','elapsed_seconds','interface','primary_route_interface','is_primary_route_interface','rx_bytes','tx_bytes','interval_seconds','rx_delta_bytes','tx_delta_bytes','rx_bytes_per_second','tx_bytes_per_second','counter_reset','sample_duration_ms']
SAMPLE_FIELDS=['sample','timestamp_utc','host_monotonic_ns','elapsed_seconds','primary_route_interface','route_verified','non_loopback_interface_count','sample_duration_ms','error']

def parse_guest(text):
    route,sep,counters=text.partition('\n__AVD_NETDEV__\n')
    if not sep:raise ValueError('Missing read-only counter output delimiter')
    match=re.search(r'(?:^|\s)dev\s+([A-Za-z0-9_.:-]+)(?=\s|$)',route)
    primary=match.group(1) if match else None
    interfaces={}
    for line in counters.splitlines():
        if ':' not in line:continue
        name,values=line.rsplit(':',1);name=name.strip();parts=values.split()
        if name=='lo' or len(parts)<16:continue
        if not all(p.isdigit() for p in parts[:16]):continue
        interfaces[name]={'rx_bytes':int(parts[0]),'tx_bytes':int(parts[8])}
    if not interfaces:raise ValueError('No non-loopback interface counters parsed')
    if primary not in interfaces:primary=None
    return primary,interfaces

def summarize(rows,samples,elapsed,stopped,host_snapshots):
    per_interface=collections.defaultdict(lambda:{'rx_bytes':0,'tx_bytes':0,'valid_intervals':0,'observed_interval_seconds':0,'counter_resets':0})
    primary={'rx_bytes':0,'tx_bytes':0,'valid_intervals':0,'observed_interval_seconds':0};known=set()
    sample_primary={r['sample']:r.get('primary_route_interface') or None for r in samples}
    for r in rows:
        entry=per_interface[r['interface']]
        if r.get('counter_reset'):entry['counter_resets']+=1
        if r.get('rx_delta_bytes') is None:continue
        entry['rx_bytes']+=r['rx_delta_bytes'];entry['tx_bytes']+=r['tx_delta_bytes'];entry['valid_intervals']+=1;entry['observed_interval_seconds']+=r['interval_seconds']
        # Count one verified routing interface only. A changed/unknown route
        # invalidates that interval for the primary aggregate, never a fallback sum.
        if r['is_primary_route_interface'] and sample_primary.get(r['sample']-1)==r['interface']:
            known.add(r['interface']);primary['rx_bytes']+=r['rx_delta_bytes'];primary['tx_bytes']+=r['tx_delta_bytes'];primary['valid_intervals']+=1;primary['observed_interval_seconds']+=r['interval_seconds']
    for entry in list(per_interface.values())+[primary]:
        seconds=entry['observed_interval_seconds']
        entry['mean_rx_bytes_per_second']=entry['rx_bytes']/seconds if seconds else None
        entry['mean_tx_bytes_per_second']=entry['tx_bytes']/seconds if seconds else None
    primary['interfaces']=sorted(known)
    if not primary['valid_intervals']:
        primary.update(status='unavailable',rx_bytes=None,tx_bytes=None)
    else:primary['status']='measured_for_verified_intervals_only'
    return {'finished_utc':utc(),'observer_wall_elapsed_seconds':elapsed,'stop_reason':stopped,'sample_count':len(samples),'sample_errors':sum(bool(r.get('error')) for r in samples),'samples_with_verified_route':sum(bool(r.get('route_verified')) for r in samples),'primary_route_interface_traffic':primary,'per_interface_traffic_do_not_sum':dict(per_interface),'host_netstat_snapshots':host_snapshots,'measurement_scope':'Guest interface total traffic across every app and background service; not precise game UID attribution. Primary totals include only adjacent samples with the same verified route interface; unknown routes, gaps, route changes, and counter resets are excluded. No all-interface sum is claimed. Mean rates divide measured byte deltas by actual host-monotonic intervals, including ADB timing uncertainty. Host netstat snapshots are background context and are not summed or attributed to the emulator.','game_update_completion':'Must be verified against actual game screen/state. Background Google or other traffic cannot establish successful game hot update. No payload, packet contents, credentials, or account data are collected.'}

def run(args):
    target=str(ipaddress.IPv4Address(args.route_target));out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    paths=[out/'guest_interfaces.csv',out/'samples.csv']+([out/'host_netstat.csv'] if args.host_netstat else [])
    if any(p.exists() for p in paths):raise FileExistsError('Observer output already exists; choose a fresh --output directory')
    stop=threading.Event();stop_reason={'reason':'max_duration'}
    def on_signal(signum,_frame):stop_reason['reason']=signal.Signals(signum).name;stop.set()
    old={sig:signal.signal(sig,on_signal) for sig in (signal.SIGINT,signal.SIGTERM)}
    adb=Adb(args);started=time.monotonic();previous={};previous_ns=None;rows=[];samples=[];snapshot_count=0
    metadata={**vars(args),'started_utc':utc(),'route_query':f'ip -4 route get {target}','interface_counter_source':'/proc/net/dev','read_only':True,'attribution':'interface total only, not game UID'}
    write_json(out/'metadata.json',metadata)
    interfaces_file=paths[0].open('x',newline='');samples_file=paths[1].open('x',newline='');host_file=paths[2].open('x',newline='') if args.host_netstat else None
    iw=csv.DictWriter(interfaces_file,INTERFACE_FIELDS);iw.writeheader();sw=csv.DictWriter(samples_file,SAMPLE_FIELDS);sw.writeheader()
    hw=csv.DictWriter(host_file,['sample','timestamp_utc','host_monotonic_ns','snapshot','error']) if host_file else None
    if hw:hw.writeheader()
    script=f"ip -4 route get {target} 2>&1; printf '\\n__AVD_NETDEV__\\n'; cat /proc/net/dev"
    target_time=started
    try:
        while not stop.is_set() and time.monotonic()-started<args.max_seconds:
            sample_id=len(samples)+1;began=time.monotonic();record={'sample':sample_id};primary=None
            try:
                result=adb.run(['shell',script],timeout=args.adb_timeout,check=False)
                if result.returncode:raise RuntimeError(f'ADB read returned {result.returncode}')
                primary,current=parse_guest(result.stdout)
                stamp=time.monotonic_ns();timestamp=utc();interval=(stamp-previous_ns)/1e9 if previous_ns is not None else None
                for name,counters in current.items():
                    row={'sample':sample_id,'timestamp_utc':timestamp,'host_monotonic_ns':stamp,'elapsed_seconds':(stamp/1e9-started),'interface':name,'primary_route_interface':primary or '', 'is_primary_route_interface':name==primary,**counters,'interval_seconds':interval,'rx_delta_bytes':None,'tx_delta_bytes':None,'counter_reset':False,'sample_duration_ms':(time.monotonic()-began)*1000}
                    if name in previous and interval and interval>0:
                        rx=counters['rx_bytes']-previous[name]['rx_bytes'];tx=counters['tx_bytes']-previous[name]['tx_bytes']
                        if rx<0 or tx<0:row['counter_reset']=True
                        else:row.update(rx_delta_bytes=rx,tx_delta_bytes=tx,rx_bytes_per_second=rx/interval,tx_bytes_per_second=tx/interval)
                    rows.append(row);iw.writerow(row)
                record.update(timestamp_utc=timestamp,host_monotonic_ns=stamp,elapsed_seconds=stamp/1e9-started,primary_route_interface=primary or '',route_verified=primary is not None,non_loopback_interface_count=len(current))
                previous=current;previous_ns=stamp
            except Exception as exc:
                stamp=time.monotonic_ns();record.update(timestamp_utc=utc(),host_monotonic_ns=stamp,elapsed_seconds=stamp/1e9-started,primary_route_interface='',route_verified=False,non_loopback_interface_count=0,error=str(exc))
                previous={};previous_ns=None
            record['sample_duration_ms']=(time.monotonic()-began)*1000;samples.append(record);sw.writerow(record);interfaces_file.flush();samples_file.flush()
            if hw:
                try:
                    result=subprocess.run(['netstat','-ibn'],capture_output=True,text=True,timeout=8,check=True)
                    hw.writerow({'sample':sample_id,'timestamp_utc':utc(),'host_monotonic_ns':time.monotonic_ns(),'snapshot':result.stdout});snapshot_count+=1
                except Exception as exc:hw.writerow({'sample':sample_id,'timestamp_utc':utc(),'host_monotonic_ns':time.monotonic_ns(),'error':str(exc)})
                host_file.flush()
            if sample_id%12==0:write_json(out/'summary.json',summarize(rows,samples,time.monotonic()-started,'running',snapshot_count))
            target_time=max(target_time+args.interval,time.monotonic())
            stop.wait(min(max(0,target_time-time.monotonic()),max(0,args.max_seconds-(time.monotonic()-started))))
    finally:
        interfaces_file.close();samples_file.close()
        if host_file:host_file.close()
        for sig,handler in old.items():signal.signal(sig,handler)
        summary=summarize(rows,samples,time.monotonic()-started,stop_reason['reason'],snapshot_count)
        write_json(out/'summary.json',summary)
        print(json.dumps(summary,ensure_ascii=False,indent=2))
    return summary

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb',default='${WORKSPACE}/sdk/platform-tools/adb');p.add_argument('--adb-port',type=int,default=22150);p.add_argument('--serial',default='emulator-22160')
    p.add_argument('--output',default='results/game_network');p.add_argument('--interval',type=float,default=5);p.add_argument('--max-seconds',type=float,default=3600);p.add_argument('--adb-timeout',type=float,default=15);p.add_argument('--route-target',default='1.1.1.1');p.add_argument('--host-netstat',action='store_true')
    args=p.parse_args()
    if args.interval<=0 or args.max_seconds<=0 or args.adb_timeout<=0:p.error('interval, max seconds, and ADB timeout must be positive')
    run(args)

if __name__=='__main__':main()
