#import <Foundation/Foundation.h>
#include <Hypervisor/Hypervisor.h>
#include <sys/sysctl.h>
#include <mach/task_info.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <mach/vm_region.h>
#include <mach/vm_statistics.h>
#include <sys/mman.h>
#include <unistd.h>
#include <cerrno>
#include <cstdio>
#include <stdexcept>
#include <vector>

static void require(bool condition, const char* label) {
    if (!condition) throw std::runtime_error(label);
}
static NSDictionary* pageInfo(void* base, size_t size) {
    size_t page=getpagesize();
    uint64_t present=0,dirty=0,reusable=0,pagedout=0,errors=0;
    vm_page_info_basic_data_t first{},last{};
    for (size_t off=0; off<size; off+=page) {
        vm_page_info_basic_data_t info{};
        mach_msg_type_number_t count=VM_PAGE_INFO_BASIC_COUNT;
        kern_return_t k=mach_vm_page_info(mach_task_self(),(uintptr_t)base+off,
            VM_PAGE_INFO_BASIC,(vm_page_info_t)&info,&count);
        if (k!=KERN_SUCCESS) {++errors;continue;}
        if (off==0) first=info;
        last=info;
        present+=!!(info.disposition&VM_PAGE_QUERY_PAGE_PRESENT);
        dirty+=!!(info.disposition&VM_PAGE_QUERY_PAGE_DIRTY);
        reusable+=!!(info.disposition&VM_PAGE_QUERY_PAGE_REUSABLE);
        pagedout+=!!(info.disposition&VM_PAGE_QUERY_PAGE_PAGED_OUT);
    }
    std::vector<char> incore(size/page);
    int mc=mincore(base,size,incore.data());
    uint64_t resident=0;
    if (!mc) for (char b:incore) resident+=!!(b&MINCORE_INCORE);
    return @{ @"pages":@(size/page),@"present":@(present),@"dirty":@(dirty),
        @"reusable":@(reusable),@"paged_out":@(pagedout),@"query_errors":@(errors),
        @"mincore_exit":@(mc),@"mincore_incore":@(resident),
        @"first_object_id":@(first.object_id),@"first_offset":@(first.offset),
        @"last_object_id":@(last.object_id),@"last_offset":@(last.offset),
        @"first_ref_count":@(first.ref_count),@"first_depth":@(first.depth) };
}
static NSDictionary* regionInfo(void* base) {
    mach_vm_address_t addr=(uintptr_t)base;mach_vm_size_t size=0;
    vm_region_top_info_data_t top{};mach_msg_type_number_t count=VM_REGION_TOP_INFO_COUNT;
    mach_port_t object=MACH_PORT_NULL;
    kern_return_t k=mach_vm_region(mach_task_self(),&addr,&size,VM_REGION_TOP_INFO,
        (vm_region_info_t)&top,&count,&object);
    if (object!=MACH_PORT_NULL) mach_port_deallocate(mach_task_self(),object);
    return @{ @"exit":@(k),@"region_size":@(size),@"object_id":@(top.obj_id),
        @"refs":@(top.ref_count),@"private_resident":@(top.private_pages_resident),
        @"shared_resident":@(top.shared_pages_resident),@"share_mode":@(top.share_mode) };
}

