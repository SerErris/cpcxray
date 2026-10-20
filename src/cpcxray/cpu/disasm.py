"""Z80 disassembler (design.md §6.13; FR-DIS-01..05, D-30, D-32).

`disassemble(read_byte, address)` decodes one instruction from any byte source (CPU view,
RAM, ROM, shadow ROM) and returns an `Instruction`. The text uses sjasmplus syntax in lower
case with `#` hex numbers (`ld a,(ix+#05)`, `jp #BB5A`), so a listing reassembles with
sjasmplus.

Every instruction knows:

* its length and raw bytes, and how it affects the control flow (`Flow`), with the target of
  jumps and calls;
* the 16-bit operand that may be an address (`word_operand`), so views can show a symbol
  instead of the number (`Instruction.text(symbols)`);
* whether sjasmplus would re-encode the mnemonic to the same bytes. Encodings for which this
  is not true (undocumented duplicates such as `ED 63` for `ld (nn),hl`, the NEG/RETN/IM
  duplicates, undefined ED opcodes, DD/FD prefixes in front of instructions that do not use
  HL) are flagged `non_canonical`; the source generator emits them as `defb` (D-32).

RST instructions are decoded by a profile (D-30). Without a profile they are plain one-byte
calls; with the CPC profile (`cpcxray.analysis.cpc_profile`) RST 1, 2, 3 and 5 take two inline
bytes, rendered as an extra `defw` line with a decoded comment.

The opcode decoding follows the bit fields used by the CPU core (see `cpcxray.cpu.z80`):
group = bits 7-6, bits_5_3, bits_2_0.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

type ByteReader = Callable[[int], int]
"""Returns the byte at a 16-bit address (the disassembler never writes)."""

type SymbolLookup = Callable[[int], str | None]
"""Returns the label for an address, or None."""


class Flow(Enum):
    """How an instruction affects the program flow (for tracing, stepping and analysis)."""

    SEQUENTIAL = "sequential"  # continues with the next instruction
    JUMP = "jump"  # always continues at `target`
    CONDITIONAL_JUMP = "conditional_jump"  # `target` or the next instruction
    INDIRECT_JUMP = "indirect_jump"  # JP (HL)/(IX)/(IY): target known only at run time
    CALL = "call"  # continues at `target`, returns to the next instruction
    CONDITIONAL_CALL = "conditional_call"
    RETURN = "return"
    CONDITIONAL_RETURN = "conditional_return"
    HALT = "halt"
    NO_RETURN = "no_return"  # e.g. CPC RST 0 (RESET): control never comes back


@dataclass(frozen=True)
class ExtraLine:
    """An additional source line that belongs to an instruction (e.g. a `defw` after an RST).

    Attributes:
        text: The line, e.g. "defw 08150h"; may contain "{word}" for a symbolic operand.
        comment: Explanatory comment without the leading ";".
        word: The value that "{word}" stands for, if any.
    """

    text: str
    comment: str = ""
    word: int | None = None


@dataclass(frozen=True)
class Instruction:  # pylint: disable=too-many-instance-attributes  # a data record
    """One decoded instruction.

    Attributes:
        address: Address of the first byte.
        raw: The instruction bytes (including inline data of profile RSTs).
        template: sjasmplus text; "{word}" marks the 16-bit operand `word_operand`.
        flow: Effect on the program flow.
        target: Jump/call target, if known statically.
        word_operand: The 16-bit operand that may be an address, if any.
        non_canonical: sjasmplus would encode `text()` differently (emit as defb, D-32).
        undocumented: The instruction is undocumented by Zilog.
        comment: Comment for the first line (e.g. the CPC RST function name).
        extra_lines: Further lines, e.g. the inline `defw` of a CPC RST.
    """

    address: int
    raw: bytes
    template: str
    flow: Flow = Flow.SEQUENTIAL
    target: int | None = None
    word_operand: int | None = None
    non_canonical: bool = False
    undocumented: bool = False
    comment: str = ""
    extra_lines: tuple[ExtraLine, ...] = field(default=())

    @property
    def length(self) -> int:
        """Number of bytes."""
        return len(self.raw)

    @property
    def next_address(self) -> int:
        """Address of the following instruction."""
        return (self.address + self.length) & 0xFFFF

    @property
    def mnemonic(self) -> str:
        """The instruction text with all numbers in hex (no symbols)."""
        return self.text()

    def text(self, symbols: SymbolLookup | None = None) -> str:
        """Return the instruction text, with a symbol for `word_operand` where one exists.

        Args:
            symbols: Label lookup; None shows plain hex numbers.
        """
        return _fill_word(self.template, self.word_operand, symbols)

    def lines(self, symbols: SymbolLookup | None = None) -> list[tuple[str, str]]:
        """Return all source lines of the instruction as (text, comment) pairs."""
        result = [(self.text(symbols), self.comment)]
        for line in self.extra_lines:
            result.append((_fill_word(line.text, line.word, symbols), line.comment))
        return result


@dataclass(frozen=True)
class RestartDecoding:
    """How a profile decodes an RST instruction.

    Attributes:
        inline_length: Number of inline data bytes after the opcode.
        flow: Effect on the program flow.
        target: Static target, if known.
        comment: Comment for the `rst` line.
        extra_lines: Lines for the inline data.
    """

    inline_length: int
    flow: Flow
    target: int | None = None
    comment: str = ""
    extra_lines: tuple[ExtraLine, ...] = ()


class DisassemblyProfile(Protocol):  # pylint: disable=too-few-public-methods  # one hook
    """Machine-specific decoding of RST instructions (D-30)."""

    def decode_restart(
        self, number: int, address: int, read_byte: ByteReader, symbols: SymbolLookup | None
    ) -> RestartDecoding:
        """Decode RST `number` (0-7) at `address` (the address of the RST opcode)."""
        ...


def hex_byte(value: int) -> str:
    """Format a byte as sjasmplus hex, e.g. #3E."""
    return f"#{value:02X}"


