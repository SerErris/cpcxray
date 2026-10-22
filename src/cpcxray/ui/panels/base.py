"""What every debugger tab shares: the panel context and the `Tab` base class.

Each tab is its own module (ui/panels/cpu.py, memory.py, ...) with a `Tab` subclass that builds
its widgets once (`build`) and fills them from `Session` calls (`refresh`). The tabs share one
`PanelContext`: the session, the emulation loop, the GUI themes, the item ids by name (one
dictionary for all tabs, so names are unique across tabs), the text inputs and lists that take
the PC keys while focused or hovered, and the refresh of all tabs after an action.

Every action is a `Session` call (the same as the MCP tool, D-27); `Tab.call` shows its error
(or "ok") in the tab's message line.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.session.errors import SessionError
from cpcxray.session.session import Session
from cpcxray.ui.emulation import EmulationLoop
from cpcxray.ui.panels import widgets
from cpcxray.ui.panels.file_selector import FileSelector
from cpcxray.ui.panels.scroll_list import ScrollList
from cpcxray.ui.panels.widgets import Styles

PANEL_WIDTH = 620
TAB_WIDTH = 614  # boxes that span a tab (the Devices tab's two columns and the gap between)


@dataclass
class PanelContext:  # pylint: disable=too-many-instance-attributes  # shared state record
    """State shared by all tabs (see the module docstring).

    Attributes:
        session: The session the panels show and drive.
        loop: The emulation loop (run, step, stop).
        styles: The GUI themes (set by `DebuggerPanels.build`, inside the Dear PyGui context).
        items: Widget ids by name, for all tabs.
        inputs: Text inputs: while one has the focus, PC keys go to it, not to the CPC.
        lists: Scroll lists: while the mouse is over one, the list keys go to it.
        selectors: File selectors: while one is open, PC keys stay away from the CPC.
        refresh_all: Refreshes every tab (after an action).
        show_tab: Opens a tab by its title (e.g. "show this address in the CPU tab").
    """

    session: Session
    loop: EmulationLoop
    styles: Styles | None = None
    items: dict[str, int | str] = field(default_factory=dict)
    inputs: list[int | str] = field(default_factory=list)
    lists: list[ScrollList] = field(default_factory=list)
    selectors: list[FileSelector] = field(default_factory=list)
    refresh_all: Callable[[], None] = lambda: None
    show_tab: Callable[[str], None] = lambda _title: None


class Tab:
    """A debugger tab: `title`, `build` (widgets) and `refresh` (contents)."""

    title = ""
    message = ""  # name of the tab's message line (the item that `call` reports into)
    live_seconds: float | None = None  # refresh while running, this often (None: frozen)

    def __init__(self, context: PanelContext) -> None:
        """Create the tab for the shared context (`build` creates the widgets)."""
        self.context = context
        self.session = context.session
        self.loop = context.loop
        self.items = context.items

    @property
    def styles(self) -> Styles:
        """The GUI themes (available once the panels are built)."""
        assert self.context.styles is not None, "build the panels first"
        return self.context.styles

    def build(self) -> None:
        """Create the tab's widgets in the current container."""
        raise NotImplementedError

    def refresh(self) -> None:
        """Fill the widgets from the session (called while the machine is stopped)."""

    # -- helpers ---------------------------------------------------------------------------------

    def call(self, action: Callable[[], Any], message: str | None = None) -> Any:
        """Run a session action; show its error (or "ok") in the message line; refresh all."""
        line = self.items[message or self.message]
        try:
            result = action()
        except (SessionError, ValueError, KeyError, OSError) as error:
            dpg.set_value(line, f"error: {error}")
            return None
        dpg.set_value(line, "ok")
        self.context.refresh_all()
        return result

    def text(self, name: str, **options: Any) -> int | str:
        """Add a text widget remembered as `name`."""
        self.items[name] = dpg.add_text("", **options)
        return self.items[name]

    def input(self, name: str, hint: str, width: int = 160, **options: Any) -> int | str:
        """Add a text input remembered as `name` (it takes the PC keys while focused)."""
        self.items[name] = dpg.add_input_text(hint=hint, width=width, **options)
        self.context.inputs.append(self.items[name])
        return self.items[name]

    def entry(self, name: str, hint: str, width: int, action: Callable[[], Any]) -> int | str:
        """Add a text input that runs an action on Enter."""
        return self.input(name, hint, width, on_enter=True, callback=lambda: self.call(action))

    def button(self, label: str, action: Callable[[], Any], **options: Any) -> int | str:
        """Add a button that runs a session action and reports into the message line."""
        return dpg.add_button(label=label, callback=lambda: self.call(action), **options)

    def scroll_list(self, visible_rows: int, **options: Any) -> ScrollList:
        """Add a scroll list (its keys work while the mouse is over it)."""
        scroll_list = ScrollList(visible_rows, themes=self.styles.row_theme, **options)
        self.context.lists.append(scroll_list)
        return scroll_list

    @contextmanager
    def path_field(  # pylint: disable=too-many-arguments  # name, size, folder, types, options
        self,
        name: str,
        width: int,
        default_folder: str,
        extensions: Sequence[str],
        **options: Any,
    ) -> Iterator[None]:
        """Add a path input with ▾ (file selector) as `name`; the block adds its buttons."""
        with widgets.path_field(self.styles, width, self.session.project_dir, default_folder,
                                extensions, **options) as (path_input, selector):  # fmt: skip
            self.items[name] = path_input
            self.context.inputs.append(path_input)
            self.context.selectors.append(selector)
            yield

    def value(self, name: str) -> Any:
        """Current value of a remembered widget."""
        return dpg.get_value(self.items[name])

    def set_lines(self, name: str, lines: list[str]) -> None:
        """Show lines in a text widget."""
        dpg.set_value(self.items[name], "\n".join(lines))
