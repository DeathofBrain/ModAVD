#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <sched.h>
#include <stddef.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef struct {
    _Alignas(16) uint64_t expected[64];
    uint64_t observed[64];
    uint64_t bad_q30[2];
    _Atomic uint64_t stop, ready, checks, errors;
    uint64_t mismatch_index, mismatch_expected, mismatch_actual, inject_bad_q30;
} RegisterState;
_Static_assert(offsetof(RegisterState, observed) == 512, "assembly ABI");
_Static_assert(offsetof(RegisterState, bad_q30) == 1024, "assembly ABI");
_Static_assert(offsetof(RegisterState, stop) == 1040, "assembly ABI");
_Static_assert(offsetof(RegisterState, ready) == 1048, "assembly ABI");
_Static_assert(offsetof(RegisterState, checks) == 1056, "assembly ABI");
_Static_assert(offsetof(RegisterState, errors) == 1064, "assembly ABI");
_Static_assert(offsetof(RegisterState, mismatch_index) == 1072, "assembly ABI");
_Static_assert(offsetof(RegisterState, mismatch_expected) == 1080, "assembly ABI");
_Static_assert(offsetof(RegisterState, mismatch_actual) == 1088, "assembly ABI");
_Static_assert(offsetof(RegisterState, inject_bad_q30) == 1096, "assembly ABI");

extern void *simd_register_worker(void *state);
static RegisterState state;
static int pin_error, fifo_error;
static bool try_fifo;

static uint64_t milliseconds(void) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return (uint64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static void *worker_start(void *unused) {
    (void)unused;
    cpu_set_t cpus;
    CPU_ZERO(&cpus);
    CPU_SET(0, &cpus);
    pin_error = sched_setaffinity(0, sizeof(cpus), &cpus) ? errno : 0;
    if (try_fifo) {
        struct sched_param parameters = {.sched_priority = 1};
        fifo_error = sched_setscheduler(0, SCHED_FIFO, &parameters) ? errno : 0;
    }
    /* All C and scheduler setup precedes loading the persistent Q values. */
    return simd_register_worker(&state);
}

int main(int argc, char **argv) {
    unsigned seconds = 45;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--seconds") && i + 1 < argc) {
            char *end;
            unsigned long value = strtoul(argv[++i], &end, 10);
            if (!argv[i][0] || *end || value < 1 || value > 90) return 2;
            seconds = value;
        } else if (!strcmp(argv[i], "--bad-q30")) {
            state.inject_bad_q30 = 1;
        } else if (!strcmp(argv[i], "--fifo")) {
            try_fifo = true;
        } else {
            fprintf(stderr, "usage: %s [--seconds 1..90] [--bad-q30] [--fifo]\n", argv[0]);
            return 2;
        }
    }
    long cpu_count = sysconf(_SC_NPROCESSORS_ONLN);
    if (cpu_count > 1) {
        cpu_set_t cpus;
        CPU_ZERO(&cpus);
        CPU_SET(1, &cpus);
        if (sched_setaffinity(0, sizeof(cpus), &cpus)) return 2;
    } else if (try_fifo) {
        return 2; /* Keep the stopping thread schedulable. */
    }
    for (unsigned i = 0; i < 64; i++) {
        state.expected[i] = UINT64_C(0x8a2e509b6fc314d7) ^
                            ((uint64_t)i * UINT64_C(0x9e3779b97f4a7c15));
    }
    state.bad_q30[0] = ~state.expected[60];
    state.bad_q30[1] = ~state.expected[61];
    pthread_t worker;
    if (pthread_create(&worker, NULL, worker_start, NULL)) return 2;
    uint64_t start = milliseconds();
    while (!atomic_load_explicit(&state.ready, memory_order_acquire)) {
        struct timespec delay = {.tv_nsec = 1000000};
        nanosleep(&delay, NULL);
        if (milliseconds() - start > 5000) return 2;
    }
    printf("{\"event\":\"ready\",\"pid\":%d,\"monotonic_ms\":%" PRIu64
           ",\"seconds\":%u,\"cpu_count\":%ld,\"worker_cpu\":0,\"pin_error\":%d,"
           "\"fifo_requested\":%s,\"fifo_error\":%d,\"bad_q30\":%s}\n",
           getpid(), milliseconds(), seconds, cpu_count, pin_error,
           try_fifo ? "true" : "false", fifo_error,
           state.inject_bad_q30 ? "true" : "false");
    fflush(stdout);
    while (milliseconds() - start < (uint64_t)seconds * 1000 &&
           !atomic_load_explicit(&state.errors, memory_order_acquire)) {
        struct timespec delay = {.tv_sec = 1};
        nanosleep(&delay, NULL);
        printf("{\"event\":\"progress\",\"monotonic_ms\":%" PRIu64
               ",\"checks\":%" PRIu64 ",\"errors\":%" PRIu64 "}\n",
               milliseconds(), atomic_load(&state.checks), atomic_load(&state.errors));
        fflush(stdout);
    }
    atomic_store_explicit(&state.stop, 1, memory_order_release);
    if (pthread_join(worker, NULL)) return 2;
    uint64_t errors = atomic_load(&state.errors), checks = atomic_load(&state.checks);
    bool passed = !errors && checks && !pin_error;
    printf("{\"event\":\"done\",\"pass\":%s,\"checks\":%" PRIu64
           ",\"errors\":%" PRIu64 ",\"mismatch_word\":%" PRIu64
           ",\"expected\":\"%016" PRIx64 "\",\"actual\":\"%016" PRIx64 "\"}\n",
           passed ? "true" : "false", checks, errors, state.mismatch_index,
           state.mismatch_expected, state.mismatch_actual);
    return passed ? 0 : 1;
}
