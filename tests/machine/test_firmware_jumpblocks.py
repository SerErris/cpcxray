"""The firmware names per machine against the jumpblocks of the real firmware (D-26).

After a boot the firmware has set up its jumpblocks in RAM: the main jumpblock from &BB00, then
TEXT INPUT and the maths jumpblock (docs/firmware.md section 7, at other addresses on each
machine), up to the indirections at &BDCD. Every 3-byte slot there that holds a jump must have a
name in the machine's table, and every named slot must hold a jump. Skipped when the ROMs are
missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.debugger.firmware_symbols import FIRMWARE_BY_MODEL
from cpcxray.machine.config import MachineConfig
from tests.machines import (
    CONFIG_464,
    CONFIG_664,
    CONFIG_6128,
    boot,
    requires_664_roms,
    requires_6128_roms,
    requires_roms,
)

JUMPBLOCK_START, INDIRECTIONS_START = 0xBB00, 0xBDCD
# LOW JUMP, FIRM JUMP and FAR CALL restarts, JP: the firmware's and AMSDOS's jumpblock entries
JUMP_OPCODES = frozenset({0xCF, 0xEF, 0xDF, 0xC3})


@pytest.mark.parametrize(
    ("config", "model"),
    [
        pytest.param(CONFIG_464, "464", marks=requires_roms, id="464"),
        pytest.param(CONFIG_664, "664", marks=requires_664_roms, id="664"),
        pytest.param(CONFIG_6128, "6128", marks=requires_6128_roms, id="6128"),
    ],
)
def test_named_entries_are_the_jumpblock(tmp_path: Path, config: MachineConfig, model: str) -> None:
    """Named slots and jump slots from &BB00 to the indirections are the same set per machine."""
    ram = boot(tmp_path, config).machine.memory.ram  # type: ignore[attr-defined]
    slots = range(JUMPBLOCK_START, INDIRECTIONS_START, 3)
    jumps = {address for address in slots if ram[address] in JUMP_OPCODES}
    named = {entry.address for entry in FIRMWARE_BY_MODEL[model]} & set(slots)
    assert sorted(f"&{address:04X}" for address in jumps ^ named) == []
    sections = {entry.address: entry.section for entry in FIRMWARE_BY_MODEL[model]}
    assert {ram[address] for address in jumps if sections[address] == "maths"} == {0xEF}
