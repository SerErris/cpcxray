"""Session disk tools: insert, eject, drafts, save, host-side catalogue and file reading."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.disk.cpm import CpmDisk
from cpcxray.disk.dsk import blank_image, save_image
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
    with pytest.raises(SessionError, match="not a DSK"):
        (tmp_path / "bad.dsk").write_bytes(b"x")
        session.disk_insert("A", "bad.dsk")
