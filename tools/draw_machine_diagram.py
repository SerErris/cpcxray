"""Draw docs/img/machine.svg: the CPC machine as a hardware block diagram with its buses.

Run `python -m tools.draw_machine_diagram` after changing the machine's structure. The style
follows classic home-computer block diagrams (owner, 2026-10-23: like the ZX Spectrum and Amiga
diagrams): chips as boxes, the control, address and data buses as thick coloured lines with the
chips tapped onto them, single signals (INT, ROM enables, ROM number) as thin coloured lines, and
under each box the Python module that implements it - where the hardware diagrams print the IC
number. A white background keeps it readable in light and dark viewers.
"""

from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parents[1] / "docs" / "img" / "machine.svg"
WIDTH, HEIGHT = 1480, 880

CONTROL, ADDRESS, DATA = "#1a9e3a", "#e03131", "#1c6dd0"  # the three buses
INTERRUPT, ROM_ENABLE, ROM_NUMBER, OTHER = "#f08c00", "#9c36b5", "#c2255c", "#495057"
CLOCK = "#212529"  # the machine's clock: who runs the Z80
INK, SUBTEXT, MODULE = "#111111", "#343a40", "#5c6370"
SANS = "'IBM Plex Sans', 'DejaVu Sans', system-ui, sans-serif"
LABEL_SANS = "'DejaVu Sans', sans-serif"  # the font BOLD_WIDTHS was measured in
MONO = "'IBM Plex Mono', 'DejaVu Sans Mono', ui-monospace, monospace"

CONTROL_Y, ADDRESS_Y, DATA_Y, BUS_END = 300, 345, 520, 1200  # ends below the FDC's right edge
BUS = 28  # every bus wider than its name's plate, so it runs on unbroken
DEVICE_Y, DEVICE_HEIGHT = 128, 112
MEMORY_Y, MEMORY_HEIGHT = 640, 110
# arrows (owner 2026-10-23): all as thick as INT, one head size, the tip GAP before the box;
# the buses and their taps keep their own widths
ARROW, GAP, HEAD = 3, 3, 4

# x, name, two lines, port, module
DEVICES = (
    (420, "Gate Array", ("mode · inks · border", "300 Hz interrupt"), "&7Fxx",
     "devices/gate_array.py"),
    (580, "CRTC 6845", ("registers R0-R17", "no timing (D-06)"), "&BCxx-&BFxx",
     "devices/crtc.py"),
    (740, "ROM select", ("upper ROM number", "0-15"), "&DFxx", "devices/rom_select.py"),
    (900, "PPI 8255 + PSG", ("SystemIO (D-07)", "VSYNC · keys"), "&F4xx-&F7xx",
     "devices/system_io.py"),
    (1060, "FDC µPD765", ("floppy controller", "two drives"), "&FB7E-&FB7F",
     "devices/fdc765.py"),
)  # fmt: skip
# x, width, name, two lines, module
MEMORY = (
    (420, 230, "RAM", ("64 KB · 128 KB on the 6128", "banking: block store (D-14)"),
     "machine/memory.py"),
    (690, 170, "Lower ROM", ("OS · 16 KB", "&0000-&3FFF"), "machine/memory.py"),
    (900, 300, "Upper ROMs 0-15", ("BASIC · AMSDOS · … · &C000-&FFFF",
                                   "decoder + ShadowRom (scrambled, D-11)"),
     "machine/memory.py · decoding/"),
)  # fmt: skip


