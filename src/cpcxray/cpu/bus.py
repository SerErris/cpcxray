"""The CPU bus interface (design.md §6.2) and a flat 64 KB bus for tests and harnesses.

The Z80 core talks to the outside world only through the six methods of `CpuBus`. Which
method is called encodes the bus signals of the access (FR-CPU-04), so observers and the
memory map can tell an opcode fetch (M1) from an ordinary read: this is what the decoding of
scrambled ROMs depends on (D-11).
"""

from __future__ import annotations

from collections.abc import Callable
from enum import IntEnum
from typing import Protocol


class Cycle(IntEnum):
    """Kind of a bus access, named after the Z80 signals that are active (FR-CPU-04).

    Used by access logs and, from phase 4, by bus observers (`on_cycle(kind, ...)`, §6.3).
    """

    M1 = 0
    """M1 + MREQ + RD: opcode or prefix fetch."""
    MEM_RD = 1
    """MREQ + RD: operand, displacement or data read."""
    MEM_WR = 2
    """MREQ + WR: memory write."""
    IO_RD = 3
    """IORQ + RD: IN."""
    IO_WR = 4
    """IORQ + WR: OUT."""
    INT_ACK = 5
    """M1 + IORQ: interrupt acknowledge, reads the data bus for IM 0 / IM 2."""


class CpuBus(Protocol):
    """Everything the Z80 core calls; each method carries its bus signals.

    Addresses and ports are 16-bit ints, values 8-bit ints. Implementations must not
    reorder or merge accesses: the CPU issues them in the order of the real Z80.
    """

    def fetch(self, addr: int) -> int:
        """Opcode or prefix fetch (M1 + MREQ + RD); returns the byte at `addr`."""
        ...

    def read(self, addr: int) -> int:
        """Memory read without M1 (MREQ + RD); returns the byte at `addr`."""
        ...

    def write(self, addr: int, value: int) -> None:
        """Memory write (MREQ + WR)."""
        ...

    def io_in(self, port: int) -> int:
        """I/O read (IORQ + RD) from the full 16-bit port address; returns the value."""
        ...

    def io_out(self, port: int, value: int) -> None:
        """I/O write (IORQ + WR) to the full 16-bit port address."""
        ...

    def int_ack(self) -> int:
        """Interrupt acknowledge (M1 + IORQ); returns the data bus value for IM 0 / IM 2."""
        ...


type Access = tuple[Cycle, int, int]
"""One logged bus access: (kind, address or port, value)."""


class FlatBus:
    """A plain 64 KB RAM bus with pluggable I/O and an optional access log.

    Used by CPU tests and harnesses (SingleStepTests, ZEXALL, z80test). It is not the CPC
    bus: that one (machine/bus.py, phase 4) adds the memory map, ROM slots, I/O devices and
    observers.

    Attributes:
        mem: The 64 KB memory; fetches, reads and writes all go here.
        log: List of `Access` tuples in order, or None when logging is off.
        on_in: Called for IN with the port; returns the value read (default &FF).
        on_out: Called for OUT with port and value (default: ignored).
        int_data: Value on the data bus during an interrupt acknowledge (default &FF).
    """

    def __init__(self, memory: bytes | bytearray | None = None, log: bool = False) -> None:
        """Create the bus.

        Args:
            memory: Initial memory contents, loaded at address 0 (rest is zero).
            log: Record every access in `log`.
        """
        self.mem = bytearray(0x10000)
        if memory is not None:
            self.mem[: len(memory)] = memory
        self.log: list[Access] | None = [] if log else None
        self.on_in: Callable[[int], int] = lambda port: 0xFF
        self.on_out: Callable[[int, int], None] = lambda port, value: None
        self.int_data = 0xFF

    def fetch(self, addr: int) -> int:
        """Opcode fetch (M1): return `mem[addr]`, logged as `Cycle.M1`."""
        v = self.mem[addr]
        if self.log is not None:
            self.log.append((Cycle.M1, addr, v))
        return v

    def read(self, addr: int) -> int:
        """Memory read: return `mem[addr]`, logged as `Cycle.MEM_RD`."""
        v = self.mem[addr]
        if self.log is not None:
            self.log.append((Cycle.MEM_RD, addr, v))
        return v

    def write(self, addr: int, value: int) -> None:
        """Memory write: store `value` at `mem[addr]`, logged as `Cycle.MEM_WR`."""
        self.mem[addr] = value
        if self.log is not None:
            self.log.append((Cycle.MEM_WR, addr, value))

    def io_in(self, port: int) -> int:
        """I/O read: return `on_in(port)`, logged as `Cycle.IO_RD`."""
        v = self.on_in(port)
        if self.log is not None:
            self.log.append((Cycle.IO_RD, port, v))
        return v

    def io_out(self, port: int, value: int) -> None:
        """I/O write: log as `Cycle.IO_WR`, then call `on_out(port, value)`."""
        if self.log is not None:
            self.log.append((Cycle.IO_WR, port, value))
        self.on_out(port, value)

    def int_ack(self) -> int:
        """Interrupt acknowledge: return `int_data`, logged as `Cycle.INT_ACK` (address 0)."""
        if self.log is not None:
            self.log.append((Cycle.INT_ACK, 0, self.int_data))
        return self.int_data
