#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 the Snapintosh project
"""Host tests for the SLBOUNCE_SCOPED_SWEEP validator (src/sp11_scoped_sweep.c), ported from and
cross-checked against the Snapintosh repository's own host model (docs/SP11-Q1N1-BOOT-STUDY.md
section 4, tools/sp11_ebs_sweep_model.py / tools/test-sp11-ebs-sweep-model.py).

    python3 tools/test-sp11-scoped-sweep.py

Runs, under ASan+UBSan:
  * tools/test-sp11-scoped-sweep.c, the real implementation's own 14 functional checks (ported one
    for one from the Python oracle's 23 -- the difference is explained in the C file: this module
    validates one range per call with a bounded ops buffer, so some Python-only cases, such as a
    negative base the Python int type can hold and this C uint64_t cannot, do not apply, and the C
    module adds one the Python model has no reason to need, SP11_SWEEP_ERR_TOO_MANY_OPS);
  * CrossLanguage: the real C validator and the Snapintosh repository's Python oracle agree on
    several synthetic memory maps, via tools/sp11_sweep_harness.c (SKIP without SNAPINTOSH_DIR);
  * Mutations: six seeded bugs (the same six tools/test-sp11-ebs-sweep-model.py seeds into the
    Python model) are each confirmed to break at least one check, applied here to the C source and
    probed through the harness binary.

SNAPINTOSH_DIR: a Snapintosh checkout carrying docs/SP11-Q1N1-BOOT-STUDY.md and
tools/sp11_ebs_sweep_model.py (SKIP the CrossLanguage class without it). Nothing is written outside
build/. Hardware: NOT RUN. Evidence: EXPERIMENTAL (host model only)."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'sp11-scoped-sweep-test'
SANITIZE = ['-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-Wall', '-Wextra', '-Werror']
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, **kw)


def have(tool):
    return shutil.which(tool) is not None


class Native(unittest.TestCase):
    """The real validator's own 14 functional checks (tools/test-sp11-scoped-sweep.c)."""

    @classmethod
    def setUpClass(cls):
        if not have('clang'):
            raise unittest.SkipTest('clang not installed')
        BUILD.mkdir(parents=True, exist_ok=True)

    def test_the_real_implementation_passes_its_own_checks(self):
        binary = BUILD / 'test-sp11-scoped-sweep'
        done = run(['clang'] + SANITIZE + ['tools/test-sp11-scoped-sweep.c', '-o', str(binary)])
        self.assertEqual(done.returncode, 0, done.stderr)
        done = run([str(binary)])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('PASS', done.stdout)


def build_harness(build_dir, source_overrides=None):
    """Builds tools/sp11_sweep_harness.c, optionally with ../src/sp11_scoped_sweep.c replaced by a
    mutated copy (source_overrides: {'sp11_scoped_sweep.c': text})."""
    workdir = build_dir / 'mutant_src'
    if source_overrides:
        workdir.mkdir(parents=True, exist_ok=True)
        for name, text in source_overrides.items():
            (workdir / name).write_text(text, encoding='utf-8')
        harness_text = (ROOT / 'tools/sp11_sweep_harness.c').read_text(encoding='utf-8')
        harness_text = harness_text.replace('#include "../src/sp11_scoped_sweep.c"',
                                            f'#include "{workdir / "sp11_scoped_sweep.c"}"')
        harness_src = workdir / 'sp11_sweep_harness.c'
        harness_src.write_text(harness_text, encoding='utf-8')
    else:
        harness_src = ROOT / 'tools/sp11_sweep_harness.c'
    binary = build_dir / ('harness-mutant' if source_overrides else 'harness')
    # -Isrc: the mutated copy's own #include "sp11_scoped_sweep.h" still resolves to the real,
    # unmutated header (only sp11_scoped_sweep.c itself is replaced).
    done = run(['clang'] + SANITIZE + ['-I', str(ROOT / 'src'), str(harness_src), '-o', str(binary)])
    return done, binary


def harness_query(binary, descriptors, base, size, chunk_cap):
    """descriptors: iterable of (type, attribute, base, pages). Returns ('OK', [(base, size), ...])
    or ('ERR', status_name)."""
    lines = [f'D {t:x} {a:x} {b:x} {p:x}' for t, a, b, p in descriptors]
    lines.append(f'R {base:x} {size:x} {chunk_cap}')
    done = subprocess.run([str(binary)], input='\n'.join(lines) + '\n', env=ENV,
                          capture_output=True, text=True, cwd=ROOT)
    out = done.stdout.splitlines()
    if not out:
        raise RuntimeError(f'harness produced no output; stderr={done.stderr!r}')
    if out[0].startswith('ERR'):
        return 'ERR', out[0].split(' ', 1)[1]
    n = int(out[0].split()[1])
    ops = [tuple(int(x, 16) for x in line.split()) for line in out[1:1 + n]]
    return 'OK', ops


