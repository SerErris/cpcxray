"""Project tab (was "Files"): the machine and its editor, analysis, snapshot (task 5i).

Layout agreed with the owner on 2026-10-22 (design canvas, artboard "Project 3: machine summary
/ Edit + analysis phases"), top to bottom:

* Machine, a summary by default: model and RAM, the config file, "changed" (orange, as in
  Disks) while the editor holds unsaved changes, "Apply (resets the machine)" beside it (owner,
  2026-10-23: at the top, away from the files to load), Edit / Close; the ROMs as tags
  (decoder and analysis named); the drives and the RAM code to analyse; the binary files to
  load, each with a light and Load - loaded only on command, when it suits the system that runs
  (the boot clears RAM). The light is a reminder: green once the file was loaded, red again
  after a reset, a new machine or a change of the file or its address; RAM is not compared.
* Edit opens the machine editor for everything a machine.toml holds (design.md §6.14): the
  config file with ▾, Load, Save, Save as… (lit blue while there are unsaved changes); model
  chips and the slot for empty ROM numbers; tables with a heading per column: ROMs (file with
  ▾, decoder - a ROM with one is always decoded -, analyse, what its header says, ✕) with
  "+ ROM in slot"; drives A and B (image with ▾, writable); RAM code to analyse (RAM targets,
  D-23: name, from, length, when the copy is taken, ✕) with "+ RAM range"; binary files to
  load into RAM (`[[load]]`: file ▾, at, light, Load, ✕) with "+ file". Apply is enabled only
  while the editor differs from the running machine in what makes it (model, ROMs, drives,
  RAM targets - not the files to load); those changes need a power cycle.
* Analysis: save the analysis, generate and verify the source (the phases follow).
* Snapshot: the .sna path with ▾, Save, Load.

The editor's rows are fixed pools (17 ROM slots, 2 drives, 4 RAM targets) shown or hidden, so
no widget is ever deleted. The configuration being edited is collected from the fields
(`_collect`) and compared with the one loaded, saved or applied last ("changed").
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels import colours
from cpcxray.ui.panels.base import PANEL_WIDTH, TAB_WIDTH, Tab
from cpcxray.ui.panels.project_text import (
    CAPTURE_CHOICES,
    DECODER_CHOICES,
    DRIVE_LETTERS,
    MODEL_CHIPS,
    SLOTS,
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
from cpcxray.ui.panels.widgets import Chips, badge, remove_button, section, separator, set_badge

SNAPSHOT_EXTENSIONS = (".sna",)
CONFIG_EXTENSIONS = (".toml",)
ROM_EXTENSIONS = (".rom", ".bin")
DISK_EXTENSIONS = (".dsk",)
RAM_ROWS = 4
LOAD_ROWS = 4
EDIT_WIDTH = 56  # the Edit / Close button
LOAD_COLUMNS = (("file", 300), ("at", 70), ("loaded", 50), ("", 50), ("", 18))
# the editor's tables: (column heading, width in pixels)
ROM_COLUMNS = (("ROM", 36), ("file", 190), ("decoder", 62), ("analyse", 48),
               ("description", 190), ("", 18))  # fmt: skip
DRIVE_COLUMNS = (("drive", 36), ("image", 330), ("writable", 60))
RAM_COLUMNS = (("name", 130), ("from", 70), ("length", 70), ("copy taken", 160), ("", 18))
MAX_TAGS = 17


class ProjectTab(Tab):  # pylint: disable=too-many-instance-attributes  # editor state
    """Machine summary and editor, analysis, snapshot."""

    title = "Project"
    message = "project_message"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create the tab (the widgets come with `build`)."""
        super().__init__(*args, **kwargs)
        self.rom_slots: list[str] = []  # the slots the ROM rows show, in row order
        self.ram_count = 0  # RAM target rows shown
        self.load_count = 0  # rows of files to load shown
        self.baseline: dict[str, Any] | None = None  # loaded, saved or applied last
        self.config_file: str | None = None  # where Save writes
        self.model_chips: Chips | None = None
        self._editing = False

    # -- building --------------------------------------------------------------------------------

    def build(self) -> None:
        """Create the machine box, the analysis box and the snapshot row."""
        with section(self.styles, width=TAB_WIDTH):
            self._build_summary()
            with dpg.group(show=False) as editor:
                self.items["machine_editor"] = editor
                self._build_editor()
        with section(self.styles, "Analysis", width=TAB_WIDTH):
            with dpg.group(horizontal=True):
                self.button("Save analysis", self.session.analysis_save)
                self.button("Generate + verify source", self.generate_source)
            self.text("files_result", wrap=PANEL_WIDTH - 20)
        with section(self.styles, "Snapshot", width=TAB_WIDTH):
            session = self.session
            with self.path_field("sna_path", 300, "output", SNAPSHOT_EXTENSIONS,
                                 default_value="output/state.sna"):  # fmt: skip
                self.button("Save", lambda: session.sna_save(self.value("sna_path")))
                self.button("Load", lambda: session.sna_load(self.value("sna_path")))
        self.text("project_message", color=colours.TEXT_DIM, wrap=PANEL_WIDTH)

    def _build_summary(self) -> None:
        """Title, config file, "changed", Edit; ROM tags; drives and RAM code to analyse."""
        # a table, so Edit / Close sits at the right edge of the box (owner, 2026-10-23)
        with dpg.table(header_row=False, policy=dpg.mvTable_SizingStretchProp):
            dpg.add_table_column(width_stretch=True)
            dpg.add_table_column(width_fixed=True, init_width_or_weight=EDIT_WIDTH)
            with dpg.table_row():
                with dpg.group(horizontal=True):
                    self.text("machine_title", color=colours.HEADING)
                    self.text("machine_file", color=colours.TEXT_DIM)
                    self.items["machine_changed"] = badge(self.styles, "changed", "changed",
                                                          show=False)  # fmt: skip
                    self.items["edit_apply"] = self.button("Apply (resets the machine)",
                                                           self.apply, show=False)  # fmt: skip
                self.items["machine_edit"] = self.button("Edit", self.toggle_editor,
                                                         width=EDIT_WIDTH)  # fmt: skip
        with dpg.group(horizontal=True):
            for index in range(MAX_TAGS):
                self.items[f"machine_tag_{index}"] = badge(self.styles, "", show=False)
        self.text("machine_drives", color=colours.TEXT_DIM)
        for row in range(LOAD_ROWS):
            with dpg.group(horizontal=True, show=False) as line:
                self.items[f"loaded_line_{row}"] = line
                self.text(f"loaded_led_{row}")
                self.text(f"loaded_text_{row}")
                self.button("Load", lambda chosen=row: self.load_file(chosen))
        self.text("loaded_result", color=colours.TEXT_DIM)
        self.text("machine_skipped", color=colours.CHANGED)

    def _build_editor(self) -> None:
        """The machine editor (see the module docstring)."""
        separator(self.styles)
        with self.path_field("config_path", 250, "config", CONFIG_EXTENSIONS):
            self.button("Load", self.load_config)
            self.items["config_save"] = self.button("Save", self.save_config)
            self.items["config_save_as"] = self.button("Save as…", self.save_config_as)
        with dpg.group(horizontal=True):
            dpg.add_text("model", color=colours.TEXT_DIM)
            self.model_chips = Chips(self.styles, list(MODEL_CHIPS))
            dpg.add_text("  empty ROM numbers answer as slot", color=colours.TEXT_DIM)
            self.items["edit_unpopulated"] = dpg.add_combo(SLOTS[1:], default_value="0",
                                                           width=50)  # fmt: skip
        separator(self.styles)  # between the parts (owner, 2026-10-23)
        dpg.add_text("ROMs", color=colours.HEADING)
        with _editor_table(ROM_COLUMNS):
            for row in range(len(SLOTS)):
                self._build_rom_row(row)
        with dpg.group(horizontal=True):
            self.button("+ ROM in slot", self.add_rom)
            self.items["edit_new_slot"] = dpg.add_combo(SLOTS, default_value="1", width=70)
        separator(self.styles)
        dpg.add_text("Drives", color=colours.HEADING)
        with _editor_table(DRIVE_COLUMNS):
            for letter in DRIVE_LETTERS:
                with dpg.table_row():
                    dpg.add_text(f"{letter}:", color=colours.TEXT_DIM)
                    with self.path_field(f"edit_drive_{letter}", 305, "disk", DISK_EXTENSIONS,
                                         hint="no disk"):  # fmt: skip
                        pass
                    self.items[f"edit_writable_{letter}"] = dpg.add_checkbox()
        separator(self.styles)
        dpg.add_text("RAM code to analyse", color=colours.HEADING)
        with _editor_table(RAM_COLUMNS):
            for row in range(RAM_ROWS):
                self._build_ram_row(row)
        self.items["edit_add_ram"] = self.button("+ RAM range", self.add_ram_target)
        separator(self.styles)
        dpg.add_text("Binary files to load into RAM", color=colours.HEADING)
        dpg.add_text("loaded only with Load - Apply resets the machine, and the boot clears RAM",
                     color=colours.TEXT_DIM)  # fmt: skip
        with _editor_table(LOAD_COLUMNS):
            for row in range(LOAD_ROWS):
                self._build_load_row(row)
        self.items["edit_add_load"] = self.button("+ file", self.add_load)

    def _build_rom_row(self, row: int) -> None:
        """One ROM row of the pool (a table row).

        Slot, file ▾, decoder, analyse, what the ROM header says, ✕.
        """
        with dpg.table_row(show=False) as table_row:
            self.items[f"rom_row_{row}"] = table_row
            self.text(f"rom_slot_{row}", color=colours.TEXT_DIM)
            with self.path_field(f"rom_file_{row}", 165, "roms", ROM_EXTENSIONS):
                pass
            self.items[f"rom_decoder_{row}"] = dpg.add_combo(
                DECODER_CHOICES, default_value="none", width=60
            )
            self.items[f"rom_analyse_{row}"] = dpg.add_checkbox()
            self.text(f"rom_header_{row}", color=colours.TEXT_DIM)
            remove_button(self.styles, lambda: self.call(lambda: self.remove_rom(row)))

    def _build_ram_row(self, row: int) -> None:
        """One RAM range row of the pool (a table row): name, from, length, copy taken, ✕."""
        with dpg.table_row(show=False) as table_row:
            self.items[f"ram_row_{row}"] = table_row
            self.input(f"ram_name_{row}", "name", 125)
            self.input(f"ram_start_{row}", "hex", 65)
            self.input(f"ram_length_{row}", "hex", 65)
            self.items[f"ram_capture_{row}"] = dpg.add_combo(
                list(CAPTURE_CHOICES), default_value="when it first runs", width=155
            )
            remove_button(self.styles, lambda: self.call(lambda: self.remove_ram_target(row)))

    def _build_load_row(self, row: int) -> None:
        """One row of the files to load (a table row): file ▾, address, ●/○, Load, ✕.

        Load loads the row's file at once - no Apply needed (owner, 2026-10-23).
        """
        with dpg.table_row(show=False) as table_row:
            self.items[f"load_row_{row}"] = table_row
            with self.path_field(f"load_file_{row}", 275, "output", (), hint="file on the PC"):
                pass
            self.input(f"load_address_{row}", "hex", 65)
            self.text(f"load_led_{row}")
            self.button("Load", lambda: self.load_row(row))
            remove_button(self.styles, lambda: self.call(lambda: self.remove_load(row)))

    # -- editor actions --------------------------------------------------------------------------

    def toggle_editor(self) -> None:
        """Edit opens the editor (filled from the running machine if nothing is edited yet)."""
        self._editing = not self._editing
        if self._editing and self.baseline is None:
            self._edit_running()
        dpg.configure_item(self.items["machine_editor"], show=self._editing)
        dpg.set_item_label(self.items["machine_edit"], "Close" if self._editing else "Edit")

    def _edit_running(self) -> None:
        """Fill the editor with the configuration of the running machine."""
        running = self.session.config_get()
        if running["config"] is not None:
            self.config_file = running["path"]
            dpg.set_value(self.items["config_path"], running["path"] or "")
            self._fill(running["config"])

    def load_config(self) -> Any:
        """Load the path field's machine.toml into the editor (Apply makes it the machine)."""
        result = self.session.config_read(self.value("config_path"))
        self.config_file = result["path"]
        self._fill(result["config"])
        return result

    def save_config(self) -> Any:
        """Write the editor's configuration to its file (the one loaded or saved last)."""
        if not self.config_file:
            raise ValueError("no config file yet: use Save as…")
        result = self.session.config_write(self._collect(strict=True), self.config_file)
        self.baseline = self._collect()
        return result

    def save_config_as(self) -> Any:
        """Write the editor's configuration to the path field's file; Save then writes there."""
        result = self.session.config_write(self._collect(strict=True), self.value("config_path"))
        self.config_file = result["path"]
        self.baseline = self._collect()
        return result

    def apply(self) -> Any:
        """Power on the machine of the editor's configuration (memory cleared)."""
        result = self.session.config_apply(self._collect(strict=True), self.config_file)
        self.baseline = self._collect()
        return result

    def add_rom(self) -> None:
        """Add a ROM row for the chosen slot."""
        slot = self.value("edit_new_slot")
        if slot in self.rom_slots:
            raise ValueError(f"slot {slot} is in the table already")
        self._show_rom_rows([*self.rom_slots, slot], keep=True)

    def remove_rom(self, row: int) -> None:
        """Remove a ROM row (the others move up)."""
        slots = self.rom_slots[:row] + self.rom_slots[row + 1 :]
        tables = [t for index, t in enumerate(self._rom_tables()) if index != row]
        self._show_rom_rows(slots, tables)

    def add_ram_target(self) -> None:
        """Add an empty RAM target row."""
        if self.ram_count == RAM_ROWS:
            raise ValueError(f"at most {RAM_ROWS} RAM ranges here")
        self._set_ram_row(self.ram_count, {"name": "", "start": "", "length": "",
                                            "capture": "first_m1"})  # fmt: skip
        self.ram_count += 1
        self._show_ram_rows()

    def add_load(self) -> None:
        """Add an empty row for a file to load."""
        if self.load_count == LOAD_ROWS:
            raise ValueError(f"at most {LOAD_ROWS} files here")
        self._set_load_row(self.load_count, {"file": "", "address": ""})
        self.load_count += 1
        self._show_load_rows()

    def remove_load(self, row: int) -> None:
        """Remove a file to load (the others move up)."""
        loads = [entry for index, entry in enumerate(self._loads()) if index != row]
        for index, entry in enumerate(loads):
            self._set_load_row(index, entry)
        self.load_count = len(loads)
        self._show_load_rows()

    def remove_ram_target(self, row: int) -> None:
        """Remove a RAM target row (the others move up)."""
        targets = [t for index, t in enumerate(self._ram_targets()) if index != row]
        for index, target in enumerate(targets):
            self._set_ram_row(index, target)
        self.ram_count = len(targets)
        self._show_ram_rows()

    # -- the editor's fields ---------------------------------------------------------------------

    def _fill(self, config: dict[str, Any]) -> None:
        """Put a configuration document into the editor's fields."""
        assert self.model_chips is not None
        machine = config.get("machine", {})
        self.model_chips.selected = [model_chip(machine.get("model", "464"))]
        self.model_chips.click(self.model_chips.selected[0])
        dpg.set_value(self.items["edit_unpopulated"], str(machine.get("unpopulated_rom", 0)))
        roms = roms_of(config)
        self._show_rom_rows([slot for slot, _ in roms], [table for _, table in roms])
        drives = config.get("drive", {})
        for letter in DRIVE_LETTERS:
            drive = drives.get(letter, {})
            dpg.set_value(self.items[f"edit_drive_{letter}"], drive.get("file", ""))
            dpg.set_value(self.items[f"edit_writable_{letter}"], bool(drive.get("writable")))
        targets = config.get("analysis", {}).get("ram", [])[:RAM_ROWS]
        for index, target in enumerate(targets):
            self._set_ram_row(index, target)
        self.ram_count = len(targets)
        self._show_ram_rows()
        loads = config.get("load", [])[:LOAD_ROWS]
        for index, entry in enumerate(loads):
            self._set_load_row(index, entry)
        self.load_count = len(loads)
        self._show_load_rows()
        self.baseline = self._collect()

    def _show_rom_rows(
        self, slots: list[str], tables: list[dict[str, Any]] | None = None, keep: bool = False
    ) -> None:
        """Show the ROM rows for `slots` (lower first, then by number) with their tables."""
        current = dict(zip(self.rom_slots, self._rom_tables(), strict=True)) if keep else {}
        if tables is not None:
            current |= dict(zip(slots, tables, strict=True))
        self.rom_slots = sorted(slots, key=SLOTS.index)
        for row in range(len(SLOTS)):
            shown = row < len(self.rom_slots)
            dpg.configure_item(self.items[f"rom_row_{row}"], show=shown)
            if not shown:
                continue
            slot = self.rom_slots[row]
            table = current.get(slot, {"file": ""})
            dpg.set_value(self.items[f"rom_slot_{row}"], f"{slot:>5}")
            dpg.set_value(self.items[f"rom_file_{row}"], table.get("file", ""))
            dpg.set_value(self.items[f"rom_decoder_{row}"], table.get("decoder") or "none")
            dpg.set_value(self.items[f"rom_analyse_{row}"], bool(table.get("analyse")))
        free = free_slots(self.rom_slots)
        dpg.configure_item(self.items["edit_new_slot"], items=free)
        dpg.set_value(self.items["edit_new_slot"], free[0] if free else "")

    def _rom_tables(self) -> list[dict[str, Any]]:
        """The ROM rows' tables, in row order."""
        return [rom_table(self.value(f"rom_file_{row}"), self.value(f"rom_decoder_{row}"),
                          bool(self.value(f"rom_analyse_{row}")))
                for row in range(len(self.rom_slots))]  # fmt: skip

    def _set_ram_row(self, row: int, target: dict[str, Any]) -> None:
        """Put a RAM target into a row (numbers as hex)."""
        dpg.set_value(self.items[f"ram_name_{row}"], target["name"])
        for key in ("start", "length"):
            value = target[key]
            dpg.set_value(self.items[f"ram_{key}_{row}"],
                          f"{value:04X}" if isinstance(value, int) else value)  # fmt: skip
        capture = target.get("capture", "first_m1")
        shown = next(label for label, value in CAPTURE_CHOICES.items() if value == capture)
        dpg.set_value(self.items[f"ram_capture_{row}"], shown)

    def _show_ram_rows(self) -> None:
        """Show as many RAM target rows as there are targets."""
        for row in range(RAM_ROWS):
            dpg.configure_item(self.items[f"ram_row_{row}"], show=row < self.ram_count)
        dpg.configure_item(self.items["edit_add_ram"], enabled=self.ram_count < RAM_ROWS)

    def _ram_targets(self) -> list[dict[str, Any]]:
        """The RAM target rows (start and length as numbers, or the text if not hex)."""
        return [{"name": self.value(f"ram_name_{row}").strip(),
                 "start": number(self.value(f"ram_start_{row}")),
                 "length": number(self.value(f"ram_length_{row}")),
                 "capture": CAPTURE_CHOICES[self.value(f"ram_capture_{row}")]}
                for row in range(self.ram_count)]  # fmt: skip

    def _set_load_row(self, row: int, entry: dict[str, Any]) -> None:
        """Put a file to load into a row (the address as hex)."""
        address = entry["address"]
        dpg.set_value(self.items[f"load_file_{row}"], entry["file"])
        dpg.set_value(self.items[f"load_address_{row}"],
                      f"{address:04X}" if isinstance(address, int) else address)  # fmt: skip

    def _show_load_rows(self) -> None:
        """Show as many rows as there are files to load."""
        for row in range(LOAD_ROWS):
            dpg.configure_item(self.items[f"load_row_{row}"], show=row < self.load_count)
        dpg.configure_item(self.items["edit_add_load"], enabled=self.load_count < LOAD_ROWS)

    def _loads(self) -> list[dict[str, Any]]:
        """The files to load (the address as a number, or the text if not hex)."""
        return [{"file": self.value(f"load_file_{row}").strip(),
                 "address": number(self.value(f"load_address_{row}"))}
                for row in range(self.load_count)]  # fmt: skip

    def _collect(self, strict: bool = False) -> dict[str, Any]:
        """The configuration document of the editor's fields.

        Args:
            strict: Raise ValueError for a field that cannot be written (Save, Apply).
        """
        assert self.model_chips is not None
        drives = {}
        for letter in DRIVE_LETTERS:
            file = self.value(f"edit_drive_{letter}").strip()
            if file:
                writable = bool(self.value(f"edit_writable_{letter}"))
                drives[letter] = {"file": file, "writable": writable}
        targets = self._ram_targets()
        loads = self._loads()
        if strict:
            for entry in loads:
                if not entry["file"] or isinstance(entry["address"], str):
                    raise ValueError("a file to load needs a file and a hex address")
            for target in targets:
                if isinstance(target["start"], str) or isinstance(target["length"], str):
                    raise ValueError(f"RAM target {target['name']!r}: start and length in hex")
            for slot, table in zip(self.rom_slots, self._rom_tables(), strict=True):
                if not table["file"]:
                    raise ValueError(f"ROM slot {slot}: no file")
        return document(MODEL_CHIPS[self.model_chips.selected[0]],
                        int(self.value("edit_unpopulated")),
                        list(zip(self.rom_slots, self._rom_tables(), strict=True)), drives,
                        targets, loads)  # fmt: skip

    def load_row(self, row: int) -> Any:
        """Load an editor row's file into RAM now (no Apply needed)."""
        address = self.value(f"load_address_{row}").strip()
        if not address:
            raise ValueError("give the address to load the file at (hex)")
        result = self.session.memory_load(address, self.value(f"load_file_{row}").strip())
        first, last = result["address"], result["last"]
        dpg.set_value(
            self.items["loaded_result"], f"{result['length']} bytes loaded at {first}-{last}"
        )
        return result

    def load_file(self, row: int) -> Any:
        """Load one of the configuration's binary files into RAM now (the summary's Load)."""
        entry = self.session.loads_get()["loads"][row]
        result = self.session.memory_load(entry["address"], entry["file"])
        first, last = result["address"], result["last"]
        dpg.set_value(
            self.items["loaded_result"], f"{result['length']} bytes loaded at {first}-{last}"
        )
        return result

    def _refresh_loads(self) -> None:
        """The summary's binary files: light green once loaded (until a reset), red not yet."""
        loads = self.session.loads_get()["loads"]
        for row in range(LOAD_ROWS):
            shown = row < len(loads)
            dpg.configure_item(self.items[f"loaded_line_{row}"], show=shown)
            if not shown:
                continue
            entry = loads[row]
            led = self.items[f"loaded_led_{row}"]
            dpg.set_value(led, "\u25cf")
            dpg.configure_item(led, color=colours.LED_ON if entry["loaded"] else colours.ERROR)
            dpg.set_value(self.items[f"loaded_text_{row}"], load_text(entry))

    # -- analysis --------------------------------------------------------------------------------

    def generate_source(self) -> Any:
        """Generate the source of the only analysis target and verify it (D-33)."""
        generated = self.session.source_generate()
        verified = self.session.source_verify()
        text = (f"{generated['path']}: {generated['lines']} lines; "
                f"{'identical' if verified['identical'] else 'DIFFERENT'} "
                f"({verified['difference_count']} differing bytes)")  # fmt: skip
        dpg.set_value(self.items["files_result"], text)
        return verified

    # -- refreshing ------------------------------------------------------------------------------

    def refresh(self) -> None:
        """The summary of the running machine; the editor's headers and "changed"."""
        running = self.session.config_get()
        config = running["config"]
        if config is None:
            dpg.set_value(self.items["machine_title"], "bare Z80 (no CPC configuration)")
            return
        dpg.set_value(self.items["machine_title"], machine_title(config["machine"]["model"]))
        dpg.set_value(self.items["machine_file"], running["path"] or "(not saved to a file)")
        tags = [rom_tag(slot, table) for slot, table in roms_of(config)]
        for index in range(MAX_TAGS):
            item = self.items[f"machine_tag_{index}"]
            shown = index < len(tags)
            dpg.configure_item(item, show=shown)
            if shown:
                analysed = "analysed" in tags[index] or "vortex" in tags[index]
                set_badge(self.styles, item, tags[index], "here" if analysed else "key")
        dpg.set_value(self.items["machine_drives"], drives_line(config))
        self._refresh_loads()
        skipped = running["skipped"]
        dpg.set_value(self.items["machine_skipped"],
                      "skipped: " + "; ".join(skipped) if skipped else "")  # fmt: skip
        changed = self.baseline is not None and self._collect() != self.baseline
        dpg.configure_item(self.items["machine_changed"], show=changed)
        for name in ("config_save", "config_save_as"):  # every unsaved change lights them
            dpg.bind_item_theme(self.items[name], self.styles.primary_button if changed
                                else self.styles.plain_button)  # fmt: skip
        self._refresh_apply(config)
        if self._editing:
            for row in range(len(self.rom_slots)):
                self._refresh_rom_row(row)
            for row in range(self.load_count):
                self._refresh_load_row(row)

    def _refresh_apply(self, running: dict[str, Any]) -> None:
        """Apply only while the editor differs from the running machine in what makes it.

        The binary files to load are left out: they change no machine, they are loaded with
        their own Load buttons (owner, 2026-10-23).
        """
        if self.baseline is None:
            return
        wanted = {key: value for key, value in self._collect().items() if key != "load"}
        current = {key: value for key, value in running.items() if key != "load"}
        machine_changed = wanted != current
        item = self.items["edit_apply"]
        dpg.configure_item(item, enabled=machine_changed, show=True)
        dpg.bind_item_theme(item, self.styles.primary_button if machine_changed
                            else self.styles.disabled_button)  # fmt: skip

    def _refresh_load_row(self, row: int) -> None:
        """An editor row's light: green once loaded (until a reset), red until then."""
        file = self.value(f"load_file_{row}").strip()
        address = self.value(f"load_address_{row}").strip()
        loaded = False
        if file and isinstance(number(address), int):
            loaded = self.session.memory_file_check(number(address), file)["loaded"]
        led = self.items[f"load_led_{row}"]
        dpg.set_value(led, "\u25cf")
        dpg.configure_item(led, color=colours.LED_ON if loaded else colours.ERROR)

    def _refresh_rom_row(self, row: int) -> None:
        """A ROM row's header text (a scrambled ROM's header read decoded)."""
        decoder = self.value(f"rom_decoder_{row}")
        file = self.value(f"rom_file_{row}").strip()
        text = ""
        if file:
            header = self.session.rom_header(file, None if decoder == "none" else decoder)
            if self.rom_slots[row] == "lower":
                header = (
                    {"type": "OS", "version": "", "name": ""} if "error" not in header else header
                )
            text = header_text(header)
        dpg.set_value(self.items[f"rom_header_{row}"], text)


@contextmanager
def _editor_table(columns: tuple[tuple[str, int], ...]) -> Iterator[int | str]:
    """A table of the machine editor with a heading per column; the block adds its rows.

    The headings say what each field of a row is (owner, 2026-10-23: readable for others too).
    """
    with dpg.table(header_row=True, policy=dpg.mvTable_SizingFixedFit) as table:
        for heading, width in columns:
            dpg.add_table_column(label=heading, width_fixed=True, init_width_or_weight=width)
        yield table
