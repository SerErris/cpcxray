"""Tests of palette, renderer and PNG encoder (design.md §5.4, §6.12, FR-DBG-01)."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np
import pytest

from cpcxray.ui.palette import (
    FIRMWARE_TO_HARDWARE,
    HARDWARE_RGB,
    HARDWARE_TO_FIRMWARE,
    OS_ROM_COLOUR_TABLE,
    firmware_rgb,
)
from cpcxray.ui.png import encode_png
from cpcxray.ui.renderer import HEIGHT, WIDTH, ScreenSnapshot, double_lines, render

OS_ROM = Path(__file__).resolve().parents[2] / "roms" / "os464.rom"
BRIGHT_RED, BRIGHT_YELLOW, BLUE = 0x0C, 0x0A, 0x04  # hardware colour numbers


def snapshot(
    ram: bytearray | None = None,
    mode: int = 1,
    inks: dict[int, int] | None = None,
    crtc: dict[int, int] | None = None,
) -> ScreenSnapshot:
    """Build a snapshot with the firmware's default screen layout (screen at &C000).

    Args:
        ram: Screen contents (default all zero).
        mode: Screen mode.
        inks: Hardware colours per pen (16 = border); others are blue.
        crtc: CRTC register overrides, e.g. {13: &28} to scroll.
    """
    all_inks = [BLUE] * 17
    for pen, colour in (inks or {}).items():
        all_inks[pen] = colour
    registers = [0] * 18
    registers[1], registers[6], registers[9], registers[12] = 40, 25, 7, 0x30
    for register, value in (crtc or {}).items():
        registers[register] = value
    return ScreenSnapshot(bytes(ram or bytearray(0x10000)), mode, tuple(all_inks), tuple(registers))


def rgba(hardware: int) -> list[int]:
    """The RGBA value of a hardware colour."""
    return [*HARDWARE_RGB[hardware], 255]


def test_palette_matches_os_rom_table() -> None:
    """The firmware→hardware table equals the one in the 464 OS ROM (&0D93)."""
    if not OS_ROM.exists():
        pytest.skip("OS ROM missing")
    rom = OS_ROM.read_bytes()
    assert rom[OS_ROM_COLOUR_TABLE : OS_ROM_COLOUR_TABLE + 27] == bytes(FIRMWARE_TO_HARDWARE)
    assert set(rom[OS_ROM_COLOUR_TABLE + 27 : OS_ROM_COLOUR_TABLE + 32]) == {1, 8, 9, 16, 17}


def test_palette_levels() -> None:
    """Firmware colours are base-3 G/R/B levels; defaults blue/bright yellow are right."""
    assert firmware_rgb(0) == (0, 0, 0) and firmware_rgb(26) == (255, 255, 255)
    assert firmware_rgb(13) == (128, 128, 128)  # white is 50 % grey
    assert HARDWARE_RGB[BLUE] == (0, 0, 128) and HARDWARE_RGB[BRIGHT_YELLOW] == (255, 255, 0)
    assert len(set(HARDWARE_TO_FIRMWARE)) == 27  # 32 hardware numbers, 27 distinct colours


def test_border_and_paper() -> None:
    """The border surrounds the 640 x 200 display area, filled with pen 0."""
    image = render(snapshot(inks={16: BRIGHT_RED, 0: BLUE}))
    assert image.shape == (HEIGHT, WIDTH, 4)
    assert image[0, 0].tolist() == rgba(BRIGHT_RED)
    top, left = (HEIGHT - 200) // 2, (WIDTH - 640) // 2
    assert image[top, left].tolist() == rgba(BLUE)
    assert image[top - 1, left].tolist() == rgba(BRIGHT_RED)
    assert image[top, left + 640].tolist() == rgba(BRIGHT_RED)


@pytest.mark.parametrize(
    ("mode", "value", "pixel_width", "pens"),
    [(2, 0b1000_0001, 1, [1, 0, 0, 0, 0, 0, 0, 1]),
     (1, 0b1000_1000, 2, [3, 0, 0, 0]),  # bits 7 and 3: pixel 0 = pen 3
     (1, 0b0100_0000, 2, [0, 1, 0, 0]),
     (0, 0b1000_0000, 4, [1, 0]),  # bit 7 is pen bit 0 of pixel 0
     (0, 0b0000_0010, 4, [8, 0])],  # bit 1 is pen bit 3 of pixel 0
)  # fmt: skip
def test_pixel_layout_per_mode(mode: int, value: int, pixel_width: int, pens: list[int]) -> None:
    """The first screen byte decodes into the right pens and pixel widths for each mode."""
    ram = bytearray(0x10000)
    ram[0xC000] = value
    inks = {pen: pen + 4 for pen in range(16)}  # every pen its own colour
    image = render(snapshot(ram, mode, inks))
    top, left = (HEIGHT - 200) // 2, (WIDTH - 640) // 2
    for index, pen in enumerate(pens):
        for offset in range(pixel_width):
            assert image[top, left + index * pixel_width + offset].tolist() == rgba(pen + 4)


def test_scan_lines_and_rows() -> None:
    """Scan line s of character row r comes from base + r * 80 + s * &800 (§5.4)."""
    ram = bytearray(0x10000)
    ram[0xC000 + 1 * 80 + 3 * 0x800] = 0xFF  # row 1, scan line 3, byte 0
    image = render(snapshot(ram, mode=2, inks={1: BRIGHT_YELLOW}))
    top, left = (HEIGHT - 200) // 2, (WIDTH - 640) // 2
    assert image[top + 8 + 3, left].tolist() == rgba(BRIGHT_YELLOW)
    assert image[top + 8 + 2, left].tolist() == rgba(BLUE)


def test_hardware_scroll() -> None:
    """R12/R13 move the screen start: content at &C000 + 2 * offset appears top left."""
    ram = bytearray(0x10000)
    ram[0xC000 + 2 * 0x28] = 0xFF  # byte at offset 2 * &28 (one 80-byte line)
    image = render(snapshot(ram, mode=2, inks={1: BRIGHT_YELLOW}, crtc={13: 0x28}))
    top, left = (HEIGHT - 200) // 2, (WIDTH - 640) // 2
    assert image[top, left].tolist() == rgba(BRIGHT_YELLOW)


def test_smaller_screen_from_crtc() -> None:
    """R1/R6 set the size of the display area; the rest is border."""
    image = render(snapshot(inks={16: BRIGHT_RED}, crtc={1: 32, 6: 20}))
    top, left = (HEIGHT - 160) // 2, (WIDTH - 512) // 2
    assert image[top, left].tolist() == rgba(BLUE)
    assert image[top, left - 1].tolist() == rgba(BRIGHT_RED)


def test_double_lines_and_png() -> None:
    """Doubling gives 768 x 544; the PNG decodes back to the same pixels."""
    image = double_lines(render(snapshot(inks={16: BRIGHT_RED})))
    assert image.shape == (544, 768, 4)
    png = encode_png(image)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", png[16:24])
    idat_length = struct.unpack(">I", png[33:37])[0]
    rows = zlib.decompress(png[41 : 41 + idat_length])
    decoded = np.frombuffer(rows, dtype=np.uint8).reshape(height, 1 + width * 4)[:, 1:]
    assert (width, height) == (768, 544)
    assert np.array_equal(decoded.reshape(height, width, 4), image)
