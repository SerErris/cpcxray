"""Text and documents of the Project tab (task 5i), without Dear PyGui (tested headless, NFR-04).

The machine summary (title, ROM tags, drives line), the ROM editor's header text and the
configuration document the editor collects from its fields (the tables of a machine.toml, as
`Session.config_apply` / `config_write` take them).
"""

from __future__ import annotations

from pathlib import PurePath
from typing import Any

MODEL_CHIPS = {"464 · 64K": "464", "664 · 64K": "664", "6128 · 128K": "6128"}
RAM_SIZES = {"464": "64K", "664": "64K", "6128": "128K"}
SLOTS = ("lower", *(str(n) for n in range(16)))
DECODER_CHOICES = ("none", "vortex")
# when a RAM range's copy is taken (D-23): GUI wording (owner, 2026-10-23) → machine.toml value
CAPTURE_CHOICES = {"when it first runs": "first_m1", "on command": "manual"}
DRIVE_LETTERS = ("A", "B")


def model_chip(model: str) -> str:
    """The model chip's label for a model ("6128" → "6128 · 128K")."""
    return next(label for label, value in MODEL_CHIPS.items() if value == model)


def machine_title(model: str) -> str:
    """The summary's title: "CPC 6128 · 128K"."""
    return f"CPC {model} · {RAM_SIZES.get(model, '?')}"


def rom_tag(slot: str, table: dict[str, Any]) -> str:
    """A ROM tag of the summary: "7 · amsdos.rom", "5 · scrambled.rom · vortex · analysed"."""
    parts = [slot, PurePath(table["file"]).name]
    if table.get("decoder"):
        parts.append(table["decoder"])
    if table.get("analyse"):
        parts.append("analysed")
    return " · ".join(parts)


def roms_of(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The ROM tables of a document as (slot, table): the lower ROM first, then by number."""
    rom = config.get("rom", {})
    found = [("lower", rom["lower"])] if "lower" in rom else []
    upper = rom.get("upper", {})
    return found + [(slot, upper[slot]) for slot in sorted(upper, key=int)]


def drives_line(config: dict[str, Any]) -> str:
    """The summary's drives and RAM code: "A: cpm.dsk (read-only) · B: empty · RAM code …"."""
    drives = config.get("drive", {})
    parts = []
    for letter in DRIVE_LETTERS:
        drive = drives.get(letter)
        if drive is None:
            parts.append(f"{letter}: empty")
        else:
            mode = "writable" if drive.get("writable") else "read-only"
            parts.append(f"{letter}: {PurePath(drive['file']).name} ({mode})")
    targets = config.get("analysis", {}).get("ram", [])
    ram = ", ".join(f"{t['name']} {t['start']:04X}+{t['length']:X}" for t in targets)
    parts.append(f"RAM code to analyse: {ram or 'none'}")
    return " · ".join(parts)


def load_text(entry: dict[str, Any]) -> str:
    """A binary file to load, for the summary: "game.bin → 8000 (2048 bytes, loaded)"."""
    name = PurePath(entry["file"]).name
    if not entry["exists"]:
        return f"{name} \u2192 {entry['address']} (file missing)"
    state = "loaded" if entry["loaded"] else "not loaded"
    return f"{name} \u2192 {entry['address']} ({entry['length']} bytes, {state})"


def header_text(header: dict[str, Any]) -> str:
    """What a ROM header says, for the editor's ROM table: "background 0.50 · CPM ROM"."""
    if "error" in header:
        return header["error"]
    text = f"{header['type']} {header['version']}".strip()
    return f"{text} · {header['name']}" if header.get("name") else text


def rom_table(file: str, decoder: str, analyse: bool) -> dict[str, Any]:
    """One ROM slot's table from the editor's fields (keys at their default left out).

    A ROM with a decoder is always decoded (owner, 2026-10-23: a scrambled ROM cannot run
    otherwise), so the editor offers no decode switch; `decode` stays at its default.
    """
    table: dict[str, Any] = {"file": file.strip()}
    if decoder != "none":
        table["decoder"] = decoder
    if analyse:
        table["analyse"] = True
    return table


def number(text: str) -> int | str:
    """A hex number of the editor ("8000", "&8000", "#8000", "0x8000"); the text if invalid."""
    digits = text.strip().lstrip("&#")
    digits = digits[2:] if digits.lower().startswith("0x") else digits
    try:
        return int(digits, 16)
    except ValueError:
        return text.strip()


def document(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # one per part of a machine.toml
    model: str,
    unpopulated_rom: int,
    roms: list[tuple[str, dict[str, Any]]],
    drives: dict[str, dict[str, Any]],
    ram_targets: list[dict[str, Any]],
    loads: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The configuration document of the editor's fields (as `config_data` writes it)."""
    rom: dict[str, Any] = {}
    for slot, table in roms:
        if slot == "lower":
            rom["lower"] = table
        else:
            rom.setdefault("upper", {})[slot] = table
    result: dict[str, Any] = {
        "machine": {"model": model, "unpopulated_rom": unpopulated_rom}, "rom": rom,
    }  # fmt: skip
    if drives:
        result["drive"] = drives
    if ram_targets:
        result["analysis"] = {"ram": ram_targets}
    if loads:
        result["load"] = loads
    return result


def free_slots(used: list[str]) -> list[str]:
    """The ROM slots not configured yet (for "+ ROM in slot")."""
    return [slot for slot in SLOTS if slot not in used]
