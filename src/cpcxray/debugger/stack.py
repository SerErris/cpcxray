"""Where the current stack starts: the "stack top" of the CPU tab (task 5b, owner 2026-10-22).

The Z80 has no notion of a stack's extent: SP only says where the next push goes. To show "the
stack" the debugger needs its top - the address the stack grows down from. Programs set it by
loading SP (`ld sp,nn`, `ld sp,(nn)`, `ld sp,hl/ix/iy`; reset sets &FFFF), so the emulator
watches these instructions only (the Z80 `stack_loaded` hook: no cost for others) and keeps a
short list of tops, each with the instruction that set it.

A new SP value just below a known top (within its area, `STACK_AREA` bytes) is a return to that
stack - the firmware restoring SP after an interrupt, say - so the top and its source stay.
Otherwise the value starts a new stack and becomes its top. The view always names the
instruction, so this estimate is visible as one.
"""

from __future__ import annotations

from dataclasses import dataclass

STACK_AREA = 1024  # how far below its top a stack is expected to reach
KEPT_TOPS = 8  # tops remembered (the most recent first)


@dataclass(frozen=True)
class StackTop:
    """A stack's top and what set it.

    Attributes:
        top: The address the stack grows down from (the first push writes top - 1).
        address: Address of the instruction that set SP, or None (reset, debugger, snapshot).
        source: How SP was set, e.g. "ld sp at &0092 (lower)", "reset".
    """

    top: int
    address: int | None
    source: str


class StackTops:
    """The known stack tops, most recent first (see the module docstring)."""

    def __init__(self) -> None:
        """Start with the reset stack (&FFFF)."""
        self.tops: list[StackTop] = []
        self.reset()

    def reset(self) -> None:
        """Forget all stacks: after a reset SP is &FFFF."""
        self.tops = [StackTop(0xFFFF, None, "reset")]

    def loaded(self, value: int, address: int | None, source: str) -> StackTop:
        """SP was loaded with `value`: a return to a known stack, or a new top.

        Returns:
            The stack SP now belongs to.
        """
        known = self._containing(value)
        if known is not None:
            self.tops.remove(known)
            self.tops.insert(0, known)
            return known
        top = StackTop(value, address, source)
        self.tops = [top, *self.tops[: KEPT_TOPS - 1]]
        return top

    def current(self, sp: int) -> StackTop | None:
        """The stack `sp` lies in (a known top at or above it, within the area), or None."""
        return self._containing(sp)

    def _containing(self, value: int) -> StackTop | None:
        """The most recent known stack whose area holds `value`.

        The distance wraps around 16 bits: `ld sp,#0000` is a stack whose first push goes to
        &FFFF.
        """
        return next((t for t in self.tops if (t.top - value) & 0xFFFF <= STACK_AREA), None)
