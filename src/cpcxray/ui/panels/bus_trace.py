"""Bus trace tab: bus access trace on/off with an access type filter, the last lines (FR-DBG-08)."""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels.base import Tab

BUS_TRACE_LINES = 30


class BusTraceTab(Tab):
    """Bus trace switch, access type filter and lines."""

    title = "Bus trace"
    message = "bus_message"

    def build(self) -> None:
        """Create the widgets."""
        with dpg.group(horizontal=True):
            self.input("bus_kinds", "M1,read,write,in,out,interrupt (empty: all)", 300)
            self.button("Start", self.start_bus_trace)
            self.button("Stop", self.session.bus_trace_stop)
        self.text("bus_message")
        self.text("bus_trace")

    def start_bus_trace(self) -> Any:
        """Start the bus trace with the access types typed in (empty: all)."""
        kinds = [k.strip() for k in self.value("bus_kinds").split(",") if k.strip()]
        return self.session.bus_trace_start(kinds=kinds or None)

    def refresh(self) -> None:
        """Last bus trace lines."""
        self.set_lines("bus_trace", self.session.bus_trace_read(BUS_TRACE_LINES)["lines"])
