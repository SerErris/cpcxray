"""Devices tab: Gate Array, ROM select, PPI, CRTC, FDC and keyboard matrix (task 5, FR-DBG-05).

Layout agreed with the owner on 2026-10-22 (design canvas, artboard "B: two columns"): two
columns of equal height - left the Gate Array (border and 16 pens as colour swatches with
firmware/hardware numbers; pens the mode does not use have grey numbers but keep their true
colour, as a faded swatch read as another colour (owner, 2026-10-22); the pen selected for ink
writes ringed yellow; ROM/RAM badges), ROM select and the PPI 8255; right the CRTC registers
(the selected one highlighted) with the screen layout. Full width below: the FDC (motor, MSR in
words, one row per drive with LED, disk, state and head position, the last commands), then the
keyboard matrix (10 lines x 8 keys named from the OS ROM's key map, held keys yellow, each
line's value). No PSG box: the PSG is emulated only as far as the keyboard needs.

Everything comes from `Session.devices_get` (the MCP tool returns the same). Unlike the other
tabs, it also refreshes once per second while the machine runs.
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.session.errors import SessionError
from cpcxray.ui.palette import HARDWARE_RGB
from cpcxray.ui.panels import colours
from cpcxray.ui.panels.base import Tab
from cpcxray.ui.panels.text import (
    colour_numbers,
    crtc_footer,
    drive_flags,
    gate_array_lines,
    keyboard_summary,
    ppi_lines,
    rom_select_lines,
)
from cpcxray.ui.panels.widgets import (
    badge,
    section,
    set_badge,
    set_swatch,
    swatch,
)

LEFT_WIDTH = 336  # Gate Array: four pens with their numbers per row
RIGHT_WIDTH = 270
COLUMN_GAP = 8  # Dear PyGui's horizontal spacing between the two columns
FULL_WIDTH = LEFT_WIDTH + COLUMN_GAP + RIGHT_WIDTH  # FDC and keyboard: as wide as the columns
COLUMNS_HEIGHT = 364  # both columns end on the same line (agreed)
GATE_ARRAY_HEIGHT = 164
BOX_GAP = 2  # vertical space between two boxes of a column
PEN_SWATCH = 16
CRTC_REGISTERS = 18
KEY_WIDTH = 54
FDC_COMMANDS = 4
DRIVES = ("A", "B")


class DevicesTab(Tab):
    """The device boxes (see the module docstring)."""

    title = "Devices"
    message = "devices_message"
    live_seconds = 1.0  # inks, CRTC, drives and keys follow a running program (owner)

    def build(self) -> None:
        """Create the boxes; `refresh` fills them."""
        self.text("devices_message", color=colours.ERROR, show=False)
        with dpg.group(horizontal=True, horizontal_spacing=COLUMN_GAP):
            with dpg.group(width=LEFT_WIDTH):
                self._build_gate_array()
                rest = (COLUMNS_HEIGHT - GATE_ARRAY_HEIGHT - 2 * BOX_GAP) // 2
                with section(self.styles, "ROM select", height=rest, width=LEFT_WIDTH):
                    self.text("devices_rom_select")
                with section(self.styles, "PPI 8255", height=rest, width=LEFT_WIDTH):
                    self.text("devices_ppi")
            self._build_crtc()
        self._build_fdc()
        self._build_keyboard()

    def _build_gate_array(self) -> None:
        """Gate Array: header, border, the 4 x 4 pens, badges, pen select and interrupt line."""
        items = self.items
        with section(self.styles, height=GATE_ARRAY_HEIGHT, width=LEFT_WIDTH):
            with dpg.group(horizontal=True):
                dpg.add_text("Gate Array", color=colours.HEADING)
                self.text("ga_header", color=colours.TEXT_DIM)
            with dpg.group(horizontal=True):
                items["ga_border_swatch"] = swatch((0, 0, 0), PEN_SWATCH)
                self.text("ga_border")
            for row in range(4):
                with dpg.group(horizontal=True):
                    for column in range(4):
                        pen = row * 4 + column
                        items[f"ga_pen_swatch_{pen}"] = swatch((0, 0, 0), PEN_SWATCH)
                        self.text(f"ga_pen_{pen}")
            with dpg.group(horizontal=True):
                items["ga_lower"] = badge(self.styles, "lower ROM")
                items["ga_upper"] = badge(self.styles, "upper ROM")
                items["ga_ram"] = badge(self.styles, "RAM 0")
            self.text("ga_footer", color=colours.TEXT_DIM)

    def _build_crtc(self) -> None:
        """CRTC: the 18 registers with names, the selected one highlighted; the screen layout."""
        with section(self.styles, height=COLUMNS_HEIGHT, width=RIGHT_WIDTH):
            with dpg.group(horizontal=True):
                dpg.add_text("CRTC 6845", color=colours.HEADING)
                self.text("crtc_selected", color=colours.TEXT_DIM)
            for number in range(CRTC_REGISTERS):
                self.text(f"crtc_{number}")
            self.text("crtc_footer", color=colours.TEXT_DIM)

    def _build_fdc(self) -> None:
        """FDC: header with the MSR, motor line, one row per drive, the last commands."""
        with section(self.styles, width=FULL_WIDTH):
            with dpg.group(horizontal=True):
                dpg.add_text("FDC µPD765", color=colours.HEADING)
                self.text("fdc_msr", color=colours.TEXT_DIM)
            with dpg.group(horizontal=True):
                self.text("fdc_motor_led")
                self.text("fdc_motor")
            for drive in DRIVES:
                with dpg.group(horizontal=True):
                    self.text(f"fdc_led_{drive}")
                    self.text(f"fdc_disk_{drive}")
                    self.text(f"fdc_flags_{drive}", color=colours.TEXT_DIM)
                with dpg.group(horizontal=True):
                    dpg.add_text("   head", color=colours.TEXT_DIM)
                    self.items[f"fdc_head_{drive}"] = dpg.add_progress_bar(width=360, height=8)
                    self.text(f"fdc_cylinder_{drive}", color=colours.TEXT_DIM)
            dpg.add_text("last commands", color=colours.TEXT_DIM)
            self.text("fdc_commands", color=colours.TEXT_DIM)

    def _build_keyboard(self) -> None:
        """Keyboard matrix: 10 lines of 8 key cells and the line's value."""
        with section(self.styles, width=FULL_WIDTH):
            with dpg.group(horizontal=True):
                dpg.add_text("Keyboard matrix", color=colours.HEADING)
                self.text("keyboard_summary", color=colours.TEXT_DIM)
            for line in range(10):
                with dpg.group(horizontal=True, horizontal_spacing=3):
                    dpg.add_text(f"{line}", color=colours.TEXT_DIM)
                    for bit in range(8):
                        self.items[f"key_{line}_{bit}"] = badge(self.styles, "", "key",
                                                                width=KEY_WIDTH)  # fmt: skip
                    self.text(f"key_value_{line}", color=colours.TEXT_DIM)
            dpg.add_text("Line 0-9 by bit 0-7 as the firmware scans them; right: the line's value "
                         "(bit clear = pressed).", color=colours.TEXT_DIM, wrap=580)  # fmt: skip

    # -- refreshing ------------------------------------------------------------------------------

    def refresh(self) -> None:
        """Fill every box from `devices_get`."""
        try:
            devices = self.session.devices_get()
        except SessionError as error:
            dpg.set_value(self.items["devices_message"], f"error: {error}")
            dpg.show_item(self.items["devices_message"])
            return
        dpg.hide_item(self.items["devices_message"])
        self._refresh_gate_array(devices["gate_array"])
        self.set_lines("devices_rom_select", rom_select_lines(devices["rom_select"]))
        self.set_lines("devices_ppi", ppi_lines(devices["system_io"]))
        self._refresh_crtc(devices["crtc"])
        self._refresh_fdc(devices["fdc"])
        self._refresh_keyboard(devices["key_lines"], devices["typing"])

    def _refresh_gate_array(self, gate_array: dict[str, Any]) -> None:
        """Border, pens (unused ones with grey numbers, the selected pen ringed), badges, footer."""
        items, styles = self.items, self.styles
        header, footer = gate_array_lines(gate_array)
        dpg.set_value(items["ga_header"], header)
        dpg.set_value(items["ga_footer"], footer)
        border = gate_array["border"]
        set_swatch(items["ga_border_swatch"], HARDWARE_RGB[border])
        dpg.set_value(
            items["ga_border"], f"border {colour_numbers(gate_array['border_firmware'], border)}"
        )
        selected = gate_array["pen"]
        dpg.bind_item_theme(
            items["ga_border_swatch"], styles.swatch_here if selected == "border" else styles.swatch
        )
        for pen, (hardware, firmware) in enumerate(
            zip(gate_array["inks"], gate_array["inks_firmware"], strict=True)
        ):
            used = pen < gate_array["pens_used"]
            set_swatch(items[f"ga_pen_swatch_{pen}"], HARDWARE_RGB[hardware])
            dpg.bind_item_theme(
                items[f"ga_pen_swatch_{pen}"],
                styles.swatch_here if pen == selected else styles.swatch,
            )
            dpg.set_value(items[f"ga_pen_{pen}"], f"{colour_numbers(firmware, hardware):<5}")
            dpg.configure_item(items[f"ga_pen_{pen}"],
                               color=colours.TEXT if used else colours.TEXT_OFF)  # fmt: skip
        lower, upper = gate_array["lower_rom_enabled"], gate_array["upper_rom_enabled"]
        set_badge(styles, items["ga_lower"], f"lower ROM {'on' if lower else 'off'}",
                  "ok" if lower else "plain")  # fmt: skip
        set_badge(styles, items["ga_upper"], f"upper ROM {'on' if upper else 'off'}",
                  "ok" if upper else "plain")  # fmt: skip
        set_badge(styles, items["ga_ram"], f"RAM {gate_array['ram_config']}", "plain")

    def _refresh_crtc(self, crtc: dict[str, Any]) -> None:
        """Registers with names; the selected one in yellow."""
        selected = crtc["selected"]
        dpg.set_value(self.items["crtc_selected"], f"sel R{selected}")
        for number, (name, value) in enumerate(zip(crtc["names"], crtc["registers"], strict=True)):
            item = self.items[f"crtc_{number}"]
            dpg.set_value(item, f"R{number:<3}{name:<14}{value:>4}")
            dpg.configure_item(item, color=colours.HERE if number == selected else colours.TEXT)
        dpg.set_value(self.items["crtc_footer"], crtc_footer(crtc))

    def _refresh_fdc(self, fdc: dict[str, Any]) -> None:
        """MSR, motor, drives (LED: the drive addressed last while the motor runs), commands."""
        items = self.items
        dpg.set_value(items["fdc_msr"], f"MSR &{fdc['msr']:02X} · {fdc['msr_meaning']}")
        motor = fdc["motor_on"]
        _led(items["fdc_motor_led"], motor)
        dpg.set_value(items["fdc_motor"], f"motor {'on' if motor else 'off'} · drive "
                      f"{fdc['selected_drive']} selected")  # fmt: skip
        for letter in DRIVES:
            drive = fdc["drives"].get(letter)
            if drive is None:
                continue
            _led(items[f"fdc_led_{letter}"], motor and fdc["selected_drive"] == letter)
            disk = drive["disk"]
            name = "no disk" if disk is None else disk.replace("\\", "/").rsplit("/", 1)[-1]
            dpg.set_value(items[f"fdc_disk_{letter}"], f"{letter}: {name}")
            dpg.set_value(items[f"fdc_flags_{letter}"], drive_flags(drive))
            cylinders = drive["cylinders"]
            dpg.set_value(items[f"fdc_head_{letter}"], min(1.0, drive["cylinder"] / cylinders))
            dpg.set_value(items[f"fdc_cylinder_{letter}"],
                          f"cylinder {drive['cylinder']} / {cylinders}")  # fmt: skip
        commands = fdc["recent_commands"][-FDC_COMMANDS:] or ["none yet"]
        self.set_lines("fdc_commands", commands)

    def _refresh_keyboard(self, key_lines: list[dict[str, Any]], typing: bool) -> None:
        """Key cells (held ones yellow) and each line's value."""
        styles = self.styles
        dpg.set_value(self.items["keyboard_summary"], keyboard_summary(key_lines, typing))
        for line in key_lines:
            number = line["line"]
            for bit, label in enumerate(line["keys"]):
                held = bit in line["held"]
                set_badge(styles, self.items[f"key_{number}_{bit}"], label or " ",
                          "key_held" if held else "key")  # fmt: skip
            dpg.set_value(self.items[f"key_value_{number}"], f"{line['value']:02X}")


def _led(item: int | str, lit: bool) -> None:
    """Show a round LED (●) lit green or dark."""
    dpg.set_value(item, "●")
    dpg.configure_item(item, color=colours.LED_ON if lit else colours.LED_OFF)
