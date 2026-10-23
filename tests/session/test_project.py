"""Session tools of the Project tab's machine editor (task 5i): config documents and ROM headers.

The machine is built from synthetic 16 KB ROM images in a temporary project (no real ROMs).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cpcxray.decoding.vortex import vortex_decode
from cpcxray.session.errors import SessionError
from cpcxray.session.project import describe_rom
from cpcxray.session.session import Session

# a background ROM: type 1, version 1.23, name table at &C010 holding "TEST" (last char | &80)
HEADER = bytes([1, 1, 2, 3, 0x10, 0xC0]) + bytes(10) + b"TES" + bytes([ord("T") | 0x80, 0])


def rom_image(prefix: bytes = b"") -> bytes:
    """A 16 KB image starting with `prefix`."""
    return prefix + bytes(0x4000 - len(prefix))


def make_project(tmp_path: Path) -> Session:
    """A project with an OS ROM and a background ROM in slot 7; the session on a flat Z80."""
    (tmp_path / "roms").mkdir()
    (tmp_path / "roms" / "os.rom").write_bytes(rom_image())
    (tmp_path / "roms" / "test.rom").write_bytes(rom_image(HEADER))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "test.toml").write_text(
        '[machine]\nmodel = "6128"\n[rom.lower]\nfile = "roms/os.rom"\n'
        '[rom.upper.7]\nfile = "roms/test.rom"\n'
    )
    return Session(project_dir=tmp_path)


def test_read_apply_and_get(tmp_path: Path) -> None:
    """A file is read as a document, applied (a new CPC) and described with ROM headers."""
    session = make_project(tmp_path)
    assert session.config_get() == {"path": None, "config": None, "roms": [], "skipped": []}
    read = session.config_read("config/test.toml")
    assert read["path"] == "config/test.toml"
    assert read["config"]["rom"]["upper"]["7"] == {"file": "roms/test.rom"}
    applied = session.config_apply(read["config"], "config/test.toml")
    assert session.machine.model == "cpc6128"
    assert applied["path"] == "config/test.toml"
    assert applied["config"]["machine"] == {"model": "6128", "unpopulated_rom": 0}
    rom = applied["roms"][1]
    assert (rom["slot"], rom["type"], rom["version"], rom["name"]) == (
        "7", "background", "1.23", "TEST")  # fmt: skip
    assert applied["roms"][0]["type"] == "lower ROM (OS)"
    assert session.machine_status()["pc"] == "0000"


def test_write_and_validation(tmp_path: Path) -> None:
    """Documents are validated before they are written or applied."""
    session = make_project(tmp_path)
    document = session.config_read("config/test.toml")["config"]
    document["machine"]["model"] = "464"
    assert session.config_write(document, "config/new.toml") == {"path": "config/new.toml"}
    assert session.config_read("config/new.toml")["config"]["machine"]["model"] == "464"
    with pytest.raises(SessionError, match="unsupported model"):
        session.config_write({"machine": {"model": "128"}}, "config/bad.toml")
    with pytest.raises(SessionError, match="slot must be in 0-15"):
        session.config_apply({"rom": {"upper": {"16": {"file": "roms/test.rom"}}}})
    with pytest.raises(SessionError, match="No such file"):
        session.config_read("config/missing.toml")


def test_rom_headers(tmp_path: Path) -> None:
    """rom_header reads a file's header; a scrambled ROM's header is decoded first."""
    session = make_project(tmp_path)
    assert session.rom_header("roms/test.rom")["name"] == "TEST"
    assert session.rom_header("roms/none.rom") == {"error": "file missing"}
    (tmp_path / "roms" / "short.rom").write_bytes(bytes(100))
    assert session.rom_header("roms/short.rom") == {"error": "100 bytes, not 16 KB"}
    scrambled = bytes(vortex_decode(0xC000 + n, b) for n, b in enumerate(rom_image(HEADER)))
    assert describe_rom(scrambled, decoder="vortex")["name"] == "TEST"
    assert describe_rom(scrambled)["name"] != "TEST"


def test_files_to_load_on_command(tmp_path: Path) -> None:
    """[[load]] files are loaded on command; "loaded" lasts until a reset or a change."""
    session = make_project(tmp_path)
    (tmp_path / "game.bin").write_bytes(b"\x3e\x01\xc9")
    document = session.config_read("config/test.toml")["config"]
    document["load"] = [{"file": "game.bin", "address": 0x8000},
                        {"file": "missing.bin", "address": 0x9000}]  # fmt: skip
    session.config_apply(document)
    loads = session.loads_get()["loads"]
    assert loads[0] == {"file": "game.bin", "address": "8000", "exists": True, "length": 3,
                        "loaded": False}  # fmt: skip
    assert (loads[1]["exists"], loads[1]["loaded"]) == (False, False)
    session.memory_load(loads[0]["address"], loads[0]["file"])
    assert session.loads_get()["loads"][0]["loaded"]
    session.memory_write("8001", "02")  # a program changes its variables: still "loaded"
    assert session.loads_get()["loads"][0]["loaded"]
    check = session.memory_file_check("9000", "game.bin")  # another address
    assert (check["exists"], check["loaded"]) == (True, False)
    session.reset()
    assert not session.loads_get()["loads"][0]["loaded"]
    session.memory_load("8000", "game.bin")
    (tmp_path / "game.bin").write_bytes(b"\x3e\x02\xc9\x00")  # the file changed
    os.utime(tmp_path / "game.bin", ns=(1, 1))
    assert not session.loads_get()["loads"][0]["loaded"]
    session.memory_load("8000", "game.bin")
    session.config_apply(document)  # a new machine
    assert not session.loads_get()["loads"][0]["loaded"]
