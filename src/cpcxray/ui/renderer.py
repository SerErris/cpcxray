"""Screen renderer: machine snapshot → RGBA image (design.md §5.4, §6.12; FR-DBG-01).

The renderer is independent of CPU timing (no raster emulation): it takes a `ScreenSnapshot` -
a copy of RAM plus the Gate Array mode and inks and the CRTC registers - and draws the whole
screen at once, so the GUI can render asynchronously to the emulation.

Output: an RGBA image of 768 x 272 pixels, i.e. mode 2 pixels horizontally (mode 1 pixels are
2 wide, mode 0 pixels 4 wide) and one row per scan line. The displayed area (R1 * 2 bytes wide,
R6 * (R9 + 1) lines high) is centred and surrounded by the border colour; R12/R13 give the start
address, so hardware scrolling is shown correctly. Displays show each scan line twice
(`double_lines`) to get the CPC's aspect ratio (768 x 544).

The pixel decoding uses per-mode lookup tables (byte → pens), built from
`cpcxray.machine.screen.pixel_pens`, and numpy for the address computation and colour lookup.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cpcxray.machine.screen import PIXELS_PER_BYTE, ScreenGeometry, pixel_pens
from cpcxray.ui.palette import HARDWARE_RGB

WIDTH = 768  # mode 2 pixels incl. border
HEIGHT = 272  # scan lines incl. border
BORDER_INK = 16


@dataclass(frozen=True)
class ScreenSnapshot:
    """Everything the renderer needs, copied at one moment (D-13).

    Attributes:
        ram: The 64 KB of RAM (the screen is always in RAM).
        mode: Gate Array screen mode 0-3.
        inks: Hardware colour numbers of pens 0-15 and the border (index 16).
        crtc: CRTC registers R0-R17.
    """

    ram: bytes
    mode: int
    inks: tuple[int, ...]
    crtc: tuple[int, ...]


def _pen_tables() -> dict[int, np.ndarray]:
    """Return, per mode, a (256, pixels per byte) table of pens for every byte value."""
    return {
        mode: np.array([pixel_pens(value, mode) for value in range(256)], dtype=np.uint8)
        for mode in (0, 1, 2, 3)
    }


PEN_TABLES = _pen_tables()
HARDWARE_RGBA = np.array([(*rgb, 255) for rgb in HARDWARE_RGB], dtype=np.uint8)


def line_addresses(geometry: ScreenGeometry, lines: int) -> np.ndarray:
    """Return the screen RAM address of every byte: shape (lines, bytes per line)."""
    line_numbers = np.arange(lines)
    rows = line_numbers // geometry.lines_per_row
    scan_lines = line_numbers % geometry.lines_per_row & 0x07  # RA0-RA2 select the 2 KB block
    columns = np.arange(geometry.bytes_per_line)
    within_page = (
        geometry.offset + rows[:, None] * geometry.bytes_per_line + columns[None, :]
    ) & 0x07FF
    return (geometry.base + within_page + scan_lines[:, None] * 0x0800) & 0xFFFF


def render(snapshot: ScreenSnapshot) -> np.ndarray:
    """Render a snapshot to an RGBA image of shape (272, 768, 4), dtype uint8."""
    image = np.empty((HEIGHT, WIDTH, 4), dtype=np.uint8)
    image[:, :] = HARDWARE_RGBA[snapshot.inks[BORDER_INK] & 0x1F]
    geometry = ScreenGeometry.from_crtc(list(snapshot.crtc))
    lines = min(geometry.rows * geometry.lines_per_row, HEIGHT)
    width = min(geometry.bytes_per_line * 8, WIDTH)
    if lines == 0 or width == 0:
        return image
    mode = snapshot.mode & 0x03
    ram = np.frombuffer(snapshot.ram, dtype=np.uint8)
    screen_bytes = ram[line_addresses(geometry, lines)]
    pens = PEN_TABLES[mode][screen_bytes].reshape(lines, -1)
    pixels = np.repeat(pens, 8 // PIXELS_PER_BYTE[mode], axis=1)[:, :width]
    ink_rgba = HARDWARE_RGBA[np.array(snapshot.inks[:16], dtype=np.uint8) & 0x1F]
    top, left = (HEIGHT - lines) // 2, (WIDTH - width) // 2
    image[top : top + lines, left : left + width] = ink_rgba[pixels]
    return image


def double_lines(image: np.ndarray) -> np.ndarray:
    """Show every scan line twice (the CPC's pixel aspect ratio): 768 x 544."""
    return np.repeat(image, 2, axis=0)
