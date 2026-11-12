/* SPDX-License-Identifier: BSD-3-Clause */
/* Copyright (c) 2026 the Snapintosh project */
/* A tiny CLI around the real sp11_devhe_tcr_vhe_to_nvhe(), for tools/test-sp11-devhe-convert.py's
 * CrossLanguage checks against the Snapintosh repository's compat/el2-entry-model.c
 * el2m_tcr_vhe_to_nvhe() (SNAPINTOSH_DIR). Reads one hex TCR_EL1-format image per stdin line,
 * prints "valid <value-hex>" or "invalid" per line. Touches no memory beyond stdio. */
#ifndef SLBOUNCE_SP11_DEVHE
#define SLBOUNCE_SP11_DEVHE
#endif
#include "../src/sp11_devhe_convert.c"

#include <stdio.h>

int main(void)
{
    char line[64];
    while (fgets(line, sizeof line, stdin)) {
        unsigned long long v;
        if (sscanf(line, "%llx", &v) != 1) continue;
        struct sp11_devhe_tcr_result r = sp11_devhe_tcr_vhe_to_nvhe((uint64_t)v);
        if (r.valid)
            printf("valid %llx\n", (unsigned long long)r.value);
        else
            printf("invalid\n");
    }
    return 0;
}
