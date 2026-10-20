"""Flag bits and flag lookup tables for the Z80 core."""

from __future__ import annotations

CF = 0x01  # carry
NF = 0x02  # add/subtract
PF = 0x04  # parity/overflow
XF = 0x08  # undocumented, bit 3 of the result
HF = 0x10  # half carry
YF = 0x20  # undocumented, bit 5 of the result
ZF = 0x40  # zero
SF = 0x80  # sign
XYF = XF | YF


def _parity(v: int) -> int:
    return PF if bin(v).count("1") % 2 == 0 else 0


# S, Z, X, Y of an 8-bit result
SZ: tuple[int, ...] = tuple((v & (SF | XYF)) | (ZF if v == 0 else 0) for v in range(256))
# S, Z, X, Y and parity of an 8-bit result
SZP: tuple[int, ...] = tuple(SZ[v] | _parity(v) for v in range(256))
PARITY: tuple[int, ...] = tuple(_parity(v) for v in range(256))
