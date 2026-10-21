"""Tests of analysis targets, RAM provenance, relocated blocks and RAM targets (design.md §8)."""

from __future__ import annotations

from cpcxray.analysis.provenance import AnalysisObserver
from cpcxray.analysis.targets import (
    EXEC_VIA_RAM,
    INSN_START,
    MODIFIED_AFTER_CAPTURE,
    ram_target,
    rom_target,
)
from cpcxray.cpu.bus import Cycle
from cpcxray.machine.cpc464 import CPC464
from tests.analysis.helpers import make_test_cpc, run_until_halt

# OS ROM: upper ROM on, JP &C000.  Upper ROM 3 at &C000:
#   LD HL,&C100 / LD DE,&4000 / LD BC,4 / LDIR      exact copy of 4 bytes to &4000
#   LD A,(&C104) / LD (&5000),A                     copy via a register (probable)
#   CALL &4000 / HALT                               run the RAM copy
# &C100: INC A / INC A / RET / NOP;  &C104: &77
OS = {0x0000: "01817F ED49 C300C0"}
UPPER = {
    0x0000: "2100C1 110040 010400 EDB0 3A04C1 320050 CD0040 76",
    0x0100: "3C 3C C9 00 77",
}


def make_machine() -> tuple[CPC464, AnalysisObserver, dict]:
    """A CPC 464 with the test ROMs and an observer for the target "rom3"."""
    machine = make_test_cpc(OS, UPPER)
    targets = {"rom3": rom_target("rom3")}
    observer = AnalysisObserver(machine.memory, targets)
    machine.bus.add_observer(observer)
    return machine, observer, targets


def test_block_move_gives_exact_tags_and_register_copy_probable() -> None:
    """LDIR copies are tagged exact; a copy through A is tagged probable (D-21)."""
    machine, observer, _ = make_machine()
    run_until_halt(machine)
    assert [observer.origin_text(a) for a in range(0x4000, 0x4004)] == [
        "rom3:&0100",
        "rom3:&0101",
        "rom3:&0102",
        "rom3:&0103",
    ]
    assert observer.origin_text(0x5000) == "rom3:&0104?"  # "?": probable


def test_execution_via_ram_marks_origin() -> None:
    """Fetching the copied code marks the ROM bytes as executed via RAM and instruction starts."""
    machine, _, targets = make_machine()
    run_until_halt(machine)
    flags = targets["rom3"].flags
    assert all(
        flags[offset] & EXEC_VIA_RAM and flags[offset] & INSN_START
        for offset in (0x100, 0x101, 0x102)
    )
    assert not flags[0x103] & EXEC_VIA_RAM  # copied but never run


def test_relocated_block_detected() -> None:
    """The 4 copied bytes form one block with 3 executed instructions (D-22)."""
    machine, observer, _ = make_machine()
    run_until_halt(machine)
    blocks = [b for b in observer.relocated_blocks() if b.ram_start == 0x4000]
    assert len(blocks) == 1
    block = blocks[0]
    assert (block.target, block.rom_start, block.length, block.executed, block.exact) == (
        "rom3",
        0x100,
        4,
        3,
        True,
    )


def test_ram_target_capture_and_modification() -> None:
    """A RAM target is captured at its first M1 fetch; later writes are recorded (D-23)."""
    machine, observer, targets = make_machine()
    targets["ram:copy"] = target = ram_target("copy", 0x4000, 4)
    run_until_halt(machine)
    assert target.image == bytes.fromhex("3C 3C C9 00")
    machine.memory.write(0x4001, 0x00)  # a plain RAM write after the capture
    observer.on_cycle(Cycle.MEM_WR, 0x4001, 0, 0)  # as the bus reports it
    assert target.modifications == {0x4001: 0}
    assert target.flags[1] & MODIFIED_AFTER_CAPTURE


def test_only_configured_targets_are_analysed() -> None:
    """Without targets nothing is tagged (FR-ANA-01): the observer is only for targets."""
    machine, observer, targets = make_machine()
    targets.clear()
    run_until_halt(machine)
    assert observer.origin_text(0x4000) is None


def test_single_value_match_is_not_executed_code() -> None:
    """A RAM byte that only equals a recent ROM read (not a copied run) is no executed ROM code.

    Like the firmware's FAR CALL: it reads &C3 from the ROM, then writes the constant &C3 (a JP
    opcode) into RAM and executes it - that must not mark the ROM byte (found with |CPM).
    """
    machine = make_test_cpc(
        {0x0000: "01817F ED49 C300C0"},
        {0x0000: "3A20C0 210040 36C9 CD0040 76", 0x0020: "C9"},  # read &C9, write #C9, call it
    )
    targets = {"rom3": rom_target("rom3")}
    observer = AnalysisObserver(machine.memory, targets)
    machine.bus.add_observer(observer)
    run_until_halt(machine)
    origin = observer.origin_text(0x4000)  # tagged (probable: the immediate &C9 at &0007) ...
    assert origin is not None and origin.endswith("?")
    flags = targets["rom3"].flags
    assert not flags[0x07] & EXEC_VIA_RAM and not flags[0x20] & EXEC_VIA_RAM  # ... not executed
