"""Tests of the symbol table, the firmware preset and the annotation file (D-26, FR-DBG-09)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.debugger.firmware_symbols import FIRMWARE_464, FIRMWARE_664, FIRMWARE_6128
from cpcxray.debugger.symbols import (
    OwnVariable,
    Symbol,
    SymbolError,
    SymbolTable,
    format_annotation_line,
    merge,
    parse_annotation_line,
    parse_variable_base,
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


@pytest.mark.parametrize("entries", [FIRMWARE_464, FIRMWARE_664, FIRMWARE_6128])
def test_firmware_tables_are_consistent(entries: tuple) -> None:  # type: ignore[type-arg]
    """Every table has unique addresses and labels, and the common jumpblocks complete."""
    addresses = [entry.address for entry in entries]
    labels = [entry.label for entry in entries]
    assert len(addresses) == len(set(addresses)) and len(labels) == len(set(labels))
    assert set(range(0xBB00, 0xBD38, 3)) <= set(addresses)  # main entries 0-189
    assert {0x0000, 0x0038, 0xB900, 0xB92A, 0xBDCD, 0xBDF1} <= set(addresses)


def test_firmware_names_per_machine() -> None:
    """Each machine gets its own names (Firmware Guide via docs/firmware.md).

    464: no entries 190-201, TEXT INPUT and maths from &BD3A, the integer jumpblock, no KM SCAN
    KEYS; 664: entries 190-200, TEXT INPUT and maths from &BD5B; 6128: also KL BANK SWITCH, TEXT
    INPUT and maths from &BD5E (docs/firmware.md section 7).
    """
    cpc464, cpc664, cpc6128 = (SymbolTable(model=m) for m in ("464", "664", "6128"))
    assert cpc464.label_at(0xBD3A) == "TEXT_INPUT" and cpc464.label_at(0xBD3D) == "MOVE_REAL"
    assert cpc464.label_at(0xBD5B) == "REAL_SUBTRACTION" and cpc464.label_at(0xBDF4) is None
    assert cpc464.label_at(0xBDCA) == "INTEGER_SIGNUM_SGN"
    assert cpc664.label_at(0xBD3A) == "KM_SET_LOCKS" and cpc664.label_at(0xBD3D) == "KM_FLUSH"
    assert cpc664.label_at(0xBD5B) == "TEXT_INPUT" and cpc664.label_at(0xBD5E) == "MOVE_REAL"
    assert cpc664.label_at(0xBDA0) == "REAL_LOG" and cpc664.label_at(0xBDF4) == "KM_SCAN_KEYS"
    assert cpc664.label_at(0xBDBE) is None
    assert cpc6128.label_at(0xBD5B) == "KL_BANK_SWITCH" and cpc6128.label_at(0xBD5E) == "TEXT_INPUT"
    assert cpc6128.label_at(0xBD61) == "MOVE_REAL" and cpc6128.label_at(0xBDA3) == "REAL_LOG"
    assert cpc6128.label_at(0xBD7F) == "REAL_RND" and cpc6128.label_at(0xBD8B) == "REAL_RND_0"
    assert cpc6128.comment_at(0xBC77) == "CAS IN OPEN (AMSDOS patch, V1.0 differs)"
    assert cpc6128.label_at(0x0023) == "KL_FAR_ICALL"


def test_set_firmware_keeps_the_owners_symbols() -> None:
    """Switching the machine replaces the firmware names; the owner's symbols stay and win."""
    table = SymbolTable(model="464")
    table.add(0x8000, "my_routine")
    table.add(0xBD3D, "my_maths_hook")  # on the 464 a firmware address
    table.set_firmware("6128")
    assert table.firmware_model == "6128"
    assert table.label_at(0x8000) == "my_routine" and table.label_at(0xBD3D) == "my_maths_hook"
    assert table.label_at(0xBD5B) == "KL_BANK_SWITCH" and table.label_at(0xBD3A) == "KM_SET_LOCKS"
    table.set_firmware("464")
    assert (
        table.label_at(0xBD5B) == "REAL_SUBTRACTION" and table.label_at(0xBD40) == "INTEGER_TO_REAL"
    )
    with pytest.raises(SymbolError, match="model"):
        table.set_firmware("664plus")


