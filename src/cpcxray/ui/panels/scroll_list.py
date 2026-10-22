"""A list of fixed height that scrolls, with a selected row and keys over the list.

Several tabs show long lists in a box of fixed height (symbols, breakpoints, trace lines, disk
files). The agreed behaviour (2026-10-22): the mouse wheel scrolls; a click selects a row; while
the mouse is over the list, the cursor keys, Page Up/Down, Home and End move the selection and
the list follows it - and these keys do not reach the CPC then. A click or Enter also
*activates* the row (e.g. opens a folder in the file selector); moving with keys only selects.

`ListState` is the selection and scroll logic without Dear PyGui (tested headless, NFR-04);
`ScrollList` is the widget: a bordered child window with one selectable per row inside a clipper,
so only the visible rows cost drawing time.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels.colours import Colour

ROW_HEIGHT = 17  # pixels per row with the default font (until the list has measured itself)
LIST_KEYS = ("up", "down", "page_up", "page_down", "home", "end", "enter")


@dataclass
class ListState:
    """Selection and first visible row of a list (no GUI).

    Attributes:
        visible_rows: How many rows the box shows at once.
        count: Number of rows.
        selected: Index of the selected row, or None.
        top: Index of the first visible row.
    """

    visible_rows: int
    count: int = 0
    selected: int | None = None
    top: int = 0

    def set_count(self, count: int) -> None:
        """New number of rows: the selection and the scroll position stay inside the list."""
        self.count = count
        if self.selected is not None and self.selected >= count:
            self.selected = count - 1 if count else None
        self.top = max(0, min(self.top, count - self.visible_rows))

    def select(self, index: int | None) -> None:
        """Select a row (None: no selection) and scroll it into view."""
        if index is not None and not 0 <= index < self.count:
            raise IndexError(f"row {index} not in a list of {self.count}")
        self.selected = index
        self._show_selected()

    def move(self, key: str) -> bool:
        """Move the selection by a key ("up", "down", "page_up", "page_down", "home", "end").

        Without a selection, any key selects the first row. Returns True if the selection
        changed.
        """
        if self.count == 0:
            return False
        before = self.selected
        page = max(1, self.visible_rows - 1)
        current = self.selected if self.selected is not None else -1
        step = {"up": -1, "down": 1, "page_up": -page, "page_down": page}
        if self.selected is None:
            target = 0
        elif key in step:
            target = current + step[key]
        elif key == "home":
            target = 0
        elif key == "end":
            target = self.count - 1
        else:
            raise ValueError(f"unknown list key {key!r}")
        self.select(max(0, min(self.count - 1, target)))
        return self.selected != before

    def _show_selected(self) -> None:
        """Scroll so that the selected row is visible (the least scrolling that does it)."""
        if self.selected is None:
            return
        if self.selected < self.top:
            self.top = self.selected
        elif self.selected >= self.top + self.visible_rows:
            self.top = self.selected - self.visible_rows + 1


type Row = str | tuple[str, Colour | None]


class ScrollList:  # pylint: disable=too-many-instance-attributes  # widget ids, rows, callbacks
    """The list widget (see the module docstring); rows are text, optionally coloured."""

    def __init__(  # pylint: disable=too-many-arguments  # size, two callbacks, look
        self,
        visible_rows: int,
        width: int = -1,
        on_select: Callable[[int], None] | None = None,
        on_activate: Callable[[int, bool], None] | None = None,
        themes: Callable[[Colour | None, bool], int | str] | None = None,
    ) -> None:
        """Create the list in the current container.

        Args:
            visible_rows: Height of the box in rows.
            width: Width in pixels (-1: the container's width).
            on_select: Called with the row index when the selection changes (click or key).
            on_activate: Called with the row index and True for Enter, False for a click
                (after `on_select`).
            themes: Returns the Dear PyGui theme for a row's text colour and selection state
                (widgets.Styles.row_theme); None uses the default look.
        """
        self.state = ListState(visible_rows)
        self.on_select = on_select
        self.on_activate = on_activate
        self._themes = themes
        self._rows: list[Row] = []
        self._items: list[int | str] = []
        self._row_height = float(ROW_HEIGHT)
        self._scroll_pending = False  # scrolled before the rows were laid out (`settle`)
        self.box = dpg.add_child_window(
            height=visible_rows * ROW_HEIGHT + 8, width=width, border=True
        )
        self._clipper = dpg.add_clipper(parent=self.box)

    @property
    def selected(self) -> int | None:
        """Index of the selected row, or None."""
        return self.state.selected

    def set_rows(self, rows: Sequence[Row]) -> None:
        """Replace the rows (the selection stays at its index if that still exists)."""
        self._rows = list(rows)
        self.state.set_count(len(self._rows))
        dpg.delete_item(self._clipper, children_only=True)
        self._items = [
            dpg.add_selectable(
                label=_row_text(row),
                parent=self._clipper,
                user_data=index,
                callback=self._clicked,
                default_value=index == self.state.selected,
            )
            for index, row in enumerate(self._rows)
        ]
        for index in range(len(self._rows)):
            self._style(index)

    def row(self, index: int) -> str:
        """The text of a row."""
        return _row_text(self._rows[index])

    def select(self, index: int | None, notify: bool = False) -> None:
        """Select a row, scroll it into view and optionally tell `on_select`."""
        before = self.state.selected
        self.state.select(index)
        self._restyle(before)
        self._scroll_to_selection()
        if notify and index is not None and self.on_select is not None:
            self.on_select(index)

    def hovered(self) -> bool:
        """True while the mouse is over the list (then its keys work, not the CPC's)."""
        return bool(dpg.is_item_hovered(self.box))

    def handle_key(self, key: str) -> None:
        """Apply a list key (`LIST_KEYS`): move the selection, or activate it with "enter"."""
        if key == "enter":
            if self.state.selected is not None and self.on_activate is not None:
                self.on_activate(self.state.selected, True)
            return
        before = self.state.selected
        self.state.top = round(dpg.get_y_scroll(self.box) / self._row_height)  # wheel moved it
        if self.state.move(key):
            self._restyle(before)
            self._scroll_to_selection()
            if self.on_select is not None and self.state.selected is not None:
                self.on_select(self.state.selected)

    def _clicked(self, _sender: int | str, _value: bool, index: int) -> None:
        """A row was clicked: it becomes the selected one and is activated."""
        self.select(index, notify=True)
        if self.on_activate is not None:
            self.on_activate(index, False)

    def _restyle(self, before: int | None) -> None:
        """Show the selection on the new row and take it off the old one."""
        for index in {before, self.state.selected} - {None}:
            assert index is not None
            if index < len(self._items):
                dpg.set_value(self._items[index], index == self.state.selected)
                self._style(index)

    def _style(self, index: int) -> None:
        """Bind the theme for a row's colour and selection."""
        if self._themes is None:
            return
        row = self._rows[index]
        colour = row[1] if isinstance(row, tuple) else None
        dpg.bind_item_theme(self._items[index], self._themes(colour, index == self.state.selected))

    def _scroll_to_selection(self) -> None:
        """Scroll the box so the selected row is visible (rows measured from the scroll range)."""
        if self.state.selected is None:
            return
        scroll_max = dpg.get_y_scroll_max(self.box)
        hidden_rows = self.state.count - self.state.visible_rows
        if scroll_max > 0 and hidden_rows > 0:
            self._row_height = scroll_max / hidden_rows
        self._scroll_pending = scroll_max <= 0 < hidden_rows  # new rows: not measured yet
        self.state.top = min(self.state.top, max(0, hidden_rows))
        dpg.set_y_scroll(self.box, self.state.top * self._row_height)

    def settle(self) -> None:
        """Every frame: scroll again once new rows are laid out (their height is known then).

        Right after `set_rows` the box has no scroll range yet, so a selection far down
        (e.g. the entry the Symbols tab keeps selected) was scrolled to by a guessed row height.
        """
        if self._scroll_pending and dpg.get_y_scroll_max(self.box) > 0:
            self._scroll_to_selection()


def _row_text(row: Row) -> str:
    """The text of a row given as text or as (text, colour)."""
    return row[0] if isinstance(row, tuple) else row
