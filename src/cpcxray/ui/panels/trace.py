"""Trace tab: the instruction trace with call depth, changes and a detail box (task 5e, FR-DBG-08).

Layout agreed with the owner on 2026-10-22 (design canvas, artboard "Trace 3: indentation +
changes + detail"):

* Controls: the "Trace runs" switch with the speed note (off: stepping is still recorded; on:
  free runs about 5x slower); analysis targets only, write file, the trace file (▾), format.
* The list: address; the instruction indented by call depth (▸ call, ◂ return; the depth
  counts the calls and returns taken inside the trace - a reading aid), the name in it in its
  type's colour; the registers it changed (PC left out); a separator where execution moves to
  another ROM or to RAM; the selected row on the yellow bar. ↑/↓ and Page Up/Down move the
  selection while the mouse is over the list; a click selects.
* The detail (framed yellow) of the selected row: address, bytes, instruction, position with
  ▲ ▼, where it ran, the depth, its memory reads and writes (stepped instructions only) and
  the registers before / after, the changes in yellow.

Everything comes from `Session.trace_records` (the MCP tool returns the same).
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels import colours
from cpcxray.ui.panels.base import TAB_WIDTH, Tab
from cpcxray.ui.panels.scroll_list import ListState
from cpcxray.ui.panels.text import TraceRow, trace_detail, trace_rows
from cpcxray.ui.panels.widgets import badge, section, set_badge

TRACE_RECORDS = 200  # the last records the list shows
LIST_ROWS = 18  # visible rows
ROW_HEIGHT = 17
INDENT = 14  # pixels per call level
TRACE_EXTENSIONS = (".txt", ".csv", ".jsonl")
NAME_COLOURS = {"FW": "FW", "VAR": "VAR", "own": "sub"}  # as in the CPU tab


class TraceList:
    """The trace table as a list: selection, keys over it, scrolling (like `ScrollList`)."""

    def __init__(self, tab: TraceTab) -> None:
        """Create the list's state; `TraceTab.build` creates its widgets."""
        self.tab = tab
        self.state = ListState(LIST_ROWS)
        self.box: int | str = 0
        self.table: int | str = 0
        self.rows: list[TraceRow] = []
        self.row_items: list[int | str] = []  # per row of `rows`: its selectable (or 0)
        self._marked: int | None = None  # the position of the selected row

    def hovered(self) -> bool:
        """True while the mouse is over the list (then its keys work, not the CPC's)."""
        return bool(dpg.is_item_hovered(self.box))

    def handle_key(self, key: str) -> None:
        """↑/↓, Page Up/Down, Home, End move the selection (instructions, not separators)."""
        if key != "enter" and self.state.move(key):
            self.tab.select(self.state.selected)

    def show(self, rows: list[TraceRow], select: int | None) -> None:
        """Rebuild the table for new rows and select a record (its index)."""
        self.rows = rows
        self.state.set_count(sum(1 for row in rows if row.kind == "instruction"))
        dpg.delete_item(self.table, children_only=True, slot=1)
        self.row_items = []
        self._marked = None
        for row in rows:
            with dpg.table_row(parent=self.table):
                self.row_items.append(self._cells(row))
        if select is not None:
            self.state.select(select)
        self.mark(self.state.selected)

    def mark(self, index: int | None) -> None:
        """Put a record's row on the yellow bar and scroll it into view.

        The bar covers the whole row, the changed registers included, as the CPU tab's PC
        line; it is the only mark (no blue selection background, the text keeps its colours),
        and the row selected before loses it (owner, 2026-10-22).
        """
        styles = self.tab.styles
        if self._marked is not None:
            dpg.unhighlight_table_row(self.table, self._marked)
            dpg.bind_item_theme(self.row_items[self._marked], styles.plain_row(colours.TEXT_DIM))
            self._marked = None
        if index is None:
            return
        position = next(i for i, r in enumerate(self.rows) if r.index == index)
        dpg.highlight_table_row(self.table, position, (*colours.HERE_BACKGROUND, 255))
        # the mouse over the selected row leaves it as it is (no grey over the bar)
        dpg.bind_item_theme(self.row_items[position], styles.plain_row(colours.TEXT_DIM, False))
        self._marked = position
        top = dpg.get_y_scroll(self.box) / ROW_HEIGHT
        if position < top or position >= top + LIST_ROWS - 1:
            dpg.set_y_scroll(self.box, max(0, position - LIST_ROWS // 2) * ROW_HEIGHT)

    def _clicked(self, item: int | str, index: int) -> None:
        """A click on a row: select it; the selectable itself never stays "on" (no blue)."""
        dpg.set_value(item, False)
        self.tab.select(index)

    def _cells(self, row: TraceRow) -> int | str:
        """The cells of one row; returns its selectable (0 for a separator)."""
        if row.kind == "separator":
            dpg.add_text("")
            dpg.add_text(row.where, color=colours.TEXT_DIM)
            dpg.add_text("")
            return 0
        item = dpg.add_selectable(
            label=row.address,
            span_columns=True,
            user_data=row.index,
            callback=lambda sender, _v, index: self._clicked(sender, index),
        )
        dpg.bind_item_theme(item, self.tab.styles.plain_row(colours.TEXT_DIM))
        with dpg.group(horizontal=True, horizontal_spacing=0):
            if row.depth:
                dpg.add_spacer(width=row.depth * INDENT)
            dpg.add_text(f"{row.marker or ' '} ", color=colours.HEADING)
            dpg.add_text(f"{row.mnemonic} {row.before}" if row.before or row.name
                         else row.mnemonic)  # fmt: skip
            if row.name:
                colour = colours.SYMBOL_TYPE_COLOURS[NAME_COLOURS.get(row.name_type, "FW")][0]
                dpg.add_text(row.name, color=colour)
            if row.after:
                dpg.add_text(row.after)
        dpg.add_text(row.changes, color=colours.TEXT_BYTES)
        return item


class TraceTab(Tab):  # pylint: disable=too-many-instance-attributes  # trace state and widgets
    """Controls, the trace list and the detail of the selected instruction."""

    title = "Trace"
    message = "trace_message"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create the tab (the list is built with the widgets)."""
        super().__init__(*args, **kwargs)
        self.records: list[dict[str, Any]] = []
        self.after: dict[str, str] | None = None
        self.list = TraceList(self)
        self._shown: tuple[int, int] | None = None  # (first, last sequence) the list shows

    # -- building --------------------------------------------------------------------------------

    def build(self) -> None:
        """Create the controls, the list and the detail."""
        self._build_controls()
        self._build_list()
        self._build_detail()
        self.context.lists.append(self.list)  # type: ignore[arg-type]  # same interface

    def _build_controls(self) -> None:
        """The switch, its note, and the options of a trace file."""
        with section(self.styles, width=TAB_WIDTH):
            with dpg.group(horizontal=True):
                self.items["trace_toggle"] = badge(
                    self.styles,
                    "○ Trace runs",
                    "plain",
                    callback=lambda: self.call(self.toggle_trace),
                )
                dpg.add_text("off: stepping is still recorded · on: free runs about 5x slower",
                             color=colours.TEXT_DIM)  # fmt: skip
            with dpg.group(horizontal=True):
                self.items["trace_targets"] = dpg.add_checkbox(label="analysis targets only")
                self.items["trace_file"] = dpg.add_checkbox(label="write file")
                with self.path_field("trace_path", 150, "output", TRACE_EXTENSIONS,
                                     default_value="output/trace.txt"):  # fmt: skip
                    self.items["trace_format"] = dpg.add_combo(
                        ("text", "csv", "jsonl"), default_value="text", width=70
                    )
            self.text("trace_message", color=colours.TEXT_DIM)

    def _build_list(self) -> None:
        """The trace table in a scrolling box."""
        with section(self.styles, width=TAB_WIDTH):
            with dpg.group(horizontal=True):
                self.text("trace_count", color=colours.HEADING)
                dpg.add_text("▸ call · ◂ return · ↑↓ with the mouse "
                             "over the list", color=colours.TEXT_DIM)  # fmt: skip
            header = f"{'addr':<5}{'instruction (indent = call depth)':<52}changes"
            dpg.add_text(header, color=colours.TEXT_DIM)  # stays while the rows scroll
            with dpg.child_window(height=LIST_ROWS * ROW_HEIGHT + 4, border=False) as box:
                self.list.box = box
                with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit) as table:
                    self.list.table = table
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=34)
                    dpg.add_table_column(width_fixed=True, init_width_or_weight=330)
                    dpg.add_table_column(width_stretch=True)

    def _build_detail(self) -> None:
        """The yellow-framed detail box of the selected instruction."""
        with section(self.styles, width=TAB_WIDTH, style="here"):
            with dpg.group(horizontal=True):
                self.text("trace_bytes", color=colours.TEXT_DIM)
                self.text("trace_instruction")
                dpg.add_spacer(width=20)
                self.text("trace_position", color=colours.TEXT_DIM)
                dpg.add_button(arrow=True, direction=dpg.mvDir_Up,
                               callback=lambda: self.list.handle_key("up"))  # fmt: skip
                dpg.add_button(arrow=True, direction=dpg.mvDir_Down,
                               callback=lambda: self.list.handle_key("down"))  # fmt: skip
            self.text("trace_where", color=colours.TEXT_DIM, wrap=TAB_WIDTH - 20)
            with dpg.group(horizontal=True, horizontal_spacing=0):
                for column in range(2):
                    with dpg.group():
                        dpg.add_text(f"{'':<4}{'before':<8}after", color=colours.TEXT_DIM)
                        for line in range(4):
                            with dpg.group(horizontal=True, horizontal_spacing=0):
                                self.text(f"trace_reg_{line}_{column}", color=colours.TEXT_DIM)
                                self.text(f"trace_before_{line}_{column}")
                                self.text(f"trace_after_{line}_{column}")
                    dpg.add_spacer(width=40)

    # -- actions ---------------------------------------------------------------------------------

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
        self._show_switch()
        return result

    def select(self, index: int | None) -> None:
        """Select a record (by index): the bar in the list and the detail."""
        if index is None or not self.records:
            return
        self.list.state.select(index)
        self.list.mark(index)
        row = next(r for r in self.list.rows if r.index == index)
        detail = trace_detail(self.records, self.after, row)
        items = self.items
        dpg.set_value(items["trace_bytes"], detail["bytes"])
        dpg.set_value(items["trace_instruction"], f"{row.mnemonic} {row.before}{row.name}"
                      f"{row.after}".rstrip())  # fmt: skip
        dpg.set_value(items["trace_position"], detail["position"])
        dpg.set_value(items["trace_where"], detail["line"])
        for number, (name, before, after, changed) in enumerate(detail["registers"]):
            line, column = divmod(number, 2)
            dpg.set_value(items[f"trace_reg_{line}_{column}"], f"{name:<4}")
            dpg.set_value(items[f"trace_before_{line}_{column}"], f"{before:<8}")
            dpg.set_value(items[f"trace_after_{line}_{column}"], after)
            dpg.configure_item(items[f"trace_after_{line}_{column}"],
                               color=colours.HERE if changed else colours.TEXT)  # fmt: skip

    # -- refreshing ------------------------------------------------------------------------------

    def refresh(self) -> None:
        """The switch, and the list when the trace changed (the newest record selected)."""
        self._show_switch()
        result = self.session.trace_records(TRACE_RECORDS)
        records = result["records"]
        shown = (records[0]["sequence"], records[-1]["sequence"]) if records else (0, 0)
        total = len(self.session.trace.records)
        dpg.set_value(self.items["trace_count"],
                      f"Trace · last {len(records)} of {total}")  # fmt: skip
        if shown == self._shown and result["after"] == self.after:
            return
        self._shown, self.records, self.after = shown, records, result["after"]
        self.list.show(trace_rows(records, self.after), len(records) - 1 if records else None)
        self.select(len(records) - 1 if records else None)

    def _show_switch(self) -> None:
        """The switch: "● Trace runs" lit green while on."""
        on = self.session.trace.enabled
        set_badge(self.styles, self.items["trace_toggle"],
                  "● Trace runs" if on else "○ Trace runs", "on" if on else "plain")  # fmt: skip
