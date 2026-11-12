/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* The scoped, attribute-filtered, chunked ExitBootServices dcache sweep (SLBOUNCE_SCOPED_SWEEP).
 * docs/SP11-Q1N1-BOOT-STUDY.md section 4.4 (Snapintosh q1n1 tree) is the design this ports; its
 * section 4.6 host model, tools/sp11_ebs_sweep_model.py, is the oracle this C code is checked
 * against (tools/test-sp11-scoped-sweep.py here) and must not diverge from without saying so.
 *
 * Pure functions: no EFI type, no allocation, no I/O, so a host test can drive them directly. The
 * caller (bounce_main.c, under SLBOUNCE_SCOPED_SWEEP) owns translating the real
 * EFI_MEMORY_DESCRIPTOR array (whatever its DescriptorSize stride is) into the
 * sp11_sweep_descriptor shape below, and owns calling clear_dcache_range() for every emitted
 * operation -- this file only decides whether a declared range may be swept at all, and how to
 * split it into bounded chunks. */
#ifndef SP11_SCOPED_SWEEP_H
#define SP11_SCOPED_SWEEP_H

#ifdef SLBOUNCE_SCOPED_SWEEP

#include <stdint.h>

#define SP11_SWEEP_PAGE_BYTES 4096u

/* bounce_main.c:117-134's own EFI_MEMORY_DESCRIPTOR.Type values, named identically. Only the
 * numeric values matter here (this file does not include efi.h), but the names make the switch in
 * sp11_sweep_type_is_swept readable against the upstream source it mirrors. */
enum {
    SP11_SWEEP_EFI_RESERVED_MEMORY_TYPE = 0,
    SP11_SWEEP_EFI_LOADER_CODE = 1,
    SP11_SWEEP_EFI_LOADER_DATA = 2,
    SP11_SWEEP_EFI_BOOT_SERVICES_CODE = 3,
    SP11_SWEEP_EFI_BOOT_SERVICES_DATA = 4,
    SP11_SWEEP_EFI_RUNTIME_SERVICES_CODE = 5,
    SP11_SWEEP_EFI_RUNTIME_SERVICES_DATA = 6,
    SP11_SWEEP_EFI_CONVENTIONAL_MEMORY = 7,
    SP11_SWEEP_EFI_UNUSABLE_MEMORY = 8,
    SP11_SWEEP_EFI_ACPI_RECLAIM_MEMORY = 9,
    SP11_SWEEP_EFI_ACPI_MEMORY_NVS = 10,
    SP11_SWEEP_EFI_MEMORY_MAPPED_IO = 11,
    SP11_SWEEP_EFI_MEMORY_MAPPED_IO_PORT_SPACE = 12,
    SP11_SWEEP_EFI_PAL_CODE = 13,
};

/* EFI_MEMORY_DESCRIPTOR.Attribute bit index 3 (UEFI spec EFI_MEMORY_WB): "the memory region
 * supports being configured as write-back cacheable". bounce_main.c's upstream sweep never reads
 * this field (docs/SP11-Q1N1-BOOT-STUDY.md section 4.3); this is the one check this design adds.
 * Attribute is a 64-bit field in the real EFI_MEMORY_DESCRIPTOR, hence UINT64_C here. */
#define SP11_SWEEP_ATTRIBUTE_WB (UINT64_C(1) << 3)

/* One EFI_MEMORY_DESCRIPTOR, reduced to what this module needs. attribute is the raw Attribute
 * field (not just a WB flag) so sp11_sweep_type_is_swept/attribute tests can be written against
 * the real bit, matching tools/sp11_ebs_sweep_model.py's Descriptor.attribute field exactly. */
struct sp11_sweep_descriptor {
    uint32_t type;
    uint64_t attribute;
    uint64_t base;
    uint64_t pages;
};

static inline uint64_t sp11_sweep_descriptor_size(const struct sp11_sweep_descriptor *d)
{
    return d->pages * SP11_SWEEP_PAGE_BYTES;
}
static inline uint64_t sp11_sweep_descriptor_end(const struct sp11_sweep_descriptor *d)
{
    return d->base + sp11_sweep_descriptor_size(d);
}
static inline int sp11_sweep_descriptor_contains(const struct sp11_sweep_descriptor *d, uint64_t base,
                                                 uint64_t size)
{
    return d->base <= base && base + size <= sp11_sweep_descriptor_end(d);
}

/* bounce_main.c:124-130's switch, reproduced exactly: these seven types and no others. Mirrors
 * tools/sp11_ebs_sweep_model.py's SWEPT_TYPES frozenset. */
int sp11_sweep_type_is_swept(uint32_t type);

enum sp11_sweep_status {
    SP11_SWEEP_OK = 0,
    SP11_SWEEP_ERR_BAD_RANGE,       /* the declared range itself has size 0 */
    SP11_SWEEP_ERR_NOT_CONTAINED,   /* not covered by exactly one descriptor (0, or more than 1) */
    SP11_SWEEP_ERR_WRONG_TYPE,      /* the one covering descriptor's Type is not swept */
    SP11_SWEEP_ERR_NOT_WRITEBACK,   /* the one covering descriptor lacks EFI_MEMORY_WB */
    SP11_SWEEP_ERR_CHUNK_CAP,       /* chunk_cap is 0 */
    SP11_SWEEP_ERR_TOO_MANY_OPS,    /* the range would need more chunks than the caller's ops buffer holds */
};
const char *sp11_sweep_status_name(int status);

struct sp11_sweep_op {
    uint64_t base;
    uint64_t size;
};

/* Validates and chunks one declared (base, size) range against descriptors[0..count). On
 * SP11_SWEEP_OK, writes the emitted operations to ops[0..*n_ops) (every op's size <= chunk_cap,
 * *n_ops <= max_ops) and returns SP11_SWEEP_OK; *n_ops and ops are left unspecified on any other
 * return value -- the caller must not sweep anything for a refused range. Mirrors
 * tools/sp11_ebs_sweep_model.py's scoped_sweep() per-range body exactly: containment first (so a
 * range split across a descriptor boundary is refused before its type or attribute is even
 * examined), then type, then attribute, then chunking. */
int sp11_sweep_range(const struct sp11_sweep_descriptor *descriptors, uint32_t count, uint64_t base,
                     uint64_t size, uint32_t chunk_cap, struct sp11_sweep_op *ops, uint32_t max_ops,
                     uint32_t *n_ops);

#endif /* SLBOUNCE_SCOPED_SWEEP */
#endif /* SP11_SCOPED_SWEEP_H */
