/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* Where the SLBOUNCE_SP11_MARKERS build may draw the de-VHE trampoline's progress markers.
 *
 * The de-VHE trampoline runs with the MMU off and no console, so a hang in it shows nothing.
 * It marks its progress by filling small squares in a strip near the top-right corner of the
 * GOP framebuffer, which the display scans out regardless of what the CPU is doing. This file
 * decides, before ExitBootServices and from nothing but the GOP mode and the memory map, whether a
 * given framebuffer is safe to write to and where the strip goes. It is pure (no EFI type, no
 * allocation, no I/O) so a host test can drive it with the unit's recorded map.
 *
 * Refusal is the default: any doubt yields no strip and the trampoline then draws nothing. The
 * markers are diagnostic only and never gate the bounce.
 *
 * The validation is the framebuffer rule q1n1 applies (platform/uefi/sp11-fbgop.h in the q1n1 fork,
 * Q1N1_SP11_GOP_TYPES_MASK), restated here because the two projects share no build, and stricter in
 * one respect: the whole framebuffer must lie inside exactly one descriptor, as it does on the unit
 * (a single RESERVED descriptor). tools/test-sp11-marker-surface.py checks the two type masks agree
 * when a q1n1 checkout is available. */
#ifndef SP11_MARKER_SURFACE_H
#define SP11_MARKER_SURFACE_H

#ifdef SLBOUNCE_SP11_MARKERS

#include <stdint.h>

/* Strip geometry. src/trans.s repeats SIDE, STEP and SLOTS as assembler constants; the test checks
 * the two copies agree. Slot 0 is drawn by bounce_main.c before ExitBootServices ("armed"); slots 1
 * to 3 are drawn by the trampoline (the dry run adds slot 4); a refused TCR conversion fills every
 * slot. A visible slot proves its stores landed, not that the dsb sy closing it returned. */
#define SP11_MARKER_SLOTS 8u
#define SP11_MARKER_SIDE_PX 32u
#define SP11_MARKER_STEP_PX 48u
#define SP11_MARKER_MARGIN_PX 24u
#define SP11_MARKER_MIN_WIDTH_PX (2u * SP11_MARKER_MARGIN_PX + SP11_MARKER_SLOTS * SP11_MARKER_STEP_PX)
#define SP11_MARKER_MIN_HEIGHT_PX (2u * SP11_MARKER_MARGIN_PX + SP11_MARKER_SIDE_PX)

/* EFI_MEMORY_TYPE values a framebuffer may sit in: reserved (0), boot services data (4), runtime
 * services data (6), memory-mapped I/O (11) and I/O port space (12). Anything else belongs to
 * another user and must not be written. */
#define SP11_MARKER_FB_TYPES_MASK ((1u << 0) | (1u << 4) | (1u << 6) | (1u << 11) | (1u << 12))

/* pixel_format: 0 = RGBX, 1 = BGRX (EFI_GRAPHICS_PIXEL_FORMAT); other formats are refused. The
 * markers are white, which is the same value in both. */
struct sp11_marker_fb {
	uint64_t base, size_bytes;
	uint32_t width, height, pixels_per_scanline, pixel_format;
};

enum {
	SP11_MARKER_OK = 0,
	SP11_MARKER_BAD_MODE,   /* geometry, alignment or pixel format this code does not trust */
	SP11_MARKER_BAD_MAP,    /* the memory map cannot be read as an array of descriptors */
	SP11_MARKER_NOT_IN_MAP, /* the framebuffer is not wholly inside one descriptor of an allowed type */
};

struct sp11_marker_strip {
	uint64_t origin;       /* address of slot 0's top-left pixel; 0 when there is no strip */
	uint64_t stride_bytes; /* bytes from a pixel to the one below it */
};

/* raw/map_bytes/desc_size are the buffer, byte count and descriptor stride GetMemoryMap returned
 * (the stride is not sizeof(EFI_MEMORY_DESCRIPTOR) in general). Returns SP11_MARKER_OK and fills
 * *out, or another SP11_MARKER_* value with *out zeroed. */
int sp11_marker_plan(const uint8_t *raw, uint64_t map_bytes, uint64_t desc_size, const struct sp11_marker_fb *fb,
		     struct sp11_marker_strip *out);

#endif /* SLBOUNCE_SP11_MARKERS */
#endif /* SP11_MARKER_SURFACE_H */
