"""Tests of the key matrix, timed typing and the key map from the OS ROM (phase 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.devices.key_map import CONTROL, SHIFT, KeyMap, KeyMapError
from cpcxray.devices.keyboard import (
    DEFAULT_GAP_US,
    DEFAULT_HOLD_US,
    MODIFIER_LEAD_US,
    Keyboard,
)

OS_ROM = Path(__file__).resolve().parents[2] / "roms" / "os464.rom"
needs_rom = pytest.mark.skipif(not OS_ROM.exists(), reason="OS ROM missing")


def test_sources_combine() -> None:
    """A key is down if any source holds it; releasing one source keeps the others."""
    keyboard = Keyboard()
    keyboard.press(66)  # api: row 8, bit 2
    keyboard.set_held("host", {66, 0})
    assert keyboard.read_row(8) == 0xFB and keyboard.read_row(0) == 0xFE
    keyboard.release_all("host")
    assert keyboard.read_row(8) == 0xFB and keyboard.read_row(0) == 0xFF
    keyboard.release(66)
    assert keyboard.held_keys() == [] and keyboard.read_row(8) == 0xFF
    assert keyboard.read_row(12) == 0xFF


def test_typing_schedule() -> None:
    """Each key is held 40 ms, then released 40 ms; SHIFT comes 20 ms before and after the key."""
    keyboard = Keyboard()
    end = keyboard.schedule_typing([(SHIFT, 27), (27,)], start_us=1000)
    lead = MODIFIER_LEAD_US
    assert end == 1000 + 2 * DEFAULT_HOLD_US + DEFAULT_GAP_US + 2 * lead
    timeline = [
        (1000, [SHIFT]),  # SHIFT alone first
        (1000 + lead, [SHIFT, 27]),
        (1000 + lead + DEFAULT_HOLD_US, [SHIFT]),  # the key goes before SHIFT
        (1000 + 2 * lead + DEFAULT_HOLD_US, []),
        (1000 + 2 * lead + DEFAULT_HOLD_US + DEFAULT_GAP_US, [27]),  # no modifier: no lead
    ]
    for time_us, held in timeline:
        keyboard.tick(time_us)
        assert keyboard.held_keys() == held, time_us
    more = keyboard.schedule_typing([(18,)], start_us=0)
    assert more > end  # follows the pending typing
    keyboard.tick(10**9)
    assert not keyboard.typing and keyboard.held_keys() == []


@needs_rom
def test_key_map_from_rom() -> None:
    """Names and characters come from the ROM tables (key = row * 8 + bit)."""
    key_map = KeyMap.from_rom(OS_ROM.read_bytes())
    assert len(key_map.names) == 79  # 80 keys, row 9 bit 6 is not connected
    expected = {
        "return": 18, "enter": 6, "del": 79, "esc": 66, "space": 47, "a": 69,
        "shift": SHIFT, "control": CONTROL, "cursor_up": 0, "f0": 15, "joy_fire1": 77,
    }  # fmt: skip
    assert {name: key_map.key_number(name) for name in expected} == expected
    assert key_map.key_number("RETURN") == 18  # names are case-insensitive (except chars)
    assert key_map.keys_for_character("p") == (27,)
    assert key_map.keys_for_character("P") == (SHIFT, 27)
    assert key_map.keys_for_character('"') == (SHIFT, 65)
    assert key_map.keys_for_character("\n") == (18,)  # newline types RETURN
    assert key_map.keys_for_character("X") == (SHIFT, 63)  # not the joystick fire button
    assert key_map.keys_for_character("\x01") == (CONTROL, 69)  # CONTROL-A


@needs_rom
def test_key_map_errors() -> None:
    """Unknown names list the known ones; untypable characters are reported."""
    key_map = KeyMap.from_rom(OS_ROM.read_bytes())
    with pytest.raises(KeyMapError, match=r"known: .*return"):
        key_map.key_number("enter_key")
    with pytest.raises(KeyMapError, match="no CPC key types"):
        key_map.keys_for_character("ä")


def test_rom_without_tables() -> None:
    """A ROM without keyboard tables is reported."""
    with pytest.raises(KeyMapError, match="no keyboard translation tables"):
        KeyMap.from_rom(bytes(0x4000))
