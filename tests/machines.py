"""Shared setup of the tests that run the real CPC firmware (config/test_cpc464.toml, 6128).

The test configurations hold the plain machine ROMs: OS, BASIC and AMSDOS (464: BASIC 1.0;
6128: its OS and BASIC 1.1). The disk images come from disk/ (the owner's real images, never
written: 464_cpm_22_orig.dsk, a 3" CP/M 2.2 SYSTEM disk; vortex_test.dsk, a fixed copy of a
Vortex-format disk). `requires_roms`, `requires_6128_roms` and `requires_disks` skip when files
are missing (NFR-07: no images in git).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cpcxray.machine.config import DriveConfig, MachineConfig, load_config
from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session

REPO = Path(__file__).resolve().parents[1]
CONFIG_464 = load_config(REPO / "config" / "test_cpc464.toml")
CONFIG_6128 = load_config(REPO / "config" / "test_cpc6128.toml")
requires_roms = pytest.mark.skipif(
    not all(rom.exists for rom in CONFIG_464.roms()), reason="464 ROM files missing"
)
requires_6128_roms = pytest.mark.skipif(
    not all(rom.exists for rom in CONFIG_6128.roms()), reason="6128 ROM files missing"
)

DISKS = REPO / "disk"
CPM_DISK = DISKS / "464_cpm_22_orig.dsk"
VORTEX_DISK = DISKS / "vortex_test.dsk"
requires_disks = pytest.mark.skipif(
    not (CPM_DISK.is_file() and VORTEX_DISK.is_file()), reason="disk images missing"
)


def with_disks(
    config: MachineConfig, a: Path | None = CPM_DISK, b: Path | None = None
) -> MachineConfig:
    """The configuration with disk images inserted read-only (A: the CP/M disk by default)."""
    drives = {letter: DriveConfig(letter, path) for letter, path in (("A", a), ("B", b)) if path}
    return replace(config, drives=drives)


def boot(project_dir: Path, config: MachineConfig = CONFIG_464) -> Session:
    """Power on a CPC from a test configuration and run it until BASIC is ready."""
    session = Session(CPC464(config), project_dir=project_dir)
    run_until_ready(session)
    return session


def run_until_ready(session: Session, limit_us: int = 10_000_000) -> int:
    """Run until BASIC shows "Ready"; return the emulated µs it took."""
    used = 0
    while "Ready" not in session.screen_text()["lines"]:
        if used >= limit_us:
            raise AssertionError("BASIC did not become ready")
        used += int(session.run(max_us=100_000, timeout_s=60)["emulated_us"])
    return used
