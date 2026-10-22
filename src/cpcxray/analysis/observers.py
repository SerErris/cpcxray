"""Execution observer: instruction starts, RST operands and far address objects (§6.15; D-31).

A bus observer registered together with the provenance observer while analysis targets exist
(D-12). It watches the first M1 fetch of every instruction (fetch address = PC) and records, for
code that belongs to an analysis target:

* INSN_START in the target flags for code executed directly in a target ROM or RAM range
  (code executed from a RAM copy is mapped back by the provenance observer, D-21);
* for RST 1, 2, 3 and 5: the RST in the database observations ("rst": n); the classifier then
  tags the two inline bytes as RST_OPERAND. The bytes after an RST are always data reads of
  the firmware handler, never code (§6.15);
* for RST 3 (FAR CALL): the 3-byte far address object at the inline pointer. If it lies in a
  target it is recorded there ("far_object"); otherwise the target and ROM select read at run
  time are kept with the RST ("far_target", "far_select"), for the "(out of range)" comment;
* for RST 2 and 3: where the call arrived, i.e. the ROM paged in at the first instruction
  executed at the routine address within the next ARRIVAL_WINDOW instructions ("reached":
  "rom6", "lower" or "RAM"), for the "(observed: ROM 6)" comment.

RSTs in a RAM copy are mapped back to their ROM origin via the provenance tags, so the
observation lands on the ROM bytes the source generator emits.
"""

from __future__ import annotations

from dataclasses import dataclass

from cpcxray.analysis.database import TargetDatabase
from cpcxray.analysis.provenance import AnalysisObserver
from cpcxray.analysis.targets import INSN_START, AnalysisTarget
from cpcxray.cpu.bus import Cycle
from cpcxray.machine.memory import MemoryMap

RESTARTS_WITH_OPERAND = frozenset({1, 2, 3, 5})
ARRIVAL_WINDOW = 1000  # instructions after RST 2/3 within which the routine must start


@dataclass
class _PendingArrival:
    """An RST 2/3 whose routine has not started yet.

    Attributes:
        database: Database of the target that executed the RST.
        rst_offset: Offset of the RST opcode in the target.
        routine_address: CPU address where the called routine starts.
        instructions_left: Instructions to wait before giving up.
    """

    database: TargetDatabase
    rst_offset: int
    routine_address: int
    instructions_left: int = ARRIVAL_WINDOW


class ExecutionObserver:
    """Records instruction starts and RST facts for analysis targets (module docstring).

    Attributes:
        handlers: Only opcode fetches matter (the bus calls nothing else, D-12).
    """

    def __init__(
        self,
        memory: MemoryMap,
        targets: dict[str, AnalysisTarget],
        databases: dict[str, TargetDatabase],
        provenance: AnalysisObserver,
    ) -> None:
        """Observe on behalf of `targets`; facts go into `databases` (both shared, may change).

        Args:
            memory: The memory map (paging state, side-effect-free reads).
            targets: Analysis targets by name.
            databases: Analysis databases by target name.
            provenance: The provenance observer, to map RAM copies back to their origin.
        """
        self.memory = memory
        self.targets = targets
        self.databases = databases
        self.provenance = provenance
        self._pending: _PendingArrival | None = None
        self.handlers = {Cycle.M1: self._on_fetch}

    def on_cycle(self, kind: Cycle, address: int, value: int, pc: int) -> None:
        """Process one bus access; only instruction-start fetches matter."""
        if kind == Cycle.M1:
            self._on_fetch(address, value, pc)

    def _on_fetch(self, address: int, value: int, pc: int) -> None:
        """An M1 fetch: the first one of an instruction (address = PC) is recorded."""
        if address == pc:
            self._on_instruction(address, value)

    def locate(self, address: int) -> tuple[str, int] | None:
        """Map a CPU address to (target name, offset), now; None if outside every target.

        A paged-in target ROM wins; RAM is mapped through its provenance tag (a RAM copy of
        target code; value matches only inside a copied run) or into a RAM-range target. A ROM
        that builds a FAR CALL and its far address object in RAM with `ld (hl),n` writes values
        equal to its own operand bytes, which must not look like an RST and a far object.
        """
        rom = self.memory.visible_rom(address)
        if rom is not None:
            return (rom, address & 0x3FFF) if rom in self.targets else None
        tag = self.provenance.trusted_origin(address)  # not a mere value match
        if tag is not None and tag.target in self.targets:
            return tag.target, tag.offset
        for target in self.targets.values():
            if target.kind == "ram" and target.contains(address):
                return target.name, address - target.start
        return None

    def _on_instruction(self, address: int, opcode: int) -> None:
        """First M1 fetch of an instruction at `address`."""
        if self._pending is not None:
            self._check_arrival(address)
        location = self.locate(address)
        if location is None:
            return
        name, offset = location
        if self.memory.visible_rom(address) is not None or self.targets[name].kind == "ram":
            self.targets[name].flags[offset] |= INSN_START  # RAM copies: provenance observer
        if opcode & 0xC7 == 0xC7 and (opcode >> 3 & 7) in RESTARTS_WITH_OPERAND:
            database = self.databases.get(name)
            if database is not None:
                self._on_restart(database, offset, address, opcode >> 3 & 7)

    def _on_restart(self, database: TargetDatabase, offset: int, address: int, number: int) -> None:
        """Record an executed RST with inline operand (and its far address object)."""
        rst_address = database.start + offset
        database.observe(rst_address, "rst", number)
        word = self._peek_word(address + 1)
        if number == 2:
            self._pending = _PendingArrival(database, offset, 0xC000 | (word & 0x3FFF))
        elif number == 3:
            routine = self._peek_word(word)
            far = self.locate(word)
            if far is not None and far[0] in self.databases:
                far_database = self.databases[far[0]]
                far_database.observe(far_database.start + far[1], "far_object", True)
            else:
                database.observe(rst_address, "far_target", routine)
                database.observe(rst_address, "far_select", self.memory.peek((word + 2) & 0xFFFF))
            self._pending = _PendingArrival(database, offset, routine)

    def _check_arrival(self, address: int) -> None:
        """Record where a pending SIDE/FAR CALL arrived, or give up after the window."""
        pending = self._pending
        assert pending is not None
        if address == pending.routine_address:
            reached = self.memory.visible_rom(address) or "RAM"
            pending.database.observe(
                pending.database.start + pending.rst_offset, "reached", reached
            )
            self._pending = None
            return
        pending.instructions_left -= 1
        if pending.instructions_left <= 0:
            self._pending = None

    def _peek_word(self, address: int) -> int:
        """Read a little-endian word as data reads see it, without side effects."""
        low = self.memory.peek(address & 0xFFFF)
        return low | self.memory.peek((address + 1) & 0xFFFF) << 8
