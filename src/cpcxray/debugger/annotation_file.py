"""The annotation file (project/annotations.txt): one line per entry (D-35, D-41..D-44).

Plain text, `#` starts a comment line:

    # group NAME  [; description]
    # context scope|[scope:]FIRST-LAST  GROUP
    # [sub|jump|data] [scope:]address  label  [group=NAME]  [; comment]
    # var address|romN+offset  label  size  [group=NAME]  [; comment]
    # before|after [scope:]address  [group=NAME]  ; one line of a comment block
    # value|address [scope:]address  [; comment]   the operand of the instruction there

Addresses are hex (optionally with a leading & or #; a global one may start with `:`); `-`
stands for "no label". A block line keeps the text after "; " as it is (indentation too; `;`
alone is an empty line). `parse_annotation_line` reads one line, the `format_*` functions
write one entry; `SymbolTable.load` / `save` (debugger/symbols.py) apply and order them.
"""

from __future__ import annotations

import re

from cpcxray.debugger.symbol_types import (
    BLOCK_POSITIONS,
    GLOBAL,
    LABEL_PATTERN,
    OPERAND_ROLES,
    OWNER_TYPES,
    AnnotationEntry,
    BlockLine,
    Context,
    Group,
    OperandOverride,
    OwnVariable,
    Symbol,
    SymbolError,
    parse_address,
    validate_scope,
)

FILE_HEADER = ("# cpcxray annotations\n"
               "# group NAME  [; description]\n"
               "# context scope|[scope:]FIRST-LAST  GROUP\n"
               "# [sub|jump|data] [scope:]address  label  [group=NAME]  [; comment]\n"
               "# var address|romN+offset  label  size  [group=NAME]  [; comment]\n"
               "# before|after [scope:]address  [group=NAME]  ; one line of a comment block\n"
               "# value|address [scope:]address  [; comment]  (the operand of the instruction)\n"
               )  # fmt: skip
GROUP_TOKEN = "group="
VARIABLE_PATTERN = re.compile(r"^(?:rom(\d{1,2})\+)?([&#]?[0-9A-Fa-f]{1,4})$")
RANGE_PATTERN = re.compile(r"^([&#]?[0-9A-Fa-f]{1,4})-([&#]?[0-9A-Fa-f]{1,4})$")


def _take_group(fields: list[str]) -> str | None:
    """Remove a `group=NAME` token from the fields and return the name (None without one)."""
    tokens = [field for field in fields if field.startswith(GROUP_TOKEN)]
    if len(tokens) > 1:
        raise SymbolError("more than one group= token")
    if not tokens:
        return None
    fields.remove(tokens[0])
    name = tokens[0][len(GROUP_TOKEN) :]
    if not LABEL_PATTERN.match(name):
        raise SymbolError(f"invalid group name {name!r}")
    return name


def _split_location(text: str) -> tuple[str, int]:
    """`[scope:]address` (a global one may start with ":") → (scope, address)."""
    scope, _, address_text = text.rpartition(":")
    return validate_scope(scope), parse_address(address_text)


def parse_annotation_line(  # pylint: disable=too-many-return-statements  # one per line type
    line: str,
) -> AnnotationEntry | None:
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
    if fields and fields[0] == "group":
        if len(fields) != 2 or not LABEL_PATTERN.match(fields[1]):
            raise SymbolError(f"expected 'group NAME [; description]', got {line!r}")
        return Group(fields[1], comment_text)
    if fields and fields[0] == "context":
        return _parse_context(fields[1:], line)
    group = _take_group(fields)
    if fields and fields[0] == "var":
        return _parse_variable(fields[1:], comment_text, line, group)
    if fields and fields[0] in BLOCK_POSITIONS:
        if len(fields) != 2:
            raise SymbolError(f"expected 'before|after [scope:]address [group=NAME] ; text', "
                              f"got {line!r}")  # fmt: skip
        scope, address = _split_location(fields[1])
        block_text = comment.removeprefix(" ").rstrip()  # keeps the indentation of the text
        return BlockLine(address, scope, fields[0], block_text, group)
    if fields and fields[0] in OPERAND_ROLES:
        if len(fields) != 2 or group is not None:
            raise SymbolError(f"expected 'value|address [scope:]address [; comment]', got {line!r}")
        scope, address = _split_location(fields[1])
        return OperandOverride(address, scope, fields[0], comment_text)
    return _parse_symbol(fields, comment_text, group, line)


