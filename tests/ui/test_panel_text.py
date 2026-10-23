"""Tests of the panel formatting and parsing helpers (no window)."""

from __future__ import annotations

import pytest

from cpcxray.session.session import Session
from cpcxray.ui.panels.text import (
    AREA,
    CodeRow,
    breakpoint_condition,
    breakpoint_where,
    bus_lines,
    bus_position,
    catalog_header,
    colour_numbers,
    cpu_state_lines,
    crtc_footer,
    disassembly_view,
    drive_chip,
    drive_flags,
    drive_geometry,
    file_detail,
    flag_bits,
    follow_lines,
    follow_target,
    gap_text,
    gate_array_lines,
    innermost,
    keyboard_summary,
    location_text,
    memory_layout,
    ordinal,
    page_line,
    parse_assignments,
    parse_memory_edit,
    ppi_lines,
    register_changes,
    register_pairs,
    register_words,
    rom_select_lines,
    split_label,
    stop_texts,
    trace_detail,
    trace_notes,
    trace_rows,
    variable_colour_indexes,
    variable_detail,
    variable_range_hint,
    variable_rows,
)


def test_registers_and_flags() -> None:
    """Pairs as bytes (main, alternate), words, flags and the CPU state; changes marked."""
    session = Session()
    session.registers_set({"hl": 0x4000, "f": 0x41, "af_": 0x0D9B, "sp": 0xBFE4})
    registers = session.registers_get()
    pairs = register_pairs(registers, ["hl", "af_"])
    assert pairs[0][3:] == ("0D 9B", True) and pairs[3][:3] == ("HL", "40 00", True)
    assert register_words(registers, ["sp"])[2] == ("SP", "BFE4", True)
    assert [letter for letter, set_ in flag_bits(0x41) if set_] == ["Z", "C"]
    assert cpu_state_lines(registers)[1].endswith("not halted")


def test_parsing() -> None:
    """Register assignments and memory edits are parsed; malformed input raises."""
    assert parse_assignments("hl=4000, A=&05") == {"hl": 0x4000, "a": 5}
    assert parse_memory_edit("4000: 3E 05") == ("4000", "3E 05")
    with pytest.raises(ValueError):
        parse_assignments("hl")
    with pytest.raises(ValueError):
        parse_memory_edit("3E 05")


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


def test_disassembly_view() -> None:
    """Separators per quarter ahead of labels, the PC row and gutter marks, names split."""
    rows = [
        {"kind": "instruction", "address": "3FFE", "bytes": "00", "mnemonic": "nop",
         "operands": ""},
        {"kind": "label", "text": "here", "name_type": "own"},
        {"kind": "instruction", "address": "4000", "bytes": "CD 5A BB", "mnemonic": "call",
         "operands": "TXT_OUTPUT", "name": "TXT_OUTPUT", "name_type": "FW"},
        {"kind": "instruction", "address": "4003", "bytes": "3A 33 B7", "mnemonic": "ld",
         "operands": "a,(TXT_GRAPHICS)", "name": "TXT_GRAPHICS", "name_type": "VAR",
         "comment": "graphics flag"},
    ]  # fmt: skip
    view = disassembly_view({"rows": rows, "paging": QUARTERS}, 0x4000, {0x3FFE})
    assert [r.kind for r in view] == ["separator", "instruction", "separator", "label",
                                      "instruction", "instruction"]  # fmt: skip
    assert view[0].text == "0000-3FFF lower ROM (os6128.rom)" and view[1].gutter == "\u25cf"
    assert view[3] == CodeRow("label", text="here:", name_type="own")
    assert view[4].pc and view[4].gutter == "\u25b6"
    assert (view[5].before, view[5].name, view[5].after) == ("a,(", "TXT_GRAPHICS", ")")
    assert view[5].comment == "graphics flag" and not view[5].pc


