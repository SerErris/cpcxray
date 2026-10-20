"""Tests of the execution observer: instruction starts, RST operands, far objects (§6.15)."""

from __future__ import annotations

from cpcxray.analysis.database import TargetDatabase
from cpcxray.analysis.observers import ExecutionObserver
from cpcxray.analysis.provenance import AnalysisObserver
from cpcxray.analysis.targets import INSN_START, AnalysisTarget, rom_target
from cpcxray.machine.cpc464 import CPC464
from tests.analysis.helpers import make_test_cpc, run_until_halt

# OS ROM: enable the upper ROM, JP &C000. RST 2 and RST 3 "handlers" jump straight to the
# routines (the real firmware reads the inline operand; enough to test the observer).
OS = {0x0000: "01817F ED49 C300C0", 0x0010: "C350C0", 0x0018: "C370C0"}
# ROM 3: &C000 rst #10 (SIDE CALL to &C050, slot 0); &C050 rst #18 (FAR CALL via &C060);
# &C060 far address object (&C070, ROM 3); &C070 halt
UPPER = {0x00: "D75000", 0x50: "DF60C0", 0x60: "70C003", 0x70: "76"}


def make_cpc() -> CPC464:
    """A CPC 464 with the test ROMs, upper ROM 3 selected."""
    return make_test_cpc(OS, UPPER)


def observe() -> tuple[dict[str, AnalysisTarget], dict[str, TargetDatabase]]:
    """Run the test program with both observers on target rom3."""
    machine = make_cpc()
    targets = {"rom3": rom_target("rom3")}
    databases = {"rom3": TargetDatabase("rom3", 0xC000, 0x4000)}
    provenance = AnalysisObserver(machine.memory, targets)
    machine.bus.add_observer(provenance)
    machine.bus.add_observer(ExecutionObserver(machine.memory, targets, databases, provenance))
    run_until_halt(machine)
    return targets, databases


def test_instruction_starts_in_rom() -> None:
    """Instructions executed directly in the target ROM are flagged as instruction starts."""
    targets, _ = observe()
    flags = targets["rom3"].flags
    assert all(flags[offset] & INSN_START for offset in (0x00, 0x50, 0x70))
    assert not flags[0x01] & INSN_START  # inline operand, never an instruction


def test_restarts_far_object_and_arrival() -> None:
    """RSTs are recorded with the ROM where the call arrived; the far object is marked."""
    _, databases = observe()
    observations = databases["rom3"].observations
    assert observations["C000"] == {"rst": 2, "reached": "rom3"}
    assert observations["C050"] == {"rst": 3, "reached": "rom3"}
    assert observations["C060"] == {"far_object": True}
