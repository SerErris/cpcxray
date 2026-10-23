"""Draw docs/img/z80.svg: the Z80 core as a CPU block diagram with its execution steps.

Run `python -m tools.draw_z80_diagram` after changing the core's structure. Like the machine and
session diagrams: the CPU's parts as boxes, and one instruction's way through them as numbered
arrows (owner, 2026-10-23: the flow inside the architecture, not a separate flow chart) - the
clock calls `Z80.step()` (1), the opcode is fetched with M1 (2) and dispatched to its handler
(3), which uses registers and flags (4) and the bus (5), and the cost goes back to the clock (6).
"""

from __future__ import annotations

from pathlib import Path

from tools.draw_machine_diagram import (
    ADDRESS,
    ARROW,
    BUS,
    CLOCK,
    CONTROL,
    DATA,
    GAP,
    HEAD,
    INTERRUPT,
    MODULE,
    OTHER,
    SUBTEXT,
    Drawing,
)

OUT = Path(__file__).resolve().parents[1] / "docs" / "img" / "z80.svg"
WIDTH, HEIGHT = 1480, 790
TOP_Y, TOP_HEIGHT = 160, 190
BOTTOM_Y, BOTTOM_HEIGHT = 420, 120
BUS_X, BUS_WIDTH = 1220, 220
SESSION, OBSERVERS = "#0b7285", "#e03131"  # the Session's register access, the bus observers


def draw_outside(drawing: Drawing) -> None:
    """The clock and the Gate Array (diagram 2) above, the frame of the core."""
    drawing.box(120, 15, 260, 60, "#ffffff", "#555555", dashed=True)
    drawing.text(250, 39, "Clock - CPC464.step()", 15, weight="bold")
    drawing.text(250, 60, "see diagram 2", 12, fill=SUBTEXT)
    drawing.box(420, 15, 230, 60, "#ffffff", "#555555", dashed=True)
    drawing.text(535, 39, "Gate Array", 15, weight="bold")
    drawing.text(535, 60, "see diagram 2", 12, fill=SUBTEXT)
    drawing.box(20, 100, 1440, 560, "#f8f9fb", "#868e96", dashed=True)
    drawing.text(40, 646, "Z80 core - cpu/z80.py (instruction-level, every bus access in order)",
                 15, weight="bold", anchor="start")  # fmt: skip


def draw_parts(drawing: Drawing) -> None:
    """Control, fetch, handler tables, bus interface; timing, registers, ALU and flags."""
    drawing.chip(40, TOP_Y, 230, TOP_HEIGHT, "Control", (
        "NMI pending: accept it", "INT and IFF1, not after EI:", "accept (IM 0 / 1 / 2)",
        "halted: dummy M1 fetch", "else: fetch the opcode"), "Z80.step()", "#fff3bf")  # fmt: skip
    drawing.chip(330, TOP_Y, 200, 120, "Opcode fetch", ("M1 cycle, PC + 1", "R incremented"),
                 "bus.fetch()", "#fff3bf")  # fmt: skip
    drawing.box(600, TOP_Y, 420, TOP_HEIGHT, "#f3f0ff", "#6b3fc0")
    drawing.text(810, TOP_Y + 22, "Handler tables", 16, weight="bold")
    drawing.text(810, TOP_Y + 40, "closures built once per CPU (_TableBuilder) · ALU inside", 12,
                 fill=SUBTEXT)  # fmt: skip
    tables = ((620, 180, "unprefixed", "opcode bits"), (820, 85, "CB", "bits, rotates"),
              (915, 85, "ED", "block, I/O"), (620, 180, "DD / FD", "IX / IY for HL"),
              (820, 180, "DD CB / FD CB", "indexed bits"))  # fmt: skip
    for number, (x, width, name, note) in enumerate(tables):
        y = TOP_Y + 55 if number < 3 else TOP_Y + 120
        drawing.chip(x, y, width, 58, name, (note,), "", "#e5dbff")
    drawing.chip(BUS_X, TOP_Y, BUS_WIDTH, TOP_HEIGHT, "Bus interface", (
        "fetch - opcode bytes (M1)", "read · write - memory", "io_in · io_out - I/O",
        "int_ack - interrupt", "every access in the order", "of the real bus cycles",
        "(M1 attribution: §5.5)"), "cpu/bus.py (CpuBus)", "#ffe3e3")  # fmt: skip
    drawing.chip(40, BOTTOM_Y, 200, BOTTOM_HEIGHT, "Timing", ("cost of each instruction",
                 "in NOPs = µs (D-04)"), "cpu/timing.py", "#fff9db")  # fmt: skip
    drawing.chip(290, BOTTOM_Y, 420, BOTTOM_HEIGHT, "Register file", (
        "byte_registers: B C D E H L F A IXH IXL IYH IYL",
        "SP · PC · WZ (MEMPTR) · I · R · IFF1/2 · IM · halted",
        "alternate set AF' BC' DE' HL'"), "Z80 attributes", "#e7f5ff")  # fmt: skip
    drawing.chip(760, BOTTOM_Y, 260, BOTTOM_HEIGHT, "Flag tables", (
        "read-only lookups per result", "S Z X Y · parity · flag bits"), "cpu/tables.py",
        "#e7f5ff")  # fmt: skip


