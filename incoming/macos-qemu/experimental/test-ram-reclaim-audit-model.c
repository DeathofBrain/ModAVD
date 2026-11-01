/* Tests the exact metadata helper shipped by the experimental QEMU patch. */
#include "exec/ram-reclaim-audit-model.h"
#include <assert.h>
#include <pthread.h>
#include <stdio.h>

#define PAGE UINT64_C(16384)
#define SIZE (8 * PAGE)

static void init(RraState *s)
{
    assert(rra_init(s, SIZE, PAGE, RRA_PRIVATE));
}

static void ranges_and_owners(void)
{
    RraState s;
    init(&s);
    assert(rra_map(&s, 0, 4096, true, "report"));
    assert(s.report_maps == 1 && s.report_map_bytes == 4096);
    assert(s.tainted_pages == 0 && s.exposures == 0);
    assert(rra_candidates(&s, 0, SIZE) == SIZE);
    assert(rra_candidates(&s, 0, 4096) == 0);
    assert(rra_candidates(&s, 4096, PAGE) == 0);
    assert(rra_map(&s, 0, 4096, false, "ring"));
    assert(s.tainted_pages == 1 && s.exposures == 1);
    assert(rra_candidates(&s, 0, PAGE) == 0);
    assert(rra_expose(&s, 4096, 4096, "ring"));
    assert(s.tainted_pages == 1 && s.causes[0].calls == 2);
    assert(s.causes[0].bytes == 8192 && s.causes[0].invalid == 0);
    assert(rra_expose(&s, 2 * PAGE - 1, 2, "cross-page"));
    assert(s.tainted_pages == 3);
    assert(rra_candidates(&s, 0, SIZE) == 5 * PAGE);
    assert(rra_candidates(&s, 2 * PAGE, 2 * PAGE) == PAGE);
    assert(rra_expose(&s, SIZE - 1, 1, "last-byte"));
    assert(s.tainted_pages == 4);
    rra_destroy(&s);
    init(&s);
    assert(rra_map(&s, SIZE - 1, 2, true, "invalid-report"));
    assert(s.whole_reason && s.tainted_pages == s.pages);
    rra_destroy(&s);
}

static void invalid_ranges_and_backing(void)
{
    RraState s;
    static const uint64_t ranges[][2] = {
        { 0, 0 }, { SIZE, 1 }, { SIZE - 1, 2 },
        { UINT64_MAX, 2 }, { 1, UINT64_MAX },
    };
    unsigned i;
    for (i = 0; i < sizeof(ranges) / sizeof(ranges[0]); i++) {
        init(&s);
        assert(rra_expose(&s, ranges[i][0], ranges[i][1], "invalid"));
        assert(s.whole_reason && s.tainted_pages == s.pages);
        assert(s.causes[0].invalid == 1);
        assert(rra_candidates(&s, 0, SIZE) == 0);
        rra_destroy(&s);
    }
    for (i = RRA_UNKNOWN; i <= RRA_CUSTOM; i++) {
        assert(rra_init(&s, SIZE, PAGE, (RraBacking)i));
        assert(rra_candidates(&s, 0, SIZE) ==
               (i == RRA_PRIVATE ? SIZE : 0));
        rra_destroy(&s);
    }
    assert(!rra_init(&s, 0, PAGE, RRA_PRIVATE));
    assert(rra_candidates(&s, 0, PAGE) == 0);
    rra_destroy(&s);
    assert(!rra_init(&s, SIZE, 0, RRA_PRIVATE));
    assert(rra_expose(&s, 0, 1, "invalid-state"));
    rra_destroy(&s);
    assert(!rra_init(&s, SIZE, 3, RRA_PRIVATE));
    rra_destroy(&s);
    assert(rra_init(&s, PAGE + 1, PAGE, RRA_PRIVATE));
    assert(s.pages == 2);
    assert(rra_candidates(&s, 0, PAGE) == PAGE);
    assert(rra_candidates(&s, PAGE, PAGE) == 0);
    rra_destroy(&s);
    /* Largest address arithmetic with a tiny, allocatable bitmap. */
    assert(rra_init(&s, UINT64_MAX, UINT64_C(1) << 63, RRA_PRIVATE));
    assert(s.pages == 2);
    assert(rra_candidates(&s, 0, UINT64_C(1) << 63) ==
           (UINT64_C(1) << 63));
    assert(rra_expose(&s, UINT64_MAX - 1, 1, "last-byte"));
    assert(s.tainted_pages == 1);
    assert(rra_expose(&s, UINT64_MAX - 1, 2, "overflow"));
    assert(s.whole_reason);
    rra_destroy(&s);
}

