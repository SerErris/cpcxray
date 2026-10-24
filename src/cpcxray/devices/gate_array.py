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
| 11       | RAM configuration (6128 banking): bits 2-0 the configuration (FR-MCH-10)    |

Interrupts (D-05): the real Gate Array's scan line counter R52 and interrupt flip-flop INT. The
scan lines are 64 µs steps of the machine clock, the same frame that the VSYNC bit of port B
uses (devices/video_timing.py; no CRTC timing, D-06). Four rules (Soft 968, Appendix XII):

1. Every scan line R52 counts up; when it reaches 52 it goes back to 0 and INT is latched
   (300 Hz).
2. Two lines after VSYNC starts (frame line 2): INT is latched if R52 >= 32, and R52 = 0 in
   any case. This resynchronisation puts the first interrupt of a frame 128 µs into VSYNC, so a
   loop waiting for VSYNC (MC WAIT FLYBACK) and the interrupt handler's frame flyback check
   both see it, and any phase error is gone within one frame.
3. The CPU's acknowledge clears INT and bit 5 of R52 (a late acknowledge delays the next one).
4. RMR bit 4 clears INT and R52.

Rule 1 runs before rule 2: in the steady state R52 reaches 52 on frame line 2 itself, so there
is exactly one interrupt there. INT stays pending until acknowledged, so one interrupt survives
a long DI phase. `interrupt_in_us` (device view) is derived from R52 and the clock.

Reset state (researched for phase 4: MAME writes &89 on reset; the 464 OS ROM's first
instruction writes &89 as well): mode 1, lower ROM enabled, upper ROM disabled, interrupt
counter at zero and no interrupt pending, RAM configuration 0. On the 6128 the RAM
configuration belongs to a separate chip (PAL) at the same port; it is part of this device here
because the CPU sees it through the same writes. The memory map ignores it on a 464.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cpcxray.devices import NOT_DRIVEN
from cpcxray.devices.video_timing import (
    FRAME_LINES,
    INTERRUPT_LINES,
    LINE_US,
    RESYNC_LINE,
)

BORDER_PEN = 16
RESYNC_MINIMUM = 32  # rule 2 latches INT only when R52 bit 5 is set


