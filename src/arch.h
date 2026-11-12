#ifndef ARCH_H
#define ARCH_H

#include <stdint.h>

void clear_dcache_range(uint64_t start, uint64_t size);
uint64_t smc(uint64_t x0, uint64_t x1, uint64_t x2, uint64_t x3);
uint64_t smc6(uint64_t x0, uint64_t x1, uint64_t x2, uint64_t x3, uint64_t x4, uint64_t x5);
void psci_off(void);
void psci_reboot(void);

/* In trans.s */
void tb_entry(void);
int tb_setjmp(uint64_t *jmp_buf) __attribute__((returns_twice));
int tb_longjmp(uint64_t *jmp_buf, uint64_t retval) __attribute__((noreturn));

/* x19-x30, sp and eight system registers; the SP11 progress-marker build adds two words for its
 * markers (trans.s SP11_JB_MARK_*). */
#ifdef SLBOUNCE_SP11_MARKERS
#define TB_JMP_BUF_QWORDS 23
#else
#define TB_JMP_BUF_QWORDS 21
#endif
extern uint64_t tb_jmp_buf[TB_JMP_BUF_QWORDS];

#endif
