"""Smoke test of the Dear PyGui window (skipped without a display)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cpcxray.machine.config import load_config
from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")), reason="no display"
)


def test_window_keyboard_and_type_box() -> None:
    """The window runs the emulation; PC keys map to CPC keys; the Type text box types.

    One test only: Dear PyGui crashes when a second context is created in the same process.
    """
    import dearpygui.dearpygui as dpg  # pylint: disable=import-outside-toplevel  # needs a display

    from cpcxray.ui.app import App  # pylint: disable=import-outside-toplevel

    config = load_config(Path(__file__).resolve().parents[2] / "config" / "machine.toml")
    if config.lower_rom is None or not config.lower_rom.exists:
        pytest.skip("ROMs missing")
    machine = CPC464(config)
    app = App(Session(machine))
    app.loop.max_speed = True
    app.loop.start()
    app.build()
    try:
        key_map = machine.key_map()
        for cpc_name in set(app._host_keys.values()):  # pylint: disable=protected-access
            key_map.key_number(cpc_name)  # raises for an unknown name
        for _ in range(100):  # boot at max speed
            app.render_frame()
        assert machine.clock_us > 0
        assert machine.keyboard.held_keys() == []  # no PC key is falsely reported as down
        app._type_text(0, "PRINT 6*7")  # pylint: disable=protected-access  # the box's callback
        for _ in range(60):
            app.render_frame()
        assert " 42" in app.session.screen_text()["lines"]
    finally:
        dpg.destroy_context()
