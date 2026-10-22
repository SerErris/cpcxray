"""Memory map, ROM slots and 6128 RAM banking (design.md §5.1, §6.4; FR-MCH-01..06, 10; D-14).

| Range       | Read                                              | Write |
|-------------|---------------------------------------------------|-------|
| &0000-&3FFF | lower ROM (OS) if enabled, else RAM               | RAM   |
| &4000-&BFFF | RAM                                               | RAM   |
| &C000-&FFFF | selected upper ROM if enabled, else RAM (screen)  | RAM   |

Reads go through a 4-entry table, one entry per 16 KB page (`addr >> 14`), holding either None
(RAM) or the ROM slot that answers. The table is recomputed only when the Gate Array changes
the ROM enables or a new upper ROM is selected. Writes always go to RAM (FR-MCH-06).

6128 RAM banking (FR-MCH-10, D-14): the 6128 has 128 KB RAM in eight 16 KB blocks. A RAM
configuration o = 0-7 (Gate Array function 11, `OUT &7Fxx` with &C0 + o) chooses the block seen
in each page:

| o | &0000 | &4000 | &8000 | &C000 | use                                       |
|---|-------|-------|-------|-------|-------------------------------------------|
| 0 | 0     | 1     | 2     | 3     | firmware default                          |
| 1 | 0     | 1     | 2     | 7     | CP/M Plus                                 |
| 2 | 4     | 5     | 6     | 7     | CP/M Plus (own interrupt handler at &0038)|
| 3 | 0     | 3     | 2     | 7     | CP/M Plus (screen block 3 at &4000)       |
| 4-7 | 0   | 4-7   | 2     | 3     | one extra block at &4000                  |

`ram` is the CPU's view of the 64 KB address space: it holds the blocks mapped now, so every
read and write path stays as it is on the 464. A configuration change swaps only the pages
whose block changes: their contents go back to the block store and the new blocks come in
(16 KB slice copies; banking is rare compared with memory accesses). No configuration maps
one block twice, so the view never aliases. Enabled ROMs still hide the banked RAM; writes go
to the banked block. The CRTC ignores banking: it always shows blocks 0-3 (`base_ram`).

Upper ROM selection follows the owner's CE/OE model (D-09): the last value written to &DFxx
selects a slot 0-15; an unpopulated number, or a value above 15, makes the slot configured as
`unpopulated_rom` answer (slot 0, BASIC, as on a real 464, D-10).

The read path tells the ROM slot whether the access is an M1 fetch: scrambled ROMs decode
data reads only and record every read in their shadow (D-11, D-17). `peek` reads the same way
without recording, for the debugger and the disassembler.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cpcxray.decoding.shadow_rom import ShadowRom

ROM_SIZE = 0x4000
UPPER_SLOTS = range(16)
BLOCK_SIZE = 0x4000
RAM_CONFIGURATIONS = (  # block number per page &0000/&4000/&8000/&C000, per configuration
    (0, 1, 2, 3),
    (0, 1, 2, 7),
    (4, 5, 6, 7),
    (0, 3, 2, 7),
    (0, 4, 2, 3),
    (0, 5, 2, 3),
    (0, 6, 2, 3),
    (0, 7, 2, 3),
)


@dataclass
class RomSlot:
    """A 16 KB ROM image in a slot, optionally scrambled (design.md §6.4; D-11, D-17).

    For a scrambled ROM, `decoder` is the decode function (e.g. `vortex_decode`) and the slot
    applies the M1 rule: opcode fetches get the raw byte, every other read the decoded one, as
    long as `decode_enabled` is set (the runtime switch, effective from the next read). Such a
    slot also records every read in its `shadow` (clear values and the access map).

    Attributes:
        data: The 16,384 bytes of the image (scrambled for a scrambled ROM).
        name: Where it came from (file name), for status reports.
        decoder: `decode(offset, raw) -> clear` for data reads, or None for a plain ROM.
        decode_enabled: The decode switch (config default, runtime toggle).
        shadow: Recorded reads; created automatically for slots with a decoder.
    """

    data: bytes
    name: str = ""
    decoder: Callable[[int, int], int] | None = None
    decode_enabled: bool = True
    shadow: ShadowRom | None = None

    def __post_init__(self) -> None:
        """Check the image size; give scrambled ROMs a shadow (D-17)."""
        if len(self.data) != ROM_SIZE:
            raise ValueError(f"ROM image {self.name!r} has {len(self.data)} bytes, expected 16384")
        if self.decoder is not None and self.shadow is None:
            self.shadow = ShadowRom(self.data)

    @property
    def scrambled(self) -> bool:
        """True if the slot has a decoder."""
        return self.decoder is not None

    def read(self, offset: int, is_m1: bool) -> int:
        """Return the byte the CPU sees at `offset` (0-&3FFF) and record the read."""
        raw = self.data[offset]
        decoder = self.decoder
        if decoder is None:
            return raw
        decoding = self.decode_enabled
        value = decoder(offset, raw) if decoding and not is_m1 else raw
        if self.shadow is not None:
            self.shadow.record(offset, value, is_m1, decoding)
        return value

    def peek(self, offset: int, as_m1: bool = False) -> int:
        """Return what a read would give, without recording it (debugger, disassembler)."""
        raw = self.data[offset]
        if self.decoder is None or as_m1 or not self.decode_enabled:
            return raw
        return self.decoder(offset, raw)


class MemoryMap:  # pylint: disable=too-many-instance-attributes  # the paging state
    """RAM, the lower ROM and the upper ROM slots, with the CPC paging logic.

    Attributes:
        ram: The CPU's view of RAM: the 64 KB of the blocks mapped now.
        ram_blocks: Number of 16 KB RAM blocks: 4 (464, no banking) or 8 (6128).
        ram_configuration: The banking configuration 0-7 (always 0 without banking).
        lower_rom: The OS ROM, or None (then &0000-&3FFF always reads RAM).
        upper_roms: Populated upper ROM slots.
        unpopulated_rom: Slot that answers for unpopulated ROM numbers (D-10).
        lower_enabled: Lower ROM visible (Gate Array RMR bit 2 = 0).
        upper_enabled: Upper ROM visible (Gate Array RMR bit 3 = 0).
        selected_upper: Last value written to the ROM select port (&DFxx).
    """

    def __init__(self, unpopulated_rom: int = 0, ram_blocks: int = 4) -> None:
        """Create the map with cleared RAM, no ROMs and both ROMs enabled (power-on state).

        Args:
            unpopulated_rom: Slot that answers for unpopulated ROM numbers (D-10).
            ram_blocks: 4 for the 464 (64 KB), 8 for the 6128 (128 KB with banking).
        """
        if ram_blocks not in (4, 8):
            raise ValueError(f"ram_blocks must be 4 or 8, got {ram_blocks}")
        self.ram = bytearray(0x10000)
        self.ram_blocks = ram_blocks
        self.ram_configuration = 0
        # the store of every block; a mapped block's current contents are in `ram`
        self._block_store = [bytearray(BLOCK_SIZE) for _ in range(ram_blocks)]
        self.lower_rom: RomSlot | None = None
        self.upper_roms: dict[int, RomSlot] = {}
        self.unpopulated_rom = unpopulated_rom
        self.lower_enabled = True
        self.upper_enabled = True
        self.selected_upper = 0
        self._read_banks: list[RomSlot | None] = [None, None, None, None]
        self._recompute()

    # -- configuration ---------------------------------------------------------------------------

    def set_lower_rom(self, rom: RomSlot | None) -> None:
        """Install (or remove, with None) the lower ROM."""
        self.lower_rom = rom
        self._recompute()

    def set_upper_rom(self, slot: int, rom: RomSlot | None) -> None:
        """Install (or remove, with None) the ROM in an upper slot 0-15."""
        if slot not in UPPER_SLOTS:
            raise ValueError(f"upper ROM slot {slot} not in 0-15")
        if rom is None:
            self.upper_roms.pop(slot, None)
        else:
            self.upper_roms[slot] = rom
        self._recompute()

    # -- paging (called by the Gate Array and the ROM select latch) ------------------------------

    def set_rom_enables(self, lower_enabled: bool, upper_enabled: bool) -> None:
        """Apply the ROM enable bits of the Gate Array (FR-MCH-02)."""
        if (lower_enabled, upper_enabled) != (self.lower_enabled, self.upper_enabled):
            self.lower_enabled = lower_enabled
            self.upper_enabled = upper_enabled
            self._recompute()

    def select_upper(self, number: int) -> None:
        """Latch an upper ROM number written to &DFxx (FR-MCH-03)."""
        if number != self.selected_upper:
            self.selected_upper = number
            self._recompute()

    def set_ram_configuration(self, configuration: int) -> None:
        """Map the RAM blocks of configuration 0-7 (Gate Array function 11; 6128 only).

        Without banking (464) the write is ignored, as on the real machine. Pages whose block
        changes are swapped: all outgoing contents are saved first, then the new blocks are
        loaded, so a block that moves to another page (configuration 3) arrives intact.
        """
        configuration &= 0x07
        if self.ram_blocks == 4 or configuration == self.ram_configuration:
            return
        old_blocks = RAM_CONFIGURATIONS[self.ram_configuration]
        new_blocks = RAM_CONFIGURATIONS[configuration]
        changed = [page for page in range(4) if old_blocks[page] != new_blocks[page]]
        for page in changed:
            start = page * BLOCK_SIZE
            self._block_store[old_blocks[page]][:] = self.ram[start : start + BLOCK_SIZE]
        for page in changed:
            start = page * BLOCK_SIZE
            self.ram[start : start + BLOCK_SIZE] = self._block_store[new_blocks[page]]
        self.ram_configuration = configuration

    def ram_block_at(self, address: int) -> int:
        """Return the RAM block mapped at `address` now (0-3 without banking)."""
        return RAM_CONFIGURATIONS[self.ram_configuration][(address & 0xFFFF) >> 14]

    def block(self, number: int) -> bytes:
        """Return the 16 KB contents of RAM block `number` (mapped or not)."""
        if not 0 <= number < self.ram_blocks:
            raise ValueError(f"RAM block {number} not in 0-{self.ram_blocks - 1}")
        mapped = RAM_CONFIGURATIONS[self.ram_configuration]
        if number in mapped:
            start = mapped.index(number) * BLOCK_SIZE
            return bytes(self.ram[start : start + BLOCK_SIZE])
        return bytes(self._block_store[number])

    def set_block(self, number: int, data: bytes) -> None:
        """Replace the 16 KB contents of RAM block `number` (mapped or not; snapshot loading)."""
        if not 0 <= number < self.ram_blocks or len(data) != BLOCK_SIZE:
            raise ValueError(f"RAM block {number}: expected 0-{self.ram_blocks - 1}, 16 KB")
        mapped = RAM_CONFIGURATIONS[self.ram_configuration]
        if number in mapped:
            start = mapped.index(number) * BLOCK_SIZE
            self.ram[start : start + BLOCK_SIZE] = data
        else:
            self._block_store[number][:] = data

    def base_ram(self) -> bytes:
        """Return blocks 0-3 as 64 KB: the RAM the CRTC displays, whatever is banked in."""
        return b"".join(self.block(number) for number in range(4))

    def effective_upper_slot(self) -> int | None:
        """Return the slot that answers for the selected number (D-10), or None if none."""
        if self.selected_upper in self.upper_roms:
            return self.selected_upper
        if self.unpopulated_rom in self.upper_roms:
            return self.unpopulated_rom
        return None

    def _recompute(self) -> None:
        """Rebuild the read table from the enables and the ROM selection."""
        lower = self.lower_rom if self.lower_enabled else None
        upper_slot = self.effective_upper_slot() if self.upper_enabled else None
        upper = self.upper_roms[upper_slot] if upper_slot is not None else None
        self._read_banks = [lower, None, None, upper]
        # names for visible_rom, computed once per paging change (it is asked on every access)
        self._visible_names = [
            "lower" if lower is not None else None, None, None,
            f"rom{upper_slot}" if upper is not None else None,
        ]  # fmt: skip

    # -- access ----------------------------------------------------------------------------------

    def read(self, address: int, is_m1: bool) -> int:
        """Read a byte as the CPU does (ROM if paged in, else RAM)."""
        rom = self._read_banks[address >> 14]
        if rom is None:
            return self.ram[address]
        return rom.read(address & 0x3FFF, is_m1)

    def write(self, address: int, value: int) -> None:
        """Write a byte: always to RAM, also under a paged-in ROM (FR-MCH-06)."""
        self.ram[address] = value

    def peek(self, address: int, as_m1: bool = False) -> int:
        """Read a byte as the CPU would (M1 or data read), without recording anything."""
        rom = self._read_banks[address >> 14]
        if rom is None:
            return self.ram[address]
        return rom.peek(address & 0x3FFF, as_m1)

    def shadows(self) -> list[tuple[str, ShadowRom]]:
        """Return (slot name, shadow) for every installed scrambled ROM."""
        slots: list[tuple[str, RomSlot | None]] = [("lower", self.lower_rom)]
        slots += [(f"rom{number}", rom) for number, rom in sorted(self.upper_roms.items())]
        return [(name, rom.shadow) for name, rom in slots if rom is not None and rom.shadow]

    def visible_rom(self, address: int) -> str | None:
        """Return which ROM answers reads at `address` now: "lower", "romN", or None for RAM."""
        return self._visible_names[address >> 14]
