"""Shared setup of the tests that use the real ROMs and disk images (phases 7-10).

The ROMs come from config/machine.toml, the disk images from disk/ (the owner's real images:
464_cpm_22_vdos.dsk, a 3" CP/M 2.2 SYSTEM disk in A:; vortex_test.dsk, a fixed copy of a VDOS
system disk kept only for the tests, in B: instead of machine.toml's disk, so the owner's working
disks can change). `requires_roms` and `requires_disks` skip when files are missing (NFR-07: no
images in git).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cpcxray.machine.config import load_config
from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session

REPO = Path(__file__).resolve().parents[1]
DISKS = REPO / "disk"
CPM_DISK = DISKS / "464_cpm_22_vdos.dsk"
VORTEX_DISK = DISKS / "vortex_test.dsk"
_MACHINE = load_config(REPO / "config" / "machine.toml")
_TEST_DRIVE_B = replace(_MACHINE.drives["B"], file=VORTEX_DISK, writable=False)
CONFIG = replace(_MACHINE, drives={**_MACHINE.drives, "B": _TEST_DRIVE_B})  # the test disk in B:
requires_roms = pytest.mark.skipif(
    not all(rom.exists for rom in CONFIG.roms()), reason="ROM files missing"
)


def boot_vdos(project_dir: Path) -> Session:
    """Boot the CPC 464 with VDOS for 4 s of emulated time (project files in `project_dir`)."""
    session = Session(CPC464(CONFIG), project_dir=project_dir)
    session.run(max_us=4_000_000, timeout_s=120)
    return session


requires_disks = pytest.mark.skipif(
    not (CPM_DISK.is_file() and VORTEX_DISK.is_file()), reason="disk images missing"
)


def run_until_ready(session: Session, limit_us: int = 10_000_000) -> int:
    """Boot until BASIC shows "Ready"; return the emulated µs it took.

    The boot time depends on the inserted disks: VDOS's turnkey function looks for HELLO.BAS /
    HELLO.BIN on drive A:, about 1 s with a disk in A:.
    """
    used = 0
    while "Ready" not in session.screen_text()["lines"]:
        if used >= limit_us:
            raise AssertionError("BASIC did not become ready")
        used += int(session.run(max_us=100_000, timeout_s=60)["emulated_us"])
    return used
