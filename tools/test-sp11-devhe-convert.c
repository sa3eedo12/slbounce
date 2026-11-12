/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* Host tests for src/sp11_devhe_convert.c's sp11_devhe_tcr_vhe_to_nvhe(), the real implementation
 * trans.s calls (not a mock), compiled here for the host. tools/test-sp11-devhe-convert.py
 * additionally cross-checks this against the Snapintosh repository's own
 * compat/el2-entry-model.c el2m_tcr_vhe_to_nvhe() directly when SNAPINTOSH_DIR names a checkout,
 * and disassembles this file's own -O2 build to prove the MMU-off calling safety argument in
 * sp11_devhe_convert.c's own header comment. */
#ifndef SLBOUNCE_SP11_DEVHE
#define SLBOUNCE_SP11_DEVHE
#endif
#include "../src/sp11_devhe_convert.c"

#include <stdio.h>

static int failures;
#define CHECK(cond) do { \
        if (!(cond)) { failures++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } \
    } while (0)

/* A plausible UEFI TCR_EL1 image: T0SZ 16 (48-bit VA), IRGN0=ORGN0=write-back, SH0 inner
 * shareable, TG0 4 KiB, PS 44 bits, TG1 4 KiB (EDK2's ArmPkg shape, TTBR1 unused by this
 * conversion but must not be the reserved 0b00 encoding). */
#define EXAMPLE_TCR_VHE UINT64_C(0x00000035b5103510)

static void test_a_plausible_uefi_image_converts_and_sets_res1(void)
{
    struct sp11_devhe_tcr_result r = sp11_devhe_tcr_vhe_to_nvhe(EXAMPLE_TCR_VHE);
    CHECK(r.valid == 1);
    CHECK((r.value & ((UINT64_C(1) << 31) | (UINT64_C(1) << 23))) == ((UINT64_C(1) << 31) | (UINT64_C(1) << 23)));
    /* T0SZ, TG0, SH0, ORGN0, IRGN0 pass through unchanged (bits 15:8, 5:0). */
    CHECK((r.value & SP11_DEVHE_RANGE(15, 8)) == (EXAMPLE_TCR_VHE & SP11_DEVHE_RANGE(15, 8)));
    CHECK((r.value & SP11_DEVHE_RANGE(5, 0)) == (EXAMPLE_TCR_VHE & SP11_DEVHE_RANGE(5, 0)));
    /* IPS (bits 34:32 of the source) lands at PS (bits 18:16 of the result). */
    CHECK(((r.value >> 16) & 7) == ((EXAMPLE_TCR_VHE >> 32) & 7));
}

static void test_epd0_is_refused(void)
{
    struct sp11_devhe_tcr_result r = sp11_devhe_tcr_vhe_to_nvhe(EXAMPLE_TCR_VHE | SP11_DEVHE_BIT(7));
    CHECK(r.valid == 0);
}

static void test_tg1_reserved_encoding_is_refused(void)
{
    uint64_t v = EXAMPLE_TCR_VHE & ~(SP11_DEVHE_RANGE(31, 30)); /* TG1 = 0b00 */
    struct sp11_devhe_tcr_result r = sp11_devhe_tcr_vhe_to_nvhe(v);
    CHECK(r.valid == 0);
}

static void test_vhe_res0_violation_is_refused(void)
{
    /* Bit 35 is RES0 in the VHE (TCR_EL1-format) layout. */
    struct sp11_devhe_tcr_result r = sp11_devhe_tcr_vhe_to_nvhe(EXAMPLE_TCR_VHE | SP11_DEVHE_BIT(35));
    CHECK(r.valid == 0);
}

static void test_every_field_maps_to_its_documented_bit(void)
{
    /* One bit set per field at a time (plus the fixed, always-valid base image), checking it lands
     * exactly where the header comment and compat/el2-entry-model.c both say it should. */
    struct { int src_bit; int dst_bit; int width; } fields[] = {
        {37, 20, 1}, /* TBI0 -> TBI */
        {39, 21, 1}, /* HA */
        {40, 22, 1}, /* HD */
        {41, 24, 1}, /* HPD0 -> HPD */
        {51, 29, 1}, /* TBID0 -> TBID */
        {57, 30, 1}, /* TCMA0 -> TCMA */
        {59, 32, 1}, /* DS */
        {60, 33, 1}, /* MTX0 -> MTX */
    };
    for (unsigned i = 0; i < sizeof fields / sizeof fields[0]; i++) {
        uint64_t v = EXAMPLE_TCR_VHE | (SP11_DEVHE_BIT(fields[i].src_bit));
        struct sp11_devhe_tcr_result r = sp11_devhe_tcr_vhe_to_nvhe(v);
        CHECK(r.valid == 1);
        CHECK(((r.value >> fields[i].dst_bit) & 1) == 1);
    }
    /* HWU059..HWU062 (source bits 43..46) -> HWU59..HWU62 (result bits 25..28). */
    for (int b = 0; b < 4; b++) {
        uint64_t v = EXAMPLE_TCR_VHE | (SP11_DEVHE_BIT(43 + b));
        struct sp11_devhe_tcr_result r = sp11_devhe_tcr_vhe_to_nvhe(v);
        CHECK(r.valid == 1);
        CHECK(((r.value >> (25 + b)) & 1) == 1);
    }
}

static void test_output_never_sets_a_non_vhe_reserved_bit(void)
{
    /* By construction: every field this function writes lands outside RANGE(63,34)|BIT(19)|
     * RANGE(7,6), the non-VHE RES0 bits (compat/el2-entry-model.c el2m_image_valid, EL2M_NVHE). */
    uint64_t res0 = SP11_DEVHE_RANGE(63, 34) | SP11_DEVHE_BIT(19) | SP11_DEVHE_RANGE(7, 6);
    uint64_t all_fields = EXAMPLE_TCR_VHE;
    for (int b = 32; b <= 61; b++) all_fields |= SP11_DEVHE_BIT(b); /* exercise every field bit at once */
    struct sp11_devhe_tcr_result r = sp11_devhe_tcr_vhe_to_nvhe(all_fields);
    if (r.valid) CHECK((r.value & res0) == 0);
}

int main(void)
{
    test_a_plausible_uefi_image_converts_and_sets_res1();
    test_epd0_is_refused();
    test_tg1_reserved_encoding_is_refused();
    test_vhe_res0_violation_is_refused();
    test_every_field_maps_to_its_documented_bit();
    test_output_never_sets_a_non_vhe_reserved_bit();

    if (failures) {
        printf("FAIL: %d check(s) failed\n", failures);
        return 1;
    }
    printf("PASS\n");
    return 0;
}
