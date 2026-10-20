"""Tests of the Vortex rule, scrambled ROM slots, shadow ROM and access maps (design.md §8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.decoding.m1map import (
    AccessMapError,
    access_ranges,
    decode_rom,
    load_m1map,
    merge_maps,
    ranges_text,
    save_m1map,
)
from cpcxray.decoding.shadow_rom import DATA, ERROR, M1, UNKNOWN, ShadowRom, merge_class
from cpcxray.decoding.vortex import vortex_decode, vortex_mask
from cpcxray.machine.cpc464 import CPC464
from cpcxray.machine.memory import RomSlot


def scramble(clear: bytes, m1_offsets: set[int]) -> bytes:
    """Encode a clear image: data bytes XOR mask, M1 bytes stay clear (§5.5)."""
    return bytes(b if i in m1_offsets else vortex_decode(i, b) for i, b in enumerate(clear))


@pytest.mark.parametrize(
    ("address", "mask"), [(0x00, 0x00), (0x04, 0x20), (0x10, 0x08), (0x14, 0x28), (0xC037, 0x28)]
)
def test_vortex_mask_cases(address: int, mask: int) -> None:
    """A2 flips D5, A4 flips D3; only the low address bits matter."""
    assert vortex_mask(address) == mask
    assert vortex_decode(address, 0x00) == mask
    assert vortex_decode(address, vortex_decode(address, 0x5A)) == 0x5A  # its own inverse


def test_slot_m1_raw_data_decoded_and_switch() -> None:
    """M1 reads give the raw byte, data reads the decoded one; the switch acts at once."""
    data = bytearray(0x4000)
    data[0x04] = 0x3E
    rom = RomSlot(bytes(data), "test", vortex_decode)
    assert rom.read(0x04, is_m1=True) == 0x3E
    assert rom.read(0x04, is_m1=False) == 0x3E ^ 0x20
    assert rom.shadow is not None
    assert rom.peek(0x05) == 0x20 and rom.shadow.hit_count[0x05] == 0  # peek records nothing
    rom.decode_enabled = False
    assert rom.read(0x04, is_m1=False) == 0x3E
    assert rom.shadow.undecoded_reads[0x04] == 1


def test_access_class_update_and_merge_rules() -> None:
    """Unknown → class; same class stays; the other class → error; error stays (D-19)."""
    assert merge_class(UNKNOWN, M1) == M1
    assert merge_class(M1, M1) == M1
    assert merge_class(M1, DATA) == ERROR
    assert merge_class(ERROR, DATA) == ERROR
    assert merge_class(DATA, UNKNOWN) == DATA
    shadow = ShadowRom(bytes(0x4000))
    shadow.record(0x10, 0, is_m1=True, decoding=True)
    shadow.record(0x10, 0, is_m1=False, decoding=True)
    shadow.record(0x20, 0, is_m1=False, decoding=False)  # class counts even with decoding off
    assert (shadow.access_map[0x10], shadow.access_map[0x20]) == (ERROR, DATA)


def test_conflicts_real_and_harmless() -> None:
    """A conflict is harmless where the mask is zero, real elsewhere (D-18)."""
    shadow = ShadowRom(bytes(range(256)) * 64)
    for offset in (0x01, 0x05):
        shadow.record(offset, 0, True, True)
        shadow.record(offset, 0, False, True)
    conflicts = {c.offset: c for c in shadow.conflicts()}
    assert conflicts[0x01].harmless and not conflicts[0x05].harmless
    assert (conflicts[0x05].as_m1, conflicts[0x05].as_data) == (0x05, 0x25)


def test_m1map_file_merge_and_ranges(tmp_path: Path) -> None:
    """Map files are exactly 16 KB; merging follows D-19; ranges read like the design."""
    first = bytearray([UNKNOWN] * 0x4000)
    first[0:4] = bytes([DATA] * 4)
    first[4:6] = bytes([M1] * 2)
    second = bytearray([UNKNOWN] * 0x4000)
    second[3] = M1
    path = tmp_path / "rom.m1map"
    save_m1map(path, first)
    merged = merge_maps(load_m1map(path), bytes(second))
    assert merged[0:6] == bytes([DATA, DATA, DATA, ERROR, M1, M1])
    assert ranges_text(access_ranges(merged)).splitlines()[:3] == [
        "C000-C002  data          3",
        "C003-C003  conflict      1",
        "C004-C005  M1            2",
    ]
    (tmp_path / "short.m1map").write_bytes(bytes(10))
    with pytest.raises(AccessMapError):
        load_m1map(tmp_path / "short.m1map")


@pytest.mark.parametrize(
    ("policy", "expected"), [("raw", 0x77), ("data", 0x77 ^ 0x28), ("ff", 0xFF)]
)
def test_fill_policies(policy: str, expected: int) -> None:
    """Never-read bytes follow the fill policy (D-20)."""
    raw = bytearray(0x4000)
    raw[0x14] = 0x77
    access_map = bytes([UNKNOWN] * 0x4000)
    assert decode_rom(bytes(raw), access_map, vortex_decode, policy)[0x14] == expected


def test_static_policy_needs_the_classifier() -> None:
    """decode_rom cannot do the static guess: it needs the classification (TargetView)."""
    with pytest.raises(AccessMapError, match="classifier"):
        decode_rom(bytes(0x4000), bytes([UNKNOWN] * 0x4000), vortex_decode, "static")


def test_cpu_in_scrambled_rom_and_decode_from_map() -> None:
    """Code runs correctly from a scrambled ROM, and decoding from the map matches it.

    Operands, and the d and op of DD CB d op, are decoded; the clear ROM rebuilt from the raw
    image and the access map equals what the CPU saw.
    """
    clear = bytearray(0x4000)
    # &C000: LD IX,&8000 / SET 3,(IX+&15) / LD A,(&C020) / HALT;  &C020: data &5A
    program = bytes.fromhex("DD210080 DDCB15DE 3A20C0 76")
    clear[: len(program)] = program
    clear[0x20] = 0x5A
    m1 = {0, 1, 4, 5, 8, 11}  # DD 21 | DD CB | 3A | 76 (d and op of DD CB are not M1)
    machine = CPC464()
    lower = bytearray(0x4000)
    lower[0:8] = bytes.fromhex("01817F ED49 C300C0")  # RMR &81: upper ROM on; JP &C000
    machine.memory.set_lower_rom(RomSlot(bytes(lower), "test-os"))
    machine.memory.set_upper_rom(0, RomSlot(scramble(bytes(clear), m1), "scrambled", vortex_decode))
    machine.reset()
    for _ in range(7):
        machine.step()
    assert machine.cpu.a == 0x5A
    assert machine.memory.ram[0x8015] == 0x08  # SET 3 executed: d and op decoded
    shadow = machine.memory.upper_roms[0].shadow
    assert shadow is not None
    assert [shadow.access_map[i] for i in (4, 5, 6, 7)] == [M1, M1, DATA, DATA]
    rebuilt = decode_rom(shadow.raw, bytes(shadow.access_map), vortex_decode, "raw")
    for offset, access_class in enumerate(shadow.access_map):
        if access_class == M1:
            assert rebuilt[offset] == shadow.m1_values[offset] == clear[offset]
        elif access_class == DATA:
            assert rebuilt[offset] == shadow.data_values[offset] == clear[offset]
