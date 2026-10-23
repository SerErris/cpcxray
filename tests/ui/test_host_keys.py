"""Headless tests of the PC keyboard by character (task 6, ui/host_keys.py, D-40).

PC key codes are plain numbers here (Dear PyGui is not needed); the CPC side is the key map of
the real 464 OS ROM (skipped without it), so the expectations are the firmware's own table.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.devices.key_map import CONTROL, SHIFT, KeyMap
from cpcxray.ui.host_keys import CharacterInput, ComboWatch, HostKeyboard, HostKeyCodes

OS_ROM = Path(__file__).resolve().parents[2] / "src" / "cpcxray" / "roms" / "os464.rom"
pytestmark = pytest.mark.skipif(not OS_ROM.exists(), reason="OS ROM missing")

# PC key codes for the tests (any numbers): letters 1xx, digits 2xx, others below
KEY_A, KEY_E, KEY_7, KEY_8, SPACE = 101, 105, 207, 208, 10
LSHIFT, LCONTROL, RALT, CAPS = 20, 21, 22, 23
NUMPAD1, CURSOR_UP, RETURN = 31, 32, 33
CODES = HostKeyCodes(
    functions={NUMPAD1: "f1", CURSOR_UP: "cursor_up", RETURN: "return"},
    letters={KEY_A: "a", KEY_E: "e"},
    shift=frozenset({LSHIFT}),
    control=frozenset({LCONTROL}),
    ignored=frozenset({LSHIFT, LCONTROL, RALT, CAPS}),
)


@pytest.fixture(name="key_map")
def fixture_key_map() -> KeyMap:
    """The CPC key map of the 464 OS ROM."""
    return KeyMap.from_rom(OS_ROM.read_bytes())


def keys(key_map: KeyMap, *names: str) -> set[int]:
    """CPC key numbers by name."""
    return {key_map.key_number(name) for name in names}


def test_a_and_shift_a(key_map: KeyMap) -> None:
    """`a` holds key a while the PC key is down; `A` holds SHIFT + a (SHIFT one frame first)."""
    host = HostKeyboard(CODES, key_map)
    assert host.update("a", {KEY_A}, {KEY_A}) == keys(key_map, "a")
    assert host.update("a", set(), {KEY_A}) == keys(key_map, "a")  # the PC's repeat: ignored
    assert host.update("", set(), set()) == set()
    assert host.update("", {LSHIFT}, {LSHIFT}) == {SHIFT}  # Shift alone passes on
    assert host.update("A", {KEY_A}, {LSHIFT, KEY_A}) == {SHIFT} | keys(key_map, "a")
    assert not host.taps and not host.unknown


def test_altgr_symbols_follow_the_character(key_map: KeyMap) -> None:
    """AltGr+8 gives `[` on a German PC: the CPC gets its `[` key, not the key at 8's place."""
    host = HostKeyboard(CODES, key_map)
    assert host.update("[", {KEY_8}, {RALT, KEY_8}) == set(key_map.keys_for_character("["))
    assert host.update("", set(), {RALT}) == set()  # AltGr is never passed on


def test_shift_7_gives_slash_without_shift(key_map: KeyMap) -> None:
    """Shift+7 = `/` (German PC): SHIFT is let go first, then the `/` key alone is held."""
    host = HostKeyboard(CODES, key_map)
    assert host.update("", {LSHIFT}, {LSHIFT}) == {SHIFT}
    assert host.update("/", {KEY_7}, {LSHIFT, KEY_7}) == set()  # SHIFT released first
    assert host.update("", set(), {LSHIFT, KEY_7}) == keys(key_map, "/")
    assert host.update("", set(), {LSHIFT}) == {SHIFT}  # the key up: Shift alone again


def test_control_letters(key_map: KeyMap) -> None:
    """Ctrl+a → CONTROL + a; Ctrl+Shift+a → CONTROL + SHIFT + a; Ctrl alone → CONTROL."""
    host = HostKeyboard(CODES, key_map)
    assert host.update("", {LCONTROL}, {LCONTROL}) == {CONTROL}
    assert host.update("", {KEY_A}, {LCONTROL, KEY_A}) == {CONTROL} | keys(key_map, "a")
    assert host.update("", {LSHIFT}, {LCONTROL, LSHIFT, KEY_A}) == (
        {CONTROL, SHIFT} | keys(key_map, "a"))  # fmt: skip


def test_function_keys_win_over_their_character(key_map: KeyMap) -> None:
    """A keypad digit stays f1 (its "1" is dropped); cursor keys and RETURN by function."""
    host = HostKeyboard(CODES, key_map)
    assert host.update("1", {NUMPAD1}, {NUMPAD1}) == keys(key_map, "f1")
    cursor = host.update("", {CURSOR_UP}, {CURSOR_UP, LSHIFT})  # Shift+cursor: COPY editing
    assert cursor == {SHIFT} | keys(key_map, "cursor_up")
    assert host.update("", {RETURN}, {RETURN}) == keys(key_map, "return")


