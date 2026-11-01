#!/usr/bin/env python3
"""Memory reclamation and long-lived TCP probes for a separately launched AVD.

No emulator process or ADB server is started here. Every command uses the ADB
binary, server port, and serial supplied by the caller. Only a temporary test
payload under /data/local/tmp is written to the guest.
"""
import argparse
import csv
import ctypes
import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import shutil
import socket
import socketserver
import statistics
import subprocess
import threading
import time

REMOTE='/data/local/tmp/official_avd_probe'

def utc(): return dt.datetime.now(dt.timezone.utc).isoformat()
def write_json(path,value): pathlib.Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')

class Adb:
    def __init__(self,args): self.prefix=[args.adb,'-P',str(args.adb_port),'-s',args.serial]
    def run(self,args,timeout=30,check=True):
        return subprocess.run(self.prefix+list(args),capture_output=True,text=True,timeout=timeout,check=check)

class HostMemory:
    def __init__(self,pid):
        self.pid=pid
        self.lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
        self.lib.proc_pid_rusage.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_void_p]
        self.lib.proc_pid_rusage.restype=ctypes.c_int
    def sample(self):
        result={}; buf=ctypes.create_string_buffer(1024)
        # rusage_info_v2 begins UUID[16], then 64-bit fields. Field indices 6/7
        # are resident_size/phys_footprint; the oversized buffer avoids ABI drift.
        if self.lib.proc_pid_rusage(self.pid,2,ctypes.byref(buf))==0:
            fields=(ctypes.c_uint64*18).from_buffer(buf,16)
            result.update(host_rss_bytes=fields[6],host_phys_footprint_bytes=fields[7])
        else: result['host_memory_error']='proc_pid_rusage errno='+str(ctypes.get_errno())
        vm=subprocess.run(['vm_stat'],capture_output=True,text=True,timeout=5).stdout
        page=int(re.search(r'page size of (\d+)',vm).group(1))
        counters={k:int(v) for k,v in re.findall(r'^(.+?):\s+(\d+)\.',vm,re.M)}
        for name,key in [('host_compressor_physical_bytes','Pages occupied by compressor'),('host_compressor_uncompressed_bytes','Pages stored in compressor'),('host_free_bytes','Pages free'),('host_swapins_pages','Swapins'),('host_swapouts_pages','Swapouts')]:
            if key in counters: result[name]=counters[key] if name.endswith('_pages') else counters[key]*page
        swap=subprocess.run(['sysctl','vm.swapusage'],capture_output=True,text=True,timeout=5).stdout
        for key,value,unit in re.findall(r'(total|used|free)\s*=\s*([\d.]+)([KMG])',swap):
            result['host_swap_'+key+'_bytes']=round(float(value)*{'K':1024,'M':1024**2,'G':1024**3}[unit])
        return result

MEM_FIELDS=['timestamp_utc','elapsed_seconds','phase','sample_duration_ms','host_rss_bytes','host_phys_footprint_bytes','host_compressor_physical_bytes','host_compressor_uncompressed_bytes','host_free_bytes','host_swap_total_bytes','host_swap_used_bytes','host_swap_free_bytes','host_swapins_pages','host_swapouts_pages','guest_MemTotal_bytes','guest_MemAvailable_bytes','guest_MemFree_bytes','guest_Cached_bytes','guest_SwapTotal_bytes','guest_SwapFree_bytes','guest_memory_error','host_memory_error']
MEM_FIELDS.append('host_disk_free_bytes')

