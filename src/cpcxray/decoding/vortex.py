"""The Vortex ROM scrambling rule (design.md §5.5; docs/vortex_decrypt.md, normative part).

The VDOS ROM stores opcodes in clear and operands/data scrambled. The hardware decodes a byte
read *without* M1 by XOR with a mask that depends on two address bits:

| A4 | A2 | mask |
|----|----|------|
| 0  | 0  | &00  |
| 0  | 1  | &20  |
| 1  | 0  | &08  |
| 1  | 1  | &28  |

The rule is stateless and its own inverse; only the low address bits matter, so ROM offsets and
CPU addresses (&C000 + offset) give the same result. Whether a read is an M1 fetch is decided
by the ROM slot, not here (D-11).
"""

from __future__ import annotations

# mask per value of the low 5 address bits (the pattern repeats every 32 bytes)
VORTEX_MASKS: tuple[int, ...] = tuple(
    (0x20 if offset & 0x04 else 0) | (0x08 if offset & 0x10 else 0) for offset in range(32)
)


def vortex_mask(address: int) -> int:
    """Return the XOR mask for `address` (&00, &08, &20 or &28)."""
    return VORTEX_MASKS[address & 0x1F]


def vortex_decode(address: int, raw: int) -> int:
    """Decode (or encode - the rule is its own inverse) a byte read without M1."""
    return raw ^ VORTEX_MASKS[address & 0x1F]


DECODERS = {"vortex": vortex_decode}
"""Decoders by the name used in machine.toml (`decoder = "vortex"`)."""
