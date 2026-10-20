"""Z80 behaviour not covered by the per-opcode JSON tests.

Interrupts, NMI, HALT, EI delay, M1 signalling, timing (D-04) and the debugger register
interface.
"""

from __future__ import annotations

import pytest

from cpcxray.cpu import timing
from cpcxray.cpu.bus import Cycle, FlatBus
from cpcxray.cpu.z80 import REGISTER_NAMES, Z80


def make(code: bytes, org: int = 0, log: bool = True) -> tuple[Z80, FlatBus]:
    """Create a CPU on a logging FlatBus with `code` at `org`; PC = org, SP = &8000."""
    bus = FlatBus(log=log)
    bus.mem[org : org + len(code)] = code
    cpu = Z80(bus)
    cpu.pc = org
    cpu.sp = 0x8000
    return cpu, bus


def kinds(bus: FlatBus) -> list[tuple[Cycle, int]]:
    """Return the logged accesses as (kind, address) pairs, without values."""
    assert bus.log is not None
    return [(k, a) for k, a, _ in bus.log]


# -- M1 signalling (FR-CPU-04, §5.5) ---------------------------------------------------------


def test_index_cb_displacement_and_opcode_are_not_m1() -> None:
    """DD CB d op: only DD and CB are M1 fetches; d and op are plain reads (§5.5)."""
    cpu, bus = make(bytes([0xDD, 0xCB, 0x05, 0xC6]))  # SET 0,(IX+5)
    cpu.ix = 0x4000
    cpu.step()
    assert kinds(bus) == [
        (Cycle.M1, 0),
        (Cycle.M1, 1),
        (Cycle.MEM_RD, 2),
        (Cycle.MEM_RD, 3),
        (Cycle.MEM_RD, 0x4005),
        (Cycle.MEM_WR, 0x4005),
    ]
    assert bus.mem[0x4005] == 0x01
    assert cpu.r == 2


def test_prefix_and_opcode_after_prefix_are_m1() -> None:
    """ED and FD prefixes and the opcodes after them are M1; operands are not."""
    cpu, bus = make(bytes([0xED, 0x44, 0xFD, 0x21, 0x34, 0x12]))  # NEG; LD IY,&1234
    cpu.step()
    cpu.step()
    assert kinds(bus) == [
        (Cycle.M1, 0),
        (Cycle.M1, 1),
        (Cycle.M1, 2),
        (Cycle.M1, 3),
        (Cycle.MEM_RD, 4),
        (Cycle.MEM_RD, 5),
    ]
    assert cpu.iy == 0x1234
    assert cpu.r == 4


def test_rst_inline_bytes_are_not_touched_by_cpu() -> None:
    """RST reads nothing after its opcode; the inline bytes are left to the firmware."""
    cpu, bus = make(bytes([0xCF, 0x50, 0x81]))  # RST 1 + inline word
    cpu.step()
    assert kinds(bus) == [(Cycle.M1, 0), (Cycle.MEM_WR, 0x7FFF), (Cycle.MEM_WR, 0x7FFE)]
    assert cpu.pc == 0x08


def test_r_register_keeps_bit7() -> None:
    """R counts in bits 0-6 and keeps bit 7."""
    cpu, _ = make(bytes(300))
    cpu.r = 0xFF
    cpu.step()
    assert cpu.r == 0x80


# -- interrupts (FR-CPU-05) ----------------------------------------------------------------------


def test_im1_interrupt() -> None:
    """IM 1: no interrupt right after EI, then a call to &0038 with IFF1/IFF2 cleared."""
    cpu, bus = make(bytes([0xFB, 0x00, 0x00]))  # EI; NOP; NOP
    cpu.im = 1
    cpu.int_line = True
    cpu.step()  # EI
    assert cpu.iff1 and cpu.ei_delay
    cpu.step()  # no interrupt directly after EI
    assert cpu.pc == 2
    assert cpu.step() == timing.INT_IM1
    assert cpu.pc == 0x38
    assert cpu.wz == 0x38
    assert not cpu.iff1 and not cpu.iff2
    assert (bus.mem[0x7FFF], bus.mem[0x7FFE]) == (0x00, 0x02)
    assert (Cycle.INT_ACK, 0) in kinds(bus)


