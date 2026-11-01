#!/usr/bin/env python3
"""Run local NAT and low-rate remote TCP controls with one memory monitor.

The coordinator must complete the guest remote preflight before invoking this
script. No emulator or ADB server is started here. This is a test runner, not a
background scheduling facility.
"""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import subprocess
import threading
import time

import soak_tests as s

def validate_preflight(path,*,expected_ipv4,expected_port,expected_framing='line64'):
    events=[]
    for line in path.read_text().splitlines():
        if line.strip():events.append(json.loads(line))
    acks=[e for e in events if e.get('event')=='heartbeat_ack']
    errors=[e for e in events if e.get('event') in ('heartbeat_error','bulk_error','connect_error','socket_error')]
    configs=[e for e in events if e.get('event')=='network_config']
    if len(configs)!=1:raise ValueError('Guest WAN preflight requires one network_config event from the framing-aware probe')
    config=configs[0]
    required={'endpoint_ipv4':str(ipaddress.IPv4Address(expected_ipv4)),'endpoint_port':expected_port,'payload_framing':expected_framing,'payload_bytes':64,'bulk_bytes':0,'interval_ms':1000}
    if any(config.get(key)!=value for key,value in required.items()):raise ValueError('Guest WAN preflight endpoint/framing/load differs from the configured run')
    if config.get('duration_seconds',0)<8:raise ValueError('Guest WAN preflight configured duration is too short')
    window=(events[-1]['monotonic_ns']-events[0]['monotonic_ns'])/1e9 if len(events)>1 else 0
    if errors or len(acks)<5 or window<8 or not any(e.get('event')=='connected' for e in events) or not any(e.get('event')=='network_complete' for e in events):
        raise ValueError('Guest WAN preflight must contain successful ~10-second checked echo: >=5 heartbeat ACKs, >=8-second recorded window, connected/completed events, no errors')
    if any(e.get('sent_bytes')!=64 or e.get('received_bytes')!=64 for e in acks):raise ValueError('Guest WAN preflight must use 64-byte heartbeats')
    if any(e.get('event') in ('bulk_ack','bulk_error') for e in events):raise ValueError('Guest WAN preflight must use bulk_bytes=0')
    if [e.get('sequence') for e in acks]!=list(range(len(acks))):raise ValueError('Guest WAN preflight heartbeat sequences must be consecutive from zero')
    gaps=[(b['monotonic_ns']-a['monotonic_ns'])/1e9 for a,b in zip(acks,acks[1:])]
    if any(gap<0 or gap>3 for gap in gaps):raise ValueError('Guest WAN preflight contains a heartbeat ACK gap over 3 seconds or regressing timestamps')
    return {'path':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'checked_acks':len(acks),'recorded_window_seconds':window,'maximum_heartbeat_ack_gap_seconds':max(gaps) if gaps else None,'checked_config':config,'endpoint_identity_note':'Endpoint and line64 framing are checked against the probe network_config event and caller configuration. Host routing may still include FakeIP/TUN/proxy.'}

