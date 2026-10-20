"""Gate Array: pens, inks, border, screen mode, ROM enables, interrupts (design.md §6.7).

Only the I/O-visible behaviour is modelled (FR-MCH-07). The Gate Array is written via ports
with A15 = 0 and A14 = 1 (&7Fxx); it cannot be read. Bits 7-6 of the value select the
function:

| Bits 7-6 | Function                                                                    |
|----------|-----------------------------------------------------------------------------|
| 00       | select pen: bits 3-0 the pen, bit 4 set selects the border                  |
| 01       | set the colour (hardware colour number, bits 4-0) of the selected pen       |
| 10       | RMR: bits 1-0 mode, bit 2 lower ROM off, bit 3 upper ROM off, bit 4 resets  |
|          | the interrupt counter and clears a pending interrupt                        |
| 11       | RAM configuration (6128, later scope)                                       |

Interrupts (D-05): INT is latched every 3,328 µs (300 Hz, 52 scan lines) and stays pending
until the CPU acknowledges it, so one interrupt survives a long DI phase. RMR bit 4 clears a
pending interrupt and restarts the period. The line counter and its resynchronisation at VSYNC
are not modelled.

Reset state (researched for phase 4: MAME writes &89 on reset; the 464 OS ROM's first
instruction writes &89 as well): mode 1, lower ROM enabled, upper ROM disabled, interrupt
counter at zero and no interrupt pending.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cpcxray.devices import NOT_DRIVEN

INTERRUPT_PERIOD_US = 3328  # 52 lines of 64 µs: 300 Hz (§5.3)
BORDER_PEN = 16


class GateArray:  # pylint: disable=too-many-instance-attributes  # the device's register state
    """The Gate Array's registers as seen through I/O.

    Attributes:
        pen: Selected pen 0-15, or 16 for the border.
        inks: Hardware colour per pen 0-15 and the border (index 16).
        mode: Screen mode 0-2 (3 is undocumented and treated like 0 by the renderer).
        lower_rom_enabled: RMR bit 2 clear.
        upper_rom_enabled: RMR bit 3 clear.
        ram_config: Last RAM configuration value (function 11; used by the 6128 only).
        interrupt_pending: An interrupt is waiting for the CPU.
        next_interrupt_us: Clock time at which the next interrupt is latched.
    """

    def __init__(
        self,
        set_rom_enables: Callable[[bool, bool], None],
        clock_us: Callable[[], int],
    ) -> None:
        """Create the Gate Array.

        Args:
            set_rom_enables: Called with (lower enabled, upper enabled) when RMR changes them.
            clock_us: Returns the machine's clock in µs (for restarting the interrupt period).
        """
        self._set_rom_enables = set_rom_enables
        self._clock_us = clock_us
        self.pen = 0
        self.inks = [0] * 17
        self.mode = 1
        self.lower_rom_enabled = True
        self.upper_rom_enabled = False
        self.ram_config = 0
        self.interrupt_pending = False
        self.next_interrupt_us = INTERRUPT_PERIOD_US

    def reset(self) -> None:
        """Hardware reset: mode 1, lower ROM on, upper ROM off, interrupt timer restarted."""
        self.pen = 0
        self.inks = [0] * 17
        self.ram_config = 0
        self.write_rmr(0x89 & 0x1F)  # same state the OS ROM sets with its first instruction
        self.interrupt_pending = False
        self.next_interrupt_us = self._clock_us() + INTERRUPT_PERIOD_US

    @staticmethod
    def decodes(port: int) -> bool:
        """The Gate Array responds to A15 = 0, A14 = 1."""
        return port & 0xC000 == 0x4000

    def io_in(self, port: int) -> int | None:
        """The Gate Array cannot be read: it does not drive the data bus."""
        del port
        return NOT_DRIVEN

    def io_out(self, port: int, value: int) -> None:
        """Handle a write to the Gate Array (see the module docstring for the functions)."""
        del port
        function = value >> 6
        if function == 0:
            self.pen = BORDER_PEN if value & 0x10 else value & 0x0F
        elif function == 1:
            self.inks[self.pen] = value & 0x1F
        elif function == 2:
            self.write_rmr(value & 0x1F)
        else:
            self.ram_config = value & 0x3F

    def write_rmr(self, rmr: int) -> None:
        """Apply RMR bits 4-0: mode, ROM enables, interrupt reset."""
        self.mode = rmr & 0x03
        self.lower_rom_enabled = not rmr & 0x04
        self.upper_rom_enabled = not rmr & 0x08
        self._set_rom_enables(self.lower_rom_enabled, self.upper_rom_enabled)
        if rmr & 0x10:  # clear a pending interrupt, restart the 300 Hz period (D-05)
            self.interrupt_pending = False
            self.next_interrupt_us = self._clock_us() + INTERRUPT_PERIOD_US

    def tick(self, clock_us: int) -> None:
        """Latch an interrupt when its time has come (called after every instruction)."""
        if clock_us >= self.next_interrupt_us:
            self.interrupt_pending = True
            self.next_interrupt_us += INTERRUPT_PERIOD_US

    def acknowledge_interrupt(self) -> None:
        """The CPU accepted the interrupt (M1 + IORQ): clear the request."""
        self.interrupt_pending = False

    def state(self) -> dict[str, Any]:
        """Return the registers for the device view (FR-DBG-05)."""
        return {
            "mode": self.mode,
            "pen": "border" if self.pen == BORDER_PEN else self.pen,
            "inks": self.inks[:16],
            "border": self.inks[BORDER_PEN],
            "lower_rom_enabled": self.lower_rom_enabled,
            "upper_rom_enabled": self.upper_rom_enabled,
            "interrupt_pending": self.interrupt_pending,
            "next_interrupt_us": self.next_interrupt_us,
        }
