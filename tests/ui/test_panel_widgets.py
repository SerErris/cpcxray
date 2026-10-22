"""Headless parts of the GUI building blocks: colour table, list logic, file selector helpers."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cpcxray.ui.panels import colours
from cpcxray.ui.panels.file_selector import (
    FolderEntry,
    entry_line,
    list_folder,
    shown_path,
    start_folder,
)
from cpcxray.ui.panels.scroll_list import ListState


def test_colour_table() -> None:
    """Mock-up colours convert; badge styles and the six symbol types are all defined."""
    assert colours.rgb("#8fb8ff") == (0x8F, 0xB8, 0xFF)
    with pytest.raises(ValueError, match="rrggbb"):
        colours.rgb("#fff")
    assert set(colours.BADGE_STYLES) >= {"plain", "ok", "changed", "here"}
    assert tuple(colours.SYMBOL_TYPE_COLOURS) == colours.SYMBOL_TYPES
    assert colours.rgb("#ffa040") == colours.CHANGED  # the agreed orange warning light


def test_list_keys_move_and_scroll() -> None:
    """Keys move the selection inside the list; the visible window follows it."""
    state = ListState(visible_rows=5)
    assert not state.move("down")  # an empty list
    state.set_count(20)
    assert state.move("down") and state.selected == 0  # first key: the first row
    state.move("page_down")
    assert (state.selected, state.top) == (4, 0)
    state.move("down")
    assert (state.selected, state.top) == (5, 1)  # scrolled by one row
    state.move("end")
    assert (state.selected, state.top) == (19, 15)
    assert not state.move("down")  # at the end
    state.move("home")
    assert (state.selected, state.top) == (0, 0)
    state.move("up")
    assert state.selected == 0
    with pytest.raises(ValueError, match="key"):
        state.move("left")


def test_list_shrinks() -> None:
    """Fewer rows keep the selection and the scroll position inside the list."""
    state = ListState(visible_rows=5, count=20)
    state.select(18)
    state.set_count(10)
    assert (state.selected, state.top) == (9, 5)
    state.set_count(0)
    assert (state.selected, state.top) == (None, 0)
    with pytest.raises(IndexError):
        state.select(3)


def test_start_folder(tmp_path: Path) -> None:
    """The dialog opens in the path's folder, else the field's folder, else the project."""
    (tmp_path / "disk").mkdir()
    (tmp_path / "output").mkdir()
    disk = tmp_path / "disk"
    assert start_folder("output/x.dsk", Path("disk"), tmp_path) == (tmp_path / "output").resolve()
    assert start_folder("", Path("disk"), tmp_path) == disk.resolve()
    assert start_folder("nowhere/x.dsk", Path("disk"), tmp_path) == disk.resolve()
    assert start_folder("", Path("missing"), tmp_path) == tmp_path.resolve()
    assert start_folder(str(disk), Path("output"), tmp_path) == disk.resolve()  # a folder


def test_list_folder(tmp_path: Path) -> None:
    """Parent, folders, then files of the wanted types (case-insensitive); no hidden ones."""
    (tmp_path / "b.dsk").write_bytes(b"12345")
    (tmp_path / "A.DSK").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / ".hidden.dsk").write_bytes(b"")
    (tmp_path / "sub").mkdir()
    os.utime(tmp_path / "b.dsk", (0, 86400))
    entries = list_folder(tmp_path, (".dsk",))
    assert [e.name for e in entries] == ["..", "sub", "A.DSK", "b.dsk"]
    assert entries[3].size == 5 and not entries[3].is_folder
    assert len(list_folder(tmp_path)) == 5  # all files
    assert entry_line(FolderEntry("sub", True)) == "sub/"
    line = entry_line(entries[3])
    assert line.startswith("b.dsk") and "        5  1970-01-0" in line


def test_shown_path(tmp_path: Path) -> None:
    """Paths inside the project are shown relative to it, others absolute."""
    assert shown_path(tmp_path / "disk" / "a.dsk", tmp_path) == str(Path("disk/a.dsk"))
    outside = tmp_path.parent / "elsewhere.dsk"
    assert shown_path(outside, tmp_path) == str(outside.resolve())
