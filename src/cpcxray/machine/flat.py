"""The machine interface used by `Session`, and the bare Z80 machine of phase 3.

`Session` (D-27) drives a machine only through the `Machine` protocol. Until the CPC 464 exists
(phase 4) the session uses `FlatMachine`: a Z80 with 64 KB of RAM, no ROM and no I/O devices.
It is enough to load a binary, step, run, trace and disassemble, which is the phase 3 milestone.
The CPC 464 will implement the same protocol (with ROM paging, the CPC disassembly profile and
symbol scopes per paged-in ROM).
"""

from __future__ import annotations

from typing import Any, Protocol

from cpcxray.cpu.bus import FlatBus
from cpcxray.cpu.disasm import DisassemblyProfile
from cpcxray.cpu.z80 import Z80
from cpcxray.debugger.symbols import GLOBAL


class Machine(Protocol):
    """What `Session` needs from a machine.

    Attributes:
        cpu: The Z80.
        clock_us: Emulated time since reset in µs (CPC NOPs, D-04).
    """

    cpu: Z80
    clock_us: int

    def reset(self) -> None:
        """Hardware reset of the machine."""
        ...

    def step(self) -> int:
        """Execute one instruction (or accept an interrupt); return its cost in µs."""
        ...

    def peek(self, address: int) -> int:
        """Read a byte as the CPU would see it, without side effects (no observers)."""
        ...

    def poke(self, address: int, value: int) -> None:
        """Write a byte to RAM, without side effects (debugger memory editing)."""
        ...

    def disassembly_profile(self) -> DisassemblyProfile | None:
        """Return the RST profile for disassembly (None for a generic Z80)."""
        ...

    def symbol_scope(self, address: int) -> str:
        """Return the symbol scope that applies to `address` right now (see symbols.py)."""
        ...

    def status(self) -> dict[str, Any]:
        """Return machine-specific status for `Session.machine_status()`."""
        ...


class FlatMachine:
    """A Z80 with 64 KB of RAM and no devices (the phase 3 test machine).

    IN returns &FF and OUT is ignored; there are no interrupts.

    Attributes:
        bus: The flat 64 KB bus.
        cpu: The CPU.
        clock_us: Emulated time since reset in µs.
    """

    model = "flat-z80"

    def __init__(self) -> None:
        """Create the machine with cleared RAM and a reset CPU."""
        self.bus = FlatBus()
        self.cpu = Z80(self.bus)
        self.clock_us = 0

    def reset(self) -> None:
        """Reset the CPU and the clock; RAM keeps its contents (as on real hardware)."""
        self.cpu.reset()
        self.clock_us = 0

    def step(self) -> int:
        """Execute one instruction; advance the clock."""
        cost = self.cpu.step()
        self.clock_us += cost
        return cost

    def peek(self, address: int) -> int:
        """Return the RAM byte at `address`."""
        return self.bus.mem[address & 0xFFFF]

    def poke(self, address: int, value: int) -> None:
        """Store a byte in RAM."""
        self.bus.mem[address & 0xFFFF] = value & 0xFF

    def disassembly_profile(self) -> DisassemblyProfile | None:
        """No RST profile: a generic Z80."""
        return None

    def symbol_scope(self, address: int) -> str:
        """Everything is RAM: always the global scope."""
        del address
        return GLOBAL

    def status(self) -> dict[str, Any]:
        """Describe the machine."""
        return {"model": self.model, "memory": "64 KB RAM, no ROM, no I/O devices"}
