"""Session methods for reading the instruction trace in detail (task 5e, the Trace tab).

A mixin of `Session` (every public method is an MCP tool, D-27). `trace_records` returns the
trace's records split into fields - the instruction as the CPU tab shows it (mnemonic, operands,
the name in them and its type), the registers before it, where it ran, whether it calls or
returns - and, for instructions the debugger stepped, their memory reads and writes
(debugger/step_accesses.py). The registers after a record are those before the next one; after
the last record they are the current registers if nothing ran since (`after`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from cpcxray.cpu.disasm import Flow, Instruction, disassemble
from cpcxray.debugger.step_accesses import StepAccesses
from cpcxray.debugger.trace import CAPTURE_BYTES, Capture, TraceGap
from cpcxray.session.debugger import DebuggerMixin
from cpcxray.session.errors import SessionError

if TYPE_CHECKING:
    from cpcxray.debugger.trace import Trace

MAX_TRACE_RECORDS = 1000
TRACED_REGISTERS = ("af", "bc", "de", "hl", "ix", "iy", "sp")  # as a capture stores them
CALL_FLOWS = (Flow.CALL, Flow.CONDITIONAL_CALL)
RETURN_FLOWS = (Flow.RETURN, Flow.CONDITIONAL_RETURN)


class TraceViewMixin(DebuggerMixin):  # pylint: disable=abstract-method  # Session implements the stubs
    """`trace_records` and the step accesses of `Session` (see the module docstring).

    It builds on `DebuggerMixin`: its disassembly rows, ROM names and observable bus.
    """

    trace: Trace
    step_accesses: StepAccesses
    _trace_tail_fresh: bool  # the last record is the last executed instruction

    # -- the step accesses ---------------------------------------------------------------------

    def _setup_trace_view(self) -> None:
        """Create the (unregistered) step access observer."""
        self.step_accesses = StepAccesses()
        self._trace_tail_fresh = False

    @contextmanager
    def _collecting_step_accesses(self) -> Iterator[None]:
        """While stepping: the step access observer, and the bus trace (always on for steps).

        Both are registered only for the step (D-12); the bus trace keeps recording afterwards
        only if its "Recording" switch is on (free runs).
        """
        bus = self._observable_bus()
        if bus is None:  # e.g. the bare test machine: nothing to watch
            yield
            return
        bus.add_observer(self.step_accesses)
        self.step_accesses.active = True
        self.bus_trace.stepping = True
        self._sync_debug_observers()
        try:
            yield
        finally:
            self.step_accesses.end()
            self.step_accesses.active = False
            bus.remove_observer(self.step_accesses)
            self.bus_trace.stepping = False
            self._sync_debug_observers()

    def _note_gaps(self, instructions: int, emulated_us: int, traced: bool) -> None:
        """After a run: a gap in each trace that did not record it (a free run, switch off)."""
        if not traced:
            self.trace.gap(instructions, emulated_us)
        if not (self.bus_trace.enabled or self.bus_trace.stepping):
            self.bus_trace.gap(instructions, emulated_us)

    # -- reading ---------------------------------------------------------------------------------

    def _capture_instruction(self, capture: Capture) -> Instruction:
        """Disassemble a trace capture (bytes beyond the captured ones: memory now)."""
        pc = capture.pc

        def reader(captured: bytes) -> Callable[[int], int]:
            """Read captured bytes near the PC, other addresses from memory."""

            def read(address: int) -> int:
                """The byte at an address (captured if within the capture)."""
                offset = (address - pc) & 0xFFFF
                return captured[offset] if offset < CAPTURE_BYTES else self.machine.peek(address)

            return read

        return disassemble(
            reader(capture.data_bytes), pc, self.machine.disassembly_profile(), self._operand_label,
            read_opcode=reader(capture.opcode_bytes),
        )  # fmt: skip

    def trace_records(self, last: int = 50) -> dict[str, Any]:
        """The last `last` trace records (max 1000) split into fields, oldest first.

        A gap - instructions that ran untraced (a free run with the trace off) - is a record
        {"kind": "gap", "sequence", "instructions", "emulated_us"}. Per instruction record
        ("kind" "instruction"): "sequence", "address", "bytes", "mnemonic", "operands" (with
        "name" and "name_type" FW / VAR / own where an operand is named, "comment"), "registers"
        before
        it (af, bc, de, hl, ix, iy, sp as 4-digit hex), "where" ("lower", "romN", "RAM") and
        "where_text", "flow" ("call", "return" or ""), "next" (the address after it), the
        owner's comment blocks "comment_before" / "comment_after" the line where it has them
        (task 9, looked up where it ran), and for stepped instructions "accesses" (access,
        address, value, variable). "after" (of the result): the registers after the last record
        (including "pc"), or None if something ran since.
        """
        if not 1 <= last <= MAX_TRACE_RECORDS:
            raise SessionError(f"last must be 1-{MAX_TRACE_RECORDS}")
        captures = list(self.trace.records)[-last:]
        records = [_gap(capture) if isinstance(capture, TraceGap) else self._trace_record(capture)
                   for capture in captures]  # fmt: skip
        after = None
        if records and self._trace_tail_fresh:
            cpu = self.machine.cpu
            after = {name: f"{getattr(cpu, name):04X}" for name in (*TRACED_REGISTERS, "pc")}
        return {"records": records, "after": after, "tracing": self.trace.enabled}

    def _trace_record(self, capture: Capture) -> dict[str, Any]:
        """One trace record as fields (see `trace_records`)."""
        instruction = self._capture_instruction(capture)
        scope = capture.where if capture.where != "RAM" else ""  # where it ran
        context = self._code_context(capture.pc, scope, capture.origin)  # D-43
        row = self._disassembly_rows(instruction, None, context)[0]
        flow = ("call" if instruction.flow in CALL_FLOWS
                else "return" if instruction.flow in RETURN_FLOWS else "")  # fmt: skip
        where = capture.where
        where_text = "RAM" if where == "RAM" else self._rom_text(where)
        if capture.origin is not None:
            where_text += f" (copied from {capture.origin.text()})"
        record = {key: row[key] for key in ("mnemonic", "operands", "name", "name_type",
                                            "comment") if key in row}  # fmt: skip
        record |= {
            "kind": "instruction", "sequence": capture.sequence,
            "address": f"{capture.pc:04X}", "bytes": row["bytes"],
            "registers": {n: f"{v:04X}" for n, v in zip(TRACED_REGISTERS, capture.registers,
                                                        strict=True)},
            "where": where, "where_text": where_text, "flow": flow,
            "next": f"{instruction.next_address:04X}",
        }  # fmt: skip
        owner = self.symbols.owner_at(capture.pc, scope, context)
        for position in ("before", "after"):  # the owner's comment blocks (task 9)
            if owner is not None and getattr(owner, position):
                record[f"comment_{position}"] = getattr(owner, position)
        accesses = self.step_accesses.by_sequence.get(capture.sequence)
        if accesses is not None:
            record["accesses"] = self._accesses(accesses, capture.pc, instruction.length,
                                                context)  # fmt: skip
        return record

    def _accesses(
        self, accesses: list[tuple[str, int, int]], pc: int, length: int, context: str | None
    ) -> list[dict[str, Any]]:
        """The reads and writes of an instruction, without reading its own operand bytes.

        The variable of an access is named as the instruction's context sees it (D-43).
        """
        result = []
        for access, address, value in accesses:
            if access == "read" and (address - pc) & 0xFFFF < length:
                continue  # the instruction's own bytes (operands)
            entry: dict[str, Any] = {"access": access, "address": f"{address:04X}",
                                     "value": f"{value:02X}"}  # fmt: skip
            if hits := self._variable_hits(address, context):
                entry["variable"] = hits[0].name
            result.append(entry)
        return result


def _gap(gap: TraceGap) -> dict[str, Any]:
    """A gap of the trace as a record (see `trace_records`)."""
    return {"kind": "gap", "sequence": gap.sequence, "instructions": gap.instructions,
            "emulated_us": gap.emulated_us}  # fmt: skip
