"""Session tools for the known OS and AMSDOS variables (FR-DBG-11): names, lookups, breakpoints.

The test CPC is a 464 (firmware variables of the 464) whose ROM 3 program writes the third
byte of KL_TIME (&B187-&B18A on the 464); no real ROMs needed.
"""

from __future__ import annotations

import pytest

from cpcxray.session.errors import SessionError
from cpcxray.session.session import Session
from tests.analysis.helpers import make_test_cpc

# OS ROM: upper ROM on, JP &C000; interrupt handler at &0038: EI / RET.
OS = {0x0000: "01817F ED49 C300C0", 0x0038: "FB C9"}
# ROM 3: C000 ld a,&07 / C002 ld (&B189),a / C005 jr C005
UPPER = {0x0000: "3E07 3289B1 18FE"}


def make_session() -> Session:
    """A session on the test 464 (ROM 3 selected, PC = 0)."""
    return Session(make_test_cpc(OS, UPPER))


def test_operands_and_lookups_name_variables() -> None:
    """Disassembly names the operand NAME+n; owner symbols win; explain and list find it."""
    session = make_session()
    session.run(max_instructions=2)  # the OS ROM switches the upper ROM on
    assert "ld (KL_TIME+2),a" in session.disassemble("C002", 1)["lines"][0]
    explained = session.address_explain("B189")
    assert explained["model"] == "464"
    assert [v["name"] for v in explained["variables"]] == ["KL_TIME+2", "KL_WORKSPACE+137"]
    assert explained["variables"][0]["size"] == 4
    assert session.address_explain("KL_TIME")["address"] == "B187"  # labels as addresses
    found = session.variable_list("time")
    assert "KL_TIME" in [v["label"] for v in found["variables"]]
    session.symbol_add("B189", "my_clock")
    assert "ld (my_clock),a" in session.disassemble("C002", 1)["lines"][0]


def test_breakpoint_on_a_variable_covers_it_and_names_the_stop() -> None:
    """A write breakpoint on KL_TIME covers its 4 bytes; the stop says KL_TIME+2."""
    session = make_session()
    added = session.breakpoint_add("KL_TIME", "write")
    assert (added["address"], added["end"], added["variable"]) == ("B187", "B18A", "KL_TIME")
    result = session.run(max_instructions=100)
    assert result["reason"] == "breakpoint"
    assert result["breakpoint"]["variable"] == "KL_TIME+2"
    assert result["breakpoint"]["address"] == "B189"
    single = session.breakpoint_add("KL_TIME", "write", end="B187")  # an explicit end wins
    assert "end" not in single


def test_amsdos_variables_need_amsdos() -> None:
    """Without AMSDOS in the ROM chain its variable names are unknown."""
    session = make_session()
    assert not session.address_explain("BE7D")["variables"]
    with pytest.raises(SessionError, match="known label"):
        session.breakpoint_add("AMSDOS_DEFAULT_DRIVE", "write")


def test_flat_machine_has_no_variables() -> None:
    """The bare Z80 machine knows no CPC variables."""
    session = Session()
    assert not session.address_explain("B187")["variables"]
    assert session.variable_list() == {"variables": [], "total": 0}
    with pytest.raises(SessionError, match="known label"):
        session.address_explain("KL_TIME")


def test_memory_view_reports_paging_and_the_variables_it_shows() -> None:
    """Variables appear where the view shows RAM at their CPU address (FR-DBG-11, -12)."""
    session = make_session()
    session.run(max_instructions=2)  # upper ROM 3 on
    cpu = session.memory_view("B185", 8)
    assert [q["text"] for q in cpu["paging"]] == ["RAM block 2"]
    labels = [v["label"] for v in cpu["variables"]]
    assert labels == ["KL_WORKSPACE", "KL_INTERRUPT_STACK", "KL_TIME"]  # &B185-&B18C
    assert session.memory_view("B185", 8, "ram")["variables"] == cpu["variables"]
    assert session.memory_view("B185", 8, "ram:2")["variables"] == cpu["variables"]
    assert not session.memory_view("3185", 8, "ram:2")["variables"]  # block 2 is not at &3185
    rom = session.memory_view("C000", 4, "rom3")
    assert rom["paging"][0]["text"].startswith("upper ROM 3 (") and not rom["variables"]
    assert session.memory_view("BFFC", 8)["paging"][1]["source"] == "rom3"  # CPU view at &C000


def test_bus_trace_names_the_variables_it_touched() -> None:
    """A RAM write to KL_TIME+2 ends with the name; ROM reads and M1 fetches stay plain."""
    session = make_session()
    session.bus_trace_start(kinds=["write", "read"])
    session.run(max_instructions=5)
    lines = session.bus_trace_read(50)["lines"]
    assert any(line.endswith("write     B189  07  ram:2  KL_TIME+2") for line in lines)
    reads = [line for line in lines if " read " in line]
    assert reads and all(line.endswith(("lower", "rom3")) for line in reads)  # ROM: no names
