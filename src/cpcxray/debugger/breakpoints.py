"""Breakpoints (design.md §6.13, FR-DBG-07).

Phase 3 implements PC breakpoints: the session checks the PC against the set of enabled
breakpoint addresses before each instruction. The other kinds (M1 at an address range, memory
read/write, I/O, ROM-select change, interrupt) arrive in phase 9 as bus observers.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Breakpoint:
    """A PC breakpoint.

    Attributes:
        id: Number used to remove it.
        address: The PC value that stops execution.
        enabled: Disabled breakpoints are kept but ignored.
        hits: How often it stopped execution.
    """

    id: int
    address: int
    enabled: bool = True
    hits: int = 0


class Breakpoints:
    """The set of PC breakpoints with a fast address lookup."""

    def __init__(self) -> None:
        """Create an empty set."""
        self._by_id: dict[int, Breakpoint] = {}
        self._next_id = 1
        self.addresses: frozenset[int] = frozenset()  # enabled addresses, checked per step

    def add(self, address: int) -> Breakpoint:
        """Add a breakpoint at `address` (an existing one there is returned instead)."""
        for breakpoint_ in self._by_id.values():
            if breakpoint_.address == address:
                breakpoint_.enabled = True
                self._update()
                return breakpoint_
        breakpoint_ = Breakpoint(self._next_id, address & 0xFFFF)
        self._by_id[breakpoint_.id] = breakpoint_
        self._next_id += 1
        self._update()
        return breakpoint_

    def remove(self, breakpoint_id: int) -> Breakpoint:
        """Remove a breakpoint by id and return it.

        Raises:
            KeyError: If there is no such breakpoint.
        """
        try:
            breakpoint_ = self._by_id.pop(breakpoint_id)
        except KeyError:
            raise KeyError(f"no breakpoint {breakpoint_id}") from None
        self._update()
        return breakpoint_

    def clear(self) -> None:
        """Remove all breakpoints."""
        self._by_id.clear()
        self._update()

    def hit(self, address: int) -> Breakpoint | None:
        """Count a stop at `address`; return the breakpoint there, if any."""
        for breakpoint_ in self._by_id.values():
            if breakpoint_.address == address and breakpoint_.enabled:
                breakpoint_.hits += 1
                return breakpoint_
        return None

    def all(self) -> list[Breakpoint]:
        """Return all breakpoints sorted by id."""
        return sorted(self._by_id.values(), key=lambda breakpoint_: breakpoint_.id)

    def _update(self) -> None:
        """Recompute the set of enabled addresses."""
        self.addresses = frozenset(b.address for b in self._by_id.values() if b.enabled)
