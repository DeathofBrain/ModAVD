// Copyright (C) 2025 The Android Open Source Project
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#include "gfxstream/host/dma_device.h"
#include "modavd-dma-sized-borrow.h"
#include "modavd-asg-external-ownership.h"
#include <atomic>
#include <cstdlib>

namespace {
std::atomic<ModavdDmaBorrowV1> sModavdDmaBorrow{nullptr};
thread_local bool sModavdPrecisePixelTransfer = false;
thread_local ModavdAsgExternalScope* sModavdAsgExternalScope = nullptr;
}

ModavdAsgExternalScope::ModavdAsgExternalScope(
    uint32_t handle, const void* base, uint64_t size, bool owned)
    : mPrevious(sModavdAsgExternalScope), mHandle(handle), mBase(base),
      mSize(size), mOwned(owned && handle && base && size &&
          size <= UINTPTR_MAX - reinterpret_cast<uintptr_t>(base)) {
    sModavdAsgExternalScope = this;
}

ModavdAsgExternalScope::~ModavdAsgExternalScope() {
    if (sModavdAsgExternalScope != this) std::abort();
    sModavdAsgExternalScope = mPrevious;
}

extern "C" MODAVD_DMA_EXPORT bool modavd_asg_external_owned_v1(
    uint32_t handle, const void* base, uint64_t size) {
    auto* scope = sModavdAsgExternalScope;
    if (!scope || scope->mConsumed || scope->mHandle != handle ||
        scope->mBase != base || scope->mSize != size) return false;
    scope->mConsumed = true;
    return scope->mOwned;
}

extern "C" MODAVD_DMA_EXPORT bool modavd_register_dma_borrow_v1(ModavdDmaBorrowV1 callback) {
    sModavdDmaBorrow.store(callback, std::memory_order_release);
    return callback != nullptr;
}

extern "C" MODAVD_DMA_EXPORT bool modavd_dma_sized_borrow_ready(void) {
    return sModavdDmaBorrow.load(std::memory_order_acquire) != nullptr;
}

extern "C" void* modavd_dma_borrow_sized(uint64_t gpa, uint64_t length) {
    if (!length || length > UINT64_MAX - gpa) return nullptr;
    auto callback = sModavdDmaBorrow.load(std::memory_order_acquire);
    return callback ? callback(gpa, length) : nullptr;
}

extern "C" bool modavd_dma_set_precise_pixel_transfer(bool active) {
    bool previous = sModavdPrecisePixelTransfer;
    sModavdPrecisePixelTransfer = active;
    return previous;
}

extern "C" bool modavd_dma_precise_pixel_transfer_active(void) {
    return sModavdPrecisePixelTransfer;
}

namespace gfxstream {
namespace host {
namespace {

static void* DefaultGfxstreamGetHostAddr(uint64_t) {
    return nullptr;
}

static void DefaultGfxstreamDmaUnlock(uint64_t) { }

}  // namespace

gfxstream_dma_get_host_addr_t g_gfxstream_dma_get_host_addr = DefaultGfxstreamGetHostAddr;
gfxstream_dma_unlock_t g_gfxstream_dma_unlock = DefaultGfxstreamDmaUnlock;

void set_gfxstream_dma_get_host_addr(gfxstream_dma_get_host_addr_t f) {
    g_gfxstream_dma_get_host_addr = f;
}

void set_gfxstream_dma_unlock(gfxstream_dma_unlock_t f) {
    g_gfxstream_dma_unlock = f;
}

}  // namespace host
}  // namespace gfxstream
