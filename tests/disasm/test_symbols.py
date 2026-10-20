"""Tests of the symbol table, the firmware preset and the annotation file (D-26, FR-DBG-09)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.debugger.firmware_symbols import FIRMWARE_464
from cpcxray.debugger.symbols import (
    SymbolError,
    SymbolTable,
    format_annotation_line,
    parse_annotation_line,
)


def test_firmware_preset() -> None:
    """The 464 jumpblocks are preloaded: complete main jumpblock, unique labels."""
    table = SymbolTable()
    assert table.label_at(0xBB5A) == "TXT_OUTPUT"
    assert table.label_at(0xBC0E) == "SCR_SET_MODE"
    assert table.label_at(0x0008) == "LOW_JUMP"
    assert [table.label_at(a) for a in (0x000E, 0x0016, 0x001E)] == ["JP_BC", "JP_DE", "JP_HL"]
    assert table.comment_at(0x001E) == "PCHL INSTRUCTION"
    assert table.find("txt_output") is table.at(0xBB5A)  # case-insensitive fallback
    assert table.comment_at(0xBB5A) == "TXT OUTPUT"
    main_jumpblock = {entry.address for entry in FIRMWARE_464 if 0xBB00 <= entry.address <= 0xBD37}
    assert main_jumpblock == set(range(0xBB00, 0xBD38, 3))
    labels = [entry.label for entry in FIRMWARE_464]
    assert len(labels) == len(set(labels))
    assert table.symbols(source="user") == []


def test_scoped_lookup_falls_back_to_global() -> None:
    """A ROM-scoped label wins in its scope only; other scopes see the global symbol."""
    table = SymbolTable(preload_firmware=False)
    table.add(0xC006, "ram_routine")
    table.add(0xC006, "vdos_init", scope="rom5")
    assert table.label_at(0xC006, "rom5") == "vdos_init"
    assert table.label_at(0xC006, "rom7") == "ram_routine"
    assert table.label_at(0xC006) == "ram_routine"


def test_add_keeps_existing_parts() -> None:
    """Adding only a comment keeps the label (and vice versa)."""
    table = SymbolTable()
    table.add(0xBB5A, comment="print A")
    symbol = table.at(0xBB5A)
    assert symbol is not None
    assert (symbol.label, symbol.comment, symbol.source) == ("TXT_OUTPUT", "print A", "user")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"address": 0x8000, "label": "9bad"}, "invalid label"),
     ({"address": 0x8000, "label": "txt_output"}, "already used"),  # case-insensitive
     ({"address": 0x8000, "label": "x", "scope": "rom16"}, "invalid scope"),
     ({"address": 0x8000}, "needs a label"),
     ({"address": 0x10000, "label": "x"}, "out of range")],
)  # fmt: skip
def test_invalid_symbols(kwargs: dict[str, object], message: str) -> None:
    """Invalid labels, duplicates, scopes, empty entries and addresses are rejected."""
    with pytest.raises(SymbolError, match=message):
        SymbolTable().add(**kwargs)  # type: ignore[arg-type]


def test_remove() -> None:
    """Removing returns the symbol; removing nothing is an error."""
    table = SymbolTable()
    table.add(0x8000, "start")
    assert table.remove(0x8000).label == "start"
    with pytest.raises(SymbolError):
        table.remove(0x8000)


def test_annotation_file_round_trip(tmp_path: Path) -> None:
    """Saving writes only user symbols; loading them back reproduces the entries."""
    table = SymbolTable()
    table.add(0x8000, "start", "entry point")
    table.add(0xC000, "vdos_header", scope="rom5")
    table.add(0x9000, comment="buffer, 256 bytes")
    path = tmp_path / "annotations.txt"
    assert table.save(path) == 3
    text = path.read_text()
    assert "8000        start                ; entry point" in text
    assert "rom5:C000   vdos_header" in text
    assert "9000        -                    ; buffer, 256 bytes" in text
    loaded = SymbolTable()
    assert loaded.load(path) == 3
    assert loaded.symbols(source="user") == table.symbols(source="user")


@pytest.mark.parametrize(
    ("line", "expected"),
    [("BB5A txt_out ; print", (0xBB5A, "txt_out", "print", "")),
     ("&8000 start", (0x8000, "start", None, "")),
     ("rom5:#C006 vdos_init", (0xC006, "vdos_init", None, "rom5")),
     ("lower:0038 - ; interrupt", (0x0038, None, "interrupt", "lower"))],
)  # fmt: skip
def test_parse_annotation_line(line: str, expected: tuple[object, ...]) -> None:
    """Addresses with & or # prefixes, scopes, '-' for no label and comments parse."""
    symbol = parse_annotation_line(line)
    assert symbol is not None
    assert (symbol.address, symbol.label, symbol.comment, symbol.scope) == expected
    assert parse_annotation_line(format_annotation_line(symbol)) == symbol


@pytest.mark.parametrize("line", ["", "   ", "# comment"])
def test_blank_and_comment_lines(line: str) -> None:
    """Blank lines and # comments are skipped."""
    assert parse_annotation_line(line) is None


def test_malformed_file_reports_line(tmp_path: Path) -> None:
    """A malformed line is reported with file name and line number."""
    path = tmp_path / "bad.txt"
    path.write_text("8000 start\nXYZ label\n")
    with pytest.raises(SymbolError, match=r"bad\.txt:2: invalid address"):
        SymbolTable().load(path)


def test_search() -> None:
    """symbols() filters by text in label or comment, scope and source."""
    table = SymbolTable()
    names = [symbol.label for symbol in table.symbols("scr set mode")]
    assert names == ["SCR_SET_MODE"]
    assert all(symbol.label.startswith("KM_") for symbol in table.symbols("km_") if symbol.label)
