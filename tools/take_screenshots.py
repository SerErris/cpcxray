"""Take the window screenshots of the README: docs/img/window_{breakpoints,cpu,trace}.png.

Run `python -m tools.take_screenshots` (needs a display; under WSL: WSLg) after GUI changes. Each
scene boots the CPC 464 of config/test_cpc464.toml in the real window, in a temporary project
folder (never the repository's project/), runs a small BASIC program and stops on a breakpoint
on the firmware's TXT_OUTPUT during LIST; Dear PyGui's frame buffer is saved as the PNG.

* "breakpoints": seven breakpoints of different types, some armed, the run continued a few times
  so several have hits - the Breakpoints tab with the last stop.
* "stepping": 59 steps into TXT_OUTPUT and one single step more (so only that step's register
  changes are yellow) - the CPU tab (registers, paging, disassembly, stack), then the Trace tab
  with the CALL TXT_OUT_ACTION 26 records back selected.

Dear PyGui allows one context per process, so each scene runs in its own process
(`--scene NAME`).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IMAGES = REPO / "docs" / "img"
CONFIG = REPO / "config" / "test_cpc464.toml"
SCENES = ("breakpoints", "stepping")
PROGRAM = '10 FOR i=1 TO 5\r20 PRINT "CPC X-Ray";i\r30 NEXT\rRUN\r'
STEPS_BEFORE_LAST = 59  # then one single step: the CPU tab shows only its changes
TRACE_RECORDS_BACK = 26  # the CALL TXT_OUT_ACTION, selected in the Trace tab


class Scene:
    """The window around a booted CPC 464 that has run the BASIC program."""

    def __init__(self, project_dir: Path) -> None:
        """Open the window and boot to BASIC at maximum speed, then run the program in real time.

        Args:
            project_dir: The temporary project folder of the session.
        """
        # pylint: disable=import-outside-toplevel  # the GUI modules need a display
        import dearpygui.dearpygui as dpg

        from cpcxray.machine.config import load_config
        from cpcxray.machine.cpc464 import CPC464
        from cpcxray.session.session import Session
        from cpcxray.ui.app import App

        self.dpg = dpg
        self.session = Session(CPC464(load_config(CONFIG)), project_dir=project_dir)
        self.app = App(self.session)
        self.app.loop.max_speed = True
        self.app.loop.start()
        self.app.build()
        self.frames_until(lambda: "Ready" in self.session.screen_text()["lines"], 600)
        self.app.loop.max_speed = False
        self.session.type_text(PROGRAM, wait=False)
        self.frames(400)

    def frames(self, count: int) -> None:
        """Draw `count` window frames."""
        for _ in range(count):
            self.app.render_frame()

    def frames_until(self, condition: object, limit: int) -> None:
        """Draw frames until `condition()` is true, at most `limit`."""
        for _ in range(limit):
            self.app.render_frame()
            if condition():  # type: ignore[operator]
                return

    def run_to_stop(self) -> None:
        """Let the machine run until a breakpoint stops it (at most 400 frames)."""
        self.app.loop.start()
        self.frames_until(lambda: not self.app.loop.running, 400)

    def stop_at_text_output(self) -> None:
        """Type LIST and stop on the PC breakpoint #1 on TXT_OUTPUT."""
        self.session.type_text("LIST\r", wait=False)
        self.frames_until(lambda: not self.app.loop.running, 400)

    def save(self, tab: str, file: Path) -> None:
        """Show `tab`, let it fill and save the window as a PNG."""
        self.app.panels.show_tab(tab)
        self.app.panels.refresh(force=True)
        self.frames(30)
        self.dpg.output_frame_buffer(file=str(file))
        self.frames(15)  # the buffer is written while the next frames are drawn
        print(f"written: {file}")

    def close(self) -> None:
        """Close the window."""
        self.dpg.destroy_context()


def breakpoints_scene(scene: Scene) -> None:
    """Breakpoints of seven types, several of them with hits: the Breakpoints tab."""
    session = scene.session
    session.breakpoint_add("TXT_OUTPUT", "pc")
    session.breakpoint_add("KL_TIME", "write")
    session.breakpoint_add("7F00", "io_out", port_mask=0xFF00)
    session.breakpoint_add("F400", "io_in", port_mask=0xFF00)
    session.breakpoint_add("C000", "read", end="C7FF", location="ram")
    session.breakpoint_add(None, "rom_select", rom=7)
    session.breakpoint_add(None, "interrupt")
    for number in range(2, 8):
        session.breakpoint_enable(number, False)
    scene.stop_at_text_output()
    for number in (2, 4, 5):
        session.breakpoint_enable(number, True)
    for _ in range(9):  # continue a few times: hits on several breakpoints
        scene.run_to_stop()
    scene.save("Breakpoints", IMAGES / "window_breakpoints.png")


def stepping_scene(scene: Scene) -> None:
    """Steps into TXT_OUTPUT: the CPU tab, then the Trace tab with an earlier record selected."""
    scene.session.breakpoint_add("TXT_OUTPUT", "pc")
    scene.stop_at_text_output()
    scene.session.step(STEPS_BEFORE_LAST)
    scene.session.step(1)
    scene.save("CPU", IMAGES / "window_cpu.png")
    scene.app.panels.show_tab("Trace")
    scene.app.panels.refresh(force=True)
    trace = scene.app.panels.tabs["Trace"]
    trace.select(len(trace.records) - 1 - TRACE_RECORDS_BACK)
    scene.save("Trace", IMAGES / "window_trace.png")


def run_scene(name: str) -> None:
    """Run one scene in this process."""
    with tempfile.TemporaryDirectory() as project_dir:
        scene = Scene(Path(project_dir))
        try:
            {"breakpoints": breakpoints_scene, "stepping": stepping_scene}[name](scene)
        finally:
            scene.close()


def main() -> None:
    """Take every screenshot (one process per scene) or, with --scene, one scene."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scene", choices=SCENES, help="run only this scene, in this process")
    arguments = parser.parse_args()
    if arguments.scene:
        run_scene(arguments.scene)
        return
    for name in SCENES:
        subprocess.run([sys.executable, "-m", "tools.take_screenshots", "--scene", name],
                       cwd=REPO, check=True)  # fmt: skip


if __name__ == "__main__":
    main()
