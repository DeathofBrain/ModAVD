/* RGBA8-only proof. Every other format retains the legacy whole-block export. */
#ifndef MODAVD_DMA_PIXEL_EXTENT_H
#define MODAVD_DMA_PIXEL_EXTENT_H
#include <cstdint>
#include <memory>
#include "host/color_buffer.h"

enum class ModavdDmaPixelPath { Legacy, Sized, Reject };
static inline ModavdDmaPixelPath modavd_dma_pixel_path(
        const std::shared_ptr<gfxstream::host::ColorBuffer>& cb,
        int32_t x, int32_t y, int32_t width, int32_t height,
        uint32_t format, uint32_t type, uint32_t declared, uint64_t* actual) {
    (void)type;
    // The existing GetGfxstreamFormat conversion treats these four formats as
    // RGBA8, independently of the original protocol type. Both the request and
    // actual target must be RGBA8. Vulkan then uses one unpadded 4-byte plane;
    // GL uses the controlled client-memory pixel-store scope in its real context.
    const bool rgba8 = format == 0x1908 || format == 0x8058 ||
                       format == 0x8057 || format == 0x8056;
    if (!cb || !rgba8 || cb->getFormat() != gfxstream::host::GfxstreamFormat::R8G8B8A8_UNORM) {
        return ModavdDmaPixelPath::Legacy;
    }
    if (x < 0 || y < 0 || width <= 0 || height <= 0 || !declared ||
        static_cast<uint32_t>(x) > cb->getWidth() ||
        static_cast<uint32_t>(width) > cb->getWidth() - static_cast<uint32_t>(x) ||
        static_cast<uint32_t>(y) > cb->getHeight() ||
        static_cast<uint32_t>(height) > cb->getHeight() - static_cast<uint32_t>(y)) {
        return ModavdDmaPixelPath::Reject;
    }
    uint64_t bytes = static_cast<uint32_t>(width);
    if (bytes > UINT64_MAX / static_cast<uint32_t>(height)) return ModavdDmaPixelPath::Reject;
    bytes *= static_cast<uint32_t>(height);
    if (bytes > UINT64_MAX / 4) return ModavdDmaPixelPath::Reject;
    bytes *= 4;
    if (bytes > declared) return ModavdDmaPixelPath::Reject;
    *actual = bytes;
    return ModavdDmaPixelPath::Sized;
}
#endif
