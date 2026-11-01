#!/usr/bin/env python3
"""Build a libc-free Android AArch64 ELF with Apple clang, no NDK download.

The deliberately narrow static linker understands only relocation types emitted
by this payload. Unknown relocation types fail closed. Input sources and the
command are preserved next to the binary for auditability.
"""
import argparse
import hashlib
import json
import pathlib
import struct
import subprocess

def build(output):
    root=pathlib.Path(__file__).resolve().parent
    out=pathlib.Path(output).resolve(); out.mkdir(parents=True,exist_ok=True)
    obj=out/'avd_probe.o'
    cmd=['clang','--target=aarch64-linux-android24','-O2','-c','-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-pic','-fno-unwind-tables','-fno-asynchronous-unwind-tables','-mno-outline-atomics',str(root/'avd_probe.c'),'-o',str(obj)]
    subprocess.run(cmd,check=True,capture_output=True)
    data=obj.read_bytes()
    if data[:6]!=b'\x7fELF\x02\x01': raise ValueError('expected ELF64 little endian')
    eh=struct.unpack_from('<16sHHIQQQIHHHHHH',data)
    if eh[2]!=183: raise ValueError('expected AArch64')
    shoff,shentsize,shnum,shstrndx=eh[6],eh[11],eh[12],eh[13]
    sections=[struct.unpack_from('<IIQQQQIIQQ',data,shoff+i*shentsize) for i in range(shnum)]
    namesec=sections[shstrndx]; names=data[namesec[4]:namesec[4]+namesec[5]]
    def cstr(b,i): return b[i:b.index(b'\0',i)].decode()
    section_names=[cstr(names,s[0]) for s in sections]
    base=0x400000; cursor=0x1000; locations={}; image=bytearray(cursor)
    for i,s in enumerate(sections):
        if not s[2]&2: continue
        align=max(1,s[8]); cursor=(cursor+align-1)//align*align
        locations[i]=cursor
        image.extend(bytes(cursor-len(image)))
        image.extend(bytes(s[5]) if s[1]==8 else data[s[4]:s[4]+s[5]])
        cursor+=s[5]
    symbols={}
    for i,s in enumerate(sections):
        if s[1]!=2: continue
        st=sections[s[6]]; stringtab=data[st[4]:st[4]+st[5]]
        table=[]
        for p in range(s[4],s[4]+s[5],s[9]):
            name,info,other,sec,value,size=struct.unpack_from('<IBBHQQ',data,p)
            if sec in locations: address=base+locations[sec]+value
            elif sec==0xfff1: address=value
            else: address=None
            table.append((cstr(stringtab,name),address,sec))
        symbols[i]=table
    relocations={}
    for s in sections:
        if s[1]!=4 or s[7] not in locations: continue
        for p in range(s[4],s[4]+s[5],s[9]):
            offset,info,addend=struct.unpack_from('<QQq',data,p)
            symidx,kind=info>>32,info&0xffffffff
            name,address,sec=symbols[s[6]][symidx]
            if address is None: raise ValueError(f'unresolved symbol {name}')
            at=locations[s[7]]+offset; place=base+at; value=address+addend
            relocations[str(kind)]=relocations.get(str(kind),0)+1
            if kind==257: struct.pack_into('<Q',image,at,value); continue
            if kind==261: struct.pack_into('<i',image,at,value-place); continue
            instruction=struct.unpack_from('<I',image,at)[0]
            if kind in (282,283):
                delta=value-place
                if delta%4 or not -(1<<27)<=delta<(1<<27): raise ValueError('branch out of range')
                instruction=(instruction&0xfc000000)|((delta>>2)&0x3ffffff)
            elif kind==275:
                delta=(value>>12)-(place>>12)
                if not -(1<<20)<=delta<(1<<20): raise ValueError('ADRP out of range')
                instruction=(instruction&~0x60ffffe0)|((delta&3)<<29)|(((delta>>2)&0x7ffff)<<5)
            elif kind==277:
                instruction=(instruction&~0x003ffc00)|((value&0xfff)<<10)
            elif kind in (278,284,285,286,299):
                scale={278:0,284:1,285:2,286:3,299:4}[kind]
                if value&((1<<scale)-1): raise ValueError('unaligned LD/ST relocation')
                instruction=(instruction&~0x003ffc00)|(((value&0xfff)>>scale)<<10)
            else: raise ValueError(f'unsupported relocation {kind} for {name}')
            struct.pack_into('<I',image,at,instruction)
    entry=next(addr for table in symbols.values() for name,addr,_ in table if name=='_start')
    ident=b'\x7fELF\x02\x01\x01'+bytes(9)
    header=struct.pack('<16sHHIQQQIHHHHHH',ident,2,183,1,entry,64,0,0,64,56,2,0,0,0)
    # One load region plus non-executable GNU stack. No dynamic loader or runtime.
    ph=struct.pack('<IIQQQQQQ',1,7,0,base,base,len(image),len(image),0x1000)
    stack=struct.pack('<IIQQQQQQ',0x6474e551,6,0,0,0,0,0,16)
    image[:len(header+ph+stack)]=header+ph+stack
    binary=out/'avd_probe'; binary.write_bytes(image); binary.chmod(0o755)
    manifest={'command':cmd,'sha256':hashlib.sha256(image).hexdigest(),'size_bytes':len(image),'entry':hex(entry),'relocations':relocations,'sections':[section_names[i] for i in locations],'note':'Test payload only; neither the emulator nor the official system image is modified.'}
    (out/'build_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps(manifest,indent=2)); return binary

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('--output',default=str(pathlib.Path(__file__).resolve().parent/'build'))
    build(p.parse_args().output)
