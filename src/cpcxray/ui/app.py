"""Dear PyGui window: CPC screen, controls and debugger panels (design.md §6.12; D-02, D-13).

Left: the rendered screen (768 x 272 texture, drawn with doubled scan lines at 768 x 544), a
tool bar (Run/Pause, Step, Step over, Step out, Reset, "Max speed", "Analysis" on/off), the
indicators (ANALYSIS while the analysis observers run, one activity LED per drive), the status
line and the "Type text" box. Right: the debugger panels (ui/panels/: CPU, memory, devices,
breakpoints, traces, symbols, disks, files). Everything runs in the GUI thread: per frame the
`EmulationLoop` executes queued MCP commands and emulates a time slice, then the screen is
rendered from a snapshot, the visible panel is refreshed (only while stopped), and the frame
is drawn.

With an MCP port, the same tools as the headless server are offered over streamable HTTP on
127.0.0.1 (D-28); their commands run between frames (D-29), so Claude sees and drives exactly
what the owner sees. The PC keyboard drives the CPC key matrix (ui/host_keys.py); the "Type text"
box types exact characters; while a text input has the focus, PC keys go to it, not to the CPC.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import dearpygui.dearpygui as dpg
import numpy as np

from cpcxray.session.commands import CommandQueue, SessionClient
from cpcxray.session.session import Session, SessionError
from cpcxray.ui.emulation import EmulationLoop
from cpcxray.ui.host_keys import build_host_key_map
from cpcxray.ui.panels.tabs import PANEL_WIDTH, DebuggerPanels
from cpcxray.ui.renderer import HEIGHT, WIDTH, render

TITLE = "CPC X-Ray"
DISPLAY_SCALE = 1  # 768 x 544 on screen
FRAME_SECONDS = 1 / 50
ANALYSIS_COLOUR = (240, 190, 40)  # the analysis observers are running (costly)
DRIVE_LED_COLOUR = (240, 60, 40)  # drive activity LED lit
DARK_COLOUR = (110, 110, 110)  # indicator off
PANEL_REFRESH_FRAMES = 5  # the visible panel is redrawn every 5th frame (10 per second)


@dataclass
class _Widgets:
    """Dear PyGui item ids of the window's widgets."""

    texture: int = 0
    status: int = 0
    run_button: int = 0
    type_box: int = 0
    analysis_button: int = 0
    analysis_light: int = 0
    drive_lights: tuple[int, ...] = ()


