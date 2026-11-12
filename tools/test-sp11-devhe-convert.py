#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 the Snapintosh project
"""Host tests for the EL2 de-VHE TCR_EL2 conversion (src/sp11_devhe_convert.c), called from
trans.s under SLBOUNCE_SP11_DEVHE (docs/SP11-Q1N1-BOOT-STUDY.md section 6).

    python3 tools/test-sp11-devhe-convert.py

Runs:
  * Native: tools/test-sp11-devhe-convert.c's own functional checks, under ASan+UBSan;
  * StackSafety: compiles src/sp11_devhe_convert.c with the exact flags the real Makefile rule
    uses (including its -O2 override) and disassembles it, asserting no SP-relative load or store
    and no x19-x28 write appear anywhere in sp11_devhe_tcr_vhe_to_nvhe, and no further bl/blr --
    the build-time proof that calling this function from trans.s with the MMU off is safe
    (sp11_devhe_convert.c's own header comment explains why this specific property, not "no SP
    arithmetic at all", is what is checked and why);
  * CrossLanguage: this file's sp11_devhe_tcr_vhe_to_nvhe() and the Snapintosh repository's own
    compat/el2-entry-model.c el2m_tcr_vhe_to_nvhe() agree on a battery of generated TCR_EL1-format
    images, including every edge case the Native tests exercise and 2000 pseudo-random ones (SKIP
    without SNAPINTOSH_DIR);
  * ObservedUnit: the real converter on the TCR_EL2 image the Surface Pro 11 unit actually held
    after the bounce (OBSERVED, one run), with no Snapintosh checkout needed;
  * Mutations: four seeded bugs are each confirmed to change this function's behaviour;
  * TransSequence: assembles the real src/trans.s with aarch64-elf-as, with and without
    -DSLBOUNCE_SP11_DEVHE (--defsym), disassembles tb_longjmp, and checks the *order* of
    register-writes and barriers against compat/el2-entry-model.c's own canonical
    el2m_seq_slbounce_devhe ops (parsed from source text when SNAPINTOSH_DIR is set; this file's
    own transcription of the same array otherwise). This is a structural, order-only check (no
    AArch64 execution occurs); it exists because the functional tests above exercise only
    sp11_devhe_tcr_vhe_to_nvhe() in isolation and cannot catch a trans.s *ordering* mistake. It
    was added after exactly such a mistake (a missing second TLBI+barriers after the register
    writes, required by el2m_seq_slbounce_devhe but absent from an earlier draft of this patch)
    was found by manual review against the model during this patch's own development -- see
    REPORT.md. MutateTransSequence reintroduces that same mistake and confirms TransSequence
    catches it, so the regression this test exists for has its own seeded-bug proof;
  * TbWindow: links the DEVHE build with the project's own Makefile, reads the link map, and checks
    that the converter lies inside the transition-back image sl.c hands tcblaunch (the pages from
    tb_entry's page, tb_size of them), where upstream's tb_entry..tb_longjmp already lives; the old
    far placement, relinked as a seeded mutant, must fail the same check (SKIP without
    aarch64-elf-gcc).

SNAPINTOSH_DIR: a Snapintosh checkout carrying compat/el2-entry-model.c (SKIP the CrossLanguage
class, and the cross-check half of TransSequence, without it). Nothing is written outside build/.
Hardware: NOT RUN. The real trampoline's behaviour when actually executed at EL2 on silicon is not
exercised by anything here (no AArch64 execution harness exists for it in this project): Evidence:
EXPERIMENTAL (host model and a structural disassembly check only), and UNIMPLEMENTED with respect
to the real trampoline's own behaviour on hardware."""
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'sp11-devhe-convert-test'
SANITIZE = ['-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-Wall', '-Wextra', '-Werror']
# The exact flags platform/uefi's... no: slbounce's own Makefile rule for this object (the -O2
# override next to SLBOUNCE_SP11_DEVHE in the Makefile; CFLAGS itself carries no -O at all).
REAL_BUILD_FLAGS = ['-Isrc/include', f'-I{ROOT / "external/gnu-efi/inc"}',
                    f'-I{ROOT / "external/gnu-efi/inc/aarch64"}', f'-I{ROOT / "external/gnu-efi/inc/protocol"}',
                    '-fpic', '-fshort-wchar', '-fno-stack-protector', '-ffreestanding',
                    '-DCONFIG_aarch64', '-D__MAKEWITH_GNUEFI', '-DGNU_EFI_USE_MS_ABI', '-mstrict-align',
                    '-DSLBOUNCE_SP11_DEVHE', '-O2']
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, **kw)


