"""The `Session` façade: the only API that acts on the machine (design.md §6.16, D-27).

GUI, MCP server and scripts are thin clients of this class; every public method is also an MCP
tool with the same name and parameters (FR-MCP-02; the parity test enforces it). Results are
plain JSON-compatible dicts and lists, so the MCP layer passes them through unchanged.

Conventions for clients:

* Addresses may be given as int, as hex string ("BB5A", "&BB5A", "#BB5A", "0xBB5A") or as a
  symbol label ("txt_output"). Addresses in results are 4-digit hex strings.
* Every run-type method is bounded (FR-MCP-04): by an instruction limit, an emulated-time
  limit and a wall-clock timeout (default 10 s). It returns why it stopped:
  "steps_done", "breakpoint", "address_reached", "returned", "instruction_limit",
  "time_limit", "timeout", "paused" or "halted" (HALT with interrupts disabled).
* Every method returns a dict (a list would become several MCP content blocks).
* Large results are limited; write them to files instead (memory_save, trace to a file)
  (FR-MCP-05). Relative file paths are relative to the project directory.
* `pause()` may be called from another thread while a run is in progress; everything else
  must be called from the thread that owns the session (see session/commands.py, D-29).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpcxray.cpu.disasm import Flow, Instruction, disassemble
from cpcxray.debugger.breakpoints import Breakpoints
from cpcxray.debugger.symbols import GLOBAL, SymbolError, SymbolTable, parse_address
from cpcxray.debugger.trace import Trace
from cpcxray.machine.flat import FlatMachine, Machine

DEFAULT_TIMEOUT_S = 10.0
MAX_TIMEOUT_S = 600.0
MAX_MEMORY_READ = 4096
MAX_DISASSEMBLE = 256
MAX_TRACE_READ = 1000
MAX_SEARCH_RESULTS = 1000
ANNOTATIONS_FILE = Path("project") / "annotations.txt"
CHECK_INTERVAL = 4096  # instructions between checks of pause and wall clock
_REPEATING_BLOCK = ("ldir", "lddr", "cpir", "cpdr", "inir", "indr", "otir", "otdr")


class SessionError(ValueError):
    """A request the session cannot carry out (bad argument, missing file, ...)."""


def hex4(value: int) -> str:
    """Format a 16-bit value as 4 hex digits."""
    return f"{value:04X}"


@dataclass
class _RunLimits:
    """Bounds of one run (FR-MCP-04)."""

    max_instructions: int | None
    max_us: int | None
    timeout_s: float


class Session:  # pylint: disable=too-many-public-methods  # one method per MCP tool (D-27)
    """Owns a machine and offers every debugger operation (D-27).

    Attributes:
        machine: The emulated machine (phase 3: `FlatMachine`).
        symbols: Labels and comments (firmware names preloaded).
        breakpoints: PC breakpoints.
        trace: The instruction trace.
        project_dir: Base directory for relative file paths.
    """

    def __init__(self, machine: Machine | None = None, project_dir: str | Path = ".") -> None:
        """Create a session.

        Args:
            machine: The machine to drive; default a `FlatMachine` (phase 3).
            project_dir: Base directory for relative paths; project/annotations.txt there is
                loaded if it exists.
        """
        self.machine: Machine = machine if machine is not None else FlatMachine()
        self.project_dir = Path(project_dir).resolve()
        self.symbols = SymbolTable()
        self.breakpoints = Breakpoints()
        self.trace = Trace()
        self._pause_requested = threading.Event()
        annotations = self.project_dir / ANNOTATIONS_FILE
        if annotations.exists():
            self.symbols.load(annotations)

    # == helpers ===============================================================================

    def _path(self, path: str) -> Path:
        """Resolve a path relative to the project directory."""
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self.project_dir / candidate

    def _address(self, value: int | str) -> int:
        """Accept an int, a hex string (BB5A, &BB5A, #BB5A, 0xBB5A) or a symbol label."""
        if isinstance(value, int):
            if not 0 <= value <= 0xFFFF:
                raise SessionError(f"address {value} out of range 0-65535")
            return value
        try:
            return parse_address(value)
        except SymbolError:
            symbol = self.symbols.find(value.strip())
            if symbol is None:
                raise SessionError(
                    f"{value!r} is neither a hex address nor a known label"
                ) from None
            return symbol.address

    def _label(self, address: int) -> str | None:
        """Return the label for `address` in the scope that applies to it now."""
        return self.symbols.label_at(address, self.machine.symbol_scope(address))

    def _disassemble(self, address: int) -> Instruction:
        """Decode the instruction at `address` from the CPU's view of memory."""
        return disassemble(
            self.machine.peek, address, self.machine.disassembly_profile(), self._label
        )

    def _register_summary(self) -> str:
        """Return the main registers as one line."""
        cpu = self.machine.cpu
        return (
            f"AF={hex4(cpu.af)} BC={hex4(cpu.bc)} DE={hex4(cpu.de)} HL={hex4(cpu.hl)} "
            f"IX={hex4(cpu.ix)} IY={hex4(cpu.iy)} SP={hex4(cpu.sp)} PC={hex4(cpu.pc)}"
        )

    def _stop_result(self, reason: str, instructions: int, emulated_us: int) -> dict[str, Any]:
        """Describe where and why a run or step stopped."""
        pc = self.machine.cpu.pc
        return {
            "reason": reason,
            "pc": hex4(pc),
            "label": self._label(pc),
            "next": self._disassemble(pc).text(self._label),
            "instructions": instructions,
            "emulated_us": emulated_us,
            "registers": self._register_summary(),
        }

    @staticmethod
    def _limits(
        max_instructions: int | None, max_us: int | None, timeout_s: float | None
    ) -> _RunLimits:
        """Validate run limits; the wall-clock timeout always applies."""
        timeout = DEFAULT_TIMEOUT_S if timeout_s is None else timeout_s
        if not 0 < timeout <= MAX_TIMEOUT_S:
            raise SessionError(f"timeout_s must be in (0, {MAX_TIMEOUT_S}]")
        for name, value in (("max_instructions", max_instructions), ("max_us", max_us)):
            if value is not None and value < 1:
                raise SessionError(f"{name} must be at least 1")
        return _RunLimits(max_instructions, max_us, timeout)

    def _run_loop(
        self,
        limits: _RunLimits,
        until_address: int | None = None,
        finished: Callable[[], str | None] | None = None,
    ) -> dict[str, Any]:
        """Execute instructions until a breakpoint, target, limit, timeout or pause.

        The instruction at the start PC is always executed, so a run continues from a
        breakpoint it stopped at.

        Args:
            limits: Instruction, emulated-time and wall-clock limits.
            until_address: Stop when the PC reaches this address ("address_reached").
            finished: Called after every instruction; a non-None result is the stop reason.
        """
        machine, cpu, trace = self.machine, self.machine.cpu, self.trace
        self._pause_requested.clear()
        deadline = time.monotonic() + limits.timeout_s
        instructions = emulated_us = 0
        reason = None
        while reason is None:
            pc = cpu.pc
            if instructions:
                if pc == until_address:
                    reason = "address_reached"
                    break
                if pc in self.breakpoints.addresses:
                    self.breakpoints.hit(pc)
                    reason = "breakpoint"
                    break
            reason = self._limit_reached(limits, instructions, emulated_us, deadline)
            if reason is not None:
                break
            if trace.enabled and trace.wants(pc):
                instruction = self._disassemble(pc)
                trace.record(
                    pc, instruction.raw, instruction.text(self._label), self._register_summary()
                )
            emulated_us += machine.step()
            instructions += 1
            if finished is not None:
                reason = finished()
        return self._stop_result(reason, instructions, emulated_us)

    def _limit_reached(
        self, limits: _RunLimits, instructions: int, emulated_us: int, deadline: float
    ) -> str | None:
        """Return the stop reason if a limit is reached before the next instruction, else None.

        Pause requests and the wall clock are checked only every CHECK_INTERVAL instructions,
        to keep the per-instruction cost low.
        """
        cpu = self.machine.cpu
        if limits.max_instructions is not None and instructions >= limits.max_instructions:
            return "instruction_limit"
        if limits.max_us is not None and emulated_us >= limits.max_us:
            return "time_limit"
        if cpu.halted and not cpu.iff1 and not cpu.nmi_pending:
            return "halted"  # nothing can wake the CPU up
        if instructions and instructions % CHECK_INTERVAL == 0:
            if self._pause_requested.is_set():
                return "paused"
            if time.monotonic() > deadline:
                return "timeout"
        return None

    # == machine ===============================================================================

    def machine_status(self) -> dict[str, Any]:
        """Return machine model, PC, clock, CPU state, breakpoint count and trace state."""
        cpu = self.machine.cpu
        return {
            **self.machine.status(),
            "pc": hex4(cpu.pc),
            "label": self._label(cpu.pc),
            "clock_us": self.machine.clock_us,
            "halted": cpu.halted,
            "interrupts_enabled": cpu.iff1,
            "interrupt_mode": cpu.im,
            "registers": self._register_summary(),
            "breakpoints": len(self.breakpoints.all()),
            "tracing": self.trace.enabled,
        }

    def reset(self) -> dict[str, Any]:
        """Reset the machine (CPU registers, clock); memory keeps its contents."""
        self.machine.reset()
        return self.machine_status()

    def memory_load(self, address: int | str, path: str) -> dict[str, Any]:
        """Load a binary file into memory (RAM) at `address`.

        Args:
            address: Load address.
            path: File path, relative to the project directory.

        Returns:
            Load address, length and the address of the last byte.
        """
        start = self._address(address)
        file = self._path(path)
        if not file.is_file():
            raise SessionError(f"file not found: {file}")
        data = file.read_bytes()
        if start + len(data) > 0x10000:
            raise SessionError(f"{len(data)} bytes do not fit at {hex4(start)}")
        for offset, value in enumerate(data):
            self.machine.poke(start + offset, value)
        return {"address": hex4(start), "length": len(data), "last": hex4(start + len(data) - 1)}

    def memory_save(self, address: int | str, length: int, path: str) -> dict[str, Any]:
        """Save `length` bytes of the CPU's view of memory from `address` to a binary file."""
        start = self._address(address)
        if not 1 <= length <= 0x10000 - start:
            raise SessionError("length must be at least 1 and end within 64 KB")
        file = self._path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(bytes(self.machine.peek(start + offset) for offset in range(length)))
        return {"address": hex4(start), "length": length, "path": str(file)}

    # == execution =============================================================================

    def step(self, count: int = 1) -> dict[str, Any]:
        """Execute `count` instructions (1-100000); stops early at a breakpoint.

        Returns:
            Stop reason ("steps_done", "breakpoint", ...), PC, next instruction, registers.
        """
        if not 1 <= count <= 100_000:
            raise SessionError("count must be 1-100000")
        result = self._run_loop(_RunLimits(count, None, MAX_TIMEOUT_S))
        if result["reason"] == "instruction_limit":
            result["reason"] = "steps_done"
        return result

    def step_over(self, timeout_s: float | None = None) -> dict[str, Any]:
        """Execute one instruction, running calls and loops to completion.

        CALL, call-like RSTs (per the machine's RST profile), DJNZ and repeating block
        instructions run until the PC reaches the following instruction; jumps and all other
        instructions are single-stepped.
        """
        instruction = self._disassemble(self.machine.cpu.pc)
        runs_through = instruction.flow in (Flow.CALL, Flow.CONDITIONAL_CALL) or (
            instruction.text().startswith(("djnz", *_REPEATING_BLOCK))
        )
        if not runs_through:
            return self.step(1)
        limits = self._limits(None, None, timeout_s)
        return self._run_loop(limits, until_address=instruction.next_address)

    def step_out(self, timeout_s: float | None = None) -> dict[str, Any]:
        """Run until the current subroutine returns, then stop at the return address.

        "Returns" means a RET/RETI/RETN that leaves SP above its value at the start.
        """
        cpu = self.machine.cpu
        start_sp = cpu.sp
        was_return = [False]

        def returned() -> str | None:
            """Stop after a return instruction that popped above the starting SP."""
            if was_return[0] and cpu.sp > start_sp:
                return "returned"
            was_return[0] = self._is_return(cpu.pc)
            return None

        was_return[0] = self._is_return(cpu.pc)
        return self._run_loop(self._limits(None, None, timeout_s), finished=returned)

    def _is_return(self, address: int) -> bool:
        """True if the instruction at `address` is RET, RET cc, RETI or RETN."""
        opcode = self.machine.peek(address)
        if opcode == 0xC9 or opcode & 0xC7 == 0xC0:
            return True
        return opcode == 0xED and self.machine.peek((address + 1) & 0xFFFF) & 0xC7 == 0x45

    def run(
        self,
        max_instructions: int | None = None,
        max_us: int | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Run until a breakpoint or a limit.

        Args:
            max_instructions: Stop after this many instructions.
            max_us: Stop after this much emulated time in µs (1 µs = 1 CPC NOP).
            timeout_s: Wall-clock limit in seconds (default 10, max 600).
        """
        return self._run_loop(self._limits(max_instructions, max_us, timeout_s))

    def run_until(
        self,
        address: int | str,
        max_instructions: int | None = None,
        max_us: int | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """Run until the PC reaches `address` (or a breakpoint or a limit).

        Args:
            address: Target address or label.
            max_instructions: Stop after this many instructions.
            max_us: Stop after this much emulated time in µs.
            timeout_s: Wall-clock limit in seconds (default 10, max 600).
        """
        target = self._address(address)
        limits = self._limits(max_instructions, max_us, timeout_s)
        return self._run_loop(limits, until_address=target)

    def pause(self) -> dict[str, Any]:
        """Ask a running run/run_until/step_over/step_out to stop soon (reason "paused").

        Safe to call from another thread; has no effect when nothing is running.
        """
        self._pause_requested.set()
        return {"pause_requested": True}

    # == CPU ===================================================================================

    def registers_get(self) -> dict[str, int]:
        """Return all registers and CPU state flags (a, f, bc, ..., iff1, im, halted)."""
        return self.machine.cpu.registers()

    def registers_set(self, values: dict[str, int]) -> dict[str, int]:
        """Set registers by name, e.g. {"pc": 32768, "a": 5}; returns all registers."""
        try:
            self.machine.cpu.set_registers(values)
        except KeyError as error:
            raise SessionError(str(error)) from None
        return self.registers_get()

    # == memory ================================================================================

    def memory_read(self, address: int | str, length: int = 128) -> dict[str, Any]:
        """Read memory as the CPU sees it: hex dump lines with ASCII (max 4096 bytes)."""
        start = self._address(address)
        if not 1 <= length <= MAX_MEMORY_READ:
            raise SessionError(f"length must be 1-{MAX_MEMORY_READ}; use memory_save for more")
        data = bytes(self.machine.peek((start + offset) & 0xFFFF) for offset in range(length))
        lines = []
        for offset in range(0, length, 16):
            chunk = data[offset : offset + 16]
            text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in chunk)
            lines.append(f"{hex4((start + offset) & 0xFFFF)}  {chunk.hex(' ').upper():<47}  {text}")
        return {"address": hex4(start), "length": length, "hex": data.hex().upper(), "lines": lines}

    def memory_write(self, address: int | str, data: str) -> dict[str, Any]:
        """Write bytes given as hex (e.g. "3E 05 C9") to RAM at `address`."""
        start = self._address(address)
        try:
            values = bytes.fromhex(data)
        except ValueError:
            raise SessionError("data must be hex bytes, e.g. '3E05C9'") from None
        if not values or start + len(values) > 0x10000:
            raise SessionError("data must be non-empty and end within 64 KB")
        for offset, value in enumerate(values):
            self.machine.poke(start + offset, value)
        return {"address": hex4(start), "length": len(values)}

    def memory_search(
        self, pattern: str, start: int | str = 0, end: int | str = 0xFFFF
    ) -> dict[str, Any]:
        """Find a byte pattern (hex, e.g. "CD5ABB") between `start` and `end` (inclusive)."""
        try:
            needle = bytes.fromhex(pattern)
        except ValueError:
            raise SessionError("pattern must be hex bytes") from None
        if not needle:
            raise SessionError("pattern must not be empty")
        first, last = self._address(start), self._address(end)
        haystack = bytes(self.machine.peek(address) for address in range(first, last + 1))
        found = []
        position = haystack.find(needle)
        while position >= 0 and len(found) < MAX_SEARCH_RESULTS:
            found.append(hex4(first + position))
            position = haystack.find(needle, position + 1)
        return {
            "pattern": needle.hex().upper(),
            "matches": found,
            "truncated": len(found) == MAX_SEARCH_RESULTS,
        }

    # == disassembly ===========================================================================

    def disassemble(self, address: int | str | None = None, count: int = 16) -> dict[str, Any]:
        """Disassemble `count` instructions (max 256) from `address` (default: PC).

        Lines show address, bytes, instruction with symbols and comments; labels get their
        own line. Non-canonical encodings are marked "[nc]".
        """
        if not 1 <= count <= MAX_DISASSEMBLE:
            raise SessionError(f"count must be 1-{MAX_DISASSEMBLE}")
        current = self.machine.cpu.pc if address is None else self._address(address)
        lines = []
        for _ in range(count):
            instruction = self._disassemble(current)
            label = self._label(current)
            if label:
                lines.append(f"{label}:")
            lines += self._format_instruction(instruction)
            current = instruction.next_address
        return {"lines": lines, "next": hex4(current)}

    def _format_instruction(self, instruction: Instruction) -> list[str]:
        """Format an instruction (and its extra lines) for `disassemble`."""
        scope = self.machine.symbol_scope(instruction.address)
        user_comment = self.symbols.comment_at(instruction.address, scope)
        lines = []
        for number, (text, comment) in enumerate(instruction.lines(self._label)):
            if number == 0:
                raw = " ".join(f"{byte:02X}" for byte in instruction.raw)
                prefix = f"{hex4(instruction.address)}  {raw:<11}"
                comments = [c for c in (comment, user_comment) if c]
                if instruction.non_canonical:
                    comments.append("[nc]")
            else:
                prefix = " " * 17
                comments = [comment] if comment else []
            line = f"{prefix}  {text}"
            if comments:  # extra-line comments keep their indent (";   to #0150 ...", §6.15)
                joined = " ; ".join(c.strip() for c in comments) if number == 0 else comments[0]
                line = f"{line:<44} ; {joined}"
            lines.append(line.rstrip())
        return lines

    # == breakpoints ===========================================================================

    def breakpoint_add(self, address: int | str) -> dict[str, Any]:
        """Add a PC breakpoint; returns its id."""
        breakpoint_ = self.breakpoints.add(self._address(address))
        return {"id": breakpoint_.id, "address": hex4(breakpoint_.address)}

    def breakpoint_remove(self, breakpoint_id: int) -> dict[str, Any]:
        """Remove a breakpoint by id."""
        try:
            removed = self.breakpoints.remove(breakpoint_id)
        except KeyError as error:
            raise SessionError(str(error)) from None
        return {"removed": removed.id, "address": hex4(removed.address)}

    def breakpoint_list(self) -> dict[str, Any]:
        """List all breakpoints with id, address, label, enabled state and hit count."""
        entries = [
            {"id": b.id, "address": hex4(b.address), "label": self._label(b.address),
             "enabled": b.enabled, "hits": b.hits}
            for b in self.breakpoints.all()
        ]  # fmt: skip
        return {"breakpoints": entries}

    # == trace =================================================================================

    def trace_start(
        self, path: str | None = None, start: int | str = 0, end: int | str = 0xFFFF
    ) -> dict[str, Any]:
        """Start the instruction trace (ring buffer, plus a text file if `path` is given).

        Args:
            path: Text file, relative to the project directory (e.g. "output/trace.txt").
            start: First PC to record.
            end: Last PC to record.
        """
        file = self._path(path) if path else None
        self.trace.start(file, (self._address(start), self._address(end)))
        first, last = self.trace.address_range
        return {"tracing": True, "path": str(file) if file else None,
                "range": [hex4(first), hex4(last)]}  # fmt: skip

    def trace_stop(self) -> dict[str, Any]:
        """Stop the trace; the ring buffer keeps the records for trace_read."""
        self.trace.stop()
        return {"tracing": False, "records": len(self.trace.records),
                "path": str(self.trace.path) if self.trace.path else None}  # fmt: skip

    def trace_read(self, last: int = 50) -> dict[str, Any]:
        """Return the last `last` trace records (max 1000) as text lines."""
        if not 1 <= last <= MAX_TRACE_READ:
            raise SessionError(f"last must be 1-{MAX_TRACE_READ}")
        records = self.trace.last(last)
        return {"lines": [record.line() for record in records], "tracing": self.trace.enabled}

    # == symbols ===============================================================================

    def symbol_add(
        self,
        address: int | str,
        label: str | None = None,
        comment: str | None = None,
        scope: str = GLOBAL,
    ) -> dict[str, Any]:
        """Add or change a label and/or comment at `address`.

        Args:
            address: The address.
            label: Identifier, unique within the scope and the global symbols.
            comment: Free text.
            scope: "" for global (RAM, firmware), "lower" (OS ROM) or "rom0".."rom15".
        """
        try:
            symbol = self.symbols.add(self._address(address), label, comment, scope)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"address": hex4(symbol.address), "label": symbol.label,
                "comment": symbol.comment, "scope": symbol.scope}  # fmt: skip

    def comment_add(self, address: int | str, comment: str, scope: str = GLOBAL) -> dict[str, Any]:
        """Set the comment at `address` (keeps an existing label)."""
        return self.symbol_add(address, comment=comment, scope=scope)

    def symbol_remove(self, address: int | str, scope: str = GLOBAL) -> dict[str, Any]:
        """Remove the symbol at `address` in `scope`."""
        try:
            removed = self.symbols.remove(self._address(address), scope)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"removed": removed.label or hex4(removed.address), "scope": removed.scope}

    def symbol_list(
        self, text: str = "", scope: str | None = None, source: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """List symbols whose label or comment contains `text`.

        Args:
            text: Search text (case-insensitive); empty lists all.
            scope: Only this scope ("", "lower", "romN"), or all.
            source: "firmware" (preloaded jumpblock names) or "user", or both.
            limit: Maximum number of entries (max 1000).
        """
        if not 1 <= limit <= 1000:
            raise SessionError("limit must be 1-1000")
        found = self.symbols.symbols(text, scope, source)
        entries = [
            {"address": hex4(s.address), "label": s.label, "comment": s.comment,
             "scope": s.scope, "source": s.source}
            for s in found[:limit]
        ]  # fmt: skip
        return {"symbols": entries, "total": len(found)}

    def symbols_load(self, path: str = str(ANNOTATIONS_FILE)) -> dict[str, Any]:
        """Merge an annotation file (default project/annotations.txt) into the symbols."""
        file = self._path(path)
        if not file.is_file():
            raise SessionError(f"file not found: {file}")
        try:
            count = self.symbols.load(file)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"loaded": count, "path": str(file)}

    def symbols_save(self, path: str = str(ANNOTATIONS_FILE)) -> dict[str, Any]:
        """Save the user symbols to an annotation file (default project/annotations.txt)."""
        file = self._path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        return {"saved": self.symbols.save(file), "path": str(file)}
