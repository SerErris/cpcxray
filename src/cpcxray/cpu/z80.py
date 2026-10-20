"""Z80 CPU core (design.md §6.2): instruction-level, software compatible (FR-CPU-01..07).

Each `step()` executes one instruction (or accepts one interrupt) atomically and returns its cost
in CPC NOPs (µs, D-04). All bus accesses are issued in the order of the real Z80 and carry their
signals through the bus method used: opcode and prefix bytes via `fetch()` (M1), everything
else via `read()`/`write()`/`io_in()`/`io_out()`; `DD CB d op` reads *d* and *op* without M1.

Opcode handlers are closures built once per CPU instance from the standard x/y/z/p/q opcode
decoding; the DD/FD tables are produced by the same factories with IX/IY substituted for HL.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from cpcxray.cpu import timing
from cpcxray.cpu.bus import CpuBus
from cpcxray.cpu.tables import CF, HF, NF, PARITY, PF, SF, SZ, SZP, XF, XYF, YF, ZF

# Indices into Z80.r8. 0-7 match the 3-bit register field of the opcodes, except that 6 means
# (HL) there and holds F here.
B, C, D, E, H, L, F, A = range(8)
IXH, IXL, IYH, IYL = range(8, 12)

type Handler = Callable[[], int]
type IndexHandler = Callable[[int], int]

REGISTER_NAMES = (
    "a", "f", "b", "c", "d", "e", "h", "l", "af", "bc", "de", "hl",
    "af_", "bc_", "de_", "hl_", "ix", "iy", "ixh", "ixl", "iyh", "iyl",
    "sp", "pc", "i", "r", "wz", "q", "iff1", "iff2", "im", "ei_delay", "halted",
)  # fmt: skip


def _r8(idx: int) -> property:
    def get(self: Z80) -> int:
        return self.r8[idx]

    def set_(self: Z80, v: int) -> None:
        self.r8[idx] = v & 0xFF

    return property(get, set_)


def _r16(hi: int, lo: int) -> property:
    def get(self: Z80) -> int:
        return self.r8[hi] << 8 | self.r8[lo]

    def set_(self: Z80, v: int) -> None:
        self.r8[hi] = (v >> 8) & 0xFF
        self.r8[lo] = v & 0xFF

    return property(get, set_)


class Z80:
    a = _r8(A)
    f = _r8(F)
    b = _r8(B)
    c = _r8(C)
    d = _r8(D)
    e = _r8(E)
    h = _r8(H)
    l = _r8(L)  # noqa: E741
    ixh = _r8(IXH)
    ixl = _r8(IXL)
    iyh = _r8(IYH)
    iyl = _r8(IYL)
    af = _r16(A, F)
    bc = _r16(B, C)
    de = _r16(D, E)
    hl = _r16(H, L)
    ix = _r16(IXH, IXL)
    iy = _r16(IYH, IYL)

    def __init__(self, bus: CpuBus) -> None:
        self.r8 = [0] * 12
        self.sp = 0
        self.pc = 0
        self.wz = 0  # MEMPTR
        self.i = 0
        self.r = 0  # bits 0-6 count M1 cycles, bit 7 only changes by LD R,A
        self.af_ = 0
        self.bc_ = 0
        self.de_ = 0
        self.hl_ = 0
        self.iff1 = False
        self.iff2 = False
        self.im = 0
        self.q = 0  # F if the last instruction changed the flags, else 0 (SCF/CCF X/Y)
        self.p = False  # last instruction was LD A,I or LD A,R (P/V quirk on interrupt)
        self.ei_delay = False  # EI just executed: no interrupt before the next instruction
        self.halted = False
        self.int_line = False  # INT input, level triggered; set by the machine
        self.nmi_pending = False
        self._nmi_line = False
        self._q_last = 0
        self.bus = bus
        tables = _Builder(self)
        self._main = tables.main
        self.reset()

    # -- bus binding -------------------------------------------------------------------------

    @property
    def bus(self) -> CpuBus:
        return self._bus

    @bus.setter
    def bus(self, bus: CpuBus) -> None:
        self._bus = bus
        self.rebind_bus()

    def rebind_bus(self) -> None:
        """Re-cache the bus methods; call after the bus swaps its fast/observed variants (D-12)."""
        bus = self._bus
        self._fe = bus.fetch
        self._rd = bus.read
        self._wr = bus.write
        self._in = bus.io_in
        self._out = bus.io_out
        self._ack = bus.int_ack

    # -- control -------------------------------------------------------------------------------

    def reset(self) -> None:
        self.pc = 0
        self.i = 0
        self.r = 0
        self.iff1 = self.iff2 = False
        self.im = 0
        self.af = 0xFFFF
        self.sp = 0xFFFF
        self.wz = 0
        self.q = 0
        self.p = False
        self.ei_delay = False
        self.halted = False
        self.nmi_pending = False

    @property
    def nmi_line(self) -> bool:
        return self._nmi_line

    @nmi_line.setter
    def nmi_line(self, level: bool) -> None:
        if level and not self._nmi_line:  # NMI is edge triggered
            self.nmi_pending = True
        self._nmi_line = level

    def step(self) -> int:
        """Execute one instruction or accept one interrupt; return its cost in µs (NOPs)."""
        if self.nmi_pending:
            return self._accept_nmi()
        if self.ei_delay:
            self.ei_delay = False
        elif self.int_line and self.iff1:
            return self._accept_int()
        self._q_last = self.q
        self.q = 0
        self.p = False
        r = self.r
        self.r = (r & 0x80) | ((r + 1) & 0x7F)
        pc = self.pc
        if self.halted:
            self._fe(pc)  # the CPU keeps fetching (and ignoring) the next opcode
            return timing.HALT_IDLE
        op = self._fe(pc)
        self.pc = (pc + 1) & 0xFFFF
        return self._main[op]()

    def _push(self, v: int) -> None:
        sp = (self.sp - 1) & 0xFFFF
        self._wr(sp, v >> 8)
        sp = (sp - 1) & 0xFFFF
        self._wr(sp, v & 0xFF)
        self.sp = sp

    def _accept_int(self) -> int:
        self.halted = False
        self.iff1 = self.iff2 = False
        if self.p:  # NMOS: LD A,I / LD A,R interrupted reports P/V = 0
            self.r8[F] &= ~PF
        self.p = False
        self.q = 0
        r = self.r
        self.r = (r & 0x80) | ((r + 1) & 0x7F)
        data = self._ack()
        if self.im == 2:
            self._push(self.pc)
            vec = (self.i << 8) | data
            self.pc = self.wz = self._rd(vec) | self._rd((vec + 1) & 0xFFFF) << 8
            return timing.INT_IM2
        if self.im == 1:
            self._push(self.pc)
            self.pc = self.wz = 0x38
            return timing.INT_IM1
        # IM 0: execute the byte on the data bus as an instruction (in practice an RST)
        self._main[data]()
        return timing.INT_IM0

    def _accept_nmi(self) -> int:
        self.nmi_pending = False
        self.halted = False
        self.iff1 = False
        self.p = False
        self.q = 0
        r = self.r
        self.r = (r & 0x80) | ((r + 1) & 0x7F)
        self._push(self.pc)
        self.pc = self.wz = 0x66
        return timing.NMI

    # -- debugger access (FR-CPU-06) -------------------------------------------------------------

    def registers(self) -> dict[str, int]:
        return {name: int(getattr(self, name)) for name in REGISTER_NAMES}

    def set_registers(self, values: Mapping[str, int | bool]) -> None:
        for name, v in values.items():
            if name not in REGISTER_NAMES:
                raise KeyError(f"unknown register {name!r}")
            if name in ("iff1", "iff2", "ei_delay", "halted"):
                setattr(self, name, bool(v))
            elif name in ("pc", "sp", "wz", "af_", "bc_", "de_", "hl_"):
                setattr(self, name, int(v) & 0xFFFF)
            elif name in ("i", "r", "q"):
                setattr(self, name, int(v) & 0xFF)
            else:
                setattr(self, name, int(v))


# Condition codes NZ, Z, NC, C, PO, PE, P, M as (flag mask, value that XORs to "true")
_COND = tuple(
    (mask, 0 if y & 1 else mask) for y, mask in enumerate((ZF, ZF, CF, CF, PF, PF, SF, SF))
)


class _Builder:
    """Builds the opcode handler tables for one CPU instance."""

    def __init__(self, cpu: Z80) -> None:
        self.cpu = cpu
        self.r = cpu.r8
        self.cb = [self._cb_op(op) for op in range(256)]
        self.ed = [self._ed_op(op) for op in range(256)]
        self.ddcb = [self._index_cb_op(op) for op in range(256)]
        self.main: list[Handler] = [self._main_op(op, H, L, False) for op in range(256)]
        self.dd: list[Handler] = [self._main_op(op, IXH, IXL, True) for op in range(256)]
        self.fd: list[Handler] = [self._main_op(op, IYH, IYL, True) for op in range(256)]

    # -- shared helpers ------------------------------------------------------------------------

    def imm8(self) -> int:
        cpu = self.cpu
        pc = cpu.pc
        cpu.pc = (pc + 1) & 0xFFFF
        return cpu._rd(pc)

    def imm16(self) -> int:
        cpu = self.cpu
        pc = cpu.pc
        lo = cpu._rd(pc)
        hi = cpu._rd((pc + 1) & 0xFFFF)
        cpu.pc = (pc + 2) & 0xFFFF
        return lo | hi << 8

    def m1(self) -> int:
        cpu = self.cpu
        pc = cpu.pc
        op = cpu._fe(pc)
        cpu.pc = (pc + 1) & 0xFFFF
        r = cpu.r
        cpu.r = (r & 0x80) | ((r + 1) & 0x7F)
        return op

    def pop16(self) -> int:
        cpu = self.cpu
        sp = cpu.sp
        lo = cpu._rd(sp)
        hi = cpu._rd((sp + 1) & 0xFFFF)
        cpu.sp = (sp + 2) & 0xFFFF
        return lo | hi << 8

    def set_f(self, f: int) -> None:
        self.r[F] = f
        self.cpu.q = f

    # 8-bit ALU on A ----------------------------------------------------------------------------

    def alu(self, y: int) -> Callable[[int], None]:
        r = self.r
        set_f = self.set_f

        def add(v: int) -> None:
            a = r[A]
            res = a + v
            r[A] = res & 0xFF
            set_f(
                SZ[res & 0xFF] | (res >> 8) | ((a ^ v ^ res) & HF)
                | ((~(a ^ v) & (a ^ res) & 0x80) >> 5)
            )  # fmt: skip

        def adc(v: int) -> None:
            a = r[A]
            res = a + v + (r[F] & CF)
            r[A] = res & 0xFF
            set_f(
                SZ[res & 0xFF] | (res >> 8) | ((a ^ v ^ res) & HF)
                | ((~(a ^ v) & (a ^ res) & 0x80) >> 5)
            )  # fmt: skip

        def sub(v: int) -> None:
            a = r[A]
            res = a - v
            r[A] = res & 0xFF
            set_f(
                SZ[res & 0xFF] | NF | ((res >> 8) & CF) | ((a ^ v ^ res) & HF)
                | (((a ^ v) & (a ^ res) & 0x80) >> 5)
            )  # fmt: skip

        def sbc(v: int) -> None:
            a = r[A]
            res = a - v - (r[F] & CF)
            r[A] = res & 0xFF
            set_f(
                SZ[res & 0xFF] | NF | ((res >> 8) & CF) | ((a ^ v ^ res) & HF)
                | (((a ^ v) & (a ^ res) & 0x80) >> 5)
            )  # fmt: skip

        def and_(v: int) -> None:
            a = r[A] & v
            r[A] = a
            set_f(SZP[a] | HF)

        def xor(v: int) -> None:
            a = r[A] ^ v
            r[A] = a
            set_f(SZP[a])

        def or_(v: int) -> None:
            a = r[A] | v
            r[A] = a
            set_f(SZP[a])

        def cp(v: int) -> None:
            a = r[A]
            res = a - v
            set_f(
                (SZ[res & 0xFF] & ~XYF) | (v & XYF) | NF | ((res >> 8) & CF)
                | ((a ^ v ^ res) & HF) | (((a ^ v) & (a ^ res) & 0x80) >> 5)
            )  # fmt: skip

        return (add, adc, sub, sbc, and_, xor, or_, cp)[y]

    def inc8(self, v: int) -> int:
        res = (v + 1) & 0xFF
        self.set_f(
            (self.r[F] & CF) | SZ[res] | (HF if v & 0x0F == 0x0F else 0)
            | (PF if v == 0x7F else 0)
        )  # fmt: skip
        return res

    def dec8(self, v: int) -> int:
        res = (v - 1) & 0xFF
        self.set_f(
            (self.r[F] & CF) | NF | SZ[res] | (HF if v & 0x0F == 0 else 0)
            | (PF if v == 0x80 else 0)
        )  # fmt: skip
        return res

    def rot(self, y: int) -> Callable[[int], int]:
        """CB rotate/shift y: RLC RRC RL RR SLA SRA SLL SRL; sets flags, returns the result."""
        r = self.r
        set_f = self.set_f

        def rlc(v: int) -> int:
            res = ((v << 1) | (v >> 7)) & 0xFF
            set_f(SZP[res] | (v >> 7))
            return res

        def rrc(v: int) -> int:
            res = (v >> 1) | ((v & 1) << 7)
            set_f(SZP[res] | (v & 1))
            return res

        def rl(v: int) -> int:
            res = ((v << 1) | (r[F] & CF)) & 0xFF
            set_f(SZP[res] | (v >> 7))
            return res

        def rr(v: int) -> int:
            res = (v >> 1) | ((r[F] & CF) << 7)
            set_f(SZP[res] | (v & 1))
            return res

        def sla(v: int) -> int:
            res = (v << 1) & 0xFF
            set_f(SZP[res] | (v >> 7))
            return res

        def sra(v: int) -> int:
            res = (v >> 1) | (v & 0x80)
            set_f(SZP[res] | (v & 1))
            return res

        def sll(v: int) -> int:
            res = ((v << 1) | 1) & 0xFF
            set_f(SZP[res] | (v >> 7))
            return res

        def srl(v: int) -> int:
            res = v >> 1
            set_f(SZP[res] | (v & 1))
            return res

        return (rlc, rrc, rl, rr, sla, sra, sll, srl)[y]

    def bit_flags(self, v: int, y: int, xy: int) -> None:
        """BIT y,v; X/Y come from `xy` (the operand, or MEMPTR high byte for memory forms)."""
        masked = v & (1 << y)
        self.set_f((self.r[F] & CF) | HF | (xy & XYF) | (0 if masked else ZF | PF) | (masked & SF))

    # -- unprefixed / DD / FD ------------------------------------------------------------------

    def _main_op(self, op: int, hi: int, lo: int, indexed: bool) -> Handler:
        cpu = self.cpu
        r = self.r
        imm8 = self.imm8
        imm16 = self.imm16
        set_f = self.set_f
        x, y, z = op >> 6, (op >> 3) & 7, op & 7
        p, q = y >> 1, y & 1
        cost = timing.MAIN[op] + (timing.index_extra(op) if indexed else 0)
        taken = timing.MAIN_TAKEN.get(op, timing.MAIN[op]) + (1 if indexed else 0)
        rmap = (B, C, D, E, hi, lo, F, A)  # F never used: 6 is the memory operand

        if indexed:

            def mem_addr() -> int:
                d = imm8()
                addr = ((r[hi] << 8 | r[lo]) + (d ^ 0x80) - 0x80) & 0xFFFF
                cpu.wz = addr
                return addr
        else:

            def mem_addr() -> int:
                return r[H] << 8 | r[L]

        pair = ((B, C), (D, E), (hi, lo))

        def get_rp(p: int) -> Callable[[], int]:
            if p == 3:
                return lambda: cpu.sp
            ph, pl = pair[p]
            return lambda: r[ph] << 8 | r[pl]

        def set_rp(p: int) -> Callable[[int], None]:
            if p == 3:

                def set_sp(v: int) -> None:
                    cpu.sp = v

                return set_sp
            ph, pl = pair[p]

            def set_pair(v: int) -> None:
                r[ph] = v >> 8
                r[pl] = v & 0xFF

            return set_pair

        def cond(y: int) -> Callable[[], int]:
            mask, inv = _COND[y]
            return lambda: (r[F] & mask) ^ inv

        if x == 0:
            if z == 0:
                if y == 0:
                    return lambda: cost
                if y == 1:

                    def ex_af() -> int:
                        af = r[A] << 8 | r[F]
                        r[A] = cpu.af_ >> 8
                        r[F] = cpu.af_ & 0xFF
                        cpu.af_ = af
                        return cost

                    return ex_af
                if y == 2:

                    def djnz() -> int:
                        d = imm8()
                        b = (r[B] - 1) & 0xFF
                        r[B] = b
                        if b:
                            cpu.pc = cpu.wz = (cpu.pc + (d ^ 0x80) - 0x80) & 0xFFFF
                            return taken
                        return cost

                    return djnz
                if y == 3:

                    def jr() -> int:
                        d = imm8()
                        cpu.pc = cpu.wz = (cpu.pc + (d ^ 0x80) - 0x80) & 0xFFFF
                        return cost

                    return jr
                c = cond(y - 4)

                def jr_cc() -> int:
                    d = imm8()
                    if c():
                        cpu.pc = cpu.wz = (cpu.pc + (d ^ 0x80) - 0x80) & 0xFFFF
                        return taken
                    return cost

                return jr_cc
            if z == 1:
                setter = set_rp(p)
                if q == 0:

                    def ld_rp_nn() -> int:
                        setter(imm16())
                        return cost

                    return ld_rp_nn
                getter = get_rp(p)

                def add_hl() -> int:
                    hl = r[hi] << 8 | r[lo]
                    v = getter()
                    res = hl + v
                    cpu.wz = (hl + 1) & 0xFFFF
                    set_f(
                        (r[F] & (SF | ZF | PF)) | ((res >> 16) & CF)
                        | (((hl ^ v ^ res) >> 8) & HF) | ((res >> 8) & XYF)
                    )  # fmt: skip
                    r[hi] = (res >> 8) & 0xFF
                    r[lo] = res & 0xFF
                    return cost

                return add_hl
            if z == 2:
                if p < 2:
                    ph, pl = pair[p]
                    if q == 0:

                        def ld_rp_a() -> int:
                            addr = r[ph] << 8 | r[pl]
                            a = r[A]
                            cpu._wr(addr, a)
                            cpu.wz = ((addr + 1) & 0xFF) | (a << 8)
                            return cost

                        return ld_rp_a

                    def ld_a_rp() -> int:
                        addr = r[ph] << 8 | r[pl]
                        r[A] = cpu._rd(addr)
                        cpu.wz = (addr + 1) & 0xFFFF
                        return cost

                    return ld_a_rp
                if p == 2:
                    if q == 0:

                        def ld_nn_hl() -> int:
                            nn = imm16()
                            cpu._wr(nn, r[lo])
                            nn = (nn + 1) & 0xFFFF
                            cpu._wr(nn, r[hi])
                            cpu.wz = nn
                            return cost

                        return ld_nn_hl

                    def ld_hl_nn() -> int:
                        nn = imm16()
                        r[lo] = cpu._rd(nn)
                        nn = (nn + 1) & 0xFFFF
                        r[hi] = cpu._rd(nn)
                        cpu.wz = nn
                        return cost

                    return ld_hl_nn
                if q == 0:

                    def ld_nn_a() -> int:
                        nn = imm16()
                        a = r[A]
                        cpu._wr(nn, a)
                        cpu.wz = ((nn + 1) & 0xFF) | (a << 8)
                        return cost

                    return ld_nn_a

                def ld_a_nn() -> int:
                    nn = imm16()
                    r[A] = cpu._rd(nn)
                    cpu.wz = (nn + 1) & 0xFFFF
                    return cost

                return ld_a_nn
            if z == 3:
                getter = get_rp(p)
                setter = set_rp(p)
                delta = 1 if q == 0 else -1

                def inc_dec_rp() -> int:
                    setter((getter() + delta) & 0xFFFF)
                    return cost

                return inc_dec_rp
            if z in (4, 5):
                fn = self.inc8 if z == 4 else self.dec8
                if y == 6:

                    def inc_dec_mem() -> int:
                        addr = mem_addr()
                        cpu._wr(addr, fn(cpu._rd(addr)))
                        return cost

                    return inc_dec_mem
                reg = rmap[y]

                def inc_dec_r() -> int:
                    r[reg] = fn(r[reg])
                    return cost

                return inc_dec_r
            if z == 6:
                if y == 6:

                    def ld_mem_n() -> int:
                        addr = mem_addr()
                        cpu._wr(addr, imm8())
                        return cost

                    return ld_mem_n
                reg = rmap[y]

                def ld_r_n() -> int:
                    r[reg] = imm8()
                    return cost

                return ld_r_n
            return self._acc_op(y, cost)

        if x == 1:
            if op == 0x76:

                def halt() -> int:
                    cpu.halted = True
                    return cost

                return halt
            if z == 6:

                def ld_r_mem() -> int:
                    r[y] = cpu._rd(mem_addr())
                    return cost

                return ld_r_mem
            if y == 6:

                def ld_mem_r() -> int:
                    cpu._wr(mem_addr(), r[z])
                    return cost

                return ld_mem_r
            dst, src = rmap[y], rmap[z]

            def ld_r_r() -> int:
                r[dst] = r[src]
                return cost

            return ld_r_r

        if x == 2:
            fn = self.alu(y)
            if z == 6:

                def alu_mem() -> int:
                    fn(cpu._rd(mem_addr()))
                    return cost

                return alu_mem
            reg = rmap[z]

            def alu_r() -> int:
                fn(r[reg])
                return cost

            return alu_r

        # x == 3
        pop16 = self.pop16
        push16 = cpu._push
        if z == 0:
            c = cond(y)

            def ret_cc() -> int:
                if c():
                    cpu.pc = cpu.wz = pop16()
                    return taken
                return cost

            return ret_cc
        if z == 1:
            if q == 0:
                if p == 3:

                    def pop_af() -> int:
                        v = pop16()
                        r[A] = v >> 8
                        r[F] = v & 0xFF
                        return cost

                    return pop_af
                setter = set_rp(p)

                def pop_rp() -> int:
                    setter(pop16())
                    return cost

                return pop_rp
            if p == 0:

                def ret() -> int:
                    cpu.pc = cpu.wz = pop16()
                    return cost

                return ret
            if p == 1:

                def exx() -> int:
                    bc, de, hl = r[B] << 8 | r[C], r[D] << 8 | r[E], r[H] << 8 | r[L]
                    r[B], r[C] = cpu.bc_ >> 8, cpu.bc_ & 0xFF
                    r[D], r[E] = cpu.de_ >> 8, cpu.de_ & 0xFF
                    r[H], r[L] = cpu.hl_ >> 8, cpu.hl_ & 0xFF
                    cpu.bc_, cpu.de_, cpu.hl_ = bc, de, hl
                    return cost

                return exx
            if p == 2:

                def jp_hl() -> int:
                    cpu.pc = r[hi] << 8 | r[lo]
                    return cost

                return jp_hl

            def ld_sp_hl() -> int:
                cpu.sp = r[hi] << 8 | r[lo]
                return cost

            return ld_sp_hl
        if z == 2:
            c = cond(y)

            def jp_cc() -> int:
                nn = imm16()
                cpu.wz = nn
                if c():
                    cpu.pc = nn
                return cost

            return jp_cc
        if z == 3:
            if y == 0:

                def jp() -> int:
                    cpu.pc = cpu.wz = imm16()
                    return cost

                return jp
            if y == 1:
                if indexed:
                    return self._index_cb_prefix(hi, lo)
                cb = self.cb
                m1 = self.m1
                return lambda: cb[m1()]()
            if y == 2:

                def out_n_a() -> int:
                    n = imm8()
                    a = r[A]
                    cpu._out(a << 8 | n, a)
                    cpu.wz = ((n + 1) & 0xFF) | (a << 8)
                    return cost

                return out_n_a
            if y == 3:

                def in_a_n() -> int:
                    port = r[A] << 8 | imm8()
                    r[A] = cpu._in(port)
                    cpu.wz = (port + 1) & 0xFFFF
                    return cost

                return in_a_n
            if y == 4:

                def ex_sp_hl() -> int:
                    sp = cpu.sp
                    sp1 = (sp + 1) & 0xFFFF
                    lo_v = cpu._rd(sp)
                    hi_v = cpu._rd(sp1)
                    cpu._wr(sp1, r[hi])
                    cpu._wr(sp, r[lo])
                    r[hi], r[lo] = hi_v, lo_v
                    cpu.wz = hi_v << 8 | lo_v
                    return cost

                return ex_sp_hl
            if y == 5:

                def ex_de_hl() -> int:  # never affected by DD/FD
                    r[D], r[E], r[H], r[L] = r[H], r[L], r[D], r[E]
                    return cost

                return ex_de_hl
            if y == 6:

                def di() -> int:
                    cpu.iff1 = cpu.iff2 = False
                    return cost

                return di

            def ei() -> int:
                cpu.iff1 = cpu.iff2 = True
                cpu.ei_delay = True
                return cost

            return ei
        if z == 4:
            c = cond(y)

            def call_cc() -> int:
                nn = imm16()
                cpu.wz = nn
                if c():
                    push16(cpu.pc)
                    cpu.pc = nn
                    return taken
                return cost

            return call_cc
        if z == 5:
            if q == 0:
                if p == 3:
                    return lambda: (push16(r[A] << 8 | r[F]), cost)[1]
                getter = get_rp(p)
                return lambda: (push16(getter()), cost)[1]
            if p == 0:

                def call() -> int:
                    nn = imm16()
                    cpu.wz = nn
                    push16(cpu.pc)
                    cpu.pc = nn
                    return cost

                return call
            # prefixes DD, ED, FD; under a DD/FD prefix they cost one extra NOP
            extra = 1 if indexed else 0
            m1 = self.m1
            if p == 2:
                ed = self.ed
                return lambda: ed[m1()]() + extra
            table_name = "dd" if p == 1 else "fd"

            def prefix() -> int:
                cpu._q_last = 0  # the prefix counts as an instruction that leaves Q = 0
                return getattr(self, table_name)[m1()]() + extra

            return prefix
        if z == 6:
            fn = self.alu(y)

            def alu_n() -> int:
                fn(imm8())
                return cost

            return alu_n

        target = y * 8

        def rst() -> int:
            push16(cpu.pc)
            cpu.pc = cpu.wz = target
            return cost

        return rst

    def _acc_op(self, y: int, cost: int) -> Handler:
        """RLCA RRCA RLA RRA DAA CPL SCF CCF."""
        cpu = self.cpu
        r = self.r
        set_f = self.set_f
        keep = SF | ZF | PF

        if y == 0:

            def rlca() -> int:
                a = r[A]
                a = ((a << 1) | (a >> 7)) & 0xFF
                r[A] = a
                set_f((r[F] & keep) | (a & XYF) | (a & CF))
                return cost

            return rlca
        if y == 1:

            def rrca() -> int:
                a = r[A]
                c = a & 1
                a = (a >> 1) | (c << 7)
                r[A] = a
                set_f((r[F] & keep) | (a & XYF) | c)
                return cost

            return rrca
        if y == 2:

            def rla() -> int:
                a = r[A]
                res = ((a << 1) | (r[F] & CF)) & 0xFF
                r[A] = res
                set_f((r[F] & keep) | (res & XYF) | (a >> 7))
                return cost

            return rla
        if y == 3:

            def rra() -> int:
                a = r[A]
                res = (a >> 1) | ((r[F] & CF) << 7)
                r[A] = res
                set_f((r[F] & keep) | (res & XYF) | (a & 1))
                return cost

            return rra
        if y == 4:

            def daa() -> int:
                a = r[A]
                f = r[F]
                diff = 6 if (f & HF) or (a & 0x0F) > 9 else 0
                carry = f & CF
                if carry or a > 0x99:
                    diff |= 0x60
                    carry = CF
                if f & NF:
                    hf = HF if (f & HF) and (a & 0x0F) < 6 else 0
                    res = (a - diff) & 0xFF
                else:
                    hf = HF if (a & 0x0F) > 9 else 0
                    res = (a + diff) & 0xFF
                r[A] = res
                set_f(SZP[res] | hf | (f & NF) | carry)
                return cost

            return daa
        if y == 5:

            def cpl() -> int:
                a = r[A] ^ 0xFF
                r[A] = a
                set_f((r[F] & (keep | CF)) | HF | NF | (a & XYF))
                return cost

            return cpl
        if y == 6:

            def scf() -> int:
                f = r[F]
                set_f((f & keep) | CF | (((cpu._q_last ^ f) | r[A]) & XYF))
                return cost

            return scf

        def ccf() -> int:
            f = r[F]
            c = f & CF
            set_f((f & keep) | (HF if c else 0) | (c ^ CF) | (((cpu._q_last ^ f) | r[A]) & XYF))
            return cost

        return ccf

    # -- CB ------------------------------------------------------------------------------------

    def _cb_op(self, op: int) -> Handler:
        cpu = self.cpu
        r = self.r
        x, y, z = op >> 6, (op >> 3) & 7, op & 7
        cost = timing.cb(op)
        if x == 0:
            fn = self.rot(y)
            if z == 6:

                def rot_mem() -> int:
                    addr = r[H] << 8 | r[L]
                    cpu._wr(addr, fn(cpu._rd(addr)))
                    return cost

                return rot_mem

            def rot_r() -> int:
                r[z] = fn(r[z])
                return cost

            return rot_r
        if x == 1:
            bit_flags = self.bit_flags
            if z == 6:

                def bit_mem() -> int:
                    bit_flags(cpu._rd(r[H] << 8 | r[L]), y, cpu.wz >> 8)
                    return cost

                return bit_mem

            def bit_r() -> int:
                v = r[z]
                bit_flags(v, y, v)
                return cost

            return bit_r
        mask = 1 << y
        keep = 0xFF if x == 3 else 0xFF ^ mask
        set_bits = mask if x == 3 else 0
        if z == 6:

            def res_set_mem() -> int:
                addr = r[H] << 8 | r[L]
                cpu._wr(addr, (cpu._rd(addr) & keep) | set_bits)
                return cost

            return res_set_mem

        def res_set_r() -> int:
            r[z] = (r[z] & keep) | set_bits
            return cost

        return res_set_r

    # -- DD CB d op / FD CB d op ---------------------------------------------------------------

    def _index_cb_prefix(self, hi: int, lo: int) -> Handler:
        cpu = self.cpu
        r = self.r
        table = self.ddcb

        def index_cb() -> int:
            pc = cpu.pc
            d = cpu._rd(pc)  # displacement and opcode are read without M1
            op = cpu._rd((pc + 1) & 0xFFFF)
            cpu.pc = (pc + 2) & 0xFFFF
            addr = ((r[hi] << 8 | r[lo]) + (d ^ 0x80) - 0x80) & 0xFFFF
            cpu.wz = addr
            return table[op](addr)

        return index_cb

    def _index_cb_op(self, op: int) -> IndexHandler:
        cpu = self.cpu
        r = self.r
        x, y, z = op >> 6, (op >> 3) & 7, op & 7
        cost = timing.index_cb(op)
        if x == 1:
            bit_flags = self.bit_flags

            def bit_idx(addr: int) -> int:
                bit_flags(cpu._rd(addr), y, addr >> 8)
                return cost

            return bit_idx
        if x == 0:
            fn = self.rot(y)
        else:
            mask = 1 << y
            keep = 0xFF if x == 3 else 0xFF ^ mask
            set_bits = mask if x == 3 else 0

            def fn(v: int) -> int:
                return (v & keep) | set_bits

        if z == 6:

            def op_idx(addr: int) -> int:
                cpu._wr(addr, fn(cpu._rd(addr)))
                return cost

            return op_idx

        def op_idx_copy(addr: int) -> int:  # undocumented: result also stored in register z
            res = fn(cpu._rd(addr))
            cpu._wr(addr, res)
            r[z] = res
            return cost

        return op_idx_copy

    # -- ED ------------------------------------------------------------------------------------

    def _ed_op(self, op: int) -> Handler:
        cpu = self.cpu
        r = self.r
        set_f = self.set_f
        x, y, z = op >> 6, (op >> 3) & 7, op & 7
        p, q = y >> 1, y & 1
        cost = timing.ED[op]

        def nop() -> int:
            return cost

        if x == 2 and z <= 3 and y >= 4:
            return self._block_op(y, z, cost)
        if x != 1:
            return nop

        pair = ((B, C), (D, E), (H, L))

        def get_rp(p: int) -> Callable[[], int]:
            if p == 3:
                return lambda: cpu.sp
            ph, pl = pair[p]
            return lambda: r[ph] << 8 | r[pl]

        if z == 0:

            def in_r_c() -> int:
                bc = r[B] << 8 | r[C]
                v = cpu._in(bc)
                cpu.wz = (bc + 1) & 0xFFFF
                set_f((r[F] & CF) | SZP[v])
                if y != 6:  # ED 70: IN F,(C) only sets the flags
                    r[y] = v
                return cost

            return in_r_c
        if z == 1:

            def out_c_r() -> int:
                bc = r[B] << 8 | r[C]
                cpu._out(bc, r[y] if y != 6 else 0)  # ED 71: OUT (C),0 (NMOS)
                cpu.wz = (bc + 1) & 0xFFFF
                return cost

            return out_c_r
        if z == 2:
            getter = get_rp(p)
            if q == 0:

                def sbc_hl() -> int:
                    hl = r[H] << 8 | r[L]
                    v = getter()
                    res = hl - v - (r[F] & CF)
                    cpu.wz = (hl + 1) & 0xFFFF
                    w = res & 0xFFFF
                    set_f(
                        ((w >> 8) & (SF | XYF)) | (0 if w else ZF) | NF | ((res >> 16) & CF)
                        | (((hl ^ v ^ res) >> 8) & HF) | (((hl ^ v) & (hl ^ res) & 0x8000) >> 13)
                    )  # fmt: skip
                    r[H] = w >> 8
                    r[L] = w & 0xFF
                    return cost

                return sbc_hl

            def adc_hl() -> int:
                hl = r[H] << 8 | r[L]
                v = getter()
                res = hl + v + (r[F] & CF)
                cpu.wz = (hl + 1) & 0xFFFF
                w = res & 0xFFFF
                set_f(
                    ((w >> 8) & (SF | XYF)) | (0 if w else ZF) | ((res >> 16) & CF)
                    | (((hl ^ v ^ res) >> 8) & HF) | ((~(hl ^ v) & (hl ^ res) & 0x8000) >> 13)
                )  # fmt: skip
                r[H] = w >> 8
                r[L] = w & 0xFF
                return cost

            return adc_hl
        if z == 3:
            imm16 = self.imm16
            if p == 3:
                if q == 0:

                    def ld_nn_sp() -> int:
                        nn = imm16()
                        cpu._wr(nn, cpu.sp & 0xFF)
                        nn = (nn + 1) & 0xFFFF
                        cpu._wr(nn, cpu.sp >> 8)
                        cpu.wz = nn
                        return cost

                    return ld_nn_sp

                def ld_sp_nn() -> int:
                    nn = imm16()
                    lo_v = cpu._rd(nn)
                    nn = (nn + 1) & 0xFFFF
                    cpu.sp = lo_v | cpu._rd(nn) << 8
                    cpu.wz = nn
                    return cost

                return ld_sp_nn
            ph, pl = pair[p]
            if q == 0:

                def ld_nn_rp() -> int:
                    nn = imm16()
                    cpu._wr(nn, r[pl])
                    nn = (nn + 1) & 0xFFFF
                    cpu._wr(nn, r[ph])
                    cpu.wz = nn
                    return cost

                return ld_nn_rp

            def ld_rp_nn() -> int:
                nn = imm16()
                r[pl] = cpu._rd(nn)
                nn = (nn + 1) & 0xFFFF
                r[ph] = cpu._rd(nn)
                cpu.wz = nn
                return cost

            return ld_rp_nn
        if z == 4:
            sub = self.alu(2)

            def neg() -> int:
                v = r[A]
                r[A] = 0
                sub(v)
                return cost

            return neg
        if z == 5:
            pop16 = self.pop16

            def retn() -> int:  # RETN and RETI both copy IFF2 to IFF1
                cpu.pc = cpu.wz = pop16()
                cpu.iff1 = cpu.iff2
                return cost

            return retn
        if z == 6:
            mode = (0, 0, 1, 2)[y & 3]

            def im() -> int:
                cpu.im = mode
                return cost

            return im
        # z == 7
        if y == 0:

            def ld_i_a() -> int:
                cpu.i = r[A]
                return cost

            return ld_i_a
        if y == 1:

            def ld_r_a() -> int:
                cpu.r = r[A]
                return cost

            return ld_r_a
        if y in (2, 3):

            def ld_a_ir() -> int:
                a = cpu.i if y == 2 else cpu.r
                r[A] = a
                set_f((r[F] & CF) | SZ[a] | (PF if cpu.iff2 else 0))
                cpu.p = True
                return cost

            return ld_a_ir
        if y in (4, 5):
            rld = y == 5

            def rrd_rld() -> int:
                hl = r[H] << 8 | r[L]
                v = cpu._rd(hl)
                a = r[A]
                if rld:
                    cpu._wr(hl, ((v << 4) | (a & 0x0F)) & 0xFF)
                    a = (a & 0xF0) | (v >> 4)
                else:
                    cpu._wr(hl, ((a << 4) | (v >> 4)) & 0xFF)
                    a = (a & 0xF0) | (v & 0x0F)
                r[A] = a
                cpu.wz = (hl + 1) & 0xFFFF
                set_f((r[F] & CF) | SZP[a])
                return cost

            return rrd_rld
        return nop

    def _block_op(self, y: int, z: int, cost: int) -> Handler:
        """LDI/CPI/INI/OUTI and the D, IR and DR variants (y = 4..7, z = 0..3)."""
        cpu = self.cpu
        r = self.r
        set_f = self.set_f
        inc = 1 if y & 1 == 0 else -1
        repeat = y >= 6
        rep_cost = timing.ED_REPEAT

        def repeat_pc() -> int:
            """Rewind PC to the ED prefix; returns the PC for the X/Y flags of a repeat."""
            pc = (cpu.pc - 2) & 0xFFFF
            cpu.pc = pc
            cpu.wz = (pc + 1) & 0xFFFF
            return pc

        if z == 0:

            def ld_block() -> int:
                hl = r[H] << 8 | r[L]
                de = r[D] << 8 | r[E]
                v = cpu._rd(hl)
                cpu._wr(de, v)
                hl = (hl + inc) & 0xFFFF
                de = (de + inc) & 0xFFFF
                bc = ((r[B] << 8 | r[C]) - 1) & 0xFFFF
                r[H], r[L], r[D], r[E], r[B], r[C] = (
                    hl >> 8, hl & 0xFF, de >> 8, de & 0xFF, bc >> 8, bc & 0xFF
                )  # fmt: skip
                n = (v + r[A]) & 0xFF
                f = (r[F] & (SF | ZF | CF)) | (PF if bc else 0)
                if repeat and bc:
                    pc = repeat_pc()
                    set_f(f | ((pc >> 8) & XYF))
                    return rep_cost
                set_f(f | (n & XF) | ((n << 4) & YF))
                return cost

            return ld_block
        if z == 1:

            def cp_block() -> int:
                hl = r[H] << 8 | r[L]
                v = cpu._rd(hl)
                a = r[A]
                res = (a - v) & 0xFF
                hl = (hl + inc) & 0xFFFF
                bc = ((r[B] << 8 | r[C]) - 1) & 0xFFFF
                r[H], r[L], r[B], r[C] = hl >> 8, hl & 0xFF, bc >> 8, bc & 0xFF
                hf = (a ^ v ^ res) & HF
                n = (res - (1 if hf else 0)) & 0xFF
                cpu.wz = (cpu.wz + inc) & 0xFFFF
                f = (r[F] & CF) | NF | (SZ[res] & (SF | ZF)) | hf | (PF if bc else 0)
                if repeat and bc and res:
                    pc = repeat_pc()
                    set_f(f | ((pc >> 8) & XYF))
                    return rep_cost
                set_f(f | (n & XF) | ((n << 4) & YF))
                return cost

            return cp_block

        def io_flags(v: int, k: int, b: int) -> int:
            return (
                SZ[b] | ((v >> 6) & NF) | ((HF | CF) if k > 0xFF else 0)
                | PARITY[(k & 7) ^ b]
            )  # fmt: skip

        if z == 2:

            def in_block() -> int:
                bc = r[B] << 8 | r[C]
                v = cpu._in(bc)
                cpu.wz = (bc + inc) & 0xFFFF
                b = (r[B] - 1) & 0xFF
                r[B] = b
                hl = r[H] << 8 | r[L]
                cpu._wr(hl, v)
                hl = (hl + inc) & 0xFFFF
                r[H], r[L] = hl >> 8, hl & 0xFF
                k = v + ((r[C] + inc) & 0xFF)
                f = io_flags(v, k, b)
                if repeat and b:
                    set_f(_io_repeat_flags(f, v, k, b, repeat_pc()))
                    return rep_cost
                set_f(f)
                return cost

            return in_block

        def out_block() -> int:
            hl = r[H] << 8 | r[L]
            v = cpu._rd(hl)
            b = (r[B] - 1) & 0xFF
            r[B] = b
            bc = b << 8 | r[C]
            cpu._out(bc, v)
            cpu.wz = (bc + inc) & 0xFFFF
            hl = (hl + inc) & 0xFFFF
            r[H], r[L] = hl >> 8, hl & 0xFF
            k = v + r[L]
            f = io_flags(v, k, b)
            if repeat and b:
                set_f(_io_repeat_flags(f, v, k, b, repeat_pc()))
                return rep_cost
            set_f(f)
            return cost

        return out_block


def _io_repeat_flags(f: int, v: int, k: int, b: int, pc: int) -> int:
    """Flags of INIR/INDR/OTIR/OTDR when they repeat (undocumented, NMOS)."""
    f = (f & ~(XYF | HF | PF)) | ((pc >> 8) & XYF)
    pf = PARITY[(k & 7) ^ b]
    if k > 0xFF:
        if v & 0x80:
            pf ^= PARITY[(b - 1) & 7] ^ PF
            hf = HF if b & 0x0F == 0x00 else 0
        else:
            pf ^= PARITY[(b + 1) & 7] ^ PF
            hf = HF if b & 0x0F == 0x0F else 0
    else:
        pf ^= PARITY[b & 7] ^ PF
        hf = 0
    return f | pf | hf
