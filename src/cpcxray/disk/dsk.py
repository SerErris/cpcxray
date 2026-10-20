"""Disk images: standard DSK, Extended DSK and raw Vortex dumps (design.md §6.11; FR-FDC-02..04).

A `DiskImage` holds the physical tracks of a disk, each with its sectors in the order they pass
the head, and every sector with its ID (C, H, R, N), the two FDC status bytes and its data. The
FDC finds sectors by ID, as the real µPD765 does, so interleaved formats (Vortex: 1,6,2,7,…)
and copy protection IDs work.

Formats:

* **Extended DSK** ("EXTENDED CPC DSK File"): per-track sizes, per-sector data lengths and
  status bytes. Read and written (saving always writes EDSK).
* **Standard DSK** ("MV - CPC"): one track size for all tracks; sector size from N. Read.
* **Raw Vortex image**: exactly 737,280 bytes = 80 cylinders x 2 heads x 9 sectors x 512 bytes,
  sector IDs &01-&09, sector *id* of cylinder *c*, head *h* at `((c * 2 + h) * 9 + id - 1) * 512`.
  Read.

Write protection (FR-FDC-04, owner's rule for real images): an image is read-only unless it was
created as a draft (`blank_image`) or explicitly loaded writable. The FDC reports a read-only
image as write protected; nothing is ever written back to a file except by `save_image` to a
path the caller chooses.

Blank formats (`blank_image`): "vortex" (80 x 2 x 9 x 512, IDs &01-&09, interleave 1,6,2,…),
"data" (40 x 1 x 9 x 512, IDs &C1-&C9) and "system" (IDs &41-&49), all filled with &E5.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

EDSK_SIGNATURE = b"EXTENDED CPC DSK File\r\nDisk-Info\r\n"
DSK_SIGNATURE = b"MV - CPC"
TRACK_SIGNATURE = b"Track-Info\r\n"
HEADER_SIZE = 0x100
CREATOR = b"cpcxray"
RAW_VORTEX_SIZE = 80 * 2 * 9 * 512
FILLER = 0xE5
SECTOR_INFO_LIMIT = 29  # sector entries that fit into the 256-byte track header

# blank formats: cylinders, heads, first sector ID, gap 3; all 9 sectors of 512 bytes
BLANK_FORMATS = {
    "vortex": (80, 2, 0x01, 0x2A),
    "data": (40, 1, 0xC1, 0x52),
    "system": (40, 1, 0x41, 0x52),
}
INTERLEAVE = (0, 5, 1, 6, 2, 7, 3, 8, 4)  # physical order 1,6,2,7,3,8,4,9,5 of sector indexes


class DiskImageError(ValueError):
    """Not a usable disk image."""


def sector_size(size_code: int) -> int:
    """Bytes of a sector with size code N (128 << N, N limited to 8 as on the µPD765)."""
    return 128 << min(size_code, 8)


@dataclass
class Sector:
    """One sector as recorded on the track.

    Attributes:
        cylinder: C of the ID field (may differ from the physical track).
        head: H of the ID field.
        sector_id: R, the sector number searched by read/write commands.
        size_code: N; the nominal size is 128 << N.
        status1: ST1 the FDC reports for this sector (EDSK: e.g. data error flags).
        status2: ST2 (e.g. &40 control mark for deleted data).
        data: The data as stored (EDSK may store less or more than the nominal size).
    """

    cylinder: int
    head: int
    sector_id: int
    size_code: int
    status1: int = 0
    status2: int = 0
    data: bytearray = field(default_factory=bytearray)

    def id_field(self) -> tuple[int, int, int, int]:
        """(C, H, R, N)."""
        return self.cylinder, self.head, self.sector_id, self.size_code

    def read(self) -> bytes:
        """The nominal number of bytes (stored data, padded with the filler byte)."""
        size = sector_size(self.size_code)
        data = bytes(self.data[:size])
        return data + bytes([FILLER]) * (size - len(data))


@dataclass
class Track:
    """A physical track: sectors in the order they pass the head.

    Attributes:
        sectors: The sectors (empty: unformatted).
        gap3: Gap 3 length used when it was formatted.
        filler: Filler byte used when it was formatted.
    """

    sectors: list[Sector] = field(default_factory=list)
    gap3: int = 0x4E
    filler: int = FILLER


@dataclass
class DiskImage:
    """A disk: physical tracks by (cylinder, head).

    Attributes:
        tracks: Track per (physical cylinder, head); missing = unformatted.
        cylinders: Number of cylinders in the image.
        heads: Number of heads (sides) in the image.
        source: File the image was loaded from (None for a draft).
        writable: Writes are allowed (else the FDC reports write protection).
        modified: Written since loading/creating (only meaningful for writable images).
    """

    tracks: dict[tuple[int, int], Track] = field(default_factory=dict)
    cylinders: int = 0
    heads: int = 1
    source: Path | None = None
    writable: bool = False
    modified: bool = False

    def track(self, cylinder: int, head: int) -> Track | None:
        """The physical track, or None if not present / unformatted."""
        track = self.tracks.get((cylinder, head))
        return track if track is not None and track.sectors else None

    def find_sector(
        self, cylinder: int, head: int, id_field: tuple[int, int, int, int]
    ) -> Sector | None:
        """Find a sector by its ID (C, H, R, N) on a physical track.

        N is not compared when it is &FF (callers that do not care), otherwise it must match.
        """
        track = self.track(cylinder, head)
        if track is None:
            return None
        want_c, want_h, want_r, want_n = id_field
        for sector in track.sectors:
            if (sector.cylinder, sector.head, sector.sector_id) == (want_c, want_h, want_r) and (
                want_n == 0xFF or sector.size_code == want_n
            ):
                return sector
        return None

    def format_track(
        self, cylinder: int, head: int, ids: list[tuple[int, int, int, int]], filler: int,
        gap3: int,
    ) -> None:  # fmt: skip
        """Replace a track with freshly formatted sectors (FORMAT TRACK)."""
        sectors = [
            Sector(c, h, r, n, data=bytearray([filler]) * sector_size(n)) for c, h, r, n in ids
        ]
        self.tracks[(cylinder, head)] = Track(sectors, gap3, filler)
        self.cylinders = max(self.cylinders, cylinder + 1)
        self.heads = max(self.heads, head + 1)
        self.modified = True

    def logical_sectors(self) -> list[Sector]:
        """All sectors, cylinder by cylinder, head by head, sorted by sector ID per track."""
        result = []
        for cylinder in range(self.cylinders):
            for head in range(self.heads):
                track = self.track(cylinder, head)
                if track is not None:
                    result += sorted(track.sectors, key=lambda sector: sector.sector_id)
        return result


# -- loading -------------------------------------------------------------------------------------


def load_image(path: str | Path, writable: bool = False) -> DiskImage:
    """Load a DSK, EDSK or raw Vortex image.

    Args:
        path: The image file.
        writable: Allow writes (in memory; saving needs an explicit `save_image`).

    Raises:
        DiskImageError: If the file is not a recognised image.
    """
    file = Path(path)
    data = file.read_bytes()
    if data.startswith(EDSK_SIGNATURE[:8]) and data[8:21] == EDSK_SIGNATURE[8:21]:
        image = _load_dsk(data, extended=True)
    elif data.startswith(DSK_SIGNATURE):
        image = _load_dsk(data, extended=False)
    elif len(data) == RAW_VORTEX_SIZE:
        image = _load_raw_vortex(data)
    else:
        raise DiskImageError(f"{file}: not a DSK, EDSK or raw Vortex image")
    image.source = file
    image.writable = writable
    return image


def _load_dsk(data: bytes, extended: bool) -> DiskImage:
    """Parse a standard or extended DSK file."""
    cylinders, heads = data[0x30], data[0x31]
    if not 1 <= heads <= 2:
        raise DiskImageError(f"unsupported number of sides: {heads}")
    image = DiskImage(cylinders=cylinders, heads=heads)
    position = HEADER_SIZE
    for index in range(cylinders * heads):
        standard_size = int.from_bytes(data[0x32:0x34], "little")
        size = data[0x34 + index] * 256 if extended else standard_size
        if size == 0:
            continue
        if data[position : position + 10] != TRACK_SIGNATURE[:10]:
            raise DiskImageError(f"track block {index} at {position:#x} has no Track-Info")
        cylinder, head = divmod(index, heads)
        image.tracks[(cylinder, head)] = _load_track(data, position, extended)
        position += size
    return image


def _load_track(data: bytes, position: int, extended: bool) -> Track:
    """Parse one Track-Info block and its sector data."""
    info = data[position : position + 256]
    count = min(info[0x15], SECTOR_INFO_LIMIT)
    track = Track(gap3=info[0x16], filler=info[0x17])
    offset = position + 256
    for number in range(count):
        entry = info[0x18 + 8 * number : 0x20 + 8 * number]
        length = entry[6] | entry[7] << 8 if extended else sector_size(info[0x14])
        if extended and length == 0:
            length = sector_size(entry[3])
        sector_data = bytearray(data[offset : offset + length])
        track.sectors.append(Sector(entry[0], entry[1], entry[2], entry[3], entry[4], entry[5],
                                    sector_data))  # fmt: skip
        offset += length
    return track


def _load_raw_vortex(data: bytes) -> DiskImage:
    """A raw dump in the Vortex layout (design.md §6.11)."""
    image = DiskImage(cylinders=80, heads=2)
    for cylinder in range(80):
        for head in range(2):
            track = Track(gap3=0x2A)
            for index in INTERLEAVE:
                offset = ((cylinder * 2 + head) * 9 + index) * 512
                sector_data = bytearray(data[offset : offset + 512])
                track.sectors.append(Sector(cylinder, head, index + 1, 2, data=sector_data))
            image.tracks[(cylinder, head)] = track
    return image


# -- saving and creating -----------------------------------------------------------------------


def to_edsk(image: DiskImage) -> bytes:
    """Serialise an image as Extended DSK."""
    blocks = []
    sizes = []
    for cylinder in range(image.cylinders):
        for head in range(image.heads):
            track = image.track(cylinder, head)
            if track is None:
                sizes.append(0)
                continue
            block = _track_block(cylinder, head, track)
            blocks.append(block)
            sizes.append(len(block) // 256)
    if any(size > 255 for size in sizes):
        raise DiskImageError("a track is too large for EDSK")
    header = bytearray(HEADER_SIZE)
    header[0 : len(EDSK_SIGNATURE)] = EDSK_SIGNATURE
    header[0x22 : 0x22 + len(CREATOR)] = CREATOR
    header[0x30], header[0x31] = image.cylinders, image.heads
    header[0x34 : 0x34 + len(sizes)] = bytes(sizes)
    return bytes(header) + b"".join(blocks)


def _track_block(cylinder: int, head: int, track: Track) -> bytes:
    """One Track-Info block plus data, padded to a multiple of 256 bytes."""
    info = bytearray(256)
    info[0 : len(TRACK_SIGNATURE)] = TRACK_SIGNATURE
    info[0x10], info[0x11] = cylinder, head
    sectors = track.sectors[:SECTOR_INFO_LIMIT]
    info[0x14] = sectors[0].size_code
    info[0x15], info[0x16], info[0x17] = len(sectors), track.gap3, track.filler
    data = bytearray()
    for number, sector in enumerate(sectors):
        entry = bytes([*sector.id_field(), sector.status1, sector.status2]) + len(
            sector.data
        ).to_bytes(2, "little")
        info[0x18 + 8 * number : 0x20 + 8 * number] = entry
        data += sector.data
    block = bytes(info) + bytes(data)
    return block + bytes(-len(block) % 256)


def save_image(image: DiskImage, path: str | Path) -> int:
    """Write the image as EDSK to `path`; returns the file size.

    Raises:
        DiskImageError: If `path` is the file a read-only image was loaded from (real images
            are never overwritten).
    """
    file = Path(path)
    if image.source is not None and not image.writable and file.resolve() == image.source.resolve():
        raise DiskImageError(f"{file} was loaded read-only; save the image to another file")
    data = to_edsk(image)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(data)
    image.modified = False
    return len(data)


def blank_image(disk_format: str) -> DiskImage:
    """A formatted, empty, writable draft image ("vortex", "data" or "system").

    Raises:
        DiskImageError: For an unknown format.
    """
    if disk_format not in BLANK_FORMATS:
        raise DiskImageError(f"format must be one of {', '.join(BLANK_FORMATS)}")
    cylinders, heads, first_id, gap3 = BLANK_FORMATS[disk_format]
    image = DiskImage(cylinders=cylinders, heads=heads, writable=True)
    order = INTERLEAVE if disk_format == "vortex" else range(9)
    for cylinder in range(cylinders):
        for head in range(heads):
            ids = [(cylinder, head, first_id + index, 2) for index in order]
            image.format_track(cylinder, head, ids, FILLER, gap3)
    image.modified = False
    return image