def hex_word(value: int) -> str:
    """Format a word as sjasmplus hex, e.g. #BB5A."""
    return f"#{value:04X}"


def _fill_word(template: str, word: int | None, symbols: SymbolLookup | None) -> str:
    """Replace "{word}" in `template` by a label (if `symbols` knows one) or by hex."""
    if word is None or "{word}" not in template:
        return template
    label = symbols(word) if symbols is not None else None
    return template.replace("{word}", label if label else hex_word(word))


# Operand names, indexed by the opcode fields
_REGISTERS = ("b", "c", "d", "e", "h", "l", "(hl)", "a")
_PAIRS = ("bc", "de", "hl", "sp")  # LD rr,nn / INC rr / ADD HL,rr / ...
_PAIRS_WITH_AF = ("bc", "de", "hl", "af")  # PUSH / POP
_CONDITIONS = ("nz", "z", "nc", "c", "po", "pe", "p", "m")
_ALU = ("add a,", "adc a,", "sub ", "sbc a,", "and ", "xor ", "or ", "cp ")
_ROTATE_SHIFT = ("rlc", "rrc", "rl", "rr", "sla", "sra", "sll", "srl")
_ACCUMULATOR_OPS = ("rlca", "rrca", "rla", "rra", "daa", "cpl", "scf", "ccf")
_INTERRUPT_MODES = ("0", "0", "1", "2", "0", "0", "1", "2")
_BLOCK = {
    (4, 0): "ldi", (4, 1): "cpi", (4, 2): "ini", (4, 3): "outi",
    (5, 0): "ldd", (5, 1): "cpd", (5, 2): "ind", (5, 3): "outd",
    (6, 0): "ldir", (6, 1): "cpir", (6, 2): "inir", (6, 3): "otir",
    (7, 0): "lddr", (7, 1): "cpdr", (7, 2): "indr", (7, 3): "otdr",
}  # fmt: skip


@dataclass
class _Decoded:  # pylint: disable=too-many-instance-attributes  # mirrors Instruction fields
    """Mutable result while decoding; turned into an `Instruction` at the end."""

    template: str
    length: int
    flow: Flow = Flow.SEQUENTIAL
    target: int | None = None
    word_operand: int | None = None
    non_canonical: bool = False
    undocumented: bool = False
    comment: str = ""
    extra_lines: tuple[ExtraLine, ...] = ()


