"""Smoke test of the Dear PyGui window (skipped without a display)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from cpcxray.machine.config import load_config
from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")), reason="no display"
)


def test_window_keyboard_type_box_and_panels(tmp_path: Path) -> None:
    """The window runs; PC keys and the Type text box type; the debugger panels work.

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
        for _ in range(400):  # boot at max speed (VDOS looks for HELLO.BAS on A:)
            app.render_frame()
            if "Ready" in app.session.screen_text()["lines"]:
                break
        assert machine.clock_us > 0
        assert machine.keyboard.held_keys() == []  # no PC key is falsely reported as down
        app._type_text(0, "PRINT 6*7")  # pylint: disable=protected-access  # the box's callback
        for _ in range(60):
            app.render_frame()
        assert " 42" in app.session.screen_text()["lines"]
        check_panels(app, dpg, tmp_path)
    finally:
        dpg.destroy_context()


def check_panels(app: Any, dpg: Any, tmp_path: Path) -> None:
    """Exercise the debugger panels of a booted machine (called inside the one GUI test)."""
    panels = app.panels
    app.loop.stop()
    panels.refresh(force=True)
    assert dpg.get_value(panels.items["registers"]).startswith("AF ")
    assert ">" in dpg.get_value(panels.items["disassembly"])  # the PC line is marked
    assert "[gate_array]" in dpg.get_value(panels.items["devices"])
    assert dpg.get_value(panels.items["memory"]).startswith("0000  ")
    dpg.set_value(panels.items["bp_kind"], "write")
    dpg.set_value(panels.items["bp_address"], "B000")
    dpg.set_value(panels.items["bp_end"], "BFFF")
    panels._call("bp_message", panels._add_bp)  # pylint: disable=protected-access
    assert "write B000-BFFF" in dpg.get_value(panels.items["breakpoints"])
    app.loop.start()
    for _ in range(20):
        app.render_frame()
        if not app.loop.running:
            break
    assert app.loop.last_stop["reason"] == "breakpoint"  # the firmware writes its RAM
    dpg.set_value(panels.items["sna_path"], str(tmp_path / "gui.sna"))
    panels._call("files_message", lambda: app.session.sna_save(  # pylint: disable=protected-access
        dpg.get_value(panels.items["sna_path"])))  # fmt: skip
    assert (tmp_path / "gui.sna").stat().st_size == 0x100 + 0x10000
    dpg.set_value(panels.items["memory_edit"], "4000: 3E 05")
    panels._call("memory_message", panels._edit_memory)  # pylint: disable=protected-access
    assert dpg.get_value(panels.items["memory_message"]) == "ok"
    dpg.set_value(panels.items["register_edit"], "hl")
    panels._call("cpu_message", panels._set_registers)  # pylint: disable=protected-access
    assert dpg.get_value(panels.items["cpu_message"]).startswith("error:")
    assert not panels.typing()
    app._toggle_analysis()  # pylint: disable=protected-access
    app.render_frame()
    assert not app.session.analysis_enabled
    assert dpg.get_value(app._widgets.analysis_light) == "analysis off"  # pylint: disable=protected-access
    app._toggle_analysis()  # pylint: disable=protected-access
    app.render_frame()
    assert dpg.get_value(app._widgets.analysis_light) == "ANALYSIS"  # pylint: disable=protected-access
    panels._call("trace_message", panels._toggle_trace)  # pylint: disable=protected-access
    assert app.session.trace.enabled
    assert dpg.get_item_label(panels.items["trace_toggle"]) == "Trace runs: on"
    panels._call("trace_message", panels._toggle_trace)  # pylint: disable=protected-access
    assert not app.session.trace.enabled
    app.loop.start()  # while running, the panels stay frozen
    dpg.set_value(panels.items["registers"], "frozen")
    panels.refresh()
    assert dpg.get_value(panels.items["registers"]) == "frozen"
    app.loop.stop()
    panels.refresh()
    assert dpg.get_value(panels.items["registers"]).startswith("AF ")
    panels.refresh(force=True)
    assert "read-only" in dpg.get_value(panels.items["drives"])  # the owner's real images
    assert "format" in dpg.get_value(panels.items["catalog"])