static task_vm_info_data_t taskMetrics() {
    task_vm_info_data_t info{};mach_msg_type_number_t count=TASK_VM_INFO_COUNT;
    require(task_info(mach_task_self(),TASK_VM_INFO,(task_info_t)&info,&count)==KERN_SUCCESS,"task_info");
    return info;
}
static NSDictionary* metrics(task_vm_info_data_t info) {
    return @{ @"resident_size":@(info.resident_size),@"phys_footprint":@(info.phys_footprint),
        @"internal":@(info.internal),@"reusable":@(info.reusable),@"compressed":@(info.compressed) };
}
static NSDictionary* wireInfo(void* base) {
    mach_vm_address_t addr=(uintptr_t)base;mach_vm_size_t size=0;
    natural_t depth=0;vm_region_submap_info_data_64_t info{};
    mach_msg_type_number_t count=VM_REGION_SUBMAP_INFO_COUNT_64;
    kern_return_t k=mach_vm_region_recurse(mach_task_self(),&addr,&size,&depth,
        (vm_region_recurse_info_t)&info,&count);
    vm_statistics64_data_t host{};count=HOST_VM_INFO64_COUNT;
    kern_return_t h=host_statistics64(mach_host_self(),HOST_VM_INFO64,(host_info64_t)&host,&count);
    return @{ @"region_exit":@(k),@"user_wired_count":@(info.user_wired_count),
        @"object_id":@(info.object_id_full),@"region_resident":@(info.pages_resident),
        @"region_reusable":@(info.pages_reusable),@"refs":@(info.ref_count),
        @"host_exit":@(h),@"host_wired_pages":@(host.wire_count) };
}
static void mapAt(void* dest,void* source,size_t size) {
    mach_vm_address_t addr=(uintptr_t)dest;vm_prot_t cur,max;
    require(mach_vm_remap(mach_task_self(),&addr,size,0,VM_FLAGS_FIXED|VM_FLAGS_OVERWRITE,
        mach_task_self(),(uintptr_t)source,false,&cur,&max,VM_INHERIT_NONE)==KERN_SUCCESS,"fixed remap");
}