def test_interrupt_not_accepted_when_disabled() -> None:
    """INT is ignored while IFF1 is clear."""
    cpu, _ = make(bytes(4))
    cpu.im = 1
    cpu.int_line = True
    cpu.step()
    assert cpu.pc == 1


def test_im2_interrupt() -> None:
    """IM 2: the handler address is read from I * 256 + data bus."""
    cpu, bus = make(bytes([0x00]), org=0x100)
    bus.mem[0x3AFE] = 0x34
    bus.mem[0x3AFF] = 0x12
    bus.int_data = 0xFE
    cpu.im = 2
    cpu.i = 0x3A
    cpu.iff1 = cpu.iff2 = True
    cpu.int_line = True
    assert cpu.step() == timing.INT_IM2
    assert cpu.pc == 0x1234
    assert (bus.mem[0x7FFF], bus.mem[0x7FFE]) == (0x01, 0x00)


def test_im0_executes_rst_from_data_bus() -> None:
    """IM 0: the RST on the data bus is executed."""
    cpu, bus = make(bytes([0x00]), org=0x200)
    bus.int_data = 0xD7  # RST &10
    cpu.im = 0
    cpu.iff1 = cpu.iff2 = True
    cpu.int_line = True
    assert cpu.step() == timing.INT_IM0
    assert cpu.pc == 0x10
    assert (bus.mem[0x7FFF], bus.mem[0x7FFE]) == (0x02, 0x00)


def test_halt_waits_for_interrupt() -> None:
    """HALT idles with M1 cycles (R counts) until an interrupt; it returns after the HALT."""
    cpu, bus = make(bytes([0x76, 0x00]))  # HALT
    cpu.im = 1
    cpu.iff1 = cpu.iff2 = True
    cpu.step()
    assert cpu.halted and cpu.pc == 1
    r = cpu.r
    for _ in range(3):
        assert cpu.step() == timing.HALT_IDLE
    assert cpu.pc == 1
    assert cpu.r == r + 3  # M1 cycles continue while halted
    cpu.int_line = True
    cpu.step()
    assert not cpu.halted
    assert cpu.pc == 0x38
    assert (bus.mem[0x7FFF], bus.mem[0x7FFE]) == (0x00, 0x01)  # returns after the HALT


def test_ld_a_i_interrupted_clears_pv() -> None:
    """NMOS quirk: an interrupt accepted right after LD A,I clears P/V."""
    cpu, _ = make(bytes([0xED, 0x57, 0x00]))  # LD A,I
    cpu.im = 1
    cpu.iff1 = cpu.iff2 = True
    cpu.step()
    assert cpu.f & 0x04  # P/V = IFF2
    cpu.int_line = True
    cpu.step()
    assert cpu.pc == 0x38
    assert not cpu.f & 0x04  # NMOS quirk


def test_nmi_is_edge_triggered_and_keeps_iff2() -> None:
    """NMI triggers once per edge, keeps IFF2, and RETN restores IFF1."""
    cpu, bus = make(bytes(8))
    cpu.iff1 = cpu.iff2 = True
    cpu.nmi_line = True
    assert cpu.step() == timing.NMI
    assert cpu.pc == 0x66
    assert not cpu.iff1 and cpu.iff2
    cpu.step()  # line still high: no second NMI
    assert cpu.pc == 0x67
    # RETN restores IFF1 from IFF2
    bus.mem[0x67:0x69] = bytes([0xED, 0x45])
    cpu.step()
    assert cpu.iff1
    assert cpu.pc == 0x0000


def test_ei_chain_delays_interrupt() -> None:
    """Consecutive EIs keep delaying the interrupt until one instruction after the last."""
    cpu, _ = make(bytes([0xFB, 0xFB, 0x00]))
    cpu.im = 1
    cpu.int_line = True
    cpu.step()
    cpu.step()
    assert cpu.pc == 2
    cpu.step()  # interrupt only after the instruction following the last EI
    assert cpu.pc == 3
    cpu.step()
    assert cpu.pc == 0x38


