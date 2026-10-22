"""Tests of disk image loading and saving: EDSK, standard DSK, raw Vortex, blank drafts."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.disk.dsk import (
    RAW_VORTEX_SIZE,
    DiskImageError,
    blank_image,
    load_image,
    save_image,
    to_edsk,
)
from tests.machines import CPM_DISK, VORTEX_DISK, requires_disks


def standard_dsk() -> bytes:
    """A 1-track standard DSK ("MV - CPC") with two 512-byte sectors &C1 and &C2."""
    header = bytearray(0x100)
    header[0:8] = b"MV - CPC"
    header[0x30], header[0x31] = 1, 1
    header[0x32:0x34] = (0x100 + 2 * 512).to_bytes(2, "little")
    info = bytearray(0x100)
    info[0:12] = b"Track-Info\r\n"
    info[0x14], info[0x15], info[0x16], info[0x17] = 2, 2, 0x52, 0xE5
    info[0x18:0x1C] = bytes([0, 0, 0xC1, 2])
    info[0x20:0x24] = bytes([0, 0, 0xC2, 2])
    return bytes(header) + bytes(info) + b"\x11" * 512 + b"\x22" * 512


def test_standard_dsk(tmp_path: Path) -> None:
    """A standard DSK loads with sector sizes from N."""
    path = tmp_path / "std.dsk"
    path.write_bytes(standard_dsk())
    image = load_image(path)
    sector = image.find_sector(0, 0, (0, 0, 0xC2, 2))
    assert sector is not None and sector.read() == b"\x22" * 512
    assert not image.writable and image.source == path


def test_raw_vortex_image(tmp_path: Path) -> None:
    """A raw 737,280-byte dump uses the Vortex layout (both heads interleaved per cylinder)."""
    raw = bytearray(RAW_VORTEX_SIZE)
    offset = ((5 * 2 + 1) * 9 + (3 - 1)) * 512  # cylinder 5, head 1, sector 3
    raw[offset : offset + 512] = b"\x77" * 512
    path = tmp_path / "raw.img"
    path.write_bytes(bytes(raw))
    image = load_image(path)
    assert image.cylinders == 80 and image.heads == 2
    sector = image.find_sector(5, 1, (5, 1, 3, 2))
    assert sector is not None and sector.read() == b"\x77" * 512


def test_blank_formats_and_edsk_round_trip(tmp_path: Path) -> None:
    """Drafts are writable and formatted; EDSK saving and loading keeps every sector."""
    image = blank_image("vortex")
    assert image.writable and len(image.logical_sectors()) == 80 * 2 * 9
    track = image.track(0, 0)
    assert track is not None and [s.sector_id for s in track.sectors] == [1, 6, 2, 7, 3, 8, 4, 9, 5]
    assert blank_image("data").track(0, 0).sectors[0].sector_id == 0xC1  # type: ignore[union-attr]
    path = tmp_path / "draft.dsk"
    assert save_image(image, path) == len(to_edsk(image))
    again = load_image(path)
    assert [s.data for s in again.logical_sectors()] == [s.data for s in image.logical_sectors()]
    with pytest.raises(DiskImageError, match="format"):
        blank_image("ibm")


def test_read_only_source_is_never_overwritten(tmp_path: Path) -> None:
    """Saving a read-only image over its own file is refused; another file is fine."""
    path = tmp_path / "real.dsk"
    save_image(blank_image("data"), path)
    image = load_image(path)
    with pytest.raises(DiskImageError, match="read-only"):
        save_image(image, path)
    save_image(image, tmp_path / "copy.dsk")


def test_not_an_image(tmp_path: Path) -> None:
    """Unknown files are refused."""
    path = tmp_path / "x.dsk"
    path.write_bytes(b"hello")
    with pytest.raises(DiskImageError, match="not a DSK"):
        load_image(path)


@requires_disks
def test_real_images() -> None:
    """The owner's images: 3" SYSTEM (IDs &41-&49) and Vortex (IDs 1-9, 2 heads, interleaved)."""
    cpm = load_image(CPM_DISK)
    assert (cpm.cylinders, cpm.heads) == (43, 1)
    assert sorted(s.sector_id for s in cpm.track(0, 0).sectors) == list(range(0x41, 0x4A))  # type: ignore[union-attr]
    vortex = load_image(VORTEX_DISK)
    assert (vortex.cylinders, vortex.heads) == (83, 2)
    track = vortex.track(1, 1)
    assert track is not None
    assert [s.id_field() for s in track.sectors][:2] == [(1, 1, 1, 2), (1, 1, 6, 2)]
    assert vortex.track(80, 0) is None  # cylinders 80-82 are not formatted
