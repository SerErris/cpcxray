"""PC keyboard → CPC keyboard by character (design.md §6.9, D-40; task 6, owner 2026-10-23).

The PC delivers characters after its own layout (German, English, …: AltGr+8 gives `[`, Shift+7
gives `/` on a German keyboard). The CPC gets the key combination that types the same character
there - the firmware's translation tables (devices/key_map.py `keys_for_character`) are the
truth table: `a` → key a, `A` → SHIFT + a, `[` → the `[` key, `{` → SHIFT + `[`. No host layout
is assumed.

Dear PyGui has no character event, but ImGui hands typed characters to the active text input,
so the window keeps a hidden one active while no panel input takes the keys (ui/app.py). Per
frame:

* `CharacterInput` turns the hidden input's text into the characters typed since the last
  frame (Dear PyGui ignores `set_value` while an input is active, so the text only grows; it
  is emptied by switching between two hidden inputs).
* `HostKeyboard.update` pairs each character with the PC key that went down in the same frame
  and holds the CPC combination while that PC key stays down, so the firmware's key repeat
  and games work; the PC's own repeat characters are ignored. A character without a key that
  went down (e.g. one Dear PyGui has no key code for) is typed as a short tap.
* Keys without a character keep a fixed mapping by function (`FUNCTION_KEYS`: RETURN, ENTER,
  DEL, CLR, ESC, TAB, COPY, cursor keys, f0-f9 and f. on the numeric keypad); they win over a
  character the same key produced (keypad digits stay function keys).
* CONTROL: the PC gives no character for Ctrl+letter, so while Ctrl is down the letter keys
  (reported by their label) give CONTROL + that key, with SHIFT if Shift is down too.
* SHIFT and CONTROL alone (e.g. Shift+cursor for COPY editing) are passed on only while no
  character combination is held - Shift+7 = `/` must not reach the CPC as SHIFT + `/`. Caps
  Lock is not passed on: it already changes the characters.
* A combination whose SHIFT / CONTROL differ from those held now gets its modifiers one frame
  before its key: the firmware scans the rows one after another and could otherwise see the
  key without its SHIFT (as for typing, devices/keyboard.py).
"""

from __future__ import annotations

import string
from collections.abc import Callable
from dataclasses import dataclass, field

from cpcxray.devices.key_map import CONTROL, SHIFT, KeyMap, KeyMapError

MAX_INPUT = 200  # characters in the hidden input before it is emptied (switched)


def function_keys(key_code: Callable[[str], int]) -> dict[int, str]:
    """PC keys without a character → CPC key name (by function, not by position).

    Args:
        key_code: The Dear PyGui code of a key name (`mvKey_<name>`).
    """
    mapping = {
        key_code("Return"): "return",
        key_code("NumPadEnter"): "enter",
        key_code("Back"): "del",
        key_code("Delete"): "clr",
        key_code("Escape"): "esc",
        key_code("Tab"): "tab",
        key_code("LAlt"): "copy",
        key_code("Up"): "cursor_up",
        key_code("Down"): "cursor_down",
        key_code("Left"): "cursor_left",
        key_code("Right"): "cursor_right",
        key_code("Decimal"): "f.",
    }
    mapping |= {key_code(f"NumPad{digit}"): f"f{digit}" for digit in range(10)}
    return mapping


def letter_keys(key_code: Callable[[str], int]) -> dict[int, str]:
    """PC letter keys (reported by their label in the PC's layout) → CPC key name, for Ctrl."""
    return {key_code(letter): letter.lower() for letter in string.ascii_uppercase}


@dataclass(frozen=True)
class HostKeyCodes:
    """The PC key codes `HostKeyboard` needs (from Dear PyGui; plain ints in the tests).

    Attributes:
        functions: Keys without a character → CPC key name.
        letters: Letter keys → CPC key name (for Ctrl + letter).
        shift: The Shift keys.
        control: The Ctrl keys.
        ignored: Keys that never pair with a character (Shift, Ctrl, Alt, AltGr, Caps Lock, …).
    """

    functions: dict[int, str]
    letters: dict[int, str]
    shift: frozenset[int]
    control: frozenset[int]
    ignored: frozenset[int] = frozenset()


