/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* Host tests for src/sp11_marker_surface.c's sp11_marker_plan(), the real implementation
 * bounce_main.c calls (not a mock). With no argument it runs its own checks. With --batch it reads
 * one case per stdin line and prints the verdict, for tools/test-sp11-marker-surface.py's
 * differential test against an independent Python oracle and its replays of the recorded unit map:
 *
 *   desc_size base size_bytes width height pixels_per_scanline pixel_format map_hex
 *
 * (all numbers hexadecimal except none; map_hex is the raw descriptor bytes, or "-" for a null
 * map). The reply is "status origin stride" in hexadecimal. */
#ifndef SLBOUNCE_SP11_MARKERS
#define SLBOUNCE_SP11_MARKERS
#endif
#ifndef SP11_MARKER_SOURCE
#define SP11_MARKER_SOURCE "../src/sp11_marker_surface.c"
#endif
#include SP11_MARKER_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures;
#define CHECK(cond) do { \
        if (!(cond)) { failures++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } \
    } while (0)

#define DESC 48u

static void put32(uint8_t *p, uint32_t v) { for (int i = 0; i < 4; i++) p[i] = (uint8_t)(v >> (8 * i)); }
static void put64(uint8_t *p, uint64_t v) { for (int i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i)); }

struct map { uint8_t raw[16 * DESC]; uint64_t bytes; };

static void add(struct map *m, uint32_t type, uint64_t base, uint64_t pages)
{
    uint8_t *d = m->raw + m->bytes;
    memset(d, 0, DESC);
    put32(d, type);
    put64(d + 8, base);
    put64(d + 24, pages);
    m->bytes += DESC;
}

/* The unit's framebuffer, as read by the probe and q1n1: 2880x1920 BGRX at 0xE4800000, one RESERVED
 * descriptor of 0x21C0 pages around it. */
static const struct sp11_marker_fb UNIT = {
    UINT64_C(0xE4800000), UINT64_C(0x1518000), 2880, 1920, 2880, 1
};

static void unit_map(struct map *m)
{
    m->bytes = 0;
    add(m, 7, UINT64_C(0xE0000000), 0x100);                 /* conventional, below */
    add(m, 0, UINT64_C(0xE4800000), 0x21C0);                /* RESERVED: the framebuffer */
    add(m, 7, UINT64_C(0xE4800000) + 0x21C0ull * 4096, 0x40); /* conventional, above */
}

static int plan(const struct map *m, uint64_t desc, const struct sp11_marker_fb *fb, struct sp11_marker_strip *s)
{
    return sp11_marker_plan(m->raw, m->bytes, desc, fb, s);
}

static void test_the_units_framebuffer_is_accepted(void)
{
    struct map m; struct sp11_marker_strip s;
    unit_map(&m);
    CHECK(plan(&m, DESC, &UNIT, &s) == SP11_MARKER_OK);
    /* 24 rows down and (2880 - 24 - 8 * 48) = 2472 pixels across, 11520 bytes per row. */
    CHECK(s.stride_bytes == 11520);
    CHECK(s.origin == UINT64_C(0xE4800000) + 24u * 11520u + 2472u * 4u);
    CHECK(s.origin == UINT64_C(0xE4845EA0));
    /* The strip's last pixel stays inside the framebuffer. */
    uint64_t last = s.origin + (uint64_t)(SP11_MARKER_SIDE_PX - 1) * s.stride_bytes +
                    (uint64_t)((SP11_MARKER_SLOTS - 1) * SP11_MARKER_STEP_PX + SP11_MARKER_SIDE_PX) * 4u;
    CHECK(last <= UNIT.base + UNIT.size_bytes);
}

static void test_other_types_and_shapes_are_refused(void)
{
    struct map m; struct sp11_marker_strip s;
    /* Allowed: reserved, boot data, runtime data, MMIO, MMIO port space. */
    static const uint32_t ok[] = {0, 4, 6, 11, 12};
    for (unsigned i = 0; i < sizeof ok / sizeof ok[0]; i++) {
        m.bytes = 0; add(&m, ok[i], UNIT.base, 0x21C0);
        CHECK(plan(&m, DESC, &UNIT, &s) == SP11_MARKER_OK);
    }
    /* Everything else, including loader memory and conventional RAM, and types no one has heard of. */
    static const uint32_t bad[] = {1, 2, 3, 5, 7, 8, 9, 10, 13, 14, 31, 32, 33, 0xffffffffu};
    for (unsigned i = 0; i < sizeof bad / sizeof bad[0]; i++) {
        m.bytes = 0; add(&m, bad[i], UNIT.base, 0x21C0);
        CHECK(plan(&m, DESC, &UNIT, &s) == SP11_MARKER_NOT_IN_MAP);
        CHECK(s.origin == 0 && s.stride_bytes == 0);
    }
    /* Split across two allowed descriptors: refused (stricter than q1n1, by design). */
    m.bytes = 0; add(&m, 0, UNIT.base, 0x1000); add(&m, 0, UNIT.base + 0x1000ull * 4096, 0x11C0);
    CHECK(plan(&m, DESC, &UNIT, &s) == SP11_MARKER_NOT_IN_MAP);
    /* An overlapping second descriptor, even of an allowed type. */
    unit_map(&m); add(&m, 0, UNIT.base + 0x100000, 1);
    CHECK(plan(&m, DESC, &UNIT, &s) == SP11_MARKER_NOT_IN_MAP);
    /* A descriptor one page short at the end. */
    m.bytes = 0; add(&m, 0, UNIT.base, 0x1517);
    CHECK(plan(&m, DESC, &UNIT, &s) == SP11_MARKER_NOT_IN_MAP);
    /* No descriptor at all around it. */
    m.bytes = 0; add(&m, 7, UINT64_C(0x80000000), 0x100);
    CHECK(plan(&m, DESC, &UNIT, &s) == SP11_MARKER_NOT_IN_MAP);
    /* A descriptor whose page count wraps the address space still counts as covering. */
    m.bytes = 0; add(&m, 0, UNIT.base - 4096, UINT64_C(0xffffffffffffff));
    CHECK(plan(&m, DESC, &UNIT, &s) == SP11_MARKER_OK);
}

