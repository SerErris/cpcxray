"""Tests of the code/data classifier and the static fill policy (§6.15; D-20, D-30, D-31)."""

from __future__ import annotations

from cpcxray.analysis.classifier import TargetView, build_observed, find_rom_header
from cpcxray.analysis.cpc_profile import CpcProfile
from cpcxray.analysis.database import Kind, Source, TargetDatabase
from cpcxray.analysis.provenance import RelocatedBlock
from cpcxray.analysis.targets import INSN_START
from cpcxray.decoding.shadow_rom import DATA, M1, UNKNOWN
from cpcxray.decoding.vortex import vortex_decode, vortex_mask
from tests.analysis.helpers import classified, plain_view


def scrambled_view() -> TargetView:
    """A scrambled ROM: `jp &C014` executed; `ld a,5 / ret` at &C014 never read.

    &C015 has the decode mask &28 (A2 and A4 set), so its clear value depends on the role.
    """
    clear = bytearray(0x40)
    clear[0:3] = bytes.fromhex("C314C0")
    clear[0x14:0x17] = bytes.fromhex("3E05C9")
    raw = bytearray(clear)
    for offset in (1, 2, 0x15):  # data bytes are stored scrambled
        raw[offset] = clear[offset] ^ vortex_mask(offset)
    access = bytearray([UNKNOWN] * 0x40)
    access[0], access[1], access[2] = M1, DATA, DATA
    image = bytes(vortex_decode(o, raw[o]) if access[o] != M1 else raw[o] for o in range(0x40))
    return TargetView("rom5", 0xC000, image, bytes(raw), bytes(access), vortex_decode)


def test_never_read_byte_depends_on_role() -> None:
    """Opcode reads of never-read bytes see the raw byte, data reads the decoded one."""
    view = scrambled_view()
    assert vortex_mask(0x15) == 0x28
    assert view.value(0x15, as_opcode=False) == 0x05
    assert view.value(0x15, as_opcode=True) == 0x05 ^ 0x28
    assert view.value(0x14, as_opcode=True) == 0x3E  # opcode stored clear


def test_static_descent_and_static_fill() -> None:
    """Static code is found from the observed jump; "static" fill keeps its opcode bytes raw."""
    view = scrambled_view()
    database = classified(view, starts=[0])
    assert database.kind_at(0) == Kind.INSN_START and database.sources[0] == Source.OBSERVED
    assert database.kind_at(1) == Kind.INSN_BODY
    assert [database.kind_at(o) for o in (0x14, 0x15, 0x16)] == [
        Kind.INSN_START, Kind.INSN_BODY, Kind.INSN_START,
    ]  # fmt: skip
    assert database.sources[0x14] == Source.STATIC
    static = view.final_image(database, "static")
    assert static[0x14:0x17] == bytes.fromhex("3E05C9")
    data = view.final_image(database, "data")
    assert data[0x14] == 0x3E ^ 0x28 and data[0x15] == 0x05
    assert view.final_image(database, "ff")[0x14] == 0xFF
    assert view.final_image(database, "raw")[0x15] == 0x2D


def test_static_descent_stops_at_ff_and_non_canonical() -> None:
    """&FF is never code; non-canonical encodings end a static path."""
    image = bytes.fromhex("00 FF 00") + bytes(5) + bytes.fromhex("ED 6B 00 00 C9")
    database = classified(plain_view(image), starts=[0])
    assert database.kind_at(1) == Kind.UNKNOWN
    database = classified(plain_view(bytes.fromhex("18 06") + bytes(6) + image[8:]), starts=[0])
    assert database.kind_at(8) == Kind.UNKNOWN  # ED 6B = non-canonical ld hl,(nn)


def test_rst_flow_kinds() -> None:
    """RST 1/5 end a path, RST 2/3 continue after their inline operand, RST 0 ends it."""
    image = bytes.fromhex("D7 00 40 00 CF 00 00 00 C7 00")
    database = classified(plain_view(image), starts=[0])
    assert database.kind_at(1) == database.kind_at(2) == Kind.RST_OPERAND
    assert database.kind_at(3) == Kind.INSN_START  # after the SIDE CALL
    assert database.kind_at(4) == Kind.INSN_START and database.kind_at(5) == Kind.RST_OPERAND
    assert database.kind_at(7) == Kind.UNKNOWN  # nothing after the LOW JUMP
    assert database.kind_at(0) == Kind.INSN_START
    # SIDE CALL slot 0 → &C000 + &0000: the target is followed (the start itself)


