"""Phase 9 debugger tools: all breakpoint kinds and locations, bus trace, memory views (FR-DBG)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.decoding.vortex import vortex_decode
from cpcxray.machine.memory import RomSlot
from cpcxray.session.errors import SessionError
from cpcxray.session.session import Session
from tests.analysis.helpers import make_test_cpc
from tests.machines import REPO, requires_roms

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
         {"pc": "C003", "access": "write", "value": "55", "location": "ram:1"}),
        ({"kind": "read", "address": "4000", "location": "ram"},
         {"pc": "C005", "access": "read", "value": "55", "location": "ram:1"}),
        ({"kind": "read", "address": "C020", "location": "rom:3"},
         {"pc": "C006", "access": "read", "value": "AA", "location": "rom3"}),
        ({"kind": "write", "address": "C020", "location": "ram"},
         {"pc": "C009", "access": "write", "value": "AA", "location": "ram:3"}),
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
    with pytest.raises(SessionError, match="CPC machine"):
        Session().breakpoint_add("4000", kind="write")


def test_bus_trace() -> None:
    """The bus trace records filtered accesses with PC and location."""
    session = make_session()
    session.bus_trace_start(kinds=["write"], start="4000", end="FFFF")
    session.step(8)  # 3 OS instructions, then ROM 3 up to ld (&C020),a
    lines = session.bus_trace_read()["lines"]
    assert len(lines) == 2
    assert "PC=C003  write     4000  55  ram:1" in lines[0]
    assert "PC=C009  write     C020  AA  ram:3" in lines[1]
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
    assert session.memory_view("0014", 1, view="rom5")["hex"] == "??"  # only at &C000-&FFFF
    edge = session.memory_view("BFFF", 2, view="raw:rom5")["hex"]
    assert edge[:2] == "??" and edge[2:] != "??"  # nothing before &C000, its first byte at it
    with pytest.raises(SessionError, match="unknown view"):
        session.memory_view("C000", 1, view="disk")


@requires_roms
def test_load_config_moves_observers() -> None:
    """Replacing the machine moves the breakpoint observer to the new bus."""
    session = make_session()
    session.breakpoint_add("BFFE", kind="write")
    session.load_config(str(REPO / "config" / "test_cpc464.toml"))
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
    session.run(max_instructions=10)  # a free run with the trace off: one gap, merged
    lines = session.trace_read(100)["lines"]
    assert len(lines) == 5 and lines[-1].startswith("---- free run not recorded: 30 instructions")
    session.trace_start(path="out/trace.txt")
    session.run(max_instructions=5)
    session.trace_stop()
    assert len(session.trace_read(100)["lines"]) == 5  # started: buffer cleared, run recorded
    assert len((tmp_path / "out" / "trace.txt").read_text(encoding="utf-8").splitlines()) == 5
    session.step(2)  # the trace is off again: the steps go to the buffer, not to the file
    assert len(session.trace_read(100)["lines"]) == 7
    assert len((tmp_path / "out" / "trace.txt").read_text(encoding="utf-8").splitlines()) == 5


def test_paging_names_the_rom_or_ram_per_quarter() -> None:
    """After the OS ROM enables the upper ROM, &C000 shows ROM 3; the RAM below stays block 3."""
    session = make_session()
    assert session.paging_get()["quarters"][3]["text"] == "RAM block 3"  # upper ROM off at reset
    session.run(max_instructions=2)  # ld bc,#7F81 / out (c),c
    quarters = session.paging_get()["quarters"]
    assert [q["source"] for q in quarters] == ["lower", "ram", "ram", "rom3"]
    assert quarters[3]["ram_block"] == 3 and quarters[3]["text"].startswith("upper ROM 3 (")
    assert "ram_configuration" not in session.paging_get()  # a 464 has no banking


def test_trace_records_with_memory_accesses(tmp_path: Path) -> None:
    """Stepped instructions carry their reads and writes (not their own operand bytes)."""
    session = Session(make_test_cpc({}, {}), project_dir=tmp_path)
    session.memory_write("9000", "3A0080 CD0A90")  # ld a,(#8000) / call #900A
    session.memory_write("8000", "3E")
    session.registers_set({"pc": 0x9000, "sp": 0xA000})
    session.step()
    session.step()
    load, call = session.trace_records(2)["records"]
    assert load["accesses"] == [{"access": "read", "address": "8000", "value": "3E"}]
    assert call["accesses"] == [{"access": "write", "address": "9FFF", "value": "90"},
                                {"access": "write", "address": "9FFE", "value": "06"}]  # fmt: skip


def test_trace_records_carry_comment_blocks(tmp_path: Path) -> None:
    """A record has the owner's blocks of its address where it ran (task 9); others none."""
    session = Session(make_test_cpc({}, {}), project_dir=tmp_path)
    session.memory_write("9000", "00 00")  # nop / nop (RAM: the global scope)
    session.symbol_add("9001", before="the second nop\n  waits", after="done")
    session.symbol_add("9001", before="in ROM 3 only", scope="rom3")  # not where it ran
    session.registers_set({"pc": 0x9000, "sp": 0xA000})
    session.step(2)
    first, second = session.trace_records(2)["records"]
    assert "comment_before" not in first and "comment_after" not in first
    assert (second["comment_before"], second["comment_after"]) == ("the second nop\n  waits",
                                                                   "done")  # fmt: skip