class MemoryMonitor:
    def __init__(self,args,out,adb=None):
        self.args=args; self.out=pathlib.Path(out); self.adb=adb; self.phase='baseline'
        self.stop_event=threading.Event(); self.thread=None; self.start=time.monotonic();self.failure=None
    def set_phase(self,phase): self.phase=phase
    def sample(self,host):
        started=time.monotonic(); row={'timestamp_utc':utc(),'elapsed_seconds':round(started-self.start,3),'phase':self.phase}
        row['host_disk_free_bytes']=shutil.disk_usage(self.out).free
        try: row.update(host.sample())
        except Exception as e: row['host_memory_error']=str(e)
        if self.adb:
            try:
                result=self.adb.run(['shell','cat','/proc/meminfo'],timeout=8)
                for name,value in re.findall(r'^(MemTotal|MemAvailable|MemFree|Cached|SwapTotal|SwapFree):\s+(\d+) kB',result.stdout,re.M): row['guest_'+name+'_bytes']=int(value)*1024
            except Exception as e: row['guest_memory_error']=str(e)
        row['sample_duration_ms']=round((time.monotonic()-started)*1000,3)
        return row
    def run(self):
        try:
            host=HostMemory(self.args.emulator_pid)
            with (self.out/'memory.csv').open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=MEM_FIELDS,extrasaction='ignore');writer.writeheader()
                target=time.monotonic()
                while not self.stop_event.is_set():
                    writer.writerow(self.sample(host)); f.flush()
                    target+=self.args.sample_interval
                    self.stop_event.wait(max(0,target-time.monotonic()))
        except BaseException as exc:
            self.failure=exc;self.stop_event.set()
    def raise_if_failed(self):
        if self.failure is not None:raise RuntimeError('Memory monitor failed: '+repr(self.failure)) from self.failure
    def wait(self,seconds):
        self.stop_event.wait(seconds);self.raise_if_failed()
    def __enter__(self):
        self.out.mkdir(parents=True,exist_ok=True)
        self.thread=threading.Thread(target=self.run,name='memory-monitor',daemon=True); self.thread.start(); return self
    def __exit__(self,*exc):
        self.stop_event.set();self.thread.join(timeout=20)
        if exc[0] is None:
            if self.thread.is_alive():raise RuntimeError('Memory monitor did not stop within 20 seconds')
            self.raise_if_failed()

