"""The file selector behind every path field's ▾ button (agreed 2026-10-22, task 5h).

A path field (disk image, snapshot, configuration, annotation file) has a ▾ button that opens
this dialog in the folder of the current path - or the field's default folder when the path is
empty or its folder does not exist. It lists the folders, then the files of the field's types
(or all files) with name, size and date, in a `ScrollList` (mouse wheel, click, keys over the
list). Clicking a folder (or "..", or Enter on it) opens it; selecting a file (click or keys)
puts its name into the name line, where a new name may be typed as well (Save as); a second
click on the same file, or Enter on it, selects it. Select puts the path into the field (relative
to the project directory when it lies inside it); the field's own buttons (Insert, Load, ...)
then act on it, so selecting never changes anything by itself.

`start_folder`, `list_folder`, `entry_line` and `shown_path` are the headless part (tested
without a display); `FileSelector` is the Dear PyGui dialog.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels.colours import TEXT_DIM, Colour
from cpcxray.ui.panels.scroll_list import ScrollList

DIALOG_WIDTH = 600
LIST_ROWS = 16
ALL_FILES = "all files"


@dataclass(frozen=True)
class FolderEntry:
    """One line of the dialog's list.

    Attributes:
        name: File or folder name ("..": the parent folder).
        is_folder: A folder (opens on click) rather than a file.
        size: File size in bytes (0 for folders).
        modified: Modification time (seconds since the epoch; 0 for "..").
    """

    name: str
    is_folder: bool
    size: int = 0
    modified: float = 0.0


def start_folder(current: str, default_folder: Path, project_dir: Path) -> Path:
    """The folder the dialog opens in.

    Args:
        current: The path field's text (may be empty, relative to the project, or absolute).
        default_folder: The field's own folder (e.g. disk/), relative to the project or absolute.
        project_dir: The project directory.

    Returns:
        The folder of `current` if it exists, else `default_folder` if it exists, else the
        project directory.
    """
    candidates = []
    if current.strip():
        path = Path(current.strip())
        path = path if path.is_absolute() else project_dir / path
        candidates.append(path if path.is_dir() else path.parent)
    folder = default_folder if default_folder.is_absolute() else project_dir / default_folder
    candidates += [folder, project_dir]
    return next(candidate.resolve() for candidate in candidates if candidate.is_dir())


def list_folder(folder: Path, extensions: Sequence[str] = ()) -> list[FolderEntry]:
    """The entries of a folder: "..", folders, then files of the given types, sorted by name.

    Args:
        folder: The folder.
        extensions: File types to list (".dsk", case-insensitive); empty lists all files.
            Hidden entries (".name") are left out.
    """
    wanted = {extension.lower() for extension in extensions}
    folders, files = [], []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if path.name.startswith("."):
            continue
        if path.is_dir():
            folders.append(FolderEntry(path.name, True))
        elif not wanted or path.suffix.lower() in wanted:
            status = path.stat()
            files.append(FolderEntry(path.name, False, status.st_size, status.st_mtime))
    parent = [FolderEntry("..", True)] if folder.parent != folder else []
    return parent + folders + files


def entry_line(entry: FolderEntry) -> str:
    """One list line: name, size and date of a file; folders end in "/"."""
    if entry.is_folder:
        return f"{entry.name}/"
    date = time.strftime("%Y-%m-%d %H:%M", time.localtime(entry.modified))
    return f"{entry.name:<36.36} {entry.size:>9}  {date}"


def shown_path(selected: str | Path, project_dir: Path) -> str:
    """The text put into the path field: relative to the project if inside it, else absolute."""
    path = Path(selected).resolve()
    try:
        return str(path.relative_to(project_dir.resolve()))
    except ValueError:
        return str(path)


class FileSelector:  # pylint: disable=too-many-instance-attributes  # dialog widgets and state
    """The dialog for one path field (see the module docstring)."""

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # field, folders, filters, callbacks
        self,
        path_input: int | str,
        project_dir: Path,
        default_folder: str,
        extensions: Sequence[str],
        row_theme: Callable[[Colour | None, bool], int | str] | None = None,
        on_selected: Callable[[str], None] | None = None,
    ) -> None:
        """Prepare the dialog for a path input (the window is built on the first `open`).

        Args:
            path_input: The Dear PyGui text input that holds the path.
            project_dir: The project directory (relative paths start there).
            default_folder: Folder used while the field has no usable path (e.g. "disk").
            extensions: File types offered first, e.g. (".dsk",); "all files" is the other
                filter.
            row_theme: Themes for the list rows (widgets.Styles.row_theme).
            on_selected: Called with the new field text after Select.
        """
        self.path_input = path_input
        self.project_dir = project_dir
        self.default_folder = Path(default_folder)
        self.extensions = tuple(extensions)
        self.on_selected = on_selected
        self.row_theme = row_theme
        self.folder = project_dir
        self.entries: list[FolderEntry] = []
        self._clicked_file: int | None = None  # a second click on it selects it
        self.window: int | str | None = None
        self.folder_text: int | str = 0
        self.filter: int | str = 0
        self.list: ScrollList | None = None
        self.name: int | str = 0
        self.message: int | str = 0

    def _build(self) -> None:
        """Create the hidden modal window (a top-level window: not inside the tab's widgets)."""
        filters = [" ".join(self.extensions), ALL_FILES] if self.extensions else [ALL_FILES]
        with dpg.window(label="Select a file", modal=True, show=False, width=DIALOG_WIDTH,
                        no_collapse=True, on_close=self.close) as window:  # fmt: skip
            self.window = window
            self.folder_text = dpg.add_text("", color=TEXT_DIM)
            self.filter = dpg.add_combo(filters, default_value=filters[0], width=160,
                                        callback=lambda: self._show(self.folder))  # fmt: skip
            self.list = ScrollList(LIST_ROWS, on_select=self._selected, on_activate=self._activated,
                                   themes=self.row_theme)  # fmt: skip
            with dpg.group(horizontal=True):
                dpg.add_text("name")
                self.name = dpg.add_input_text(width=DIALOG_WIDTH - 220, on_enter=True,
                                               callback=self.select)  # fmt: skip
                dpg.add_button(label="Select", callback=self.select)
                dpg.add_button(label="Cancel", callback=self.close)
            self.message = dpg.add_text("", color=TEXT_DIM)

    def is_open(self) -> bool:
        """True while the dialog is shown (its list then takes the list keys)."""
        return self.window is not None and bool(dpg.is_item_shown(self.window))

    def open(self) -> None:
        """Show the dialog in the folder of the field's current path."""
        if self.window is None:
            self._build()
        current = dpg.get_value(self.path_input) or ""
        self._show(start_folder(current, self.default_folder, self.project_dir))
        name = Path(current.strip()).name if current.strip() else ""
        dpg.set_value(self.name, name if not (self.folder / name).is_dir() else "")
        dpg.set_value(self.message, "")
        self._clicked_file = None
        dpg.show_item(self.window)

    def close(self) -> None:
        """Hide the dialog without changing the field."""
        if self.window is not None:
            dpg.hide_item(self.window)

    def select(self) -> None:
        """Select: put the folder + name into the path field and close."""
        name = (dpg.get_value(self.name) or "").strip()
        if not name:
            dpg.set_value(self.message, "select a file or type a name")
            return
        path = Path(name) if Path(name).is_absolute() else self.folder / name
        if path.is_dir():
            self._show(path.resolve())
            return
        text = shown_path(path, self.project_dir)
        dpg.set_value(self.path_input, text)
        self.close()
        if self.on_selected is not None:
            self.on_selected(text)

    def _show(self, folder: Path) -> None:
        """List a folder with the chosen filter."""
        chosen = dpg.get_value(self.filter)
        extensions = () if chosen == ALL_FILES else self.extensions
        try:
            entries = list_folder(folder, extensions)
        except OSError as error:
            dpg.set_value(self.message, f"error: {error}")
            return
        self.folder, self.entries = folder, entries
        dpg.set_value(self.folder_text, str(folder))
        assert self.list is not None  # built by open()
        self.list.set_rows([entry_line(entry) for entry in entries])

    def _selected(self, index: int) -> None:
        """A row was selected: a file's name goes into the name line."""
        entry = self.entries[index]
        if not entry.is_folder:
            dpg.set_value(self.name, entry.name)

    def _activated(self, index: int, by_key: bool) -> None:
        """A click or Enter: open a folder; Enter or a second click on a file selects it."""
        entry = self.entries[index]
        if entry.is_folder:
            self._show((self.folder / entry.name).resolve())
        elif by_key or index == self._clicked_file:
            self.select()
        self._clicked_file = None if by_key or entry.is_folder else index
