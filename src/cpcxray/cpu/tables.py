"""Flag bits and flag lookup tables for the Z80 core.

The F register layout (bit 7 … bit 0) is S Z Y H X P/V N C. X and Y (bits 3 and 5) are the
undocumented flags; most instructions copy them from bits 3 and 5 of their result.
"""

from __future__ import annotations

FLAG_C = 0x01  # carry
FLAG_N = 0x02  # add/subtract (set by subtractions, used by DAA)
FLAG_PV = 0x04  # parity/overflow
FLAG_X = 0x08  # undocumented, usually bit 3 of the result
FLAG_H = 0x10  # half carry (carry/borrow between bits 3 and 4)
FLAG_Y = 0x20  # undocumented, usually bit 5 of the result
FLAG_Z = 0x40  # zero
FLAG_S = 0x80  # sign (bit 7 of the result)
FLAGS_XY = FLAG_X | FLAG_Y


def _parity_flag(value: int) -> int:
    """Return FLAG_PV if `value` has an even number of set bits (Z80 parity), else 0."""
    return FLAG_PV if bin(value).count("1") % 2 == 0 else 0


SIGN_ZERO_XY: tuple[int, ...] = tuple(
    (result & (FLAG_S | FLAGS_XY)) | (FLAG_Z if result == 0 else 0) for result in range(256)
)
"""S, Z, X, Y flags of an 8-bit result, indexed by the result."""

SIGN_ZERO_XY_PARITY: tuple[int, ...] = tuple(
    SIGN_ZERO_XY[result] | _parity_flag(result) for result in range(256)
)
"""S, Z, X, Y and parity flags of an 8-bit result, indexed by the result."""

PARITY: tuple[int, ...] = tuple(_parity_flag(value) for value in range(256))
"""FLAG_PV if the index has even parity, else 0."""
