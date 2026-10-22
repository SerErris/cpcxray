"""Tests of the panel formatting and parsing helpers (no window)."""

from __future__ import annotations

import pytest

from cpcxray.session.session import Session
from cpcxray.ui.panels.text import (
    AREA,
    breakpoint_condition,
    breakpoint_where,
    colour_numbers,
    crtc_footer,
    drive_flags,
    flags_text,
    gate_array_lines,
    keyboard_summary,
    line_address,
    location_text,
    mark_pc,
    memory_layout,
    ordinal,
    paging_lines,
    parse_assignments,
    parse_memory_edit,
    ppi_lines,
    registers_lines,
    rom_select_lines,
    stop_texts,
    variable_range_hint,
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


def test_mark_pc() -> None:
    """Disassembly markers: PC and breakpoints."""
    lines = ["start:", "0000  3E 05        ld a,#05", "0002  C9           ret"]
    assert mark_pc(lines, 0x0002, {0x0000}) == [
        "  start:", "* 0000  3E 05        ld a,#05", "> 0002  C9           ret",
    ]  # fmt: skip


def test_breakpoint_texts() -> None:
    """Table columns, locations and ordinals of the Breakpoints tab."""
    read = {"id": 1, "kind": "read", "enabled": True, "hits": 2, "address": "B8B4", "end": "B8B7",
            "location": "rom:3", "label": None, "variable": "KL_TIME"}  # fmt: skip
    port = {"id": 2, "kind": "io_out", "enabled": False, "hits": 0, "port": "DF00", "mask": "FF00"}
    assert breakpoint_where(read) == ("B8B4-B8B7", "KL_TIME")
    assert breakpoint_condition(read) == "rom:3"
    assert (breakpoint_where(port), breakpoint_condition(port)) == (("port DF00", ""), "mask FF00")
    assert breakpoint_where({"kind": "rom_select", "rom": 7}) == ("ROM 7", "")
    assert breakpoint_where({"kind": "interrupt"}) == ("\u2014", "")
    assert [location_text(x) for x in ("any", "lower", "rom7", "rom:3", "ram:2", "ram")] == [
        "", "lower ROM", "ROM 7", "ROM 3", "RAM block 2", "RAM"]  # fmt: skip
    assert [ordinal(n) for n in (1, 2, 3, 4, 11, 12, 21, 112)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "21st", "112th"]  # fmt: skip
    variables = [{"label": "KL_TIME", "address": "B8B4", "last": "B8B7", "size": 4}]
    assert variable_range_hint("kl_time", variables) == (
        "end empty: the whole variable KL_TIME (B8B4-B8B7, 4 bytes)")  # fmt: skip
    assert variable_range_hint("KL_TIMEX", variables) == ""


def test_stop_texts() -> None:
    """The "Last stop" box for a PC breakpoint, a memory breakpoint and a plain step."""
    assert stop_texts({"stop": None})["headline"] == "no stop yet"
    pc_stop = {
        "stop": {
            "reason": "breakpoint",
            "pc": "BB5A",
            "breakpoint": {"id": 1, "kind": "pc", "pc": "BB5A", "location": "ram:2"},
        },
        "instruction": ["BB5A  CF 00 94  rst #08"],
        "hits": 4,
        "label": "TXT_OUTPUT",
    }
    texts = stop_texts(pc_stop)
    assert texts["title"] == "breakpoint #1 \u00b7 4th hit" and texts["kind"] == "pc"
    assert texts["headline"] == "before the instruction at BB5A TXT_OUTPUT \u00b7 RAM block 2"
    hit = {"id": 2, "kind": "write", "access": "write", "address": "B8B4", "value": "08",
           "pc": "00B4", "location": "ram:2", "variable": "KL_TIME"}  # fmt: skip
    write_stop = {"stop": {"reason": "breakpoint", "pc": "00B5", "breakpoint": hit},
                  "instruction": [], "hits": None, "label": None}  # fmt: skip
    texts = stop_texts(write_stop)
    assert texts["title"] == "breakpoint #2 (removed)"
    assert texts["headline"] == ("write B8B4 = 08 (KL_TIME) by the instruction at 00B4 "
                                 "\u00b7 RAM block 2")  # fmt: skip
    step = stop_texts({"stop": {"reason": "steps_done", "pc": "0038", "label": "INTERRUPT_ENTRY"}})
    assert (step["title"], step["headline"], step["kind"]) == ("step", "at 0038 INTERRUPT_ENTRY",
                                                               None)  # fmt: skip


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


def test_device_texts() -> None:
    """The Devices tab's texts: colours fw/hw, Gate Array lines, ROM select, PPI, CRTC, FDC."""
    assert colour_numbers(20, 0x13) == "20/13"
    gate_array = {"mode": 1, "pen": 15, "interrupt_in_us": 1784, "line_counter_r52": 24}
    assert gate_array_lines(gate_array) == [
        "mode 1 \u00b7 colours fw/hw (hex)",
        "pen select P15 \u00b7 IRQ in 1784 \u00b5s \u00b7 R52 24",
    ]
    rom_select = {"selected": 7, "answering": 7, "file": "amsdos.rom", "paged_in": False,
                  "installed": ["lower", "0", "7"]}  # fmt: skip
    assert rom_select_lines(rom_select) == [
        "selected 7 amsdos.rom",
        "not paged in (upper ROM off)",
        "installed: lower, 0, 7",
    ]
    empty = rom_select | {"selected": 3, "answering": 0, "file": "basic.rom", "paged_in": True}
    assert rom_select_lines(empty)[:2] == ["selected 3 (empty: ROM 0 basic.rom)", "paged in"]
    system_io = {"port_a": 0x0E, "port_b": 0x1E, "port_c": 0x30,
                 "meanings": {"a": "keyboard data via PSG", "b": "B", "c": "C"}}  # fmt: skip
    assert ppi_lines(system_io)[0] == "A &0E \u00b7 keyboard data via PSG"
    screen = {"start": "C000", "characters": 40, "lines": 25, "scan_lines": 8}
    assert crtc_footer({"screen": screen}) == "&C000 \u00b7 40 \u00d7 25 \u00b7 8 lines"
    drive = {"disk": "a.dsk", "writable": False, "modified": False}
    assert drive_flags(drive) == "read-only \u00b7 unchanged"
    assert drive_flags(drive | {"writable": True, "modified": True}) == "writable \u00b7 changed"
    assert drive_flags({"disk": None}) == ""
    lines = [{"line": 2, "keys": ["Clr", "[", "Return", "]", "f4", "Shift", "\\", "Ctrl"],
              "held": [5]},
             {"line": 8, "keys": ["1"] * 5 + ["A", "Caps", "Z"], "held": [5]}]  # fmt: skip
    assert keyboard_summary(lines, False) == "Shift + A held \u00b7 not typing"
    assert keyboard_summary([], True) == "no key held \u00b7 typing"
