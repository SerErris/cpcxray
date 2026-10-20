"""The CPU bus interface (design.md §6.2) and a flat 64 KB bus for tests and harnesses."""

from __future__ import annotations

from collections.abc import Callable
from enum import IntEnum
from typing import Protocol


class Cycle(IntEnum):
    """Kind of a bus access, by its signals (FR-CPU-04)."""

    M1 = 0  # M1 + MREQ + RD: opcode or prefix fetch
    MEM_RD = 1  # MREQ + RD: operand, displacement, data
    MEM_WR = 2  # MREQ + WR
    IO_RD = 3  # IORQ + RD
    IO_WR = 4  # IORQ + WR
    INT_ACK = 5  # M1 + IORQ


class CpuBus(Protocol):
    """Everything the Z80 core calls; each method carries its bus signals."""

    def fetch(self, addr: int) -> int: ...  # M1 + MREQ + RD
    def read(self, addr: int) -> int: ...  # MREQ + RD
    def write(self, addr: int, value: int) -> None: ...  # MREQ + WR
    def io_in(self, port: int) -> int: ...  # IORQ + RD
    def io_out(self, port: int, value: int) -> None: ...  # IORQ + WR
    def int_ack(self) -> int: ...  # M1 + IORQ, data bus for IM 0 / IM 2


type Access = tuple[Cycle, int, int]  # (kind, address or port, value)


class FlatBus:
    """64 KB RAM, pluggable I/O, optional access log. Not the CPC bus (that is machine/bus.py)."""

    def __init__(self, memory: bytes | bytearray | None = None, log: bool = False) -> None:
        self.mem = bytearray(0x10000)
        if memory is not None:
            self.mem[: len(memory)] = memory
        self.log: list[Access] | None = [] if log else None
        self.on_in: Callable[[int], int] = lambda port: 0xFF
        self.on_out: Callable[[int, int], None] = lambda port, value: None
        self.int_data = 0xFF  # value on the data bus during interrupt acknowledge

    def fetch(self, addr: int) -> int:
        v = self.mem[addr]
        if self.log is not None:
            self.log.append((Cycle.M1, addr, v))
        return v

    def read(self, addr: int) -> int:
        v = self.mem[addr]
        if self.log is not None:
            self.log.append((Cycle.MEM_RD, addr, v))
        return v

    def write(self, addr: int, value: int) -> None:
        self.mem[addr] = value
        if self.log is not None:
            self.log.append((Cycle.MEM_WR, addr, value))

    def io_in(self, port: int) -> int:
        v = self.on_in(port)
        if self.log is not None:
            self.log.append((Cycle.IO_RD, port, v))
        return v

    def io_out(self, port: int, value: int) -> None:
        if self.log is not None:
            self.log.append((Cycle.IO_WR, port, value))
        self.on_out(port, value)

    def int_ack(self) -> int:
        if self.log is not None:
            self.log.append((Cycle.INT_ACK, 0, self.int_data))
        return self.int_data
