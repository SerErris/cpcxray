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
what the owner sees. The PC keyboard drives the CPC key matrix by character (ui/host_keys.py,
D-40): a hidden text input stays active while no other input is being typed into and collects
the characters the PC's layout produces; the "Type text" box types a whole line; while a panel
input is active, a list is under the mouse or a file selector is open, keys go there instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import dearpygui.dearpygui as dpg
import numpy as np

from cpcxray.session.commands import CommandQueue, SessionClient
from cpcxray.session.session import Session, SessionError
from cpcxray.ui.emulation import EmulationLoop
from cpcxray.ui.host_keys import (
    MAX_INPUT,
    CharacterInput,
    HostKeyboard,
    HostKeyCodes,
    function_keys,
    letter_keys,
)
from cpcxray.ui.panels.tabs import PANEL_WIDTH, DebuggerPanels
from cpcxray.ui.panels.widgets import ICON_FONT_SIZE, Styles, load_font
from cpcxray.ui.renderer import HEIGHT, WIDTH, render

TITLE = "CPC X-Ray"
DISPLAY_SCALE = 1  # 768 x 544 on screen
FRAME_SECONDS = 1 / 50
ANALYSIS_COLOUR = (240, 190, 40)  # the analysis observers are running (costly)
DRIVE_LED_COLOUR = (240, 60, 40)  # drive activity LED lit
DARK_COLOUR = (110, 110, 110)  # indicator off
PANEL_REFRESH_FRAMES = 5  # the visible panel is redrawn every 5th frame (10 per second)
NOTE_SECONDS = 3.0  # how long the status line names a character the CPC cannot type
MISSING_SECONDS = 60.0  # how long it names missing ROM / disk files after the start
# ImGui codes of the symbol keys (Apostrophe … GraveAccent): Dear PyGui 2.3 lacks some of them
IMGUI_SYMBOL_KEYS = range(596, 607)