# advance widths of DejaVu Sans Bold in 1/1000 em (measured once with Pillow); other characters
# count as "M"
BOLD_WIDTHS = {
    " ": 348,
    "!": 456,
    '"': 521,
    "#": 838,
    "$": 696,
    "%": 1002,
    "&": 872,
    "'": 306,
    "(": 457,
    ")": 457,
    "*": 523,
    "+": 838,
    ",": 380,
    "-": 415,
    ".": 380,
    "/": 365,
    "0": 696,
    "1": 696,
    "2": 696,
    "3": 696,
    "4": 696,
    "5": 696,
    "6": 696,
    "7": 696,
    "8": 696,
    "9": 696,
    ":": 400,
    ";": 400,
    "<": 838,
    "=": 838,
    ">": 838,
    "?": 580,
    "@": 1000,
    "A": 774,
    "B": 762,
    "C": 734,
    "D": 830,
    "E": 683,
    "F": 683,
    "G": 821,
    "H": 837,
    "I": 372,
    "J": 372,
    "K": 775,
    "L": 637,
    "M": 995,
    "N": 837,
    "O": 850,
    "P": 733,
    "Q": 850,
    "R": 770,
    "S": 720,
    "T": 682,
    "U": 812,
    "V": 774,
    "W": 1103,
    "X": 771,
    "Y": 724,
    "Z": 725,
    "[": 457,
    "\\": 365,
    "]": 457,
    "^": 838,
    "_": 500,
    "`": 500,
    "a": 675,
    "b": 716,
    "c": 593,
    "d": 716,
    "e": 678,
    "f": 435,
    "g": 716,
    "h": 712,
    "i": 343,
    "j": 343,
    "k": 665,
    "l": 343,
    "m": 1042,
    "n": 712,
    "o": 687,
    "p": 716,
    "q": 716,
    "r": 493,
    "s": 595,
    "t": 478,
    "u": 712,
    "v": 652,
    "w": 924,
    "x": 645,
    "y": 652,
    "z": 582,
    "{": 712,
    "|": 365,
    "}": 712,
    "~": 838,
    "·": 380,
    "ç": 593,
    "é": 678,
    "µ": 736,
    "\u00d7": 838,
    "…": 1000,
    "→": 838,
    "ä": 675,
    "ö": 687,
    "ü": 712,
    "ß": 719,
}


def label_width(content: str, size: float) -> float:
    """Width of `content` in bold at `size` (DejaVu Sans Bold metrics)."""
    return sum(BOLD_WIDTHS.get(character, BOLD_WIDTHS["M"]) for character in content) * size / 1000


def _shortened(points: list[tuple[float, float]], length: float) -> list[tuple[float, float]]:
    """The polyline with its last segment shortened by `length` (at most to its start)."""
    (start_x, start_y), (end_x, end_y) = points[-2], points[-1]
    segment = math.hypot(end_x - start_x, end_y - start_y)
    keep = max(segment - length, 0) / segment if segment else 0
    return [*points[:-1], (start_x + (end_x - start_x) * keep, start_y + (end_y - start_y) * keep)]


