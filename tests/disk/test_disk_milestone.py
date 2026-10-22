"""Phase 10 milestone: data is read from and written to disk images through the emulated FDC.

With the owner's ROMs and images (skipped when missing): AMSDOS lists the real 3" CP/M disk in
A:, loads a BASIC program from it, refuses to write to it (the real image is inserted
read-only) and saves to a draft image whose contents are then checked host-side. The
catalogue is also read on the 664 and 6128.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.machine.config import MachineConfig
from cpcxray.session.session import Session
from tests.machines import (
    CONFIG_464,
    CONFIG_664,
    CONFIG_6128,
    boot,
    requires_664_roms,
    requires_6128_roms,
    requires_disks,
    requires_roms,
    with_disks,
)

pytestmark = [requires_roms, requires_disks]


@pytest.fixture(name="session")
def fixture_session(tmp_path: Path) -> Session:
    """A booted CPC 464 (OS, BASIC 1.0, AMSDOS) with the 3" CP/M disk in A: (read-only)."""
    return boot(tmp_path, with_disks(CONFIG_464))


def type_and_run(session: Session, text: str) -> list[str]:
    """Type a line and let the CPC process it; return the non-empty screen lines."""
    session.type_text(text, wait=False)
    session.run(max_us=6_000_000, timeout_s=300)
    return [line for line in session.screen_text()["lines"] if line.strip()]


def test_catalogue(session: Session) -> None:
    """CAT lists the files of the image, sorted, with sizes and the free space."""
    lines = type_and_run(session, "CAT\r")
    assert "Drive A: user  0" in lines
    assert "AMSDOS  .COM   1K   EX1     .BAS   2K" in lines
    assert "ED      .COM   7K   XSUB    .COM   1K" in lines
    assert " 50K free" in lines


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(CONFIG_664, marks=requires_664_roms, id="664"),
        pytest.param(CONFIG_6128, marks=requires_6128_roms, id="6128"),
    ],
)
def test_catalogue_on_664_and_6128(tmp_path: Path, config: MachineConfig) -> None:
    """AMSDOS reads the disk in A: on the V1.1/V1.2 firmware as well (664: built-in drive)."""
    lines = type_and_run(boot(tmp_path, with_disks(config)), "CAT\r")
    assert "Drive A: user  0" in lines
    assert "AMSDOS  .COM   1K   EX1     .BAS   2K" in lines
    assert " 50K free" in lines


def test_load_basic_program(session: Session) -> None:
    """LOAD brings an ASCII BASIC program into memory exactly as stored on the image."""
    type_and_run(session, 'LOAD"EX1"\r')
    lines = type_and_run(session, "LIST\r")
    assert lines[-3:] == ["390 SAVE dumpfile$,b,&C000,&4000", "400 DATA 2,26,6", "Ready"]


def test_real_disk_is_write_protected_and_drafts_take_writes(session: Session) -> None:
    """SAVE to the real image fails (write protected); a draft receives the file."""
    type_and_run(session, '10 PRINT "HELLO FROM CPCXRAY"\r')
    lines = type_and_run(session, 'SAVE"TEST"\r')
    assert "Drive A: disc is write protected" in lines
    assert not session.disk_list()["drives"][0]["modified"]
    assert type_and_run(session, "C")[-2:] == ["Break", "Ready"]  # cancel
    session.disk_new("A", "data")
    type_and_run(session, 'SAVE"TEST"\r')
    files = session.disk_catalog("A")["files"]
    assert [f["name"] for f in files] == ["TEST.BAS"]
    header = session.disk_read_file("TEST.BAS", "A")["amsdos_header"]
    assert header["type"] == "BASIC" and header["load_address"] == 0x170
    type_and_run(session, "NEW\r")
    type_and_run(session, 'LOAD"TEST"\r')
    assert type_and_run(session, "RUN\r")[-2:] == ["HELLO FROM CPCXRAY", "Ready"]
