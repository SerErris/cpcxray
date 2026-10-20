"""Tests of the panel formatting and parsing helpers (no window)."""

from __future__ import annotations

import pytest

from cpcxray.session.session import Session
from cpcxray.ui.panels.text import (
    breakpoint_lines,
    devices_lines,
    flags_text,
    mark_pc,
    parse_assignments,
    parse_memory_edit,
    registers_lines,
)


def test_flags_and_registers() -> None:
    """Flags as letters; the register block shows every register."""
    assert flags_text(0b1100_0001) == "SZ-----C"
    session = Session()
    session.registers_set({"hl": 0x4000, "f": 0x41})
    lines = registers_lines(session.registers_get())
    assert lines[0].endswith("flags -Z-----C") and lines[3].startswith("HL 4000")
    assert len(lines) == 8


def test_parsing() -> None:
    """Register assignments and memory edits are parsed; malformed input raises."""
    assert parse_assignments("hl=4000, A=&05") == {"hl": 0x4000, "a": 5}
    assert parse_memory_edit("4000: 3E 05") == ("4000", "3E 05")
    with pytest.raises(ValueError):
        parse_assignments("hl")
    with pytest.raises(ValueError):
        parse_memory_edit("3E 05")


def test_mark_pc_and_lists() -> None:
    """Disassembly markers; breakpoint and device formatting."""
    lines = ["start:", "0000  3E 05        ld a,#05", "0002  C9           ret"]
    assert mark_pc(lines, 0x0002, {0x0000}) == [
        "  start:", "* 0000  3E 05        ld a,#05", "> 0002  C9           ret",
    ]  # fmt: skip
    entries = [{"id": 1, "kind": "read", "enabled": True, "hits": 2, "address": "C020",
                "location": "rom:3", "label": None},
               {"id": 2, "kind": "io_out", "enabled": False, "hits": 0, "port": "DF00",
                "mask": "FF00"}]  # fmt: skip
    assert breakpoint_lines(entries) == [
        "  1  on   hits 2     read C020 rom:3",
        "  2  off  hits 0     io_out DF00 mask FF00",
    ]
    assert breakpoint_lines([]) == ["(no breakpoints)"]
    assert devices_lines({"crtc": {"selected": 12}}) == ["[crtc]", "  selected: 12"]