class Drawing:
    """SVG elements collected in drawing order (later ones lie on top)."""

    def __init__(self, name: str, marker_size: float = 7) -> None:
        """Start an empty drawing.

        Args:
            name: Prefix of the arrowhead ids, unique per diagram: pages that show several
                diagrams inline would otherwise use the first diagram's heads for all of them.
            marker_size: Length of an arrowhead in line widths (a head grows with its line).
        """
        self.parts: list[str] = []
        self.name = name
        self.marker_size = marker_size

    def text(  # pylint: disable=too-many-arguments  # position, content and style
        self, x: float, y: float, content: str, size: float = 14, *, weight: str = "normal",
        fill: str = INK, anchor: str = "middle", mono: bool = False,
    ) -> None:  # fmt: skip
        """A line of text."""
        family = MONO if mono else SANS
        self.parts.append(
            f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{escape(content)}</text>'
        )

    def box(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # geometry, style
        self, x: float, y: float, width: float, height: float, fill: str,
        stroke: str = "#343a40", *, dashed: bool = False,
    ) -> None:  # fmt: skip
        """A rectangle."""
        dash = ' stroke-dasharray="7 5"' if dashed else ""
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="4" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="2"{dash}/>'
        )

    def line(  # pylint: disable=too-many-arguments  # geometry and stroke style
        self, points: list[tuple[float, float]], colour: str, width: float, *,
        dashed: bool = False, arrow: str | None = None, cap: str = "round",
    ) -> None:  # fmt: skip
        """A polyline (a bus, a tap or a signal), optionally ending in an arrow.

        `cap` "butt" ends a wide bus flush at its end point (a round cap would reach into the
        box it ends at by half its width).

        With an arrow the line stops at the base of the head and the head reaches the last
        point: a line running on under the head would poke out past its tip.
        """
        if arrow:
            points = _shortened(points, self.marker_size * width)
        path = " ".join(("M" if i == 0 else "L") + f"{x},{y}" for i, (x, y) in enumerate(points))
        dash = ' stroke-dasharray="6 5"' if dashed else ""
        marker = f' marker-end="url(#{self.name}-{arrow})"' if arrow else ""
        self.parts.append(
            f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="{width}" '
            f'stroke-linejoin="round" stroke-linecap="{cap}"{dash}{marker}/>'
        )

    def label(self, x: float, y: float, content: str, colour: str, size: float = 12.5) -> None:
        """Bold text on a white plate, so it reads on top of lines.

        `y` is the vertical centre of plate and text: a label on a line takes the line's y.
        A line break in `content` makes a label of several lines on one plate.
        """
        lines = content.split("\n")
        line_height = size * 1.2
        widths = [label_width(line, size) for line in lines]
        height = size + 6 + line_height * (len(lines) - 1)
        # left and right the same space as above and below the capitals (cap height 0.73 em)
        width = max(widths) + size + 6 - 0.73 * size
        self.parts.append(
            f'<rect x="{x - width / 2}" y="{y - height / 2}" width="{width}" height="{height}" '
            f'rx="3" fill="#ffffff" opacity="0.92"/>'
        )
        first_baseline = y - line_height * (len(lines) - 1) / 2 + size * 0.36
        for number, (line, text_width) in enumerate(zip(lines, widths, strict=True)):
            # textLength: a browser with another font still fills exactly the measured width
            self.parts.append(
                f'<text x="{x}" y="{first_baseline + number * line_height}" '
                f'font-family="{LABEL_SANS}" font-size="{size}" font-weight="bold" '
                f'fill="{colour}" text-anchor="middle" '
                f'textLength="{text_width:.1f}">{escape(line)}</text>'
            )

    def chip(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # geometry, texts
        self, x: float, y: float, width: float, height: float, name: str,
        lines: tuple[str, ...], module: str, fill: str, port: str = "",
    ) -> None:  # fmt: skip
        """A component: name, description lines, its port (I/O devices), its module."""
        self.box(x, y, width, height, fill)
        centre = x + width / 2
        self.text(centre, y + 24, name, 16, weight="bold")
        for number, content in enumerate(lines):
            self.text(centre, y + 44 + 17 * number, content, 13, fill=SUBTEXT)
        if port:
            self.text(centre, y + 44 + 17 * len(lines) + 2, port, 13, weight="bold", fill=SUBTEXT,
                      mono=True)  # fmt: skip
        self.text(centre, y + height - 10, module, 11.5, fill=MODULE, mono=True)

    def svg(self, width: int, height: int, title: str, markers: dict[str, str]) -> str:
        """The complete SVG document: size, accessible title, arrowheads by name and colour."""
        size = self.marker_size
        definitions = "".join(  # the head's base (x 0) sits at the shortened line's end
            f'<marker id="{self.name}-{name}" viewBox="0 0 10 10" refX="0" refY="5" '
            f'markerWidth="{size}" markerHeight="{size}" '
            f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
            f'fill="{colour}"/></marker>'
            for name, colour in markers.items()
        )
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
                f'width="{width}" height="{height}" role="img" aria-label="{title}">')  # fmt: skip
        background = f'<rect width="{width}" height="{height}" fill="#ffffff"/>'
        return "\n".join([head, f"<defs>{definitions}</defs>", background, *self.parts, "</svg>"])


def draw_outside(drawing: Drawing) -> None:
    """The Session above the machine, the configuration, the machine's frame."""
    drawing.box(225, 15, 250, 55, "#ffffff", "#555555", dashed=True)
    drawing.text(350, 38, "Session - see diagram 1", 15, weight="bold")
    drawing.text(350, 58, "drives the machine, observes the bus", 12, fill=SUBTEXT)
    drawing.box(520, 15, 160, 55, "#ebfbee", "#2b8a3e")
    drawing.text(600, 39, "machine.toml", 15, weight="bold")
    drawing.text(600, 58, "machine/config.py", 11.5, fill=MODULE, mono=True)
    drawing.box(20, 90, 1440, 700, "#f8f9fb", "#868e96", dashed=True)
    drawing.line([(600, 70), (600, 90 - GAP)], OTHER, ARROW, arrow="other")
    drawing.label(650, 81, "power-on", OTHER, 11.5)
    drawing.text(40, 776, "CPC machine - machine/cpc464.py (464 · 664 · 6128)", 15,
                 weight="bold", anchor="start")  # fmt: skip


