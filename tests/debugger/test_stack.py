"""The stack top rule of the CPU tab (debugger/stack.py, task 5b)."""

from __future__ import annotations

from cpcxray.debugger.stack import KEPT_TOPS, STACK_AREA, StackTops


def test_new_top_and_return_to_a_known_stack() -> None:
    """A load far from known tops starts a stack; one just below a known top returns to it."""
    stacks = StackTops()
    assert stacks.current(0xFFF0) is not None and stacks.current(0xFFF0).source == "reset"
    firmware = stacks.loaded(0xC000, 0x064C, "ld sp at &064C (lower)")
    assert stacks.current(0xBFE6) == firmware
    interrupt = stacks.loaded(0xB100, 0x010E, "ld sp at &010E (lower)")
    assert stacks.current(0xB0F0) == interrupt
    back = stacks.loaded(0xBFE6, 0x0130, "ld sp at &0130 (lower)")  # SP restored
    assert back == firmware and stacks.tops[0] == firmware  # the top and its source stay
    assert stacks.current(0xC000 - STACK_AREA - 2) is None  # deeper than a stack reaches


def test_wrap_and_limit() -> None:
    """`ld sp,#0000` is a stack below &10000; only the most recent tops are kept."""
    stacks = StackTops()
    stacks.reset()
    zero = stacks.loaded(0x0000, 0x0100, "ld sp at &0100 (RAM)")
    assert stacks.current(0xFFFE) == zero
    for number in range(KEPT_TOPS + 3):
        stacks.loaded(0x2000 + number * 0x1000, number, f"load {number}")
    assert len(stacks.tops) == KEPT_TOPS
    stacks.reset()
    assert [t.source for t in stacks.tops] == ["reset"]