class _Reader:
    """Reads instruction bytes relative to the instruction address.

    Opcode and prefix bytes (the CPU's M1 fetches) are read with `read_opcode`, all other
    bytes with `read_byte`; for a scrambled ROM the two differ (§5.5).
    """

    def __init__(
        self, read_byte: ByteReader, address: int, read_opcode: ByteReader | None = None
    ) -> None:
        """Read from `read_byte` (and `read_opcode` for M1 bytes), starting at `address`."""
        self.read_byte = read_byte
        self.read_opcode = read_opcode or read_byte
        self.address = address

    def byte(self, offset: int) -> int:
        """Return the byte at address + offset, as a data read (operands)."""
        return self.read_byte((self.address + offset) & 0xFFFF)

    def opcode(self, offset: int) -> int:
        """Return the byte at address + offset, as an M1 fetch (opcode or prefix)."""
        return self.read_opcode((self.address + offset) & 0xFFFF)

    def word(self, offset: int) -> int:
        """Return the little-endian word at address + offset."""
        return self.byte(offset) | self.byte(offset + 1) << 8

    def signed(self, offset: int) -> int:
        """Return the byte at address + offset as two's complement."""
        value = self.byte(offset)
        return value - 256 if value & 0x80 else value


def disassemble(
    read_byte: ByteReader,
    address: int,
    profile: DisassemblyProfile | None = None,
    symbols: SymbolLookup | None = None,
    read_opcode: ByteReader | None = None,
) -> Instruction:
    """Decode the instruction at `address`.

    Args:
        read_byte: Byte source for operands and data (e.g. the CPU view of memory).
        address: Address of the first byte.
        profile: RST decoding (e.g. the CPC profile); None decodes RSTs as plain calls.
        symbols: Label lookup used by profiles for their comments.
        read_opcode: Byte source for the M1 bytes (opcode, prefixes and the opcode after a
            prefix); for a scrambled ROM it returns the raw byte while `read_byte` decodes.
            None reads everything with `read_byte`.

    Returns:
        The instruction, including the inline data of profile RSTs. `raw` holds the bytes
        as the CPU sees them (clear bytes for a scrambled ROM).
    """
    reader = _Reader(read_byte, address, read_opcode)
    opcode = reader.opcode(0)
    if opcode in (0xDD, 0xFD):
        decoded = _decode_indexed(reader, "ix" if opcode == 0xDD else "iy")
    elif opcode == 0xED:
        decoded = _decode_ed(reader)
    elif opcode == 0xCB:
        decoded = _decode_cb(reader)
    else:
        decoded = _decode_unprefixed(reader, opcode, 0)
        if opcode & 0xC7 == 0xC7 and profile is not None:  # RST
            _apply_profile(decoded, profile, opcode >> 3 & 7, reader, symbols)
    m1_bytes = 2 if opcode in (0xCB, 0xDD, 0xED, 0xFD) and decoded.length > 1 else 1
    raw = bytes(
        reader.opcode(offset) if offset < m1_bytes else reader.byte(offset)
        for offset in range(decoded.length)
    )
    return Instruction(
        address=address,
        raw=raw,
        template=decoded.template,
        flow=decoded.flow,
        target=decoded.target,
        word_operand=decoded.word_operand,
        non_canonical=decoded.non_canonical,
        undocumented=decoded.undocumented,
        comment=decoded.comment,
        extra_lines=decoded.extra_lines,
    )


def disassemble_range(  # pylint: disable=too-many-arguments  # same options as disassemble
    read_byte: ByteReader,
    address: int,
    count: int,
    *,
    profile: DisassemblyProfile | None = None,
    symbols: SymbolLookup | None = None,
    read_opcode: ByteReader | None = None,
) -> list[Instruction]:
    """Decode `count` consecutive instructions starting at `address`."""
    instructions = []
    for _ in range(count):
        instruction = disassemble(read_byte, address, profile, symbols, read_opcode)
        instructions.append(instruction)
        address = instruction.next_address
    return instructions


def _apply_profile(
    decoded: _Decoded,
    profile: DisassemblyProfile,
    number: int,
    reader: _Reader,
    symbols: SymbolLookup | None,
) -> None:
    """Let the profile decode RST `number`: inline bytes, flow and comments."""
    restart = profile.decode_restart(number, reader.address, reader.read_byte, symbols)
    decoded.length = 1 + restart.inline_length
    decoded.flow = restart.flow
    decoded.target = restart.target
    decoded.comment = restart.comment
    decoded.extra_lines = restart.extra_lines


