"""Debugger panels of the GUI (design.md §6.12, §6.13; FR-DBG-02..10).

A tab bar next to the screen:

| Tab         | Shows / does                                                               |
|-------------|----------------------------------------------------------------------------|
| CPU         | registers (editable), paging per 16 KB (ROM or RAM block), disassembly     |
|             | around the PC or at an address (marked per quarter), step out, run to      |
| Memory      | hex dump through a view (CPU, RAM, ROM, raw ROM, shadow ROM), editing;     |
|             | what the view shows per 16 KB; known OS/AMSDOS variables coloured + legend |
| Devices     | Gate Array, CRTC, SystemIO, ROM select, FDC                                |
| Breakpoints | list; add any kind with location qualifier; enable, disable, remove        |
| Trace       | instruction trace on/off and its last lines                                |
| Bus trace   | bus access trace on/off (kinds filter) and its last lines                  |
| Symbols     | search; add "address label ; comment"                                      |
| Disks       | drives A:/B:, insert (read-only unless ticked), eject, blank draft, save   |
|             | (back to its file if writable), save as,                                   |
|             | catalogue read directly from the image                                     |
| Files       | .sna save/load, analysis save, source generate + verify                    |

Every action is a `Session` call (the same as the MCP tool); errors show in the panel's message
line. While the machine runs freely the panels stay frozen; they are filled once when the window
opens and when a tab is opened, refreshed (the visible tab) when the machine stops - pause,
breakpoint, step - and after every panel action.

The instruction trace is off for free runs unless switched on with its toggle button (it costs
about 5x speed); stepping is always recorded into its buffer. A file is written only when
"write file" is ticked before switching the trace on.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.session.errors import SessionError
from cpcxray.session.session import Session
from cpcxray.ui.emulation import EmulationLoop
from cpcxray.ui.panels.text import (
    AREA,
    BYTES_PER_ROW,
    breakpoint_lines,
    devices_lines,
    mark_pc,
    memory_layout,
    paging_lines,
    parse_assignments,
    parse_memory_edit,
    registers_lines,
    with_paging,
)

PANEL_WIDTH = 620
DISASSEMBLY_LINES = 24
MEMORY_BYTES = 256
MEMORY_ROWS = MEMORY_BYTES // BYTES_PER_ROW
# Text colours of the memory panel: known variables take these in turn (FR-DBG-11)
VARIABLE_COLOURS = ((255, 200, 90), (120, 200, 255), (150, 230, 120), (255, 140, 170),
                    (200, 160, 255), (110, 220, 210))  # fmt: skip
AREA_COLOUR = (140, 140, 140)  # inside a pack's area, in no named variable
DEFAULT_COLOUR = (-255, 0, 0, 255)  # Dear PyGui: the theme's text colour
TRACE_LINES = 30
BREAKPOINT_KINDS = ("pc", "m1", "read", "write", "access", "io_in", "io_out", "io",
                    "rom_select", "interrupt")  # fmt: skip
LOCATIONS = ("any", "rom", "ram", "lower", *(f"rom:{n}" for n in range(16)),
             *(f"ram:{n}" for n in range(8)))  # RAM blocks; 4-7 only on a 6128  # fmt: skip


class DebuggerPanels:  # pylint: disable=too-many-instance-attributes  # one item id per widget
    """The tab bar with all debugger panels."""

    def __init__(self, session: Session, loop: EmulationLoop) -> None:
        """Create the panels for a session driven by `loop` (build() creates the widgets)."""
        self.session = session
        self.loop = loop
        self.items: dict[str, int | str] = {}
        self.inputs: list[int | str] = []  # text inputs: PC keys go there, not to the CPC
        self._refreshers: dict[int | str, Callable[[], None]] = {}

    # -- helpers ---------------------------------------------------------------------------------

    def _call(self, message_item: str, action: Callable[[], Any]) -> Any:
        """Run a session action; show its error (or "ok") in a message line."""
        try:
            result = action()
        except (SessionError, ValueError, KeyError, OSError) as error:
            dpg.set_value(self.items[message_item], f"error: {error}")
            return None
        dpg.set_value(self.items[message_item], "ok")
        self.refresh(force=True)
        return result

    def _text(self, name: str, **options: Any) -> None:
        """Add a text widget remembered as `name`."""
        self.items[name] = dpg.add_text("", **options)

    def _input(self, name: str, hint: str, width: int = 160, **options: Any) -> None:
        """Add a text input remembered as `name`."""
        self.items[name] = dpg.add_input_text(hint=hint, width=width, **options)
        self.inputs.append(self.items[name])

    def typing(self) -> bool:
        """True while one of the panels' text inputs has the keyboard focus."""
        return any(dpg.is_item_focused(item) for item in self.inputs)

    def _value(self, name: str) -> Any:
        """Current value of a remembered widget."""
        return dpg.get_value(self.items[name])

    # -- building --------------------------------------------------------------------------------

    def build(self) -> None:
        """Create the tab bar in the current container."""
        with dpg.tab_bar(callback=self._tab_changed) as tab_bar:
            self.items["tab_bar"] = tab_bar
            for title, builder, refresher in (
                ("CPU", self._build_cpu, self._refresh_cpu),
                ("Memory", self._build_memory, self._refresh_memory),
                ("Devices", self._build_devices, self._refresh_devices),
                ("Breakpoints", self._build_breakpoints, self._refresh_breakpoints),
                ("Trace", self._build_trace, self._refresh_trace),
                ("Bus trace", self._build_bus_trace, self._refresh_bus_trace),
                ("Symbols", self._build_symbols, self._refresh_symbols),
                ("Disks", self._build_disks, self._refresh_disks),
                ("Files", self._build_files, lambda: None),
            ):
                with dpg.tab(label=title) as tab:
                    builder()
                self._refreshers[tab] = refresher

    def _button(self, label: str, message: str, action: Callable[[], Any]) -> None:
        """Add a button that runs a session action and reports into `message`."""
        dpg.add_button(label=label, callback=lambda: self._call(message, action))

    def _entry(  # pylint: disable=too-many-arguments  # widget name, layout and action
        self, name: str, hint: str, width: int, message: str, action: Callable[[], Any]
    ) -> None:
        """Add a text input that runs an action on Enter and reports into `message`."""
        self._input(name, hint, width, on_enter=True,
                    callback=lambda: self._call(message, action))  # fmt: skip

    def _build_cpu(self) -> None:
        """Registers, register editing, paging, disassembly, step out / run to."""
        self._text("registers")
        with dpg.group(horizontal=True):
            self._entry("register_edit", "hl=4000, a=5", 200, "cpu_message", self._set_registers)
            self._button("Step out", "cpu_message", self.loop.step_out)
            self._entry("run_to", "run to address", 110, "cpu_message", self._run_to)
        self._text("cpu_message")
        self._text("paging")
        with dpg.group(horizontal=True):
            dpg.add_text("Disassembly at")
            self._input("disassembly_address", "PC (empty) or address/label", 200)
        self._text("disassembly")

    def _build_memory(self) -> None:
        """View selection, editing, paging, hex dump (variables coloured) and its legend."""
        with dpg.group(horizontal=True):
            self._input("memory_address", "address", 100, default_value="0000")
            self.items["memory_view"] = dpg.add_combo(
                self._memory_views(), default_value="cpu", width=120
            )
            self._entry("memory_edit", "4000: 3E 05 C9  (RAM)", 220, "memory_message",
                        self._edit_memory)  # fmt: skip
        self._text("memory_message")
        self._text("memory_paging")
        for row in range(MEMORY_ROWS):  # one text per byte, so each byte can have its colour
            with dpg.group(horizontal=True, horizontal_spacing=0):
                self._text(f"memory_address_{row}")
                for column in range(BYTES_PER_ROW):
                    self._text(f"memory_byte_{row}_{column}")
                self._text(f"memory_ascii_{row}")
        self.items["memory_legend"] = dpg.add_group()

    def _build_devices(self) -> None:
        """Device state."""
        self._text("devices")

    def _build_breakpoints(self) -> None:
        """Breakpoint form and list."""
        session = self.session
        with dpg.group(horizontal=True):
            self.items["bp_kind"] = dpg.add_combo(BREAKPOINT_KINDS, default_value="pc", width=100)
            self._input("bp_address", "address/port", 100)
            self._input("bp_end", "end (range)", 90)
            self.items["bp_location"] = dpg.add_combo(LOCATIONS, default_value="any", width=80)
        with dpg.group(horizontal=True):
            self._input("bp_mask", "port mask (FF00)", 120)
            self._input("bp_rom", "ROM number", 90)
            self._button("Add", "bp_message", self._add_bp)
            self._input("bp_id", "id", 50)
            self._button("Enable", "bp_message",
                         lambda: session.breakpoint_enable(self._bp_id(), True))  # fmt: skip
            self._button("Disable", "bp_message",
                         lambda: session.breakpoint_enable(self._bp_id(), False))  # fmt: skip
            self._button("Remove", "bp_message",
                         lambda: session.breakpoint_remove(self._bp_id()))  # fmt: skip
        self._text("bp_message")
        self._text("breakpoints")

    def _build_trace(self) -> None:
        """Toggle for tracing free runs, file options, and the last lines (steps always)."""
        with dpg.group(horizontal=True):
            self.items["trace_toggle"] = dpg.add_button(
                label="Trace runs: off", width=130,
                callback=lambda: self._call("trace_message", self._toggle_trace),
            )  # fmt: skip
            self.items["trace_targets"] = dpg.add_checkbox(label="analysis targets only")
        with dpg.group(horizontal=True):
            self.items["trace_file"] = dpg.add_checkbox(label="write file")
            self._input("trace_path", "output/trace.txt", 200, default_value="output/trace.txt")
            self.items["trace_format"] = dpg.add_combo(("text", "csv", "jsonl"),
                                                       default_value="text", width=70)  # fmt: skip
        dpg.add_text("Stepping is always recorded; tracing free runs costs about 5x speed.")
        self._text("trace_message")
        self._text("trace")

    def _build_bus_trace(self) -> None:
        """Bus trace switch, kinds filter and lines."""
        with dpg.group(horizontal=True):
            self._input("bus_kinds", "M1,read,write,in,out,interrupt (empty: all)", 300)
            self._button("Start", "bus_message", self._start_bus_trace)
            self._button("Stop", "bus_message", self.session.bus_trace_stop)
        self._text("bus_message")
        self._text("bus_trace")

    def _build_symbols(self) -> None:
        """Symbol search and adding."""
        with dpg.group(horizontal=True):
            self._input("symbol_search", "search label or comment", 200)
            self._entry("symbol_add", "BB5A label ; comment", 260, "symbol_message",
                        self._add_symbol)  # fmt: skip
        self._text("symbol_message")
        self._text("symbols")

    def _build_disks(self) -> None:
        """Drive state, insert/eject/draft/save, and the catalogue of a drive."""
        session = self.session
        self._text("drives")
        with dpg.group(horizontal=True):
            self.items["disk_drive"] = dpg.add_combo(("A", "B"), default_value="A", width=50)
            self._input("disk_path", "disk/image.dsk", 220)
            self.items["disk_writable"] = dpg.add_checkbox(label="writable")
            self._button("Insert", "disk_message", lambda: session.disk_insert(
                self._value("disk_drive"), self._value("disk_path"),
                bool(self._value("disk_writable"))))  # fmt: skip
            self._button("Eject", "disk_message",
                         lambda: session.disk_eject(self._value("disk_drive")))  # fmt: skip
        with dpg.group(horizontal=True):
            self.items["disk_format"] = dpg.add_combo(("vortex", "data", "system"),
                                                      default_value="vortex", width=80)  # fmt: skip
            self._button("New draft", "disk_message", lambda: session.disk_new(
                self._value("disk_drive"), self._value("disk_format")))  # fmt: skip
            self._button("Save", "disk_message",
                         lambda: session.disk_save(self._value("disk_drive")))  # fmt: skip
            self._button("Save as", "disk_message", lambda: session.disk_save(
                self._value("disk_drive"), self._value("disk_path")))  # fmt: skip
        self._text("disk_message")
        self._text("catalog")

    def _build_files(self) -> None:
        """Snapshots, analysis and source files."""
        session = self.session
        with dpg.group(horizontal=True):
            self._input("sna_path", "output/state.sna", 220, default_value="output/state.sna")
            self._button("Save .sna", "files_message",
                         lambda: session.sna_save(self._value("sna_path")))  # fmt: skip
            self._button("Load .sna", "files_message",
                         lambda: session.sna_load(self._value("sna_path")))  # fmt: skip
        with dpg.group(horizontal=True):
            self._button("Save analysis", "files_message", session.analysis_save)
            self._button("Generate + verify source", "files_message", self._generate_source)
        self._text("files_message", wrap=PANEL_WIDTH)
        self._text("files_result", wrap=PANEL_WIDTH)

    # -- actions ---------------------------------------------------------------------------------

    def _set_registers(self) -> Any:
        """Apply "name=value" assignments from the register input."""
        return self.session.registers_set(parse_assignments(self._value("register_edit")))

    def _run_to(self) -> None:
        """Start a free run that stops at the given address."""
        address = self.session._address(self._value("run_to"))  # pylint: disable=protected-access
        self.loop.run_to(address)

    def _edit_memory(self) -> Any:
        """Write "address: bytes" into RAM."""
        address, data = parse_memory_edit(self._value("memory_edit"))
        return self.session.memory_write(address, data)

    def _bp_id(self) -> int:
        """The breakpoint id typed into the id box."""
        return int(self._value("bp_id"))

    def _add_bp(self) -> Any:
        """Add a breakpoint from the form."""
        mask_text, rom_text = self._value("bp_mask").strip(), self._value("bp_rom").strip()
        return self.session.breakpoint_add(
            self._value("bp_address").strip() or None,
            kind=self._value("bp_kind"),
            end=self._value("bp_end").strip() or None,
            location=self._value("bp_location"),
            port_mask=int(mask_text, 16) if mask_text else 0xFFFF,
            rom=int(rom_text) if rom_text else None,
        )

    def _toggle_trace(self) -> Any:
        """Switch tracing of free runs on (with the chosen options) or off."""
        if self.session.trace.enabled:
            result = self.session.trace_stop()
        else:
            path = self._value("trace_path").strip() if self._value("trace_file") else None
            result = self.session.trace_start(
                path=path or None, file_format=self._value("trace_format"),
                targets_only=bool(self._value("trace_targets")),
            )  # fmt: skip
        label = "Trace runs: on" if self.session.trace.enabled else "Trace runs: off"
        dpg.set_item_label(self.items["trace_toggle"], label)
        return result

    def _start_bus_trace(self) -> Any:
        """Start the bus trace with the kinds typed in (empty: all)."""
        kinds = [k.strip() for k in self._value("bus_kinds").split(",") if k.strip()]
        return self.session.bus_trace_start(kinds=kinds or None)

    def _add_symbol(self) -> Any:
        """Add "address label ; comment" as a user symbol."""
        entry, _, comment = self._value("symbol_add").partition(";")
        fields = entry.split()
        if not fields:
            raise ValueError("expected 'address label ; comment'")
        label = fields[1] if len(fields) > 1 else None
        return self.session.symbol_add(fields[0], label, comment.strip() or None)

    def _generate_source(self) -> Any:
        """Generate the source of the only analysis target and verify it (D-33)."""
        generated = self.session.source_generate()
        verified = self.session.source_verify()
        text = (f"{generated['path']}: {generated['lines']} lines; "
                f"{'identical' if verified['identical'] else 'DIFFERENT'} "
                f"({verified['difference_count']} differing bytes)")  # fmt: skip
        dpg.set_value(self.items["files_result"], text)
        return verified

    def _memory_views(self) -> list[str]:
        """Views offered for the installed ROMs."""
        views = ["cpu", "ram"]
        memory = getattr(self.session.machine, "memory", None)
        if memory is None:
            return views
        if memory.lower_rom is not None:
            views.append("lower")
        for slot, rom in sorted(memory.upper_roms.items()):
            views.append(f"rom{slot}")
            if rom.shadow is not None:
                views += [f"raw:rom{slot}", f"shadow:rom{slot}"]
        return views

    # -- refreshing ------------------------------------------------------------------------------

    def _tab_changed(self) -> None:
        """Fill a tab when it is opened, also while running (a one-off: no running cost)."""
        refresher = self._refreshers.get(dpg.get_value(self.items["tab_bar"]))
        if refresher is not None:
            refresher()

    def refresh(self, force: bool = False) -> None:
        """Refresh the visible tab while stopped (all tabs with force=True after an action)."""
        if self.loop.running and not force:
            return  # frozen during free runs (no use, and it costs time)
        if force:
            for refresher in self._refreshers.values():
                refresher()
            return
        active = dpg.get_value(self.items["tab_bar"])
        refresher = self._refreshers.get(active)
        if refresher is not None:
            refresher()

    def _set_lines(self, name: str, lines: list[str]) -> None:
        """Show lines in a text widget."""
        dpg.set_value(self.items[name], "\n".join(lines))

    def _refresh_cpu(self) -> None:
        """Registers, paging and disassembly (following the PC unless an address is given).

        The disassembly marks each 16 KB quarter with the ROM or RAM block the CPU reads there
        now (FR-DBG-12).
        """
        session = self.session
        self._set_lines("registers", registers_lines(session.registers_get()))
        self._set_lines("paging", paging_lines(session.paging_get()))
        address = self._value("disassembly_address").strip() or None
        try:
            listing = session.disassemble(address, DISASSEMBLY_LINES)
            lines = with_paging(listing["lines"], listing["paging"])
        except SessionError as error:
            lines = [f"error: {error}"]
        pc = session.machine.cpu.pc
        self._set_lines("disassembly", mark_pc(lines, pc, set(session.breakpoints.addresses)))

    def _refresh_memory(self) -> None:
        """The hex dump of the selected view, with its paging and known variables.

        What the view shows per 16 KB quarter (FR-DBG-12) heads the dump; the bytes of known
        OS/AMSDOS variables are coloured, one colour per variable, and the legend below names
        them in the same colours (FR-DBG-11).
        """
        try:
            dump = self.session.memory_view(self._value("memory_address") or "0",
                                            MEMORY_BYTES, self._value("memory_view"))  # fmt: skip
        except SessionError as error:
            dump = None
            self._set_lines("memory_paging", [f"error: {error}"])
        rows, legend = memory_layout(dump, len(VARIABLE_COLOURS)) if dump else ([], [])
        if dump is not None:
            paging = [f"{q['start']}-{q['end']}  {q['text']}" for q in dump["paging"]]
            self._set_lines("memory_paging", paging)
        for index in range(MEMORY_ROWS):
            row = rows[index] if index < len(rows) else None
            dpg.set_value(self.items[f"memory_address_{index}"], f"{row.address} " if row else "")
            for column in range(BYTES_PER_ROW):
                text, colour = row.cells[column] if row and column < len(row.cells) else ("", None)
                item = self.items[f"memory_byte_{index}_{column}"]
                dpg.set_value(item, f" {text}" if text else "")
                dpg.configure_item(item, color=_colour(colour))
            dpg.set_value(self.items[f"memory_ascii_{index}"], f"  {row.ascii}" if row else "")
        dpg.delete_item(self.items["memory_legend"], children_only=True)
        for text, colour in legend:
            dpg.add_text(text, parent=self.items["memory_legend"], color=_colour(colour),
                         wrap=PANEL_WIDTH)  # fmt: skip

    def _refresh_devices(self) -> None:
        """Device state."""
        try:
            lines = devices_lines(self.session.devices_get())
        except SessionError as error:
            lines = [f"error: {error}"]
        self._set_lines("devices", lines)

    def _refresh_breakpoints(self) -> None:
        """Breakpoint list."""
        self._set_lines(
            "breakpoints", breakpoint_lines(self.session.breakpoint_list()["breakpoints"])
        )

    def _refresh_trace(self) -> None:
        """Last instruction trace lines."""
        result = self.session.trace_read(TRACE_LINES)
        self._set_lines("trace", result.get("lines", []))

    def _refresh_bus_trace(self) -> None:
        """Last bus trace lines."""
        self._set_lines("bus_trace", self.session.bus_trace_read(TRACE_LINES)["lines"])

    def _refresh_disks(self) -> None:
        """Drives and the catalogue of the selected drive (read directly from the image)."""
        lines = []
        for drive in self.session.disk_list()["drives"]:
            state = "empty"
            if drive["disk"] is not None:
                mode = "writable" if drive["writable"] else "read-only"
                changed = ", modified" if drive["modified"] else ""
                state = f"{drive['disk']} ({mode}{changed})"
            lines.append(f"{drive['drive']}: cyl {drive['head_cylinder']:2}  {state}")
        self._set_lines("drives", lines)
        try:
            catalog = self.session.disk_catalog(self._value("disk_drive"))
            files = [f"{f['user']:2} {f['name']:<13}{f['size']:7}{'  SYS' if f['system'] else ''}"
                     for f in catalog["files"]]  # fmt: skip
            files.append(f"{catalog['format']} format, {catalog['used_kb']}K used")
        except SessionError as error:
            files = [str(error)]
        self._set_lines("catalog", files)

    def _refresh_symbols(self) -> None:
        """Symbols matching the search text."""
        result = self.session.symbol_list(self._value("symbol_search"))
        lines = [f"{s.get('scope') or '':6} {s['address']}  {s.get('label') or '-':<20} "
                 f"{s.get('comment') or ''}" for s in result["symbols"][:60]]  # fmt: skip
        self._set_lines("symbols", [*lines, f"({result['total']} matching)"])


def _colour(colour: int | None) -> tuple[int, ...]:
    """The text colour of a memory cell or legend line (see `memory_layout`)."""
    if colour is None:
        return DEFAULT_COLOUR
    return AREA_COLOUR if colour == AREA else VARIABLE_COLOURS[colour]
