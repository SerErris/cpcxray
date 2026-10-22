"""Symbols and comments per address, and the annotation file (design.md §6.13, D-26, FR-DBG-09).

A symbol gives an address a label, a comment, or both. Symbols have a *scope*:

* "" (global): RAM addresses and firmware jumpblocks, valid whatever is paged in;
* "lower": the lower (OS) ROM;
* "rom0" … "rom15": an upper ROM slot, so ROM labels do not leak into other ROMs or RAM.

A lookup for an address in a scope first checks that scope, then the global symbols. Which
scope applies to an address at run time (ROM paged in or not) is decided by the caller; the
memory map provides it from phase 4.

The firmware jumpblock names of the machine are preloaded (source "firmware") as upper-case
labels (TXT_OUTPUT, JP_HL): the 464, 664 and 6128 have different sets
(debugger/firmware_symbols.py); `set_firmware` switches them when the machine changes.
Everything the owner or the tools add has source "user" and is what the annotation file
stores. The two are layers: the owner's label or comment at a firmware address lies over the
firmware name and does not replace it. A lookup shows the owner's label if there is one (else
the firmware label) and both comments ("TXT OUTPUT · hooked by my program"); removing the
owner's entry brings the plain firmware name back. Label lookups by name fall back to a
case-insensitive match and find firmware labels under an owner's label too.

Symbol types (task 5g, owner 2026-10-22): firmware names are "FW"; the owner's labels are
"sub" (a subroutine), "jump" (a jump target inside code) or "data" (a table, a string); a
comment without a label has no type. The owner's *variables* (`OwnVariable`, type "var") are
named byte ranges with a size: at an absolute address, or at an offset in the data area (IY)
of a background ROM, which the firmware assigns at boot - they are resolved like the known
OS/AMSDOS variables (debugger/variables.py) and used wherever those are.

Annotation file format (plain text, one entry per line, `#` starts a comment line):

    # [sub|jump|data] [scope:]address  label  [; comment]
    # var address|romN+offset  label  size  [; comment]
    BB5A        my_output       ; print char in A (no type: "sub")
    sub rom5:C4D8   read_sector     ; reads one sector
    data C015       disk_messages
    8000        -               ; a comment only, no label
    var rom5+0012   vd_drive_table  16 ; one entry per drive

Addresses are hex (optionally with a leading & or #); `-` stands for "no label". Files of
earlier versions (no type words, no variables) load unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cpcxray.debugger.firmware_symbols import FIRMWARE_BY_MODEL

GLOBAL = ""
LABEL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
SCOPE_PATTERN = re.compile(r"^(lower|rom([0-9]|1[0-5]))$")
FILE_HEADER = ("# cpcxray annotations\n# [sub|jump|data] [scope:]address  label  [; comment]\n"
               "# var address|romN+offset  label  size  [; comment]\n")  # fmt: skip
OWNER_TYPES = ("sub", "jump", "data")  # the owner's labels; "var": OwnVariable
VARIABLE_PATTERN = re.compile(r"^(?:rom(\d{1,2})\+)?([&#]?[0-9A-Fa-f]{1,4})$")


class SymbolError(ValueError):
    """Invalid symbol (bad label, scope or address, or a duplicate label)."""


@dataclass(frozen=True)
class Symbol:
    """A label and/or comment at an address.

    Attributes:
        address: 16-bit address.
        label: Label, or None for a comment-only entry.
        comment: Comment, or None.
        scope: "" (global), "lower" or "romN".
        source: "firmware" (preloaded) or "user" (saved in the annotation file).
        type: "FW" (firmware), "sub", "jump" or "data" (the owner's labels), or None (a
            comment only).
    """

    address: int
    label: str | None = None
    comment: str | None = None
    scope: str = GLOBAL
    source: str = "user"
    type: str | None = None

    @property
    def key(self) -> tuple[str, int]:
        """(scope, address): at most one symbol per key."""
        return self.scope, self.address


@dataclass(frozen=True)
class OwnVariable:
    """A variable of the owner's (type "var"): a named byte range in RAM.

    Attributes:
        label: Label, unique among all labels.
        size: Size in bytes (1-65536).
        address: The absolute address, or with `rom` the offset in that ROM's data area.
        rom: None for an absolute address; else the background ROM whose data area (IY, from
            KL_ROM_IY_TABLE) the offset counts from - it moves with the ROMs installed.
        comment: What it holds, or None.
    """

    label: str
    size: int
    address: int
    rom: int | None = None
    comment: str | None = None

    @property
    def base_text(self) -> str:
        """Where it is, as in the annotation file: "A612" or "rom5+0012"."""
        location = f"{self.address:04X}"
        return location if self.rom is None else f"rom{self.rom}+{location}"


def validate_scope(scope: str) -> str:
    """Return `scope` if valid ("", "lower", "rom0".."rom15"), else raise SymbolError."""
    if scope != GLOBAL and not SCOPE_PATTERN.match(scope):
        raise SymbolError(f"invalid scope {scope!r} (use '', 'lower' or 'rom0'..'rom15')")
    return scope


def parse_address(text: str) -> int:
    """Parse a hex address such as BB5A, &BB5A or #BB5A.

    Raises:
        SymbolError: If it is not a 16-bit hex number.
    """
    digits = text.strip().lstrip("&#")
    if digits.lower().startswith("0x"):
        digits = digits[2:]
    try:
        address = int(digits, 16)
    except ValueError:
        raise SymbolError(f"invalid address {text!r}") from None
    if not 0 <= address <= 0xFFFF:
        raise SymbolError(f"address {text!r} out of range")
    return address


class SymbolTable:
    """All symbols, with lookups by address and by label."""

    def __init__(self, preload_firmware: bool = True, model: str = "464") -> None:
        """Create the table, optionally with the firmware jumpblock names of a machine.

        Args:
            preload_firmware: Preload the firmware names.
            model: The machine whose firmware names are preloaded: "464", "664" or "6128".
        """
        self._symbols: dict[tuple[str, int], Symbol] = {}  # the owner's layer
        self._firmware: dict[int, Symbol] = {}  # the firmware layer, all global
        self._variables: dict[str, OwnVariable] = {}  # the owner's variables by label
        self.firmware_model: str | None = None
        self.modified = False  # the owner's symbols or variables changed since load / save
        if preload_firmware:
            self.set_firmware(model)

    def set_firmware(self, model: str) -> None:
        """Replace the preloaded firmware names by those of another machine (D-26).

        The owner's symbols stay; where one sits at a firmware address it lies over the name.

        Raises:
            SymbolError: For an unknown model.
        """
        if model not in FIRMWARE_BY_MODEL:
            raise SymbolError(f"no firmware names for model {model!r}: 464, 664 or 6128")
        self._firmware = {}
        for entry in FIRMWARE_BY_MODEL[model]:
            comment = entry.name + (f" ({entry.note})" if entry.note else "")
            self._firmware[entry.address] = Symbol(
                entry.address, entry.label, comment, GLOBAL, "firmware", "FW"
            )
        self.firmware_model = model

    # -- changes -----------------------------------------------------------------------------

    def add(
        self,
        address: int,
        label: str | None = None,
        comment: str | None = None,
        scope: str = GLOBAL,
        type: str | None = None,  # pylint: disable=redefined-builtin  # the MCP parameter name
    ) -> Symbol:
        """Add or change the owner's symbol at (scope, address).

        A label must be a valid identifier and unique among the global symbols, those of the
        same scope and the owner's variables. A part not given keeps the owner's earlier one;
        a label's type is "sub", "jump" or "data" (default: the earlier one, else "sub"). At a
        firmware address only the owner's layer changes; a label or comment equal to the
        firmware's is not stored again (annotation files from before the layering may repeat
        it).

        Returns:
            The symbol as lookups now see it (both layers, see `at`).

        Raises:
            SymbolError: On an invalid label, scope, type or address, a duplicate label, or if
                neither label nor comment is given.
        """
        validate_scope(scope)
        if type is not None and type not in OWNER_TYPES:
            raise SymbolError(f"invalid type {type!r} (use {', '.join(OWNER_TYPES)})")
        if not 0 <= address <= 0xFFFF:
            raise SymbolError(f"address {address} out of range")
        if label is None and comment is None:
            raise SymbolError("a symbol needs a label, a comment or both")
        existing = self._symbols.get((scope, address))
        if label is None and existing is not None:
            label = existing.label
        if comment is None and existing is not None:
            comment = existing.comment
        if type is None and existing is not None:
            type = existing.type
        if label is not None:
            self._check_label(label, address, scope)
        firmware = self._firmware.get(address)
        if firmware is not None:
            label = None if label == firmware.label else label
            comment = None if comment == firmware.comment else comment
        if label is None and comment is None:  # nothing of the owner's left
            self._symbols.pop((scope, address), None)
        else:
            type = (type or "sub") if label is not None else None
            self._symbols[(scope, address)] = Symbol(address, label, comment, scope, "user", type)
        self.modified = True
        found = self.at(address, scope)
        assert found is not None  # the firmware symbol or the owner's just stored
        return found

    def remove(self, address: int, scope: str = GLOBAL) -> Symbol:
        """Remove the owner's symbol at (scope, address) and return it.

        A firmware name underneath stays; firmware names themselves cannot be removed.

        Raises:
            SymbolError: If the owner has no symbol there.
        """
        try:
            removed = self._symbols.pop((validate_scope(scope), address))
        except KeyError:
            where = f"{scope + ':' if scope else ''}{address:04X}"
            if address in self._firmware:
                message = f"only the firmware name at {where}; it cannot be removed"
            else:
                message = f"no symbol at {where}"
            raise SymbolError(message) from None
        self.modified = True
        return removed

    def _check_label(self, label: str, address: int | None, scope: str = GLOBAL) -> None:
        """Raise SymbolError unless `label` is valid and free (a symbol elsewhere, a variable).

        Args:
            label: The label.
            address: The symbol's address (its own label there is free); None for a variable.
            scope: The symbol's scope.
        """
        if not LABEL_PATTERN.match(label):
            raise SymbolError(f"invalid label {label!r}")
        other = self.find(label, scope)
        if other is not None and other.address != address:
            raise SymbolError(f"label {label!r} already used at {other.address:04X}")
        if address is not None and label.lower() in self._variables:
            raise SymbolError(f"label {label!r} already used by a variable")

    # -- the owner's variables ---------------------------------------------------------------

    def add_variable(self, variable: OwnVariable, replaces: str | None = None) -> OwnVariable:
        """Add or change one of the owner's variables (type "var").

        Args:
            variable: The variable; its label must be free (but for the one it replaces).
            replaces: Label of the variable it replaces (a rename or change), or None.

        Raises:
            SymbolError: On an invalid label, size, address or ROM, or a label in use.
        """
        if not 1 <= variable.size <= 0x10000:
            raise SymbolError(f"invalid size {variable.size} (1-65536)")
        if not 0 <= variable.address <= 0xFFFF:
            raise SymbolError(f"address {variable.address} out of range")
        if variable.rom is not None and not 0 <= variable.rom <= 15:
            raise SymbolError(f"invalid ROM {variable.rom} (0-15)")
        self._check_label(variable.label, None)
        key = variable.label.lower()
        if key in self._variables and key != (replaces or "").lower():
            raise SymbolError(f"label {variable.label!r} already used by a variable")
        if replaces is not None:
            self._variables.pop(replaces.lower(), None)
        self._variables[key] = variable
        self.modified = True
        return variable

    def remove_variable(self, label: str) -> OwnVariable:
        """Remove one of the owner's variables and return it.

        Raises:
            SymbolError: If there is no such variable.
        """
        try:
            removed = self._variables.pop(label.lower())
        except KeyError:
            raise SymbolError(f"no variable {label!r} of yours") from None
        self.modified = True
        return removed

    def variable(self, label: str) -> OwnVariable | None:
        """Return the owner's variable with this label (case-insensitive), or None."""
        return self._variables.get(label.lower())

    def own_variables(self) -> list[OwnVariable]:
        """The owner's variables, sorted by label."""
        return sorted(self._variables.values(), key=lambda variable: variable.label.lower())

    # -- lookups -----------------------------------------------------------------------------

    def at(self, address: int, scope: str = GLOBAL) -> Symbol | None:
        """Return the symbol for `address` in `scope`, falling back to the global one.

        The owner's symbol (in `scope`, else global) lies over the firmware name: its label
        wins, the comments are joined firmware first ("TXT OUTPUT · my comment"), and the
        result has source "user". Without an owner's symbol the firmware one is returned.
        """
        owner = self.owner_at(address, scope)
        firmware = self._firmware.get(address)
        if owner is None or firmware is None:
            return owner or firmware
        comments = [c for c in (firmware.comment, owner.comment) if c]
        return Symbol(
            address, owner.label or firmware.label, " · ".join(comments) or None, owner.scope,
            type=owner.type if owner.label else firmware.type,
        )  # fmt: skip

    def owner_at(self, address: int, scope: str = GLOBAL) -> Symbol | None:
        """Return only the owner's symbol for `address` (in `scope`, else global), or None."""
        return self._symbols.get((scope, address)) or self._symbols.get((GLOBAL, address))

    def firmware_at(self, address: int) -> Symbol | None:
        """Return only the firmware symbol at `address`, or None."""
        return self._firmware.get(address)

    def label_at(self, address: int, scope: str = GLOBAL) -> str | None:
        """Return the label for `address` (see `at`), or None."""
        symbol = self.at(address, scope)
        return symbol.label if symbol is not None else None

    def comment_at(self, address: int, scope: str = GLOBAL) -> str | None:
        """Return the comment for `address` (see `at`), or None."""
        symbol = self.at(address, scope)
        return symbol.comment if symbol is not None else None

    def find(self, label: str, scope: str = GLOBAL) -> Symbol | None:
        """Return the symbol with this label in `scope` or globally, or None.

        An exact match wins; otherwise a label that differs only in case is accepted if it is
        unique, so "txt_output" still finds the firmware's TXT_OUTPUT.
        """
        candidates = [
            s
            for s in [*self._symbols.values(), *self._firmware.values()]
            if s.scope in (scope, GLOBAL) and s.label
        ]
        for symbol in candidates:
            if symbol.label == label:
                return self.at(symbol.address, symbol.scope)
        folded = [s for s in candidates if (s.label or "").lower() == label.lower()]
        return self.at(folded[0].address, folded[0].scope) if len(folded) == 1 else None

    def symbols(
        self, text: str = "", scope: str | None = None, source: str | None = None
    ) -> list[Symbol]:
        """List symbols sorted by scope and address, as lookups see them (see `at`).

        A firmware name with the owner's symbol over it is listed once, with source "user".

        Args:
            text: Only symbols whose label or comment contains this text (case-insensitive).
            scope: Only this scope, or None for all.
            source: Only "firmware" or "user", or None for both.
        """
        needle = text.lower()
        layered = [self.at(key[1], key[0]) for key in self._symbols]
        layered += [s for a, s in self._firmware.items() if (GLOBAL, a) not in self._symbols]
        result = [
            symbol
            for symbol in layered
            if symbol is not None
            and (scope is None or symbol.scope == scope)
            and (source is None or symbol.source == source)
            and (
                not needle
                or needle in (symbol.label or "").lower()
                or needle in (symbol.comment or "").lower()
            )
        ]
        return sorted(result, key=lambda symbol: symbol.key)

    def owner_symbols(self) -> list[Symbol]:
        """The owner's layer only, sorted by scope and address (what the annotation file holds)."""
        return sorted(self._symbols.values(), key=lambda symbol: symbol.key)

    # -- annotation file ---------------------------------------------------------------------

    def load(self, path: str | Path) -> int:
        """Merge the user symbols from an annotation file into the table.

        Returns:
            The number of entries read.

        Raises:
            SymbolError: On a malformed line (the message gives file and line number).
        """
        was_modified = self.modified
        count = 0
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            try:
                entry = parse_annotation_line(line)
                if entry is not None:
                    self._merge_entry(entry)
            except SymbolError as error:
                raise SymbolError(f"{path}:{number}: {error}") from None
            count += entry is not None
        self.modified = was_modified  # what the file holds is not an unsaved change
        return count

    def _merge_entry(self, entry: Symbol | OwnVariable) -> None:
        """Add a symbol or variable read from a file or another table (it replaces its own)."""
        if isinstance(entry, OwnVariable):
            self.add_variable(entry, replaces=entry.label)
        else:
            self.add(entry.address, entry.label, entry.comment, entry.scope, entry.type)

    def save(self, path: str | Path) -> int:
        """Write the owner's symbols and variables to an annotation file.

        Returns:
            The number of entries written.
        """
        user = self.owner_symbols()
        variables = self.own_variables()
        text = (FILE_HEADER + "".join(format_annotation_line(s) for s in user)
                + "".join(format_variable_line(v) for v in variables))  # fmt: skip
        Path(path).write_text(text, encoding="utf-8")
        self.modified = False
        return len(user) + len(variables)