# -- timing (D-04) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "setup", "nops"),
    [
        pytest.param(b"\x00", {}, 1, id="NOP"),
        pytest.param(b"\x80", {}, 1, id="ADD A,B"),
        pytest.param(b"\xa0", {}, 1, id="AND B"),
        pytest.param(b"\x07", {}, 1, id="RLCA"),
        pytest.param(b"\x37", {}, 1, id="SCF"),
        pytest.param(b"\x27", {}, 1, id="DAA"),
        pytest.param(b"\x04", {}, 1, id="INC B"),
        pytest.param(b"\x78", {}, 1, id="LD A,B"),
        pytest.param(b"\xe9", {}, 1, id="JP (HL)"),
        pytest.param(b"\x76", {}, 1, id="HALT"),
        pytest.param(b"\x08", {}, 1, id="EX AF,AF'"),
        pytest.param(b"\xeb", {}, 1, id="EX DE,HL"),
        pytest.param(b"\xd9", {}, 1, id="EXX"),
        pytest.param(b"\xf3", {}, 1, id="DI"),
        pytest.param(b"\xfb", {}, 1, id="EI"),
        pytest.param(b"\xc6\x01", {}, 2, id="ADD A,n"),
        pytest.param(b"\x86", {}, 2, id="ADD A,(HL)"),
        pytest.param(b"\xdd\x84", {}, 2, id="ADD A,IXH"),
        pytest.param(b"\xe6\x01", {}, 2, id="AND n"),
        pytest.param(b"\xcb\x00", {}, 2, id="RLC B"),
        pytest.param(b"\xcb\x40", {}, 2, id="BIT 0,B"),
        pytest.param(b"\xcb\xc0", {}, 2, id="SET 0,B"),
        pytest.param(b"\xed\x44", {}, 2, id="NEG"),
        pytest.param(b"\x03", {}, 2, id="INC BC"),
        pytest.param(b"\xdd\x24", {}, 2, id="INC IXH"),
        pytest.param(b"\x06\x01", {}, 2, id="LD B,n"),
        pytest.param(b"\xdd\x7c", {}, 2, id="LD A,IXH"),
        pytest.param(b"\x7e", {}, 2, id="LD A,(HL)"),
        pytest.param(b"\x77", {}, 2, id="LD (HL),A"),
        pytest.param(b"\x0a", {}, 2, id="LD A,(BC)"),
        pytest.param(b"\x12", {}, 2, id="LD (DE),A"),
        pytest.param(b"\xdd\xe9", {}, 2, id="JP (IX)"),
        pytest.param(b"\x20\x05", {"f": 0x40}, 2, id="JR NZ not taken"),
        pytest.param(b"\xf9", {}, 2, id="LD SP,HL"),
        pytest.param(b"\xed\x00", {}, 2, id="ED nop"),
        pytest.param(b"\xed\x46", {}, 2, id="IM 0"),
        pytest.param(b"\xdd\x00", {}, 2, id="DD prefix + NOP"),
        pytest.param(b"\x09", {}, 3, id="ADD HL,BC"),
        pytest.param(b"\xcb\x46", {}, 3, id="BIT 0,(HL)"),
        pytest.param(b"\x34", {}, 3, id="INC (HL)"),
        pytest.param(b"\xdd\x23", {}, 3, id="INC IX"),
        pytest.param(b"\x01\x00\x00", {}, 3, id="LD BC,nn"),
        pytest.param(b"\xdd\x26\x00", {}, 3, id="LD IXH,n"),
        pytest.param(b"\xed\x47", {}, 3, id="LD I,A"),
        pytest.param(b"\xed\x5f", {}, 3, id="LD A,R"),
        pytest.param(b"\x36\x00", {}, 3, id="LD (HL),n"),
        pytest.param(b"\xc3\x00\x10", {}, 3, id="JP nn"),
        pytest.param(b"\xca\x00\x10", {"f": 0x00}, 3, id="JP Z,nn"),
        pytest.param(b"\x20\x05", {"f": 0x00}, 3, id="JR NZ taken"),
        pytest.param(b"\x18\x05", {}, 3, id="JR d"),
        pytest.param(b"\xc9", {}, 3, id="RET"),
        pytest.param(b"\xf1", {}, 3, id="POP AF"),
        pytest.param(b"\xe1", {}, 3, id="POP HL"),
        pytest.param(b"\xdd\xf9", {}, 3, id="LD SP,IX"),
        pytest.param(b"\xdb\x00", {}, 3, id="IN A,(n)"),
        pytest.param(b"\xd3\x00", {}, 3, id="OUT (n),A"),
        pytest.param(b"\x10\xfe", {"b": 1}, 3, id="DJNZ not taken"),
        pytest.param(b"\xdd\x09", {}, 4, id="ADD IX,BC"),
        pytest.param(b"\xed\x4a", {}, 4, id="ADC HL,BC"),
        pytest.param(b"\xed\x42", {}, 4, id="SBC HL,BC"),
        pytest.param(b"\xcb\x06", {}, 4, id="RLC (HL)"),
        pytest.param(b"\xcb\x86", {}, 4, id="RES 0,(HL)"),
        pytest.param(b"\xdd\x21\x00\x00", {}, 4, id="LD IX,nn"),
        pytest.param(b"\x3a\x00\x10", {}, 4, id="LD A,(nn)"),
        pytest.param(b"\x32\x00\x10", {}, 4, id="LD (nn),A"),
        pytest.param(b"\x10\xfe", {"b": 2}, 4, id="DJNZ taken"),
        pytest.param(b"\xed\x45", {}, 4, id="RETN"),
        pytest.param(b"\xed\x4d", {}, 4, id="RETI"),
        pytest.param(b"\xc5", {}, 4, id="PUSH BC"),
        pytest.param(b"\xf5", {}, 4, id="PUSH AF"),
        pytest.param(b"\xc0", {"f": 0x00}, 4, id="RET NZ taken"),
        pytest.param(b"\xed\x78", {}, 4, id="IN A,(C)"),
        pytest.param(b"\xed\x70", {}, 4, id="IN F,(C)"),
        pytest.param(b"\xed\x79", {}, 4, id="OUT (C),A"),
        pytest.param(b"\xed\x71", {}, 4, id="OUT (C),0"),
        pytest.param(b"\xc7", {}, 4, id="RST 0"),
        pytest.param(b"\xff", {}, 4, id="RST 7"),
        pytest.param(b"\xdd\x86\x00", {}, 5, id="ADD A,(IX+d)"),
        pytest.param(b"\xdd\xa6\x00", {}, 5, id="AND (IX+d)"),
        pytest.param(b"\xed\xa1", {}, 5, id="CPI"),
        pytest.param(b"\xed\xa9", {}, 5, id="CPD"),
        pytest.param(b"\xed\x6f", {}, 5, id="RLD"),
        pytest.param(b"\xed\xa0", {}, 5, id="LDI"),
        pytest.param(b"\x22\x00\x10", {}, 5, id="LD (nn),HL"),
        pytest.param(b"\x2a\x00\x10", {}, 5, id="LD HL,(nn)"),
        pytest.param(b"\xdd\x7e\x00", {}, 5, id="LD A,(IX+d)"),
        pytest.param(b"\xdd\x77\x00", {}, 5, id="LD (IX+d),A"),
        pytest.param(b"\xdd\xe5", {}, 5, id="PUSH IX"),
        pytest.param(b"\xdd\xe1", {}, 5, id="POP IX"),
        pytest.param(b"\xfd\xe1", {}, 5, id="POP IY"),
        pytest.param(b"\xcd\x00\x10", {}, 5, id="CALL nn"),
        pytest.param(b"\xc4\x00\x10", {"f": 0x00}, 5, id="CALL NZ taken"),
        pytest.param(b"\xed\xa3", {}, 5, id="OUTI"),
        pytest.param(b"\xed\xa2", {}, 5, id="INI"),
        pytest.param(b"\xed\xb0", {"bc": 1}, 5, id="LDIR last"),
        pytest.param(b"\xed\xb1", {"bc": 1}, 5, id="CPIR last"),
        pytest.param(b"\xed\xb2", {"b": 1}, 5, id="INIR last"),
        pytest.param(b"\xed\xb3", {"b": 1}, 5, id="OTIR last"),
        pytest.param(b"\xdd\xcb\x00\x46", {}, 6, id="BIT 0,(IX+d)"),
        pytest.param(b"\xdd\x34\x00", {}, 6, id="INC (IX+d)"),
        pytest.param(b"\xed\x43\x00\x10", {}, 6, id="LD (nn),BC"),
        pytest.param(b"\xdd\x36\x00\x00", {}, 6, id="LD (IX+d),n"),
        pytest.param(b"\xed\x4b\x00\x10", {}, 6, id="LD BC,(nn)"),
        pytest.param(b"\xdd\x22\x00\x10", {}, 6, id="LD (nn),IX"),
        pytest.param(b"\xdd\x2a\x00\x10", {}, 6, id="LD IX,(nn)"),
        pytest.param(b"\xe3", {}, 6, id="EX (SP),HL"),
        pytest.param(b"\xed\xb0", {"bc": 2}, 6, id="LDIR repeating"),
        pytest.param(b"\xed\xb1", {"bc": 2}, 6, id="CPIR repeating"),
        pytest.param(b"\xed\xb2", {"b": 2}, 6, id="INIR repeating"),
        pytest.param(b"\xed\xbb", {"b": 2}, 6, id="OTDR repeating"),
        pytest.param(b"\xdd\xcb\x00\x06", {}, 7, id="RLC (IX+d)"),
        pytest.param(b"\xdd\xcb\x00\xc6", {}, 7, id="SET 0,(IX+d)"),
        pytest.param(b"\xdd\xe3", {}, 7, id="EX (SP),IX"),
        pytest.param(b"\xc4\x00\x10", {"f": 0x40}, 3, id="CALL NZ not taken"),
        pytest.param(b"\xc0", {"f": 0x40}, 2, id="RET NZ not taken"),
    ],
)
def test_timing(code: bytes, setup: dict[str, int], nops: int) -> None:
    """Instruction costs in NOPs match the CPC timing cheat sheet, one case per entry (D-04)."""
    cpu, _ = make(code, org=0x100, log=False)
    cpu.hl = 0x4000
    cpu.de = 0x5000
    cpu.set_registers(setup)
    assert cpu.step() == nops


