"""Instruction timing in CPC "NOPs" (1 NOP = 1 µs = 4 T-states), D-04.

On the CPC the Gate Array stretches every instruction to a whole number of NOPs. These tables
give that cost per opcode; conditional instructions have a separate cost when the condition is
taken, repeating block instructions when they repeat. The values follow the commonly published
CPC timing tables; the emulator only needs them to be good enough for the firmware (NFR-03).
"""

from __future__ import annotations

# Unprefixed opcodes; for conditional instructions the not-taken cost.
# fmt: off
MAIN: tuple[int, ...] = (
    # 0 1  2  3  4  5  6  7  8  9  A  B  C  D  E  F
    1, 3, 2, 2, 1, 1, 2, 1, 1, 3, 2, 2, 1, 1, 2, 1,  # 0x
    3, 3, 2, 2, 1, 1, 2, 1, 3, 3, 2, 2, 1, 1, 2, 1,  # 1x
    2, 3, 5, 2, 1, 1, 2, 1, 2, 3, 5, 2, 1, 1, 2, 1,  # 2x
    2, 3, 4, 2, 3, 3, 3, 1, 2, 3, 4, 2, 1, 1, 2, 1,  # 3x
    1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 2, 1,  # 4x
    1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 2, 1,  # 5x
    1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 2, 1,  # 6x
    2, 2, 2, 2, 2, 2, 1, 2, 1, 1, 1, 1, 1, 1, 2, 1,  # 7x
    1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 2, 1,  # 8x
    1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 2, 1,  # 9x
    1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 2, 1,  # Ax
    1, 1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1, 2, 1,  # Bx
    2, 3, 3, 3, 3, 4, 2, 4, 2, 3, 3, 0, 3, 5, 2, 4,  # Cx
    2, 3, 3, 3, 3, 4, 2, 4, 2, 1, 3, 3, 3, 0, 2, 4,  # Dx
    2, 3, 3, 6, 3, 4, 2, 4, 2, 1, 3, 1, 3, 0, 2, 4,  # Ex
    2, 3, 3, 1, 3, 4, 2, 4, 2, 2, 3, 1, 3, 0, 2, 4,  # Fx
)
# fmt: on

# Cost when the condition is taken: DJNZ, JR cc, RET cc, CALL cc.
MAIN_TAKEN: dict[int, int] = {0x10: 4, 0x20: 3, 0x28: 3, 0x30: 3, 0x38: 3}
for _op in range(0xC0, 0x100, 8):
    MAIN_TAKEN[_op] = 4  # RET cc
    MAIN_TAKEN[_op + 4] = 5  # CALL cc


def cb(op: int) -> int:
    """CB-prefixed opcodes."""
    if op & 7 != 6:
        return 2
    return 3 if op >> 6 == 1 else 4  # BIT b,(HL) / rotate, RES, SET (HL)


def index_cb(op: int) -> int:
    """DD CB d op / FD CB d op."""
    return 6 if op >> 6 == 1 else 7


def _ed(op: int) -> int:
    if 0x40 <= op < 0x80:
        z = op & 7
        if z in (0, 1, 2):  # IN r,(C) / OUT (C),r / ADC/SBC HL,rr
            return 4
        if z == 3:  # LD (nn),rr / LD rr,(nn)
            return 6
        if z == 5:  # RETN / RETI
            return 4
        if z == 7:
            y = (op >> 3) & 7
            return (3, 3, 3, 3, 5, 5, 2, 2)[y]  # LD I,A .. LD A,R, RRD, RLD, NOP
        return 2  # NEG, IM
    if op in (0xA0, 0xA2, 0xA3, 0xA8, 0xAA, 0xAB, 0xB0, 0xB2, 0xB3, 0xB8, 0xBA, 0xBB):
        return 5  # LDI/INI/OUTI and variants, block forms when they finish
    if op in (0xA1, 0xA9, 0xB1, 0xB9):
        return 4  # CPI/CPD, CPIR/CPDR when they finish
    return 2  # undefined ED opcodes act as 2-NOP no-ops


ED: tuple[int, ...] = tuple(_ed(op) for op in range(256))
ED_REPEAT = 6  # LDIR, CPIR, INIR, OTIR and decrementing variants while repeating

# Opcodes whose (HL) operand becomes (IX+d)/(IY+d) under a DD/FD prefix.
INDEXED_MEMORY: frozenset[int] = frozenset(
    {0x34, 0x35, 0x36}
    | {0x46 + 8 * y for y in range(8) if y != 6}
    | {0x70 + z for z in range(8) if z != 6}
    | {0x86 + 8 * y for y in range(8)}
)


def index_extra(op: int) -> int:
    """Extra cost of a DD/FD prefix on top of the unprefixed opcode."""
    return 3 if op in INDEXED_MEMORY else 1


# Interrupt acceptance (instruction boundary only).
INT_IM0 = 5  # plus nothing: the RST on the data bus is included
INT_IM1 = 5
INT_IM2 = 7
NMI = 4
HALT_IDLE = 1  # one NOP per step while halted
