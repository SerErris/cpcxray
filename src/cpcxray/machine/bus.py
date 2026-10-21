"""The CPC bus: memory map, I/O broadcast and bus observers (design.md §6.3; D-08, D-12).

The Z80 calls `fetch`/`read`/`write`/`io_in`/`io_out`/`int_ack` (the `CpuBus` protocol):

* Memory accesses go to the `MemoryMap`; `fetch` passes `is_m1=True` so scrambled ROM slots can
  tell opcode fetches from data reads (D-11, phase 7).
* I/O is broadcast (D-08): every device whose `decodes(port)` is true sees an OUT; for an IN,
  all responding devices drive the data bus and the values are ANDed (open-collector style);
  with no responder the bus reads &FF.
* The interrupt acknowledge clears the Gate Array's request; the data bus reads &FF (IM 1
  ignores it; IM 2 on a CPC would read &FF too).

Bus observers (trace, breakpoints, analysis) get `on_cycle(kind, address, value, pc)` for the
accesses they want, where `pc` is the address of the instruction being executed. They are
opt-in (D-12), and per access kind: an observer may declare `cycles` (the `Cycle` kinds it
wants, default all), or `handlers`, a dict from kind to `handler(address, value, pc)` that the
bus calls directly (no dispatch inside the observer). Each CPU-facing method (fetch, read,
write, ...) is the plain fast variant while no observer wants its kind, and the observed
variant otherwise; only the interested observers are called. The CPU looks the methods up on
every access, so a swap takes effect immediately. Observers whose interest changes call
`refresh_observers()`.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Protocol

from cpcxray.cpu.bus import Cycle
from cpcxray.devices.gate_array import GateArray
from cpcxray.machine.memory import MemoryMap


class IoDevice(Protocol):
    """An I/O device on the CPC bus; it decodes its own address bits (D-08)."""

    def decodes(self, port: int) -> bool:
        """Return True if the device responds to `port`."""
        ...

    def io_in(self, port: int) -> int | None:
        """Return the value the device drives for an IN, or None if it does not drive."""
        ...

    def io_out(self, port: int, value: int) -> None:
        """Handle an OUT."""
        ...


class BusObserver(Protocol):  # pylint: disable=too-few-public-methods  # one callback
    """Receives every bus access while registered."""

    def on_cycle(self, kind: Cycle, address: int, value: int, pc: int) -> None:
        """Called after each access; `pc` is the address of the current instruction."""
        ...


class CpcBus:  # pylint: disable=too-many-instance-attributes  # swappable method slots (D-12)
    """Connects the CPU with memory and devices.

    Attributes:
        memory: The memory map.
        devices: I/O devices in broadcast order.
        gate_array: Receives the interrupt acknowledge.
        instruction_pc: PC of the instruction being executed (set by the machine while
            observers are registered; reported to observers).
    """

    def __init__(self, memory: MemoryMap, devices: list[IoDevice], gate_array: GateArray) -> None:
        """Create the bus in the fast (unobserved) configuration."""
        self.memory = memory
        self.devices = devices
        self.gate_array = gate_array
        self.instruction_pc = 0
        self._observers: list[BusObserver] = []
        self._handlers: dict[Cycle, tuple[Callable[[int, int, int], None], ...]] = {}
        self.refresh_observers()

    # -- observers (D-12) ------------------------------------------------------------------------

    @property
    def observed(self) -> bool:
        """True while at least one observer is registered."""
        return bool(self._observers)

    def add_observer(self, observer: BusObserver) -> None:
        """Register an observer (for the access kinds in its `cycles`, default all)."""
        self._observers.append(observer)
        self.refresh_observers()

    def remove_observer(self, observer: BusObserver) -> None:
        """Unregister an observer; kinds nobody watches go back to the fast methods."""
        self._observers.remove(observer)
        self.refresh_observers()

    def refresh_observers(self) -> None:
        """Recompute who watches which access kind and pick fast or observed methods."""
        self._handlers = {kind: tuple(self._handler(o, kind) for o in self._observers
                                      if self._wants(o, kind)) for kind in Cycle}  # fmt: skip
        watched = self._handlers
        self.fetch = self._fetch_observed if watched[Cycle.M1] else self._fetch
        self.read = self._read_observed if watched[Cycle.MEM_RD] else self._read
        self.write = self._write_observed if watched[Cycle.MEM_WR] else self._write
        self.io_in = self._io_in_observed if watched[Cycle.IO_RD] else self._io_in
        self.io_out = self._io_out_observed if watched[Cycle.IO_WR] else self._io_out
        self.int_ack = self._int_ack_observed if watched[Cycle.INT_ACK] else self._int_ack

    @staticmethod
    def _wants(observer: BusObserver, kind: Cycle) -> bool:
        """True if the observer wants accesses of this kind (`handlers`, `cycles`, or all)."""
        handlers = getattr(observer, "handlers", None)
        if handlers is not None:
            return kind in handlers
        return kind in getattr(observer, "cycles", Cycle)

    @staticmethod
    def _handler(observer: BusObserver, kind: Cycle) -> Callable[[int, int, int], None]:
        """The callable for one kind: a direct handler, or `on_cycle` with the kind bound."""
        handlers = getattr(observer, "handlers", None)
        if handlers is not None:
            return handlers[kind]  # type: ignore[no-any-return]
        return partial(observer.on_cycle, kind)

    def _notify(self, kind: Cycle, address: int, value: int) -> None:
        """Pass one access to the observers that want its kind."""
        for handler in self._handlers[kind]:
            handler(address, value, self.instruction_pc)

    # -- fast implementations --------------------------------------------------------------------

    def _fetch(self, address: int) -> int:
        """M1 opcode fetch."""
        return self.memory.read(address, True)

    def _read(self, address: int) -> int:
        """Memory read without M1."""
        return self.memory.read(address, False)

    def _write(self, address: int, value: int) -> None:
        """Memory write (always RAM)."""
        self.memory.write(address, value)

    def _io_in(self, port: int) -> int:
        """IN: AND of all responding devices, &FF if none drives the bus."""
        value = 0xFF
        for device in self.devices:
            if device.decodes(port):
                driven = device.io_in(port)
                if driven is not None:
                    value &= driven
        return value

    def _io_out(self, port: int, value: int) -> None:
        """OUT: every device that decodes the port receives the value."""
        for device in self.devices:
            if device.decodes(port):
                device.io_out(port, value)

    def _int_ack(self) -> int:
        """Interrupt acknowledge: the Gate Array clears its request; data bus &FF."""
        self.gate_array.acknowledge_interrupt()
        return 0xFF

    # -- observed implementations ----------------------------------------------------------------

    def _fetch_observed(self, address: int) -> int:
        """M1 fetch, reported as Cycle.M1."""
        value = self._fetch(address)
        self._notify(Cycle.M1, address, value)
        return value

    def _read_observed(self, address: int) -> int:
        """Memory read, reported as Cycle.MEM_RD."""
        value = self._read(address)
        self._notify(Cycle.MEM_RD, address, value)
        return value

    def _write_observed(self, address: int, value: int) -> None:
        """Memory write, reported as Cycle.MEM_WR."""
        self._write(address, value)
        self._notify(Cycle.MEM_WR, address, value)

    def _io_in_observed(self, port: int) -> int:
        """IN, reported as Cycle.IO_RD."""
        value = self._io_in(port)
        self._notify(Cycle.IO_RD, port, value)
        return value

    def _io_out_observed(self, port: int, value: int) -> None:
        """OUT, reported as Cycle.IO_WR."""
        self._io_out(port, value)
        self._notify(Cycle.IO_WR, port, value)

    def _int_ack_observed(self) -> int:
        """Interrupt acknowledge, reported as Cycle.INT_ACK."""
        value = self._int_ack()
        self._notify(Cycle.INT_ACK, 0, value)
        return value