def test_timing_table_complete() -> None:
    """The timing tables cover all opcodes with a positive cost."""
    assert len(timing.MAIN) == 256
    assert len(timing.ED) == 256
    prefixes = {0xCB, 0xDD, 0xED, 0xFD}
    assert all(timing.MAIN[op] >= 1 for op in range(256) if op not in prefixes)


# -- debugger access (FR-CPU-06) ---------------------------------------------------------------


def test_registers_roundtrip() -> None:
    """registers()/set_registers() cover every register and reject unknown names."""
    cpu, _ = make(b"")
    values = {
        "af": 0x1234, "bc": 0x2345, "de": 0x3456, "hl": 0x4567, "ix": 0x5678, "iy": 0x6789,
        "af_": 0x789A, "bc_": 0x89AB, "de_": 0x9ABC, "hl_": 0xABCD, "sp": 0xBCDE, "pc": 0xCDEF,
        "i": 0x12, "r": 0x34, "wz": 0x4321, "im": 2, "iff1": 1, "iff2": 0,
        "flags_written_by_last_instruction": 0x28,
    }  # fmt: skip
    cpu.set_registers(values)
    regs = cpu.registers()
    assert set(regs) == set(REGISTER_NAMES)
    for name, v in values.items():
        assert regs[name] == v, name
    assert (regs["a"], regs["f"], regs["ixh"], regs["iyl"]) == (0x12, 0x34, 0x56, 0x89)
    with pytest.raises(KeyError):
        cpu.set_registers({"xx": 1})


def test_replacing_the_bus() -> None:
    """Assigning a new bus makes the CPU use it."""
    cpu, _ = make(bytes([0x3E, 0x11]))
    other = FlatBus(bytes([0x3E, 0x22]))
    cpu.bus = other
    cpu.step()
    assert cpu.a == 0x22
