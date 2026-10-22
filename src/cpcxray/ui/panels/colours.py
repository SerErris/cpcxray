"""The GUI's colour table: every colour of the panels comes from here (agreed mock-ups, 2026-10-22).

The design canvas "cpcxray GUI tabs" fixes the palette: dark panels, blue headings and buttons,
yellow for "selected / here" (the PC line, the selected entry's frame), green for "fine / saved",
orange for "changed, not saved", red for errors and removal. Symbol types (task 5g) and known
variables have their own colours, used the same way in every tab.

No Dear PyGui import: the table is plain data, usable and testable headless (NFR-04).
"""

from __future__ import annotations

from typing import Final

type Colour = tuple[int, int, int]


def rgb(text: str) -> Colour:
    """Convert "#rrggbb" (as in the mock-ups) to an (r, g, b) tuple."""
    digits = text.lstrip("#")
    if len(digits) != 6:
        raise ValueError(f"expected #rrggbb, got {text!r}")
    return int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16)


# -- the frame of the window ------------------------------------------------------------------
BACKGROUND: Final = rgb("#232326")  # window
PANEL: Final = rgb("#2b2b30")  # a section (box) inside a tab
PANEL_DIM: Final = rgb("#26262a")  # a section without content (dashed in the mock-ups)
BORDER: Final = rgb("#45454c")  # frame of a section
INPUT: Final = rgb("#333337")  # text inputs, combos
ROW_ALTERNATE: Final = rgb("#34343a")  # list rows, hovered
TRACK: Final = rgb("#3a3a40")  # bars and plain badges

# -- text -------------------------------------------------------------------------------------
TEXT: Final = rgb("#dcdcdc")
TEXT_DIM: Final = rgb("#a0a0a8")  # labels, explanations, numbers of secondary interest
TEXT_BYTES: Final = rgb("#c8c8cc")  # hex dumps beside other text
TEXT_OFF: Final = rgb("#6a6a72")  # unused (pens the mode does not use, "unknown" bytes)
HEADING: Final = rgb("#8fb8ff")  # section titles
HEADING_HOVER: Final = rgb("#b8d2ff")
WHITE: Final = rgb("#ffffff")

# -- buttons and tabs -------------------------------------------------------------------------
ACCENT: Final = rgb("#3d6ea8")  # primary button, active tab, selected chip
BUTTON: Final = rgb("#2e3c4f")  # other buttons, inactive tabs
SELECTED_ROW: Final = rgb("#25344a")  # the selected list row

# -- states -----------------------------------------------------------------------------------
HERE: Final = rgb("#ffd75e")  # yellow: the selected entry's frame, the PC, the active target
HERE_BACKGROUND: Final = rgb("#4a4220")  # yellow bar behind the PC line
OK: Final = rgb("#7ee093")  # green: saved, identical, writable
OK_BACKGROUND: Final = rgb("#23432c")
OK_BORDER: Final = rgb("#3fb950")
CHANGED: Final = rgb("#ffa040")  # orange warning light: changed and not saved
CHANGED_BACKGROUND: Final = rgb("#5a3410")
CHANGED_BORDER: Final = rgb("#ff8c1a")
ERROR: Final = rgb("#ff8a8a")  # error messages, remove (✕)
ERROR_BACKGROUND: Final = rgb("#4a2a2a")
LED_ON: Final = rgb("#3fb950")  # an activity LED (motor, drive) lit
LED_OFF: Final = rgb("#55555c")
KEY_HELD_TEXT: Final = rgb("#1d1d1f")  # dark text on the yellow of a held key
ANALYSED: Final = rgb("#ffc85a")  # a ROM that is an analysis target

# -- badges: (text, background, border or None) -----------------------------------------------
BADGE_STYLES: Final[dict[str, tuple[Colour, Colour, Colour | None]]] = {
    "plain": (TEXT_DIM, TRACK, None),  # "read-only", "draft", "changed" while not lit
    "ok": (OK, OK_BACKGROUND, None),  # "saved", "identical ✓", "writable"
    "on": (OK, OK_BACKGROUND, OK_BORDER),  # a switch that is on ("● Analysis on")
    "changed": (CHANGED, CHANGED_BACKGROUND, CHANGED_BORDER),  # lit orange warning light
    "error": (ERROR, ERROR_BACKGROUND, None),
    "here": (HERE, HERE_BACKGROUND, HERE),  # the selected / active thing
    "key": (TEXT, ROW_ALTERNATE, None),  # a key of the keyboard matrix
    "key_held": (KEY_HELD_TEXT, HERE, None),  # a key held down
    "flag_on": (WHITE, ACCENT, None),  # a CPU flag that is set
    "flag_off": (TEXT_OFF, ROW_ALTERNATE, None),  # a CPU flag that is clear
}