def draw_steps(drawing: Drawing) -> None:
    """One instruction's way: the numbered arrows 1-6, INT, and the CPU bus."""
    drawing.line([(200, 75), (200, TOP_Y - GAP)], CLOCK, ARROW, arrow="clock")
    drawing.label(200, 119, "1 step()", CLOCK, 12)
    drawing.line([(535, 75), (535, 88), (245, 88), (245, TOP_Y - GAP)], INTERRUPT, ARROW,
                 arrow="interrupt")  # fmt: skip
    drawing.label(390, 88, "INT", INTERRUPT, 12)
    drawing.line([(270, 220), (330 - GAP, 220)], CLOCK, ARROW, arrow="clock")
    drawing.label(299, 204, "2", CLOCK, 12)
    drawing.line([(430, TOP_Y), (430, 128), (BUS_X + 40, 128), (BUS_X + 40, TOP_Y - GAP)],
                 CONTROL, ARROW, arrow="bus")  # fmt: skip
    drawing.label(820, 128, "M1 fetch", CONTROL, 11.5)
    drawing.line([(530, 220), (600 - GAP, 220)], CLOCK, ARROW, arrow="clock")
    drawing.label(564, 204, "3 opcode", CLOCK, 11.5)
    drawing.line([(650, BOTTOM_Y), (650, TOP_Y + TOP_HEIGHT + GAP)], CLOCK, ARROW, arrow="clock")
    drawing.line([(700, TOP_Y + TOP_HEIGHT), (700, BOTTOM_Y - GAP)], CLOCK, ARROW, arrow="clock")
    drawing.label(675, 393, "4 read · write", CLOCK, 11.5)
    drawing.line([(890, BOTTOM_Y), (890, TOP_Y + TOP_HEIGHT + GAP)], CLOCK, ARROW, arrow="clock")
    drawing.label(890, 393, "4 flag lookup", CLOCK, 11.5)
    drawing.line([(1020, 250), (BUS_X - GAP, 250)], CLOCK, ARROW, arrow="clock")
    drawing.label(1120, 236, "5", CLOCK, 12)
    drawing.label(1120, 268, "operands,", CLOCK, 11.5)
    drawing.label(1120, 286, "memory, I/O", CLOCK, 11.5)
    drawing.line([(620, TOP_Y + TOP_HEIGHT), (620, 375), (140, 375), (140, BOTTOM_Y - GAP)], CLOCK,
                 ARROW, arrow="clock")  # fmt: skip
    drawing.label(380, 375, "cost", CLOCK, 11.5)
    drawing.line([(40, 480), (28, 480), (28, 45), (120 - GAP, 45)], CLOCK, ARROW, arrow="clock")
    drawing.label(30, 393, "6 cost", CLOCK, 11.5)
    # the CPU bus below the bus interface
    for x, colour in ((1265, CONTROL), (1330, ADDRESS), (1395, DATA)):
        drawing.line([(x, TOP_Y + TOP_HEIGHT), (x, 698)], colour, BUS, cap="butt")
    drawing.box(1180, 698, 260, 70, "#ffffff", "#555555", dashed=True)
    drawing.text(1310, 724, "CPU bus - see diagram 2", 15, weight="bold")
    drawing.text(1310, 746, "control · address · data", 12, fill=SUBTEXT)
    drawing.text(1310, 762, "watched by the bus observers (D-12)", 11.5, fill=OBSERVERS)


def draw_session(drawing: Drawing) -> None:
    """The Session (diagram 1): reads and sets the registers between two instructions."""
    drawing.box(640, 698, 400, 70, "#ffffff", "#555555", dashed=True)
    drawing.text(840, 724, "Session - see diagram 1", 15, weight="bold")
    drawing.text(840, 746, "PC breakpoints · instruction trace · step result", 12, fill=SUBTEXT)
    drawing.text(840, 762, "registers_get / set · .sna", 11.5, fill=MODULE, mono=True)
    drawing.line([(840, 696), (840, 600), (500, 600), (500, BOTTOM_Y + BOTTOM_HEIGHT + GAP)],
                 SESSION, ARROW, dashed=True, arrow="session")  # fmt: skip
    drawing.label(670, 600, "reads · sets registers between instructions", SESSION, 11.5)


def draw_disassembler(drawing: Drawing) -> None:
    """The disassembler beside the core: the same opcode structure, never executed."""
    drawing.box(40, 698, 560, 70, "#ffffff", "#555555", dashed=True)
    drawing.text(320, 724, "Disassembler - cpu/disasm.py + CPC RST profile", 14, weight="bold")
    drawing.text(320, 746, "decodes the same opcodes from bytes - never executes them", 12,
                 fill=SUBTEXT)  # fmt: skip
    drawing.text(320, 762, "analysis/cpc_profile.py", 11, fill=MODULE, mono=True)


def z80_svg() -> str:
    """The Z80 diagram as an SVG document."""
    drawing = Drawing("z80", marker_size=HEAD)
    draw_outside(drawing)
    draw_parts(drawing)
    draw_steps(drawing)
    draw_disassembler(drawing)
    draw_session(drawing)
    markers = {"clock": CLOCK, "interrupt": INTERRUPT, "bus": CONTROL, "session": SESSION,
               "other": OTHER}  # fmt: skip
    return drawing.svg(WIDTH, HEIGHT, "Block diagram of the Z80 core and one instruction", markers)


def main() -> None:
    """Write docs/img/z80.svg."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(z80_svg(), encoding="utf-8")
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
