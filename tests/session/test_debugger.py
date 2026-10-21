"""Phase 9 debugger tools: all breakpoint kinds and locations, bus trace, memory views (FR-DBG)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.decoding.vortex import vortex_decode
from cpcxray.machine.memory import RomSlot
from cpcxray.session.errors import SessionError
from cpcxray.session.session import Session
from tests.analysis.helpers import make_test_cpc
from tests.vdos import REPO, requires_roms

# OS ROM: upper ROM on, JP &C000; interrupt handler at &0038: EI / RET.
OS = {0x0000: "01817F ED49 C300C0", 0x0038: "FB C9"}
# ROM 3:
#   C000 ld hl,&4000      C003 ld (hl),&55     C005 ld a,(hl)       C006 ld a,(&C020)
#   C009 ld (&C020),a     C00C ld bc,&DF03     C00F out (c),c       C011 ei
#   C012 jr C012          C020 &AA (data)
UPPER = {0x0000: "210040 3655 7E 3A20C0 3220C0 0103DF ED49 FB 18FE", 0x0020: "AA"}


def make_session() -> Session:
    """A session on the test CPC (ROM 3 selected, PC = 0)."""
    return Session(make_test_cpc(OS, UPPER))


def run(session: Session) -> dict:
    """Run until a breakpoint (or 3000 instructions: the first interrupt comes after 3.3 ms)."""
    return session.run(max_instructions=3000)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"kind": "write", "address": "4000"},
         {"pc": "C003", "access": "write", "value": "55", "location": "ram:0"}),
        ({"kind": "read", "address": "4000", "location": "ram"},
         {"pc": "C005", "access": "read", "value": "55", "location": "ram:0"}),
        ({"kind": "read", "address": "C020", "location": "rom:3"},
         {"pc": "C006", "access": "read", "value": "AA", "location": "rom3"}),
        ({"kind": "write", "address": "C020", "location": "ram"},
         {"pc": "C009", "access": "write", "value": "AA", "location": "ram:0"}),
        ({"kind": "access", "address": "C010", "end": "C030", "location": "rom"},
         {"pc": "C006", "access": "read", "address": "C020"}),
        ({"kind": "m1", "address": "C006"}, {"pc": "C006", "access": "M1"}),
        ({"kind": "io_out", "address": "DF00", "port_mask": 0xFF00},
         {"pc": "C00F", "access": "out", "address": "DF03", "value": "03", "location": "io"}),
        ({"kind": "rom_select", "rom": 3}, {"pc": "C00F", "kind": "rom_select"}),
        ({"kind": "interrupt"}, {"kind": "interrupt", "access": "interrupt"}),
    ],
)  # fmt: skip
def test_access_breakpoints(arguments: dict, expected: dict) -> None:
    """Each kind stops after the accessing instruction and reports PC, access and location."""
    session = make_session()
    session.breakpoint_add(**arguments)
    result = run(session)
    assert result["reason"] == "breakpoint"
    hit = result["breakpoint"]
    assert {key: hit[key] for key in expected} == expected


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "read", "address": "4000", "location": "rom"},  # the read went to RAM
        {"kind": "read", "address": "C020", "location": "lower"},  # ROM 3, not the lower ROM
        {"kind": "write", "address": "C020", "location": "rom"},  # writes always go to RAM
        {"kind": "rom_select", "rom": 4},
        {"kind": "io_in", "address": "DF00", "port_mask": 0xFF00},
    ],
)
def test_location_and_filters_prevent_stops(arguments: dict) -> None:
    """Location qualifiers and filters that do not match never stop."""
    session = make_session()
    session.breakpoint_add(**arguments)
    assert run(session)["reason"] == "instruction_limit"


def test_pc_breakpoints_single_range_location() -> None:
    """PC breakpoints stop before the instruction; ranges and locations work."""
    session = make_session()
    session.breakpoint_add("C005", end="C006")
    result = run(session)
    assert (result["reason"], result["pc"], result["breakpoint"]["location"]) == (
        "breakpoint", "C005", "rom3",
    )  # fmt: skip
    session = make_session()
    session.breakpoint_add("C005", location="ram")  # the code runs from ROM 3
    assert run(session)["reason"] == "instruction_limit"


def test_enable_remove_and_list() -> None:
    """Disabled breakpoints are kept but ignored; removal unregisters the observer (D-12)."""
    session = make_session()
    added = session.breakpoint_add("4000", kind="write")
    session.breakpoint_enable(added["id"], False)
    assert run(session)["reason"] == "instruction_limit"
    listed = session.breakpoint_list()["breakpoints"]
    assert listed == [{"id": 1, "kind": "write", "enabled": False, "hits": 0,
                       "address": "4000", "location": "any", "label": None}]  # fmt: skip
    session.breakpoint_remove(added["id"])
    assert session._breakpoint_observer is None  # pylint: disable=protected-access


def test_invalid_breakpoints() -> None:
    """Unknown kinds/locations and observer kinds on the flat machine are refused."""
    session = make_session()
    with pytest.raises(SessionError, match="kind must be"):
        session.breakpoint_add("4000", kind="watch")
    with pytest.raises(SessionError, match="location"):
        session.breakpoint_add("4000", kind="read", location="rom:16")
    with pytest.raises(SessionError, match="needs an address"):
        session.breakpoint_add(kind="read")
    with pytest.raises(SessionError, match="CPC 464"):
        Session().breakpoint_add("4000", kind="write")


def test_bus_trace() -> None:
    """The bus trace records filtered accesses with PC and location."""
    session = make_session()
    session.bus_trace_start(kinds=["write"], start="4000", end="FFFF")
    session.step(8)  # 3 OS instructions, then ROM 3 up to ld (&C020),a
    lines = session.bus_trace_read()["lines"]
    assert len(lines) == 2
    assert "PC=C003  write     4000  55  ram:0" in lines[0]
    assert "PC=C009  write     C020  AA  ram:0" in lines[1]
    assert not session.bus_trace_stop()["tracing"]
    with pytest.raises(SessionError, match="kinds"):
        session.bus_trace_start(kinds=["fetch"])


def test_memory_views() -> None:
    """CPU vs RAM under a ROM; raw, decoded and shadow views of a scrambled ROM."""
    machine = make_test_cpc(OS, UPPER)
    scrambled = bytearray(0x4000)
    scrambled[0x14] = 0x77  # mask &28 at offset &14
    machine.memory.set_upper_rom(5, RomSlot(bytes(scrambled), "scr", vortex_decode))
    session = Session(machine)
    session.step(8)  # ld (&C020),a wrote &AA into the RAM under ROM 3
    assert session.memory_view("C020", 1)["hex"] == "AA"
    machine.poke(0xC020, 0x11)
    assert session.memory_view("C020", 1, view="ram")["hex"] == "11"
    assert session.memory_view("C020", 1, view="cpu")["hex"] == "AA"
    assert session.memory_view("C014", 1, view="raw:rom5")["hex"] == "77"
    assert session.memory_view("C014", 1, view="rom5")["hex"] == f"{0x77 ^ 0x28:02X}"
    assert session.memory_view("C014", 1, view="shadow")["hex"] == "??"  # never read
    machine.memory.upper_roms[5].read(0x14, False)
    assert session.memory_view("C014", 1, view="shadow:rom5")["hex"] == f"{0x77 ^ 0x28:02X}"
    assert session.memory_view("C010", 16, view="shadow")["lines"][0].startswith("C010  -- --")
    with pytest.raises(SessionError, match="unknown view"):
        session.memory_view("C000", 1, view="disk")


@requires_roms
def test_load_config_moves_observers() -> None:
    """Replacing the machine moves the breakpoint observer to the new bus."""
    session = make_session()
    session.breakpoint_add("BF00", kind="write")
    session.load_config(str(REPO / "config" / "machine.toml"))
    observer = session._breakpoint_observer  # pylint: disable=protected-access
    assert observer is not None
    assert observer in session.machine.bus._observers  # type: ignore[attr-defined]  # pylint: disable=protected-access
    assert session.run(max_us=2_000_000)["reason"] == "breakpoint"  # the firmware writes the stack


def test_steps_are_recorded_runs_only_when_switched_on(tmp_path: Path) -> None:
    """Stepping fills the trace buffer with the trace off; free runs need the switch."""
    session = Session(make_test_cpc(OS, UPPER), project_dir=tmp_path)
    session.step(3)
    session.step_over()
    lines = session.trace_read(10)["lines"]
    assert len(lines) == 4 and not session.trace_read()["tracing"]
    session.run(max_instructions=20)
    assert len(session.trace_read(100)["lines"]) == 4  # a free run with the trace off: nothing
    session.trace_start(path="out/trace.txt")
    session.run(max_instructions=5)
    session.trace_stop()
    assert len(session.trace_read(100)["lines"]) == 5  # started: buffer cleared, run recorded
    assert len((tmp_path / "out" / "trace.txt").read_text(encoding="utf-8").splitlines()) == 5
    session.step(2)  # the trace is off again: the steps go to the buffer, not to the file
    assert len(session.trace_read(100)["lines"]) == 7
    assert len((tmp_path / "out" / "trace.txt").read_text(encoding="utf-8").splitlines()) == 5
