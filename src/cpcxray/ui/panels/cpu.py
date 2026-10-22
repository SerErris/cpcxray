"""CPU tab: registers (editable), paging per 16 KB, disassembly; step out, run to (FR-DBG-02..05).

The disassembly follows the PC unless an address is given, and marks each 16 KB quarter with
the ROM or RAM block the CPU reads there now (FR-DBG-12).
"""

from __future__ import annotations

from typing import Any

import dearpygui.dearpygui as dpg

from cpcxray.session.errors import SessionError
from cpcxray.ui.panels.base import Tab
from cpcxray.ui.panels.text import (
    mark_pc,
    paging_lines,
    parse_assignments,
    registers_lines,
    with_paging,
)

DISASSEMBLY_LINES = 24


class CpuTab(Tab):
    """Registers, register editing, paging, disassembly, step out / run to."""

    title = "CPU"
    message = "cpu_message"

    def build(self) -> None:
        """Create the widgets."""
        self.text("registers")
        with dpg.group(horizontal=True):
            self.entry("register_edit", "hl=4000, a=5", 200, self.set_registers)
            self.button("Step out", self.loop.step_out)
            self.entry("run_to", "run to address", 110, self.run_to)
        self.text("cpu_message")
        self.text("paging")
        with dpg.group(horizontal=True):
            dpg.add_text("Disassembly at")
            self.input("disassembly_address", "PC (empty) or address/label", 200)
        self.text("disassembly")

    def set_registers(self) -> Any:
        """Apply "name=value" assignments from the register input."""
        return self.session.registers_set(parse_assignments(self.value("register_edit")))

    def run_to(self) -> None:
        """Start a free run that stops at the given address."""
        address = self.session._address(self.value("run_to"))  # pylint: disable=protected-access
        self.loop.run_to(address)

    def refresh(self) -> None:
        """Registers, paging and disassembly (following the PC unless an address is given)."""
        session = self.session
        self.set_lines("registers", registers_lines(session.registers_get()))
        self.set_lines("paging", paging_lines(session.paging_get()))
        address = self.value("disassembly_address").strip() or None
        try:
            listing = session.disassemble(address, DISASSEMBLY_LINES)
            lines = with_paging(listing["lines"], listing["paging"])
        except SessionError as error:
            lines = [f"error: {error}"]
        pc = session.machine.cpu.pc
        self.set_lines("disassembly", mark_pc(lines, pc, set(session.breakpoints.addresses)))
