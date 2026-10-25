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
into RAM right after reading a pointer byte &C3 from the ROM, which made an operand byte of the
calling ROM look like executed code.

Relocated blocks (D-22): consecutive RAM addresses whose tags point to consecutive offsets of
the same target form a block (target, rom_start, length, ram_start). The same ROM range copied
to several places gives several blocks; the one with the most executed instructions is the
preferred one for disassembly. A copy is remembered after it is overwritten: before a trusted
tag is replaced by another origin (or cleared), the byte goes into a history per copy - the
target and the distance RAM address - ROM offset, which all bytes of one copy share - so a copy
that one program made (CP/M's jumpblocks at &BE80) stays a block when another program (BASIC's
at &BE8F) later uses the same RAM. The RAM byte's execution count starts again for the new
origin. `merge_copies` joins the pieces of one copy (detected now, saved earlier).

RAM targets (D-23): captured at their first M1 fetch (capture = "first_m1") or on command;
writes into the range afterwards are recorded with the runtime value.
"""

from __future__ import annotations

from array import array
from collections import deque
from dataclasses import dataclass, replace

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
        # overwritten copies: (target, RAM address - ROM offset) → RAM address → (exact, executed)
        self.copy_history: dict[tuple[str, int], dict[int, tuple[bool, int]]] = {}
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
        new_origin = None if tag is None else (tag.target, tag.offset)
        if old is not None and new_origin != (old.target, old.offset):  # another copy or none
            self._remember_overwritten(address, old)
        self.ram_origin[address] = tag
        for target in self.targets.values():
            if target.kind == "ram" and target.image is not None and target.contains(address):
                target.modifications[address] = value
                target.flags[address - target.start] |= MODIFIED_AFTER_CAPTURE

    # -- relocated blocks ------------------------------------------------------------------------

    def _remember_overwritten(self, address: int, old: RamTag) -> None:
        """Keep a copied byte that gets another origin in the copy history (D-22).

        Only trusted tags count (exact, or inside a copied run): a lone value match is no copy.
        The byte's execution count goes with it; the RAM address starts counting again.
        """
        if self.trusted_origin(address) is None:
            return
        copy = self.copy_history.setdefault((old.target, address - old.offset), {})
        exact, executed = copy.get(address, (True, 0))
        copy[address] = (exact and old.exact, executed + self.ram_executed[address])
        self.ram_executed[address] = 0

    def relocated_blocks(self) -> list[RelocatedBlock]:
        """Runs of RAM bytes copied from consecutive ROM offsets, now or earlier (D-22).

        The bytes of a copy - the current tags and the copy history - share target and the
        distance RAM address - ROM offset; each run of consecutive addresses is a block.
        """
        copies = {key: dict(history) for key, history in self.copy_history.items()}
        for address, tag in enumerate(self.ram_origin):
            if tag is not None:
                copy = copies.setdefault((tag.target, address - tag.offset), {})
                exact, executed = copy.get(address, (True, 0))
                copy[address] = (exact and tag.exact, executed + self.ram_executed[address])
        blocks = []
        for (target, distance), copy in copies.items():
            addresses = sorted(copy)
            start = 0
            for index in range(1, len(addresses) + 1):
                if index < len(addresses) and addresses[index] == addresses[index - 1] + 1:
                    continue
                run = addresses[start:index]
                if len(run) >= MIN_BLOCK_LENGTH:
                    blocks.append(RelocatedBlock(
                        target, run[0] - distance, len(run), run[0],
                        sum(copy[a][1] for a in run), all(copy[a][0] for a in run),
                    ))  # fmt: skip
                start = index
        return sorted(blocks, key=lambda b: (b.ram_start, b.rom_start))

    def origin_text(self, address: int) -> str | None:
        """Describe the provenance of a RAM byte, e.g. "rom5:&1234" (None if untagged)."""
        tag = self.ram_origin[address]
        return tag.text() if tag is not None else None


def merge_copies(blocks: list[RelocatedBlock]) -> list[RelocatedBlock]:
    """Join the pieces of each copy: blocks of the same target and RAM - ROM distance (D-22).

    A block inside another one of the same copy disappears (a fragment left in RAM after part
    of a copy was overwritten must not hide the full copy saved earlier); overlapping or
    adjacent ones become one block with the higher execution count.
    """
    by_copy: dict[tuple[str, int], list[RelocatedBlock]] = {}
    for block in blocks:
        by_copy.setdefault((block.target, block.ram_start - block.rom_start), []).append(block)
    merged = []
    for pieces in by_copy.values():
        pieces.sort(key=lambda b: (b.ram_start, -b.length))
        current = pieces[0]
        for piece in pieces[1:]:
            end = current.ram_start + current.length
            if piece.ram_start > end:
                merged.append(current)
                current = piece
                continue
            inside = piece.ram_start + piece.length <= end  # a fragment: only its count counts
            current = replace(
                current,
                length=max(end, piece.ram_start + piece.length) - current.ram_start,
                executed=max(current.executed, piece.executed),
                exact=current.exact and (inside or piece.exact),
            )
        merged.append(current)
    return sorted(merged, key=lambda b: (b.ram_start, b.rom_start))


def capture(target: AnalysisTarget, memory: MemoryMap) -> None:
    """Capture a RAM target's image now (D-23); later writes are recorded as modifications."""
    target.image = bytes(memory.ram[target.start : target.start + target.length])
    target.modifications.clear()
    for index in range(target.length):
        target.flags[index] &= ~MODIFIED_AFTER_CAPTURE & 0xFF
