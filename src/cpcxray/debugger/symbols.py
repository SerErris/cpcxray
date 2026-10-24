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

Comment blocks (task 9, owner 2026-10-23): besides the comment at the end of its line, an
address can have a block of comment lines *before* it (a routine header) and one *after* it (a
longer explanation between two instructions). Blocks are the owner's only (`before`, `after`:
text with line breaks); the source generator writes them as `;` lines around the instruction.

Operand overrides (owner 2026-10-24): the owner decides for the word operand of the instruction
at an address whether it is a number ("value": always hex, `ld hl,#0010` and not
`ld hl,SIDE_CALL`) or an address ("address": named, even where the judgement of `ld rr,nn` says
number). They win over that judgement in the disassembly, the trace and the generated source.

Annotation file format (plain text, one entry per line, `#` starts a comment line):

    # [sub|jump|data] [scope:]address  label  [; comment]
    # var address|romN+offset  label  size  [; comment]
    # before|after [scope:]address  ; one line of a comment block
    # value|address [scope:]address  [; comment]   the operand of the instruction there
    BB5A        my_output       ; print char in A (no type: "sub")
    before rom5:C4D8 ; read_sector: reads one sector into the buffer
    before rom5:C4D8 ;   in:  D = track, E = sector
    sub rom5:C4D8   read_sector     ; reads one sector
    after rom5:C4D8  ; the FDC is busy until the result phase
    data C015       disk_messages
    8000        -               ; a comment only, no label
    var rom5+0012   vd_drive_table  16 ; one entry per drive
    value rom5:C123  ; the 16 is a sector count, not RST 2

Addresses are hex (optionally with a leading & or #); `-` stands for "no label". A block line
keeps the text after "; " as it is (indentation too; `;` alone is an empty line); the lines of
one block are read in file order. Files of earlier versions (no type words, no variables, no
blocks) load unchanged.
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
               "# var address|romN+offset  label  size  [; comment]\n"
               "# before|after [scope:]address  ; one line of a comment block\n"
               "# value|address [scope:]address  [; comment]  (the operand of the instruction)\n"
               )  # fmt: skip
OWNER_TYPES = ("sub", "jump", "data")  # the owner's labels; "var": OwnVariable
BLOCK_POSITIONS = ("before", "after")  # comment blocks around a line (task 9)
OPERAND_ROLES = ("value", "address")  # the owner's operand overrides ("auto": none)
VARIABLE_PATTERN = re.compile(r"^(?:rom(\d{1,2})\+)?([&#]?[0-9A-Fa-f]{1,4})$")


class SymbolError(ValueError):
    """Invalid symbol (bad label, scope or address, or a duplicate label)."""


@dataclass(frozen=True)
class Symbol:  # pylint: disable=too-many-instance-attributes  # the parts of one entry
    """A label and/or comments at an address.

    Attributes:
        address: 16-bit address.
        label: Label, or None for a comment-only entry.
        comment: Comment at the end of the line, or None.
        scope: "" (global), "lower" or "romN".
        source: "firmware" (preloaded) or "user" (saved in the annotation file).
        type: "FW" (firmware), "sub", "jump" or "data" (the owner's labels), or None (a
            comment only).
        before: Comment block before the line (one line per text line), or None.
        after: Comment block after the line, or None.
    """

    address: int
    label: str | None = None
    comment: str | None = None
    scope: str = GLOBAL
    source: str = "user"
    type: str | None = None
    before: str | None = None
    after: str | None = None

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


@dataclass(frozen=True)
class BlockLine:
    """One line of a comment block in the annotation file (`before|after [scope:]address ; …`).

    Attributes:
        address: 16-bit address.
        scope: "" (global), "lower" or "romN".
        position: "before" or "after" the line at the address.
        text: The line's text ("" for an empty line).
    """

    address: int
    scope: str
    position: str
    text: str


@dataclass(frozen=True)
class OperandOverride:
    """The owner's decision about the word operand of the instruction at an address.

    Attributes:
        address: 16-bit address of the instruction.
        scope: "" (global), "lower" or "romN".
        role: "value" (a number: always hex) or "address" (named).
        comment: Why, or None.
    """

    address: int
    scope: str
    role: str
    comment: str | None = None


def _block_text(text: str | None) -> str | None:
    """A comment block as stored: lines without trailing blanks, no empty lines at the end.

    None stays None (nothing given); a block without any text becomes None (removed).
    """
    if text is None:
        return None
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) or None


