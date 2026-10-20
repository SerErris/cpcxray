"""Shared setup of the tests that boot the real CPC 464 ROMs with VDOS (phases 7 and 8).

The ROMs come from config/machine.toml; `requires_roms` skips a module when any is missing
(NFR-07: no ROM images in git).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.machine.config import load_config
from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session

REPO = Path(__file__).resolve().parents[1]
CONFIG = load_config(REPO / "config" / "machine.toml")
requires_roms = pytest.mark.skipif(
    not all(rom.exists for rom in CONFIG.roms()), reason="ROM files missing"
)


def boot_vdos(project_dir: Path) -> Session:
    """Boot the CPC 464 with VDOS for 4 s of emulated time (project files in `project_dir`)."""
    session = Session(CPC464(CONFIG), project_dir=project_dir)
    session.run(max_us=4_000_000, timeout_s=120)
    return session
