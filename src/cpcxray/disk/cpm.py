"""CP/M file system on disk images: directory and file contents (host side, no emulation).

AMSDOS and VDOS store files the CP/M way. With the disk format known, the directory and every
file can be read straight from the image - the reference against which the emulated FDC, AMSDOS
and VDOS are checked, and a tool for the owner (`disk_catalog`, `disk_read_file`).

| Format   | Sector IDs | Heads | Reserved tracks          | Block | Directory  |
|----------|------------|-------|--------------------------|-------|------------|
| `system` | &41-&49    | 1     | 2 (boot, CP/M)           | 1 KB  | 64 entries |
| `data`   | &C1-&C9    | 1     | 0                        | 1 KB  | 64 entries |
| `vortex` | &01-&09    | 2     | cylinder 0, both heads   | 4 KB  | 128 entries|

All have 9 sectors of 512 bytes per track. Logical order: cylinder by cylinder, head 0 before
head 1, sectors by ID (the physical interleave does not matter). The Vortex layout was checked
against a real VDOS disk (Vortex_init.dsk: READ.ME reads as text).

Directory entry: user (&E5 = free), name 8 + type 3 (bit 7 = attributes), EX, S1, S2, RC and
16 one-byte block numbers (8-bit pointers: fewer than 256 blocks). An entry covers EXM + 1
logical 16 KB extents; it holds (EX & EXM) * 128 + RC records of 128 bytes.

Files written by AMSDOS/VDOS from BASIC start with a 128-byte AMSDOS header (file type, load
and entry address, length, checksum over bytes 0-66); `amsdos_header` recognises it.
"""

from __future__ import annotations

from dataclasses import dataclass

from cpcxray.disk.dsk import DiskImage, Sector

RECORD = 128
FREE = 0xE5
ENTRY_SIZE = 32
AMSDOS_TYPES = {0: "BASIC", 1: "BASIC (protected)", 2: "binary", 3: "binary (protected)",
                4: "screen", 6: "ASCII", 0x16: "ASCII"}  # fmt: skip


@dataclass(frozen=True)
class CpmFormat:  # pylint: disable=too-many-instance-attributes  # one field per parameter
    """Geometry and file system parameters of a disk format.

    Attributes:
        name: "system", "data" or "vortex".
        first_sector_id: ID of the first sector of a track.
        heads: Heads used.
        reserved_tracks: Logical tracks (cylinder x heads) before the directory.
        block_size: Allocation block size in bytes.
        directory_entries: Number of directory entries.
    """

    name: str
    first_sector_id: int
    heads: int
    reserved_tracks: int
    block_size: int
    directory_entries: int
    sectors_per_track: int = 9
    sector_size: int = 512

    @property
    def extent_mask(self) -> int:
        """EXM: logical 16 KB extents per directory entry minus one (8-bit block pointers)."""
        return 16 * self.block_size // 16384 - 1


FORMATS = {
    "system": CpmFormat("system", 0x41, 1, 2, 1024, 64),
    "data": CpmFormat("data", 0xC1, 1, 0, 1024, 64),
    "vortex": CpmFormat("vortex", 0x01, 2, 2, 4096, 128),
}


class CpmError(ValueError):
    """The image does not have a known CP/M format, or a file is missing."""


@dataclass(frozen=True)
class CpmFile:
    """A file in the directory.

    Attributes:
        user: User number 0-15.
        name: "NAME.EXT" (attribute bits removed).
        size: Size in bytes (records x 128).
        read_only: R/O attribute (bit 7 of the first type character).
        system: SYS attribute (bit 7 of the second type character).
        blocks: Allocation blocks in file order.
    """

    user: int
    name: str
    size: int
    read_only: bool
    system: bool
    blocks: tuple[int, ...]


def detect_format(image: DiskImage) -> CpmFormat:
    """Recognise the format from the sector IDs of cylinder 0, head 0 (and the head count).

    Raises:
        CpmError: If the IDs match no known format.
    """
    track = image.track(0, 0)
    if track is None:
        raise CpmError("cylinder 0 is not formatted")
    lowest = min(sector.sector_id for sector in track.sectors)
    for cpm_format in FORMATS.values():
        if lowest == cpm_format.first_sector_id and image.heads >= cpm_format.heads:
            return cpm_format
    raise CpmError(f"unknown disk format (first sector ID &{lowest:02X}, {image.heads} heads)")


