#!/usr/bin/env python3
"""Render official-AVD evidence with matplotlib; missing measurements are skipped.

Usage: .venv/bin/python plot_results.py [--results results] [--output reports/figures]
Reads files only; never connects to ADB or operates the emulator.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import numpy as np

MIB=1024**2
COLORS={'footprint':'#1762a3','rss':'#da8634','guest':'#23836a','host':'#1762a3','compressor':'#7762a6','swap':'#bc5474'}

def numeric(value):
    try:
        v=float(value)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError): return None

def read_json(path):
    if not path.exists(): return None
    return json.loads(path.read_text())

def read_csv(path):
    if not path.exists(): return []
    with path.open(newline='') as f: return list(csv.DictReader(f))

def read_jsonl(path):
    if not path.exists(): return []
    rows=[]
    for line in path.read_text().splitlines():
        try: row=json.loads(line)
        except json.JSONDecodeError: continue  # A concurrent writer may have a partial final line.
        if isinstance(row,dict): rows.append(row)
    return rows

class Plotter:
    def __init__(self,results,output):
        self.results=results;self.output=output;output.mkdir(parents=True,exist_ok=True)
        self.generated=[];self.skipped=[];self.inputs={}
        plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.grid':True,'grid.alpha':.22,'grid.linewidth':.6,'axes.axisbelow':True,'figure.dpi':130,'savefig.dpi':180,'svg.fonttype':'none'})
    def record(self,path):
        if path.exists(): self.inputs[str(path.resolve())]=hashlib.sha256(path.read_bytes()).hexdigest()
        return path
    def save(self,fig,name,caption,source_paths):
        fig.text(.08,.012,caption,fontsize=8,color='#535c67',va='bottom')
        paths=[]
        for ext in ('png','svg'):
            path=self.output/f'{name}.{ext}'
            fig.savefig(path,bbox_inches='tight',facecolor='white',metadata={'Creator':'Official AVD baseline / matplotlib'})
            paths.append(str(path.resolve()))
        plt.close(fig)
        self.generated.append({'name':name,'files':paths,'sources':[str(p.resolve()) for p in source_paths if p.exists()],'caption':caption})
    def skip(self,name,reason): self.skipped.append({'name':name,'reason':reason})

    def memory(self,folder,name,reclamation=False):
        path=self.record(self.results/folder/'memory.csv');rows=read_csv(path)
        rows=[r for r in rows if numeric(r.get('elapsed_seconds')) is not None]
        if not rows:return self.skip(name,'No valid elapsed time samples in '+str(path))
        rows.sort(key=lambda r:float(r['elapsed_seconds']))
        definitions=[('Emulator process memory (MiB)',[('host_phys_footprint_bytes','Physical footprint',COLORS['footprint']),('host_rss_bytes','RSS',COLORS['rss'])]),('Guest available memory (MiB)',[('guest_MemAvailable_bytes','MemAvailable',COLORS['guest'])])]
        definitions.append(('Whole-host pressure (MiB)',[('host_compressor_physical_bytes','Compressor physical',COLORS['compressor']),('host_swap_used_bytes','Swap used',COLORS['swap'])]))
        panels=[(label,defs) for label,defs in definitions if any(numeric(r.get(key)) is not None for key,_,_ in defs for r in rows)]
        if not panels:return self.skip(name,'No measured memory values in '+str(path))
        fig,axes=plt.subplots(len(panels),1,figsize=(10,2.7*len(panels)),sharex=True,squeeze=False)
        axes=axes[:,0]
        for ax,(label,defs) in zip(axes,panels):
            for key,title,color in defs:
                pts=[(float(r['elapsed_seconds'])/60,numeric(r.get(key))/MIB) for r in rows if numeric(r.get(key)) is not None]
                if pts:ax.plot(*zip(*pts),label=title,color=color,linewidth=1.35)
            ax.set_ylabel(label);ax.legend(loc='best',frameon=False,ncol=2)
        if reclamation:
            segments=[];start=0
            for i in range(1,len(rows)+1):
                if i==len(rows) or rows[i].get('phase')!=rows[start].get('phase'):
                    end=float(rows[i]['elapsed_seconds'])/60 if i<len(rows) else float(rows[-1]['elapsed_seconds'])/60
                    segments.append((rows[start].get('phase',''),float(rows[start]['elapsed_seconds'])/60,end));start=i
            seen=set()
            for phase,start,end in segments:
                if 'allocate_hold' in phase:color='#efa341';alpha=.16;kind='Allocate + hold'
                elif 'released_observe' in phase:color='#68ad83';alpha=.11;kind='Released: observe'
                else:continue
                for ax in axes:ax.axvspan(start,end,color=color,alpha=alpha,linewidth=0)
                if 'allocate_hold' in phase:
                    round_no=phase.split('_')[1] if phase.startswith('round_') else '?'
                    axes[0].text((start+end)/2,1.015,f'R{round_no}',transform=axes[0].get_xaxis_transform(),ha='center',fontsize=9)
                seen.add(kind)
            handles=[Patch(facecolor='#efa341',alpha=.25,label='Allocate + hold'),Patch(facecolor='#68ad83',alpha=.2,label='Released: observe')]
            if seen:fig.legend(handles=[h for h in handles if h.get_label() in seen],loc='upper right',frameon=False,ncol=2,bbox_to_anchor=(.98,1.005))
        meta_path=self.record(self.results/folder/'metadata.json');meta=read_json(meta_path) or {}
        title='Guest 1 GiB allocation and release' if reclamation else 'Long observation: memory trend'
        seconds=float(rows[-1]['elapsed_seconds'])-float(rows[0]['elapsed_seconds'])
        fig.suptitle(f'{title} | {seconds/60:.1f} min measured',fontsize=14,x=.08,ha='left',y=1.035)
        axes[-1].set_xlabel('Elapsed time (minutes)')
        fig.tight_layout(rect=(0,.05,1,1))
        context='System compressor and swap include unrelated host processes.'
        if reclamation:context+=' Shading follows sampling phases; release boundary uncertainty is one sampling interval.'
        label=meta.get('workload_label','')
        if label:context+=' Workload: '+str(label)+'.'
        self.save(fig,name,context,[path,meta_path])

    def network(self,folder='soak',name='network-continuity',remote=False):
        sources=[];datasets={}
        for client in ('host','guest'):
            path=self.record(self.results/folder/f'{client}_network.jsonl');sources.append(path)
            events=[r for r in read_jsonl(path) if numeric(r.get('monotonic_ns')) is not None]
            if events:datasets[client]=events
        if not datasets:return self.skip(name,'No timed host or guest TCP events in '+str(self.results/folder))
        fig,(rtt,loss)=plt.subplots(2,1,figsize=(10,6.5),sharex=True,gridspec_kw={'height_ratios':[2.2,1]})
        summary=[]
        for client,events in datasets.items():
            origin=events[0]['monotonic_ns'];color=COLORS[client]
            ack=[e for e in events if e.get('event')=='heartbeat_ack' and numeric(e.get('elapsed_ns')) is not None]
            if ack:
                x=np.array([(e['monotonic_ns']-origin)/60e9 for e in ack]);y=np.array([e['elapsed_ns']/1e6 for e in ack])
                rtt.plot(x,y,label=f'{client.title()} (n={len(ack)})',color=color,linewidth=.65,alpha=.8)
                summary.append(f'{client}: RTT median {np.median(y):.2f} ms / P95 {np.percentile(y,95):.2f} ms')
            elapsed=[(e['monotonic_ns']-origin)/60e9 for e in events]
            disconnected=np.cumsum([e.get('event') in ('heartbeat_error','bulk_error') for e in events])
            count=int(disconnected[-1]);loss.step(elapsed,disconnected,where='post',label=f'{client.title()}: {count} lost connections',color=color,linewidth=1.5)
            failed=[(e['monotonic_ns']-origin)/60e9 for e in events if e.get('event') in ('connect_error','socket_error')]
            if failed:loss.scatter(failed,[0]*len(failed),marker='x',color=color,s=25,label=f'{client.title()}: {len(failed)} connect/socket errors',zorder=4)
            for e in events:
                if e.get('event') in ('heartbeat_error','bulk_error'):
                    rtt.axvline((e['monotonic_ns']-origin)/60e9,color=color,linestyle=':',alpha=.55)
        rtt.set_ylabel('Heartbeat echo RTT (ms)');rtt.legend(frameon=False,ncol=2)
        if not any(e.get('event')=='heartbeat_ack' for events in datasets.values() for e in events):rtt.text(.5,.5,'No completed heartbeat measurements',transform=rtt.transAxes,ha='center')
        largest_count=max(sum(e.get('event') in ('heartbeat_error','bulk_error') for e in events) for events in datasets.values())
        loss.set_ylabel('Lost connection events');loss.set_ylim(0,max(1,largest_count+1));loss.yaxis.set_major_locator(plt.MaxNLocator(integer=True));loss.legend(frameon=False,loc='upper left',fontsize=8)
        loss.set_xlabel('Minutes since each client\'s first event (separate clocks)')
        fig.suptitle('Remote TCP echo through current host route' if remote else 'TCP continuity: emulator NAT and host control',fontsize=14,x=.08,ha='left')
        fig.tight_layout(rect=(0,.13,1,.97))
        caption='Remote service over the configured host route; FakeIP/TUN/proxy may intervene. Direct Internet routing is not established.\n' if remote else 'Local echo through guest 10.0.2.2 versus host loopback; WAN reliability is not measured.\n'
        caption+='\n'.join(summary)+'\nClock origins are normalized independently; events are not wall-clock aligned.'
        meta_path=self.record(self.results/folder/'metadata.json');sources.append(meta_path)
        self.save(fig,name,caption,sources)

    def screenshots(self):
        path=self.record(self.results/'screenshots.json');report=read_json(path) or {};groups=[];sources=[path]
        for scene in ('static','dynamic'):
            for fmt in ('raw','png'):
                record=report.get('scenes',{}).get(scene,{}).get('formats',{}).get(fmt,{})
                samples=record.get('samples',[])
                csv_path=self.record(self.results/f'screenshots-{scene}-{fmt}.csv');sources.append(csv_path)
                if not samples:samples=read_csv(csv_path)
                timed=[r for r in samples if numeric(r.get('duration_ms')) is not None]
                if timed:groups.append((scene,fmt,timed))
        if not groups:return self.skip('screenshot-latency','No timed raw/PNG screencap samples')
        fig,(boxes,ecdf)=plt.subplots(1,2,figsize=(12,5.2),gridspec_kw={'width_ratios':[1.2,1]})
        values=[[float(r['duration_ms']) for r in rows] for _,_,rows in groups]
        bp=boxes.boxplot(values,patch_artist=True,widths=.5,showfliers=False)
        colors=['#1762a3','#4c91bf','#da8634','#edb46a']
        for i,((scene,fmt,rows),vals,patch) in enumerate(zip(groups,values,bp['boxes'])):
            color=colors[i%len(colors)];patch.set_facecolor(color);patch.set_alpha(.48)
            bad=[float(r['duration_ms']) for r in rows if str(r.get('valid','')).lower() not in ('true','1')]
            if bad:boxes.scatter([i+1]*len(bad),bad,color='#bc3744',marker='x',s=25,zorder=4)
            med=np.median(vals);p95=np.percentile(vals,95)
            boxes.annotate(f'n={len(vals)}\np50={med:.1f}\np95={p95:.1f}',(i+1,max(vals)),xytext=(0,8),textcoords='offset points',ha='center',fontsize=8)
            ordered=np.sort(vals);ecdf.plot(ordered,np.arange(1,len(vals)+1)/len(vals),color=color,linewidth=1.7,label=f'{scene.title()} {fmt.upper()}')
        boxes.set_xticks(range(1,len(groups)+1),[f'{s.title()}\n{f.upper()}' for s,f,_ in groups]);boxes.set_ylabel('Capture wall time (ms)');boxes.margins(y=.25)
        ecdf.set_xlabel('Capture wall time (ms)');ecdf.set_ylabel('Empirical cumulative probability');ecdf.set_ylim(0,1.04);ecdf.legend(frameon=False,loc='lower right',fontsize=8)
        status=report.get('status','CSV measurements')
        fig.suptitle(f'ADB screencap latency | report: {status}',fontsize=14,x=.08,ha='left')
        fig.tight_layout(rect=(0,.075,1,.96))
        self.save(fig,'screenshot-latency','All recorded timed attempts; red crosses are invalid captures. Timing includes ADB transport and excludes image validation.\nBox = quartiles/median; whiskers = 1.5 IQR. Values above whiskers remain in the ECDF and percentile calculations.',sources)

    def boot(self):
        path=self.record(self.results/'sync_shutdown.json');report=read_json(path) or {}
        rounds=[r for r in report.get('rounds',[]) if numeric(r.get('boot_after_s')) is not None]
        if not rounds:return self.skip('cold-boot','No sync-round boot_after_s measurements')
        fig,ax=plt.subplots(figsize=(9,4.3));xs=[int(r['round']) for r in rounds];ys=[float(r['boot_after_s']) for r in rounds]
        ax.bar(xs,ys,color=[COLORS['guest'] if r.get('status')=='passed' else '#bc5474' for r in rounds],width=.63)
        median=float(np.median(ys));p95=float(np.percentile(ys,95))
        ax.axhline(median,color='#283f59',linestyle='--',linewidth=1,label=f'Median {median:.2f} s | P95 {p95:.2f} s');ax.legend(frameon=False)
        for x,y in zip(xs,ys):ax.text(x,y+.15,f'{y:.2f}',ha='center',fontsize=8)
        ax.set_xticks(xs);ax.set_xlabel('Sync / orderly shutdown round');ax.set_ylabel('Cold boot after shutdown (s)');ax.margins(y=.22)
        fig.suptitle(f'Cold boot: {len(rounds)} measured rounds | report: {report.get("status","unknown")}',fontsize=14,x=.08,ha='left')
        fig.tight_layout(rect=(0,.06,1,.95))
        self.save(fig,'cold-boot','Only sync_shutdown.json boot_after_s is used. boot_before_s is excluded because it may reuse an existing start timestamp.\nGreen: payload verification passed; pink: the recorded round did not pass. These timings include only measured restarts.',[path])

    def durability(self):
        path=self.record(self.results/'no_sync_shutdown.json');report=read_json(path) or {};rounds=report.get('rounds',[])
        counts=collections.defaultdict(collections.Counter);verified=set();no_verification=0
        for i,row in enumerate(rounds):
            files=row.get('files',{})
            if not files:no_verification+=1;continue
            delay=row.get('delay_requested_ms');verified.add((delay,row.get('round',i)))
            for state in files.values():counts[(delay,state.get('kind','unknown'))][state.get('classification','unknown')]+=1
        if not counts:return self.skip('no-sync-durability','No per-file verification results; harness errors are not data-loss counts')
        groups=sorted(counts,key=lambda g:(numeric(g[0]) or 0,str(g[1])))
        classifications=[k for k in ('new','old','zero','missing','corrupt','unreadable','unknown') if any(k in c for c in counts.values())]
        classifications+=sorted({k for c in counts.values() for k in c}-set(classifications))
        colors={'new':'#23836a','old':'#da8634','zero':'#bc5474','missing':'#5d6676','corrupt':'#925ca7','unreadable':'#4554a3','unknown':'#91969d'}
        labels={'new':'Expected new content','old':'Previous content','zero':'Zero bytes','missing':'Missing file','corrupt':'Corrupt content','unreadable':'Unreadable','unknown':'Unknown'}
        fig,ax=plt.subplots(figsize=(10,4.8));bottom=np.zeros(len(groups))
        for cl in classifications:
            vals=np.array([counts[g][cl] for g in groups]);ax.bar(range(len(groups)),vals,bottom=bottom,color=colors.get(cl,'#91969d'),width=.68,label=labels.get(cl,cl))
            for x,v,b in zip(range(len(groups)),vals,bottom):
                if v:
                    tiny=v < max(sum(c.values()) for c in counts.values())*.05
                    ax.text(x,b+v+(2 if tiny else -v/2),str(v),ha='center',va='bottom' if tiny else 'center',fontsize=9,color='#202b38' if tiny or cl=='old' else 'white')
            bottom+=vals
        ax.set_xticks(range(len(groups)),[f'{delay} ms\n{kind}' for delay,kind in groups]);ax.set_ylabel('Verified files');ax.set_xlabel('Requested delay after host receives CLOSED marker');ax.legend(frameon=False,ncol=min(3,len(classifications)),loc='upper center',bbox_to_anchor=(.5,1.22),fontsize=8);ax.yaxis.set_major_locator(plt.MaxNLocator(integer=True))
        ax.set_ylim(0,max(bottom)*1.1)
        fig.suptitle(f'No-sync orderly exit: file outcomes | report: {report.get("status","unknown")}',fontsize=14,x=.08,ha='left',y=1.035)
        fig.tight_layout(rect=(0,.075,1,1))
        self.save(fig,'no-sync-durability',f'Only measured file outcomes: {len(verified)} verified rounds; {no_verification} rounds without verification are excluded.\nSynthetic workload, not game cache. adb emu kill is an orderly emulator exit, not host SIGKILL or power loss.',[path])

    def run(self):
        jobs=[('memory-reclamation',lambda:self.memory('memory','memory-reclamation',True)),('memory-long-observation',lambda:self.memory('soak','memory-long-observation')),('network-continuity',self.network),('network-remote-continuity',lambda:self.network('soak/wan','network-remote-continuity',True)),('screenshot-latency',self.screenshots),('cold-boot',self.boot),('no-sync-durability',self.durability)]
        for name,job in jobs:
            try:job()
            except Exception as exc:self.skip(name,f'{type(exc).__name__}: {exc}')
        manifest={'created_utc':dt.datetime.now(dt.timezone.utc).isoformat(),'generated':self.generated,'skipped':self.skipped,'input_sha256':self.inputs,'policy':'Measured samples only; missing inputs are skipped. Rerun after tests complete to replace partial figures.'}
        (self.output/'plot_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        print(json.dumps({'generated':[g['name'] for g in self.generated],'skipped':self.skipped,'manifest':str((self.output/'plot_manifest.json').resolve())},indent=2))
        return manifest

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--results',type=Path,default=Path(__file__).resolve().parent/'results');p.add_argument('--output',type=Path,default=Path(__file__).resolve().parent/'reports/figures')
    args=p.parse_args();Plotter(args.results.resolve(),args.output.resolve()).run()

if __name__=='__main__':main()
