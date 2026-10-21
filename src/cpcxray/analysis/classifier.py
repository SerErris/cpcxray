"""Code/data classification of an analysis target (design.md §6.15; D-20, D-30, D-31).

`TargetView` is how the classifier and the source generator see a target: its clear bytes, the
access class of every byte (scrambled ROMs), and its address space. Bytes of a relocated block
(code copied to RAM and run there, D-22) are seen at their RAM address, all others at their
CPU address in the target (&C000.. for upper ROMs).

For a never-read byte of a scrambled ROM the clear value depends on how it would be read
(§5.5): an opcode or prefix byte (M1) is clear as stored, every other byte must be decoded. The
view therefore answers opcode reads with the raw byte and data reads with the decoded byte;
`final_image` fixes each such byte once the classification says which role it has.

Classification, in order of strength (owner > observed > static, `Source`):

1. owner overrides (`classify` tool): "code" disassembles the range sequentially, the data
   kinds mark every byte;
2. observed (`build_observed`): instruction starts from the target flags (executed directly or
   via a RAM copy), their bodies by disassembly, the inline bytes of RST 1/2/3/5 as
   RST_OPERAND, recorded far address objects as FAR_ADDR;
3. static (`classify_static`): recursive descent from the observed code and the jumpblock of a
   ROM header, following the control flow incl. the CPC RST flow kinds (D-30). It never
   enters a classified byte, a &FF opcode (§6.15: never code in unknown areas), or a
   non-canonical encoding (unlikely in real code), and stays inside the target and the
   relocated block it started in. Opcode and prefix bytes must be unread (or M1); operand
   bytes may have been read as data, because operands always are (e.g. a jumpblock whose
   targets a catalogue command read);
4. data reads (`mark_data_reads`): all remaining bytes read as data become BYTE_DATA.

`classify_target` runs all steps in this order.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace

from cpcxray.analysis.cpc_profile import CpcProfile
from cpcxray.analysis.database import Kind, Source, TargetDatabase
from cpcxray.analysis.provenance import RelocatedBlock
from cpcxray.analysis.targets import EXEC_VIA_RAM, INSN_START
from cpcxray.cpu.disasm import Flow, Instruction, SymbolLookup, disassemble
from cpcxray.decoding.m1map import FILL_POLICIES
from cpcxray.decoding.shadow_rom import DATA, ERROR, M1, UNKNOWN

PREFIXES = frozenset({0xCB, 0xDD, 0xED, 0xFD})
RST_OPCODES_WITH_OPERAND = frozenset({0xCF, 0xD7, 0xDF, 0xEF})
_CONTINUES = frozenset({
    Flow.SEQUENTIAL, Flow.HALT, Flow.CONDITIONAL_RETURN, Flow.CONDITIONAL_JUMP, Flow.CALL,
    Flow.CONDITIONAL_CALL,
})  # fmt: skip
_BRANCHES = frozenset({Flow.JUMP, Flow.CONDITIONAL_JUMP, Flow.CALL, Flow.CONDITIONAL_CALL})
MAX_ROM_NAMES = 64
MAX_NAME_LENGTH = 16


def opcode_byte_count(instruction: Instruction) -> int:
    """Number of leading M1 bytes: the opcode, plus the opcode after a CB/DD/ED/FD prefix.

    DD CB d op has two M1 bytes (DD, CB); d and op are data reads (§5.5).
    """
    return 2 if instruction.raw[0] in PREFIXES and instruction.length > 1 else 1


@dataclass(frozen=True)
class TargetView:  # pylint: disable=too-many-instance-attributes  # image, access and layout
    """The bytes and address space of one target.

    Attributes:
        name: Target name ("rom5", "lower", "ram:<name>").
        start: CPU address of offset 0.
        image: Clear value per byte; never-read scrambled bytes hold the decoded value.
        raw: Scrambled image (None for plain ROMs and RAM targets).
        access: Access class per byte (None if not recorded).
        decode: The decode rule `decode(offset, raw)` of a scrambled ROM.
        blocks: Relocated blocks shown at their RAM address (non-overlapping).
        resolved: The image is final (`final_image`): opcode reads see `image` too.
    """

    name: str
    start: int
    image: bytes
    raw: bytes | None = None
    access: bytes | None = None
    decode: Callable[[int, int], int] | None = None
    blocks: tuple[RelocatedBlock, ...] = field(default=())
    resolved: bool = False

    @property
    def length(self) -> int:
        """Number of bytes."""
        return len(self.image)

    @property
    def scrambled(self) -> bool:
        """True for a scrambled ROM (it has an access map)."""
        return self.access is not None

    def never_read(self, offset: int) -> bool:
        """True if a scrambled byte was never read (access class unknown)."""
        return self.access is not None and self.access[offset] == UNKNOWN

    def value(self, offset: int, as_opcode: bool = False) -> int:
        """Return the clear byte; for never-read scrambled bytes it depends on the role."""
        if as_opcode and not self.resolved and self.raw is not None and self.never_read(offset):
            return self.raw[offset]
        return self.image[offset]

    # -- address space -----------------------------------------------------------------------

    def block_at(self, offset: int) -> RelocatedBlock | None:
        """Return the relocated block containing the offset, or None."""
        for block in self.blocks:
            if block.rom_start <= offset < block.rom_start + block.length:
                return block
        return None

    def address(self, offset: int) -> int:
        """Return the address at which the byte is seen (RAM address inside a block)."""
        block = self.block_at(offset)
        if block is not None:
            return block.ram_start + offset - block.rom_start
        return self.start + offset

    def offset_for_read(self, address: int, block: RelocatedBlock | None) -> int | None:
        """Map an address to an offset, for reading bytes in the coordinates of `block`."""
        if block is not None and block.ram_start <= address < block.ram_start + block.length:
            return block.rom_start + address - block.ram_start
        if self.start <= address < self.start + self.length:
            return address - self.start
        return None

    def offset_of_target(self, address: int, context: RelocatedBlock | None = None) -> int | None:
        """Map a jump/call target to an offset; None if outside or ambiguous.

        Args:
            address: The referenced address.
            context: The block the reference is made from: its own RAM range wins.

        RAM addresses of blocks map into the blocks. Several blocks may share a RAM range
        (overlays: different ROM routines copied to the same place at different times);
        a reference from outside them is then ambiguous. Target addresses map to offsets
        outside every block (a target-address reference into a block's range would mean
        the ROM copy itself runs, which the block's RAM coordinates cannot express).
        """
        if (
            context is not None
            and context.ram_start <= address < context.ram_start + context.length
        ):
            return context.rom_start + address - context.ram_start
        matches = [
            block.rom_start + address - block.ram_start
            for block in self.blocks
            if block.ram_start <= address < block.ram_start + block.length
        ]
        if matches:
            return matches[0] if len(matches) == 1 else None
        if self.start <= address < self.start + self.length:
            offset = address - self.start
            return offset if self.block_at(offset) is None else None
        return None

    def overlaid(self, block: RelocatedBlock) -> bool:
        """True if another block shares part of this block's RAM range (an overlay)."""
        return any(
            other is not block
            and other.ram_start < block.ram_start + block.length
            and block.ram_start < other.ram_start + other.length
            for other in self.blocks
        )

    def disassemble(
        self, offset: int, profile: CpcProfile | None, symbols: SymbolLookup | None = None
    ) -> Instruction:
        """Decode the instruction at an offset in its coordinates (RAM address in a block).

        Args:
            offset: Offset of the first byte.
            profile: RST decoding (None: plain one-byte RSTs).
            symbols: Label lookup for the comments of profile RSTs (far call targets).
        """
        block = self.block_at(offset)

        def read(address: int, as_opcode: bool) -> int:
            """Read a byte of the target in the block's coordinates (0 outside)."""
            mapped = self.offset_for_read(address, block)
            return 0 if mapped is None else self.value(mapped, as_opcode)

        return disassemble(
            lambda address: read(address, False), self.address(offset), profile, symbols,
            read_opcode=lambda address: read(address, True),
        )  # fmt: skip

    def fits(self, offset: int, length: int) -> bool:
        """True if `length` bytes from `offset` lie in the target and in the same block."""
        if offset + length > self.length:
            return False
        block = self.block_at(offset)
        return all(self.block_at(offset + index) is block for index in range(1, length))

    # -- final image ---------------------------------------------------------------------------

    def final_image(self, database: TargetDatabase, fill_policy: str) -> bytes:
        """Return the clear image with the never-read bytes filled per policy (D-20).

        "static" keeps the raw byte where the classification found an opcode byte and
        decodes all other never-read bytes; "raw", "data" and "ff" ignore the classification.

        Raises:
            ValueError: For an unknown policy.
        """
        if fill_policy not in FILL_POLICIES:
            raise ValueError(f"fill policy must be one of {', '.join(FILL_POLICIES)}")
        if self.raw is None or self.access is None:
            return self.image
        image = bytearray(self.image)  # never-read bytes hold the decoded value ("data")
        never_read = [o for o in range(self.length) if self.access[o] == UNKNOWN]
        if fill_policy == "raw":
            for offset in never_read:
                image[offset] = self.raw[offset]
        elif fill_policy == "ff":
            for offset in never_read:
                image[offset] = 0xFF
        elif fill_policy == "static":
            for offset in opcode_offsets(database, self):
                if self.access[offset] == UNKNOWN:
                    image[offset] = self.raw[offset]
        return bytes(image)

    def resolve(self, database: TargetDatabase, fill_policy: str) -> TargetView:
        """Return a view of the final image (for the source generator and the dump)."""
        return replace(self, image=self.final_image(database, fill_policy), resolved=True)


def opcode_offsets(database: TargetDatabase, view: TargetView) -> list[int]:
    """Return the offsets of all opcode/prefix bytes of classified instructions."""
    result = []
    for offset in range(view.length):
        if database.kinds[offset] == Kind.INSN_START:
            instruction = view.disassemble(offset, None)
            result += [offset + index for index in range(opcode_byte_count(instruction))]
    return result


def mark_instruction(
    database: TargetDatabase, offset: int, instruction: Instruction, source: Source
) -> None:
    """Classify an instruction's bytes; inline bytes of CPC RSTs become RST_OPERAND.

    A body byte that is already an instruction start of the same or a stronger source keeps
    it (overlapping code: both starts are real, the generator resolves the overlap).
    """
    database.classify(offset, Kind.INSN_START, source)
    is_restart = instruction.raw[0] in RST_OPCODES_WITH_OPERAND
    for index in range(1, instruction.length):
        body = offset + index
        if database.kinds[body] == Kind.INSN_START and database.sources[body] >= source:
            continue
        database.classify(body, Kind.RST_OPERAND if is_restart else Kind.INSN_BODY, source)


# -- owner and observed ----------------------------------------------------------------------


def apply_overrides(database: TargetDatabase, view: TargetView, profile: CpcProfile) -> None:
    """Apply the owner's range classifications (source OWNER)."""
    for first, last, kind in database.override_ranges():
        first, last = max(first, 0), min(last, view.length - 1)
        if kind != Kind.INSN_START:
            for offset in range(first, last + 1):
                database.classify(offset, kind, Source.OWNER)
            continue
        offset = first
        while offset <= last:  # "code": consecutive instructions through the range
            instruction = view.disassemble(offset, profile)
            if not view.fits(offset, instruction.length):
                database.classify(offset, Kind.BYTE_DATA, Source.OWNER)
                offset += 1
                continue
            mark_instruction(database, offset, instruction, Source.OWNER)
            offset += instruction.length


def build_observed(
    database: TargetDatabase, view: TargetView, flags: bytes, profile: CpcProfile
) -> None:
    """Rebuild the owner and observed classification from the facts (module docstring)."""
    database.reset_classification()
    apply_overrides(database, view, profile)
    starts = [o for o in range(view.length) if flags[o] & INSN_START]
    direct = [o for o in starts if _executed_directly(view, flags, o)]
    via_ram = [o for o in starts if not _executed_directly(view, flags, o)]
    for offset in direct + via_ram:  # direct execution is the stronger evidence
        if database.sources[offset] >= Source.OWNER:
            continue
        instruction = view.disassemble(offset, profile)
        if offset in via_ram and _overlaps_observed(database, offset, instruction.length):
            continue  # e.g. an operand of a directly executed instruction, copied to RAM
        if view.fits(offset, instruction.length):
            mark_instruction(database, offset, instruction, Source.OBSERVED)
        else:  # crosses a block or target end: the generator emits it as defb
            database.classify(offset, Kind.INSN_START, Source.OBSERVED)
    for address_text, facts in database.observations.items():
        offset = int(address_text, 16) - database.start
        if facts.get("far_object") and 0 <= offset <= view.length - 3:
            for index in range(3):
                database.classify(offset + index, Kind.FAR_ADDR, Source.OBSERVED)


def _executed_directly(view: TargetView, flags: bytes, offset: int) -> bool:
    """True if the instruction start ran in the target itself, not only from a RAM copy.

    Scrambled ROMs: its opcode byte was fetched with M1 there. Other targets: it has no
    EXEC_VIA_RAM flag.
    """
    if view.access is not None:
        return view.access[offset] in (M1, ERROR)
    return not flags[offset] & EXEC_VIA_RAM


def _overlaps_observed(database: TargetDatabase, offset: int, length: int) -> bool:
    """True if any byte of an instruction is already part of an observed instruction."""
    return any(
        database.sources[o] == Source.OBSERVED
        and database.kinds[o] in (Kind.INSN_START, Kind.INSN_BODY, Kind.RST_OPERAND)
        for o in range(offset, min(offset + length, database.length))
    )


def mark_data_reads(database: TargetDatabase, view: TargetView) -> None:
    """Classify the still unclassified bytes that were read as data as BYTE_DATA (observed)."""
    if view.access is not None:
        for offset, access_class in enumerate(view.access):
            if access_class == DATA and database.sources[offset] == Source.NONE:
                database.classify(offset, Kind.BYTE_DATA, Source.OBSERVED)


def classify_target(
    database: TargetDatabase,
    view: TargetView,
    flags: bytes,
    profile: CpcProfile,
    static: bool = True,
) -> None:
    """Rebuild the whole classification: owner, observed, static (optional), data reads."""
    build_observed(database, view, flags, profile)
    if static:
        classify_static(database, view, profile)
    mark_data_reads(database, view)


# -- static ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class RomHeader:
    """A recognised upper-ROM header at &C000 (type, version, name table, jumpblock).

    Attributes:
        rom_type: Byte 0: 0 foreground, 1 background, 2 extension (bit 7: built in).
        name_table: Offset of the name table.
        names: (offset, length, text) of each name; the first one is the ROM's own name.
            Hidden names (a control character, e.g. &81) are shown as "&81".
        jumpblock: Offsets of the `jp` entries: one per name, then any further `jp` entries
            up to the name table (internal entries without a name).
    """

    rom_type: int
    name_table: int
    names: tuple[tuple[int, int, str], ...]
    jumpblock: tuple[int, ...]

    @property
    def table_end(self) -> int:
        """Offset of the 0 byte that ends the name table."""
        offset, length, _ = self.names[-1]
        return offset + length


def find_rom_header(view: TargetView) -> RomHeader | None:
    """Recognise a ROM header (upper ROMs at &C000 only); None if implausible."""
    if view.start != 0xC000 or view.length < 8 or view.block_at(0) is not None:
        return None
    rom_type = view.value(0)
    pointer = view.value(4) | view.value(5) << 8
    if rom_type & 0x7F > 2 or not 0xC008 <= pointer < 0xC000 + view.length:
        return None
    names = []
    offset = pointer - 0xC000
    while offset < view.length and view.value(offset) != 0 and len(names) < MAX_ROM_NAMES:
        first = offset
        while offset < view.length and not view.value(offset) & 0x80:
            offset += 1
        offset += 1
        if offset - first > MAX_NAME_LENGTH:
            return None
        names.append((first, offset - first, _name_text(view, first, min(offset, view.length))))
    if not names or offset >= view.length:
        return None
    jumpblock = [6 + 3 * index for index in range(len(names))]
    if any(view.value(entry, as_opcode=True) != 0xC3 for entry in jumpblock):
        return None
    table = pointer - 0xC000
    while (
        jumpblock[-1] + 6 <= table
        and view.value(jumpblock[-1] + 3, as_opcode=True) == 0xC3
        and view.block_at(jumpblock[-1] + 3) is None  # a relocated block is no jumpblock
    ):
        jumpblock.append(jumpblock[-1] + 3)
    return RomHeader(rom_type, table, tuple(names), tuple(jumpblock))


def _name_text(view: TargetView, first: int, end: int) -> str:
    """Text of a ROM name: characters without bit 7; control characters as "&81"."""
    parts = []
    for offset in range(first, end):
        value = view.value(offset)
        character = value & 0x7F
        parts.append(chr(character) if 0x20 <= character < 0x7F else f"&{value:02X}")
    return "".join(parts)


def far_object_offset(view: TargetView, instruction: Instruction) -> int | None:
    """Return the offset of the far address object of a FAR CALL (RST 3) in the target."""
    if instruction.raw[0] != 0xDF or instruction.length != 3:
        return None
    pointer = instruction.raw[1] | instruction.raw[2] << 8
    offset = view.offset_of_target(pointer)
    if offset is None or offset + 3 > view.length or not view.fits(offset, 3):
        return None
    return offset


def successors(view: TargetView, instruction: Instruction, offset: int) -> list[int]:
    """Return the offsets where the control flow can continue after the instruction at `offset`."""
    result = []
    if instruction.flow in _CONTINUES:
        result.append(instruction.next_address)
    if instruction.flow in _BRANCHES and instruction.target is not None:
        opcode, raw = instruction.raw[0], instruction.raw
        far_call = opcode == 0xDF
        other_rom = opcode == 0xD7 and len(raw) == 3 and raw[2] >> 6 != 0  # SIDE CALL slot != 0
        if not (far_call or other_rom):
            result.append(instruction.target)
    context = view.block_at(offset)
    offsets = [view.offset_of_target(address, context) for address in result]
    neighbour = _jump_table_neighbour(view, instruction, offset)
    return [offset for offset in [*offsets, neighbour] if offset is not None]


def _jump_table_neighbour(view: TargetView, instruction: Instruction, offset: int) -> int | None:
    """After `jp nn`: the offset of a following `jp nn` entry of a jump table, if any.

    Jump tables (ROM jumpblocks, RAM vectors copied from the ROM) are runs of `C3 nn nn`;
    an unconditional jump has no fall-through, so without this the entries after the first
    executed one would stay data. Only entries whose target lies in the target's own address
    space (ROM or relocated blocks) count.
    """
    if instruction.raw[0] != 0xC3 or instruction.length != 3:
        return None
    entry = offset + 3
    if not view.fits(offset, 6) or view.value(entry, as_opcode=True) != 0xC3:
        return None
    target = view.value(entry + 1) | view.value(entry + 2) << 8
    return entry if view.offset_of_target(target, view.block_at(offset)) is not None else None


def classify_static(
    database: TargetDatabase,
    view: TargetView,
    profile: CpcProfile,
    extra_seeds: Iterable[int] = (),
) -> int:
    """Recursive descent into unclassified bytes (source STATIC).

    Args:
        database: The database; bytes with any classification are never entered.
        view: The target.
        profile: RST decoding (inline lengths and flow kinds, D-30).
        extra_seeds: Further entry points (offsets), e.g. from the owner.

    Returns:
        The number of instructions found.
    """
    work = list(extra_seeds)
    header = find_rom_header(view)
    if header is not None:
        work += header.jumpblock
    for offset in range(view.length):
        if database.kinds[offset] == Kind.INSN_START:
            instruction = view.disassemble(offset, profile)
            work += successors(view, instruction, offset)
            _mark_far_object(database, view, instruction)
    found = 0
    while work:
        offset = work.pop()
        if database.kinds[offset] != Kind.UNKNOWN or view.value(offset, as_opcode=True) == 0xFF:
            continue
        instruction = view.disassemble(offset, profile)
        if instruction.non_canonical or not _static_code_possible(
            database, view, offset, instruction
        ):
            continue
        mark_instruction(database, offset, instruction, Source.STATIC)
        found += 1
        work += successors(view, instruction, offset)
        _mark_far_object(database, view, instruction)
    return found


def _mark_far_object(database: TargetDatabase, view: TargetView, instruction: Instruction) -> None:
    """Classify the far address object of a FAR CALL (static) if its bytes are unclassified."""
    far = far_object_offset(view, instruction)
    if far is not None and all(database.kinds[far + i] == Kind.UNKNOWN for i in range(3)):
        for index in range(3):
            database.classify(far + index, Kind.FAR_ADDR, Source.STATIC)


def _static_code_possible(
    database: TargetDatabase, view: TargetView, offset: int, instruction: Instruction
) -> bool:
    """True if a guessed instruction fits: unclassified bytes, opcode bytes not read as data.

    An opcode byte read as data would have to hold two values (§5.5); an operand byte read
    as data is what executing the instruction does anyway. Inside a relocated block every
    byte was read as data by the copy to RAM, and the CPU executes the copied (decoded)
    values, so there the data reads say nothing against code (D-21, D-22).
    """
    if not view.fits(offset, instruction.length):
        return False
    opcode_bytes = 0 if view.block_at(offset) is not None else opcode_byte_count(instruction)
    for index in range(instruction.length):
        if database.kinds[offset + index] != Kind.UNKNOWN:
            return False
        opcode_read_as_data = (
            index < opcode_bytes
            and view.access is not None
            and view.access[offset + index] not in (UNKNOWN, M1)
        )
        if opcode_read_as_data:
            return False
    return True