def test_taps_unknown_characters_and_space(key_map: KeyMap) -> None:
    """A character without a PC key going down is a tap; ä and € are not on the CPC."""
    host = HostKeyboard(CODES, key_map)
    assert host.update("^", {SPACE}, {SPACE}) == set(key_map.keys_for_character("^"))  # dead key
    assert host.update("", set(), set()) == set()
    assert host.update("äb", set(), set()) == set()  # ä: no key code in Dear PyGui
    assert host.unknown == ["ä"] and host.taps == [key_map.keys_for_character("b")]
    assert host.update("€", {KEY_E}, {RALT, KEY_E}) == set() and host.unknown == ["ä", "€"]
    assert host.update(" ", {SPACE}, {SPACE}) == keys(key_map, "space")


def test_character_input_reports_only_new_characters() -> None:
    """The hidden input's text only grows; each frame gives what was added (an edit: nothing)."""
    characters = CharacterInput()
    assert characters.new_characters("a") == "a"
    assert characters.new_characters("aA[") == "A["
    assert characters.new_characters("aA[") == ""
    assert characters.new_characters("aA") == ""  # Backspace edited the hidden input
    assert characters.new_characters("aAb") == "b"
    characters.reset()
    assert characters.new_characters("x") == "x"


MEMORY_VIEW, LOCATION = 501, 502  # combo item ids for the ComboWatch tests


class Combos:
    """Combo values for `ComboWatch.holds_keys` (`value_of`)."""

    def __init__(self) -> None:
        """Both combos at their first entry."""
        self.values = {MEMORY_VIEW: "cpu", LOCATION: "any"}

    def value_of(self, combo: int) -> object:
        """The value of `combo`."""
        return self.values[combo]


def frame(watch: ComboWatch, combos: Combos, clicked: bool = False, released: bool = False,
          over: int | None = None) -> bool:  # fmt: skip
    """One frame of mouse state; True while the keys stay with the window."""
    return watch.holds_keys(clicked, released, over, combos.value_of)


def test_combo_list_keeps_the_keys_until_a_choice_is_taken() -> None:
    """Click a combo, then an entry: the keys come back one frame after the entry's release.

    The bug of 0.9.0: the hidden input took the keys back on the frame after the click on the
    combo, which closed its list - no combo in the window could be changed.
    """
    watch, combos = ComboWatch(), Combos()
    assert not frame(watch, combos)
    assert frame(watch, combos, clicked=True, over=MEMORY_VIEW)  # the list opens
    assert frame(watch, combos, released=True, over=MEMORY_VIEW)  # the release keeps it open
    assert frame(watch, combos)  # open, the mouse on its way to an entry
    assert frame(watch, combos, clicked=True)  # a click on an entry (outside the combo)
    combos.values[MEMORY_VIEW] = "ram"  # Dear PyGui takes the choice
    assert frame(watch, combos, released=True)  # closed in this frame: still held
    assert not frame(watch, combos)  # now the keys go back to the CPC


def test_combo_list_closes_on_a_value_change_or_a_second_click() -> None:
    """A choice with the keyboard closes the list; so does a second click on the combo."""
    watch, combos = ComboWatch(), Combos()
    assert frame(watch, combos, clicked=True, over=LOCATION)
    combos.values[LOCATION] = "rom"  # chosen with the cursor keys and Enter
    assert frame(watch, combos)
    assert not frame(watch, combos)
    assert frame(watch, combos, clicked=True, over=LOCATION)
    assert frame(watch, combos, released=True, over=LOCATION)
    assert frame(watch, combos, clicked=True, over=LOCATION)  # the same combo again: closes
    assert frame(watch, combos, released=True, over=LOCATION)
    assert not frame(watch, combos)


def test_combo_watch_follows_a_click_on_another_combo() -> None:
    """A click on another combo while a list is open opens that one's list."""
    watch, combos = ComboWatch(), Combos()
    assert frame(watch, combos, clicked=True, over=MEMORY_VIEW)
    assert frame(watch, combos, released=True, over=MEMORY_VIEW)
    assert frame(watch, combos, clicked=True, over=LOCATION)
    assert watch.open_combo == LOCATION
    assert frame(watch, combos, released=True, over=LOCATION)
    assert frame(watch, combos)


def test_clicks_elsewhere_leave_the_keys_alone() -> None:
    """Without a combo, clicks never hold the keys (buttons, lists, the CPC screen)."""
    watch, combos = ComboWatch(), Combos()
    assert not frame(watch, combos, clicked=True)
    assert not frame(watch, combos, released=True)
    assert not frame(watch, combos)