class App:  # pylint: disable=too-many-instance-attributes  # loop, widgets, panels, MCP
    """The GUI application around one session."""

    def __init__(
        self, session: Session, mcp_port: int | None = None, autostart: bool = False
    ) -> None:
        """Create the app.

        Args:
            session: The session to show and drive.
            mcp_port: Serve MCP over HTTP on 127.0.0.1 at this port, or None.
            autostart: Start running when the window opens (the CLI default: the CPC boots
                like a real machine); False starts stopped.
        """
        self.session = session
        self.commands = CommandQueue()
        self.loop = EmulationLoop(session, self.commands)
        self.autostart = autostart
        self.mcp_port = mcp_port
        self._widgets = _Widgets()
        self._host_keys: dict[int, str] = {}
        self.panels = DebuggerPanels(session, self.loop)
        self._frames = 0

    # -- building the window ---------------------------------------------------------------------

    def build(self) -> None:
        """Create the Dear PyGui context, texture, window and viewport."""
        dpg.create_context()
        self._host_keys = build_host_key_map()  # needs the context
        with dpg.texture_registry():
            self._widgets.texture = dpg.add_dynamic_texture(
                WIDTH, HEIGHT, np.zeros(WIDTH * HEIGHT * 4, dtype=np.float32)
            )
        with dpg.window(tag="main"), dpg.group(horizontal=True):
            with dpg.group():
                self._build_screen_column()
            with dpg.child_window(width=PANEL_WIDTH + 20, border=False):
                self.panels.build()
        dpg.create_viewport(
            title=TITLE, width=WIDTH * DISPLAY_SCALE + PANEL_WIDTH + 70, height=2 * HEIGHT + 170
        )
        dpg.setup_dearpygui()
        dpg.set_primary_window("main", True)
        dpg.show_viewport()

    def _build_screen_column(self) -> None:
        """Tool bar, screen, status line and the "Type text" box."""
        with dpg.group(horizontal=True):
            self._widgets.run_button = dpg.add_button(
                label="Run", width=70, callback=self._toggle_run
            )
            dpg.add_button(label="Step", callback=self.loop.step)
            dpg.add_button(label="Step over", callback=self.loop.step_over)
            dpg.add_button(label="Step out", callback=self.loop.step_out)
            dpg.add_button(label="Reset", callback=self.loop.reset)
            dpg.add_checkbox(label="Max speed", callback=self._set_max_speed)
            self._widgets.analysis_button = dpg.add_button(
                label="Analysis: on", width=110, callback=self._toggle_analysis
            )
            if self.mcp_port:
                dpg.add_text(f"MCP: http://127.0.0.1:{self.mcp_port}/mcp")
        dpg.add_image(
            self._widgets.texture,
            width=WIDTH * DISPLAY_SCALE,
            height=2 * HEIGHT * DISPLAY_SCALE,
        )
        with dpg.group(horizontal=True):
            self._widgets.analysis_light = dpg.add_text("ANALYSIS")
            self._widgets.drive_lights = tuple(
                dpg.add_text(f"\u25cf {name}:") for name in ("A", "B")
            )  # one activity LED per drive (the motor is shared, see devices/fdc765.py)
        self._widgets.status = dpg.add_text("", wrap=WIDTH)
        with dpg.group(horizontal=True):
            dpg.add_text("Type text:")
            self._widgets.type_box = dpg.add_input_text(
                width=WIDTH - 160, on_enter=True, callback=self._type_text,
                hint="characters are typed exactly; Enter adds RETURN",
            )  # fmt: skip

    def _toggle_analysis(self) -> None:
        """Analysis button: switch the analysis observers on or off (about 2x speed when off)."""
        self.session.analysis_enable(not self.session.analysis_enabled)

    def _update_indicators(self) -> None:
        """Analysis indicator and drive LEDs."""
        enabled = self.session.analysis_enabled and bool(self.session.analysis_targets)
        dpg.set_item_label(
            self._widgets.analysis_button, "Analysis: on" if self.session.analysis_enabled
            else "Analysis: off",
        )  # fmt: skip
        dpg.set_value(self._widgets.analysis_light, "ANALYSIS" if enabled else "analysis off")
        dpg.configure_item(self._widgets.analysis_light,
                           color=ANALYSIS_COLOUR if enabled else DARK_COLOUR)  # fmt: skip
        fdc = getattr(self.session.machine, "fdc", None)
        lights = fdc.drive_lights() if fdc is not None else [False, False]
        for item, lit in zip(self._widgets.drive_lights, lights, strict=True):
            dpg.configure_item(item, color=DRIVE_LED_COLOUR if lit else DARK_COLOUR)

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

    def _type_text(self, _sender: int, text: str) -> None:
        """Type the box's text (plus RETURN) into the running CPC, then clear the box."""
        try:
            self.session.type_text(text + "\r", wait=False)
        except SessionError as error:
            dpg.set_value(self._widgets.status, f"cannot type: {error}")
            return
        dpg.set_value(self._widgets.type_box, "")

    def _update_host_keys(self) -> None:
        """Hold the CPC keys for the PC keys that are down (not while typing into the box)."""
        keyboard = self.session.machine.keyboard  # type: ignore[attr-defined]
        if dpg.is_item_focused(self._widgets.type_box) or self.panels.typing():
            keyboard.release_all("host")
            return
        held = set()
        key_map = self.session.machine.key_map()  # type: ignore[attr-defined]
        for host_key, cpc_key in self._host_keys.items():
            if dpg.is_key_down(host_key):
                held.add(key_map.key_number(cpc_key))
        keyboard.set_held("host", held)

    def render_frame(self) -> None:
        """One GUI frame: keyboard, emulate, update texture and status, draw."""
        if self.session.machine.memory.lower_rom is not None:  # type: ignore[attr-defined]
            self._update_host_keys()
        self.loop.frame()
        image = render(self.session.machine.snapshot())  # type: ignore[attr-defined]
        dpg.set_value(self._widgets.texture, (image.astype(np.float32) / 255.0).ravel())
        dpg.set_item_label(self._widgets.run_button, "Pause" if self.loop.running else "Run")
        dpg.set_value(self._widgets.status, self.loop.status_line())
        self._update_indicators()
        if self._frames % PANEL_REFRESH_FRAMES == 0:
            self.panels.refresh()
        self._frames += 1
        dpg.render_dearpygui_frame()

    def run(self, max_frames: int | None = None) -> None:
        """Show the window until it is closed (or `max_frames` frames have been drawn)."""
        if self.mcp_port:
            from cpcxray.mcp_server.server import (  # pylint: disable=import-outside-toplevel
                serve_http_in_background,
            )

            serve_http_in_background(SessionClient(self.session, self.commands), self.mcp_port)
        self.build()
        if self.autostart:
            self.loop.start()
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
            self.session.analysis_save()  # access maps and databases survive (D-19, D-31)
            dpg.destroy_context()
