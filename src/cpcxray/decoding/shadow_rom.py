"""Shadow ROM and M1/data access map of a scrambled ROM (design.md §6.5; D-17, D-18, D-19).

Only ROM slots with a decoder have a shadow (D-17). Every read of the slot is recorded:

* the access class per byte (`access_map`, D-19): &7F unknown (initial), &01 read with M1,
  &00 read as data, &FF both (error). Update rule per read: unknown → the read's class; same
  class → unchanged; the other class → &FF. The class is independent of the decode switch.
* the clear value per byte, separately for M1 reads (raw = clear) and data reads (decoded), so a
  conflict keeps both values (D-18). Reads made with decoding switched off are counted
  separately and never enter the clear values.
* the PC of the first instruction that read the byte and the number of reads (diagnostics).

A conflict (&FF) is *real* where the decode mask is non-zero: a plain replacement ROM can hold
only one value there. Where A2 = A4 = 0 the mask is zero and both readings agree: harmless.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass

from cpcxray.decoding.vortex import vortex_mask

ROM_SIZE = 0x4000
UNKNOWN = 0x7F
M1 = 0x01
DATA = 0x00
ERROR = 0xFF
CLASS_NAMES = {UNKNOWN: "unknown", M1: "M1", DATA: "data", ERROR: "conflict"}
NO_PC = 0x10000  # first_pc value for bytes never read


def merge_class(current: int, new: int) -> int:
    """Combine two access classes (the D-19 update and merge rule)."""
    if current in (UNKNOWN, new):
        return new
    if new == UNKNOWN:
        return current
    return ERROR


@dataclass(frozen=True)
class Conflict:
    """A byte read both with M1 and as data (D-18).

    Attributes:
        offset: ROM offset 0-&3FFF.
        raw: The byte in the ROM image.
        as_m1: Value seen by M1 fetches (= raw).
        as_data: Value seen by data reads (decoded).
        harmless: The decode mask is zero here, so both values are the same.
        first_pc: PC of the first instruction that read the byte.
    """

    offset: int
    raw: int
    as_m1: int
    as_data: int
    harmless: bool
    first_pc: int | None


class ShadowRom:  # pylint: disable=too-many-instance-attributes  # one array per recorded fact
    """Recorded reads of one scrambled ROM.

    Attributes:
        raw: The scrambled ROM image.
        access_map: Access class per byte (D-19).
        m1_values: Value of the last M1 read per byte (only valid where read with M1).
        data_values: Decoded value of the last data read per byte.
        undecoded_reads: Number of reads made with decoding switched off, per byte.
        first_pc: PC of the first reading instruction per byte (NO_PC if never read).
        hit_count: Number of reads per byte.
        instruction_pc: PC of the instruction being executed (set by the machine).
    """

    def __init__(self, raw: bytes) -> None:
        """Create an empty shadow for the ROM image `raw`."""
        self.raw = raw
        self.access_map = bytearray([UNKNOWN] * ROM_SIZE)
        self.m1_values = bytearray(ROM_SIZE)
        self.data_values = bytearray(ROM_SIZE)
        self.undecoded_reads = array("I", bytes(4 * ROM_SIZE))
        self.first_pc = array("I", [NO_PC] * ROM_SIZE)
        self.hit_count = array("I", bytes(4 * ROM_SIZE))
        self.instruction_pc = 0

    def record(self, offset: int, value: int, is_m1: bool, decoding: bool) -> None:
        """Record one read (called by the ROM slot for every read).

        Args:
            offset: ROM offset.
            value: The value the CPU received.
            is_m1: The read was an opcode fetch.
            decoding: The decode switch was on.
        """
        new_class = M1 if is_m1 else DATA
        current = self.access_map[offset]
        if current != new_class:
            self.access_map[offset] = merge_class(current, new_class)
        if decoding:
            if is_m1:
                self.m1_values[offset] = value
            else:
                self.data_values[offset] = value
        else:
            self.undecoded_reads[offset] += 1
        if self.first_pc[offset] == NO_PC:
            self.first_pc[offset] = self.instruction_pc
        self.hit_count[offset] += 1

    def merge_access_map(self, other: bytes) -> None:
        """Merge an access map (e.g. loaded from a .m1map file) into this one (D-19)."""
        if len(other) != ROM_SIZE:
            raise ValueError(f"access map has {len(other)} bytes, expected {ROM_SIZE}")
        self.access_map[:] = bytes(
            merge_class(a, b) for a, b in zip(self.access_map, other, strict=True)
        )

    def conflicts(self) -> list[Conflict]:
        """Return all bytes read both with M1 and as data."""
        result = []
        for offset, access_class in enumerate(self.access_map):
            if access_class == ERROR:
                raw = self.raw[offset]
                mask = vortex_mask(offset)
                first = self.first_pc[offset]
                first_pc = None if first == NO_PC else first
                result.append(Conflict(offset, raw, raw, raw ^ mask, mask == 0, first_pc))
        return result

    def counts(self) -> dict[str, int]:
        """Return the number of bytes per access class."""
        totals = dict.fromkeys(CLASS_NAMES.values(), 0)
        for access_class in self.access_map:
            totals[CLASS_NAMES[access_class]] += 1
        return totals
