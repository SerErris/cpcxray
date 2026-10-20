"""Instruction trace (design.md §6.6, D-24, FR-ANA-05).

While tracing is on, the session records every executed instruction before executing it: PC,
bytes, the instruction text with symbols, and the main registers. Records go to a ring buffer
(read with `Session.trace_read`) and optionally to a text file. An address range filter limits
tracing to the code of interest. Tracing costs nothing when off: the session checks a single
flag per instruction.

Phase 3 provides the text sink; CSV, JSON Lines, the GUI panel and the target/provenance
filters follow with the analysis (phase 7).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class TraceRecord:
    """One executed instruction.

    Attributes:
        sequence: Running number since the trace started.
        pc: Address of the instruction.
        raw: Instruction bytes.
        text: Instruction text with symbols.
        registers: Register summary before execution, e.g. "AF=0044 BC=…".
    """

    sequence: int
    pc: int
    raw: bytes
    text: str
    registers: str

    def line(self) -> str:
        """Format the record as one text line."""
        raw = " ".join(f"{byte:02X}" for byte in self.raw)
        return f"{self.pc:04X}  {raw:<11}  {self.text:<28}  {self.registers}"


class Trace:
    """Collects trace records into a ring buffer and an optional text file."""

    def __init__(self, capacity: int = 10_000) -> None:
        """Create a stopped trace keeping the last `capacity` records."""
        self.enabled = False
        self.records: deque[TraceRecord] = deque(maxlen=capacity)
        self.address_range: tuple[int, int] = (0x0000, 0xFFFF)
        self.path: Path | None = None
        self._file: TextIO | None = None
        self._sequence = 0

    def start(self, path: Path | None = None, address_range: tuple[int, int] | None = None) -> None:
        """Start tracing (restarting clears the buffer).

        Args:
            path: Text file to write as well, or None for the ring buffer only.
            address_range: Only instructions with first <= PC <= last are recorded.
        """
        self.stop()
        self.records.clear()
        self._sequence = 0
        self.address_range = address_range or (0x0000, 0xFFFF)
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("w", encoding="utf-8")  # pylint: disable=consider-using-with  # closed in stop()
        self.enabled = True

    def stop(self) -> None:
        """Stop tracing and close the file; the ring buffer keeps its records."""
        self.enabled = False
        if self._file is not None:
            self._file.close()
            self._file = None

    def wants(self, pc: int) -> bool:
        """Return True if an instruction at `pc` is to be recorded."""
        return self.address_range[0] <= pc <= self.address_range[1]

    def record(self, pc: int, raw: bytes, text: str, registers: str) -> None:
        """Append a record to the buffer and the file."""
        self._sequence += 1
        record = TraceRecord(self._sequence, pc, raw, text, registers)
        self.records.append(record)
        if self._file is not None:
            self._file.write(record.line() + "\n")

    def last(self, count: int) -> list[TraceRecord]:
        """Return the last `count` records, oldest first."""
        if count <= 0:
            return []
        return list(self.records)[-count:]
