"""Breakpoints tab: last stop, the breakpoint table, a form for new ones (task 5d, FR-DBG-07).

Layout agreed with the owner on 2026-10-22 (design canvas, artboard "Breakpoints 3: combined"),
top to bottom:

* Last stop (framed yellow): the type chip and what triggered ("before the instruction at BB5A
  TXT_OUTPUT · RAM block 2", or for a memory breakpoint access, address = value, variable and
  the instruction's PC), the instruction itself, breakpoint number and hit count
  (`Session.last_stop_get`: also stops of runs started over MCP).
* Breakpoints · n: on/off, #, type chip, where (address or range, port, ROM; the label or
  variable name in colour), condition (location, mask), hits, ✕; disabled rows dimmed, the
  breakpoint of the last stop on a yellow bar. Clicking an address shows it in the CPU tab
  (pc, m1) or the Memory tab (read, write, access). Enable all / Disable all / Remove all.
* New breakpoint: the ten types as chips coloured by category (code yellow, memory green, I/O
  blue, system purple), a line explaining the chosen type, only the fields it needs, Add. A
  variable name with an empty end shows the range it will cover.
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.session.errors import SessionError
from cpcxray.ui.panels import colours
from cpcxray.ui.panels.base import TAB_WIDTH, Tab
from cpcxray.ui.panels.colours import BREAKPOINT_CATEGORIES, BREAKPOINT_TYPE_CATEGORY
from cpcxray.ui.panels.text import (
    breakpoint_condition,
    breakpoint_where,
    stop_texts,
    variable_range_hint,
)
from cpcxray.ui.panels.widgets import Chips, badge, remove_button, section

BREAKPOINT_TYPES = tuple(BREAKPOINT_TYPE_CATEGORY)
LOCATIONS = ("any", "rom", "ram", "lower", *(f"rom:{n}" for n in range(16)),
             *(f"ram:{n}" for n in range(8)))  # RAM blocks; 4-7 only on a 6128  # fmt: skip
EXPLANATIONS = {
    "pc": "stops before the instruction at the address (or in the range) runs",
    "m1": "stops after an instruction whose opcode is fetched (M1) from the address or range",
    "read": "stops after an instruction that reads the address or range (data, not opcodes)",
    "write": "stops after an instruction that writes the address or range (writes go to RAM)",
    "access": "stops after an instruction that reads or writes the address or range",
    "io_in": "stops after an IN from the port (the bits under the mask must match)",
    "io_out": "stops after an OUT to the port (the bits under the mask must match)",
    "io": "stops after an IN or OUT on the port (the bits under the mask must match)",
    "rom_select": "stops after an OUT that selects an upper ROM (ROM empty: any)",
    "interrupt": "stops when the CPU accepts an interrupt",
}
ADDRESS_TYPES = ("pc", "m1", "read", "write", "access")
PORT_TYPES = ("io_in", "io_out", "io")
VARIABLE_TYPES = ("read", "write", "access")
MEMORY_TAB_TYPES = ("read", "write", "access")  # their address opens in the Memory tab
NAME_WIDTH = 150


def _chip_colours(kind: str) -> tuple[colours.Colour, colours.Colour]:
    """(text and frame, background) of a breakpoint type's category."""
    return BREAKPOINT_CATEGORIES[BREAKPOINT_TYPE_CATEGORY[kind]]


