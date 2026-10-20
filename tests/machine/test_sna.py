"""Tests of .sna snapshots: round trip, header layout, version 3 memory chunks (FR-DBG-10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.machine.sna import (
    HEADER_SIZE,
    SnapshotError,
    decompress_chunk,
    load_sna,
    save_sna,
)
from cpcxray.session.errors import SessionError
from cpcxray.session.session import Session
from tests.session.test_debugger import OS, UPPER, make_test_cpc


def running_machine() -> Session:
    """A session on the test CPC after a few instructions, with distinctive device state."""
    session = Session(make_test_cpc(OS, UPPER))
    session.step(6)
    machine = session.machine
    machine.gate_array.io_out(0x7F00, 0x05)  # select pen 5  # type: ignore[attr-defined]
    machine.gate_array.io_out(0x7F00, 0x4B)  # ink: hardware colour 11
    machine.crtc.io_out(0xBC00, 12)  # type: ignore[attr-defined]
    machine.crtc.io_out(0xBD00, 0x30)  # type: ignore[attr-defined]
    machine.cpu.set_registers({"ix": 0x1234, "iy": 0x5678, "bc_": 0xABCD, "im": 1})
    return session


def test_round_trip(tmp_path: Path) -> None:
    """Save and load reproduce CPU registers, RAM and device state."""
    session = running_machine()
    machine = session.machine
    path = tmp_path / "state.sna"
    assert session.sna_save(str(path))["size"] == HEADER_SIZE + 0x10000
    data = path.read_bytes()
    assert data[:8] == b"MV - SNA" and data[0x10] == 3
    assert data[0x23:0x25] == machine.cpu.pc.to_bytes(2, "little")

    fresh = Session(make_test_cpc(OS, UPPER))
    result = fresh.sna_load(str(path))
    assert result["version"] == 3 and result["pc"] == f"{machine.cpu.pc:04X}"
    expected = machine.cpu.registers()
    loaded = fresh.machine.cpu.registers()
    for name in ("af", "bc", "de", "hl", "ix", "iy", "sp", "pc", "af_", "bc_", "de_", "hl_",
                 "i", "r", "iff1", "iff2", "im"):  # fmt: skip
        assert loaded[name] == expected[name], name
    assert fresh.machine.memory.ram == machine.memory.ram  # type: ignore[attr-defined]
    gate_array = fresh.machine.gate_array  # type: ignore[attr-defined]
    assert gate_array.inks[5] == 11 and gate_array.mode == 1
    assert fresh.machine.crtc.registers[12] == 0x30  # type: ignore[attr-defined]
    assert fresh.machine.rom_select.selected == 3  # type: ignore[attr-defined]
    assert fresh.step(1)["pc"] == session.step(1)["pc"]  # continues identically


def test_version_3_memory_chunk(tmp_path: Path) -> None:
    """A version 3 file with dump size 0 and a compressed MEM0 chunk loads."""
    ram = bytearray(0x10000)
    ram[0x4000:0x4004] = b"\xe5\x01\x02\x03"
    compressed = bytearray()
    index = 0
    while index < len(ram):  # simple encoder: runs of zeros, E5 escaped
        if ram[index] == 0:
            run = 1
            while index + run < len(ram) and ram[index + run] == 0 and run < 255:
                run += 1
            compressed += bytes([0xE5, run, 0])
            index += run
        elif ram[index] == 0xE5:
            compressed += b"\xe5\x00"
            index += 1
        else:
            compressed.append(ram[index])
            index += 1
    assert decompress_chunk(bytes(compressed)) == bytes(ram)
    header = bytearray(HEADER_SIZE)
    header[0:8], header[0x10] = b"MV - SNA", 3
    header[0x23:0x25] = (0x4000).to_bytes(2, "little")
    path = tmp_path / "v3.sna"
    path.write_bytes(bytes(header) + b"MEM0" + len(compressed).to_bytes(4, "little") + compressed)
    machine = make_test_cpc(OS, UPPER)
    assert load_sna(machine, path)["pc"] == 0x4000
    assert machine.memory.ram[0x4000:0x4004] == b"\xe5\x01\x02\x03"


def test_bad_files(tmp_path: Path) -> None:
    """Files without signature or RAM are refused."""
    machine = make_test_cpc(OS, UPPER)
    path = tmp_path / "bad.sna"
    path.write_bytes(b"not a snapshot" + bytes(300))
    with pytest.raises(SnapshotError, match=r"not a \.sna"):
        load_sna(machine, path)
    header = bytearray(HEADER_SIZE)
    header[0:8], header[0x10] = b"MV - SNA", 1
    path.write_bytes(bytes(header))
    with pytest.raises(SnapshotError, match="no 64 KB"):
        load_sna(machine, path)
    with pytest.raises(SessionError):
        Session(machine).sna_load(str(path))
    save_sna(machine, tmp_path / "ok.sna")
