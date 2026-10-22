"""Tests of the sjasmplus source generator and the round-trip check (§6.15; D-22, D-32, D-33).

Every generated source is assembled with sjasmplus and must reproduce the image byte for byte.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cpcxray.analysis.provenance import RelocatedBlock
from cpcxray.analysis.source_gen import generate_source, split_data, string_operands
from cpcxray.analysis.verify import compare, verify_source
from cpcxray.cpu.disasm import disassemble
from cpcxray.debugger.symbols import SymbolTable
from cpcxray.debugger.variables import VariableMap
from tests.analysis.helpers import classified, plain_view, round_trip
from tests.analysis.test_classifier import scrambled_view


def code_lines(text: str) -> list[str]:
    """Return the source lines without surrounding whitespace."""
    return [line.strip() for line in text.splitlines()]


def test_cpc_strings() -> None:
    """Strings with a bit-7 terminator become "NAM","E"+#80; short runs stay bytes."""
    assert string_operands(b"WORL\xc4") == '"WORL","D"+#80'
    assert string_operands(b'A"B\x01') == '"A",#22,"B",#01'
    data = b"\x01\x02HELLO WORL\xc4AB\x03"
    assert split_data(data) == [(0, 2, False), (2, 11, True), (13, 3, False)]
    assert split_data(b"OK\xcb") == [(0, 3, True)]


def test_rst_rendering(tmp_path: Path) -> None:
    """RST 1/2/3/5 with their inline defw lines, the far address object and observations."""
    image = bytes.fromhex(
        "CF 50 81"  # rst #08  LOW JUMP to &0150, upper ROM off
        "D7 50 41"  # rst #10  SIDE CALL to &C150, slot 1
        "DF 10 C0"  # rst #18  FAR CALL via the far address object at &C010
        "EF 5A BB"  # rst #28  FIRM JUMP to &BB5A
        "C9 00 00 00"
        "5A BB 07"  # far address object: &BB5A in ROM 7
    )
    observations = {"C003": {"rst": 2, "reached": "rom6"}, "C010": {"far_object": True}}
    view = plain_view(image)
    database = classified(view, starts=[0, 3, 6], observations=observations)
    generated, result = round_trip(tmp_path, view, database)
    assert result.identical, result.differences
    lines = code_lines(generated.text)
    assert "rst #08                         ; LOW JUMP" in lines
    assert "defw 08150h                     ;   to #0150 [lower ROM on, upper ROM off]" in lines
    assert (
        "defw 04150h                     ;   to #C150 [slot 1]  (observed: reached rom6)" in lines
    )
    assert "defw far_c010                   ;   to TXT_OUTPUT [ROM 7]" in lines  # firmware name
    assert "far_c010:" in lines
    assert "defw TXT_OUTPUT                 ; far address: to TXT_OUTPUT" in lines
    assert "defb #07                        ;   ROM 7" in lines
    assert any(line.startswith("rst #28") for line in lines)


def test_labels_and_externals(tmp_path: Path) -> None:
    """Call/jump targets get labels; firmware addresses outside the image get EQU lines."""
    image = bytes.fromhex("CD 06 C0 C3 5A BB C9 18 FD")
    view = plain_view(image)
    database = classified(view, starts=[0])
    symbols = SymbolTable()
    generated = generate_source(
        view.resolve(database, "static"), database, symbols, title="t", output_name="t.bin",
        fill_policy="static",
    )  # fmt: skip
    lines = code_lines(generated.text)
    assert "TXT_OUTPUT              EQU #BB5A" in lines
    assert "call sub_c006" in lines and "sub_c006:" in lines
    assert "jp TXT_OUTPUT                   ; [static]" in lines
    assert generated.names == [(0xC006, "rom1", "sub_c006", "sub")]  # the Symbols tab's (5g)
    _, result = round_trip(tmp_path, view, database)
    assert result.identical


def test_relocated_block_uses_disp(tmp_path: Path) -> None:
    """A block copied to RAM is assembled at its RAM address with DISP/ENT and ram_ labels."""
    image = bytes.fromhex("CD 00 80 C9") + bytes.fromhex("21 00 80 C3 00 80")
    block = RelocatedBlock("rom1", 4, 6, 0x8000, executed=2)
    view = plain_view(image, blocks=(block,))
    database = classified(view, starts=[0, 4, 7])
    generated, result = round_trip(tmp_path, view, database)
    assert result.identical, result.differences
    lines = code_lines(generated.text)
    assert "DISP #8000" in lines
    assert "ENT                             ; end of DISP #8000" in lines
    assert "; ---- end of RAM copy #8000-#8005 (from #C004) ----" in lines
    assert "call ram_8000" in lines and "ram_8000:" in lines and "jp ram_8000" in lines
    assert any("copied to RAM #8000-#8005 (from #C004)" in line for line in lines)