static void generations_and_saturation(void)
{
    RraState s;
    RraReservation token, forged;
    init(&s);
    assert(rra_reserve(&s, 0, PAGE, s.generation, &token));
    assert(rra_reservation_valid(&s, &token));
    assert(!rra_expose(&s, 4096, 1, "overlap"));
    assert(!rra_expose(&s, 0, 0, "unknown-extent"));
    assert(!rra_taint_all(&s, "unknown"));
    assert(rra_expose(&s, PAGE, 1, "non-overlap"));
    forged = token;
    forged.length *= 2;
    rra_release(&s, &forged);
    assert(rra_reservation_valid(&s, &token));
    rra_release(&s, &token);
    assert(!rra_reservation_valid(&s, &token));
    assert(rra_reserve(&s, 0, PAGE, s.generation, &token));
    rra_invalidate(&s, "backing-transition");
    assert(!rra_reservation_valid(&s, &token));
    assert(!rra_reserve(&s, 0, PAGE, token.generation, &forged));
    rra_destroy(&s);
    init(&s);
    s.generation = UINT64_MAX;
    assert(rra_reserve(&s, 0, PAGE, UINT64_MAX, &token));
    rra_invalidate(&s, "saturated-generation");
    assert(s.generation == UINT64_MAX);
    assert(!rra_reservation_valid(&s, &token));
    rra_destroy(&s);
    init(&s);
    s.next_id = UINT64_MAX;
    assert(!rra_reserve(&s, 0, PAGE, s.generation, &token));
    s.exposures = s.exposure_bytes = UINT64_MAX;
    assert(rra_expose(&s, 0, PAGE, "saturated-counters"));
    assert(s.exposures == UINT64_MAX && s.exposure_bytes == UINT64_MAX);
    assert(rra_add(UINT64_MAX - 4, 5) == UINT64_MAX);
    rra_destroy(&s);
}

static uint32_t random_state = 1;
static uint32_t next_random(void)
{
    random_state = random_state * 1664525u + 1013904223u;
    return random_state;
}

static void randomized_page_oracle(void)
{
    RraState s;
    unsigned trial;
    for (trial = 0; trial < 500; trial++) {
        bool oracle[8] = { false };
        unsigned op, page;
        init(&s);
        for (op = 0; op < 40; op++) {
            uint64_t start = next_random() % SIZE;
            uint64_t length = next_random() % (SIZE - start) + 1;
            bool report_owner = (next_random() % 4 == 0);
            uint64_t expected = 0;
            assert(rra_map(&s, start, length, report_owner, "random-map"));
            if (!report_owner) {
                for (page = 0; page < 8; page++) {
                    if (start < (page + 1) * PAGE && page * PAGE < start + length) {
                        oracle[page] = true;
                    }
                }
            }
            for (page = 0; page < 8; page++) {
                expected += !oracle[page] * PAGE;
                assert(rra_candidates(&s, page * PAGE, PAGE) ==
                       (oracle[page] ? 0 : PAGE));
            }
            assert(rra_candidates(&s, 0, SIZE) == expected);
        }
        rra_destroy(&s);
    }
}

typedef struct Race {
    pthread_mutex_t lock;
    pthread_cond_t condition;
    RraState state;
    unsigned ready, successes;
    bool go, exposure;
    RraReservation token;
} Race;

typedef struct Worker {
    Race *race;
    bool reserve;
} Worker;

static void *race_worker(void *opaque)
{
    Worker *worker = opaque;
    Race *race = worker->race;
    bool success;
    assert(!pthread_mutex_lock(&race->lock));
    race->ready++;
    assert(!pthread_cond_broadcast(&race->condition));
    while (!race->go) {
        assert(!pthread_cond_wait(&race->condition, &race->lock));
    }
    success = worker->reserve ?
        rra_reserve(&race->state, 0, PAGE, race->state.generation, &race->token) :
        rra_expose(&race->state, PAGE - 1, 1, "racing-exposure");
    race->successes += success;
    if (!worker->reserve) {
        race->exposure = success;
    }
    assert(!pthread_mutex_unlock(&race->lock));
    return NULL;
}

static void concurrent_reservations(void)
{
    unsigned trial;
    for (trial = 0; trial < 500; trial++) {
        Race race = { 0 };
        Worker workers[2] = { { &race, true }, { &race, trial % 2 == 0 } };
        pthread_t threads[2];
        unsigned i;
        init(&race.state);
        assert(!pthread_mutex_init(&race.lock, NULL));
        assert(!pthread_cond_init(&race.condition, NULL));
        for (i = 0; i < 2; i++) {
            assert(!pthread_create(&threads[i], NULL, race_worker, &workers[i]));
        }
        assert(!pthread_mutex_lock(&race.lock));
        while (race.ready != 2) {
            assert(!pthread_cond_wait(&race.condition, &race.lock));
        }
        race.go = true;
        assert(!pthread_cond_broadcast(&race.condition));
        assert(!pthread_mutex_unlock(&race.lock));
        for (i = 0; i < 2; i++) {
            assert(!pthread_join(threads[i], NULL));
        }
        assert(race.successes == 1);
        assert(!pthread_mutex_lock(&race.lock));
        if (race.state.reservation_id) {
            assert(rra_reservation_valid(&race.state, &race.token));
            assert(!rra_expose(&race.state, PAGE - 1, 1, "blocked"));
            rra_release(&race.state, &race.token);
            assert(rra_expose(&race.state, PAGE - 1, 1, "released"));
        } else {
            assert(race.exposure && race.state.tainted_pages == 1);
        }
        rra_destroy(&race.state);
        assert(!pthread_mutex_unlock(&race.lock));
        assert(!pthread_mutex_destroy(&race.lock));
        assert(!pthread_cond_destroy(&race.condition));
    }
}

int main(void)
{
    ranges_and_owners();
    invalid_ranges_and_backing();
    generations_and_saturation();
    randomized_page_oracle();
    concurrent_reservations();
    puts("PASS: ranges, owner classes, overflow, backing, generations, "
         "20,000 randomized operations and 500 two-thread races");
    return 0;
}
