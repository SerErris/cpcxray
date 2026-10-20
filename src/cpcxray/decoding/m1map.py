"""M1/data access map files and decoding from the map (design.md §6.5; D-19, D-20, FR-DEC-04).

File format `<rom>.m1map` (D-19): exactly 16,384 bytes, byte i describes ROM offset i:
&7F unknown, &01 read with M1, &00 read as data, &FF both (error). It loads into a bytearray in
one call, lines up 1:1 with the ROM in a hex editor, and `cmp` shows differences. Maps from
several runs are merged with the update rule (unknown + X = X, M1 + data = &FF).

Decoding from the map works without an emulator run: M1 bytes are clear already, data bytes
are decoded, and bytes never read (or in conflict) follow a fill policy (D-20):

* "raw": keep the scrambled byte;
* "data": decode as if it were data;
* "ff": &FF;
* "static": code/data guess by recursive disassembly - needs the classifier of phase 8.

Conflict bytes (&FF) take the M1 value unless `conflict="data"`: in a plain replacement ROM a
byte can hold only one value (D-18), and the owner decides which.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cpcxray.decoding.shadow_rom import CLASS_NAMES, DATA, ERROR, M1, ROM_SIZE, UNKNOWN, merge_class

FILL_POLICIES = ("raw", "data", "ff", "static")


class AccessMapError(ValueError):
    """Wrong file size, unknown fill policy or class value."""


def load_m1map(path: str | Path) -> bytes:
    """Read a .m1map file (16,384 bytes of class values).

    Raises:
        AccessMapError: If the size or a value is wrong.
    """
    data = Path(path).read_bytes()
    if len(data) != ROM_SIZE:
        raise AccessMapError(f"{path}: {len(data)} bytes, expected {ROM_SIZE}")
    invalid = set(data) - set(CLASS_NAMES)
    if invalid:
        raise AccessMapError(f"{path}: invalid class values {sorted(invalid)}")
    return data


def save_m1map(path: str | Path, access_map: bytes) -> None:
    """Write an access map as a .m1map file."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(access_map))


def merge_maps(first: bytes, second: bytes) -> bytes:
    """Merge two access maps with the D-19 rule."""
    return bytes(merge_class(a, b) for a, b in zip(first, second, strict=True))


@dataclass(frozen=True)
class AccessRange:
    """A run of consecutive bytes with the same access class.

    Attributes:
        first: First ROM offset.
        last: Last ROM offset.
        access_class: Class name ("unknown", "M1", "data", "conflict").
    """

    first: int
    last: int
    access_class: str

    @property
    def length(self) -> int:
        """Number of bytes."""
        return self.last - self.first + 1


def access_ranges(access_map: bytes) -> list[AccessRange]:
    """Split the map into runs of equal class."""
    ranges = []
    start = 0
    for offset in range(1, ROM_SIZE + 1):
        if offset == ROM_SIZE or access_map[offset] != access_map[start]:
            ranges.append(AccessRange(start, offset - 1, CLASS_NAMES[access_map[start]]))
            start = offset
    return ranges


def ranges_text(ranges: list[AccessRange], base: int = 0xC000) -> str:
    """Format ranges like "C000-C003  data         4" (addresses = base + offset)."""
    return "".join(
        f"{base + r.first:04X}-{base + r.last:04X}  {r.access_class:<9} {r.length:5}\n"
        for r in ranges
    )


def ranges_json(ranges: list[AccessRange], base: int = 0xC000) -> str:
    """Format ranges as JSON (addresses as 4-digit hex strings)."""
    return json.dumps(
        [{"first": f"{base + r.first:04X}", "last": f"{base + r.last:04X}",
          "class": r.access_class, "length": r.length} for r in ranges],
        indent=1,
    )  # fmt: skip


def decode_rom(
    raw: bytes,
    access_map: bytes,
    decode: Callable[[int, int], int],
    fill_policy: str = "raw",
    conflict: str = "m1",
) -> bytes:
    """Build the clear ROM from the scrambled image and the access map (FR-DEC-04).

    Args:
        raw: The scrambled ROM image.
        access_map: Class per byte.
        decode: The decode rule, e.g. `vortex_decode`.
        fill_policy: For never-read bytes: "raw", "data" or "ff" ("static" from phase 8).
        conflict: Value for conflict bytes: "m1" (raw, as fetched) or "data" (decoded).

    Raises:
        AccessMapError: For an unknown or not yet available policy.
    """
    if fill_policy not in FILL_POLICIES or conflict not in ("m1", "data"):
        raise AccessMapError(f"unknown fill policy {fill_policy!r} / conflict choice {conflict!r}")
    if fill_policy == "static":
        raise AccessMapError("fill policy 'static' needs the code/data classifier (phase 8)")
    clear = bytearray(raw)
    for offset, access_class in enumerate(access_map):
        if access_class == DATA or (access_class == ERROR and conflict == "data"):
            clear[offset] = decode(offset, raw[offset])
        elif access_class == UNKNOWN:
            if fill_policy == "data":
                clear[offset] = decode(offset, raw[offset])
            elif fill_policy == "ff":
                clear[offset] = 0xFF
        # M1 and conflicts resolved as M1 keep the raw byte, which is the clear opcode
    return bytes(clear)


__all__ = [
    "DATA", "ERROR", "FILL_POLICIES", "M1", "UNKNOWN", "AccessMapError", "AccessRange",
    "access_ranges", "decode_rom", "load_m1map", "merge_maps", "ranges_json", "ranges_text",
    "save_m1map",
]  # fmt: skip
