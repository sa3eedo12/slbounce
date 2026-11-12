/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2024 Nikita Travkin <nikita@trvn.ru> */

/* System Control Register (EL2) */
.equ	SCTLR_EL2_RES0,		0b1111111111111111111011111111111100000101000100001100011110000000
.equ	SCTLR_EL2_RES1,		0b0000000000000000000000000000000000110000110001010000100000110000
.equ	SCTLR_EL2_M,		1 << 0	/* MPU enable. */

/* Counter-timer Hypervisor Control Register */
.equ	CNTHCTL_EL2_EL1PCTEN,	1 << 0	/* Traps accesses to the EL1 physical counter register to EL2. */
.equ	CNTHCTL_EL2_EL1PCEN,	1 << 1	/* Traps accesses to the EL1 physical timer registers to EL2. */

/* Hypervisor Configuration Register (EL2) */
.equ	HCR_EL2_TGE,		1 << 27	/* Traps general exceptions. */
.equ	HCR_EL2_E2H,		1 << 34	/* EL2 Host. */

.ifdef SLBOUNCE_SP11_DEVHE
/* docs/SP11-Q1N1-BOOT-STUDY.md section 6 / docs/SP11-EL2-ENTRY.md section 2.3's canonical
 * sequence: the non-VHE EL2 state q1n1 already sees on the A16 (HCR_EL2 OBSERVED there,
 * docs/A16-Q1N1-PROXY.md in the q1n1 tree; E2H clear, RW and TGE set) and a maximally permissive
 * non-VHE CPTR_EL2 (RES1 only: nothing trapped -- the same value every sequence in the host
 * model's own canonical data uses for this register, compat/el2-entry-model.c
 * EL2M_CANON_CPTR). Neither is a conversion of a saved image: there is no "HCR_EL1" or
 * "CPTR_EL1" to convert from (this patch's REPORT.md explains the CPTR case fully). */
.equ	SP11_DEVHE_HCR_A16,	0x88000010
.equ	SP11_DEVHE_CPTR_NOTRAP,	0x22ff
.endif


/*
 * tb_entry() - Entry point for "Transition back" feature of tcblaunch.exe
 * x0: Pointer to tz_data.tb_data struct.
 */
