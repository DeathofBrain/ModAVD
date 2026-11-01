/* Compile against the exact registry header used by the experimental core. */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "exec/hvf-map-registry.h"

static HmrRegistry registry;
static uint64_t random_state = 0x6d6f64617664;
static unsigned random_u(unsigned limit)
{
    random_state ^= random_state << 13;
    random_state ^= random_state >> 7;
    random_state ^= random_state << 17;
    return random_state % limit;
}

int main(void)
{
    hmr_init(&registry);
    assert(hmr_update(&registry, 0x10000, 0x10000, 0x100000, 7, true, false));
    assert(hmr_update(&registry, 0x14000, 0x4000, 0, 1, false, true));
    assert(registry.count == 3);
    assert(hmr_lookup(&registry, 0x10000, 0x4000)->flags == 7);
    assert(hmr_lookup(&registry, 0x14000, 0x4000)->flags == 1);
    assert(hmr_lookup(&registry, 0x18000, 0x8000)->flags == 7);
    assert(!hmr_lookup(&registry, 0x13000, 0x2000));
    assert(hmr_update(&registry, 0x14000, 0x4000, 0, 7, false, true));
    assert(registry.count == 1);
    const HmrMap *owner = hmr_lookup(&registry, 0x10000, 0x10000);
    assert(owner && hmr_no_alias(&registry, owner, 0x100000, 0x10000));
    assert(hmr_update(&registry, 0x50000, 0x4000, 0x104000, 3, true, false));
    owner = hmr_lookup(&registry, 0x10000, 0x10000);
    assert(!hmr_no_alias(&registry, owner, 0x104000, 0x4000));
    assert(hmr_update(&registry, 0x14000, 0x8000, 0x900000, 5, true, false));
    assert(hmr_lookup(&registry, 0x14000, 0x8000)->hva == 0x900000);
    assert(hmr_lookup(&registry, 0x1c000, 0x4000)->hva == 0x10c000);
    assert(hmr_update(&registry, 0x14000, 0x4000, 0, 0, false, false));
    assert(!hmr_lookup(&registry, 0x14000, 1));
    assert(hmr_lookup(&registry, 0x18000, 0x4000)->hva == 0x904000);

    /* Independent per-page oracle exercises splitting, replacement, protection,
     * unmap holes, and effective permissions after many overlapping updates. */
    struct { bool mapped; uint64_t hva, flags; } oracle[64] = {0};
    hmr_init(&registry);
    for (unsigned step = 0; step < 20000; step++) {
        unsigned first = random_u(64), pages = 1 + random_u(64 - first);
        unsigned operation = random_u(3);
        uint64_t flags = random_u(8), hva = (1 + random_u(512)) * 0x1000ULL;
        bool covered = true;
        for (unsigned page = first; page < first + pages; page++) {
            covered &= oracle[page].mapped;
        }
        if (operation == 2 && !covered) {
            continue; /* A failed kernel protect must not update metadata. */
        }
        assert(hmr_update(&registry, first * 0x1000ULL, pages * 0x1000ULL,
                           hva, flags, operation == 0, operation == 2));
        for (unsigned page = first; page < first + pages; page++) {
            if (operation != 2) {
                oracle[page].mapped = operation == 0;
                oracle[page].hva = hva + (page - first) * 0x1000ULL;
            }
            oracle[page].flags = flags;
        }
        for (unsigned page = 0; page < 64; page++) {
            uint64_t gpa = page * 0x1000ULL;
            const HmrMap *m = hmr_lookup(&registry, gpa, 0x1000);
            assert(!!m == oracle[page].mapped);
            if (m) {
                assert(m->hva + gpa - m->gpa == oracle[page].hva);
                assert(m->flags == oracle[page].flags);
            }
        }
    }
    hmr_init(&registry);
    assert(!hmr_update(&registry, UINT64_MAX - 0x1000, 0x2000, 0, 7, true, false));
    assert(!registry.complete);
    assert(!hmr_lookup(&registry, 0, 1));
    hmr_init(&registry);
    assert(!hmr_update(&registry, 0, 0x2000, UINT64_MAX - 0x1000, 7, true, false));
    hmr_init(&registry);
    assert(!hmr_update(&registry, 0x1000, 0x1000, 0, 1, false, true));
    assert(!registry.complete); /* Successful protect of an untracked gap. */
    hmr_init(&registry);
    for (unsigned i = 0; i < HMR_CAPACITY; i++) {
        assert(hmr_update(&registry, i * 0x2000ULL, 0x1000,
                           0x10000000 + i * 0x2000ULL, 3, true, false));
    }
    assert(!hmr_update(&registry, HMR_CAPACITY * 0x2000ULL, 0x1000,
                        0x90000000, 3, true, false));
    assert(!registry.complete);
    hmr_init(&registry);
    registry.generation = UINT64_MAX;
    assert(!hmr_update(&registry, 0x1000, 0x1000, 0x10000, 7, true, false));
    assert(!registry.complete);
    puts("PASS: effective permissions, splits, aliases, generations, capacity, and 20,000 oracle updates");
}