def test_position_independent_copy_stays_at_rom_address(tmp_path: Path) -> None:
    """A RAM copy shown at its ROM address gets a comment line, no DISP (D-22)."""
    image = bytes.fromhex("C9 00 C3 00 C0 C3 00 C0")  # ret / nop / jumpblock (2 entries)
    copy = RelocatedBlock("rom1", 5, 3, 0xAD0E, executed=1)  # starts at the second entry
    view = replace(plain_view(image), ram_copies=(copy,))
    database = classified(view, starts=[0, 2, 5])
    generated, result = round_trip(tmp_path, view, database)
    assert result.identical, result.differences
    lines = code_lines(generated.text)
    marker = (
        "; ---- #C005-#C007 copied to RAM #AD0E-#AD10 and executed there"
        " (position-independent) ----"
    )
    assert marker in lines
    assert lines[lines.index(marker) + 1] == "jp l_c000"
    assert "DISP #AD0E" not in lines


def non_canonical_encodings() -> list[bytes]:
    """Every encoding the disassembler flags as non-canonical (D-32), with operands."""
    candidates = [bytes([0xED, op, 0x34, 0x12]) for op in range(256)]
    for prefix in (0xDD, 0xFD):
        candidates += [bytes([prefix, op, 0x34, 0x12, 0x00]) for op in range(256)]
        candidates += [bytes([prefix, 0xCB, 0x05, op]) for op in range(256)]
    found = []
    for candidate in candidates:
        padded = candidate + bytes(4)
        instruction = disassemble(lambda a, data=padded: data[a], 0)
        if instruction.non_canonical:
            found.append(instruction.raw)
    return found


def test_every_non_canonical_encoding_round_trips(tmp_path: Path) -> None:
    """All non-canonical encodings are emitted as defb with the mnemonic and reassemble (D-32)."""
    encodings = non_canonical_encodings()
    assert len(encodings) > 400
    image = b"".join(encodings)
    starts, offset = [], 0
    for encoding in encodings:
        starts.append(offset)
        offset += len(encoding)
    view = plain_view(image, start=0x4000, name="lower")
    database = classified(view, starts=starts, static=False)
    generated, result = round_trip(tmp_path, view, database)
    assert result.identical, result.differences[:5]
    assert generated.statistics["non_canonical"] == len(encodings)
    assert "defb #ED,#6B,#34,#12            ; ld hl,(#1234)" in code_lines(generated.text)


def test_scrambled_never_accessed_and_static(tmp_path: Path) -> None:
    """Never-read bytes are marked; static code is disassembled and reassembles with "static"."""
    view = scrambled_view()
    database = classified(view, starts=[0])
    generated, result = round_trip(tmp_path, view, database)
    assert result.identical, result.differences
    lines = code_lines(generated.text)
    assert "; ---- never accessed: #C003-#C03F (61 bytes, fill: static) ----" in lines
    assert any(line.startswith("ld a,#05") and "[static]" in line for line in lines)
    assert "jp l_c014" in lines
    for policy in ("raw", "data", "ff"):  # no static code: the bytes are emitted as data
        database = classified(view, starts=[0], static=False)
        _, result = round_trip(tmp_path, view, database, policy)
        assert result.identical, (policy, result.differences)


def test_rom_header_rendering(tmp_path: Path) -> None:
    """The ROM header gets its labels, the name table strings and RSX labels."""
    image = bytearray(0x40)
    image[0:6] = bytes.fromhex("01 01 02 03 20 C0")
    image[6:15] = bytes.fromhex("C3 30 C0 C3 31 C0 C3 32 C0")
    image[0x20:0x29] = b"TES\xd4CM\xc4\x81\x00"
    image[0x30:0x33] = bytes.fromhex("C9 C9 C9")
    view = plain_view(bytes(image))
    database = classified(view)
    generated, result = round_trip(tmp_path, view, database)
    assert result.identical, result.differences
    lines = code_lines(generated.text)
    expected_lines = (
        "rom_type:", "rom_name_pointer:", "rom_name_table:", "rom_jumpblock:", "rom_init:",
        "rsx_cmd:", "rsx_81:",
        'defb "TES","T"+#80', "defw rom_name_table             ; name table",
        'jp rsx_cmd                      ; RSX "CMD"; [static]',
    )  # fmt: skip
    for expected in expected_lines:
        assert expected in lines, expected
    assert "defb #01                        ; background ROM" in lines


