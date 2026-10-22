"""Smoke test of the Dear PyGui window (skipped without a display)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session
from cpcxray.ui.panels.tabs import list_key_codes
from tests.machines import CONFIG_464, VORTEX_DISK, with_disks

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")), reason="no display"
)


def test_window_keyboard_type_box_and_panels(tmp_path: Path) -> None:
    """The window runs; PC keys and the Type text box type; the debugger panels work.

    One test only: Dear PyGui crashes when a second context is created in the same process.
    """
    import dearpygui.dearpygui as dpg  # pylint: disable=import-outside-toplevel  # needs a display

    from cpcxray.ui.app import App  # pylint: disable=import-outside-toplevel

    config = with_disks(CONFIG_464, b=VORTEX_DISK)
    if not all(rom.exists for rom in config.roms()) or not VORTEX_DISK.is_file():
        pytest.skip("ROMs or disk images missing")
    machine = CPC464(config)
    app = App(Session(machine))
    app.loop.max_speed = True
    app.loop.start()
    app.build()
    try:
        key_map = machine.key_map()
        for cpc_name in set(app._host_keys.values()):  # pylint: disable=protected-access
            key_map.key_number(cpc_name)  # raises for an unknown name
        for _ in range(400):  # boot at max speed
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
    tabs = panels.tabs
    assert dpg.get_item_label(panels.items["disk_mode"]) == "read-only"  # filled at start
    dpg.set_value(panels.items["disk_path"], "")
    tabs["Disks"].call(lambda: app.session.disk_save("B", ""))
    assert dpg.get_value(panels.items["disk_message"]).startswith("error: give a file name")
    app.loop.stop()
    panels.refresh(force=True)
    check_cpu(app, dpg)
    assert dpg.get_value(panels.items["crtc_1"]).startswith("R1  H displayed")
    assert dpg.get_value(panels.items["ga_header"]).startswith("mode 1")
    assert dpg.get_value(panels.items["fdc_disk_B"]).startswith("B: ")  # the Vortex test disk
    assert dpg.get_item_label(panels.items["key_2_5"]) == "Shift"  # names from the OS ROM
    assert dpg.get_value(panels.items["memory_address_0"]) == "0000 "
    check_memory_variables(panels, dpg)
    breakpoints = tabs["Breakpoints"]
    breakpoints.types.click("write")
    assert dpg.get_value(panels.items["bp_explanation"]).startswith("write: stops after")
    dpg.set_value(panels.items["bp_address"], "KL_TIME")
    breakpoints._update_hint()  # pylint: disable=protected-access  # the input's callback
    assert "the whole variable KL_TIME" in dpg.get_value(panels.items["bp_hint"])
    dpg.set_value(panels.items["bp_address"], "B000")
    dpg.set_value(panels.items["bp_end"], "BFFF")
    breakpoints.call(breakpoints.add_breakpoint)
    assert dpg.get_value(panels.items["bp_count"]) == "Breakpoints \u00b7 1"
    app.loop.start()
    for _ in range(20):
        app.render_frame()
        if not app.loop.running:
            break
    assert app.loop.last_stop["reason"] == "breakpoint"  # the firmware writes its RAM
    panels.refresh(force=True)
    assert dpg.get_value(panels.items["stop_headline"]).startswith("write B")
    assert dpg.get_value(panels.items["stop_title"]).startswith("breakpoint #1")
    dpg.set_value(panels.items["sna_path"], str(tmp_path / "gui.sna"))
    tabs["Files"].call(lambda: app.session.sna_save(dpg.get_value(panels.items["sna_path"])))
    assert (tmp_path / "gui.sna").stat().st_size == 0x100 + 0x10000
    dpg.set_value(panels.items["memory_edit"], "4000: 3E 05")
    tabs["Memory"].call(tabs["Memory"].edit_memory)
    assert dpg.get_value(panels.items["memory_message"]) == "ok"
    dpg.set_value(panels.items["register_edit"], "hl")
    tabs["CPU"].call(tabs["CPU"].set_registers)
    assert dpg.get_value(panels.items["cpu_message"]).startswith("error:")
    assert not panels.typing()
    # no list key may read as held at rest (Dear PyGui's mvKey_Prior/Next did: lists jumped back)
    assert not any(dpg.is_key_down(code) for code in list_key_codes().values())
    check_symbols_list(panels, dpg)
    check_file_selector(panels, dpg, tmp_path)
    check_toggles_and_freeze(app, dpg)


def check_toggles_and_freeze(app: Any, dpg: Any) -> None:
    """Trace and analysis toggles, panels frozen while running (inside the one GUI test)."""
    app.session.analysis_target_add("rom7")  # any ROM can be an analysis target (AMSDOS here)
    panels = app.panels
    app._toggle_analysis()  # pylint: disable=protected-access
    app.render_frame()
    assert not app.session.analysis_enabled
    assert dpg.get_value(app._widgets.analysis_light) == "analysis off"  # pylint: disable=protected-access
    app._toggle_analysis()  # pylint: disable=protected-access
    app.render_frame()
    assert dpg.get_value(app._widgets.analysis_light) == "ANALYSIS"  # pylint: disable=protected-access
    trace = panels.tabs["Trace"]
    trace.call(trace.toggle_trace)
    assert app.session.trace.enabled
    assert dpg.get_item_label(panels.items["trace_toggle"]) == "\u25cf Trace runs"
    trace.call(trace.toggle_trace)
    assert not app.session.trace.enabled
    app.loop.start()  # while running, the panels stay frozen
    dpg.set_value(panels.items["reg_PC"], "frozen")
    panels.refresh()
    assert dpg.get_value(panels.items["reg_PC"]) == "frozen"
    devices_tab = next(i for i, t in panels._tab_ids.items() if t.title == "Devices")  # pylint: disable=protected-access
    dpg.set_value(panels.items["tab_bar"], devices_tab)
    dpg.set_value(panels.items["crtc_1"], "stale")
    panels.refresh()  # the Devices tab is live: refreshed while running
    assert dpg.get_value(panels.items["crtc_1"]).startswith("R1")
    dpg.set_value(panels.items["tab_bar"], next(iter(panels._tab_ids)))  # pylint: disable=protected-access  # CPU
    app.loop.stop()
    panels.refresh()
    assert dpg.get_value(panels.items["reg_PC"]) != "frozen"
    panels.refresh(force=True)
    assert dpg.get_item_label(panels.items["disk_mode"]) == "read-only"  # the real images
    assert "K free" in dpg.get_value(panels.items["catalog_header"])
    disks = panels.tabs["Disks"]
    disks.select_drive("B")  # the Vortex test disk
    assert dpg.get_item_label(panels.items["disk_insert"]) == "Eject"
    disks.file_list.select(0, notify=True)
    assert dpg.get_value(panels.items["file_title"]) == disks.files[0]["name"]


def check_memory_variables(panels: Any, dpg: Any) -> None:
    """The memory panel colours the bytes of known variables and names them (FR-DBG-11/12)."""
    time = panels.session.variable_list("KL_TIME")["variables"][0]["address"]  # per machine
    dpg.set_value(panels.items["memory_address"], time)
    dpg.set_value(panels.items["memory_view"], "ram")
    panels.refresh(force=True)
    assert dpg.get_value(panels.items["memory_paging"]).endswith("RAM block 2")
    first_byte = dpg.get_item_configuration(panels.items["memory_byte_0_0"])["color"]
    assert tuple(round(c * 255) for c in first_byte[:3]) == (255, 200, 90)  # 1st variable
    legend = [dpg.get_value(item)
              for item in dpg.get_item_children(panels.items["memory_legend"], 1)]  # fmt: skip
    assert legend[0].startswith("in KL_WORKSPACE") and "KL_TIME (4)" in legend[1]
    dpg.set_value(panels.items["memory_address"], "0000")
    dpg.set_value(panels.items["memory_view"], "cpu")


def check_symbols_list(panels: Any, dpg: Any) -> None:
    """The symbol matches fill the scroll list; keys move the selection into the add line."""
    symbols = panels.tabs["Symbols"]
    dpg.set_value(panels.items["symbol_search"], "txt_out")
    symbols.refresh()
    assert symbols.list.state.count >= 1 and "TXT_OUTPUT" in symbols.list.row(0)
    symbols.list.handle_key("down")  # no selection yet: the first row
    assert dpg.get_value(panels.items["symbol_add"]).startswith("BB5A TXT_OUTPUT ; TXT OUTPUT")
    dpg.set_value(panels.items["symbol_search"], "")


def check_file_selector(panels: Any, dpg: Any, tmp_path: Path) -> None:
    """The ▾ dialog opens in the field's folder, lists .sna files and fills the field."""
    (tmp_path / "other.sna").write_bytes(b"")
    selector = next(s for s in panels.context.selectors if s.path_input == panels.items["sna_path"])
    dpg.set_value(panels.items["sna_path"], str(tmp_path / "gui.sna"))
    selector.open()
    assert selector.is_open() and selector.folder == tmp_path.resolve()
    names = [entry.name for entry in selector.entries]
    assert names[0] == ".." and "other.sna" in names and "gui.sna" in names
    index = names.index("other.sna")
    selector.list.select(index, notify=True)  # a click: the name line takes it
    selector.select()
    assert not selector.is_open()
    assert dpg.get_value(panels.items["sna_path"]) == str(tmp_path / "other.sna")


def check_cpu(app: Any, dpg: Any) -> None:
    """The CPU tab: registers, the PC row marked, the stack with its top (task 5b)."""
    items = app.panels.items
    assert dpg.get_value(items["reg_PC"]) == f"{app.session.machine.cpu.pc:04X}"
    gutters = [dpg.get_value(items[f"code_gutter_{row}"]) for row in range(10)]
    assert "\u25b6" in gutters  # the PC row is marked
    assert dpg.get_value(items["stack_top"]).startswith("top &")
    dpg.set_value(items["show_bytes"], False)
    app.panels.tabs["CPU"]._bytes_switched()  # pylint: disable=protected-access  # its callback
    assert not dpg.get_item_configuration(items["bytes_column"])["enabled"]