def test_far_object_classified() -> None:
    """A FAR CALL found statically marks its far address object (pointer inside the image)."""
    image = bytes.fromhex("DF 08 C0 C9 00 00 00 00 5A BB 07")
    database = classified(plain_view(image), starts=[0])
    assert [database.kind_at(o) for o in (8, 9, 10)] == [Kind.FAR_ADDR] * 3
    observed = classified(
        plain_view(image), starts=[0], observations={"C008": {"far_object": True}}, static=False
    )
    assert observed.kind_at(8) == Kind.FAR_ADDR and observed.sources[8] == Source.OBSERVED


def test_data_reads_are_byte_data() -> None:
    """Bytes read as data (and not part of an instruction) are observed BYTE_DATA."""
    view = scrambled_view()
    access = bytearray(view.access or b"")
    access[0x30] = DATA
    view = TargetView(view.name, view.start, view.image, view.raw, bytes(access), view.decode)
    database = classified(view, starts=[0], static=False)
    assert database.kind_at(0x30) == Kind.BYTE_DATA
    assert database.kind_at(1) == Kind.INSN_BODY  # operand: read as data, but code


def test_owner_overrides() -> None:
    """Owner code and data overrides win over observed and static classification."""
    view = plain_view(bytes.fromhex("3E 05 C9 41 42 43"))
    database = TargetDatabase(view.name, view.start, view.length)
    database.add_override(1, 2, Kind.STRING)
    database.add_override(3, 5, Kind.INSN_START)
    build_observed(database, view, bytes([INSN_START, 0, 0, 0, 0, 0]), CpcProfile())
    assert database.kind_at(0) == Kind.INSN_START  # observed, length cut by the owner string
    assert database.kind_at(1) == Kind.STRING and database.sources[1] == Source.OWNER
    assert [database.kind_at(o) for o in (3, 4, 5)] == [Kind.INSN_START] * 3  # ld b,c / ld b,d …
    assert database.sources[3] == Source.OWNER


def test_relocated_block_coordinates() -> None:
    """Inside a block, instructions are decoded at their RAM address."""
    image = bytes.fromhex("CD 00 80 C9") + bytes.fromhex("C3 00 80")
    view = plain_view(image, blocks=(RelocatedBlock("rom1", 4, 3, 0x8000, executed=1),))
    assert view.address(4) == 0x8000 and view.address(3) == 0xC003
    instruction = view.disassemble(4, None)
    assert instruction.address == 0x8000 and instruction.target == 0x8000
    assert view.offset_of_target(0x8000) == 4
    assert view.offset_of_target(0xC004) is None  # ROM address of the copy: ambiguous
    database = classified(view, starts=[0])
    assert database.kind_at(4) == Kind.INSN_START  # call &8000 followed into the block


def test_rom_header() -> None:
    """Header with names (incl. a hidden one) and jumpblock entries."""
    image = bytearray(0x40)
    image[0:6] = bytes.fromhex("01 01 02 03 20 C0")
    image[6:15] = bytes.fromhex("C3 30 C0 C3 31 C0 C3 32 C0")
    image[0x20:0x28] = b"TES\xd4CM\xc4\x81"
    image[0x30:0x33] = bytes.fromhex("C9 C9 C9")
    header = find_rom_header(plain_view(bytes(image)))
    assert header is not None
    assert [name for _, _, name in header.names] == ["TEST", "CMD", "&81"]
    assert header.jumpblock == (6, 9, 12)
    assert header.table_end == 0x28
    assert find_rom_header(plain_view(bytes(image), start=0x4000)) is None


def test_static_code_over_operands_read_as_data() -> None:
    """Operand bytes read as data do not block static code; opcode bytes read as data do."""
    view = scrambled_view()
    access = bytearray(view.access or b"")
    access[0x15] = DATA  # the operand of ld a,#05 was read (e.g. by a catalogue command)
    image = bytearray(view.image)
    image[0x15] = 0x05
    operand_read = TargetView(view.name, view.start, bytes(image), view.raw, bytes(access),
                              view.decode)  # fmt: skip
    database = classified(operand_read, starts=[0])
    assert database.kind_at(0x14) == Kind.INSN_START and database.kind_at(0x15) == Kind.INSN_BODY
    access[0x14] = DATA  # the opcode byte itself read as data: not code
    opcode_read = TargetView(view.name, view.start, bytes(image), view.raw, bytes(access),
                             view.decode)  # fmt: skip
    database = classified(opcode_read, starts=[0])
    assert database.kind_at(0x14) == Kind.BYTE_DATA
