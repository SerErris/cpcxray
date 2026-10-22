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
stores. Label lookups by name fall back to a case-insensitive match.

Annotation file format (plain text, one entry per line, `#` starts a comment line):

    # [scope:]address  label  [; comment]
    BB5A        my_output       ; print char in A
    rom5:C000   rom_header
    8000        -               ; a comment only, no label

Addresses are hex (optionally with a leading & or #); `-` stands for "no label".
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
FILE_HEADER = "# cpcxray annotations\n# [scope:]address  label  [; comment]\n"


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
    """

    address: int
    label: str | None = None
    comment: str | None = None
    scope: str = GLOBAL
    source: str = "user"

    @property
    def key(self) -> tuple[str, int]:
        """(scope, address): at most one symbol per key."""
        return self.scope, self.address


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
        self._symbols: dict[tuple[str, int], Symbol] = {}
        self.firmware_model: str | None = None
        if preload_firmware:
            self.set_firmware(model)

    def set_firmware(self, model: str) -> None:
        """Replace the preloaded firmware names by those of another machine (D-26).

        The owner's symbols stay; where one sits at a firmware address it wins over the name.

        Raises:
            SymbolError: For an unknown model.
        """
        if model not in FIRMWARE_BY_MODEL:
            raise SymbolError(f"no firmware names for model {model!r}: 464, 664 or 6128")
        for key in [key for key, symbol in self._symbols.items() if symbol.source == "firmware"]:
            del self._symbols[key]
        for entry in FIRMWARE_BY_MODEL[model]:
            if (GLOBAL, entry.address) in self._symbols:
                continue
            comment = entry.name + (f" ({entry.note})" if entry.note else "")
            self._symbols[(GLOBAL, entry.address)] = Symbol(
                entry.address, entry.label, comment, GLOBAL, "firmware"
            )
        self.firmware_model = model

    # -- changes -----------------------------------------------------------------------------

    def add(
        self,
        address: int,
        label: str | None = None,
        comment: str | None = None,
        scope: str = GLOBAL,
    ) -> Symbol:
        """Add or replace the symbol at (scope, address).

        A label must be a valid identifier and unique among the global symbols and those of
        the same scope. A replaced firmware symbol keeps its label if no new one is given.

        Returns:
            The stored symbol.

        Raises:
            SymbolError: On an invalid label, scope or address, a duplicate label, or if
                neither label nor comment is given.
        """
        validate_scope(scope)
        if not 0 <= address <= 0xFFFF:
            raise SymbolError(f"address {address} out of range")
        if label is None and comment is None:
            raise SymbolError("a symbol needs a label, a comment or both")
        existing = self._symbols.get((scope, address))
        if label is None and existing is not None:
            label = existing.label
        if comment is None and existing is not None:
            comment = existing.comment
        if label is not None:
            if not LABEL_PATTERN.match(label):
                raise SymbolError(f"invalid label {label!r}")
            other = self.find(label, scope)
            if other is not None and other.key != (scope, address):
                raise SymbolError(f"label {label!r} already used at {other.address:04X}")
        symbol = Symbol(address, label, comment, scope, "user")
        self._symbols[symbol.key] = symbol
        return symbol

    def remove(self, address: int, scope: str = GLOBAL) -> Symbol:
        """Remove the symbol at (scope, address) and return it.

        Raises:
            SymbolError: If there is none.
        """
        try:
            return self._symbols.pop((validate_scope(scope), address))
        except KeyError:
            raise SymbolError(f"no symbol at {scope + ':' if scope else ''}{address:04X}") from None

    # -- lookups -----------------------------------------------------------------------------

    def at(self, address: int, scope: str = GLOBAL) -> Symbol | None:
        """Return the symbol for `address` in `scope`, falling back to the global one."""
        return self._symbols.get((scope, address)) or self._symbols.get((GLOBAL, address))

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
        candidates = [s for s in self._symbols.values() if s.scope in (scope, GLOBAL) and s.label]
        for symbol in candidates:
            if symbol.label == label:
                return symbol
        folded = [s for s in candidates if (s.label or "").lower() == label.lower()]
        return folded[0] if len(folded) == 1 else None

    def symbols(
        self, text: str = "", scope: str | None = None, source: str | None = None
    ) -> list[Symbol]:
        """List symbols sorted by scope and address.

        Args:
            text: Only symbols whose label or comment contains this text (case-insensitive).
            scope: Only this scope, or None for all.
            source: Only "firmware" or "user", or None for both.
        """
        needle = text.lower()
        result = [
            symbol
            for symbol in self._symbols.values()
            if (scope is None or symbol.scope == scope)
            and (source is None or symbol.source == source)
            and (
                not needle
                or needle in (symbol.label or "").lower()
                or needle in (symbol.comment or "").lower()
            )
        ]
        return sorted(result, key=lambda symbol: symbol.key)

    # -- annotation file ---------------------------------------------------------------------

    def load(self, path: str | Path) -> int:
        """Merge the user symbols from an annotation file into the table.

        Returns:
            The number of entries read.

        Raises:
            SymbolError: On a malformed line (the message gives file and line number).
        """
        count = 0
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            try:
                symbol = parse_annotation_line(line)
            except SymbolError as error:
                raise SymbolError(f"{path}:{number}: {error}") from None
            if symbol is not None:
                self.add(symbol.address, symbol.label, symbol.comment, symbol.scope)
                count += 1
        return count

    def save(self, path: str | Path) -> int:
        """Write all user symbols to an annotation file; returns the number written."""
        user = self.symbols(source="user")
        text = FILE_HEADER + "".join(format_annotation_line(s) for s in user)
        Path(path).write_text(text, encoding="utf-8")
        return len(user)


def parse_annotation_line(line: str) -> Symbol | None:
    """Parse one annotation file line; None for blank and `#` comment lines.

    Raises:
        SymbolError: If the line is malformed.
    """
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    entry, _, comment = text.partition(";")
    fields = entry.split()
    if len(fields) not in (1, 2):
        raise SymbolError(f"expected '[scope:]address label [; comment]', got {line!r}")
    location = fields[0]
    scope, _, address_text = location.rpartition(":")
    label = fields[1] if len(fields) == 2 and fields[1] != "-" else None
    comment_text = comment.strip() or None
    if label is None and comment_text is None:
        raise SymbolError(f"entry without label and comment: {line!r}")
    return Symbol(parse_address(address_text), label, comment_text, validate_scope(scope))


def format_annotation_line(symbol: Symbol) -> str:
    """Format a symbol as an annotation file line (with trailing newline)."""
    location = f"{symbol.scope}:{symbol.address:04X}" if symbol.scope else f"{symbol.address:04X}"
    line = f"{location:<11} {symbol.label or '-':<20}"
    if symbol.comment:
        line += f" ; {symbol.comment}"
    return line.rstrip() + "\n"


def merge(tables: Iterable[SymbolTable]) -> SymbolTable:
    """Return a new table with the user symbols of all `tables` (later ones win)."""
    tables = list(tables)
    model = next((table.firmware_model for table in tables if table.firmware_model), "464")
    merged = SymbolTable(preload_firmware=True, model=model)
    for table in tables:
        for symbol in table.symbols(source="user"):
            merged.add(symbol.address, symbol.label, symbol.comment, symbol.scope)
    return merged