@dataclass
class _Widgets:  # pylint: disable=too-many-instance-attributes  # one id per widget
    """Dear PyGui item ids of the window's widgets."""

    texture: int = 0
    status: int = 0
    run_button: int = 0
    type_box: int = 0
    key_inputs: tuple[int, int] = (0, 0)  # the two hidden character inputs (ui/host_keys.py)
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
        self._host_codes: HostKeyCodes | None = None
        self._host_keyboard: HostKeyboard | None = None
        self._host_keys: tuple[int, ...] = ()  # the PC keys polled every frame
        self._characters = CharacterInput()
        self._key_input = 0  # which hidden input collects the characters now
        self._note = ("", 0.0)  # a note for the status line (missing ROMs, …) and until when
        self.panels = DebuggerPanels(session, self.loop)
        self._frames = 0

    # -- building the window ---------------------------------------------------------------------

    def build(self) -> None:
        """Create the Dear PyGui context, texture, window and viewport."""
        dpg.create_context()
        self._host_codes, self._host_keys = _host_key_codes()  # needs the context
        styles = Styles()  # the palette of ui/panels/colours.py for the whole window
        dpg.bind_theme(styles.window)
        font = load_font()
        if font is not None:
            dpg.bind_font(font)
            styles.icon_font = load_font(size=ICON_FONT_SIZE)
        with dpg.texture_registry():
            self._widgets.texture = dpg.add_dynamic_texture(
                WIDTH, HEIGHT, np.zeros(WIDTH * HEIGHT * 4, dtype=np.float32)
            )
        with dpg.window(tag="main"), dpg.group(horizontal=True):
            with dpg.group():
                self._build_screen_column()
            with dpg.child_window(width=PANEL_WIDTH + 20, border=False):
                self.panels.build(styles)
        self.panels.refresh(force=True)  # fill every panel once (they are frozen while running)
        skipped = getattr(self.session.machine, "skipped_roms", [])
        if skipped:  # e.g. a ROM file missing from roms/: say so instead of a black screen
            self._note = ("missing: " + "; ".join(skipped), time.monotonic() + MISSING_SECONDS)
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
        # off-screen: they only collect the characters the PC keyboard types (D-40)
        self._widgets.key_inputs = (dpg.add_input_text(width=1, pos=(-100, -100)),
                                    dpg.add_input_text(width=1, pos=(-100, -100)))  # fmt: skip

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
        """Hold the CPC keys for what the PC keyboard types now (ui/host_keys.py, D-40).

        The hidden input takes the keys back only when it has lost them and no mouse button is
        down and no other input is being typed into (taking them every frame would swallow
        clicks); "active", not "focused": an input stays focused after a click elsewhere.
        """
        machine = self.session.machine
        keyboard = machine.keyboard  # type: ignore[attr-defined]
        hidden = self._widgets.key_inputs[self._key_input]
        elsewhere = dpg.is_item_active(self._widgets.type_box) or self.panels.typing()
        mouse_down = any(dpg.is_mouse_button_down(button) for button in (0, 1, 2))
        if not dpg.is_item_active(hidden) and not elsewhere and not mouse_down:
            dpg.focus_item(hidden)
        characters = self._characters.new_characters(dpg.get_value(hidden))
        if elsewhere or self._host_codes is None:
            keyboard.release_all("host")
            return
        key_map = machine.key_map()  # type: ignore[attr-defined]  # follows a new OS ROM
        if self._host_keyboard is None or self._host_keyboard.key_map is not key_map:
            self._host_keyboard = HostKeyboard(self._host_codes, key_map)
        host = self._host_keyboard
        pressed = {key for key in self._host_keys if dpg.is_key_pressed(key)}
        down = {key for key in self._host_keys if dpg.is_key_down(key)}
        keyboard.set_held("host", host.update(characters, pressed, down))
        if host.taps:
            keyboard.schedule_typing(host.taps, machine.clock_us)
            host.taps.clear()
        if host.unknown:
            self._note = (f"no CPC key types {''.join(host.unknown)!r}",
                                  time.monotonic() + NOTE_SECONDS)  # fmt: skip
            host.unknown.clear()
        if len(dpg.get_value(hidden)) > MAX_INPUT and not down:
            self._switch_key_input()

    def _switch_key_input(self) -> None:
        """Empty the hidden input: the other one takes over (an active input keeps its text)."""
        self._key_input = 1 - self._key_input
        dpg.set_value(self._widgets.key_inputs[self._key_input], "")
        self._characters.reset()
        dpg.focus_item(self._widgets.key_inputs[self._key_input])

    def render_frame(self) -> None:
        """One GUI frame: keyboard, emulate, update texture and status, draw."""
        if self.session.machine.memory.lower_rom is not None:  # type: ignore[attr-defined]
            self._update_host_keys()
        self.panels.handle_keys()  # list keys over a list (they do not reach the CPC then)
        self.loop.frame()
        image = render(self.session.machine.snapshot())  # type: ignore[attr-defined]
        dpg.set_value(self._widgets.texture, (image.astype(np.float32) / 255.0).ravel())
        dpg.set_item_label(self._widgets.run_button, "Pause" if self.loop.running else "Run")
        note, until = self._note
        status = self.loop.status_line()
        if note and time.monotonic() < until:
            status = f"{status}  \u00b7  {note}"
        dpg.set_value(self._widgets.status, status)
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


def _host_key_codes() -> tuple[HostKeyCodes, tuple[int, ...]]:
    """The PC key codes for `HostKeyboard`, and all keys to poll each frame.

    Only real keys are polled: Dear PyGui 2.3 has constants of -1 for keys it does not support
    and ASCII values for some symbol keys, which read as held.
    """

    def code(name: str) -> int:
        """The Dear PyGui code of `mvKey_<name>`."""
        return int(getattr(dpg, f"mvKey_{name}"))

    functions = function_keys(code)
    letters = letter_keys(code)
    shift = frozenset({code("LShift"), code("RShift")})
    control = frozenset({code("LControl"), code("RControl")})
    others = {code("LAlt"), code("RAlt"), code("CapsLock"), code("LWin"), code("RWin")}
    characters = {code(str(digit)) for digit in range(10)} | {code("Spacebar")}
    codes = HostKeyCodes(functions, letters, shift, control,
                         ignored=frozenset(shift | control | others))  # fmt: skip
    polled = set(functions) | set(letters) | shift | control | others | characters
    return codes, tuple(sorted(polled | set(IMGUI_SYMBOL_KEYS)))
