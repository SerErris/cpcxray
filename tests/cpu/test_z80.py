"""Z80 behaviour not covered by the per-opcode JSON tests: interrupts, NMI, HALT, EI delay,
M1 signalling, timing (D-04) and the debugger register interface."""

from __future__ import annotations

import pytest

from cpcxray.cpu import timing
from cpcxray.cpu.bus import Cycle, FlatBus
from cpcxray.cpu.z80 import REGISTER_NAMES, Z80


def make(code: bytes, org: int = 0, log: bool = True) -> tuple[Z80, FlatBus]:
    bus = FlatBus(log=log)
    bus.mem[org : org + len(code)] = code
    cpu = Z80(bus)
    cpu.pc = org
    cpu.sp = 0x8000
    return cpu, bus


def kinds(bus: FlatBus) -> list[tuple[Cycle, int]]:
    assert bus.log is not None
    return [(k, a) for k, a, _ in bus.log]


# -- M1 signalling (FR-CPU-04, §5.5) ---------------------------------------------------------


def test_index_cb_displacement_and_opcode_are_not_m1() -> None:
    # DD CB d op: only DD and CB are M1; d and op are ordinary reads
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
    # The CPU itself never reads past an RST; the firmware handler reads them as data.
    cpu, bus = make(bytes([0xCF, 0x50, 0x81]))  # RST 1 + inline word
    cpu.step()
    assert kinds(bus) == [(Cycle.M1, 0), (Cycle.MEM_WR, 0x7FFF), (Cycle.MEM_WR, 0x7FFE)]
    assert cpu.pc == 0x08


def test_r_register_keeps_bit7() -> None:
    cpu, _ = make(bytes(300))
    cpu.r = 0xFF
    cpu.step()
    assert cpu.r == 0x80


# -- interrupts (FR-CPU-05) ----------------------------------------------------------------------


def test_im1_interrupt() -> None:
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
    cpu, _ = make(bytes(4))
    cpu.im = 1
    cpu.int_line = True
    cpu.step()
    assert cpu.pc == 1


def test_im2_interrupt() -> None:
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
    cpu, bus = make(bytes([0x00]), org=0x200)
    bus.int_data = 0xD7  # RST &10
    cpu.im = 0
    cpu.iff1 = cpu.iff2 = True
    cpu.int_line = True
    assert cpu.step() == timing.INT_IM0
    assert cpu.pc == 0x10
    assert (bus.mem[0x7FFF], bus.mem[0x7FFE]) == (0x02, 0x00)


def test_halt_waits_for_interrupt() -> None:
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
        (b"\x00", {}, 1),  # NOP
        (b"\x7e", {}, 2),  # LD A,(HL)
        (b"\xcd\x00\x10", {}, 5),  # CALL nn
        (b"\xc5", {}, 4),  # PUSH BC
        (b"\xe3", {}, 6),  # EX (SP),HL
        (b"\x20\x05", {"f": 0x00}, 3),  # JR NZ taken
        (b"\x20\x05", {"f": 0x40}, 2),  # JR NZ not taken
        (b"\xc4\x00\x10", {"f": 0x00}, 5),  # CALL NZ taken
        (b"\xc4\x00\x10", {"f": 0x40}, 3),  # CALL NZ not taken
        (b"\xc0", {"f": 0x00}, 4),  # RET NZ taken
        (b"\x10\xfe", {"b": 2}, 4),  # DJNZ taken
        (b"\x10\xfe", {"b": 1}, 3),  # DJNZ not taken
        (b"\xed\xb0", {"bc": 2}, 6),  # LDIR repeating
        (b"\xed\xb0", {"bc": 1}, 5),  # LDIR last
        (b"\xed\x79", {}, 4),  # OUT (C),A
        (b"\xd3\x00", {}, 3),  # OUT (n),A
        (b"\xdd\x7e\x00", {}, 5),  # LD A,(IX+d)
        (b"\xdd\x34\x00", {}, 6),  # INC (IX+d)
        (b"\xdd\xe5", {}, 5),  # PUSH IX
        (b"\xdd\xcb\x00\x46", {}, 6),  # BIT 0,(IX+d)
        (b"\xdd\xcb\x00\xc6", {}, 7),  # SET 0,(IX+d)
        (b"\xcb\x46", {}, 3),  # BIT 0,(HL)
        (b"\xcb\xc6", {}, 4),  # SET 0,(HL)
    ],
)
def test_timing(code: bytes, setup: dict[str, int], nops: int) -> None:
    cpu, _ = make(code, org=0x100, log=False)
    cpu.hl = 0x4000
    cpu.de = 0x5000
    cpu.set_registers(setup)
    assert cpu.step() == nops


def test_timing_table_complete() -> None:
    assert len(timing.MAIN) == 256
    assert len(timing.ED) == 256
    prefixes = {0xCB, 0xDD, 0xED, 0xFD}
    assert all(timing.MAIN[op] >= 1 for op in range(256) if op not in prefixes)


# -- debugger access (FR-CPU-06) ---------------------------------------------------------------


def test_registers_roundtrip() -> None:
    cpu, _ = make(b"")
    values = {
        "af": 0x1234, "bc": 0x2345, "de": 0x3456, "hl": 0x4567, "ix": 0x5678, "iy": 0x6789,
        "af_": 0x789A, "bc_": 0x89AB, "de_": 0x9ABC, "hl_": 0xABCD, "sp": 0xBCDE, "pc": 0xCDEF,
        "i": 0x12, "r": 0x34, "wz": 0x4321, "im": 2, "iff1": 1, "iff2": 0, "q": 0x28,
    }  # fmt: skip
    cpu.set_registers(values)
    regs = cpu.registers()
    assert set(regs) == set(REGISTER_NAMES)
    for name, v in values.items():
        assert regs[name] == v, name
    assert (regs["a"], regs["f"], regs["ixh"], regs["iyl"]) == (0x12, 0x34, 0x56, 0x89)
    with pytest.raises(KeyError):
        cpu.set_registers({"xx": 1})


def test_rebind_bus() -> None:
    cpu, _ = make(bytes([0x3E, 0x11]))
    other = FlatBus(bytes([0x3E, 0x22]))
    cpu.bus = other
    cpu.step()
    assert cpu.a == 0x22
