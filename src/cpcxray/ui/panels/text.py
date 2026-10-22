"""Text formatting and input parsing for the debugger panels (no Dear PyGui; FR-DBG-02..09).

The panels (ui/panels/tabs.py) only arrange widgets; what they show comes from `Session` methods and
is formatted here, so it is tested without a window and is exactly what MCP clients get.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

FLAG_LETTERS = "SZ5H3PNC"


def flags_text(f: int) -> str:
    """Return the flags as letters, "-" for a clear flag (e.g. "SZ-H--NC")."""
    return "".join(
        letter if f & (0x80 >> index) else "-" for index, letter in enumerate(FLAG_LETTERS)
    )


def registers_lines(registers: dict[str, int]) -> list[str]:
    """Format the register set for the register panel (FR-DBG-02)."""
    r = registers
    return [
        f"AF {r['af']:04X}   AF' {r['af_']:04X}   flags {flags_text(r['f'])}",
        f"BC {r['bc']:04X}   BC' {r['bc_']:04X}",
        f"DE {r['de']:04X}   DE' {r['de_']:04X}",
        f"HL {r['hl']:04X}   HL' {r['hl_']:04X}",
        f"IX {r['ix']:04X}   IY  {r['iy']:04X}",
        f"SP {r['sp']:04X}   PC  {r['pc']:04X}   WZ {r['wz']:04X}",
        f"I  {r['i']:02X}     R   {r['r']:02X}     IM {r['im']}",
        f"IFF1 {r['iff1']}  IFF2 {r['iff2']}  halted {r['halted']}",
    ]


def parse_assignments(text: str) -> dict[str, int]:
    """Parse "hl=4000, a=5" (hex values) into a register dict.

    Raises:
        ValueError: On malformed input.
    """
    values = {}
    for part in text.replace(";", ",").split(","):
        if not part.strip():
            continue
        name, separator, value = part.partition("=")
        if not separator:
            raise ValueError(f"expected name=value, got {part.strip()!r}")
        values[name.strip().lower()] = int(value.strip().lstrip("&#"), 16)
    if not values:
        raise ValueError("nothing to set")
    return values


def parse_memory_edit(text: str) -> tuple[str, str]:
    """Parse "4000: 3E 05 C9" into (address, hex bytes).

    Raises:
        ValueError: On malformed input.
    """
    address, separator, data = text.partition(":")
    if not separator or not address.strip() or not data.strip():
        raise ValueError("expected 'address: hex bytes', e.g. '4000: 3E 05'")
    return address.strip(), data.strip()


def line_address(line: str) -> int | None:
    """The address of a disassembly instruction line ("C006  CD ..."); None for other lines."""
    try:
        return int(line[:4], 16) if line[4:6] == "  " else None
    except ValueError:
        return None


def paging_lines(paging: dict[str, Any]) -> list[str]:
    """Format the paging per 16 KB quarter (FR-DBG-12): what the CPU reads there now."""
    configuration = paging.get("ram_configuration")
    title = "Paging" if configuration is None else f"Paging (RAM configuration {configuration})"
    return [f"{title}:"] + [f"  {q['start']}-{q['end']}  {q['text']}" for q in paging["quarters"]]


def with_paging(lines: list[str], quarters: list[dict[str, Any]]) -> list[str]:
    """Put a "; ---- <quarter>: <ROM or RAM>" line before the first line of each 16 KB quarter.

    Args:
        lines: Disassembly lines (instruction lines, label lines, continuation lines).
        quarters: Quarter descriptions as `paging_get` / `disassemble` return them.
    """
    texts = {int(q["start"], 16) >> 14: f"; ---- {q['start']}-{q['end']}: {q['text']}"
             for q in quarters}  # fmt: skip
    result: list[str] = []
    labels: list[str] = []  # label lines wait for their instruction: the separator goes first
    current = None
    for line in lines:
        address = line_address(line)
        if address is None:
            (labels if line.endswith(":") else result).append(line)
            continue
        if address >> 14 != current and address >> 14 in texts:
            current = address >> 14
            result.append(texts[current])
        result += [*labels, line]
        labels.clear()
    return result + labels


AREA = -1  # colour of a byte inside a pack's area but in no named variable
BYTES_PER_ROW = 16


@dataclass(frozen=True)
class MemoryRow:
    """One line of the memory panel's dump.

    Attributes:
        address: Address of the first byte, 4-digit hex.
        cells: Per byte its hex text ("--" if unknown) and colour: an index into the panel's
            palette for a known variable, AREA, or None.
        ascii: The bytes as characters ("." if not printable).
    """

    address: str
    cells: tuple[tuple[str, int | None], ...]
    ascii: str


def memory_layout(
    dump: dict[str, Any], colours: int
) -> tuple[list[MemoryRow], list[tuple[str, int | None]]]:
    """Lay out a `memory_view` result with its known variables highlighted (FR-DBG-11).

    Every byte gets the colour of the smallest named variable containing it; the variables
    take the colours 0 .. `colours` - 1 in turn, so neighbours differ. The legend lists these
    variables in the same colours, with range, size, the structure they belong to and their
    description; it starts with the pack areas in view.

    Returns:
        The rows and the legend lines (text, colour).
    """
    start = int(dump["address"], 16)
    variables = dump.get("variables", [])
    areas = [v for v in variables if v["area"]]
    named = [v for v in variables if not v["area"]]
    cells, colour_of = _coloured_bytes(start, dump["hex"], named, areas, colours)
    rows = []
    for row_start in range(0, len(cells), BYTES_PER_ROW):
        row_cells = tuple(cells[row_start : row_start + BYTES_PER_ROW])
        ascii_text = "".join(_printable(text) for text, _ in row_cells)
        rows.append(MemoryRow(f"{(start + row_start) & 0xFFFF:04X}", row_cells, ascii_text))
    return rows, _legend(areas, named, colour_of)


def _legend(
    areas: list[dict[str, Any]], named: list[dict[str, Any]], colour_of: dict[str, int]
) -> list[tuple[str, int | None]]:
    """The legend lines: the areas in view, then each coloured variable in its colour."""
    legend: list[tuple[str, int | None]] = []
    if areas:
        legend.append(("in " + ", ".join(a["label"] for a in areas), AREA))
    for label, colour in colour_of.items():
        variable = next(v for v in named if v["label"] == label)
        legend.append((_legend_text(variable, named), colour))
    return legend


def _contains(variable: dict[str, Any], address: int) -> bool:
    """True if `address` is one of the variable's bytes."""
    return int(variable["address"], 16) <= address <= int(variable["last"], 16)


