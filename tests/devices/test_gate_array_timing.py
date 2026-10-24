"""Tests of the Gate Array's line counter R52 and its lock to VSYNC (D-05).

The device tests drive a Gate Array alone with its own clock, one scan line at a time, and list
the frame lines where INT is latched. The machine test runs the real firmware's MC WAIT FLYBACK
from a bad interrupt phase, the hang the old free-running 3,328 µs timer could get stuck in.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.devices.gate_array import GateArray
from cpcxray.devices.video_timing import FRAME_LINES, LINE_US
from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session
from tests.machines import CONFIG_464, requires_roms, run_until_ready

STEADY_LINES = [2, 54, 106, 158, 210, 262]  # six interrupts per frame, the first in VSYNC


class Clocked:
    """A Gate Array with its own machine clock, advanced one scan line at a time."""

    def __init__(self) -> None:
        """Reset state at clock 0."""
        self.clock_us = 0
        self.gate_array = GateArray(lambda lower, upper: None, None, lambda: self.clock_us)
        self.gate_array.reset()

    @property
    def line(self) -> int:
        """The scan line the clock is in, counted from reset."""
        return self.clock_us // LINE_US

    def run_lines(self, count: int, acknowledge: bool = True) -> list[int]:
        """Run `count` scan lines; return the frame lines where INT was latched.

        Args:
            count: Scan lines to run.
            acknowledge: The CPU accepts every interrupt at once (else INT stays pending).
        """
        latched = []
        for _ in range(count):
            self.clock_us += LINE_US
            pending_before = self.gate_array.interrupt_pending
            self.gate_array.tick(self.clock_us)
            if self.gate_array.interrupt_pending and not pending_before:
                latched.append(self.line % FRAME_LINES)
                if acknowledge:
                    self.gate_array.acknowledge_interrupt()
        return latched

    def run_to_frame_line(self, frame_line: int) -> None:
        """Run (acknowledging) until the clock is at the start of `frame_line`."""
        self.run_lines((frame_line - self.line) % FRAME_LINES)


def test_steady_phase_after_reset() -> None:
    """Frame 0 starts at line 54 (R52 is only 2 at the resync), then lines 2, 54, ... 262."""
    clocked = Clocked()
    assert clocked.run_lines(FRAME_LINES) == STEADY_LINES[1:]
    assert clocked.run_lines(FRAME_LINES) == STEADY_LINES
    assert clocked.run_lines(FRAME_LINES) == STEADY_LINES  # one request at line 2, not two


def test_resync_after_rmr_bit4() -> None:
    """RMR bit 4 at line 100 shifts the phase; the resync at line 2 brings it back."""
    clocked = Clocked()
    clocked.run_lines(FRAME_LINES)
    clocked.run_to_frame_line(100)
    clocked.gate_array.write_rmr(0x10 | 0x01)
    assert clocked.run_lines(FRAME_LINES - 100) == [152, 204, 256, 308]
    # line 2 of the next frame: R52 = 6 < 32, so no request there, only R52 = 0
    assert clocked.run_lines(FRAME_LINES) == STEADY_LINES[1:]
    assert clocked.run_lines(FRAME_LINES) == STEADY_LINES


@pytest.mark.parametrize("bad_line", [281, 300, 311])
def test_bad_phase_resyncs_within_one_frame(bad_line: int) -> None:
    """INT forced just before VSYNC: the resync at line 2 restores the phase in the next frame.

    Line 2 itself requests only when R52 has reached 32 there (INT at line 282 or before).
    """
    clocked = Clocked()
    clocked.run_lines(FRAME_LINES)
    clocked.run_to_frame_line(bad_line - 52)
    clocked.gate_array.r52 = 0  # 52 lines later R52 reaches 52 at `bad_line`
    assert clocked.run_lines(52) == [bad_line]
    clocked.run_to_frame_line(0)
    first_request = [2] if bad_line <= FRAME_LINES + 2 - 32 else []
    assert clocked.run_lines(FRAME_LINES) == first_request + STEADY_LINES[1:]
    assert clocked.run_lines(FRAME_LINES) == STEADY_LINES


def test_late_acknowledge_clears_bit_5() -> None:
    """INT stays pending under DI; acknowledged at R52 = 40 it leaves 8: next INT 44 lines on."""
    clocked = Clocked()
    clocked.run_lines(FRAME_LINES)
    clocked.run_to_frame_line(53)
    assert clocked.run_lines(1, acknowledge=False) == [54]
    clocked.run_lines(40, acknowledge=False)  # interrupts disabled for 40 lines
    gate_array = clocked.gate_array
    assert gate_array.interrupt_pending and gate_array.line_counter == 40
    gate_array.acknowledge_interrupt()
    assert not gate_array.interrupt_pending and gate_array.line_counter == 8
    assert clocked.run_lines(44) == [138]


def test_interrupt_in_us_predicts_the_next_request() -> None:
    """The derived time to the next INT matches the simulation, resync cases included."""
    for frame_line in range(0, FRAME_LINES, 7):
        for r52 in (0, 10, 31, 35, 51):
            clocked = Clocked()
            clocked.run_to_frame_line(frame_line)
            clocked.clock_us += 5  # inside the line
            clocked.gate_array.r52 = r52
            predicted = clocked.gate_array.interrupt_in_us
            start = clocked.clock_us
            while not clocked.gate_array.interrupt_pending:
                clocked.clock_us = clocked.gate_array.next_line_us
                clocked.gate_array.tick(clocked.clock_us)
            assert clocked.clock_us - start == predicted, (frame_line, r52)


def test_frame_line_in_device_view() -> None:
    """The device view shows the frame line (VSYNC: lines 0-7) next to R52."""
    clocked = Clocked()
    clocked.run_lines(FRAME_LINES + 60)
    state = clocked.gate_array.state()
    assert state["frame_line"] == 60 and state["line_counter_r52"] == 6


# -- the real firmware -----------------------------------------------------------------------------

# &8000: CALL &BD19 (MC WAIT FLYBACK) / LD HL,&9000 / INC (HL) / JR &8000
FLYBACK_LOOP = bytes.fromhex("CD19BD 210090 34 18F7")


@requires_roms
def test_wait_flyback_returns_after_a_bad_phase(tmp_path: Path) -> None:
    """MC WAIT FLYBACK returns in every frame even when INT was forced just before VSYNC.

    Without the resync (rule 2) INT at line 305 or 310 makes the loop miss VSYNC: its handler
    (about 2 ms) is still running when VSYNC ends, frame after frame.
    """
    session = Session(CPC464(CONFIG_464), project_dir=tmp_path)
    run_until_ready(session)
    machine = session.machine
    gate_array = machine.gate_array
    machine.memory.ram[0x8000 : 0x8000 + len(FLYBACK_LOOP)] = FLYBACK_LOOP
    machine.cpu.pc = 0x8000
    for bad_line in (270, 285, 300, 305, 310):
        while gate_array.frame_line != bad_line - 52:
            machine.step()
        gate_array.r52 = 0  # INT at `bad_line`, its handler runs into VSYNC
        frames_with_return = 0
        for _ in range(10):
            count = machine.memory.ram[0x9000]
            machine.run(FRAME_LINES * LINE_US)
            frames_with_return += machine.memory.ram[0x9000] != count
        assert frames_with_return == 10, bad_line
