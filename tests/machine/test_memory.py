"""Tests of the memory map: all combinations of ROM enables and ROM selection (design.md §8)."""

from __future__ import annotations

import itertools

import pytest

from cpcxray.machine.memory import MemoryMap, RomSlot


def rom(fill: int) -> RomSlot:
    """A ROM image filled with one byte value, so reads show which ROM answered."""
    return RomSlot(bytes([fill]) * 0x4000, f"rom{fill:02x}")


@pytest.fixture(name="memory")
def fixture_memory() -> MemoryMap:
    """Lower ROM = &AA, upper slot 0 (BASIC) = &B0, slot 7 = &B7; RAM filled with &11."""
    memory = MemoryMap()
    memory.ram[:] = bytes([0x11]) * 0x10000
    memory.set_lower_rom(rom(0xAA))
    memory.set_upper_rom(0, rom(0xB0))
    memory.set_upper_rom(7, rom(0xB7))
    return memory


@pytest.mark.parametrize(
    ("lower", "upper", "selected", "expected"),
    [
        (lower, upper, selected, (0xAA if lower else 0x11, 0x11, 0x11,
                                  ({7: 0xB7}.get(selected, 0xB0) if upper else 0x11)))
        for lower, upper, selected in itertools.product((True, False), (True, False), (0, 5, 7, 99))
    ],
)  # fmt: skip
def test_paging_combinations(
    memory: MemoryMap, lower: bool, upper: bool, selected: int, expected: tuple[int, ...]
) -> None:
    """Every combination of enables and selection reads the right ROM or RAM per bank.

    Selecting the unpopulated slot 5 or the out-of-range 99 makes slot 0 answer (D-10).
    """
    memory.set_rom_enables(lower, upper)
    memory.select_upper(selected)
    for bank, value in enumerate(expected):
        assert memory.read(bank * 0x4000 + 0x123, is_m1=False) == value, bank


def test_writes_go_to_ram_under_roms(memory: MemoryMap) -> None:
    """Writes to ROM areas change the RAM underneath, visible once the ROM is paged out."""
    memory.write(0x0100, 0x42)
    memory.write(0xC100, 0x43)
    assert (memory.read(0x0100, False), memory.read(0xC100, False)) == (0xAA, 0xB0)
    memory.set_rom_enables(False, False)
    assert (memory.read(0x0100, False), memory.read(0xC100, False)) == (0x42, 0x43)


def test_visible_rom(memory: MemoryMap) -> None:
    """visible_rom names the ROM that answers (for symbol scopes), or None for RAM."""
    memory.select_upper(7)
    assert [memory.visible_rom(a) for a in (0x0000, 0x4000, 0x8000, 0xC000)] == [
        "lower",
        None,
        None,
        "rom7",
    ]
    memory.select_upper(5)  # unpopulated: BASIC answers
    assert memory.visible_rom(0xC000) == "rom0"
    memory.set_rom_enables(False, False)
    assert memory.visible_rom(0x0000) is None and memory.visible_rom(0xC000) is None


def test_no_fallback_rom_reads_ram() -> None:
    """Without a slot for unpopulated numbers the upper area reads RAM."""
    memory = MemoryMap()
    memory.ram[0xC000] = 0x55
    memory.select_upper(3)
    assert memory.read(0xC000, False) == 0x55


def test_rom_size_is_checked() -> None:
    """A ROM image must be exactly 16 KB."""
    with pytest.raises(ValueError, match="16384"):
        RomSlot(bytes(100), "short")
