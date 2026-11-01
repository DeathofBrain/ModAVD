// ABI-compatible AArch64 wrappers for the exact API34/r14 ARM64 guest encoder.
// The original Impl validation code returns -2 for invalid handles. Its patched
// success exits return zero, without calling the encoder. Release mLock before
// the host round trip, preserving bindlock2's deadlock-avoidance rule.
.text
.global wrapper_bind
.global wrapper_bind2
.type wrapper_bind, %function
wrapper_bind:
    stp x19, x20, [sp, #-64]!
    stp x21, x22, [sp, #16]
    stp x23, x24, [sp, #32]
    stp x25, x30, [sp, #48]
    ldr x19, [x0, #8]
    mov x20, x1
    mov x21, x3
    mov x22, x4
    mov x23, x5
    mov x24, x6
    add x0, x19, #0x660
    bl mutex_lock
    mov x0, x19
    mov x1, x20
    mov x3, x21
    mov x4, x22
    mov x5, x23
    mov x6, x24
    bl impl_bind
    mov w25, w0
    add x0, x19, #0x660
    bl mutex_unlock
    mov w0, w25
    cbnz w0, 1f
    mov x0, x20
    mov x1, x21
    mov x2, x22
    mov x3, x23
    mov x4, x24
    mov w5, #1
    bl encoder_bind
1:
    ldp x25, x30, [sp, #48]
    ldp x23, x24, [sp, #32]
    ldp x21, x22, [sp, #16]
    ldp x19, x20, [sp], #64
    ret
.size wrapper_bind, .-wrapper_bind

.type wrapper_bind2, %function
wrapper_bind2:
    stp x19, x20, [sp, #-64]!
    stp x21, x22, [sp, #16]
    stp x23, x24, [sp, #32]
    stp x25, x30, [sp, #48]
    ldr x19, [x0, #8]
    mov x20, x1
    mov x21, x3
    mov w22, w4
    mov x23, x5
    add x0, x19, #0x660
    bl mutex_lock
    mov x0, x19
    mov x1, x20
    mov x3, x21
    mov w4, w22
    mov x5, x23
    bl impl_bind2
    mov w25, w0
    add x0, x19, #0x660
    bl mutex_unlock
    mov w0, w25
    cbnz w0, 2f
    mov x0, x20
    mov x1, x21
    mov w2, w22
    mov x3, x23
    mov w4, #1
    bl encoder_bind2
2:
    ldp x25, x30, [sp, #48]
    ldp x23, x24, [sp, #32]
    ldp x21, x22, [sp, #16]
    ldp x19, x20, [sp], #64
    ret
.size wrapper_bind2, .-wrapper_bind2
