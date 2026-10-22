"""Memory written since the last stop: the Memory tab's "Follow CPU" (task 5c).

While "Follow CPU" is on, a bus observer of this class notes every memory write - the address
and the last value - so the Memory tab can mark the bytes written since the last stop (dim red)
and jump to where the program wrote. It watches writes only and is registered only while
Follow is on (D-12); a free run without it pays nothing.
"""

from __future__ import annotations

from collections.abc import Callable

from cpcxray.cpu.bus import Cycle


class MemoryWrites:  # pylint: disable=too-few-public-methods  # an observer: clear, and its handler
    """A bus observer collecting the addresses written (and their last values).

    Attributes:
        handlers: The access kind it watches (the bus calls it directly).
        written: Address → the value written last, since `clear`.
        last: (address, value) of the latest write, or None.
    """

    def __init__(self) -> None:
        """Create an empty collection."""
        self.written: dict[int, int] = {}
        self.last: tuple[int, int] | None = None
        self.handlers: dict[Cycle, Callable[[int, int, int], None]] = {Cycle.MEM_WR: self._write}

    def clear(self) -> None:
        """Start a new collection (a run begins after a stop)."""
        self.written = {}
        self.last = None

    def _write(self, address: int, value: int, _pc: int) -> None:
        """A memory write."""
        self.written[address] = value
        self.last = (address, value)
