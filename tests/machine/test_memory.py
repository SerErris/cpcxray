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


# -- 6128 RAM banking (FR-MCH-10, D-14) --------------------------------------------------------

EXPECTED_BLOCKS = {  # Firmware Guide §2.5: block per page &0000/&4000/&8000/&C000
    0: (0, 1, 2, 3), 1: (0, 1, 2, 7), 2: (4, 5, 6, 7), 3: (0, 3, 2, 7),
    4: (0, 4, 2, 3), 5: (0, 5, 2, 3), 6: (0, 6, 2, 3), 7: (0, 7, 2, 3),
}  # fmt: skip


def banked_memory() -> MemoryMap:
    """A 6128 memory map whose 8 blocks are filled with &B0 + block number, no ROMs."""
    memory = MemoryMap(ram_blocks=8)
    for number in range(8):
        memory.set_block(number, bytes([0xB0 + number]) * 0x4000)
    return memory


@pytest.mark.parametrize("configuration", range(8))
def test_every_configuration_maps_its_blocks(configuration: int) -> None:
    """Each configuration shows the documented block in every page, for reads and writes."""
    memory = banked_memory()
    memory.set_ram_configuration(configuration)
    for page, block in enumerate(EXPECTED_BLOCKS[configuration]):
        address = page * 0x4000 + 0x123
        assert memory.read(address, is_m1=False) == 0xB0 + block
        assert memory.ram_block_at(address) == block
        memory.write(address, 0x40 + block)  # goes to the banked block
    memory.set_ram_configuration(0)
    for block in EXPECTED_BLOCKS[configuration]:
        assert memory.block(block)[0x123] == 0x40 + block


def test_blocks_survive_switching_and_moving() -> None:
    """Configuration 3 moves block 3 to &4000 and back; every block keeps its contents."""
    memory = banked_memory()
    memory.write(0xC000, 0x33)  # block 3 at &C000 in configuration 0
    memory.set_ram_configuration(3)
    assert memory.read(0x4000, is_m1=False) == 0x33  # the same block, now at &4000
    assert memory.read(0xC000, is_m1=False) == 0xB7  # block 7
    memory.write(0x4001, 0x34)
    memory.set_ram_configuration(2)
    memory.set_ram_configuration(0)
    assert memory.ram[0xC000:0xC002] == b"\x33\x34"
    assert [memory.block(n)[0x2000] for n in range(8)] == [0xB0 + n for n in range(8)]


def test_banked_ram_stays_hidden_by_roms_and_crtc_sees_blocks_0_to_3() -> None:
    """An enabled upper ROM still hides block 7; the screen RAM (base_ram) is block 3."""
    memory = banked_memory()
    memory.set_upper_rom(0, rom(0xEE))
    memory.set_rom_enables(lower_enabled=False, upper_enabled=True)
    memory.set_ram_configuration(1)
    assert memory.read(0xC000, is_m1=False) == 0xEE
    memory.write(0xC000, 0x77)  # under the ROM: into block 7
    assert memory.block(7)[0] == 0x77
    assert memory.base_ram()[0xC000] == 0xB3 and len(memory.base_ram()) == 0x10000


def test_464_ignores_ram_configuration() -> None:
    """Without banking (64 KB) a configuration write changes nothing; blocks 4-7 do not exist."""
    memory = MemoryMap()
    memory.ram[0xC000] = 0x12
    memory.set_ram_configuration(2)
    assert memory.ram_configuration == 0 and memory.read(0xC000, is_m1=False) == 0x12
    with pytest.raises(ValueError, match="RAM block 4"):
        memory.block(4)
    with pytest.raises(ValueError, match="ram_blocks"):
        MemoryMap(ram_blocks=6)
