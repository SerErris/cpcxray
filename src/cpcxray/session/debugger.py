"""Session methods of the full debugger (design.md §6.13, §6.16; FR-DBG-03, -07, -08, -10).

A mixin of `Session`; every public method is an MCP tool as well (D-27):

* breakpoints of all kinds with location qualifiers (debugger/breakpoints.py); a memory
  breakpoint on a known OS/AMSDOS variable covers it and names it when it stops (FR-DBG-11);
* the bus trace (debugger/bus_trace.py);
* memory views: CPU, RAM, a ROM image, the shadow ROM of a scrambled ROM; the paging per
  16 KB quarter (`paging_get`, FR-DBG-12);
* `.sna` snapshots (machine/sna.py, D-25).

Breakpoint and bus-trace observers are registered on the bus only while they are needed (D-12);
the bare `FlatMachine` has no observable bus, so it supports PC breakpoints only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cpcxray.analysis.operands import Role, immediate_role, loaded_pair
from cpcxray.analysis.source_gen import FIRMWARE_JUMPBLOCKS
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
from cpcxray.machine.memory import BLOCK_SIZE
from cpcxray.machine.sna import SnapshotError, load_sna, save_sna
from cpcxray.session.errors import SessionError

if TYPE_CHECKING:
    from pathlib import Path

    from cpcxray.cpu.disasm import Instruction, SymbolLookup
    from cpcxray.debugger.symbols import SymbolTable
    from cpcxray.debugger.variables import Variable, VariableHit
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
    symbols: SymbolTable
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

    def _operand_label(self, address: int) -> str | None:
        """Label or variable name of an operand (implemented by Session)."""
        raise NotImplementedError

    def _disassemble(self, address: int) -> Instruction:
        """Decode the instruction at an address (implemented by Session)."""
        raise NotImplementedError

    def _variable_hits(self, address: int) -> list[VariableHit]:
        """Known variables containing an address (implemented by Session)."""
        raise NotImplementedError

    def _variable_named(self, label: str) -> Variable | None:
        """The known variable with a label (implemented by Session)."""
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

    def _instruction_labels(self, instruction: Instruction) -> SymbolLookup:
        """The operand lookup for one instruction: `_operand_label`, judged for `ld rr,nn`.

        The immediate of `ld rr,nn` may be a number: `ld bc,#0000` must not read
        `ld bc,RESET_ENTRY`. As in the source generator (analysis/operands.py), a value used
        as a number gets no name; one of unknown use keeps the owner's labels and variables
        but not a firmware name below the jumpblocks (the restarts at &0000-&003F).
        """
        role = immediate_role(instruction, self._disassemble)  # UNKNOWN if no ld rr,nn
        if loaded_pair(instruction) is None or role == Role.ADDRESS:
            return self._operand_label
        if role == Role.NUMBER:
            return lambda _word: None

        def unknown_use(word: int) -> str | None:
            """Name the value unless the name would only be a low firmware address."""
            if word >= FIRMWARE_JUMPBLOCKS or self.symbols.owner_at(
                word, self.machine.symbol_scope(word)
            ):
                return self._operand_label(word)
            return next((hit.name for hit in self._variable_hits(word)), None)

        return unknown_use

    def _instruction_text(self, instruction: Instruction) -> str:
        """The instruction's first line with operand names (`_instruction_labels`)."""
        return instruction.text(self._instruction_labels(instruction))

    def _setup_debugger(self) -> None:
        """Create the breakpoint set and the (stopped) bus trace."""
        self.breakpoints = Breakpoints()
        self.bus_trace = BusTrace(self._location)
        self._breakpoint_observer = None
        self._bus_trace_observing = False

    def _location(self, address: int, kind: Cycle) -> str:
        """Where a memory access goes: "lower", "romN" or "ram:<block>" (writes always RAM).

        The RAM block is the one mapped at the address now (6128 banking, D-14; the 464 has
        blocks 0-3 at fixed places).
        """
        rom = None if kind == Cycle.MEM_WR else self.machine.symbol_scope(address & 0xFFFF)
        if rom:
            return rom
        memory = getattr(self.machine, "memory", None)
        return f"ram:{memory.ram_block_at(address)}" if memory is not None else "ram:0"

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
        bus.refresh_observers()  # breakpoint kinds or trace filters may have changed

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
        if hit is None:
            return None
        detail = hit.describe()
        if hit.access in ("read", "write") and (hits := self._variable_hits(hit.address)):
            detail["variable"] = hits[0].name  # FR-DBG-11: "write to NAME+n"
        return detail

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
            end: Last address of a range (address kinds). Without it, a read, write or
                access breakpoint on the name of a known OS/AMSDOS variable covers the whole
                variable (FR-DBG-11).
            location: What must be paged in at the access: "any", "rom", "lower",
                "rom:<n>", "ram" or "ram:<block>" (the RAM block mapped at the
                address: 0-3 by address on a 464, 0-7 by banking on a 6128). Writes always go
                to RAM.
            port_mask: Port bits compared with `address` (I/O kinds), e.g. &FF00.
            rom: "rom_select": only when this ROM number is selected.
        """
        if kind not in ADDRESS_KINDS | PORT_KINDS and kind not in ("rom_select", "interrupt"):
            raise SessionError("kind must be pc, m1, read, write, access, io_in, io_out, io, "
                               "rom_select or interrupt")  # fmt: skip
        if kind != "pc" and self._observable_bus() is None:
            raise SessionError(f"{kind} breakpoints need a CPC machine (observable bus)")
        needs_address = kind in ADDRESS_KINDS or kind in PORT_KINDS
        if needs_address and address is None:
            raise SessionError(f"a {kind} breakpoint needs an address or port")
        first = self._address(address) if address is not None and needs_address else 0
        last = self._address(end) if end is not None and kind in ADDRESS_KINDS else first
        if end is None and kind in ("read", "write", "access") and isinstance(address, str):
            variable = self._variable_named(address)
            if variable is not None and variable.address == first:
                last = variable.last
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
            if hits := self._variable_hits(breakpoint_.first):
                entry["variable"] = hits[0].name
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
            raise SessionError("the bus trace needs a CPC machine (observable bus)")
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
        """Return the last accesses: sequence, PC, access, address, value, location, variable.

        A read or write that went to RAM ends with the known OS/AMSDOS variable it touched
        (`NAME` or `NAME+n`, FR-DBG-11), looked up when reading - recording stays as fast as
        before. AMSDOS's names use its workspace as it is now.
        """
        if not 1 <= last <= MAX_BUS_TRACE_READ:
            raise SessionError(f"last must be 1-{MAX_BUS_TRACE_READ}")
        names: dict[int, str] = {}  # address → variable name, looked up once per address

        def variable(record: Any) -> str:
            """The variable a RAM read or write touched, or ""."""
            if record.access not in ("read", "write") or not record.location.startswith("ram"):
                return ""
            if record.address not in names:
                hits = self._variable_hits(record.address)
                names[record.address] = hits[0].name if hits else ""
            return names[record.address]

        records = self.bus_trace.last(last)
        return {"tracing": self.bus_trace.enabled,
                "lines": [record.line(variable(record)) for record in records]}  # fmt: skip

    # == memory views ==========================================================================

    def paging_get(self) -> dict[str, Any]:
        """What the CPU reads in each 16 KB quarter now (FR-DBG-12).

        Each quarter shows the ROM paged in - the lower ROM, or upper ROM n with its file
        name - or else the RAM block mapped there (6128 banking, D-14). `ram_block` is the
        block under the quarter in any case: writes always go to RAM. A 6128 also reports its
        RAM configuration (Gate Array function 11).
        """
        memory = getattr(self.machine, "memory", None)
        quarters = []
        for index in range(4):
            start = index * BLOCK_SIZE
            quarters.append(self._quarter(start, memory))
        result: dict[str, Any] = {"quarters": quarters}
        if memory is not None and memory.ram_blocks > 4:
            result["ram_configuration"] = memory.ram_configuration
        return result

    def _quarter(self, start: int, memory: Any) -> dict[str, Any]:
        """Describe the 16 KB quarter from `start` (see `paging_get`)."""
        rom = self.machine.symbol_scope(start) or None
        block = memory.ram_block_at(start) if memory is not None else start // BLOCK_SIZE
        text = self._rom_text(rom) if rom is not None else f"RAM block {block}"
        return {"start": f"{start:04X}", "end": f"{start + BLOCK_SIZE - 1:04X}",
                "source": rom or "ram", "ram_block": block, "text": text}  # fmt: skip

    def _rom_text(self, slot_name: str) -> str:
        """Name a ROM slot for the paging: "lower ROM (os6128.rom)", "upper ROM 7 (amsdos.rom)"."""
        name = self._rom_slot(slot_name).name
        if slot_name == "lower":
            return f"lower ROM ({name})"
        return f"upper ROM {slot_name.removeprefix('rom')} ({name})"

    def memory_view(
        self, address: int | str, length: int = 128, view: str = "cpu"
    ) -> dict[str, Any]:
        """Read memory through a view (FR-DBG-03): hex dump lines with ASCII.

        Args:
            address: Start address (in a ROM view: the CPU address or the ROM offset).
            length: Number of bytes (max 4096).
            view: "cpu" (what the CPU reads now), "ram" (RAM even under a paged-in ROM),
                "ram:N" (RAM block N, mapped or not, at offset address & &3FFF: 0-3, 0-7 on
                a 6128),
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
        last = min(start + length - 1, 0xFFFF)
        return {"address": f"{start:04X}", "length": length, "view": view, "hex": hex_text,
                "lines": hex_dump(start, data), "paging": self._view_paging(view, start, last),
                "variables": self._view_variables(view, start, last)}  # fmt: skip

    def _view_paging(self, view: str, first: int, last: int) -> list[dict[str, Any]]:
        """What a view shows in `first`-`last` (FR-DBG-12): per quarter for "cpu" and "ram"."""
        if view in ("cpu", "ram"):
            quarters = self.paging_get()["quarters"][first >> 14 : (last >> 14) + 1]
            if view == "ram":
                quarters = [q | {"source": "ram", "text": f"RAM block {q['ram_block']}"}
                            for q in quarters]  # fmt: skip
            return quarters
        if view.startswith("ram:"):
            text = f"RAM block {view.partition(':')[2]}"
        else:  # a ROM view; `memory_view` has checked it already
            mode, _, slot = view.rpartition(":")
            text = self._rom_text(self._slot_name(None if view == "shadow" else slot))
            text += {"raw": ", as stored", "shadow": ", clear values recorded"}.get(mode, "")
            if view == "shadow":
                text += ", clear values recorded"
        return [{"start": f"{first:04X}", "end": f"{last:04X}", "source": view, "text": text}]

    def _view_variables(self, view: str, first: int, last: int) -> list[dict[str, Any]]:
        """Known OS/AMSDOS variables in `first`-`last` where the view shows them (FR-DBG-11).

        Variables live in RAM at CPU addresses: the "ram" view always shows them, "cpu" only
        where no ROM is paged in, "ram:N" only where block N is mapped now; ROM views never.
        """
        variables = getattr(self, "variables", None)
        memory = getattr(self.machine, "memory", None)
        if variables is None or not (view in ("cpu", "ram") or view.startswith("ram:")):
            return []
        found = []
        for variable in variables.overlapping(first, last, self.machine.peek):
            address = max(variable.address, first)
            if view == "cpu" and self.machine.symbol_scope(address):
                continue
            if view.startswith("ram:") and (
                memory is None or f"ram:{memory.ram_block_at(address)}" != view
            ):
                continue
            found.append(variable.describe())
        return found

    def _block_reader(self, view: str) -> Any:
        """Return a reader for the view "ram:N": RAM block N at offset address & &3FFF."""
        memory = getattr(self.machine, "memory", None)
        number_text = view.partition(":")[2]
        blocks = memory.ram_blocks if memory is not None else 0
        if not number_text.isdigit() or not 0 <= int(number_text) < blocks:
            raise SessionError(f"unknown view {view!r}: RAM blocks are ram:0-ram:{blocks - 1}")
        contents = memory.block(int(number_text))  # type: ignore[union-attr]
        return lambda address: contents[address & 0x3FFF]

    def _view_reader(self, view: str) -> Any:
        """Return a function address → byte (or None if unknown) for a memory view."""
        if view == "cpu":
            return self.machine.peek
        if view == "ram":
            memory = getattr(self.machine, "memory", None)
            ram = memory.ram if memory is not None else None
            return (lambda a: ram[a]) if ram is not None else self.machine.peek
        if view.startswith("ram:"):
            return self._block_reader(view)
        mode, _, slot = view.rpartition(":")
        if view == "shadow":  # the only scrambled ROM
            mode, slot = "shadow", ""
        try:
            rom = self._rom_slot(self._slot_name(slot or None))
        except (SessionError, ValueError):
            raise SessionError(f"unknown view {view!r}: cpu, ram, ram:N, lower, romN, raw:romN, "
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
