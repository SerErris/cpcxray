"""Line formatting of the sjasmplus source generator (analysis/source_gen.py).

Pure helpers without generator state: data split into strings and defb lines, string
operands with CPC terminators, one formatted source line, EQU lines, generated name types.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from cpcxray.cpu.disasm import hex_byte, hex_word

INDENT = " " * 8
COMMENT_COLUMN = 40
BYTES_PER_LINE = 8
STRING_LINE_LIMIT = 48
MIN_STRING = 4


def name_type(label: str) -> str:
    """The symbol type of a generated name (task 5g).

    sub_, far_, ram_, rom_init and rsx_ names are "sub", l_ names "jump", d_ names and the
    ROM header's fields "data".
    """
    if label.startswith(("sub_", "far_", "ram_", "rom_init", "rsx_")):
        return "sub"
    return "jump" if label.startswith("l_") else "data"


def _printable(value: int) -> bool:
    """True for characters that can go inside a sjasmplus "..." string unescaped."""
    return 0x20 <= value <= 0x7E and value not in (0x22, 0x5C)


def _terminator(value: int) -> bool:
    """True for a CPC string terminator: a printable character with bit 7 set."""
    return bool(value & 0x80) and _printable(value & 0x7F)


def string_operands(data: bytes) -> str:
    """Render bytes as defb operands: "TEXT" runs, "E"+#80 terminators, #xx otherwise."""
    tokens, index = [], 0
    while index < len(data):
        if _printable(data[index]):
            end = index
            while end < len(data) and _printable(data[end]):
                end += 1
            tokens.append('"' + data[index:end].decode("ascii") + '"')
            index = end
            continue
        value = data[index]
        tokens.append(f'"{chr(value & 0x7F)}"+#80' if _terminator(value) else hex_byte(value))
        index += 1
    return ",".join(tokens)


def split_data(data: bytes) -> list[tuple[int, int, bool]]:
    """Split plain data into lines: (start, length, is_string).

    Strings are runs of at least MIN_STRING printable characters, or at least two followed by
    a CPC terminator (bit 7 set); everything else goes into defb lines of up to 8 bytes.
    """
    lines: list[tuple[int, int, bool]] = []
    pending_start, index = None, 0
    while index < len(data):
        end = index
        while end < len(data) and _printable(data[end]) and end - index < STRING_LINE_LIMIT:
            end += 1
        terminated = end < len(data) and end - index < STRING_LINE_LIMIT and _terminator(data[end])
        printable_length = end - index
        if printable_length >= MIN_STRING or (terminated and printable_length >= 2):
            if pending_start is not None:
                lines += _byte_lines(pending_start, index)
                pending_start = None
            length = printable_length + (1 if terminated else 0)
            lines.append((index, length, True))
            index += length
            continue
        if pending_start is None:
            pending_start = index
        index += 1
    if pending_start is not None:
        lines += _byte_lines(pending_start, len(data))
    return lines


def _byte_lines(start: int, end: int) -> list[tuple[int, int, bool]]:
    """Cut start..end into defb lines of up to BYTES_PER_LINE bytes."""
    return [
        (offset, min(BYTES_PER_LINE, end - offset), False)
        for offset in range(start, end, BYTES_PER_LINE)
    ]


def _format_line(text: str, comment: str = "", label: str | None = None) -> list[str]:
    """Format one source line (label on its own line, comment at COMMENT_COLUMN).

    A line longer than the column keeps one space before the comment.
    """
    lines = [f"{label}:"] if label else []
    line = INDENT + text
    if comment:
        line = f"{line:<{COMMENT_COLUMN - 1}} ; {comment}"
    lines.append(line.rstrip())
    return lines


def _identifier(text: str) -> str:
    """Turn an RSX name into an identifier part (lower case, other characters as _)."""
    cleaned = re.sub(r"[^a-z0-9]", "_", text.lower()).strip("_")
    return cleaned


def _excluding(lookup: Callable[[int], str | None], excluded: int) -> Callable[[int], str | None]:
    """A symbol lookup that finds no label for one address (it belongs to another ROM)."""

    def wrapped(address: int) -> str | None:
        """Look up every address but the excluded one."""
        return None if address == excluded else lookup(address)

    return wrapped


def _equ(label: str, address: int) -> str:
    """An EQU line; the label is padded to 24 columns but always followed by a space."""
    return f"{label:<23} EQU {hex_word(address)}"


def _no_label(_address: int) -> None:
    """Symbol lookup that never finds a label (numbers stay hex)."""