# bounce_main.c:124-130's seven swept Type values, named identically to the harness/model.
LOADER_CODE, LOADER_DATA, BS_CODE, BS_DATA, RT_CODE, RT_DATA = 1, 2, 3, 4, 5, 6
ACPI_RECLAIM, CONVENTIONAL = 9, 7
WB = 1 << 3


class CrossLanguage(unittest.TestCase):
    """src/sp11_scoped_sweep.c (through tools/sp11_sweep_harness.c) and the Snapintosh
    repository's own tools/sp11_ebs_sweep_model.py must agree on the same synthetic memory maps."""

    @classmethod
    def setUpClass(cls):
        cls.snap = None
        for c in (os.environ.get('SNAPINTOSH_DIR'),):
            if c and (Path(c) / 'tools/sp11_ebs_sweep_model.py').is_file():
                cls.snap = Path(c)
                break
        cls.have_py = cls.snap is not None
        cls.have_c = have('clang')
        BUILD.mkdir(parents=True, exist_ok=True)
        if cls.have_c:
            done, binary = build_harness(BUILD)
            cls.harness_ok = done.returncode == 0
            cls.harness_err = done.stderr
            cls.binary = binary
        else:
            cls.harness_ok = False

    def setUp(self):
        if not self.have_py:
            self.skipTest('SNAPINTOSH_DIR not set: no Snapintosh checkout to cross-check against')
        if not self.have_c:
            self.skipTest('clang not installed')
        self.assertTrue(self.harness_ok, self.harness_err)
        sys.path.insert(0, str(self.snap / 'tools'))
        import sp11_ebs_sweep_model as m
        self.m = m

    def _compare(self, descriptors, declared_base, declared_size, chunk_cap):
        m = self.m
        model_descs = [m.Descriptor(t, base=b, pages=p, attribute=a) for t, a, b, p in descriptors]
        c_status, c_result = harness_query(self.binary, descriptors, declared_base, declared_size, chunk_cap)
        try:
            py_ops = m.scoped_sweep(model_descs, [(declared_base, declared_size)], chunk_cap=chunk_cap)
            py_status = 'OK'
        except m.SweepRefused:
            py_status = 'ERR'
            py_ops = None
        self.assertEqual(c_status, py_status, f'C said {c_status} ({c_result!r}), Python said {py_status}')
        if py_status == 'OK':
            self.assertEqual(sorted(c_result), sorted(py_ops))

    def test_agree_on_a_clean_coverage_case(self):
        self._compare([(LOADER_DATA, WB, 0x90000000, (64 << 20) // 4096)], 0x90000000, 64 << 20, 1 << 20)

    def test_agree_on_the_issue_13_shaped_refusal(self):
        # Declaring the 0xA7F19000 range itself, with no attribute set, must be refused by both.
        self._compare([(BS_DATA, 0, 0xA7F19000, 231)], 0xA7F19000, 231 * 4096, 1 << 20)

    def test_agree_on_a_boundary_straddle_refusal(self):
        self._compare([(LOADER_DATA, WB, 0x1000, 1), (LOADER_DATA, WB, 0x2000, 1)], 0x1800, 0x1000, 1 << 20)

    def test_agree_on_a_non_swept_type_refusal(self):
        self._compare([(CONVENTIONAL, WB, 0x6000, 1)], 0x6000, 0x1000, 1 << 20)

    def test_agree_on_chunking_a_large_range(self):
        pages = (8 << 20) // 4096
        self._compare([(LOADER_DATA, WB, 0x90000000, pages)], 0x90000000, 8 << 20, 1 << 20)


class Mutations(unittest.TestCase):
    """Each entry seeds one textual change into src/sp11_scoped_sweep.c, rebuilds the harness
    against the mutated copy (the original file is never written to), and checks that the mutation
    is caught -- mirroring tools/test-sp11-ebs-sweep-model.py's own six seeded bugs in the Python
    model exactly (see the comment above each case for which one it mirrors)."""

    @classmethod
    def setUpClass(cls):
        if not have('clang'):
            raise unittest.SkipTest('clang not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.source_path = ROOT / 'src/sp11_scoped_sweep.c'
        cls.source_text = cls.source_path.read_text(encoding='utf-8')

    def _mutant_binary(self, name, needle, replacement):
        self.assertIn(needle, self.source_text, 'mutation target text not found (source changed?)')
        mutated = self.source_text.replace(needle, replacement, 1)
        build_dir = BUILD / f'mutant-{name}'
        done, binary = build_harness(build_dir, {'sp11_scoped_sweep.c': mutated})
        return done, binary

    def test_drop_the_attribute_filter(self):
        # mirrors "drop the attribute filter" in tools/test-sp11-ebs-sweep-model.py
        done, binary = self._mutant_binary(
            'no-attr-filter',
            "if (!(d->attribute & SP11_SWEEP_ATTRIBUTE_WB)) return SP11_SWEEP_ERR_NOT_WRITEBACK;",
            "if (0) return SP11_SWEEP_ERR_NOT_WRITEBACK;")
        self.assertEqual(done.returncode, 0, done.stderr)
        status, _ = harness_query(binary, [(BS_DATA, 0, 0x5000, 1)], 0x5000, 0x1000, 1 << 20)
        self.assertEqual(status, 'OK', 'mutation did not break anything: still refused non-WB memory')

    def test_accept_more_than_one_covering_descriptor(self):
        # mirrors "accept more than one covering descriptor". Two descriptors that both genuinely
        # contain the declared range (not a boundary straddle, where neither contains it alone and
        # matches is 0 either way): the mutant's relaxed bound (> 100) lets matches == 2 through.
        done, binary = self._mutant_binary(
            'multi-cover',
            "if (matches != 1) return SP11_SWEEP_ERR_NOT_CONTAINED;",
            "if (matches > 100) return SP11_SWEEP_ERR_NOT_CONTAINED;")
        self.assertEqual(done.returncode, 0, done.stderr)
        status, _ = harness_query(binary, [(LOADER_DATA, WB, 0x1000, 2), (LOADER_DATA, WB, 0x1000, 4)],
                                  0x1000, 0x1000, 1 << 20)
        self.assertEqual(status, 'OK', 'mutation did not break anything: still refused the double coverage')

    def test_stop_refusing_non_swept_types(self):
        # mirrors "stop refusing non-swept types"
        done, binary = self._mutant_binary(
            'no-type-filter',
            "if (!sp11_sweep_type_is_swept(d->type)) return SP11_SWEEP_ERR_WRONG_TYPE;",
            "if (0) return SP11_SWEEP_ERR_WRONG_TYPE;")
        self.assertEqual(done.returncode, 0, done.stderr)
        status, _ = harness_query(binary, [(CONVENTIONAL, WB, 0x6000, 1)], 0x6000, 0x1000, 1 << 20)
        self.assertEqual(status, 'OK', 'mutation did not break anything: still refused a non-swept type')

    def test_let_chunk_cap_be_zero(self):
        # mirrors "let chunk_cap be zero". A chunk_cap of 0 makes no progress per loop iteration
        # (remaining never decreases), so even with this check gone the module's own separate,
        # unrelated max_ops bound in the chunking loop below still refuses it eventually -- just
        # with a different, less diagnostic status (SP11_SWEEP_ERR_TOO_MANY_OPS instead of
        # SP11_SWEEP_ERR_CHUNK_CAP). The probe checks for exactly that regression (the wrong reason
        # reported, not a progression to OK, since OK is architecturally unreachable here either
        # way) -- a real, demonstrable loss of diagnosability this mutation causes, found by trying
        # to write this probe the same way the other five are written and discovering it could not
        # reach OK at all (see this patch's REPORT.md).
        done, binary = self._mutant_binary(
            'chunk-cap-zero',
            "if (chunk_cap == 0) return SP11_SWEEP_ERR_CHUNK_CAP;",
            "if (chunk_cap > 0xffffffffu) return SP11_SWEEP_ERR_CHUNK_CAP;")
        self.assertEqual(done.returncode, 0, done.stderr)
        status, detail = harness_query(binary, [(LOADER_DATA, WB, 0x1000, 1)], 0x1000, 0x1000, 0)
        self.assertEqual(status, 'ERR')
        self.assertNotEqual(detail, 'CHUNK_CAP',
                            'mutation did not break anything: chunk_cap=0 was still reported as CHUNK_CAP')
        self.assertEqual(detail, 'TOO_MANY_OPS', 'unexpected refusal reason for chunk_cap=0 under the mutant')

    def test_widen_classic_equivalent_to_ignore_the_type_filter(self):
        # mirrors "widen classic_sweep to ignore the type filter": sp11_sweep_type_is_swept itself.
        done, binary = self._mutant_binary(
            'classic-widen',
            "default:\n        return 0;\n    }\n}",
            "default:\n        return 1;\n    }\n}")
        self.assertEqual(done.returncode, 0, done.stderr)
        # A conventional-memory descriptor, declared, must now be accepted by the widened type check
        # (the attribute check still passes since it is WB), proving the mutation changed something.
        status, _ = harness_query(binary, [(CONVENTIONAL, WB, 0x6000, 1)], 0x6000, 0x1000, 1 << 20)
        self.assertEqual(status, 'OK', 'mutation did not widen the type filter')

    def test_off_by_one_the_chunk_size(self):
        # mirrors "off-by-one the chunk size"
        done, binary = self._mutant_binary(
            'chunk-off-by-one',
            "uint64_t take = remaining < chunk_cap ? remaining : chunk_cap;",
            "uint64_t take = remaining < chunk_cap ? remaining : chunk_cap + 1;")
        self.assertEqual(done.returncode, 0, done.stderr)
        status, ops = harness_query(binary, [(LOADER_DATA, WB, 0x90000000, (8 << 20) // 4096)],
                                    0x90000000, 8 << 20, 1 << 20)
        self.assertEqual(status, 'OK')
        self.assertTrue(any(size > (1 << 20) for _, size in ops), 'mutation did not exceed the chunk cap')


if __name__ == '__main__':
    unittest.main()