def have(tool):
    return shutil.which(tool) is not None


# The TCR_EL2 the Surface Pro 11 unit held after slbounce's bounce under the unmodified VHE restore
# (a TCR_EL1-format image: T0SZ 20, 4 KiB granule, IPS 44 bits, EPD1 set), read by q1n1 91748c0's EL2
# page on 2026-11-12 (OBSERVED, one run; docs/SURFACE-PRO-11.md in the Snapintosh repository). The
# non-VHE image below keeps the same TTBR0 walk and is what the trampoline would write.
OBSERVED_UNIT_TCR = 0x480803514
OBSERVED_UNIT_TCR_NVHE = 0x80843514


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have('clang'):
            raise unittest.SkipTest('clang not installed')
        BUILD.mkdir(parents=True, exist_ok=True)

    def test_the_real_implementation_passes_its_own_checks(self):
        binary = BUILD / 'test-sp11-devhe-convert'
        done = run(['clang'] + SANITIZE + ['tools/test-sp11-devhe-convert.c', '-o', str(binary)])
        self.assertEqual(done.returncode, 0, done.stderr)
        done = run([str(binary)])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('PASS', done.stdout)


class StackSafety(unittest.TestCase):
    """The safety argument sp11_devhe_convert.c's own header comment makes, checked at build time
    rather than assumed: compiled with the real aarch64-elf cross-compiler and the real build
    rule's flags, sp11_devhe_tcr_vhe_to_nvhe must never dereference through sp, never write
    x19-x28, and never itself call out (bl/blr)."""

    @classmethod
    def setUpClass(cls):
        cls.cc = shutil.which('aarch64-elf-gcc')
        cls.objdump = shutil.which('aarch64-elf-objdump')
        if not (cls.cc and cls.objdump):
            raise unittest.SkipTest('aarch64-elf-gcc/objdump not installed')
        BUILD.mkdir(parents=True, exist_ok=True)

    def _disassemble(self, source_text=None):
        src = ROOT / 'src/sp11_devhe_convert.c'
        if source_text is not None:
            src = BUILD / 'mutant_devhe_convert.c'
            src.write_text(source_text, encoding='utf-8')
        obj = BUILD / (src.stem + '.o')
        done = run([self.cc] + REAL_BUILD_FLAGS + ['-c', str(src), '-o', str(obj)])
        self.assertEqual(done.returncode, 0, done.stderr)
        done = run([self.objdump, '-d', '--no-show-raw-insn', str(obj)])
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def _function_body(self, disasm, name='sp11_devhe_tcr_vhe_to_nvhe'):
        lines = disasm.splitlines()
        start = next(i for i, l in enumerate(lines) if f'<{name}>:' in l)
        body = []
        for l in lines[start + 1:]:
            if l.strip() == '' and body:
                break
            body.append(l)
        self.assertTrue(body, f'{name} not found in disassembly')
        return body

    def test_no_sp_relative_memory_access(self):
        body = self._function_body(self._disassemble())
        offenders = [l for l in body if '[sp' in l]
        self.assertFalse(offenders, 'SP-relative load/store found:\n' + '\n'.join(offenders))

    def test_no_callee_saved_register_write(self):
        body = self._function_body(self._disassemble())
        # Match x19-x28 as a whole register token (preceded by start-of-operand punctuation,
        # followed by a comma or end of line), not as a substring of a hex immediate such as
        # "#0x20000000000" (confirmed to otherwise false-positive on this exact function).
        import re
        pattern = re.compile(r'(?<![0-9a-fx])(x1[9]|x2[0-8])(?![0-9])')
        offenders = [l for l in body if pattern.search(l)]
        self.assertFalse(offenders, 'x19-x28 referenced:\n' + '\n'.join(offenders))

    def test_no_further_call(self):
        body = self._function_body(self._disassemble())
        offenders = [l for l in body if '\tbl\t' in l or '\tblr\t' in l]
        self.assertFalse(offenders, 'a further call found:\n' + '\n'.join(offenders))