class GateArray:  # pylint: disable=too-many-instance-attributes  # the device's register state
    """The Gate Array's registers as seen through I/O.

    Attributes:
        pen: Selected pen 0-15, or 16 for the border.
        inks: Hardware colour per pen 0-15 and the border (index 16).
        mode: Screen mode 0-2 (3 is undocumented and treated like 0 by the renderer).
        lower_rom_enabled: RMR bit 2 clear.
        upper_rom_enabled: RMR bit 3 clear.
        ram_config: Last RAM configuration value, bits 5-0 (function 11; banking uses 2-0).
        interrupt_pending: An interrupt is waiting for the CPU (the flip-flop INT).
        r52: The scan line counter, 0-51 (bit 5 matters for rules 2 and 3).
        next_line_us: Machine clock time of the next scan line boundary; the machine calls
            `tick` once the clock reaches it.
    """

    def __init__(
        self,
        set_rom_enables: Callable[[bool, bool], None],
        set_ram_configuration: Callable[[int], None] | None = None,
        clock_us: Callable[[], int] | None = None,
    ) -> None:
        """Create the Gate Array.

        Args:
            set_rom_enables: Called with (lower enabled, upper enabled) when RMR changes them.
            set_ram_configuration: Called with the configuration 0-7 of a function 11 write.
            clock_us: Returns the machine clock in µs (for `interrupt_in_us`); without it the
                time counts from the start of the current scan line.
        """
        self._set_rom_enables = set_rom_enables
        self._set_ram_configuration = set_ram_configuration
        self._clock_us = clock_us
        self.pen = 0
        self.inks = [0] * 17
        self.mode = 1
        self.lower_rom_enabled = True
        self.upper_rom_enabled = False
        self.ram_config = 0
        self.interrupt_pending = False
        self.r52 = 0
        self.next_line_us = LINE_US

    def reset(self) -> None:
        """Hardware reset (clock 0): mode 1, lower ROM on, upper ROM off, R52 = 0, no INT."""
        self.pen = 0
        self.inks = [0] * 17
        self.write_ram_config(0)
        self.write_rmr(0x89 & 0x1F)  # same state the OS ROM sets with its first instruction
        self.interrupt_pending = False
        self.r52 = 0
        self.next_line_us = LINE_US

    def set_clock(self, clock_us: int) -> None:
        """Continue counting at `clock_us` (a snapshot load or a clock set from outside)."""
        self.next_line_us = (clock_us // LINE_US + 1) * LINE_US

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
            self.write_ram_config(value & 0x3F)

    def write_ram_config(self, value: int) -> None:
        """Apply a RAM configuration (function 11): bits 2-0 select the 6128 banking."""
        self.ram_config = value
        if self._set_ram_configuration is not None:
            self._set_ram_configuration(value & 0x07)

    def write_rmr(self, rmr: int) -> None:
        """Apply RMR bits 4-0: mode, ROM enables, interrupt reset."""
        self.mode = rmr & 0x03
        self.lower_rom_enabled = not rmr & 0x04
        self.upper_rom_enabled = not rmr & 0x08
        self._set_rom_enables(self.lower_rom_enabled, self.upper_rom_enabled)
        if rmr & 0x10:  # rule 4: clear a pending interrupt and R52 (D-05)
            self.interrupt_pending = False
            self.r52 = 0

    def tick(self, clock_us: int) -> None:
        """Apply rules 1 and 2 for every scan line that began up to `clock_us`.

        The machine calls it after an instruction once the clock has reached `next_line_us`;
        usually one line has begun, after a long jump of the clock several.
        """
        while clock_us >= self.next_line_us:
            line = self.next_line_us // LINE_US  # the scan line that just began
            self.next_line_us += LINE_US
            self.r52 += 1  # rule 1: one more line
            if self.r52 == INTERRUPT_LINES:
                self.r52 = 0
                self.interrupt_pending = True
            if line % FRAME_LINES == RESYNC_LINE:  # rule 2, after rule 1 (one INT, not two)
                if self.r52 >= RESYNC_MINIMUM:
                    self.interrupt_pending = True
                self.r52 = 0

    @property
    def line_counter(self) -> int:
        """The scan line counter R52 (0-51)."""
        return self.r52

    @property
    def interrupt_in_us(self) -> int:
        """µs until the next interrupt is latched (rule 1, or earlier by the resync of rule 2)."""
        first_line = self.next_line_us // LINE_US  # the line the next boundary begins
        to_count_end = INTERRUPT_LINES - self.r52  # boundaries until R52 reaches 52
        to_resync = (RESYNC_LINE - first_line) % FRAME_LINES + 1  # boundaries until line 2
        boundaries = to_count_end
        if to_resync < to_count_end:  # the resync comes first: INT there, or 52 lines after
            boundaries = (
                to_resync if self.r52 + to_resync >= RESYNC_MINIMUM else to_resync + INTERRUPT_LINES
            )
        return self.next_line_us + (boundaries - 1) * LINE_US - self._now()

    @property
    def frame_line(self) -> int:
        """The current scan line of the frame, 0-311 (VSYNC: lines 0-7)."""
        return self._now() // LINE_US % FRAME_LINES

    def _now(self) -> int:
        """The machine clock, or the start of the current scan line without one."""
        return self._clock_us() if self._clock_us is not None else self.next_line_us - LINE_US

    def acknowledge_interrupt(self) -> None:
        """The CPU accepted the interrupt (M1 + IORQ): clear INT and R52 bit 5 (rule 3)."""
        self.interrupt_pending = False
        self.r52 &= 0x1F

    def state(self) -> dict[str, Any]:
        """Return the registers for the device view (FR-DBG-05)."""
        return {
            "mode": self.mode,
            "pen": "border" if self.pen == BORDER_PEN else self.pen,
            "inks": self.inks[:16],
            "border": self.inks[BORDER_PEN],
            "lower_rom_enabled": self.lower_rom_enabled,
            "upper_rom_enabled": self.upper_rom_enabled,
            "ram_config": self.ram_config,
            "interrupt_pending": self.interrupt_pending,
            "interrupt_in_us": self.interrupt_in_us,
            "line_counter_r52": self.line_counter,
            "frame_line": self.frame_line,
        }