def parse_annotation_line(line: str) -> Symbol | OwnVariable | None:
    """Parse one annotation file line; None for blank and `#` comment lines.

    A line without a type word is a "sub" label (or a comment only), as in older files.

    Raises:
        SymbolError: If the line is malformed.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    entry, _, comment = text.partition(";")
    fields = entry.split()
    comment_text = comment.strip() or None
    if fields and fields[0] == "var":
        return _parse_variable(fields[1:], comment_text, line)
    symbol_type = fields.pop(0) if fields and fields[0] in OWNER_TYPES else None
    if len(fields) not in (1, 2):
        raise SymbolError(f"expected '[type] [scope:]address label [; comment]', got {line!r}")
    scope, _, address_text = fields[0].rpartition(":")
    label = fields[1] if len(fields) == 2 and fields[1] != "-" else None
    if label is None and comment_text is None:
        raise SymbolError(f"entry without label and comment: {line!r}")
    if label is not None:
        symbol_type = symbol_type or "sub"
    return Symbol(parse_address(address_text), label, comment_text, validate_scope(scope),
                  type=symbol_type)  # fmt: skip


def _parse_variable(fields: list[str], comment: str | None, line: str) -> OwnVariable:
    """The fields after "var": `address|romN+offset label size`."""
    if len(fields) != 3:
        raise SymbolError(f"expected 'var address|romN+offset label size [; comment]', "
                          f"got {line!r}")  # fmt: skip
    location, label, size_text = fields
    rom, address = parse_variable_base(location)
    if not LABEL_PATTERN.match(label):
        raise SymbolError(f"invalid label {label!r}")
    try:
        size = int(size_text, 0)
    except ValueError:
        raise SymbolError(f"invalid size {size_text!r}") from None
    return OwnVariable(label, size, address, rom, comment)


def parse_variable_base(text: str) -> tuple[int | None, int]:
    """Parse a variable's place: "A612" / "&A612" (absolute) or "rom5+0012" (ROM data area).

    Returns:
        (ROM number or None, address or offset).

    Raises:
        SymbolError: If it is neither.
    """
    match = VARIABLE_PATTERN.match(text.strip())
    if match is None:
        raise SymbolError(f"invalid variable address {text!r} (use A612 or rom5+0012)")
    rom = int(match.group(1)) if match.group(1) is not None else None
    if rom is not None and rom > 15:
        raise SymbolError(f"invalid ROM {rom} (0-15)")
    return rom, parse_address(match.group(2))


def format_annotation_line(symbol: Symbol) -> str:
    """Format a symbol as an annotation file line (with trailing newline)."""
    location = f"{symbol.scope}:{symbol.address:04X}" if symbol.scope else f"{symbol.address:04X}"
    line = f"{symbol.type or '':<4} {location:<11} {symbol.label or '-':<20}"
    if symbol.comment:
        line += f" ; {symbol.comment}"
    return line.strip() + "\n"


def format_variable_line(variable: OwnVariable) -> str:
    """Format one of the owner's variables as an annotation file line (with trailing newline)."""
    line = f"var  {variable.base_text:<11} {variable.label:<20} {variable.size}"
    if variable.comment:
        line += f" ; {variable.comment}"
    return line + "\n"


def merge(tables: Iterable[SymbolTable]) -> SymbolTable:
    """Return a new table with the user symbols and variables of all `tables` (later ones win)."""
    tables = list(tables)
    model = next((table.firmware_model for table in tables if table.firmware_model), "464")
    merged = SymbolTable(preload_firmware=True, model=model)
    for table in tables:
        for entry in [*table.owner_symbols(), *table.own_variables()]:
            merged._merge_entry(entry)  # pylint: disable=protected-access  # same class
    merged.modified = False
    return merged
