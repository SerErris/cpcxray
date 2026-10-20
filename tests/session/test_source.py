"""Session tools of phase 8: database save/load, classify, source_generate, source_verify."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpcxray.cli import main
from cpcxray.session.errors import SessionError
from cpcxray.session.session import Session
from tests.analysis.test_observers import make_cpc


def booted_session(project: Path) -> Session:
    """A session on the test CPC with target rom3, run until HALT."""
    session = Session(make_cpc(), project_dir=project)
    session.analysis_target_add("rom3")
    assert session.run(max_instructions=100)["reason"] == "halted"
    return session


def test_generate_and_verify(tmp_path: Path) -> None:
    """The source of the run reassembles byte-identically; RSTs and far object rendered."""
    session = booted_session(tmp_path)
    generated = session.source_generate()
    assert generated["path"] == str(tmp_path / "output" / "test.asm")
    assert generated["statistics"]["instructions_observed"] == 3
    text = Path(generated["path"]).read_text(encoding="utf-8")
    assert "(observed: reached rom3)" in text and "far_c060:" in text
    result = session.source_verify()
    assert result["identical"] and result["difference_count"] == 0
    assert result["assembled_length"] == 0x4000


def test_verify_reports_differences(tmp_path: Path) -> None:
    """A changed source is reported with address, values and source line (D-33)."""
    session = booted_session(tmp_path)
    path = Path(session.source_generate()["path"])
    path.write_text(path.read_text(encoding="utf-8").replace("halt", "nop"), encoding="utf-8")
    result = session.source_verify()
    assert not result["identical"]
    difference = result["differences"][0]
    assert difference["line"] > 0
    assert difference == {"address": "C070", "expected": "76", "assembled": "00",
                          "line": difference["line"], "source": "nop"}  # fmt: skip


def test_classify_override(tmp_path: Path) -> None:
    """An owner string override changes the source and survives save/load."""
    session = booted_session(tmp_path)
    result = session.classify("C100", "C103", "string")
    assert result["overrides"] == [{"first": "C100", "last": "C103", "kind": "string"}]
    assert result["kinds"]["string"] == 4
    with pytest.raises(SessionError, match="kind must be"):
        session.classify("C100", "C103", "poem")
    with pytest.raises(SessionError, match="outside"):
        session.classify("BFFF", "C003", "string")
    session.classify("C100", "C103", "auto")
    assert session.analysis_target_list()["targets"][0]["kinds"]["string"] == 0


def test_save_and_load_database(tmp_path: Path) -> None:
    """The database file keeps flags and observations; a new session merges it at start."""
    session = booted_session(tmp_path)
    session.classify("C100", "C101", "word_table")
    saved = session.analysis_save()
    database_file = tmp_path / "project" / "test.analysis.json"
    assert str(database_file) in saved["saved"]
    data = json.loads(database_file.read_text(encoding="utf-8"))
    assert data["observations"]["C000"] == {"rst": 2, "reached": "rom3"}

    fresh = Session(make_cpc(), project_dir=tmp_path)
    fresh.analysis_target_add("rom3")  # merges project/test.analysis.json
    generated = fresh.source_generate()
    assert generated["statistics"]["instructions_observed"] == 3  # from the saved flags
    assert generated["kinds"]["word_table"] == 2
    assert fresh.source_verify()["identical"]
    assert fresh.analysis_load()["kinds"]["insn_start"] >= 3


def test_cli_source_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`cpcxray source-verify` compares a source with an image file."""
    session = booted_session(tmp_path)
    source = session.source_generate()["path"]
    image = tmp_path / "image.bin"
    image.write_bytes(session.machine.memory.upper_roms[3].data)  # type: ignore[attr-defined]
    assert main(["source-verify", source, str(image)]) == 0
    assert "identical: 16384 bytes" in capsys.readouterr().out
    image.write_bytes(b"\x00" * 0x4000)
    assert main(["source-verify", source, str(image)]) == 1


def test_target_required(tmp_path: Path) -> None:
    """Without analysis targets the database tools ask for one."""
    session = Session(make_cpc(), project_dir=tmp_path)
    with pytest.raises(SessionError, match="give the target"):
        session.source_generate()
