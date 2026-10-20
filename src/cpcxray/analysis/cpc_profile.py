"""Amstrad CPC disassembly profile: the firmware RST conventions (design.md §5.6, §6.15, D-30).

The CPC firmware uses the restart instructions as compact calls. RST 1, 2, 3 and 5 are
followed by a 16-bit inline operand that the firmware handler reads as data (`ld e,(hl)` /
`ld d,(hl)`); it is never code. This profile tells the disassembler how many inline bytes
follow, how the RST affects the program flow, and how to explain the operand:

    rst #08             ; LOW JUMP
    defw 08150h         ;   to #0150 [lower ROM on, upper ROM off]
    rst #10             ; SIDE CALL
    defw 04150h         ;   to #C150 [slot 1]
    rst #18             ; FAR CALL
    defw far_c7f3       ;   to SCR_SET_MODE [ROM 7]
    rst #28             ; FIRM JUMP
    defw sub_0038       ;   to #0038

RST 1 operand bits 15-14 follow the 464 OS ROM, not docs/cpc_rst.md (§5.6): bit 15 set
disables the upper ROM, bit 14 set disables the lower ROM.
"""

from __future__ import annotations

from dataclasses import dataclass

from cpcxray.cpu.disasm import (
    ByteReader,
    ExtraLine,
    Flow,
    RestartDecoding,
    SymbolLookup,
    hex_word,
)


@dataclass(frozen=True)
class RestartSpec:
    """One row of the CPC RST table (design.md §6.15).

    Attributes:
        number: RST number 0-7 (the call address is number * 8).
        name: Firmware name, used as the comment of the `rst` line.
        inline_length: Bytes of inline data after the opcode.
        flow: Effect on the program flow (used by stepping and the static classifier).
    """

    number: int
    name: str
    inline_length: int
    flow: Flow


RESTARTS = (
    RestartSpec(0, "RESET", 0, Flow.NO_RETURN),
    RestartSpec(1, "LOW JUMP", 2, Flow.JUMP),
    RestartSpec(2, "SIDE CALL", 2, Flow.CALL),
    RestartSpec(3, "FAR CALL", 2, Flow.CALL),
    RestartSpec(4, "RAM LAM", 0, Flow.SEQUENTIAL),
    RestartSpec(5, "FIRM JUMP", 2, Flow.JUMP),
    RestartSpec(6, "USER RESTART", 0, Flow.SEQUENTIAL),  # default; configurable per profile
    RestartSpec(7, "INTERRUPT", 0, Flow.NO_RETURN),  # must not be executed by programs
)

# RST 1 operand bits 15-14 → ROM state in words (§5.6; 1 = disabled)
_LOW_JUMP_ROM_STATES = (
    "lower ROM on, upper ROM on",  # 00
    "lower ROM off, upper ROM on",  # 01: bit 14 disables the lower ROM
    "lower ROM on, upper ROM off",  # 10: bit 15 disables the upper ROM
    "lower ROM off, upper ROM off",  # 11
)


def rom_select_text(select: int) -> str:
    """Explain the ROM select byte of a far address (RST 3).

    Args:
        select: 0-251 selects that upper ROM; 252-255 only set the ROM enables.

    Returns:
        "ROM n", or the enable state such as "UR=on, LR=off".
    """
    if select < 252:
        return f"ROM {select}"
    upper = "on" if select in (252, 253) else "off"
    lower = "on" if select in (252, 254) else "off"
    return f"UR={upper}, LR={lower}"


def defw_hex(word: int) -> str:
    """Format an inline operand word as in the design examples: 08150h."""
    return f"0{word:04X}h"


@dataclass
class CpcProfile:
    """The CPC RST profile for the disassembler (implements `DisassemblyProfile`).

    Attributes:
        user_restart_inline_length: Inline bytes after RST 6; the owner configures it per
            analysis target (default 0).
        user_restart_flow: How RST 6 affects the flow; its handler is user code, so this is
            configurable too (default: continues after the RST and its inline bytes).
        image_range: (first, last) address of the analysed image. A FAR CALL pointer
            outside it is reported "(out of range)" instead of being dereferenced; None
            dereferences every pointer.
    """

    user_restart_inline_length: int = 0
    user_restart_flow: Flow = Flow.SEQUENTIAL
    image_range: tuple[int, int] | None = None

    def decode_restart(
        self, number: int, address: int, read_byte: ByteReader, symbols: SymbolLookup | None
    ) -> RestartDecoding:
        """Decode RST `number` at `address` with its inline operand.

        Args:
            number: RST number 0-7.
            address: Address of the RST opcode.
            read_byte: Byte source.
            symbols: Label lookup for targets and far address objects.

        Returns:
            Inline length, flow, target and the comments/extra lines to show.
        """
        spec = RESTARTS[number]
        if number == 6:
            return self._decode_user_restart(address, read_byte, spec)
        if spec.inline_length == 0:
            return RestartDecoding(0, spec.flow, comment=spec.name)
        operand_address = (address + 1) & 0xFFFF
        word = read_byte(operand_address) | read_byte((operand_address + 1) & 0xFFFF) << 8
        if number == 1:
            target = word & 0x3FFF
            state = _LOW_JUMP_ROM_STATES[word >> 14]
            line = ExtraLine(f"defw {defw_hex(word)}", f"  to {hex_word(target)} [{state}]")
        elif number == 2:
            target = 0xC000 + (word & 0x3FFF)
            line = ExtraLine(
                f"defw {defw_hex(word)}", f"  to {hex_word(target)} [slot {word >> 14}]"
            )
        elif number == 3:
            target, comment = self._decode_far_address(word, read_byte, symbols)
            line = ExtraLine("defw {word}", comment, word)
        else:  # 5: FIRM JUMP
            target = word
            line = ExtraLine("defw {word}", f"  to {hex_word(target)}", word)
        return RestartDecoding(2, spec.flow, target, spec.name, (line,))

    def _decode_far_address(
        self, pointer: int, read_byte: ByteReader, symbols: SymbolLookup | None
    ) -> tuple[int | None, str]:
        """Read the 3-byte far address object at `pointer`: (target, comment)."""
        if self.image_range is not None and not (
            self.image_range[0] <= pointer and pointer + 2 <= self.image_range[1]
        ):
            return None, "  (out of range)"
        target = read_byte(pointer) | read_byte((pointer + 1) & 0xFFFF) << 8
        select = read_byte((pointer + 2) & 0xFFFF)
        label = symbols(target) if symbols is not None else None
        return target, f"  to {label or hex_word(target)} [{rom_select_text(select)}]"

    def _decode_user_restart(
        self, address: int, read_byte: ByteReader, spec: RestartSpec
    ) -> RestartDecoding:
        """RST 6 with the configured number of inline bytes (shown as defb)."""
        length = self.user_restart_inline_length
        flow = self.user_restart_flow
        if length == 0:
            return RestartDecoding(0, flow, comment=spec.name)
        data = [read_byte((address + 1 + offset) & 0xFFFF) for offset in range(length)]
        line = ExtraLine("defb " + ",".join(f"#{byte:02X}" for byte in data), "  inline data")
        return RestartDecoding(length, flow, comment=spec.name, extra_lines=(line,))