class BreakpointsTab(Tab):
    """Last stop, breakpoint table and the form for new breakpoints."""

    title = "Breakpoints"
    message = "bp_message"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create the tab; the table is rebuilt only when the breakpoints change."""
        super().__init__(*args, **kwargs)
        self._shown: tuple[Any, ...] | None = None  # what the table shows now
        self.types: Chips | None = None

    def build(self) -> None:
        """Create the three boxes."""
        self._build_last_stop()
        self._build_table()
        self._build_form()
        self.text("bp_message", color=colours.TEXT_DIM)

    def _build_last_stop(self) -> None:
        """The yellow-framed box of the last stop."""
        with section(self.styles, width=TAB_WIDTH, style="here"):
            with dpg.group(horizontal=True):
                dpg.add_text("Last stop", color=colours.HEADING)
                self.text("stop_title", color=colours.TEXT_DIM)
            with dpg.group(horizontal=True):
                self.items["stop_kind"] = badge(self.styles, "", "plain", show=False)
                self.text("stop_headline")
            self.text("stop_instruction", color=colours.TEXT_DIM)

    def _build_table(self) -> None:
        """The breakpoint table (rows come with `refresh`) and the buttons for all."""
        session = self.session
        with section(self.styles, width=TAB_WIDTH):
            with dpg.group(horizontal=True):
                self.text("bp_count", color=colours.HEADING)
                dpg.add_text("click an address: show it in CPU / Memory", color=colours.TEXT_DIM)
            with dpg.table(header_row=True, policy=dpg.mvTable_SizingFixedFit,
                           borders_innerH=False, row_background=False) as table:  # fmt: skip
                self.items["bp_table"] = table
                for label in ("on", "no.", "type", "where", "condition", "hits", ""):
                    dpg.add_table_column(label=label)
            with dpg.group(horizontal=True):
                self.button("Enable all", lambda: self._for_all(
                    lambda b: session.breakpoint_enable(b, True)))  # fmt: skip
                self.button("Disable all", lambda: self._for_all(
                    lambda b: session.breakpoint_enable(b, False)))  # fmt: skip
                self.button("Remove all", lambda: self._for_all(session.breakpoint_remove))

    def _build_form(self) -> None:
        """New breakpoint: type chips, explanation, the fields of the type, Add."""
        with section(self.styles, "New breakpoint", width=TAB_WIDTH):
            self.types = Chips(
                self.styles,
                BREAKPOINT_TYPES,
                ["pc"],
                on_change=lambda _selected: self._type_changed(),
                chip_colours={kind: _chip_colours(kind) for kind in BREAKPOINT_TYPES},
            )
            self.text("bp_explanation", color=colours.TEXT_DIM)
            with dpg.group(horizontal=True):
                with dpg.group(horizontal=True) as address_fields:
                    self.items["bp_address_fields"] = address_fields
                    dpg.add_text("address", color=colours.TEXT_DIM)
                    self.input("bp_address", "BB5A or a name", 130, callback=self._update_hint)
                    dpg.add_text("end", color=colours.TEXT_DIM)
                    self.input("bp_end", "(range)", 70, callback=self._update_hint)
                    dpg.add_text("location", color=colours.TEXT_DIM)
                    self.items["bp_location"] = dpg.add_combo(LOCATIONS, default_value="any",
                                                              width=80)  # fmt: skip
                with dpg.group(horizontal=True) as port_fields:
                    self.items["bp_port_fields"] = port_fields
                    dpg.add_text("port", color=colours.TEXT_DIM)
                    self.input("bp_port", "FA7E", 70)
                    dpg.add_text("mask", color=colours.TEXT_DIM)
                    self.input("bp_mask", "FFFF", 60)
                with dpg.group(horizontal=True) as rom_fields:
                    self.items["bp_rom_fields"] = rom_fields
                    dpg.add_text("ROM", color=colours.TEXT_DIM)
                    self.input("bp_rom", "any", 50)
                self.items["bp_add"] = self.button("Add", self.add_breakpoint)
                dpg.bind_item_theme(self.items["bp_add"], self.styles.primary_button)
            self.text("bp_hint", color=colours.TEXT_DIM)
        self._type_changed()

    # -- actions ---------------------------------------------------------------------------------

    @property
    def kind(self) -> str:
        """The breakpoint type chosen in the form."""
        assert self.types is not None
        return self.types.selected[0]

    def add_breakpoint(self) -> Any:
        """Add a breakpoint from the form (only the fields of the chosen type count)."""
        kind = self.kind
        if kind in PORT_TYPES:
            mask = self.value("bp_mask").strip()
            return self.session.breakpoint_add(
                self.value("bp_port").strip() or None,
                kind=kind,
                port_mask=int(mask, 16) if mask else 0xFFFF,
            )
        if kind == "rom_select":
            rom = self.value("bp_rom").strip()
            return self.session.breakpoint_add(kind=kind, rom=int(rom) if rom else None)
        if kind == "interrupt":
            return self.session.breakpoint_add(kind=kind)
        return self.session.breakpoint_add(
            self.value("bp_address").strip() or None, kind=kind,
            end=self.value("bp_end").strip() or None, location=self.value("bp_location"),
        )  # fmt: skip

    def _for_all(self, action: Any) -> dict[str, Any]:
        """Apply an action to every breakpoint id."""
        ids = [entry["id"] for entry in self.session.breakpoint_list()["breakpoints"]]
        for breakpoint_id in ids:
            action(breakpoint_id)
        return {"breakpoints": len(ids)}

    def _type_changed(self) -> None:
        """Show the explanation and the fields of the chosen type."""
        kind = self.kind
        dpg.set_value(self.items["bp_explanation"], f"{kind}: {EXPLANATIONS[kind]}")
        dpg.configure_item(self.items["bp_address_fields"], show=kind in ADDRESS_TYPES)
        dpg.configure_item(self.items["bp_port_fields"], show=kind in PORT_TYPES)
        dpg.configure_item(self.items["bp_rom_fields"], show=kind == "rom_select")
        self._update_hint()

    def _update_hint(self) -> None:
        """Name the range a variable breakpoint without an end will cover."""
        hint = ""
        name = self.value("bp_address").strip()
        if self.kind in VARIABLE_TYPES and name and not self.value("bp_end").strip():
            try:
                hint = variable_range_hint(name, self.session.variable_list(name)["variables"])
            except SessionError:
                hint = ""
        dpg.set_value(self.items["bp_hint"], hint)

    def _show(self, entry: dict[str, Any]) -> None:
        """Show a breakpoint's address in the CPU tab (code) or the Memory tab (data)."""
        if entry["kind"] in MEMORY_TAB_TYPES:
            dpg.set_value(self.items["memory_address"], entry["address"])
            self.context.show_tab("Memory")
        else:
            dpg.set_value(self.items["disassembly_address"], entry["address"])
            self.context.show_tab("CPU")

    # -- refreshing ------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Last stop and the table (rebuilt only when something in it changed)."""
        last = self.session.last_stop_get()
        self._refresh_last_stop(last)
        entries = self.session.breakpoint_list()["breakpoints"]
        hit = (last.get("stop") or {}).get("breakpoint") or {}
        shown = (tuple(tuple(sorted(e.items())) for e in entries), hit.get("id"))
        if shown != self._shown:
            self._shown = shown
            self._rebuild_rows(entries, hit.get("id"))

    def _refresh_last_stop(self, last: dict[str, Any]) -> None:
        """Fill the "Last stop" box."""
        texts = stop_texts(last)
        dpg.set_value(self.items["stop_title"], texts["title"])
        dpg.set_value(self.items["stop_headline"], texts["headline"])
        self.set_lines("stop_instruction", texts["instruction"])
        chip = self.items["stop_kind"]
        if texts["kind"] is None:
            dpg.hide_item(chip)
        else:
            dpg.set_item_label(chip, texts["kind"])
            dpg.bind_item_theme(chip, self.styles.chip(_chip_colours(texts["kind"]), False))
            dpg.show_item(chip)

    def _rebuild_rows(self, entries: list[dict[str, Any]], stopped_by: int | None) -> None:
        """One table row per breakpoint (see the module docstring)."""
        table = self.items["bp_table"]
        dpg.delete_item(table, children_only=True, slot=1)  # the rows; the columns stay
        dpg.set_value(self.items["bp_count"], f"Breakpoints · {len(entries)}")
        for index, entry in enumerate(entries):
            with dpg.table_row(parent=table):
                self._row(entry)
            if entry["id"] == stopped_by:
                dpg.highlight_table_row(table, index, (*colours.HERE_BACKGROUND, 255))

    def _row(self, entry: dict[str, Any]) -> None:
        """The cells of one breakpoint row."""
        session, styles = self.session, self.styles
        identifier, enabled = entry["id"], entry["enabled"]
        text_colour = colours.TEXT if enabled else colours.TEXT_OFF
        dpg.add_checkbox(default_value=enabled, callback=lambda _s, value: self.call(
            lambda: session.breakpoint_enable(identifier, value)))  # fmt: skip
        dpg.add_text(str(identifier), color=text_colour)
        chip = dpg.add_button(label=entry["kind"])
        dpg.bind_item_theme(chip, styles.chip(_chip_colours(entry["kind"]), False))
        where, name = breakpoint_where(entry)
        with dpg.group(horizontal=True):
            if "address" in entry:
                link = dpg.add_button(label=where, callback=lambda: self._show(entry))
                dpg.bind_item_theme(link, styles.link)
            else:
                dpg.add_text(where, color=text_colour)
            if name:
                colour = colours.SYMBOL_TYPE_COLOURS["VAR" if entry.get("variable") == name
                                                     else "FW"][0]  # fmt: skip
                dpg.add_text(name, color=colour if enabled else colours.TEXT_OFF)
        dpg.add_text(breakpoint_condition(entry), color=colours.TEXT_DIM)
        dpg.add_text(str(entry["hits"]), color=text_colour)
        remove_button(styles, lambda: self.call(lambda: session.breakpoint_remove(identifier)))
