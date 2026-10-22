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

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from cpcxray.analysis.operands import Role, immediate_role, loaded_pair
from cpcxray.analysis.source_gen import FIRMWARE_JUMPBLOCKS
from cpcxray.cpu.bus import Cycle
from cpcxray.cpu.disasm import disassemble
from cpcxray.debugger.breakpoints import (
    ADDRESS_KINDS,
    PORT_KINDS,
    BreakpointObserver,
    Breakpoints,
    Hit,
)
from cpcxray.debugger.bus_trace import ACCESS_KINDS, BusTrace
from cpcxray.debugger.stack import StackTops
from cpcxray.decoding.shadow_rom import M1, UNKNOWN
from cpcxray.devices.key_map import KeyMapError
from cpcxray.machine.memory import BLOCK_SIZE
from cpcxray.machine.sna import SnapshotError, load_sna, save_sna
from cpcxray.session.errors import SessionError
from cpcxray.ui.palette import HARDWARE_TO_FIRMWARE

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


PENS_PER_MODE = {0: 16, 1: 4, 2: 2, 3: 4}  # mode 3 (undocumented) shows 4 pens
KEY_LABELS = {
    "cursor_up": "Cur\u2191", "cursor_down": "Cur\u2193", "cursor_left": "Cur\u2190",
    "cursor_right": "Cur\u2192", "joy_up": "Joy\u2191", "joy_down": "Joy\u2193",
    "joy_left": "Joy\u2190", "joy_right": "Joy\u2192", "joy_fire1": "Fire1", "joy_fire2": "Fire2",
    "control": "Ctrl", "caps_lock": "Caps",
}  # fmt: skip


def _key_label(name: str) -> str:
    """A key's label for the matrix view: "Cur↑", "Shift", "A", "f7", ";"."""
    if name in KEY_LABELS:
        return KEY_LABELS[name]
    if len(name) == 1:
        return name.upper()
    return name if name.startswith("f") else name.capitalize()


MAX_STACK_WORDS = 256


def _code_row(text: str, comments: list[str | None]) -> dict[str, Any]:
    """A disassembly row without its address: mnemonic, operands, the joined comments."""
    mnemonic, _, operands = text.partition(" ")
    row: dict[str, Any] = {"kind": "data", "address": "", "bytes": "", "mnemonic": mnemonic,
                           "operands": operands}  # fmt: skip
    present = [comment.strip() for comment in comments if comment]
    if present:
        row["comment"] = " ; ".join(present)
    return row


