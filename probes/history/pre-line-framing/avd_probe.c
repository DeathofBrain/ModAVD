/* Standalone AArch64 Linux/Android test payload; no libc or Android SDK. */
typedef unsigned long u64;
typedef long i64;
typedef unsigned int u32;
typedef unsigned short u16;
typedef unsigned char u8;

static i64 call6(i64 n, i64 a, i64 b, i64 c, i64 d, i64 e, i64 f) {
    register i64 x0 __asm__("x0")=a, x1 __asm__("x1")=b;
    register i64 x2 __asm__("x2")=c, x3 __asm__("x3")=d;
    register i64 x4 __asm__("x4")=e, x5 __asm__("x5")=f;
    register i64 x8 __asm__("x8")=n;
    __asm__ volatile("svc #0" : "+r"(x0) : "r"(x1),"r"(x2),"r"(x3),"r"(x4),"r"(x5),"r"(x8) : "memory", "cc");
    return x0;
}
#define C(n,a,b,c) call6(n,(i64)(a),(i64)(b),(i64)(c),0,0,0)
static u64 length(const char *s) { u64 n=0; while(s[n]) ++n; return n; }
static void out(const char *s) { u64 n=length(s),p=0; while(p<n) { i64 k=C(64,1,s+p,n-p); if(k<=0) return; p+=k; } }
static void number(u64 n) { char b[24]; u64 p=24; do { b[--p]='0'+n%10; n/=10; } while(n); C(64,1,b+p,24-p); }
static u64 parse(const char *s) { u64 n=0; while(*s>='0'&&*s<='9') n=n*10+*s++-'0'; return n; }
static int equal(const char *a,const char *b) { while(*a&&*a==*b) {a++;b++;} return *a==*b; }
struct ts { i64 sec,nsec; };
static u64 now(void) { struct ts t; C(113,1,&t,0); return t.sec*1000000000UL+t.nsec; }
static void pause_ns(u64 n) { struct ts t={n/1000000000UL,n%1000000000UL}; while(C(101,&t,&t,0)==-4) {} }
static void event(const char *type,u64 v) { out("{\"event\":\"");out(type);out("\",\"monotonic_ns\":");number(now());out(",\"value\":");number(v);out("}\n"); }
static int memory_test(u64 bytes,u64 hold,u64 step) {
    event("memory_before_allocate",bytes);
    i64 r=call6(222,0,bytes,3,0x22,-1,0);
    if(r<0&&r>-4096) { event("memory_mmap_error",(u64)-r); return 2; }
    volatile u8 *p=(volatile u8*)r;
    for(u64 i=0;i<bytes;i+=step) p[i]=(u8)(1+(i/step)%255);
    if(bytes) p[bytes-1]=0x5a;
    event("memory_touched",bytes);
    pause_ns(hold*1000000000UL);
    i64 ret=C(215,p,bytes,0);
    event(ret==0?"memory_released":"memory_munmap_error",ret==0?bytes:(u64)-ret);
    return ret==0?0:3;
}

struct addr4 { u16 family,port; u32 addr; u8 zero[8]; };
struct timeval { i64 sec,usec; };
static u16 be16(u16 v) { return (v>>8)|(v<<8); }
static u32 ipv4(const char *s) { u32 a=0; for(int k=0;k<4;k++) { u32 v=0; while(*s>='0'&&*s<='9')v=v*10+*s++-'0'; a|=(v&255)<<(k*8); if(*s=='.') s++; } return a; }
static void network_event(const char *type,u64 seq,u64 sent,u64 received,u64 elapsed,u64 code) {
    out("{\"event\":\"");out(type);out("\",\"monotonic_ns\":");number(now());
    out(",\"sequence\":");number(seq);out(",\"sent_bytes\":");number(sent);out(",\"received_bytes\":");number(received);
    out(",\"elapsed_ns\":");number(elapsed);out(",\"errno\":");number(code);out("}\n");
}
static int tcp_test(const char *ip,u64 port,u64 duration,u64 interval_ms,u64 size,u64 bulk_bytes) {
    if(size==0||size>1048576) return 4;
    i64 mapping=call6(222,0,size*2,3,0x22,-1,0);
    if(mapping<0&&mapping>-4096) return 5;
    u8 *tx=(u8*)mapping,*rx=tx+size;
    struct addr4 a={2,be16((u16)port),ipv4(ip),{0}};
    u64 start=now(),deadline=start+duration*1000000000UL,seq=0;
    i64 fd=-1;
    while(now()<deadline) {
        if(fd<0) {
            u64 cstart=now();
            fd=C(198,2,1,0);
            if(fd<0) {network_event("socket_error",seq,0,0,now()-cstart,-fd);pause_ns(1000000000UL);continue;}
            struct timeval timeout={10,0};
            call6(208,fd,1,20,(i64)&timeout,sizeof(timeout),0);
            call6(208,fd,1,21,(i64)&timeout,sizeof(timeout),0);
            i64 ret=C(203,fd,&a,sizeof(a));
            if(ret<0) {network_event("connect_error",seq,0,0,now()-cstart,-ret);C(57,fd,0,0);fd=-1;pause_ns(1000000000UL);continue;}
            network_event("connected",seq,0,0,now()-cstart,0);
        }
        for(u64 i=0;i<size;i++) tx[i]=(u8)((seq+i)%251);
        u64 began=now(),sent=0,received=0,err=0;
        while(sent<size) {
            i64 n=call6(206,fd,(i64)(tx+sent),size-sent,0x4000,0,0);
            if(n<=0) {err=n<0?-n:104;break;} sent+=n;
        }
        while(!err&&received<size) {
            i64 n=call6(207,fd,(i64)(rx+received),size-received,0,0,0);
            if(n<=0) {err=n<0?-n:104;break;} received+=n;
        }
        if(!err) for(u64 i=0;i<size;i++) if(tx[i]!=rx[i]) {err=10001;break;}
        int bulk=seq<(bulk_bytes+size-1)/size;
        network_event(bulk?(err?"bulk_error":"bulk_ack"):(err?"heartbeat_error":"heartbeat_ack"),seq,sent,received,now()-began,err);
        ++seq;
        if(err) {C(57,fd,0,0);fd=-1;pause_ns(1000000000UL);}
        u64 elapsed=now()-began,interval=interval_ms*1000000UL;
        if(!err&&!bulk&&interval>elapsed) pause_ns(interval-elapsed);
    }
    if(fd>=0) C(57,fd,0,0);
    C(215,tx,size*2,0);
    event("network_complete",seq);
    return 0;
}
int main_start(u64 *sp) {
    u64 argc=sp[0]; char **argv=(char**)(sp+1);
    if(argc>=2&&equal(argv[1],"memory")) return memory_test(argc>2?parse(argv[2]):1073741824UL,argc>3?parse(argv[3]):60,argc>4?parse(argv[4]):4096);
    if(argc>=5&&equal(argv[1],"tcp")) return tcp_test(argv[2],parse(argv[3]),parse(argv[4]),argc>5?parse(argv[5]):1000,argc>6?parse(argv[6]):65536,argc>7?parse(argv[7]):33554432);
    out("avd_probe memory [bytes] [hold_seconds] [page_size]\navd_probe tcp ipv4 port seconds [interval_ms] [payload_bytes] [bulk_bytes]\n");
    return 1;
}
__asm__(".section .text.start,\"ax\"\n.global _start\n_start:\nmov x0, sp\nbl main_start\nmov x8, #93\nsvc #0\n");
