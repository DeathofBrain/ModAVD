#include <Hypervisor/Hypervisor.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

/* Generated verbatim from the production QEMU function by the Python runner. */
#include "hvf-simd-map.inc"

static const hv_simd_fp_reg_t canonical[32] = {
    HV_SIMD_FP_REG_Q0, HV_SIMD_FP_REG_Q1, HV_SIMD_FP_REG_Q2, HV_SIMD_FP_REG_Q3,
    HV_SIMD_FP_REG_Q4, HV_SIMD_FP_REG_Q5, HV_SIMD_FP_REG_Q6, HV_SIMD_FP_REG_Q7,
    HV_SIMD_FP_REG_Q8, HV_SIMD_FP_REG_Q9, HV_SIMD_FP_REG_Q10, HV_SIMD_FP_REG_Q11,
    HV_SIMD_FP_REG_Q12, HV_SIMD_FP_REG_Q13, HV_SIMD_FP_REG_Q14, HV_SIMD_FP_REG_Q15,
    HV_SIMD_FP_REG_Q16, HV_SIMD_FP_REG_Q17, HV_SIMD_FP_REG_Q18, HV_SIMD_FP_REG_Q19,
    HV_SIMD_FP_REG_Q20, HV_SIMD_FP_REG_Q21, HV_SIMD_FP_REG_Q22, HV_SIMD_FP_REG_Q23,
    HV_SIMD_FP_REG_Q24, HV_SIMD_FP_REG_Q25, HV_SIMD_FP_REG_Q26, HV_SIMD_FP_REG_Q27,
    HV_SIMD_FP_REG_Q28, HV_SIMD_FP_REG_Q29, HV_SIMD_FP_REG_Q30, HV_SIMD_FP_REG_Q31,
};

static void checked(hv_return_t result) {
    if (result != HV_SUCCESS) {
        fprintf(stderr, "Hypervisor API failed: 0x%x\n", result);
        exit(2);
    }
}

static int check_mapping(void) {
    for (int i = 0; i < 32; ++i) {
        const hv_simd_fp_reg_t actual = regno_to_hv_simd_fp_reg_type(i);
        if (actual != canonical[i]) {
            printf("{\"ok\":false,\"phase\":\"mapping\",\"register\":%d,"
                   "\"actual\":%d,\"expected\":%d}\n", i, actual, canonical[i]);
            return 1;
        }
        for (int j = 0; j < i; ++j) {
            if (actual == regno_to_hv_simd_fp_reg_type(j)) {
                fprintf(stderr, "SIMD register mapping aliases %d and %d\n", j, i);
                return 1;
            }
        }
    }
    return 0;
}

static hv_simd_fp_uchar16_t pattern(unsigned reg, unsigned round, unsigned stage) {
    const uint64_t words[2] = {
        UINT64_C(0x13579bdf2468ace0) ^ ((uint64_t)reg << 32) ^
            ((uint64_t)round << 8) ^ stage,
        UINT64_C(0xfedcba9876543210) ^ ((uint64_t)(31 - reg) << 40) ^
            ((uint64_t)stage << 16) ^ round,
    };
    hv_simd_fp_uchar16_t result;
    memcpy(&result, words, sizeof(result));
    return result;
}

static void expect_value(hv_simd_fp_uchar16_t actual,
                         hv_simd_fp_uchar16_t expected,
                         const char *phase, unsigned reg, unsigned round) {
    if (memcmp(&actual, &expected, sizeof(actual)) != 0) {
        fprintf(stderr, "%s SIMD Q%u differs at round %u\n", phase, reg, round);
        exit(3);
    }
}

static void run_guest(hv_vcpu_t cpu, hv_vcpu_exit_t *state, uint64_t pc) {
    checked(hv_vcpu_set_reg(cpu, HV_REG_PC, pc));
    checked(hv_vcpu_set_reg(cpu, HV_REG_X0, UINT64_C(0x20000000)));
    checked(hv_vcpu_set_reg(cpu, HV_REG_CPSR, 0x3c5));
    checked(hv_vcpu_run(cpu));
    if (state->reason != HV_EXIT_REASON_EXCEPTION ||
        (state->exception.syndrome >> 26) != 0x16) {
        fprintf(stderr, "Unexpected guest exit: reason=%d syndrome=0x%llx\n",
                state->reason, (unsigned long long)state->exception.syndrome);
        exit(4);
    }
}

