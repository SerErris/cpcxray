"""Memory tab: hex dump through a view, editing, paging, known variables (FR-DBG-06, -11, -12).

What the view shows per 16 KB quarter heads the dump; the bytes of known OS/AMSDOS variables
are coloured, one colour per variable (colours.VARIABLE_COLOURS in turn), area-only bytes grey,
and the legend below names them in the same colours.
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.session.errors import SessionError
from cpcxray.ui.panels.base import PANEL_WIDTH, Tab
from cpcxray.ui.panels.colours import AREA_COLOUR, THEME_DEFAULT, VARIABLE_COLOURS
from cpcxray.ui.panels.text import AREA, BYTES_PER_ROW, memory_layout, parse_memory_edit

MEMORY_BYTES = 256
MEMORY_ROWS = MEMORY_BYTES // BYTES_PER_ROW


class MemoryTab(Tab):
    """View selection, editing, paging, hex dump (variables coloured) and its legend."""

    title = "Memory"
    message = "memory_message"

    def build(self) -> None:
        """Create the widgets: one text per byte, so each byte can have its colour."""
        with dpg.group(horizontal=True):
            self.input("memory_address", "address", 100, default_value="0000")
            self.items["memory_view"] = dpg.add_combo(
                self.memory_views(), default_value="cpu", width=120
            )
            self.entry("memory_edit", "4000: 3E 05 C9  (RAM)", 220, self.edit_memory)
        self.text("memory_message")
        self.text("memory_paging")
        for row in range(MEMORY_ROWS):
            with dpg.group(horizontal=True, horizontal_spacing=0):
                self.text(f"memory_address_{row}")
                for column in range(BYTES_PER_ROW):
                    self.text(f"memory_byte_{row}_{column}")
                self.text(f"memory_ascii_{row}")
        self.items["memory_legend"] = dpg.add_group()

    def edit_memory(self) -> Any:
        """Write "address: bytes" into RAM."""
        address, data = parse_memory_edit(self.value("memory_edit"))
        return self.session.memory_write(address, data)

    def memory_views(self) -> list[str]:
        """Views offered for the installed ROMs."""
        views = ["cpu", "ram"]
        memory = getattr(self.session.machine, "memory", None)
        if memory is None:
            return views
        if memory.lower_rom is not None:
            views.append("lower")
        for slot, rom in sorted(memory.upper_roms.items()):
            views.append(f"rom{slot}")
            if rom.shadow is not None:
                views += [f"raw:rom{slot}", f"shadow:rom{slot}"]
        return views

    def refresh(self) -> None:
        """The hex dump of the selected view, with its paging and known variables."""
        try:
            dump = self.session.memory_view(self.value("memory_address") or "0",
                                            MEMORY_BYTES, self.value("memory_view"))  # fmt: skip
        except SessionError as error:
            dump = None
            self.set_lines("memory_paging", [f"error: {error}"])
        rows, legend = memory_layout(dump, len(VARIABLE_COLOURS)) if dump else ([], [])
        if dump is not None:
            paging = [f"{q['start']}-{q['end']}  {q['text']}" for q in dump["paging"]]
            self.set_lines("memory_paging", paging)
        for index in range(MEMORY_ROWS):
            row = rows[index] if index < len(rows) else None
            dpg.set_value(self.items[f"memory_address_{index}"], f"{row.address} " if row else "")
            for column in range(BYTES_PER_ROW):
                text, colour = row.cells[column] if row and column < len(row.cells) else ("", None)
                item = self.items[f"memory_byte_{index}_{column}"]
                dpg.set_value(item, f" {text}" if text else "")
                dpg.configure_item(item, color=_colour(colour))
            dpg.set_value(self.items[f"memory_ascii_{index}"], f"  {row.ascii}" if row else "")
        dpg.delete_item(self.items["memory_legend"], children_only=True)
        for text, colour in legend:
            dpg.add_text(text, parent=self.items["memory_legend"], color=_colour(colour),
                         wrap=PANEL_WIDTH)  # fmt: skip


def _colour(colour: int | None) -> tuple[int, ...]:
    """The text colour of a memory cell or legend line (see `memory_layout`)."""
    if colour is None:
        return THEME_DEFAULT
    return AREA_COLOUR if colour == AREA else VARIABLE_COLOURS[colour]
