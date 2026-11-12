/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* Host tests for src/sp11_scoped_sweep.c, the real implementation (not a mock), compiled here for
 * the host rather than aarch64-elf. Ports every case from the Snapintosh repository's
 * tools/test-sp11-ebs-sweep-model.py one for one (the comment above each test names which Python
 * test it mirrors); tools/test-sp11-scoped-sweep.py additionally cross-checks this file's output
 * against that Python module directly when SNAPINTOSH_DIR names a checkout.
 *
 * Build and run: see tools/test-sp11-scoped-sweep.py, which drives this with the project's usual
 * sanitizer flags. Compiled standalone: no EFI header, no gnu-efi, no gate other than
 * SLBOUNCE_SCOPED_SWEEP itself (defined on the command line exactly as the real build defines it). */
#ifndef SLBOUNCE_SCOPED_SWEEP
#define SLBOUNCE_SCOPED_SWEEP
#endif
#include "../src/sp11_scoped_sweep.c"

#include <stdio.h>
#include <string.h>

static int failures;
#define CHECK(cond) do { \
        if (!(cond)) { failures++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } \
    } while (0)

#define PAGE SP11_SWEEP_PAGE_BYTES

static struct sp11_sweep_descriptor desc(uint32_t type, uint64_t attribute, uint64_t base, uint64_t pages)
{
    struct sp11_sweep_descriptor d;
    d.type = type;
    d.attribute = attribute;
    d.base = base;
    d.pages = pages;
    return d;
}

static uint64_t ops_total(const struct sp11_sweep_op *ops, uint32_t n)
{
    uint64_t total = 0;
    for (uint32_t i = 0; i < n; i++) total += ops[i].size;
    return total;
}

/* classic_sweep()'s own properties, which scoped_sweep's design is checked against: the seven
 * swept types, named here identically to bounce_main.c:124-130 and to SWEPT_TYPES in the Python
 * module. (ClassicSweep.test_seven_swept_types_exactly, test_every_swept_type_is_included_whole,
 * test_non_swept_types_are_excluded) */
static void test_seven_swept_types(void)
{
    static const uint32_t swept[] = {
        SP11_SWEEP_EFI_LOADER_CODE, SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_EFI_BOOT_SERVICES_CODE,
        SP11_SWEEP_EFI_BOOT_SERVICES_DATA, SP11_SWEEP_EFI_RUNTIME_SERVICES_CODE,
        SP11_SWEEP_EFI_RUNTIME_SERVICES_DATA, SP11_SWEEP_EFI_ACPI_RECLAIM_MEMORY,
    };
    unsigned count = 0;
    for (uint32_t t = 0; t < 14; t++) {
        int want = 0;
        for (unsigned i = 0; i < sizeof swept / sizeof swept[0]; i++) if (swept[i] == t) want = 1;
        CHECK(sp11_sweep_type_is_swept(t) == want);
        if (want) count++;
    }
    CHECK(count == 7);
}

/* Attribute is genuinely ignored by classic sweep semantics (the type-only switch
 * bounce_main.c:124-130 uses): sp11_sweep_type_is_swept must not take attribute as a parameter at
 * all, so there is nothing further to test here beyond the signature itself compiling.
 * (ClassicSweep.test_attribute_is_never_consulted, restated: this module's classic-equivalent
 * check has no attribute parameter to ignore) */

/* ScopedSweep.test_coverage_of_a_single_declared_range */
static void test_coverage_single_range(void)
{
    struct sp11_sweep_descriptor d[] = {desc(SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_ATTRIBUTE_WB,
                                             0x90000000, (64u << 20) / PAGE)};
    struct sp11_sweep_op ops[256];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 1, 0x90000000, 64u << 20, 1u << 30, ops, 256, &n);
    CHECK(rc == SP11_SWEEP_OK);
    CHECK(ops_total(ops, n) == (64u << 20));
    CHECK(n == 1 && ops[0].base == 0x90000000 && ops[0].size == (64u << 20));
}

/* ScopedSweep.test_a_sub_range_of_a_larger_descriptor_is_covered */
static void test_sub_range_covered(void)
{
    struct sp11_sweep_descriptor d[] = {desc(SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_ATTRIBUTE_WB,
                                             0x90000000, (64u << 20) / PAGE)};
    struct sp11_sweep_op ops[8];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 1, 0x90001000, 0x1000, 1u << 30, ops, 8, &n);
    CHECK(rc == SP11_SWEEP_OK);
    CHECK(n == 1 && ops[0].base == 0x90001000 && ops[0].size == 0x1000);
}