def test_owner_labels_and_comments() -> None:
    """Owner labels replace RSX names; owner comments end code lines and start data lines."""
    image = bytearray(0x40)
    image[0:6] = bytes.fromhex("01 01 02 03 20 C0")
    image[6:15] = bytes.fromhex("C3 30 C0 C3 31 C0 C3 32 C0")
    image[0x20:0x29] = b"TES\xd4CM\xc4\x81\x00"
    image[0x30:0x33] = bytes.fromhex("C9 C9 C9")
    view = plain_view(bytes(image))
    database = classified(view)
    symbols = SymbolTable()
    symbols.add(0xC031, "run_command", "the CMD routine", "rom1")
    symbols.add(0xC038, "table", None, "rom1")
    symbols.add(0xC03C, None, "second half", "rom1")
    symbols.add(0xC000, None, "the ROM type", "rom1")
    generated = generate_source(
        view.resolve(database, "static"), database, symbols, title="t", output_name="t.bin",
        fill_policy="static",
    )  # fmt: skip
    lines = code_lines(generated.text)
    assert "run_command:" in lines and "rsx_cmd:" not in lines
    assert 'jp run_command                  ; RSX "CMD"; [static]' in lines
    assert "ret                             ; the CMD routine; [static]" in lines
    assert "table:" in lines
    assert "defb #00,#00,#00,#00            ; second half" in lines
    assert "defb #01                        ; background ROM; the ROM type" in lines


def test_compare_reports_lines() -> None:
    """Differences carry both values and the source line."""
    result = compare(b"\x01\x02", b"\x01\x03\x04", 0xC000, ["a", "defb 2,3"], [1, 2, 2])
    assert not result.identical and result.difference_count == 2
    assert result.differences[0] == {
        "address": "C001", "expected": "03", "assembled": "02", "line": 2, "source": "defb 2,3",
    }  # fmt: skip
    assert result.differences[1]["assembled"] is None


def test_line_map_covers_every_byte(tmp_path: Path) -> None:
    """The generator maps every target byte to a source line."""
    view = scrambled_view()
    database = classified(view, starts=[0])
    generated, _ = round_trip(tmp_path, view, database)
    assert all(line > 0 for line in generated.line_of_offset)


def test_overlay_blocks_share_a_ram_range(tmp_path: Path) -> None:
    """Two routines run at the same RAM address (overlays): each gets its DISP and labels."""
    image = bytes.fromhex(
        "C9 00 00 00"  # &C000 ret
        "00 18 FD"  # &C004 → RAM &8000: nop / jr &8000
        "00 00 18 FC"  # &C007 → RAM &8000: nop / nop / jr &8000
    )
    blocks = (RelocatedBlock("rom1", 4, 3, 0x8000, executed=3),
              RelocatedBlock("rom1", 7, 4, 0x8000, executed=4))  # fmt: skip
    view = plain_view(image, blocks=blocks)
    database = classified(view, starts=[0, 4, 5, 7, 8, 9])
    generated, result = round_trip(tmp_path, view, database)
    assert result.identical, result.differences
    lines = code_lines(generated.text)
    assert lines.count("DISP #8000") == 2
    assert "ram_8000_c004:" in lines and "jr ram_8000_c004" in lines
    assert "ram_8000_c007:" in lines and "jr ram_8000_c007" in lines


def test_immediates_labels_only_for_addresses(tmp_path: Path) -> None:
    """LDIR's HL/DE become labels, its BC stays a number; low firmware addresses stay hex."""
    image = (
        bytes.fromhex(
            "21 15 C0"  # ld hl,&C015: source of the copy, the "DATA" bytes
            "11 00 BF"  # ld de,&BF00: destination
            "01 16 00"  # ld bc,&0016: length - also the address of the firmware's JP_DE
            "ED B0"  # ldir
            "11 0E 00"  # ld de,&000E: no evidence, low: a number, not JP_BC
            "21 5A BB"  # ld hl,&BB5A: no evidence, firmware jumpblock: TXT_OUTPUT
            "C9"
        )
        + bytes(3)
        + b"DATA"
    )
    view = plain_view(image)
    database = classified(view, starts=[0, 3, 6, 9, 11, 14, 17])
    generated, result = round_trip(tmp_path, view, database)
    assert result.identical, result.differences
    lines = code_lines(generated.text)
    assert "ld hl,d_c015" in lines and "d_c015:" in lines
    assert "ld de,#BF00" in lines  # an address, but no label known there (outside the image)
    assert "ld bc,#0016" in lines and "ld de,#000E" in lines
    assert "ld hl,TXT_OUTPUT" in lines
    reference = "JP_DE                   EQU #0016       ; PCDE INSTRUCTION"
    assert reference in lines  # the reference block names every restart ...
    assert "ld bc,#0016" in lines  # ... but a number stays a number
    assert "; firmware restarts and low kernel entries (464/664/6128), for reference" in lines
    assert lines.count("TXT_OUTPUT              EQU #BB5A") == 1  # used entries: own block