static void test_bad_modes_are_refused_before_the_map_is_read(void)
{
    struct map m; struct sp11_marker_strip s; struct sp11_marker_fb fb;
    unit_map(&m);
    fb = UNIT; fb.pixel_format = 2; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.pixel_format = 3; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.pixel_format = 0; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_OK);
    fb = UNIT; fb.base = 0; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.base += 2; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.width = SP11_MARKER_MIN_WIDTH_PX - 1; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.width = SP11_MARKER_MIN_WIDTH_PX; fb.pixels_per_scanline = fb.width;
    CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_OK);
    fb = UNIT; fb.height = SP11_MARKER_MIN_HEIGHT_PX - 1; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.width = 16385; fb.pixels_per_scanline = 16385; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.height = 16385; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.pixels_per_scanline = fb.width - 1; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.pixels_per_scanline = 65537; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.size_bytes = 2880ull * 4 * 1919; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    fb = UNIT; fb.base = ~UINT64_C(0) - 0xfff; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
    /* The smallest size that holds the last row: height-1 full strides plus one row of pixels. */
    fb = UNIT; fb.size_bytes = 1919ull * 2880 * 4 + 2880ull * 4; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_OK);
    fb.size_bytes -= 4; CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE);
}

static void test_unreadable_maps_are_refused(void)
{
    struct map m; struct sp11_marker_strip s;
    unit_map(&m);
    CHECK(sp11_marker_plan(0, m.bytes, DESC, &UNIT, &s) == SP11_MARKER_BAD_MAP);
    CHECK(sp11_marker_plan(m.raw, 0, DESC, &UNIT, &s) == SP11_MARKER_BAD_MAP);
    CHECK(sp11_marker_plan(m.raw, m.bytes, 39, &UNIT, &s) == SP11_MARKER_BAD_MAP);
    CHECK(sp11_marker_plan(m.raw, m.bytes, 0, &UNIT, &s) == SP11_MARKER_BAD_MAP);
    CHECK(sp11_marker_plan(m.raw, m.bytes - 1, DESC, &UNIT, &s) == SP11_MARKER_BAD_MAP);
    /* A larger stride than the structure is normal: the platform may pad each descriptor. */
    struct map big; memset(&big, 0, sizeof big);
    uint64_t stride = 64;
    put32(big.raw, 0); put64(big.raw + 8, UNIT.base); put64(big.raw + 24, 0x21C0);
    CHECK(sp11_marker_plan(big.raw, stride, stride, &UNIT, &s) == SP11_MARKER_OK);
}

static void test_a_refusal_leaves_no_strip(void)
{
    struct map m; struct sp11_marker_strip s = {123, 456};
    m.bytes = 0; add(&m, 7, UNIT.base, 0x21C0);
    CHECK(plan(&m, DESC, &UNIT, &s) == SP11_MARKER_NOT_IN_MAP && s.origin == 0 && s.stride_bytes == 0);
    s.origin = 1; s.stride_bytes = 2;
    struct sp11_marker_fb fb = UNIT; fb.pixel_format = 9;
    CHECK(plan(&m, DESC, &fb, &s) == SP11_MARKER_BAD_MODE && s.origin == 0 && s.stride_bytes == 0);
}

static int hexval(int c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

static int batch(void)
{
    static char line[1 << 20];
    while (fgets(line, sizeof line, stdin)) {
        unsigned long long desc, base, size, w, h, ppsl, fmt;
        int consumed = 0;
        if (sscanf(line, "%llx %llx %llx %llx %llx %llx %llx %n", &desc, &base, &size, &w, &h, &ppsl, &fmt, &consumed) != 7)
            continue;
        const char *hex = line + consumed;
        static uint8_t raw[1 << 19];
        uint64_t n = 0;
        int null_map = hex[0] == '-';
        while (!null_map && hexval(hex[0]) >= 0 && hexval(hex[1]) >= 0 && n < sizeof raw) {
            raw[n++] = (uint8_t)(hexval(hex[0]) << 4 | hexval(hex[1]));
            hex += 2;
        }
        struct sp11_marker_fb fb = {base, size, (uint32_t)w, (uint32_t)h, (uint32_t)ppsl, (uint32_t)fmt};
        struct sp11_marker_strip s;
        int st = sp11_marker_plan(null_map ? 0 : raw, n, desc, &fb, &s);
        printf("%x %llx %llx\n", st, (unsigned long long)s.origin, (unsigned long long)s.stride_bytes);
    }
    return 0;
}

int main(int argc, char **argv)
{
    if (argc > 1 && !strcmp(argv[1], "--batch"))
        return batch();
    test_the_units_framebuffer_is_accepted();
    test_other_types_and_shapes_are_refused();
    test_bad_modes_are_refused_before_the_map_is_read();
    test_unreadable_maps_are_refused();
    test_a_refusal_leaves_no_strip();
    if (failures) {
        printf("%d check(s) failed\n", failures);
        return 1;
    }
    printf("PASS\n");
    return 0;
}