def prepare(args):
    binary=pathlib.Path(args.probe).resolve(); adb=Adb(args)
    a=adb.run(['push',str(binary),REMOTE],timeout=60)
    b=adb.run(['shell','chmod','755',REMOTE])
    c=adb.run(['shell',REMOTE,'memory','16777216','1','4096'],timeout=30,check=False)
    out=pathlib.Path(args.output);out.mkdir(parents=True,exist_ok=True)
    write_json(out/'probe_prepare.json',{'timestamp_utc':utc(),'binary':str(binary),'sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),'push_stdout':a.stdout,'push_stderr':a.stderr,'chmod_returncode':b.returncode,'smoke_returncode':c.returncode,'smoke_stdout':c.stdout,'smoke_stderr':c.stderr})
    if c.returncode or 'memory_released' not in c.stdout: raise RuntimeError('Android payload smoke test failed; inspect probe_prepare.json')

def memory(args):
    out=pathlib.Path(args.output);out.mkdir(parents=True,exist_ok=True);adb=Adb(args)
    began_test=time.monotonic()
    metadata=vars(args).copy(); metadata.update(started_utc=utc(),status='running',test='1 GiB anonymous guest allocation/touch/release',guest_payload=REMOTE)
    write_json(out/'metadata.json',metadata)
    phases=[]
    success=False
    try:
        with MemoryMonitor(args,out,adb) as mon:
            mon.wait(args.baseline_seconds)
            for r in range(1,args.rounds+1):
                mon.set_phase(f'round_{r}_allocate_hold')
                started=utc();began=time.monotonic()
                result=adb.run(['shell',REMOTE,'memory',str(args.allocation_bytes),str(args.hold_seconds),str(args.guest_page_size)],timeout=args.hold_seconds+90,check=False)
                (out/f'round_{r}.stdout.jsonl').write_text(result.stdout)
                (out/f'round_{r}.stderr.log').write_text(result.stderr)
                released=time.monotonic()
                valid=result.returncode==0 and 'memory_touched' in result.stdout and 'memory_released' in result.stdout
                row={'round':r,'started_utc':started,'returncode':result.returncode,'allocation_hold_seconds':round(released-began,3),'valid':valid,'observe_seconds':args.observe_seconds,'observation_completed':False}
                phases.append(row);write_json(out/'rounds.json',phases)
                if not valid: raise RuntimeError(f'round {r} allocation failed; memory.csv retained')
                mon.raise_if_failed();mon.set_phase(f'round_{r}_released_observe')
                observe_start=time.monotonic()
                row.update(observe_started_utc=utc(),observe_started_monotonic=observe_start)
                write_json(out/'rounds.json',phases)
                try:
                    mon.wait(args.observe_seconds)
                    row['observation_completed']=True
                finally:
                    end=time.monotonic()
                    row.update(observe_finished_utc=utc(),observe_finished_monotonic=end,actual_observe_seconds=end-observe_start)
                    write_json(out/'rounds.json',phases)
        success=True
    finally:
        metadata.update(status='completed' if success else 'incomplete',finished_utc=utc(),elapsed_seconds=time.monotonic()-began_test,completed_rounds=sum(r['observation_completed'] for r in phases))
        write_json(out/'metadata.json',metadata);summarize_memory(out)

class EchoHandler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data=self.request.recv(1048576)
            if not data:return
            self.request.sendall(data)
class EchoServer(socketserver.ThreadingTCPServer):
    allow_reuse_address=True;daemon_threads=True
    def handle_error(self,request,client_address): pass

def host_tcp(args,port,path,stop_event):
    deadline=time.monotonic()+args.duration;seq=0;connection=None;began=time.monotonic()
    framing=getattr(args,'payload_framing','binary')
    if framing=='line' and (args.payload_bytes!=64 or args.bulk_bytes!=0):
        raise ValueError('line framing requires payload_bytes=64 and bulk_bytes=0')
    pattern=bytes(range(251))*((args.payload_bytes+501)//251)
    with pathlib.Path(path).open('w') as f:
        def emit(event,**kw):
            f.write(json.dumps({'timestamp_utc':utc(),'event':event,'monotonic_ns':time.monotonic_ns(),**kw})+'\n');f.flush()
        emit('network_config',payload_framing='line64' if framing=='line' else 'binary',endpoint_ipv4=args.host_endpoint,endpoint_port=port,duration_seconds=args.duration,interval_ms=args.heartbeat_ms,payload_bytes=args.payload_bytes,bulk_bytes=args.bulk_bytes)
        while time.monotonic()<deadline and not stop_event.is_set():
            if connection is None:
                t=time.monotonic_ns()
                try: connection=socket.create_connection((args.host_endpoint,port),timeout=10);connection.settimeout(10);emit('connected',sequence=seq,elapsed_ns=time.monotonic_ns()-t)
                except OSError as e:emit('connect_error',sequence=seq,errno=e.errno,elapsed_ns=time.monotonic_ns()-t);stop_event.wait(1);continue
            offset=seq%251
            payload=(f'{seq:016x}'.encode('ascii')+bytes(33+(seq+i)%94 for i in range(16,63))+b'\n') if framing=='line' else pattern[offset:offset+args.payload_bytes]
            t=time.monotonic_ns();sent=received=0;error=0
            try:
                connection.sendall(payload);sent=len(payload);chunks=[]
                while received<len(payload):
                    data=connection.recv(len(payload)-received)
                    if not data:raise ConnectionResetError('EOF')
                    chunks.append(data);received+=len(data)
                if b''.join(chunks)!=payload:raise ValueError('echo content differs')
            except (OSError,ValueError) as e:
                error=getattr(e,'errno',None) or 10001;connection.close();connection=None
            bulk=seq*args.payload_bytes<args.bulk_bytes
            emit(('bulk_error' if error else 'bulk_ack') if bulk else ('heartbeat_error' if error else 'heartbeat_ack'),sequence=seq,sent_bytes=sent,received_bytes=received,elapsed_ns=time.monotonic_ns()-t,errno=error)
            seq+=1
            stop_event.wait((0 if bulk else max(0,args.heartbeat_ms/1000-(time.monotonic_ns()-t)/1e9)) if not error else 1)
        if connection:connection.close()
        emit('network_complete',sequence=seq,elapsed_ns=round((time.monotonic()-began)*1e9))

def soak(args):
    if args.payload_framing=='line' and (args.payload_bytes!=64 or args.bulk_bytes!=0):
        raise ValueError('line framing requires --payload-bytes 64 --bulk-bytes 0')
    out=pathlib.Path(args.output);out.mkdir(parents=True,exist_ok=True);adb=Adb(args)
    server=EchoServer(('127.0.0.1',args.echo_port),EchoHandler) if args.local_echo else None
    port=server.server_address[1] if server else args.echo_port
    if server: threading.Thread(target=server.serve_forever,daemon=True,name='echo-server').start()
    metadata=vars(args).copy();metadata.update(started_utc=utc(),actual_echo_port=port,network_scope='guest NAT to host echo server; WAN not covered' if server else 'configured external TCP echo endpoint',guest_payload=REMOTE)
    write_json(out/'metadata.json',metadata)
    stop=threading.Event();host=threading.Thread(target=host_tcp,args=(args,port,out/'host_network.jsonl',stop),name='host-tcp',daemon=True)
    started=time.monotonic();returncode=None;mon=None
    try:
        with MemoryMonitor(args,out,adb) as mon:
            mon.set_phase(args.workload_label);host.start()
            with (out/'guest_network.jsonl').open('w') as f,(out/'guest_network.stderr.log').open('w') as err:
                command=adb.prefix+['shell',REMOTE,'tcp',args.guest_endpoint,str(port),str(args.duration),str(args.heartbeat_ms),str(args.payload_bytes),str(args.bulk_bytes)]
                if args.payload_framing=='line':command.append('line64')
                metadata['guest_command']=command;write_json(out/'metadata.json',metadata)
                proc=subprocess.Popen(command,stdout=f,stderr=err,text=True)
                try:returncode=proc.wait(timeout=args.duration+120)
                except subprocess.TimeoutExpired:proc.kill();proc.wait();raise
            # A dead guest probe must not silently truncate the host control and
            # memory observation window.
            remaining=args.duration-(time.monotonic()-started)
            if remaining>0:mon.wait(remaining)
    finally:
        stop.set();host.join(timeout=15)
        if server:server.shutdown();server.server_close()
        metadata.update(finished_utc=utc(),elapsed_seconds=round(time.monotonic()-started,3),guest_returncode=returncode,memory_monitor_error=repr(mon.failure) if mon is not None and mon.failure is not None else None)
        write_json(out/'metadata.json',metadata)
        summarize_network(out)
        summarize_memory(out)

def quantile(values,q):
    if not values:return None
    values=sorted(values);idx=(len(values)-1)*q;low=math.floor(idx);high=math.ceil(idx)
    return values[low]+(values[high]-values[low])*(idx-low)

def summarize_network(out):
    out=pathlib.Path(out);all_results={}
    meta_path=out/'metadata.json';metadata=json.loads(meta_path.read_text()) if meta_path.exists() else {}
    expected=metadata.get('duration');interval=metadata.get('heartbeat_ms',1000)/1000
    for label in ['host','guest']:
        source=out/f'{label}_network.jsonl'
        if not source.exists():continue
        events=[];invalid=0
        for line in source.read_text().splitlines():
            try:events.append(json.loads(line))
            except ValueError:invalid+=1
        acks=[e for e in events if e.get('event')=='heartbeat_ack']
        connections=[e for e in events if e.get('event')=='connected']
        errors=[e for e in events if e.get('event') in ('heartbeat_error','bulk_error','connect_error','socket_error')]
        bulk=[e for e in events if e.get('event')=='bulk_ack']
        rtts=[e['elapsed_ns']/1e6 for e in acks]
        window=(events[-1]['monotonic_ns']-events[0]['monotonic_ns'])/1e9 if len(events)>1 else 0
        outages=[];request_outages=[];outage_start=None;request_start=None
        for e in events:
            if e.get('event') in ('heartbeat_error','bulk_error','connect_error','socket_error') and outage_start is None:
                outage_start=e['monotonic_ns'];request_start=outage_start-e.get('elapsed_ns',0)
            if e.get('event') in ('heartbeat_ack','bulk_ack') and outage_start is not None:
                outages.append((e['monotonic_ns']-outage_start)/1e9)
                request_outages.append((e['monotonic_ns']-request_start)/1e9)
                outage_start=None;request_start=None
        tx=sum(e.get('sent_bytes',0) for e in events);rx=sum(e.get('received_bytes',0) for e in events)
        bulk_start=bulk[0]['monotonic_ns']-bulk[0]['elapsed_ns'] if bulk else 0
        bulk_elapsed=(bulk[-1]['monotonic_ns']-bulk_start)/1e9 if bulk else 0
        bulk_rx=sum(e['received_bytes'] for e in bulk)
        ack_span=(acks[-1]['monotonic_ns']-acks[0]['monotonic_ns'])/1e9 if len(acks)>1 else 0
        completed=any(e.get('event')=='network_complete' for e in events)
        tolerance=max(2,interval+1)
        duration_ok=expected is not None and window>=max(0,expected-tolerance)
        required_ack_span=max(0,expected-bulk_elapsed-max(3,3*interval)) if expected is not None else None
        ack_gaps=[(b['monotonic_ns']-a['monotonic_ns'])/1e9 for a,b in zip(acks,acks[1:])]
        gap_threshold=max(3*interval,3)
        max_ack_gap=max(ack_gaps) if ack_gaps else None
        # The clients sleep between requests rather than following an absolute
        # real-time schedule. Allow 1% scheduling overhead in the count while
        # independently requiring full duration/span and no ACK gap above 3 s.
        ack_count_fraction=.99
        minimum_ack_count=max(1,math.floor(required_ack_span/interval*ack_count_fraction)) if required_ack_span is not None and interval>0 else None
        ack_count_ok=minimum_ack_count is not None and len(acks)>=minimum_ack_count
        ack_gap_ok=len(acks)>1 and all(0<=gap<=gap_threshold for gap in ack_gaps)
        ack_coverage_ok=bool(acks) and required_ack_span is not None and ack_span>=required_ack_span and ack_count_ok and ack_gap_ok
        reasons=[]
        if not completed:reasons.append('No network_complete event')
        if not duration_ok:reasons.append('Recorded event window is shorter than expected duration minus timestamp/heartbeat tolerance, or expected duration is missing')
        if not ack_coverage_ok:reasons.append('Checked heartbeat ACKs do not span the required observation window')
        if not ack_count_ok:reasons.append('Checked heartbeat ACK count is below the duration/interval requirement')
        if not ack_gap_ok:reasons.append(f'Adjacent checked heartbeat ACK gap exceeds {gap_threshold:g} seconds, timestamps regress, or fewer than two ACKs exist')
        if errors:reasons.append(f'{len(errors)} socket/connect/heartbeat/bulk errors')
        if invalid:reasons.append(f'{invalid} invalid JSONL lines')
        if not acks:reasons.append('No checked heartbeat ACKs')
        if label=='guest' and metadata.get('guest_returncode') not in (None,0):reasons.append('Guest ADB/probe process returned nonzero')
        failed=bool(errors or invalid or (completed and (not acks or not ack_count_ok or not ack_gap_ok)) or (label=='guest' and metadata.get('guest_returncode') not in (None,0)))
        status='failed' if failed else 'passed' if completed and duration_ok and ack_coverage_ok else 'blocked'
        all_results[label]={'status':status,'status_reasons':reasons,'expected_duration_seconds':expected,'duration_tolerance_seconds':tolerance,'duration_ok':duration_ok,'heartbeat_coverage_ok':ack_coverage_ok,'heartbeat_span_seconds':ack_span,'minimum_heartbeat_span_seconds':required_ack_span,'acks':len(acks),'connections':len(connections),'reconnections':max(0,len(connections)-1),'heartbeat_errors':sum(e.get('event')=='heartbeat_error' for e in errors),'bulk_errors':sum(e.get('event')=='bulk_error' for e in errors),'connect_errors':sum(e.get('event')=='connect_error' for e in errors),'socket_errors':sum(e.get('event')=='socket_error' for e in errors),'invalid_lines':invalid,'window_seconds':window,'sent_bytes':tx,'received_bytes':rx,'verified_received_bytes':sum(e.get('received_bytes',0) for e in acks+bulk),'achieved_receive_bytes_per_second':rx/window if window else None,'bulk_receive_bytes':bulk_rx,'bulk_seconds':bulk_elapsed,'bulk_receive_bytes_per_second':bulk_rx/bulk_elapsed if bulk_elapsed else None,'rtt_median_ms':statistics.median(rtts) if rtts else None,'rtt_p95_ms':quantile(rtts,.95),'rtt_max_ms':max(rtts) if rtts else None,'recovery_seconds':outages,'detected_error_to_ack_seconds':outages,'failed_request_start_to_ack_seconds':request_outages,'reconnect_handshake_seconds':[e.get('elapsed_ns',0)/1e9 for e in connections[1:]],'unrecovered_outage':outage_start is not None,'completed':completed}
        all_results[label].update(minimum_heartbeat_ack_count=minimum_ack_count,heartbeat_ack_count_fraction=ack_count_fraction,heartbeat_ack_count_ok=ack_count_ok,maximum_heartbeat_ack_gap_seconds=max_ack_gap,heartbeat_ack_gap_threshold_seconds=gap_threshold,heartbeat_ack_gaps_ok=ack_gap_ok)
        with (out/f'{label}_network.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=['event','monotonic_ns','timestamp_utc','sequence','sent_bytes','received_bytes','elapsed_ns','errno'],extrasaction='ignore');writer.writeheader();writer.writerows(events)
    overall='passed' if set(all_results)=={'host','guest'} and all(r['status']=='passed' for r in all_results.values()) else 'failed' if any(r['status']=='failed' for r in all_results.values()) else 'blocked'
    write_json(out/'network_summary.json',{'status':overall,'results':all_results,'network_scope':metadata.get('network_scope','unspecified'),'interpretation':'Achieved traffic at the configured offered load; this is not a link-capacity benchmark. Host and guest monotonic clocks are evaluated separately. completed means loop execution ended, not a pass. recovery_seconds is a compatibility alias for detected_error_to_ack_seconds and excludes failure-detection latency; failed_request_start_to_ack_seconds includes the failed request interval, not the unknowable exact physical outage start. Byte totals can include partial failed I/O; verified_received_bytes includes only fully checked ACK payloads.'})
    return all_results

def summarize_memory(out):
    out=pathlib.Path(out);source=out/'memory.csv'
    if not source.exists():return {}
    rows=list(csv.DictReader(source.open()));result={}
    numeric=['host_rss_bytes','host_phys_footprint_bytes','host_compressor_physical_bytes','host_swap_used_bytes','guest_MemAvailable_bytes']
    for key in numeric:
        points=[(float(r['elapsed_seconds']),float(r[key])) for r in rows if r.get(key)]
        if not points:continue
        xs=[p[0] for p in points];ys=[p[1] for p in points];mx=statistics.mean(xs);my=statistics.mean(ys);den=sum((x-mx)**2 for x in xs)
        result[key]={'first':ys[0],'last':ys[-1],'min':min(ys),'max':max(ys),'change':ys[-1]-ys[0],'slope_bytes_per_hour':sum((x-mx)*(y-my) for x,y in points)/den*3600 if den else None}
    phases={}
    for phase in dict.fromkeys(r['phase'] for r in rows):
        subset=[r for r in rows if r['phase']==phase];entry={'samples':len(subset),'elapsed_start':float(subset[0]['elapsed_seconds']),'elapsed_end':float(subset[-1]['elapsed_seconds'])}
        for key in numeric:
            vals=[float(r[key]) for r in subset if r.get(key)]
            if vals:entry[key]={'first':vals[0],'last':vals[-1],'max':max(vals),'min':min(vals),'median_last_12_samples':statistics.median(vals[-12:])}
        phases[phase]=entry
    rounds=[]
    for name in phases:
        match=re.fullmatch(r'round_(\d+)_allocate_hold',name)
        if not match:continue
        released='round_'+match.group(1)+'_released_observe'
        hold=[r for r in rows if r['phase']==name];observed=[r for r in rows if r['phase']==released]
        if not observed:continue
        entry={'round':int(match.group(1)),'observed_release_seconds':float(observed[-1]['elapsed_seconds'])-float(observed[0]['elapsed_seconds'])}
        for key in ['host_phys_footprint_bytes','host_rss_bytes','guest_MemAvailable_bytes']:
            before=[float(r[key]) for r in hold[-3:] if r.get(key)]
            after=[float(r[key]) for r in observed[-12:] if r.get(key)]
            if before and after:entry[key]={'hold_median_last_3':statistics.median(before),'after_median_last_12':statistics.median(after),'change_after_minus_hold':statistics.median(after)-statistics.median(before)}
        rounds.append(entry)
    summary={'sample_count':len(rows),'duration_seconds':float(rows[-1]['elapsed_seconds']) if rows else 0,'metrics':result,'phases':phases,'reclamation_rounds':rounds,'host_error_samples':sum(bool(r.get('host_memory_error')) for r in rows),'guest_error_samples':sum(bool(r.get('guest_memory_error')) for r in rows),'note':'phys_footprint and RSS describe the emulator process; compression and swap describe the entire host. System changes cannot be attributed solely to the emulator.'}
    disk_values=[int(r['host_disk_free_bytes']) for r in rows if r.get('host_disk_free_bytes')]
    summary['minimum_host_disk_free_bytes']=min(disk_values) if disk_values else None
    write_json(out/'memory_summary.json',summary);return summary

def monitor(args):
    with MemoryMonitor(args,args.output,None if args.host_only else Adb(args)) as mon:
        mon.set_phase(args.workload_label);time.sleep(args.duration)
    summarize_memory(args.output)

def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--adb',default='${WORKSPACE}/sdk/platform-tools/adb')
    p.add_argument('--adb-port',type=int,default=22150);p.add_argument('--serial',default='emulator-22160')
    p.add_argument('--emulator-pid',type=int);p.add_argument('--output',required=True)
    p.add_argument('--sample-interval',type=float,default=5)
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('--probe',default=str(pathlib.Path(__file__).resolve().parent/'probes/build/avd_probe'))
    m=sub.add_parser('memory');m.add_argument('--rounds',type=int,default=3);m.add_argument('--allocation-bytes',type=int,default=1024**3);m.add_argument('--hold-seconds',type=int,default=60);m.add_argument('--observe-seconds',type=float,default=600);m.add_argument('--baseline-seconds',type=float,default=30);m.add_argument('--guest-page-size',type=int,default=4096)
    s=sub.add_parser('soak');s.add_argument('--duration',type=int,default=7200);s.add_argument('--echo-port',type=int,default=0);s.add_argument('--local-echo',action=argparse.BooleanOptionalAction,default=True);s.add_argument('--host-endpoint',default='127.0.0.1');s.add_argument('--guest-endpoint',default='10.0.2.2');s.add_argument('--heartbeat-ms',type=int,default=1000);s.add_argument('--payload-bytes',type=int,default=65536);s.add_argument('--bulk-bytes',type=int,default=32*1024**2);s.add_argument('--payload-framing',choices=('binary','line'),default='binary');s.add_argument('--workload-label',default='idle_non_game_control')
    m=sub.add_parser('monitor');m.add_argument('--duration',type=float,default=60);m.add_argument('--host-only',action='store_true');m.add_argument('--workload-label',default='idle_non_game_control')
    sub.add_parser('summarize')
    return p

def main():
    p=parser();args=p.parse_args()
    if args.command in ('memory','soak','monitor') and args.emulator_pid is None:p.error('--emulator-pid is required')
    if args.sample_interval<=0:p.error('--sample-interval must be positive')
    if args.command=='prepare':prepare(args)
    elif args.command=='memory':memory(args)
    elif args.command=='soak':soak(args)
    elif args.command=='monitor':monitor(args)
    else:summarize_memory(args.output);summarize_network(args.output)

if __name__=='__main__':main()