def test_disassembly_view_with_comment_blocks() -> None:
    """A block before goes above the label (after the separator), a block after below (task 9)."""
    rows = [
        {"kind": "note", "text": "read_sector: one sector", "position": "before"},
        {"kind": "note", "text": "", "position": "before"},
        {"kind": "label", "text": "read_sector", "name_type": "sub"},
        {"kind": "instruction", "address": "4000", "bytes": "00", "mnemonic": "nop",
         "operands": ""},
        {"kind": "note", "text": "the FDC is busy now", "position": "after"},
    ]  # fmt: skip
    view = disassembly_view({"rows": rows, "paging": QUARTERS}, 0x4000, set())
    assert [r.kind for r in view] == ["separator", "note", "note", "label", "instruction",
                                      "note"]  # fmt: skip
    assert [r.text for r in view if r.kind == "note"] == [
        "; read_sector: one sector",
        ";",
        "; the FDC is busy now",
    ]


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


def test_disk_texts() -> None:
    """Drive chips, geometry, the catalogue header and the file detail of the Disks tab."""
    drive_a = {"drive": "A", "disk": "/x/disk/464_cpm_22_orig.dsk", "cylinders": 40,
               "double_sided": False}  # fmt: skip
    assert drive_chip(drive_a) == "\u25cf A: 464_cpm_22_orig.dsk"
    assert drive_chip({"drive": "B", "disk": None}) == "\u25cb B: no disk"
    assert drive_geometry(drive_a) == '3" \u00b7 40 tracks \u00b7 1 side'
    assert drive_geometry({"cylinders": 80, "double_sided": True}) == "80 tracks \u00b7 2 sides"
    catalog = {"files": [{}] * 26, "used_kb": 119, "free_kb": 50}
    assert catalog_header(catalog) == "26 files \u00b7 119K used \u00b7 50K free"
    header = {"type": "binary", "name": "RITDEMO.BIN", "load_address": 0x2950,
              "entry_address": 0x2950, "length": 26432}  # fmt: skip
    data = bytes(128) + bytes.fromhex("AF010000CD32BC3E01011818CD32BCAF")
    program = {"type": "binary", "load_address": 0x2950, "entry_address": 0x2950,
               "source": "AMSDOS header"}  # fmt: skip
    info = {
        "size": 26624,
        "amsdos_header": header,
        "hex": data.hex(),
        "program": program,
        "entry": {"bytes": "AF 01", "code": [{"address": "2950", "text": "xor a"}]},
    }
    detail = file_detail(info, 0)
    assert detail["rows"][0] == ("on disk", "26624 bytes \u00b7 user 0")
    assert ("length", "26432 (&6740)") in detail["rows"]
    assert detail["blocks"][0] == (
        "after the header (load &2950)",
        ["AF010000CD32BC3E 01011818CD32BCAF .....2.>.....2.."],
    )
    assert detail["blocks"][1] == ("at the entry &2950 (entry = load)", ["AF 01"])
    assert detail["code"] == [{"address": "2950", "text": "xor a"}]
    call = {"address": "2954", "text": "call SCR_SET_INK", "label": "SCR_SET_INK"}
    assert split_label(call) == ("call ", "SCR_SET_INK", "")
    assert split_label({"text": "ld (KL_TIME+1),a", "label": "KL_TIME+1"}) == (
        "ld (", "KL_TIME+1", "),a")  # fmt: skip
    assert split_label({"text": "xor a"}) == ("xor a", "", "")
    text = file_detail({"size": 1152, "amsdos_header": None, "hex": b"10 CLS\r\n".hex(),
                        "text": "10 CLS\r\n20 END"}, 0)  # fmt: skip
    assert text["blocks"] == [("first lines", ["10 CLS", "20 END"])]
    binary = file_detail({"size": 2, "amsdos_header": None, "hex": "00ff"}, 3)
    assert binary["blocks"] == [("first bytes", ["00FF .."])]
    cpm = {"type": "CP/M program", "load_address": 0x100, "entry_address": 0x100,
           "source": "CP/M"}  # fmt: skip
    com = file_detail({"size": 128, "amsdos_header": None, "hex": "c3ce04", "program": cpm,
                       "entry": {"bytes": "C3 CE 04", "code": []}}, 0)  # fmt: skip
    assert com["rows"][1:] == [("header", "none \u00b7 CP/M program"),
                               ("load", "&0100 (CP/M)"), ("entry", "&0100")]  # fmt: skip
    assert com["blocks"] == [("at the entry &0100 (entry = load)", ["C3 CE 04"])]
    basic = {"type": "BASIC, ASCII text", "load_address": 0x170, "entry_address": None,
             "source": "BASIC"}  # fmt: skip
    ascii_basic = file_detail({"size": 128, "amsdos_header": None, "hex": b"10 CLS".hex(),
                               "text": "10 CLS", "program": basic}, 0)  # fmt: skip
    assert ("load", "&0170 (BASIC program)") in ascii_basic["rows"]


