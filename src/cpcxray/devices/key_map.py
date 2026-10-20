"""CPC key numbers, key names and characters, taken from the OS ROM (design.md §6.9).

The CPC keyboard is a matrix of 10 rows x 8 bits; the firmware numbers the keys
`key = row * 8 + bit` (0-79). The firmware's key translation tables in the OS ROM give the
character of every key in three variants - normal, with SHIFT, with CONTROL - 80 bytes each,
one after the other (at &1D69 in the 464 OS ROM). Instead of a hand-typed table, `KeyMap`
reads these tables from the installed ROM (found by the unique sequence "09oilkm,", the
normal characters of keys 32-39), so names and characters always match the firmware.

Key names: printable keys are named by their unshifted character ("a", "0", ",", "@", ...);
special keys by their function, derived from their firmware codes: "return", "enter" (small
ENTER), "del", "clr", "copy", "esc", "tab", "caps_lock", "space", "shift", "control",
"cursor_up/down/left/right", "f0"-"f9", "f." and the joystick "joy_up/down/left/right",
"joy_fire1/2" (row 9).
"""

from __future__ import annotations

from dataclasses import dataclass, field

KEY_COUNT = 80
TABLE_MARKER = b"09oilkm,"  # normal characters of keys 32-39
MARKER_KEY = 32
SHIFT = 21  # row 2, bit 5 (code &FF in the tables: handled by the firmware itself)
CONTROL = 23  # row 2, bit 7
JOYSTICK_ROW = 9

_SPECIAL_CODES = {
    0xF0: "cursor_up", 0xF1: "cursor_down", 0xF2: "cursor_left", 0xF3: "cursor_right",
    0x8A: "f.", 0x8B: "enter", 0xE0: "copy", 0xFC: "esc", 0xFD: "caps_lock",
    0x09: "tab", 0x7F: "del", 0x0D: "return", 0x10: "clr", 0x20: "space",
    **{0x80 + digit: f"f{digit}" for digit in range(10)},
}  # fmt: skip
_JOYSTICK_BITS = ("joy_up", "joy_down", "joy_left", "joy_right", "joy_fire2", "joy_fire1")
_CHARACTER_ALIASES = {"\n": "\r"}  # newline types RETURN


class KeyMapError(ValueError):
    """Unknown key name, a character no key produces, or no key tables in the ROM."""


@dataclass
class KeyMap:
    """Key names and characters of the CPC keyboard.

    Attributes:
        normal: Character code per key number without modifiers (80 bytes).
        shifted: Character codes with SHIFT.
        controlled: Character codes with CONTROL.
        names: Key name → key number.
    """

    normal: bytes
    shifted: bytes
    controlled: bytes
    names: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Derive the key names from the tables."""
        if not self.names:
            self.names = _key_names(self.normal)

    @classmethod
    def from_rom(cls, rom: bytes) -> KeyMap:
        """Find the three translation tables in an OS ROM image.

        Raises:
            KeyMapError: If the ROM does not contain them.
        """
        marker = rom.find(TABLE_MARKER)
        if marker < 0:
            raise KeyMapError("no keyboard translation tables in this ROM")
        start = marker - MARKER_KEY
        tables = [rom[start + n * KEY_COUNT : start + (n + 1) * KEY_COUNT] for n in range(3)]
        return cls(*tables)

    def key_number(self, name: str) -> int:
        """Return the key number for a key name (see the module docstring).

        Raises:
            KeyMapError: For an unknown name.
        """
        try:
            return self.names[name.lower() if len(name) > 1 else name]
        except KeyError:
            raise KeyMapError(
                f"unknown key {name!r}; known: {', '.join(sorted(self.names))}"
            ) from None

    def keys_for_character(self, character: str) -> tuple[int, ...]:
        """Return the keys to hold for a character: (key,), (SHIFT, key) or (CONTROL, key).

        Rows 0-8 are searched first, so the joystick row never types letters.

        Raises:
            KeyMapError: If no key produces the character.
        """
        code = ord(_CHARACTER_ALIASES.get(character, character))
        search_order = [*range(JOYSTICK_ROW * 8), KEY_COUNT - 1]  # rows 0-8, then DEL
        for table, modifiers in (
            (self.normal, ()),
            (self.shifted, (SHIFT,)),
            (self.controlled, (CONTROL,)),
        ):
            for key in search_order:
                if table[key] == code and key not in (SHIFT, CONTROL):
                    return (*modifiers, key)
        raise KeyMapError(f"no CPC key types {character!r}")


def _key_names(normal: bytes) -> dict[str, int]:
    """Name every key from its unshifted firmware code (special codes by function)."""
    names = {"shift": SHIFT, "control": CONTROL}
    for key in range(KEY_COUNT):
        row, bit = divmod(key, 8)
        code = normal[key]
        if row == JOYSTICK_ROW and bit < len(_JOYSTICK_BITS):
            names[_JOYSTICK_BITS[bit]] = key
        elif code in _SPECIAL_CODES:
            names.setdefault(_SPECIAL_CODES[code], key)
        elif 0x20 < code < 0x7F:
            names.setdefault(chr(code), key)
    return names