SNAPINTOSH_BRIDGE = """
#define SNAP_PLATFORM_SP11 1
#define SNAP_PLATFORM_A16 0
#include "{el2_model_c}"
#include <stdio.h>
int main(void) {{
    char line[64];
    while (fgets(line, sizeof line, stdin)) {{
        unsigned long long v;
        if (sscanf(line, "%llx", &v) != 1) continue;
        uint64_t out = 0;
        int rc = el2m_tcr_vhe_to_nvhe((uint64_t)v, &out);
        if (rc == 0) printf("valid %llx\\n", (unsigned long long)out);
        else printf("invalid\\n");
    }}
    return 0;
}}
"""


class CrossLanguage(unittest.TestCase):
    """sp11_devhe_tcr_vhe_to_nvhe() (this file's own CLI, tools/sp11_devhe_cli.c) and the
    Snapintosh repository's el2m_tcr_vhe_to_nvhe() (compat/el2-entry-model.c) must agree."""

    @classmethod
    def setUpClass(cls):
        cls.snap = None
        c = os.environ.get('SNAPINTOSH_DIR')
        if c and (Path(c) / 'compat/el2-entry-model.c').is_file():
            cls.snap = Path(c)
        cls.have_py = cls.snap is not None
        cls.have_c = have('clang')
        if not (cls.have_py and cls.have_c):
            return
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.mine = BUILD / 'devhe-cli-mine'
        done = run(['clang'] + SANITIZE + ['tools/sp11_devhe_cli.c', '-o', str(cls.mine)])
        assert done.returncode == 0, done.stderr
        bridge_c = BUILD / 'devhe-cli-model.c'
        bridge_c.write_text(SNAPINTOSH_BRIDGE.format(el2_model_c=cls.snap / 'compat/el2-entry-model.c'),
                            encoding='utf-8')
        cls.model = BUILD / 'devhe-cli-model'
        done = run(['clang', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra',
                    f'-I{cls.snap / "compat"}', str(bridge_c), '-o', str(cls.model)])
        assert done.returncode == 0, done.stderr

    def setUp(self):
        if not self.have_py:
            self.skipTest('SNAPINTOSH_DIR not set: no Snapintosh checkout to cross-check against')
        if not self.have_c:
            self.skipTest('clang not installed')

    def _query(self, binary, values):
        text = '\n'.join(f'{v:x}' for v in values) + '\n'
        done = subprocess.run([str(binary)], input=text, env=ENV, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.splitlines()

    def test_agree_on_the_documented_example_and_edge_cases(self):
        values = [
            0x00000035b5103510,             # a plausible UEFI image
            0x00000035b5103510 | (1 << 7),  # EPD0 set: invalid
            0x00000035b5103510 & ~(3 << 30),  # TG1 = 0b00: invalid
            0x00000035b5103510 | (1 << 35),  # RES0 violation: invalid
            0,                                # all zero: TG1 = 0b00, invalid
            OBSERVED_UNIT_TCR,                # what the Surface Pro 11 held after the bounce
        ]
        mine = self._query(self.mine, values)
        model = self._query(self.model, values)
        self.assertEqual(mine, model)

    def test_agree_on_two_thousand_pseudo_random_images(self):
        rng = random.Random(0xC0FFEE)
        values = [rng.getrandbits(64) for _ in range(2000)]
        mine = self._query(self.mine, values)
        model = self._query(self.model, values)
        self.assertEqual(mine, model)


class ObservedUnit(unittest.TestCase):
    """The real converter on the image the unit actually held, with no Snapintosh checkout needed: a
    conversion proven only on synthetic images says nothing about the walk this firmware configured."""

    @classmethod
    def setUpClass(cls):
        cls.have_c = have('clang')
        if not cls.have_c:
            return
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.cli = BUILD / 'devhe-cli-observed'
        done = run(['clang'] + SANITIZE + ['tools/sp11_devhe_cli.c', '-o', str(cls.cli)])
        assert done.returncode == 0, done.stderr

    def setUp(self):
        if not self.have_c:
            self.skipTest('clang not installed')

    def _convert(self, value):
        done = subprocess.run([str(self.cli)], input=f'{value:x}\n', env=ENV, capture_output=True, text=True,
                              cwd=ROOT)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.strip()

    def test_observed_tcr_keeps_the_walk_in_the_nonvhe_layout(self):
        self.assertEqual(self._convert(OBSERVED_UNIT_TCR), f'valid {OBSERVED_UNIT_TCR_NVHE:x}')
        # The fields TTBR0's walk depends on are carried bit for bit: T0SZ, IRGN0, ORGN0, SH0, TG0.
        self.assertEqual(OBSERVED_UNIT_TCR & 0xff3f, OBSERVED_UNIT_TCR_NVHE & 0xff3f)
        # IPS (bits 34:32) moves to PS (bits 18:16); the two non-VHE RES1 bits (31, 23) are set.
        self.assertEqual((OBSERVED_UNIT_TCR >> 32) & 7, (OBSERVED_UNIT_TCR_NVHE >> 16) & 7)
        self.assertEqual(OBSERVED_UNIT_TCR_NVHE & ((1 << 31) | (1 << 23)), (1 << 31) | (1 << 23))

    def test_a_wrong_conversion_of_the_observed_tcr_is_not_accepted(self):
        # EPD0 set would mean the unit's own TTBR0 walk is off: nothing to carry, so it must refuse.
        self.assertEqual(self._convert(OBSERVED_UNIT_TCR | (1 << 7)), 'invalid')
        # TG1 = 0b00 is the reserved granule encoding in the TCR_EL1 layout.
        self.assertEqual(self._convert(OBSERVED_UNIT_TCR & ~(3 << 30)), 'invalid')


class Mutations(unittest.TestCase):
    """Each entry seeds one textual change into src/sp11_devhe_convert.c, rebuilds the CLI against
    the mutated copy, and checks that it now disagrees with a fixed, known-good reference set of
    (input, expected-output) pairs taken from the unmutated function."""

    REFERENCE_INPUTS = [0x00000035b5103510, 0x00000035b5103510 | (1 << 37), 0x00000035b5103510 | (1 << 59),
                       0x00000035b5103510 | (0xF << 43),
                       0x00000035b5103510 | (1 << 7),           # EPD0 set: invalid
                       0x00000035b5103510 & ~(0x3 << 30)]        # TG1 = 0b00: invalid

    @classmethod
    def setUpClass(cls):
        if not have('clang'):
            raise unittest.SkipTest('clang not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.source_path = ROOT / 'src/sp11_devhe_convert.c'
        cls.source_text = cls.source_path.read_text(encoding='utf-8')
        cls.reference = cls._run_cli(cls, None, cls.REFERENCE_INPUTS)

    def _run_cli(self, mutated_text, values):
        if mutated_text is None:
            binary = BUILD / 'cli-reference'
            done = run(['clang'] + SANITIZE + ['tools/sp11_devhe_cli.c', '-o', str(binary)])
        else:
            workdir = BUILD / 'mutant_src'
            workdir.mkdir(parents=True, exist_ok=True)
            (workdir / 'sp11_devhe_convert.c').write_text(mutated_text, encoding='utf-8')
            (workdir / 'sp11_devhe_convert.h').write_text(
                (ROOT / 'src/sp11_devhe_convert.h').read_text(encoding='utf-8'), encoding='utf-8')
            cli_text = (ROOT / 'tools/sp11_devhe_cli.c').read_text(encoding='utf-8')
            cli_text = cli_text.replace('#include "../src/sp11_devhe_convert.c"',
                                        f'#include "{workdir / "sp11_devhe_convert.c"}"')
            cli_src = workdir / 'sp11_devhe_cli.c'
            cli_src.write_text(cli_text, encoding='utf-8')
            binary = BUILD / 'cli-mutant'
            done = run(['clang'] + SANITIZE + [str(cli_src), '-o', str(binary)])
        assert done.returncode == 0, done.stderr
        text = '\n'.join(f'{v:x}' for v in values) + '\n'
        result = subprocess.run([str(binary)], input=text, env=ENV, capture_output=True, text=True, cwd=ROOT)
        return result.stdout.splitlines()

    def _assert_mutation_changes_output(self, name, needle, replacement):
        self.assertIn(needle, self.source_text, f'mutation target for {name!r} not found (source changed?)')
        mutated = self.source_text.replace(needle, replacement, 1)
        out = self._run_cli(mutated, self.REFERENCE_INPUTS)
        self.assertNotEqual(out, self.reference, f'mutation {name!r} did not change any reference output')

    def test_drop_epd0_refusal(self):
        self._assert_mutation_changes_output(
            'drop EPD0 refusal',
            "if (!tcr_vhe_image_valid(vhe) || (vhe & SP11_DEVHE_BIT(7)))",
            "if (!tcr_vhe_image_valid(vhe) || 0)")

    def test_drop_tg1_check(self):
        self._assert_mutation_changes_output(
            'drop TG1 reserved-encoding check',
            "if (!((v >> 30) & 3)) return 0;",
            "if (0) return 0;")

    def test_wrong_ips_shift(self):
        self._assert_mutation_changes_output(
            'off-by-one the IPS->PS shift',
            "o |= ((vhe >> 32) & 7) << 16;  /* IPS -> PS */",
            "o |= ((vhe >> 32) & 7) << 17;  /* IPS -> PS */")

    def test_wrong_res1_bit(self):
        self._assert_mutation_changes_output(
            'drop one RES1 bit',
            "uint64_t o = SP11_DEVHE_BIT(31) | SP11_DEVHE_BIT(23); /* RES1 in the non-VHE layout */",
            "uint64_t o = SP11_DEVHE_BIT(31); /* RES1 in the non-VHE layout */")


# The el2m_op mnemonics TransSequence cares about, in the register names trans.s actually writes.
# VBAR_EL2/TPIDR_EL0/DAIF are written by both the gated and plain paths unchanged by this patch and
# are not among the nine registers the host model tracks (docs/SP11-EL2-ENTRY.md section 2.11);
# they, and their own per-write ISBs (every `ld_sys` expands to ldr+msr+isb), are filtered out of
# both the disassembly and the model-derived sequence below so the comparison is of only the
# registers and barriers the de-VHE transition itself is about.
TRACKED_MSR_REGS = {'hcr_el2', 'cnthctl_el2', 'cptr_el2', 'cpacr_el1', 'ttbr0_el2', 'tcr_el2', 'mair_el2',
                    'sctlr_el2'}
UNTRACKED_MSR_REGS = {'vbar_el2', 'tpidr_el0', 'daif'}

# This patch's own trans.s, read after the last real disassembly taken while developing it
# (`aarch64-elf-as --defsym SLBOUNCE_SP11_DEVHE=1 -c src/trans.s` then objdump, both reproduced by
# this test itself below): the exact filtered op order tb_longjmp's SP11_DEVHE branch must have.
# This is also, independently, el2m_seq_slbounce_devhe's own ops (compat/el2-entry-model.c) with
# its leading MSR(SCTLR,0),ISB (that is tb_entry's own, separate-function SCTLR clear, not
# anything in tb_longjmp) and its MSR(VBAR,...),ISB (untracked, see above) dropped -- the
# CrossLanguage-style check below reads that real array's source text and asserts exactly that.
EXPECTED_ON = ['TLBI', 'DSB', 'TLBI', ('MSR', 'hcr_el2'), 'ISB', ('MSR', 'cnthctl_el2'),
              ('MSR', 'cptr_el2'), ('MSR', 'ttbr0_el2'), ('MSR', 'tcr_el2'), ('MSR', 'mair_el2'),
              'DSB', 'TLBI', 'DSB', 'ISB', ('MSR', 'sctlr_el2'), 'ISB']
# The unmodified (gate-off) sequence, same filtering, for a regression check that this patch has
# not altered the VHE path's own register order (the A16/default build is not this patch's to
# change). cpacr_el1 here is upstream's own `ld_sys cpacr_el1, #120` (docs/SP11-Q1N1-BOOT-
# STUDY.md section 5.1: under HCR_EL2.E2H=1 this name reaches the real CPTR_EL2 hardware; the
# mnemonic the assembler emits is still literally "cpacr_el1", which is why it is a separate
# token from cptr_el2 in TRACKED_MSR_REGS above, not reused for both paths).
EXPECTED_OFF = ['TLBI', 'DSB', 'TLBI', ('MSR', 'hcr_el2'), 'ISB', ('MSR', 'cnthctl_el2'),
               ('MSR', 'cpacr_el1'), 'ISB', ('MSR', 'ttbr0_el2'), 'ISB', ('MSR', 'tcr_el2'), 'ISB',
               ('MSR', 'mair_el2'), 'ISB', 'DSB', 'ISB', ('MSR', 'sctlr_el2'), 'ISB']

MODEL_REG_NAME = {'HCR': 'hcr_el2', 'CNTHCTL': 'cnthctl_el2', 'CPTR': 'cptr_el2', 'TTBR0': 'ttbr0_el2',
                  'TCR': 'tcr_el2', 'MAIR': 'mair_el2', 'SCTLR': 'sctlr_el2', 'VBAR': 'vbar_el2'}


def _parse_model_ops(c_text, array_name):
    """A deliberately small parser for one `static const struct el2m_op NAME[] = { ... };` array
    in compat/el2-entry-model.c: enough to recover the MSR(EL2M_REG, ...)/TLBI/DSB/ISB token
    sequence (in trans.s's own register-name spelling, via MODEL_REG_NAME), not a general C
    parser. Raises AssertionError with a clear message if the array is not found, so a renamed or
    restructured model fails this test loudly instead of silently skipping the cross-check."""
    m = re.search(r'static const struct el2m_op ' + re.escape(array_name) + r'\[\]\s*=\s*\{(.*?)\n\};',
                 c_text, re.S)
    assert m, f'{array_name} not found in compat/el2-entry-model.c (has it been renamed?)'
    body = m.group(1)
    ops = []
    for piece in body.split(','):
        piece = piece.strip()
        if not piece:
            continue
        if piece in ('TLBI', 'DSB', 'ISB'):
            ops.append(piece)
            continue
        msr = re.search(r'MSR\(\s*EL2M_(\w+)', piece)
        if msr:
            reg = MODEL_REG_NAME.get(msr.group(1))
            assert reg, f'unmapped model register EL2M_{msr.group(1)} in {array_name}; add it to MODEL_REG_NAME'
            ops.append(('MSR', reg))
    return ops


def _drop_untracked(ops):
    """Removes every untracked MSR (UNTRACKED_MSR_REGS) together with the single ISB immediately
    following it -- each is a `ld_sys` expansion (ldr; msr; isb) or this file's own model entry,
    one ISB per write, not a free-standing barrier -- so dropping the write without its own ISB
    would misalign every later index against EXPECTED_ON/EXPECTED_OFF."""
    result, i = [], 0
    while i < len(ops):
        op = ops[i]
        if isinstance(op, tuple) and op[1] in UNTRACKED_MSR_REGS:
            i += 1
            if i < len(ops) and ops[i] == 'ISB':
                i += 1
            continue
        result.append(op)
        i += 1
    return result


class TransSequence(unittest.TestCase):
    """Assembles the real src/trans.s (not a copy) and checks tb_longjmp's instruction order."""

    @classmethod
    def setUpClass(cls):
        cls.as_ = shutil.which('aarch64-elf-as')
        cls.objdump = shutil.which('aarch64-elf-objdump')
        if not (cls.as_ and cls.objdump):
            raise unittest.SkipTest('aarch64-elf-as/objdump not installed')
        BUILD.mkdir(parents=True, exist_ok=True)

    def _sequence(self, defsym, source_path=None):
        src = source_path or (ROOT / 'src/trans.s')
        obj = BUILD / (('trans-on' if defsym else 'trans-off') +
                       ('' if source_path is None else '-mutant') + '.o')
        cmd = [self.as_]
        if defsym:
            cmd += ['--defsym', 'SLBOUNCE_SP11_DEVHE=1']
        cmd += ['-c', str(src), '-o', str(obj)]
        done = run(cmd)
        self.assertEqual(done.returncode, 0, done.stderr)
        done = run([self.objdump, '-d', '--no-show-raw-insn', str(obj)])
        self.assertEqual(done.returncode, 0, done.stderr)
        lines = done.stdout.splitlines()
        start = next(i for i, l in enumerate(lines) if '<tb_longjmp>:' in l)
        ops = []
        for l in lines[start + 1:]:
            if l.strip() == '' and ops:
                break
            t = l.split('\t')
            mnem = t[1].strip() if len(t) > 1 else ''
            if mnem == 'tlbi':
                ops.append('TLBI')
            elif mnem == 'dsb':
                ops.append('DSB')
            elif mnem == 'isb':
                ops.append('ISB')
            elif mnem == 'msr':
                reg = t[2].split(',')[0].strip() if len(t) > 2 else ''
                ops.append(('MSR', reg))
        return _drop_untracked(ops)

    def test_gated_sequence_matches_the_model(self):
        self.assertEqual(self._sequence(defsym=True), EXPECTED_ON)

    def test_default_sequence_is_the_unmodified_vhe_order(self):
        self.assertEqual(self._sequence(defsym=False), EXPECTED_OFF)

    def test_expected_on_matches_the_real_model_source(self):
        snap = os.environ.get('SNAPINTOSH_DIR')
        if not snap:
            self.skipTest('SNAPINTOSH_DIR not set: no Snapintosh checkout to cross-check against')
        model_path = Path(snap) / 'compat/el2-entry-model.c'
        self.assertTrue(model_path.is_file(), f'{model_path} missing')
        ops = _parse_model_ops(model_path.read_text(encoding='utf-8'), 'slbounce_devhe_ops')
        # Drop tb_entry's own SCTLR=0 clear (op[0:2]) and the untracked VBAR write (next MSR+ISB):
        # el2m_seq_slbounce_devhe models the whole restore, including the part this trans.s change
        # does not touch; TransSequence above checks only tb_longjmp's own SP11_DEVHE branch.
        self.assertEqual(ops[0], ('MSR', 'sctlr_el2'))
        self.assertEqual(ops[1], 'ISB')
        rest = ops[2:]
        self.assertEqual(rest[0], 'TLBI')
        self.assertEqual(rest[1], 'DSB')
        self.assertEqual(rest[2], 'TLBI')
        self.assertEqual(rest[3], ('MSR', 'vbar_el2'))
        self.assertEqual(rest[4], 'ISB')
        self.assertEqual(_drop_untracked(rest[5:]), EXPECTED_ON[3:])


class MutateTransSequence(unittest.TestCase):
    """Reintroduces the exact bug TransSequence was written to catch (the missing second
    TLBI+barriers after the register-write group) into a scratch copy of trans.s, and confirms
    TransSequence's own check fails against it -- the project's mutation-testing discipline
    applied to this new test itself, not only to sp11_devhe_convert.c's functional logic above."""

    @classmethod
    def setUpClass(cls):
        if not (shutil.which('aarch64-elf-as') and shutil.which('aarch64-elf-objdump')):
            raise unittest.SkipTest('aarch64-elf-as/objdump not installed')
        BUILD.mkdir(parents=True, exist_ok=True)

    def test_dropping_the_second_tlbi_is_caught(self):
        text = (ROOT / 'src/trans.s').read_text(encoding='utf-8')
        needle = "\tdsb\tish\n\ttlbi\talle2\n\tdsb\tish\n\tisb\n.else"
        self.assertIn(needle, text, 'mutation target not found (trans.s restructured?)')
        mutated = text.replace(needle, '.else', 1)
        mutant_path = BUILD / 'trans-mutant-no-second-tlbi.s'
        mutant_path.write_text(mutated, encoding='utf-8')
        seq = TransSequence()
        seq.as_ = shutil.which('aarch64-elf-as')
        seq.objdump = shutil.which('aarch64-elf-objdump')
        observed = seq._sequence(defsym=True, source_path=mutant_path)
        self.assertNotEqual(observed, EXPECTED_ON, 'mutation (dropped second TLBI) was not caught')


def _link_with_map(out_dir, map_path, reorder=None):
    """Builds the DEVHE-gated slbounce.so under out_dir with the project's own Makefile and a link
    map next to it. `reorder`, if given, is a callable that rearranges the link line's argument list
    (used by the mutation below to put the converter back at the end, where it used to be)."""
    shutil.rmtree(out_dir, ignore_errors=True)
    args = ['make', f'OUT_DIR={out_dir}', 'CROSS_COMPILE=aarch64-elf-', 'SLBOUNCE_ALWAYS_SWITCH=1', 'DEBUG=1',
            'SLBOUNCE_SCOPED_SWEEP=1', 'SLBOUNCE_SP11_DEVHE=1', '-j4']
    so = out_dir / 'slbounce.so'
    env = dict(ENV)
    # LDFLAGS in the environment is appended to by the Makefile's own `LDFLAGS +=`, not replaced.
    env['LDFLAGS'] = f'-Wl,-Map,{map_path}'
    done = subprocess.run(args + [str(so)], cwd=ROOT, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    if reorder is None:
        return
    # Relink by hand with the same link line, rearranged, and a second map for the mutant.
    dry = subprocess.run(args + ['-n', '-B', str(so)], cwd=ROOT, env=ENV, capture_output=True, text=True).stdout
    line = next(l for l in dry.splitlines() if 'aarch64-elf-gcc' in l and f'-o {so}' in l)
    import shlex
    argv = [a for a in shlex.split(line) if a != '-s']
    argv = reorder(argv, out_dir)
    argv[argv.index('-o') + 1] = str(out_dir / 'mutant.so')
    argv.insert(1, f'-Wl,-Map,{map_path}')
    done = subprocess.run(argv, cwd=ROOT, env=ENV, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def _tb_window(map_text):
    """(window_start, window_end, converter_start, converter_end) from a GNU ld map, using the same
    definition sl.c gives tcblaunch: the pages from tb_entry's page, tb_size of them."""
    sl = (ROOT / 'src/sl.c').read_text(encoding='utf-8')
    pages = int(re.search(r'tb_size\s*=\s*4096\s*\*\s*(\d+)\s*;', sl).group(1))
    entry = int(re.search(r'^\s+0x([0-9a-f]+)\s+tb_entry\s*$', map_text, re.M).group(1), 16)
    conv_sym = int(re.search(r'^\s+0x([0-9a-f]+)\s+sp11_devhe_tcr_vhe_to_nvhe\s*$', map_text, re.M).group(1), 16)
    sect = re.search(r'^ \.text\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+\S*sp11_devhe_convert\.o\s*$', map_text, re.M)
    start, size = int(sect.group(1), 16), int(sect.group(2), 16)
    assert start <= conv_sym < start + size
    window_start = entry & ~0xfff
    return window_start, window_start + 4096 * pages, start, start + size


class TbWindow(unittest.TestCase):
    """trans.s calls sp11_devhe_tcr_vhe_to_nvhe from tb_longjmp, with the MMU off, after tcblaunch has
    handed EL2 back. sl.c defines the transition-back image as the pages starting at tb_entry's page
    (tb_virt = tb_entry & ~0xfff, tb_size = 2 pages), and upstream's tb_entry..tb_longjmp lies wholly
    inside it. The converter is a new callee, so it has to lie inside it too: a call out of the window
    would be the first instruction fetch from an image page that upstream's design never relies on for
    this path. The Makefile links the converter straight after trans.o for exactly this reason; this
    reads the link map and fails if that ever stops being true."""

    @classmethod
    def setUpClass(cls):
        if not (have('aarch64-elf-gcc') and have('make')):
            raise unittest.SkipTest('aarch64-elf-gcc/make not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.map = BUILD / 'tb-window.map'
        _link_with_map(BUILD / 'tb-window', cls.map)
        cls.text = cls.map.read_text(encoding='utf-8', errors='replace')

    def test_the_converter_lies_inside_the_transition_back_image(self):
        lo, hi, start, end = _tb_window(self.text)
        self.assertTrue(lo <= start and end <= hi, f'converter {start:#x}..{end:#x} outside TB window {lo:#x}..{hi:#x}')

    def test_all_of_trans_o_lies_inside_the_window_too(self):
        lo, hi, _, _ = _tb_window(self.text)
        sect = re.search(r'^ \.text\s+0x([0-9a-f]+)\s+0x([0-9a-f]+)\s+\S*src/trans\.o\s*$', self.text, re.M)
        start, size = int(sect.group(1), 16), int(sect.group(2), 16)
        self.assertTrue(lo <= start and start + size <= hi)

    def test_a_far_placement_is_caught(self):
        """The old order (converter after libfdt) must fail the same check, or the check proves nothing."""
        def to_the_end(argv, out_dir):
            conv = str(out_dir / 'src/sp11_devhe_convert.o')
            argv = [a for a in argv if a != conv]
            # After the last object, before the libraries and the linker script (-lefi ... -T script).
            last_obj = max(i for i, a in enumerate(argv) if a.endswith('.o'))
            argv.insert(last_obj + 1, conv)
            return argv

        mutant_map = BUILD / 'tb-window-mutant.map'
        _link_with_map(BUILD / 'tb-window-mutant', mutant_map, reorder=to_the_end)
        lo, hi, start, end = _tb_window(mutant_map.read_text(encoding='utf-8', errors='replace'))
        self.assertFalse(lo <= start and end <= hi, 'the mutant placement was not caught')


if __name__ == '__main__':
    unittest.main()
