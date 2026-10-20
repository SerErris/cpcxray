"""Exports of a scrambled ROM's analysis: clear dump, ROM map, coverage CSV (design.md §6.5).

* `rom_dump`: the 16 KB clear ROM for a plain replacement ROM (FR-DEC-05), never-read bytes
  per fill policy (D-20).
* `rom_map`: an HTML page with a colour-coded map of all 16,384 bytes (128 per row) and the
  list of never-accessed ranges, plus a text summary (FR-DEC-06). Categories per byte:
  never accessed, M1, data, executed via RAM (code copied to RAM and run there, D-21), real
  conflict, harmless conflict.
* `coverage_csv`: one row per byte with class, raw and clear value, hits and first PC.
"""

from __future__ import annotations

import base64
import csv
import html
from pathlib import Path

import numpy as np

from cpcxray.decoding.m1map import access_ranges, decode_rom, ranges_text
from cpcxray.decoding.shadow_rom import (
    CLASS_NAMES,
    DATA,
    ERROR,
    M1,
    NO_PC,
    ROM_SIZE,
    UNKNOWN,
    ShadowRom,
)
from cpcxray.decoding.vortex import vortex_decode, vortex_mask
from cpcxray.ui.png import encode_png

BYTES_PER_ROW = 128
CATEGORY_COLOURS = {
    "never accessed": (40, 40, 40),
    "M1": (60, 200, 60),
    "data": (60, 110, 230),
    "executed via RAM": (190, 80, 230),
    "conflict": (230, 40, 40),
    "harmless conflict": (240, 160, 40),
}
EXEC_VIA_RAM = 0x01  # bit in the per-byte analysis flags (see analysis/targets.py)


def byte_categories(shadow: ShadowRom, flags: bytes | None = None) -> list[str]:
    """Return the ROM map category of every byte."""
    categories = []
    for offset, access_class in enumerate(shadow.access_map):
        if access_class == ERROR:
            category = "harmless conflict" if vortex_mask(offset) == 0 else "conflict"
        elif flags is not None and flags[offset] & EXEC_VIA_RAM:
            category = "executed via RAM"
        elif access_class == M1:
            category = "M1"
        elif access_class == DATA:
            category = "data"
        else:
            category = "never accessed"
        categories.append(category)
    return categories


def rom_dump(path: str | Path, shadow: ShadowRom, fill_policy: str, conflict: str = "m1") -> bytes:
    """Write the clear ROM image; return it."""
    clear = decode_rom(shadow.raw, bytes(shadow.access_map), vortex_decode, fill_policy, conflict)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(clear)
    return clear


def rom_map(path: str | Path, shadow: ShadowRom, title: str, flags: bytes | None = None,
            base: int = 0xC000) -> dict[str, object]:  # fmt: skip
    """Write the HTML ROM map and a .txt summary next to it; return the summary numbers."""
    categories = byte_categories(shadow, flags)
    counts = {name: categories.count(name) for name in CATEGORY_COLOURS}
    pixels = np.array([CATEGORY_COLOURS[c] for c in categories], dtype=np.uint8)
    rgba = np.concatenate([pixels, np.full((ROM_SIZE, 1), 255, np.uint8)], axis=1)
    png = encode_png(rgba.reshape((ROM_SIZE // BYTES_PER_ROW, BYTES_PER_ROW, 4)))
    never = [r for r in access_ranges(bytes(shadow.access_map)) if r.access_class == "unknown"]
    never_lines = "".join(
        f"{base + r.first:04X}-{base + r.last:04X}  {r.length:5} bytes\n" for r in never
    )
    summary = (
        f"ROM map: {title}\n"
        + "".join(f"{name:<18} {count:6} bytes\n" for name, count in counts.items())
        + f"\nNever accessed ({len(never)} ranges):\n{never_lines}"
    )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.with_suffix(".txt").write_text(summary, encoding="utf-8")
    target.write_text(_rom_map_html(title, png, counts, never_lines, base), encoding="utf-8")
    return {"html": str(target), "text": str(target.with_suffix(".txt")), "counts": counts,
            "never_accessed_ranges": len(never)}  # fmt: skip


def _rom_map_html(
    title: str, png: bytes, counts: dict[str, int], never_lines: str, base: int
) -> str:
    """Build the ROM map page: legend, scaled map image with row addresses, never-accessed list."""
    image = base64.b64encode(png).decode("ascii")
    legend = "".join(
        f'<span class="key" style="background:rgb{colour}"></span>{html.escape(name)}: '
        f"{counts[name]}&nbsp;&nbsp; "
        for name, colour in CATEGORY_COLOURS.items()
    )
    rows = "".join(
        f"<div>{base + row * BYTES_PER_ROW:04X}</div>" for row in range(ROM_SIZE // BYTES_PER_ROW)
    )
    return f"""<!doctype html><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>body{{font-family:monospace;background:#111;color:#ddd}}
.key{{display:inline-block;width:12px;height:12px;margin-right:4px;vertical-align:middle}}
.map{{display:flex;gap:6px}} .rows div{{height:6px;font-size:6px;line-height:6px}}
img{{image-rendering:pixelated;width:{BYTES_PER_ROW * 6}px}}</style>
<h1>{html.escape(title)}</h1><p>{legend}</p>
<p>One pixel per byte, {BYTES_PER_ROW} bytes per row; row addresses on the left.</p>
<div class="map"><div class="rows">{rows}</div><img src="data:image/png;base64,{image}"></div>
<h2>Never accessed</h2><pre>{html.escape(never_lines)}</pre>
"""


def coverage_csv(path: str | Path, shadow: ShadowRom, flags: bytes | None = None,
                 base: int = 0xC000) -> int:  # fmt: skip
    """Write one CSV row per byte; return the number of rows."""
    clear = decode_rom(shadow.raw, bytes(shadow.access_map), vortex_decode, "raw")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["offset", "address", "class", "raw", "clear", "hits", "first_pc",
                         "exec_via_ram"])  # fmt: skip
        for offset in range(ROM_SIZE):
            first = shadow.first_pc[offset]
            writer.writerow([
                f"{offset:04X}", f"{base + offset:04X}", CLASS_NAMES[shadow.access_map[offset]],
                f"{shadow.raw[offset]:02X}", f"{clear[offset]:02X}", shadow.hit_count[offset],
                "" if first == NO_PC else f"{first:04X}",
                int(bool(flags and flags[offset] & EXEC_VIA_RAM)),
            ])  # fmt: skip
    return ROM_SIZE


__all__ = ["CATEGORY_COLOURS", "UNKNOWN", "coverage_csv", "ranges_text", "rom_dump", "rom_map"]
