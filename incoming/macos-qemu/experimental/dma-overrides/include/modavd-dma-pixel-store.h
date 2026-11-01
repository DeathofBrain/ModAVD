/* Applied only to the private, exact RGBA8 RC call on its real GL context. */
#ifndef MODAVD_DMA_PIXEL_STORE_H
#define MODAVD_DMA_PIXEL_STORE_H
#include "modavd-dma-sized-borrow.h"
#include <array>
#include <cstdint>

template<class Dispatch> class ModavdDmaPixelStoreScope {
public:
    ModavdDmaPixelStoreScope(Dispatch& gl, bool pack) : mGl(gl) {
        if (!modavd_dma_precise_pixel_transfer_active()) return;
        mActive = true;
        // PACK/UNPACK_ALIGNMENT, ROW_LENGTH, SKIP_ROWS, SKIP_PIXELS. IMAGE_HEIGHT
        // and SKIP_IMAGES do not affect these two-dimensional transfers.
        mNames = pack ? std::array<uint32_t,4>{0x0d05,0x0d02,0x0d03,0x0d04} :
                        std::array<uint32_t,4>{0x0cf5,0x0cf2,0x0cf3,0x0cf4};
        mTarget = pack ? 0x88eb : 0x88ec; // PIXEL_PACK/UNPACK_BUFFER
        mBindingName = pack ? 0x88ed : 0x88ef;
        mGl.glGetIntegerv(mBindingName, &mBinding);
        for (unsigned i=0;i<mNames.size();++i) mGl.glGetIntegerv(mNames[i], &mSaved[i]);
        mGl.glBindBuffer(mTarget, 0);
        for (unsigned i=0;i<mNames.size();++i) mGl.glPixelStorei(mNames[i], i ? 0 : 1);
        // Failed/unsupported state changes cannot authorize a client access.
        int value = -1;
        mGl.glGetIntegerv(mBindingName, &value);
        mOk = value == 0;
        for (unsigned i=0;i<mNames.size();++i) {
            value = -1;
            mGl.glGetIntegerv(mNames[i], &value);
            mOk = mOk && value == (i ? 0 : 1);
        }
    }
    ~ModavdDmaPixelStoreScope() {
        if (!mActive) return;
        for (unsigned i=0;i<mNames.size();++i) mGl.glPixelStorei(mNames[i], mSaved[i]);
        mGl.glBindBuffer(mTarget, mBinding);
    }
    bool isOk() const { return mOk; }
    ModavdDmaPixelStoreScope(const ModavdDmaPixelStoreScope&) = delete;
    ModavdDmaPixelStoreScope& operator=(const ModavdDmaPixelStoreScope&) = delete;
private:
    Dispatch& mGl;
    std::array<uint32_t,4> mNames{};
    std::array<int,4> mSaved{-1,-1,-1,-1};
    uint32_t mTarget=0, mBindingName=0;
    int mBinding = -1;
    bool mActive = false, mOk = true;
};
#endif
