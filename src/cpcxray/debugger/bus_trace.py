"""Bus trace: a switchable ring buffer of bus accesses (design.md §6.13; FR-DBG-08, D-12).

Unlike the instruction trace (one record per instruction, debugger/trace.py) this records every
access the CPU makes: M1 fetches, memory reads and writes, IN, OUT and the interrupt
acknowledge, each with the PC of the instruction, the address or port, the value and where a
memory access went ("lower", "romN", "ram:0"). For a scrambled ROM the M1/data distinction is
visible here directly.

The trace is a bus observer, registered only while it runs (D-12). Filters: access kinds and an
address range (memory addresses; ports are compared against the range as well). `enabled`
records free runs (the "Recording" switch); stepping is recorded in any case (`stepping`, set by
the session while it steps), like the instruction trace (owner, 2026-10-22). A free run that is
not recorded leaves a gap record (kind None: the instructions and µs it ran), so the buffer shows
that something is missing; consecutive gaps merge.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from cpcxray.cpu.bus import Cycle
from cpcxray.debugger.breakpoints import CYCLE_NAMES, Access, Locator

ACCESS_KINDS = {name: kind for kind, name in CYCLE_NAMES.items()}


@dataclass(frozen=True)
class BusRecord(Access):
    """One recorded bus access (fields of `Access`) with its running number."""

    sequence: int = 0

    def line(self, variable: str = "") -> str:
        """Format the record as one text line, ending with the variable it touched, if known."""
        text = (f"{self.sequence:8}  PC={self.pc:04X}  {self.access:<9} {self.address:04X}  "
                f"{self.value:02X}  {self.location}")  # fmt: skip
        return f"{text:<46}{variable}" if variable else text


class BusTrace:  # pylint: disable=too-many-instance-attributes  # buffer, filters and switches
    """The ring buffer and its filters; `on_cycle` is the observer callback.

    `cycles` is the kinds filter, so the bus calls the trace only for the wanted kinds.
    """

    def __init__(self, locate: Locator, capacity: int = 10_000) -> None:
        """Create a stopped trace; `locate(address, kind)` tells where a memory access went."""
        self.locate = locate
        # (sequence, kind, address, value, pc, location): records are built on reading
        self.records: deque[tuple[int, Cycle | None, int, int, int, str]] = deque(maxlen=capacity)
        self.kinds: frozenset[Cycle] = frozenset(Cycle)
        self.cycles = self.kinds
        self.address_range = (0x0000, 0xFFFF)
        self.enabled = False
        self.stepping = False  # the session steps: record although `enabled` is off
        self._sequence = 0

    def start(
        self,
        kinds: frozenset[Cycle] | None = None,
        address_range: tuple[int, int] | None = None,
        capacity: int | None = None,
    ) -> None:
        """Clear the buffer and start recording with the given filters."""
        if capacity is not None:
            self.records = deque(maxlen=capacity)
        self.records.clear()
        self._sequence = 0
        self.kinds = kinds if kinds is not None else frozenset(Cycle)
        self.cycles = self.kinds
        self.address_range = address_range or (0x0000, 0xFFFF)
        self.enabled = True

    def on_cycle(self, kind: Cycle, address: int, value: int, pc: int) -> None:
        """Record one access if it passes the filters."""
        if kind not in self.kinds or not self.address_range[0] <= address <= self.address_range[1]:
            return
        self._sequence += 1
        port = kind in (Cycle.IO_RD, Cycle.IO_WR, Cycle.INT_ACK)
        location = "io" if port else self.locate(address, kind)
        self.records.append((self._sequence, kind, address, value, pc, location))

    def gap(self, instructions: int, emulated_us: int) -> None:
        """A free run was not recorded: note it (merged into a gap just before; none at start).

        A gap record is (sequence, None, instructions, emulated µs, 0, "gap").
        """
        if not self.records:
            return
        last = self.records[-1]
        if last[1] is None:
            self.records[-1] = (last[0], None, last[2] + instructions, last[3] + emulated_us, 0,
                                "gap")  # fmt: skip
            return
        self._sequence += 1
        self.records.append((self._sequence, None, instructions, emulated_us, 0, "gap"))

    def last(self, count: int) -> list[BusRecord | str]:
        """Return the last `count` records, oldest first (a gap as its text line)."""
        if count <= 0:
            return []
        return [
            BusRecord(CYCLE_NAMES[kind], address, value, pc, location, sequence)
            if kind is not None
            else _gap_line(sequence, address, value)
            for sequence, kind, address, value, pc, location in list(self.records)[-count:]
        ]


def _gap_line(sequence: int, instructions: int, emulated_us: int) -> str:
    """A gap record as a text line."""
    return (f"{sequence:8}  ---- free run not recorded: {instructions:,} instructions, "
            f"{emulated_us:,} \u00b5s")  # fmt: skip
