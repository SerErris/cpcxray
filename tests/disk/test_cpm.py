"""Tests of the host-side CP/M reader (directory, files, AMSDOS header) on drafts and real disks."""

from __future__ import annotations

import pytest

from cpcxray.disk.cpm import FORMATS, CpmDisk, CpmError, amsdos_header, detect_format
from cpcxray.disk.dsk import blank_image, load_image
from tests.vdos import CPM_DISK, VORTEX_DISK, requires_disks


def write_file(
    disk: CpmDisk, entry_index: int, name: bytes, blocks: list[int], data: bytes
) -> None:
    """Put a file into a draft: one directory entry (one extent) and its blocks."""
    records = -(-len(data) // 128)
    extent, last_records = divmod(records - 1, 128)
    pointers = bytes(blocks).ljust(16, b"\0")
    entry = bytes([0]) + name + bytes([extent, 0, 0, last_records + 1]) + pointers
    put_bytes(disk, 0, entry_index * 32, entry)
    block_size = disk.format.block_size
    for index, block in enumerate(blocks):
        put_bytes(disk, block, 0, data[index * block_size : (index + 1) * block_size])


def put_bytes(disk: CpmDisk, block: int, offset: int, data: bytes) -> None:
    """Write bytes into a block of the image (through the logical sector layout)."""
    fmt = disk.format
    first_sector = block * (fmt.block_size // fmt.sector_size)
    for position, value in enumerate(data):
        logical, within = divmod(offset + position, fmt.sector_size)
        disk.sector(first_sector + logical).data[within] = value


@pytest.mark.parametrize("disk_format", ["vortex", "data", "system"])
def test_files_on_drafts(disk_format: str) -> None:
    """A file written into a draft is listed and read back (format detected from the IDs)."""
    disk = CpmDisk(blank_image(disk_format))
    assert disk.format is FORMATS[disk_format] and not disk.files()
    data = bytes(range(256)) * 25  # 6400 bytes, 50 records
    blocks = [1, 2] if disk_format == "vortex" else [2, 3, 4, 5, 6, 7, 8]
    write_file(disk, 0, b"HELLO   TXT", blocks, data)
    files = disk.files()
    assert len(files) == 1
    found = files[0]
    assert (found.name, found.size, found.blocks) == ("HELLO.TXT", 6400, tuple(blocks))
    assert disk.read_file("hello.txt") == data
    with pytest.raises(CpmError, match="no file"):
        disk.read_file("NONE.TXT")


def test_extent_mask() -> None:
    """Vortex: 4 KB blocks with 8-bit pointers → 4 logical extents per entry (EXM 3)."""
    assert FORMATS["vortex"].extent_mask == 3 and FORMATS["data"].extent_mask == 0


def test_amsdos_header() -> None:
    """The header is recognised by its checksum."""
    header = bytearray(128)
    header[1:12] = b"TEST    BAS"
    header[18], header[21:23], header[64:67] = 0, (0x170).to_bytes(2, "little"), b"\x1d\0\0"
    header[67:69] = (sum(header[:67]) & 0xFFFF).to_bytes(2, "little")
    assert amsdos_header(bytes(header)) == {"user": 0, "name": "TEST.BAS", "type": "BASIC",
                                            "load_address": 0x170, "entry_address": 0,
                                            "length": 29}  # fmt: skip
    header[67] ^= 1
    assert amsdos_header(bytes(header)) is None


def test_unknown_format() -> None:
    """A disk with unexpected IDs is not guessed."""
    image = blank_image("data")
    for sector in image.track(0, 0).sectors:  # type: ignore[union-attr]
        sector.sector_id = 0x81
    with pytest.raises(CpmError, match="unknown disk format"):
        detect_format(image)


@requires_disks
def test_real_disks() -> None:
    """The owner's disks: catalogue, a text file and BASIC files read directly."""
    cpm = CpmDisk(load_image(CPM_DISK))
    assert cpm.format.name == "system" and len(cpm.files()) == 26
    assert not [f.name for f in cpm.files() if f.system]
    assert cpm.read_file("DUMP.ASM").startswith(b";\tFILE DUMP PROGRAM, READS AN INPUT FILE")
    assert amsdos_header(cpm.read_file("EX1.BAS")) is None  # an ASCII BASIC file
    vortex = CpmDisk(load_image(VORTEX_DISK))
    assert vortex.format.name == "vortex" and len(vortex.files()) == 25
    assert vortex.read_file("READ.ME").startswith(b"\r\nDa das X-Laufwerk auch ohne das X-Module")
    assert vortex.read_file("F1Z.BAS").startswith(b'10 CLS\r\n20 LOAD "f1z.bin":CALL &BF2D\r\n')