# -- unprefixed (and the DD/FD forms that share it) ------------------------------------------


def _decode_unprefixed(reader: _Reader, opcode: int, offset: int) -> _Decoded:
    """Decode an unprefixed opcode whose byte is at `offset` (1 after an ineffective DD/FD).

    The DD/FD decoder handles the forms that use IX/IY itself and only falls back to this
    function for instructions the prefix does not affect.
    """
    group, bits_5_3, bits_2_0 = opcode >> 6, opcode >> 3 & 7, opcode & 7
    operand_offset = offset + 1
    if group == 1:
        if opcode == 0x76:
            return _Decoded("halt", operand_offset, Flow.HALT)
        return _Decoded(f"ld {_REGISTERS[bits_5_3]},{_REGISTERS[bits_2_0]}", operand_offset)
    if group == 2:
        return _Decoded(_ALU[bits_5_3] + _REGISTERS[bits_2_0], operand_offset)
    if group == 0:
        return _decode_group0(reader, opcode, operand_offset)
    return _decode_group3(reader, opcode, operand_offset)


def _decode_group0(  # pylint: disable=too-many-return-statements  # one case per column
    reader: _Reader, opcode: int, operand_offset: int
) -> _Decoded:
    """Decode opcodes &00-&3F (loads, 16-bit arithmetic, INC/DEC, relative jumps, ...)."""
    bits_5_3, bits_2_0 = opcode >> 3 & 7, opcode & 7
    pair_index, bit_3 = bits_5_3 >> 1, bits_5_3 & 1
    if bits_2_0 == 0:
        if bits_5_3 == 0:
            return _Decoded("nop", operand_offset)
        if bits_5_3 == 1:
            return _Decoded("ex af,af'", operand_offset)
        target = (reader.address + operand_offset + 1 + reader.signed(operand_offset)) & 0xFFFF
        if bits_5_3 == 2:
            return _Decoded(
                "djnz {word}", operand_offset + 1, Flow.CONDITIONAL_JUMP, target, target
            )
        if bits_5_3 == 3:
            return _Decoded("jr {word}", operand_offset + 1, Flow.JUMP, target, target)
        condition = _CONDITIONS[bits_5_3 - 4]
        return _Decoded(
            f"jr {condition},{{word}}", operand_offset + 1, Flow.CONDITIONAL_JUMP, target, target
        )
    if bits_2_0 == 1:
        if bit_3 == 0:
            word = reader.word(operand_offset)
            return _Decoded(
                f"ld {_PAIRS[pair_index]},{{word}}", operand_offset + 2, word_operand=word
            )
        return _Decoded(f"add hl,{_PAIRS[pair_index]}", operand_offset)
    if bits_2_0 == 2:
        indirect = ("(bc)", "(de)")
        if pair_index < 2:
            text = f"ld {indirect[pair_index]},a" if bit_3 == 0 else f"ld a,{indirect[pair_index]}"
            return _Decoded(text, operand_offset)
        word = reader.word(operand_offset)
        register = "hl" if pair_index == 2 else "a"
        text = f"ld ({{word}}),{register}" if bit_3 == 0 else f"ld {register},({{word}})"
        return _Decoded(text, operand_offset + 2, word_operand=word)
    if bits_2_0 == 3:
        return _Decoded(f"{'inc' if bit_3 == 0 else 'dec'} {_PAIRS[pair_index]}", operand_offset)
    if bits_2_0 in (4, 5):
        return _Decoded(
            f"{'inc' if bits_2_0 == 4 else 'dec'} {_REGISTERS[bits_5_3]}", operand_offset
        )
    if bits_2_0 == 6:
        value = hex_byte(reader.byte(operand_offset))
        return _Decoded(f"ld {_REGISTERS[bits_5_3]},{value}", operand_offset + 1)
    return _Decoded(_ACCUMULATOR_OPS[bits_5_3], operand_offset)