class CharacterInput:
    """The characters typed into the hidden input since the last frame (module docstring)."""

    def __init__(self) -> None:
        """Start with an empty text."""
        self.seen = ""

    def new_characters(self, text: str) -> str:
        """The characters `text` has gained since the last call.

        The text only grows while the input is active; it shrinks when an editing key (e.g.
        Backspace, Ctrl+X) changed it or after `reset` - then nothing new is reported.
        """
        new = text[len(self.seen) :] if text.startswith(self.seen) else ""
        self.seen = text
        return new

    def reset(self) -> None:
        """The input was emptied (the window switched to the other hidden input)."""
        self.seen = ""


@dataclass
class HostKeyboard:  # pylint: disable=too-many-instance-attributes  # pairing state of one frame
    """Which CPC keys the PC keyboard holds now (see the module docstring).

    Attributes:
        codes: The PC key codes.
        key_map: The CPC keyboard of the installed OS ROM.
        combinations: PC key → (character, CPC keys) it holds while down.
        taps: Characters to type as short taps (no PC key went down with them); the window
            schedules them and empties the list.
        unknown: Characters no CPC key types, since the window last emptied the list.
    """

    codes: HostKeyCodes
    key_map: KeyMap
    combinations: dict[int, tuple[str, tuple[int, ...]]] = field(default_factory=dict)
    taps: list[tuple[int, ...]] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    _held_modifiers: frozenset[int] = frozenset()  # SHIFT / CONTROL held in the last frame
    _waiting: set[int] = field(default_factory=set)  # PC keys whose CPC key comes next frame

    def update(self, characters: str, pressed: set[int], down: set[int]) -> set[int]:
        """The CPC keys to hold this frame.

        Args:
            characters: The characters typed since the last frame (`CharacterInput`).
            pressed: The PC keys that went down this frame.
            down: The PC keys that are down now.
        """
        for host_key in [k for k in self.combinations if k not in down]:
            del self.combinations[host_key]
            self._waiting.discard(host_key)
        held: set[int] = set()
        function_pressed = pressed & self.codes.functions.keys()
        for host_key in down & self.codes.functions.keys():
            held.add(self.key_map.key_number(self.codes.functions[host_key]))
        control = bool(down & self.codes.control)
        shift = bool(down & self.codes.shift)
        if control:
            held |= self._control_keys(down, shift)
        elif not function_pressed:  # a keypad digit's character belongs to its f-key
            self._take_characters(characters, pressed)
        modifiers = {SHIFT if shift else None, CONTROL if control else None} - {None}
        combination_keys = self._combination_keys()
        if combination_keys is None:  # no character held: SHIFT / CONTROL alone pass on
            held |= modifiers
        else:
            held |= combination_keys
        self._held_modifiers = frozenset(held & {SHIFT, CONTROL})
        return held

    def _control_keys(self, down: set[int], shift: bool) -> set[int]:
        """Ctrl + letter keys → CONTROL (+ SHIFT) + those letters; Ctrl alone → CONTROL."""
        keys = {CONTROL}
        if shift:
            keys.add(SHIFT)
        for host_key in down & self.codes.letters.keys():
            keys.add(self.key_map.key_number(self.codes.letters[host_key]))
        return keys

    def _take_characters(self, characters: str, pressed: set[int]) -> None:
        """Pair the new characters with the PC keys that went down; taps for the others."""
        free = sorted(pressed - self.codes.ignored - self.combinations.keys()
                      - self.codes.functions.keys())  # fmt: skip
        repeating = {character for character, _ in self.combinations.values()}
        for character in characters:
            try:
                keys = self.key_map.keys_for_character(character)
            except KeyMapError:
                self.unknown.append(character)
                continue
            if free:
                host_key = free.pop(0)
                self.combinations[host_key] = (character, keys)
                modifiers = frozenset(keys) & {SHIFT, CONTROL}
                if modifiers != self._held_modifiers:
                    self._waiting.add(host_key)  # its SHIFT / CONTROL first, the key next frame
            elif character not in repeating:  # the PC's own repeat is ignored
                self.taps.append(keys)

    def _combination_keys(self) -> set[int] | None:
        """The CPC keys of the held combinations (None if none is held).

        A combination waiting for its modifiers contributes only them this frame.
        """
        if not self.combinations:
            return None
        keys: set[int] = set()
        for host_key, (_character, combination) in self.combinations.items():
            if host_key in self._waiting:
                keys |= set(combination) & {SHIFT, CONTROL}
            else:
                keys |= set(combination)
        self._waiting.clear()
        return keys
