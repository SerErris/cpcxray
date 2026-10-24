"""CPU tab: registers, paging, disassembly and the stack (task 5b; FR-DBG-02..05, -12).

Layout agreed with the owner on 2026-10-22 (design canvas, artboard "CPU 3: combined"):

* Registers: AF/BC/DE/HL main and alternate as byte pairs, IX, IY, SP, PC, WZ beside them, the
  flags as eight letter chips (lit when set), I, R, IM, IFF1/2, halted; the registers the last
  step changed in yellow (`Session.last_stop_get`: "changed"). Below: set registers, Step out,
  run to.
* Paging: four tiles, one per 16 KB quarter (range, ROM file or RAM block), ROM tiles tinted,
  the quarter holding the PC framed yellow.
* The disassembly (gutter: ▶ PC, ● breakpoint; address; bytes, switchable; mnemonic; operands
  with the name in its type's colour: FW blue, VAR green, the owner's amber) with a separator
  row where a 16 KB quarter starts and the PC row on a yellow bar. The view stays put while the
  PC moves inside it and follows the PC when it leaves (or from the address typed in).
* Beside it the stack: words from SP (SP marked) up to the stack's top, and where the top came
  from ("ld sp at &064C (lower)": `Session.stack_get`, debugger/stack.py).
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.session.errors import SessionError
from cpcxray.ui.panels import colours
from cpcxray.ui.panels.base import TAB_WIDTH, Tab
from cpcxray.ui.panels.symbol_text import context_text
from cpcxray.ui.panels.text import (
    CodeRow,
    cpu_state_lines,
    disassembly_view,
    flag_bits,
    parse_assignments,
    register_pairs,
    register_words,
    routine_range,
)
from cpcxray.ui.panels.widgets import BOX_GAP, badge, section, set_badge

CODE_ROWS = 24  # rows of the disassembly (instructions, labels, separators)
STACK_WIDTH = 150
STACK_ROWS = 22
LOWER_HEIGHT = CODE_ROWS * 17 + 52  # the disassembly and the stack end on the same line
TILE_WIDTH = (TAB_WIDTH - 3 * BOX_GAP) // 4
KEEP_BELOW = 4  # the view moves on when the PC comes this close to its end


class CpuTab(Tab):  # pylint: disable=too-many-instance-attributes  # view state and widgets
    """Registers, paging tiles, disassembly and stack (see the module docstring)."""

    title = "CPU"
    message = "cpu_message"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create the tab; the disassembly starts at the PC."""
        super().__init__(*args, **kwargs)
        self._view_start: int | None = None  # first address of the following view
        self._row_addresses = [""] * CODE_ROWS  # the instruction address of each row ("": none)
        self._menu_address = 0  # the line the code context menu is open for
        self._view_addresses: list[int] = []  # instruction addresses shown
        self._highlighted: set[int] = set()  # table rows with a coloured bar

    # -- building --------------------------------------------------------------------------------

    def build(self) -> None:
        """Create the register box, the paging tiles, the disassembly and the stack."""
        self._build_registers()
        with dpg.group(horizontal=True, horizontal_spacing=BOX_GAP) as tiles:
            dpg.bind_item_theme(tiles, self.styles.box_row)
            for quarter in range(4):
                with section(self.styles, width=TILE_WIDTH) as tile:
                    self.items[f"tile_{quarter}"] = tile
                    self.text(f"tile_range_{quarter}", color=colours.TEXT_DIM)
                    self.text(f"tile_text_{quarter}")
        with dpg.group(horizontal=True, horizontal_spacing=BOX_GAP) as lower:
            dpg.bind_item_theme(lower, self.styles.box_row)
            self._build_disassembly()
            self._build_stack()

    def _build_registers(self) -> None:
        """Register table, words, flags and state; the row with set / Step out / run to."""
        items = self.items
        with section(self.styles, width=TAB_WIDTH):
            with dpg.group(horizontal=True, horizontal_spacing=22):
                with dpg.group():
                    dpg.add_text(f"{'':<4}{'main':<7}alternate", color=colours.TEXT_DIM)
                    for name, *_ in register_pairs(_ZERO_REGISTERS, []):
                        with dpg.group(horizontal=True, horizontal_spacing=0):
                            dpg.add_text(f"{name:<4}", color=colours.TEXT_DIM)
                            self.text(f"reg_{name}_main")
                            dpg.add_text("  ")
                            self.text(f"reg_{name}_alt")
                with dpg.group():
                    dpg.add_text("")
                    for name, *_ in register_words(_ZERO_REGISTERS, []):
                        with dpg.group(horizontal=True, horizontal_spacing=0):
                            dpg.add_text(f"{name:<4}", color=colours.TEXT_DIM)
                            self.text(f"reg_{name}")
                with dpg.group():
                    with dpg.group(horizontal=True, horizontal_spacing=3):
                        for letter, _ in flag_bits(0):
                            items[f"flag_{letter}"] = badge(self.styles, letter, "flag_off",
                                                            width=18)  # fmt: skip
                    self.text("cpu_state")
                    dpg.add_text("yellow: changed by the last step", color=colours.TEXT_DIM)
            with dpg.group(horizontal=True):
                dpg.add_text("set", color=colours.TEXT_DIM)
                self.entry("register_edit", "hl=4000, a=5", 150, self.set_registers)
                self.button("Step out", self.loop.step_out)
                dpg.add_text("run to", color=colours.TEXT_DIM)
                self.entry("run_to", "address/label", 120, self.run_to)
            self.text("cpu_message", color=colours.TEXT_DIM)

    def _build_disassembly(self) -> None:
        """The disassembly table: gutter, address, bytes, mnemonic, operands (+ comment)."""
        width = TAB_WIDTH - STACK_WIDTH - BOX_GAP
        with section(self.styles, width=width, height=LOWER_HEIGHT):
            with dpg.group(horizontal=True):
                dpg.add_text("at", color=colours.TEXT_DIM)
                self.input("disassembly_address", "PC or address/label", 150,
                           callback=self._address_changed)  # fmt: skip
                self.items["show_bytes"] = dpg.add_checkbox(
                    label="bytes", default_value=True, callback=self._bytes_switched
                )
                self.text("code_context", color=colours.TEXT_DIM)  # of the PC (or the typed view)
            with dpg.table(header_row=False, policy=dpg.mvTable_SizingFixedFit,
                           pad_outerX=False) as table:  # fmt: skip
                self.items["code_table"] = table
                dpg.add_table_column(width_fixed=True, init_width_or_weight=12)
                dpg.add_table_column(width_fixed=True, init_width_or_weight=34)
                self.items["bytes_column"] = dpg.add_table_column(
                    width_fixed=True, init_width_or_weight=64
                )
                dpg.add_table_column(width_fixed=True, init_width_or_weight=32)
                dpg.add_table_column(width_stretch=True)
                for row in range(CODE_ROWS):
                    with dpg.table_row():
                        self.text(f"code_gutter_{row}")
                        self.text(f"code_address_{row}", color=colours.TEXT_DIM)
                        self._context_opener(self.items[f"code_address_{row}"], row)
                        self.text(f"code_bytes_{row}", color=colours.TEXT_OFF)
                        self.text(f"code_mnemonic_{row}")
                        with dpg.group(horizontal=True, horizontal_spacing=0) as operands:
                            for part in ("before", "name", "after", "comment"):
                                self.text(f"code_{part}_{row}")
                        self._operand_menu(operands, row)
            dpg.add_text("blue firmware · green variable · amber yours",
                         color=colours.TEXT_DIM)  # fmt: skip
        self._build_context_menu()

    def _operand_menu(self, operands: int | str, row: int) -> None:
        """Right-click menu of a row's operands: the owner's operand override (`operand_set`)."""
        self.context.popup_owners.append(operands)
        # built like dpg.popup, which opens at the window's corner here: shown at the mouse
        with dpg.window(popup=True, show=False, autosize=True, no_title_bar=True) as menu:
            dpg.add_text("the operand is", color=colours.TEXT_DIM)
            for role, text in (("value", "a number (hex)"), ("address", "an address (named)"),
                               ("auto", "judged by cpcxray")):  # fmt: skip
                dpg.add_button(label=text, width=150, user_data=(row, role, menu),
                               callback=lambda _s, _a, data: self._set_operand(*data))  # fmt: skip
        with dpg.item_handler_registry() as handlers:
            dpg.add_item_clicked_handler(
                button=dpg.mvMouseButton_Right, user_data=menu,
                callback=lambda _s, _a, item: _open_at_mouse(item),
            )  # fmt: skip
        dpg.bind_item_handler_registry(operands, handlers)

    def _build_context_menu(self) -> None:
        """The "code context" menu of the address column (task 10, D-43): one for all rows."""
        with dpg.window(popup=True, show=False, autosize=True, no_title_bar=True) as menu:
            self.items["context_menu"] = menu
            dpg.add_text("code context", color=colours.HEADING)
            with dpg.group(horizontal=True):
                dpg.add_text("group", color=colours.TEXT_DIM)
                self.items["context_group"] = dpg.add_combo([], width=100)
            with dpg.group(horizontal=True):
                self.text("context_from", color=colours.TEXT_DIM)
                dpg.add_text("to", color=colours.TEXT_DIM)
                self.input("context_to", "last address", 80)
            for label, mode in (("from - to", "range"), ("this routine", "routine"),
                                ("whole scope", "scope"), ("none", "none")):  # fmt: skip
                dpg.add_button(
                    label=label, width=150, user_data=mode,
                    callback=lambda _s, _a, choice: self._set_context(choice),
                )  # fmt: skip

    def _context_opener(self, address_cell: int | str, row: int) -> None:
        """A right click on a row's address opens the code context menu for that line."""
        self.context.popup_owners.append(address_cell)
        with dpg.item_handler_registry() as handlers:
            dpg.add_item_clicked_handler(
                button=dpg.mvMouseButton_Right, user_data=row,
                callback=lambda _s, _a, index: self._open_context_menu(index),
            )  # fmt: skip
        dpg.bind_item_handler_registry(address_cell, handlers)

    def _open_context_menu(self, row: int) -> None:
        """Fill the menu for the row's line (its address, the groups) and show it at the mouse."""
        address = self._row_addresses[row] if row < len(self._row_addresses) else ""
        if not address:
            return
        self._menu_address = int(address, 16)
        names = [g["name"] for g in self.session.group_list()["groups"] if not g["built_in"]]
        dpg.configure_item(self.items["context_group"], items=names)
        if names and self.value("context_group") not in names:
            dpg.set_value(self.items["context_group"], names[0])
        dpg.set_value(self.items["context_from"], f"from {address}")
        _open_at_mouse(self.items["context_menu"])

    def _set_context(self, mode: str) -> None:
        """Apply the menu's choice to the line's code: a range, its routine, the scope, none."""
        dpg.configure_item(self.items["context_menu"], show=False)
        address = self._menu_address
        self.call(lambda: self._apply_context(mode, address))

    def _apply_context(self, mode: str, address: int) -> Any:
        """The session call for one code context choice (see `_set_context`)."""
        session = self.session
        scope = session.machine.symbol_scope(address)
        if mode == "none":  # the context that applies to the line: the narrowest one
            current = session.symbols.context_at(scope, address)
            if current is None:
                raise ValueError("this code has no context")
            return session.context_remove(scope, current.first, current.last)
        group = self.value("context_group")
        if not group:
            raise ValueError("create a group first (Symbols tab)")
        if mode == "scope":
            return session.context_set(group, scope)
        if mode == "routine":
            subs = session.symbol_search(types=["sub"], scope=scope, limit=5000)["entries"]
            starts = [int(entry["address"], 16) for entry in subs]
            first, last = routine_range(address, starts, 0x3FFF if scope == "lower" else 0xFFFF)
        else:
            first, last = address, session._address(self.value("context_to"))  # pylint: disable=protected-access  # the address parser
        return session.context_set(group, scope, first, last)

    def _set_operand(self, row: int, role: str, menu: int | str) -> None:
        """Store the operand decision of the instruction in `row` and close the menu."""
        dpg.configure_item(menu, show=False)
        address = self._row_addresses[row] if row < len(self._row_addresses) else ""
        if address:
            self.call(lambda: self.session.operand_set(address, role))

    def _build_stack(self) -> None:
        """The stack column: SP marked, words up to the top, the top and its source."""
        with section(self.styles, width=STACK_WIDTH, height=LOWER_HEIGHT):
            with dpg.group(horizontal=True):
                dpg.add_text("Stack", color=colours.HEADING)
                dpg.add_text("from SP", color=colours.TEXT_DIM)
            for row in range(STACK_ROWS):
                with dpg.group(horizontal=True, horizontal_spacing=0):
                    self.text(f"stack_marker_{row}", color=colours.HERE)
                    self.text(f"stack_address_{row}", color=colours.TEXT_DIM)
                    self.text(f"stack_value_{row}")
            dpg.add_separator()
            self.text("stack_top")
            self.text("stack_source", color=colours.TEXT_DIM, wrap=STACK_WIDTH - 16)

    # -- actions ---------------------------------------------------------------------------------

    def set_registers(self) -> Any:
        """Apply "name=value" assignments from the register input."""
        return self.session.registers_set(parse_assignments(self.value("register_edit")))

    def run_to(self) -> None:
        """Start a free run that stops at the given address."""
        address = self.session._address(self.value("run_to"))  # pylint: disable=protected-access
        self.loop.run_to(address)

    def _address_changed(self) -> None:
        """A new start address was typed: show it (empty: follow the PC)."""
        self._view_start = None
        self.refresh()

    def _bytes_switched(self) -> None:
        """Show or hide the bytes column (a table column hides with `enabled`, not `show`)."""
        dpg.configure_item(self.items["bytes_column"], enabled=bool(self.value("show_bytes")))

    # -- refreshing ------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Registers, paging tiles, disassembly and stack."""
        session = self.session
        registers = session.registers_get()
        stop = session.last_stop_get().get("stop") or {}
        self._refresh_registers(registers, stop.get("changed", []))
        self._refresh_tiles(session.paging_get()["quarters"], registers["pc"])
        self._refresh_code(registers["pc"])
        self._refresh_stack()

    def _refresh_registers(self, registers: dict[str, int], changed: list[str]) -> None:
        """Values, yellow where the last step changed them; flags; I, R, IM, IFF."""
        items = self.items
        for name, main, main_changed, alternate, alternate_changed in register_pairs(
            registers, changed
        ):
            _set_text(items[f"reg_{name}_main"], main, main_changed)
            _set_text(items[f"reg_{name}_alt"], alternate, alternate_changed)
        for name, value, value_changed in register_words(registers, changed):
            _set_text(items[f"reg_{name}"], value, value_changed)
        for letter, set_ in flag_bits(registers["f"]):
            set_badge(
                self.styles, items[f"flag_{letter}"], letter, "flag_on" if set_ else "flag_off"
            )
        self.set_lines("cpu_state", cpu_state_lines(registers))

    def _refresh_tiles(self, quarters: list[dict[str, Any]], pc: int) -> None:
        """One tile per quarter: ROM tiles tinted, the PC's framed yellow."""
        for number, quarter in enumerate(quarters):
            dpg.set_value(self.items[f"tile_range_{number}"],
                          f"{quarter['start']}-{quarter['end']}")  # fmt: skip
            dpg.set_value(self.items[f"tile_text_{number}"], _tile_text(quarter))
            theme = self.styles.tile(quarter["source"] != "ram", pc >> 14 == number)
            dpg.bind_item_theme(self.items[f"tile_{number}"], theme)

    def _refresh_code(self, pc: int) -> None:
        """The disassembly: from the typed address, or a view that keeps the PC inside."""
        typed = self.value("disassembly_address").strip()
        try:
            if typed:
                start: int | str = typed
            else:
                start = self._follow(pc)
            listing = self.session.disassemble(start, CODE_ROWS)
        except SessionError as error:
            self._show_rows([CodeRow("separator", text=f"error: {error}")])
            return
        rows = disassembly_view(listing, pc, set(self.session.breakpoints.addresses))
        self._view_addresses = [int(r.address, 16) for r in rows if r.kind == "instruction"]
        self._show_rows(rows[:CODE_ROWS])
        self._show_context(pc if not typed or not self._view_addresses else self._view_addresses[0])

    def _show_context(self, address: int) -> None:
        """The line under the listing: the code context of the PC line (or the first line)."""
        scope = self.session.machine.symbol_scope(address)
        found = self.session.symbols.context_at(scope, address)
        context = {"group": found.group, "context": found.text} if found is not None else None
        dpg.set_value(self.items["code_context"], context_text(context))

    def _follow(self, pc: int) -> int:
        """The view's start: kept while the PC is inside it (not too close to its end)."""
        shown = self._view_addresses[: max(1, len(self._view_addresses) - KEEP_BELOW)]
        if self._view_start is None or pc not in shown:
            self._view_start = pc
        return self._view_start

    def _show_rows(self, rows: list[CodeRow]) -> None:
        """Fill the table's rows; bars for the PC row and the separators."""
        items, table = self.items, self.items["code_table"]
        for index in self._highlighted:
            dpg.unhighlight_table_row(table, index)
        self._highlighted = set()
        for index in range(CODE_ROWS):
            row = rows[index] if index < len(rows) else CodeRow("empty")
            self._row_addresses[index] = row.address if row.kind == "instruction" else ""
            texts = _row_texts(row)
            for part, (text, colour) in texts.items():
                item = items[f"code_{part}_{index}"]
                dpg.set_value(item, text)
                if colour is not None:
                    dpg.configure_item(item, color=colour)
            if row.pc or row.kind == "separator":
                background = colours.HERE_BACKGROUND if row.pc else colours.PANEL_DIM
                dpg.highlight_table_row(table, index, (*background, 255))
                self._highlighted.add(index)

    def _refresh_stack(self) -> None:
        """Words from SP up to the top; the top and where it came from."""
        stack = self.session.stack_get(STACK_ROWS)
        words = stack["words"]
        for row in range(STACK_ROWS):
            word = words[row] if row < len(words) else None
            dpg.set_value(self.items[f"stack_marker_{row}"], "SP▶ " if row == 0 else "    ")
            dpg.set_value(
                self.items[f"stack_address_{row}"], f"{word['address']}  " if word else ""
            )
            dpg.set_value(self.items[f"stack_value_{row}"], word["value"] if word else "")
        top = stack["top"]
        dpg.set_value(self.items["stack_top"], f"top &{top}" if top else "top unknown")
        dpg.set_value(self.items["stack_source"], stack["source"])


