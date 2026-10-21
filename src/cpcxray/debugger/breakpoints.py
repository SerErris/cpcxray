"""Breakpoints of all kinds with location qualifiers (design.md §6.13; FR-DBG-07).

Kinds:

| Kind         | Stops when                                               | Checked             |
|--------------|----------------------------------------------------------|---------------------|
| `pc`         | the next instruction starts at an address (or in a range)| before the step     |
| `m1`         | an M1 fetch (opcode or prefix) hits the address/range     | bus observer        |
| `read`       | a data read (not M1) hits the address/range               | bus observer        |
| `write`      | a memory write hits the address/range                     | bus observer        |
| `access`     | a data read or a write hits the address/range             | bus observer        |
| `io_in`      | IN from a port matching `port & mask`                     | bus observer        |
| `io_out`     | OUT to a port matching `port & mask`                      | bus observer        |
| `io`         | IN or OUT matching                                        | bus observer        |
| `rom_select` | OUT to the ROM select port (&DFxx), optionally a ROM number| bus observer       |
| `interrupt`  | the CPU accepts an interrupt                              | bus observer        |

A `pc` breakpoint stops *before* the instruction. All others stop *after* the instruction that
caused the access ("who accesses this byte"); the stop reports that instruction's PC, the access,
address, value and location.

Location qualifier (address kinds): what was paged in at the access. `any`; `rom` (any ROM);
`lower`; `rom:<n>` (upper ROM slot n as it answers, D-10); `ram`; `ram:<page>` (RAM bank; the
464 has page 0 only, banking comes with the 6128, D-14). Writes always go to RAM (FR-MCH-06), so
a write under a paged-in ROM matches `ram`, a read of the same address `rom:<n>`.

The bus observer is registered only while such breakpoints are enabled (D-12); PC breakpoints
cost one set lookup per instruction.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cpcxray.cpu.bus import Cycle

KINDS = ("pc", "m1", "read", "write", "access", "io_in", "io_out", "io", "rom_select", "interrupt")
ADDRESS_KINDS = frozenset({"pc", "m1", "read", "write", "access"})
PORT_KINDS = frozenset({"io_in", "io_out", "io"})
LOCATION_PATTERN = re.compile(r"^(any|rom|ram|lower|rom:([0-9]|1[0-5])|ram:[0-9]+)$")
_CYCLES = {
    "m1": {Cycle.M1},
    "read": {Cycle.MEM_RD},
    "write": {Cycle.MEM_WR},
    "access": {Cycle.MEM_RD, Cycle.MEM_WR},
    "io_in": {Cycle.IO_RD},
    "io_out": {Cycle.IO_WR},
    "io": {Cycle.IO_RD, Cycle.IO_WR},
    "rom_select": {Cycle.IO_WR},
    "interrupt": {Cycle.INT_ACK},
}
CYCLE_NAMES = {Cycle.M1: "M1", Cycle.MEM_RD: "read", Cycle.MEM_WR: "write",
               Cycle.IO_RD: "in", Cycle.IO_WR: "out", Cycle.INT_ACK: "interrupt"}  # fmt: skip

type Locator = Callable[[int, Cycle], str]
"""Returns where an access went: "lower", "rom5", "ram:0" (see `location_matches`)."""


def location_matches(qualifier: str, location: str) -> bool:
    """True if an access location ("lower", "romN", "ram:P") satisfies a qualifier."""
    if qualifier == "any":
        return True
    if qualifier == "ram":
        return location.startswith("ram")
    if qualifier == "rom":
        return not location.startswith("ram")
    if qualifier.startswith("rom:"):
        return location == "rom" + qualifier[4:]
    return location == qualifier  # "lower", "ram:P"


def is_rom_select_port(port: int) -> bool:
    """The ROM select latch answers to A13 = 0 (&DFxx, design.md §5.2)."""
    return not port & 0x2000


@dataclass
class Breakpoint:  # pylint: disable=too-many-instance-attributes  # one field per option
    """A breakpoint.

    Attributes:
        id: Number used to remove or enable it.
        kind: One of KINDS.
        first: Address or port (first of a range); 0 for kinds without one.
        last: Last address of the range (= first for a single address).
        location: Location qualifier (address kinds).
        port_mask: Port bits that must match `first` (port kinds).
        rom_number: `rom_select`: only this ROM number, or None for any.
        enabled: Disabled breakpoints are kept but ignored.
        hits: How often it stopped execution.
    """

    id: int
    kind: str
    first: int = 0
    last: int = 0
    location: str = "any"
    port_mask: int = 0xFFFF
    rom_number: int | None = None
    enabled: bool = True
    hits: int = 0

    @property
    def address(self) -> int:
        """The (first) address, for PC breakpoints."""
        return self.first

    def spec(self) -> tuple[Any, ...]:
        """What the breakpoint checks (two with the same spec are the same breakpoint)."""
        return (self.kind, self.first, self.last, self.location, self.port_mask, self.rom_number)

    def matches(self, kind: Cycle, address: int, value: int, location: Callable[[], str]) -> bool:
        """True if a bus access triggers this (observer) breakpoint.

        Args:
            kind: The access.
            address: Memory address or port.
            value: Value read or written.
            location: Returns where the access went (only called when needed).
        """
        if kind not in _CYCLES[self.kind]:
            return False
        if self.kind in PORT_KINDS:
            return (address ^ self.first) & self.port_mask == 0
        if self.kind == "rom_select":
            return is_rom_select_port(address) and self.rom_number in (None, value)
        if self.kind == "interrupt":
            return True
        if not self.first <= address <= self.last:
            return False
        return location_matches(self.location, location())

    def describe(self) -> dict[str, Any]:
        """Return the breakpoint as a JSON-friendly dict."""
        entry: dict[str, Any] = {"id": self.id, "kind": self.kind, "enabled": self.enabled,
                                 "hits": self.hits}  # fmt: skip
        if self.kind in ADDRESS_KINDS:
            entry["address"] = f"{self.first:04X}"
            if self.last != self.first:
                entry["end"] = f"{self.last:04X}"
            entry["location"] = self.location
        elif self.kind in PORT_KINDS:
            entry |= {"port": f"{self.first:04X}", "mask": f"{self.port_mask:04X}"}
        elif self.kind == "rom_select" and self.rom_number is not None:
            entry["rom"] = self.rom_number
        return entry


@dataclass(frozen=True)
class Access:
    """One bus access as reported by breakpoints and the bus trace.

    Attributes:
        access: "M1", "read", "write", "in", "out" or "interrupt".
        address: Memory address or port.
        value: Value read or written.
        pc: Address of the instruction that made the access.
        location: Where the access went ("lower", "romN", "ram:0"; ports: "io").
    """

    access: str
    address: int
    value: int
    pc: int
    location: str

    def access_fields(self) -> dict[str, str]:
        """Return the access as JSON-friendly hex strings."""
        return {"access": self.access, "address": f"{self.address:04X}",
                "value": f"{self.value:02X}", "pc": f"{self.pc:04X}",
                "location": self.location}  # fmt: skip


@dataclass(frozen=True)
class Hit(Access):
    """A breakpoint triggered by a bus access (reported in the stop result).

    Attributes:
        breakpoint: The breakpoint.
    """

    breakpoint: Breakpoint | None = None

    def describe(self) -> dict[str, Any]:
        """Return the hit as a JSON-friendly dict."""
        assert self.breakpoint is not None
        return {"id": self.breakpoint.id, "kind": self.breakpoint.kind, **self.access_fields()}


class Breakpoints:
    """All breakpoints, with the fast lookups the run loop needs."""

    def __init__(self) -> None:
        """Create an empty set."""
        self._by_id: dict[int, Breakpoint] = {}
        self._next_id = 1
        self.addresses: frozenset[int] = frozenset()  # enabled single-address PC breakpoints
        self.pc_ranges: tuple[Breakpoint, ...] = ()  # enabled PC breakpoints over a range
        self.observed: tuple[Breakpoint, ...] = ()  # enabled breakpoints of the bus observer

    def add(  # pylint: disable=too-many-arguments  # one argument per breakpoint option
        self,
        kind: str = "pc",
        first: int = 0,
        last: int | None = None,
        *,
        location: str = "any",
        port_mask: int = 0xFFFF,
        rom_number: int | None = None,
    ) -> Breakpoint:
        """Add a breakpoint; an identical one is re-enabled and returned instead.

        Raises:
            ValueError: For an unknown kind or location, or a bad range.
        """
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {', '.join(KINDS)}")
        if not LOCATION_PATTERN.match(location):
            raise ValueError("location must be any, rom, ram, lower, rom:<0-15> or ram:<page>")
        last = first if last is None else last
        if not 0 <= first <= last <= 0xFFFF:
            raise ValueError("address range must satisfy 0 <= first <= last <= &FFFF")
        candidate = Breakpoint(0, kind, first, last, location, port_mask & 0xFFFF, rom_number)
        for existing in self._by_id.values():
            if existing.spec() == candidate.spec():
                existing.enabled = True
                self._update()
                return existing
        candidate.id = self._next_id
        self._next_id += 1
        self._by_id[candidate.id] = candidate
        self._update()
        return candidate

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

    def enable(self, breakpoint_id: int, enabled: bool = True) -> Breakpoint:
        """Enable or disable a breakpoint.

        Raises:
            KeyError: If there is no such breakpoint.
        """
        if breakpoint_id not in self._by_id:
            raise KeyError(f"no breakpoint {breakpoint_id}")
        breakpoint_ = self._by_id[breakpoint_id]
        breakpoint_.enabled = enabled
        self._update()
        return breakpoint_

    def clear(self) -> None:
        """Remove all breakpoints."""
        self._by_id.clear()
        self._update()

    def pc_hit(self, pc: int, location: Callable[[], str]) -> Breakpoint | None:
        """Return the enabled PC breakpoint for an instruction at `pc` (counting the hit)."""
        for breakpoint_ in self._by_id.values():
            if (
                breakpoint_.enabled
                and breakpoint_.kind == "pc"
                and breakpoint_.first <= pc <= breakpoint_.last
                and location_matches(breakpoint_.location, location())
            ):
                breakpoint_.hits += 1
                return breakpoint_
        return None

    def hit(self, address: int) -> Breakpoint | None:
        """Count a stop at a PC breakpoint at `address` (any location)."""
        return self.pc_hit(address, lambda: "ram:0")

    def all(self) -> list[Breakpoint]:
        """Return all breakpoints sorted by id."""
        return sorted(self._by_id.values(), key=lambda breakpoint_: breakpoint_.id)

    def _update(self) -> None:
        """Recompute the lookups of enabled breakpoints."""
        enabled = [b for b in self._by_id.values() if b.enabled]
        self.addresses = frozenset(b.first for b in enabled if b.kind == "pc" and b.first == b.last)
        self.pc_ranges = tuple(b for b in enabled if b.kind == "pc" and b.first != b.last)
        self.observed = tuple(b for b in enabled if b.kind != "pc")


class BreakpointObserver:
    """Bus observer for all breakpoints except PC; remembers the first hit of an instruction."""

    def __init__(self, breakpoints: Breakpoints, locate: Locator) -> None:
        """Watch `breakpoints.observed`; `locate(address, kind)` tells where an access went."""
        self.breakpoints = breakpoints
        self.locate = locate
        self.hit: Hit | None = None

    @property
    def cycles(self) -> frozenset[Cycle]:
        """The access kinds the enabled breakpoints need (the bus calls only for these)."""
        return frozenset().union(*(_CYCLES[b.kind] for b in self.breakpoints.observed))

    def on_cycle(self, kind: Cycle, address: int, value: int, pc: int) -> None:
        """Check one access against the enabled breakpoints."""
        if self.hit is not None:
            return
        for breakpoint_ in self.breakpoints.observed:
            if breakpoint_.matches(kind, address, value, lambda: self.locate(address, kind)):
                port_access = kind in (Cycle.IO_RD, Cycle.IO_WR, Cycle.INT_ACK)
                location = "io" if port_access else self.locate(address, kind)
                self.hit = Hit(CYCLE_NAMES[kind], address, value, pc, location, breakpoint_)
                return

    def take(self) -> Hit | None:
        """Return the pending hit (counting it) and clear it."""
        hit, self.hit = self.hit, None
        if hit is not None and hit.breakpoint is not None:
            hit.breakpoint.hits += 1
        return hit
