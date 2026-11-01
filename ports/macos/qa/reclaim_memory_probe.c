/* Bounded Android RAM regression: protected live data next to discard/reuse.
 * JSONL stages let the host sample QEMU independently. No disk writes. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

static atomic_bool finished;
static atomic_uint errors;
static atomic_uint_fast64_t live_checks;
static uint64_t *live_words;
static size_t live_count;

static uint64_t pattern(size_t i, uint64_t seed) {
    uint64_t x = (uint64_t)i + seed + UINT64_C(0x9e3779b97f4a7c15);
    x = (x ^ (x >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
    x = (x ^ (x >> 27)) * UINT64_C(0x94d049bb133111eb);
    return x ^ (x >> 31);
}

static uint64_t millis(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now)) return 0;
    return (uint64_t)now.tv_sec * 1000 + (uint64_t)now.tv_nsec / 1000000;
}

static void stage(const char *name, unsigned round) {
    printf("{\"event\":\"%s\",\"round\":%u,\"monotonic_ms\":%" PRIu64
           ",\"live_checks\":%" PRIuFAST64 ",\"errors\":%u}\n",
           name, round, millis(), atomic_load(&live_checks), atomic_load(&errors));
    fflush(stdout);
}

static bool check(const uint64_t *words, size_t count, uint64_t seed, bool zero) {
    for (size_t i = 0; i < count; ++i) {
        if (words[i] != (zero ? 0 : pattern(i, seed))) {
            atomic_fetch_add(&errors, 1);
            return false;
        }
    }
    return true;
}

static void fill(uint64_t *words, size_t count, uint64_t seed) {
    for (size_t i = 0; i < count; ++i) words[i] = pattern(i, seed);
}

static void *watch_live(void *unused) {
    (void)unused;
    while (!atomic_load(&finished)) {
        if (!check(live_words, live_count, UINT64_C(0x3174bdef), false)) break;
        atomic_fetch_add(&live_checks, 1);
        struct timespec delay = {.tv_sec = 0, .tv_nsec = 200000000};
        while (nanosleep(&delay, &delay) && errno == EINTR) {}
    }
    return NULL;
}

static unsigned argument(const char *text, unsigned low, unsigned high) {
    char *end = NULL;
    errno = 0;
    unsigned long value = strtoul(text, &end, 10);
    if (errno || !text[0] || !end || *end || value < low || value > high) {
        fprintf(stderr, "argument must be %u..%u\n", low, high);
        exit(2);
    }
    return (unsigned)value;
}

int main(int argc, char **argv) {
    if (argc > 4) {
        fprintf(stderr, "usage: %s [churn-MiB=256] [rounds=3] [release-seconds=8]\n", argv[0]);
        return 2;
    }
    const unsigned mib = argc > 1 ? argument(argv[1], 128, 512) : 256;
    const unsigned rounds = argc > 2 ? argument(argv[2], 1, 5) : 3;
    const unsigned seconds = argc > 3 ? argument(argv[3], 2, 15) : 8;
    const size_t live_bytes = 16 * 1024 * 1024;
    const size_t churn_bytes = (size_t)mib * 1024 * 1024;
    long queried_page = sysconf(_SC_PAGESIZE);
    if (queried_page < 1024 || queried_page > 65536) return 2;
    const size_t page = (size_t)queried_page;
    const size_t total = live_bytes + page + churn_bytes + page;
    unsigned char *mapping = mmap(NULL, total, PROT_READ | PROT_WRITE,
                                  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapping == MAP_FAILED) { perror("mmap"); return 2; }
    if (mprotect(mapping + live_bytes, page, PROT_NONE) ||
        mprotect(mapping + live_bytes + page + churn_bytes, page, PROT_NONE)) {
        perror("mprotect"); munmap(mapping, total); return 2;
    }
    live_words = (uint64_t *)mapping;
    live_count = live_bytes / sizeof(uint64_t);
    uint64_t *churn = (uint64_t *)(mapping + live_bytes + page);
    const size_t count = churn_bytes / sizeof(uint64_t);
    fill(live_words, live_count, UINT64_C(0x3174bdef));
    pthread_t watcher;
    if (pthread_create(&watcher, NULL, watch_live, NULL)) {
        munmap(mapping, total); return 2;
    }
    printf("{\"event\":\"begin\",\"churn_bytes\":%zu,\"live_bytes\":%zu,"
           "\"guest_page_bytes\":%zu,\"rounds\":%u,\"release_seconds\":%u}\n",
           churn_bytes, live_bytes, page, rounds, seconds);
    fflush(stdout);
    for (unsigned round = 1; round <= rounds && !atomic_load(&errors); ++round) {
        if (!check(churn, count, 0, true)) break;
        fill(churn, count, UINT64_C(0xba453da4) + round);
        if (!check(churn, count, UINT64_C(0xba453da4) + round, false)) break;
        stage("filled_and_verified", round);
        /* Leave a bounded resident plateau for host measurement. */
        struct timespec held = {.tv_sec = 2, .tv_nsec = 0};
        while (nanosleep(&held, &held) && errno == EINTR) {}
        if (madvise(churn, churn_bytes, MADV_DONTNEED)) {
            perror("madvise"); atomic_fetch_add(&errors, 1); break;
        }
        stage("released", round);
        struct timespec delay = {.tv_sec = (time_t)seconds, .tv_nsec = 0};
        while (nanosleep(&delay, &delay) && errno == EINTR) {}
        if (!check(churn, count, 0, true)) break;
        stage("zero_reuse_verified", round);
    }
    atomic_store(&finished, true);
    if (pthread_join(watcher, NULL)) atomic_fetch_add(&errors, 1);
    if (!check(live_words, live_count, UINT64_C(0x3174bdef), false)) stage("live_error", 0);
    bool pass = atomic_load(&errors) == 0 && atomic_load(&live_checks) > 0;
    printf("{\"event\":\"done\",\"pass\":%s,\"rounds\":%u,\"live_checks\":%"
           PRIuFAST64 ",\"errors\":%u}\n", pass ? "true" : "false", rounds,
           atomic_load(&live_checks), atomic_load(&errors));
    fflush(stdout);
    if (munmap(mapping, total)) return 2;
    return pass ? 0 : 1;
}
