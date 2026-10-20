"""Runner for the SingleStepTests/z80 per-opcode JSON tests (design.md §8, phase 1).

Checks the final CPU state and RAM, and the sequence of bus accesses including which reads are
M1 fetches. The test data samples the bus every half T-state; an access is a sample with r/w
pins. Read data appears on the following sample; for an M1 fetch that sample already carries
the refresh address I:R, which is how M1 fetches are told apart from ordinary reads.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cpcxray.cpu.bus import Access, Cycle, FlatBus
from cpcxray.cpu.z80 import Z80

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "external" / "singlestep-z80" / "v1"

# test key -> CPU attribute
_KEYS = {
    "a": "a", "f": "f", "b": "b", "c": "c", "d": "d", "e": "e", "h": "h", "l": "l",
    "i": "i", "r": "r", "pc": "pc", "sp": "sp", "wz": "wz", "ix": "ix", "iy": "iy",
    "af_": "af_", "bc_": "bc_", "de_": "de_", "hl_": "hl_", "im": "im",
    "iff1": "iff1", "iff2": "iff2", "ei": "ei_delay",
    "q": "flags_written_by_last_instruction",  # hidden flag latch "Q"
    "p": "last_instruction_was_ld_a_i_or_r",  # "P" in the test data
}  # fmt: skip

type ExpectedAccess = tuple[Cycle | None, int, int]  # kind None: M1 or read, ambiguous


def data_files() -> list[Path]:
    """Return the SingleStepTests JSON files (one per opcode), sorted; empty if not fetched."""
    return sorted(DATA_DIR.glob("*.json"))


def limit_per_file() -> int | None:
    """Tests per file: env CPCXRAY_SST ("all" or a number), default 25."""
    value = os.environ.get("CPCXRAY_SST", "25")
    return None if value == "all" else int(value)


def load(path: Path) -> list[dict[str, Any]]:
    """Load one SingleStepTests file: 1000 test dicts (name, initial, final, cycles, ports)."""
    with path.open() as f:
        return json.load(f)


class _TestBus(FlatBus):
    """FlatBus with logging whose IN results are the values listed in the test's "ports"."""

    def __init__(self) -> None:
        """Create a logging bus; IN returns `port_values` in order (&FF when empty)."""
        super().__init__(log=True)
        self.port_values: list[int] = []
        self.on_in = lambda port: self.port_values.pop(0) if self.port_values else 0xFF


def _refresh_address(initial_state: dict[str, Any], m1_fetches_so_far: int) -> int:
    """Return the refresh address I:R that the CPU puts on the bus after an M1 fetch.

    R counts M1 fetches in bits 0-6; the value shown is R before the current fetch counted.
    """
    refresh_register = initial_state["r"]
    counted = (refresh_register & 0x80) | ((refresh_register + m1_fetches_so_far) & 0x7F)
    return (initial_state["i"] << 8) | counted


def expected_accesses(test: dict[str, Any]) -> list[ExpectedAccess]:
    """Derive the expected bus accesses from a test's per-half-T-state `cycles` samples.

    Each sample is [address, data, pins]; pins is a 4-character string "rwmi" (read, write,
    memory request, I/O request) with "-" for inactive pins.

    Returns:
        (kind, address, value) per access in order. kind is None where an M1 fetch and an
        ordinary read cannot be told apart (the read address equals the refresh address).
    """
    samples = test["cycles"]
    m1_fetches = 0
    accesses: list[ExpectedAccess] = []
    for index, (address, data, pins) in enumerate(samples):
        is_io = pins[3] == "i"
        if pins[1] == "w":
            accesses.append((Cycle.IO_WR if is_io else Cycle.MEM_WR, address, data))
            continue
        if pins[0] != "r":  # neither read nor write: idle or refresh sample
            continue
        next_address, next_data = samples[index + 1][:2]  # read data appears one sample later
        value = data if data is not None else next_data
        if is_io:
            accesses.append((Cycle.IO_RD, address, value))
            continue
        refresh = _refresh_address(test["initial"], m1_fetches)
        kind: Cycle | None
        if address == refresh:
            kind = None  # the read address equals the refresh address: cannot tell
            if next_address == refresh:
                m1_fetches += 1  # assume M1; keeps the count right in the common case
        elif next_address == refresh:
            kind = Cycle.M1  # the refresh address follows only an M1 fetch
            m1_fetches += 1
        else:
            kind = Cycle.MEM_RD
        accesses.append((kind, address, value))
    return accesses


class Runner:  # pylint: disable=too-few-public-methods  # one job: run tests
    """Runs tests on one reused CPU and bus (building a CPU is the expensive part)."""

    def __init__(self) -> None:
        """Create the reused bus and CPU."""
        self.bus = _TestBus()
        self.cpu = Z80(self.bus)

    def run(self, test: dict[str, Any]) -> list[str]:
        """Run one test; return a list of mismatch descriptions (empty when it passes)."""
        init, final = test["initial"], test["final"]
        bus, cpu = self.bus, self.cpu
        log = bus.log
        assert log is not None
        log.clear()
        bus.mem[:] = bytes(0x10000)
        bus.port_values = [v for _, v, kind in test.get("ports", []) if kind == "r"]
        cpu.halted = False
        cpu.nmi_pending = False
        cpu.int_line = False
        for key, attr in _KEYS.items():
            v = init[key]
            setattr(
                cpu,
                attr,
                bool(v)
                if attr in ("iff1", "iff2", "ei_delay", "last_instruction_was_ld_a_i_or_r")
                else v,
            )
        for addr, v in init["ram"]:
            bus.mem[addr] = v

        cpu.step()

        errors = []
        for key, attr in _KEYS.items():
            got = int(getattr(cpu, attr))
            if got != final[key]:
                errors.append(f"{key}: got {got:#x}, expected {final[key]:#x}")
        for addr, v in final["ram"]:
            if bus.mem[addr] != v:
                errors.append(f"ram[{addr:#06x}]: got {bus.mem[addr]:#04x}, expected {v:#04x}")
        want = expected_accesses(test)
        if not _accesses_match(log, want):
            errors.append(f"bus: got {_fmt(log)}, expected {_fmt(want)}")
        return errors


def _accesses_match(got: list[Access], want: list[ExpectedAccess]) -> bool:
    """Compare logged accesses with the expected ones (kind None accepts M1 or MEM_RD)."""
    if len(got) != len(want):
        return False
    for (gk, ga, gv), (wk, wa, wv) in zip(got, want, strict=True):
        if ga != wa or gv != wv:
            return False
        if wk is None:
            if gk not in (Cycle.M1, Cycle.MEM_RD):
                return False
        elif gk != wk:
            return False
    return True


def _fmt(accesses: list[Access] | list[ExpectedAccess]) -> str:
    """Format accesses compactly for failure messages, e.g. `M1:1234=3E MEM_RD:1235=05`."""
    return " ".join(f"{k.name if k is not None else 'RD?'}:{a:04X}={v:02X}" for k, a, v in accesses)
