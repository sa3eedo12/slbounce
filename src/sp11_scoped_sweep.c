/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* See sp11_scoped_sweep.h. Ported from tools/sp11_ebs_sweep_model.py's scoped_sweep() (Snapintosh
 * q1n1 tree, docs/SP11-Q1N1-BOOT-STUDY.md section 4.4/4.6); tools/test-sp11-scoped-sweep.py checks
 * this file against that Python oracle directly when SNAPINTOSH_DIR names a checkout. */
#ifdef SLBOUNCE_SCOPED_SWEEP
#include "sp11_scoped_sweep.h"

int sp11_sweep_type_is_swept(uint32_t type)
{
    switch (type) {
    case SP11_SWEEP_EFI_LOADER_CODE:
    case SP11_SWEEP_EFI_LOADER_DATA:
    case SP11_SWEEP_EFI_BOOT_SERVICES_CODE:
    case SP11_SWEEP_EFI_BOOT_SERVICES_DATA:
    case SP11_SWEEP_EFI_RUNTIME_SERVICES_CODE:
    case SP11_SWEEP_EFI_RUNTIME_SERVICES_DATA:
    case SP11_SWEEP_EFI_ACPI_RECLAIM_MEMORY:
        return 1;
    default:
        return 0;
    }
}

const char *sp11_sweep_status_name(int status)
{
    switch (status) {
    case SP11_SWEEP_OK: return "OK";
    case SP11_SWEEP_ERR_BAD_RANGE: return "BAD_RANGE";
    case SP11_SWEEP_ERR_NOT_CONTAINED: return "NOT_CONTAINED";
    case SP11_SWEEP_ERR_WRONG_TYPE: return "WRONG_TYPE";
    case SP11_SWEEP_ERR_NOT_WRITEBACK: return "NOT_WRITEBACK";
    case SP11_SWEEP_ERR_CHUNK_CAP: return "CHUNK_CAP";
    case SP11_SWEEP_ERR_TOO_MANY_OPS: return "TOO_MANY_OPS";
    default: return "UNKNOWN";
    }
}

int sp11_sweep_range(const struct sp11_sweep_descriptor *descriptors, uint32_t count, uint64_t base,
                     uint64_t size, uint32_t chunk_cap, struct sp11_sweep_op *ops, uint32_t max_ops,
                     uint32_t *n_ops)
{
    if (chunk_cap == 0) return SP11_SWEEP_ERR_CHUNK_CAP;
    if (size == 0) return SP11_SWEEP_ERR_BAD_RANGE;

    /* Containment: exactly one descriptor must cover the whole declared range. Checked before type
     * or attribute, matching the Python model's own ordering, so a range that straddles a
     * descriptor boundary is reported as NOT_CONTAINED even if every descriptor it touches would
     * individually have passed the later checks. */
    uint32_t covering = count; /* count is never itself a valid index: "not yet found" sentinel */
    uint32_t matches = 0;
    for (uint32_t i = 0; i < count; i++) {
        if (sp11_sweep_descriptor_contains(&descriptors[i], base, size)) {
            if (matches == 0) covering = i;
            matches++;
        }
    }
    if (matches != 1) return SP11_SWEEP_ERR_NOT_CONTAINED;

    const struct sp11_sweep_descriptor *d = &descriptors[covering];
    if (!sp11_sweep_type_is_swept(d->type)) return SP11_SWEEP_ERR_WRONG_TYPE;
    if (!(d->attribute & SP11_SWEEP_ATTRIBUTE_WB)) return SP11_SWEEP_ERR_NOT_WRITEBACK;

    uint32_t n = 0;
    uint64_t at = base, remaining = size;
    while (remaining > 0) {
        if (n >= max_ops) return SP11_SWEEP_ERR_TOO_MANY_OPS;
        uint64_t take = remaining < chunk_cap ? remaining : chunk_cap;
        ops[n].base = at;
        ops[n].size = take;
        n++;
        at += take;
        remaining -= take;
    }
    *n_ops = n;
    return SP11_SWEEP_OK;
}

#endif /* SLBOUNCE_SCOPED_SWEEP */
