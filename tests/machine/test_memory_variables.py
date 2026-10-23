"""The OS and AMSDOS variables (FR-DBG-11) against the real firmware of the 464, 664 and 6128.

Two checks, skipped when the ROMs are missing:

* After a boot (and after BASIC commands) known variables hold the values they must have.
* The machine columns of docs/memory.md match the code: the OS ROMs of two machines are
  disassembled and aligned by instruction shape (operands masked); where the 6128 code uses a
  RAM address, the aligned 664 or 464 instruction must use an address of the same variable.
  This is how the undocumented 664 column was derived, and it confirms the 464 column.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path

import pytest

from cpcxray.cpu.disasm import Instruction, disassemble
from cpcxray.debugger.variables import VariableMap
from cpcxray.machine.config import MachineConfig
from cpcxray.session.session import Session
from tests.machines import (
    CONFIG_464,
    CONFIG_664,
    CONFIG_6128,
    REPO,
    boot,
    requires_664_roms,
    requires_6128_roms,
    requires_roms,
)

MACHINES = [
    pytest.param(CONFIG_464, "464", marks=requires_roms, id="464"),
    pytest.param(CONFIG_664, "664", marks=requires_664_roms, id="664"),
    pytest.param(CONFIG_6128, "6128", marks=requires_6128_roms, id="6128"),
]
CODE_END = 0x3800  # the OS ROM's code; the font follows
VARIABLES_START, VARIABLES_END = 0xB100, 0xB900  # the firmware variables of all machines

# 6128 addresses whose aligned 464 instruction uses another variable, with the reason
NOT_VARIABLES_ON_464 = {
    0xB1B9: "IX base below the sound channel blocks (TD: base address for calculating)",
    0xB1BC: "IX base below the sound channel blocks",
    0xB1D5: "IX base below the sound channel blocks",
    0xB67F: "TD: a vestige from the 464 - the 6128 code still uses the 464 address",
    0xB6B2: "the 464 keeps its line flags inside GRA_LINE_WORK (different layout)",
    0xB101: "alignment artefact: different routines matched",
    0xB5D6: "alignment artefact: a constant, not an address",
}


def word(session: Session, address: int) -> int:
    """Read a little-endian word through the CPU view."""
    return session.machine.peek(address) | session.machine.peek(address + 1) << 8


def address_of(session: Session, label: str) -> int:
    """The address of a variable on the running machine."""
    return int(session.variable_list(label)["variables"][0]["address"], 16)


@pytest.mark.parametrize(("config", "model"), MACHINES)
def test_known_values_after_boot(tmp_path: Path, config: MachineConfig, model: str) -> None:
    """Foreground ROM, screen mode, key tables, ROM table and AMSDOS read as expected."""
    session = boot(tmp_path, config)
    assert session.variables is not None and session.variables.model == model
    peek = session.machine.peek
    assert peek(address_of(session, "KL_FOREGROUND_ROM")) == 0  # BASIC
    assert word(session, address_of(session, "KL_FOREGROUND_ENTRY")) == 0xC006
    assert peek(address_of(session, "SCR_MODE")) == 1
    for table in ("NORMAL", "SHIFT", "CONTROL", "REPEAT"):
        pointer = address_of(session, f"KM_{table}_TABLE_ADDRESS")
        assert word(session, pointer) == address_of(session, f"KM_{table}_TABLE")
    rom_table = address_of(session, "KL_ROM_IY_TABLE")
    assert word(session, rom_table + 2 * 7) == 0xA700  # AMSDOS in slot 7: its data area
    assert session.variables.amsdos_base(peek) == 0xA700
    assert peek(address_of(session, "AMSDOS_DEFAULT_DRIVE")) == 0
    assert peek(address_of(session, "AMSDOS_DEFAULT_USER")) == 0
    assert word(session, address_of(session, "AMSDOS_DPH_POINTER")) == address_of(
        session, "AMSDOS_DPH_A")  # fmt: skip


@pytest.mark.parametrize(("config", "model"), MACHINES)
def test_values_follow_basic_commands(tmp_path: Path, config: MachineConfig, model: str) -> None:
    """MODE, LOCATE and |USER change the screen mode, cursor and AMSDOS user variables."""
    session = boot(tmp_path, config)
    session.type_text('MODE 2:|USER,3:LOCATE 7,4:PRINT "X";:WHILE 1:WEND\r')
    session.run(max_us=500_000)
    peek = session.machine.peek
    assert peek(address_of(session, "SCR_MODE")) == 2, model
    assert peek(address_of(session, "TXT_CURSOR_ROW")) == 3  # 0-based, whole screen
    assert peek(address_of(session, "TXT_CURSOR_COLUMN")) == 7  # after the X at column 7
    assert peek(address_of(session, "AMSDOS_DEFAULT_USER")) == 3


def listing(rom: bytes) -> list[Instruction]:
    """Disassemble the OS ROM's code linearly."""
    instructions, address = [], 0
    while address < CODE_END:
        instruction = disassemble(lambda a: rom[a & 0x3FFF], address)
        instructions.append(instruction)
        address += instruction.length
    return instructions


def aligned_ram_operands(rom: bytes, other: bytes) -> dict[int, int]:
    """Map RAM operands of `rom` to those of `other` at the same place in aligned code.

    The instruction sequences are matched by shape (the template with "{word}" for the
    16-bit operand). Each address of `rom` maps to the address the aligned instructions of
    `other` use most; addresses without a clear majority (two thirds) are left out.
    """
    first, second = listing(rom), listing(other)
    matcher = SequenceMatcher(None, [i.template for i in first], [i.template for i in second],
                              autojunk=False)  # fmt: skip
    found: dict[int, Counter[int]] = defaultdict(Counter)
    for block in matcher.get_matching_blocks():
        for index in range(block.size):
            mine, theirs = first[block.a + index], second[block.b + index]
            operand = mine.word_operand
            if (
                "{word}" in mine.template
                and operand is not None
                and (VARIABLES_START <= operand < VARIABLES_END)
            ):
                found[operand][theirs.word_operand or 0] += 1
    return majorities(found)


def majorities(found: dict[int, Counter[int]]) -> dict[int, int]:
    """For each address, the target seen in at least two thirds of its aligned uses."""
    result = {}
    for address, targets in found.items():
        target, count = targets.most_common(1)[0]
        if count * 3 >= sum(targets.values()) * 2:
            result[address] = target
    return result


@pytest.mark.parametrize(
    "model",
    [pytest.param("664", marks=requires_664_roms), pytest.param("464", marks=requires_roms)],
)
@requires_6128_roms
def test_machine_columns_match_the_code(model: str) -> None:
    """Every RAM variable the 6128 code uses is the same variable in the aligned code."""
    roms = REPO / "src" / "cpcxray" / "roms"  # the standard ROMs, in git
    pairs = aligned_ram_operands((roms / "os6128.rom").read_bytes(),
                                 (roms / f"os{model}.rom").read_bytes())  # fmt: skip
    cpc6128, other = VariableMap("6128"), VariableMap(model)
    no_memory = bytes(0x10000).__getitem__  # firmware variables only
    exceptions = NOT_VARIABLES_ON_464 if model == "464" else {}
    differing = []
    for address, target in sorted(pairs.items()):
        hits = cpc6128.lookup(address, no_memory)
        if not hits or address in exceptions:
            continue
        labels = [hit.variable.label for hit in other.lookup(target, no_memory)]
        if hits[0].variable.label not in labels:
            differing.append(f"&{address:04X} {hits[0].name} -> &{target:04X} {labels}")
    assert len(pairs) > 150
    assert not differing, differing
