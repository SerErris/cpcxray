"""The step access observer of the Trace tab (debugger/step_accesses.py, task 5e)."""

from __future__ import annotations

from cpcxray.cpu.bus import Cycle
from cpcxray.debugger.step_accesses import KEPT_INSTRUCTIONS, StepAccesses


def test_accesses_filed_per_instruction() -> None:
    """Reads and writes go to the instruction begun last; nothing before begin / after end."""
    observer = StepAccesses()
    read, write = observer.handlers[Cycle.MEM_RD], observer.handlers[Cycle.MEM_WR]
    read(0x1000, 1, 0)  # no instruction yet
    observer.begin(7)
    read(0xB733, 0, 0x140B)
    write(0xBFE0, 0xE6, 0x140B)
    observer.end()
    write(0x2000, 2, 0)
    assert observer.by_sequence == {7: [("read", 0xB733, 0), ("write", 0xBFE0, 0xE6)]}
    assert Cycle.M1 not in observer.handlers  # opcode fetches are not accesses
    for sequence in range(8, 8 + KEPT_INSTRUCTIONS):
        observer.begin(sequence)
    assert len(observer.by_sequence) == KEPT_INSTRUCTIONS and 7 not in observer.by_sequence