/* ScopedSweep.test_boundedness_never_exceeds_declared_total */
static void test_boundedness(void)
{
    struct sp11_sweep_descriptor d[] = {desc(SP11_SWEEP_EFI_BOOT_SERVICES_DATA, SP11_SWEEP_ATTRIBUTE_WB,
                                             0x1000, 4096)}; /* 16 MiB descriptor */
    struct sp11_sweep_op ops[8];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 1, 0x1000, 4096, 1u << 30, ops, 8, &n);
    CHECK(rc == SP11_SWEEP_OK);
    uint64_t total1 = ops_total(ops, n);
    rc = sp11_sweep_range(d, 1, 0x100000, 8192, 1u << 30, ops, 8, &n);
    CHECK(rc == SP11_SWEEP_OK);
    uint64_t total2 = ops_total(ops, n);
    CHECK(total1 + total2 == 4096u + 8192u);
    CHECK(total1 + total2 < sp11_sweep_descriptor_size(&d[0]));
}

/* ScopedSweep.test_firmware_owned_memory_never_declared_is_never_touched: the issue-13-shaped range
 * (0xA7F19000, 231 pages) is never swept because it was never declared, even though it sits right
 * next to a descriptor that was. */
static void test_undeclared_never_swept(void)
{
    struct sp11_sweep_descriptor d[] = {
        desc(SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_ATTRIBUTE_WB, 0x90000000, 16),
        desc(SP11_SWEEP_EFI_BOOT_SERVICES_DATA, SP11_SWEEP_ATTRIBUTE_WB, 0xA7F19000, 231),
    };
    struct sp11_sweep_op ops[8];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 2, 0x90000000, 16 * PAGE, 1u << 30, ops, 8, &n);
    CHECK(rc == SP11_SWEEP_OK);
    for (uint32_t i = 0; i < n; i++) CHECK(ops[i].base != 0xA7F19000);
}

/* ScopedSweep.test_attribute_filter_refuses_non_write_back: the section 4.3 fix. */
static void test_attribute_filter(void)
{
    struct sp11_sweep_descriptor d[] = {desc(SP11_SWEEP_EFI_BOOT_SERVICES_DATA, 0, 0x5000, 1)};
    struct sp11_sweep_op ops[8];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 1, 0x5000, 0x1000, 1u << 30, ops, 8, &n);
    CHECK(rc == SP11_SWEEP_ERR_NOT_WRITEBACK);
}

/* ScopedSweep.test_non_swept_type_is_refused_even_if_declared */
static void test_non_swept_type_refused(void)
{
    struct sp11_sweep_descriptor d[] = {desc(SP11_SWEEP_EFI_CONVENTIONAL_MEMORY, SP11_SWEEP_ATTRIBUTE_WB,
                                             0x6000, 1)};
    struct sp11_sweep_op ops[8];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 1, 0x6000, 0x1000, 1u << 30, ops, 8, &n);
    CHECK(rc == SP11_SWEEP_ERR_WRONG_TYPE);
}

/* ScopedSweep.test_a_range_split_across_a_descriptor_boundary_is_refused */
static void test_boundary_split_refused(void)
{
    struct sp11_sweep_descriptor d[] = {
        desc(SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_ATTRIBUTE_WB, 0x1000, 1),
        desc(SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_ATTRIBUTE_WB, 0x2000, 1),
    };
    struct sp11_sweep_op ops[8];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 2, 0x1800, 0x1000, 1u << 30, ops, 8, &n); /* straddles 0x2000 */
    CHECK(rc == SP11_SWEEP_ERR_NOT_CONTAINED);
}

/* ScopedSweep.test_an_undeclared_range_outside_any_descriptor_is_refused */
static void test_outside_any_descriptor_refused(void)
{
    struct sp11_sweep_op ops[8];
    uint32_t n = 0;
    int rc = sp11_sweep_range(NULL, 0, 0x7000, 0x1000, 1u << 30, ops, 8, &n);
    CHECK(rc == SP11_SWEEP_ERR_NOT_CONTAINED);
}

/* ScopedSweep.test_malformed_declared_range_is_refused: this module's size is unsigned, so only the
 * zero-size case applies (a negative base or size cannot be represented; the Python model's int
 * type can hold what this C struct's uint64_t base cannot, so that negative case is N/A here, not
 * silently dropped -- see tools/test-sp11-scoped-sweep.py's own note on this). */
static void test_malformed_range_refused(void)
{
    struct sp11_sweep_descriptor d[] = {desc(SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_ATTRIBUTE_WB, 0x1000, 1)};
    struct sp11_sweep_op ops[8];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 1, 0x1000, 0, 1u << 30, ops, 8, &n);
    CHECK(rc == SP11_SWEEP_ERR_BAD_RANGE);
}

/* ScopedSweep.test_chunk_cap_bounds_every_op */
static void test_chunk_cap_bounds(void)
{
    uint64_t pages = (8u << 20) / PAGE;
    struct sp11_sweep_descriptor d[] = {desc(SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_ATTRIBUTE_WB,
                                             0x90000000, pages)};
    struct sp11_sweep_op ops[64];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 1, 0x90000000, 8u << 20, 1u << 20, ops, 64, &n);
    CHECK(rc == SP11_SWEEP_OK);
    CHECK(n > 1);
    for (uint32_t i = 0; i < n; i++) CHECK(ops[i].size <= (1u << 20));
    CHECK(ops_total(ops, n) == (8u << 20));
}

