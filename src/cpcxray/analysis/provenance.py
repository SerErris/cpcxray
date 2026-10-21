"""RAM provenance, relocated blocks and RAM-target capture (design.md §6.6; D-21, D-22, D-23).

`AnalysisObserver` is a bus observer, registered only while analysis targets exist (D-12).

Provenance (ROM targets, D-21): code that a ROM copies into RAM and runs there is never fetched
from the ROM with M1, so without tracking it would look like data. Every RAM byte gets a tag
`(target, offset, exact)` when it is written with a value read from a ROM target:

* exact: in a block move (LDI/LDD/LDIR/LDDR) the write follows the target read of the same
  instruction;
* probable: the written value equals one of the last few target reads (copies via registers);
* any other write clears the tag; if the tagged byte had not run yet, its origin is flagged
  MODIFIED_AFTER_COPY.

An M1 fetch from tagged RAM flags the origin byte EXEC_VIA_RAM, and INSN_START for the first
byte of an instruction, and counts executions per RAM address. A probable tag counts only inside
a copied run (a neighbouring RAM byte tagged with the consecutive offset of the same ROM): a
single value match is a coincidence too often - the firmware's FAR CALL writes a JP opcode &C3
into RAM right after reading a pointer byte &C3 from the ROM, which made a VDOS operand byte look
like executed code (found with |CPM, &C324).

Relocated blocks (D-22): consecutive RAM addresses whose tags point to consecutive offsets of
the same target form a block (target, rom_start, length, ram_start). The same ROM range copied
to several places gives several blocks; the one with the most executed instructions is the
preferred one for disassembly.

RAM targets (D-23): captured at their first M1 fetch (capture = "first_m1") or on command;
writes into the range afterwards are recorded with the runtime value.
"""

from __future__ import annotations

from array import array
from collections import deque
from dataclasses import dataclass

from cpcxray.analysis.targets import (
    EXEC_VIA_RAM,
    INSN_START,
    MODIFIED_AFTER_CAPTURE,
    MODIFIED_AFTER_COPY,
    AnalysisTarget,
)
from cpcxray.cpu.bus import Cycle
from cpcxray.machine.memory import MemoryMap

RECENT_READS = 4  # target reads remembered for "probable" tags
BLOCK_MOVE_OPCODES = frozenset({0xA0, 0xA8, 0xB0, 0xB8})  # after ED: LDI LDD LDIR LDDR
MIN_BLOCK_LENGTH = 2


@dataclass(frozen=True)
class RamTag:
    """Where a RAM byte came from.

    Attributes:
        target: Name of the ROM target, e.g. "rom5".
        offset: Offset of the origin byte in the ROM.
        exact: Copied by a block move (True) or matched by value (False).
    """

    target: str
    offset: int
    exact: bool

    def text(self) -> str:
        """The origin as shown in traces, e.g. "rom5:&0105" ("?" marks a probable tag)."""
        return f"{self.target}:&{self.offset:04X}{'' if self.exact else '?'}"


@dataclass(frozen=True)
class RelocatedBlock:
    """A run of ROM bytes copied to RAM (D-22).

    Attributes:
        target: ROM target name.
        rom_start: Offset of the first byte in the ROM.
        length: Number of bytes.
        ram_start: RAM address of the first copied byte.
        executed: Instructions executed inside the RAM copy so far.
        exact: All tags of the block came from block moves.
        manual: Added by the owner rather than detected.
    """

    target: str
    rom_start: int
    length: int
    ram_start: int
    executed: int = 0
    exact: bool = True
    manual: bool = False


