"""Port meanings of the Bus trace tab (debugger/ports.py, task 5f)."""

from __future__ import annotations

from cpcxray.debugger.ports import PortMeanings, gate_array_meaning


def test_gate_array() -> None:
    """The four Gate Array functions by the value's top bits."""
    assert gate_array_meaning(0x89) == "Gate Array: mode 1, lower ROM on, upper ROM off"
    assert gate_array_meaning(0x9E).endswith("lower ROM off, upper ROM off, interrupt reset")
    assert gate_array_meaning(0x10) == "Gate Array: select border"
    assert gate_array_meaning(0x45).startswith("Gate Array: colour &05")
    assert gate_array_meaning(0xC4) == "Gate Array: RAM configuration 4"


def test_devices_by_address_line() -> None:
    """CRTC select then write, ROM select, PPI, FDC and printer by their address lines."""
    ports = PortMeanings()
    assert ports.meaning("out", 0xBD00, 48) == "CRTC: register = 48"  # no select seen yet
    assert ports.meaning("out", 0xBC00, 12) == "CRTC: select R12"
    assert ports.meaning("out", 0xBD00, 48) == "CRTC: R12 Start high = 48"
    assert ports.meaning("out", 0x7F00, 0x89).startswith("Gate Array: mode 1")
    assert ports.meaning("out", 0xDF00, 7) == "ROM select: ROM 7"
    assert ports.meaning("out", 0xF600, 0x30) == "PPI port C: line 0, motor on, PSG inactive"
    assert ports.meaning("in", 0xF500, 0x1E) == "PPI port B: VSYNC 0"
    assert ports.meaning("out", 0xFA7E, 1) == "FDC: motor on"
    assert ports.meaning("in", 0xFB7E, 0x80) == "FDC: main status"
    assert ports.meaning("out", 0xEF00, 0x41) == "printer"
    assert ports.meaning("in", 0xFFFF, 0xFF) == "no device"
