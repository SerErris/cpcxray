"""The analysis database: what every byte of a target is (design.md §6.15; D-31).

The sjasmplus source is generated from this database, not from the trace: the trace is
chronological, repeats loops and says nothing about never-executed bytes, while the database has
exactly one entry per target byte and accumulates knowledge over many runs (D-31).

Per byte:

* data kind (`Kind`): instruction start or body, RST inline operand, far address object, string,
  word table, byte data or unknown;
* source (`Source`) of that classification: observed at run time, static guess (classifier), or
  the owner. A classification is only replaced by one from an equal or stronger source:
  owner > observed > static.

Also kept: runtime observations by CPU address (RST executions, which ROM a SIDE CALL or FAR
CALL reached, far address objects) and the owner's range overrides. The per-byte kinds
are derived data: the classifier rebuilds them from the persistent facts (owner overrides,
access map, target flags, observations) before generating source, so dropping an override
takes effect. The session stores the target flags and relocated blocks in the same file.

File: `project/<rom>.analysis.json` (`ram_<name>.analysis.json` for RAM targets); loading
merges into the current state (the stronger source wins per byte), so knowledge from several
runs adds up.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any


class Kind(IntEnum):
    """What a byte is (D-31)."""

    UNKNOWN = 0
    INSN_START = 1
    INSN_BODY = 2
    RST_OPERAND = 3
    FAR_ADDR = 4
    STRING = 5
    WORD_TABLE = 6
    BYTE_DATA = 7


class Source(IntEnum):
    """Where a classification came from; higher values win."""

    NONE = 0
    STATIC = 1
    OBSERVED = 2
    OWNER = 3


KIND_NAMES = {kind: kind.name.lower() for kind in Kind}
FILE_VERSION = 1


@dataclass
class TargetDatabase:
    """The analysis database of one target.

    Attributes:
        target: Target name, e.g. "rom5".
        start: CPU address of the first byte (&C000 for upper ROMs).
        length: Number of bytes.
        kinds: `Kind` per byte.
        sources: `Source` per byte.
        observations: Runtime facts by CPU address, e.g. {"C123": {"side_call_rom": 6}}.
        overrides: Owner's range classifications [{"first": "C123", "last": "C140",
            "kind": "string"}], applied with source OWNER.
    """

    target: str
    start: int
    length: int
    kinds: bytearray = field(default_factory=bytearray)
    sources: bytearray = field(default_factory=bytearray)
    observations: dict[str, dict[str, Any]] = field(default_factory=dict)
    overrides: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Allocate the per-byte arrays."""
        if not self.kinds:
            self.kinds = bytearray(self.length)
        if not self.sources:
            self.sources = bytearray(self.length)

    # -- classification ----------------------------------------------------------------------

    def classify(self, offset: int, kind: Kind, source: Source) -> bool:
        """Set the kind of one byte if `source` is at least as strong as the current one.

        Returns:
            True if the byte changed.
        """
        if not 0 <= offset < self.length or source < self.sources[offset]:
            return False
        changed = self.kinds[offset] != kind or self.sources[offset] != source
        self.kinds[offset] = kind
        self.sources[offset] = source
        return changed

    def classify_instruction(
        self, offset: int, length: int, source: Source, operand_offsets: tuple[int, ...] = ()
    ) -> None:
        """Mark an instruction: start, body bytes, and inline RST operands."""
        self.classify(offset, Kind.INSN_START, source)
        for index in range(1, length):
            kind = Kind.RST_OPERAND if index in operand_offsets else Kind.INSN_BODY
            self.classify(offset + index, kind, source)

    def kind_at(self, offset: int) -> Kind:
        """Return the kind of a byte."""
        return Kind(self.kinds[offset])

    def add_override(self, first: int, last: int, kind: Kind) -> None:
        """Store an owner classification of the offsets first..last (applied by the classifier).

        A new override replaces older ones for exactly the same range.
        """
        entry = {"first": f"{self.start + first:04X}", "last": f"{self.start + last:04X}",
                 "kind": KIND_NAMES[kind]}  # fmt: skip
        self.overrides = [
            o for o in self.overrides if (o["first"], o["last"]) != (entry["first"], entry["last"])
        ]
        self.overrides.append(entry)

    def remove_override(self, first: int, last: int) -> None:
        """Drop the owner classification of exactly the offsets first..last."""
        key = (f"{self.start + first:04X}", f"{self.start + last:04X}")
        self.overrides = [o for o in self.overrides if (o["first"], o["last"]) != key]

    def override_ranges(self) -> list[tuple[int, int, Kind]]:
        """Return the overrides as (first offset, last offset, kind), oldest first."""
        by_name = {name: kind for kind, name in KIND_NAMES.items()}
        return [
            (int(o["first"], 16) - self.start, int(o["last"], 16) - self.start, by_name[o["kind"]])
            for o in self.overrides
        ]

    def observe(self, address: int, key: str, value: Any) -> None:
        """Record a runtime fact for a CPU address (e.g. the ROM a SIDE CALL reached)."""
        self.observations.setdefault(f"{address:04X}", {})[key] = value

    def counts(self) -> dict[str, dict[str, int]]:
        """Return the number of bytes per kind and per source."""
        return {
            "kinds": {KIND_NAMES[k]: self.kinds.count(k) for k in Kind},
            "sources": {s.name.lower(): self.sources.count(s) for s in Source},
        }

    def reset_classification(self) -> None:
        """Forget all kinds before a rebuild from the facts (overrides, flags, observations)."""
        self.kinds[:] = bytes(self.length)
        self.sources[:] = bytes(self.length)

    def observations_at(self, offset: int) -> dict[str, Any]:
        """Return the runtime facts recorded for a byte (empty dict if none)."""
        return self.observations.get(f"{self.start + offset:04X}", {})

    # -- persistence -------------------------------------------------------------------------

    def to_json(self, extra: dict[str, Any] | None = None) -> str:
        """Serialise (per-byte arrays as hex strings, compact and diff-friendly per run)."""
        data: dict[str, Any] = {
            "version": FILE_VERSION,
            "target": self.target,
            "start": f"{self.start:04X}",
            "length": self.length,
            "kinds": self.kinds.hex(),
            "sources": self.sources.hex(),
            "observations": self.observations,
            "overrides": self.overrides,
            **(extra or {}),
        }
        return json.dumps(data, indent=1)

    def save(self, path: str | Path, extra: dict[str, Any] | None = None) -> None:
        """Write the database file (extra: e.g. access map and flags of the target)."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(self.to_json(extra), encoding="utf-8")

    def merge_file(self, path: str | Path) -> dict[str, Any]:
        """Merge a saved database: per byte the stronger source wins (ties: the file wins).

        Returns:
            The file's other entries (e.g. relocations, access map) for the caller.

        Raises:
            ValueError: If the file belongs to another target or has a wrong size.
        """
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data["target"] != self.target or data["length"] != self.length:
            raise ValueError(f"{path}: database of {data['target']} ({data['length']} bytes)")
        kinds, sources = bytes.fromhex(data["kinds"]), bytes.fromhex(data["sources"])
        for offset in range(self.length):
            if sources[offset] and sources[offset] >= self.sources[offset]:
                self.kinds[offset] = kinds[offset]
                self.sources[offset] = sources[offset]
        for address, facts in data.get("observations", {}).items():
            self.observations.setdefault(address, {}).update(facts)
        for override in data.get("overrides", []):
            if override not in self.overrides:
                self.overrides.append(override)
        known = {"version", "target", "start", "length", "kinds", "sources", "observations",
                 "overrides"}  # fmt: skip
        return {key: value for key, value in data.items() if key not in known}
