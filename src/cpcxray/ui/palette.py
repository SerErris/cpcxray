"""The CPC colour palette: hardware colour numbers to RGB (design.md §5.4, §6.12).

The Gate Array takes *hardware* colour numbers 0-31 (written as &40-&5F with the function
bits). BASIC and the firmware use *firmware* colour numbers 0-26. Each colour channel has three
levels (off, half, full), and the firmware number encodes them in base 3:

    firmware colour = 9 * green + 3 * red + blue      (each 0, 1 or 2)

so the RGB value of a firmware colour is computed, not tabulated. The firmware's own table
(at &0D93 in the 464 OS ROM, used by SCR INK ENCODE) maps firmware colour n to its 5-bit
hardware number; it is reproduced in `FIRMWARE_TO_HARDWARE`, and tests/ui checks it against
the ROM. The ROM table continues with the five hardware numbers not used by any firmware
colour (1, 8, 9, 16, 17); they duplicate other colours: 1 white, 8 purple, 9 pastel yellow,
16 blue, 17 sea green.
"""

from __future__ import annotations

CHANNEL_LEVELS = (0x00, 0x80, 0xFF)  # off, half, full

# 5-bit hardware colour numbers for firmware colours 0-26 (as in the OS ROM at &0D93).
FIRMWARE_TO_HARDWARE = (
    0x14, 0x04, 0x15, 0x1C, 0x18, 0x1D, 0x0C, 0x05, 0x0D,
    0x16, 0x06, 0x17, 0x1E, 0x00, 0x1F, 0x0E, 0x07, 0x0F,
    0x12, 0x02, 0x13, 0x1A, 0x19, 0x1B, 0x0A, 0x03, 0x0B,
)  # fmt: skip
DUPLICATE_HARDWARE = {0x01: 13, 0x08: 7, 0x09: 25, 0x10: 1, 0x11: 19}  # → firmware colour
OS_ROM_COLOUR_TABLE = 0x0D93  # where the 464 OS ROM keeps the same table (+ the duplicates)

FIRMWARE_NAMES = (
    "black", "blue", "bright blue", "red", "magenta", "mauve", "bright red", "purple",
    "bright magenta", "green", "cyan", "sky blue", "yellow", "white", "pastel blue", "orange",
    "pink", "pastel magenta", "bright green", "sea green", "bright cyan", "lime", "pastel green",
    "pastel cyan", "bright yellow", "pastel yellow", "bright white",
)  # fmt: skip


def firmware_rgb(colour: int) -> tuple[int, int, int]:
    """Return (red, green, blue) of firmware colour 0-26."""
    green, rest = divmod(colour, 9)
    red, blue = divmod(rest, 3)
    return CHANNEL_LEVELS[red], CHANNEL_LEVELS[green], CHANNEL_LEVELS[blue]


def _hardware_to_firmware() -> tuple[int, ...]:
    """Build the inverse table: hardware number 0-31 → firmware colour."""
    table = [0] * 32
    for firmware, hardware in enumerate(FIRMWARE_TO_HARDWARE):
        table[hardware] = firmware
    for hardware, firmware in DUPLICATE_HARDWARE.items():
        table[hardware] = firmware
    return tuple(table)


HARDWARE_TO_FIRMWARE: tuple[int, ...] = _hardware_to_firmware()
"""Firmware colour for each hardware colour number 0-31."""

HARDWARE_RGB: tuple[tuple[int, int, int], ...] = tuple(
    firmware_rgb(firmware) for firmware in HARDWARE_TO_FIRMWARE
)
"""(red, green, blue) for each hardware colour number 0-31."""
