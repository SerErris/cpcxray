"""Phase 10 milestone: data is read from the real disk images through the emulated FDC.

With the owner's ROMs and images (skipped when missing): VDOS lists the 3" CP/M disk in A: and
the Vortex disk in B:, loads a BASIC program from B:, refuses to write to the read-only real
disk, and saves to a draft image whose contents are then checked host-side. Running the disk
code also extends the decoded part of VDOS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.session.session import Session
from tests.vdos import boot_vdos, requires_disks, requires_roms

pytestmark = [requires_roms, requires_disks]


@pytest.fixture(name="session")
def fixture_session(tmp_path: Path) -> Session:
    """A booted CPC 464 with VDOS, 464_cpm_22_vdos.dsk in A:, vortex_test.dsk in B: (read-only)."""
    return boot_vdos(tmp_path)


def type_and_run(session: Session, text: str) -> list[str]:
    """Type a line and let the CPC process it; return the non-empty screen lines."""
    session.type_text(text, wait=False)
    session.run(max_us=6_000_000, timeout_s=300)
    return [line for line in session.screen_text()["lines"] if line.strip()]


def test_catalogue_of_both_drives(session: Session) -> None:
    """CAT on A: (3" SYSTEM format) and on B: (Vortex format) list the files of the images."""
    unknown_before = session.shadow_status()["classes"]["unknown"]
    lines = type_and_run(session, "CAT\r")
    assert "Laufwerk A: User  0" in lines
    assert "AMSDOS  .COM   1K   EX2     .BAS   1K" in lines
    assert "119K in  26 Files,  50K frei" in lines
    type_and_run(session, "CLS\r")  # VDOS's CAT waits for a key when the screen is full
    type_and_run(session, "|B\r")
    lines = type_and_run(session, "CAT\r")
    assert "Laufwerk B: User  0" in lines
    assert "F1Z     .BAS   1K   SYS     .COM   2K" in lines
    assert "172K in  28 Files, 532K frei" in lines
    assert session.shadow_status()["classes"]["unknown"] < unknown_before  # more VDOS decoded


def test_load_basic_program_from_vortex_disk(session: Session) -> None:
    """LOAD from B: brings the program into memory exactly as stored on the image."""
    type_and_run(session, "|B\r")
    type_and_run(session, 'LOAD"F1Z.BAS"\r')
    lines = type_and_run(session, "LIST\r")
    assert lines[-3:] == ["10 CLS", '20 LOAD "f1z.bin":CALL &BF2D', "Ready"]


def test_real_disk_is_write_protected_and_drafts_take_writes(session: Session) -> None:
    """SAVE to the real image fails (write protected); a draft receives the file."""
    type_and_run(session, "|B\r")
    type_and_run(session, '10 PRINT "HELLO FROM CPCXRAY"\r')
    lines = type_and_run(session, 'SAVE"TEST"\r')
    assert "Laufwerk B: Diskette hat Schreibschutz" in lines
    assert not session.disk_list()["drives"][1]["modified"]
    type_and_run(session, "N")  # do not retry
    session.disk_new("B", "vortex")
    type_and_run(session, 'SAVE"TEST"\r')
    files = session.disk_catalog("B")["files"]
    assert [f["name"] for f in files] == ["TEST.BAS"]
    header = session.disk_read_file("TEST.BAS", "B")["amsdos_header"]
    assert header["type"] == "BASIC" and header["load_address"] == 0x170
    type_and_run(session, "NEW\r")
    type_and_run(session, 'LOAD"TEST"\r')
    assert type_and_run(session, "RUN\r")[-2:] == ["HELLO FROM CPCXRAY", "Ready"]
