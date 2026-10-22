"""Device tests driving OUT/IN sequences through the CPC bus (design.md §8, phase 4).

The machine has no ROMs here; every test talks to the devices through `bus.io_out` /
`bus.io_in`, so the partial address decoding (D-08) is exercised as well.
"""

from __future__ import annotations

import pytest

from cpcxray.devices.gate_array import INTERRUPT_PERIOD_US
from cpcxray.devices.system_io import FRAME_US, port_b_meaning, port_c_meaning
from cpcxray.machine.cpc464 import CPC464


@pytest.fixture(name="machine")
def fixture_machine() -> CPC464:
    """A CPC 464 without ROMs, freshly reset."""
    return CPC464()


# -- Gate Array --------------------------------------------------------------------------------


def test_gate_array_reset_state(machine: CPC464) -> None:
    """After reset: mode 1, lower ROM on, upper ROM off, no interrupt pending."""
    state = machine.gate_array.state()
    assert (state["mode"], state["lower_rom_enabled"], state["upper_rom_enabled"]) == (
        1,
        True,
        False,
    )
    assert (machine.memory.lower_enabled, machine.memory.upper_enabled) == (True, False)
    assert not state["interrupt_pending"]


def test_gate_array_pens_inks_border(machine: CPC464) -> None:
    """Pen select then colour writes set inks; bit 4 of the pen selects the border."""
    machine.bus.io_out(0x7F00, 0x03)  # pen 3
    machine.bus.io_out(0x7F00, 0x40 | 0x0A)  # colour 10
    machine.bus.io_out(0x7F00, 0x10)  # border
    machine.bus.io_out(0x7F00, 0x40 | 0x14)  # colour 20
    state = machine.gate_array.state()
    assert (state["inks"][3], state["border"], state["pen"]) == (10, 20, "border")


@pytest.mark.parametrize(
    ("rmr", "mode", "lower", "upper"),
    [
        (0x80, 0, True, True),
        (0x89, 1, True, False),
        (0x86, 2, False, True),
        (0x8C, 0, False, False),
    ],
)
def test_gate_array_rmr_pages_roms(
    machine: CPC464, rmr: int, mode: int, lower: bool, upper: bool
) -> None:
    """RMR sets the mode and the ROM enables of the memory map (FR-MCH-02)."""
    machine.bus.io_out(0x7F89, rmr)
    assert machine.gate_array.mode == mode
    assert (machine.memory.lower_enabled, machine.memory.upper_enabled) == (lower, upper)


def test_gate_array_is_not_decoded_on_other_ports(machine: CPC464) -> None:
    """A write with A14 = 0 or A15 = 1 does not reach the Gate Array."""
    machine.bus.io_out(0xBC00, 0x8C)  # CRTC select port: A14 = 0
    machine.bus.io_out(0xFF00, 0x8C)  # A15 = 1
    assert machine.gate_array.mode == 1


def test_interrupt_timer_and_acknowledge(machine: CPC464) -> None:
    """INT is latched every 3,328 µs and cleared by the acknowledge (D-05)."""
    gate_array = machine.gate_array
    assert gate_array.interrupt_in_us == INTERRUPT_PERIOD_US and gate_array.line_counter == 0
    gate_array.tick(INTERRUPT_PERIOD_US - 1)  # counts down ...
    assert not gate_array.interrupt_pending
    assert gate_array.interrupt_in_us == 1 and gate_array.line_counter == 51
    gate_array.tick(1)  # ... reaches 0: interrupt, reload
    assert gate_array.interrupt_pending
    assert gate_array.interrupt_in_us == INTERRUPT_PERIOD_US
    assert gate_array.state()["interrupt_in_us"] == INTERRUPT_PERIOD_US
    assert machine.bus.int_ack() == 0xFF
    assert not machine.gate_array.interrupt_pending


def test_rmr_bit4_clears_interrupt_and_restarts_period(machine: CPC464) -> None:
    """RMR bit 4 clears a pending interrupt and restarts the 300 Hz period."""
    machine.gate_array.tick(5000)
    assert machine.gate_array.interrupt_pending
    machine.bus.io_out(0x7F00, 0x99)  # RMR with bit 4
    assert not machine.gate_array.interrupt_pending
    assert machine.gate_array.interrupt_in_us == INTERRUPT_PERIOD_US


