"""Session disk tools: insert, eject, drafts, save, host-side catalogue and file reading."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.disk.cpm import CpmDisk
from cpcxray.disk.dsk import blank_image, load_image, save_image
from cpcxray.session.errors import SessionError
from cpcxray.session.session import Session
from tests.analysis.helpers import make_test_cpc
from tests.disk.test_cpm import write_file


def test_drives_insert_eject_and_drafts(tmp_path: Path) -> None:
    """Drafts are writable and inserted; saving and re-inserting a file keeps the data."""
    session = Session(make_test_cpc({}, {}), project_dir=tmp_path)
    assert [d["disk"] for d in session.disk_list()["drives"]] == [None, None]
    session.disk_new("b", "vortex")
    image = session.machine.fdc.drives[1].image  # type: ignore[attr-defined]
    write_file(CpmDisk(image), 0, b"NOTE    TXT", [1], b"hello disk\x1a")
    assert session.disk_catalog("B")["files"][0]["name"] == "NOTE.TXT"
    assert session.disk_read_file("NOTE.TXT", "B")["text"].startswith("hello disk")
    saved = session.disk_save("B", "out/draft.dsk")
    session.disk_eject("B")
    session.disk_insert("A:", "out/draft.dsk")
    drive_a = session.disk_list()["drives"][0]
    assert drive_a["disk"] == saved["path"] and not drive_a["writable"]
    result = session.disk_read_file("NOTE.TXT", path="out/draft.dsk", out_path="out/note.txt")
    assert (tmp_path / "out" / "note.txt").read_bytes() == b"hello disk\x1a".ljust(
        128, b"\xe5"
    )  # rest of the record: format filler
    assert result["amsdos_header"] is None


def test_errors(tmp_path: Path) -> None:
    """Bad drive letters, empty drives, unknown formats and the real-image guard."""
    session = Session(make_test_cpc({}, {}), project_dir=tmp_path)
    with pytest.raises(SessionError, match="drive must be"):
        session.disk_eject("C")
    with pytest.raises(SessionError, match="no disk"):
        session.disk_catalog("A")
    with pytest.raises(SessionError, match="format"):
        session.disk_new("A", "amiga")
    real = tmp_path / "real.dsk"
    save_image(blank_image("data"), real)
    session.disk_insert("A", str(real))  # read-only
    with pytest.raises(SessionError, match="read-only"):
        session.disk_save("A", str(real))
    session.disk_new("B", "vortex")
    with pytest.raises(SessionError, match="give a file name"):
        session.disk_save("B", "  ")  # the GUI's empty path field
    with pytest.raises(SessionError, match="is a directory"):
        session.disk_save("B", str(tmp_path))
    with pytest.raises(SessionError, match="draft"):
        session.disk_save("B")  # a draft has no file yet
    with pytest.raises(SessionError, match="read-only"):
        session.disk_save("A")  # never over a read-only image
    session.disk_insert("A", str(real), writable=True)
    session.disk_new("B", "data")
    image_a = session.machine.fdc.drives[0].image  # type: ignore[attr-defined]
    image_a.find_sector(0, 0, (0, 0, 0xC1, 2)).data[0] = 0x42
    assert session.disk_save("A")["path"] == str(real)  # writable: back to its own file
    saved = load_image(real).find_sector(0, 0, (0, 0, 0xC1, 2))
    assert saved is not None and saved.data[0] == 0x42  # the change is in the file
    with pytest.raises(SessionError, match="not a DSK"):
        (tmp_path / "bad.dsk").write_bytes(b"x")
        session.disk_insert("A", "bad.dsk")


def test_save_as_moves_the_drive_to_the_new_file(tmp_path: Path) -> None:
    """Save as writes a writable copy the drive uses from then on; Save clears "changed"."""
    session = Session(make_test_cpc({}, {}), project_dir=tmp_path)
    real = tmp_path / "real.dsk"
    save_image(blank_image("data"), real)
    original = real.read_bytes()
    session.disk_insert("A", str(real))  # read-only, like the owner's real images
    image = session.machine.fdc.drives[0].image  # type: ignore[attr-defined]
    image.modified = True  # as after a write by the CPC
    saved = session.disk_save("A", "copy.dsk")
    assert (saved["writable"], saved["modified"]) == (True, False)
    drive_a = session.disk_list()["drives"][0]
    assert drive_a["disk"] == str(tmp_path / "copy.dsk") and drive_a["writable"]
    image.find_sector(0, 0, (0, 0, 0xC1, 2)).data[0] = 0x42
    image.modified = True
    assert session.disk_save("A")["path"] == str(tmp_path / "copy.dsk")  # Save: the copy
    assert not session.disk_list()["drives"][0]["modified"]
    assert real.read_bytes() == original  # the old file stays untouched


def test_binary_file_entry_code(tmp_path: Path) -> None:
    """A binary file's entry is disassembled with symbol names; numbers stay numbers."""
    session = Session(make_test_cpc({}, {}), project_dir=tmp_path)
    session.disk_new("A", "data")
    header = bytearray(128)
    header[1:12] = b"PROG    BIN"
    header[18] = 2  # binary
    header[21:23] = header[26:28] = (0x4000).to_bytes(2, "little")  # load = entry
    code = bytes.fromhex("010000 CD5ABB C9")  # ld bc,#0000 / call TXT_OUTPUT / ret
    header[64:67] = len(code).to_bytes(3, "little")
    header[67:69] = (sum(header[:67]) & 0xFFFF).to_bytes(2, "little")
    image = session.machine.fdc.drives[0].image  # type: ignore[attr-defined]
    write_file(CpmDisk(image), 0, b"PROG    BIN", [2], bytes(header) + code)
    entry = session.disk_read_file("PROG.BIN", "A")["entry"]
    assert entry["address"] == "4000" and entry["bytes"].startswith("01 00 00 CD 5A BB C9")
    assert entry["code"][:3] == [
        {"address": "4000", "text": "ld bc,#0000"},
        {"address": "4003", "text": "call TXT_OUTPUT", "label": "TXT_OUTPUT", "label_type": "FW"},
        {"address": "4006", "text": "ret"},
    ]
    catalog = session.disk_catalog("A")
    assert (catalog["capacity_kb"], catalog["used_kb"], catalog["free_kb"]) == (178, 1, 177)


def test_cpm_and_ascii_basic_programs(tmp_path: Path) -> None:
    """Files without a header: a .COM file goes to &0100 (CP/M), ASCII BASIC to &0170."""
    session = Session(make_test_cpc({}, {}), project_dir=tmp_path)
    session.disk_new("A", "data")
    image = session.machine.fdc.drives[0].image  # type: ignore[attr-defined]
    write_file(CpmDisk(image), 0, b"HELLO   COM", [2], bytes.fromhex("0E09 110901 CD0500 C9"))
    write_file(CpmDisk(image), 1, b"LIST    BAS", [3], b"10 PRINT 1\r\n\x1a")
    com = session.disk_read_file("HELLO.COM", "A")
    assert com["program"] == {"type": "CP/M program", "load_address": 0x100,
                              "entry_address": 0x100, "source": "CP/M"}  # fmt: skip
    assert [line["text"] for line in com["entry"]["code"][:4]] == [
        "ld c,#09", "ld de,#0109", "call #0005", "ret"]  # fmt: skip
    basic = session.disk_read_file("LIST.BAS", "A")
    assert basic["program"]["load_address"] == 0x170 and "entry" not in basic