def _record(  # pylint: disable=too-many-arguments  # one argument per record field
    address: str, mnemonic: str, flow: str, next_address: str, sp: str, *,
            where: str = "lower ROM (os6128.rom)") -> dict[str, object]:  # fmt: skip
    """A trace record as `trace_records` returns it (only the fields the texts use)."""
    registers = {"af": "0044", "bc": "0000", "de": "0000", "hl": "0000", "ix": "0000",
                 "iy": "0000", "sp": sp}  # fmt: skip
    return {"address": address, "bytes": "00", "mnemonic": mnemonic, "operands": "",
            "flow": flow, "next": next_address, "registers": registers,
            "where_text": where}  # fmt: skip


def test_trace_rows_and_detail() -> None:
    """Depth from taken calls and returns (relative), changes, separators, the detail."""
    assert register_changes({"af": "0D9B", "sp": "BFE4", "bc": "0000", "de": "0", "hl": "0",
                             "ix": "0", "iy": "0"},
                            {"af": "009B", "sp": "BFE2", "bc": "0000", "de": "0", "hl": "0",
                             "ix": "0", "iy": "0"}) == "A=00 SP=BFE2"  # fmt: skip
    records = [
        _record("1D24", "ret", "return", "1D25", "BFE6"),  # returns out of the start level
        _record("1BD9", "call", "call", "1BDC", "BFE8", where="RAM"),  # taken
        _record("4000", "ret", "return", "4001", "BFE6", where="RAM"),  # not taken (cond.)
        _record("4001", "nop", "", "4002", "BFE6", where="RAM"),
    ]
    rows = trace_rows(records, {**records[3]["registers"], "pc": "4002"})  # type: ignore[dict-item]
    kinds = [(row.kind, row.depth, row.marker) for row in rows]
    assert kinds == [("separator", 0, ""), ("instruction", 1, "\u25c2"),
                     ("separator", 0, ""), ("instruction", 0, "\u25b8"),
                     ("instruction", 1, "\u25c2"), ("instruction", 1, "")]  # fmt: skip
    assert rows[2].where == "; ---- RAM" and rows[1].changes == "SP=BFE8"
    detail = trace_detail(records, None, rows[5])
    assert detail["position"] == "4 / 4" and detail["registers"][0][2] == ""  # no "after"
    assert detail["line"].endswith("memory accesses: stepped instructions only")


def test_trace_rows_mark_comment_blocks() -> None:
    """A record with a comment block is marked; the detail shows the blocks cut short."""
    records = [
        _record("9000", "nop", "", "9001", "A000", where="RAM"),
        {**_record("9001", "nop", "", "9002", "A000", where="RAM"),
         "comment_before": "one\ntwo\nthree\nfour\nfive", "comment_after": "done"},
    ]  # fmt: skip
    rows = trace_rows(records, None)  # type: ignore[arg-type]
    assert [row.blocks for row in rows if row.kind == "instruction"] == [False, True]
    assert trace_detail(records, None, rows[1])["notes"] == []  # type: ignore[arg-type]
    assert trace_notes(records[1]) == ["; one", "; two", "; three", "; four", "; …",
                                       ";   done"]  # fmt: skip


