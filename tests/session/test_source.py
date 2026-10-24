"""Session tools of phase 8: database save/load, classify, source_generate, source_verify."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpcxray.cli import main
from cpcxray.decoding.vortex import vortex_decode
from cpcxray.machine.memory import RomSlot
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
    assert generated["path"] == str(tmp_path / "project" / "test.asm")
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
    shown = session.analysis_status()["verify"]  # the Project tab keeps the first ones
    assert (shown["difference_count"], shown["differences"][0]) == (1, difference)


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
    session.classify("C100", "C103", "word_data")  # numbers, not addresses
    assert session.analysis_target_list()["targets"][0]["kinds"]["word_data"] == 4
    session.classify("C100", "C103", "auto")


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


def test_analysis_switch_keeps_collected_data(tmp_path: Path) -> None:
    """Off: the observers leave the bus (decoding goes on); on again: nothing was lost."""
    session = booted_session(tmp_path)
    bus = session.machine.bus  # type: ignore[attr-defined]
    observers = (session._analysis_observer, session._execution_observer)  # pylint: disable=protected-access
    flags_before = bytes(session.analysis_targets["rom3"].flags)
    assert session.analysis_enable(False) == {"analysis_enabled": False, "observing": False,
                                              "targets": ["rom3"]}  # fmt: skip
    assert not bus.observed
    assert session.analysis_target_list()["analysis_enabled"] is False
    session.analysis_enable(True)
    assert all(o in bus._observers for o in observers)  # the same objects: tags kept  # pylint: disable=protected-access
    assert bytes(session.analysis_targets["rom3"].flags) == flags_before


def test_analysis_status_phases(tmp_path: Path) -> None:
    """analysis_status (Project tab): collect, changed until saved, source and its round trip."""
    session = Session(make_cpc(), project_dir=tmp_path)
    assert session.analysis_status()["target"] is None
    session.analysis_target_add("rom3")
    status = session.analysis_status()
    assert (status["target"], status["kind"], status["decode"]) == ("rom3", "rom", None)
    assert not status["save"]["changed"]  # nothing collected yet
    session.run(max_instructions=100)
    quick = session.analysis_status(classify=False)  # the GUI while running: no classification
    assert quick["collect"]["instruction_starts"] == 3
    assert not quick["collect"]["coverage_current"]
    status = session.analysis_status()
    assert status["collect"]["coverage_current"]
    assert status["collect"]["instruction_starts"] == 3
    assert status["collect"]["coverage"]["code"] > 0
    assert sum(status["collect"]["coverage"].values()) == 0x4000
    assert status["save"]["changed"]
    session.analysis_save()
    assert not session.analysis_status()["save"]["changed"]
    session.source_generate()
    status = session.analysis_status()
    assert status["source"]["path"] == "project/test.asm" and status["verify"] is None
    session.source_verify()
    assert session.analysis_status()["verify"]["identical"]


def test_analysis_status_of_a_scrambled_rom(tmp_path: Path) -> None:
    """A ROM with a decoder has the Decode phase: how its bytes were read, its access map."""
    machine = make_cpc()
    machine.memory.set_upper_rom(5, RomSlot(bytes(0x4000), "scr", vortex_decode))
    session = Session(machine, project_dir=tmp_path)
    session.analysis_target_add("rom5")
    machine.memory.upper_roms[5].read(0x10, True)  # an opcode fetch
    machine.memory.upper_roms[5].read(0x20, False)  # a data read
    decode = session.analysis_status("rom5")["decode"]
    assert session.analysis_status("rom5")["decoder"] == "vortex"
    assert (decode["classes"]["M1"], decode["classes"]["data"]) == (1, 1)
    assert decode["classes"]["unknown"] == 0x4000 - 2
    assert (decode["conflicts"], decode["map_path"], decode["map_exists"]) == (
        0, "project/scr.m1map", False)  # fmt: skip
