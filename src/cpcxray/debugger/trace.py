"""Instruction trace (design.md §6.6; D-24, FR-ANA-05).

While tracing is on, the session records every executed instruction before executing it:

* PC, where it runs ("lower", "romN" or "RAM") and, for RAM, its provenance (e.g.
  "rom5:&0105": code copied from ROM 5, D-21);
* the instruction bytes as the CPU sees them (clear) and, for a scrambled ROM, the raw bytes
  in the ROM image;
* the instruction text with symbols and the main registers.

Lazy formatting: while running, the session only captures what cannot be recomputed later -
PC, the bytes at the PC (as M1 and as data reads), the registers, where the code runs and its
RAM origin (`Capture`). Disassembly and text are produced by the session's formatter when the
buffer is read, or immediately while a file is being written (only on request).

Sinks (D-24): a ring buffer (`Session.trace_read`, the GUI panel) and optionally a file
in one of three formats: "text" (one aligned line per instruction, for reading), "csv" or
"jsonl" (JSON Lines, for scripts). Filters: an address range for the PC, and "analysis targets
only" (decided by the session: code in a target ROM, its RAM copies and RAM targets).

Tracing costs nothing when off: the session checks a single flag per instruction. Free runs
are traced only while the trace is switched on (it costs about 5x speed); the session records
stepping (step, step over, step out) into the ring buffer anyway. The file sink is written
only while the trace is on and a path was given.
"""

from __future__ import annotations

import csv
import json
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, TextIO

TRACE_FORMATS = ("text", "csv", "jsonl")
CAPTURE_BYTES = 4  # the longest instruction (incl. a CPC RST with its inline word)
CSV_FIELDS = ("sequence", "pc", "where", "origin", "bytes", "raw", "text", "registers")


def _hex_bytes(data: bytes | None) -> str:
    """Format bytes as "3E 05" (empty for None)."""
    return data.hex(" ").upper() if data else ""


@dataclass(frozen=True)
class TraceRecord:  # pylint: disable=too-many-instance-attributes  # one field per trace column
    """One executed instruction.

    Attributes:
        sequence: Running number since the trace started.
        pc: Address of the instruction.
        raw: Instruction bytes as the CPU sees them (clear bytes in a scrambled ROM).
        text: Instruction text with symbols.
        registers: Register summary before execution, e.g. "AF=0044 BC=…".
        where: "lower", "romN" or "RAM".
        origin: For RAM: where the bytes were copied from (e.g. "rom5:&0105"), else None.
        rom_raw: For a scrambled ROM: the raw (scrambled) bytes in the image, else None.
    """

    sequence: int
    pc: int
    raw: bytes
    text: str
    registers: str
    where: str = "RAM"
    origin: str | None = None
    rom_raw: bytes | None = None

    def line(self) -> str:
        """Format the record as one text line."""
        line = f"{self.pc:04X}  {_hex_bytes(self.raw):<11}  {self.text:<28}  {self.registers}"
        notes = [self.where] if self.where != "RAM" else []
        if self.origin:
            notes.append(f"RAM <- {self.origin}")
        if self.rom_raw is not None and self.rom_raw != self.raw:
            notes.append(f"raw {_hex_bytes(self.rom_raw)}")
        return f"{line}  [{'; '.join(notes)}]" if notes else line

    def as_dict(self) -> dict[str, object]:
        """Return the record as a JSON/CSV-friendly dict (hex strings for bytes and PC)."""
        values = asdict(self)
        values["pc"] = f"{self.pc:04X}"
        values["bytes"] = _hex_bytes(self.raw)
        values["raw"] = _hex_bytes(self.rom_raw)
        del values["rom_raw"]
        return values


