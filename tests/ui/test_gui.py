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
    check_bus_trace(app, dpg)
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
    tabs["Project"].call(lambda: app.session.sna_save(dpg.get_value(panels.items["sna_path"])))
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
    check_project_editor(panels, dpg, tmp_path)
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
    """The Memory tab: a jump shows the variable's page, the table names it, Follow CPU."""
    memory = panels.tabs["Memory"]
    time = panels.session.variable_list("KL_TIME")["variables"][0]["address"]  # per machine
    memory.jump(int(time, 16))
    first = f"{int(time, 16) & 0xFF00:04X}"
    assert dpg.get_value(panels.items["memory_address_0"]) == f"{first} "  # the whole page
    assert any(row["label"] == "KL_TIME" for row in memory.table_rows)
    memory._cell_clicked(*divmod(int(time, 16) & 0xFF, 16))  # pylint: disable=protected-access
    assert dpg.get_value(panels.items["memory_selected_name"]) == "KL_TIME"
    memory.call(memory.toggle_follow)
    panels.session.step()
    memory.refresh()
    status = dpg.get_value(panels.items["memory_follow_status"])
    assert "since the last stop" in status
    memory.call(memory.toggle_follow)
    assert not panels.session.memory_changes_get()["following"]
    memory.jump(0)


def check_symbols_list(panels: Any, dpg: Any) -> None:
    """Symbols tab (5g): search, a key selects into the detail, the form adds a variable."""
    symbols = panels.tabs["Symbols"]
    dpg.set_value(panels.items["symbol_search"], "txt_out")
    symbols.refresh()
    assert symbols.list.state.count >= 1 and "TXT_OUTPUT" in symbols.list.row(0)
    symbols.list.handle_key("down")  # no selection yet: the first row
    assert dpg.get_value(panels.items["symbol_title"]) == "TXT_OUTPUT"
    assert dpg.get_value(panels.items["symbol_value_1"]).startswith("firmware: text vdu")
    symbols.form_chips.click("var")
    dpg.set_value(panels.items["form_var_name"], "gui_counter")
    dpg.set_value(panels.items["form_size"], "2")
    dpg.set_value(panels.items["form_offset"], "A000")
    symbols._update_now()  # pylint: disable=protected-access
    assert dpg.get_value(panels.items["form_now"]) == "\u2192 now at A000-A001"
    symbols.call(symbols.add_from_form)
    assert dpg.get_value(panels.items["symbol_message"]) == "ok"
    dpg.set_value(panels.items["symbol_search"], "gui_counter")
    symbols.refresh()
    assert "var  A000 \u270e gui_counter" in symbols.list.row(0)
    assert dpg.get_item_label(panels.items["symbols_saved"]) == "not saved"
    panels.session.variable_remove("gui_counter")
    symbols.form_chips.click("sub")
    dpg.set_value(panels.items["symbol_search"], "")


def check_project_editor(panels: Any, dpg: Any, tmp_path: Path) -> None:
    """Project tab (5i): the editor shows the running machine; "changed"; Save as… writes it."""
    project = panels.tabs["Project"]
    project.toggle_editor()
    project.refresh()
    assert not dpg.is_item_shown(panels.items["machine_changed"])
    assert not dpg.get_item_configuration(panels.items["edit_apply"])["enabled"]
    assert project.rom_slots[0] == "lower" and "7" in project.rom_slots
    project.add_ram_target()
    project.refresh()
    assert dpg.is_item_shown(panels.items["machine_changed"])
    assert dpg.get_item_configuration(panels.items["edit_apply"])["enabled"]  # changes the machine
    project.remove_ram_target(0)
    project.add_load()  # a file to load changes no machine: no Apply
    project.refresh()
    assert dpg.is_item_shown(panels.items["machine_changed"])
    assert not dpg.get_item_configuration(panels.items["edit_apply"])["enabled"]
    project.remove_load(0)
    project.refresh()
    assert not dpg.is_item_shown(panels.items["machine_changed"])
    dpg.set_value(panels.items["config_path"], str(tmp_path / "copy.toml"))
    project.call(project.save_config_as)
    assert dpg.get_value(panels.items["project_message"]) == "ok"
    assert (tmp_path / "copy.toml").read_text().startswith("# CPC X-Ray machine configuration")
    project.toggle_editor()


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


def check_bus_trace(app: Any, dpg: Any) -> None:
    """The Bus trace tab: recording, grouped lines, Home into the history, End to the newest."""
    tab = app.panels.tabs["Bus trace"]
    tab.call(tab.toggle)
    assert app.session.bus_trace.enabled
    for _ in range(30):
        app.session.step()
    tab.refresh()
    assert dpg.get_value(app.panels.items["bus_summary"]).startswith("Bus trace \u00b7")
    tab.handle_key("home")
    assert not tab.following and dpg.get_value(app.panels.items["bus_position"]).startswith(
        "records 1-"
    )
    tab.handle_key("end")
    assert tab.following
    tab.call(tab.toggle)
    assert not app.session.bus_trace.enabled
