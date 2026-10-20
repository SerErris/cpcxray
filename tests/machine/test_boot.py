"""Phase 4 milestone: the CPC 464 boots to "BASIC 1.0 / Ready" (design.md §8, §9).

Uses the real ROMs from config/machine.toml (skipped when they are missing). The check reads
the text from screen RAM (character cells against the ROM font), not pixels. VDOS (decoder
slot) is skipped until phase 7, as decided for phase 4.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.machine.config import load_config
from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session

CONFIG = Path(__file__).resolve().parents[2] / "config" / "machine.toml"
_config = load_config(CONFIG)
needs_roms = pytest.mark.skipif(
    _config.lower_rom is None or not _config.lower_rom.exists or not _config.upper_roms[0].exists,
    reason="OS / BASIC ROM files missing (roms/ is not in git)",
)


@needs_roms
def test_boots_to_basic_ready() -> None:
    """After 3 emulated seconds the screen shows the 464 sign-on, BASIC 1.0 and Ready."""
    session = Session(CPC464(_config))
    session.run(max_us=3_000_000, timeout_s=60)
    lines = session.screen_text()["lines"]
    assert " Amstrad 64K Microcomputer  (v1)" in lines
    assert " ©1984 Amstrad Consumer Electronics plc" in lines
    assert " BASIC 1.0" in lines
    assert "Ready" in lines
    status = session.machine_status()
    assert (status["interrupts_enabled"], status["interrupt_mode"]) == (True, 1)
    assert status["skipped_roms"] == [r for r in status["skipped_roms"] if "decoder" in r]


@needs_roms
def test_boot_milestones_in_order() -> None:
    """Firmware milestones: ROMs configured at reset, jumpblock set up, screen programmed."""
    machine = CPC464(_config)
    assert (machine.cpu.pc, machine.memory.lower_enabled, machine.memory.upper_enabled) == (
        0, True, False,
    )  # fmt: skip
    machine.run(1_000_000)
    assert machine.memory.ram[0xBB5A] == 0xCF  # TXT OUTPUT jumpblock entry: RST 1
    assert machine.crtc.registers[1] == 40 and machine.crtc.registers[12] & 0x30 == 0x30
    assert machine.memory.ram[0xBDF4] == 0x00  # KM SCAN KEYS is 664/6128 only
