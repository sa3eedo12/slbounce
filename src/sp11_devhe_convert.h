/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* The EL2 de-VHE TCR_EL2 field conversion, called from trans.s under SLBOUNCE_SP11_DEVHE. See
 * sp11_devhe_convert.c for why this is the one register of the five trans.s rewrites (HCR, MAIR,
 * TCR, TTBR0, CNTHCTL, CPTR) that is implemented as a called C function rather than inline
 * assembly, and this patch's REPORT.md for the full placement and safety argument. */
#ifndef SP11_DEVHE_CONVERT_H
#define SP11_DEVHE_CONVERT_H

#ifdef SLBOUNCE_SP11_DEVHE

#include <stdint.h>

/* AAPCS64 returns an 8+8 byte aggregate with no floating-point member in X0:X1 directly: no
 * memory is touched to pass this back to trans.s, which is the property this function's caller
 * needs (docs/SP11-EL2-ENTRY.md section 2.3: "no load or store occurs" in this part of the
 * sequence). valid is 0 exactly when the input could not be converted (docs/SP11-Q1N1-BOOT-
 * STUDY.md section 6, el2m_tcr_vhe_to_nvhe's own two refusal conditions): value is unspecified
 * then, and the caller must not write it to TCR_EL2. */
struct sp11_devhe_tcr_result {
    uint64_t value;
    uint64_t valid;
};

/* Converts a TCR_EL1-format image (what trans.s's saved tcr_el1, or equivalently TCR_EL2 read
 * back under HCR_EL2.E2H=1, actually holds -- docs/SP11-EL2-ENTRY.md section 2.7) into the
 * non-VHE TCR_EL2 layout (E2H=0), field by field. Ported from Snapintosh's host model,
 * compat/el2-entry-model.c el2m_tcr_vhe_to_nvhe(); tools/test-sp11-devhe-convert.py cross-checks
 * this function against that one directly (built from a real Snapintosh checkout) when
 * SNAPINTOSH_DIR is set, and against its own from-scratch Python re-derivation of the same field
 * table unconditionally. */
struct sp11_devhe_tcr_result sp11_devhe_tcr_vhe_to_nvhe(uint64_t vhe);

#endif /* SLBOUNCE_SP11_DEVHE */
#endif /* SP11_DEVHE_CONVERT_H */
