"""Tests of the known OS and AMSDOS variables (FR-DBG-11, D-34): tables and lookups.

Headless and without ROMs: the tables generated from docs/memory.md are checked for
consistency, the lookups run against a plain 64 KB memory with a hand-made ROM chain.
"""

from __future__ import annotations

import itertools

import pytest

from cpcxray.debugger.firmware_symbols import FIRMWARE_BY_MODEL
from cpcxray.debugger.variable_tables import (
    AMSDOS_FIXED,
    AMSDOS_WORKSPACE,
    OS_VARIABLES_BY_MODEL,
    VariableEntry,
)
from cpcxray.debugger.variables import VariableError, VariableMap

MODELS = ("464", "664", "6128")


def areas(entries: tuple[VariableEntry, ...]) -> list[VariableEntry]:
    """The pack areas of a table; all of them end in _WORKSPACE, except AMSDOS_FIXED."""
    found = [entry for entry in entries if entry.area]
    assert all(e.label.endswith("_WORKSPACE") or e.label == "AMSDOS_FIXED" for e in found)
    return found


@pytest.mark.parametrize("model", MODELS)
def test_tables_are_consistent(model: str) -> None:
    """Unique labels that never repeat a firmware name; each variable lies inside one area."""
    entries = OS_VARIABLES_BY_MODEL[model] + AMSDOS_FIXED + AMSDOS_WORKSPACE
    labels = [entry.label for entry in entries]
    assert len(labels) == len(set(labels))
    assert not set(labels) & {entry.label for entry in FIRMWARE_BY_MODEL[model]}
    pack_areas = areas(entries)
    for first, second in itertools.pairwise(pack_areas):
        assert first.address + first.size <= second.address or second.address < first.address
    for entry in entries:
        assert entry.size > 0 and entry.address + entry.size <= 0xC000
        end = entry.address + entry.size
        containing = [
            a for a in pack_areas if a.address <= entry.address and end <= a.address + a.size
        ]
        assert len(containing) == 1, entry.label


def test_664_differs_from_6128_only_in_the_kernel_rom_variables() -> None:
    """The 664 lacks the RAM bank number at &B8D5; &B8D6-&B8F9 are one lower (docs/memory.md)."""
    cpc664 = {entry.label: entry for entry in OS_VARIABLES_BY_MODEL["664"]}
    for entry in OS_VARIABLES_BY_MODEL["6128"]:
        if entry.label == "KL_RAM_BANK":
            assert entry.label not in cpc664
        elif entry.label != "KL_WORKSPACE":
            shift = 1 if 0xB8D6 <= entry.address <= 0xB8F9 else 0
            assert cpc664[entry.label].address == entry.address - shift, entry.label


class Memory:
    """A plain 64 KB memory with helpers to place words."""

    def __init__(self) -> None:
        """All zero."""
        self.data = bytearray(0x10000)

    def read(self, address: int) -> int:
        """Read a byte (the `read_byte` of the lookups)."""
        return self.data[address]

    def word(self, address: int, value: int) -> None:
        """Store a little-endian word."""
        self.data[address : address + 2] = value.to_bytes(2, "little")


def with_amsdos(model: str, base: int = 0xA700) -> Memory:
    """A memory where the ROM chain holds a BASIC-like block, then AMSDOS's block below `base`."""
    memory = Memory()
    chain = VariableMap(model).find("KL_ROM_CHAIN", memory.read)
    assert chain is not None
    memory.word(chain.address, 0xA000)  # first block: another ROM's
    memory.word(0xA000, base - 4)  # its successor: AMSDOS's RSX block
    memory.word(base - 4, 0)  # end of the chain
    memory.word(0xBE7D, base)  # AMSDOS_MEMORY_POOL
    return memory


def test_lookup_names_the_smallest_variable_first() -> None:
    """An address inside nested variables lists all of them, the smallest first, as NAME+n."""
    variables, memory = VariableMap("6128"), Memory()
    names = [hit.name for hit in variables.lookup(0xB727, memory.read)]
    assert names == ["TXT_CURSOR_COLUMN", "TXT_CURRENT_WINDOW+1", "TXT_WORKSPACE+114"]
    assert [hit.name for hit in variables.lookup(0xB8B6, memory.read)] == [
        "KL_TIME+2", "KL_WORKSPACE+137"]  # fmt: skip
    assert variables.lookup(0x4000, memory.read) == []
    assert VariableMap("464").lookup(0xB286, memory.read)[0].name == "TXT_CURSOR_COLUMN"


def test_amsdos_variables_only_when_amsdos_is_in_the_rom_chain() -> None:
    """Without AMSDOS (empty chain) its variables do not exist, not even the fixed block."""
    variables = VariableMap("464")
    empty = Memory()
    empty.word(0xBE7D, 0xA700)  # a stale value without the RSX block in the chain
    assert variables.amsdos_base(empty.read) is None
    assert variables.lookup(0xBE7D, empty.read) == []
    assert variables.find("AMSDOS_DEFAULT_DRIVE", empty.read) is None
    memory = with_amsdos("464")
    assert variables.amsdos_base(memory.read) == 0xA700
    assert variables.lookup(0xBE7D, memory.read)[0].name == "AMSDOS_MEMORY_POOL"


@pytest.mark.parametrize("model", MODELS)
def test_amsdos_workspace_follows_its_base(model: str) -> None:
    """A workspace moved down (ROMs with higher numbers reserved RAM first) moves the names."""
    memory = with_amsdos(model, base=0x9F00)
    variables = VariableMap(model)
    drive = variables.find("amsdos_default_drive", memory.read)  # case-insensitive
    assert drive is not None and drive.address == 0x9F00 and drive.owner == "amsdos"
    assert variables.lookup(0x9F00 + 0x08, memory.read)[0].name == "AMSDOS_IN_FCB_DRIVE"
    assert variables.lookup(0xA700, memory.read) == []  # the usual base is not used now
    assert variables.find("AMSDOS_RETRIES", memory.read).address == 0xBE66  # type: ignore[union-attr]


def test_unknown_model() -> None:
    """Only the three CPC firmware models have variables."""
    with pytest.raises(VariableError, match="model"):
        VariableMap("472")
