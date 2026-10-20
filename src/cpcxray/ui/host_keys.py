"""PC keyboard → CPC keyboard for the GUI window (design.md §6.9, phase 6).

Every frame the GUI looks at which PC keys are down and holds the matching CPC keys (source
"host" in the keyboard matrix), so key repeat, SHIFT/CONTROL combinations and games work as on
the real machine.

Mapping (owner's PC uses a German QWERTZ layout):

* Letters, digits, space: by their label (Dear PyGui reports letter keys by the layout).
* Function keys: RETURN, ENTER (numeric keypad), DEL (Backspace), CLR (Delete), ESC, TAB,
  CAPS LOCK, SHIFT, CONTROL, COPY (Alt), cursor keys.
* CPC function keypad f0-f9 and f.: the numeric keypad 0-9 and its decimal point.
* Symbol keys: the CPC key at the same position (the PC symbols differ by layout). For exact
  symbols use the window's "Type text" box, which types characters like `type_text`.
"""

from __future__ import annotations

import string

import dearpygui.dearpygui as dpg

# Dear PyGui 2.3 still defines mvKey_Plus/Colon/Quote/Tilde with old ASCII values (61, 59, 39,
# 96), which are no ImGui keys - is_key_down() reports them as held. These are the ImGui key
# codes of the same keys (ImGuiKey_Apostrophe, _Semicolon, _Equal).
IMGUI_KEY_APOSTROPHE = 596
IMGUI_KEY_SEMICOLON = 601
IMGUI_KEY_EQUAL = 602


def _key(name: str) -> int:
    """Return the Dear PyGui key constant mvKey_<name>."""
    return int(getattr(dpg, f"mvKey_{name}"))


def build_host_key_map() -> dict[int, str]:
    """Return Dear PyGui key → CPC key name."""
    mapping = {_key(letter): letter.lower() for letter in string.ascii_uppercase}
    mapping |= {_key(digit): digit for digit in string.digits}
    mapping |= {
        _key("Spacebar"): "space",
        _key("Return"): "return",
        _key("NumPadEnter"): "enter",
        _key("Back"): "del",
        _key("Delete"): "clr",
        _key("Escape"): "esc",
        _key("Tab"): "tab",
        _key("CapsLock"): "caps_lock",
        _key("LShift"): "shift",
        _key("RShift"): "shift",
        _key("LControl"): "control",
        _key("RControl"): "control",
        _key("LAlt"): "copy",
        _key("Up"): "cursor_up",
        _key("Down"): "cursor_down",
        _key("Left"): "cursor_left",
        _key("Right"): "cursor_right",
        _key("Decimal"): "f.",
        # symbol keys at the same position as on the CPC keyboard
        _key("Minus"): "-",
        IMGUI_KEY_EQUAL: "^",
        _key("Open_Brace"): "@",
        _key("Close_Brace"): "[",
        IMGUI_KEY_SEMICOLON: ";",
        IMGUI_KEY_APOSTROPHE: ":",
        _key("Backslash"): "]",
        _key("Comma"): ",",
        _key("Period"): ".",
        _key("Slash"): "/",
    }
    mapping |= {_key(f"NumPad{digit}"): f"f{digit}" for digit in range(10)}
    return mapping
