"""Draw docs/img/session.svg: the session layer as a block diagram, in the machine diagram's style.

Run `python -m tools.draw_session_diagram` after changing the session's structure. There are no
buses in this layer; the Session API takes their place as the thick spine everything hangs
on (every public method is one MCP tool, D-27): the clients above call it, its mixins hang below
it, and below them the services they use. At the bottom the machine (diagram 2) - called by the
session, watched by the observers - and the project folder the services read and write.
"""

from __future__ import annotations

from pathlib import Path

from tools.draw_machine_diagram import ARROW, GAP, HEAD, INK, MODULE, OTHER, SUBTEXT, Drawing

OUT = Path(__file__).resolve().parents[1] / "docs" / "img" / "session.svg"
WIDTH, HEIGHT = 1480, 840

API, CALLS, USES = "#f59f00", "#3b5bdb", "#7048e8"  # the spine, client calls, mixin → service
MACHINE_CALLS, OBSERVERS, FILES = "#e8590c", "#e03131", "#2b8a3e"
SPINE_Y, SPINE_START, SPINE_END = 290, 200, 1360
SPINE_WIDTH = 30  # wider than the label's plate, so the spine runs on unbroken
MIXIN_Y, MIXIN_HEIGHT, MIXIN_WIDTH = 340, 112, 165
SERVICE_Y, SERVICE_HEIGHT = 520, 112
BOTTOM_Y = 730
CLIENT_Y = 45

# x, name, lines, module
MIXINS = (
    (260, "DebuggerMixin", ("breakpoints · bus trace", "memory views · paging",
                            "devices · stack · .sna"), "session/debugger.py"),
    (439, "Views", ("TraceViewMixin", "BusViewMixin", "MemoryFollowMixin"), "session/*_view.py"),
    (618, "SymbolsMixin", ("labels · comments", "own variables", "search · detail"),
     "session/symbols_view.py"),
    (797, "DiskMixin", ("drives A and B", "insert · eject · save", "catalogue · files"),
     "session/disks.py"),
    (976, "ProjectMixin", ("config documents", "ROM headers", "files to load"),
     "session/project.py"),
    (1155, "AnalysisMixin", ("targets · decoding", "database · source", "verify · phases"),
     "session/analysis*.py"),
)  # fmt: skip
# x, width, name, lines, module
SERVICES = (
    (260, 344, "debugger/", ("breakpoints · bus_trace · trace · stack",
                             "step_accesses · memory_writes · ports"), "debugger/*.py"),
    (618, 165, "Symbols", ("SymbolTable", "VariableMap"), "debugger/symbols.py"),
    (797, 165, "disk/", ("DSK · EDSK images", "CP/M reader"), "disk/dsk.py · cpm.py"),
    (976, 165, "Configuration", ("load · validate", "write machine.toml"), "machine/config.py"),
    (1155, 205, "analysis/ · decoding/", ("observers · database", "classifier · source_gen",
                                          "shadow ROM · m1map"), "analysis/ · decoding/"),
)  # fmt: skip


def draw_clients(drawing: Drawing) -> None:
    """The clients above the session layer, in their own frame, and their way to the Session API."""
    drawing.box(20, 10, 1440, 145, "#ffffff", "#868e96", dashed=True)
    drawing.text(40, 33, "Clients", 15, weight="bold", anchor="start")
    drawing.chip(40, CLIENT_Y, 150, 82, "Claude Code", ("an MCP client",), "", "#ffe8cc")
    drawing.chip(230, CLIENT_Y, 210, 82, "MCP server", ("every method = one tool",),
                 "mcp_server/server.py", "#e5dbff")  # fmt: skip
    drawing.chip(480, CLIENT_Y, 210, 82, "Command queue", ("between time slices (D-29)",),
                 "session/commands.py", "#e5dbff")  # fmt: skip
    drawing.chip(
        730, CLIENT_Y, 210, 82, "Window", ("GUI thread",), "ui/app.py · ui/panels/", "#ffe8cc"
    )
    drawing.chip(980, CLIENT_Y, 150, 82, "CLI", ("cpcxray …",), "cli.py", "#ffe8cc")
    drawing.chip(1170, CLIENT_Y, 190, 82, "Scripts · tests", ("Python",), "tests/", "#ffe8cc")
    drawing.line([(190, CLIENT_Y + 41), (230 - GAP, CLIENT_Y + 41)], CALLS, ARROW, arrow="calls")
    drawing.label(209, CLIENT_Y + 92, "stdio / HTTP", CALLS, 10.5)
    drawing.line([(440, CLIENT_Y + 41), (480 - GAP, CLIENT_Y + 41)], CALLS, ARROW, arrow="calls")
    for x in (585, 835, 1055, 1265):
        drawing.line(
            [(x, CLIENT_Y + 82), (x, SPINE_Y - SPINE_WIDTH / 2 - GAP)], CALLS, ARROW, arrow="calls"
        )
    drawing.label(835, 237, "direct calls in the GUI thread", CALLS, 11.5)


def draw_frame(drawing: Drawing) -> None:
    """The session layer's frame (drawn first: every line crosses it on top)."""
    drawing.box(20, 175, 1440, 505, "#f8f9fb", "#868e96", dashed=True)
    drawing.text(40, 198, "Session layer - session/ (D-27)", 15, weight="bold", anchor="start")