def draw_clock(drawing: Drawing) -> None:
    """The machine's clock: CPC464.step() runs the Z80 one instruction at a time.

    The Session's run loop calls it once per instruction; it sets INT, calls `Z80.step()`,
    adds the returned cost to the clock and ticks the Gate Array (and the keyboard's typing).
    """
    drawing.line([(250, 70), (250, 104), (120, 104), (120, 128 - GAP)], CLOCK, ARROW,
                 arrow="clock")  # fmt: skip
    drawing.label(185, 104, "step · run", CLOCK, 11.5)
    drawing.chip(40, 128, 160, 92, "Clock", ("CPC464.step()", "1 instruction per call"),
                 "machine/cpc464.py", "#fff9db")  # fmt: skip
    drawing.line([(80, 220), (80, 280 - GAP)], CLOCK, ARROW, arrow="clock")
    drawing.label(80, 249, "Z80.step()", CLOCK, 11.5)
    drawing.line([(200, 160), (420 - GAP, 160)], CLOCK, ARROW, arrow="clock")
    drawing.label(250, 160, "tick", CLOCK, 11)


def draw_taps_and_signals(drawing: Drawing) -> None:
    """The chips' taps onto the buses and the single signals (drawn under the buses)."""
    for x, *_ in DEVICES:
        drawing.line([(x + 30, DEVICE_Y + DEVICE_HEIGHT), (x + 30, CONTROL_Y)], CONTROL, 4)
        drawing.line([(x + 65, DEVICE_Y + DEVICE_HEIGHT), (x + 65, ADDRESS_Y)], ADDRESS, 5)
        drawing.line([(x + 100, DEVICE_Y + DEVICE_HEIGHT), (x + 100, DATA_Y)], DATA, 5)
    for x, *_ in MEMORY:
        drawing.line([(x + 30, MEMORY_Y), (x + 30, CONTROL_Y)], CONTROL, 4)
        drawing.line([(x + 65, MEMORY_Y), (x + 65, ADDRESS_Y)], ADDRESS, 5)
        drawing.line([(x + 100, MEMORY_Y), (x + 100, DATA_Y)], DATA, 5)
    # the Gate Array's ROM enables and RAM configuration, ROM select's number
    drawing.line([(545, DEVICE_Y + DEVICE_HEIGHT), (545, 578), (1170, 578)], ROM_ENABLE, ARROW)
    for x in (620, 830, 1170):
        drawing.line([(x, 578), (x, MEMORY_Y - GAP)], ROM_ENABLE, ARROW, arrow="enable")
    drawing.line([(865, DEVICE_Y + DEVICE_HEIGHT), (865, 606), (1110, 606), (1110, MEMORY_Y - GAP)],
                 ROM_NUMBER, ARROW, arrow="number")  # fmt: skip


def draw_buses(drawing: Drawing) -> None:
    """The three buses, the CPU bus, INT and the Session's observers."""
    drawing.line([(370, CONTROL_Y), (BUS_END, CONTROL_Y)], CONTROL, BUS, cap="butt")
    drawing.line([(370, ADDRESS_Y), (BUS_END, ADDRESS_Y)], ADDRESS, BUS, cap="butt")
    drawing.line([(370, DATA_Y), (BUS_END, DATA_Y)], DATA, BUS, cap="butt")
    drawing.line([(180, 330), (220, 330)], ADDRESS, BUS, cap="butt")
    drawing.line([(180, 420), (220, 420)], CONTROL, BUS, cap="butt")
    drawing.line([(180, 510), (220, 510)], DATA, BUS, cap="butt")
    drawing.line([(420, 228), (150, 228), (150, 280 - GAP)], INTERRUPT, ARROW, arrow="interrupt")
    drawing.label(380, 228, "INT", INTERRUPT)
    drawing.line([(295, 70), (295, 280 - GAP)], OTHER, ARROW, dashed=True, arrow="other")
    drawing.label(372, 252, "bus observers (D-12)", OTHER, 11.5)


