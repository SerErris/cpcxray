"""Smoke test of the Dear PyGui window (skipped without a display)."""

from __future__ import annotations

import os

import pytest

from cpcxray.machine.cpc464 import CPC464
from cpcxray.machine.memory import RomSlot
from cpcxray.session.session import Session

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")), reason="no display"
)


def test_window_renders_frames() -> None:
    """The window opens, runs the emulation for a few frames and closes."""
    from cpcxray.ui.app import App  # pylint: disable=import-outside-toplevel  # needs a display

    machine = CPC464()
    rom = bytearray(0x4000)
    rom[0:2] = bytes.fromhex("18FE")  # JR $
    machine.memory.set_lower_rom(RomSlot(bytes(rom), "test-os"))
    machine.reset()
    app = App(Session(machine))
    app.loop.start()
    app.run(max_frames=10)
    assert machine.clock_us > 0
