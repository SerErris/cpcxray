"""Tests of the address-or-number judgement of immediate loads (analysis/operands.py)."""

from __future__ import annotations

import pytest

from cpcxray.analysis.operands import Role, immediate_role
from cpcxray.cpu.disasm import Instruction, disassemble


def judge(code: str) -> Role:
    """Judge the `ld rr,nn` at address 0 of a hex program."""
    data = bytes.fromhex(code) + bytes(16)

    def following(address: int) -> Instruction:
        """Decode the program at an address."""
        return disassemble(lambda a: data[a], address)

    return immediate_role(following(0), following)


@pytest.mark.parametrize(
    ("code", "role"),
    [
        ("21 00 C0  11 00 40  01 16 00  ED B0", Role.ADDRESS),  # ld hl / ld de / ld bc / ldir
        ("11 00 40  21 00 C0  01 16 00  ED B0", Role.ADDRESS),  # DE of the copy
        ("01 16 00  21 00 C0  11 00 40  ED B0", Role.NUMBER),  # BC: the length
        ("01 16 00  0B  78  B1  20 FB", Role.NUMBER),  # dec bc / ld a,b / or c loop
        ("01 05 00  10 FE", Role.NUMBER),  # B counts DJNZ
        ("21 00 40  7E", Role.ADDRESS),  # ld a,(hl)
        ("11 00 40  1A", Role.ADDRESS),  # ld a,(de)
        ("21 00 40  EB  1A", Role.ADDRESS),  # ex de,hl: now DE points
        ("DD 21 00 40  DD 7E 05", Role.ADDRESS),  # ld a,(ix+5)
        ("21 00 40  E9", Role.ADDRESS),  # jp (hl)
        ("31 00 C0", Role.ADDRESS),  # ld sp,nn
        ("21 16 00  26 00  7E", Role.UNKNOWN),  # ld h,0 overwrites HL first
        ("21 16 00  C9", Role.UNKNOWN),  # ret: nothing known
        ("21 16 00  19", Role.UNKNOWN),  # add hl,de: arithmetic, no evidence
        ("3E 05", Role.UNKNOWN),  # not an immediate 16-bit load
    ],
)
def test_roles(code: str, role: Role) -> None:
    """Block moves, loops and accesses through the pair decide; overwrites end the search."""
    assert judge(code) is role
