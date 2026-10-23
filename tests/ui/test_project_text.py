"""Headless tests of the Project tab's text and documents (task 5i, ui/panels/project_text.py)."""

from __future__ import annotations

from cpcxray.ui.panels.project_text import (
    document,
    drives_line,
    free_slots,
    header_text,
    load_text,
    machine_title,
    model_chip,
    number,
    rom_table,
    rom_tag,
    roms_of,
)

CONFIG = {
    "machine": {"model": "6128", "unpopulated_rom": 0},
    "rom": {"lower": {"file": "roms/os6128.rom"},
            "upper": {"7": {"file": "roms/amsdos.rom"},
                      "5": {"file": "roms/x.rom", "decoder": "vortex", "analyse": True}}},
    "drive": {"A": {"file": "disk/cpm.dsk", "writable": False}},
    "analysis": {"ram": [{"name": "loader", "start": 0x8000, "length": 0x100,
                          "capture": "first_m1"}]},
}  # fmt: skip


def test_summary_texts() -> None:
    """Title, model chips, ROM tags in slot order, the drives line."""
    assert machine_title("6128") == "CPC 6128 · 128K"
    assert model_chip("664") == "664 · 64K"
    assert [rom_tag(slot, table) for slot, table in roms_of(CONFIG)] == [
        "lower · os6128.rom", "5 · x.rom · vortex · analysed",
        "7 · amsdos.rom"]  # fmt: skip
    assert drives_line(CONFIG) == (
        "A: cpm.dsk (read-only) · B: empty · RAM code to analyse: loader 8000+100"
    )


def test_editor_fields_make_the_document() -> None:
    """The editor's fields give the same document as the configuration they were filled from."""
    roms = [(slot, rom_table(t["file"], t.get("decoder") or "none", bool(t.get("analyse"))))
            for slot, t in roms_of(CONFIG)]  # fmt: skip
    assert document("6128", 0, roms, CONFIG["drive"], CONFIG["analysis"]["ram"]) == CONFIG
    assert rom_table(" roms/a.rom ", "none", False) == {"file": "roms/a.rom"}
    assert rom_table("x.rom", "vortex", True) == {"file": "x.rom", "decoder": "vortex",
                                                  "analyse": True}  # fmt: skip
    assert document("464", 0, [], {}, []) == {
        "machine": {"model": "464", "unpopulated_rom": 0}, "rom": {}}  # fmt: skip


def test_numbers_headers_and_slots() -> None:
    """Hex numbers (invalid ones stay text), header texts, the free slots."""
    assert [number(t) for t in ("8000", "&8000", "#100", "0x10", "zz")] == [
        0x8000, 0x8000, 0x100, 0x10, "zz"]  # fmt: skip
    assert header_text({"type": "background", "version": "0.50", "name": "CPM ROM"}) == (
        "background 0.50 · CPM ROM"
    )
    assert header_text({"error": "file missing"}) == "file missing"
    assert free_slots(["lower", "0", "7"])[:3] == ["1", "2", "3"]


def test_load_text() -> None:
    """A binary file to load: name, address, size and state (or a missing file)."""
    entry = {"file": "bin/game.bin", "address": "8000", "exists": True, "length": 3,
             "loaded": True}  # fmt: skip
    assert load_text(entry) == "game.bin \u2192 8000 (3 bytes, loaded)"
    assert load_text(entry | {"loaded": False}).endswith("not loaded)")
    assert load_text(entry | {"exists": False}) == "game.bin \u2192 8000 (file missing)"
