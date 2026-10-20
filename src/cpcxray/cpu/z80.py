# pylint: disable=too-many-lines  # one implementation of every Z80 instruction, kept together
"""Z80 CPU core (design.md §6.2): instruction-level, software compatible (FR-CPU-01..07).

Each `Z80.step()` executes one instruction (or accepts one interrupt) atomically and returns its
cost in CPC NOPs (µs, D-04). All bus accesses are issued in the order of the real Z80 and carry
their signals through the bus method used: opcode and prefix bytes via `fetch()` (M1),
everything else via `read()`/`write()`/`io_in()`/`io_out()`; `DD CB d op` reads *d* and *op*
without M1. Correct M1 attribution is what the descrambling of the VDOS ROM relies on (§5.5).

Structure
---------
* `Z80` holds the register file and the control logic (`step`, interrupts, NMI, HALT).
* `_TableBuilder` creates the opcode handler tables once per CPU instance. Every handler is a
  closure over the CPU and its register list. The handlers are created by decoding the opcode
  bits the standard way (see "Decoding Z80 Opcodes", z80.info):

      opcode bits   7 6 | 5 4 3 | 2 1 0
      field         group | bits_5_3 | bits_2_0
                          | pair_index (bits 5-4) + bit_3

  Depending on the group, `bits_5_3` selects a destination register, an ALU operation, a
  condition or a bit number, and `bits_2_0` a source register or a sub-operation; each factory
  gives the field a meaningful name where it is used. Register field value 6 means the memory
  operand (HL) (or (IX+d)/(IY+d)).
* The DD and FD tables are produced by the same factory with IX/IY substituted for HL, so there
  is exactly one implementation of every instruction.
* Every handler returns the instruction's cost in NOPs, looked up from `cpcxray.cpu.timing`
  when the handler is built.

Readability is preferred over speed: this implementation is the reference for a later port.

Verification: all 1.6 M SingleStepTests/z80 vectors pass (state, RAM, bus-access order and M1),
see `tests/cpu/`. Undocumented behaviour covered: X/Y flags, MEMPTR (WZ), the hidden flag
latch "Q" (SCF/CCF), IXH/IXL/IYH/IYL, SLL, DDCB/FDCB register copies, ED duplicates,
block-instruction repeat flags, OUT (C),0, the LD A,I/LD A,R P/V quirk on interrupt acceptance.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from cpcxray.cpu import timing
from cpcxray.cpu.bus import CpuBus
from cpcxray.cpu.tables import (
    FLAG_C,
    FLAG_H,
    FLAG_N,
    FLAG_PV,
    FLAG_S,
    FLAG_X,
    FLAG_Y,
    FLAG_Z,
    FLAGS_XY,
    PARITY,
    SIGN_ZERO_XY,
    SIGN_ZERO_XY_PARITY,
)

# Indices into Z80.byte_registers. 0-7 match the 3-bit register field of the opcodes, except
# that the field value 6 means the memory operand (HL) there and the list holds F at index 6.
B, C, D, E, H, L, F, A = range(8)
IXH, IXL, IYH, IYL = range(8, 12)
MEMORY_OPERAND = 6  # register field value that selects (HL) / (IX+d) / (IY+d)

type Handler = Callable[[], int]
"""An opcode handler: executes the instruction (opcode already fetched), returns NOPs."""

type IndexedBitHandler = Callable[[int], int]
"""A DD CB / FD CB handler: gets the effective address IX+d / IY+d, returns NOPs."""

REGISTER_NAMES = (
    "a", "f", "b", "c", "d", "e", "h", "l", "af", "bc", "de", "hl",
    "af_", "bc_", "de_", "hl_", "ix", "iy", "ixh", "ixl", "iyh", "iyl",
    "sp", "pc", "i", "r", "wz", "iff1", "iff2", "im", "ei_delay", "halted",
    "flags_written_by_last_instruction", "last_instruction_was_ld_a_i_or_r",
)  # fmt: skip
"""Names accepted by `Z80.registers()` / `Z80.set_registers()` (the debugger interface)."""


def _signed_byte(value: int) -> int:
    """Interpret an 8-bit value as two's complement (-128..127), e.g. a jump displacement."""
    return value - 256 if value & 0x80 else value


def _byte_register_property(index: int) -> property:
    """Create a property for the 8-bit register stored at `Z80.byte_registers[index]`.

    Args:
        index: Index into `Z80.byte_registers` (B, C, D, E, H, L, F, A, IXH, IXL, IYH, IYL).

    Returns:
        A property that reads the register and writes it masked to 8 bits.
    """

    def get_register(self: Z80) -> int:
        """Return the register value."""
        return self.byte_registers[index]

    def set_register(self: Z80, value: int) -> None:
        """Set the register, masked to 8 bits."""
        self.byte_registers[index] = value & 0xFF

    return property(get_register, set_register)


def _register_pair_property(high_index: int, low_index: int) -> property:
    """Create a property for a register pair made of two `Z80.byte_registers` entries.

    Args:
        high_index: Index of the high byte in `Z80.byte_registers`.
        low_index: Index of the low byte in `Z80.byte_registers`.

    Returns:
        A property that combines/splits the pair as a 16-bit value.
    """

    def get_pair(self: Z80) -> int:
        """Return the 16-bit pair value."""
        return self.byte_registers[high_index] << 8 | self.byte_registers[low_index]

    def set_pair(self: Z80, value: int) -> None:
        """Set both halves from a 16-bit value."""
        self.byte_registers[high_index] = (value >> 8) & 0xFF
        self.byte_registers[low_index] = value & 0xFF

    return property(get_pair, set_pair)