def _parse_symbol(fields: list[str], comment: str | None, group: str | None, line: str) -> Symbol:
    """A label / comment line: `[type] [scope:]address label` (the group taken out)."""
    symbol_type = fields.pop(0) if fields and fields[0] in OWNER_TYPES else None
    if len(fields) not in (1, 2):
        raise SymbolError(f"expected '[type] [scope:]address label [; comment]', got {line!r}")
    scope, address = _split_location(fields[0])
    label = fields[1] if len(fields) == 2 and fields[1] != "-" else None
    if label is None and comment is None:
        raise SymbolError(f"entry without label and comment: {line!r}")
    if group is not None and (scope != GLOBAL or label is None):
        raise SymbolError(f"only a global label can have a group: {line!r}")
    if label is not None:
        symbol_type = symbol_type or "sub"
    return Symbol(address, label, comment, scope, type=symbol_type, group=group)


def _parse_context(fields: list[str], line: str) -> Context:
    """The fields after "context": `scope|[scope:]FIRST-LAST GROUP`."""
    if len(fields) != 2 or not LABEL_PATTERN.match(fields[1]):
        raise SymbolError(f"expected 'context scope|[scope:]FIRST-LAST GROUP', got {line!r}")
    place, group = fields
    scope, _, span = place.rpartition(":")
    match = RANGE_PATTERN.match(span)
    if match is None:
        if scope:
            raise SymbolError(f"invalid context range {place!r}")
        return Context(validate_scope(span), None, None, group)
    first, last = parse_address(match.group(1)), parse_address(match.group(2))
    if first > last:
        raise SymbolError(f"invalid context range {place!r}")
    return Context(validate_scope(scope), first, last, group)


def _parse_variable(
    fields: list[str], comment: str | None, line: str, group: str | None
) -> OwnVariable:
    """The fields after "var": `address|romN+offset label size` (the group taken out)."""
    if len(fields) != 3:
        raise SymbolError(f"expected 'var address|romN+offset label size [group=NAME] "
                          f"[; comment]', got {line!r}")  # fmt: skip
    location, label, size_text = fields
    rom, address = parse_variable_base(location)
    if not LABEL_PATTERN.match(label):
        raise SymbolError(f"invalid label {label!r}")
    try:
        size = int(size_text, 0)
    except ValueError:
        raise SymbolError(f"invalid size {size_text!r}") from None
    return OwnVariable(label, size, address, rom, comment, group)


def parse_variable_base(text: str) -> tuple[int | None, int]:
    """Parse a variable's place: "A612" / "&A612" (absolute) or "rom5+0012" (ROM data area).

    Returns:
        (ROM number or None, address or offset).

    Raises:
        SymbolError: If it is neither.
    """
    match = VARIABLE_PATTERN.match(text.strip().removeprefix(":"))
    if match is None:
        raise SymbolError(f"invalid variable address {text!r} (use A612 or rom5+0012)")
    rom = int(match.group(1)) if match.group(1) is not None else None
    if rom is not None and rom > 15:
        raise SymbolError(f"invalid ROM {rom} (0-15)")
    return rom, parse_address(match.group(2))


def _group_suffix(group: str | None) -> str:
    """The ` group=NAME` token of a file line, or ""."""
    return f" {GROUP_TOKEN}{group}" if group else ""


def format_symbol_lines(symbol: Symbol) -> str:
    """Format a symbol as annotation file lines: its block before, its line, its block after."""
    location = f"{symbol.scope}:{symbol.address:04X}" if symbol.scope else f"{symbol.address:04X}"
    group = _group_suffix(symbol.group)

    def file_lines(position: str, block: str | None) -> str:
        """One `before|after address [group=NAME] ; text` line per line of the block."""
        if not block:
            return ""
        return "".join(f"{position:<6} {location:<11}{group} ; {text}".rstrip() + "\n"
                       for text in block.split("\n"))  # fmt: skip

    line = format_annotation_line(symbol) if symbol.label or symbol.comment else ""
    return file_lines("before", symbol.before) + line + file_lines("after", symbol.after)


def format_annotation_line(symbol: Symbol) -> str:
    """Format a symbol's label and comment as an annotation file line (with trailing newline)."""
    location = f"{symbol.scope}:{symbol.address:04X}" if symbol.scope else f"{symbol.address:04X}"
    line = f"{symbol.type or '':<4} {location:<11} {symbol.label or '-':<20}"
    line += _group_suffix(symbol.group if symbol.source == "user" else None)
    if symbol.comment:
        line += f" ; {symbol.comment}"
    return line.strip() + "\n"


def format_group_line(group: Group) -> str:
    """Format a group as an annotation file line (with trailing newline)."""
    line = f"group {group.name}"
    return line + (f" ; {group.description}" if group.description else "") + "\n"


def format_context_line(context: Context) -> str:
    """Format a context as an annotation file line (with trailing newline)."""
    return f"context {context.text:<16} {context.group}\n"


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
    line += _group_suffix(variable.group)
    if variable.comment:
        line += f" ; {variable.comment}"
    return line + "\n"
