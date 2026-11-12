#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 the Snapintosh project
"""Host tests for the SLBOUNCE_SP11_DEVHE progress markers: src/sp11_marker_surface.{h,c} (where
the markers may be drawn), the hook in src/bounce_main.c that arms them, and the marker code in
src/trans.s that draws them with the MMU off.

    python3 tools/test-sp11-marker-surface.py

Runs:
  * Native: tools/test-sp11-marker-surface.c's own checks on the real sp11_marker_plan(), under
    ASan and UBSan;
  * Oracle: the same function against an independent Python implementation written from the rules
    in sp11_marker_surface.h, on thousands of generated framebuffer and memory-map cases (every
    verdict must be reached, or the comparison proves little);
  * RecordedUnit: the real function on the memory map and GOP facts the Surface Pro 11 unit reported
    (OBSERVED, three probe runs; SKIP without SNAPINTOSH_DIR naming a checkout that has them), and
    on copies of that map altered so the framebuffer is no longer safe;
  * Mutations: seeded bugs in sp11_marker_surface.c, each of which must be caught;
  * MarkerCode: assembles the real src/trans.s with aarch64-elf-as in each gate combination and checks
    the marker code in the disassembly (the exact squares, their place in the register sequence, no
    store while a register write is unsynchronized, no registers or stack it may not touch; the dry
    run draws and halts before any de-VHE configuration write; de-VHE without markers has no marker
    code at all), plus seeded mutants of trans.s that must each be caught, and the Makefile's refusal of
    gate combinations that make no sense;
  * HookSource: the arming hook calls no service that allocates (any allocation between q1n1's last
    GetMemoryMap and ExitBootServices invalidates the map key), and runs after the sweep and before
    the real ExitBootServices;
  * Agreement: the constants repeated in C, assembler and the buffer layout, and, with
    Q1N1_SP11_DIR naming a q1n1 checkout, the framebuffer type mask q1n1 uses.

Hardware: NOT RUN. Nothing here executes the trampoline; the markers' visibility on the unit, and
whether a store to the framebuffer with the MMU off is harmless there, are unmeasured. Evidence:
EXPERIMENTAL (host model and a structural disassembly check only)."""
import os
from pathlib import Path
import random
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / 'build' / 'sp11-marker-test'
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
SANITIZE = ['-std=gnu11', '-O1', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
            '-Wall', '-Wextra', '-Werror']

OK, BAD_MODE, BAD_MAP, NOT_IN_MAP = 0, 1, 2, 3
MAX64 = (1 << 64) - 1
SLOTS, SIDE, STEP, MARGIN = 8, 32, 48, 24
MIN_W, MIN_H = 2 * MARGIN + SLOTS * STEP, 2 * MARGIN + SIDE
ALLOWED_TYPES = {0, 4, 6, 11, 12}


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, env=ENV, capture_output=True, text=True, **kw)


def have(tool):
    return shutil.which(tool) is not None


def compile_driver(out, source=None, sanitize=True):
    cmd = ['clang'] + (SANITIZE if sanitize else ['-std=gnu11', '-O1', '-g']) + [f'-I{ROOT / "src"}']
    if source is not None:
        cmd.append(f'-DSP11_MARKER_SOURCE="{source}"')
    cmd += ['tools/test-sp11-marker-surface.c', '-o', str(out)]
    done = run(cmd)
    assert done.returncode == 0, done.stderr
    return out


def desc(typ, base, pages, size=48):
    d = bytearray(size)
    d[0:4] = typ.to_bytes(4, 'little')
    d[8:16] = (base % (1 << 64)).to_bytes(8, 'little')
    d[24:32] = max(pages, 0).to_bytes(8, 'little')
    return bytes(d)


def oracle(desc_size, base, size, w, h, ppsl, fmt, raw):
    """sp11_marker_plan, written separately from the rules in sp11_marker_surface.h.
    Returns (status, origin, stride)."""
    if fmt > 1 or base == 0 or base % 4:
        return BAD_MODE, 0, 0
    if not (MIN_W <= w <= 16384 and MIN_H <= h <= 16384) or not (w <= ppsl <= 65536):
        return BAD_MODE, 0, 0
    stride = ppsl * 4
    extent = (h - 1) * stride + w * 4
    if size < extent or base + extent > MAX64:
        return BAD_MODE, 0, 0
    if raw is None or not raw or desc_size < 40 or len(raw) % desc_size:
        return BAD_MAP, 0, 0
    end_fb = base + extent
    hits = []
    for off in range(0, len(raw), desc_size):
        typ = int.from_bytes(raw[off:off + 4], 'little')
        dbase = int.from_bytes(raw[off + 8:off + 16], 'little')
        pages = int.from_bytes(raw[off + 24:off + 32], 'little')
        dend = min(dbase + pages * 4096, MAX64)
        if dbase < end_fb and dend > base:
            hits.append((typ, dbase, dend))
    if len(hits) != 1:
        return NOT_IN_MAP, 0, 0
    typ, dbase, dend = hits[0]
    if not (dbase <= base and end_fb <= dend) or typ not in ALLOWED_TYPES:
        return NOT_IN_MAP, 0, 0
    return OK, base + MARGIN * stride + (w - MARGIN - SLOTS * STEP) * 4, stride