def test_bus_lines() -> None:
    """A separator, the instruction with its name split, its accesses; the position line."""
    view = {"groups": [{"pc": "1BD6", "first": 40, "where": "lower ROM (os6128.rom)",
                        "mnemonic": "call", "operands": "TXT_OUTPUT", "name": "TXT_OUTPUT",
                        "name_type": "FW",
                        "accesses": [{"index": 43, "access": "write", "address": "BFE6",
                                      "value": "1BD9", "role": "data",
                                      "meaning": "stack: return address", "word": True},
                                     {"index": 41, "access": "read", "address": "1BD7",
                                      "value": "5A", "role": "operand", "meaning": "operand",
                                      "word": False}]}]}  # fmt: skip
    lines = bus_lines(view)
    assert [line.kind for line in lines] == ["separator", "instruction", "access", "access"]
    assert lines[0].text == "; ---- lower ROM (os6128.rom)" and lines[1].name == "TXT_OUTPUT"
    assert lines[2].body == "BFE6 = 1BD9" and lines[2].meaning == "stack: return address"
    assert not lines[2].quiet
    assert lines[3].quiet
    assert bus_position(lines, 10_000) == "records 41-42 of 10,000"
    assert bus_position([], 0) == "no records"


def test_gaps_in_both_traces() -> None:
    """A gap is a row / line of its own; the registers after the record before it are unknown."""
    gap = {"kind": "gap", "sequence": 3, "instructions": 51240, "emulated_us": 20000}
    assert gap_text(gap) == "---- free run not recorded: 51,240 instructions, 20,000 \u00b5s"
    records = [_record("4000", "nop", "", "4001", "BFE6"), gap,
               _record("5000", "nop", "", "5001", "BFE6")]  # fmt: skip
    rows = trace_rows(records, None)  # type: ignore[arg-type]
    assert [row.kind for row in rows] == ["separator", "instruction", "gap", "separator",
                                          "instruction"]  # fmt: skip
    assert rows[1].changes == ""  # what came after it was not traced
    view = {"groups": [{"gap": {"instructions": 50, "emulated_us": 100}, "first": 7}]}
    assert bus_lines(view)[0].kind == "gap" and bus_lines(view)[0].record == 7


def test_memory_texts() -> None:
    """Pages, innermost variables, colours in order, the table, detail and Follow texts."""
    assert page_line(0xB727) == 0xB70 and page_line(0x00FF) == 0
    window = {"label": "WINDOW", "address": "B726", "last": "B733", "size": 14, "area": False}
    column = {"label": "COLUMN", "address": "B727", "last": "B727", "size": 1, "area": False,
              "description": "cursor column"}  # fmt: skip
    area = {"label": "TXT_WORKSPACE", "address": "B6B5", "last": "B7C2", "size": 270,
            "area": True}  # fmt: skip
    variables = [area, window, column]
    assert innermost(variables, 0xB727) is column and innermost(variables, 0xB700) is None
    assert variable_colour_indexes(variables, 0xB700, 0x100) == {"WINDOW": 0, "COLUMN": 1}
    rows = variable_rows(variables)
    assert [(r["label"], r["range"], r["indent"]) for r in rows] == [
        ("WINDOW", "B726-B733", False), ("COLUMN", "B727", True)]  # fmt: skip
    assert variable_detail(column, variables) == "B727 \u00b7 1 byte \u00b7 in WINDOW"
    changes = {"following": True, "written_count": 3, "last_written": None,
               "written": [{"address": "B727", "value": "07"}],
               "last_step": {"pc": "11AD", "accesses": [
                   {"access": "read", "address": "B72C", "value": "27", "variable": "RIGHT"},
                   {"access": "write", "address": "B727", "value": "07",
                    "variable": "COLUMN"}]}}  # fmt: skip
    assert follow_lines(changes, 0xB700, 0x100) == [
        "last step at PC 11AD: write B727 = 07 (COLUMN) \u00b7 read B72C = 27 (RIGHT)",
        "since the last stop: 1 bytes written in view (3 in all)"]  # fmt: skip
    assert follow_target(changes) == 0xB727  # writes before reads
    assert follow_target({"last_step": None, "last_written": {"address": "C000"}}) == 0xC000
