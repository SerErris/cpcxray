"""Devices tab: Gate Array, CRTC, SystemIO, ROM select, FDC (today as text, task 5 redesigns it)."""

from __future__ import annotations

from cpcxray.session.errors import SessionError
from cpcxray.ui.panels.base import Tab
from cpcxray.ui.panels.text import devices_lines


class DevicesTab(Tab):
    """Device state."""

    title = "Devices"

    def build(self) -> None:
        """Create the widgets."""
        self.text("devices")

    def refresh(self) -> None:
        """Device state from `devices_get`."""
        try:
            lines = devices_lines(self.session.devices_get())
        except SessionError as error:
            lines = [f"error: {error}"]
        self.set_lines("devices", lines)
