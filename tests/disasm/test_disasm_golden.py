"""Golden tests of the disassembler over every opcode (design.md §8, phase 3; FR-DIS-05, D-32).

Every opcode of every prefix group (unprefixed, CB, ED, DD, FD, DD CB, FD CB) is placed in its
own 8-byte slot from &8000 with fixed operand bytes, so each instruction's address is known.

* Round trip: a sjasmplus source of all canonical instructions (`text()`), with non-canonical
  ones as `defb`, must assemble to exactly the original bytes.
* Flag check: assembling the mnemonics of the non-canonical instructions must give different
  bytes, otherwise they would not need the flag.
* Golden file: the listing of all instructions (bytes, text, flow, flags) must match
  tests/data/golden/disasm_all_opcodes.txt. After an intended change, regenerate it with
  `CPCXRAY_UPDATE_GOLDEN=1 pytest tests/disasm`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from cpcxray.cpu.disasm import Instruction, disassemble

GOLDEN_FILE = Path(__file__).resolve().parents[1] / "data" / "golden" / "disasm_all_opcodes.txt"
SLOT_BASE = 0x8000
SLOT_SIZE = 8
OPERAND_BYTES = bytes([0x34, 0x12, 0x56])  # immediate/word operands: &34, &1234, ...
NEGATIVE_DISPLACEMENT = 0xFB  # -5, used for the indexed forms in a second pass


def all_encodings() -> list[bytes]:
    """Return one byte sequence per opcode form, with operand bytes appended."""
    encodings = []
    for opcode in range(256):
        if opcode not in (0xCB, 0xDD, 0xED, 0xFD):
            encodings.append(bytes([opcode]) + OPERAND_BYTES)
    encodings += [bytes([0xCB, opcode]) for opcode in range(256)]
    encodings += [bytes([0xED, opcode]) + OPERAND_BYTES for opcode in range(256)]
    for prefix in (0xDD, 0xFD):
        for opcode in range(256):
            if opcode != 0xCB:
                encodings.append(bytes([prefix, opcode]) + OPERAND_BYTES)
        for displacement in (0x05, NEGATIVE_DISPLACEMENT):
            encodings += [bytes([prefix, 0xCB, displacement, opcode]) for opcode in range(256)]
    return encodings


def disassemble_slots() -> list[Instruction]:
    """Place every encoding in its slot and disassemble it there."""
    memory = bytearray(0x10000)
    instructions = []
    for slot, encoding in enumerate(all_encodings()):
        address = SLOT_BASE + slot * SLOT_SIZE
        memory[address : address + len(encoding)] = encoding
        instructions.append(disassemble(lambda a: memory[a], address))
    return instructions


def assemble(lines: list[str], workdir: Path) -> bytes:
    """Assemble one source line per slot with sjasmplus; return the bytes from &8000.

    Each line is followed by `ALIGN 8,0`, which pads the slot with zeros, so slot n of the
    output starts at offset 8*n.
    """
    source = ['    OUTPUT "out.bin"', f"    ORG #{SLOT_BASE:04X}"]
    for line in lines:
        source += [f"    {line}", "    ALIGN 8,0"]
    (workdir / "slots.asm").write_text("\n".join(source) + "\n")
    result = subprocess.run(
        ["sjasmplus", "--nologo", "slots.asm"],
        cwd=workdir, capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    return (workdir / "out.bin").read_bytes()


def slot_bytes(binary: bytes, slot: int, length: int) -> bytes:
    """Return the first `length` bytes of a slot in an assembled binary."""
    return binary[slot * SLOT_SIZE : slot * SLOT_SIZE + length]


def as_defb(instruction: Instruction) -> str:
    """Return the instruction's bytes as a `defb` line."""
    return "defb " + ",".join(f"#{byte:02X}" for byte in instruction.raw)


def golden_line(instruction: Instruction) -> str:
    """Format one golden-file line: bytes, text, flow and flags."""
    flags = ("nc " if instruction.non_canonical else "") + (
        "ud" if instruction.undocumented else ""
    )
    raw = " ".join(f"{byte:02X}" for byte in instruction.raw)
    return f"{raw:<12} {instruction.text():<24} {instruction.flow.value:<18} {flags}".rstrip()


requires_sjasmplus = pytest.mark.skipif(
    shutil.which("sjasmplus") is None, reason="sjasmplus not on the PATH"
)


@requires_sjasmplus
def test_canonical_instructions_reassemble_byte_identically(tmp_path: Path) -> None:
    """Disassembled text of every canonical instruction assembles to the original bytes."""
    instructions = disassemble_slots()
    lines = [as_defb(i) if i.non_canonical else i.text() for i in instructions]
    binary = assemble(lines, tmp_path)
    mismatches = [
        f"{i.address:04X} {i.text()}: {slot_bytes(binary, n, i.length).hex()} != {i.raw.hex()}"
        for n, i in enumerate(instructions)
        if slot_bytes(binary, n, i.length) != i.raw
    ]
    assert not mismatches, "\n".join(mismatches[:20])


@requires_sjasmplus
def test_non_canonical_flag_is_needed(tmp_path: Path) -> None:
    """Every instruction flagged non-canonical assembles to different bytes from its text."""
    instructions = disassemble_slots()  # same slots, so relative jump targets stay in range
    lines = [i.text() if i.non_canonical else as_defb(i) for i in instructions]
    binary = assemble(lines, tmp_path)
    identical = [
        f"{i.raw.hex()} {i.text()}"
        for n, i in enumerate(instructions)
        if i.non_canonical and slot_bytes(binary, n, i.length) == i.raw
    ]
    assert not identical, "flagged but canonical:\n" + "\n".join(identical[:20])


def test_golden_listing() -> None:
    """The listing of all opcode forms matches the reviewed golden file."""
    listing = "\n".join(golden_line(i) for i in disassemble_slots()) + "\n"
    if os.environ.get("CPCXRAY_UPDATE_GOLDEN") == "1":
        GOLDEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_FILE.write_text(listing)
    assert GOLDEN_FILE.exists(), "golden file missing; run with CPCXRAY_UPDATE_GOLDEN=1"
    assert listing == GOLDEN_FILE.read_text()


def test_lengths_cover_every_form() -> None:
    """Every form decodes to 1-4 bytes; prefix-only forms are single defb bytes."""
    for instruction in disassemble_slots():
        assert 1 <= instruction.length <= 4, instruction.text()
        if instruction.text().startswith("defb"):
            assert instruction.length == 1