def test_scoped_lookup_falls_back_to_global() -> None:
    """A ROM-scoped label wins in its scope only; other scopes see the global symbol."""
    table = SymbolTable(preload_firmware=False)
    table.add(0xC006, "ram_routine")
    table.add(0xC006, "rom_entry", scope="rom5")
    assert table.label_at(0xC006, "rom5") == "rom_entry"
    assert table.label_at(0xC006, "rom7") == "ram_routine"
    assert table.label_at(0xC006) == "ram_routine"


def test_add_keeps_existing_parts() -> None:
    """Adding only a comment keeps the owner's label (and vice versa)."""
    table = SymbolTable(preload_firmware=False)
    table.add(0x8000, "start")
    table.add(0x8000, comment="entry")
    symbol = table.at(0x8000)
    assert symbol is not None
    assert (symbol.label, symbol.comment, symbol.source) == ("start", "entry", "user")


def test_owner_layer_over_firmware(tmp_path: Path) -> None:
    """The owner's comment or label lies over a firmware name and never replaces it."""
    table = SymbolTable()
    table.add(0xBB5A, comment="hooked by my program")
    symbol = table.at(0xBB5A)
    assert symbol is not None
    assert (symbol.label, symbol.comment, symbol.source) == (
        "TXT_OUTPUT",
        "TXT OUTPUT · hooked by my program",
        "user",
    )
    owner = table.owner_at(0xBB5A)
    assert owner is not None and (owner.label, owner.comment) == (None, "hooked by my program")
    table.add(0xBB5A, "my_output")
    assert table.label_at(0xBB5A) == "my_output"
    assert table.find("TXT_OUTPUT") == table.at(0xBB5A)  # the firmware label still finds it
    path = tmp_path / "annotations.txt"
    table.save(path)
    assert "BB5A        my_output            ; hooked by my program" in path.read_text()
    assert table.remove(0xBB5A).label == "my_output"
    assert (table.label_at(0xBB5A), table.comment_at(0xBB5A)) == ("TXT_OUTPUT", "TXT OUTPUT")
    with pytest.raises(SymbolError, match="cannot be removed"):
        table.remove(0xBB5A)
    assert [s.label for s in table.symbols("txt output")] == ["TXT_OUTPUT"]


def test_old_annotation_files_repeating_firmware_names(tmp_path: Path) -> None:
    """A firmware label or comment repeated in an annotation file is not stored again."""
    path = tmp_path / "annotations.txt"
    path.write_text("BB5A TXT_OUTPUT ; print A\nBB5D TXT_WR_CHAR ; TXT WR CHAR\n")
    table = SymbolTable()
    table.load(path)
    assert table.comment_at(0xBB5A) == "TXT OUTPUT · print A"
    assert [(s.address, s.label, s.comment) for s in table.owner_symbols()] == [
        (0xBB5A, None, "print A")
    ]


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
    table.add(0xC000, "rom_header", scope="rom5")
    table.add(0x9000, comment="buffer, 256 bytes")
    path = tmp_path / "annotations.txt"
    assert table.save(path) == 3
    text = path.read_text()
    assert "8000        start                ; entry point" in text
    assert "rom5:C000   rom_header" in text
    assert "9000        -                    ; buffer, 256 bytes" in text
    loaded = SymbolTable()
    assert loaded.load(path) == 3
    assert loaded.symbols(source="user") == table.symbols(source="user")


