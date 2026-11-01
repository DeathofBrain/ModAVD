#include <Hypervisor/Hypervisor.h>
#include <assert.h>
#include <errno.h>
#include <mach/mach.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static void checked(hv_return_t r) {
    if (r) { fprintf(stderr,"HV error %x\n",r); exit(2); }
}
static void metrics(const char *tag) {
    task_vm_info_data_t info = {};
    mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    assert(!task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&info, &count));
    printf("%s resident=%llu footprint=%llu reusable=%llu\n",tag,info.resident_size,info.phys_footprint,info.reusable);
}
static uint64_t run(hv_vcpu_t cpu, hv_vcpu_exit_t *state, int write) {
    checked(hv_vcpu_set_reg(cpu, HV_REG_PC, write ? 0x10000020 : 0x10000000));
    checked(hv_vcpu_set_reg(cpu, HV_REG_CPSR, 0x3c5));
    checked(hv_vcpu_set_reg(cpu, HV_REG_X0, 0x40000000));
    checked(hv_vcpu_set_reg(cpu, HV_REG_X1, 0x3434343434343434));
    checked(hv_vcpu_set_reg(cpu, HV_REG_X3, 16384));
    checked(hv_vcpu_set_reg(cpu, HV_REG_X4, 4096));
    checked(hv_vcpu_set_reg(cpu, HV_REG_X5, 0));
    checked(hv_vcpu_set_reg(cpu, HV_REG_X6, 0x20000000));
    checked(hv_vcpu_run(cpu));
    printf("guest exit=%d ec=%llx\n",state->reason,state->exception.syndrome>>26);
    if (state->reason != HV_EXIT_REASON_EXCEPTION || (state->exception.syndrome>>26) != 0x16) exit(3);
    uint64_t value;
    checked(hv_vcpu_get_reg(cpu, HV_REG_X5, &value));
    return value;
}
int main(int argc, char **argv) {
    if (getpagesize() != 16384) {
        fprintf(stderr,"This probe expects a 16 KiB native host page.\n");
        return 2;
    }
    const size_t size = 64*1024*1024;
    int advice = argc>1 ? atoi(argv[1]) : MADV_DONTNEED;
    int shared = argc>2 ? atoi(argv[2]) : 0;
    const size_t offset = argc>3 ? 16384 : 0;
    const size_t discard_size = argc>3 ? 2*1024*1024 : size;
    void *memory = mmap(NULL,size,PROT_READ|PROT_WRITE,MAP_ANON|(shared?MAP_SHARED:MAP_PRIVATE),-1,0);
    void *code = mmap(NULL,16384,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
    uint64_t *observed = mmap(NULL,128*1024,PROT_READ|PROT_WRITE,MAP_ANON|MAP_PRIVATE,-1,0);
    assert(memory != MAP_FAILED && code != MAP_FAILED && observed != MAP_FAILED);
    // Read one word per 4KiB page, add to the checksum, and store each
    // observed word in a separate buffer so mismatches cannot cancel out.
    const uint32_t instructions[] = {
        0xf9400002,0x8b0200a5,0xf90000c2,0x910020c6,0x8b040000,0xf1000463,0x54ffff41,0xd4000002,
        0xf9000001,0x8b040000,0xf1000463,0x54ffffa1,0xd4000002,
    };
    memcpy(code,instructions,sizeof(instructions));
    memset(memory,0x78,size);
    checked(hv_vm_create(NULL));
    checked(hv_vm_map(code,0x10000000,16384,HV_MEMORY_READ|HV_MEMORY_EXEC));
    checked(hv_vm_map(observed,0x20000000,128*1024,HV_MEMORY_READ|HV_MEMORY_WRITE));
    checked(hv_vm_map(memory,0x40000000,size,HV_MEMORY_READ|HV_MEMORY_WRITE|HV_MEMORY_EXEC));
    hv_vcpu_t cpu;
    hv_vcpu_exit_t *exit;
    checked(hv_vcpu_create(&cpu,&exit,NULL));
    checked(hv_vcpu_set_sys_reg(cpu,HV_SYS_REG_SCTLR_EL1,0x30d00800));
    assert(run(cpu,exit,0)==0x7878787878787878ULL*(size/4096));
    metrics("before");
    errno=0;
    int result;
    if(advice>=200) {
        checked(hv_vm_unmap(0x40000000+offset,discard_size));
        metrics("after-unmap");
        if(advice>=400) {
            void *replaced=mmap((char*)memory+offset,discard_size,PROT_READ|PROT_WRITE,MAP_ANON|MAP_FIXED|(shared?MAP_SHARED:MAP_PRIVATE),-1,0);
            result=replaced==(char*)memory+offset?0:-1;
        } else if(advice>=300) result=msync((char*)memory+offset,discard_size,advice-300);
        else result=madvise((char*)memory+offset,discard_size,advice-200);
        metrics("before-remap");
        checked(hv_vm_map((char*)memory+offset,0x40000000+offset,discard_size,HV_MEMORY_READ|HV_MEMORY_WRITE|HV_MEMORY_EXEC));
    } else if(advice>=100) result=msync((char*)memory+offset,discard_size,advice-100);
    else result=madvise((char*)memory+offset,discard_size,advice);
    printf("discard(%d)=%d errno=%d shared=%d\n",advice,result,errno,shared);
    metrics("after");
    uint64_t guest_sum=run(cpu,exit,0),host_sum=0;
    for(size_t i=0;i<size;i+=4096) {
        uint64_t value=*(uint64_t*)((char*)memory+i);
        assert(observed[i/4096]==value);
        host_sum+=value;
        if(advice>=400) assert(value==((i>=offset&&i<offset+discard_size)?0:0x7878787878787878));
    }
    assert(guest_sum==host_sum);
    printf("guest sum=%llx host sum=%llx (all 4KiB boundaries agree)\n",(unsigned long long)guest_sum,(unsigned long long)host_sum);
    metrics("after-guest-read");
    run(cpu,exit,1);
    metrics("after-guest-write");
    assert(run(cpu,exit,0)==0x3434343434343434ULL*(size/4096));
    for(size_t i=0;i<size;i+=4096) {
        assert(*(uint64_t*)((char*)memory+i)==0x3434343434343434);
        assert(observed[i/4096]==0x3434343434343434);
    }
    printf("PASS: all 4KiB guest writes visible to host\n");
    checked(hv_vcpu_destroy(cpu));
    checked(hv_vm_unmap(0x10000000,16384));
    checked(hv_vm_unmap(0x20000000,128*1024));
    checked(hv_vm_unmap(0x40000000,size));
    checked(hv_vm_destroy());
    munmap(memory,size);
    munmap(code,16384);
    munmap(observed,128*1024);
}
