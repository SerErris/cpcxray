"""Phase 7 milestone: VDOS is found and initialised; conflict report, dump and ROM map.

Uses the real ROMs of config/machine.toml, including the scrambled VDOS 2.11-X in slot 5
(skipped when they are missing).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpcxray.decoding.m1map import decode_rom, load_m1map
from cpcxray.decoding.vortex import vortex_decode
from cpcxray.machine.config import load_config
from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session

REPO = Path(__file__).resolve().parents[2]
CONFIG = load_config(REPO / "config" / "machine.toml")
pytestmark = pytest.mark.skipif(
    not all(rom.exists for rom in CONFIG.roms()), reason="ROM files missing"
)


@pytest.fixture(name="session", scope="module")
def fixture_session(tmp_path_factory: pytest.TempPathFactory) -> Session:
    """Boot the CPC 464 with VDOS (project files in a temporary directory)."""
    session = Session(CPC464(CONFIG), project_dir=tmp_path_factory.mktemp("project"))
    session.run(max_us=4_000_000, timeout_s=120)
    return session


def test_vdos_signs_on(session: Session) -> None:
    """VDOS is found by the ROM scan, initialises and prints its sign-on; BASIC is ready."""
    lines = session.screen_text()["lines"]
    assert " (C)1986   VDOS 2.11-X by vortex GmbH" in lines
    assert "Ready" in lines
    assert session.rom_list()["roms"][2] == {
        "slot": 5,
        "name": "vdos211x.rom",
        "type": "background",
        "version": "1.333",
    }


def test_conflict_report(session: Session) -> None:
    """The conflict report lists bytes read both with M1 and as data (none at boot)."""
    report = session.shadow_conflicts()
    assert report["slot"] == "rom5"
    assert report["total"] == len(report["conflicts"]) and report["real"] == 0
    status = session.shadow_status()
    assert status["classes"]["M1"] > 1000 and status["classes"]["data"] > 1000


def test_dump_and_rom_map(session: Session) -> None:
    """Dump and ROM map are produced; the dump equals decoding raw image + saved map."""
    dump = session.rom_dump(fill_policy="raw")
    clear = Path(dump["path"]).read_bytes()
    assert len(clear) == 0x4000
    assert clear[6:9] == bytes.fromhex("C3E9C1")  # ROM jumpblock: JP &C1E9 (operand decoded)
    saved = session.m1map_save()
    raw = session.machine.memory.upper_roms[5].data  # type: ignore[attr-defined]
    assert decode_rom(raw, load_m1map(saved["path"]), vortex_decode, "raw") == clear
    rom_map = session.rom_map()
    assert Path(rom_map["html"]).read_text(encoding="utf-8").startswith("<!doctype html>")
    assert "Never accessed" in Path(rom_map["text"]).read_text(encoding="utf-8")
    assert rom_map["counts"]["executed via RAM"] > 0


def test_relocated_blocks_and_targets(session: Session) -> None:
    """VDOS copies code to RAM and runs it: blocks are detected for the target rom5."""
    assert [t["name"] for t in session.analysis_target_list()["targets"]] == ["rom5"]
    executed = [b for b in session.reloc_list()["blocks"] if b["executed"]]
    assert {(b["rom_start"], b["ram_start"]) for b in executed} >= {("0105", "BE8F")}


def test_access_map_is_merged_at_start(session: Session) -> None:
    """A new session in the same project merges the saved access map (D-19)."""
    session.m1map_save()
    fresh = Session(CPC464(CONFIG), project_dir=session.project_dir)
    assert fresh.shadow_status()["classes"] == session.shadow_status()["classes"]


def test_trace_formats(session: Session) -> None:
    """The trace writes text, CSV and JSON Lines; records show slot, origin and raw bytes."""
    for file_format in ("text", "csv", "jsonl"):
        session.reset()
        session.trace_start(f"trace.{file_format}", file_format=file_format, targets_only=True)
        session.run(max_us=1_500_000, timeout_s=120)  # the ROM scan reaches VDOS's init
        session.trace_stop()
    records = [
        json.loads(line) for line in (session.project_dir / "trace.jsonl").read_text().splitlines()
    ]
    assert records[0]["pc"] == "C006" and records[0]["where"] == "rom5"
    assert (records[0]["raw"], records[0]["bytes"]) == ("C3 C9 C1", "C3 E9 C1")
    csv_header = (session.project_dir / "trace.csv").read_text().splitlines()[0]
    assert csv_header == "sequence,pc,where,origin,bytes,raw,text,registers"