.global tb_entry
tb_entry:
	mov	x9, x0			// x0 contains struct address, save.
	hvc	0x1			// Magic moment here - The call will return in EL2

	/* Disable MMU for now */
	mrs	x0, sctlr_el2
	bic	x0, x0, #SCTLR_EL2_M
	msr	sctlr_el2, x0
	isb

	/* Make sure interrupts are disabled too */
	msr	daifset, #0b1111

	ldr	x1, [x9, #8]		// tb_data->sp holds framebuffer base
	ldr	x2, [x9, #16]		// tb_data->tcr holds framebuffer stride
	cbnz	x1, draw_on_screen	// If we have a framebuffer, draw on it and hang.

	ldr	x0, [x9]		// tb_data->mair holds a pointer to our tb_jmp_buf
	mov	x1, #1
	bl	tb_longjmp

	b	_psci_off

draw_on_screen:
	mov	x0, #(16 * 4)
	mov	x4, #0x0000FF00
	mul	x0, x0, x2

draw_loop:
	str	w4, [x1, x0]
	sub	x0, x0, #4
	cbnz	x0, draw_loop

	b	halt


/* _psci_off() - Turn off the device */
_psci_off:
	mov	x0, #0xc4000000
	add	x0, x0, #0x8
	smc	0x0


/* halt() - Loop forever */
halt:
	wfe
	b	halt


.macro movq Xn, imm
	movz    \Xn, (\imm >> 0 ) & 0xFFFF
	movk    \Xn, (\imm >> 16) & 0xFFFF, lsl 16
	movk    \Xn, (\imm >> 32) & 0xFFFF, lsl 32
	movk    \Xn, (\imm >> 48) & 0xFFFF, lsl 48
.endm


/*
 * tb_setjmp() - setjmp that saves system registers too.
 * x0: Pointer to the register save array.
 *
 * Note that EL1 registers are saved.
 */
.global tb_setjmp
tb_setjmp:

	.macro st_sys sysreg, offt
		/* Save a system register */
		mrs	x2, \sysreg
		str	x2, [x0,\offt]
	.endm

	/* Save system registers. */

	/* AArch64Support.S */
	st_sys	vbar_el1,  #104
	st_sys	tpidr_el0, #112

	/* ArmLibSupport.S */
	st_sys	cpacr_el1, #120
	st_sys	ttbr0_el1, #128
	st_sys	tcr_el1,   #136
	st_sys	mair_el1,  #144
	st_sys	sctlr_el1, #152

	st_sys	daif,      #160

	/* Save calle-saved registers and the stack. */
	stp	x19, x20, [x0,#0]
	stp	x21, x22, [x0,#16]
	stp	x23, x24, [x0,#32]
	stp	x25, x26, [x0,#48]
	stp	x27, x28, [x0,#64]
	stp	x29, x30, [x0,#80]

	mov	x2, sp
	str	x2, [x0,#96]

	mov	x0, #0
	ret


/*
 * tb_longjmp() - longjmp that restores system registers too.
 * x0: Pointer to the register save array.
 * x1: Return code.
 *
 * Note that the registers are restored in EL2
 */
.global tb_longjmp
tb_longjmp:

	.macro ld_sys sysreg, offt
		/* Save a system register */
		ldr	x2, [x0,\offt]
		msr	\sysreg, x2
		isb
	.endm

	/* Restore system registers. */

	/*
	 * Invalidate all TLB.
	 * With workaround for Cortex-A76 r3p0 errata 1286807,
	 * tho probably doesn't matter for 1 core here.
	 */
	tlbi	alle2
	dsb	ish
	tlbi	alle2

	/* AArch64Support.S */
	ld_sys	vbar_el2,  #104
	ld_sys	tpidr_el0, #112

		//cptr_el2

	/* ArchInitialize() */
.ifdef SLBOUNCE_SP11_DEVHE
	/* The SP11 fork: give the EFI application and q1n1 a non-VHE EL2 instead of the VHE one
	 * slbounce gives every other target (docs/SP11-Q1N1-BOOT-STUDY.md section 6). x0 (the
	 * jmp_buf pointer) moves to x19, and x1 (the caller's retval argument, used again at this
	 * function's very end) moves to x20 -- neither is restored from the jmp_buf until the ldp
	 * sequence below, so both are safe to use as scratch here -- freeing x0/x1 for
	 * sp11_devhe_tcr_vhe_to_nvhe's argument and two-register return value. */
	mov	x19, x0
	mov	x20, x1

	movq	x2, SP11_DEVHE_HCR_A16
	msr	hcr_el2, x2
	isb

	/* CNTHCTL_EL2, non-VHE layout: the same two bits and the same numeric value as the plain
	 * build's write below, but E2H is already 0 here, so they now mean what this file's own
	 * CNTHCTL_EL2_EL1PCTEN/EL1PCEN comment says (EL1 passthrough), not the VHE layout's EL0
	 * controls those bit positions actually are under E2H=1 (REPORT.md). */
	mov	x2, #(CNTHCTL_EL2_EL1PCTEN | CNTHCTL_EL2_EL1PCEN)
	msr	cnthctl_el2, x2

	/* CPTR_EL2, non-VHE layout: fixed, maximally permissive (REPORT.md: no conversion of
	 * cpacr_el1 is attempted; neither does the host model's own canonical data). */
	movq	x2, SP11_DEVHE_CPTR_NOTRAP
	msr	cptr_el2, x2

	/* TTBR0_EL2, non-VHE layout: el2m_ttbr0_vhe_to_nvhe's whole body (clear the ASID, RES0
	 * unless E2H is 1) is one instruction, done inline rather than called. */
	ldr	x2, [x19, #128]
	and	x2, x2, #0x0000ffffffffffff
	msr	ttbr0_el2, x2

	/* TCR_EL2, non-VHE layout: the eleven-field remap, out of line (sp11_devhe_convert.c). On
	 * SP11_DEVHE_TCR_INVALID (x1 == 0) halt rather than risk writing an unconvertible image:
	 * the existing fail-safe loop below, not a new one. */
	ldr	x0, [x19, #136]
	bl	sp11_devhe_tcr_vhe_to_nvhe
	cbz	x1, halt
	msr	tcr_el2, x0
	/* MAIR_EL2 (below, unchanged): no VHE/non-VHE layout difference exists for it
	 * (docs/SP11-EL2-ENTRY.md section 2.7's layout list does not name it), so the saved
	 * mair_el1 image is valid as MAIR_EL2 verbatim in either regime. */
	ldr	x2, [x19, #144]
	msr	mair_el2, x2

	/* Restore x0 = the jmp_buf pointer and x1 = the caller's retval (both clobbered above by
	 * the TCR call's argument and two-register return value): every instruction after this
	 * point, unchanged by this gate, addresses the jmp_buf through x0 and the final return
	 * value computation through x1. */
	mov	x0, x19
	mov	x1, x20

	/* A second TLBI, with its own barriers, after every translation-relevant register
	 * (TTBR0_EL2, TCR_EL2, MAIR_EL2) has been rewritten above and before SCTLR_EL2 turns the
	 * MMU back on below: compat/el2-entry-model.c's el2m_seq_slbounce_devhe (the canonical,
	 * host-tested reference this trans.s change ports) requires this exact "DSB, TLBI, DSB,
	 * ISB" after its own register-write group, not only the TLBI the unmodified code above
	 * already runs before any register is touched (SP11-EL2-ENTRY.md section 2.3 steps 10-13;
	 * section 2.5 on this exact placement: "a second one, with its barriers, follows the
	 * register writes"). Without it the model refuses the sequence as MMU_ON_STALE_TLB even
	 * though the first, earlier TLBI already ran -- that first one precedes the new TCR/TTBR0
	 * configuration being installed, so it cannot be relied on to cover any translation a
	 * speculative walk forms under the new configuration while the MMU is still (architecturally)
	 * capable of being re-enabled with stale state (section 2.11: the model does not assume away
	 * speculative TLB fills). The VHE (`.else`) path below is unchanged and does not get this
	 * second TLBI: el2m_ref_slbounce_vhe is only accepted with strict_tlbi_after_config relaxed,
	 * i.e. it is OBSERVED to work on real hardware without it, and nothing here may alter that
	 * path (the non-SP11 behaviour is not this patch's to change). */
	dsb	ish
	tlbi	alle2
	dsb	ish
	isb
.else
	movq	x2, (HCR_EL2_TGE | HCR_EL2_E2H)
	msr	hcr_el2, x2
	isb

	mov	x2, #(CNTHCTL_EL2_EL1PCTEN | CNTHCTL_EL2_EL1PCEN)
	msr	cnthctl_el2, x2

	/* ArmLibSupport.S */
	ld_sys	cpacr_el1, #120
	ld_sys	ttbr0_el2, #128
	ld_sys	tcr_el2,   #136
	ld_sys	mair_el2,  #144

	dsb	ish
	isb
.endif

	ldr	x2, [x0,#152]
	movq	x3, ~SCTLR_EL2_RES0
	and	x2, x2, x3
	movq	x3, SCTLR_EL2_RES1
	orr	x2, x2, x3
	msr	sctlr_el2, x2
	isb

	/* Now restore calle-saved registers and the stack */
	ldp	x19, x20, [x0,#0]
	ldp	x21, x22, [x0,#16]
	ldp	x23, x24, [x0,#32]
	ldp	x25, x26, [x0,#48]
	ldp	x27, x28, [x0,#64]
	ldp	x29, x30, [x0,#80]

	ldr	x2, [x0,#96]
	mov	sp, x2

	/* Finally, restore interrupts */
	ld_sys	daif,      #160

	cmp w1, 0
	csinc w0, w1, wzr, ne		// x0 = (x1 ? x1 : 1)
	br	x30


.data

.align	3
.global tb_jmp_buf
tb_jmp_buf:
	.zero	8 * 12	// x19-x30 - callee saved registers.
	.zero	8 * 1	// x31 - sp
	.zero	8 * 8	// System registers
