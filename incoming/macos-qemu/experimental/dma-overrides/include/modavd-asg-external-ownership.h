#pragma once

#include "modavd-dma-sized-borrow.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Optional, single-use provenance query. Absence always means unknown. */
MODAVD_DMA_EXPORT bool modavd_asg_external_owned_v1(
    uint32_t handle, const void* base, uint64_t size);

#ifdef __cplusplus
}

/* A producer may certify only a RingBlob it actually retains and whose map and
 * allocation extent exactly match the values passed to create_instance. */
class ModavdAsgExternalScope final {
public:
    ModavdAsgExternalScope(uint32_t handle, const void* base, uint64_t size,
                          bool owned);
    ~ModavdAsgExternalScope();
    ModavdAsgExternalScope(const ModavdAsgExternalScope&) = delete;
    ModavdAsgExternalScope& operator=(const ModavdAsgExternalScope&) = delete;

private:
    friend bool modavd_asg_external_owned_v1(uint32_t, const void*, uint64_t);
    ModavdAsgExternalScope* mPrevious;
    uint32_t mHandle;
    const void* mBase;
    uint64_t mSize;
    bool mOwned;
    bool mConsumed = false;
};
#endif
