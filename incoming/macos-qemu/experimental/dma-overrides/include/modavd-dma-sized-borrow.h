/* Private additive API. Existing renderer structs and vtables are unchanged. */
#ifndef MODAVD_DMA_SIZED_BORROW_H
#define MODAVD_DMA_SIZED_BORROW_H
#include <stdbool.h>
#include <stdint.h>

#if defined(__GNUC__)
#define MODAVD_DMA_EXPORT __attribute__((visibility("default")))
#else
#define MODAVD_DMA_EXPORT
#endif
#ifdef __cplusplus
extern "C" {
#endif
typedef void* (*ModavdDmaBorrowV1)(uint64_t gpa, uint64_t length);
MODAVD_DMA_EXPORT bool modavd_register_dma_borrow_v1(ModavdDmaBorrowV1 callback);
MODAVD_DMA_EXPORT bool modavd_dma_sized_borrow_ready(void);
/* NULL on absent callback, empty/overflowing range or rejected registration. */
void* modavd_dma_borrow_sized(uint64_t gpa, uint64_t length);
typedef void* (*ModavdDmaLegacyBorrowV1)(void* opaque, uint64_t gpa);
/* Private direct RC implementation; no existing callback/vtable ABI changes. */
int modavd_rc_color_buffer_dma_v1(int is_read, uint32_t color_buffer,
    int32_t x, int32_t y, int32_t width, int32_t height,
    uint32_t format, uint32_t type, uint64_t gpa, uint32_t declared,
    void* opaque, ModavdDmaLegacyBorrowV1 legacy);
bool modavd_dma_set_precise_pixel_transfer(bool active);
bool modavd_dma_precise_pixel_transfer_active(void);
#ifdef __cplusplus
}
#endif
#endif
