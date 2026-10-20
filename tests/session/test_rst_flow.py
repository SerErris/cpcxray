"""RST control flow with the CPC profile, end to end through `Session` (design.md §6.15, D-30).

Which RSTs return to the instruction after their inline bytes (call-like) and which never come
back (jump-like) decides how stepping works now and how recursive disassembly follows code
later (phases 7/8). The test machine is the flat Z80 with the CPC profile and minimal RST
handlers in RAM that behave like the firmware ones:

* call-like (RST 2, 3): skip the inline word on return (`EX (SP),HL / INC HL / INC HL /
  EX (SP),HL / RET`), as the firmware's SIDE CALL and FAR CALL do;
* jump-like (RST 1, 5): never return to the RST; the routine's RET goes to our caller.
"""

from __future__ import annotations

import pytest

from cpcxray.analysis.cpc_profile import CpcProfile
from cpcxray.cpu.disasm import DisassemblyProfile, Flow
from cpcxray.machine.flat import FlatMachine
from cpcxray.session.session import Session

SKIP_INLINE_WORD_AND_RETURN = bytes.fromhex("E3 23 23 E3 C9")  # EX (SP),HL; INC HL x2; ...; RET


class CpcProfileMachine(FlatMachine):
    """The flat machine, but disassembling with the CPC RST profile."""

    def __init__(self, profile: CpcProfile | None = None) -> None:
        """Create the machine with the given (or default) CPC profile."""
        super().__init__()
        self.profile = profile or CpcProfile()

    def disassembly_profile(self) -> DisassemblyProfile | None:
        """Return the CPC profile."""
        return self.profile


def make_session(code: bytes, profile: CpcProfile | None = None) -> Session:
    """Session with `code` at &8000, PC there, SP at &C000, handlers at &0010 and &0018."""
    session = Session(CpcProfileMachine(profile))
    for address in (0x0010, 0x0018):  # SIDE CALL, FAR CALL handlers
        for offset, value in enumerate(SKIP_INLINE_WORD_AND_RETURN):
            session.machine.poke(address + offset, value)
    for offset, value in enumerate(code):
        session.machine.poke(0x8000 + offset, value)
    session.registers_set({"pc": 0x8000, "sp": 0xC000})
    return session


@pytest.mark.parametrize("opcode", [0xD7, 0xDF])  # RST 2 SIDE CALL, RST 3 FAR CALL
def test_call_like_restart_steps_over_inline_bytes(opcode: int) -> None:
    """step_over a call-like RST stops after its inline word, not after the opcode."""
    session = make_session(bytes([opcode, 0x34, 0x12, 0x00]))
    result = session.step_over()
    assert (result["reason"], result["pc"]) == ("address_reached", "8003")


@pytest.mark.parametrize(("opcode", "target"), [(0xCF, "0008"), (0xEF, "0028"), (0xC7, "0000")])
def test_jump_like_restart_is_single_stepped(opcode: int, target: str) -> None:
    """step_over a jump-like or non-returning RST executes just the RST (into its handler)."""
    session = make_session(bytes([opcode, 0x34, 0x12, 0x00]))
    result = session.step_over()
    assert (result["reason"], result["pc"]) == ("steps_done", target)


def test_listing_continues_after_inline_bytes() -> None:
    """The listing decodes the instruction after an RST's inline word as the next one."""
    session = make_session(bytes.fromhex("CF 50 81 DF F3 C7 3E 05"))
    lines = [" ".join(line.split()) for line in session.disassemble("8000", 3)["lines"]]
    assert lines[0] == "8000 CF 50 81 rst #08 ; LOW JUMP"
    assert lines[1] == "defw 08150h ; to #0150 [lower ROM on, upper ROM off]"
    assert lines[2] == "8003 DF F3 C7 rst #18 ; FAR CALL"
    assert lines[4] == "8006 3E 05 ld a,#05"


def test_flow_table() -> None:
    """Every RST's flow as the static classifier will use it (§6.15)."""
    session = make_session(bytes(8))
    expected = {
        0: Flow.NO_RETURN, 1: Flow.JUMP, 2: Flow.CALL, 3: Flow.CALL,
        4: Flow.SEQUENTIAL, 5: Flow.JUMP, 6: Flow.SEQUENTIAL, 7: Flow.NO_RETURN,
    }  # fmt: skip
    for number, flow in expected.items():
        session.machine.poke(0x8000, 0xC7 | number << 3)
        assert session._disassemble(0x8000).flow == flow, number  # pylint: disable=protected-access


def test_user_restart_is_configurable() -> None:
    """RST 6 takes the configured inline length and flow (its handler is user code)."""
    profile = CpcProfile(user_restart_inline_length=1, user_restart_flow=Flow.CALL)
    session = make_session(bytes.fromhex("F7 99 00"), profile)
    instruction = session._disassemble(0x8000)  # pylint: disable=protected-access
    assert (instruction.length, instruction.flow) == (2, Flow.CALL)
