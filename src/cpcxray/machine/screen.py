"""Screen memory layout and text recognition (design.md §5.4, §6.16 `screen_text`).

Screen address of byte column x in character row r, scan line s (0-7), from the CRTC:

    base   = (R12 & &30) << 10                 16 KB page, &C000 when bits = 11
    offset = ((R12 & &03) << 8 | R13) * 2      hardware scroll, in bytes
    addr   = base + ((offset + r * R1 * 2 + x) & &07FF) + s * &0800

Pixel layout per byte (bit 7 = leftmost bit position):

* mode 0: 2 pixels, pen of pixel 0 = bits 7,3,5,1 (as pen bits 0-3), pixel 1 = bits 6,2,4,0
* mode 1: 4 pixels, pen of pixel n = bit (7-n) | bit (3-n) << 1
* mode 2: 8 pixels, pen = bit (7-n)

`screen_text` recognises characters by comparing each 8x8 character cell with the font in
the OS ROM (&3800-&3FFF, 8 bytes per character, bit 7 = left), trying each pen in the cell as
the paper colour. This reads text on the screen without a renderer (character cells, not
pixels), as used by the boot milestone and the MCP tool.
"""

from __future__ import annotations

from dataclasses import dataclass

FONT_ADDRESS = 0x3800  # in the lower ROM
CELL_BYTES = {0: 4, 1: 2, 2: 1, 3: 4}  # bytes per character cell line, per mode
PIXELS_PER_BYTE = {0: 2, 1: 4, 2: 8, 3: 2}
_SPECIAL_CHARACTERS = {164: "©"}  # CPC character set codes outside ASCII that matter in text


@dataclass(frozen=True)
class ScreenGeometry:
    """Screen layout from the CRTC registers.

    Attributes:
        base: Address of the 16 KB screen page.
        offset: Byte offset of the first character (hardware scroll).
        bytes_per_line: Bytes per scan line (R1 * 2).
        rows: Character rows (R6).
        lines_per_row: Scan lines per character row (R9 + 1).
    """

    base: int
    offset: int
    bytes_per_line: int
    rows: int
    lines_per_row: int

    @classmethod
    def from_crtc(cls, registers: list[int]) -> ScreenGeometry:
        """Compute the geometry from CRTC R1, R6, R9, R12 and R13."""
        r12, r13 = registers[12], registers[13]
        return cls(
            base=(r12 & 0x30) << 10,
            offset=((r12 & 0x03) << 8 | r13) * 2,
            bytes_per_line=registers[1] * 2,
            rows=registers[6],
            lines_per_row=(registers[9] & 0x07) + 1,
        )

    def address(self, row: int, line: int, byte_column: int) -> int:
        """Return the address of a byte (character row, scan line in the row, byte column)."""
        within_page = (self.offset + row * self.bytes_per_line + byte_column) & 0x07FF
        return (self.base + within_page + line * 0x0800) & 0xFFFF


def pixel_pens(value: int, mode: int) -> list[int]:
    """Return the pens of the pixels in one screen byte, left to right."""
    if mode == 2:
        return [(value >> (7 - n)) & 1 for n in range(8)]
    if mode == 1:
        return [((value >> (7 - n)) & 1) | (((value >> (3 - n)) & 1) << 1) for n in range(4)]
    pens = []
    for n in range(2):  # modes 0 and 3
        bits = [(value >> (7 - n)) & 1, (value >> (3 - n)) & 1,
                (value >> (5 - n)) & 1, (value >> (1 - n)) & 1]  # fmt: skip
        pens.append(bits[0] | bits[1] << 1 | bits[2] << 2 | bits[3] << 3)
    return pens


def font_lookup(font: bytes) -> dict[bytes, str]:
    """Map 8-byte glyph bitmaps to characters; printable ASCII wins over other codes."""
    lookup: dict[bytes, str] = {}
    codes = [*range(32, 127), *range(0, 32), *range(127, 256)]
    for code in codes:
        glyph = font[code * 8 : code * 8 + 8]
        lookup.setdefault(
            glyph, _SPECIAL_CHARACTERS.get(code, chr(code) if 32 <= code < 127 else "?")
        )
    return lookup


def cell_pens(
    memory_byte: callable, geometry: ScreenGeometry, mode: int, row: int, column: int
) -> list[list[int]]:  # type: ignore[valid-type]
    """Return the 8x8 grid of pens of one character cell (scan lines 0-7)."""
    cell_bytes = CELL_BYTES[mode]
    grid = []
    for line in range(8):
        pens: list[int] = []
        for byte_index in range(cell_bytes):
            address = geometry.address(row, line, column * cell_bytes + byte_index)
            pens += pixel_pens(memory_byte(address), mode)
        grid.append(pens)
    return grid


def recognise_cell(grid: list[list[int]], lookup: dict[bytes, str]) -> str:
    """Return the character in a cell, trying each pen as paper; "?" if unknown."""
    width = len(grid[0])
    step = width // 8  # mode 0 cells are 8 pixels wide too; mode 2 cells 8; mode 1 8
    candidates = sorted({pen for line in grid for pen in line})
    for paper in candidates:
        glyph = bytes(
            sum(1 << (7 - n) for n in range(8) if line[n * step] != paper) for line in grid
        )
        if glyph in lookup:
            return lookup[glyph]
    return "?"


def screen_text(
    memory_byte: callable,  # type: ignore[valid-type]
    font: bytes,
    crtc_registers: list[int],
    mode: int,
) -> list[str]:
    """Recognise the text on the screen.

    Args:
        memory_byte: Reads a RAM byte (the screen is always RAM).
        font: The 2 KB character matrix (256 x 8 bytes) from the OS ROM.
        crtc_registers: CRTC R0-R17.
        mode: Gate Array screen mode.

    Returns:
        One string per character row, trailing spaces removed.
    """
    geometry = ScreenGeometry.from_crtc(crtc_registers)
    if geometry.bytes_per_line == 0 or geometry.rows == 0:
        return []
    lookup = font_lookup(font)
    columns = geometry.bytes_per_line // CELL_BYTES[mode]
    lines = []
    for row in range(geometry.rows):
        text = "".join(
            recognise_cell(cell_pens(memory_byte, geometry, mode, row, column), lookup)
            for column in range(columns)
        )
        lines.append(text.rstrip())
    return lines
