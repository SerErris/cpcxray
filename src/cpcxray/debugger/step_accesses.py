"""The memory reads and writes of stepped instructions (task 5e: the Trace tab's detail).

While the debugger steps (step, step over, step out), a bus observer of this class collects the
memory accesses of every instruction, filed under the instruction's trace sequence number, so
the Trace tab can say "reads B733 (TXT_GRAPHICS_CHARACTERS) = 00". It is registered only while
stepping (D-12): free runs, traced or not, do not pay for it - their records have no accesses
(owner, 2026-10-22: to decide later).
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

from cpcxray.cpu.bus import Cycle

KEPT_INSTRUCTIONS = 1000  # accesses are kept for this many stepped instructions


class StepAccesses:
    """A bus observer collecting reads and writes per trace sequence number.

    Attributes:
        handlers: The access kinds it watches (the bus calls these directly).
        by_sequence: Per trace sequence: (access, address, value) in bus order.
        active: Registered on a bus right now (instructions get an entry only then, so an
            instruction without one was not watched - not one without accesses).
    """

    def __init__(self) -> None:
        """Create an empty collection (nothing is recorded until `begin`)."""
        self.by_sequence: OrderedDict[int, list[tuple[str, int, int]]] = OrderedDict()
        self._current: list[tuple[str, int, int]] | None = None
        self.active = False
        self.handlers: dict[Cycle, Callable[[int, int, int], None]] = {
            Cycle.MEM_RD: self._read,
            Cycle.MEM_WR: self._write,
        }

    def begin(self, sequence: int) -> None:
        """The instruction with this trace sequence number runs next: file its accesses."""
        self._current = self.by_sequence.setdefault(sequence, [])
        while len(self.by_sequence) > KEPT_INSTRUCTIONS:
            self.by_sequence.popitem(last=False)

    def end(self) -> None:
        """Stepping stopped: accesses until the next `begin` belong to no instruction."""
        self._current = None

    def _read(self, address: int, value: int, _pc: int) -> None:
        """A memory read (not an opcode fetch)."""
        if self._current is not None:
            self._current.append(("read", address, value))

    def _write(self, address: int, value: int, _pc: int) -> None:
        """A memory write."""
        if self._current is not None:
            self._current.append(("write", address, value))
