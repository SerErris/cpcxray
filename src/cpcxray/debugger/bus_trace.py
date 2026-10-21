"""Bus trace: a switchable ring buffer of bus accesses (design.md §6.13; FR-DBG-08, D-12).

Unlike the instruction trace (one record per instruction, debugger/trace.py) this records every
access the CPU makes: M1 fetches, memory reads and writes, IN, OUT and the interrupt
acknowledge, each with the PC of the instruction, the address or port, the value and where a
memory access went ("lower", "romN", "ram:0"). For a scrambled ROM the M1/data distinction is
visible here directly.

The trace is a bus observer, registered only while it runs (D-12). Filters: access kinds and an
address range (memory addresses; ports are compared against the range as well).
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

    def line(self) -> str:
        """Format the record as one text line."""
        return (f"{self.sequence:8}  PC={self.pc:04X}  {self.access:<9} {self.address:04X}  "
                f"{self.value:02X}  {self.location}")  # fmt: skip


class BusTrace:
    """The ring buffer and its filters; `on_cycle` is the observer callback.

    `cycles` is the kinds filter, so the bus calls the trace only for the wanted kinds.
    """

    def __init__(self, locate: Locator, capacity: int = 10_000) -> None:
        """Create a stopped trace; `locate(address, kind)` tells where a memory access went."""
        self.locate = locate
        # (sequence, kind, address, value, pc, location): records are built on reading
        self.records: deque[tuple[int, Cycle, int, int, int, str]] = deque(maxlen=capacity)
        self.kinds: frozenset[Cycle] = frozenset(Cycle)
        self.cycles = self.kinds
        self.address_range = (0x0000, 0xFFFF)
        self.enabled = False
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

    def last(self, count: int) -> list[BusRecord]:
        """Return the last `count` records, oldest first."""
        if count <= 0:
            return []
        return [
            BusRecord(CYCLE_NAMES[kind], address, value, pc, location, sequence)
            for sequence, kind, address, value, pc, location in list(self.records)[-count:]
        ]
