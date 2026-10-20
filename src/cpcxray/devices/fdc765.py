"""µPD765 floppy disk controller - stub with no drive ready (design.md §6.11; FR-FDC-05).

Phase 10 implements the full controller with disk images. Until then this stub follows the
µPD765 command / result protocol closely enough that disk ROMs (AMSDOS, VDOS) see a controller
whose drives are all "not ready" instead of a bus that reads &FF - which their result-phase
loops (wait for RQM, read while DIO is set) would read forever, filling RAM (seen with VDOS in
phase 7).

Ports (DDI-1 decoding, also used by VDOS: A10 = 0 and A7 = 0):

| Port  | A8 A0 | Function                                     |
|-------|-------|----------------------------------------------|
| &FA7E | 0 x   | motor on/off latch (write)                   |
| &FB7E | 1 0   | main status register MSR (read)              |
| &FB7F | 1 1   | data register: command bytes in, results out |

MSR bits: 7 RQM (ready for a byte), 6 DIO (1 = the controller has a byte for the CPU), 4 CB
(command in progress). Idle MSR = &80. After the command bytes, the result bytes are offered
with MSR = &D0 until read. Results for a drive that is not ready:

* data commands (read/write/format/scan/read ID): ST0 = &48 | head | unit (abnormal termination,
  not ready), ST1 = ST2 = 0, then C, H, R, N from the command;
* SENSE DRIVE STATUS: ST3 with only head and unit (not ready, no track 0);
* SENSE INTERRUPT STATUS after RECALIBRATE/SEEK: ST0 = &68 | unit (abnormal, seek end, not
  ready), PCN = 0; without a pending interrupt: &80 (invalid command);
* SPECIFY, RECALIBRATE, SEEK: no result phase; unknown commands: ST0 = &80.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from cpcxray.devices import NOT_DRIVEN

MSR_RQM = 0x80
MSR_DIO = 0x40
MSR_CB = 0x10
ST0_ABNORMAL = 0x40
ST0_INVALID = 0x80
ST0_SEEK_END = 0x20
ST0_NOT_READY = 0x08

# command code (low 5 bits) → number of command bytes including the command itself
COMMAND_LENGTHS = {
    0x02: 9, 0x03: 3, 0x04: 2, 0x05: 9, 0x06: 9, 0x07: 2, 0x08: 1, 0x09: 9,
    0x0A: 2, 0x0C: 9, 0x0D: 6, 0x0F: 3, 0x11: 9, 0x19: 9, 0x1D: 9,
}  # fmt: skip
DATA_COMMANDS = frozenset({0x02, 0x05, 0x06, 0x09, 0x0C, 0x11, 0x19, 0x1D})
NAMES = {
    0x02: "READ TRACK", 0x03: "SPECIFY", 0x04: "SENSE DRIVE STATUS", 0x05: "WRITE DATA",
    0x06: "READ DATA", 0x07: "RECALIBRATE", 0x08: "SENSE INTERRUPT STATUS",
    0x09: "WRITE DELETED DATA", 0x0A: "READ ID", 0x0C: "READ DELETED DATA", 0x0D: "FORMAT TRACK",
    0x0F: "SEEK", 0x11: "SCAN EQUAL", 0x19: "SCAN LOW OR EQUAL", 0x1D: "SCAN HIGH OR EQUAL",
}  # fmt: skip


class FdcStub:
    """A µPD765 whose drives are never ready.

    Attributes:
        motor_on: Last value written to the motor latch (bit 0).
        command: Command bytes received so far.
        results: Result bytes waiting to be read.
        pending_seek_unit: Unit of a RECALIBRATE/SEEK whose interrupt is not yet sensed.
        log: The last commands (name, bytes), for the device view.
    """

    def __init__(self) -> None:
        """Create an idle controller."""
        self.motor_on = False
        self.command: list[int] = []
        self.results: deque[int] = deque()
        self.pending_seek_unit: int | None = None
        self.log: deque[tuple[str, list[int]]] = deque(maxlen=16)

    def reset(self) -> None:
        """Back to the idle state, motor off."""
        self.__init__()  # pylint: disable=unnecessary-dunder-call  # same state as new

    @staticmethod
    def decodes(port: int) -> bool:
        """FDC and motor latch respond to A10 = 0 and A7 = 0."""
        return port & 0x0480 == 0

    def main_status(self) -> int:
        """Return the MSR: offering results, receiving a command, or idle."""
        if self.results:
            return MSR_RQM | MSR_DIO | MSR_CB
        if self.command:
            return MSR_RQM | MSR_CB
        return MSR_RQM

    def io_in(self, port: int) -> int | None:
        """Read the MSR (&FB7E) or the next result byte (&FB7F)."""
        if not port & 0x0100:
            return NOT_DRIVEN  # the motor latch cannot be read
        if not port & 0x0001:
            return self.main_status()
        return self.results.popleft() if self.results else 0xFF

    def io_out(self, port: int, value: int) -> None:
        """Write the motor latch (&FA7E) or a command byte (&FB7F)."""
        if not port & 0x0100:
            self.motor_on = bool(value & 1)
        elif port & 0x0001:
            self._command_byte(value)

    def _command_byte(self, value: int) -> None:
        """Collect a command; when complete, prepare its result."""
        if self.results:  # a write during the result phase restarts (lenient)
            self.results.clear()
        self.command.append(value)
        code = self.command[0] & 0x1F
        if len(self.command) < COMMAND_LENGTHS.get(code, 1):
            return
        command, self.command = self.command, []
        self.log.append((NAMES.get(code, f"invalid &{code:02X}"), command))
        self.results.extend(self._result(code, command))

    def _result(  # pylint: disable=too-many-return-statements  # one case per command group
        self, code: int, command: list[int]
    ) -> list[int]:
        """Return the result bytes of a complete command for a drive that is not ready."""
        unit_and_head = command[1] & 0x07 if len(command) > 1 else 0
        if code in DATA_COMMANDS or code in (0x0A, 0x0D):
            st0 = ST0_ABNORMAL | ST0_NOT_READY | unit_and_head
            chrn = command[2:6] if len(command) >= 6 else [0, 0, 0, 0]
            return [st0, 0, 0, *chrn]
        if code == 0x04:
            return [unit_and_head]  # ST3: not ready, not track 0, not two-sided
        if code in (0x07, 0x0F):
            self.pending_seek_unit = command[1] & 0x03
            return []
        if code == 0x08:
            if self.pending_seek_unit is None:
                return [ST0_INVALID]
            unit, self.pending_seek_unit = self.pending_seek_unit, None
            return [ST0_ABNORMAL | ST0_SEEK_END | ST0_NOT_READY | unit, 0]
        if code == 0x03:
            return []
        return [ST0_INVALID]

    def state(self) -> dict[str, Any]:
        """Return the state for the device view (FR-DBG-05)."""
        return {
            "stub": "no drive ready (full controller: phase 10)",
            "motor_on": self.motor_on,
            "msr": self.main_status(),
            "recent_commands": [f"{name} {bytes(data).hex(' ')}" for name, data in self.log],
        }
