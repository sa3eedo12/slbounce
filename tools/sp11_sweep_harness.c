/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* A tiny stdin-driven bridge onto the real src/sp11_scoped_sweep.c, so
 * tools/test-sp11-scoped-sweep.py's CrossLanguage tests can feed the exact same synthetic memory
 * maps to this C implementation and to the Snapintosh repository's own
 * tools/sp11_ebs_sweep_model.py, and compare the results, the same pattern
 * tools/test-sp11-usb-ports.py already uses in that repository for the USB port table validator.
 *
 * Protocol (one call = one declared range against one descriptor set), read from stdin:
 *   D <type> <attribute-hex> <base-hex> <pages>     -- zero or more descriptor lines
 *   R <base-hex> <size-hex> <chunk-cap>              -- exactly one range line, must be last
 * Output, to stdout:
 *   OK <n>                 followed by n lines "<base-hex> <size-hex>", one per emitted op
 *   ERR <status-name>      (sp11_sweep_status_name) on any refusal
 * Never touches real memory, never calls clear_dcache_range (there is no such function linked
 * in): this is sp11_sweep_range()'s own pure logic only. */
#ifndef SLBOUNCE_SCOPED_SWEEP
#define SLBOUNCE_SCOPED_SWEEP
#endif
#include "../src/sp11_scoped_sweep.c"

#include <stdio.h>
#include <string.h>

#define MAX_DESCRIPTORS 64
#define MAX_OPS 4096

int main(void)
{
    static struct sp11_sweep_descriptor descriptors[MAX_DESCRIPTORS];
    uint32_t count = 0;
    char line[256];
    uint64_t rbase = 0, rsize = 0;
    uint32_t chunk_cap = 0;
    int have_range = 0;

    while (fgets(line, sizeof line, stdin)) {
        char kind;
        if (sscanf(line, " %c", &kind) != 1) continue;
        if (kind == 'D') {
            unsigned type;
            unsigned long long attr, base, pages;
            if (sscanf(line, " D %x %llx %llx %llx", &type, &attr, &base, &pages) != 4) {
                fprintf(stderr, "bad D line: %s", line);
                return 2;
            }
            if (count >= MAX_DESCRIPTORS) {
                fprintf(stderr, "too many descriptors\n");
                return 2;
            }
            descriptors[count].type = type;
            descriptors[count].attribute = (uint64_t)attr;
            descriptors[count].base = (uint64_t)base;
            descriptors[count].pages = (uint64_t)pages;
            count++;
        } else if (kind == 'R') {
            unsigned long long base, size;
            unsigned cap;
            if (sscanf(line, " R %llx %llx %u", &base, &size, &cap) != 3) {
                fprintf(stderr, "bad R line: %s", line);
                return 2;
            }
            rbase = (uint64_t)base;
            rsize = (uint64_t)size;
            chunk_cap = cap;
            have_range = 1;
        }
    }
    if (!have_range) {
        fprintf(stderr, "no R line given\n");
        return 2;
    }

    static struct sp11_sweep_op ops[MAX_OPS];
    uint32_t n = 0;
    int rc = sp11_sweep_range(descriptors, count, rbase, rsize, chunk_cap, ops, MAX_OPS, &n);
    if (rc != SP11_SWEEP_OK) {
        printf("ERR %s\n", sp11_sweep_status_name(rc));
        return 0;
    }
    printf("OK %u\n", n);
    for (uint32_t i = 0; i < n; i++)
        printf("%llx %llx\n", (unsigned long long)ops[i].base, (unsigned long long)ops[i].size);
    return 0;
}
