/* Exact cpus.c owner code is inserted between the fixture and test cases. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <glib.h>
typedef struct CPUState {
    struct CPUState *next;
    bool stopped, stop, unplug;
    unsigned references;
} CPUState;
static CPUState *cpus;
static bool bql = true, vm_running = true, virtual_clock = true;
static unsigned wait_count;
static void (*during_wait)(void), (*during_unlock)(void);
static int qemu_pause_cond, qemu_global_mutex;
#define CPU_FOREACH(cpu) for ((cpu) = cpus; (cpu); (cpu) = (cpu)->next)
#define OBJECT(cpu) (cpu)
#define QEMU_CLOCK_VIRTUAL 0
static bool qemu_mutex_iothread_locked(void) { return bql; }
static bool qemu_in_vcpu_thread(void) { return false; }
static bool qemu_cpu_is_self(CPUState *cpu) { (void)cpu; return false; }
static void qemu_cpu_stop(CPUState *cpu, bool ignored) {
    (void)ignored; cpu->stopped = true; cpu->stop = false;
}
static void qemu_cpu_kick(CPUState *cpu) { (void)cpu; }
static void qemu_clock_enable(int clock, bool enabled) {
    (void)clock; virtual_clock = enabled;
}
static void object_ref(CPUState *cpu) { cpu->references++; }
static void object_unref(CPUState *cpu) { assert(cpu->references > 1); cpu->references--; }
static bool qemu_vm_reclaim_running(void) { return vm_running; }
static bool all_vcpus_paused(void) {
    CPUState *cpu;
    CPU_FOREACH(cpu) if (!cpu->stopped) return false;
    return true;
}
static void replay_mutex_unlock(void) {}
static void replay_mutex_lock(void) {
    if (during_unlock) {
        void (*hook)(void) = during_unlock; during_unlock = NULL;
        assert(!bql); bql = true; hook(); bql = false;
    }
}
static void qemu_mutex_unlock_iothread(void) { assert(bql); bql = false; }
static void qemu_mutex_lock_iothread(void) { assert(!bql); bql = true; }
static void qemu_cond_wait(void *condition, void *mutex) {
    (void)condition; (void)mutex;
    assert(bql && ++wait_count < 10);
    if (during_wait) {
        void (*hook)(void) = during_wait; during_wait = NULL; hook();
    }
    CPUState *cpu;
    CPU_FOREACH(cpu) if (cpu->stop) qemu_cpu_stop(cpu, true);
}
#include "pause-owner-under-test.inc"
static CPUState fixture[4];
static void reset(void) {
    assert(!reclaim_paused_cpus);
    for (unsigned i = 0; i < 4; i++) fixture[i] = (CPUState){.references = 1};
    cpus = &fixture[0]; fixture[0].next = &fixture[1]; fixture[1].next = &fixture[2];
    fixture[1].stopped = true; fixture[2].stop = true;
    bql = vm_running = virtual_clock = true;
    wait_count = 0; during_wait = during_unlock = NULL;
}
static void references_balanced(void) {
    assert(!reclaim_paused_cpus && bql);
    for (unsigned i = 0; i < 4; i++) assert(fixture[i].references == 1);
}
static void stop_request(void) { vm_running = false; pause_all_vcpus(); }
static void start_request(void) { vm_running = true; resume_all_vcpus(); }
static void stop_then_start(void) { stop_request(); start_request(); }
static void replace_cpu(void) {
    qemu_cpu_stop(&fixture[0], true); // cpu_remove_sync joins before unlink.
    fixture[0].unplug = true;
    cpus = &fixture[3]; fixture[3].next = &fixture[1];
    fixture[3].stopped = true; cpu_resume(&fixture[3]);
}
int main(void) {
    reset(); pause_all_vcpus_for_reclaim(); assert(all_vcpus_paused());
    assert(qemu_reclaim_pause_active() && !virtual_clock);
    resume_vcpus_after_reclaim();
    assert(!fixture[0].stopped && !fixture[0].stop);
    assert(fixture[1].stopped && !fixture[1].stop);
    assert(fixture[2].stopped && fixture[2].stop);
    references_balanced();

    reset(); during_wait = stop_request; pause_all_vcpus_for_reclaim();
    resume_vcpus_after_reclaim(); assert(all_vcpus_paused() && !virtual_clock);
    references_balanced(); start_request(); assert(!fixture[0].stopped);

    reset(); during_wait = stop_then_start; pause_all_vcpus_for_reclaim();
    assert(all_vcpus_paused() && !virtual_clock);
    resume_vcpus_after_reclaim();
    for (unsigned i = 0; i < 3; i++) assert(!fixture[i].stopped && !fixture[i].stop);
    references_balanced();

    reset(); during_unlock = start_request; pause_all_vcpus_for_reclaim();
    assert(all_vcpus_paused() && !virtual_clock);
    resume_vcpus_after_reclaim();
    for (unsigned i = 0; i < 3; i++) assert(!fixture[i].stopped);
    references_balanced();

    reset(); during_wait = replace_cpu; pause_all_vcpus_for_reclaim();
    assert(all_vcpus_paused()); resume_vcpus_after_reclaim();
    assert(!fixture[3].stopped && fixture[0].stopped && fixture[0].unplug);
    assert(fixture[1].stopped && fixture[2].stopped);
    references_balanced();

    reset(); pause_all_vcpus_for_reclaim(); cpu_resume(&fixture[0]);
    fixture[0].unplug = true; resume_vcpus_after_reclaim();
    assert(fixture[0].stopped); references_balanced();
    puts("PASS: original CPU stops, concurrent stop/start and replay gap, topology replacement, unplug, refs");
}