@pytest.mark.parametrize(
    ("line", "expected"),
    [("BB5A txt_out ; print", (0xBB5A, "txt_out", "print", "")),
     ("&8000 start", (0x8000, "start", None, "")),
     ("rom5:#C006 rom_entry", (0xC006, "rom_entry", None, "rom5")),
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


def test_symbol_types() -> None:
    """Firmware names are FW; the owner's labels sub (default), jump or data; comments none."""
    table = SymbolTable()
    assert table.at(0xBB5A).type == "FW"
    assert table.add(0x8000, "start").type == "sub"
    assert table.add(0x8010, "loop", type="jump").type == "jump"
    assert table.add(0x8010, comment="the main loop").type == "jump"  # kept
    assert table.add(0x9000, comment="a buffer").type is None
    assert table.add(0xBB5A, comment="hooked").type == "FW"  # a comment over a firmware name
    assert table.add(0xBB5A, "my_output", type="data").type == "data"
    with pytest.raises(SymbolError, match="invalid type"):
        table.add(0x8020, "x", type="var")


def test_own_variables() -> None:
    """The owner's variables: absolute or in a ROM's data area; labels unique with symbols."""
    table = SymbolTable()
    table.add(0x8000, "start")
    counter = table.add_variable(OwnVariable("counter", 2, 0xA000, comment="frames"))
    assert counter.base_text == "A000"
    assert OwnVariable("drives", 16, 0x12, rom=5).base_text == "rom5+0012"
    table.add_variable(OwnVariable("drives", 16, 0x12, rom=5))
    assert [v.label for v in table.own_variables()] == ["counter", "drives"]
    with pytest.raises(SymbolError, match="already used"):
        table.add_variable(OwnVariable("start", 1, 0x9000))  # a symbol's label
    with pytest.raises(SymbolError, match="already used by a variable"):
        table.add(0x8100, "counter")
    with pytest.raises(SymbolError, match="already used by a variable"):
        table.add_variable(OwnVariable("counter", 1, 0x9000))
    table.add_variable(OwnVariable("frame_count", 2, 0xA000), replaces="counter")  # rename
    assert table.variable("COUNTER") is None and table.variable("frame_count") is not None
    with pytest.raises(SymbolError, match="invalid size"):
        table.add_variable(OwnVariable("empty", 0, 0xA000))
    assert table.remove_variable("drives").rom == 5
    with pytest.raises(SymbolError, match="no variable"):
        table.remove_variable("drives")


def test_annotation_file_with_types_and_variables(tmp_path: Path) -> None:
    """Type words and var lines are written and read back; `modified` follows load and save."""
    table = SymbolTable()
    assert not table.modified
    table.add(0x8010, "loop", "again", type="jump")
    table.add(0xC015, "messages", scope="rom5", type="data")
    table.add_variable(OwnVariable("drives", 16, 0x12, rom=5, comment="one per drive"))
    assert table.modified
    path = tmp_path / "annotations.txt"
    assert table.save(path) == 3
    assert not table.modified
    text = path.read_text()
    assert "jump 8010        loop                 ; again" in text
    assert "data rom5:C015   messages" in text
    assert "var  rom5+0012   drives               16 ; one per drive" in text
    loaded = SymbolTable()
    assert loaded.load(path) == 3 and not loaded.modified
    assert loaded.owner_symbols() == table.owner_symbols()
    assert loaded.own_variables() == table.own_variables()
    assert merge([table]).own_variables() == table.own_variables()


@pytest.mark.parametrize(
    ("line", "message"),
    [("var A000 counter", "expected 'var"), ("var rom16+0000 x 1", "invalid ROM"),
     ("var A000 counter two", "invalid size"), ("var xyz counter 1", "invalid variable address")],
)  # fmt: skip
def test_malformed_variable_lines(line: str, message: str) -> None:
    """A var line needs place, label and size."""
    with pytest.raises(SymbolError, match=message):
        parse_annotation_line(line)


def test_old_lines_are_sub_labels() -> None:
    """A line without a type word (files of earlier versions) is a "sub" label."""
    symbol = parse_annotation_line("BB5A my_output ; print")
    assert isinstance(symbol, Symbol) and symbol.type == "sub"
    comment = parse_annotation_line("9000 - ; buffer")
    assert isinstance(comment, Symbol) and comment.type is None
    assert parse_variable_base("&A612") == (None, 0xA612)