struct DirectCounts { uint64_t free,speculative; };
static uint64_t counter(const char* name) {
    uint64_t value=0;size_t length=sizeof(value);
    require(sysctlbyname(name,&value,&length,nullptr,0)==0,"direct page counter");
    return value;
}
static DirectCounts directCounts() {
    return {counter("vm.page_free_count"),counter("vm.page_speculative_count")};
}
static NSDictionary* directJson(DirectCounts c) {
    return @{ @"free":@(c.free),@"speculative":@(c.speculative) };
}
static vm_statistics64_data_t hostMetrics() {
    vm_statistics64_data_t info{};mach_msg_type_number_t count=HOST_VM_INFO64_COUNT;
    require(host_statistics64(mach_host_self(),HOST_VM_INFO64,(host_info64_t)&info,&count)==KERN_SUCCESS,"host stats");
    return info;
}
static NSDictionary* hostJson(vm_statistics64_data_t info) {
    return @{ @"free":@(info.free_count),@"speculative":@(info.speculative_count),
        @"active":@(info.active_count),@"inactive":@(info.inactive_count),
        @"wired":@(info.wire_count),@"compressed":@(info.compressor_page_count),
        @"internal":@(info.internal_page_count),@"external":@(info.external_page_count),
        @"purgeable":@(info.purgeable_count),@"purges":@(info.purges),
        @"compressions":@(info.compressions),@"decompressions":@(info.decompressions) };
}
static NSDictionary* runCase(bool independent,size_t targetSize,int round) {
    const size_t chunk=2*1024*1024,neighbor=chunk,total=targetSize+2*neighbor;
    const uint64_t ipa=0x40000000;const hv_memory_flags_t flags=HV_MEMORY_READ|HV_MEMORY_WRITE;
    char* base=(char*)mmap(nullptr,total,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
    require(base!=MAP_FAILED,"base mmap");
    if(independent) {
        for(size_t off=0;off<total;off+=chunk) {
            char* backing=(char*)mmap(nullptr,chunk,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
            require(backing!=MAP_FAILED,"chunk mmap");
            for(size_t p=0;p<chunk;p+=getpagesize())backing[p]=(char)(p/getpagesize()+1);
            mapAt(base+off,backing,chunk);require(munmap(backing,chunk)==0,"chunk source cleanup");
        }
    } else for(size_t p=0;p<total;p+=getpagesize())base[p]=(char)(p/getpagesize()+1);
    char* target=base+neighbor;
    NSMutableDictionary* out=[@{ @"independent_initial_objects":@(independent),
        @"round":@(round),@"target_bytes":@(targetSize),@"chunks":@(targetSize/chunk),
        @"left_initial":pageInfo(base,neighbor),@"right_initial":pageInfo(target+targetSize,neighbor) } mutableCopy];
    require(hv_vm_create(nullptr)==HV_SUCCESS,"hv create");
    require(hv_vm_map(base,ipa,total,flags)==HV_SUCCESS,"initial SINGLE whole HVF map");
    void* code=mmap(nullptr,getpagesize(),PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
    require(code!=MAP_FAILED,"code mmap");
    const uint32_t instructions[]={0xf9000001,0x8b040000,0xf1000463,0x54ffffa1,0xd4000002};
    memcpy(code,instructions,sizeof(instructions));
    require(hv_vm_map(code,0x10000000,getpagesize(),HV_MEMORY_READ|HV_MEMORY_EXEC)==HV_SUCCESS,"code map");
    hv_vcpu_t cpu;hv_vcpu_exit_t* exit;
    require(hv_vcpu_create(&cpu,&exit,nullptr)==HV_SUCCESS,"cpu create");
    require(hv_vcpu_set_sys_reg(cpu,HV_SYS_REG_SCTLR_EL1,0x30d00800)==HV_SUCCESS,"SCTLR");
    NSMutableArray* generations=[NSMutableArray array];
    for(int gen=0;gen<(independent?2:1);++gen) {
        require(hv_vcpu_set_reg(cpu,HV_REG_PC,0x10000000)==HV_SUCCESS,"PC");
        require(hv_vcpu_set_reg(cpu,HV_REG_CPSR,0x3c5)==HV_SUCCESS,"CPSR");
        require(hv_vcpu_set_reg(cpu,HV_REG_X0,ipa+neighbor)==HV_SUCCESS,"X0");
        require(hv_vcpu_set_reg(cpu,HV_REG_X1,0x3434343434343434ULL+gen)==HV_SUCCESS,"X1");
        require(hv_vcpu_set_reg(cpu,HV_REG_X3,targetSize/4096)==HV_SUCCESS,"X3");
        require(hv_vcpu_set_reg(cpu,HV_REG_X4,4096)==HV_SUCCESS,"X4");
        require(hv_vcpu_run(cpu)==HV_SUCCESS,"synthetic vCPU write sweep");
        require(exit->reason==HV_EXIT_REASON_EXCEPTION&&(exit->exception.syndrome>>26)==0x16,"HVC completion");
        NSMutableDictionary* generation=[@{ @"generation":@(gen),@"synthetic_vcpu_4K_writes":@(targetSize/4096),
            @"target_before":pageInfo(target,targetSize),@"wire_before":wireInfo(target) } mutableCopy];
        std::vector<mach_vm_address_t> aliases;
        NSMutableArray* oldStates=[NSMutableArray array];
        for(size_t off=0;off<targetSize;off+=chunk) {
            mach_vm_address_t old=0;vm_prot_t cur,max;
            require(mach_vm_remap(mach_task_self(),&old,chunk,0,VM_FLAGS_ANYWHERE,mach_task_self(),
                (uintptr_t)target+off,false,&cur,&max,VM_INHERIT_NONE)==KERN_SUCCESS,"rollback alias");
            require(hv_vm_unmap(ipa+neighbor+off,chunk)==HV_SUCCESS,"partial HVF unmap");
            void* fresh=mmap(nullptr,chunk,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
            require(fresh!=MAP_FAILED,"fresh independent chunk mmap");
            mapAt(target+off,fresh,chunk);
            require(hv_vm_map(target+off,ipa+neighbor+off,chunk,flags)==HV_SUCCESS,"fresh HVF chunk map");
            require(munmap(fresh,chunk)==0,"fresh source cleanup");
            aliases.push_back(old);
        }
        uint64_t minRefs=UINT64_MAX,maxRefs=0,oldPresent=0,oldPagedout=0;
        for(mach_vm_address_t old:aliases) {
            NSDictionary* state=pageInfo((void*)old,chunk);[oldStates addObject:state];
            uint64_t refs=[state[@"first_ref_count"] unsignedLongLongValue];
            minRefs=std::min(minRefs,refs);maxRefs=std::max(maxRefs,refs);
            oldPresent+=[state[@"present"] unsignedLongLongValue];
            oldPagedout+=[state[@"paged_out"] unsignedLongLongValue];
        }
        generation[@"old_states_after_all_commits"]=oldStates;
        generation[@"old_ref_min"]=@(minRefs);generation[@"old_ref_max"]=@(maxRefs);
        generation[@"old_present_before_cleanup"]=@(oldPresent);generation[@"old_pagedout_before_cleanup"]=@(oldPagedout);
        generation[@"new_after_all_commits"]=pageInfo(target,targetSize);
        generation[@"left_before_cleanup"]=pageInfo(base,neighbor);
        generation[@"right_before_cleanup"]=pageInfo(target+targetSize,neighbor);
        task_vm_info_data_t taskPre=taskMetrics();vm_statistics64_data_t pre=hostMetrics();
        DirectCounts directPre=directCounts();
        // Critical measurement window: kernel metadata calls and existing alias
        // destruction only. No user allocations, memory reads, extra aliases,
        // madvise, data zeroing, pressure or guest execution in this window.
        for(mach_vm_address_t old:aliases)require(mach_vm_deallocate(mach_task_self(),old,chunk)==KERN_SUCCESS,"old cleanup");
        DirectCounts directPost=directCounts();
        vm_statistics64_data_t post=hostMetrics();task_vm_info_data_t taskPost=taskMetrics();
        generation[@"direct_before_cleanup"]=directJson(directPre);
        generation[@"direct_after_cleanup"]=directJson(directPost);
        generation[@"direct_free_page_delta"]=@((int64_t)directPost.free-directPre.free);
        generation[@"direct_free_plus_speculative_delta"]=@((int64_t)directPost.free+directPost.speculative-directPre.free-directPre.speculative);
        generation[@"host_before_cleanup"]=hostJson(pre);generation[@"host_after_cleanup"]=hostJson(post);
        generation[@"task_before_cleanup"]=metrics(taskPre);generation[@"task_after_cleanup"]=metrics(taskPost);
        generation[@"free_page_delta"]=@((int64_t)post.free_count-pre.free_count);
        generation[@"free_plus_speculative_delta"]=@((int64_t)post.free_count+post.speculative_count-pre.free_count-pre.speculative_count);
        generation[@"inactive_page_delta"]=@((int64_t)post.inactive_count-pre.inactive_count);
        generation[@"task_footprint_delta"]=@((int64_t)taskPost.phys_footprint-(int64_t)taskPre.phys_footprint);
        generation[@"new_after_cleanup"]=pageInfo(target,targetSize);
        generation[@"left_after_cleanup"]=pageInfo(base,neighbor);
        generation[@"right_after_cleanup"]=pageInfo(target+targetSize,neighbor);
        [generations addObject:generation];
    }
    out[@"generations"]=generations;
    require(hv_vcpu_destroy(cpu)==HV_SUCCESS,"cpu destroy");
    require(hv_vm_unmap(ipa,total)==HV_SUCCESS,"RAM unmap");
    require(hv_vm_unmap(0x10000000,getpagesize())==HV_SUCCESS,"code unmap");
    require(hv_vm_destroy()==HV_SUCCESS,"VM destroy");
    require(munmap(code,getpagesize())==0,"code cleanup");
    require(munmap(base,total)==0,"RAM cleanup");
    return out;
}
int main(){@autoreleasepool {
    NSMutableDictionary* report=[@{ @"page_size":@(getpagesize()),
        @"scope":@"Synthetic HVF ownVM only; one initial whole HVF map. Three rounds64/128MiB target, independent2MiBbacking vs monolithiccontrol; independent runs two generations. vCPU only writes synthetic data before each generation, never executes in commit/cleanup measurement. Existing pertransaction rollback aliases retained until bounded batchcleanup; no additional diagnostic alias. No guest data loads, madvise, system pressure, ADB or production changes. Peak touched RAM132MiB. Direct vm.page_free_count/speculative_count sysctl read for primary physical-release metric; host_statistics64 retained as possibly rate-limited background only." } mutableCopy];
    try {
        NSMutableArray* cases=[NSMutableArray array];
        for(int round=0;round<3;++round)for(size_t size:{64UL*1024*1024,128UL*1024*1024})
            for(bool independent:{false,true})[cases addObject:runCase(independent,size,round)];
        report[@"cases"]=cases;report[@"pass"]=@YES;
    }catch(const std::exception& e){report[@"pass"]=@NO;report[@"error"]=[NSString stringWithUTF8String:e.what()];}
    NSData* data=[NSJSONSerialization dataWithJSONObject:report options:NSJSONWritingPrettyPrinted error:nil];
    fwrite(data.bytes,1,data.length,stdout);fputc('\n',stdout);
    return [report[@"pass"] boolValue]?0:1;
}}
