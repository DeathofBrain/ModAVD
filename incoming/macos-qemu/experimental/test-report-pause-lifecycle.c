#include <assert.h>
#include <stdio.h>
#include "exec/report-pause-lifecycle.h"

int main(void)
{
    const uint64_t features = 1ULL << 5;
    const uint8_t driver_ok = 4;
    RplSnapshot before = {1, 2, 3, features, 0, driver_ok, true, true, true};
    assert(rpl_unchanged(&before, &before, features, driver_ok));
    RplSnapshot with_poison_feature = before;
    with_poison_feature.features |= 1ULL << 4;
    assert(rpl_unchanged(&with_poison_feature, &with_poison_feature,
                         features, driver_ok));
    RplSnapshot after;
#define REJECT(change) do { after = before; change; \
    assert(!rpl_unchanged(&before, &after, features, driver_ok)); } while (0)
    REJECT(after.vm_epoch++);          /* reset, shutdown, stop/start, snapshot */
    REJECT(after.queue_epoch++);       /* queue addresses, features, status */
    REJECT(after.device_epoch++);      /* poison/config/reset/unrealize */
    REJECT(after.features ^= 1);       /* even an unrelated feature invalidates */
    REJECT(after.features &= ~(1ULL << 5));
    REJECT(after.status = 0);
    REJECT(after.status |= 128);       /* changed device status */
    REJECT(after.poison = 0xfeed);
    REJECT(after.realized = false);
    REJECT(after.running = false);
    REJECT(after.queue_ready = false);
    REJECT(after.vm_epoch = UINT64_MAX);
    REJECT(after.queue_epoch = UINT64_MAX);
    REJECT(after.device_epoch = UINT64_MAX);
#undef REJECT
    for (unsigned bits = 0; bits < 16; bits++) {
        bool stopped = bits & 1, stop = bits & 2;
        bool same_epoch = bits & 4, running = bits & 8;
        bool resume = rpl_resume_cpu(stopped, stop, same_epoch, running);
        if (stopped || stop || !same_epoch || !running) {
            assert(!resume);
        } else {
            assert(resume);
        }
    }
    puts("PASS: reset/queue/feature/poison/unrealize/run/saturation cancellation; previous CPU stops preserved");
}
