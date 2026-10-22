"""The `Session` façade: the only API that acts on the machine (design.md §6.16, D-27).

GUI, MCP server and scripts are thin clients of this class; every public method is also an MCP
tool with the same name and parameters (FR-MCP-02; the parity test enforces it). Results are
plain JSON-compatible dicts and lists, so the MCP layer passes them through unchanged.

Conventions for clients:

* Addresses may be given as int, as hex string ("BB5A", "&BB5A", "#BB5A", "0xBB5A") or as a
  symbol label ("TXT_OUTPUT"; case-insensitive if unique). Addresses in results are 4-digit
  hex strings.
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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cpcxray.analysis.provenance import RamTag
from cpcxray.cpu.disasm import Flow, Instruction, disassemble
from cpcxray.debugger.symbols import GLOBAL, SymbolError, SymbolTable, parse_address
from cpcxray.debugger.trace import CAPTURE_BYTES, Capture, Trace, TraceRecord
from cpcxray.debugger.variables import Variable, VariableHit, VariableMap
from cpcxray.devices.key_map import KeyMap, KeyMapError
from cpcxray.machine.config import ConfigError
from cpcxray.machine.cpc464 import CPC464, build_cpc464
from cpcxray.machine.flat import FlatMachine, Machine
from cpcxray.machine.screen import FONT_ADDRESS, screen_text
from cpcxray.session.analysis import AnalysisMixin
from cpcxray.session.debugger import DebuggerMixin
from cpcxray.session.disks import DiskMixin
from cpcxray.session.errors import SessionError
from cpcxray.ui.png import encode_png
from cpcxray.ui.renderer import double_lines, render

DEFAULT_TIMEOUT_S = 10.0
MAX_TIMEOUT_S = 600.0
MAX_MEMORY_READ = 4096
MAX_DISASSEMBLE = 256
MAX_TRACE_READ = 1000
MAX_SEARCH_RESULTS = 1000
ANNOTATIONS_FILE = Path("project") / "annotations.txt"
DEFAULT_CONFIG = Path("config") / "machine.toml"
CHECK_INTERVAL = 4096  # instructions between checks of pause and wall clock
_REPEATING_BLOCK = ("ldir", "lddr", "cpir", "cpdr", "inir", "indr", "otir", "otdr")


def hex4(value: int) -> str:
    """Format a 16-bit value as 4 hex digits."""
    return f"{value:04X}"


@dataclass
class _RunLimits:
    """Bounds of one run (FR-MCP-04)."""

    max_instructions: int | None
    max_us: int | None
    timeout_s: float


# The façade owns all debugger state and offers one public method per MCP tool (D-27).
class Session(AnalysisMixin, DebuggerMixin, DiskMixin):  # pylint: disable=too-many-public-methods,too-many-instance-attributes
    """Owns a machine and offers every debugger operation (D-27).

    Attributes:
        machine: The emulated machine (phase 3: `FlatMachine`).
        symbols: Labels and comments (firmware names preloaded).
        breakpoints: Breakpoints of all kinds (FR-DBG-07).
        bus_trace: The bus access ring buffer (FR-DBG-08).
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
        self.symbols = SymbolTable(model=getattr(self.machine, "firmware_model", "464"))
        self.variables = self._variable_map(self.machine)
        self._setup_debugger()
        self.analysis_enabled = True  # the analysis observers run while targets exist
        self.trace = Trace(self._format_capture)
        self._recording_steps = False  # step/step_over/step_out record into the trace buffer
        self._pause_requested = threading.Event()
        self.pause_generation = 0  # counts pause() calls; the GUI stops free-running on change
        annotations = self.project_dir / ANNOTATIONS_FILE
        if annotations.exists():
            self.symbols.load(annotations)
        self._reset_analysis_state()
        if isinstance(self.machine, CPC464):
            self._setup_analysis()  # targets from the config, saved access maps and databases

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
            if symbol is not None:
                return symbol.address
            variable = self._variable_named(value)
            if variable is not None:
                return variable.address
            raise SessionError(f"{value!r} is neither a hex address nor a known label") from None

    def _label(self, address: int) -> str | None:
        """Return the label for `address` in the scope that applies to it now."""
        return self.symbols.label_at(address, self.machine.symbol_scope(address))

    @staticmethod
    def _variable_map(machine: Machine) -> VariableMap | None:
        """The known OS/AMSDOS variables of a CPC (FR-DBG-11); None for the flat machine."""
        model = getattr(machine, "firmware_model", None)
        return VariableMap(model) if model is not None else None

    def _variable_hits(self, address: int) -> list[VariableHit]:
        """The known variables containing `address` now, the smallest first."""
        if self.variables is None:
            return []
        return self.variables.lookup(address, self.machine.peek)

    def _variable_named(self, label: str) -> Variable | None:
        """The known variable with this label, if it exists now (AMSDOS only when installed)."""
        if self.variables is None:
            return None
        return self.variables.find(label.strip(), self.machine.peek)

    def _operand_label(self, address: int) -> str | None:
        """Name an operand: a symbol label, else a known variable (`NAME` or `NAME+n`)."""
        label = self._label(address)
        if label is not None:
            return label
        hits = self._variable_hits(address)
        return hits[0].name if hits else None

    def _disassemble(self, address: int) -> Instruction:
        """Decode the instruction at `address` from the CPU's view of memory."""
        return disassemble(
            self.machine.peek, address, self.machine.disassembly_profile(), self._operand_label,
            read_opcode=self.machine.peek_opcode,
        )  # fmt: skip

    def _register_summary(self) -> str:
        """Return the main registers as one line."""
        cpu = self.machine.cpu
        return (
            f"AF={hex4(cpu.af)} BC={hex4(cpu.bc)} DE={hex4(cpu.de)} HL={hex4(cpu.hl)} "
            f"IX={hex4(cpu.ix)} IY={hex4(cpu.iy)} SP={hex4(cpu.sp)} PC={hex4(cpu.pc)}"
        )

    def _stop_result(
        self, reason: str, instructions: int, emulated_us: int,
        breakpoint_hit: dict[str, Any] | None = None,
    ) -> dict[str, Any]:  # fmt: skip
        """Describe where and why a run or step stopped (with the breakpoint that hit)."""
        pc = self.machine.cpu.pc
        result: dict[str, Any] = {
            "reason": reason,
            "pc": hex4(pc),
            "label": self._label(pc),
            "next": self._disassemble(pc).text(self._operand_label),
            "instructions": instructions,
            "emulated_us": emulated_us,
            "registers": self._register_summary(),
        }
        if breakpoint_hit is not None:
            result["breakpoint"] = breakpoint_hit
        return result

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
        breakpoint it stopped at. PC breakpoints stop before an instruction, access
        breakpoints (memory, I/O, ROM select, interrupt) after the instruction that hit them.

        Args:
            limits: Instruction, emulated-time and wall-clock limits.
            until_address: Stop when the PC reaches this address ("address_reached").
            finished: Called after every instruction; a non-None result is the stop reason.
        """
        machine, cpu, trace = self.machine, self.machine.cpu, self.trace
        tracing = trace.enabled or self._recording_steps  # free runs: only if switched on
        self._pause_requested.clear()
        deadline = time.monotonic() + limits.timeout_s
        instructions = emulated_us = 0
        reason = None
        breakpoint_hit = None
        while reason is None:
            pc = cpu.pc
            if instructions:
                if pc == until_address:
                    reason = "address_reached"
                    break
                breakpoint_hit = self._pc_breakpoint(pc)
                if breakpoint_hit is not None:
                    reason = "breakpoint"
                    break
            reason = self._limit_reached(limits, instructions, emulated_us, deadline)
            if reason is not None:
                break
            if tracing and trace.wants(pc):
                self._trace_instruction(pc)
            emulated_us += machine.step()
            instructions += 1
            if self._breakpoint_observer is not None:
                breakpoint_hit = self._access_breakpoint()
                if breakpoint_hit is not None:
                    reason = "breakpoint"
                    break
            if finished is not None:
                reason = finished()
        return self._stop_result(reason, instructions, emulated_us, breakpoint_hit)

    def _scrambled_bytes(self, address: int, length: int) -> bytes | None:
        """Return the raw image bytes if `address` lies in a paged-in scrambled ROM, else None."""
        if not isinstance(self.machine, CPC464):
            return None
        scope = self.machine.symbol_scope(address)
        if not scope:
            return None
        rom = self._rom_slot(scope)
        if not rom.scrambled:
            return None
        first = address & 0x3FFF
        return rom.data[first : first + length]

    def _trace_instruction(self, pc: int) -> None:
        """Capture the instruction at `pc` for the trace (applying the targets-only filter).

        Only the facts are taken (bytes, registers, location); the disassembly is made when
        the trace is read (`_format_capture`), which keeps tracing cheap.
        """
        machine = self.machine
        scope = machine.symbol_scope(pc)
        where = scope or "RAM"
        observer = self._analysis_observer
        origin = observer.ram_origin[pc] if observer is not None and not scope else None
        if self.trace.targets_only and not self._in_analysis_target(pc, where, origin):
            return
        memory = getattr(machine, "memory", None)
        if scope or memory is None:
            data_bytes = bytes(machine.peek((pc + i) & 0xFFFF) for i in range(CAPTURE_BYTES))
        else:  # RAM of the CPC: one slice (the rare wrap at &FFFF reads zeros)
            data_bytes = bytes(memory.ram[pc : pc + CAPTURE_BYTES]).ljust(CAPTURE_BYTES, b"\0")
        rom_bytes = self._scrambled_bytes(pc, CAPTURE_BYTES) if scope else None
        # M1 fetches see the raw image of a scrambled ROM, elsewhere the same bytes as data reads
        opcode_bytes = rom_bytes if rom_bytes is not None else data_bytes
        cpu = machine.cpu
        registers = (cpu.af, cpu.bc, cpu.de, cpu.hl, cpu.ix, cpu.iy, cpu.sp)
        self.trace.record(pc, opcode_bytes, data_bytes, registers, where=where, origin=origin,
                          rom_bytes=rom_bytes)  # fmt: skip

    def _format_capture(self, capture: Capture) -> TraceRecord:
        """Disassemble a trace capture into a readable record (bytes beyond it: memory now)."""
        pc = capture.pc

        def reader(captured: bytes) -> Callable[[int], int]:
            """Read captured bytes near the PC, other addresses from memory."""

            def read(address: int) -> int:
                """The byte at an address (captured if within the capture)."""
                offset = (address - pc) & 0xFFFF
                return captured[offset] if offset < CAPTURE_BYTES else self.machine.peek(address)

            return read

        instruction = disassemble(
            reader(capture.data_bytes), pc, self.machine.disassembly_profile(), self._operand_label,
            read_opcode=reader(capture.opcode_bytes),
        )  # fmt: skip
        af, bc, de, hl, ix, iy, sp = capture.registers
        registers = (f"AF={hex4(af)} BC={hex4(bc)} DE={hex4(de)} HL={hex4(hl)} "
                     f"IX={hex4(ix)} IY={hex4(iy)} SP={hex4(sp)} PC={hex4(pc)}")  # fmt: skip
        rom_raw = capture.rom_bytes[: instruction.length] if capture.rom_bytes else None
        origin = capture.origin.text() if capture.origin is not None else None
        return TraceRecord(capture.sequence, pc, instruction.raw,
                           instruction.text(self._operand_label),
                           registers, capture.where, origin, rom_raw)  # fmt: skip

    def _in_analysis_target(self, pc: int, where: str, origin: RamTag | None) -> bool:
        """True if code at `pc` belongs to an analysis target (ROM, its RAM copy, RAM range)."""
        if where in self.analysis_targets:
            return True
        if origin is not None and origin.target in self.analysis_targets:
            return True
        return any(t.kind == "ram" and t.contains(pc) for t in self.analysis_targets.values())

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

    # == CPC 464: configuration, ROMs, devices, screen ==========================================

    def _cpc(self) -> CPC464:
        """Return the machine as a CPC 464, or raise if it is the flat test machine."""
        if not isinstance(self.machine, CPC464):
            raise SessionError("not available on the flat Z80 machine; use load_config first")
        return self.machine

    def load_config(self, path: str = str(DEFAULT_CONFIG)) -> dict[str, Any]:
        """Power on a CPC with the model and ROMs of a machine.toml (default config/machine.toml).

        Replaces the current machine (memory is cleared); the owner's symbols, breakpoints and
        the trace are kept, the firmware names follow the new model (D-26); the breakpoint and
        bus-trace observers move to the new bus.
        """
        try:
            machine = build_cpc464(self._path(path))
        except ConfigError as error:
            raise SessionError(str(error)) from None
        self.machine = machine
        self.symbols.set_firmware(machine.firmware_model)
        self.variables = self._variable_map(machine)
        self._breakpoint_observer = None
        self._bus_trace_observing = False
        self._sync_debug_observers()
        self._setup_analysis()
        return self.machine_status()

    def rom_list(self) -> dict[str, Any]:
        """List the installed ROMs (slot, file, header type/version) and the skipped ones."""
        machine = self._cpc()
        return {"roms": machine.rom_list(), "skipped": list(machine.skipped_roms)}

    def rom_load(self, slot: str | int, path: str) -> dict[str, Any]:
        """Load a 16 KB ROM image into "lower" or upper slot 0-15, then reset (D-09)."""
        machine = self._cpc()
        try:
            machine.load_rom(self._slot(slot), self._path(path))
        except (ConfigError, ValueError) as error:
            raise SessionError(str(error)) from None
        machine.reset()
        return self.rom_list()

    def rom_unload(self, slot: str | int) -> dict[str, Any]:
        """Empty "lower" or upper slot 0-15, then reset (D-09)."""
        machine = self._cpc()
        machine.unload_rom(self._slot(slot))
        machine.reset()
        return self.rom_list()

    @staticmethod
    def _slot(slot: str | int) -> str | int:
        """Validate a slot: "lower" or 0-15 (int or decimal string)."""
        if slot == "lower":
            return "lower"
        try:
            number = int(slot)
        except ValueError:
            raise SessionError(f"slot must be 'lower' or 0-15, got {slot!r}") from None
        if not 0 <= number <= 15:
            raise SessionError(f"slot must be 'lower' or 0-15, got {slot!r}")
        return number

    def screen_image(self, path: str | None = None) -> dict[str, Any]:
        """Render the CPC screen (with border, scan lines doubled: 768 x 544) as PNG.

        Over MCP the image is returned as image content. With `path` (relative to the project
        directory, e.g. "output/screen.png") it is also written to that file.
        """
        image = double_lines(render(self._cpc().snapshot()))
        png = encode_png(image)
        file = None
        if path:
            file = self._path(path)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(png)
        height, width = image.shape[:2]
        return {"width": width, "height": height, "path": str(file) if file else None, "png": png}

    # -- keyboard (phase 6) -------------------------------------------------------------------

    def _key_map(self) -> KeyMap:
        """Return the machine's key map, as a SessionError if it is unavailable."""
        try:
            return self._cpc().key_map()
        except KeyMapError as error:
            raise SessionError(str(error)) from None

    def type_text(
        self, text: str, wait: bool = True, timeout_s: float | None = None
    ) -> dict[str, Any]:
        r"""Type text on the CPC keyboard, e.g. "PRINT 1+1\r" (\r or \n = RETURN).

        Each character's keys (with SHIFT or CONTROL where needed) are held for 40 ms and
        released for 40 ms of emulated time, so the firmware sees every key (about 12
        characters per emulated second).

        Args:
            text: The characters to type.
            wait: Run the machine until everything is typed (plus 0.1 s for the firmware to
                react); False only queues the keys (the GUI keeps running and types them).
            timeout_s: Wall-clock limit when waiting (default 10 s).
        """
        key_map = self._key_map()
        try:
            groups = [key_map.keys_for_character(character) for character in text]
        except KeyMapError as error:
            raise SessionError(str(error)) from None
        machine = self._cpc()
        done_us = machine.keyboard.schedule_typing(groups, machine.clock_us)
        result: dict[str, Any] = {"characters": len(text), "typed_by_us": done_us}
        if not wait:
            return result
        settle_us = done_us + 100_000
        limits = self._limits(None, settle_us - machine.clock_us + 1_000_000, timeout_s)

        def typed() -> str | None:
            """Stop once the keys are typed and the firmware had time to react."""
            if not machine.keyboard.typing and machine.clock_us >= settle_us:
                return "typed"
            return None

        result.update(self._run_loop(limits, finished=typed))
        return result

    def key_press(self, key: str) -> dict[str, Any]:
        """Hold a CPC key until key_release.

        Key names come from the OS ROM tables: characters like "a", "1", "," and names like
        "return", "shift", "control", "cursor_up", "f1", "space", "esc", "copy", "del",
        "joy_fire1".
        """
        number = self._key_number(key)
        keyboard = self._cpc().keyboard
        keyboard.press(number, "api")
        return {"key": key, "number": number, "held": keyboard.held_keys()}

    def key_release(self, key: str) -> dict[str, Any]:
        """Release a key held by key_press; "all" releases every key held by key_press."""
        keyboard = self._cpc().keyboard
        if key == "all":
            keyboard.release_all("api")
        else:
            keyboard.release(self._key_number(key), "api")
        return {"key": key, "held": keyboard.held_keys()}

    def _key_number(self, name: str) -> int:
        """Return the key number for a key name (SessionError if unknown)."""
        try:
            return self._key_map().key_number(name)
        except KeyMapError as error:
            raise SessionError(str(error)) from None

    def devices_get(self) -> dict[str, Any]:
        """Return the state of Gate Array, CRTC, ROM select, SystemIO and keyboard."""
        return self._cpc().devices()

    def screen_text(self) -> dict[str, Any]:
        """Return the text on the CPC screen, recognised from screen RAM with the ROM font.

        One string per character row; unknown cells show "?".
        """
        machine = self._cpc()
        lower = machine.memory.lower_rom
        if lower is None:
            raise SessionError("no lower ROM installed: the font is needed to read the screen")
        font = lower.data[FONT_ADDRESS : FONT_ADDRESS + 0x0800]  # the lower ROM starts at &0000
        screen = machine.memory.base_ram()  # the CRTC ignores 6128 banking: blocks 0-3
        lines = screen_text(
            lambda address: screen[address], font,
            machine.crtc.registers, machine.gate_array.mode,
        )  # fmt: skip
        return {"mode": machine.gate_array.mode, "lines": lines}

    # == execution =============================================================================

    def step(self, count: int = 1) -> dict[str, Any]:
        """Execute `count` instructions (1-100000); stops early at a breakpoint.

        Returns:
            Stop reason ("steps_done", "breakpoint", ...), PC, next instruction, registers.
        """
        if not 1 <= count <= 100_000:
            raise SessionError("count must be 1-100000")
        with self._step_recording():
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
        with self._step_recording():
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
        with self._step_recording():
            return self._run_loop(self._limits(None, None, timeout_s), finished=returned)

    @contextmanager
    def _step_recording(self) -> Iterator[None]:
        """Record the instructions of a step into the trace ring buffer, even with the trace off.

        Stepping is where the trace is wanted (and cheap: few instructions); free runs are
        traced only when the trace is switched on, because tracing costs about 5x speed.
        The file sink is written only while the trace is on.
        """
        previous, self._recording_steps = self._recording_steps, True
        try:
            yield
        finally:
            self._recording_steps = previous

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
        self.pause_generation += 1
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
        for number, (text, comment) in enumerate(instruction.lines(self._operand_label)):
            if number == 0:
                raw = " ".join(f"{byte:02X}" for byte in instruction.raw)
                prefix = f"{hex4(instruction.address)}  {raw:<11}"
                comments = [c for c in (comment, user_comment) if c]
                scrambled_raw = self._scrambled_bytes(instruction.address, instruction.length)
                if scrambled_raw is not None and scrambled_raw != instruction.raw:
                    comments.append(f"raw {scrambled_raw.hex(' ').upper()}")
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

    # == trace =================================================================================

    def trace_start(  # pylint: disable=too-many-arguments  # one argument per trace option
        self,
        path: str | None = None,
        start: int | str = 0,
        end: int | str = 0xFFFF,
        file_format: str = "text",
        targets_only: bool = False,
    ) -> dict[str, Any]:
        """Switch the instruction trace on for runs (ring buffer, plus a file if `path`, D-24).

        Tracing slows the emulation to about 1/5; stepping (step, step_over, step_out) is
        recorded into the ring buffer anyway, without switching the trace on.

        Records show where code runs (ROM slot or RAM, with the ROM origin of copied code)
        and, for scrambled ROMs, the raw bytes next to the clear ones.

        Args:
            path: File, relative to the project directory (e.g. "output/trace.txt").
            start: First PC to record.
            end: Last PC to record.
            file_format: "text", "csv" or "jsonl" (JSON Lines).
            targets_only: Record only analysis-target code (incl. its RAM copies).
        """
        file = self._path(path) if path else None
        try:
            self.trace.start(file, (self._address(start), self._address(end)), file_format,
                             targets_only)  # fmt: skip
        except ValueError as error:
            raise SessionError(str(error)) from None
        first, last = self.trace.address_range
        return {"tracing": True, "path": str(file) if file else None, "format": file_format,
                "range": [hex4(first), hex4(last)], "targets_only": targets_only}  # fmt: skip

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

    def address_explain(self, address: int | str) -> dict[str, Any]:
        """Tell what an address is: its symbol and the known OS/AMSDOS variables containing it.

        Variables (FR-DBG-11) are the firmware's of this machine (464/664/6128) and AMSDOS's
        when it is installed (its workspace where AMSDOS put it); the smallest one comes first,
        named `LABEL` or `LABEL+offset`.
        """
        value = self._address(address)
        result: dict[str, Any] = {"address": hex4(value), "label": self._label(value),
                                  "variables": []}  # fmt: skip
        if self.variables is not None:
            result["model"] = self.variables.model
            result["variables"] = [{"name": hit.name, **hit.variable.describe()}
                                   for hit in self._variable_hits(value)]  # fmt: skip
        return result

    def variable_list(self, text: str = "", limit: int = 100) -> dict[str, Any]:
        """List the known OS/AMSDOS variables (FR-DBG-11) whose label or description has `text`.

        Args:
            text: Search text (case-insensitive); empty lists all.
            limit: Maximum number of entries (max 1000).
        """
        if not 1 <= limit <= 1000:
            raise SessionError("limit must be 1-1000")
        if self.variables is None:
            return {"variables": [], "total": 0}
        wanted = text.lower()
        found = [v for v in self.variables.variables(self.machine.peek)
                 if wanted in v.label.lower() or wanted in v.description.lower()]  # fmt: skip
        return {"model": self.variables.model, "variables": [v.describe() for v in found[:limit]],
                "total": len(found)}  # fmt: skip

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
