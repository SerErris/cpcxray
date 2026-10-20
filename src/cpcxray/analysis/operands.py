"""Is a 16-bit immediate an address or a number? (source generator labels, design.md §6.15).

`ld bc,#0016` and `ld hl,#0016` look alike, but only an address should become a label: a
counter shown as `JP_DE` misleads the reader (the bytes are the same either way). Jump and call
targets and memory operands such as `ld hl,(nn)` are always addresses; this module judges the
immediate loads `ld rr,nn` (rr = BC, DE, HL, SP, IX, IY).

The judgement follows the straight-line code after the load (`LOOKAHEAD` instructions, stopping
at any jump, call or return) and watches the loaded register pair:

* block instructions: LDIR/LDDR/LDI/LDD and CPIR/CPDR/CPI/CPD use HL (and DE) as addresses and
  BC as a counter; INIR/OTIR/... use HL as an address and B as a counter;
* DJNZ, or DEC BC (the classic `dec bc / ld a,b / or c` loop), make BC a counter;
* an access through the pair - `(hl)`, `(de)`, `(bc)`, `(ix+d)`, `jp (hl)` - makes it an address;
* `ex de,hl` swaps the watched pair; loading it again (`ld h,…`, `pop hl`, …) ends the search.

`ld sp,nn` is always an address. Without evidence the result is UNKNOWN, and the caller labels
the value only where it is clearly an address (a line start of the source, the target's own
address range, the firmware jumpblock area).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from enum import Enum

from cpcxray.cpu.disasm import Flow, Instruction

LOOKAHEAD = 8
IMMEDIATE_LOAD = re.compile(r"^ld (bc|de|hl|sp|ix|iy),\{word\}$")
BLOCK_COPY = frozenset({"ldir", "lddr", "ldi", "ldd", "cpir", "cpdr", "cpi", "cpd"})
BLOCK_IO = frozenset({"inir", "indr", "otir", "otdr", "ini", "ind", "outi", "outd"})
_HALVES = {"bc": ("b", "c"), "de": ("d", "e"), "hl": ("h", "l"), "ix": ("ixh", "ixl"),
           "iy": ("iyh", "iyl"), "sp": ()}  # fmt: skip


class Role(Enum):
    """What a 16-bit immediate is used as."""

    ADDRESS = "address"
    NUMBER = "number"
    UNKNOWN = "unknown"


def loaded_pair(instruction: Instruction) -> str | None:
    """The register pair of an immediate 16-bit load (`ld hl,nn` → "hl"), else None."""
    match = IMMEDIATE_LOAD.match(instruction.template)
    return match[1] if match else None


def immediate_role(instruction: Instruction, following: Callable[[int], Instruction]) -> Role:
    """Judge the immediate of `ld rr,nn` from the code after it (see the module docstring).

    Args:
        instruction: The load.
        following: Decodes the instruction at an address (same address space as the load).

    Returns:
        ADDRESS, NUMBER, or UNKNOWN (also for instructions that are no immediate load).
    """
    pair = loaded_pair(instruction)
    if pair is None:
        return Role.UNKNOWN
    if pair == "sp":
        return Role.ADDRESS
    address = instruction.next_address
    for _ in range(LOOKAHEAD):
        current = following(address)
        role, pair = _effect(current.text(), pair)
        if role is not None:
            return role
        if pair is None or current.flow != Flow.SEQUENTIAL:
            return Role.UNKNOWN
        address = current.next_address
    return Role.UNKNOWN


def _effect(text: str, pair: str) -> tuple[Role | None, str | None]:  # pylint: disable=too-many-return-statements  # one rule per case
    """What one instruction says about the watched pair: (role or None, pair after it).

    The pair after it is None when the instruction overwrites the pair (search ends).
    """
    if text in BLOCK_COPY:
        if pair in ("hl", "de"):
            return Role.ADDRESS, pair
        return (Role.NUMBER if pair == "bc" else None), pair
    if text in BLOCK_IO:
        return (Role.ADDRESS if pair == "hl" else Role.NUMBER if pair == "bc" else None), pair
    if pair == "bc" and (text.startswith("djnz") or text == "dec bc"):
        return Role.NUMBER, pair
    indexed = pair in ("ix", "iy") and (f"({pair}+" in text or f"({pair}-" in text)
    if f"({pair})" in text or indexed:
        return Role.ADDRESS, pair
    if text == "ex de,hl" and pair in ("de", "hl"):
        return None, "hl" if pair == "de" else "de"
    if _overwrites(text, pair):
        return None, None
    return None, pair


def _overwrites(text: str, pair: str) -> bool:
    """True if the instruction loads a new value into the pair or one of its halves."""
    if text == "exx":  # BC, DE, HL swapped with the second set
        return pair in ("bc", "de", "hl")
    if text.startswith("ex (sp),"):
        return text.endswith(pair)
    if text.startswith(("ld ", "pop ", "in ")):
        destination = text.split(" ", 1)[1].split(",", 1)[0]
        return destination in (pair, *_HALVES[pair])
    return False