class CpmDisk:
    """Read access to the CP/M file system of an image."""

    def __init__(self, image: DiskImage, cpm_format: CpmFormat | None = None) -> None:
        """Use `cpm_format`, or detect it from the image."""
        self.image = image
        self.format = cpm_format or detect_format(image)

    def sector(self, number: int) -> Sector:
        """The sector holding logical sector `number` (0 = first after the reserved tracks).

        Raises:
            CpmError: If the sector is missing from the image.
        """
        fmt = self.format
        track, index = divmod(number, fmt.sectors_per_track)
        cylinder, head = divmod(track + fmt.reserved_tracks, fmt.heads)
        id_field = (cylinder, head, fmt.first_sector_id + index, 0xFF)
        sector = self.image.find_sector(cylinder, head, id_field)
        if sector is None:
            raise CpmError(f"sector C{cylinder} H{head} R&{id_field[2]:02X} missing")
        return sector

    def logical_sector(self, number: int) -> bytes:
        """The data of a logical sector (0 = first sector after the reserved tracks)."""
        return self.sector(number).read()

    def block(self, number: int) -> bytes:
        """The data of an allocation block."""
        per_block = self.format.block_size // self.format.sector_size
        return b"".join(self.logical_sector(number * per_block + i) for i in range(per_block))

    def directory_entries(self) -> list[bytes]:
        """All 32-byte directory entries (including free ones)."""
        size = self.format.directory_entries * ENTRY_SIZE
        blocks = -(-size // self.format.block_size)
        data = b"".join(self.block(number) for number in range(blocks))[:size]
        return [data[i : i + ENTRY_SIZE] for i in range(0, size, ENTRY_SIZE)]

    def files(self) -> list[CpmFile]:
        """The files of all users, sorted by user and name."""
        extents: dict[tuple[int, str], list[bytes]] = {}
        for entry in self.directory_entries():
            if entry[0] < 16:
                extents.setdefault((entry[0], entry_name(entry)), []).append(entry)
        files = []
        mask = self.format.extent_mask
        for (user, name), entries in sorted(extents.items()):
            entries.sort(key=lambda e: (e[14], e[12]))  # S2, EX
            records = sum((e[12] & mask) * 128 + e[15] for e in entries)
            blocks = tuple(b for e in entries for b in e[16:32] if b)
            first = entries[0]
            files.append(CpmFile(user, name, records * RECORD, bool(first[9] & 0x80),
                                 bool(first[10] & 0x80), blocks))  # fmt: skip
        return files

    def read_file(self, name: str, user: int = 0) -> bytes:
        """The contents of a file (whole records; text files end at the first &1A).

        Raises:
            CpmError: If there is no such file.
        """
        wanted = name.upper()
        for found in self.files():
            if found.user == user and found.name == wanted:
                data = b"".join(self.block(number) for number in found.blocks)
                return data[: found.size]
        raise CpmError(f"no file {wanted!r} for user {user}")


def entry_name(entry: bytes) -> str:
    """Return "NAME.EXT" of a directory entry, attribute bits removed."""
    base = bytes(b & 0x7F for b in entry[1:9]).decode("ascii", "replace").rstrip()
    extension = bytes(b & 0x7F for b in entry[9:12]).decode("ascii", "replace").rstrip()
    return f"{base}.{extension}"


def amsdos_header(data: bytes) -> dict[str, object] | None:
    """Decode the 128-byte AMSDOS header of a file, or None if there is none (checksum)."""
    if len(data) < RECORD:
        return None
    if sum(data[:67]) & 0xFFFF != int.from_bytes(data[67:69], "little"):
        return None
    file_type = data[18]
    return {
        "user": data[0],
        "name": entry_name(data[0:12]),
        "type": AMSDOS_TYPES.get(file_type, f"&{file_type:02X}"),
        "load_address": int.from_bytes(data[21:23], "little"),
        "entry_address": int.from_bytes(data[26:28], "little"),
        "length": int.from_bytes(data[64:67], "little"),
    }