def draw_layer(drawing: Drawing) -> None:
    """The Session core, the Session API spine and the mixins."""
    for x, *_ in MIXINS:
        drawing.line([(x + MIXIN_WIDTH / 2, SPINE_Y), (x + MIXIN_WIDTH / 2, MIXIN_Y)], API, 6)
    drawing.line([(SPINE_START, SPINE_Y), (SPINE_END, SPINE_Y)], API, SPINE_WIDTH)
    drawing.label(780, SPINE_Y,
                  "Session API · every public method is one MCP tool · dict results · "
                  "SessionError · bounded runs", INK, 12.5)  # fmt: skip
    drawing.chip(40, 215, 160, 250, "Session", ("run / step loop", "CPU · memory", "disassembly",
                 "trace", "machine install", "addresses · paths"), "session/session.py",
                 "#fff3bf")  # fmt: skip
    for x, name, lines, module in MIXINS:
        drawing.chip(x, MIXIN_Y, MIXIN_WIDTH, MIXIN_HEIGHT, name, lines, module, "#e7f5ff")


def draw_services(drawing: Drawing) -> None:
    """The services below the mixins and which mixin uses which."""
    uses = ((260, 400), (439, 520), (618, 700), (797, 880), (976, 1058), (1155, 1258))
    for mixin_x, service_x in uses:
        start = mixin_x + MIXIN_WIDTH / 2
        if abs(service_x - start) < 10:  # no tiny step: straight down into the service
            points = [(start, MIXIN_Y + MIXIN_HEIGHT), (start, SERVICE_Y - GAP)]
        else:
            points = [(start, MIXIN_Y + MIXIN_HEIGHT), (start, 486), (service_x, 486),
                      (service_x, SERVICE_Y - GAP)]  # fmt: skip
        drawing.line(points, USES, ARROW, arrow="uses")
    for x, width, name, lines, module in SERVICES:
        drawing.chip(x, SERVICE_Y, width, SERVICE_HEIGHT, name, lines, module, "#f3f0ff")


def draw_bottom(drawing: Drawing) -> None:
    """The machine (diagram 2) and the project folder, with what reaches them."""
    drawing.box(40, BOTTOM_Y, 540, 90, "#ffffff", "#555555", dashed=True)
    drawing.text(310, BOTTOM_Y + 30, "CPC machine - see diagram 2", 16, weight="bold")
    drawing.text(310, BOTTOM_Y + 52, "CPU bus · memory · ROM slots · devices · drives", 12.5,
                 fill=SUBTEXT)  # fmt: skip
    drawing.text(310, BOTTOM_Y + 74, "machine/cpc464.py", 11.5, fill=MODULE, mono=True)
    drawing.box(640, BOTTOM_Y, 720, 90, "#ebfbee", "#2b8a3e")
    drawing.text(1000, BOTTOM_Y + 30, "Project folder", 16, weight="bold")
    contents = ("config/*.toml · roms/ · disk/ · project/annotations.txt · "
                "project/<rom>.analysis.json · .m1map · .asm · exports")  # fmt: skip
    drawing.text(1000, BOTTOM_Y + 52, contents, 12, fill=SUBTEXT)
    drawing.text(1000, BOTTOM_Y + 74, "relative paths start here", 11.5, fill=MODULE, mono=True)
    # the session drives the machine; the disks go into its drives
    drawing.line([(120, 465), (120, BOTTOM_Y - GAP)], MACHINE_CALLS, ARROW, arrow="machine")
    drawing.label(120, 597, "step · run · peek · poke", MACHINE_CALLS, 11.5)
    drawing.line([(840, SERVICE_Y + SERVICE_HEIGHT), (840, 700), (520, 700),
                   (520, BOTTOM_Y - GAP)], MACHINE_CALLS, ARROW, arrow="machine")  # fmt: skip
    drawing.label(610, 700, "disk images\ninto the drives", MACHINE_CALLS, 11)
    # the observers watch the machine's bus
    drawing.line([(400, SERVICE_Y + SERVICE_HEIGHT), (400, BOTTOM_Y - GAP)], OBSERVERS, ARROW,
                 dashed=True, arrow="observe")  # fmt: skip
    drawing.line([(1210, SERVICE_Y + SERVICE_HEIGHT), (1210, 668), (460, 668),
                   (460, BOTTOM_Y - GAP)], OBSERVERS, ARROW, dashed=True,
                 arrow="observe")  # fmt: skip
    drawing.label(320, 687, "bus observers (D-12)", OBSERVERS, 11.5)
    # files read and written
    for x in (700, 920, 1058, 1310):
        drawing.line([(x, SERVICE_Y + SERVICE_HEIGHT), (x, BOTTOM_Y - GAP)], FILES, ARROW,
                     arrow="files")  # fmt: skip
    drawing.label(1135, 713, "read · write", FILES, 11.5)


def session_svg() -> str:
    """The session diagram as an SVG document."""
    drawing = Drawing("session", marker_size=HEAD)
    draw_frame(drawing)
    draw_clients(drawing)
    draw_layer(drawing)
    draw_services(drawing)
    draw_bottom(drawing)
    markers = {"calls": CALLS, "uses": USES, "machine": MACHINE_CALLS, "observe": OBSERVERS,
               "files": FILES, "other": OTHER}  # fmt: skip
    return drawing.svg(WIDTH, HEIGHT, "Block diagram of the session layer", markers)


def main() -> None:
    """Write docs/img/session.svg."""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(session_svg(), encoding="utf-8")
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