def block_lines(symbol: Symbol | None, position: str) -> list[str]:
    """The lines of a symbol's comment block "before" / "after" its line (task 9), or []."""
    block = getattr(symbol, position) if symbol is not None else None
    return block.split("\n") if block else []


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
        self._operands: dict[tuple[str, int], OperandOverride] = {}  # by (scope, address)
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

    def add(  # pylint: disable=too-many-arguments  # the parts of a symbol
        self,
        address: int,
        label: str | None = None,
        comment: str | None = None,
        scope: str = GLOBAL,
        type: str | None = None,  # pylint: disable=redefined-builtin  # the MCP parameter name
        *,
        before: str | None = None,
        after: str | None = None,
    ) -> Symbol:
        """Add or change the owner's symbol at (scope, address).

        A label must be a valid identifier and unique among the global symbols, those of the
        same scope and the owner's variables. A part not given (None) keeps the owner's
        earlier one; an empty comment block ("") removes that block. A label's type is "sub",
        "jump" or "data" (default: the earlier one, else "sub"). At a firmware address only
        the owner's layer changes; a label or comment equal to the firmware's is not stored
        again (annotation files from before the layering may repeat it).

        Args:
            address: The address.
            label: The label, or None to keep the earlier one.
            comment: The comment at the end of the line, or None to keep the earlier one.
            scope: "" (global), "lower" or "romN".
            type: "sub", "jump" or "data" for a label.
            before: The comment block before the line (several lines); "" removes it.
            after: The comment block after the line; "" removes it.

        Returns:
            The symbol as lookups now see it (both layers, see `at`); an empty one when
            removing a block left nothing at the address.

        Raises:
            SymbolError: On an invalid label, scope, type or address, a duplicate label, or if
                no part at all is given.
        """
        validate_scope(scope)
        if type is not None and type not in OWNER_TYPES:
            raise SymbolError(f"invalid type {type!r} (use {', '.join(OWNER_TYPES)})")
        if not 0 <= address <= 0xFFFF:
            raise SymbolError(f"address {address} out of range")
        if label is None and comment is None and before is None and after is None:
            raise SymbolError("a symbol needs a label, a comment or a comment block")
        existing = self._symbols.get((scope, address))
        if existing is not None:
            label = existing.label if label is None else label
            comment = existing.comment if comment is None else comment
            type = existing.type if type is None else type
            before = existing.before if before is None else before
            after = existing.after if after is None else after
        before, after = _block_text(before), _block_text(after)
        if label is not None:
            self._check_label(label, address, scope)
        firmware = self._firmware.get(address)
        if firmware is not None:
            label = None if label == firmware.label else label
            comment = None if comment == firmware.comment else comment
        if label is None and comment is None and before is None and after is None:
            self._symbols.pop((scope, address), None)  # nothing of the owner's left
        else:
            type = (type or "sub") if label is not None else None
            self._symbols[(scope, address)] = Symbol(
                address, label, comment, scope, "user", type, before, after
            )
        self.modified = True
        found = self.at(address, scope)
        return found if found is not None else Symbol(address, scope=scope)  # nothing left

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

    # -- the owner's operand overrides --------------------------------------------------------

    def set_operand(
        self, address: int, role: str, scope: str = GLOBAL, comment: str | None = None
    ) -> OperandOverride | None:
        """Decide the operand of the instruction at (scope, address).

        Args:
            address: The instruction's address.
            role: "value" (a number), "address" (named) or "auto" (remove the decision: the
                judgement applies again).
            scope: "" (global), "lower" or "romN".
            comment: Why, or None.

        Returns:
            The override, or None after "auto".

        Raises:
            SymbolError: On an invalid role, scope or address.
        """
        validate_scope(scope)
        if not 0 <= address <= 0xFFFF:
            raise SymbolError(f"address {address} out of range")
        if role not in (*OPERAND_ROLES, "auto"):
            raise SymbolError(f"invalid role {role!r} (use value, address or auto)")
        self.modified = True
        if role == "auto":
            self._operands.pop((scope, address), None)
            return None
        override = OperandOverride(address, scope, role, comment or None)
        self._operands[(scope, address)] = override
        return override

    def operand_at(self, address: int, scope: str = GLOBAL) -> str | None:
        """The owner's operand decision at `address` ("value" / "address"; scope, else global)."""
        override = self._operands.get((scope, address)) or self._operands.get((GLOBAL, address))
        return override.role if override is not None else None

    def operand_overrides(self) -> list[OperandOverride]:
        """The owner's operand overrides, sorted by scope and address."""
        return [self._operands[key] for key in sorted(self._operands)]

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
            type=owner.type if owner.label else firmware.type, before=owner.before,
            after=owner.after,
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
            text: Only symbols whose label, comment or a comment block contains this text
                (case-insensitive).
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
                or any(
                    needle in (text or "").lower()
                    for text in (symbol.label, symbol.comment, symbol.before, symbol.after)
                )
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
        blocks: dict[tuple[str, int, str], list[str]] = {}  # the lines of each block, in order
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            try:
                entry = parse_annotation_line(line)
                if isinstance(entry, BlockLine):
                    key = (entry.scope, entry.address, entry.position)
                    blocks.setdefault(key, []).append(entry.text)
                elif isinstance(entry, OperandOverride):
                    self.set_operand(entry.address, entry.role, entry.scope, entry.comment)
                elif entry is not None:
                    self._merge_entry(entry)
            except SymbolError as error:
                raise SymbolError(f"{path}:{number}: {error}") from None
            count += entry is not None
        for (scope, address, position), lines in blocks.items():  # a block of the file replaces
            self.add(address, scope=scope, **{position: "\n".join(lines)})
        self.modified = was_modified  # what the file holds is not an unsaved change
        return count

    def _merge_entry(self, entry: Symbol | OwnVariable) -> None:
        """Add a symbol or variable read from a file or another table (it replaces its own)."""
        if isinstance(entry, OwnVariable):
            self.add_variable(entry, replaces=entry.label)
        else:
            self.add(entry.address, entry.label, entry.comment, entry.scope, entry.type,
                     before=entry.before, after=entry.after)  # fmt: skip

    def save(self, path: str | Path) -> int:
        """Write the owner's symbols and variables to an annotation file.

        Returns:
            The number of entries written.
        """
        user = self.owner_symbols()
        variables = self.own_variables()
        operands = self.operand_overrides()
        text = (FILE_HEADER + "".join(format_symbol_lines(s) for s in user)
                + "".join(format_variable_line(v) for v in variables)
                + "".join(format_operand_line(o) for o in operands))  # fmt: skip
        Path(path).write_text(text, encoding="utf-8")
        self.modified = False
        return len(user) + len(variables) + len(operands)