class DebuggerMixin:
    """Breakpoints, bus trace, memory views and snapshots of `Session` (module docstring)."""

    machine: Machine
    symbols: SymbolTable
    breakpoints: Breakpoints
    bus_trace: BusTrace
    _breakpoint_observer: BreakpointObserver | None
    _bus_trace_observing: bool
    last_stop: dict[str, Any] | None  # the result of the last run or step (Session sets it)

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

    def _format_instruction(self, instruction: Instruction) -> list[str]:
        """An instruction as `disassemble` lines (implemented by Session)."""
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

    def _instruction_labels(
        self, instruction: Instruction, following: Callable[[int], Instruction] | None = None
    ) -> SymbolLookup:
        """The operand lookup for one instruction: `_operand_label`, judged for `ld rr,nn`.

        The immediate of `ld rr,nn` may be a number: `ld bc,#0000` must not read
        `ld bc,RESET_ENTRY`. As in the source generator (analysis/operands.py), a value used
        as a number gets no name; one of unknown use keeps the owner's labels and variables
        but not a firmware name below the jumpblocks (the restarts at &0000-&003F).

        Args:
            instruction: The instruction.
            following: Decodes the code after it (default: memory, `_disassemble`; a file's
                bytes for code read from a disk).
        """
        role = immediate_role(instruction, following or self._disassemble)  # UNKNOWN: no ld rr,nn
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

    def _code_lines(
        self, read: Callable[[int], int], address: int, count: int
    ) -> list[dict[str, Any]]:
        """Disassemble `count` instructions of bytes that are not in memory (a disk file).

        Args:
            read: The byte at an address (as if the bytes were loaded there).
            address: Where to start.
            count: Number of instructions.

        Returns:
            Per instruction "address" and "text" (names as in the live disassembly, numbers
            stay numbers), and for a named operand "label" and "label_type" (FW, VAR, own).
        """

        def decode(at: int) -> Instruction:
            """The instruction at an address of the bytes."""
            return disassemble(read, at, self.machine.disassembly_profile(), self._operand_label)

        code = []
        for _ in range(count):
            instruction = decode(address)
            names = self._instruction_labels(instruction, decode)
            word = instruction.word_operand
            label = names(word) if word is not None else None
            line: dict[str, Any] = {"address": f"{address:04X}", "text": instruction.text(names)}
            if word is not None and label is not None:
                line |= {"label": label, "label_type": self._label_type(word)}
            code.append(line)
            address = instruction.next_address
        return code

    def _disassembly_rows(
        self, instruction: Instruction, label: str | None
    ) -> list[dict[str, Any]]:
        """`disassemble`'s rows of one instruction, for colouring (task 5b).

        A label row ("kind" "label"); the instruction ("instruction": address, bytes,
        mnemonic, operands); its extra lines, e.g. a RST's inline `defw` ("data"). Each with
        "comment" if any and, where an operand is named, "name" and "name_type" (FW, VAR, own).
        """
        address = instruction.address
        rows: list[dict[str, Any]] = []
        if label:
            rows.append({"kind": "label", "text": label, "name_type": self._label_type(address)})
        names = self._instruction_labels(instruction)
        owner_comment = self.symbols.comment_at(address, self.machine.symbol_scope(address))
        words = [instruction.word_operand, *(line.word for line in instruction.extra_lines)]
        for number, (text, comment) in enumerate(instruction.lines(names)):
            first = number == 0
            row = _code_row(text, [comment, owner_comment if first else None])
            if first:
                row |= {"kind": "instruction", "address": f"{address:04X}",
                        "bytes": instruction.raw.hex(" ").upper()}  # fmt: skip
            word = words[number]
            name = names(word) if word is not None else None
            if word is not None and name and name in row["operands"]:
                row |= {"name": name, "name_type": self._label_type(word)}
            rows.append(row)
        return rows

    def _label_type(self, address: int) -> str:
        """The type of the name `_operand_label` gives an address (task 5g colours).

        "FW" for a firmware name (the owner's comment over it changes nothing), "own" for the
        owner's label, "VAR" for a known variable.
        """
        scope = self.machine.symbol_scope(address)
        owner = self.symbols.owner_at(address, scope)
        if owner is not None and owner.label:
            return "own"
        if self.symbols.firmware_at(address) is not None:
            return "FW"
        return "VAR"

    def _instruction_text(self, instruction: Instruction) -> str:
        """The instruction's first line with operand names (`_instruction_labels`)."""
        return instruction.text(self._instruction_labels(instruction))

    def devices_get(self) -> dict[str, Any]:
        """Return the state of Gate Array, CRTC, ROM select, PPI (SystemIO), FDC and keyboard.

        Besides the registers: the inks as firmware colours and the pens the mode uses, the
        selected ROM's file and whether it is paged in, the CRTC register names and screen
        layout, the PPI ports and the FDC status in words, and `key_lines`: the keyboard
        matrix lines 0-9 with the names of their 8 keys (from the OS ROM's key map), the
        line's value (bit clear = pressed) and the held keys.
        """
        machine = self._cpc()
        state = machine.devices()
        gate_array = state["gate_array"]
        gate_array["inks_firmware"] = [HARDWARE_TO_FIRMWARE[ink] for ink in gate_array["inks"]]
        gate_array["border_firmware"] = HARDWARE_TO_FIRMWARE[gate_array["border"]]
        gate_array["pens_used"] = PENS_PER_MODE[gate_array["mode"]]
        rom_select = state["rom_select"]
        answering = machine.memory.effective_upper_slot()
        rom = machine.memory.upper_roms.get(answering)
        rom_select |= {
            "answering": answering,
            "file": rom.name if rom is not None else None,
            "paged_in": bool(gate_array["upper_rom_enabled"]),
            "installed": (["lower"] if machine.memory.lower_rom is not None else [])
            + [str(slot) for slot in sorted(machine.memory.upper_roms)],
        }
        state["key_lines"] = self._key_lines(machine)
        return state

    @staticmethod
    def _key_lines(machine: CPC464) -> list[dict[str, Any]]:
        """The keyboard matrix by line: key labels (bit 0-7), value, held bits."""
        try:
            names = {number: name for name, number in machine.key_map().names.items()}
        except KeyMapError:
            names = {}
        rows = machine.keyboard.rows
        return [
            {"line": line, "value": rows[line],
             "keys": [_key_label(names.get(line * 8 + bit, "")) for bit in range(8)],
             "held": [bit for bit in range(8) if not rows[line] >> bit & 1]}
            for line in range(len(rows))
        ]  # fmt: skip

    def _setup_debugger(self) -> None:
        """Create the breakpoint set and the (stopped) bus trace."""
        self.breakpoints = Breakpoints()
        self.bus_trace = BusTrace(self._location)
        self._breakpoint_observer = None
        self._bus_trace_observing = False
        self.last_stop = None
        self.stacks = StackTops()
        self._watch_stack()

    def _watch_stack(self) -> None:
        """Let the CPU report its SP loads (the stack top rule, debugger/stack.py)."""
        self.machine.cpu.stack_loaded = self._stack_loaded

    def _stack_loaded(self, address: int, value: int) -> None:
        """The CPU loaded SP: a return to a known stack, or a new top."""
        where = self.machine.symbol_scope(address) or "RAM"
        self.stacks.loaded(value, address, f"ld sp at &{address:04X} ({where})")

    def stack_get(self, count: int = 16) -> dict[str, Any]:
        """The stack: words from SP up to the stack's top (at most `count`, 1-256).

        The top is where the stack started: the debugger watches the instructions that load SP
        and keeps the tops they set (debugger/stack.py); "source" names the instruction, e.g.
        "ld sp at &0092 (lower)". Without a known top, `count` words from SP are shown.
        """
        if not 1 <= count <= MAX_STACK_WORDS:
            raise SessionError(f"count must be 1-{MAX_STACK_WORDS}")
        sp = self.machine.cpu.sp
        stack = self.stacks.current(sp)
        if stack is not None:
            count = min(count, ((stack.top - sp) & 0xFFFF) // 2)
        words = []
        for index in range(count):
            address = (sp + 2 * index) & 0xFFFF
            value = self.machine.peek(address) | self.machine.peek((address + 1) & 0xFFFF) << 8
            words.append({"address": f"{address:04X}", "value": f"{value:04X}"})
        return {"sp": f"{sp:04X}", "top": f"{stack.top:04X}" if stack else None,
                "source": stack.source if stack else "no known top", "words": words}  # fmt: skip

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
        recording = self.bus_trace.enabled or self.bus_trace.stepping
        if recording and not self._bus_trace_observing:
            bus.add_observer(self.bus_trace)
            self._bus_trace_observing = True
        elif not recording and self._bus_trace_observing:
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

    def last_stop_get(self) -> dict[str, Any]:
        """The last stop of a run or step, with what explains it.

        A run that only used up its time budget ("time_limit") does not count: the GUI runs
        in time slices, and its last stop is the breakpoint, step or pause before.

        Returns:
            "stop": the run's result as it was returned (reason, PC, "breakpoint" detail), or
            None before the first stop; for a stop: "instruction", the disassembled lines of the
            instruction that stopped (a PC breakpoint: the one about to run; an access
            breakpoint: the one that made the access); for a breakpoint stop: "hits", its hit
            count now (None if removed since), and "label", the label of its address.
        """
        stop = self.last_stop
        if stop is None:
            return {"stop": None}
        hit = stop.get("breakpoint")
        pc = int(hit["pc"] if hit else stop["pc"], 16)
        instruction = self._format_instruction(self._disassemble(pc))
        result: dict[str, Any] = {"stop": stop, "instruction": instruction}
        if hit is not None:
            current = [b for b in self.breakpoints.all() if b.id == hit["id"]]
            result["hits"] = current[0].hits if current else None
            result["label"] = self._label(int(hit.get("address", hit["pc"]), 16))
        return result

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
        lines = [record if isinstance(record, str) else record.line(variable(record))
                 for record in records]  # fmt: skip  # a str: a gap's line
        return {"tracing": self.bus_trace.enabled, "lines": lines}

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
        self.stacks.reset()  # the snapshot's stack: its top is not known
        self.stacks.loaded(self.machine.cpu.sp, None, "snapshot")
        return {"path": str(file), "version": info["version"], "pc": f"{info['pc']:04X}"}
