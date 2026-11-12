#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026 the Snapintosh project
"""Default-off proof for both new slbounce gates (SLBOUNCE_SCOPED_SWEEP, SLBOUNCE_SP11_DEVHE):
with neither set, this worktree's build must be byte-identical to the pristine tree's, at the
object level and the final .efi level, and the make command lines must be unchanged. Mirrors the
proof technique q1n1's own tools/test-q1n1-sp11-usb-ports.py DefaultOff class already uses for its
SNAP_PLATFORM_SP11 gate (the "same technique transfers" the task names: compare preprocessed/
compiled output and `make -n` command lines against a pristine comparison tree).

    python3 tools/test-sp11-defaultoff.py

SLBOUNCE_PRISTINE_DIR: a pristine slbounce checkout at the pinned commit (SKIP every test that
needs one without it; the default-off-hash test below is SKIP-free, since this project's own
known-good hash for the pinned commit is itself an input the task gave). Needs CROSS_COMPILE=
aarch64-elf- (the toolchain this host test suite already assumes present, aarch64-elf-gcc/as/ld/
objcopy). Builds happen in this tree's own out/ and the pristine tree's own out/ (never written to
otherwise); nothing is written outside those. Hardware: NOT RUN. Evidence: EXPERIMENTAL/host-only
for the new gates; this file's own claims about the *default* build are the one place in this
patch where PASS is closer to VERIFIED, since it runs the project's real Makefile and real
toolchain, unmodified, exactly as a human operator would."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PRISTINE = os.environ.get('SLBOUNCE_PRISTINE_DIR')
# The exact command line this project's own task brief gives for a clean build.
MAKE_ARGS = ['SLBOUNCE_ALWAYS_SWITCH=1', 'DEBUG=1', 'CROSS_COMPILE=aarch64-elf-']
KNOWN_GOOD_HASH = 'c78b69fd876005cfa4b760c8a063990425deb490ecf2a323e89bfcf42e1f4803'
KNOWN_GOOD_SIZE = 105760
# The two lower-gate builds as they were first built and staged for the unit, before the progress
# markers existed (2026-11-12). Like the default hash above they are specific to this toolchain; if a
# later change to the scoped sweep or the de-VHE sequence is meant to alter them, update the hash with
# that change and say so. They prove that adding the markers changed neither build.
SCOPED_ONLY = ('54a6115be42a99546e889b6d0877ce63c20884929ce4391d123dfc1923f228d1', 122216)
DEVHE_NO_MARKERS = ('3032fda349f16d57e8aff228d0ef276d3cd8e5c277f4ea8c67eef6fa4cb508d3', 122240)


def have(tool):
    return shutil.which(tool) is not None


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class DefaultOff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not have('aarch64-elf-gcc'):
            raise unittest.SkipTest('aarch64-elf-gcc (CROSS_COMPILE=aarch64-elf-) not installed')

    def _clean_build(self, tree, extra_args=()):
        out = Path(tree) / 'out'
        if out.exists():
            shutil.rmtree(out)
        done = subprocess.run(['make'] + MAKE_ARGS + list(extra_args) + ['-j4'], cwd=tree,
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return out

    def test_a_default_build_matches_the_known_good_pinned_hash(self):
        # The hash the task brief itself gives for a clean build of the pristine tree; this
        # worktree's gates are new source, so matching it here is itself part of the proof that
        # they change nothing when unset (if they did, this would not match, independent of
        # whether a pristine comparison tree happens to be configured in this environment).
        out = self._clean_build(ROOT)
        efi = out / 'slbounce.efi'
        self.assertEqual(efi.stat().st_size, KNOWN_GOOD_SIZE)
        self.assertEqual(sha256(efi), KNOWN_GOOD_HASH)

    @unittest.skipUnless(PRISTINE, 'SLBOUNCE_PRISTINE_DIR not set: no pristine tree to compare against')
    def test_default_build_is_byte_identical_to_the_pristine_tree(self):
        mine = self._clean_build(ROOT)
        pristine = self._clean_build(PRISTINE)
        for name in ('slbounce.efi', 'sltest.efi', 'dtbhack.efi'):
            self.assertEqual(sha256(mine / name), sha256(pristine / name), f'{name} differs')

    @unittest.skipUnless(PRISTINE, 'SLBOUNCE_PRISTINE_DIR not set: no pristine tree to compare against')
    def test_default_object_code_is_byte_identical_including_trans_o(self):
        mine = self._clean_build(ROOT)
        pristine = self._clean_build(PRISTINE)
        for rel in ('src/bounce_main.o', 'src/trans.o', 'src/arch.o', 'src/sl.o'):
            self.assertEqual(sha256(mine / rel), sha256(pristine / rel), f'{rel} differs')
        # Only the gated additions exist in neither tree's default object list.
        for rel in ('src/sp11_scoped_sweep.o', 'src/sp11_devhe_convert.o', 'src/sp11_marker_surface.o'):
            self.assertFalse((mine / rel).exists(), f'{rel} must not exist in a default build')

    @unittest.skipUnless(PRISTINE, 'SLBOUNCE_PRISTINE_DIR not set: no pristine tree to compare against')
    def test_default_make_command_lines_are_unchanged(self):
        # make -n (dry run): no build happens, so this needs no prior clean and is cheap to run
        # directly against both trees' committed Makefiles. $(CURDIR) (OUT_DIR, GNUEFI_DIR) makes
        # every absolute path tree-specific even with nothing else different, so each tree's own
        # root is substituted with a placeholder before comparing -- the normalization q1n1's own
        # precedent does not need (its OUT_DIR/-I paths are relative), not a relaxation of what
        # "unchanged" means here.
        lines = {}
        for label, tree in (('mine', ROOT), ('pristine', Path(PRISTINE))):
            out_dir = Path(tree) / 'out'
            if out_dir.exists():
                shutil.rmtree(out_dir)
            done = subprocess.run(['make', '-n'] + MAKE_ARGS, cwd=tree, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            root_str = str(Path(tree).resolve())
            lines[label] = [' '.join(line.split()).replace(root_str, '<ROOT>') for line in done.stdout.splitlines()]
        self.assertGreater(len(lines['mine']), 10)
        self.assertEqual(lines['mine'], lines['pristine'])

    def test_the_gates_change_the_build_when_set(self):
        # The converse control: with both gates set, the build must differ from the default (and
        # must still succeed) -- otherwise the gate-off tests above would be proving nothing.
        default_out = self._clean_build(ROOT)
        default_hash = sha256(default_out / 'slbounce.efi')
        gated_out = self._clean_build(ROOT, ['SLBOUNCE_SCOPED_SWEEP=1', 'SLBOUNCE_SP11_DEVHE=1'])
        self.assertNotEqual(sha256(gated_out / 'slbounce.efi'), default_hash)
        self.assertTrue((gated_out / 'src/sp11_scoped_sweep.o').exists())
        self.assertTrue((gated_out / 'src/sp11_devhe_convert.o').exists())
        self.assertFalse((gated_out / 'src/sp11_marker_surface.o').exists(),
                         'the marker object belongs to SLBOUNCE_SP11_MARKERS only')
        # Leave the tree in its default state for anything that runs after this file.
        self._clean_build(ROOT)

    def test_each_gate_adds_only_its_own_code_and_the_lower_builds_are_unchanged(self):
        scoped = self._clean_build(ROOT, ['SLBOUNCE_SCOPED_SWEEP=1'])
        self.assertEqual((sha256(scoped / 'slbounce.efi'), (scoped / 'slbounce.efi').stat().st_size), SCOPED_ONLY)
        devhe = self._clean_build(ROOT, ['SLBOUNCE_SCOPED_SWEEP=1', 'SLBOUNCE_SP11_DEVHE=1'])
        self.assertEqual((sha256(devhe / 'slbounce.efi'), (devhe / 'slbounce.efi').stat().st_size), DEVHE_NO_MARKERS,
                         'de-VHE without markers is no longer the build that was reviewed and tested')
        # Every build lands in the same out/, so each hash is taken before the next build replaces it.
        marks = self._clean_build(ROOT, ['SLBOUNCE_SCOPED_SWEEP=1', 'SLBOUNCE_SP11_DEVHE=1', 'SLBOUNCE_SP11_MARKERS=1'])
        marks_hash = sha256(marks / 'slbounce.efi')
        self.assertNotEqual(marks_hash, DEVHE_NO_MARKERS[0])
        self.assertTrue((marks / 'src/sp11_marker_surface.o').exists())
        dry = self._clean_build(ROOT, ['SLBOUNCE_SCOPED_SWEEP=1', 'SLBOUNCE_SP11_DEVHE=1', 'SLBOUNCE_SP11_MARKERS=1',
                                       'SLBOUNCE_SP11_MARKERS_DRYRUN=1'])
        self.assertNotEqual(sha256(dry / 'slbounce.efi'), marks_hash)
        # The same markers with the sweep gate off still build (the two gates are independent).
        alone = self._clean_build(ROOT, ['SLBOUNCE_SP11_DEVHE=1', 'SLBOUNCE_SP11_MARKERS=1'])
        self.assertTrue((alone / 'slbounce.efi').exists())
        self._clean_build(ROOT)


if __name__ == '__main__':
    unittest.main()
