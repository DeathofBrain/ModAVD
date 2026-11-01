/* Q0..Q31 remain live between setup and exit. The check loop uses only GPRs
 * plus SIMD load/store instructions; there are no calls or SIMD computations. */
.text
.align 2
.global simd_register_worker
.type simd_register_worker, %function
simd_register_worker:
    sub sp, sp, #128
    stp q8, q9, [sp, #0]
    stp q10, q11, [sp, #32]
    stp q12, q13, [sp, #64]
    stp q14, q15, [sp, #96]
    mov x9, x0
    ldp q0, q1, [x9, #0]
    ldp q2, q3, [x9, #32]
    ldp q4, q5, [x9, #64]
    ldp q6, q7, [x9, #96]
    ldp q8, q9, [x9, #128]
    ldp q10, q11, [x9, #160]
    ldp q12, q13, [x9, #192]
    ldp q14, q15, [x9, #224]
    ldp q16, q17, [x9, #256]
    ldp q18, q19, [x9, #288]
    ldp q20, q21, [x9, #320]
    ldp q22, q23, [x9, #352]
    ldp q24, q25, [x9, #384]
    ldp q26, q27, [x9, #416]
    ldp q28, q29, [x9, #448]
    ldp q30, q31, [x9, #480]
    ldr x0, [x9, #1096]
    cbz x0, .Lready
    ldr q30, [x9, #1024]
.Lready:
    mov x15, #0
    add x10, x9, #1048
    mov x0, #1
    stlr x0, [x10]
.Lcheck:
    add x10, x9, #1040
    ldar x0, [x10]
    cbnz x0, .Lexit
    add x11, x9, #512
    stp q0, q1, [x11, #0]
    stp q2, q3, [x11, #32]
    stp q4, q5, [x11, #64]
    stp q6, q7, [x11, #96]
    stp q8, q9, [x11, #128]
    stp q10, q11, [x11, #160]
    stp q12, q13, [x11, #192]
    stp q14, q15, [x11, #224]
    stp q16, q17, [x11, #256]
    stp q18, q19, [x11, #288]
    stp q20, q21, [x11, #320]
    stp q22, q23, [x11, #352]
    stp q24, q25, [x11, #384]
    stp q26, q27, [x11, #416]
    stp q28, q29, [x11, #448]
    stp q30, q31, [x11, #480]
    mov x10, x9
    mov x12, #64
.Lcompare:
    ldr x13, [x10], #8
    ldr x14, [x11], #8
    cmp x13, x14
    b.ne .Lmismatch
    subs x12, x12, #1
    b.ne .Lcompare
    add x15, x15, #1
    add x10, x9, #1056
    stlr x15, [x10]
    b .Lcheck
.Lmismatch:
    mov x0, #64
    sub x0, x0, x12
    str x0, [x9, #1072]
    str x13, [x9, #1080]
    str x14, [x9, #1088]
    add x10, x9, #1064
    mov x0, #1
    stlr x0, [x10]
.Lexit:
    ldp q8, q9, [sp, #0]
    ldp q10, q11, [sp, #32]
    ldp q12, q13, [sp, #64]
    ldp q14, q15, [sp, #96]
    add sp, sp, #128
    mov x0, #0
    ret
.size simd_register_worker, .-simd_register_worker
.section .note.GNU-stack,"",%progbits
