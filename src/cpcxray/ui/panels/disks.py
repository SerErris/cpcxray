"""Disks tab: drives A:/B:, insert (read-only unless ticked), eject, draft, Save, Save as (FR-DSK).

Save writes a writable image back to its file; Save as writes the path field's file, which the
drive then uses (writable, the old file untouched). The catalogue is read directly from the
image. The path field's ▾ opens the file selector in the image's folder (disk/).
"""

from __future__ import annotations

import dearpygui.dearpygui as dpg

from cpcxray.session.errors import SessionError
from cpcxray.ui.panels.base import Tab

DISK_EXTENSIONS = (".dsk",)


class DisksTab(Tab):
    """Drive state, insert/eject/draft/save, and the catalogue of a drive."""

    title = "Disks"
    message = "disk_message"

    def build(self) -> None:
        """Create the widgets."""
        session = self.session
        self.text("drives")
        with dpg.group(horizontal=True):
            self.items["disk_drive"] = dpg.add_combo(("A", "B"), default_value="A", width=50)
            with self.path_field("disk_path", 220, "disk", DISK_EXTENSIONS, hint="disk/image.dsk"):
                self.items["disk_writable"] = dpg.add_checkbox(label="writable")
                self.button("Insert", lambda: session.disk_insert(
                    self.value("disk_drive"), self.value("disk_path"),
                    bool(self.value("disk_writable"))))  # fmt: skip
                self.button("Eject", lambda: session.disk_eject(self.value("disk_drive")))
        with dpg.group(horizontal=True):
            self.items["disk_format"] = dpg.add_combo(("vortex", "data", "system"),
                                                      default_value="vortex", width=80)  # fmt: skip
            self.button("New draft", lambda: session.disk_new(
                self.value("disk_drive"), self.value("disk_format")))  # fmt: skip
            self.button("Save", lambda: session.disk_save(self.value("disk_drive")))
            self.button("Save as", lambda: session.disk_save(
                self.value("disk_drive"), self.value("disk_path")))  # fmt: skip
        self.text("disk_message")
        self.text("catalog")

    def refresh(self) -> None:
        """Drives and the catalogue of the selected drive (read directly from the image)."""
        lines = []
        for drive in self.session.disk_list()["drives"]:
            state = "empty"
            if drive["disk"] is not None:
                mode = "writable" if drive["writable"] else "read-only"
                changed = ", changed" if drive["modified"] else ""
                state = f"{drive['disk']} ({mode}{changed})"
            lines.append(f"{drive['drive']}: cyl {drive['head_cylinder']:2}  {state}")
        self.set_lines("drives", lines)
        try:
            catalog = self.session.disk_catalog(self.value("disk_drive"))
            files = [f"{f['user']:2} {f['name']:<13}{f['size']:7}{'  SYS' if f['system'] else ''}"
                     for f in catalog["files"]]  # fmt: skip
            files.append(f"{catalog['format']} format, {catalog['used_kb']}K used")
        except SessionError as error:
            files = [str(error)]
        self.set_lines("catalog", files)