/* ScopedSweep.test_chunk_cap_must_be_positive */
static void test_chunk_cap_positive(void)
{
    struct sp11_sweep_descriptor d[] = {desc(SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_ATTRIBUTE_WB, 0x1000, 1)};
    struct sp11_sweep_op ops[8];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 1, 0x1000, 0x1000, 0, ops, 8, &n);
    CHECK(rc == SP11_SWEEP_ERR_CHUNK_CAP);
}

/* This module's own addition, since its signature is bounded (the Python model's ops list is
 * unbounded): a range that would need more chunks than the caller's buffer holds is refused, not
 * silently truncated. */
static void test_too_many_ops_refused(void)
{
    struct sp11_sweep_descriptor d[] = {desc(SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_ATTRIBUTE_WB,
                                             0x90000000, (4u << 20) / PAGE)};
    struct sp11_sweep_op ops[2];
    uint32_t n = 0;
    int rc = sp11_sweep_range(d, 1, 0x90000000, 4u << 20, 1u << 20, ops, 2, &n);
    CHECK(rc == SP11_SWEEP_ERR_TOO_MANY_OPS); /* 4 chunks needed, only 2 slots given */
}

/* Equivalence.test_scoped_total_never_exceeds_classic_total /
 * test_declaring_every_wb_extent_matches_classic_minus_non_wb: declaring every write-back
 * swept-type byte reproduces exactly a write-back-only classic sweep's coverage, strictly less
 * than classic's own total once a non-WB descriptor is in the mix. */
static void test_equivalence_to_classic_wb_subset(void)
{
    struct sp11_sweep_descriptor mixed[] = {
        desc(SP11_SWEEP_EFI_LOADER_CODE, SP11_SWEEP_ATTRIBUTE_WB, 0x80000000, 4),
        desc(SP11_SWEEP_EFI_LOADER_DATA, SP11_SWEEP_ATTRIBUTE_WB, 0x90000000, (64u << 20) / PAGE),
        desc(SP11_SWEEP_EFI_BOOT_SERVICES_DATA, 0 /* non-WB */, 0xA7F19000, 231),
        desc(SP11_SWEEP_EFI_RUNTIME_SERVICES_DATA, SP11_SWEEP_ATTRIBUTE_WB, 0xA8000000, 16),
        desc(SP11_SWEEP_EFI_CONVENTIONAL_MEMORY, SP11_SWEEP_ATTRIBUTE_WB, 0xB0000000, 256),
    };
    uint64_t classic_total = 0;
    for (unsigned i = 0; i < sizeof mixed / sizeof mixed[0]; i++)
        if (sp11_sweep_type_is_swept(mixed[i].type)) classic_total += sp11_sweep_descriptor_size(&mixed[i]);

    /* Declare every WB swept-type extent. */
    struct sp11_sweep_op ops[256];
    uint32_t n_total = 0;
    uint64_t scoped_total = 0;
    for (unsigned i = 0; i < sizeof mixed / sizeof mixed[0]; i++) {
        if (!sp11_sweep_type_is_swept(mixed[i].type) || !(mixed[i].attribute & SP11_SWEEP_ATTRIBUTE_WB))
            continue;
        uint32_t n = 0;
        int rc = sp11_sweep_range(mixed, sizeof mixed / sizeof mixed[0], mixed[i].base,
                                  sp11_sweep_descriptor_size(&mixed[i]), 1u << 30, ops, 256, &n);
        CHECK(rc == SP11_SWEEP_OK);
        scoped_total += ops_total(ops, n);
        n_total += n;
    }
    (void)n_total;
    CHECK(scoped_total < classic_total); /* the non-WB 231-page range and the RT data range differ */
    uint64_t wb_swept_total = sp11_sweep_descriptor_size(&mixed[0]) + sp11_sweep_descriptor_size(&mixed[1]) +
                              sp11_sweep_descriptor_size(&mixed[3]);
    CHECK(scoped_total == wb_swept_total);
}

/* ScopedSweep.test_empty_declared_ranges_is_the_empty_sweep: this module validates one range per
 * call rather than a list, so the equivalent statement is that calling it zero times sweeps
 * nothing -- trivially true and not worth its own CHECK; recorded here for parity with the Python
 * suite's enumeration. */

int main(void)
{
    test_seven_swept_types();
    test_coverage_single_range();
    test_sub_range_covered();
    test_boundedness();
    test_undeclared_never_swept();
    test_attribute_filter();
    test_non_swept_type_refused();
    test_boundary_split_refused();
    test_outside_any_descriptor_refused();
    test_malformed_range_refused();
    test_chunk_cap_bounds();
    test_chunk_cap_positive();
    test_too_many_ops_refused();
    test_equivalence_to_classic_wb_subset();

    if (failures) {
        printf("FAIL: %d check(s) failed\n", failures);
        return 1;
    }
    printf("PASS\n");
    return 0;
}