_ZERO_REGISTERS = dict.fromkeys(
    ("af", "bc", "de", "hl", "af_", "bc_", "de_", "hl_", "ix", "iy", "sp", "pc", "wz"), 0
)


def _open_at_mouse(menu: int | str) -> None:
    """Show a popup window at the mouse (the operand menu)."""
    dpg.configure_item(menu, show=True)
    dpg.set_item_pos(menu, dpg.get_mouse_pos(local=False))


def _set_text(item: int | str, text: str, changed: bool) -> None:
    """A register value: yellow if the last step changed it."""
    dpg.set_value(item, text)
    dpg.configure_item(item, color=colours.HERE if changed else colours.TEXT)


def _tile_text(quarter: dict[str, Any]) -> str:
    """A paging tile's text, short: "lower ROM os6128", "ROM 7 amsdos", "RAM block 2"."""
    text = str(quarter["text"])
    for long, short in (("lower ROM (", "lower ROM "), ("upper ROM ", "ROM ")):
        text = text.replace(long, short)
    return text.replace(".rom)", "").replace("(", "").replace(")", "")


def _row_texts(row: CodeRow) -> dict[str, tuple[str, colours.Colour | None]]:
    """The cell texts and colours of a disassembly row (None: keep the colour)."""
    empty = {part: ("", None) for part in ("gutter", "address", "bytes", "mnemonic", "before",
                                             "name", "after", "comment")}  # fmt: skip
    if row.kind == "separator":
        # in the wide operands column: Dear PyGui clips a cell's text even with no_clip
        return empty | {"before": (row.text, colours.TEXT_DIM)}
    if row.kind == "label":
        colour = colours.name_colour(row.name_type)
        return empty | {"name": (row.text, colour)}  # the operands column (see above)
    if row.kind == "note":  # the owner's comment block (task 9), in the wide operands column
        return empty | {"before": (row.text, colours.TEXT_DIM)}
    if row.kind == "empty":
        return empty
    gutter_colour = colours.HERE if row.pc else colours.ERROR
    name_colour = colours.name_colour(row.name_type)
    return {
        "gutter": (row.gutter, gutter_colour),
        "address": (row.address, colours.TEXT_DIM),
        "bytes": (row.data_bytes, colours.TEXT_OFF),
        "mnemonic": (row.mnemonic, colours.TEXT),
        "before": (f" {row.before}" if row.before else (" " if row.name else ""), colours.TEXT),
        "name": (row.name, name_colour),
        "after": (row.after, colours.TEXT),
        "comment": (f"  ; {row.comment}" if row.comment else "", colours.TEXT_DIM),
    }