@dataclass(frozen=True)
class Capture:  # pylint: disable=too-many-instance-attributes  # one field per captured fact
    """What the run loop stores per instruction: cheap to take, formatted later.

    Attributes:
        sequence: Running number since the trace started.
        pc: Address of the instruction.
        opcode_bytes: CAPTURE_BYTES bytes at the PC as M1 fetches see them.
        data_bytes: The same bytes as data reads see them (differ in a scrambled ROM).
        registers: AF, BC, DE, HL, IX, IY, SP before the instruction.
        where: "lower", "romN" or "RAM".
        origin: For RAM: the provenance tag (an object with `text()`), else None.
        rom_bytes: For a scrambled ROM: the raw image bytes at the PC, else None.
    """

    sequence: int
    pc: int
    opcode_bytes: bytes
    data_bytes: bytes
    registers: tuple[int, ...]
    where: str
    origin: Any = None
    rom_bytes: bytes | None = None


type Formatter = Callable[[Capture], TraceRecord]
"""Turns a capture into a readable record (disassembly with symbols; provided by the session)."""


class Trace:  # pylint: disable=too-many-instance-attributes  # sink, filters and buffer state
    """Collects captures into a ring buffer and, while a file is open, formatted lines."""

    def __init__(self, formatter: Formatter, capacity: int = 10_000) -> None:
        """Create a stopped trace keeping the last `capacity` captures."""
        self.formatter = formatter
        self.enabled = False
        self.records: deque[Capture] = deque(maxlen=capacity)
        self.address_range: tuple[int, int] = (0x0000, 0xFFFF)
        self.targets_only = False
        self.path: Path | None = None
        self.file_format = "text"
        self._file: TextIO | None = None
        self._csv: csv.DictWriter[str] | None = None
        self._sequence = 0

    def start(
        self,
        path: Path | None = None,
        address_range: tuple[int, int] | None = None,
        file_format: str = "text",
        targets_only: bool = False,
    ) -> None:
        """Start tracing (restarting clears the buffer).

        Args:
            path: File to write as well, or None for the ring buffer only.
            address_range: Only instructions with first <= PC <= last are recorded.
            file_format: "text", "csv" or "jsonl".
            targets_only: Record only code of analysis targets (incl. their RAM copies).

        Raises:
            ValueError: For an unknown format.
        """
        if file_format not in TRACE_FORMATS:
            raise ValueError(f"trace format must be one of {', '.join(TRACE_FORMATS)}")
        self.stop()
        self.records.clear()
        self._sequence = 0
        self.address_range = address_range or (0x0000, 0xFFFF)
        self.targets_only = targets_only
        self.path = path
        self.file_format = file_format
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("w", encoding="utf-8", newline="")  # pylint: disable=consider-using-with  # closed in stop()
            if file_format == "csv":
                self._csv = csv.DictWriter(self._file, fieldnames=CSV_FIELDS)
                self._csv.writeheader()
        self.enabled = True

    def stop(self) -> None:
        """Stop tracing and close the file; the ring buffer keeps its records."""
        self.enabled = False
        if self._file is not None:
            self._file.close()
            self._file = None
            self._csv = None

    def wants(self, pc: int) -> bool:
        """Return True if an instruction at `pc` is inside the address range."""
        return self.address_range[0] <= pc <= self.address_range[1]

    def record(  # pylint: disable=too-many-arguments  # one argument per captured fact
        self,
        pc: int,
        opcode_bytes: bytes,
        data_bytes: bytes,
        registers: tuple[int, ...],
        *,
        where: str = "RAM",
        origin: Any = None,
        rom_bytes: bytes | None = None,
    ) -> None:
        """Store a capture; while a file is open, also format and write it."""
        self._sequence += 1
        capture = Capture(self._sequence, pc, opcode_bytes, data_bytes, registers, where, origin,
                          rom_bytes)  # fmt: skip
        self.records.append(capture)
        if self._file is None:
            return
        record = self.formatter(capture)
        if self.file_format == "text":
            self._file.write(record.line() + "\n")
        elif self.file_format == "jsonl":
            self._file.write(json.dumps(record.as_dict()) + "\n")
        elif self._csv is not None:
            self._csv.writerow(record.as_dict())

    def last(self, count: int) -> list[TraceRecord]:
        """Return the last `count` records, oldest first, formatted now."""
        if count <= 0:
            return []
        return [self.formatter(capture) for capture in list(self.records)[-count:]]
