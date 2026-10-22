"""Tests of the CPC 6128 RAM banking in the machine (FR-MCH-10, D-14; design.md §9 phase 11).

A test 6128 (no real ROMs needed) runs a program that switches the RAM configuration with
`OUT &7Fxx` (Gate Array function 11), as the firmware's KL BANK SELECT does; the debugger,
screen snapshot and .sna snapshots see the banked RAM correctly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.machine.config import MachineConfig, load_config
from cpcxray.machine.cpc464 import CPC464
from cpcxray.machine.memory import RomSlot
from cpcxray.session.errors import SessionError
from cpcxray.session.session import Session
from tests.analysis.helpers import run_until_halt
from tests.machines import CONFIG_6128, boot, requires_6128_roms

# Lower ROM at &0000: configuration 2 (blocks 4-7), write &42 to &4000 (block 5), back to
# configuration 0, read &4000 (block 1) into D, configuration 1 (block 7 at &C000), write &77
# to &C000, halt.
PROGRAM = bytes.fromhex(
    "01C27F ED49"  # ld bc,#7FC2 / out (c),c
    "3E42 320040"  # ld a,#42 / ld (#4000),a
    "01C07F ED49"  # ld bc,#7FC0 / out (c),c
    "3A0040 57"  # ld a,(#4000) / ld d,a
    "01C17F ED49"  # ld bc,#7FC1 / out (c),c
    "3E77 3200C0"  # ld a,#77 / ld (#C000),a
    "76"  # halt
)


def make_6128() -> CPC464:
    """A 6128 (128 KB, banking) with the test program as its lower ROM; blocks 0-7 = &B0+n."""
    machine = CPC464(MachineConfig(Path("."), model="6128"))
    machine.memory.set_lower_rom(RomSlot(PROGRAM.ljust(0x4000, b"\x00"), "test os"))
    machine.reset()
    for number in range(8):
        machine.memory.set_block(number, bytes([0xB0 + number]) * 0x4000)
    machine.cpu.sp = 0x8000
    return machine


def test_program_switches_banks_through_the_gate_array() -> None:
    """OUT &7FC0+o maps the blocks; the program's writes land in the banked blocks."""
    machine = make_6128()
    run_until_halt(machine)
    memory = machine.memory
    assert memory.block(5)[0] == 0x42  # written at &4000 in configuration 2
    assert machine.cpu.byte_registers[2] == 0xB1  # D: read &4000 in configuration 0 = block 1
    assert memory.block(7)[0] == 0x77 and memory.block(3)[0] == 0xB3
    status = machine.status()
    assert status["model"] == "cpc6128" and status["ram_kb"] == 128
    assert status["ram_configuration"] == 1 and status["ram_blocks_mapped"] == [0, 1, 2, 7]
    assert machine.gate_array.state()["ram_config"] == 0x01
    assert machine.snapshot().ram[0xC000] == 0xB3  # the screen shows block 3, not block 7
    machine.reset()
    assert memory.ram_configuration == 0 and memory.read(0xC000, is_m1=False) == 0xB3


def test_464_has_no_banking() -> None:
    """A 464 ignores the RAM configuration port and reports 64 KB."""
    machine = CPC464()
    machine.gate_array.io_out(0x7F00, 0xC2)
    status = machine.status()
    assert status["ram_kb"] == 64 and status["ram_configuration"] == 0
    assert status["ram_blocks_mapped"] == [0, 1, 2, 3]


def test_debugger_sees_blocks() -> None:
    """Breakpoints qualify by RAM block; memory views read blocks mapped or not."""
    session = Session(make_6128())
    session.breakpoint_add("4000", kind="write", location="ram:5")
    result = session.run(max_instructions=50)
    assert result["reason"] == "breakpoint"
    assert result["breakpoint"]["location"] == "ram:5"
    assert session.memory_view("4000", 2, view="ram:5")["hex"] == "42B5"
    assert session.memory_view("C000", 1, view="ram:7")["hex"] == "B7"
    with pytest.raises(SessionError, match="ram:0-ram:7"):
        session.memory_view("0", 1, view="ram:8")
    with pytest.raises(SessionError, match="ram:0-ram:3"):
        Session(CPC464()).memory_view("0", 1, view="ram:4")


def test_sna_keeps_128k_and_the_configuration(tmp_path: Path) -> None:
    """A 6128 snapshot holds all 8 blocks (CPC type 6128) and restores the banking."""
    session = Session(make_6128())
    session.run(max_instructions=50)
    path = tmp_path / "banked.sna"
    assert session.sna_save(str(path))["size"] == 0x100 + 0x20000
    data = path.read_bytes()
    assert data[0x6D] == 2 and data[0x6B:0x6D] == (128).to_bytes(2, "little")
    assert data[0x41] == 0x01  # RAM configuration 1

    fresh = Session(make_6128())
    fresh.sna_load(str(path))
    original, loaded = session.machine.memory, fresh.machine.memory  # type: ignore[attr-defined]
    assert loaded.ram_configuration == 1 and loaded.ram == original.ram
    assert [loaded.block(n) for n in range(8)] == [original.block(n) for n in range(8)]

    small = Session(CPC464())  # a 464 takes the first 64 KB
    small.sna_load(str(path))
    assert small.machine.memory.ram[0xC000] == 0xB3  # type: ignore[attr-defined]


def test_config_model_6128(tmp_path: Path) -> None:
    """`model = "6128"` in machine.toml powers on a 6128."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "machine.toml").write_text('[machine]\nmodel = "6128"\n', encoding="utf-8")
    config = load_config(config_dir / "machine.toml")
    assert config.model == "6128"
    assert CPC464(config).memory.ram_blocks == 8


@requires_6128_roms
def test_6128_boots_and_banks_from_basic(tmp_path: Path) -> None:
    """Milestone: the real 6128 firmware boots to BASIC 1.1; OUT &7F00,&C4 banks block 4 in."""
    session = boot(tmp_path, CONFIG_6128)
    lines = session.screen_text()["lines"]
    assert any("128K Microcomputer" in line for line in lines)
    assert any("BASIC 1.1" in line for line in lines)
    session.type_text(
        "OUT &7F00,&C4:POKE &4000,99:OUT &7F00,&C0:PRINT PEEK(&4000);:"
        "OUT &7F00,&C4:PRINT PEEK(&4000):OUT &7F00,&C0\r"
    )
    session.run(max_us=1_000_000)
    assert " 0  99" in session.screen_text()["lines"]
    assert session.machine.memory.block(4)[0] == 99  # type: ignore[attr-defined]
