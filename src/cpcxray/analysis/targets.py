"""Analysis targets: what is analysed (design.md §6.6; D-16, D-17, D-23; FR-ANA-01..04).

Nothing is analysed unless the owner says so (FR-ANA-01): a target is a ROM slot ("lower",
"rom0" ... "rom15") or a RAM range, marked in the configuration (`analyse = true`,
`[[analysis.ram]]`) or added at run time. Several targets may be active.

Per target the analysis keeps one flag byte per byte of the target (`flags`):

* EXEC_VIA_RAM: the byte was copied to RAM and executed there (D-21);
* INSN_START: an instruction started at this byte (observed in RAM, mapped back);
* MODIFIED_AFTER_COPY: its RAM copy was overwritten before it ran (D-22);
* MODIFIED_AFTER_CAPTURE: for RAM targets, written after the image was captured (D-23).

RAM-range targets are analysed on an image captured on command or at the first M1 fetch inside
the range; later writes into the range are recorded with their runtime value (D-23).
Scrambled ROM targets additionally have the shadow ROM and the access map of their slot (D-17).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

EXEC_VIA_RAM = 0x01
INSN_START = 0x02
MODIFIED_AFTER_COPY = 0x04
MODIFIED_AFTER_CAPTURE = 0x08
FLAG_NAMES = {
    EXEC_VIA_RAM: "executed via RAM",
    INSN_START: "instruction start",
    MODIFIED_AFTER_COPY: "modified after copy",
    MODIFIED_AFTER_CAPTURE: "modified after capture",
}
ROM_SIZE = 0x4000


@dataclass
class AnalysisTarget:  # pylint: disable=too-many-instance-attributes  # ROM and RAM variants
    """One analysis target and its per-byte state.

    Attributes:
        name: "lower", "romN" for ROM slots; "ram:<name>" for RAM ranges.
        kind: "rom" or "ram".
        start: CPU address of the first byte (&0000 lower ROM, &C000 upper ROM, range start).
        length: Number of bytes.
        capture: RAM targets: "first_m1" or "manual".
        flags: Flag bits per byte (see the module docstring).
        image: RAM targets: the captured bytes (None until captured).
        modifications: RAM targets: address → value written after the capture.
    """

    name: str
    kind: Literal["rom", "ram"]
    start: int
    length: int
    capture: str = "manual"
    flags: bytearray = field(default_factory=bytearray)
    image: bytes | None = None
    modifications: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Allocate the flag bytes."""
        if not self.flags:
            self.flags = bytearray(self.length)

    def contains(self, address: int) -> bool:
        """True if the CPU address lies inside a RAM target's range."""
        return self.start <= address < self.start + self.length

    def flag_counts(self) -> dict[str, int]:
        """Return how many bytes carry each flag."""
        return {
            name: sum(1 for value in self.flags if value & bit) for bit, name in FLAG_NAMES.items()
        }


def rom_target(slot: str) -> AnalysisTarget:
    """Create a target for "lower" or an upper ROM slot "romN"."""
    start = 0x0000 if slot == "lower" else 0xC000
    return AnalysisTarget(slot, "rom", start, ROM_SIZE)


def ram_target(name: str, start: int, length: int, capture: str = "first_m1") -> AnalysisTarget:
    """Create a RAM-range target (D-23)."""
    return AnalysisTarget(f"ram:{name}", "ram", start, length, capture)
