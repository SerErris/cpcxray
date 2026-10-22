"""Files tab: .sna save/load, analysis save, source generate + verify (task 5i: "Project").

The snapshot path field's ▾ opens the file selector in output/ (.sna files).
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels.base import PANEL_WIDTH, Tab

SNAPSHOT_EXTENSIONS = (".sna",)


class FilesTab(Tab):
    """Snapshots, analysis and source files."""

    title = "Files"
    message = "files_message"

    def build(self) -> None:
        """Create the widgets."""
        session = self.session
        with self.path_field("sna_path", 220, "output", SNAPSHOT_EXTENSIONS,
                             default_value="output/state.sna"):  # fmt: skip
            self.button("Save .sna", lambda: session.sna_save(self.value("sna_path")))
            self.button("Load .sna", lambda: session.sna_load(self.value("sna_path")))
        with dpg.group(horizontal=True):
            self.button("Save analysis", session.analysis_save)
            self.button("Generate + verify source", self.generate_source)
        self.text("files_message", wrap=PANEL_WIDTH)
        self.text("files_result", wrap=PANEL_WIDTH)

    def generate_source(self) -> Any:
        """Generate the source of the only analysis target and verify it (D-33)."""
        generated = self.session.source_generate()
        verified = self.session.source_verify()
        text = (f"{generated['path']}: {generated['lines']} lines; "
                f"{'identical' if verified['identical'] else 'DIFFERENT'} "
                f"({verified['difference_count']} differing bytes)")  # fmt: skip
        dpg.set_value(self.items["files_result"], text)
        return verified
