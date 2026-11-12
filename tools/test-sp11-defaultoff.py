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
        for rel in ('src/sp11_scoped_sweep.o', 'src/sp11_devhe_convert.o'):
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
        # Leave the tree in its default state for anything that runs after this file.
        self._clean_build(ROOT)


if __name__ == '__main__':
    unittest.main()
