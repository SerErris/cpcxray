"""Symbols tab: search the symbols, add "address label ; comment" (FR-DBG-09, D-26).

The matches scroll in a list (keys over the list); firmware names show in the FW colour, the
owner's in the normal text colour (the six symbol types come with task 5g). Selecting a row puts
it into the add line, ready to change.
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels.base import Tab
from cpcxray.ui.panels.colours import SYMBOL_TYPE_COLOURS
from cpcxray.ui.panels.scroll_list import Row, ScrollList

SYMBOL_ROWS = 24
MAX_SYMBOLS = 1000


class SymbolsTab(Tab):
    """Symbol search, list and adding."""

    title = "Symbols"
    message = "symbol_message"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create the tab (the list is built with the widgets)."""
        super().__init__(*args, **kwargs)
        self.symbols: list[dict[str, Any]] = []
        self.list: ScrollList | None = None

    def build(self) -> None:
        """Create the widgets."""
        with dpg.group(horizontal=True):
            self.input("symbol_search", "search label or comment", 200, callback=self.refresh)
            self.entry("symbol_add", "BB5A label ; comment", 260, self.add_symbol)
        self.text("symbol_message")
        self.list = self.scroll_list(SYMBOL_ROWS, on_select=self.selected)
        self.text("symbols_total")

    def add_symbol(self) -> Any:
        """Add "address label ; comment" as a user symbol."""
        entry, _, comment = self.value("symbol_add").partition(";")
        fields = entry.split()
        if not fields:
            raise ValueError("expected 'address label ; comment'")
        label = fields[1] if len(fields) > 1 else None
        return self.session.symbol_add(fields[0], label, comment.strip() or None)

    def selected(self, index: int) -> None:
        """A symbol was selected: put it into the add line."""
        symbol = self.symbols[index]
        scope = f"{symbol['scope']}:" if symbol.get("scope") else ""
        comment = f" ; {symbol['comment']}" if symbol.get("comment") else ""
        text = f"{scope}{symbol['address']} {symbol.get('label') or '-'}{comment}"
        dpg.set_value(self.items["symbol_add"], text)

    def refresh(self) -> None:
        """Symbols matching the search text."""
        result = self.session.symbol_list(self.value("symbol_search"), limit=MAX_SYMBOLS)
        self.symbols = result["symbols"]
        assert self.list is not None
        self.list.set_rows([_row(symbol) for symbol in self.symbols])
        shown = f"{len(self.symbols)} shown, " if result["total"] > len(self.symbols) else ""
        dpg.set_value(self.items["symbols_total"], f"({shown}{result['total']} matching)")


def _row(symbol: dict[str, Any]) -> Row:
    """One list row: scope, address, label, comment; firmware names in the FW colour."""
    text = (f"{symbol.get('scope') or '':6} {symbol['address']}  {symbol.get('label') or '-':<20} "
            f"{symbol.get('comment') or ''}")  # fmt: skip
    colour = SYMBOL_TYPE_COLOURS["FW"][0] if symbol.get("source") == "firmware" else None
    return text, colour
