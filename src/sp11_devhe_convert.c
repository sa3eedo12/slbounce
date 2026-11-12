/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* See sp11_devhe_convert.h.
 *
 * Why this one register is a called function and not inline assembly like the other four
 * (HCR_EL2 is a fixed constant; MAIR_EL2 has no VHE/non-VHE layout difference at all; TTBR0_EL2's
 * whole conversion is "clear bits 63:48", one `and`; CNTHCTL_EL2 and CPTR_EL2 are fixed constants
 * for reasons this patch's REPORT.md explains): TCR_EL2's conversion is an eleven-field bitfield
 * remap across bit positions up to 60 (compat/el2-entry-model.c el2m_tcr_vhe_to_nvhe, Snapintosh
 * repository). Hand-written as a chain of AArch64 ubfx/bfi pairs, this would be both the hardest
 * part of this whole patch to get right blind and the hardest for the coordinator to check by
 * inspection against the already-reviewed C model it must match. As plain, portable C instead, it
 * can be: (a) built and unit-tested on the host directly (tools/test-sp11-devhe-convert.c/.py);
 * (b) cross-checked bit for bit against the real Snapintosh model function, not a transcription of
 * it, when SNAPINTOSH_DIR is set; and (c) read as a direct line-for-line translation of the C this
 * patch's REPORT.md cites.
 *
 * Calling it from the middle of tb_longjmp, with the MMU off, is only safe because this function
 * touches no memory and preserves every register AAPCS64 requires a callee to preserve: it has no
 * local array, no global, no further call, and every intermediate value lives in a register the
 * compiler is free to pick from x0-x17 (caller-saved) for a four-or-so-deep expression tree this
 * small. tools/test-sp11-devhe-convert.py's `test_compiled_leaf_touches_no_stack_or_x19_x28`
 * compiles this file with the exact aarch64-elf-gcc flags and -O2 override the real build uses
 * (CFLAGS carries no -O at all; this file's own Makefile rule adds -O2 for exactly this reason) and
 * disassembles the result, asserting no SP-relative load or store and no x19-x28 write appear
 * anywhere in the function body, and no further `bl`/`blr` either -- a build-time proof of the
 * property this placement depends on, not merely an assumption about what the compiler happens to
 * do today. This aarch64-elf-gcc still emits a `sub sp, sp, #0x10` / `add sp, sp, #0x10` pair
 * around the body at every optimization level tried (-O2 and -O3, with and without
 * -fomit-frame-pointer): confirmed, by reading every instruction in between, to never dereference
 * through sp (no `[sp, ...]` operand appears at all), so it adjusts the stack pointer's value but
 * performs no stack access -- harmless with the MMU off specifically because nothing is read or
 * written through it. The test checks for the dereference, not the arithmetic, and is the reason
 * this paragraph can say so with more than a visual read of one build's disassembly. If a future
 * toolchain or flag change ever turned that reservation into a real spill, that test would catch it
 * before trans.s did. */
#ifdef SLBOUNCE_SP11_DEVHE
#include "sp11_devhe_convert.h"

#define SP11_DEVHE_BIT(n) (UINT64_C(1) << (n))
#define SP11_DEVHE_RANGE(hi, lo) ((~UINT64_C(0) >> (63 - (hi))) & (~UINT64_C(0) << (lo)))

/* el2m_image_valid(EL2M_TCR, EL2M_VHE, v) (compat/el2-entry-model.c): the VHE-layout RES0 bits
 * (TCR_EL1 format) must be zero, and TG1 (bits 31:30) must not be the reserved granule encoding
 * 0b00. This is validity of the *source* image; the converted output's own RES0/RES1 bits are
 * satisfied by construction below (every field this function writes lands inside the non-VHE
 * layout's defined bits, and the two RES1 bits are set unconditionally), so no separate output
 * check is needed -- tools/test-sp11-devhe-convert.py proves this for the full input space the
 * oracle model is prepared to check. */
static int tcr_vhe_image_valid(uint64_t v)
{
    uint64_t res0 = SP11_DEVHE_RANGE(63, 62) | SP11_DEVHE_BIT(35) | SP11_DEVHE_BIT(6);
    if (v & res0) return 0;
    if (!((v >> 30) & 3)) return 0; /* TG1 == 0b00 */
    return 1;
}

struct sp11_devhe_tcr_result sp11_devhe_tcr_vhe_to_nvhe(uint64_t vhe)
{
    struct sp11_devhe_tcr_result r = {0, 0};
    /* EPD0 (bit 7): the VHE image disables its own TTBR0 walk, so there is nothing for the
     * non-VHE TTBR0 walk this conversion exists to carry forward to continue from. */
    if (!tcr_vhe_image_valid(vhe) || (vhe & SP11_DEVHE_BIT(7)))
        return r;

    uint64_t o = SP11_DEVHE_BIT(31) | SP11_DEVHE_BIT(23); /* RES1 in the non-VHE layout */
    o |= vhe & (SP11_DEVHE_RANGE(15, 8) | SP11_DEVHE_RANGE(5, 0)); /* TG0,SH0,ORGN0,IRGN0; T0SZ */
    o |= ((vhe >> 32) & 7) << 16;  /* IPS -> PS */
    o |= ((vhe >> 37) & 1) << 20;  /* TBI0 -> TBI */
    o |= ((vhe >> 39) & 1) << 21;  /* HA */
    o |= ((vhe >> 40) & 1) << 22;  /* HD */
    o |= ((vhe >> 41) & 1) << 24;  /* HPD0 -> HPD */
    o |= ((vhe >> 43) & 15) << 25; /* HWU059..HWU062 -> HWU59..HWU62 */
    o |= ((vhe >> 51) & 1) << 29;  /* TBID0 -> TBID */
    o |= ((vhe >> 57) & 1) << 30;  /* TCMA0 -> TCMA */
    o |= ((vhe >> 59) & 1) << 32;  /* DS */
    o |= ((vhe >> 60) & 1) << 33;  /* MTX0 -> MTX */

    r.value = o;
    r.valid = 1;
    return r;
}

#endif /* SLBOUNCE_SP11_DEVHE */
