"""Known RAM variables of the firmware and of AMSDOS, per machine (FR-DBG-11, D-34).

The debugger uses them to tell whether an address is a known OS or AMSDOS variable: lookups,
operand names in disassembly and trace, breakpoints on a variable name. The data comes from
docs/memory.md (generated into debugger/variable_tables.py): firmware variables have one
address per machine (464, 664, 6128); AMSDOS's are the same on every machine.

An address inside a variable is named `LABEL+offset`; when several variables contain it (a
pack's area, a block, a field of the block), the smallest one comes first.

AMSDOS keeps a fixed block at &BE40-&BE7F and a workspace that its ROM initialisation reserves,
normally from &A6FC. Neither exists on a machine without AMSDOS (on a 464 the fixed block is
then stack), and the workspace moves down when ROMs with higher numbers reserve RAM first. So
both are looked up from the running machine: AMSDOS_MEMORY_POOL (&BE7D) holds the workspace
base, and it counts only if the RSX block 4 bytes below that base is in the firmware's ROM
chain (KL_ROM_CHAIN) - which also tells that the fixed block is AMSDOS's. The workspace
variables are moved from their documented base (&A700) to the actual one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cpcxray.debugger.variable_tables import (
    AMSDOS_FIXED,
    AMSDOS_WORKSPACE,
    AMSDOS_WORKSPACE_BASE,
    OS_VARIABLES_BY_MODEL,
    VariableEntry,
)

ByteReader = Callable[[int], int]

AMSDOS_MEMORY_POOL = next(entry.address for entry in AMSDOS_FIXED
                          if entry.label == "AMSDOS_MEMORY_POOL")  # fmt: skip
RSX_BLOCK_BELOW_BASE = 4  # AMSDOS's RSX chaining block lies 4 bytes below its workspace base
MAX_CHAIN_BLOCKS = 32  # a ROM chain has at most one block per ROM (16), guards against loops


class VariableError(ValueError):
    """Unknown machine model for the variable tables."""


@dataclass(frozen=True)
class Variable:
    """A variable at its address on the running machine.

    Attributes:
        label: Label, e.g. TXT_CURSOR_ROW.
        address: First byte.
        size: Size in bytes.
        description: What it holds.
        owner: "firmware" or "amsdos".
        area: A pack's area (all its variables and the bytes between them), not a variable.
    """

    label: str
    address: int
    size: int
    description: str
    owner: str
    area: bool = False

    @property
    def last(self) -> int:
        """Address of the last byte."""
        return self.address + self.size - 1

    def describe(self) -> dict[str, object]:
        """The variable as a dict for the session (addresses as 4-digit hex)."""
        return {"label": self.label, "address": f"{self.address:04X}", "size": self.size,
                "last": f"{self.last:04X}", "owner": self.owner, "area": self.area,
                "description": self.description}  # fmt: skip


@dataclass(frozen=True)
class VariableHit:
    """An address found inside a variable.

    Attributes:
        variable: The variable.
        offset: Distance of the address from the variable's first byte.
    """

    variable: Variable
    offset: int

    @property
    def name(self) -> str:
        """`LABEL` at the first byte, else `LABEL+offset` (decimal)."""
        return self.variable.label + (f"+{self.offset}" if self.offset else "")


def _variables(entries: tuple[VariableEntry, ...], owner: str, shift: int = 0) -> list[Variable]:
    """Turn table entries into variables, moved by `shift` bytes."""
    return [Variable(entry.label, entry.address + shift, entry.size, entry.description, owner,
                     entry.area) for entry in entries]  # fmt: skip


def _read_word(read_byte: ByteReader, address: int) -> int:
    """Read a little-endian word."""
    return read_byte(address & 0xFFFF) | read_byte((address + 1) & 0xFFFF) << 8


class VariableMap:
    """The known variables of one machine model, resolved against its memory."""

    def __init__(self, model: str) -> None:
        """Create the map for a firmware model ("464", "664" or "6128").

        Raises:
            VariableError: For an unknown model.
        """
        if model not in OS_VARIABLES_BY_MODEL:
            raise VariableError(f"no variables for model {model!r}")
        self.model = model
        self.firmware = _variables(OS_VARIABLES_BY_MODEL[model], "firmware")
        self._rom_chain = next(v.address for v in self.firmware if v.label == "KL_ROM_CHAIN")

    def amsdos_base(self, read_byte: ByteReader) -> int | None:
        """Return AMSDOS's workspace base if AMSDOS is installed (see the module docstring)."""
        base = _read_word(read_byte, AMSDOS_MEMORY_POOL)
        rsx_block = (base - RSX_BLOCK_BELOW_BASE) & 0xFFFF
        block = _read_word(read_byte, self._rom_chain)
        for _ in range(MAX_CHAIN_BLOCKS):
            if block == 0:
                return None
            if block == rsx_block:
                return base
            block = _read_word(read_byte, block)
        return None

    def variables(self, read_byte: ByteReader) -> list[Variable]:
        """All variables that exist now: the firmware's, and AMSDOS's when it is installed."""
        base = self.amsdos_base(read_byte)
        if base is None:
            return list(self.firmware)
        return (self.firmware + _variables(AMSDOS_FIXED, "amsdos")
                + _variables(AMSDOS_WORKSPACE, "amsdos", base - AMSDOS_WORKSPACE_BASE))  # fmt: skip

    def lookup(self, address: int, read_byte: ByteReader) -> list[VariableHit]:
        """Return every variable containing `address`, the smallest first."""
        hits = [VariableHit(variable, address - variable.address)
                for variable in self.variables(read_byte)
                if variable.address <= address <= variable.last]  # fmt: skip
        return sorted(hits, key=lambda hit: hit.variable.size)

    def overlapping(self, first: int, last: int, read_byte: ByteReader) -> list[Variable]:
        """Return the variables with a byte in `first`-`last`, by address (outer ones first)."""
        found = [v for v in self.variables(read_byte) if v.address <= last and v.last >= first]
        return sorted(found, key=lambda v: (v.address, -v.size))

    def find(self, label: str, read_byte: ByteReader) -> Variable | None:
        """Return the variable with this label (case-insensitive), if it exists now."""
        wanted = label.upper()
        return next((v for v in self.variables(read_byte) if v.label == wanted), None)
