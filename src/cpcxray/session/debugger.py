"""Session methods of the full debugger (design.md §6.13, §6.16; FR-DBG-03, -07, -08, -10).

A mixin of `Session`; every public method is an MCP tool as well (D-27):

* breakpoints of all kinds with location qualifiers (debugger/breakpoints.py);
* the bus trace (debugger/bus_trace.py);
* memory views: CPU, RAM, a ROM image, the shadow ROM of a scrambled ROM;
* `.sna` snapshots (machine/sna.py, D-25).

Breakpoint and bus-trace observers are registered on the bus only while they are needed (D-12);
the bare `FlatMachine` has no observable bus, so it supports PC breakpoints only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cpcxray.cpu.bus import Cycle
from cpcxray.debugger.breakpoints import (
    ADDRESS_KINDS,
    PORT_KINDS,
    BreakpointObserver,
    Breakpoints,
    Hit,
)
from cpcxray.debugger.bus_trace import ACCESS_KINDS, BusTrace
from cpcxray.decoding.shadow_rom import M1, UNKNOWN
from cpcxray.machine.sna import SnapshotError, load_sna, save_sna
from cpcxray.session.errors import SessionError

if TYPE_CHECKING:
    from pathlib import Path

    from cpcxray.machine.cpc464 import CPC464
    from cpcxray.machine.flat import Machine
    from cpcxray.machine.memory import RomSlot

MAX_MEMORY_READ = 4096
MAX_BUS_TRACE_READ = 2000


def hex_dump(start: int, data: list[int | None]) -> list[str]:
    """Hex dump lines of 16 bytes with ASCII; None (unknown) shows as "--"."""
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        hex_text = " ".join("--" if value is None else f"{value:02X}" for value in chunk)
        ascii_text = "".join(
            chr(value) if value is not None and 32 <= value < 127 else "." for value in chunk
        )
        lines.append(f"{(start + offset) & 0xFFFF:04X}  {hex_text:<47}  {ascii_text}")
    return lines


class DebuggerMixin:
    """Breakpoints, bus trace, memory views and snapshots of `Session` (module docstring)."""

    machine: Machine
    breakpoints: Breakpoints
    bus_trace: BusTrace
    _breakpoint_observer: BreakpointObserver | None
    _bus_trace_observing: bool

    # -- helpers (provided by Session / AnalysisMixin) -------------------------------------------

    def _path(self, path: str) -> Path:
        """Resolve a project-relative path (implemented by Session)."""
        raise NotImplementedError

    def _address(self, value: int | str) -> int:
        """Parse an address or label (implemented by Session)."""
        raise NotImplementedError

    def _label(self, address: int) -> str | None:
        """Label of an address (implemented by Session)."""
        raise NotImplementedError

    def _cpc(self) -> CPC464:
        """Return the machine as a CPC 464 (implemented by Session)."""
        raise NotImplementedError

    def _rom_slot(self, slot_name: str) -> RomSlot:
        """Return the ROM of a slot (implemented by AnalysisMixin)."""
        raise NotImplementedError

    def _slot_name(self, slot: str | int | None) -> str:
        """Normalise a slot name (implemented by AnalysisMixin)."""
        raise NotImplementedError

    def _setup_debugger(self) -> None:
        """Create the breakpoint set and the (stopped) bus trace."""
        self.breakpoints = Breakpoints()
        self.bus_trace = BusTrace(self._location)
        self._breakpoint_observer = None
        self._bus_trace_observing = False

    def _location(self, address: int, kind: Cycle) -> str:
        """Where a memory access goes: "lower", "romN" or "ram:0" (writes always RAM)."""
        if kind == Cycle.MEM_WR:
            return "ram:0"
        return self.machine.symbol_scope(address & 0xFFFF) or "ram:0"

    def _observable_bus(self) -> Any:
        """Return the machine's bus if it supports observers, else None (FlatMachine)."""
        bus = getattr(self.machine, "bus", None)
        return bus if hasattr(bus, "add_observer") else None

    def _sync_debug_observers(self) -> None:
        """Register the breakpoint observer / bus trace exactly while they are needed (D-12)."""
        bus = self._observable_bus()
        if bus is None:
            return
        wanted = bool(self.breakpoints.observed)
        if wanted and self._breakpoint_observer is None:
            self._breakpoint_observer = BreakpointObserver(self.breakpoints, self._location)
            bus.add_observer(self._breakpoint_observer)
        elif not wanted and self._breakpoint_observer is not None:
            bus.remove_observer(self._breakpoint_observer)
            self._breakpoint_observer = None
        if self.bus_trace.enabled and not self._bus_trace_observing:
            bus.add_observer(self.bus_trace)
            self._bus_trace_observing = True
        elif not self.bus_trace.enabled and self._bus_trace_observing:
            bus.remove_observer(self.bus_trace)
            self._bus_trace_observing = False

    # -- hooks of the run loop -------------------------------------------------------------------

    def _pc_breakpoint(self, pc: int) -> dict[str, Any] | None:
        """Before an instruction: the PC breakpoint that stops it, as a stop detail."""
        breakpoints = self.breakpoints
        if pc not in breakpoints.addresses and not breakpoints.pc_ranges:
            return None
        hit = breakpoints.pc_hit(pc, lambda: self._location(pc, Cycle.M1))
        if hit is None:
            return None
        return {"id": hit.id, "kind": "pc", "pc": f"{pc:04X}",
                "location": self._location(pc, Cycle.M1)}  # fmt: skip

    def _access_breakpoint(self) -> dict[str, Any] | None:
        """After an instruction: the access breakpoint it triggered, as a stop detail."""
        observer = self._breakpoint_observer
        if observer is None or observer.hit is None:
            return None
        hit: Hit | None = observer.take()
        return hit.describe() if hit is not None else None

    # == breakpoints ===========================================================================

    def breakpoint_add(  # pylint: disable=too-many-arguments  # one argument per option
        self,
        address: int | str | None = None,
        kind: str = "pc",
        *,
        end: int | str | None = None,
        location: str = "any",
        port_mask: int = 0xFFFF,
        rom: int | None = None,
    ) -> dict[str, Any]:
        """Add a breakpoint (FR-DBG-07); returns it with its id.

        Args:
            address: Address (pc, m1, read, write, access) or port (io_in, io_out, io).
            kind: "pc" (before the instruction), "m1", "read", "write", "access" (data read or
                write), "io_in", "io_out", "io", "rom_select", "interrupt". All but "pc" stop
                after the instruction that made the access and report its PC.
            end: Last address of a range (address kinds).
            location: What must be paged in at the access: "any", "rom", "lower",
                "rom:<n>", "ram" or "ram:<page>". Writes always go to RAM.
            port_mask: Port bits compared with `address` (I/O kinds), e.g. &FF00.
            rom: "rom_select": only when this ROM number is selected.
        """
        if kind not in ADDRESS_KINDS | PORT_KINDS and kind not in ("rom_select", "interrupt"):
            raise SessionError("kind must be pc, m1, read, write, access, io_in, io_out, io, "
                               "rom_select or interrupt")  # fmt: skip
        if kind != "pc" and self._observable_bus() is None:
            raise SessionError(f"{kind} breakpoints need the CPC 464 machine (observable bus)")
        needs_address = kind in ADDRESS_KINDS or kind in PORT_KINDS
        if needs_address and address is None:
            raise SessionError(f"a {kind} breakpoint needs an address or port")
        first = self._address(address) if address is not None and needs_address else 0
        last = self._address(end) if end is not None and kind in ADDRESS_KINDS else first
        try:
            breakpoint_ = self.breakpoints.add(kind, first, last, location=location,
                                               port_mask=port_mask, rom_number=rom)  # fmt: skip
        except ValueError as error:
            raise SessionError(str(error)) from None
        self._sync_debug_observers()
        return self._describe_breakpoint(breakpoint_.id)

    def breakpoint_remove(self, breakpoint_id: int) -> dict[str, Any]:
        """Remove a breakpoint by id."""
        try:
            removed = self.breakpoints.remove(breakpoint_id)
        except KeyError as error:
            raise SessionError(str(error)) from None
        self._sync_debug_observers()
        return {"removed": removed.id, **removed.describe()}

    def breakpoint_enable(self, breakpoint_id: int, enabled: bool = True) -> dict[str, Any]:
        """Enable or disable a breakpoint (disabled ones are kept but ignored)."""
        try:
            self.breakpoints.enable(breakpoint_id, enabled)
        except KeyError as error:
            raise SessionError(str(error)) from None
        self._sync_debug_observers()
        return self._describe_breakpoint(breakpoint_id)

    def breakpoint_list(self) -> dict[str, Any]:
        """List all breakpoints: id, kind, address/port, location, enabled, hits."""
        return {"breakpoints": [self._describe_breakpoint(b.id) for b in self.breakpoints.all()]}

    def _describe_breakpoint(self, breakpoint_id: int) -> dict[str, Any]:
        """A breakpoint as a dict, with the label of its address."""
        breakpoint_ = next(b for b in self.breakpoints.all() if b.id == breakpoint_id)
        entry = breakpoint_.describe()
        if breakpoint_.kind in ADDRESS_KINDS:
            entry["label"] = self._label(breakpoint_.first)
        return entry

    # == bus trace =============================================================================

    def bus_trace_start(
        self,
        kinds: list[str] | None = None,
        start: int | str = 0,
        end: int | str = 0xFFFF,
        capacity: int = 10_000,
    ) -> dict[str, Any]:
        """Record every bus access into a ring buffer (FR-DBG-08); clears the buffer.

        Args:
            kinds: Accesses to record: "M1", "read", "write", "in", "out", "interrupt"
                (default all).
            start: First address/port recorded.
            end: Last address/port recorded.
            capacity: Ring buffer size (1-1,000,000).
        """
        if self._observable_bus() is None:
            raise SessionError("the bus trace needs the CPC 464 machine (observable bus)")
        names = kinds or list(ACCESS_KINDS)
        unknown = [name for name in names if name not in ACCESS_KINDS]
        if unknown or not 1 <= capacity <= 1_000_000:
            raise SessionError(f"kinds must be from {', '.join(ACCESS_KINDS)}; capacity 1-1e6")
        first, last = self._address(start), self._address(end)
        self.bus_trace.start(frozenset(ACCESS_KINDS[n] for n in names), (first, last), capacity)
        self._sync_debug_observers()
        return {"tracing": True, "kinds": names, "range": [f"{first:04X}", f"{last:04X}"]}

    def bus_trace_stop(self) -> dict[str, Any]:
        """Stop the bus trace (the buffer is kept for bus_trace_read)."""
        self.bus_trace.enabled = False
        self._sync_debug_observers()
        return {"tracing": False, "records": len(self.bus_trace.records)}

    def bus_trace_read(self, last: int = 100) -> dict[str, Any]:
        """Return the last accesses: sequence, PC, access, address, value, location."""
        if not 1 <= last <= MAX_BUS_TRACE_READ:
            raise SessionError(f"last must be 1-{MAX_BUS_TRACE_READ}")
        records = self.bus_trace.last(last)
        return {"tracing": self.bus_trace.enabled, "lines": [r.line() for r in records]}

    # == memory views ==========================================================================

    def memory_view(
        self, address: int | str, length: int = 128, view: str = "cpu"
    ) -> dict[str, Any]:
        """Read memory through a view (FR-DBG-03): hex dump lines with ASCII.

        Args:
            address: Start address (in a ROM view: the CPU address or the ROM offset).
            length: Number of bytes (max 4096).
            view: "cpu" (what the CPU reads now), "ram" (RAM even under a paged-in ROM),
                "lower" / "romN" (the ROM image, decoded for data reads if scrambled),
                "raw:romN" (a scrambled ROM image as stored), "shadow:romN" (the clear values
                recorded by the shadow ROM; never-read bytes "--"; "shadow": the only
                scrambled ROM).
        """
        start = self._address(address)
        if not 1 <= length <= MAX_MEMORY_READ:
            raise SessionError(f"length must be 1-{MAX_MEMORY_READ}")
        read = self._view_reader(view)
        data = [read((start + offset) & 0xFFFF) for offset in range(length)]
        hex_text = "".join("??" if value is None else f"{value:02X}" for value in data)
        return {"address": f"{start:04X}", "length": length, "view": view, "hex": hex_text,
                "lines": hex_dump(start, data)}  # fmt: skip

    def _view_reader(self, view: str) -> Any:
        """Return a function address → byte (or None if unknown) for a memory view."""
        if view == "cpu":
            return self.machine.peek
        if view == "ram":
            memory = getattr(self.machine, "memory", None)
            ram = memory.ram if memory is not None else None
            return (lambda a: ram[a]) if ram is not None else self.machine.peek
        mode, _, slot = view.rpartition(":")
        if view == "shadow":  # the only scrambled ROM
            mode, slot = "shadow", ""
        try:
            rom = self._rom_slot(self._slot_name(slot or None))
        except (SessionError, ValueError):
            raise SessionError(f"unknown view {view!r}: cpu, ram, lower, romN, raw:romN, "
                               "shadow:romN") from None  # fmt: skip
        if mode == "":
            return lambda a: rom.peek(a & 0x3FFF)
        if mode == "raw":
            return lambda a: rom.data[a & 0x3FFF]
        if mode == "shadow" and rom.shadow is not None:
            shadow = rom.shadow

            def clear_value(address: int) -> int | None:
                """The clear value the shadow recorded (None if never read)."""
                offset = address & 0x3FFF
                access_class = shadow.access_map[offset]
                if access_class == UNKNOWN:
                    return None
                return rom.data[offset] if access_class == M1 else shadow.data_values[offset]

            return clear_value
        raise SessionError(f"view {view!r} needs a scrambled ROM (shadow) or a valid mode")

    # == snapshots =============================================================================

    def sna_save(self, path: str) -> dict[str, Any]:
        """Save the machine state as a .sna snapshot (version 3, D-25)."""
        file = self._path(path)
        size = save_sna(self._cpc(), file)
        return {"path": str(file), "size": size}

    def sna_load(self, path: str) -> dict[str, Any]:
        """Load a .sna snapshot (versions 1-3); the configured ROMs stay installed."""
        file = self._path(path)
        try:
            info = load_sna(self._cpc(), file)
        except (OSError, SnapshotError) as error:
            raise SessionError(str(error)) from None
        return {"path": str(file), "version": info["version"], "pc": f"{info['pc']:04X}"}
