"""Text of the Symbols tab (task 5g), without Dear PyGui (tested headless, NFR-04).

The list rows, the detail panel's lines, the "→ now at" of the variable form and parsing the
form's base ("absolute" or a ROM's data area plus an offset).
"""

from __future__ import annotations

from typing import Any

MARKS = {"yours": "✎", "analysis": "◇"}  # ✎ the owner's, ◇ from the analysis
SCOPES = ("all", "global", "lower", *(f"rom{n}" for n in range(16)))
BASES = ("absolute", *(f"rom{n}" for n in range(16)))


def entry_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    """What identifies a list entry across refreshes: type, address, scope, label."""
    return entry["type"] or "", entry["address"], entry["scope"], entry["label"] or ""


def symbol_row(entry: dict[str, Any]) -> str:
    """One list row: type, address, the ✎ / ◇ mark and the label (or "-")."""
    mark = MARKS.get(entry.get("mark") or "", " ")
    return f"{entry['type'] or '':<4} {entry['address']} {mark} {entry['label'] or '-'}"


def scope_text(scope: str) -> str:
    """A scope for people: "global", "lower ROM", "ROM 5"."""
    if not scope:
        return "global"
    return "lower ROM" if scope == "lower" else f"ROM {scope[3:]}"


def scope_value(choice: str) -> str | None:
    """The scope filter's choice as `symbol_search` takes it (None for "all")."""
    if choice == "all":
        return None
    return "" if choice == "global" else choice


def detail_rows(detail: dict[str, Any], entry: dict[str, Any]) -> list[tuple[str, str]]:
    """The detail panel's key / value lines for an entry and its `symbol_detail`."""
    rows = [("address", f"{detail['address']}  ({scope_text(detail['scope'])})")]
    rows.append(("from", _origin(detail, entry)))
    variable = detail.get("variable")
    if variable is not None and variable.get("description"):
        rows.append(("holds", variable["description"]))
    if detail.get("machines"):
        rows.append(("machines", " · ".join(detail["machines"])))
    code = detail.get("code")
    if code is not None and entry["type"] not in ("VAR", "var"):
        rows.append(("code", code["text"]))
    return rows


def _origin(detail: dict[str, Any], entry: dict[str, Any]) -> str:
    """Where a name comes from: firmware section, variable, the owner, the analysis."""
    variable = detail.get("variable")
    firmware = detail.get("firmware")
    if entry["type"] == "FW" and firmware is not None:
        note = f" ({firmware['note']})" if firmware["note"] else ""
        return f"firmware: {firmware['section']}, {firmware['name']}{note}"
    if variable is not None and variable["owner"] == "owner":
        now = variable.get("now") or "not placed now"
        return f"your variable: {variable['base']}, {variable['size']} bytes → {now}"
    if variable is not None:
        owner = "AMSDOS" if variable["owner"] == "amsdos" else "OS"
        return (
            f"{owner} variable: {variable['address']}-{variable['last']}, {variable['size']} bytes"
        )
    if entry.get("mark") == "analysis":
        return f"analysis: the source generator's name ({entry['type']})"
    if entry.get("mark") == "yours":
        return f"yours ({entry['type'] or 'comment'})"
    return ""


def variable_base(choice: str, offset_text: str) -> str:
    """The form's base and offset as `variable_add` takes it: "A612" or "rom5+0012"."""
    offset = offset_text.strip().lstrip("&#") or "0"
    return offset if choice == "absolute" else f"{choice}+{offset}"


def now_text(where: dict[str, Any]) -> str:
    """The form's "→ now at A612-A621" from `variable_locate` (or why it has no place now)."""
    if where.get("first") is None:
        return "→ the ROM has no data area now"
    return f"→ now at {where['first']}-{where['last']}"


def counts_label(symbol_type: str, counts: dict[str, int]) -> str:
    """A type chip's text with its count: "FW 275"."""
    return f"{symbol_type} {counts.get(symbol_type, 0)}"