# -- CRTC, ROM select ----------------------------------------------------------------------


def test_crtc_select_write_read(machine: CPC464) -> None:
    """Select a register at &BCxx, write at &BDxx; only R12-R17 read back (&BFxx)."""
    for register, value in ((1, 40), (12, 0x30), (13, 0x05)):
        machine.bus.io_out(0xBC00, register)
        machine.bus.io_out(0xBD00, value)
    assert machine.crtc.registers[1] == 40
    assert machine.crtc.screen_start() == 0x3005
    screen = machine.crtc.state()["screen"]
    assert screen == {"start": "C00A", "characters": 40, "lines": 0, "scan_lines": 1}
    assert machine.crtc.state()["names"][13] == "Start low"
    machine.bus.io_out(0xBC00, 13)
    assert machine.bus.io_in(0xBF00) == 0x05
    machine.bus.io_out(0xBC00, 1)
    assert machine.bus.io_in(0xBF00) == 0x00  # R1 is write-only


def test_rom_select_latch(machine: CPC464) -> None:
    """OUT (C),C with BC = &DF07 latches ROM 7 in the memory map (FR-MCH-03)."""
    machine.bus.io_out(0xDF07, 7)
    assert (machine.rom_select.selected, machine.memory.selected_upper) == (7, 7)


# -- SystemIO -----------------------------------------------------------------------------------


def test_port_b_vsync(machine: CPC464) -> None:
    """Port B is the constant plus VSYNC in the first 512 µs of every frame (D-05)."""
    machine.clock_us = 100
    assert machine.bus.io_in(0xF500) == 0x1F
    machine.clock_us = 600
    assert machine.bus.io_in(0xF500) == 0x1E
    machine.clock_us = 3 * FRAME_US + 511
    assert machine.bus.io_in(0xF500) & 1


def test_keyboard_scan_sequence(machine: CPC464) -> None:
    """The firmware's sequence reads a keyboard row through PSG register 14 (§6.9)."""
    machine.keyboard.press(8 * 8 + 2)  # key 66: row 8, bit 2
    machine.bus.io_out(0xF400, 14)  # register number on port A
    machine.bus.io_out(0xF600, 0xC0)  # PSG function "select register"
    machine.bus.io_out(0xF600, 0x40 | 8)  # PSG read, row 8
    assert machine.bus.io_in(0xF400) == 0xFB
    machine.bus.io_out(0xF600, 0x40 | 3)
    assert machine.bus.io_in(0xF400) == 0xFF
    machine.keyboard.release(8 * 8 + 2)
    machine.bus.io_out(0xF600, 0x40 | 8)
    assert machine.bus.io_in(0xF400) == 0xFF


def test_port_c_bit_set_reset(machine: CPC464) -> None:
    """The control port's bit set/reset changes one bit of port C."""
    machine.bus.io_out(0xF700, 0x0F)  # set bit 7
    machine.bus.io_out(0xF700, 0x0D)  # set bit 6
    assert machine.system_io.port_c == 0xC0
    machine.bus.io_out(0xF700, 0x0C)  # reset bit 6
    assert machine.system_io.port_c == 0x80


def test_unanswered_in_reads_ff(machine: CPC464) -> None:
    """An IN no device answers reads &FF (D-08)."""
    assert machine.bus.io_in(0xFFFF) == 0xFF


def test_ppi_meanings() -> None:
    """Ports B and C in words: VSYNC, frequency, brand; keyboard line, motor, PSG function."""
    assert port_b_meaning(0x1E) == "VSYNC 0, 50 Hz, Amstrad"
    assert port_b_meaning(0x0B) == "VSYNC 1, 60 Hz, Schneider"
    assert port_c_meaning(0x30) == "line 0, motor on, PSG inactive"
    assert port_c_meaning(0xC9) == "line 9, motor off, PSG select register"
