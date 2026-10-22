"""CRTC 6845: a register file only, no timing (design.md §6.8, D-06).

Ports with A14 = 0; bits 9-8 of the port select the function:

| Port  | A9 A8 | Function                       |
|-------|-------|--------------------------------|
| &BCxx | 0 0   | select register (0-31)         |
| &BDxx | 0 1   | write the selected register    |
| &BExx | 1 0   | status (not on type 0/1: &FF)  |
| &BFxx | 1 1   | read the selected register     |

Only registers 0-17 exist. Reading returns the value for the registers readable on a 464's
CRTC (R12-R17); all others read as 0. The renderer uses R1 (displayed characters per line),
R6 (displayed character rows), R9 (scan lines per character - 1) and R12/R13 (screen start
address, used for hardware scrolling).
"""

from __future__ import annotations

from typing import Any

from cpcxray.devices import NOT_DRIVEN

REGISTER_COUNT = 18
READABLE_REGISTERS = frozenset({12, 13, 14, 15, 16, 17})
REGISTER_NAMES = (
    "H total", "H displayed", "H sync pos", "Sync widths", "V total", "V adjust",
    "V displayed", "V sync pos", "Interlace", "Max raster", "Cursor start", "Cursor end",
    "Start high", "Start low", "Cursor high", "Cursor low", "Pen high", "Pen low",
)  # fmt: skip


class Crtc:
    """The CRTC register file.

    Attributes:
        registers: Values of R0-R17.
        selected: Number of the selected register.
    """

    def __init__(self) -> None:
        """Create the CRTC with all registers 0."""
        self.registers = [0] * REGISTER_COUNT
        self.selected = 0

    def reset(self) -> None:
        """Clear all registers (the firmware programs them at boot)."""
        self.registers = [0] * REGISTER_COUNT
        self.selected = 0

    @staticmethod
    def decodes(port: int) -> bool:
        """The CRTC responds to A14 = 0."""
        return port & 0x4000 == 0

    def io_out(self, port: int, value: int) -> None:
        """Select a register (&BCxx) or write the selected one (&BDxx)."""
        function = (port >> 8) & 0x03
        if function == 0:
            self.selected = value & 0x1F
        elif function == 1 and self.selected < REGISTER_COUNT:
            self.registers[self.selected] = value

    def io_in(self, port: int) -> int | None:
        """Read the selected register (&BFxx) or the status (&BExx, absent: &FF)."""
        function = (port >> 8) & 0x03
        if function == 3:
            if self.selected in READABLE_REGISTERS:
                return self.registers[self.selected]
            return 0x00
        if function == 2:
            return 0xFF
        return NOT_DRIVEN  # select/write ports do not drive the bus when read

    def screen_start(self) -> int:
        """Return the screen start in the CRTC's 14-bit address space (R12/R13)."""
        return (self.registers[12] & 0x3F) << 8 | self.registers[13]

    def state(self) -> dict[str, Any]:
        """Return the registers for the device view (FR-DBG-05), with names and the screen.

        "screen": where the screen starts in RAM (R12/R13: bits 13-12 of R12 the 16 KB page,
        the rest a word offset), characters per line (R1), character lines (R6) and scan lines
        per character (R9 + 1).
        """
        registers = self.registers
        start = self.screen_start()
        page, offset = (start >> 12 & 0x03) * 0x4000, (start & 0x03FF) * 2
        return {
            "selected": self.selected,
            "registers": list(registers),
            "names": list(REGISTER_NAMES),
            "screen": {
                "start": f"{(page + offset) & 0xFFFF:04X}",
                "characters": registers[1],
                "lines": registers[6],
                "scan_lines": registers[9] + 1,
            },
        }
