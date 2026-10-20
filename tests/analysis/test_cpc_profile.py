"""Tests of the CPC RST profile against the output format of design.md §6.15 (FR-DIS-01..03)."""

from __future__ import annotations

import pytest

from cpcxray.analysis.cpc_profile import CpcProfile, rom_select_text
from cpcxray.cpu.disasm import Flow, Instruction, disassemble


def decode(code: bytes, profile: CpcProfile | None = None, labels: dict[int, str] | None = None,
           extra: dict[int, bytes] | None = None) -> Instruction:  # fmt: skip
    """Disassemble `code` at &4000 with the CPC profile; `extra` places more bytes."""
    memory = bytearray(0x10000)
    memory[0x4000 : 0x4000 + len(code)] = code
    for address, data in (extra or {}).items():
        memory[address : address + len(data)] = data
    lookup = (labels or {}).get
    return disassemble(lambda a: memory[a], 0x4000, profile or CpcProfile(), lookup)


@pytest.mark.parametrize(
    ("opcode", "name", "flow"),
    [(0xC7, "RESET", Flow.NO_RETURN), (0xE7, "RAM LAM", Flow.SEQUENTIAL),
     (0xF7, "USER RESTART", Flow.SEQUENTIAL), (0xFF, "INTERRUPT", Flow.NO_RETURN)],
)  # fmt: skip
def test_single_byte_restarts(opcode: int, name: str, flow: Flow) -> None:
    """RST 0, 4, 6, 7 are one byte with the function name as comment."""
    instruction = decode(bytes([opcode, 0x50, 0x81]))
    assert instruction.length == 1
    assert instruction.lines() == [(f"rst #{opcode & 0x38:02X}", name)]
    assert instruction.flow == flow


def test_low_jump() -> None:
    """RST 1: inline low address; bit 15 = upper ROM off, bit 14 = lower ROM off (§5.6)."""
    instruction = decode(bytes([0xCF, 0x50, 0x81]))
    assert instruction.length == 3
    assert instruction.lines() == [
        ("rst #08", "LOW JUMP"),
        ("defw 08150h", "  to #0150 [lower ROM on, upper ROM off]"),
    ]
    assert (instruction.flow, instruction.target) == (Flow.JUMP, 0x0150)


@pytest.mark.parametrize(
    ("word", "state"),
    [(0x0150, "lower ROM on, upper ROM on"), (0x4150, "lower ROM off, upper ROM on"),
     (0x8150, "lower ROM on, upper ROM off"), (0xC150, "lower ROM off, upper ROM off")],
)  # fmt: skip
def test_low_jump_rom_states(word: int, state: str) -> None:
    """All four RST 1 ROM states are spelled out in words."""
    instruction = decode(bytes([0xCF, word & 0xFF, word >> 8]))
    assert instruction.lines()[1][1].endswith(f"[{state}]")


def test_side_call() -> None:
    """RST 2: bits 15-14 relative slot, bits 13-0 + &C000 the routine."""
    instruction = decode(bytes([0xD7, 0x50, 0x41]))
    assert instruction.lines() == [
        ("rst #10", "SIDE CALL"),
        ("defw 04150h", "  to #C150 [slot 1]"),
    ]
    assert (instruction.flow, instruction.target) == (Flow.CALL, 0xC150)


def test_far_call_with_symbols() -> None:
    """RST 3: the operand points to a far address object; target and ROM in the comment."""
    labels = {0xC7F3: "far_c7f3", 0xBB5A: "scr_set_mode"}
    far_object = {0xC7F3: bytes([0x5A, 0xBB, 0x07])}
    instruction = decode(bytes([0xDF, 0xF3, 0xC7]), labels=labels, extra=far_object)
    assert instruction.lines(labels.get) == [
        ("rst #18", "FAR CALL"),
        ("defw far_c7f3", "  to scr_set_mode [ROM 7]"),
    ]
    assert (instruction.flow, instruction.target) == (Flow.CALL, 0xBB5A)


def test_far_call_without_symbols_and_out_of_range() -> None:
    """Without labels hex is shown; a pointer outside the image is not dereferenced."""
    far_object = {0xC7F3: bytes([0x5A, 0xBB, 0xFD])}
    instruction = decode(bytes([0xDF, 0xF3, 0xC7]), extra=far_object)
    assert instruction.lines()[1] == ("defw #C7F3", "  to #BB5A [UR=on, LR=off]")
    outside = decode(bytes([0xDF, 0xF3, 0xC7]), CpcProfile(image_range=(0x4000, 0x7FFF)))
    assert outside.lines()[1] == ("defw #C7F3", "  (out of range)")
    assert outside.target is None


def test_firm_jump() -> None:
    """RST 5: inline jump target, label in the defw, hex in the comment."""
    instruction = decode(bytes([0xEF, 0x38, 0x00]), labels={0x0038: "sub_0038"})
    assert instruction.lines({0x0038: "sub_0038"}.get) == [
        ("rst #28", "FIRM JUMP"),
        ("defw sub_0038", "  to #0038"),
    ]
    assert (instruction.flow, instruction.target) == (Flow.JUMP, 0x0038)


def test_user_restart_with_inline_bytes() -> None:
    """RST 6 takes the configured number of inline bytes, shown as defb."""
    instruction = decode(bytes([0xF7, 0x12, 0x34]), CpcProfile(user_restart_inline_length=2))
    assert instruction.length == 3
    assert instruction.lines() == [("rst #30", "USER RESTART"), ("defb #12,#34", "  inline data")]


@pytest.mark.parametrize(
    ("select", "text"),
    [(0, "ROM 0"), (7, "ROM 7"), (251, "ROM 251"), (252, "UR=on, LR=on"),
     (253, "UR=on, LR=off"), (254, "UR=off, LR=on"), (255, "UR=off, LR=off")],
)  # fmt: skip
def test_rom_select_text(select: int, text: str) -> None:
    """The far-address ROM select byte is explained as in §6.15."""
    assert rom_select_text(select) == text


def test_generic_decoding_without_profile() -> None:
    """Without a profile an RST is a plain one-byte call."""
    memory = bytearray(0x10000)
    memory[0:3] = bytes([0xCF, 0x50, 0x81])
    instruction = disassemble(lambda a: memory[a], 0)
    assert (instruction.length, instruction.flow, instruction.target) == (1, Flow.CALL, 0x08)
