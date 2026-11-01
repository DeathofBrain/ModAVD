// Host contract QA: real Registry, MessageChannel, SharedMemory and Sharer TUs.
// The generated .inc files are exact production functions, not rewrites.
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <future>
#include <iostream>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#include <sys/resource.h>
#include <unistd.h>
#include "aemu/base/Log.h"
#include "aemu/base/async/CallbackRegistry.h"
#include "aemu/base/memory/SharedMemory.h"
#include "aemu/base/synchronization/Lock.h"
#include "android/recording/video/VideoFrameSharer.h"
#include "android/gpu_frame.h"
using android::base::AutoLock;
using android::base::Lock;
using android::base::CallbackRegistry;
using android::base::SharedMemory;
using android::recording::VideoFrameSharer;
using namespace std::chrono_literals;
static std::atomic<int> readCalls{0}, postOn{0}, postOff{0}, invalidations{0};
static std::atomic<bool> postEnabled{false}, synchronousRepost{false};
static std::mutex gateMutex;
static std::condition_variable gateCV;
static bool blockRead=false, readEntered=false, releaseRead=false;
static void readPixels(void* dst, uint32_t n, uint32_t) {
    {
        std::unique_lock<std::mutex> l(gateMutex);
        if (blockRead) {
            readEntered=true; gateCV.notify_all();
            gateCV.wait(l, [] { return releaseRead; });
        }
    }
    std::memset(dst, 0x6b, n);
    ++readCalls;
}
ReadPixelsFunc android_getReadPixelsFunc() { return readPixels; }
static auto sReceiverState=std::make_unique<CallbackRegistry>();
static void frameReceivedForwader(void*) { sReceiverState->invokeCallbacks(); }
namespace android { namespace opengl {
class GpuFrameBridge {
public:
    static GpuFrameBridge* create() { return new GpuFrameBridge; }
    void setLooper(void*) {}
    void setDisplayId(uint32_t) {}
    void setFrameReceiver(void(*)(void*), void*) {}
    void invalidateRecordingBuffers() { ++invalidations; }
};
}}
using android::opengl::GpuFrameBridge;
static GpuFrameBridge* sBridge[2]{};
static Lock sLock[2];
static void* sLooper=nullptr;
int sRecordCounter[2]{};
static std::atomic_bool sRequestPost{false};
static void gpu_frame_set_post(bool on, GpuFrameBridge*, uint32_t) {
    postEnabled=on;
    if (on) ++postOn; else ++postOff;
}
struct GpuOps {
    void repost() {
        if (synchronousRepost && postEnabled) frameReceivedForwader(nullptr);
    }
};
static GpuOps ops;
static GpuOps* android_getVirtioGpuOps() { return &ops; }
#include "gpu-production.inc"
struct Globals {
    Lock lock;
    std::unique_ptr<VideoFrameSharer> webrtc_module;
    std::string sharedMemoryHandle;
    uint32_t fbWidth=8, fbHeight=6;
};
static auto sGlobals=std::make_unique<Globals>();
static int android_serial_number_port=900000+getpid();
#define D(...) ((void)0)
#include "screen-production.inc"
static void require(bool cond, const char* text) {
    if (!cond) throw std::runtime_error(text);
}
static auto observer(const char* name) {
    auto r=std::make_unique<SharedMemory>(name, sizeof(VideoFrameSharer::VideoInfo)+8*6*4);
    require(r->open(SharedMemory::AccessMode::READ_ONLY)==0,"observer must open real POSIX mapping");
    return r;
}
static uint32_t frame(SharedMemory& r) {
    return static_cast<VideoFrameSharer::VideoInfo*>(r.get())->frameNumber;
}
static void cleanup() {
    sGlobals.reset(); // exercises production Sharer destructor before Registry teardown
    for (auto*& p:sBridge) { delete p; p=nullptr; }
}
static void lifecycle() {
    require(stop_shared_memory_module(),"stop before start");
    require(sRecordCounter[0]==0,"stop before start must own no reference");
    synchronousRepost=true;
    const char* name=start_shared_memory_module(60);
    require(name,"start succeeds");
    require(sRecordCounter[0]==1,"SHM owns exactly one record reference");
    auto r=observer(name); auto* mapping=r->get();
    const auto before=frame(*r);
    require(before>0 && readCalls>0,"synchronous repost delivered real Sharer callback");
    for (int i=0;i<100;i++) require(start_shared_memory_module(24)==name,"repeat start retains exact handle pointer");
    require(sRecordCounter[0]==1,"repeat start is idempotent");
    require(stop_shared_memory_module() && stop_shared_memory_module(),"repeat stop succeeds");
    require(sRecordCounter[0]==0 && !postEnabled,"stop releases SHM reference");
    const auto stopped=frame(*r); sReceiverState->invokeCallbacks();
    require(frame(*r)==stopped,"stopped callback removed");
    auto still=observer(name);
    require(start_shared_memory_module(60)==name,"restart keeps handle pointer");
    require(r->get()==mapping && frame(*r)>stopped,"restart reuses persistent mapping and resumes frames");
    require(sRecordCounter[0]==1,"restart owns one reference");
    stop_shared_memory_module();
    const auto end=frame(*r);
    sGlobals->webrtc_module.reset();
    require(sRecordCounter[0]==0,"destructor after stop does not release twice");
    sReceiverState->invokeCallbacks(); require(frame(*r)==end,"destruction leaves no callback");
    SharedMemory gone(name,sizeof(VideoFrameSharer::VideoInfo)+8*6*4);
    require(gone.open(SharedMemory::AccessMode::READ_ONLY)!=0,"destructor unlinks region only at object destruction");
}
static void no_frames() {
    synchronousRepost=false;
    for(int i=0;i<128;i++) { start_shared_memory_module(60); stop_shared_memory_module(); }
    require(sRecordCounter[0]==0,"128 start/stop cycles balance with no frame processing");
    require(sReceiverState->available()==sReceiverState->max_slots(),"no pending ADD/REMOVE saturation");
    sReceiverState->invokeCallbacks(); require(readCalls==0,"no resurrected callback after drain");
}
static void multi_owner() {
    gpu_frame_set_record_mode(true,0);
    const auto on=postOn.load();
    for(int i=0;i<100;i++) {
        start_shared_memory_module(60); start_shared_memory_module(60);
        require(sRecordCounter[0]==2,"other owner plus one SHM owner");
        stop_shared_memory_module(); stop_shared_memory_module();
        require(sRecordCounter[0]==1 && postEnabled,"other owner remains active");
    }
    require(postOn==on && postOff==0 && invalidations==0,"SHM must not reconfigure or invalidate another owner");
    gpu_frame_set_record_mode(false,0);
    require(sRecordCounter[0]==0 && postOff==1 && invalidations==1,"last owner disables and invalidates exactly once");
}
static void destructors() {
    { VideoFrameSharer never(8,6,"qa9never"+std::to_string(getpid())); }
    require(sRecordCounter[0]==0,"never started destructor has no reference");
    synchronousRepost=true;
    { VideoFrameSharer live(8,6,"qa9live"+std::to_string(getpid())); require(live.initialize(),"direct init"); live.start(); live.start(); require(sRecordCounter[0]==1,"direct repeat start"); }
    require(sRecordCounter[0]==0,"active destructor releases exactly once");
    const auto calls=readCalls.load(); sReceiverState->invokeCallbacks();
    require(readCalls==calls,"no callback after active destructor");
}
static void failed_init() {
    struct rlimit old{}, none{}; require(getrlimit(RLIMIT_NOFILE,&old)==0,"get rlimit");
    none=old; none.rlim_cur=0; require(setrlimit(RLIMIT_NOFILE,&none)==0,"restrict open descriptors");
    const char* result=start_shared_memory_module(60);
    const int restored=setrlimit(RLIMIT_NOFILE,&old);
    require(restored==0,"restore descriptor limit");
    require(result==nullptr && !sGlobals->webrtc_module,"failed initialize resets module");
    require(sRecordCounter[0]==0,"failed-init destruction owns no reference");
    stop_shared_memory_module(); require(sRecordCounter[0]==0,"stop after failed init owns nothing");
    synchronousRepost=true; require(start_shared_memory_module(60),"retry after init failure succeeds");
    require(sRecordCounter[0]==1,"retry owns one reference");
    stop_shared_memory_module();
}
static void concurrent() {
    synchronousRepost=true;
    auto wave=[](auto fn) {
        std::promise<void> go; auto ready=go.get_future().share(); std::vector<std::thread> ts;
        for(int t=0;t<8;t++) ts.emplace_back([=] { ready.wait(); for(int i=0;i<64;i++) fn(); });
        go.set_value(); for(auto& t:ts)t.join();
    };
    wave([]{start_shared_memory_module(60);});
    require(sRecordCounter[0]==1,"concurrent starts deduplicate construction and registration");
    wave([]{stop_shared_memory_module();}); require(sRecordCounter[0]==0,"concurrent stops release once");
    wave([]{start_shared_memory_module(60);stop_shared_memory_module();});
    stop_shared_memory_module(); require(sRecordCounter[0]==0,"concurrent mixed calls leave no reference after final stop");
}
static void sharer_concurrent() {
    VideoFrameSharer sharer(8,6,"qa9threads"+std::to_string(getpid()));
    require(sharer.initialize(),"direct concurrent Sharer initializes");
    synchronousRepost=true;
    auto wave=[](auto fn) {
        std::promise<void> go; auto ready=go.get_future().share(); std::vector<std::thread> ts;
        for(int t=0;t<8;t++) ts.emplace_back([=] { ready.wait(); for(int i=0;i<64;i++) fn(); });
        go.set_value(); for(auto& t:ts)t.join();
    };
    wave([&]{sharer.start();}); require(sRecordCounter[0]==1,"Sharer lock independently deduplicates concurrent starts");
    wave([&]{sharer.stop();}); require(sRecordCounter[0]==0,"Sharer lock independently deduplicates concurrent stops");
    wave([&]{sharer.start();sharer.stop();}); sharer.stop();
    require(sRecordCounter[0]==0,"direct mixed lifecycle remains balanced");
}
static void barrier(bool destroy) {
    start_shared_memory_module(60); auto r=observer(sGlobals->sharedMemoryHandle.c_str());
    { std::lock_guard<std::mutex> l(gateMutex); blockRead=true; }
    auto invoked=std::async(std::launch::async, []{sReceiverState->invokeCallbacks();});
    { std::unique_lock<std::mutex> l(gateMutex); require(gateCV.wait_for(l,2s,[]{return readEntered;}),"callback entered latch"); }
    std::promise<void> stopping;
    auto begun=stopping.get_future();
    auto stopped=std::async(std::launch::async,[&]{stopping.set_value();if(destroy)sGlobals->webrtc_module.reset();else stop_shared_memory_module();});
    begun.wait();
    require(stopped.wait_for(50ms)==std::future_status::timeout,"stop/destructor must wait for in-flight callback");
    { std::lock_guard<std::mutex> l(gateMutex); releaseRead=true; gateCV.notify_all(); }
    invoked.get(); stopped.get();
    require(sRecordCounter[0]==0,"callback barrier releases exactly once");
    const auto n=frame(*r); sReceiverState->invokeCallbacks(); require(frame(*r)==n,"no callback after lifetime barrier returns");
}
struct Nested { CallbackRegistry* registry; int a=0,b=0; bool added=false; };
static void nestedB(void* p){ ++static_cast<Nested*>(p)->b; }
static void nestedA(void* p){ auto* n=static_cast<Nested*>(p); ++n->a; if(!n->added){n->added=true;n->registry->registerCallback(nestedB,n);} }
static void registry_order() {
    CallbackRegistry r; Nested n{&r}; r.registerCallback(nestedA,&n); r.invokeCallbacks();
    require(n.a==1 && n.b==0,"callback-internal register is deferred to next invoke");
    r.invokeCallbacks();require(n.a==1 && n.b==1,"same-key registration replaces callback next round");
    r.unregisterCallback(&n);r.invokeCallbacks();require(n.b==1,"unregister erases callback");
    r.registerCallback(nestedB,&n);r.unregisterCallback(&n);r.invokeCallbacks();require(n.b==1,"pending completed ADD cannot resurrect removed opaque");
    r.registerCallback(nestedB,&n);r.invokeCallbacks();require(n.b==2,"serial re-add after unregister works");r.unregisterCallback(&n);
    // Fill all 64 slots: unregister must drain without needing a 65th slot.
    int keys[64]{};for(int i=0;i<64;i++)r.registerCallback([](void*){},&keys[i]);
    require(r.available()==0,"real 64-slot queue saturated");
    for(auto& k:keys)r.unregisterCallback(&k);
    require(r.available()==64,"external unregister frees pending queue without invoke");
}
static void registry_cycles() {
    CallbackRegistry r; int key=0;
    for(int i=0;i<128;i++){r.registerCallback([](void*){},&key);r.unregisterCallback(&key);}
    require(r.available()==64,"real registry can complete 128 no-frame cycles");
}
static void registry_overlap() {
    CallbackRegistry r; std::atomic<int> removed{0}, other{0};
    auto count=[](void* p){++*static_cast<std::atomic<int>*>(p);};
    r.registerCallback(count,&removed);
    for(int i=0;i<63;i++)r.registerCallback(count,&other);
    require(r.available()==0,"queue full before overlapping producer/remover");
    std::atomic<bool> done{false}; std::promise<void> begun; auto ready=begun.get_future();
    std::thread producer([&]{begun.set_value();for(int i=0;i<4096;i++)r.registerCallback(count,&other);done=true;});
    ready.wait(); r.unregisterCallback(&removed);
    while(!done)r.unregisterCallback(&other);
    producer.join(); r.unregisterCallback(&other);
    r.invokeCallbacks();
    require(removed==0 && other==0,"overlapping queued ADDs never resurrect a completed removed key");
    require(r.available()==64,"overlapping producer drains without frames");
}
int main(int argc,char**argv) {
    try {
        require(argc==2,"one case required"); std::string c=argv[1];
        if(c=="lifecycle")lifecycle();else if(c=="no_frames")no_frames();else if(c=="multi_owner")multi_owner();
        else if(c=="destructors")destructors();else if(c=="failed_init")failed_init();else if(c=="concurrent")concurrent();
        else if(c=="sharer_concurrent")sharer_concurrent();else if(c=="stop_barrier")barrier(false);else if(c=="destructor_barrier")barrier(true);
        else if(c=="registry_order")registry_order();else if(c=="registry_cycles")registry_cycles();else if(c=="registry_overlap")registry_overlap();else throw std::runtime_error("unknown case");
        cleanup(); std::cout<<"PASS "<<c<<"\n";return 0;
    } catch(const std::exception& e){std::cerr<<"FAIL "<<argv[1]<<": "<<e.what()<<"\n";return 1;}
}
