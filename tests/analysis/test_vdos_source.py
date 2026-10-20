"""Phase 8 milestone: the generated VDOS source reassembles byte-identically (D-33).

Uses the real ROMs of config/machine.toml (skipped when they are missing): boots the CPC 464
with VDOS, generates output/vdos211x.asm from the analysis database and assembles it with
sjasmplus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.cli import main
from cpcxray.session.session import Session
from tests.vdos import REPO, boot_vdos, requires_roms

pytestmark = requires_roms


@pytest.fixture(name="session", scope="module")
def fixture_session(tmp_path_factory: pytest.TempPathFactory) -> Session:
    """Boot the CPC 464 with VDOS (project files in a temporary directory)."""
    return boot_vdos(tmp_path_factory.mktemp("project"))


def test_vdos_source_reassembles_identically(session: Session) -> None:
    """Milestone: source_generate → sjasmplus → the clear dump, byte for byte."""
    generated = session.source_generate()
    assert generated["statistics"]["instructions_observed"] > 1000
    assert generated["statistics"]["instructions_static"] > 0
    result = session.source_verify()
    assert result["identical"], result["differences"][:10]
    assert result["assembled_length"] == 0x4000


def test_vdos_source_content(session: Session) -> None:
    """Header, RSX labels, relocated code (DISP) and never-accessed markers are rendered."""
    text = Path(session.source_generate()["path"]).read_text(encoding="utf-8")
    lines = [line.strip() for line in text.splitlines()]
    assert "rom_type:" in lines and "rom_name_table:" in lines and "rsx_dir:" in lines
    assert "DISP #BE8F" in lines and "DISP #BF00" in lines
    assert any(line.startswith("; ---- never accessed:") for line in lines)


def test_static_dump_matches_source(session: Session, tmp_path: Path) -> None:
    """The "static" dump equals what the source assembles to (CLI round trip)."""
    dump = session.rom_dump(fill_policy="static", path=str(tmp_path / "clear.rom"))
    source = session.source_generate(path=str(tmp_path / "vdos.asm"))["path"]
    assert main(["source-verify", source, dump["path"]]) == 0


def test_other_fill_policies_reassemble(session: Session, tmp_path: Path) -> None:
    """With "data" fill (no static code) the source reassembles too."""
    path = str(tmp_path / "vdos_data.asm")
    session.source_generate(path=path, fill_policy="data")
    assert session.source_verify(path=path)["identical"]


def test_decode_from_map_static(session: Session, tmp_path: Path) -> None:
    """Static fill without a run: from the raw image and the saved access map."""
    saved = session.m1map_save(path=str(tmp_path / "vdos.m1map"))
    out = tmp_path / "from_map.rom"
    raw = REPO / "roms" / "vdos211x.rom"
    session.rom_decode_from_map(str(raw), saved["path"], str(out), fill_policy="static")
    clear = out.read_bytes()
    assert len(clear) == 0x4000
    assert clear[6:9] == bytes.fromhex("C3E9C1")  # ROM jumpblock: JP &C1E9