def parse_annotation_line(  # pylint: disable=too-many-return-statements  # one per line type
    line: str,
) -> Symbol | OwnVariable | BlockLine | OperandOverride | None:
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
    if fields and fields[0] in BLOCK_POSITIONS:
        if len(fields) != 2:
            raise SymbolError(f"expected 'before|after [scope:]address ; text', got {line!r}")
        scope, _, address_text = fields[1].rpartition(":")
        block_text = comment.removeprefix(" ").rstrip()  # keeps the indentation of the text
        return BlockLine(parse_address(address_text), validate_scope(scope), fields[0], block_text)
    if fields and fields[0] in OPERAND_ROLES:
        if len(fields) != 2:
            raise SymbolError(f"expected 'value|address [scope:]address [; comment]', got {line!r}")
        scope, _, address_text = fields[1].rpartition(":")
        return OperandOverride(parse_address(address_text), validate_scope(scope), fields[0],
                               comment_text)  # fmt: skip
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


def format_symbol_lines(symbol: Symbol) -> str:
    """Format a symbol as annotation file lines: its block before, its line, its block after."""
    location = f"{symbol.scope}:{symbol.address:04X}" if symbol.scope else f"{symbol.address:04X}"

    def file_lines(position: str, block: str | None) -> str:
        """One `before|after address ; text` line per line of the block."""
        if not block:
            return ""
        return "".join(f"{position:<6} {location:<11} ; {text}".rstrip() + "\n"
                       for text in block.split("\n"))  # fmt: skip

    line = format_annotation_line(symbol) if symbol.label or symbol.comment else ""
    return file_lines("before", symbol.before) + line + file_lines("after", symbol.after)


def format_annotation_line(symbol: Symbol) -> str:
    """Format a symbol's label and comment as an annotation file line (with trailing newline)."""
    location = f"{symbol.scope}:{symbol.address:04X}" if symbol.scope else f"{symbol.address:04X}"
    line = f"{symbol.type or '':<4} {location:<11} {symbol.label or '-':<20}"
    if symbol.comment:
        line += f" ; {symbol.comment}"
    return line.strip() + "\n"


def format_operand_line(override: OperandOverride) -> str:
    """Format an operand override as an annotation file line (with trailing newline)."""
    location = override.scope + ":" if override.scope else ""
    line = f"{override.role:<7} {location}{override.address:04X}"
    if override.comment:
        line += f" ; {override.comment}"
    return line + "\n"


def format_variable_line(variable: OwnVariable) -> str:
    """Format one of the owner's variables as an annotation file line (with trailing newline)."""
    line = f"var  {variable.base_text:<11} {variable.label:<20} {variable.size}"
    if variable.comment:
        line += f" ; {variable.comment}"
    return line + "\n"


def merge(tables: Iterable[SymbolTable]) -> SymbolTable:
    """A new table with the owner's symbols, variables and operand overrides of all `tables`.

    Later tables win.
    """
    tables = list(tables)
    model = next((table.firmware_model for table in tables if table.firmware_model), "464")
    merged = SymbolTable(preload_firmware=True, model=model)
    for table in tables:
        for entry in [*table.owner_symbols(), *table.own_variables()]:
            merged._merge_entry(entry)  # pylint: disable=protected-access  # same class
        for override in table.operand_overrides():
            merged.set_operand(override.address, override.role, override.scope, override.comment)
    merged.modified = False
    return merged
