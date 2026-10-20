"""Instruction timing in CPC "NOPs" (1 NOP = 1 µs = 4 T-states), D-04.

On the CPC the Gate Array stretches every instruction to a whole number of NOPs. These tables
give that cost per opcode; conditional instructions have a separate cost when the condition is
taken, repeating block instructions when they repeat. The handlers in `cpcxray.cpu.z80` look
the costs up once, when they are built, and return them from every execution.

Source: "Z80 timings on Amstrad CPC - Cheat sheet" V1.3 (2023-07-09, CPCWiki, measured
values from cpctech instrtim.html, listed in design.md §10):
https://www.cpcwiki.eu/imgs/b/b4/Z80_CPC_Timings_cheat_sheet.20230709.pdf
Every entry was checked against it; `tests/cpu/test_z80.py::test_timing` pins one case per
sheet entry. Exact values only matter for delay loops and interrupt spacing; the emulator is not
cycle exact (NFR-03).
"""

from __future__ import annotations

# Unprefixed opcodes, indexed by opcode; for conditional instructions the not-taken
# cost. Prefix bytes (CB, DD, ED, FD) are 0: their cost comes from the prefixed tables.
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
    """Return the cost of a CB-prefixed opcode.

    Args:
        op: The opcode byte after CB.

    Returns:
        2 for register operands, 3 for BIT n,(HL), 4 for rotate/shift/RES/SET (HL).
    """
    if op & 7 != 6:
        return 2
    return 3 if op >> 6 == 1 else 4  # BIT b,(HL) / rotate, RES, SET (HL)


def index_cb(op: int) -> int:
    """Return the cost of DD CB d op / FD CB d op.

    Args:
        op: The final opcode byte.

    Returns:
        6 for BIT n,(IX+d), 7 for all other forms.
    """
    return 6 if op >> 6 == 1 else 7


# ED &40-&7F by bits 2-0 of the opcode: IN r,(C), OUT (C),r, ADC/SBC HL,rr, LD (nn),rr /
# LD rr,(nn), NEG, RETN/RETI, IM; bits 2-0 = 7 is split by bits 5-3 (_ED_GROUP1_COLUMN7).
_ED_GROUP1_BY_BITS_2_0 = (4, 4, 4, 6, 2, 4, 2)
# ED &47-&7F with bits 2-0 = 7, by bits 5-3: LD I,A, LD R,A, LD A,I, LD A,R, RRD, RLD, NOP, NOP
_ED_GROUP1_COLUMN7 = (3, 3, 3, 3, 5, 5, 2, 2)


def _ed(opcode: int) -> int:
    """Return the cost of an ED-prefixed opcode (used to build `ED`).

    Args:
        opcode: The opcode byte after ED.

    Returns:
        The cost in NOPs; block instructions give the cost of their final iteration.
        Undefined ED opcodes act as 2-NOP no-ops.
    """
    bits_5_3, bits_2_0 = (opcode >> 3) & 7, opcode & 7
    if 0x40 <= opcode < 0x80:
        if bits_2_0 == 7:
            return _ED_GROUP1_COLUMN7[bits_5_3]
        return _ED_GROUP1_BY_BITS_2_0[bits_2_0]
    if 0xA0 <= opcode <= 0xBB and bits_2_0 <= 3:
        return 5  # LDI CPI INI OUTI, D variants, IR/DR forms on their last iteration
    return 2


ED: tuple[int, ...] = tuple(_ed(opcode) for opcode in range(256))
ED_REPEAT = 6  # LDIR, CPIR, INIR, OTIR and decrementing variants while repeating

# Opcodes whose (HL) operand becomes (IX+d)/(IY+d) under a DD/FD prefix.
INDEXED_MEMORY: frozenset[int] = frozenset(
    {0x34, 0x35, 0x36}
    | {0x46 + 8 * y for y in range(8) if y != 6}
    | {0x70 + z for z in range(8) if z != 6}
    | {0x86 + 8 * y for y in range(8)}
)


def index_extra(op: int) -> int:
    """Return the extra cost of a DD/FD prefix on top of the unprefixed opcode.

    Args:
        op: The opcode byte after DD/FD.

    Returns:
        3 when (HL) becomes (IX+d)/(IY+d) (displacement read and address calculation),
        2 for POP IX/IY (measured: 5 NOPs against 3 for POP HL), otherwise 1 (the prefix).
    """
    if op == 0xE1:
        return 2
    return 3 if op in INDEXED_MEMORY else 1


# Interrupt acceptance (instruction boundary only).
INT_IM0 = 5  # acknowledge plus the RST taken from the data bus
INT_IM1 = 5  # acknowledge plus call to &0038 (the CPC firmware uses IM 1)
INT_IM2 = 7  # acknowledge, push, read the vector from I:data (see note below)
# The cheat sheet lists "Mode 2: 19", which matches the 19 T-states of an IM 2 response
# rather than NOPs; 7 NOPs is an estimate (19 T-states plus wait-state alignment). The CPC
# firmware runs in IM 1, so this value only matters for programs that switch to IM 2.
NMI = 4  # call to &0066
HALT_IDLE = 1  # one NOP per step while halted