def run(args):
    args.wan_ipv4=str(ipaddress.IPv4Address(args.wan_ipv4))
    preflight=validate_preflight(args.guest_wan_preflight,expected_ipv4=args.wan_ipv4,expected_port=args.wan_port)
    out=Path(args.output).resolve();out.mkdir(parents=True,exist_ok=True)
    if any((out/name).exists() for name in ('metadata.json','memory.csv','host_network.jsonl','guest_network.jsonl','orchestration.json')):
        raise FileExistsError('Prior soak evidence exists; choose a fresh --output directory')
    remote_out=out/'wan';remote_out.mkdir(parents=True,exist_ok=True)
    adb=s.Adb(args);stop=threading.Event();processes={};hosts=[];opened=[];mon=None;success=False
    server=s.EchoServer(('127.0.0.1',0),s.EchoHandler)
    threading.Thread(target=server.serve_forever,daemon=True,name='local-echo-server').start()
    local_port=server.server_address[1]
    common=vars(args).copy();common['guest_wan_preflight']=str(args.guest_wan_preflight.resolve())
    local=argparse.Namespace(**{**common,'host_endpoint':'127.0.0.1','guest_endpoint':'10.0.2.2','echo_port':local_port,'payload_bytes':65536,'bulk_bytes':32*1024**2,'heartbeat_ms':1000,'payload_framing':'binary'})
    remote=argparse.Namespace(**{**common,'host_endpoint':args.wan_ipv4,'guest_endpoint':args.wan_ipv4,'echo_port':args.wan_port,'payload_bytes':64,'bulk_bytes':0,'heartbeat_ms':1000,'payload_framing':'line'})
    groups={'local':(local,out),'wan':(remote,remote_out)}
    scopes={'local':'guest NAT to host loopback echo server; WAN not covered','wan':f'Public echo service {args.wan_hostname}:{args.wan_port}, reached at {args.wan_ipv4} through current host routing. FakeIP/TUN/proxy may intervene; this does not demonstrate direct Internet routing.'}
    metadata={}
    for name,(group,directory) in groups.items():
        metadata[name]={**vars(group),'started_utc':s.utc(),'network_scope':scopes[name],'guest_payload':s.REMOTE,'guest_wan_preflight_receipt':preflight,'shared_memory_csv':str(out/'memory.csv')}
        s.write_json(directory/'metadata.json',metadata[name])
    started=time.monotonic()
    orchestration={'status':'running','started_utc':s.utc(),'duration_seconds':args.duration,'workload_label':args.workload_label,'memory_monitor_count':1,'groups':{name:str(directory) for name,(_,directory) in groups.items()},'preflight':preflight}
    s.write_json(out/'orchestration.json',orchestration)
    try:
        with s.MemoryMonitor(args,out,adb) as mon:
            mon.set_phase(args.workload_label)
            for name,(group,directory) in groups.items():
                host=threading.Thread(target=s.host_tcp,args=(group,group.echo_port,directory/'host_network.jsonl',stop),daemon=True,name=f'{name}-host-tcp')
                host.start();hosts.append(host)
                stdout=(directory/'guest_network.jsonl').open('w');stderr=(directory/'guest_network.stderr.log').open('w');opened.extend([stdout,stderr])
                cmd=adb.prefix+['shell',s.REMOTE,'tcp',group.guest_endpoint,str(group.echo_port),str(args.duration),'1000',str(group.payload_bytes),str(group.bulk_bytes)]
                if group.payload_framing=='line':cmd.append('line64')
                metadata[name]['guest_command']=cmd;s.write_json(directory/'metadata.json',metadata[name])
                processes[name]=subprocess.Popen(cmd,stdout=stdout,stderr=stderr,text=True)
            while True:
                elapsed=time.monotonic()-started
                if elapsed>=args.duration and all(p.poll() is not None for p in processes.values()) and all(not t.is_alive() for t in hosts):break
                if elapsed>args.duration+120:raise TimeoutError('TCP clients did not finish within duration + 120 seconds')
                mon.wait(min(1,max(.05,args.duration+120-elapsed)))
        success=True
    finally:
        stop.set()
        for proc in processes.values():
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
        for host in hosts:host.join(timeout=15)
        for f in opened:f.close()
        server.shutdown();server.server_close()
        ended=s.utc();elapsed=time.monotonic()-started
        for name,(_,directory) in groups.items():
            metadata[name].update(finished_utc=ended,elapsed_seconds=elapsed,guest_returncode=processes[name].returncode if name in processes else None,memory_monitor_error=repr(mon.failure) if mon is not None and mon.failure is not None else None)
            s.write_json(directory/'metadata.json',metadata[name]);s.summarize_network(directory)
        memory=s.summarize_memory(out)
        statuses={name:json.loads((directory/'network_summary.json').read_text())['status'] for name,(_,directory) in groups.items()}
        orchestration.update(finished_utc=ended,elapsed_seconds=elapsed,execution_completed=success,network_statuses=statuses,memory_sample_count=memory.get('sample_count',0),memory_host_error_samples=memory.get('host_error_samples',0),memory_guest_error_samples=memory.get('guest_error_samples',0))
        orchestration['status']='passed' if success and statuses=={'local':'passed','wan':'passed'} and memory.get('sample_count',0)>1 and not memory.get('host_error_samples') and not memory.get('guest_error_samples') else 'failed' if 'failed' in statuses.values() else 'blocked'
        s.write_json(out/'orchestration.json',orchestration)
    print(json.dumps(orchestration,indent=2))
    return orchestration

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb',default='${WORKSPACE}/sdk/platform-tools/adb');p.add_argument('--adb-port',type=int,default=22150);p.add_argument('--serial',default='emulator-22160')
    p.add_argument('--emulator-pid',type=int,required=True);p.add_argument('--output',default='results/soak');p.add_argument('--sample-interval',type=float,default=5)
    p.add_argument('--duration',type=int,default=7200);p.add_argument('--workload-label',default='idle_non_game_control')
    p.add_argument('--wan-ipv4',required=True);p.add_argument('--wan-port',type=int,default=4242);p.add_argument('--wan-hostname',default='tcpbin.com');p.add_argument('--guest-wan-preflight',type=Path,required=True)
    args=p.parse_args()
    if args.duration<=0 or args.sample_interval<=0:p.error('duration and sample interval must be positive')
    run(args)

if __name__=='__main__':main()
