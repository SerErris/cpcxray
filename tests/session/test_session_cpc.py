"""Tests of the CPC 464 session methods (phase 4): ROMs, devices, screen text, config."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session, SessionError

REPO = Path(__file__).resolve().parents[2]


def make_rom(path: Path, header: bytes = b"\x01\x01\x02\x00") -> Path:
    """Write a 16 KB ROM image with the given header bytes (type, mark, version, mod)."""
    path.write_bytes(header + bytes(0x4000 - len(header)))
    return path


def test_cpc_methods_need_a_cpc() -> None:
    """On the flat test machine the CPC methods explain how to switch."""
    with pytest.raises(SessionError, match="load_config"):
        Session().devices_get()


def test_rom_load_list_unload(tmp_path: Path) -> None:
    """ROMs can be loaded into and removed from slots; each change resets (D-09)."""
    session = Session(CPC464(), project_dir=tmp_path)
    make_rom(tmp_path / "test.rom")
    session.registers_set({"pc": 0x1234})
    roms = session.rom_load("6", "test.rom")["roms"]
    assert roms == [{"slot": 6, "name": "test.rom", "type": "background", "version": "1.20"}]
    assert session.registers_get()["pc"] == 0  # reset
    assert session.rom_unload(6)["roms"] == []
    with pytest.raises(SessionError, match="slot"):
        session.rom_load("16", "test.rom")
    with pytest.raises(SessionError, match="16384"):
        (tmp_path / "short.rom").write_bytes(bytes(10))
        session.rom_load("lower", "short.rom")


def test_devices_get(tmp_path: Path) -> None:
    """devices_get reports the device registers after reset."""
    devices = Session(CPC464(), project_dir=tmp_path).devices_get()
    assert devices["gate_array"]["mode"] == 1
    assert devices["gate_array"]["upper_rom_enabled"] is False
    assert devices["keyboard_rows"] == "FF FF FF FF FF FF FF FF FF FF"


def test_screen_text_needs_lower_rom(tmp_path: Path) -> None:
    """Reading the screen needs the font from the OS ROM."""
    with pytest.raises(SessionError, match="font"):
        Session(CPC464(), project_dir=tmp_path).screen_text()


def test_load_config_switches_to_cpc(tmp_path: Path) -> None:
    """load_config powers on a CPC 464 from a machine.toml; a bad config is a SessionError."""
    (tmp_path / "config").mkdir()
    make_rom(tmp_path / "os.rom")
    (tmp_path / "config" / "machine.toml").write_text('[rom.lower]\nfile = "os.rom"\n')
    session = Session(project_dir=tmp_path)
    assert session.load_config()["model"] == "cpc464"
    assert session.rom_list()["roms"][0]["slot"] == "lower"
    (tmp_path / "config" / "bad.toml").write_text('[machine]\nmodel = "6128"\n')
    with pytest.raises(SessionError, match="model"):
        session.load_config("config/bad.toml")
