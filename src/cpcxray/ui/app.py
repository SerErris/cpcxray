"""Dear PyGui shell: CPC screen, run/pause/step/reset, status (design.md §6.12; D-02, D-13).

The window shows the rendered screen (768 x 272 texture, drawn with doubled scan lines at
768 x 544) and a tool bar: Run/Pause, Step, Step over, Reset and "Max speed". Everything runs
in the GUI thread: per frame the `EmulationLoop` executes queued MCP commands and emulates a
time slice, then the screen is rendered from a snapshot and the frame is drawn.

With an MCP port, the same tools as the headless server are offered over streamable HTTP on
127.0.0.1 (D-28); their commands run between frames (D-29), so Claude sees and drives exactly
what the owner sees. Keyboard input follows in phase 6; the full debugger panels in phase 9.
"""

from __future__ import annotations

import time

import dearpygui.dearpygui as dpg
import numpy as np

from cpcxray.session.commands import CommandQueue, SessionClient
from cpcxray.session.session import Session
from cpcxray.ui.emulation import EmulationLoop
from cpcxray.ui.renderer import HEIGHT, WIDTH, render

TITLE = "CPC X-Ray"
DISPLAY_SCALE = 1  # 768 x 544 on screen
FRAME_SECONDS = 1 / 50


class App:
    """The GUI application around one session."""

    def __init__(self, session: Session, mcp_port: int | None = None) -> None:
        """Create the app; with `mcp_port` the MCP server runs over HTTP on 127.0.0.1."""
        self.session = session
        self.commands = CommandQueue()
        self.loop = EmulationLoop(session, self.commands)
        self.mcp_port = mcp_port
        self._texture = 0
        self._status = 0
        self._run_button = 0

    # -- building the window ---------------------------------------------------------------------

    def build(self) -> None:
        """Create the Dear PyGui context, texture, window and viewport."""
        dpg.create_context()
        with dpg.texture_registry():
            self._texture = dpg.add_dynamic_texture(
                WIDTH, HEIGHT, np.zeros(WIDTH * HEIGHT * 4, dtype=np.float32)
            )
        with dpg.window(tag="main"):
            with dpg.group(horizontal=True):
                self._run_button = dpg.add_button(label="Run", width=70, callback=self._toggle_run)
                dpg.add_button(label="Step", callback=self.loop.step)
                dpg.add_button(label="Step over", callback=self.loop.step_over)
                dpg.add_button(label="Reset", callback=self.loop.reset)
                dpg.add_checkbox(label="Max speed", callback=self._set_max_speed)
                if self.mcp_port:
                    dpg.add_text(f"MCP: http://127.0.0.1:{self.mcp_port}/mcp")
            dpg.add_image(
                self._texture, width=WIDTH * DISPLAY_SCALE, height=2 * HEIGHT * DISPLAY_SCALE
            )
            self._status = dpg.add_text("")
        dpg.create_viewport(title=TITLE, width=WIDTH * DISPLAY_SCALE + 40, height=2 * HEIGHT + 120)
        dpg.setup_dearpygui()
        dpg.set_primary_window("main", True)
        dpg.show_viewport()

    def _toggle_run(self) -> None:
        """Run/Pause button."""
        if self.loop.running:
            self.loop.stop()
        else:
            self.loop.start()

    def _set_max_speed(self, _sender: int, value: bool) -> None:
        """Max speed check box."""
        self.loop.max_speed = value

    # -- main loop -------------------------------------------------------------------------------

    def render_frame(self) -> None:
        """One GUI frame: emulate, update texture and status, draw."""
        self.loop.frame()
        image = render(self.session.machine.snapshot())  # type: ignore[attr-defined]
        dpg.set_value(self._texture, (image.astype(np.float32) / 255.0).ravel())
        dpg.set_item_label(self._run_button, "Pause" if self.loop.running else "Run")
        dpg.set_value(self._status, self.loop.status_line())
        dpg.render_dearpygui_frame()

    def run(self, max_frames: int | None = None) -> None:
        """Show the window until it is closed (or `max_frames` frames have been drawn)."""
        if self.mcp_port:
            from cpcxray.mcp_server.server import (  # pylint: disable=import-outside-toplevel
                serve_http_in_background,
            )

            serve_http_in_background(SessionClient(self.session, self.commands), self.mcp_port)
        self.build()
        frames = 0
        try:
            while dpg.is_dearpygui_running() and (max_frames is None or frames < max_frames):
                started = time.perf_counter()
                self.render_frame()
                frames += 1
                remaining = FRAME_SECONDS - (time.perf_counter() - started)
                if remaining > 0 and not self.loop.max_speed:
                    time.sleep(remaining)
        finally:
            dpg.destroy_context()
