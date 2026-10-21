"""Memory map and ROM slots of the CPC 464 (design.md §5.1, §6.4; FR-MCH-01..06, D-09, D-10, D-14).

| Range       | Read                                              | Write |
|-------------|---------------------------------------------------|-------|
| &0000-&3FFF | lower ROM (OS) if enabled, else RAM               | RAM   |
| &4000-&BFFF | RAM                                               | RAM   |
| &C000-&FFFF | selected upper ROM if enabled, else RAM (screen)  | RAM   |

Reads go through a 4-entry table, one entry per 16 KB bank (`addr >> 14`), holding either None
(RAM) or the ROM slot that answers. The table is recomputed only when the Gate Array changes
the ROM enables or a new upper ROM is selected. Writes always go to RAM (FR-MCH-06); 6128
banking will add a write table without changing this structure (D-14).

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
        ram: 64 KB of RAM.
        lower_rom: The OS ROM, or None (then &0000-&3FFF always reads RAM).
        upper_roms: Populated upper ROM slots.
        unpopulated_rom: Slot that answers for unpopulated ROM numbers (D-10).
        lower_enabled: Lower ROM visible (Gate Array RMR bit 2 = 0).
        upper_enabled: Upper ROM visible (Gate Array RMR bit 3 = 0).
        selected_upper: Last value written to the ROM select port (&DFxx).
    """

    def __init__(self, unpopulated_rom: int = 0) -> None:
        """Create the map with cleared RAM, no ROMs and both ROMs enabled (power-on state)."""
        self.ram = bytearray(0x10000)
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
