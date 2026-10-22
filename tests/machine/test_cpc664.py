"""Tests of the CPC 664 model: 64 KB without banking, firmware V1.1 names (FR-MCH-10, D-26).

The 664 is a 464 with a built-in disc drive and the newer firmware: the same architecture in
cpcxray, a different OS ROM and BASIC 1.1. The boot check uses config/test_cpc664.toml and is
skipped when the ROMs are missing.
"""

from __future__ import annotations

from pathlib import Path

from cpcxray.machine.config import MachineConfig
from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session
from tests.machines import CONFIG_664, REPO, requires_664_roms, run_until_ready


def test_664_has_64k_and_no_banking() -> None:
    """A 664 has four RAM blocks and ignores the RAM configuration port, like a 464."""
    machine = CPC464(MachineConfig(Path("."), model="664"))
    machine.gate_array.io_out(0x7F00, 0xC2)
    status = machine.status()
    assert (status["model"], status["ram_kb"], status["ram_configuration"]) == ("cpc664", 64, 0)
    assert machine.firmware_model == "664"


def test_session_uses_the_machines_firmware_names(tmp_path: Path) -> None:
    """The session preloads the names of the machine's firmware and switches them on reload."""
    session = Session(CPC464(MachineConfig(Path("."), model="664")), project_dir=tmp_path)
    assert session.symbols.firmware_model == "664"
    assert session.symbol_list("KM_SET_LOCKS")["symbols"][0]["address"] == "BD3A"
    session.load_config(str(REPO / "config" / "test_cpc464.toml"))
    assert session.symbols.firmware_model == "464"
    assert session.symbol_list("KM_SET_LOCKS")["total"] == 0
    assert session.symbol_list("MOVE_REAL")["symbols"][0]["address"] == "BD3D"


@requires_664_roms
def test_664_firmware_boots(tmp_path: Path) -> None:
    """The 664 OS signs on as firmware v2 with BASIC 1.1 and BASIC executes typed commands.

    BASIC calls the lower ROM at fixed addresses, so it needs the 664's own BASIC 1.1: the
    6128's (same version number) loops on "Syntax error" under the 664 OS.
    """
    session = Session(CPC464(CONFIG_664), project_dir=tmp_path)
    run_until_ready(session)
    lines = session.screen_text()["lines"]
    assert " Amstrad 64K Microcomputer  (v2)" in lines and " BASIC 1.1" in lines
    assert session.machine_status()["skipped_roms"] == []
    session.type_text("PRINT 6*7\r")
    session.run(max_us=500_000)
    assert " 42" in session.screen_text()["lines"]
