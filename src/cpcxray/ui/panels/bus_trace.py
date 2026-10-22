"""Bus trace tab: recording controls and the accesses grouped per instruction (task 5f, FR-DBG-08).

Layout agreed with the owner on 2026-10-22 (design canvas, artboard "Bus trace 3: grouped,
detail switches"):

* Controls: the "Recording" switch for free runs - stepping is always recorded (owner,
  2026-10-22) - with the record count and its cost (it records every access type, within from /
  to, keeping as many records as asked); Clear; and what the list shows: the access types as
  chips in their colours (fetch grey, read blue, write red, in/out purple, interrupt yellow) - a
  filter of the view, applied at once (owner, 2026-10-22: rather filter than not record); the
  instructions' own fetches and operand bytes (off); word accesses combined (on).
* The list, grouped per instruction: PC and instruction, then its accesses with a type chip
  (F, R, W, IN, OUT, INT), "address = value" and the meaning (`Session.bus_trace_view`:
  variables, stack, return addresses, port devices); fetches and operands greyed; a separator
  where the ROM / RAM context changes.
* History: the list moves through everything recorded - ↑ ↓ (a line), Page Up / Page Down
  (a screen), Home / End (oldest / newest) with the mouse over the list, the mouse wheel, and
  the scroll bar beside it; a position line ("records 9,973-10,000 of 10,000"). At a stop the
  list shows the newest records unless the owner has moved back into the history; then it
  stays there, and "↓ newest" returns.

The list draws a window of the history: the rows are a fixed pool filled from
`bus_trace_view` around the position, so a long buffer costs nothing until it is shown.
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.debugger.bus_trace import ACCESS_KINDS
from cpcxray.ui.panels import colours
from cpcxray.ui.panels.base import TAB_WIDTH, Tab
from cpcxray.ui.panels.colours import ACCESS_COLOURS, ACCESS_LETTERS
from cpcxray.ui.panels.text import BusLine, bus_lines, bus_position
from cpcxray.ui.panels.widgets import BOX_GAP, Chips, badge, section, set_badge

LINES = 22  # rows of the list
WINDOW = 600  # records read around the position
NAME_COLOURS = {"FW": "FW", "VAR": "VAR", "own": "sub"}  # as in the CPU tab
WHEEL_LINES = 3
DEFAULT_CAPACITY = 10_000
SLIDER_WIDTH = 14


class BusTraceTab(Tab):  # pylint: disable=too-many-instance-attributes  # position and widgets
    """Recording controls and the grouped, scrollable bus trace (see the module docstring)."""

    title = "Bus trace"
    message = "bus_message"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create the tab; the list follows the newest records."""
        super().__init__(*args, **kwargs)
        self.following = True  # show the newest records at every refresh
        self.anchor = 0  # buffer index of the top line's record (while not following)
        self.lines: list[BusLine] = []  # the lines of the current window
        self.start = 0  # the window's line at the top of the list
        self.total = 0
        self.types: Chips | None = None

    # -- the list interface (keys and wheel over the list, see DebuggerPanels) ------------------

    def hovered(self) -> bool:
        """True while the mouse is over the list."""
        return bool(dpg.is_item_hovered(self.items["bus_box"]))

    def handle_key(self, key: str) -> None:
        """↑ ↓ a line, Page Up / Down a screen, Home / End the oldest / newest records."""
        if key == "home":
            self.following, self.anchor = False, 0
        elif key == "end":
            self.following = True
        elif key in ("up", "down", "page_up", "page_down"):
            step = {"up": -1, "down": 1, "page_up": -(LINES - 1), "page_down": LINES - 1}
            self._move(step[key])
            return
        self.refresh()

    def wheel(self, delta: int) -> None:
        """The mouse wheel: a few lines (up for a positive delta)."""
        self._move(-delta * WHEEL_LINES)

    def _move(self, lines: int) -> None:
        """Move the list by lines; reaching the newest end follows it again."""
        target = self.start + lines
        if target + LINES >= len(self.lines) and self._window_at_end:
            self.following = True
        elif self.lines:
            target = max(0, min(target, len(self.lines) - 1))
            if target == 0 and lines < 0:  # above the window: one record further back
                self.anchor = max(0, self.lines[0].record - 1)
            else:
                self.anchor = self.lines[target].record
            self.following = False
        self.refresh()

    # -- building --------------------------------------------------------------------------------

    def build(self) -> None:
        """Create the controls and the list."""
        self._build_controls()
        self._build_list()
        self.context.lists.append(self)  # type: ignore[arg-type]  # same interface

    def _build_controls(self) -> None:
        """Switch, count, Clear; access type chips; range and capacity; show switches."""
        session = self.session
        with section(self.styles, width=TAB_WIDTH):
            with dpg.group(horizontal=True):
                self.items["bus_toggle"] = badge(
                    self.styles, "○ Recording", "plain", callback=lambda: self.call(self.toggle)
                )
                self.text("bus_count", color=colours.TEXT_DIM)
                self.button("Clear", session.bus_trace_clear)
            with dpg.group(horizontal=True):
                dpg.add_text("show", color=colours.TEXT_DIM)
                self.types = Chips(self.styles, list(ACCESS_KINDS), list(ACCESS_KINDS),
                                   multiple=True, chip_colours=ACCESS_COLOURS,
                                   on_change=lambda _selected: self.refresh())  # fmt: skip
            with dpg.group(horizontal=True):
                dpg.add_text("from", color=colours.TEXT_DIM)
                self.input("bus_from", "0000", 60, default_value="0000")
                dpg.add_text("to", color=colours.TEXT_DIM)
                self.input("bus_to", "FFFF", 60, default_value="FFFF")
                dpg.add_text("keep", color=colours.TEXT_DIM)
                self.input("bus_keep", "10000", 70, default_value=str(DEFAULT_CAPACITY))
            with dpg.group(horizontal=True):
                dpg.add_text("    ", color=colours.TEXT_DIM)
                self.items["bus_fetches"] = dpg.add_checkbox(label="fetches and operands",
                                                             callback=self.refresh)  # fmt: skip
                self.items["bus_combine"] = dpg.add_checkbox(label="combine word accesses",
                                                             default_value=True,
                                                             callback=self.refresh)  # fmt: skip
            self.text("bus_message", color=colours.TEXT_DIM)

    def _build_list(self) -> None:
        """The pooled rows of the list and the scroll bar beside them."""
        with section(self.styles, width=TAB_WIDTH):
            with dpg.group(horizontal=True):
                self.text("bus_summary", color=colours.HEADING)
                dpg.add_text("R read · W write · IN/OUT port · F fetch",
                             color=colours.TEXT_DIM)  # fmt: skip
            with dpg.group(horizontal=True):
                self.text("bus_position", color=colours.TEXT_DIM)
                dpg.add_text("· ↑↓ PgUp PgDn Home End over the list",
                             color=colours.TEXT_DIM)  # fmt: skip
                self.items["bus_newest"] = dpg.add_button(
                    label="↓ newest", show=False, callback=lambda: self.handle_key("end")
                )
            with dpg.group(horizontal=True, horizontal_spacing=BOX_GAP):
                with dpg.child_window(width=TAB_WIDTH - SLIDER_WIDTH - 24,
                                      height=LINES * 17 + 6, border=False,
                                      no_scrollbar=True) as box:  # fmt: skip
                    self.items["bus_box"] = box
                    with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit):
                        dpg.add_table_column(width_fixed=True, init_width_or_weight=34)
                        dpg.add_table_column(width_fixed=True, init_width_or_weight=230)
                        dpg.add_table_column(width_stretch=True)
                        for row in range(LINES):
                            self._build_row(row)
                self.items["bus_slider"] = dpg.add_slider_int(
                    vertical=True, height=LINES * 17 + 6, width=SLIDER_WIDTH, min_value=0,
                    max_value=0, format="", callback=lambda _s, value: self._slid(value),
                )  # fmt: skip

    def _build_row(self, row: int) -> None:
        """One pooled row: PC; instruction parts or a type chip and "address = value"; meaning."""
        with dpg.table_row():
            self.text(f"bus_pc_{row}", color=colours.TEXT_DIM)
            with dpg.group(horizontal=True, horizontal_spacing=0):
                self.items[f"bus_chip_{row}"] = badge(self.styles, "R", "plain", show=False)
                for part in ("code", "name", "after"):
                    self.text(f"bus_{part}_{row}")
            self.text(f"bus_meaning_{row}", color=colours.TEXT_DIM)

    # -- actions ---------------------------------------------------------------------------------

    def toggle(self) -> Any:
        """Start recording with the chosen types, range and capacity, or stop it."""
        session = self.session
        if session.bus_trace.enabled:
            return session.bus_trace_stop()
        return session.bus_trace_start(  # every access type: the chips filter the view
            start=self.value("bus_from") or "0000",
            end=self.value("bus_to") or "FFFF",
            capacity=int(self.value("bus_keep") or DEFAULT_CAPACITY),
        )  # fmt: skip

    def _slid(self, value: int) -> None:
        """The scroll bar was moved: show the history from that record (or the newest)."""
        self.following = value >= self.total - LINES
        self.anchor = value
        self.refresh()

    # -- refreshing ------------------------------------------------------------------------------

    @property
    def _window_at_end(self) -> bool:
        """The window read last reaches the newest record."""
        return not self.lines or self.lines[-1].record >= self.total - 1

    def refresh(self) -> None:
        """Controls state and the list at the position (the newest while following)."""
        session = self.session
        trace = session.bus_trace
        on = trace.enabled
        set_badge(self.styles, self.items["bus_toggle"],
                  "● Recording" if on else "○ Recording", "on" if on else "plain")  # fmt: skip
        capacity = trace.records.maxlen or DEFAULT_CAPACITY
        dpg.set_value(self.items["bus_count"], f"{len(trace.records)} of {capacity} records "
                      "· on: free runs about 1.7x slower; steps always")  # fmt: skip
        assert self.types is not None
        everything = len(self.types.selected) == len(ACCESS_KINDS)
        switches = {"fetches": bool(self.value("bus_fetches")),
                    "combine": bool(self.value("bus_combine")),
                    "types": None if everything else list(self.types.selected)}  # fmt: skip
        top = None if self.following else max(0, self.anchor - WINDOW // 2)
        view = session.bus_trace_view(top, WINDOW, **switches)
        self.total = view["total"]
        self.lines = bus_lines(view)
        if self.following:
            self.start = max(0, len(self.lines) - LINES)
        else:
            self.start = next((i for i, line in enumerate(self.lines)
                               if line.record >= self.anchor), 0)  # fmt: skip
        self._show(self.lines[self.start : self.start + LINES])

    def _show(self, shown: list[BusLine]) -> None:
        """Fill the pooled rows, the summary, the position line and the scroll bar."""
        for row in range(LINES):
            self._fill_row(row, shown[row] if row < len(shown) else BusLine("empty", 0))
        instructions = sum(1 for line in shown if line.kind == "instruction")
        accesses = sum(1 for line in shown if line.kind == "access")
        dpg.set_value(self.items["bus_summary"], f"Bus trace · {instructions} instructions "
                      f"· {accesses} accesses shown")  # fmt: skip
        dpg.set_value(self.items["bus_position"], bus_position(shown, self.total))
        dpg.configure_item(self.items["bus_newest"], show=not self.following)
        slider = self.items["bus_slider"]
        # top = oldest: a reversed range (ImGui allows minimum > maximum)
        dpg.configure_item(slider, min_value=max(0, self.total - 1), max_value=0)
        dpg.set_value(slider, shown[0].record if shown else 0)

    def _fill_row(self, row: int, line: BusLine) -> None:
        """One row: a separator, an instruction, an access, or empty."""
        items = self.items
        texts = {"pc": "", "code": "", "name": "", "after": "", "meaning": ""}
        colour = colours.TEXT
        chip = items[f"bus_chip_{row}"]
        dpg.configure_item(chip, show=line.kind == "access")
        if line.kind == "separator":
            texts["code"], colour = line.text, colours.TEXT_DIM
        elif line.kind == "gap":  # orange: something is missing here; numbers beside it
            texts["code"], _, texts["meaning"] = line.text.partition(": ")
            colour = colours.CHANGED
        elif line.kind == "instruction":
            texts |= {"pc": line.pc, "name": line.name, "after": line.after,
                      "code": f"{line.mnemonic} {line.before}".rstrip() if not line.name
                      else f"{line.mnemonic} {line.before}"}  # fmt: skip
        elif line.kind == "access":
            set_badge(self.styles, chip, ACCESS_LETTERS[line.access], f"access_{line.access}")
            texts |= {"code": f" {line.body}", "meaning": line.meaning}
            colour = colours.TEXT_OFF if line.quiet else colours.TEXT
        for part in ("pc", "code", "name", "after", "meaning"):
            dpg.set_value(items[f"bus_{part}_{row}"], texts[part])
        dpg.configure_item(items[f"bus_code_{row}"], color=colour)
        name_colour = colours.SYMBOL_TYPE_COLOURS[NAME_COLOURS.get(line.name_type, "FW")][0]
        dpg.configure_item(items[f"bus_name_{row}"], color=name_colour)
        port = line.access in ("in", "out")
        meaning_colour = ACCESS_COLOURS["out"][0] if port else colours.TEXT_DIM
        dpg.configure_item(
            items[f"bus_meaning_{row}"],
            color=colours.CHANGED if line.kind == "gap" else meaning_colour,
        )