def test_bus_trace_view(tmp_path: Path) -> None:
    """Grouped per instruction; roles, words, stack meanings; the window; clearing."""
    session = Session(make_test_cpc({}, {}), project_dir=tmp_path)
    session.memory_write("9000", "C5 CD0590 76 C9")  # push bc / call #9005 / halt / ret
    session.registers_set({"pc": 0x9000, "sp": 0xA000, "bc": 0x7F85})
    session.bus_trace_start()
    for _ in range(3):
        session.step()
    view = session.bus_trace_view()
    groups = view["groups"]
    assert len(groups) == 3
    push, call, ret = groups[0], groups[1], groups[2]
    assert (push["pc"], push["mnemonic"], push["operands"]) == ("9000", "push", "bc")
    assert [(a["access"], a["address"], a["value"], a["word"], a["meaning"])
            for a in push["accesses"]] == [("write", "9FFE", "7F85", True, "stack")]  # fmt: skip
    assert call["accesses"][0]["meaning"] == "stack: return address"
    assert ret["accesses"][0]["meaning"] == "stack: returns to 9004"
    detailed = session.bus_trace_view(fetches=True, combine=False)["groups"][1]["accesses"]
    assert [a["role"] for a in detailed] == ["fetch", "operand", "operand", "data", "data"]
    window = session.bus_trace_view(top=1, count=2)
    assert window["top"] == 1 and window["count"] == 2 and window["total"] == view["total"]
    writes = session.bus_trace_view(types=["write"])["groups"]  # the view filter
    assert [g["pc"] for g in writes] == ["9000", "9001"]  # ret only reads: left out
    with pytest.raises(SessionError, match="unknown access types"):
        session.bus_trace_view(types=["fetch"])
    assert session.bus_trace_clear()["cleared"] and session.bus_trace_view()["total"] == 0


def test_bus_trace_records_steps_without_recording(tmp_path: Path) -> None:
    """Stepping always lands in the bus trace; free runs only while "Recording" is on."""
    session = Session(make_test_cpc({}, {}), project_dir=tmp_path)
    session.memory_write("9000", "C5 C5 18FE")  # push bc / push bc / jr $
    session.registers_set({"pc": 0x9000, "sp": 0xA000})
    session.step()
    assert not session.bus_trace.enabled and session.bus_trace_view()["groups"][0]["pc"] == "9000"
    assert not session._bus_trace_observing  # pylint: disable=protected-access  # off the bus again
    before = session.bus_trace_view()["total"]
    session.run(max_instructions=50)  # a free run: not recorded with the switch off - a gap
    view = session.bus_trace_view()
    assert view["total"] == before + 1 and view["groups"][-1]["gap"]["instructions"] == 50


def test_memory_follow_and_changes(tmp_path: Path) -> None:
    """Follow CPU: writes since the last stop, the last step's accesses, off the bus again."""
    session = Session(make_test_cpc({}, {}), project_dir=tmp_path)
    session.memory_write("9000", "3A0080 320090 320190 18F5")  # read / 2 writes / jr back
    session.registers_set({"pc": 0x9000, "sp": 0xA000})
    assert session.memory_changes_get()["following"] is False
    session.memory_follow(True)
    session.step()
    changes = session.memory_changes_get()
    last_step = changes["last_step"] or {}
    assert last_step["accesses"] == [{"access": "read", "address": "8000",
                                                 "value": "00"}]  # fmt: skip
    session.step()
    changes = session.memory_changes_get()
    assert [w["address"] for w in changes["written"]] == ["9000"]  # this step only
    session.run(max_instructions=7)  # a run: everything it writes
    changes = session.memory_changes_get()
    assert changes["written_count"] == 2 and changes["last_step"] is None
    last_written = changes["last_written"] or {}
    assert last_written["address"] in ("9000", "9001")
    session.memory_follow(False)
    assert not session.memory_changes_get()["written"]