int main(int argc, char **argv) {
    if (check_mapping()) {
        return 1;
    }
    if (argc == 2 && strcmp(argv[1], "--map-only") == 0) {
        puts("{\"ok\":true,\"mapping_unique_registers\":32}");
        return 0;
    }
    if (argc != 1) {
        return 2;
    }

    const size_t page_size = (size_t)getpagesize();
    uint32_t *code = mmap(NULL, page_size, PROT_READ | PROT_WRITE,
                          MAP_ANON | MAP_PRIVATE, -1, 0);
    hv_simd_fp_uchar16_t *data = mmap(NULL, page_size, PROT_READ | PROT_WRITE,
                                     MAP_ANON | MAP_PRIVATE, -1, 0);
    if (code == MAP_FAILED || data == MAP_FAILED) {
        perror("mmap");
        return 2;
    }
    /* STR/LDR Qn, [X0, #16*n] for every register, followed by HVC #0.
     * Both entry points are written before mapping and before any guest run. */
    for (unsigned i = 0; i < 32; ++i) {
        code[i] = UINT32_C(0x3d800000) | (i << 10) | i;
        code[64 + i] = UINT32_C(0x3dc00000) | (i << 10) | i;
    }
    code[32] = code[96] = UINT32_C(0xd4000002);

    checked(hv_vm_create(NULL));
    checked(hv_vm_map(code, UINT64_C(0x10000000), page_size,
                      HV_MEMORY_READ | HV_MEMORY_EXEC));
    checked(hv_vm_map(data, UINT64_C(0x20000000), page_size,
                      HV_MEMORY_READ | HV_MEMORY_WRITE));
    hv_vcpu_t cpu;
    hv_vcpu_exit_t *state;
    checked(hv_vcpu_create(&cpu, &state, NULL));
    checked(hv_vcpu_set_sys_reg(cpu, HV_SYS_REG_SCTLR_EL1, UINT64_C(0x30d00800)));
    checked(hv_vcpu_set_sys_reg(cpu, HV_SYS_REG_CPACR_EL1, UINT64_C(3) << 20));

    const unsigned rounds = 16;
    for (unsigned round = 0; round < rounds; ++round) {
        for (unsigned i = 0; i < 32; ++i) {
            checked(hv_vcpu_set_simd_fp_reg(cpu, regno_to_hv_simd_fp_reg_type(i),
                                           pattern(i, round, 1)));
        }
        /* Read canonical hardware names, so a symmetric set/get mapping bug
         * cannot hide behind a round trip that uses the same wrong name. */
        for (unsigned i = 0; i < 32; ++i) {
            hv_simd_fp_uchar16_t actual;
            checked(hv_vcpu_get_simd_fp_reg(cpu, canonical[i], &actual));
            expect_value(actual, pattern(i, round, 1), "canonical get", i, round);
        }
        memset(data, 0, 32 * sizeof(*data));
        run_guest(cpu, state, UINT64_C(0x10000000));
        for (unsigned i = 0; i < 32; ++i) {
            expect_value(data[i], pattern(i, round, 1), "guest STR", i, round);
            data[i] = pattern(i, round, 2);
        }
        run_guest(cpu, state, UINT64_C(0x10000100));
        for (unsigned i = 0; i < 32; ++i) {
            hv_simd_fp_uchar16_t actual;
            checked(hv_vcpu_get_simd_fp_reg(cpu, regno_to_hv_simd_fp_reg_type(i),
                                           &actual));
            expect_value(actual, pattern(i, round, 2), "guest LDR/mapped get", i, round);
        }
        /* Test get independently from put with canonical writes as well. */
        for (unsigned i = 0; i < 32; ++i) {
            checked(hv_vcpu_set_simd_fp_reg(cpu, canonical[i], pattern(i, round, 3)));
        }
        for (unsigned i = 0; i < 32; ++i) {
            hv_simd_fp_uchar16_t actual;
            checked(hv_vcpu_get_simd_fp_reg(cpu, regno_to_hv_simd_fp_reg_type(i),
                                           &actual));
            expect_value(actual, pattern(i, round, 3), "canonical put/mapped get", i, round);
        }
    }

    checked(hv_vcpu_destroy(cpu));
    checked(hv_vm_unmap(UINT64_C(0x10000000), page_size));
    checked(hv_vm_unmap(UINT64_C(0x20000000), page_size));
    checked(hv_vm_destroy());
    if (munmap(code, page_size) || munmap(data, page_size)) {
        perror("munmap");
        return 2;
    }
    printf("{\"ok\":true,\"mapping_unique_registers\":32,\"rounds\":%u,"
           "\"canonical_api_put_get\":true,\"guest_store_all_simd\":true,"
           "\"guest_load_all_simd\":true,\"mapped_get_independent_put\":true,"
           "\"guest_runs\":%u,\"private_mapped_bytes\":%zu}\n",
           rounds, rounds * 2, page_size * 2);
    return 0;
}
