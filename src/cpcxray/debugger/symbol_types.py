"""The data types of the owner's knowledge about code (debugger/symbols.py, annotation_file.py).

Symbols (labels, comments, comment blocks), the owner's variables, groups and contexts
(task 10, D-43, D-44), operand overrides (D-42), and the small helpers both modules use:
scope and address checks, comment block text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GLOBAL = ""
LABEL_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
SCOPE_PATTERN = re.compile(r"^(lower|rom([0-9]|1[0-5]))$")
OWNER_TYPES = ("sub", "jump", "data")  # the owner's labels; "var": OwnVariable
BLOCK_POSITIONS = ("before", "after")  # comment blocks around a line (task 9)
OPERAND_ROLES = ("value", "address")  # the owner's operand overrides ("auto": none)
FIRMWARE_GROUP = "firmware"
BUILTIN_GROUPS = (FIRMWARE_GROUP, "amsdos")  # what cpcxray knows (D-43); not the owner's

SymbolKey = tuple[str, int, str | None]  # (scope, address, group): group only for scope ""


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
        group: The group (D-43): an owner's group for a global label, "firmware" for the
            firmware names, else None (ungrouped).
    """

    address: int
    label: str | None = None
    comment: str | None = None
    scope: str = GLOBAL
    source: str = "user"
    type: str | None = None
    before: str | None = None
    after: str | None = None
    group: str | None = None

    @property
    def key(self) -> SymbolKey:
        """(scope, address, group): at most one owner's symbol per key (D-44)."""
        return self.scope, self.address, self.group

    @property
    def sort_key(self) -> tuple[str, int, str]:
        """For listings: by scope, address, then group (ungrouped first)."""
        return self.scope, self.address, self.group or ""


@dataclass(frozen=True)
class OwnVariable:  # pylint: disable=too-many-instance-attributes  # the parts of one variable
    """A variable of the owner's (type "var"): a named byte range in RAM.

    Attributes:
        label: Label, unique among all labels.
        size: Size in bytes (1-65536).
        address: The absolute address, or with `rom` the offset in that ROM's data area.
        rom: None for an absolute address; else the background ROM whose data area (IY, from
            KL_ROM_IY_TABLE) the offset counts from - it moves with the ROMs installed.
        comment: What it holds, or None.
        group: The owner's group it is in (D-43), or None.
    """

    label: str
    size: int
    address: int
    rom: int | None = None
    comment: str | None = None
    group: str | None = None

    @property
    def base_text(self) -> str:
        """Where it is, as in the annotation file: "A612" or "rom5+0012"."""
        location = f"{self.address:04X}"
        return location if self.rom is None else f"rom{self.rom}+{location}"


@dataclass(frozen=True)
class Group:
    """One of the owner's groups (D-43).

    Attributes:
        name: Its name (an identifier).
        description: What it holds, or None (the heading of its section in the source).
    """

    name: str
    description: str | None = None


@dataclass(frozen=True)
class Context:
    """A group given to code (D-43): a range of a scope, or the whole scope.

    Attributes:
        scope: "" (RAM code), "lower" or "romN".
        first: First address of the range, or None for the whole scope.
        last: Last address (inclusive), or None for the whole scope.
        group: The group the code works with.
    """

    scope: str
    first: int | None
    last: int | None
    group: str

    @property
    def place(self) -> tuple[str, int | None, int | None]:
        """(scope, first, last): at most one context per place."""
        return self.scope, self.first, self.last

    @property
    def text(self) -> str:
        """As in the annotation file: "rom5", "rom5:C3F6-C4FF" or "9000-93FF"."""
        if self.first is None or self.last is None:
            return self.scope or "ram"
        span = f"{self.first:04X}-{self.last:04X}"
        return f"{self.scope}:{span}" if self.scope else span

    def contains(self, scope: str, address: int) -> bool:
        """True if code at (scope, address) is in this context."""
        if scope != self.scope:
            return False
        return self.first is None or self.last is None or self.first <= address <= self.last


@dataclass(frozen=True)
class BlockLine:
    """One line of a comment block in the annotation file (`before|after [scope:]address ; …`).

    Attributes:
        address: 16-bit address.
        scope: "" (global), "lower" or "romN".
        position: "before" or "after" the line at the address.
        text: The line's text ("" for an empty line).
        group: The group of the global label the block belongs to, or None.
    """

    address: int
    scope: str
    position: str
    text: str
    group: str | None = None


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


AnnotationEntry = Symbol | OwnVariable | BlockLine | OperandOverride | Group | Context


def normalize_block(text: str | None) -> str | None:
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