def _coloured_bytes(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # the parts of one dump
    start: int, hex_text: str, named: list[dict[str, Any]], areas: list[dict[str, Any]],
    colours: int,
) -> tuple[list[tuple[str, int | None]], dict[str, int]]:  # fmt: skip
    """Per byte its text and colour (see `memory_layout`), and the colour of each variable."""
    colour_of: dict[str, int] = {}
    cells = []
    for offset in range(len(hex_text) // 2):
        address, byte = start + offset, hex_text[2 * offset : 2 * offset + 2]
        containing = [v for v in named if _contains(v, address)]
        if containing:
            label = min(containing, key=lambda v: v["size"])["label"]  # the smallest
            colour: int | None = colour_of.setdefault(label, len(colour_of) % colours)
        else:
            colour = AREA if any(_contains(area, address) for area in areas) else None
        cells.append(("--" if byte == "??" else byte, colour))
    return cells, colour_of


def _printable(hex_byte: str) -> str:
    """The character of a hex byte for the ASCII column ("." if unknown or not printable)."""
    if hex_byte == "--":
        return "."
    value = int(hex_byte, 16)
    return chr(value) if 32 <= value < 127 else "."


def _legend_text(variable: dict[str, Any], named: list[dict[str, Any]]) -> str:
    """A legend line, e.g. "B726-B726  TXT_CURSOR_ROW (1) in TXT_CURRENT_WINDOW: cursor ..."."""
    first, last = int(variable["address"], 16), int(variable["last"], 16)
    parents = [v for v in named if v is not variable and v["size"] > variable["size"]
               and int(v["address"], 16) <= first and last <= int(v["last"], 16)]  # fmt: skip
    parent = f" in {min(parents, key=lambda v: v['size'])['label']}" if parents else ""
    return (f"{variable['address']}-{variable['last']}  {variable['label']} "
            f"({variable['size']}){parent}: {variable['description']}")  # fmt: skip


def mark_pc(lines: list[str], pc: int, breakpoints: set[int]) -> list[str]:
    """Prefix disassembly lines: ">" at the PC, "*" at a breakpoint, else spaces."""
    marked = []
    for line in lines:
        address = line_address(line)
        marker = " "
        if address is not None and address in breakpoints:
            marker = "*"
        if address == pc:
            marker = ">"
        marked.append(f"{marker} {line}" if address is not None else f"  {line}")
    return marked


def devices_lines(devices: dict[str, Any]) -> list[str]:
    """Format the device state (FR-DBG-05) one device per block."""
    lines = []
    for name, state in devices.items():
        lines.append(f"[{name}]")
        if isinstance(state, dict):
            for key, value in state.items():
                lines.append(f"  {key}: {json.dumps(value)}")
        else:
            lines.append(f"  {json.dumps(state)}")
    return lines


def breakpoint_lines(breakpoints: list[dict[str, Any]]) -> list[str]:
    """Format the breakpoint list."""
    lines = []
    for entry in breakpoints:
        where = entry.get("address") or entry.get("port") or ""
        if "end" in entry:
            where += "-" + entry["end"]
        details = [entry["kind"], where]
        if "mask" in entry:
            details.append(f"mask {entry['mask']}")
        if entry.get("location", "any") != "any":
            details.append(entry["location"])
        if "rom" in entry:
            details.append(f"ROM {entry['rom']}")
        if entry.get("label"):
            details.append(f"({entry['label']})")
        state = "on " if entry["enabled"] else "off"
        lines.append(f"{entry['id']:3}  {state}  hits {entry['hits']:<5} " + " ".join(details))
    return lines or ["(no breakpoints)"]