# A CPU has about 30 registers and state flags; each is an attribute on purpose.
class Z80:  # pylint: disable=too-many-instance-attributes
    """A Zilog Z80 (NMOS), executed one instruction per `step()`.

    Register storage: the 8-bit registers B C D E H L F A IXH IXL IYH IYL live in the list
    `byte_registers` (indices from the module constants); the opcode handlers access that list
    directly, so that DD/FD can substitute IXH/IXL for H/L by index. The named properties (`a`,
    `hl`, `ix`, ...) are for everybody else. Alternate registers (`af_` ... `hl_`), `sp`, `pc`,
    `wz` (MEMPTR), `i` and `r` are plain int attributes named after the hardware registers.

    Interrupt inputs are set by the machine: `int_line` (level triggered, maskable) and
    `nmi_line` (edge triggered). Both are only sampled at instruction boundaries (D-03).

    Attributes:
        byte_registers: The 8-bit register file, see above.
        sp: Stack pointer.
        pc: Program counter.
        wz: MEMPTR, the internal address latch (affects BIT n,(HL) X/Y flags).
        i: Interrupt vector register.
        r: Refresh register; bits 0-6 count M1 cycles, bit 7 only changes by LD R,A.
        af_: Alternate AF, as a 16-bit value. Likewise `bc_`, `de_`, `hl_`.
        iff1: Interrupt enable flip-flop 1 (interrupts accepted when set).
        iff2: Interrupt enable flip-flop 2 (copy kept during NMI; read by LD A,I/R).
        im: Interrupt mode 0, 1 or 2.
        bus: The bus all accesses go to. It may be replaced at any time; the handlers look
            its methods up on every access, so a bus that swaps in observed variants of its
            methods (D-12) needs no notification.
        flags_written_by_last_instruction: The value the last instruction wrote to F, or 0
            if it did not change the flags. Not a programmer-visible register: an internal
            latch of the Z80, called "Q" in emulator research and in the SingleStepTests.
            SCF and CCF compute their undocumented X/Y flags from it.
        flags_written_by_previous_instruction: `flags_written_by_last_instruction` of the
            instruction before the current one, saved by `step()` for SCF/CCF.
        last_instruction_was_ld_a_i_or_r: True right after LD A,I or LD A,R. Also internal
            state ("P" in the SingleStepTests): if an interrupt is accepted right after one of
            these instructions, the NMOS Z80 clears the P/V flag they just set.
        ei_delay: EI was just executed; no maskable interrupt before the next instruction.
        halted: The CPU executed HALT and idles until an interrupt.
        int_line: State of the INT input.
        nmi_pending: A falling NMI edge was seen and the NMI not yet accepted.
    """

    a = _byte_register_property(A)
    f = _byte_register_property(F)
    b = _byte_register_property(B)
    c = _byte_register_property(C)
    d = _byte_register_property(D)
    e = _byte_register_property(E)
    h = _byte_register_property(H)
    l = _byte_register_property(L)  # noqa: E741
    ixh = _byte_register_property(IXH)
    ixl = _byte_register_property(IXL)
    iyh = _byte_register_property(IYH)
    iyl = _byte_register_property(IYL)
    af = _register_pair_property(A, F)
    bc = _register_pair_property(B, C)
    de = _register_pair_property(D, E)
    hl = _register_pair_property(H, L)
    ix = _register_pair_property(IXH, IXL)
    iy = _register_pair_property(IYH, IYL)

    def __init__(self, bus: CpuBus) -> None:
        """Create a CPU connected to `bus`, build its opcode tables and reset it.

        Building the tables creates about 2000 closures; reuse CPU instances in loops.

        Args:
            bus: The bus the CPU issues all its accesses to.
        """
        self.byte_registers = [0] * 12
        self.sp = 0
        self.pc = 0
        self.wz = 0
        self.i = 0
        self.r = 0
        self.af_ = 0
        self.bc_ = 0
        self.de_ = 0
        self.hl_ = 0
        self.iff1 = False
        self.iff2 = False
        self.im = 0
        self.flags_written_by_last_instruction = 0
        self.last_instruction_was_ld_a_i_or_r = False
        self.ei_delay = False
        self.halted = False
        self.int_line = False
        self.nmi_pending = False
        self._nmi_line = False
        self.flags_written_by_previous_instruction = 0
        self.bus = bus
        tables = _TableBuilder(self)
        self._unprefixed_table = tables.unprefixed
        self.reset()

    # -- control -------------------------------------------------------------------------------

    def reset(self) -> None:
        """Hardware reset: PC, I, R = 0, interrupts disabled, IM 0, AF = SP = &FFFF.

        The other registers keep their values (they are undefined after power-on).
        """
        self.pc = 0
        self.i = 0
        self.r = 0
        self.iff1 = self.iff2 = False
        self.im = 0
        self.af = 0xFFFF
        self.sp = 0xFFFF
        self.wz = 0
        self.flags_written_by_last_instruction = 0
        self.last_instruction_was_ld_a_i_or_r = False
        self.ei_delay = False
        self.halted = False
        self.nmi_pending = False

    @property
    def nmi_line(self) -> bool:
        """State of the NMI input; a rising edge (inactive → active) latches an NMI."""
        return self._nmi_line

    @nmi_line.setter
    def nmi_line(self, active: bool) -> None:
        """Set the NMI input; an inactive → active transition makes an NMI pending."""
        if active and not self._nmi_line:  # NMI is edge triggered
            self.nmi_pending = True
        self._nmi_line = active

    def increment_refresh(self) -> None:
        """Count one M1 cycle in R: bits 0-6 increment and wrap, bit 7 is kept."""
        self.r = (self.r & 0x80) | ((self.r + 1) & 0x7F)

    def step(self) -> int:
        """Execute one instruction or accept one interrupt.

        Order of checks: a pending NMI is accepted first; then a maskable interrupt if INT is
        active, IFF1 is set and the previous instruction was not EI. Otherwise one instruction
        is fetched (M1) and executed; while halted, the CPU only performs a dummy M1 fetch.
        Prefixed instructions (CB, DD, ED, FD, DD CB) execute completely within one step.

        Returns:
            The cost in µs (CPC NOPs, D-04).
        """
        if self.nmi_pending:
            return self._accept_nmi()
        if self.ei_delay:
            self.ei_delay = False
        elif self.int_line and self.iff1:
            return self._accept_interrupt()
        self.flags_written_by_previous_instruction = self.flags_written_by_last_instruction
        self.flags_written_by_last_instruction = 0
        self.last_instruction_was_ld_a_i_or_r = False
        self.increment_refresh()
        if self.halted:
            self.bus.fetch(self.pc)  # the CPU keeps fetching (and ignoring) the next opcode
            return timing.HALT_IDLE
        opcode = self.bus.fetch(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return self._unprefixed_table[opcode]()

    def push_word(self, value: int) -> None:
        """Push a 16-bit value: write the high byte to SP-1, then the low byte to SP-2."""
        stack_pointer = (self.sp - 1) & 0xFFFF
        self.bus.write(stack_pointer, value >> 8)
        stack_pointer = (stack_pointer - 1) & 0xFFFF
        self.bus.write(stack_pointer, value & 0xFF)
        self.sp = stack_pointer

    def _accept_interrupt(self) -> int:
        """Accept a maskable interrupt in the current interrupt mode.

        Leaves HALT, clears IFF1/IFF2, increments R (the acknowledge is an M1 cycle) and reads
        the data bus via `int_ack()`. IM 1 calls &0038; IM 2 calls the address read from the
        vector table at I*256 + data byte; IM 0 executes the data byte as an instruction
        (in practice an RST; multi-byte instructions are not supported).

        Returns:
            The cost in NOPs.
        """
        self.halted = False
        self.iff1 = self.iff2 = False
        if (
            self.last_instruction_was_ld_a_i_or_r
        ):  # NMOS: LD A,I / LD A,R interrupted reports P/V = 0
            self.byte_registers[F] &= ~FLAG_PV
        self.last_instruction_was_ld_a_i_or_r = False
        self.flags_written_by_last_instruction = 0
        self.increment_refresh()
        data_bus = self.bus.int_ack()
        if self.im == 2:
            self.push_word(self.pc)
            vector_address = (self.i << 8) | data_bus
            low_byte = self.bus.read(vector_address)
            high_byte = self.bus.read((vector_address + 1) & 0xFFFF)
            self.pc = self.wz = high_byte << 8 | low_byte
            return timing.INT_IM2
        if self.im == 1:
            self.push_word(self.pc)
            self.pc = self.wz = 0x38
            return timing.INT_IM1
        # IM 0: execute the byte on the data bus as an instruction (in practice an RST)
        self._unprefixed_table[data_bus]()
        return timing.INT_IM0

    def _accept_nmi(self) -> int:
        """Accept a pending NMI: leave HALT, clear IFF1 (IFF2 keeps the old state), call &0066.

        No M1 fetch is issued for the NMI acknowledge cycle (the CPC does not use NMI).

        Returns:
            The cost in NOPs.
        """
        self.nmi_pending = False
        self.halted = False
        self.iff1 = False
        self.last_instruction_was_ld_a_i_or_r = False
        self.flags_written_by_last_instruction = 0
        self.increment_refresh()
        self.push_word(self.pc)
        self.pc = self.wz = 0x66
        return timing.NMI

    # -- debugger access (FR-CPU-06) -------------------------------------------------------------

    def registers(self) -> dict[str, int]:
        """Return all registers and CPU state flags by name (see `REGISTER_NAMES`).

        Booleans (iff1, iff2, ei_delay, halted) are returned as 0/1.
        """
        return {name: int(getattr(self, name)) for name in REGISTER_NAMES}

    def set_registers(self, values: Mapping[str, int | bool]) -> None:
        """Set registers by name; values are masked to the register width.

        Args:
            values: Mapping of register name (see `REGISTER_NAMES`) to new value.

        Raises:
            KeyError: For an unknown register name.
        """
        for name, value in values.items():
            if name not in REGISTER_NAMES:
                raise KeyError(f"unknown register {name!r}")
            if name in ("iff1", "iff2", "ei_delay", "halted", "last_instruction_was_ld_a_i_or_r"):
                setattr(self, name, bool(value))
            elif name in ("pc", "sp", "wz", "af_", "bc_", "de_", "hl_"):
                setattr(self, name, int(value) & 0xFFFF)
            elif name in ("i", "r", "flags_written_by_last_instruction"):
                setattr(self, name, int(value) & 0xFF)
            else:
                setattr(self, name, int(value))


# Condition codes, indexed by the 3-bit condition field: NZ, Z, NC, C, PO, PE, P, M.
# Each entry is (flag tested, required flag state).
_CONDITIONS = (
    (FLAG_Z, False),  # NZ
    (FLAG_Z, True),  # Z
    (FLAG_C, False),  # NC
    (FLAG_C, True),  # C
    (FLAG_PV, False),  # PO (parity odd)
    (FLAG_PV, True),  # PE (parity even)
    (FLAG_S, False),  # P (plus)
    (FLAG_S, True),  # M (minus)
)


# Each factory method is one decision tree over the opcode bits that defines and returns one
# small handler closure per case; pylint counts every closure as a local and every case as a
# return/branch. Splitting the trees would scatter the instruction set without making any
# single instruction easier to read, so these size checks are disabled for this class only.
class _TableBuilder:  # pylint: disable=too-many-locals,too-many-return-statements,too-many-branches,too-many-statements,too-many-instance-attributes
    """Builds the opcode handler tables for one CPU instance.

    Tables (each a list of 256 handlers, indexed by opcode):

    * `unprefixed`: unprefixed opcodes; CB, DD, ED and FD entries dispatch to the tables below.
    * `cb_table`: CB-prefixed opcodes (rotate/shift, BIT, RES, SET).
    * `ed_table`: ED-prefixed opcodes (undefined ones are 2-NOP no-ops).
    * `dd_table` / `fd_table`: like `unprefixed` with IX / IY replacing HL (and (IX+d)
      replacing (HL)).
    * `indexed_cb_table`: DD CB d op / FD CB d op, shared by IX and IY; handlers take the
      effective address.

    Conventions inside the factories: `cpu` is the Z80, `registers` its `byte_registers` list,
    `nops` the cost of the instruction (and `nops_if_taken` the cost when a condition is met).
    Factories for the HL-like pair get `pair_high`/`pair_low`, the indices of H/L, IXH/IXL or
    IYH/IYL.
    """

    def __init__(self, cpu: Z80) -> None:
        """Build all handler tables for `cpu`.

        Args:
            cpu: The CPU whose registers and bus the handlers operate on.
        """
        self.cpu = cpu
        self.registers = cpu.byte_registers
        self.cb_table = [self._make_cb_handler(opcode) for opcode in range(256)]
        self.ed_table = [self._make_ed_handler(opcode) for opcode in range(256)]
        self.indexed_cb_table = [self._make_indexed_cb_handler(opcode) for opcode in range(256)]
        self.unprefixed: list[Handler] = [
            self._make_handler(opcode, H, L, indexed=False) for opcode in range(256)
        ]
        self.dd_table: list[Handler] = [
            self._make_handler(opcode, IXH, IXL, indexed=True) for opcode in range(256)
        ]
        self.fd_table: list[Handler] = [
            self._make_handler(opcode, IYH, IYL, indexed=True) for opcode in range(256)
        ]

    # -- shared helpers ------------------------------------------------------------------------

    def read_immediate_byte(self) -> int:
        """Read the byte at PC as an operand (no M1) and advance PC."""
        cpu = self.cpu
        value = cpu.bus.read(cpu.pc)
        cpu.pc = (cpu.pc + 1) & 0xFFFF
        return value

    def read_immediate_word(self) -> int:
        """Read the little-endian word at PC as an operand (no M1) and advance PC by 2."""
        low_byte = self.read_immediate_byte()
        high_byte = self.read_immediate_byte()
        return high_byte << 8 | low_byte

    def fetch_opcode(self) -> int:
        """Fetch the opcode byte after a prefix with M1, advance PC and increment R."""
        cpu = self.cpu
        opcode = cpu.bus.fetch(cpu.pc)
        cpu.pc = (cpu.pc + 1) & 0xFFFF
        cpu.increment_refresh()
        return opcode

    def pop_word(self) -> int:
        """Pop a 16-bit value: read the low byte at SP, then the high byte at SP+1."""
        cpu = self.cpu
        low_byte = cpu.bus.read(cpu.sp)
        high_byte = cpu.bus.read((cpu.sp + 1) & 0xFFFF)
        cpu.sp = (cpu.sp + 2) & 0xFFFF
        return high_byte << 8 | low_byte

    def set_flags(self, flags: int) -> None:
        """Store new flags and remember them in `flags_written_by_last_instruction`."""
        self.registers[F] = flags
        self.cpu.flags_written_by_last_instruction = flags

    def condition_test(self, condition: int) -> Callable[[], bool]:
        """Return a test for a condition code.

        Args:
            condition: 0 NZ, 1 Z, 2 NC, 3 C, 4 PO, 5 PE, 6 P, 7 M.

        Returns:
            A function that returns True if the condition currently holds.
        """
        registers = self.registers
        flag, required_state = _CONDITIONS[condition]

        def condition_holds() -> bool:
            """Return True if the tested flag has the required state."""
            return bool(registers[F] & flag) == required_state

        return condition_holds

    # 8-bit ALU on A ----------------------------------------------------------------------------

    def alu_operation(self, operation: int) -> Callable[[int], None]:
        """Return the 8-bit ALU operation that combines A with an operand.

        Args:
            operation: From the opcode: 0 ADD, 1 ADC, 2 SUB, 3 SBC, 4 AND, 5 XOR, 6 OR, 7 CP.

        Returns:
            A function taking the operand value; it updates A (except CP) and the flags.
        """
        registers = self.registers
        set_flags = self.set_flags

        def add_flags(accumulator: int, operand: int, result: int) -> int:
            """Flags of an 8-bit addition (`result` may exceed 255)."""
            half_carry = (accumulator ^ operand ^ result) & FLAG_H
            overflow = FLAG_PV if ~(accumulator ^ operand) & (accumulator ^ result) & 0x80 else 0
            carry = FLAG_C if result > 0xFF else 0
            return SIGN_ZERO_XY[result & 0xFF] | half_carry | overflow | carry

        def subtract_flags(accumulator: int, operand: int, result: int) -> int:
            """Flags of an 8-bit subtraction (`result` may be negative)."""
            half_borrow = (accumulator ^ operand ^ result) & FLAG_H
            overflow = FLAG_PV if (accumulator ^ operand) & (accumulator ^ result) & 0x80 else 0
            borrow = FLAG_C if result < 0 else 0
            return SIGN_ZERO_XY[result & 0xFF] | FLAG_N | half_borrow | overflow | borrow

        def add(operand: int) -> None:
            """ADD A,v: S Z X Y from the result, H half carry, V overflow, N = 0, C carry."""
            accumulator = registers[A]
            result = accumulator + operand
            registers[A] = result & 0xFF
            set_flags(add_flags(accumulator, operand, result))

        def add_with_carry(operand: int) -> None:
            """ADC A,v: like ADD, plus the carry flag."""
            accumulator = registers[A]
            result = accumulator + operand + (registers[F] & FLAG_C)
            registers[A] = result & 0xFF
            set_flags(add_flags(accumulator, operand, result))

        def subtract(operand: int) -> None:
            """SUB v: S Z X Y from the result, H half borrow, V overflow, N = 1, C borrow."""
            accumulator = registers[A]
            result = accumulator - operand
            registers[A] = result & 0xFF
            set_flags(subtract_flags(accumulator, operand, result))

        def subtract_with_carry(operand: int) -> None:
            """SBC A,v: like SUB, minus the carry flag."""
            accumulator = registers[A]
            result = accumulator - operand - (registers[F] & FLAG_C)
            registers[A] = result & 0xFF
            set_flags(subtract_flags(accumulator, operand, result))

        def logical_and(operand: int) -> None:
            """AND v: S Z X Y P from the result, H = 1, N = C = 0."""
            result = registers[A] & operand
            registers[A] = result
            set_flags(SIGN_ZERO_XY_PARITY[result] | FLAG_H)

        def logical_xor(operand: int) -> None:
            """XOR v: S Z X Y P from the result, H = N = C = 0."""
            result = registers[A] ^ operand
            registers[A] = result
            set_flags(SIGN_ZERO_XY_PARITY[result])

        def logical_or(operand: int) -> None:
            """OR v: S Z X Y P from the result, H = N = C = 0."""
            result = registers[A] | operand
            registers[A] = result
            set_flags(SIGN_ZERO_XY_PARITY[result])

        def compare(operand: int) -> None:
            """CP v: flags of SUB v without changing A; X and Y come from the operand."""
            accumulator = registers[A]
            flags = subtract_flags(accumulator, operand, accumulator - operand)
            set_flags((flags & ~FLAGS_XY) | (operand & FLAGS_XY))

        operations = (
            add, add_with_carry, subtract, subtract_with_carry,
            logical_and, logical_xor, logical_or, compare,
        )  # fmt: skip
        return operations[operation]

    def increment_byte(self, value: int) -> int:
        """INC: return value+1 and set S Z X Y H, V (value was &7F), N = 0; C unchanged."""
        result = (value + 1) & 0xFF
        half_carry = FLAG_H if value & 0x0F == 0x0F else 0
        overflow = FLAG_PV if value == 0x7F else 0
        self.set_flags((self.registers[F] & FLAG_C) | SIGN_ZERO_XY[result] | half_carry | overflow)
        return result

    def decrement_byte(self, value: int) -> int:
        """DEC: return value-1 and set S Z X Y H, V (value was &80), N = 1; C unchanged."""
        result = (value - 1) & 0xFF
        half_borrow = FLAG_H if value & 0x0F == 0 else 0
        overflow = FLAG_PV if value == 0x80 else 0
        self.set_flags(
            (self.registers[F] & FLAG_C) | FLAG_N | SIGN_ZERO_XY[result] | half_borrow | overflow
        )
        return result

    def rotate_shift_operation(self, operation: int) -> Callable[[int], int]:
        """Return a CB rotate/shift operation.

        All of them set S Z X Y P from the result, H = N = 0 and C from the bit shifted out.

        Args:
            operation: 0 RLC, 1 RRC, 2 RL, 3 RR, 4 SLA, 5 SRA, 6 SLL (undocumented), 7 SRL.

        Returns:
            A function taking the operand; it sets the flags and returns the result.
        """
        registers = self.registers
        set_flags = self.set_flags

        def finish(result: int, carry_out: int) -> int:
            """Set the flags for `result` and the shifted-out bit; return the result."""
            set_flags(SIGN_ZERO_XY_PARITY[result] | (FLAG_C if carry_out else 0))
            return result

        def rotate_left_circular(value: int) -> int:
            """RLC: bit 7 goes to bit 0 and to C."""
            return finish(((value << 1) | (value >> 7)) & 0xFF, value & 0x80)

        def rotate_right_circular(value: int) -> int:
            """RRC: bit 0 goes to bit 7 and to C."""
            return finish((value >> 1) | ((value & 1) << 7), value & 1)

        def rotate_left(value: int) -> int:
            """RL: rotate left through carry."""
            carry_in = registers[F] & FLAG_C
            return finish(((value << 1) | carry_in) & 0xFF, value & 0x80)

        def rotate_right(value: int) -> int:
            """RR: rotate right through carry."""
            carry_in = registers[F] & FLAG_C
            return finish((value >> 1) | (carry_in << 7), value & 1)

        def shift_left_arithmetic(value: int) -> int:
            """SLA: shift left, bit 0 = 0."""
            return finish((value << 1) & 0xFF, value & 0x80)

        def shift_right_arithmetic(value: int) -> int:
            """SRA: shift right, bit 7 is kept."""
            return finish((value >> 1) | (value & 0x80), value & 1)

        def shift_left_logical(value: int) -> int:
            """SLL (undocumented): shift left, bit 0 = 1."""
            return finish(((value << 1) | 1) & 0xFF, value & 0x80)

        def shift_right_logical(value: int) -> int:
            """SRL: shift right, bit 7 = 0."""
            return finish(value >> 1, value & 1)

        operations = (
            rotate_left_circular, rotate_right_circular, rotate_left, rotate_right,
            shift_left_arithmetic, shift_right_arithmetic, shift_left_logical,
            shift_right_logical,
        )  # fmt: skip
        return operations[operation]

    def set_bit_test_flags(self, value: int, bit_number: int, xy_source: int) -> None:
        """Set the flags of BIT bit_number,value.

        Z and P/V are set if the bit is 0, S if bit 7 is tested and set, H = 1, N = 0,
        C unchanged.

        Args:
            value: The tested value.
            bit_number: The bit number 0-7.
            xy_source: Source of the undocumented X/Y flags: the operand for registers, the
                high byte of MEMPTR for BIT n,(HL), the high byte of IX+d for indexed forms.
        """
        tested_bit = value & (1 << bit_number)
        zero_flags = 0 if tested_bit else FLAG_Z | FLAG_PV
        sign_flag = tested_bit & FLAG_S  # only non-zero when bit 7 is tested and set
        self.set_flags(
            (self.registers[F] & FLAG_C) | FLAG_H | (xy_source & FLAGS_XY) | zero_flags | sign_flag
        )

    # -- unprefixed / DD / FD ------------------------------------------------------------------

    def _make_handler(self, opcode: int, pair_high: int, pair_low: int, indexed: bool) -> Handler:
        """Create the handler for an unprefixed opcode, or its DD/FD form.

        With `indexed`, HL is replaced by IX/IY (`pair_high`/`pair_low`), the H and L register
        operands by IXH/IXL or IYH/IYL, and (HL) by (IX+d)/(IY+d). Instructions that use
        (IX+d) together with H or L use the real H/L register (e.g. LD H,(IX+d)). EX DE,HL,
        EXX and ED instructions are never affected. Opcodes that do not involve HL behave as
        unprefixed, at one extra NOP.

        Args:
            opcode: The opcode byte.
            pair_high: Index of the high byte of the HL-like pair in `byte_registers`.
            pair_low: Index of the low byte of the HL-like pair in `byte_registers`.
            indexed: True for the DD/FD tables.

        Returns:
            The handler.
        """
        cpu = self.cpu
        registers = self.registers
        read_immediate_byte = self.read_immediate_byte
        read_immediate_word = self.read_immediate_word
        set_flags = self.set_flags
        group, bits_5_3, bits_2_0 = opcode >> 6, (opcode >> 3) & 7, opcode & 7
        pair_index, bit_3 = bits_5_3 >> 1, bits_5_3 & 1
        nops = timing.MAIN[opcode] + (timing.index_extra(opcode) if indexed else 0)
        nops_if_taken = timing.MAIN_TAKEN.get(opcode, timing.MAIN[opcode]) + (1 if indexed else 0)
        # register field → index in byte_registers (H/L become IXH/IXL or IYH/IYL under DD/FD);
        # entry 6 is never used as a register, it selects the memory operand
        register_for_field = (B, C, D, E, pair_high, pair_low, F, A)

        if indexed:

            def memory_operand_address() -> int:
                """Read the displacement d (no M1) and return IX+d / IY+d; sets MEMPTR."""
                displacement = _signed_byte(read_immediate_byte())
                index_register = registers[pair_high] << 8 | registers[pair_low]
                address = (index_register + displacement) & 0xFFFF
                cpu.wz = address
                return address
        else:

            def memory_operand_address() -> int:
                """Return HL, the address of the (HL) operand."""
                return registers[H] << 8 | registers[L]

        register_pairs = ((B, C), (D, E), (pair_high, pair_low))  # pair index 3 is SP or AF

        def pair_getter(pair: int) -> Callable[[], int]:
            """Return a getter for a register pair: 0 BC, 1 DE, 2 HL/IX/IY, 3 SP."""
            if pair == 3:
                return lambda: cpu.sp
            high, low = register_pairs[pair]
            return lambda: registers[high] << 8 | registers[low]

        def pair_setter(pair: int) -> Callable[[int], None]:
            """Return a setter for a register pair: 0 BC, 1 DE, 2 HL/IX/IY, 3 SP."""
            if pair == 3:

                def set_stack_pointer(value: int) -> None:
                    """Set SP."""
                    cpu.sp = value

                return set_stack_pointer
            high, low = register_pairs[pair]

            def set_register_pair(value: int) -> None:
                """Set both halves of the pair from a 16-bit value."""
                registers[high] = value >> 8
                registers[low] = value & 0xFF

            return set_register_pair

        def relative_jump(displacement_byte: int) -> None:
            """Jump to PC + signed displacement (PC already points after the instruction)."""
            cpu.pc = cpu.wz = (cpu.pc + _signed_byte(displacement_byte)) & 0xFFFF

        if group == 0:
            if bits_2_0 == 0:
                if bits_5_3 == 0:
                    return lambda: nops  # NOP
                if bits_5_3 == 1:

                    def exchange_af_with_alternate() -> int:
                        """EX AF,AF': swap AF with the alternate AF."""
                        current_af = registers[A] << 8 | registers[F]
                        registers[A] = cpu.af_ >> 8
                        registers[F] = cpu.af_ & 0xFF
                        cpu.af_ = current_af
                        return nops

                    return exchange_af_with_alternate
                if bits_5_3 == 2:

                    def decrement_b_and_jump_if_not_zero() -> int:
                        """DJNZ d: decrement B, jump relative if B != 0 (MEMPTR = target)."""
                        displacement_byte = read_immediate_byte()
                        new_b = (registers[B] - 1) & 0xFF
                        registers[B] = new_b
                        if new_b != 0:
                            relative_jump(displacement_byte)
                            return nops_if_taken
                        return nops

                    return decrement_b_and_jump_if_not_zero
                if bits_5_3 == 3:

                    def jump_relative() -> int:
                        """JR d: jump relative (MEMPTR = target)."""
                        relative_jump(read_immediate_byte())
                        return nops

                    return jump_relative
                condition_holds = self.condition_test(bits_5_3 - 4)  # NZ, Z, NC, C only

                def jump_relative_if() -> int:
                    """JR cc,d: the displacement is always read."""
                    displacement_byte = read_immediate_byte()
                    if condition_holds():
                        relative_jump(displacement_byte)
                        return nops_if_taken
                    return nops

                return jump_relative_if
            if bits_2_0 == 1:
                set_pair = pair_setter(pair_index)
                if bit_3 == 0:

                    def load_pair_immediate() -> int:
                        """LD rr,nn (BC, DE, HL/IX/IY, SP)."""
                        set_pair(read_immediate_word())
                        return nops

                    return load_pair_immediate
                get_pair = pair_getter(pair_index)

                def add_pair_to_hl() -> int:
                    """ADD HL,rr (or IX/IY): H, C and X/Y from the high byte; S Z P/V kept.

                    MEMPTR = HL + 1 (value before the addition).
                    """
                    hl_value = registers[pair_high] << 8 | registers[pair_low]
                    operand = get_pair()
                    result = hl_value + operand
                    cpu.wz = (hl_value + 1) & 0xFFFF
                    half_carry = ((hl_value ^ operand ^ result) >> 8) & FLAG_H  # from bit 11
                    carry = FLAG_C if result > 0xFFFF else 0
                    kept_flags = registers[F] & (FLAG_S | FLAG_Z | FLAG_PV)
                    set_flags(kept_flags | half_carry | carry | ((result >> 8) & FLAGS_XY))
                    registers[pair_high] = (result >> 8) & 0xFF
                    registers[pair_low] = result & 0xFF
                    return nops

                return add_pair_to_hl
            if bits_2_0 == 2:
                if pair_index < 2:  # (BC) or (DE)
                    address_high, address_low = register_pairs[pair_index]
                    if bit_3 == 0:

                        def store_a_indirect() -> int:
                            """LD (BC),A / LD (DE),A: MEMPTR = A:(address+1 low byte)."""
                            address = registers[address_high] << 8 | registers[address_low]
                            accumulator = registers[A]
                            cpu.bus.write(address, accumulator)
                            cpu.wz = (accumulator << 8) | ((address + 1) & 0xFF)
                            return nops

                        return store_a_indirect

                    def load_a_indirect() -> int:
                        """LD A,(BC) / LD A,(DE): MEMPTR = address + 1."""
                        address = registers[address_high] << 8 | registers[address_low]
                        registers[A] = cpu.bus.read(address)
                        cpu.wz = (address + 1) & 0xFFFF
                        return nops

                    return load_a_indirect
                if pair_index == 2:
                    if bit_3 == 0:

                        def store_hl_direct() -> int:
                            """LD (nn),HL/IX/IY: write low byte, then high; MEMPTR = nn + 1."""
                            address = read_immediate_word()
                            cpu.bus.write(address, registers[pair_low])
                            address = (address + 1) & 0xFFFF
                            cpu.bus.write(address, registers[pair_high])
                            cpu.wz = address
                            return nops

                        return store_hl_direct

                    def load_hl_direct() -> int:
                        """LD HL/IX/IY,(nn): read low byte, then high; MEMPTR = nn + 1."""
                        address = read_immediate_word()
                        registers[pair_low] = cpu.bus.read(address)
                        address = (address + 1) & 0xFFFF
                        registers[pair_high] = cpu.bus.read(address)
                        cpu.wz = address
                        return nops

                    return load_hl_direct
                if bit_3 == 0:

                    def store_a_direct() -> int:
                        """LD (nn),A: MEMPTR = A:(nn+1 low byte)."""
                        address = read_immediate_word()
                        accumulator = registers[A]
                        cpu.bus.write(address, accumulator)
                        cpu.wz = (accumulator << 8) | ((address + 1) & 0xFF)
                        return nops

                    return store_a_direct

                def load_a_direct() -> int:
                    """LD A,(nn): MEMPTR = nn + 1."""
                    address = read_immediate_word()
                    registers[A] = cpu.bus.read(address)
                    cpu.wz = (address + 1) & 0xFFFF
                    return nops

                return load_a_direct
            if bits_2_0 == 3:
                get_pair = pair_getter(pair_index)
                set_pair = pair_setter(pair_index)
                step_value = 1 if bit_3 == 0 else -1

                def increment_or_decrement_pair() -> int:
                    """INC rr / DEC rr: no flags affected."""
                    set_pair((get_pair() + step_value) & 0xFFFF)
                    return nops

                return increment_or_decrement_pair
            if bits_2_0 in (4, 5):
                change_value = self.increment_byte if bits_2_0 == 4 else self.decrement_byte
                if bits_5_3 == MEMORY_OPERAND:

                    def increment_or_decrement_memory() -> int:
                        """INC/DEC (HL) or (IX+d): read, modify, write back."""
                        address = memory_operand_address()
                        cpu.bus.write(address, change_value(cpu.bus.read(address)))
                        return nops

                    return increment_or_decrement_memory
                target_register = register_for_field[bits_5_3]

                def increment_or_decrement_register() -> int:
                    """INC r / DEC r (including IXH/IXL/IYH/IYL)."""
                    registers[target_register] = change_value(registers[target_register])
                    return nops

                return increment_or_decrement_register
            if bits_2_0 == 6:
                if bits_5_3 == MEMORY_OPERAND:

                    def load_memory_immediate() -> int:
                        """LD (HL),n / LD (IX+d),n: d is read before n."""
                        address = memory_operand_address()
                        cpu.bus.write(address, read_immediate_byte())
                        return nops

                    return load_memory_immediate
                target_register = register_for_field[bits_5_3]

                def load_register_immediate() -> int:
                    """LD r,n (including IXH/IXL/IYH/IYL)."""
                    registers[target_register] = read_immediate_byte()
                    return nops

                return load_register_immediate
            return self._make_accumulator_handler(bits_5_3, nops)

        if group == 1:
            destination_field, source_field = bits_5_3, bits_2_0
            if opcode == 0x76:

                def halt() -> int:
                    """HALT: PC already points past HALT; the CPU idles until an interrupt."""
                    cpu.halted = True
                    return nops

                return halt
            if source_field == MEMORY_OPERAND:

                def load_register_from_memory() -> int:
                    """LD r,(HL) / LD r,(IX+d): the destination is always the real register."""
                    registers[destination_field] = cpu.bus.read(memory_operand_address())
                    return nops

                return load_register_from_memory
            if destination_field == MEMORY_OPERAND:

                def store_register_to_memory() -> int:
                    """LD (HL),r / LD (IX+d),r: the source is always the real register."""
                    cpu.bus.write(memory_operand_address(), registers[source_field])
                    return nops

                return store_register_to_memory
            destination_register = register_for_field[destination_field]
            source_register = register_for_field[source_field]

            def load_register_from_register() -> int:
                """LD r,r' (including IXH/IXL/IYH/IYL under DD/FD)."""
                registers[destination_register] = registers[source_register]
                return nops

            return load_register_from_register

        if group == 2:
            alu = self.alu_operation(bits_5_3)
            if bits_2_0 == MEMORY_OPERAND:

                def alu_with_memory() -> int:
                    """ALU op with (HL) / (IX+d): ADD ADC SUB SBC AND XOR OR CP."""
                    alu(cpu.bus.read(memory_operand_address()))
                    return nops

                return alu_with_memory
            source_register = register_for_field[bits_2_0]

            def alu_with_register() -> int:
                """ALU op with a register: ADD ADC SUB SBC AND XOR OR CP."""
                alu(registers[source_register])
                return nops

            return alu_with_register

        # group == 3
        pop_word = self.pop_word
        push_word = cpu.push_word
        if bits_2_0 == 0:
            condition_holds = self.condition_test(bits_5_3)

            def return_if() -> int:
                """RET cc: return if the condition holds (MEMPTR = return address)."""
                if condition_holds():
                    cpu.pc = cpu.wz = pop_word()
                    return nops_if_taken
                return nops

            return return_if
        if bits_2_0 == 1:
            if bit_3 == 0:
                if pair_index == 3:

                    def pop_af() -> int:
                        """POP AF (loads F, but does not count as a flag change for SCF/CCF)."""
                        value = pop_word()
                        registers[A] = value >> 8
                        registers[F] = value & 0xFF
                        return nops

                    return pop_af
                set_pair = pair_setter(pair_index)

                def pop_pair() -> int:
                    """POP BC / DE / HL / IX / IY."""
                    set_pair(pop_word())
                    return nops

                return pop_pair
            if pair_index == 0:

                def return_from_call() -> int:
                    """RET (MEMPTR = return address)."""
                    cpu.pc = cpu.wz = pop_word()
                    return nops

                return return_from_call
            if pair_index == 1:

                def exchange_with_alternate_set() -> int:
                    """EXX: swap BC, DE, HL with the alternate set (never IX/IY)."""
                    current_bc = registers[B] << 8 | registers[C]
                    current_de = registers[D] << 8 | registers[E]
                    current_hl = registers[H] << 8 | registers[L]
                    registers[B], registers[C] = cpu.bc_ >> 8, cpu.bc_ & 0xFF
                    registers[D], registers[E] = cpu.de_ >> 8, cpu.de_ & 0xFF
                    registers[H], registers[L] = cpu.hl_ >> 8, cpu.hl_ & 0xFF
                    cpu.bc_, cpu.de_, cpu.hl_ = current_bc, current_de, current_hl
                    return nops

                return exchange_with_alternate_set
            if pair_index == 2:

                def jump_to_hl() -> int:
                    """JP (HL) / JP (IX) / JP (IY): MEMPTR unchanged."""
                    cpu.pc = registers[pair_high] << 8 | registers[pair_low]
                    return nops

                return jump_to_hl

            def load_sp_from_hl() -> int:
                """LD SP,HL / LD SP,IX / LD SP,IY."""
                cpu.sp = registers[pair_high] << 8 | registers[pair_low]
                return nops

            return load_sp_from_hl
        if bits_2_0 == 2:
            condition_holds = self.condition_test(bits_5_3)

            def jump_if() -> int:
                """JP cc,nn: the address is always read; MEMPTR = nn in both cases."""
                target_address = read_immediate_word()
                cpu.wz = target_address
                if condition_holds():
                    cpu.pc = target_address
                return nops

            return jump_if
        if bits_2_0 == 3:
            if bits_5_3 == 0:

                def jump() -> int:
                    """JP nn (MEMPTR = nn)."""
                    cpu.pc = cpu.wz = read_immediate_word()
                    return nops

                return jump
            if bits_5_3 == 1:
                if indexed:
                    return self._make_indexed_cb_prefix_handler(pair_high, pair_low)
                cb_table = self.cb_table
                fetch_opcode = self.fetch_opcode

                def cb_prefix() -> int:
                    """CB prefix: fetch the next opcode with M1 and run it from the CB table."""
                    return cb_table[fetch_opcode()]()

                return cb_prefix
            if bits_5_3 == 2:

                def output_a_to_port() -> int:
                    """OUT (n),A: port = A:n; MEMPTR = A:(n+1 low byte)."""
                    port_low = read_immediate_byte()
                    accumulator = registers[A]
                    cpu.bus.io_out(accumulator << 8 | port_low, accumulator)
                    cpu.wz = (accumulator << 8) | ((port_low + 1) & 0xFF)
                    return nops

                return output_a_to_port
            if bits_5_3 == 3:

                def input_a_from_port() -> int:
                    """IN A,(n): port = A:n, no flags; MEMPTR = port + 1."""
                    port = registers[A] << 8 | read_immediate_byte()
                    registers[A] = cpu.bus.io_in(port)
                    cpu.wz = (port + 1) & 0xFFFF
                    return nops

                return input_a_from_port
            if bits_5_3 == 4:

                def exchange_top_of_stack_with_hl() -> int:
                    """EX (SP),HL/IX/IY: read low, high; write high, low; MEMPTR = new value."""
                    stack_low_address = cpu.sp
                    stack_high_address = (cpu.sp + 1) & 0xFFFF
                    stack_low_byte = cpu.bus.read(stack_low_address)
                    stack_high_byte = cpu.bus.read(stack_high_address)
                    cpu.bus.write(stack_high_address, registers[pair_high])
                    cpu.bus.write(stack_low_address, registers[pair_low])
                    registers[pair_high] = stack_high_byte
                    registers[pair_low] = stack_low_byte
                    cpu.wz = stack_high_byte << 8 | stack_low_byte
                    return nops

                return exchange_top_of_stack_with_hl
            if bits_5_3 == 5:

                def exchange_de_with_hl() -> int:
                    """EX DE,HL: never affected by a DD/FD prefix."""
                    registers[D], registers[H] = registers[H], registers[D]
                    registers[E], registers[L] = registers[L], registers[E]
                    return nops

                return exchange_de_with_hl
            if bits_5_3 == 6:

                def disable_interrupts() -> int:
                    """DI: clear IFF1 and IFF2."""
                    cpu.iff1 = cpu.iff2 = False
                    return nops

                return disable_interrupts

            def enable_interrupts() -> int:
                """EI: set IFF1 and IFF2; interrupts are accepted after the next instruction."""
                cpu.iff1 = cpu.iff2 = True
                cpu.ei_delay = True
                return nops

            return enable_interrupts
        if bits_2_0 == 4:
            condition_holds = self.condition_test(bits_5_3)

            def call_if() -> int:
                """CALL cc,nn: the address is always read; MEMPTR = nn in both cases."""
                target_address = read_immediate_word()
                cpu.wz = target_address
                if condition_holds():
                    push_word(cpu.pc)
                    cpu.pc = target_address
                    return nops_if_taken
                return nops

            return call_if
        if bits_2_0 == 5:
            if bit_3 == 0:
                if pair_index == 3:

                    def push_af() -> int:
                        """PUSH AF."""
                        push_word(registers[A] << 8 | registers[F])
                        return nops

                    return push_af
                get_pair = pair_getter(pair_index)

                def push_pair() -> int:
                    """PUSH BC / DE / HL / IX / IY."""
                    push_word(get_pair())
                    return nops

                return push_pair
            if pair_index == 0:

                def call() -> int:
                    """CALL nn: push the return address (high byte first); MEMPTR = nn."""
                    target_address = read_immediate_word()
                    cpu.wz = target_address
                    push_word(cpu.pc)
                    cpu.pc = target_address
                    return nops

                return call
            # prefixes DD, ED, FD; under a DD/FD prefix they cost one extra NOP
            extra_prefix_nops = 1 if indexed else 0
            fetch_opcode = self.fetch_opcode
            if pair_index == 2:
                ed_table = self.ed_table

                def ed_prefix() -> int:
                    """ED prefix: run the next opcode from the ED table (a DD/FD before is lost)."""
                    return ed_table[fetch_opcode()]() + extra_prefix_nops

                return ed_prefix
            table_attribute = "dd_table" if pair_index == 1 else "fd_table"

            def index_prefix() -> int:
                """DD/FD prefix: fetch the next opcode with M1 and run it from the DD/FD table.

                The table is looked up at run time because it does not exist yet while the
                unprefixed table is being built. A DD/FD prefix after another one replaces it.
                """
                # the prefix acts as an instruction of its own that does not change the flags
                cpu.flags_written_by_previous_instruction = 0
                index_table = getattr(self, table_attribute)
                return index_table[fetch_opcode()]() + extra_prefix_nops

            return index_prefix
        if bits_2_0 == 6:
            alu = self.alu_operation(bits_5_3)

            def alu_with_immediate() -> int:
                """ALU op with an immediate byte: ADD ADC SUB SBC AND XOR OR CP n."""
                alu(read_immediate_byte())
                return nops

            return alu_with_immediate

        restart_address = bits_5_3 * 8

        def restart() -> int:
            """RST n: call address n = &00, &08, ... &38 (MEMPTR = target).

            The CPU reads nothing after the RST opcode; the inline bytes of the CPC RST
            conventions are read later as data by the firmware handler (§6.15).
            """
            push_word(cpu.pc)
            cpu.pc = cpu.wz = restart_address
            return nops

        return restart

    def _make_accumulator_handler(self, operation: int, nops: int) -> Handler:
        """Create the handler for an accumulator/flag opcode (group 0, bits 2-0 = 7).

        Args:
            operation: 0 RLCA, 1 RRCA, 2 RLA, 3 RRA, 4 DAA, 5 CPL, 6 SCF, 7 CCF.
            nops: Cost of the instruction.

        Returns:
            The handler.
        """
        cpu = self.cpu
        registers = self.registers
        set_flags = self.set_flags
        kept_flags_mask = FLAG_S | FLAG_Z | FLAG_PV

        def rotate_flags(result: int, carry_out: int) -> int:
            """Flags of the A rotations: S Z P/V kept, X/Y from A, H = N = 0, C = carry_out."""
            kept_flags = registers[F] & kept_flags_mask
            return kept_flags | (result & FLAGS_XY) | (FLAG_C if carry_out else 0)

        if operation == 0:

            def rotate_a_left_circular() -> int:
                """RLCA: rotate A left, bit 7 to bit 0 and to C."""
                value = registers[A]
                result = ((value << 1) | (value >> 7)) & 0xFF
                registers[A] = result
                set_flags(rotate_flags(result, value & 0x80))
                return nops

            return rotate_a_left_circular
        if operation == 1:

            def rotate_a_right_circular() -> int:
                """RRCA: rotate A right, bit 0 to bit 7 and to C."""
                value = registers[A]
                result = (value >> 1) | ((value & 1) << 7)
                registers[A] = result
                set_flags(rotate_flags(result, value & 1))
                return nops

            return rotate_a_right_circular
        if operation == 2:

            def rotate_a_left() -> int:
                """RLA: rotate A left through carry."""
                value = registers[A]
                result = ((value << 1) | (registers[F] & FLAG_C)) & 0xFF
                registers[A] = result
                set_flags(rotate_flags(result, value & 0x80))
                return nops

            return rotate_a_left
        if operation == 3:

            def rotate_a_right() -> int:
                """RRA: rotate A right through carry."""
                value = registers[A]
                result = (value >> 1) | ((registers[F] & FLAG_C) << 7)
                registers[A] = result
                set_flags(rotate_flags(result, value & 1))
                return nops

            return rotate_a_right
        if operation == 4:

            def decimal_adjust_a() -> int:
                """DAA: decimal adjust A after BCD addition (N = 0) or subtraction (N = 1).

                The correction is &06 for the low digit (H set or digit > 9) plus &60 for the
                high digit (C set or A > &99, which also sets C). H follows the low-digit
                adjustment; S Z X Y P from the result; N kept.
                """
                value = registers[A]
                flags = registers[F]
                low_digit = value & 0x0F
                correction = 0x06 if (flags & FLAG_H) or low_digit > 9 else 0
                carry = flags & FLAG_C
                if carry or value > 0x99:
                    correction |= 0x60
                    carry = FLAG_C
                if flags & FLAG_N:
                    half_carry = FLAG_H if (flags & FLAG_H) and low_digit < 6 else 0
                    result = (value - correction) & 0xFF
                else:
                    half_carry = FLAG_H if low_digit > 9 else 0
                    result = (value + correction) & 0xFF
                registers[A] = result
                set_flags(SIGN_ZERO_XY_PARITY[result] | half_carry | (flags & FLAG_N) | carry)
                return nops

            return decimal_adjust_a
        if operation == 5:

            def complement_a() -> int:
                """CPL: A = NOT A; H = N = 1, X/Y from A, other flags kept."""
                result = registers[A] ^ 0xFF
                registers[A] = result
                kept_flags = registers[F] & (kept_flags_mask | FLAG_C)
                set_flags(kept_flags | FLAG_H | FLAG_N | (result & FLAGS_XY))
                return nops

            return complement_a

        def carry_flag_xy(flags: int) -> int:
            """X/Y of SCF/CCF (undocumented, NMOS).

            X/Y = ((flags written by the previous instruction XOR F) OR A): taken from A if
            the previous instruction changed the flags, otherwise from F OR A.
            """
            return ((cpu.flags_written_by_previous_instruction ^ flags) | registers[A]) & FLAGS_XY

        if operation == 6:

            def set_carry_flag() -> int:
                """SCF: C = 1, H = N = 0, S Z P/V kept."""
                flags = registers[F]
                set_flags((flags & kept_flags_mask) | FLAG_C | carry_flag_xy(flags))
                return nops

            return set_carry_flag

        def complement_carry_flag() -> int:
            """CCF: C inverted, H = old C, N = 0, S Z P/V kept."""
            flags = registers[F]
            old_carry = flags & FLAG_C
            new_flags = (flags & kept_flags_mask) | (FLAG_H if old_carry else 0)
            new_flags |= 0 if old_carry else FLAG_C
            set_flags(new_flags | carry_flag_xy(flags))
            return nops

        return complement_carry_flag

    # -- CB ------------------------------------------------------------------------------------

    def _make_cb_handler(self, opcode: int) -> Handler:
        """Create the handler for a CB-prefixed opcode (operands are the real registers/(HL)).

        Args:
            opcode: The opcode byte after CB: group 0 rotate/shift, 1 BIT, 2 RES, 3 SET;
                bits 5-3 the operation or bit number; bits 2-0 the register (6 = (HL)).

        Returns:
            The handler.
        """
        cpu = self.cpu
        registers = self.registers
        group, bits_5_3, operand_field = opcode >> 6, (opcode >> 3) & 7, opcode & 7
        nops = timing.cb(opcode)

        def hl_address() -> int:
            """Return HL, the address of the (HL) operand."""
            return registers[H] << 8 | registers[L]

        if group == 0:
            rotate_or_shift = self.rotate_shift_operation(bits_5_3)
            if operand_field == MEMORY_OPERAND:

                def rotate_or_shift_memory() -> int:
                    """Rotate/shift (HL): read, modify, write back."""
                    address = hl_address()
                    cpu.bus.write(address, rotate_or_shift(cpu.bus.read(address)))
                    return nops

                return rotate_or_shift_memory

            def rotate_or_shift_register() -> int:
                """Rotate/shift a register."""
                registers[operand_field] = rotate_or_shift(registers[operand_field])
                return nops

            return rotate_or_shift_register
        bit_number = bits_5_3
        if group == 1:
            set_bit_test_flags = self.set_bit_test_flags
            if operand_field == MEMORY_OPERAND:

                def test_bit_in_memory() -> int:
                    """BIT n,(HL): X/Y come from the high byte of MEMPTR."""
                    set_bit_test_flags(cpu.bus.read(hl_address()), bit_number, cpu.wz >> 8)
                    return nops

                return test_bit_in_memory

            def test_bit_in_register() -> int:
                """BIT n,r: X/Y come from the register."""
                value = registers[operand_field]
                set_bit_test_flags(value, bit_number, value)
                return nops

            return test_bit_in_register
        change_bit = _bit_changer(bit_number, set_bit=group == 3)
        if operand_field == MEMORY_OPERAND:

            def change_bit_in_memory() -> int:
                """RES/SET n,(HL): read, modify, write back; no flags."""
                address = hl_address()
                cpu.bus.write(address, change_bit(cpu.bus.read(address)))
                return nops

            return change_bit_in_memory

        def change_bit_in_register() -> int:
            """RES/SET n,r: no flags."""
            registers[operand_field] = change_bit(registers[operand_field])
            return nops

        return change_bit_in_register

    # -- DD CB d op / FD CB d op ---------------------------------------------------------------

    def _make_indexed_cb_prefix_handler(self, index_high: int, index_low: int) -> Handler:
        """Create the DD/FD table entry for CB, which handles DD CB d op / FD CB d op.

        Args:
            index_high: Index of IXH or IYH in `byte_registers`.
            index_low: Index of IXL or IYL in `byte_registers`.

        Returns:
            The handler.
        """
        cpu = self.cpu
        registers = self.registers
        indexed_cb_table = self.indexed_cb_table
        read_immediate_byte = self.read_immediate_byte

        def indexed_cb_prefix() -> int:
            """Read d and op WITHOUT M1 (they are decoded data in a scrambled ROM, §5.5).

            Computes IX+d / IY+d (also MEMPTR) and runs the handler for op. R was incremented
            only for DD and CB.
            """
            displacement = _signed_byte(read_immediate_byte())
            final_opcode = read_immediate_byte()
            index_register = registers[index_high] << 8 | registers[index_low]
            address = (index_register + displacement) & 0xFFFF
            cpu.wz = address
            return indexed_cb_table[final_opcode](address)

        return indexed_cb_prefix

    def _make_indexed_cb_handler(self, opcode: int) -> IndexedBitHandler:
        """Create the handler for the final opcode byte of DD CB d op / FD CB d op.

        Args:
            opcode: The final opcode byte: group 0 rotate/shift, 1 BIT, 2 RES, 3 SET. For
                groups other than BIT, a register field other than 6 means the result is also
                copied to that register (undocumented).

        Returns:
            A handler taking the effective address.
        """
        cpu = self.cpu
        registers = self.registers
        group, bits_5_3, copy_register_field = opcode >> 6, (opcode >> 3) & 7, opcode & 7
        nops = timing.index_cb(opcode)
        if group == 1:
            set_bit_test_flags = self.set_bit_test_flags
            bit_number = bits_5_3

            def test_bit_indexed(address: int) -> int:
                """BIT n,(IX+d): X/Y come from the high byte of the address."""
                set_bit_test_flags(cpu.bus.read(address), bit_number, address >> 8)
                return nops

            return test_bit_indexed
        if group == 0:
            modify = self.rotate_shift_operation(bits_5_3)
        else:
            modify = _bit_changer(bits_5_3, set_bit=group == 3)

        if copy_register_field == MEMORY_OPERAND:

            def modify_indexed(address: int) -> int:
                """Rotate/shift/RES/SET (IX+d): read, modify, write back."""
                cpu.bus.write(address, modify(cpu.bus.read(address)))
                return nops

            return modify_indexed

        def modify_indexed_and_copy(address: int) -> int:
            """As `modify_indexed`, and the result is also stored in a register (undocumented)."""
            result = modify(cpu.bus.read(address))
            cpu.bus.write(address, result)
            registers[copy_register_field] = result
            return nops

        return modify_indexed_and_copy

    # -- ED ------------------------------------------------------------------------------------

    def _make_ed_handler(self, opcode: int) -> Handler:
        """Create the handler for an ED-prefixed opcode.

        ED &40-&7F: I/O with (C), 16-bit ADC/SBC, LD (nn),rr, NEG, RETN/RETI, IM, LD I/R,
        RRD/RLD (including the undocumented duplicates). ED &A0-&BB: block instructions.
        Everything else is a 2-NOP no-op.

        Args:
            opcode: The opcode byte after ED.

        Returns:
            The handler.
        """
        cpu = self.cpu
        registers = self.registers
        set_flags = self.set_flags
        group, bits_5_3, bits_2_0 = opcode >> 6, (opcode >> 3) & 7, opcode & 7
        pair_index, bit_3 = bits_5_3 >> 1, bits_5_3 & 1
        nops = timing.ED[opcode]

        def no_operation() -> int:
            """Undefined ED opcode (or ED 77/7F): no operation."""
            return nops

        if group == 2 and bits_2_0 <= 3 and bits_5_3 >= 4:
            return self._make_block_handler(bits_5_3, bits_2_0, nops)
        if group != 1:
            return no_operation

        register_pairs = ((B, C), (D, E), (H, L))  # pair index 3 is SP

        def pair_getter(pair: int) -> Callable[[], int]:
            """Return a getter for a register pair: 0 BC, 1 DE, 2 HL, 3 SP."""
            if pair == 3:
                return lambda: cpu.sp
            high, low = register_pairs[pair]
            return lambda: registers[high] << 8 | registers[low]

        def bc_value() -> int:
            """Return BC, the port address of the (C) instructions."""
            return registers[B] << 8 | registers[C]

        if bits_2_0 == 0:
            destination_field = bits_5_3

            def input_from_port_c() -> int:
                """IN r,(C): port = BC; S Z X Y P from the value, H = N = 0; MEMPTR = BC+1."""
                port = bc_value()
                value = cpu.bus.io_in(port)
                cpu.wz = (port + 1) & 0xFFFF
                set_flags((registers[F] & FLAG_C) | SIGN_ZERO_XY_PARITY[value])
                if destination_field != MEMORY_OPERAND:  # ED 70: IN F,(C) only sets the flags
                    registers[destination_field] = value
                return nops

            return input_from_port_c
        if bits_2_0 == 1:
            source_field = bits_5_3

            def output_to_port_c() -> int:
                """OUT (C),r: port = BC; MEMPTR = BC + 1. ED 71 outputs 0 (NMOS)."""
                port = bc_value()
                value = registers[source_field] if source_field != MEMORY_OPERAND else 0
                cpu.bus.io_out(port, value)
                cpu.wz = (port + 1) & 0xFFFF
                return nops

            return output_to_port_c
        if bits_2_0 == 2:
            get_pair = pair_getter(pair_index)

            def word_flags(hl_value: int, operand: int, result: int, subtract: bool) -> int:
                """Flags of 16-bit ADC/SBC; `result` may exceed 16 bits or be negative."""
                result_word = result & 0xFFFF
                sign_and_xy = (result_word >> 8) & (FLAG_S | FLAGS_XY)
                zero = 0 if result_word else FLAG_Z
                half_carry = ((hl_value ^ operand ^ result) >> 8) & FLAG_H  # from bit 11
                if subtract:
                    overflow_bits = (hl_value ^ operand) & (hl_value ^ result)
                    carry = FLAG_C if result < 0 else 0
                else:
                    overflow_bits = ~(hl_value ^ operand) & (hl_value ^ result)
                    carry = FLAG_C if result > 0xFFFF else 0
                overflow = FLAG_PV if overflow_bits & 0x8000 else 0
                subtract_flag = FLAG_N if subtract else 0
                return sign_and_xy | zero | half_carry | overflow | subtract_flag | carry

            def store_hl(result: int) -> None:
                """Store the low 16 bits of `result` in HL."""
                registers[H] = (result >> 8) & 0xFF
                registers[L] = result & 0xFF

            if bit_3 == 0:

                def subtract_pair_from_hl_with_carry() -> int:
                    """SBC HL,rr: 16-bit subtract with borrow; MEMPTR = HL + 1."""
                    hl_value = registers[H] << 8 | registers[L]
                    operand = get_pair()
                    result = hl_value - operand - (registers[F] & FLAG_C)
                    cpu.wz = (hl_value + 1) & 0xFFFF
                    set_flags(word_flags(hl_value, operand, result, subtract=True))
                    store_hl(result)
                    return nops

                return subtract_pair_from_hl_with_carry

            def add_pair_to_hl_with_carry() -> int:
                """ADC HL,rr: 16-bit add with carry; MEMPTR = HL + 1."""
                hl_value = registers[H] << 8 | registers[L]
                operand = get_pair()
                result = hl_value + operand + (registers[F] & FLAG_C)
                cpu.wz = (hl_value + 1) & 0xFFFF
                set_flags(word_flags(hl_value, operand, result, subtract=False))
                store_hl(result)
                return nops

            return add_pair_to_hl_with_carry
        if bits_2_0 == 3:
            read_immediate_word = self.read_immediate_word
            if pair_index == 3:
                if bit_3 == 0:

                    def store_sp_direct() -> int:
                        """LD (nn),SP: write low, then high byte; MEMPTR = nn + 1."""
                        address = read_immediate_word()
                        cpu.bus.write(address, cpu.sp & 0xFF)
                        address = (address + 1) & 0xFFFF
                        cpu.bus.write(address, cpu.sp >> 8)
                        cpu.wz = address
                        return nops

                    return store_sp_direct

                def load_sp_direct() -> int:
                    """LD SP,(nn): read low, then high byte; MEMPTR = nn + 1."""
                    address = read_immediate_word()
                    low_byte = cpu.bus.read(address)
                    address = (address + 1) & 0xFFFF
                    cpu.sp = cpu.bus.read(address) << 8 | low_byte
                    cpu.wz = address
                    return nops

                return load_sp_direct
            pair_high, pair_low = register_pairs[pair_index]
            if bit_3 == 0:

                def store_pair_direct() -> int:
                    """LD (nn),BC/DE/HL (ED 63 is the non-canonical HL form); MEMPTR = nn+1."""
                    address = read_immediate_word()
                    cpu.bus.write(address, registers[pair_low])
                    address = (address + 1) & 0xFFFF
                    cpu.bus.write(address, registers[pair_high])
                    cpu.wz = address
                    return nops

                return store_pair_direct

            def load_pair_direct() -> int:
                """LD BC/DE/HL,(nn) (ED 6B is the non-canonical HL form); MEMPTR = nn+1."""
                address = read_immediate_word()
                registers[pair_low] = cpu.bus.read(address)
                address = (address + 1) & 0xFFFF
                registers[pair_high] = cpu.bus.read(address)
                cpu.wz = address
                return nops

            return load_pair_direct
        if bits_2_0 == 4:
            subtract = self.alu_operation(2)

            def negate_a() -> int:
                """NEG (and its 7 undocumented duplicates): A = 0 - A with SUB flags."""
                value = registers[A]
                registers[A] = 0
                subtract(value)
                return nops

            return negate_a
        if bits_2_0 == 5:
            pop_word = self.pop_word

            def return_from_interrupt() -> int:
                """RETN / RETI (and duplicates): return and copy IFF2 to IFF1."""
                cpu.pc = cpu.wz = pop_word()
                cpu.iff1 = cpu.iff2
                return nops

            return return_from_interrupt
        if bits_2_0 == 6:
            interrupt_mode = (0, 0, 1, 2)[bits_5_3 & 3]

            def set_interrupt_mode() -> int:
                """IM 0/1/2 (ED 4E/6E set mode 0 on NMOS)."""
                cpu.im = interrupt_mode
                return nops

            return set_interrupt_mode
        # bits_2_0 == 7
        if bits_5_3 == 0:

            def load_i_from_a() -> int:
                """LD I,A."""
                cpu.i = registers[A]
                return nops

            return load_i_from_a
        if bits_5_3 == 1:

            def load_r_from_a() -> int:
                """LD R,A: sets all 8 bits of R (the only way to change bit 7)."""
                cpu.r = registers[A]
                return nops

            return load_r_from_a
        if bits_5_3 in (2, 3):
            load_from_i = bits_5_3 == 2

            def load_a_from_i_or_r() -> int:
                """LD A,I / LD A,R: S Z X Y from the value, P/V = IFF2, H = N = 0.

                Sets `last_instruction_was_ld_a_i_or_r`, so that an interrupt accepted right
                after it clears P/V again (NMOS).
                """
                value = cpu.i if load_from_i else cpu.r
                registers[A] = value
                iff2_flag = FLAG_PV if cpu.iff2 else 0
                set_flags((registers[F] & FLAG_C) | SIGN_ZERO_XY[value] | iff2_flag)
                cpu.last_instruction_was_ld_a_i_or_r = True
                return nops

            return load_a_from_i_or_r
        if bits_5_3 in (4, 5):
            rotate_left = bits_5_3 == 5

            def rotate_digits() -> int:
                """RRD / RLD: rotate BCD digits between A (low nibble) and (HL).

                S Z X Y P from A, H = N = 0, C kept; MEMPTR = HL + 1.
                """
                address = registers[H] << 8 | registers[L]
                memory_value = cpu.bus.read(address)
                accumulator = registers[A]
                if rotate_left:  # RLD: (HL) high ← (HL) low ← A low ← (HL) high
                    new_memory_value = ((memory_value << 4) | (accumulator & 0x0F)) & 0xFF
                    accumulator = (accumulator & 0xF0) | (memory_value >> 4)
                else:  # RRD: A low ← (HL) low ← (HL) high ← A low
                    new_memory_value = ((accumulator << 4) | (memory_value >> 4)) & 0xFF
                    accumulator = (accumulator & 0xF0) | (memory_value & 0x0F)
                cpu.bus.write(address, new_memory_value)
                registers[A] = accumulator
                cpu.wz = (address + 1) & 0xFFFF
                set_flags((registers[F] & FLAG_C) | SIGN_ZERO_XY_PARITY[accumulator])
                return nops

            return rotate_digits
        return no_operation

    def _make_block_handler(self, variant: int, kind: int, nops: int) -> Handler:
        """Create the handler for a block instruction (ED &A0-&BB).

        One handler call performs one iteration. A repeating form (…IR/…DR) that has to
        repeat rewinds PC to the ED prefix, so the next `step()` executes it again; interrupts
        can be accepted in between, as on the real CPU. While repeating, X/Y come from bits
        13/11 of PC (undocumented, verified by the SingleStepTests).

        Args:
            variant: 4 increment, 5 decrement, 6 increment + repeat, 7 decrement + repeat.
            kind: 0 LD, 1 CP, 2 IN, 3 OUT.
            nops: Cost of the final (non-repeating) iteration.

        Returns:
            The handler.
        """
        cpu = self.cpu
        registers = self.registers
        set_flags = self.set_flags
        address_step = 1 if variant in (4, 6) else -1
        repeats = variant >= 6
        nops_if_repeating = timing.ED_REPEAT

        def get_pair(high: int, low: int) -> int:
            """Return the register pair made of `registers[high]` and `registers[low]`."""
            return registers[high] << 8 | registers[low]

        def set_pair(high: int, low: int, value: int) -> None:
            """Store a 16-bit value in the register pair (masked to 16 bits)."""
            registers[high] = (value >> 8) & 0xFF
            registers[low] = value & 0xFF

        def rewind_to_repeat() -> int:
            """Rewind PC to the ED prefix and set MEMPTR = PC + 1; return the new PC."""
            cpu.pc = (cpu.pc - 2) & 0xFFFF
            cpu.wz = (cpu.pc + 1) & 0xFFFF
            return cpu.pc

        def xy_from_value(value: int) -> int:
            """X = bit 3 and Y = bit 1 of `value` (block LD/CP, last iteration)."""
            return (value & FLAG_X) | ((value << 4) & FLAG_Y)

        if kind == 0:

            def block_load() -> int:
                """LDI/LDD/LDIR/LDDR: (DE) = (HL), HL/DE step, BC-1.

                P/V = BC != 0, H = N = 0, S Z C kept; X/Y from bits 3/1 of value + A.
                """
                source_address = get_pair(H, L)
                destination_address = get_pair(D, E)
                value = cpu.bus.read(source_address)
                cpu.bus.write(destination_address, value)
                set_pair(H, L, source_address + address_step)
                set_pair(D, E, destination_address + address_step)
                remaining_count = (get_pair(B, C) - 1) & 0xFFFF
                set_pair(B, C, remaining_count)
                flags = registers[F] & (FLAG_S | FLAG_Z | FLAG_C)
                if remaining_count != 0:
                    flags |= FLAG_PV
                if repeats and remaining_count != 0:
                    program_counter = rewind_to_repeat()
                    set_flags(flags | ((program_counter >> 8) & FLAGS_XY))
                    return nops_if_repeating
                set_flags(flags | xy_from_value(value + registers[A]))
                return nops

            return block_load
        if kind == 1:

            def block_compare() -> int:
                """CPI/CPD/CPIR/CPDR: compare A with (HL), HL steps, BC-1, MEMPTR steps.

                S Z H from A - (HL), N = 1, P/V = BC != 0, C kept; X/Y from bits 3/1 of
                A - (HL) - H. The repeating forms stop when BC = 0 or a match is found.
                """
                address = get_pair(H, L)
                value = cpu.bus.read(address)
                accumulator = registers[A]
                difference = (accumulator - value) & 0xFF
                set_pair(H, L, address + address_step)
                remaining_count = (get_pair(B, C) - 1) & 0xFFFF
                set_pair(B, C, remaining_count)
                half_borrow = (accumulator ^ value ^ difference) & FLAG_H
                cpu.wz = (cpu.wz + address_step) & 0xFFFF
                flags = (registers[F] & FLAG_C) | FLAG_N | half_borrow
                flags |= SIGN_ZERO_XY[difference] & (FLAG_S | FLAG_Z)
                if remaining_count != 0:
                    flags |= FLAG_PV
                if repeats and remaining_count != 0 and difference != 0:
                    program_counter = rewind_to_repeat()
                    set_flags(flags | ((program_counter >> 8) & FLAGS_XY))
                    return nops_if_repeating
                set_flags(flags | xy_from_value(difference - (1 if half_borrow else 0)))
                return nops

            return block_compare

        def io_flags(value: int, carry_sum: int, new_b: int) -> int:
            """Flags of INI/IND/OUTI/OUTD (undocumented).

            Args:
                value: The transferred byte.
                carry_sum: value plus C±1 (IN) or value plus L after the step (OUT).
                new_b: B after the decrement.

            Returns:
                S Z X Y from B, N = bit 7 of value, H = C = (carry_sum > 255),
                P = parity((carry_sum & 7) XOR B).
            """
            subtract_flag = FLAG_N if value & 0x80 else 0
            carry_flags = (FLAG_H | FLAG_C) if carry_sum > 0xFF else 0
            parity = PARITY[(carry_sum & 7) ^ new_b]
            return SIGN_ZERO_XY[new_b] | subtract_flag | carry_flags | parity

        def finish_io(value: int, carry_sum: int, new_b: int) -> int:
            """Set the flags of an I/O block step and repeat if needed; return the cost."""
            flags = io_flags(value, carry_sum, new_b)
            if repeats and new_b != 0:
                program_counter = rewind_to_repeat()
                set_flags(_io_repeat_flags(flags, value, carry_sum, new_b, program_counter))
                return nops_if_repeating
            set_flags(flags)
            return nops

        if kind == 2:

            def block_input() -> int:
                """INI/IND/INIR/INDR: (HL) = IN (BC), B-1, HL steps; MEMPTR = BC±1 (old B)."""
                port = get_pair(B, C)
                value = cpu.bus.io_in(port)
                cpu.wz = (port + address_step) & 0xFFFF
                new_b = (registers[B] - 1) & 0xFF
                registers[B] = new_b
                address = get_pair(H, L)
                cpu.bus.write(address, value)
                set_pair(H, L, address + address_step)
                carry_sum = value + ((registers[C] + address_step) & 0xFF)
                return finish_io(value, carry_sum, new_b)

            return block_input

        def block_output() -> int:
            """OUTI/OUTD/OTIR/OTDR: B-1, OUT (BC) = (HL), HL steps; MEMPTR = BC±1 (new B)."""
            address = get_pair(H, L)
            value = cpu.bus.read(address)
            new_b = (registers[B] - 1) & 0xFF
            registers[B] = new_b
            port = get_pair(B, C)
            cpu.bus.io_out(port, value)
            cpu.wz = (port + address_step) & 0xFFFF
            set_pair(H, L, address + address_step)
            carry_sum = value + registers[L]
            return finish_io(value, carry_sum, new_b)

        return block_output


def _bit_changer(bit_number: int, set_bit: bool) -> Callable[[int], int]:
    """Return a function that sets (SET) or clears (RES) one bit of a byte.

    Args:
        bit_number: The bit 0-7.
        set_bit: True for SET, False for RES.

    Returns:
        A function taking a byte and returning the modified byte.
    """
    mask = 1 << bit_number

    def change_bit(value: int) -> int:
        """Return `value` with the bit set or cleared."""
        return value | mask if set_bit else value & ~mask & 0xFF

    return change_bit


def _io_repeat_flags(flags: int, value: int, carry_sum: int, new_b: int, pc: int) -> int:
    """Flags of INIR/INDR/OTIR/OTDR while they repeat (undocumented, NMOS).

    X/Y come from bits 13/11 of PC; H and P/V are modified depending on the carry of
    `carry_sum` and bit 7 of the transferred byte (research by D. Banks et al., matched by
    the SingleStepTests).

    Args:
        flags: Flags of the non-repeating form (`io_flags`).
        value: The transferred byte.
        carry_sum: The intermediate sum used for H/C/P.
        new_b: B after the decrement.
        pc: PC after rewinding to the ED prefix.

    Returns:
        The new flags.
    """
    flags = (flags & ~(FLAGS_XY | FLAG_H | FLAG_PV)) | ((pc >> 8) & FLAGS_XY)
    parity = PARITY[(carry_sum & 7) ^ new_b]
    if carry_sum > 0xFF:
        if value & 0x80:
            parity ^= PARITY[(new_b - 1) & 7] ^ FLAG_PV
            half_carry = FLAG_H if new_b & 0x0F == 0x00 else 0
        else:
            parity ^= PARITY[(new_b + 1) & 7] ^ FLAG_PV
            half_carry = FLAG_H if new_b & 0x0F == 0x0F else 0
    else:
        parity ^= PARITY[new_b & 7] ^ FLAG_PV
        half_carry = 0
    return flags | parity | half_carry
