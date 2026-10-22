"""Tests of the panel formatting and parsing helpers (no window)."""

from __future__ import annotations

import pytest

from cpcxray.session.session import Session
from cpcxray.ui.panels.text import (
    AREA,
    breakpoint_lines,
    devices_lines,
    flags_text,
    line_address,
    mark_pc,
    memory_layout,
    paging_lines,
    parse_assignments,
    parse_memory_edit,
    registers_lines,
    with_paging,
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


QUARTERS = [
    {"start": "0000", "end": "3FFF", "source": "lower", "ram_block": 0,
     "text": "lower ROM (os6128.rom)"},
    {"start": "4000", "end": "7FFF", "source": "ram", "ram_block": 5, "text": "RAM block 5"},
]  # fmt: skip


def test_paging_lines() -> None:
    """One line per quarter; a 6128 names its RAM configuration in the title (FR-DBG-12)."""
    assert paging_lines({"quarters": QUARTERS, "ram_configuration": 5}) == [
        "Paging (RAM configuration 5):",
        "  0000-3FFF  lower ROM (os6128.rom)",
        "  4000-7FFF  RAM block 5",
    ]
    assert paging_lines({"quarters": QUARTERS[:1]})[0] == "Paging:"


def test_with_paging_marks_each_quarter_before_its_labels() -> None:
    """A separator before the first line of each quarter, ahead of that line's label."""
    lines = ["3FFE  00           nop", "here:", "4000  CF 80 91     rst #08",
             "                   defw 09180h", "4003  C9           ret"]  # fmt: skip
    assert with_paging(lines, QUARTERS) == [
        "; ---- 0000-3FFF: lower ROM (os6128.rom)",
        "3FFE  00           nop",
        "; ---- 4000-7FFF: RAM block 5",
        "here:",
        "4000  CF 80 91     rst #08",
        "                   defw 09180h",
        "4003  C9           ret",
    ]
    assert line_address("here:") is None and line_address("4003  C9           ret") == 0x4003


def test_memory_layout_colours_variables_and_builds_the_legend() -> None:
    """Each named variable gets the next colour; area-only bytes AREA; the legend matches."""
    dump = {
        "address": "B724", "hex": "0102030405??",
        "variables": [
            {"label": "TXT_WORKSPACE", "address": "B6B5", "last": "B7C2", "size": 270,
             "area": True, "description": "text VDU variables"},
            {"label": "TXT_CURRENT_WINDOW", "address": "B726", "last": "B733", "size": 14,
             "area": False, "description": "parameters of the current stream"},
            {"label": "TXT_CURSOR_ROW", "address": "B726", "last": "B726", "size": 1,
             "area": False, "description": "cursor line"},
        ],
    }  # fmt: skip
    rows, legend = memory_layout(dump, colours=2)
    assert rows[0].address == "B724" and rows[0].ascii == "......"
    assert [colour for _, colour in rows[0].cells] == [AREA, AREA, 0, 1, 1, 1]
    assert rows[0].cells[5] == ("--", 1)  # unknown byte
    assert legend == [
        ("in TXT_WORKSPACE", AREA),
        ("B726-B726  TXT_CURSOR_ROW (1) in TXT_CURRENT_WINDOW: cursor line", 0),
        ("B726-B733  TXT_CURRENT_WINDOW (14): parameters of the current stream", 1),
    ]
    plain, no_legend = memory_layout({"address": "4000", "hex": "41" * 20}, colours=2)
    assert len(plain) == 2 and plain[0].ascii == "A" * 16 and not no_legend
    assert {colour for row in plain for _, colour in row.cells} == {None}
