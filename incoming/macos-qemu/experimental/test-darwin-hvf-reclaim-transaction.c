/*
 * Native verifier for the exact Darwin/HVF transaction header used by QEMU.
 * At most 65.1 MiB of guest mappings; CPU bursts are bounded to a few ms.
 */
#include <Hypervisor/Hypervisor.h>
#include <assert.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include "exec/darwin-hvf-reclaim-transaction.h"

#define MAX_CPUS 8
#define DATA_SIZE (64u * 1024u * 1024u)
#define PAGE_COUNT (DATA_SIZE / 4096u)
#define RESULT_SIZE (PAGE_COUNT * sizeof(uint64_t))
#define DATA_GPA UINT64_C(0x40000000)
#define CODE_GPA UINT64_C(0x10000000)
#define RESULT_GPA UINT64_C(0x20000000)
#define OLD_VALUE UINT64_C(0x7878787878787878)
#define NEW_VALUE UINT64_C(0x3434343434343434)
#define HV_FLAGS (HV_MEMORY_READ | HV_MEMORY_WRITE | HV_MEMORY_EXEC)

enum Command { NONE, READ, WRITE, SPIN, FINISH };
typedef struct Worker {
    pthread_t thread;
    hv_vcpu_t cpu;
    hv_vcpu_exit_t *exit;
    unsigned index;
    enum Command command;
    bool ready, running;
    uint64_t *observed;
} Worker;
static Worker workers[MAX_CPUS];
static pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t condition = PTHREAD_COND_INITIALIZER;
static void *memory;
static unsigned cpu_count;
static DhrtOutcome fatal_outcome;