def test_far_call_into_another_rom_gets_no_own_label(tmp_path: Path) -> None:
    """A FAR CALL to &C008 in ROM 7, made from ROM 5, must not use ROM 5's label at &C008."""
    image = bytes.fromhex(
        "DF 0B C0"  # rst #18 via the far object at &C00B
        "CD 08 C0"  # call &C008 (ROM 5's own routine there gets a label)
        "C9 00"
        "C9"  # &C008: ret
        "00 00"
        "08 C0 07"  # &C00B far object: &C008 in ROM 7
    )
    view = plain_view(image, name="rom5")
    database = classified(view, starts=[0, 3, 6, 8], observations={"C00B": {"far_object": True}})
    generated, result = round_trip(tmp_path, view, database)
    assert result.identical, result.differences
    lines = code_lines(generated.text)
    assert "call sub_c008" in lines and "sub_c008:" in lines  # own code: labelled
    assert "defw #C008                      ; far address: to #C008" in lines  # ROM 7: hex
    assert any(line.startswith("defw far_c00b") and "to #C008 [ROM 7]" in line for line in lines)


def test_variables_name_outside_addresses(tmp_path: Path) -> None:
    """Known variables name outside references (LABEL+n), with one EQU each (FR-DBG-11)."""
    image = bytes.fromhex(
        "3A D9 B8"  # ld a,(&B8D9): KL_FOREGROUND_ROM
        "32 B6 B8"  # ld (&B8B6),a: inside KL_TIME
        "21 B4 B8"  # ld hl,&B8B4: no evidence, but the first byte of KL_TIME
        "21 B5 B8"  # ld hl,&B8B5: no evidence, inside a variable: a number
        "2A 5A BB"  # ld hl,(&BB5A): a firmware symbol wins
        "C9"
    )
    view = plain_view(image)
    database = classified(view, starts=[0, 3, 6, 9, 12, 15])
    variables = tuple(VariableMap("6128").variables(bytes(0x10000).__getitem__))
    generated = generate_source(
        view.resolve(database, "static"), database, SymbolTable(model="6128"), title="t",
        output_name="t.bin", fill_policy="static", variables=variables,
    )  # fmt: skip
    lines = code_lines(generated.text)
    assert "ld a,(KL_FOREGROUND_ROM)" in lines and "ld (KL_TIME+2),a" in lines
    assert "ld hl,KL_TIME" in lines and "ld hl,#B8B5" in lines
    assert "ld hl,(TXT_OUTPUT)" in lines
    assert "; firmware and AMSDOS variables used by this code" in lines
    equ = "KL_TIME                 EQU #B8B4"
    assert any(line.startswith(equ) and "4 bytes: elapsed time" in line for line in lines)
    assert sum(line.startswith("KL_TIME ") for line in lines) == 1
    source = tmp_path / "vars.asm"
    source.write_text(generated.text, encoding="utf-8")
    result = verify_source(source, view.final_image(database, "static"), view.start,
                           generated.line_of_offset)  # fmt: skip
    assert result.identical, result.differences


def test_long_labels_keep_a_space_before_equ(tmp_path: Path) -> None:
    """A label of 24 characters or more is still separated from EQU (sjasmplus needs it)."""
    image = bytes.fromhex("3A 60 BE C9")  # ld a,(&BE60): AMSDOS_RECORD_BUFFER_POINTER
    view = plain_view(image)
    database = classified(view, starts=[0, 3])
    memory = bytearray(0x10000)
    memory[0xB1A6:0xB1A8] = (0xA6FC).to_bytes(2, "little")  # 464 ROM chain: AMSDOS's block
    memory[0xBE7D:0xBE7F] = (0xA700).to_bytes(2, "little")
    variables = tuple(VariableMap("464").variables(memory.__getitem__))
    generated = generate_source(
        view.resolve(database, "static"), database, SymbolTable(), title="t",
        output_name="t.bin", fill_policy="static", variables=variables,
    )  # fmt: skip
    lines = code_lines(generated.text)
    assert any(line.startswith("AMSDOS_RECORD_BUFFER_POINTER EQU #BE60") for line in lines)
    source = tmp_path / "long.asm"
    source.write_text(generated.text, encoding="utf-8")
    result = verify_source(source, view.final_image(database, "static"), view.start,
                           generated.line_of_offset)  # fmt: skip
    assert result.identical, result.differences
