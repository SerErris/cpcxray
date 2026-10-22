"""Debugger panels of the GUI: the tab bar (design.md §6.12, §6.13; FR-DBG-02..12).

A tab bar next to the screen, one module per tab:

| Tab         | Module         | Shows / does                                                   |
|-------------|----------------|----------------------------------------------------------------|
| CPU         | cpu.py         | registers (editable), paging per 16 KB, disassembly, run to    |
| Memory      | memory.py      | hex dump through a view, editing, paging, known variables      |
| Devices     | devices.py     | Gate Array, CRTC, SystemIO, ROM select, FDC                    |
| Breakpoints | breakpoints.py | list; add any type with location qualifier; enable, remove     |
| Trace       | trace.py       | instruction trace on/off and its last lines                    |
| Bus trace   | bus_trace.py   | bus access trace on/off (access type filter) and its lines     |
| Symbols     | symbols.py     | search (scroll list), add "address label ; comment"            |
| Disks       | disks.py       | drives, insert/eject, draft, Save / Save as, catalogue         |
| Files       | files.py       | .sna save/load, analysis save, source generate + verify        |

Shared pieces: base.py (`Tab`, the panel context), widgets.py (themes and building blocks),
colours.py (the colour table), scroll_list.py, file_selector.py; text.py formats headless.

While the machine runs freely the panels stay frozen; they are filled once when the window opens
and when a tab is opened, refreshed (the visible tab) when the machine stops - pause,
breakpoint, step - and after every panel action. A tab with `live_seconds` (Devices: once per
second, owner 2026-10-22) is also refreshed that often while running, when visible. PC keys go
to a focused text input, to a list under the mouse (its keys) or to an open file selector
instead of the CPC.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import dearpygui.dearpygui as dpg

from cpcxray.session.session import Session
from cpcxray.ui.emulation import EmulationLoop
from cpcxray.ui.panels.base import PANEL_WIDTH, PanelContext, Tab
from cpcxray.ui.panels.breakpoints import BreakpointsTab
from cpcxray.ui.panels.bus_trace import BusTraceTab
from cpcxray.ui.panels.cpu import CpuTab
from cpcxray.ui.panels.devices import DevicesTab
from cpcxray.ui.panels.disks import DisksTab
from cpcxray.ui.panels.files import FilesTab
from cpcxray.ui.panels.memory import MemoryTab
from cpcxray.ui.panels.scroll_list import ScrollList
from cpcxray.ui.panels.symbols import SymbolsTab
from cpcxray.ui.panels.trace import TraceTab
from cpcxray.ui.panels.widgets import Styles

__all__ = ["PANEL_WIDTH", "DebuggerPanels"]

TAB_CLASSES: tuple[type[Tab], ...] = (
    CpuTab, MemoryTab, DevicesTab, BreakpointsTab, TraceTab, BusTraceTab, SymbolsTab, DisksTab,
    FilesTab,
)  # fmt: skip


# Dear PyGui 2.3 still defines mvKey_Prior/Next (Page Up/Down) with old values (266, 267) that
# are no ImGui keys: it reports them as held all the time and "pressed" on other input such as a
# mouse click, which sent every list back to its first row. These are the ImGui key codes.
IMGUI_KEY_PAGE_UP = 517
IMGUI_KEY_PAGE_DOWN = 518


def list_key_codes() -> dict[str, int]:
    """Dear PyGui key codes of the list keys (scroll_list.LIST_KEYS)."""
    return {
        "up": dpg.mvKey_Up, "down": dpg.mvKey_Down, "page_up": IMGUI_KEY_PAGE_UP,
        "page_down": IMGUI_KEY_PAGE_DOWN, "home": dpg.mvKey_Home, "end": dpg.mvKey_End,
        "enter": dpg.mvKey_Return,
    }  # fmt: skip


class DebuggerPanels:
    """The tab bar with all debugger tabs."""

    def __init__(self, session: Session, loop: EmulationLoop) -> None:
        """Create the panels for a session driven by `loop` (build() creates the widgets)."""
        self.session = session
        self.loop = loop
        self.context = PanelContext(session, loop, refresh_all=lambda: self.refresh(force=True),
                                    show_tab=self.show_tab)  # fmt: skip
        self.items = self.context.items
        self.tabs = {tab_class.title: tab_class(self.context) for tab_class in TAB_CLASSES}
        self._tab_ids: dict[int | str, Tab] = {}
        self._live_refreshed = 0.0  # when the visible tab was last refreshed while running

    def build(self, styles: Styles | None = None) -> None:
        """Create the tab bar in the current container (inside the Dear PyGui context).

        Args:
            styles: The GUI themes (the app creates them once); None creates them here.
        """
        self.context.styles = styles or Styles()
        # Dear PyGui 2.3 stretches the first tab over the bar's free width (a width left over
        # for the next item) unless the bar sits in a group with an explicit width
        with dpg.group(width=-1), dpg.tab_bar(callback=self._tab_changed) as tab_bar:
            self.items["tab_bar"] = tab_bar
            for tab in self.tabs.values():
                with dpg.tab(label=tab.title) as tab_id:
                    tab.build()
                self._tab_ids[tab_id] = tab

    def show_tab(self, title: str) -> None:
        """Open the tab with this title (and fill it)."""
        for tab_id, tab in self._tab_ids.items():
            if tab.title == title:
                dpg.set_value(self.items["tab_bar"], tab_id)
                tab.refresh()

    def typing(self) -> bool:
        """True while PC keys belong to the panels, not the CPC.

        That is while a text input has the focus, the mouse is over a scroll list (its keys),
        or a file selector is open.
        """
        context = self.context
        return (
            any(dpg.is_item_focused(item) for item in context.inputs)
            or any(scroll_list.hovered() for scroll_list in context.lists)
            or any(selector.is_open() for selector in context.selectors)
        )

    def handle_keys(self) -> None:
        """Every frame: the list keys go to the list under the mouse (an open dialog's first)."""
        target = self._list_under_mouse()
        if target is None:
            return
        for name, code in list_key_codes().items():
            if dpg.is_key_pressed(code):
                target.handle_key(name)

    def _list_under_mouse(self) -> ScrollList | None:
        """The scroll list under the mouse, if any (an open file selector's list first)."""
        candidates = [s.list for s in self.context.selectors if s.is_open() and s.list]
        if not candidates:
            candidates = self.context.lists
        return next((scroll_list for scroll_list in candidates if scroll_list.hovered()), None)

    def _tab_changed(self) -> None:
        """Fill a tab when it is opened, also while running (a one-off: no running cost)."""
        tab = self._tab_ids.get(dpg.get_value(self.items["tab_bar"]))
        if tab is not None:
            tab.refresh()

    def refresh(self, force: bool = False) -> None:
        """Refresh the visible tab while stopped (all tabs with force=True after an action)."""
        if self.loop.running and not force:
            self._refresh_live()  # the others stay frozen (no use, and it costs time)
            return
        refreshers: list[Callable[[], None]]
        if force:
            refreshers = [tab.refresh for tab in self.tabs.values()]
        else:
            active = self._tab_ids.get(dpg.get_value(self.items["tab_bar"]))
            refreshers = [active.refresh] if active is not None else []
        for refresher in refreshers:
            refresher()

    def _refresh_live(self) -> None:
        """While running: refresh the visible tab if it is live and its interval has passed."""
        active = self._tab_ids.get(dpg.get_value(self.items["tab_bar"]))
        if active is None or active.live_seconds is None:
            return
        now = time.monotonic()
        if now - self._live_refreshed >= active.live_seconds:
            self._live_refreshed = now
            active.refresh()