static void hv_check(hv_return_t result) {
    if (result != HV_SUCCESS) {
        fprintf(stderr, "Unexpected HV error: 0x%x\n", result);
        abort();
    }
}
static void mach_check(kern_return_t result) {
    if (result != KERN_SUCCESS) {
        fprintf(stderr, "Unexpected Mach error: %s\n", mach_error_string(result));
        abort();
    }
}
static void metrics(const char *tag) {
    task_vm_info_data_t info = {};
    mach_msg_type_number_t count = TASK_VM_INFO_COUNT;
    mach_check(task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&info, &count));
    printf("%s resident=%llu footprint=%llu reusable=%llu\n", tag,
           info.resident_size, info.phys_footprint, info.reusable);
}
static void timed_wait(void) {
    struct timespec until;
    assert(clock_gettime(CLOCK_REALTIME, &until) == 0);
    until.tv_sec += 3;
    assert(pthread_cond_timedwait(&condition, &mutex, &until) == 0);
}
static bool all_stopped(void) {
    for (unsigned i = 0; i < cpu_count; i++) {
        if (!workers[i].ready || workers[i].running || workers[i].command != NONE) {
            return false;
        }
    }
    return true;
}
static void await_stopped(void) {
    assert(pthread_mutex_lock(&mutex) == 0);
    while (!all_stopped()) timed_wait();
    assert(pthread_mutex_unlock(&mutex) == 0);
}
static void *worker_main(void *opaque) {
    Worker *w = opaque;
    hv_check(hv_vcpu_create(&w->cpu, &w->exit, NULL));
    hv_check(hv_vcpu_set_sys_reg(w->cpu, HV_SYS_REG_SCTLR_EL1, 0x30d00800));
    assert(pthread_mutex_lock(&mutex) == 0);
    w->ready = true;
    assert(pthread_cond_broadcast(&condition) == 0);
    for (;;) {
        while (w->command == NONE) timed_wait();
        enum Command command = w->command;
        if (command == FINISH) break;
        w->running = true;
        assert(pthread_cond_broadcast(&condition) == 0);
        assert(pthread_mutex_unlock(&mutex) == 0);
        hv_check(hv_vcpu_set_reg(w->cpu, HV_REG_PC,
                 CODE_GPA + (command == WRITE ? 0x20 : command == SPIN ? 0x40 : 0)));
        hv_check(hv_vcpu_set_reg(w->cpu, HV_REG_CPSR, 0x3c5));
        hv_check(hv_vcpu_set_reg(w->cpu, HV_REG_X0, DATA_GPA));
        hv_check(hv_vcpu_set_reg(w->cpu, HV_REG_X1, NEW_VALUE));
        hv_check(hv_vcpu_set_reg(w->cpu, HV_REG_X3, PAGE_COUNT));
        hv_check(hv_vcpu_set_reg(w->cpu, HV_REG_X4, 4096));
        hv_check(hv_vcpu_set_reg(w->cpu, HV_REG_X5, 0));
        hv_check(hv_vcpu_set_reg(w->cpu, HV_REG_X6, RESULT_GPA + w->index * RESULT_SIZE));
        hv_check(hv_vcpu_set_reg(w->cpu, HV_REG_X7, DATA_GPA));
        hv_check(hv_vcpu_set_reg(w->cpu, HV_REG_X8, PAGE_COUNT));
        hv_check(hv_vcpu_run(w->cpu));
        if (command == SPIN) {
            assert(w->exit->reason == HV_EXIT_REASON_CANCELED);
        } else {
            assert(w->exit->reason == HV_EXIT_REASON_EXCEPTION);
            assert((w->exit->exception.syndrome >> 26) == 0x16);
        }
        assert(pthread_mutex_lock(&mutex) == 0);
        w->running = false;
        w->command = NONE;
        assert(pthread_cond_broadcast(&condition) == 0);
    }
    assert(pthread_mutex_unlock(&mutex) == 0);
    hv_check(hv_vcpu_destroy(w->cpu));
    return NULL;
}
static void dispatch(enum Command command) {
    assert(pthread_mutex_lock(&mutex) == 0);
    assert(all_stopped());
    for (unsigned i = 0; i < cpu_count; i++) workers[i].command = command;
    assert(pthread_cond_broadcast(&condition) == 0);
    assert(pthread_mutex_unlock(&mutex) == 0);
}
static void pause_running_cpus(void) {
    assert(pthread_mutex_lock(&mutex) == 0);
    for (;;) {
        bool all_running = true;
        for (unsigned i = 0; i < cpu_count; i++) all_running &= workers[i].running;
        if (all_running) break;
        timed_wait();
    }
    assert(pthread_mutex_unlock(&mutex) == 0);
    /* Allow workers to enter hv_vcpu_run; do not depend on this delay for safety.
     * The acknowledgement below is the boundary before any mapping mutation. */
    usleep(2000);
    hv_vcpu_t cpus[MAX_CPUS];
    for (unsigned i = 0; i < cpu_count; i++) cpus[i] = workers[i].cpu;
    hv_check(hv_vcpus_exit(cpus, cpu_count));
    await_stopped();
}
static void verify_read(size_t offset, size_t length, bool replaced, bool written) {
    dispatch(READ);
    await_stopped();
    for (size_t page = 0; page < PAGE_COUNT; page++) {
        size_t byte = page * 4096;
        uint64_t expected = written ? NEW_VALUE :
            (replaced && byte >= offset && byte < offset + length ? 0 : OLD_VALUE);
        assert(*(uint64_t *)((char *)memory + byte) == expected);
        for (unsigned i = 0; i < cpu_count; i++) assert(workers[i].observed[page] == expected);
    }
}
static void transaction(size_t offset, size_t length, DhrtFault fault) {
    void *target = (char *)memory + offset;
    const uint64_t gpa = DATA_GPA + offset;
    DhrtAttributes original;
    assert(dhrt_attributes(target, length, &original));
    printf("attributes current=%d maximum=%d inherit=%d shared=%d\n",
           original.current, original.maximum, original.inheritance, original.shared);
    dispatch(SPIN);
    pause_running_cpus();
    DhrtOutcome outcome = dhrt_replace_private(target, gpa, length, HV_FLAGS, fault);
    printf("transaction fault=%d result=%d stage=%d hv=0x%x mach=%d restore_hv=0x%x restore_mach=%d cleanup_failed=%d\n",
           fault, outcome.result, outcome.stage, outcome.hv_error,
           outcome.mach_error, outcome.restore_hv_error,
           outcome.restore_mach_error, outcome.cleanup_failed);
    if (fault == DHRT_FAIL_UNMAP_UNCERTAIN) {
        assert(outcome.result == DHRT_FATAL && outcome.retained_old_alias &&
               outcome.retained_fresh && !outcome.cleanup_failed);
        fatal_outcome = outcome;
        /* Guest commands must never resume after an uncertain unmap. */
        return;
    }
    assert(outcome.result == (fault == DHRT_NO_FAULT ? DHRT_COMMITTED : DHRT_ROLLED_BACK));
    assert(!outcome.cleanup_failed && !outcome.retained_old_alias && !outcome.retained_fresh);
    assert(dhrt_attributes_match(target, length, &original));
    metrics(fault ? "after-rollback" : "after-commit");
    verify_read(offset, length, fault == DHRT_NO_FAULT, false);
    metrics(fault ? "after-rollback-guest-read" : "after-commit-guest-read");
}
int main(int argc, char **argv) {
    assert(getpagesize() == 16384);
    cpu_count = argc > 1 ? (unsigned)atoi(argv[1]) : 2;
    assert(cpu_count == 2 || cpu_count == 4 || cpu_count == 8);
    bool partial = argc > 2;
    const size_t offset = partial ? 16384 : 0;
    const size_t length = partial ? 2 * 1024 * 1024 : DATA_SIZE;
    printf("Native transaction probe: %u vCPUs, %s\n", cpu_count,
           partial ? "2 MiB interior range" : "64 MiB full range");
    memory = mmap(NULL, DATA_SIZE, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    void *code = mmap(NULL, 16384, PROT_READ | PROT_WRITE, MAP_ANON | MAP_PRIVATE, -1, 0);
    void *results = mmap(NULL, cpu_count * RESULT_SIZE, PROT_READ | PROT_WRITE,
                         MAP_ANON | MAP_PRIVATE, -1, 0);
    assert(memory != MAP_FAILED && code != MAP_FAILED && results != MAP_FAILED);
    const uint32_t instructions[] = {
        0xf9400002, 0x8b0200a5, 0xf90000c2, 0x910020c6,
        0x8b040000, 0xf1000463, 0x54ffff41, 0xd4000002,
        0xf9000001, 0x8b040000, 0xf1000463, 0x54ffffa1, 0xd4000002,
        0, 0, 0,
        /* Repeatedly scan memory without a guest exit until the host kicks. */
        0xf9400002, 0x8b0200a5, 0x8b040000, 0xf1000463,
        0x54ffff81, 0xaa0703e0, 0xaa0803e3, 0x17fffff9,
    };
    memcpy(code, instructions, sizeof(instructions));
    memset(memory, 0x78, DATA_SIZE);
    /* Match the emulator's MADV_DONTFORK inheritance. */
    mach_check(mach_vm_inherit(mach_task_self(), (uintptr_t)memory,
                               DATA_SIZE, VM_INHERIT_NONE));
    hv_check(hv_vm_create(NULL));
    hv_check(hv_vm_map(code, CODE_GPA, 16384, HV_MEMORY_READ | HV_MEMORY_EXEC));
    hv_check(hv_vm_map(results, RESULT_GPA, cpu_count * RESULT_SIZE, HV_MEMORY_READ | HV_MEMORY_WRITE));
    hv_check(hv_vm_map(memory, DATA_GPA, DATA_SIZE, HV_FLAGS));
    for (unsigned i = 0; i < cpu_count; i++) {
        workers[i].index = i;
        workers[i].observed = (uint64_t *)((char *)results + i * RESULT_SIZE);
        assert(pthread_create(&workers[i].thread, NULL, worker_main, &workers[i]) == 0);
    }
    await_stopped();
    verify_read(offset, length, false, false);
    metrics("before");
    bool fatal_only = argc > 3;
    if (fatal_only) {
        transaction(offset, length, DHRT_FAIL_UNMAP_UNCERTAIN);
        goto cleanup;
    }
    transaction(offset, length, DHRT_FAIL_UNMAP);
    transaction(offset, length, DHRT_FAIL_REPLACE);
    transaction(offset, length, DHRT_FAIL_MAP);
    transaction(offset, length, DHRT_NO_FAULT);
    dispatch(WRITE);
    await_stopped();
    verify_read(offset, length, true, true);
    metrics("after-guest-reuse");
cleanup:
    dispatch(FINISH); // Destroys stopped vCPUs without another hv_vcpu_run.
    for (unsigned i = 0; i < cpu_count; i++) assert(pthread_join(workers[i].thread, NULL) == 0);
    hv_check(hv_vm_unmap(CODE_GPA, 16384));
    hv_check(hv_vm_unmap(RESULT_GPA, cpu_count * RESULT_SIZE));
    hv_check(hv_vm_unmap(DATA_GPA, DATA_SIZE));
    hv_check(hv_vm_destroy());
    if (fatal_only) {
        mach_check(mach_vm_deallocate(mach_task_self(),
                                      fatal_outcome.retained_old_alias, length));
        assert(munmap((void *)(uintptr_t)fatal_outcome.retained_fresh, length) == 0);
    }
    assert(munmap(memory, DATA_SIZE) == 0);
    assert(munmap(code, 16384) == 0);
    assert(munmap(results, cpu_count * RESULT_SIZE) == 0);
    puts(fatal_only ? "PASS: uncertain-unmap branch retains resources and never resumes guest" :
         "PASS: all vCPUs acknowledge stops; rollback, neighbors, zeros, and guest reuse agree");
}