def draw_chips(drawing: Drawing) -> None:
    """CPU, bus logic, I/O devices, memory and the bus names."""
    drawing.chip(40, 280, 140, 280, "CPU", ("Z80", "", "see diagram 3"), "cpu/z80.py", "#fff3bf")
    bus_logic = ("bus logic", "memory routing", "I/O broadcast:", "OUT to all,",
                 "IN values ANDed", "(D-08)")  # fmt: skip
    drawing.chip(220, 280, 150, 280, "CpcBus", bus_logic, "machine/bus.py", "#ffe3e3")
    drawing.label(200, 269, "CPU bus", INK, 12)
    for x, name, lines, port, module in DEVICES:
        drawing.chip(x, DEVICE_Y, 140, DEVICE_HEIGHT, name, lines, module, "#e7f5ff", port)
    for x, width, name, lines, module in MEMORY:
        drawing.chip(x, MEMORY_Y, width, MEMORY_HEIGHT, name, lines, module, "#ebfbee")
    drawing.label(800, CONTROL_Y, "Control bus · M1 · MREQ · IORQ · RD · WR", CONTROL, 13)
    drawing.label(800, ADDRESS_Y, "Address bus · A0-A15", ADDRESS, 13.5)
    drawing.label(800, DATA_Y, "Data bus · D0-D7", DATA, 13.5)
    drawing.label(1000, 578, "ROM enables · RAM configuration", ROM_ENABLE, 12)
    drawing.label(1000, 606, "upper ROM number", ROM_NUMBER, 12)


def draw_periphery(drawing: Drawing) -> None:
    """Keyboard, disk drives and images; the renderer outside the machine."""
    drawing.chip(1270, 130, 170, 75, "Keyboard", ("10 \u00d7 8 matrix + key map",),
                 "devices/keyboard.py", "#e7f5ff")  # fmt: skip
    drawing.line([(985, DEVICE_Y), (985, 112), (1355, 112), (1355, 130 - GAP)], OTHER, ARROW,
                 arrow="other")  # fmt: skip
    drawing.label(1165, 112, "keyboard rows (PSG port A)", OTHER, 11.5)
    drawing.chip(1270, 250, 170, 75, "Disk drives", ('A: 3" · B: 80 tracks',),
                 "devices/fdc765.py", "#e7f5ff")  # fmt: skip
    drawing.line([(1200, 205), (1240, 205), (1240, 287), (1270 - GAP, 287)], OTHER, ARROW,
                 arrow="other")  # fmt: skip
    drawing.chip(1270, 365, 170, 75, "Disk images", ("DSK · EDSK · raw",), "disk/dsk.py",
                 "#ebfbee")  # fmt: skip
    drawing.line([(1355, 325), (1355, 365 - GAP)], OTHER, ARROW, arrow="other")
    drawing.box(420, 812, 330, 50, "#ffffff", "#555555", dashed=True)
    drawing.text(585, 834, "Renderer - ui/renderer.py", 14, weight="bold")
    drawing.text(585, 853, "draws screen RAM with inks and CRTC registers", 11.5, fill=SUBTEXT)
    drawing.line([(535, MEMORY_Y + MEMORY_HEIGHT), (535, 812 - GAP)], OTHER, ARROW, dashed=True,
                 arrow="other")  # fmt: skip


def machine_svg() -> str:
    """The machine diagram as an SVG document."""
    drawing = Drawing("machine", marker_size=HEAD)
    draw_outside(drawing)
    draw_taps_and_signals(drawing)
    draw_clock(drawing)
    draw_buses(drawing)
    draw_chips(drawing)
    draw_periphery(drawing)
    markers = {"interrupt": INTERRUPT, "enable": ROM_ENABLE, "number": ROM_NUMBER, "other": OTHER,
               "clock": CLOCK}  # fmt: skip
    return drawing.svg(WIDTH, HEIGHT, "Block diagram of the emulated CPC machine and its buses",
                       markers)  # fmt: skip


def main() -> None:
    """Write docs/img/machine.svg."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(machine_svg(), encoding="utf-8")
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