def batch(binary, cases):
    lines = []
    for c in cases:
        dsz, base, size, w, h, ppsl, fmt, raw = c
        lines.append(f'{dsz:x} {base:x} {size:x} {w:x} {h:x} {ppsl:x} {fmt:x} ' + ('-' if raw is None else raw.hex()))
    done = subprocess.run([str(binary), '--batch'], input='\n'.join(lines) + '\n', env=ENV, capture_output=True,
                          text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    out = []
    for line in done.stdout.splitlines():
        st, origin, stride = (int(x, 16) for x in line.split())
        out.append((st, origin, stride))
    return out


UNIT_FB = (0xE4800000, 0x1518000, 2880, 1920, 2880, 1)


def make_cases(n, seed):
    rng = random.Random(seed)
    cases = []
    for _ in range(n):
        base, size, w, h, ppsl, fmt = UNIT_FB
        roll = rng.random()
        if roll < 0.15:
            w = rng.choice([MIN_W - 1, MIN_W, MIN_W + 1, 1000, 1920, 16384, 16385, 0])
            ppsl = w + rng.choice([0, 0, 1, -1, 64])
        elif roll < 0.25:
            h = rng.choice([MIN_H - 1, MIN_H, 1080, 16384, 16385, 0])
        elif roll < 0.32:
            fmt = rng.choice([0, 1, 2, 3, 255])
        elif roll < 0.40:
            base = rng.choice([0, base + 1, base + 2, base + 4, MAX64 - 0xfff, MAX64 - 3, 4096])
        elif roll < 0.48:
            ppsl = rng.choice([w - 1, w, w + 7, 65536, 65537])
        stride = max(ppsl, 0) * 4
        extent = max(h - 1, 0) * stride + w * 4
        size = rng.choice([extent, extent, extent + 4096, extent - 4, extent - 1, 0])
        dsz = rng.choice([48, 48, 48, 40, 56, 64, 39, 0])
        typ = rng.choice([0, 0, 4, 6, 11, 12, 1, 2, 5, 7, 14, 32, 0xffffffff])
        fb_pages = (extent + 4095) // 4096
        shape = rng.random()
        descs = []
        if shape < 0.55:
            start = base & ~0xfff
            descs.append(desc(typ, start, fb_pages + rng.choice([0, 0, 1, 16]), max(dsz, 40)))
        elif shape < 0.70:
            cut = rng.randrange(1, max(fb_pages, 2))
            start = base & ~0xfff
            descs.append(desc(rng.choice([0, 4]), start, cut, max(dsz, 40)))
            descs.append(desc(rng.choice([0, 4, 7]), start + cut * 4096, fb_pages, max(dsz, 40)))
        elif shape < 0.80:
            descs.append(desc(typ, (base & ~0xfff) + rng.choice([-4096, 4096, 0]), fb_pages - 1, max(dsz, 40)))
        elif shape < 0.88:
            descs.append(desc(0, (base & ~0xfff) - 4096, 0xffffffffffffff, max(dsz, 40)))
        else:
            descs.append(desc(7, 0x80000000, 0x100, max(dsz, 40)))
        for _ in range(rng.randrange(0, 4)):
            descs.append(desc(rng.choice([0, 4, 7, 11]), rng.choice([0x80000000, base, base + 0x100000, 0xE0000000]),
                              rng.choice([1, 0x40, fb_pages]), max(dsz, 40)))
        rng.shuffle(descs)
        raw = b''.join(descs)
        mode = rng.random()
        if mode < 0.05:
            raw = None
        elif mode < 0.10:
            raw = raw[:-1] if raw else raw
        elif mode < 0.12:
            raw = b''
        cases.append((dsz, base, size, w, h, ppsl, fmt, raw))
    return cases


class Native(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have('clang'):
            raise unittest.SkipTest('clang not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.exe = compile_driver(BUILD / 'native')

    def test_the_real_implementation_passes_its_own_checks(self):
        done = run([str(self.exe)])
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('PASS', done.stdout)

    def test_agrees_with_the_independent_oracle(self):
        cases = make_cases(6000, 0x5011)
        got = batch(self.exe, cases)
        bad = [(c, g, oracle(*c)) for c, g in zip(cases, got) if g != oracle(*c)]
        self.assertEqual(bad[:3], [])
        seen = {oracle(*c)[0] for c in cases}
        self.assertEqual(seen, {OK, BAD_MODE, BAD_MAP, NOT_IN_MAP}, 'a verdict was never reached: the cases prove little')
        accepted = sum(1 for c in cases if oracle(*c)[0] == OK)
        self.assertGreater(accepted, 200)


class RecordedUnit(unittest.TestCase):
    """The probe's three recordings of the unit's memory map and GOP (git-ignored local data)."""

    @classmethod
    def setUpClass(cls):
        if not have('clang'):
            raise unittest.SkipTest('clang not installed')
        snap = os.environ.get('SNAPINTOSH_DIR')
        cls.runs = []
        if snap:
            for n in (1, 2, 3):
                d = Path(snap) / 'downloads/sp11' / f'x1-run0{n}'
                if (d / 'memmap.raw').is_file() and (d / 'summary.txt').is_file() and (d / 'memmap.txt').is_file():
                    cls.runs.append(d)
        if not cls.runs:
            raise unittest.SkipTest('SNAPINTOSH_DIR does not name a checkout with downloads/sp11/x1-run0N')
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.exe = compile_driver(BUILD / 'recorded')

    @staticmethod
    def _facts(d):
        kv = dict(line.split('=', 1) for line in (d / 'summary.txt').read_text().splitlines() if '=' in line
                  and not line.startswith('cfg='))
        header = (d / 'memmap.txt').read_text().splitlines()[1]
        dsz = int(re.search(r'descriptor_size=(\d+)', header).group(1))
        return (dsz, int(kv['gop_base'], 16), int(kv['gop_size'], 16), int(kv['gop_width']),
                int(kv['gop_height']), int(kv['gop_stride_pixels']), int(kv['gop_format'], 16))

    def test_the_units_framebuffer_is_accepted_on_every_recording(self):
        for d in self.runs:
            with self.subTest(run=d.name):
                dsz, base, size, w, h, ppsl, fmt = self._facts(d)
                raw = (d / 'memmap.raw').read_bytes()
                (got,) = batch(self.exe, [(dsz, base, size, w, h, ppsl, fmt, raw)])
                want = (OK, base + MARGIN * ppsl * 4 + (w - MARGIN - SLOTS * STEP) * 4, ppsl * 4)
                self.assertEqual(got, want)
                self.assertEqual(oracle(dsz, base, size, w, h, ppsl, fmt, raw), want)

    def test_the_same_map_refuses_a_framebuffer_that_is_no_longer_safe(self):
        for d in self.runs:
            with self.subTest(run=d.name):
                dsz, base, size, w, h, ppsl, fmt = self._facts(d)
                raw = bytearray((d / 'memmap.raw').read_bytes())
                index = next(o for o in range(0, len(raw), dsz) if int.from_bytes(raw[o + 8:o + 16], 'little') == base)
                self.assertEqual(int.from_bytes(raw[index:index + 4], 'little'), 0, 'the unit reports RESERVED')
                for name, edit in (('conventional', lambda r: r.__setitem__(slice(index, index + 4), (7).to_bytes(4, 'little'))),
                                   ('loader data', lambda r: r.__setitem__(slice(index, index + 4), (2).to_bytes(4, 'little'))),
                                   ('one page short', lambda r: r.__setitem__(
                                       slice(index + 24, index + 32),
                                       (int.from_bytes(r[index + 24:index + 32], 'little') - 0x1000).to_bytes(8, 'little')))):
                    altered = bytearray(raw)
                    edit(altered)
                    (got,) = batch(self.exe, [(dsz, base, size, w, h, ppsl, fmt, bytes(altered))])
                    self.assertEqual(got[0], NOT_IN_MAP, name)
                gone = bytes(raw[:index] + raw[index + dsz:])
                (got,) = batch(self.exe, [(dsz, base, size, w, h, ppsl, fmt, gone)])
                self.assertEqual(got[0], NOT_IN_MAP, 'descriptor removed')


class Mutations(unittest.TestCase):
    """Each seeded bug must be caught by the driver's own checks or by the oracle comparison."""

    MUTANTS = [
        ('accept any type', 'allowed = type < 32u && ((SP11_MARKER_FB_TYPES_MASK >> type) & 1u);', 'allowed = 1;'),
        ('accept several overlapping descriptors', 'if (overlapping != 1 || !contains || !allowed)',
         'if (overlapping < 1 || !contains || !allowed)'),
        ('accept a partial cover', 'contains = base <= fb->base && fb_end <= end;', 'contains = base <= fb->base;'),
        ('forget the alignment check', 'if (!fb->base || (fb->base & 3u))', 'if (!fb->base)'),
        ('wrong pixel size', 'uint64_t stride = (uint64_t)fb->pixels_per_scanline * 4u;',
         'uint64_t stride = (uint64_t)fb->pixels_per_scanline * 2u;'),
        ('origin one pixel off', 'out->origin = fb->base + (uint64_t)SP11_MARKER_MARGIN_PX * stride + x0 * 4u;',
         'out->origin = fb->base + (uint64_t)SP11_MARKER_MARGIN_PX * stride + x0 * 4u + 4u;'),
        ('forget the overflow check', 'if (fb->size_bytes < extent || fb->base > ~UINT64_C(0) - extent)',
         'if (fb->size_bytes < extent)'),
        ('wrap instead of saturate', 'uint64_t end = pages > (~UINT64_C(0) - base) / 4096u ? ~UINT64_C(0) : base + pages * 4096u;',
         'uint64_t end = base + pages * 4096u;'),
        ('accept the reserved pixel formats', 'if (fb->pixel_format > 1)', 'if (fb->pixel_format > 3)'),
        ('leave a stale strip on refusal', '\tout->origin = 0;\n\tout->stride_bytes = 0;\n\n\tif (fb->pixel_format',
         '\tif (fb->pixel_format'),
    ]

    @classmethod
    def setUpClass(cls):
        if not have('clang'):
            raise unittest.SkipTest('clang not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.text = (ROOT / 'src/sp11_marker_surface.c').read_text(encoding='utf-8')
        cls.cases = make_cases(4000, 0xbeef)

    def test_every_seeded_bug_is_caught(self):
        survivors = []
        for name, needle, replacement in self.MUTANTS:
            self.assertIn(needle, self.text, f'mutation target for {name!r} not found (source changed?)')
            work = BUILD / 'mutant_src'
            work.mkdir(parents=True, exist_ok=True)
            mutant = work / 'sp11_marker_surface.c'
            mutant.write_text(self.text.replace(needle, replacement, 1), encoding='utf-8')
            exe = compile_driver(BUILD / 'mutant', source=mutant, sanitize=False)
            caught = 'driver' if run([str(exe)]).returncode else None
            if caught is None:
                got = batch(exe, self.cases)
                caught = 'oracle' if any(g != oracle(*c) for c, g in zip(self.cases, got)) else None
            print(f'mutant | {caught or "SURVIVED":7} | {name}', file=sys.stderr)
            if caught is None:
                survivors.append(name)
        self.assertEqual(survivors, [])
        self.assertGreaterEqual(len(self.MUTANTS), 10)


# --------------------------------------------------------------------------------------------
# The assembled trampoline
# --------------------------------------------------------------------------------------------
# tb_longjmp's whole instruction order with the markers in place: the two ld_sys writes before the
# SP11 branch (each with its own isb), the markers and the register sequence the model checks, then
# the daif restore. Every marker follows an isb that follows the last system register write.
EXPECTED_MAIN_ORDER = ['TLBI', 'DSB', 'TLBI', 'msr vbar_el2', 'ISB', 'msr tpidr_el0', 'ISB',
                       'MARK1', 'msr hcr_el2', 'ISB', 'MARK2',
                       'msr cnthctl_el2', 'msr cptr_el2', 'msr ttbr0_el2', 'msr tcr_el2', 'msr mair_el2',
                       'DSB', 'TLBI', 'DSB', 'ISB', 'MARK3', 'msr sctlr_el2', 'ISB', 'msr daif', 'ISB']
EXPECTED_DRYRUN_ORDER = ['TLBI', 'DSB', 'TLBI', 'msr vbar_el2', 'ISB', 'msr tpidr_el0', 'ISB',
                         'MARK1', 'MARK2', 'MARK3', 'MARK4', 'msr sctlr_el2', 'ISB', 'msr daif', 'ISB']
EXPECTED_SLOTS = [(STEP * 4 * k, SIDE, SIDE) for k in (1, 2, 3)]
EXPECTED_DRYRUN_SLOTS = [(STEP * 4 * k, SIDE, SIDE) for k in (1, 2, 3, 4)]
DEVHE, MARKERS, DRYRUN = 'SLBOUNCE_SP11_DEVHE', 'SLBOUNCE_SP11_MARKERS', 'SLBOUNCE_SP11_MARKERS_DRYRUN'
SCRATCH = {'x23', 'x24', 'x25', 'x26', 'w26', 'x27'}


def parse_dump(text):
    """{symbol: [(address, mnemonic, operands, raw)]} from objdump -d --no-show-raw-insn. `operands`
    has any trailing comment and symbol annotation removed; `raw` keeps them."""
    blocks, current = {}, None
    for line in text.splitlines():
        m = re.match(r'^[0-9a-f]+ <([^>]+)>:$', line)
        if m:
            current = blocks.setdefault(m.group(1), [])
            continue
        m = re.match(r'^\s*([0-9a-f]+):\t(\S+)\t?(.*)$', line)
        if m and current is not None:
            raw = m.group(3).strip()
            current.append((int(m.group(1), 16), m.group(2), re.split(r'\s*(?://|<)', raw)[0].strip(), raw))
    return blocks


def marker_groups(block):
    """Each marker group runs from `cbz x21, T` up to, not including, the instruction at T."""
    groups, i = [], 0
    while i < len(block):
        _, mnem, ops, _ = block[i]
        if mnem == 'cbz' and ops.startswith('x21,'):
            target = int(ops.split(',')[1].split()[0], 16)
            j = i
            while j < len(block) and block[j][0] < target:
                j += 1
            groups.append(block[i:j])
            i = j
        else:
            i += 1
    return groups


def shape(group):
    """(offset bytes, rows, columns) a marker group fills, or None if it does not look like one."""
    def imm(prefix, mnem):
        found = [o for _, m, o, _ in group if m == mnem and o.startswith(prefix)]
        return int(found[0].split('#')[1], 0) if found else None
    values = (imm('x23, x21,', 'add'), imm('x24,', 'mov'), imm('x25,', 'mov'))
    return None if None in values else values


def analyze(source, gates=(DEVHE, MARKERS)):
    """Problems found in the marker code of the assembled `source` with the given assembler gates;
    [] means none. Without MARKERS there must be no marker code at all."""
    obj = BUILD / 'trans-analyze.o'
    cmd = ['aarch64-elf-as'] + [a for g in gates for a in ('--defsym', f'{g}=1')] + ['-c', str(source), '-o', str(obj)]
    done = run(cmd)
    if done.returncode:
        return [f'does not assemble: {done.stderr.strip()[:200]}']
    blocks = parse_dump(run(['aarch64-elf-objdump', '-d', '--no-show-raw-insn', str(obj)]).stdout)
    main, refused = blocks.get('tb_longjmp', []), blocks.get('sp11_tb_refused')
    problems = []
    if MARKERS not in gates:
        if any(m in ('str', 'stp') for _, m, _, _ in main):
            problems.append('a build without markers stores in tb_longjmp')
        if any(re.search(r'\bx2[1-7]\b|\bw26\b', o) for _, m, o, _ in main if m != 'ldp'):
            problems.append('a build without markers touches x21-x27')
        if refused is not None:
            problems.append('a build without markers has the refusal block')
        if any(m == 'dsb' and o == 'sy' for _, m, o, _ in main):
            problems.append('a build without markers has a dsb sy')
        return problems
    dry = DRYRUN in gates
    if dry and refused is not None:
        return ['the dry run must not assemble the refusal block']
    if not dry and refused is None:
        return ['sp11_tb_refused is missing']

    # The order of every barrier, TLBI, marker and the system register writes in tb_longjmp.
    groups = marker_groups(main)
    first = {g[0][0]: f'MARK{n}' for n, g in enumerate(groups, 1)}
    inside = {a for g in groups for a, _, _, _ in g}
    order = []
    for addr, mnem, ops, _ in main:
        if addr in first:
            order.append(first[addr])
        elif addr in inside:
            continue
        elif mnem == 'tlbi':
            order.append('TLBI')
        elif mnem == 'dsb' and ops == 'ish':
            order.append('DSB')
        elif mnem == 'isb':
            order.append('ISB')
        elif mnem == 'msr':
            order.append('msr ' + ops.split(',')[0].strip())
    want_order = EXPECTED_DRYRUN_ORDER if dry else EXPECTED_MAIN_ORDER
    if order != want_order:
        problems.append(f'instruction order differs from the documented one: {order}')
    if dry:
        # The dry run stops before any de-VHE configuration write (the TLBI and the VBAR_EL2 and
        # TPIDR_EL0 restores come first, in every build): nothing of the sequence may be written
        # before the halt, and the halt must be the instruction right after the last marker.
        before_halt = []
        for entry in main:
            if groups and entry[0] > groups[-1][-1][0]:
                break
            before_halt.append(entry)
        written = {o.split(',')[0].strip() for _, m, o, _ in before_halt if m == 'msr'}
        if written & {'hcr_el2', 'cnthctl_el2', 'cptr_el2', 'ttbr0_el2', 'tcr_el2', 'mair_el2', 'sctlr_el2'}:
            problems.append('the dry run writes a translation or EL2 control register before its halt')
        following = [e for e in main if groups and e[0] > groups[-1][-1][0]][:1]
        if not following or following[0][1] != 'b' or 'halt' not in following[0][3]:
            problems.append('the dry run does not halt right after its last marker')

    # The squares: slots 1 to 3 in tb_longjmp (slot 4 too in the dry run) and, outside the dry run, one
    # bar across every slot in the refusal block.
    want_slots = EXPECTED_DRYRUN_SLOTS if dry else EXPECTED_SLOTS
    if [shape(g) for g in groups] != want_slots:
        problems.append(f'slot shapes {[shape(g) for g in groups]}, expected {want_slots}')
    rgroups = marker_groups(refused) if refused else []
    if not dry and [shape(g) for g in rgroups] != [(0, SIDE, SLOTS * STEP)]:
        problems.append(f'refusal bar shape {[shape(g) for g in rgroups]}, expected {[(0, SIDE, SLOTS * STEP)]}')

    # Each group: one post-indexed 32-bit store, closed by dsb sy, touching only its scratch registers
    # and never the stack.
    for n, g in enumerate(groups + rgroups, 1):
        stores = [o for _, m, o, _ in g if m == 'str']
        if stores != ['w26, [x27], #4']:
            problems.append(f'group {n}: stores {stores}')
        if (g[-1][1], g[-1][2]) != ('dsb', 'sy'):
            problems.append(f'group {n} does not end with dsb sy')
        for _, m, o, _ in g:
            if m in ('cbz', 'b.ne', 'dsb', 'str'):
                continue
            if o.split(',')[0].strip() not in SCRATCH:
                problems.append(f'group {n}: {m} {o} writes outside the scratch registers')
            if re.search(r'\bsp\b', o):
                problems.append(f'group {n} uses the stack pointer')

    # No store while a system register write is unsynchronized. The refusal block is entered from
    # the middle of the sequence, so it starts with a write pending and must clear it first.
    for name, block, pending in (('tb_longjmp', main, False), ('sp11_tb_refused', refused or [], True)):
        for _, m, _, _ in block:
            if m == 'msr':
                pending = True
            elif m == 'isb':
                pending = False
            elif m in ('str', 'stp') and pending:
                problems.append(f'{name}: a store while a register write is unsynchronized')
                break
    return problems


class MarkerCode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (have('aarch64-elf-as') and have('aarch64-elf-objdump')):
            raise unittest.SkipTest('aarch64-elf-as/objdump not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.text = (ROOT / 'src/trans.s').read_text(encoding='utf-8')

    def test_the_marker_build_has_exactly_the_documented_markers(self):
        self.assertEqual(analyze(ROOT / 'src/trans.s'), [])

    def test_the_dry_run_draws_every_slot_and_halts_before_any_configuration_write(self):
        self.assertEqual(analyze(ROOT / 'src/trans.s', (DEVHE, MARKERS, DRYRUN)), [])

    def test_the_default_build_has_no_marker_code(self):
        self.assertEqual(analyze(ROOT / 'src/trans.s', ()), [])

    def test_de_vhe_without_markers_has_no_marker_code(self):
        self.assertEqual(analyze(ROOT / 'src/trans.s', (DEVHE,)), [])

    def test_the_dry_run_is_not_a_standalone_gate(self):
        # DRYRUN without MARKERS must not change the assembled code: the markers are what it is a mode of.
        base = BUILD / 'trans-dry-alone.o'
        for gates, name in (((DEVHE, DRYRUN), 'with-dryrun'), ((DEVHE,), 'without')):
            obj = BUILD / f'trans-{name}.o'
            cmd = ['aarch64-elf-as'] + [a for g in gates for a in ('--defsym', f'{g}=1')] + ['-c', 'src/trans.s', '-o', str(obj)]
            self.assertEqual(run(cmd).returncode, 0)
        a = run(['aarch64-elf-objdump', '-d', str(BUILD / 'trans-with-dryrun.o')]).stdout
        b = run(['aarch64-elf-objdump', '-d', str(BUILD / 'trans-without.o')]).stdout
        self.assertEqual(a.split('\n', 2)[2], b.split('\n', 2)[2])
        self.assertFalse(base.exists())

    def _mutant(self, name, needle, replacement):
        self.assertIn(needle, self.text, f'mutation target for {name!r} not found (trans.s restructured?)')
        path = BUILD / f'trans-mutant-{re.sub(r"[^a-z0-9]+", "-", name)}.s'
        path.write_text(self.text.replace(needle, replacement, 1), encoding='utf-8')
        problems = analyze(path)
        print(f'mutant | {"caught" if problems else "SURVIVED"} | {name}', file=sys.stderr)
        self.assertNotEqual(problems, [], f'mutation {name!r} was not caught')

    def test_a_marker_before_the_hcr_isb_is_caught(self):
        self._mutant('marker before the HCR isb', '\tisb\n\tsp11_mark 2', '\tsp11_mark 2\n\tisb')

    def test_a_refusal_block_without_its_isb_is_caught(self):
        self._mutant('refusal block without isb', 'sp11_tb_refused:\n\tisb\n', 'sp11_tb_refused:\n')

    def test_a_wrong_slot_spacing_is_caught(self):
        self._mutant('slot step off', '.equ\tSP11_MARK_STEP_BYTES,\t48 * 4', '.equ\tSP11_MARK_STEP_BYTES,\t47 * 4')

    def test_a_marker_that_writes_a_register_it_may_not_is_caught(self):
        self._mutant('marker clobbers x19', '\tmov\tx27, x23\n', '\tmov\tx19, x23\n\tmov\tx27, x23\n')

    def test_a_marker_before_the_final_isb_of_the_tlbi_group_is_caught(self):
        self._mutant('marker before the tlbi group isb', '\tisb\n\tsp11_mark 3\t', '\tsp11_mark 3\t\n\tisb\n\t')

    def test_a_missing_completion_barrier_is_caught(self):
        self._mutant('no dsb sy', '\tdsb\tsy\n9:', '9:')

    def test_a_dry_run_that_does_not_halt_is_caught(self):
        path = BUILD / 'trans-mutant-dry-no-halt.s'
        needle = '\tsp11_mark 2\n\tsp11_mark 3\n\tsp11_mark 4\n\tb\thalt\n'
        self.assertIn(needle, self.text)
        path.write_text(self.text.replace(needle, '\tsp11_mark 2\n\tsp11_mark 3\n\tsp11_mark 4\n', 1), encoding='utf-8')
        self.assertNotEqual(analyze(path, (DEVHE, MARKERS, DRYRUN)), [], 'a dry run without its halt was not caught')

    def test_a_dry_run_without_its_acknowledgement_slot_is_caught(self):
        path = BUILD / 'trans-mutant-dry-no-ack.s'
        needle = '\tsp11_mark 4\n\tb\thalt\n'
        self.assertIn(needle, self.text)
        path.write_text(self.text.replace(needle, '\tb\thalt\n', 1), encoding='utf-8')
        self.assertNotEqual(analyze(path, (DEVHE, MARKERS, DRYRUN)), [], 'a dry run without slot 4 was not caught')

    def test_a_dry_run_that_keeps_the_register_writes_is_caught(self):
        path = BUILD / 'trans-mutant-dry-keeps-hcr.s'
        needle = '\tsp11_mark 2\n\tsp11_mark 3\n\tsp11_mark 4\n\tb\thalt\n'
        self.assertIn(needle, self.text)
        path.write_text(self.text.replace(
            needle, '\tsp11_mark 2\n\tsp11_mark 3\n\tsp11_mark 4\n\tmsr\thcr_el2, x2\n\tb\thalt\n', 1), encoding='utf-8')
        self.assertNotEqual(analyze(path, (DEVHE, MARKERS, DRYRUN)), [], 'a dry run that writes HCR_EL2 was not caught')


class MakefileGates(unittest.TestCase):
    """Gate combinations that make no sense are refused by the Makefile itself."""

    def _make(self, *args):
        # -B so every command is printed, not only those for objects that happen to be out of date.
        return run(['make', '-n', '-B', 'CROSS_COMPILE=aarch64-elf-', *args])

    def test_markers_need_the_de_vhe_gate(self):
        done = self._make('SLBOUNCE_SP11_MARKERS=1')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('needs SLBOUNCE_SP11_DEVHE=1', done.stdout + done.stderr)

    def test_the_dry_run_needs_the_marker_gate(self):
        done = self._make('SLBOUNCE_SP11_DEVHE=1', 'SLBOUNCE_SP11_MARKERS_DRYRUN=1')
        self.assertNotEqual(done.returncode, 0)
        self.assertIn('needs SLBOUNCE_SP11_MARKERS=1', done.stdout + done.stderr)

    def test_the_valid_combinations_are_accepted_and_pass_the_gates_to_both_tools(self):
        for args, as_defs, c_defs, absent in (
                (('SLBOUNCE_SP11_DEVHE=1', 'SLBOUNCE_SP11_MARKERS=1'), [MARKERS], [MARKERS], [DRYRUN]),
                (('SLBOUNCE_SP11_DEVHE=1', 'SLBOUNCE_SP11_MARKERS=1', 'SLBOUNCE_SP11_MARKERS_DRYRUN=1'),
                 [MARKERS, DRYRUN], [MARKERS], []),
                (('SLBOUNCE_SP11_DEVHE=1',), [], [], [MARKERS, DRYRUN])):
            done = self._make(*args)
            self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
            for g in as_defs:
                self.assertIn(f'--defsym {g}=1', done.stdout)
            for g in c_defs:
                self.assertIn(f'-D{g}', done.stdout)
            for g in absent:
                self.assertNotIn(g, done.stdout)


class HookSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / 'src/bounce_main.c').read_text(encoding='utf-8')

    @staticmethod
    def _body(text, signature):
        start = text.index(signature)
        brace = text.index('{', start)
        depth, i = 0, brace
        while True:
            depth += {'{': 1, '}': -1}.get(text[i], 0)
            i += 1
            if depth == 0:
                return text[brace:i]

    def test_the_hook_calls_nothing_that_allocates(self):
        body = self._body(self.text, 'static void sp11_arm_progress_markers(void)')
        calls = set(re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', body)) - {'if', 'for', 'while', 'sizeof', 'volatile'}
        allowed = {'sp11_arm_progress_markers', 'uefi_call_wrapper', 'EFI_ERROR', 'sp11_marker_plan'}
        self.assertEqual(calls - allowed, set(), 'the hook calls something unreviewed: it must not allocate')
        for forbidden in ('Print', 'Dbg', 'Lib', 'AllocatePool', 'AllocatePages', 'FreePool', 'GetMemoryMap'):
            self.assertNotIn(forbidden, body)
        self.assertIn('BS->LocateProtocol', body)

    def test_the_hook_runs_after_the_sweep_and_before_the_real_exit_boot_services(self):
        body = self._body(self.text, 'EFI_STATUS sl_ExitBootServices(EFI_HANDLE ImageHandle, UINTN MapKey)')
        sweep = body.index('sp11_run_scoped_sweep()')
        arm = body.index('sp11_arm_progress_markers();')
        # The first mention is the early return taken when the device tree does not allow the switch.
        real = body.rindex('uefi_call_wrapper(real_ExitBootServices')
        self.assertLess(sweep, arm)
        self.assertLess(arm, real)
        self.assertEqual(body.count('sp11_arm_progress_markers();'), 1)

    def test_the_hook_leaves_the_words_zero_unless_every_check_passed(self):
        body = self._body(self.text, 'static void sp11_arm_progress_markers(void)')
        self.assertLess(body.index('tb_jmp_buf[21] = 0;'), body.index('LocateProtocol'))
        self.assertLess(body.index('sp11_marker_plan('), body.index('tb_jmp_buf[21] = strip.origin;'))
        self.assertLess(body.index('dsb sy'), body.index('tb_jmp_buf[21] = strip.origin;'))

    def test_the_gate_is_the_only_way_in(self):
        for needle in ('static void sp11_arm_progress_markers', 'sp11_arm_progress_markers();', '#include "sp11_marker_surface.h"'):
            at = self.text.index(needle)
            before = self.text[:at]
            self.assertGreater(before.rfind('#ifdef SLBOUNCE_SP11_MARKERS'), before.rfind('#endif'),
                               f'{needle!r} is outside an SLBOUNCE_SP11_MARKERS block')


class Agreement(unittest.TestCase):
    def test_the_constants_in_c_assembler_and_the_buffer_layout_agree(self):
        header = (ROOT / 'src/sp11_marker_surface.h').read_text(encoding='utf-8')
        trans = (ROOT / 'src/trans.s').read_text(encoding='utf-8')
        arch = (ROOT / 'src/arch.h').read_text(encoding='utf-8')
        hook = (ROOT / 'src/bounce_main.c').read_text(encoding='utf-8')

        def c(name):
            return int(re.search(rf'#define {name}\s+(\d+)u', header).group(1))

        def s(name):
            return eval(re.search(rf'\.equ\t{name},\s*([0-9 *]+)', trans).group(1))

        self.assertEqual((c('SP11_MARKER_SIDE_PX'), c('SP11_MARKER_STEP_PX') * 4, c('SP11_MARKER_SLOTS')),
                         (s('SP11_MARK_SIDE'), s('SP11_MARK_STEP_BYTES'), s('SP11_MARK_SLOTS')))
        self.assertEqual((SIDE, STEP, SLOTS, MARGIN), (c('SP11_MARKER_SIDE_PX'), c('SP11_MARKER_STEP_PX'),
                                                       c('SP11_MARKER_SLOTS'), c('SP11_MARKER_MARGIN_PX')))
        # The two extra words follow the 21 the plain build saves, and the flush covers all of them.
        self.assertEqual((s('SP11_JB_MARK_ORIGIN'), s('SP11_JB_MARK_STRIDE')), (8 * 21, 8 * 22))
        self.assertIn('tb_jmp_buf[21] = strip.origin;', hook)
        self.assertIn('tb_jmp_buf[22] = strip.stride_bytes;', hook)
        self.assertIn('#define TB_JMP_BUF_QWORDS 23', arch)
        self.assertIn('#define TB_JMP_BUF_QWORDS 21', arch)
        self.assertIn('8*TB_JMP_BUF_QWORDS', hook)

    def test_only_the_marker_build_has_the_longer_buffer(self):
        if not (have('aarch64-elf-as') and have('aarch64-elf-size')):
            self.skipTest('aarch64-elf-as/size not installed')
        BUILD.mkdir(parents=True, exist_ok=True)
        sizes = {}
        for label, gates in (('default', ()), ('devhe', (DEVHE,)), ('markers', (DEVHE, MARKERS)),
                             ('dryrun', (DEVHE, MARKERS, DRYRUN))):
            obj = BUILD / f'trans-size-{label}.o'
            cmd = ['aarch64-elf-as'] + [a for g in gates for a in ('--defsym', f'{g}=1')] + ['-c', 'src/trans.s', '-o', str(obj)]
            self.assertEqual(run(cmd).returncode, 0)
            sizes[label] = int(run(['aarch64-elf-size', str(obj)]).stdout.splitlines()[1].split()[1])
        self.assertEqual(sizes, {'default': 8 * 21, 'devhe': 8 * 21, 'markers': 8 * 23, 'dryrun': 8 * 23})

    def test_the_wider_buffer_declaration_is_behind_the_marker_gate(self):
        arch = (ROOT / 'src/arch.h').read_text(encoding='utf-8')
        at = arch.index('#define TB_JMP_BUF_QWORDS 23')
        self.assertGreater(arch[:at].rfind('#ifdef SLBOUNCE_SP11_MARKERS'), arch[:at].rfind('#endif'))

    def test_the_type_mask_matches_q1n1s(self):
        q1n1 = os.environ.get('Q1N1_SP11_DIR')
        if not q1n1 or not (Path(q1n1) / 'platform/uefi/sp11-fbgop.h').is_file():
            self.skipTest('Q1N1_SP11_DIR does not name a q1n1 checkout')
        theirs = (Path(q1n1) / 'platform/uefi/sp11-fbgop.h').read_text(encoding='utf-8')
        ours = (ROOT / 'src/sp11_marker_surface.h').read_text(encoding='utf-8')

        def bits(text, name):
            body = re.search(rf'#define {name}\s*\((.*?)\)\s*(?:\n\n|\n/\*|\n#)', text, re.S).group(1)
            return {int(n) for n in re.findall(r'1u\s*<<\s*(\d+)', body)}

        self.assertEqual(bits(theirs, 'Q1N1_SP11_GOP_TYPES_MASK'), bits(ours, 'SP11_MARKER_FB_TYPES_MASK'))
        self.assertEqual(bits(ours, 'SP11_MARKER_FB_TYPES_MASK'), ALLOWED_TYPES)


if __name__ == '__main__':
    unittest.main()
