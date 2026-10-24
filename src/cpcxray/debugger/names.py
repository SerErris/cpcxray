"""Names of addresses by the context of the code that uses them (task 10, D-43, D-44).

One RAM address can mean different things in code that never runs at the same time (VDOS and
CP/M in the same RAM): the owner puts their variables and RAM labels in groups and gives code
ranges a context (a group, debugger/symbols.py). Wherever a name for an address is needed -
an operand, the variable an access touched, the label of a RAM address - `ranked_names`
orders the candidates:

1. the firmware name at the address (jumpblock, restart): fixed by the machine, it always wins;
2. the owner's label of the address's own ROM scope ("lower", "romN"; not grouped);
3. the owner's global labels and all variables containing the address, by rank - (a) the
   group of the code's context, (b) built-in ("firmware", "amsdos") and ungrouped, (c) the
   other groups in their order - and within a rank labels before variables, then the smallest
   variable (`LABEL+n` inside one).

Without groups or without a context this is the order cpcxray used before groups existed.
`context_group` finds the context of code: the narrowest range of its scope, else the whole
scope; RAM code copied from a ROM (provenance) takes its ROM origin's context.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from cpcxray.debugger.symbols import FIRMWARE_GROUP, GLOBAL, SymbolTable

if TYPE_CHECKING:
    from cpcxray.debugger.variables import Variable


class Origin(Protocol):  # pylint: disable=too-few-public-methods  # the provenance tag's fields
    """Where RAM code was copied from (analysis/provenance.py `RamTag`)."""

    target: str
    offset: int


@dataclass(frozen=True)
class Name:
    """A name for an address.

    Attributes:
        text: The label, or `LABEL` / `LABEL+n` inside a variable.
        type: "FW", "sub", "jump", "data" (labels), "var" (the owner's variable) or "VAR" (a
            known OS/AMSDOS one) - the colour in the GUI.
        group: Its group, or None (ungrouped).
        variable: The variable for a variable name, else None.
        offset: The address's distance from the variable's first byte (0 for labels).
    """

    text: str
    type: str
    group: str | None
    variable: Variable | None = None
    offset: int = 0


def rom_base(scope: str) -> int:
    """The CPU address of a ROM scope's first byte: &0000 (lower), &C000 (upper)."""
    return 0x0000 if scope == "lower" else 0xC000


def context_group(
    symbols: SymbolTable, scope: str, address: int, origin: Origin | None = None
) -> str | None:
    """The group of the context of code at (scope, address), or None.

    Args:
        symbols: The table with the contexts.
        scope: Where the code runs: "lower", "romN" or "" (RAM).
        address: The code's address.
        origin: For RAM code: the ROM it was copied from (provenance), if known - its
            context counts, else the RAM's own.
    """
    if not scope and origin is not None:
        scope, address = origin.target, rom_base(origin.target) + origin.offset
    context = symbols.context_at(scope, address)
    return context.group if context is not None else None


def ranked_names(
    symbols: SymbolTable,
    variables: list[Variable],
    address: int,
    scope: str = GLOBAL,
    prefer: str | None = None,
) -> list[Name]:
    """Every name for `address`, the best first (see the module docstring).

    Args:
        symbols: Firmware and owner's symbols.
        variables: The variables that exist now (`VariableMap.variables`).
        address: The address named.
        scope: The scope the address is in now (a ROM's own labels), "" for RAM.
        prefer: The group of the code's context, or None.
    """
    names: list[Name] = []
    firmware = symbols.firmware_at(address)
    if firmware is not None and firmware.label:
        names.append(Name(firmware.label, "FW", FIRMWARE_GROUP))
    own_scope = symbols.scope_symbol(address, scope) if scope != GLOBAL else None
    if own_scope is not None and own_scope.label:
        names.append(Name(own_scope.label, own_scope.type or "sub", None))
    ranked: list[tuple[tuple[int, int], int, int, Name]] = []
    for symbol in symbols.owners_at(address):
        if symbol.label:
            name = Name(symbol.label, symbol.type or "sub", symbol.group)
            ranked.append((symbols.rank(symbol.group, prefer), 0, 0, name))
    for variable in variables:
        if variable.address <= address <= variable.last:
            offset = address - variable.address
            text = variable.label + (f"+{offset}" if offset else "")
            kind = "var" if variable.owner == "owner" else "VAR"
            name = Name(text, kind, variable.group, variable, offset)
            ranked.append((symbols.rank(variable.group, prefer), 1, variable.size, name))
    ranked.sort(key=lambda entry: entry[:3])
    return names + [entry[3] for entry in ranked]


def best_name(
    symbols: SymbolTable,
    variables: list[Variable],
    address: int,
    scope: str = GLOBAL,
    prefer: str | None = None,
) -> Name | None:
    """The name `ranked_names` puts first, or None."""
    names = ranked_names(symbols, variables, address, scope, prefer)
    return names[0] if names else None
