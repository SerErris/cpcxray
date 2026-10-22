"""Trace tab: instruction trace of free runs on/off, file options, the last lines (FR-DBG-08).

The trace is off for free runs unless switched on with its toggle button (it costs about 5x
speed); stepping is always recorded into its buffer. A file is written only when "write file" is
ticked before switching the trace on.
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels.base import Tab

TRACE_LINES = 30


class TraceTab(Tab):
    """Toggle for tracing free runs, file options, and the last lines (steps always)."""

    title = "Trace"
    message = "trace_message"

    def build(self) -> None:
        """Create the widgets."""
        with dpg.group(horizontal=True):
            self.items["trace_toggle"] = dpg.add_button(
                label="Trace runs: off", width=130, callback=lambda: self.call(self.toggle_trace)
            )
            self.items["trace_targets"] = dpg.add_checkbox(label="analysis targets only")
        with dpg.group(horizontal=True):
            self.items["trace_file"] = dpg.add_checkbox(label="write file")
            self.input("trace_path", "output/trace.txt", 200, default_value="output/trace.txt")
            self.items["trace_format"] = dpg.add_combo(("text", "csv", "jsonl"),
                                                       default_value="text", width=70)  # fmt: skip
        dpg.add_text("Stepping is always recorded; tracing free runs costs about 5x speed.")
        self.text("trace_message")
        self.text("trace")

    def toggle_trace(self) -> Any:
        """Switch tracing of free runs on (with the chosen options) or off."""
        if self.session.trace.enabled:
            result = self.session.trace_stop()
        else:
            path = self.value("trace_path").strip() if self.value("trace_file") else None
            result = self.session.trace_start(
                path=path or None, file_format=self.value("trace_format"),
                targets_only=bool(self.value("trace_targets")),
            )  # fmt: skip
        label = "Trace runs: on" if self.session.trace.enabled else "Trace runs: off"
        dpg.set_item_label(self.items["trace_toggle"], label)
        return result

    def refresh(self) -> None:
        """Last instruction trace lines."""
        result = self.session.trace_read(TRACE_LINES)
        self.set_lines("trace", result.get("lines", []))