# -- symbol types (task 5g): (text colour, chip background) -----------------------------------
SYMBOL_TYPES: Final = ("FW", "VAR", "var", "sub", "jump", "data")
SYMBOL_TYPE_COLOURS: Final[dict[str, tuple[Colour, Colour]]] = {
    "FW": (rgb("#8fb8ff"), rgb("#25344a")),  # Amstrad firmware jumpblock and restarts
    "VAR": (rgb("#7ee093"), rgb("#23432c")),  # Amstrad OS / AMSDOS variables
    "var": (rgb("#6edcd2"), rgb("#1f3d3a")),  # the owner's or the analysis' variables
    "sub": (rgb("#ffc85a"), rgb("#4a3a1a")),  # subroutines (call targets)
    "jump": (rgb("#ff9e6e"), rgb("#4a2e1e")),  # jump targets inside code
    "data": (rgb("#c8a0ff"), rgb("#3a2a4a")),  # tables, strings, other data
}


def name_colour(name_type: str | None) -> Colour:
    """The text colour of a name of this symbol type (FW for an unknown or missing type)."""
    return SYMBOL_TYPE_COLOURS.get(name_type or "FW", SYMBOL_TYPE_COLOURS["FW"])[0]


# -- breakpoint types by category (task 5d): (text and frame, chip background) --------------
BREAKPOINT_CATEGORIES: Final[dict[str, tuple[Colour, Colour]]] = {
    "code": (HERE, HERE_BACKGROUND),  # pc, m1
    "memory": (OK, OK_BACKGROUND),  # read, write, access
    "io": (HEADING, SELECTED_ROW),  # io_in, io_out, io
    "system": (rgb("#c8a0ff"), rgb("#3a2a4a")),  # rom_select, interrupt
}
BREAKPOINT_TYPE_CATEGORY: Final = {
    "pc": "code", "m1": "code", "read": "memory", "write": "memory", "access": "memory",
    "io_in": "io", "io_out": "io", "io": "io", "rom_select": "system", "interrupt": "system",
}  # fmt: skip

# -- bus access types (task 5f; the same in Follow CPU, 5c): (text and frame, background) ---
ACCESS_COLOURS: Final[dict[str, tuple[Colour, Colour]]] = {
    "M1": (TEXT_DIM, ROW_ALTERNATE),  # opcode fetch
    "read": (rgb("#78b4ff"), SELECTED_ROW),
    "write": (ERROR, ERROR_BACKGROUND),
    "in": (rgb("#c8a0ff"), rgb("#3a2a4a")),
    "out": (rgb("#c8a0ff"), rgb("#3a2a4a")),
    "interrupt": (HERE, HERE_BACKGROUND),
}
ACCESS_LETTERS: Final = {"M1": "F", "read": "R", "write": "W", "in": "IN", "out": "OUT",
                         "interrupt": "INT"}  # fmt: skip

# -- known variables in the memory view (FR-DBG-11): taken in turn ---------------------------
VARIABLE_COLOURS: Final = (
    (255, 200, 90), (120, 200, 255), (150, 230, 120), (255, 140, 170), (200, 160, 255),
    (110, 220, 210),
)  # fmt: skip
AREA_COLOUR: Final = (140, 140, 140)  # inside a pack's area, in no named variable

# Dear PyGui reads a colour with a negative first component as "use the theme's colour"
THEME_DEFAULT: Final = (-255, 0, 0, 255)

# Follow CPU marks of the Memory tab (task 5c)
WRITTEN_LAST: Final = rgb("#8a2e2e")  # written by the last step: strong red (white text)
WRITTEN_SINCE: Final = rgb("#4a2a2a")  # written since the last stop: dim red
READ_LAST: Final = rgb("#78b4ff")  # read by the last step: a blue frame
BADGE_STYLES.update(
    {
        "mem_last": (WHITE, WRITTEN_LAST, None),
        "mem_written": (TEXT, WRITTEN_SINCE, None),
        "mem_read": (TEXT, PANEL, READ_LAST),
    }
)

# the access type chips of the Bus trace tab, as badges
BADGE_STYLES.update(
    {
        f"access_{name}": (text, background, None)
        for name, (text, background) in ACCESS_COLOURS.items()
    }
)
