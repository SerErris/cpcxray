"""Memory tab: the hex grid with known variables, paging and "Follow CPU" (task 5c).

Layout agreed with the owner on 2026-10-22 (design canvas, artboard "Memory 1: tidy grid +
variable table + follow CPU"):

* Controls: the address ("at"), ◀ ▶ (a page back / forward), the view (cpu, ram, a ROM ...),
  go to PC / SP / HL, the "Follow CPU" switch, the write field (always to RAM). While Follow is
  on: what the last step wrote and read, and how much was written since the last stop.
* Paging tiles as in the CPU tab; the quarter in view framed yellow.
* 16 lines of 16 bytes, every line starting at &xxx0; the scroll bar beside the grid, the
  mouse wheel and ↑ ↓ (a line) / Page Up / Page Down (a page) over the grid move through the
  64 KB. Jumps (a typed address, PC / SP / HL, Follow CPU, ◀ ▶) show the whole page holding the
  address (&xx00-&xxFF), the address itself framed. Bytes take the colour of their known
  variable; the selected variable's bytes are framed yellow; hovering a byte names it.
* Follow CPU marks: written by the last step (strong red), written since the last stop (dim
  red), read by the last step (blue frame). At every stop the view goes to the page of the last
  access (writes before reads) and selects the variable written last.
* Variables in view: the areas in one line, a detail line for the selected variable, and a
  table (colour, range, name - a structure's fields indented -, size); a click selects.

The data come from `Session.memory_view`, `memory_changes_get` and `memory_follow` (the MCP
tools return the same).
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.session.errors import SessionError
from cpcxray.ui.panels import colours
from cpcxray.ui.panels.base import TAB_WIDTH, Tab
from cpcxray.ui.panels.colours import AREA_COLOUR, VARIABLE_COLOURS
from cpcxray.ui.panels.scroll_list import ScrollList
from cpcxray.ui.panels.text import (
    LINE_BYTES,
    PAGE,
    follow_lines,
    follow_target,
    innermost,
    page_line,
    parse_memory_edit,
    variable_colour_indexes,
    variable_detail,
    variable_rows,
)
from cpcxray.ui.panels.widgets import BOX_GAP, badge, section, set_badge

ROWS = PAGE // LINE_BYTES  # 16 lines on screen
LAST_LINE = 0x10000 // LINE_BYTES - ROWS  # the top line that shows &FF00-&FFFF
TILE_WIDTH = (TAB_WIDTH - 3 * BOX_GAP) // 4
TABLE_ROWS = 8
WHEEL_LINES = 3
CELL_WIDTH = 18


class MemoryTab(Tab):  # pylint: disable=too-many-instance-attributes  # view state and widgets
    """The hex grid, its controls, paging and the variables in view (module docstring)."""

    title = "Memory"
    message = "memory_message"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create the tab at &0000."""
        super().__init__(*args, **kwargs)
        self.top_line = 0  # address / 16 of the first line shown
        self.focus: int | None = None  # the address a jump went to (framed)
        self.selected: str | None = None  # the selected variable's label
        self.variables: list[dict[str, Any]] = []
        self.colour_indexes: dict[str, int] = {}
        self.table: ScrollList | None = None
        self.table_rows: list[dict[str, Any]] = []
        self._cells: dict[int, tuple[str, int | str]] = {}  # what each byte cell shows now
        self._clock: int | None = None  # the machine clock at the last refresh (a stop moves it)

    # -- the list interface (keys and wheel over the grid, see DebuggerPanels) ------------------

    def hovered(self) -> bool:
        """True while the mouse is over the grid."""
        return bool(dpg.is_item_hovered(self.items["memory_grid"]))

    def handle_key(self, key: str) -> None:
        """↑ ↓ a line, Page Up / Page Down a page, Home / End the start / end of memory."""
        moves = {"up": -1, "down": 1, "page_up": -ROWS, "page_down": ROWS,
                 "home": -LAST_LINE, "end": LAST_LINE}  # fmt: skip
        if key in moves:
            self.scroll_to(self.top_line + moves[key])

    def wheel(self, delta: int) -> None:
        """The mouse wheel: a few lines (up for a positive delta)."""
        self.scroll_to(self.top_line - delta * WHEEL_LINES)

    # -- building --------------------------------------------------------------------------------

    def build(self) -> None:
        """Create the controls, the paging tiles, the grid and the variables."""
        self._build_controls()
        with dpg.group(horizontal=True, horizontal_spacing=BOX_GAP) as tiles:
            dpg.bind_item_theme(tiles, self.styles.box_row)
            for quarter in range(4):
                with section(self.styles, width=TILE_WIDTH) as tile:
                    self.items[f"memory_tile_{quarter}"] = tile
                    self.text(f"memory_tile_range_{quarter}", color=colours.TEXT_DIM)
                    self.text(f"memory_tile_text_{quarter}")
        self._build_grid()
        self._build_variables()
        self.context.lists.append(self)  # type: ignore[arg-type]  # keys and wheel

    def _build_controls(self) -> None:
        """Address, page buttons, view, go to; Follow CPU, write; the follow status."""
        with section(self.styles, width=TAB_WIDTH):
            with dpg.group(horizontal=True):
                dpg.add_text("at", color=colours.TEXT_DIM)
                self.input("memory_address", "address", 90, default_value="0000",
                           on_enter=True, callback=self._address_entered)  # fmt: skip
                dpg.add_button(
                    arrow=True,
                    direction=dpg.mvDir_Left,
                    callback=lambda: self.scroll_to(self._page_start() - ROWS, True),
                )
                dpg.add_button(
                    arrow=True,
                    direction=dpg.mvDir_Right,
                    callback=lambda: self.scroll_to(self._page_start() + ROWS, True),
                )
                dpg.add_text("view", color=colours.TEXT_DIM)
                self.items["memory_view"] = dpg.add_combo(
                    self.memory_views(),
                    default_value="cpu",
                    width=110,
                    callback=self._view_changed,
                )
                dpg.add_text("go to", color=colours.TEXT_DIM)
                for register in ("PC", "SP", "HL"):
                    self.button(register, lambda name=register: self.go_to_register(name))
            with dpg.group(horizontal=True):
                self.items["memory_follow"] = badge(
                    self.styles,
                    "○ Follow CPU",
                    "plain",
                    callback=lambda: self.call(self.toggle_follow),
                )
                dpg.add_text("write", color=colours.TEXT_DIM)
                self.entry("memory_edit", "4000: 3E 05 C9", 170, self.edit_memory)
                dpg.add_text("(always to RAM)", color=colours.TEXT_DIM)
            self.text("memory_follow_status", color=colours.TEXT_DIM)
            with dpg.group(horizontal=True) as legend:
                self.items["memory_legend"] = legend
                for style, text in (("mem_last", "written by the last step"),
                                    ("mem_written", "written since the last stop"),
                                    ("mem_read", "read by the last step")):  # fmt: skip
                    badge(self.styles, "07", style)
                    dpg.add_text(text, color=colours.TEXT_DIM)
            self.text("memory_message", color=colours.TEXT_DIM)

    def _build_grid(self) -> None:
        """The header, 16 lines of 16 byte cells and ASCII, the scroll bar, the hover line."""
        with section(self.styles, width=TAB_WIDTH):
            with dpg.group(horizontal=True, horizontal_spacing=BOX_GAP):
                with dpg.child_window(width=TAB_WIDTH - 40, height=(ROWS + 1) * 17 + 6,
                                      border=False, no_scrollbar=True) as grid:  # fmt: skip
                    self.items["memory_grid"] = grid
                    self._grid_line("header", [f"{c:02X}" for c in range(LINE_BYTES)])
                    for row in range(ROWS):
                        self._grid_line(row, None)
                self.items["memory_slider"] = dpg.add_slider_int(
                    vertical=True, height=(ROWS + 1) * 17 + 6, width=14, min_value=LAST_LINE,
                    max_value=0, format="", callback=lambda _s, line: self.scroll_to(line),
                )  # fmt: skip
            self.text("memory_hover", color=colours.TEXT_DIM)

    def _grid_line(self, row: int | str, header: list[str] | None) -> None:
        """One line of the grid: address, 16 cells (a gap after 8), ASCII."""
        with dpg.group(horizontal=True, horizontal_spacing=2):
            self.text(f"memory_address_{row}", color=colours.TEXT_DIM)
            for column in range(LINE_BYTES):
                if column == 8:
                    dpg.add_spacer(width=6)
                label = header[column] if header else "--"
                cell = dpg.add_button(
                    label=label,
                    width=CELL_WIDTH,
                    user_data=(row, column),
                    callback=lambda _s, _v, at: self._cell_clicked(*at),
                )
                theme = self.styles.byte_cell(colours.TEXT_DIM if header else colours.TEXT,
                                              None, None)  # fmt: skip
                dpg.bind_item_theme(cell, theme)
                self.items[f"memory_byte_{row}_{column}"] = cell
            dpg.add_spacer(width=6)
            self.text(f"memory_ascii_{row}", color=colours.TEXT_DIM)
        if header:
            dpg.set_value(self.items[f"memory_address_{row}"], "     ")
            dpg.set_value(self.items[f"memory_ascii_{row}"], "ASCII")

    def _build_variables(self) -> None:
        """Variables in view: the areas, the selected one's detail, the table."""
        with section(self.styles, width=TAB_WIDTH):
            with dpg.group(horizontal=True):
                self.text("memory_variables_count", color=colours.HEADING)
                self.text("memory_areas", color=colours.TEXT_DIM)
            with section(self.styles, width=TAB_WIDTH - 20, style="here"):
                with dpg.group(horizontal=True):
                    self.text("memory_selected_name")
                    self.text("memory_selected_detail", color=colours.TEXT_DIM)
                self.text("memory_selected_description", color=colours.TEXT_DIM,
                          wrap=TAB_WIDTH - 40)  # fmt: skip
            self.table = self.scroll_list(TABLE_ROWS, on_select=self._variable_selected)

    # -- actions ---------------------------------------------------------------------------------

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

    def edit_memory(self) -> Any:
        """Write "address: bytes" into RAM."""
        address, data = parse_memory_edit(self.value("memory_edit"))
        return self.session.memory_write(address, data)

    def toggle_follow(self) -> Any:
        """Switch Follow CPU on or off (its write observer, `memory_follow`)."""
        following = bool(self.session.memory_changes_get()["following"])
        return self.session.memory_follow(not following)

    def go_to_register(self, name: str) -> None:
        """Show the page of the address in PC, SP or HL."""
        self.jump(self.session.registers_get()[name.lower()])

    def jump(self, address: int) -> None:
        """Show the whole page holding `address` and frame the address."""
        self.focus = address & 0xFFFF
        dpg.set_value(self.items["memory_address"], f"{self.focus:04X}")
        self.scroll_to(page_line(self.focus))

    def scroll_to(self, line: int, page: bool = False) -> None:
        """Show memory from a line (address / 16), kept inside the 64 KB."""
        self.top_line = max(0, min(LAST_LINE, line))
        if page:  # ◀ ▶: the address field follows the page
            dpg.set_value(self.items["memory_address"], f"{self.top_line * LINE_BYTES:04X}")
        self.refresh()

    def _view_changed(self) -> None:
        """Another view: a ROM shows only where the CPU sees it - go there if outside."""
        view = self.value("memory_view")
        if view in ("cpu", "ram") or view.startswith("ram:"):
            self.refresh()
            return
        base = 0x0000 if view == "lower" else 0xC000  # the lower ROM, else an upper ROM
        start = self.top_line * LINE_BYTES
        if not base <= start <= base + 0x3FFF - PAGE + 1:
            self.jump(base)
        else:
            self.refresh()

    def _page_start(self) -> int:
        """The line of the page the top line belongs to (◀ ▶ step whole pages)."""
        return page_line(self.top_line * LINE_BYTES)

    def _address_entered(self) -> None:
        """An address (or label) was typed: go to its page."""
        try:
            address = self.session._address(self.value("memory_address"))  # pylint: disable=protected-access
        except SessionError as error:
            dpg.set_value(self.items["memory_message"], f"error: {error}")
            return
        self.jump(address)

    def _cell_clicked(self, row: int | str, column: int) -> None:
        """A byte was clicked: select its variable."""
        if isinstance(row, str):
            return
        variable = innermost(self.variables, self._address_of(row, column))
        if variable is not None:
            self.selected = variable["label"]
            self.refresh()

    def _variable_selected(self, index: int) -> None:
        """A row of the table was selected."""
        self.selected = self.table_rows[index]["label"]
        self.refresh()

    def _address_of(self, row: int, column: int) -> int:
        """The address a grid cell shows."""
        return (self.top_line * LINE_BYTES + row * LINE_BYTES + column) & 0xFFFF

    # -- refreshing ------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Follow the CPU after a stop; then the grid, paging, follow status and variables."""
        session = self.session
        changes = session.memory_changes_get()
        clock = getattr(session.machine, "clock_us", 0)
        target = None
        if changes["following"] and self._clock is not None and clock != self._clock:
            target = follow_target(changes)  # a stop since the last refresh: go where it wrote
            if target is not None:
                self.focus = target
                self.top_line = min(LAST_LINE, page_line(target))
                dpg.set_value(self.items["memory_address"], f"{target:04X}")
        self._clock = clock
        start = self.top_line * LINE_BYTES
        try:
            dump = session.memory_view(start, PAGE, self.value("memory_view"))
        except SessionError as error:
            dpg.set_value(self.items["memory_message"], f"error: {error}")
            return
        self.variables = dump["variables"]
        if target is not None and (variable := innermost(self.variables, target)) is not None:
            self.selected = variable["label"]  # Follow CPU: the variable written last
        self.colour_indexes = variable_colour_indexes(self.variables, start, PAGE)
        self._refresh_controls(changes, start)
        self._refresh_tiles(start)
        hex_text = dump["hex"]
        values = [None if hex_text[i : i + 2] == "??" else int(hex_text[i : i + 2], 16)
                  for i in range(0, len(hex_text), 2)]  # fmt: skip  # None: not in this view
        self._refresh_grid(values, start, changes)
        self._refresh_variables()

    def _refresh_controls(self, changes: dict[str, Any], start: int) -> None:
        """The Follow switch, its status lines and legend."""
        following = changes["following"]
        set_badge(self.styles, self.items["memory_follow"],
                  "● Follow CPU" if following else "○ Follow CPU",
                  "on" if following else "plain")  # fmt: skip
        self.set_lines("memory_follow_status", follow_lines(changes, start, PAGE))
        dpg.configure_item(self.items["memory_legend"], show=following)
        dpg.configure_item(self.items["memory_follow_status"], show=following)

    def _refresh_tiles(self, start: int) -> None:
        """One tile per quarter; the quarter in view framed yellow."""
        for number, quarter in enumerate(self.session.paging_get()["quarters"]):
            dpg.set_value(self.items[f"memory_tile_range_{number}"],
                          f"{quarter['start']}-{quarter['end']}")  # fmt: skip
            text = str(quarter["text"]).replace("lower ROM (", "lower ROM ")
            text = text.replace("upper ROM ", "ROM ").replace(".rom)", "").replace("(", "")
            dpg.set_value(self.items[f"memory_tile_text_{number}"], text.replace(")", ""))
            theme = self.styles.tile(quarter["source"] != "ram", start >> 14 == number)
            dpg.bind_item_theme(self.items[f"memory_tile_{number}"], theme)

    def _refresh_grid(self, data: list[int | None], start: int, changes: dict[str, Any]) -> None:
        """Addresses, the 256 cells (colour, marks, frames), ASCII, the scroll bar."""
        marks = _follow_marks(changes)
        for row in range(ROWS):
            line_start = start + row * LINE_BYTES
            dpg.set_value(self.items[f"memory_address_{row}"], f"{line_start & 0xFFFF:04X} ")
            chunk = data[row * LINE_BYTES : (row + 1) * LINE_BYTES]
            ascii_text = "".join(" " if b is None else chr(b) if 0x20 <= b < 0x7F else "."
                                 for b in chunk)  # fmt: skip
            dpg.set_value(self.items[f"memory_ascii_{row}"], ascii_text)
            for column in range(LINE_BYTES):
                address = (line_start + column) & 0xFFFF
                byte = chunk[column] if column < len(chunk) else None
                value = "--" if byte is None else f"{byte:02X}"
                self._show_cell(row, column, value, self._cell_theme(address, marks))
        dpg.set_value(self.items["memory_slider"], self.top_line)
        self._show_hover()

    def _show_hover(self) -> None:
        """Name the byte under the mouse: its address and variable."""
        hovered = next(
            (
                (r, c)
                for r in range(ROWS)
                for c in range(LINE_BYTES)
                if dpg.is_item_hovered(self.items[f"memory_byte_{r}_{c}"])
            ),
            None,
        )
        hover_text = ""
        if hovered is not None:
            address = self._address_of(*hovered)
            variable = innermost(self.variables, address)
            hover_text = f"{address:04X} {variable['label'] if variable else ''}"
        dpg.set_value(self.items["memory_hover"], hover_text)

    def _cell_theme(self, address: int, marks: dict[int, str]) -> int | str:
        """A byte's look: its variable's colour, a Follow mark, a frame."""
        variable = innermost(self.variables, address)
        if variable is not None:
            text = VARIABLE_COLOURS[self.colour_indexes[variable["label"]] % len(VARIABLE_COLOURS)]
        elif any(v.get("area") and _holds(v, address) for v in self.variables):
            text = AREA_COLOUR
        else:
            text = colours.TEXT
        mark = marks.get(address)
        background = {"last": colours.WRITTEN_LAST, "written": colours.WRITTEN_SINCE}.get(
            mark or ""
        )
        if mark == "last":
            text = colours.WHITE
        border = None
        if mark == "read":
            border = colours.READ_LAST
        elif address == self.focus or (variable is not None and variable["label"] == self.selected):
            border = colours.HERE
        return self.styles.byte_cell(text, background, border)

    def _show_cell(self, row: int, column: int, value: str, theme: int | str) -> None:
        """Update a cell only when its text or look changed (256 cells per refresh)."""
        key = row * LINE_BYTES + column
        if self._cells.get(key) == (value, theme):
            return
        self._cells[key] = (value, theme)
        cell = self.items[f"memory_byte_{row}_{column}"]
        dpg.set_item_label(cell, value)
        dpg.bind_item_theme(cell, theme)

    def _refresh_variables(self) -> None:
        """Count, areas, the selected variable's detail and the table."""
        assert self.table is not None
        self.table_rows = variable_rows(self.variables)
        areas = [v["label"] for v in self.variables if v.get("area")]
        dpg.set_value(self.items["memory_variables_count"],
                      f"Variables in view · {len(self.table_rows)}")  # fmt: skip
        dpg.set_value(self.items["memory_areas"], f"in {', '.join(areas)}" if areas else "")
        rows = []
        for entry in self.table_rows:
            index = self.colour_indexes.get(entry["label"])
            colour = (
                VARIABLE_COLOURS[index % len(VARIABLE_COLOURS)]
                if index is not None
                else AREA_COLOUR
            )
            name = ("  " if entry["indent"] else "") + entry["label"]
            rows.append((f"■ {entry['range']:<10} {name:<26} {entry['size']:>4}", colour))
        self.table.set_rows(rows)
        selected = next((v for v in self.variables if v["label"] == self.selected), None)
        position = next((i for i, e in enumerate(self.table_rows)
                         if e["label"] == self.selected), None)  # fmt: skip
        if position is not None and position != self.table.selected:
            self.table.select(position)
        dpg.set_value(self.items["memory_selected_name"],
                      selected["label"] if selected else "click a byte or a variable")  # fmt: skip
        dpg.set_value(self.items["memory_selected_detail"],
                      variable_detail(selected, self.variables) if selected else "")  # fmt: skip
        dpg.set_value(self.items["memory_selected_description"],
                      selected.get("description", "") if selected else "")  # fmt: skip


def _holds(variable: dict[str, Any], address: int) -> bool:
    """True if a variable covers an address."""
    return int(variable["address"], 16) <= address <= int(variable["last"], 16)


def _follow_marks(changes: dict[str, Any]) -> dict[int, str]:
    """Per address: "last" (written by the last step), "written" (since the stop), "read"."""
    marks = {int(w["address"], 16): "written" for w in changes.get("written", [])}
    step = changes.get("last_step") or {"accesses": []}
    for access in step["accesses"]:
        address = int(access["address"], 16)
        if access["access"] == "write":
            marks[address] = "last"
        elif address not in marks:
            marks[address] = "read"
    return marks