class AnalysisObserver:  # pylint: disable=too-many-instance-attributes  # per-instruction state
    """Bus observer maintaining provenance, execution via RAM and RAM-target captures.

    Attributes:
        handlers: Called by the bus per access kind: memory accesses only; I/O and interrupt
            acknowledges are not observed (D-12).
    """

    def __init__(self, memory: MemoryMap, targets: dict[str, AnalysisTarget]) -> None:
        """Observe on behalf of `targets` (shared with the session, may change at run time)."""
        self.memory = memory
        self.targets = targets
        self.ram_origin: list[RamTag | None] = [None] * 0x10000
        self.ram_executed = array("I", bytes(4 * 0x10000))
        self._recent: deque[tuple[str, int, int]] = deque(maxlen=RECENT_READS)
        self._instruction_reads: list[tuple[str, int, int]] = []
        self._block_move = False
        self._instruction_pc = -1
        self._previous_m1 = -1
        self.handlers = {Cycle.M1: self._on_fetch, Cycle.MEM_RD: self._on_read,
                         Cycle.MEM_WR: self._on_write}  # fmt: skip

    # -- the observer callback -------------------------------------------------------------------

    def on_cycle(self, kind: Cycle, address: int, value: int, pc: int) -> None:
        """Process one bus access.

        The bus calls `handlers` directly; this serves callers that have the kind at hand.
        """
        if kind == Cycle.M1:
            self._on_fetch(address, value, pc)
        elif kind == Cycle.MEM_RD:
            self._on_read(address, value)
        elif kind == Cycle.MEM_WR:
            self._on_write(address, value)

    def _on_fetch(self, address: int, value: int, pc: int) -> None:
        """M1 fetch: track instruction boundaries, block moves, execution from tagged RAM."""
        if address == pc:  # first opcode byte of a new instruction
            self._instruction_pc = pc
            self._instruction_reads.clear()
            self._block_move = False
        elif self._previous_m1 == 0xED and address == pc + 1:
            self._block_move = value in BLOCK_MOVE_OPCODES
        self._previous_m1 = value
        if self.memory.visible_rom(address) is None:
            self._on_ram_fetch(address, address == pc)

    def _on_ram_fetch(self, address: int, instruction_start: bool) -> None:
        """M1 from RAM: provenance of tagged bytes, first-M1 capture of RAM targets."""
        tag = self.trusted_origin(address)
        if tag is not None:
            target = self.targets.get(tag.target)
            if target is not None:
                target.flags[tag.offset] |= EXEC_VIA_RAM | (INSN_START if instruction_start else 0)
            if instruction_start:
                self.ram_executed[address] += 1
        for target in self.targets.values():
            if (target.kind == "ram" and target.image is None and target.capture == "first_m1"
                    and target.contains(address)):  # fmt: skip
                capture(target, self.memory)

    def trusted_origin(self, address: int) -> RamTag | None:
        """Return the tag of a RAM byte if it counts as a copy: exact, or inside a copied run.

        A single value match is no evidence (module docstring): code that builds bytes with
        `ld (hl),n` writes values that equal its own operand bytes.
        """
        tag = self.ram_origin[address]
        if tag is not None and (tag.exact or self._in_copied_run(address, tag)):
            return tag
        return None

    def _in_copied_run(self, address: int, tag: RamTag) -> bool:
        """True if a neighbouring RAM byte carries the neighbouring offset of the same target."""
        before = self.ram_origin[(address - 1) & 0xFFFF]
        after = self.ram_origin[(address + 1) & 0xFFFF]
        previous_byte = (tag.target, tag.offset - 1)
        next_byte = (tag.target, tag.offset + 1)
        return (before is not None and (before.target, before.offset) == previous_byte) or (
            after is not None and (after.target, after.offset) == next_byte
        )

    def _on_read(self, address: int, value: int, _pc: int = 0) -> None:
        """Data read: remember reads from ROM targets."""
        rom = self.memory.visible_rom(address)
        if rom is not None and rom in self.targets:
            entry = (rom, address & 0x3FFF, value)
            self._instruction_reads.append(entry)
            self._recent.append(entry)

    def _on_write(self, address: int, value: int, _pc: int = 0) -> None:
        """RAM write: tag, retag or clear provenance; record writes into captured targets."""
        tag = None
        if self._block_move and self._instruction_reads:
            target, offset, read_value = self._instruction_reads[-1]
            if read_value == value:
                tag = RamTag(target, offset, True)
        if tag is None:
            for target, offset, read_value in reversed(self._recent):
                if read_value == value:
                    tag = RamTag(target, offset, False)
                    break
        old = self.ram_origin[address]
        if tag is None and old is not None and self.ram_executed[address] == 0:
            origin = self.targets.get(old.target)
            if origin is not None:
                origin.flags[old.offset] |= MODIFIED_AFTER_COPY
        self.ram_origin[address] = tag
        for target in self.targets.values():
            if target.kind == "ram" and target.image is not None and target.contains(address):
                target.modifications[address] = value
                target.flags[address - target.start] |= MODIFIED_AFTER_CAPTURE

    # -- relocated blocks ------------------------------------------------------------------------

    def relocated_blocks(self) -> list[RelocatedBlock]:
        """Detect runs of RAM bytes copied from consecutive ROM offsets (D-22)."""
        blocks = []
        address = 0
        while address < 0x10000:
            tag = self.ram_origin[address]
            if tag is None:
                address += 1
                continue
            start, exact, executed = address, tag.exact, 0
            while address < 0x10000:
                current = self.ram_origin[address]
                expected_offset = tag.offset + (address - start)
                if (
                    current is None
                    or current.target != tag.target
                    or current.offset != expected_offset
                ):
                    break
                exact = exact and current.exact
                executed += self.ram_executed[address]
                address += 1
            if address - start >= MIN_BLOCK_LENGTH:
                blocks.append(
                    RelocatedBlock(tag.target, tag.offset, address - start, start, executed, exact)
                )
        return blocks

    def origin_text(self, address: int) -> str | None:
        """Describe the provenance of a RAM byte, e.g. "rom5:&1234" (None if untagged)."""
        tag = self.ram_origin[address]
        return tag.text() if tag is not None else None


def capture(target: AnalysisTarget, memory: MemoryMap) -> None:
    """Capture a RAM target's image now (D-23); later writes are recorded as modifications."""
    target.image = bytes(memory.ram[target.start : target.start + target.length])
    target.modifications.clear()
    for index in range(target.length):
        target.flags[index] &= ~MODIFIED_AFTER_CAPTURE & 0xFF