def _decode_group3(  # pylint: disable=too-many-return-statements  # one case per column
    reader: _Reader, opcode: int, operand_offset: int
) -> _Decoded:
    """Decode opcodes &C0-&FF (jumps, calls, returns, stack, I/O, immediate ALU, RST)."""
    bits_5_3, bits_2_0 = opcode >> 3 & 7, opcode & 7
    pair_index, bit_3 = bits_5_3 >> 1, bits_5_3 & 1
    if bits_2_0 == 0:
        return _Decoded(f"ret {_CONDITIONS[bits_5_3]}", operand_offset, Flow.CONDITIONAL_RETURN)
    if bits_2_0 == 1:
        if bit_3 == 0:
            return _Decoded(f"pop {_PAIRS_WITH_AF[pair_index]}", operand_offset)
        texts = ("ret", "exx", "jp (hl)", "ld sp,hl")
        flows = (Flow.RETURN, Flow.SEQUENTIAL, Flow.INDIRECT_JUMP, Flow.SEQUENTIAL)
        return _Decoded(texts[pair_index], operand_offset, flows[pair_index])
    if bits_2_0 == 2:
        target = reader.word(operand_offset)
        return _Decoded(
            f"jp {_CONDITIONS[bits_5_3]},{{word}}",
            operand_offset + 2, Flow.CONDITIONAL_JUMP, target, target,
        )  # fmt: skip
    if bits_2_0 == 3:
        if bits_5_3 == 0:
            target = reader.word(operand_offset)
            return _Decoded("jp {word}", operand_offset + 2, Flow.JUMP, target, target)
        if bits_5_3 in (2, 3):
            port = hex_byte(reader.byte(operand_offset))
            text = f"out ({port}),a" if bits_5_3 == 2 else f"in a,({port})"
            return _Decoded(text, operand_offset + 1)
        texts = {4: "ex (sp),hl", 5: "ex de,hl", 6: "di", 7: "ei"}
        return _Decoded(texts[bits_5_3], operand_offset)  # 1 = CB prefix, handled before
    if bits_2_0 == 4:
        target = reader.word(operand_offset)
        return _Decoded(
            f"call {_CONDITIONS[bits_5_3]},{{word}}",
            operand_offset + 2, Flow.CONDITIONAL_CALL, target, target,
        )  # fmt: skip
    if bits_2_0 == 5:
        if bit_3 == 0:
            return _Decoded(f"push {_PAIRS_WITH_AF[pair_index]}", operand_offset)
        target = reader.word(operand_offset)  # pair_index 0: CALL (1-3 are prefixes)
        return _Decoded("call {word}", operand_offset + 2, Flow.CALL, target, target)
    if bits_2_0 == 6:
        return _Decoded(_ALU[bits_5_3] + hex_byte(reader.byte(operand_offset)), operand_offset + 1)
    restart_address = bits_5_3 * 8
    return _Decoded(f"rst {hex_byte(restart_address)}", operand_offset, Flow.CALL, restart_address)


# -- CB --------------------------------------------------------------------------------------


def _decode_cb(reader: _Reader) -> _Decoded:
    """Decode CB xx: rotate/shift, BIT, RES, SET on registers and (HL)."""
    opcode = reader.opcode(1)
    group, bits_5_3, bits_2_0 = opcode >> 6, opcode >> 3 & 7, opcode & 7
    operand = _REGISTERS[bits_2_0]
    if group == 0:
        return _Decoded(f"{_ROTATE_SHIFT[bits_5_3]} {operand}", 2, undocumented=bits_5_3 == 6)
    return _Decoded(f"{('bit', 'res', 'set')[group - 1]} {bits_5_3},{operand}", 2)


# -- ED --------------------------------------------------------------------------------------


