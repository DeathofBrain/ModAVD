"""Bounded read-only memory monitor for the windowed login/download phase."""
import argparse,json,signal,threading,time
from pathlib import Path
from soak_tests import Adb,MemoryMonitor,summarize_memory,utc,write_json
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--emulator-pid',type=int,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--max-seconds',type=int,default=7200)
a=p.parse_args()
a.adb='${WORKSPACE}/sdk/platform-tools/adb';a.adb_port=22150;a.serial='emulator-22160';a.sample_interval=5
out=a.output.resolve();out.mkdir(parents=True,exist_ok=False)
stop=threading.Event()
for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
write_json(out/'metadata.json',{'started_utc':utc(),'emulator_pid':a.emulator_pid,'phase':'windowed_manual_login_and_resource_download','sample_interval_s':5,'account_contents_collected':False})
started=time.monotonic()
with MemoryMonitor(a,out,Adb(a)) as mon:
    mon.set_phase('windowed_login_and_download')
    while not stop.wait(.5) and time.monotonic()-started<a.max_seconds:mon.raise_if_failed()
summary=summarize_memory(out)
write_json(out/'completion.json',{'finished_utc':utc(),'elapsed_seconds':time.monotonic()-started,'sample_count':summary.get('sample_count')})
