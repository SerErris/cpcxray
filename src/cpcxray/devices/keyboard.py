"""The CPC keyboard matrix (design.md §6.9).

Ten rows of eight keys; a 0 bit means "pressed", so an idle row reads &FF. The firmware scans
the rows itself through the PSG's I/O port (see system_io.py), which is why the matrix is
emulated rather than the firmware keyboard calls (D-07). Host key mapping and typing text into
the emulator follow in phase 6.
"""

from __future__ import annotations

ROWS = 10


class Keyboard:
    """The key matrix.

    Attributes:
        rows: One byte per row, bit = 0 for a pressed key.
    """

    def __init__(self) -> None:
        """Create the matrix with no key pressed."""
        self.rows = bytearray([0xFF] * ROWS)

    def reset(self) -> None:
        """Release all keys."""
        self.rows[:] = bytes([0xFF] * ROWS)

    def press(self, row: int, bit: int) -> None:
        """Press the key at (row, bit)."""
        self.rows[row] &= ~(1 << bit) & 0xFF

    def release(self, row: int, bit: int) -> None:
        """Release the key at (row, bit)."""
        self.rows[row] |= 1 << bit

    def read_row(self, row: int) -> int:
        """Return a row's bits; rows above 9 read &FF (nothing connected)."""
        return self.rows[row] if row < ROWS else 0xFF
