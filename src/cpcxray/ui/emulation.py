"""The GUI's emulation loop: time slices, real-time pacing, queued commands (D-13, D-29).

The emulation runs single-threaded in the GUI thread. Every GUI frame calls `frame()`, which

1. executes the commands queued by other threads (the MCP server over HTTP) - they see a
   consistent machine because nothing else runs at that moment;
2. if the machine is running, emulates a time slice: in real-time mode exactly as much
   emulated time as wall-clock time has passed since the last frame (so the CPC keeps its
   real speed whatever the display's frame rate), in max-speed mode as many 20 ms slices as
   fit into a frame's wall-clock budget.

A run stops on its own at a breakpoint or a HALT that cannot end; a `pause` from MCP stops
the free run too (the session counts pause calls in `pause_generation`).

This module has no Dear PyGui dependency, so it is tested without a window.
"""

from __future__ import annotations

import time
from typing import Any

from cpcxray.session.commands import CommandQueue
from cpcxray.session.session import Session

FRAME_US = 19968  # one CPC frame (D-05)
CPC_FPS = 50  # the CPC's own frame rate: the reference for the GUI's display speed
MAX_CATCH_UP_US = 5 * FRAME_US  # after a stall, do not try to catch up more than this
MAX_SPEED_WALL_BUDGET_S = 0.015  # per GUI frame, leaves time for rendering at 50-60 Hz


class EmulationLoop:  # pylint: disable=too-many-instance-attributes  # loop state and statistics
    """Drives the session from the GUI thread.

    Attributes:
        session: The session (owned by the GUI thread).
        commands: Queue of commands from other threads (MCP over HTTP).
        running: The machine runs freely (Run pressed, not yet stopped).
        max_speed: Run as fast as possible instead of in real time.
        last_stop: Result of the last run that stopped on its own (breakpoint, ...).
        speed: Emulated time / wall-clock time over the last frames (1.0 = real time).
        fps: GUI frames per wall-clock second over the last frames; the CPC shows 50, so
            `fps / CPC_FPS` is the display speed (40 fps = 0.80 even if emulation keeps up).
        until_address: Target of "run to address": the free run stops when the PC gets there.
    """

    def __init__(self, session: Session, commands: CommandQueue) -> None:
        """Create a stopped loop."""
        self.session = session
        self.commands = commands
        self.running = False
        self.max_speed = False
        self.last_stop: dict[str, Any] | None = None
        self.speed = 0.0
        self.fps = 0.0
        self.until_address: int | None = None
        self._seen_pause = session.pause_generation
        self._last_frame = time.perf_counter()

    # -- control (called from the GUI thread) ----------------------------------------------------

    def start(self) -> None:
        """Start running freely."""
        self.running = True
        self.last_stop = None
        self._last_frame = time.perf_counter()

    def stop(self) -> None:
        """Stop running (also ends a "run to address"); for the session this is a stop."""
        if self.running:
            self.session._free_run_stopped()  # pylint: disable=protected-access  # its stop bookkeeping
        self.running = False
        self.until_address = None

    def run_to(self, address: int) -> None:
        """Run freely until the PC reaches `address` (or a breakpoint stops it)."""
        self.start()
        self.until_address = address

    def step_out(self) -> dict[str, Any]:
        """Stop and run until the current subroutine returns (bounded by the session)."""
        self.stop()
        self.last_stop = self.session.step_out()
        return self.last_stop

    def step(self) -> dict[str, Any]:
        """Stop and execute one instruction."""
        self.stop()
        self.last_stop = self.session.step()
        return self.last_stop

    def step_over(self) -> dict[str, Any]:
        """Stop and step over one instruction (calls run to completion)."""
        self.stop()
        self.last_stop = self.session.step_over()
        return self.last_stop

    def reset(self) -> dict[str, Any]:
        """Reset the machine; a running machine keeps running."""
        self.last_stop = None
        return self.session.reset()

    # -- per frame -------------------------------------------------------------------------------

    def frame(self) -> None:
        """Execute queued commands, then emulate the next time slice if running."""
        self.commands.run_pending()
        if self.session.pause_generation != self._seen_pause:
            self._seen_pause = self.session.pause_generation
            self.running = False
        now = time.perf_counter()
        elapsed = now - self._last_frame
        self._last_frame = now
        if elapsed > 0:
            self.fps = 0.8 * self.fps + 0.2 / elapsed
        if not self.running:
            self.speed = 0.0
            return
        emulated_us = self._run_max_speed() if self.max_speed else self._run_real_time(elapsed)
        if elapsed > 0:
            self.speed = 0.8 * self.speed + 0.2 * (emulated_us / (elapsed * 1e6))

    def _run_real_time(self, elapsed_s: float) -> int:
        """Emulate as much time as has passed on the wall clock (bounded)."""
        budget_us = min(int(elapsed_s * 1e6), MAX_CATCH_UP_US)
        if budget_us <= 0:
            return 0
        return self._run_slice(budget_us)

    def _run_max_speed(self) -> int:
        """Emulate frame-sized slices until the wall-clock budget of this frame is used."""
        deadline = time.perf_counter() + MAX_SPEED_WALL_BUDGET_S
        emulated_us = 0
        while self.running and time.perf_counter() < deadline:
            emulated_us += self._run_slice(FRAME_US)
        return emulated_us

    def _run_slice(self, budget_us: int) -> int:
        """Run one slice; a stop other than the time limit ends the free run."""
        if self.until_address is not None:
            result = self.session.run_until(self.until_address, max_us=budget_us, timeout_s=5)
        else:
            result = self.session.run(max_us=budget_us, timeout_s=5)
        if result["reason"] != "time_limit":
            self.stop()
            self.last_stop = result
        return int(result["emulated_us"])

    def status_line(self) -> str:
        """Return a one-line status: state, PC, clock, speed, last stop reason."""
        cpu = self.session.machine.cpu
        state = "running" if self.running else "paused"
        clock_s = self.session.machine.clock_us / 1e6
        line = f"{state}  PC={cpu.pc:04X}  t={clock_s:8.3f} s"
        if self.running:
            line += f"  speed {self.speed:4.2f}x" + (" (max)" if self.max_speed else "")
            line += f"  {self.fps:4.1f} fps ({self.fps / CPC_FPS:4.2f})"
        if self.session.trace.enabled:
            line += "  TRACE" + (" to file" if self.session.trace.path else "")
        if self.session.bus_trace.enabled:
            line += "  BUS TRACE"
        if self.last_stop is not None:
            line += f"  stopped: {self.last_stop['reason']} at {self.last_stop['pc']}"
            hit = self.last_stop.get("breakpoint")
            if hit is not None and "access" in hit:
                line += (f" ({hit['access']} {hit['address']}={hit['value']} by "
                         f"{hit['pc']}, {hit['location']})")  # fmt: skip
        return line