def _decode_ed(reader: _Reader) -> _Decoded:  # pylint: disable=too-many-return-statements,too-many-branches
    """Decode ED xx, including the undocumented duplicates (flagged non-canonical)."""
    opcode = reader.opcode(1)
    group, bits_5_3, bits_2_0 = opcode >> 6, opcode >> 3 & 7, opcode & 7
    pair_index, bit_3 = bits_5_3 >> 1, bits_5_3 & 1
    if group == 2 and bits_2_0 <= 3 and bits_5_3 >= 4:
        return _Decoded(_BLOCK[(bits_5_3, bits_2_0)], 2)
    if group != 1:
        return _Decoded("nop", 2, non_canonical=True, undocumented=True)  # undefined: no-op
    if bits_2_0 == 0:
        if bits_5_3 == 6:
            return _Decoded("in f,(c)", 2, undocumented=True)
        return _Decoded(f"in {_REGISTERS[bits_5_3]},(c)", 2)
    if bits_2_0 == 1:
        if bits_5_3 == 6:
            return _Decoded("out (c),0", 2, undocumented=True)
        return _Decoded(f"out (c),{_REGISTERS[bits_5_3]}", 2)
    if bits_2_0 == 2:
        return _Decoded(f"{'sbc' if bit_3 == 0 else 'adc'} hl,{_PAIRS[pair_index]}", 2)
    if bits_2_0 == 3:
        word = reader.word(2)
        pair = _PAIRS[pair_index]
        text = f"ld ({{word}}),{pair}" if bit_3 == 0 else f"ld {pair},({{word}})"
        hl_form = pair_index == 2  # ED 63 / ED 6B duplicate the shorter 22 / 2A
        return _Decoded(text, 4, word_operand=word, non_canonical=hl_form, undocumented=hl_form)
    if bits_2_0 == 4:
        duplicate = opcode != 0x44
        return _Decoded("neg", 2, non_canonical=duplicate, undocumented=duplicate)
    if bits_2_0 == 5:
        if opcode == 0x4D:
            return _Decoded("reti", 2, Flow.RETURN)
        duplicate = opcode != 0x45
        return _Decoded("retn", 2, Flow.RETURN, non_canonical=duplicate, undocumented=duplicate)
    if bits_2_0 == 6:
        duplicate = opcode not in (0x46, 0x56, 0x5E)
        return _Decoded(
            f"im {_INTERRUPT_MODES[bits_5_3]}", 2, non_canonical=duplicate, undocumented=duplicate
        )
    texts = ("ld i,a", "ld r,a", "ld a,i", "ld a,r", "rrd", "rld")
    if bits_5_3 < 6:
        return _Decoded(texts[bits_5_3], 2)
    return _Decoded("nop", 2, non_canonical=True, undocumented=True)  # ED 77 / ED 7F


# -- DD / FD ---------------------------------------------------------------------------------


def _index_operand(reader: _Reader, index_name: str, offset: int) -> str:
    """Format (ix+d) / (iy-d) from the displacement at `offset`."""
    displacement = reader.signed(offset)
    sign = "+" if displacement >= 0 else "-"
    return f"({index_name}{sign}{hex_byte(abs(displacement))})"


def _decode_indexed(reader: _Reader, index_name: str) -> _Decoded:
    """Decode DD xx / FD xx (IX/IY instructions, DD CB d op) or a lone prefix.

    A DD/FD prefix followed by DD, ED or FD has no effect of its own: it is decoded as a
    one-byte `defb`. A prefix in front of an instruction that does not involve HL makes the
    instruction behave as unprefixed: decoded with its normal mnemonic, flagged non-canonical.
    """
    opcode = reader.opcode(1)
    if opcode in (0xDD, 0xED, 0xFD):
        return _Decoded(f"defb {hex_byte(reader.opcode(0))}", 1, non_canonical=False)
    if opcode == 0xCB:
        return _decode_indexed_cb(reader, index_name)
    decoded = _decode_index_specific(reader, opcode, index_name)
    if decoded is not None:
        return decoded
    decoded = _decode_unprefixed(reader, opcode, 1)
    decoded.non_canonical = True  # the prefix has no effect; sjasmplus omits it
    decoded.undocumented = True
    return decoded


