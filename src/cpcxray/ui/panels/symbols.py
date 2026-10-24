"""Symbols tab: every name, its detail, your symbols and variables, the annotation file (5g).

Layout agreed with the owner on 2026-10-22 (design canvas, artboard "Symbols 3"), top to
bottom:

* Search: text (label, comment or a variable's description), scope, "only yours"; type chips
  with counts that show or hide each type (FW, VAR, var, sub, jump, data); a legend line.
* A compact list (type, address, label in the type's colour, ✎ yours / ◇ from the analysis)
  beside the selected entry's detail (framed yellow): address and scope, where the name comes
  from, the machines it exists on, the instruction there; your label (with its type), your
  comment and your comment blocks before / after the line (task 9: several lines each, e.g. a
  routine header) to edit - Save / Remove yours; Show in CPU, Show in Memory, Breakpoint here.
* Your symbols: the type chips var / sub / jump / data; sub, jump, data take address, name,
  scope and comment; var takes name, size, base (absolute, or the data area of ROM n) plus
  offset and comment, and shows where it is now.
* The annotation file: path, your entries, saved or not, Load, Save.

Groups (task 10, D-43, D-44): a group filter in the search; the list shows the owner's group of
an entry ("[vdos]"); the detail editor and the form have a group choice for the owner's
variables and global labels; a Groups panel adds, describes, renames, moves and removes the
owner's groups.
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.debugger.symbols import OWNER_TYPES
from cpcxray.session.errors import SessionError
from cpcxray.ui.panels import colours
from cpcxray.ui.panels.base import TAB_WIDTH, Tab
from cpcxray.ui.panels.scroll_list import ScrollList
from cpcxray.ui.panels.symbol_text import (
    BASES,
    MARKS,
    SCOPES,
    UNGROUPED,
    counts_label,
    detail_rows,
    entry_key,
    group_choices,
    group_filter_choices,
    group_filter_value,
    group_line,
    groupable,
    now_text,
    scope_value,
    symbol_row,
    variable_base,
)
from cpcxray.ui.panels.widgets import BOX_GAP, Chips, badge, section, set_badge

LIST_WIDTH = 252  # type, address, the mark and a label of 20 characters
LIST_ROWS = 17
LOWER_HEIGHT = 340  # the list and the detail beside it end on the same line
DETAIL_ROWS = 5
MAX_ENTRIES = 5000
FORM_TYPES = ("var", *OWNER_TYPES)
ANNOTATIONS = "project/annotations.txt"
CODE_TYPES = ("sub", "jump")  # shown in the CPU tab; the others in the Memory tab
BLOCK_HEIGHT = 46  # a comment block field: three lines


class SymbolsTab(Tab):  # pylint: disable=too-many-instance-attributes  # filters, list, form
    """Search, list and detail, your symbols, the annotation file."""

    title = "Symbols"
    message = "symbol_message"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Create the tab (the widgets come with `build`)."""
        super().__init__(*args, **kwargs)
        self.entries: list[dict[str, Any]] = []
        self.list: ScrollList | None = None
        self.type_chips: Chips | None = None
        self.form_chips: Chips | None = None
        self._shown: Any = None  # the search result the list shows
        self._selected_key: tuple[str, str, str, str] | None = None
        self._detail_shown: Any = None
        self._group_names: list[str] = []  # the owner's groups the choices show

    # -- building --------------------------------------------------------------------------------

    def build(self) -> None:
        """Create the four parts (see the module docstring)."""
        self._build_search()
        with dpg.group(horizontal=True, horizontal_spacing=BOX_GAP) as row:
            dpg.bind_item_theme(row, self.styles.box_row)
            with section(self.styles, width=LIST_WIDTH, height=LOWER_HEIGHT):
                self.text("symbols_total", color=colours.TEXT_DIM)
                self.list = self.scroll_list(LIST_ROWS, on_select=self._selected)
            with section(self.styles, width=TAB_WIDTH - LIST_WIDTH - BOX_GAP,
                         height=LOWER_HEIGHT, style="here"):  # fmt: skip
                self._build_detail()
        self._build_form()
        self._build_groups()
        self._build_file()
        self.text("symbol_message", color=colours.TEXT_DIM)

    def _build_search(self) -> None:
        """Search text, scope, only yours; the type chips with counts; the legend."""
        with section(self.styles, width=TAB_WIDTH):
            with dpg.group(horizontal=True):
                self.input("symbol_search", "search names and comments", 210,
                           callback=self.refresh)  # fmt: skip
                dpg.add_text("scope", color=colours.TEXT_DIM)
                self.items["symbol_scope"] = dpg.add_combo(
                    SCOPES, default_value="all", width=80, callback=self.refresh
                )
                dpg.add_text("group", color=colours.TEXT_DIM)
                self.items["symbol_group_filter"] = dpg.add_combo(
                    group_filter_choices([]), default_value="all", width=90, callback=self.refresh
                )
                self.items["symbol_yours"] = dpg.add_checkbox(
                    label="only yours", callback=self.refresh
                )
            self.type_chips = Chips(
                self.styles, colours.SYMBOL_TYPES, colours.SYMBOL_TYPES, multiple=True,
                on_change=lambda _types: self.refresh(), chip_colours=colours.SYMBOL_TYPE_COLOURS,
            )  # fmt: skip
            with dpg.group(horizontal=True):
                legend = (("FW", "firmware"), ("VAR", "OS/AMSDOS variable"),
                          ("var", "your variable"), ("sub", "subroutine"), ("jump", "jump"),
                          ("data", "data"))  # fmt: skip
                for symbol_type, meaning in legend:
                    dpg.add_text(meaning, color=colours.name_colour(symbol_type))
                dpg.add_text(f"{MARKS['yours']} yours  {MARKS['analysis']} from analysis",
                             color=colours.TEXT_DIM)  # fmt: skip

    def _build_detail(self) -> None:
        """The selected entry: title, key/value lines, your label and comment, the buttons."""
        with dpg.group(horizontal=True):
            self.text("symbol_title")
            self.items["symbol_badge"] = badge(self.styles, "", show=False)
        for number in range(DETAIL_ROWS):
            with dpg.group(horizontal=True):
                self.text(f"symbol_key_{number}", color=colours.TEXT_DIM)
                self.text(f"symbol_value_{number}", wrap=TAB_WIDTH - LIST_WIDTH - 90)
        dpg.add_separator()
        with dpg.group(horizontal=True):
            dpg.add_text("your label  ", color=colours.TEXT_DIM)
            self.input("symbol_edit_label", "label", 170)
            self.items["symbol_edit_type"] = dpg.add_combo(OWNER_TYPES, default_value="sub",
                                                           width=60)  # fmt: skip
            self.items["symbol_edit_group"] = dpg.add_combo(group_choices([]), width=70,
                                                            default_value=UNGROUPED)  # fmt: skip
        with dpg.group(horizontal=True):
            dpg.add_text("your comment", color=colours.TEXT_DIM)
            self.input("symbol_edit_comment", "comment", 236)
        for position in ("before", "after"):
            with dpg.group(horizontal=True):
                dpg.add_text(f"{position:<12}", color=colours.TEXT_DIM)
                self.input(f"symbol_edit_{position}", f"comment lines {position} the line", 236,
                           multiline=True, height=BLOCK_HEIGHT, tab_input=False)  # fmt: skip
        with dpg.group(horizontal=True):
            self.items["symbol_save"] = self.button("Save", self.save_selected)
            self.items["symbol_remove"] = self.button("Remove yours", self.remove_selected)
        with dpg.group(horizontal=True):
            self.button("Show in CPU", lambda: self.show_selected("CPU"))
            self.button("Show in Memory", lambda: self.show_selected("Memory"))
            self.button("Breakpoint here", self.breakpoint_selected)

    def _build_form(self) -> None:
        """Your symbols: type chips; the fields of a label or of a variable."""
        with section(self.styles, "Your symbols", width=TAB_WIDTH):
            self.form_chips = Chips(
                self.styles, FORM_TYPES, ("sub",), on_change=lambda _types: self._form_type(),
                chip_colours=colours.SYMBOL_TYPE_COLOURS,
            )  # fmt: skip
            with dpg.group(horizontal=True) as label_fields:
                self.items["form_label_fields"] = label_fields
                self.input("form_address", "address", 70)
                self.input("form_name", "name", 170)
                dpg.add_text("scope", color=colours.TEXT_DIM)
                self.items["form_scope"] = dpg.add_combo(SCOPES[1:], default_value="global",
                                                         width=80)  # fmt: skip
            with dpg.group(horizontal=True, show=False) as variable_fields:
                self.items["form_variable_fields"] = variable_fields
                self.input("form_var_name", "name", 170)
                self.input("form_size", "size", 50, callback=self._update_now)
                dpg.add_text("base", color=colours.TEXT_DIM)
                self.items["form_base"] = dpg.add_combo(
                    BASES, default_value="absolute", width=90, callback=self._update_now
                )
                dpg.add_text("+", color=colours.TEXT_DIM)
                self.input("form_offset", "offset", 60, callback=self._update_now)
                self.text("form_now", color=colours.TEXT_DIM)
            with dpg.group(horizontal=True):
                self.input("form_comment", "comment", 380)
                dpg.add_text("group", color=colours.TEXT_DIM)
                self.items["form_group"] = dpg.add_combo(group_choices([]), width=80,
                                                         default_value=UNGROUPED)  # fmt: skip
                self.button("Add", self.add_from_form, width=60)

    def _build_groups(self) -> None:
        """Your groups (D-43): pick one, its name and description, add / rename / move / remove."""
        with section(self.styles, "Groups", width=TAB_WIDTH):
            with dpg.group(horizontal=True):
                self.items["groups_pick"] = dpg.add_combo([], width=90, callback=self._group_picked)
                self.input("group_name", "name", 90)
                self.input("group_description", "description", 300)
                self.button("Add / save", self.save_group)
            with dpg.group(horizontal=True):
                self.button("Rename", self.rename_group)
                self.button("Up", lambda: self.move_group(-1))
                self.button("Down", lambda: self.move_group(1))
                self.button("Remove", self.remove_group)
                self.items["group_force"] = dpg.add_checkbox(label="force")
            self.text("groups_lines", color=colours.TEXT_DIM, wrap=TAB_WIDTH - 20)

    def _build_file(self) -> None:
        """The annotation file: path, entries, saved or not, Load and Save."""
        session = self.session
        with (
            section(self.styles, width=TAB_WIDTH),
            self.path_field("symbols_path", 230, "project", (".txt",), default_value=ANNOTATIONS),
        ):
            self.text("symbols_entries", color=colours.TEXT_DIM)
            self.items["symbols_saved"] = badge(self.styles, "saved", "ok")
            self.button("Load", lambda: session.symbols_load(self.value("symbols_path")))
            self.button("Save", lambda: session.symbols_save(self.value("symbols_path")))

    # -- actions ---------------------------------------------------------------------------------

    def _selected(self, index: int) -> None:
        """An entry was selected in the list: remember it, show its detail."""
        self._selected_key = entry_key(self.entries[index])
        self._show_detail(self.entries[index])

    def _selected_entry(self) -> dict[str, Any]:
        """The selected entry (ValueError without one: shown in the message line)."""
        assert self.list is not None
        if self.list.selected is None:
            raise ValueError("select an entry in the list first")
        return self.entries[self.list.selected]

    def save_selected(self) -> Any:
        """Store your label / comments of the selected entry (a variable of yours: change it).

        The comment blocks are stored as they are in their fields: an empty field removes a
        block.
        """
        entry = self._selected_entry()
        label = self.value("symbol_edit_label").strip() or None
        comment = self.value("symbol_edit_comment").strip() or None
        if entry["type"] == "var" and entry["mark"] == "yours":
            own = self._detail_shown[1]["variable"]
            return self.session.variable_add(
                label or entry["label"], own["size"], own["base"], comment,
                replaces=entry["label"], group=self._chosen_group("symbol_edit_group"),
            )  # fmt: skip
        before, after = self.value("symbol_edit_before"), self.value("symbol_edit_after")
        if label is None and comment is None and not before.strip() and not after.strip():
            raise ValueError("give a label, a comment or a comment block")
        symbol_type = self.value("symbol_edit_type") if label else None
        group = self._chosen_group("symbol_edit_group") if entry["scope"] == "" else None
        old_group = (entry.get("group") or "") if groupable(entry) else group
        if group is not None and old_group != group:  # moved to another group (D-44)
            self.session.symbol_remove(entry["address"], "", group=old_group)
        result = self.session.symbol_add(entry["address"], label, comment, entry["scope"],
                                         symbol_type, before=before, after=after,
                                         group=group)  # fmt: skip
        if label:
            self._selected_key = (result["type"], entry["address"], entry["scope"], label)
        return result

    def remove_selected(self) -> Any:
        """Remove your symbol (or variable) of the selected entry; a firmware name stays."""
        entry = self._selected_entry()
        if entry["type"] == "var" and entry["mark"] == "yours":
            return self.session.variable_remove(entry["label"])
        group = (entry.get("group") or "") if entry["scope"] == "" else None
        return self.session.symbol_remove(entry["address"], entry["scope"], group)

    def show_selected(self, tab: str) -> None:
        """Show the selected entry's address in the CPU or the Memory tab."""
        entry = self._selected_entry()
        name = "disassembly_address" if tab == "CPU" else "memory_address"
        dpg.set_value(self.items[name], entry["address"])
        self.context.show_tab(tab)

    def breakpoint_selected(self) -> Any:
        """A breakpoint on the selected entry: PC for code, access for data and variables."""
        entry = self._selected_entry()
        if entry["type"] in ("VAR", "var"):
            return self.session.breakpoint_add(entry["label"], "access")
        kind = "pc" if entry["type"] in ("FW", *CODE_TYPES, None) else "access"
        return self.session.breakpoint_add(entry["address"], kind)

    def add_from_form(self) -> Any:
        """Add the form's label or variable."""
        assert self.form_chips is not None
        form_type = self.form_chips.selected[0]
        comment = self.value("form_comment").strip() or None
        if form_type == "var":
            size_text = self.value("form_size").strip()
            if not size_text.isdigit():
                raise ValueError("the size is a number of bytes")
            base = variable_base(self.value("form_base"), self.value("form_offset"))
            return self.session.variable_add(self.value("form_var_name").strip(), int(size_text),
                                             base, comment,
                                             group=self._chosen_group("form_group"))  # fmt: skip
        scope = scope_value(self.value("form_scope")) or ""
        group = self._chosen_group("form_group") if not scope else None
        return self.session.symbol_add(self.value("form_address").strip(),
                                       self.value("form_name").strip() or None, comment, scope,
                                       form_type, group=group)  # fmt: skip

    def _chosen_group(self, name: str) -> str:
        """A group choice as the session takes it: "" for no group, else the name."""
        choice = self.value(name)
        return "" if choice in (UNGROUPED, "") else choice

    # -- groups (D-43) ---------------------------------------------------------------------------

    def _group_picked(self) -> None:
        """A group was picked: its name and description into the fields."""
        name = self.value("groups_pick")
        groups = self.session.group_list()["groups"]
        group = next((g for g in groups if g["name"] == name), None)
        dpg.set_value(self.items["group_name"], name)
        dpg.set_value(self.items["group_description"], (group or {}).get("description") or "")

    def save_group(self) -> Any:
        """Add the group of the name field, or change its description."""
        name = self.value("group_name").strip()
        if not name:
            raise ValueError("give the group a name")
        return self.session.group_add(name, self.value("group_description").strip() or None)

    def rename_group(self) -> Any:
        """Rename the picked group to the name field."""
        old, new = self.value("groups_pick"), self.value("group_name").strip()
        if not old or not new or old == new:
            raise ValueError("pick a group and type its new name")
        result = self.session.group_rename(old, new)
        dpg.set_value(self.items["groups_pick"], new)
        return result

    def move_group(self, step: int) -> Any:
        """Move the picked group up (-1) or down (+1) in the order."""
        name = self.value("groups_pick")
        if name not in self._group_names:
            raise ValueError("pick a group")
        position = self._group_names.index(name) + step
        if not 0 <= position < len(self._group_names):
            raise ValueError("the group is at the end already")
        return self.session.group_add(name, None, position)

    def remove_group(self) -> Any:
        """Remove the picked group (with "force": its items become ungrouped)."""
        name = self.value("groups_pick")
        if not name:
            raise ValueError("pick a group")
        return self.session.group_remove(name, bool(self.value("group_force")))

    def _refresh_groups(self) -> None:
        """The group choices everywhere and the Groups panel's lines (when the groups changed)."""
        groups = self.session.group_list()["groups"]
        names = [g["name"] for g in groups if not g["built_in"]]
        dpg.set_value(self.items["groups_lines"], "\n".join(group_line(g) for g in groups))
        if names == self._group_names:
            return
        self._group_names = names
        dpg.configure_item(self.items["groups_pick"], items=names)
        dpg.configure_item(self.items["symbol_group_filter"], items=group_filter_choices(names))
        for name in ("symbol_edit_group", "form_group"):
            dpg.configure_item(self.items[name], items=group_choices(names))

    def _form_type(self) -> None:
        """The form's type changed: a variable's fields or a label's."""
        assert self.form_chips is not None
        is_variable = self.form_chips.selected[0] == "var"
        dpg.configure_item(self.items["form_variable_fields"], show=is_variable)
        dpg.configure_item(self.items["form_label_fields"], show=not is_variable)
        self._update_now()

    def _update_now(self) -> None:
        """Where the form's variable is now ("→ now at A612-A621")."""
        size_text = self.value("form_size").strip()
        size = int(size_text) if size_text.isdigit() and int(size_text) > 0 else 1
        try:
            where = self.session.variable_locate(
                variable_base(self.value("form_base"), self.value("form_offset")), size
            )
            text = now_text(where)
        except SessionError:
            text = ""
        dpg.set_value(self.items["form_now"], text)

    # -- refreshing ------------------------------------------------------------------------------

    def refresh(self) -> None:
        """The list (rebuilt when the search result changed), the detail, the file box."""
        assert self.list is not None and self.type_chips is not None
        result = self.session.symbol_search(
            self.value("symbol_search"), types=list(self.type_chips.selected),
            scope=scope_value(self.value("symbol_scope")),
            only_yours=bool(self.value("symbol_yours")), limit=MAX_ENTRIES,
            group=group_filter_value(self.value("symbol_group_filter")),
        )  # fmt: skip
        for symbol_type in colours.SYMBOL_TYPES:
            self.type_chips.set_label(symbol_type, counts_label(symbol_type, result["counts"]))
        if result != self._shown:
            self._shown = result
            self.entries = result["entries"]
            self.list.set_rows([(symbol_row(e), colours.name_colour(e["type"]))
                                for e in self.entries])  # fmt: skip
            keys = [entry_key(entry) for entry in self.entries]
            index = keys.index(self._selected_key) if self._selected_key in keys else None
            self.list.select(index)
            dpg.set_value(self.items["symbols_total"], f"{result['total']} names")
            self._detail_shown = None
        selected = self.list.selected
        self._show_detail(self.entries[selected] if selected is not None else None)
        self._refresh_file()
        self._refresh_groups()
        if self.form_chips is not None and self.form_chips.selected[0] == "var":
            self._update_now()

    def _show_detail(self, entry: dict[str, Any] | None) -> None:
        """Fill the detail panel (None: nothing selected)."""
        key = entry_key(entry) if entry is not None else None
        if self._detail_shown is not None and self._detail_shown[0] == key:
            return
        items = self.items
        rows: list[tuple[str, str]] = []
        detail: dict[str, Any] = {}
        if entry is None:
            dpg.set_value(items["symbol_title"], "select a name")
            dpg.configure_item(items["symbol_title"], color=colours.TEXT_DIM)
            dpg.configure_item(items["symbol_badge"], show=False)
        else:
            group = (
                (entry.get("group") or "") if groupable(entry) and entry["scope"] == "" else None
            )
            detail = self.session.symbol_detail(entry["address"], entry["scope"], entry["label"],
                                                group=group)  # fmt: skip
            rows = detail_rows(detail, entry)
            dpg.set_value(items["symbol_title"], entry["label"] or f"comment at {entry['address']}")
            dpg.configure_item(items["symbol_title"], color=colours.name_colour(entry["type"]))
            set_badge(self.styles, items["symbol_badge"], entry["type"] or "note", "plain")
            dpg.configure_item(items["symbol_badge"], show=True)
        self._detail_shown = (key, detail)
        for number in range(DETAIL_ROWS):
            label, value = rows[number] if number < len(rows) else ("", "")
            dpg.set_value(items[f"symbol_key_{number}"], f"{label:<9}" if label else "")
            dpg.set_value(items[f"symbol_value_{number}"], value)
        self._fill_edit_fields(entry, detail)

    def _fill_edit_fields(self, entry: dict[str, Any] | None, detail: dict[str, Any]) -> None:
        """Your label, type, comment and comment blocks of the entry, ready to change."""
        label, comment, symbol_type, before, after = "", "", "sub", "", ""
        yours = detail.get("yours")
        variable = detail.get("variable")
        if entry is not None and entry["type"] == "var" and entry["mark"] == "yours":
            label, comment = entry["label"], (variable or {}).get("description", "")
        elif yours is not None:
            label, comment = yours["label"] or "", yours["comment"] or ""
            symbol_type = yours["type"] or "sub"
            before, after = yours.get("before") or "", yours.get("after") or ""
        elif entry is not None and entry["mark"] == "analysis":
            label, symbol_type = entry["label"], entry["type"]
        dpg.set_value(self.items["symbol_edit_label"], label)
        dpg.set_value(self.items["symbol_edit_comment"], comment)
        dpg.set_value(self.items["symbol_edit_type"], symbol_type)
        dpg.set_value(self.items["symbol_edit_before"], before)
        dpg.set_value(self.items["symbol_edit_after"], after)
        dpg.set_value(self.items["symbol_edit_group"],
                      (entry or {}).get("group") or UNGROUPED)  # fmt: skip
        dpg.configure_item(self.items["symbol_edit_group"], show=groupable(entry))
        is_variable = entry is not None and entry["type"] in ("VAR", "var")
        dpg.configure_item(self.items["symbol_edit_type"], show=not is_variable)
        for position in ("before", "after"):  # a variable has no line to comment around
            dpg.configure_item(self.items[f"symbol_edit_{position}"], enabled=not is_variable)
        removable = entry is not None and entry["mark"] == "yours"
        dpg.configure_item(self.items["symbol_remove"], enabled=removable)
        dpg.bind_item_theme(self.items["symbol_remove"], self.styles.plain_button if removable
                            else self.styles.disabled_button)  # fmt: skip

    def _refresh_file(self) -> None:
        """Your entries and whether they are saved."""
        status = self.session.symbols_status(self.value("symbols_path"))
        count = status["symbols"] + status["variables"] + status["operands"]
        dpg.set_value(self.items["symbols_entries"], f"{count} entries of yours")
        if status["modified"]:
            set_badge(self.styles, self.items["symbols_saved"], "not saved", "changed")
        else:
            set_badge(self.styles, self.items["symbols_saved"], "saved", "ok")
