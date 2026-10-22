"""Disks tab: drive chips, the drive's card, file list and file detail (task 5h, FR-FDC).

Layout agreed with the owner on 2026-10-22 (design canvas, artboard "Disks 2: drive chips, list
and detail"), top to bottom:

* Drive chips ("● A: 464_cpm_22_orig.dsk", "○ B: no disk") select the drive to work with; a
  "changed" badge beside a chip while the CPC's writes to that disk are not saved.
* The drive's card (framed yellow): activity LED, disk name, badges (read-only / writable,
  draft, "changed" - lit orange while unsaved, dim grey otherwise), geometry, head position;
  the image path with ▾ (file selector in the image's folder, disk images or all files),
  writable, one Insert / Eject button (never both), Save (only when there is something to save
  back to its file), Save as… (the drive then uses the new file, writable; the old file stays
  untouched); new draft in the format system / data / vortex.
* The catalogue (read directly from the image, CP/M) in a scroll list, files with size, used
  and free space; beside it the selected file (framed yellow): size and user, the AMSDOS
  header, the bytes after the header, the code at the entry address with symbol names - or a
  text file's first lines.
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.session.errors import SessionError
from cpcxray.ui.panels import colours
from cpcxray.ui.panels.base import TAB_WIDTH, Tab
from cpcxray.ui.panels.file_selector import shown_path
from cpcxray.ui.panels.scroll_list import ScrollList
from cpcxray.ui.panels.text import (
    catalog_header,
    disk_name,
    drive_chip,
    drive_geometry,
    file_detail,
    split_label,
)
from cpcxray.ui.panels.widgets import BOX_GAP, badge, section, set_badge

DISK_EXTENSIONS = (".dsk",)
DRAFT_FORMATS = ("system", "data", "vortex")
DRIVES = ("A", "B")
LIST_WIDTH = 200  # names and sizes; the rest is the detail's (a 16-byte line with ASCII)
LIST_ROWS = 19
LOWER_HEIGHT = 384  # the file list and the detail beside it end on the same line
DETAIL_ROWS = 6  # key/value rows of the file detail
CARD_PADDING = (9, 7)  # the card's first item from its outer corner (padding and frame)


class DisksTab(Tab):  # pylint: disable=too-many-instance-attributes  # drive, file, list state
    """Drive chips, the drive card, catalogue and file detail."""

    title = "Disks"
    message = "disk_message"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create the tab; drive A is selected first."""
        super().__init__(*args, **kwargs)
        self.drive = "A"
        self.files: list[dict[str, Any]] = []
        self.file_list: ScrollList | None = None
        self._catalog_shown: Any = None  # the catalogue the list shows (rebuilt on change)
        self._detail_shown: Any = None  # the file the detail shows

    # -- building --------------------------------------------------------------------------------

    def build(self) -> None:
        """Create the chips, the card, the file list and the detail."""
        with dpg.group(horizontal=True):
            for letter in DRIVES:
                self.items[f"disk_chip_{letter}"] = dpg.add_button(
                    label=letter, callback=lambda _s, _v, chosen: self.select_drive(chosen),
                    user_data=letter,
                )  # fmt: skip
                self.items[f"disk_chip_changed_{letter}"] = badge(
                    self.styles, "changed", "changed", show=False
                )
        self._build_card()
        with dpg.group(horizontal=True, horizontal_spacing=BOX_GAP) as row:
            dpg.bind_item_theme(row, self.styles.box_row)
            with section(self.styles, width=LIST_WIDTH, height=LOWER_HEIGHT):
                self.text("catalog_count", color=colours.HEADING)
                self.text("catalog_header", color=colours.TEXT_DIM)
                self.file_list = self.scroll_list(LIST_ROWS, on_select=self._file_selected)
            detail_width = TAB_WIDTH - LIST_WIDTH - BOX_GAP
            with section(self.styles, width=detail_width, height=LOWER_HEIGHT, style="here"):
                self.text("file_title", color=colours.HEADING)
                for row_number in range(DETAIL_ROWS):
                    with dpg.group(horizontal=True):
                        self.text(f"file_key_{row_number}", color=colours.TEXT_DIM)
                        self.text(f"file_value_{row_number}")
                self.items["file_blocks"] = dpg.add_group()
        self.text("disk_message", color=colours.TEXT_DIM)

    def _build_card(self) -> None:
        """The selected drive's card (framed yellow)."""
        styles, session = self.styles, self.session
        with section(styles, width=TAB_WIDTH, style="here"):
            with dpg.group(horizontal=True):
                self.text("disk_led")
                self.text("disk_title")
                self.items["disk_mode"] = badge(styles, "read-only")
                self.items["disk_draft"] = badge(styles, "draft", show=False)
                self.items["disk_changed"] = badge(styles, "changed")
                self.text("disk_geometry", color=colours.TEXT_DIM)
            with dpg.group(horizontal=True):
                dpg.add_text("head", color=colours.TEXT_DIM)
                self.items["disk_head"] = dpg.add_progress_bar(width=380, height=8)
                self.text("disk_cylinder", color=colours.TEXT_DIM)
            with self.path_field("disk_path", 250, "disk", DISK_EXTENSIONS,
                                 placement=self._over_card, hint="disk/image.dsk"):  # fmt: skip
                self.items["disk_writable"] = dpg.add_checkbox(label="writable")
                self.items["disk_insert"] = self.button("Insert", self.insert_or_eject, width=56)
                self.items["disk_save"] = self.button("Save", lambda: session.disk_save(self.drive))
                self.items["disk_save_as"] = self.button("Save as…", self.save_as)
            with dpg.group(horizontal=True):
                dpg.add_text("new draft", color=colours.TEXT_DIM)
                for disk_format in DRAFT_FORMATS:
                    self.button(disk_format, lambda chosen=disk_format: session.disk_new(
                        self.drive, chosen))  # fmt: skip

    def _over_card(self) -> tuple[int, int, int]:
        """The file selector opens on the drive card: its top left corner, its width (owner)."""
        x, y = dpg.get_item_rect_min(self.items["disk_led"])  # first item of the card
        padding_x, padding_y = CARD_PADDING
        return int(x - padding_x), int(y - padding_y), TAB_WIDTH

    # -- actions ---------------------------------------------------------------------------------

    def select_drive(self, letter: str) -> None:
        """Work with another drive: its card, its image path, its catalogue."""
        self.drive = letter
        self._catalog_shown = self._detail_shown = None
        self._show_image_path()
        self.refresh()

    def insert_or_eject(self) -> Any:
        """Eject the disk in the drive, or insert the path field's image when it is empty."""
        if self._drive_state()["disk"] is not None:
            return self.session.disk_eject(self.drive)
        result = self.session.disk_insert(self.drive, self.value("disk_path").strip(),
                                          bool(self.value("disk_writable")))  # fmt: skip
        self._catalog_shown = None
        return result

    def save_as(self) -> Any:
        """Save the disk to the path field's file; the drive then uses that file."""
        result = self.session.disk_save(self.drive, self.value("disk_path"))
        self._show_image_path()
        return result

    def _file_selected(self, index: int) -> None:
        """A file was selected in the list: show its detail."""
        self._show_detail(self.files[index])

    def _drive_state(self) -> dict[str, Any]:
        """The selected drive as `disk_list` describes it."""
        drives = self.session.disk_list()["drives"]
        return next(d for d in drives if d["drive"] == self.drive)

    def _show_image_path(self) -> None:
        """Put the drive's image (relative to the project) into the path field."""
        disk = self._drive_state()["disk"]
        if disk is not None and disk != "draft":
            dpg.set_value(self.items["disk_path"], shown_path(disk, self.session.project_dir))

    # -- refreshing ------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Chips, card, catalogue (rebuilt only when it changed) and the detail."""
        drives = self.session.disk_list()["drives"]
        for drive in drives:
            letter = drive["drive"]
            chip = self.items[f"disk_chip_{letter}"]
            dpg.set_item_label(chip, drive_chip(drive))
            selected = letter == self.drive
            dpg.bind_item_theme(chip, self.styles.primary_button if selected
                                else self.styles.plain_button)  # fmt: skip
            dpg.configure_item(self.items[f"disk_chip_changed_{letter}"],
                               show=bool(drive.get("modified")))  # fmt: skip
        drive = next(d for d in drives if d["drive"] == self.drive)
        if not self.value("disk_path").strip():
            self._show_image_path()
        self._refresh_card(drive)
        self._refresh_catalog(drive)

    def _refresh_card(self, drive: dict[str, Any]) -> None:
        """LED, name, badges, geometry, head; the buttons that make sense now."""
        items, styles = self.items, self.styles
        fdc = getattr(self.session.machine, "fdc", None)
        lit = fdc is not None and fdc.drive_lights()[DRIVES.index(self.drive)]
        dpg.set_value(items["disk_led"], "●")
        dpg.configure_item(items["disk_led"], color=colours.LED_ON if lit else colours.LED_OFF)
        disk = drive["disk"]
        dpg.set_value(items["disk_title"], f"{self.drive}: {disk_name(disk)}")
        has_disk = disk is not None
        writable = bool(drive.get("writable"))
        set_badge(styles, items["disk_mode"], "writable" if writable else "read-only",
                  "ok" if writable else "plain")  # fmt: skip
        dpg.configure_item(items["disk_mode"], show=has_disk)
        dpg.configure_item(items["disk_draft"], show=disk == "draft")
        modified = bool(drive.get("modified"))
        set_badge(styles, items["disk_changed"], "changed", "changed" if modified else "plain")
        dpg.configure_item(items["disk_changed"], show=has_disk)
        dpg.set_value(items["disk_geometry"], drive_geometry(drive))
        dpg.set_value(items["disk_head"], min(1.0, drive["head_cylinder"] / drive["cylinders"]))
        dpg.set_value(items["disk_cylinder"],
                      f"cylinder {drive['head_cylinder']} / {drive['cylinders']}")  # fmt: skip
        dpg.set_item_label(items["disk_insert"], "Eject" if has_disk else "Insert")
        can_save = has_disk and modified and writable and disk != "draft"
        dpg.configure_item(items["disk_save"], enabled=can_save)
        dpg.bind_item_theme(items["disk_save"], styles.plain_button if can_save
                            else styles.disabled_button)  # fmt: skip
        dpg.configure_item(items["disk_save_as"], enabled=has_disk)

    def _refresh_catalog(self, drive: dict[str, Any]) -> None:
        """The file list, rebuilt when the disk's directory changed."""
        assert self.file_list is not None
        try:
            catalog = self.session.disk_catalog(self.drive) if drive["disk"] else None
        except SessionError as error:
            catalog = {"error": str(error)}
        if catalog == self._catalog_shown:
            return
        self._catalog_shown = catalog
        if catalog is None or "error" in catalog:
            self.files = []
            dpg.set_value(self.items["catalog_count"],
                          "no disk" if catalog is None else catalog["error"])  # fmt: skip
            dpg.set_value(self.items["catalog_header"], "")
            self.file_list.set_rows([])
            self._show_detail(None)
            return
        self.files = catalog["files"]
        count, space = catalog_header(catalog).split(" \u00b7 ", 1)
        dpg.set_value(self.items["catalog_count"], count)
        dpg.set_value(self.items["catalog_header"], space)
        rows = [(f"{f['name']:<13}{f['size']:>7}", colours.TEXT_DIM if f["system"] else None)
                for f in self.files]  # fmt: skip
        self.file_list.set_rows(rows)
        selected = self.file_list.selected
        self._show_detail(self.files[selected] if selected is not None else None)

    def _show_detail(self, entry: dict[str, Any] | None) -> None:
        """Fill the detail panel for a file of the catalogue (None: empty)."""
        key = (self.drive, entry and entry["name"], entry and entry["user"])
        if key == self._detail_shown:
            return
        self._detail_shown = key
        items = self.items
        dpg.delete_item(items["file_blocks"], children_only=True)
        rows: list[tuple[str, str]] = []
        if entry is None:
            dpg.set_value(items["file_title"], "select a file")
        else:
            dpg.set_value(items["file_title"], entry["name"])
            try:
                info = self.session.disk_read_file(entry["name"], self.drive, user=entry["user"])
                detail = file_detail(info, entry["user"])
                rows = detail["rows"]
                self._add_blocks(detail["blocks"], detail["code"])
            except SessionError as error:
                rows = [("error", str(error))]
        for number in range(DETAIL_ROWS):
            label, value = rows[number] if number < len(rows) else ("", "")
            dpg.set_value(items[f"file_key_{number}"], f"{label:<8}" if label else "")
            dpg.set_value(items[f"file_value_{number}"], value)

    def _add_blocks(self, blocks: list[tuple[str, list[str]]], code: list[dict[str, Any]]) -> None:
        """The detail's blocks: a dividing line, a dim title, its lines; then the code."""
        parent = self.items["file_blocks"]
        for title, lines in blocks:
            dpg.add_separator(parent=parent)
            dpg.add_text(title, color=colours.TEXT_DIM, parent=parent)
            dpg.add_text("\n".join(lines), color=colours.TEXT_BYTES, parent=parent)
        for line in code:  # address (dim), instruction, the name in its type's colour
            before, label, after = split_label(line)
            with dpg.group(horizontal=True, horizontal_spacing=0, parent=parent):
                dpg.add_text(f"{line['address']}  ", color=colours.TEXT_DIM)
                dpg.add_text(before)
                if label:
                    label_type = {"own": "sub"}.get(line.get("label_type", "FW"),
                                                    line.get("label_type", "FW"))  # fmt: skip
                    dpg.add_text(label, color=colours.SYMBOL_TYPE_COLOURS[label_type][0])
                if after:
                    dpg.add_text(after)
