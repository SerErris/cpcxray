"""Text formatting and input parsing for the debugger panels (no Dear PyGui; FR-DBG-02..09).

The panels (ui/panels/tabs.py) only arrange widgets; what they show comes from `Session` methods and
is formatted here, so it is tested without a window and is exactly what MCP clients get.
"""

from __future__ import annotations

import json
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


def mark_pc(lines: list[str], pc: int, breakpoints: set[int]) -> list[str]:
    """Prefix disassembly lines: ">" at the PC, "*" at a breakpoint, else spaces."""
    marked = []
    for line in lines:
        address_text = line[:4]
        try:
            address = int(address_text, 16) if line[4:6] == "  " else None
        except ValueError:
            address = None
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