def _decode_index_specific(  # pylint: disable=too-many-return-statements,too-many-branches,too-many-locals
    reader: _Reader, opcode: int, index_name: str
) -> _Decoded | None:
    """Decode the DD/FD forms that actually use IX/IY; return None for all others."""
    group, bits_5_3, bits_2_0 = opcode >> 6, opcode >> 3 & 7, opcode & 7
    halves = (f"{index_name}h", f"{index_name}l")
    registers = (*_REGISTERS[:4], *halves, "(hl)", "a")  # H/L → IXH/IXL; 6 is replaced below
    uses_memory = (group == 1 and (bits_5_3 == 6) != (bits_2_0 == 6)) or (
        group == 2 and bits_2_0 == 6
    )
    if uses_memory:  # LD r,(ix+d) / LD (ix+d),r / ALU (ix+d): the other register is the real one
        memory = _index_operand(reader, index_name, 2)
        if group == 2:
            return _Decoded(_ALU[bits_5_3] + memory, 3)
        if bits_2_0 == 6:
            return _Decoded(f"ld {_REGISTERS[bits_5_3]},{memory}", 3)
        return _Decoded(f"ld {memory},{_REGISTERS[bits_2_0]}", 3)
    uses_half = group in (1, 2) and (bits_2_0 in (4, 5) or (group == 1 and bits_5_3 in (4, 5)))
    if uses_half and opcode != 0x76:
        if group == 1:
            text = f"ld {registers[bits_5_3]},{registers[bits_2_0]}"
        else:
            text = _ALU[bits_5_3] + registers[bits_2_0]
        return _Decoded(text, 2, undocumented=True)
    if opcode in (0x34, 0x35):
        return _Decoded(
            f"{'inc' if opcode == 0x34 else 'dec'} {_index_operand(reader, index_name, 2)}", 3
        )
    if opcode == 0x36:
        memory = _index_operand(reader, index_name, 2)
        return _Decoded(f"ld {memory},{hex_byte(reader.byte(3))}", 4)
    if opcode in (0x24, 0x25, 0x2C, 0x2D):
        half = halves[(opcode >> 3) & 1]
        return _Decoded(f"{'inc' if opcode & 1 == 0 else 'dec'} {half}", 2, undocumented=True)
    if opcode in (0x26, 0x2E):
        half = halves[(opcode >> 3) & 1]
        return _Decoded(f"ld {half},{hex_byte(reader.byte(2))}", 3, undocumented=True)
    specific: dict[int, tuple[str, int, Flow]] = {
        0x09: (f"add {index_name},bc", 2, Flow.SEQUENTIAL),
        0x19: (f"add {index_name},de", 2, Flow.SEQUENTIAL),
        0x29: (f"add {index_name},{index_name}", 2, Flow.SEQUENTIAL),
        0x39: (f"add {index_name},sp", 2, Flow.SEQUENTIAL),
        0x23: (f"inc {index_name}", 2, Flow.SEQUENTIAL),
        0x2B: (f"dec {index_name}", 2, Flow.SEQUENTIAL),
        0xE1: (f"pop {index_name}", 2, Flow.SEQUENTIAL),
        0xE5: (f"push {index_name}", 2, Flow.SEQUENTIAL),
        0xE3: (f"ex (sp),{index_name}", 2, Flow.SEQUENTIAL),
        0xE9: (f"jp ({index_name})", 2, Flow.INDIRECT_JUMP),
        0xF9: (f"ld sp,{index_name}", 2, Flow.SEQUENTIAL),
    }
    if opcode in specific:
        text, length, flow = specific[opcode]
        return _Decoded(text, length, flow)
    word_forms = {0x21: f"ld {index_name},{{word}}", 0x22: f"ld ({{word}}),{index_name}",
                  0x2A: f"ld {index_name},({{word}})"}  # fmt: skip
    if opcode in word_forms:
        return _Decoded(word_forms[opcode], 4, word_operand=reader.word(2))
    return None


def _decode_indexed_cb(reader: _Reader, index_name: str) -> _Decoded:
    """Decode DD CB d op / FD CB d op.

    For rotate/shift/RES/SET with a register field other than 6, the result is also copied to
    that register (undocumented; sjasmplus syntax `rlc (ix+5),b`). For BIT, the register
    field has no effect: only field 6 is the canonical encoding.
    """
    opcode = reader.byte(3)
    group, bits_5_3, bits_2_0 = opcode >> 6, opcode >> 3 & 7, opcode & 7
    memory = _index_operand(reader, index_name, 2)
    copy = "" if bits_2_0 == 6 else "," + _REGISTERS[bits_2_0]
    if group == 0:
        undocumented = bits_2_0 != 6 or bits_5_3 == 6
        return _Decoded(f"{_ROTATE_SHIFT[bits_5_3]} {memory}{copy}", 4, undocumented=undocumented)
    if group == 1:
        duplicate = bits_2_0 != 6
        return _Decoded(
            f"bit {bits_5_3},{memory}", 4, non_canonical=duplicate, undocumented=duplicate
        )
    operation = "res" if group == 2 else "set"
    return _Decoded(f"{operation} {bits_5_3},{memory}{copy}", 4, undocumented=bits_2_0 != 6)
