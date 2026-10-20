"""Instruction trace (design.md §6.6; D-24, FR-ANA-05).

While tracing is on, the session records every executed instruction before executing it:

* PC, where it runs ("lower", "romN" or "RAM") and, for RAM, its provenance (e.g.
  "rom5:&0105": code copied from VDOS, D-21);
* the instruction bytes as the CPU sees them (clear) and, for a scrambled ROM, the raw bytes
  in the ROM image;
* the instruction text with symbols and the main registers.

Sinks (D-24): a ring buffer (`Session.trace_read`, later the GUI panel) and optionally a file
in one of three formats: "text" (one aligned line per instruction, for reading), "csv" or
"jsonl" (JSON Lines, for scripts). Filters: an address range for the PC, and "analysis targets
only" (decided by the session: code in a target ROM, its RAM copies and RAM targets).

Tracing costs nothing when off: the session checks a single flag per instruction.
"""

from __future__ import annotations

import csv
import json
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

TRACE_FORMATS = ("text", "csv", "jsonl")
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


class Trace:  # pylint: disable=too-many-instance-attributes  # sink, filters and buffer state
    """Collects trace records into a ring buffer and an optional file."""

    def __init__(self, capacity: int = 10_000) -> None:
        """Create a stopped trace keeping the last `capacity` records."""
        self.enabled = False
        self.records: deque[TraceRecord] = deque(maxlen=capacity)
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

    def record(  # pylint: disable=too-many-arguments  # one argument per trace column
        self,
        pc: int,
        raw: bytes,
        text: str,
        registers: str,
        *,
        where: str = "RAM",
        origin: str | None = None,
        rom_raw: bytes | None = None,
    ) -> None:
        """Append a record to the buffer and write it to the file."""
        self._sequence += 1
        record = TraceRecord(self._sequence, pc, raw, text, registers, where, origin, rom_raw)
        self.records.append(record)
        if self._file is None:
            return
        if self.file_format == "text":
            self._file.write(record.line() + "\n")
        elif self.file_format == "jsonl":
            self._file.write(json.dumps(record.as_dict()) + "\n")
        elif self._csv is not None:
            self._csv.writerow(record.as_dict())

    def last(self, count: int) -> list[TraceRecord]:
        """Return the last `count` records, oldest first."""
        if count <= 0:
            return []
        return list(self.records)[-count:]
