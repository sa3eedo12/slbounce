/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* See sp11_marker_surface.h. */
#ifdef SLBOUNCE_SP11_MARKERS
#include "sp11_marker_surface.h"

static uint32_t le32(const uint8_t *p)
{
	return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint64_t le64(const uint8_t *p)
{
	return le32(p) | (uint64_t)le32(p + 4) << 32;
}

int sp11_marker_plan(const uint8_t *raw, uint64_t map_bytes, uint64_t desc_size, const struct sp11_marker_fb *fb,
		     struct sp11_marker_strip *out)
{
	out->origin = 0;
	out->stride_bytes = 0;

	if (fb->pixel_format > 1)
		return SP11_MARKER_BAD_MODE;
	if (!fb->base || (fb->base & 3u))
		return SP11_MARKER_BAD_MODE;
	if (fb->width < SP11_MARKER_MIN_WIDTH_PX || fb->height < SP11_MARKER_MIN_HEIGHT_PX ||
	    fb->width > 16384u || fb->height > 16384u)
		return SP11_MARKER_BAD_MODE;
	if (fb->pixels_per_scanline < fb->width || fb->pixels_per_scanline > 65536u)
		return SP11_MARKER_BAD_MODE;

	uint64_t stride = (uint64_t)fb->pixels_per_scanline * 4u;
	uint64_t extent = (uint64_t)(fb->height - 1u) * stride + (uint64_t)fb->width * 4u;
	if (fb->size_bytes < extent || fb->base > ~UINT64_C(0) - extent)
		return SP11_MARKER_BAD_MODE;
	uint64_t fb_end = fb->base + extent;

	/* A descriptor's first 40 bytes are fixed by the UEFI specification: type at 0, physical start
	 * at 8, page count at 24. */
	if (!raw || !map_bytes || desc_size < 40u || map_bytes % desc_size)
		return SP11_MARKER_BAD_MAP;

	uint64_t overlapping = 0;
	int contains = 0, allowed = 0;
	for (uint64_t off = 0; off < map_bytes; off += desc_size) {
		const uint8_t *d = raw + off;
		uint32_t type = le32(d);
		uint64_t base = le64(d + 8), pages = le64(d + 24);
		/* Saturate rather than wrap: a wrapped end would make a huge descriptor look empty. */
		uint64_t end = pages > (~UINT64_C(0) - base) / 4096u ? ~UINT64_C(0) : base + pages * 4096u;
		if (base >= fb_end || end <= fb->base)
			continue;
		overlapping++;
		contains = base <= fb->base && fb_end <= end;
		allowed = type < 32u && ((SP11_MARKER_FB_TYPES_MASK >> type) & 1u);
	}
	if (overlapping != 1 || !contains || !allowed)
		return SP11_MARKER_NOT_IN_MAP;

	uint64_t x0 = fb->width - SP11_MARKER_MARGIN_PX - SP11_MARKER_SLOTS * SP11_MARKER_STEP_PX;
	out->origin = fb->base + (uint64_t)SP11_MARKER_MARGIN_PX * stride + x0 * 4u;
	out->stride_bytes = stride;
	return SP11_MARKER_OK;
}

#endif /* SLBOUNCE_SP11_MARKERS */
