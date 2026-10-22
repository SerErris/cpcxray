"""Breakpoints tab: add any type with a location qualifier; enable, disable, remove (FR-DBG-07)."""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels.base import Tab
from cpcxray.ui.panels.text import breakpoint_lines

BREAKPOINT_TYPES = ("pc", "m1", "read", "write", "access", "io_in", "io_out", "io",
                    "rom_select", "interrupt")  # fmt: skip
LOCATIONS = ("any", "rom", "ram", "lower", *(f"rom:{n}" for n in range(16)),
             *(f"ram:{n}" for n in range(8)))  # RAM blocks; 4-7 only on a 6128  # fmt: skip


class BreakpointsTab(Tab):
    """Breakpoint form and list."""

    title = "Breakpoints"
    message = "bp_message"

    def build(self) -> None:
        """Create the widgets."""
        session = self.session
        with dpg.group(horizontal=True):
            self.items["bp_kind"] = dpg.add_combo(BREAKPOINT_TYPES, default_value="pc", width=100)
            self.input("bp_address", "address/port", 100)
            self.input("bp_end", "end (range)", 90)
            self.items["bp_location"] = dpg.add_combo(LOCATIONS, default_value="any", width=80)
        with dpg.group(horizontal=True):
            self.input("bp_mask", "port mask (FF00)", 120)
            self.input("bp_rom", "ROM number", 90)
            self.button("Add", self.add_breakpoint)
            self.input("bp_id", "id", 50)
            self.button("Enable", lambda: session.breakpoint_enable(self.breakpoint_id(), True))
            self.button("Disable", lambda: session.breakpoint_enable(self.breakpoint_id(), False))
            self.button("Remove", lambda: session.breakpoint_remove(self.breakpoint_id()))
        self.text("bp_message")
        self.text("breakpoints")

    def breakpoint_id(self) -> int:
        """The breakpoint id typed into the id box."""
        return int(self.value("bp_id"))

    def add_breakpoint(self) -> Any:
        """Add a breakpoint from the form."""
        mask_text, rom_text = self.value("bp_mask").strip(), self.value("bp_rom").strip()
        return self.session.breakpoint_add(
            self.value("bp_address").strip() or None,
            kind=self.value("bp_kind"),
            end=self.value("bp_end").strip() or None,
            location=self.value("bp_location"),
            port_mask=int(mask_text, 16) if mask_text else 0xFFFF,
            rom=int(rom_text) if rom_text else None,
        )

    def refresh(self) -> None:
        """Breakpoint list."""
        self.set_lines(
            "breakpoints", breakpoint_lines(self.session.breakpoint_list()["breakpoints"])
        )
