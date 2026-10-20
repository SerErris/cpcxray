"""Tests of the GUI emulation loop without a window (D-13, D-29)."""

from __future__ import annotations

import time

from cpcxray.machine.cpc464 import CPC464
from cpcxray.machine.memory import RomSlot
from cpcxray.session.commands import CommandQueue
from cpcxray.session.session import Session
from cpcxray.ui.emulation import EmulationLoop


def make_loop(code: bytes) -> EmulationLoop:
    """A loop around a CPC 464 whose OS ROM holds `code` at &0000."""
    machine = CPC464()
    rom = bytearray(0x4000)
    rom[: len(code)] = code
    machine.memory.set_lower_rom(RomSlot(bytes(rom), "test-os"))
    machine.reset()
    return EmulationLoop(Session(machine), CommandQueue())


def test_real_time_pacing() -> None:
    """In real-time mode the emulated time follows the wall clock."""
    loop = make_loop(bytes.fromhex("18FE"))  # JR $
    loop.start()
    started = time.perf_counter()
    while time.perf_counter() - started < 0.5:
        loop.frame()
        time.sleep(0.02)
    wall_us = (time.perf_counter() - started) * 1e6
    assert 0.8 * wall_us < loop.session.machine.clock_us <= 1.05 * wall_us


def test_breakpoint_stops_free_run() -> None:
    """A breakpoint ends the free run and is reported in the status line."""
    loop = make_loop(bytes.fromhex("00 00 00 18FB"))  # NOPs, JR back to &0000
    loop.session.breakpoint_add("0002")
    loop.start()
    for _ in range(5):
        loop.frame()
        time.sleep(0.01)
    assert not loop.running
    assert "stopped: breakpoint at 0002" in loop.status_line()


def test_queued_commands_run_between_frames() -> None:
    """Commands from other threads run in frame(); pause stops the free run."""
    loop = make_loop(bytes.fromhex("18FE"))
    loop.start()
    future = loop.commands.submit(lambda: loop.session.registers_get()["pc"])
    loop.frame()
    assert future.result(timeout=1) == 0
    loop.session.pause()  # as the MCP server does, from another thread
    loop.frame()
    assert not loop.running


def test_max_speed_runs_faster_than_real_time() -> None:
    """Max speed emulates more than a frame per frame on CPython (~4x)."""
    loop = make_loop(bytes.fromhex("18FE"))
    loop.max_speed = True
    loop.start()
    loop.frame()
    loop.frame()
    assert loop.session.machine.clock_us > 2 * 19968


def test_step_and_reset() -> None:
    """Step stops a free run; reset returns to &0000."""
    loop = make_loop(bytes.fromhex("00 00 18FC"))
    loop.start()
    assert loop.step()["pc"] == "0001"
    assert not loop.running
    assert loop.reset()["pc"] == "0000"
