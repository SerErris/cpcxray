"""SystemIO: replaces the 8255 PPI and the AY-3-8912 sound chip (design.md §5.2, §6.9, D-07).

Ports with A11 = 0; bits 9-8 select the PPI port:

| Port  | PPI port  | Use on the CPC                                                        |
|-------|-----------|-----------------------------------------------------------------------|
| &F4xx | A         | PSG data bus: register number out, keyboard row in                   |
| &F5xx | B (in)    | bit 0 VSYNC, bits 1-3 manufacturer, bit 4 50 Hz, 5 /EXP, 6 printer   |
|       |           | busy, 7 cassette in - emulated as a constant plus the VSYNC bit       |
| &F6xx | C (out)   | bits 3-0 keyboard row, bits 7-6 PSG function; 5-4 cassette (ignored) |
| &F7xx | control   | mode set ignored; "bit set/reset" (bit 7 = 0) changes one bit of C  |

The firmware reads a keyboard row with: OUT &F4xx = 14, OUT &F6xx with bits 7-6 = 11 (select
PSG register 14), OUT &F6xx = &40 + row, IN &F4xx → the row's key bits (0 = pressed). That is
all of the PSG this emulator needs: other PSG registers and all sound are ignored.

VSYNC (D-05) is computed when port B is read: `(clock_us % 19968) < 512`, i.e. the first 8
scan lines of each 19,968 µs frame. The port B constant (manufacturer and 50 Hz bits) is
configurable; &1E means Amstrad, 50 Hz (to be confirmed by the boot milestone, §4).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cpcxray.devices.keyboard import Keyboard

FRAME_US = 19968  # 312 lines of 64 µs (§5.3)
VSYNC_US = 512  # 8 lines
KEYBOARD_PSG_REGISTER = 14
DEFAULT_PORT_B = 0x1E  # Amstrad (bits 3-1 = 111), 50 Hz (bit 4); VSYNC added on read


class SystemIO:  # pylint: disable=too-many-instance-attributes  # mirrors the PPI/PSG latches
    """The PPI/PSG ports as far as the firmware needs them.

    Attributes:
        keyboard: The key matrix read through PSG register 14.
        port_b_constant: Port B value without the VSYNC bit.
        port_a: Last value written to port A (PSG data bus).
        port_c: Last value of port C (row bits 3-0, PSG function bits 7-6).
        psg_register: PSG register selected by the "select register" function.
    """

    def __init__(
        self, keyboard: Keyboard, clock_us: Callable[[], int], port_b_constant: int = DEFAULT_PORT_B
    ) -> None:
        """Create SystemIO.

        Args:
            keyboard: The key matrix.
            clock_us: Returns the machine clock in µs (for VSYNC).
            port_b_constant: Port B bits 7-1 (bit 0 is VSYNC).
        """
        self.keyboard = keyboard
        self._clock_us = clock_us
        self.port_b_constant = port_b_constant & 0xFE
        self.port_a = 0xFF
        self.port_c = 0x00
        self.psg_register = 0

    def reset(self) -> None:
        """PPI reset: port latches cleared, no PSG register selected."""
        self.port_a = 0xFF
        self.port_c = 0x00
        self.psg_register = 0

    @staticmethod
    def decodes(port: int) -> bool:
        """SystemIO responds to A11 = 0."""
        return port & 0x0800 == 0

    def vsync(self) -> bool:
        """Return True during the first 512 µs of each frame (D-05)."""
        return self._clock_us() % FRAME_US < VSYNC_US

    def io_out(self, port: int, value: int) -> None:
        """Handle writes to ports A, C and the control port (B is input only)."""
        select = (port >> 8) & 0x03
        if select == 0:
            self.port_a = value
        elif select == 2:
            self._write_port_c(value)
        elif select == 3 and not value & 0x80:  # bit set/reset of port C
            bit = (value >> 1) & 0x07
            new_c = self.port_c | (1 << bit) if value & 1 else self.port_c & ~(1 << bit)
            self._write_port_c(new_c & 0xFF)

    def _write_port_c(self, value: int) -> None:
        """Store port C; PSG function 11 latches the register number from port A."""
        self.port_c = value
        if value & 0xC0 == 0xC0:
            self.psg_register = self.port_a & 0x0F

    def io_in(self, port: int) -> int | None:
        """Read port B (constant + VSYNC) or port A (keyboard row via PSG register 14)."""
        select = (port >> 8) & 0x03
        if select == 1:
            return self.port_b_constant | (1 if self.vsync() else 0)
        if select == 0 and self.psg_register == KEYBOARD_PSG_REGISTER:
            return self.keyboard.read_row(self.port_c & 0x0F)
        return 0xFF

    def state(self) -> dict[str, Any]:
        """Return the latches for the device view."""
        return {
            "port_a": self.port_a,
            "port_b": self.port_b_constant | (1 if self.vsync() else 0),
            "port_c": self.port_c,
            "psg_register": self.psg_register,
            "keyboard_row": self.port_c & 0x0F,
            "vsync": self.vsync(),
        }
