"""Tests of the Session façade on the phase 3 flat machine (D-27, FR-DBG-02..09, FR-MCP-04/05)."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.session.session import Session, SessionError

# &8000: LD A,5 / CALL &800A / INC A / HALT ... &800A: LD B,3 / loop: DJNZ loop / RET
PROGRAM = bytes.fromhex("3E05 CD0A80 3C 76 000000 0603 10FE C9")


@pytest.fixture(name="session")
def fixture_session(tmp_path: Path) -> Session:
    """A session with PROGRAM at &8000, PC at &8000 and SP at &F000."""
    (tmp_path / "prog.bin").write_bytes(PROGRAM)
    session = Session(project_dir=tmp_path)
    session.memory_load("8000", "prog.bin")
    session.registers_set({"pc": 0x8000, "sp": 0xF000})
    return session


@pytest.mark.parametrize("address", [0x8002, "8002", "&8002", "#8002", "0x8002"])
def test_address_forms(session: Session, address: int | str) -> None:
    """Addresses may be ints or hex strings with any of the usual prefixes."""
    assert session.memory_read(address, 1)["hex"] == "CD"


def test_address_by_label(session: Session) -> None:
    """Labels (user or firmware) are accepted wherever an address is."""
    session.symbol_add("800A", "subroutine")
    assert session.disassemble("subroutine", 1)["lines"] == [
        "subroutine:",
        "800A  06 03        ld b,#03",
    ]
    assert session.memory_read("txt_output", 1)["address"] == "BB5A"
    with pytest.raises(SessionError, match="neither a hex address nor a known label"):
        session.memory_read("no_such_label")


def test_step_and_step_over(session: Session) -> None:
    """Step executes one instruction; step_over runs a CALL to completion."""
    assert session.step()["pc"] == "8002"
    result = session.step_over()
    assert (result["reason"], result["pc"], result["instructions"]) == (
        "address_reached",
        "8005",
        6,
    )


def test_step_over_djnz_and_plain_instruction(session: Session) -> None:
    """step_over finishes a DJNZ loop; other instructions are single-stepped."""
    session.registers_set({"pc": 0x800C, "b": 3})
    assert session.step_over()["pc"] == "800E"
    assert session.step_over()["reason"] == "steps_done"  # RET is single-stepped


def test_step_out(session: Session) -> None:
    """step_out returns from the current subroutine to the caller."""
    session.run_until("800C")  # inside the subroutine
    result = session.step_out()
    assert (result["reason"], result["pc"]) == ("returned", "8005")


def test_run_stops_at_halt_and_breakpoints(session: Session) -> None:
    """Run stops at a breakpoint, continues from it, and stops at HALT with DI."""
    breakpoint_id = session.breakpoint_add("800A")["id"]
    first = session.run()
    assert (first["reason"], first["pc"]) == ("breakpoint", "800A")
    assert session.breakpoint_list()["breakpoints"][0]["hits"] == 1
    session.breakpoint_remove(breakpoint_id)
    second = session.run()
    assert (second["reason"], second["pc"]) == ("halted", "8007")


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [({"max_instructions": 2}, "instruction_limit"), ({"max_us": 3}, "time_limit")],
)
def test_run_limits(session: Session, arguments: dict[str, int], reason: str) -> None:
    """Run limits stop execution with the matching reason (FR-MCP-04)."""
    assert session.run(**arguments)["reason"] == reason


def test_wall_clock_timeout(session: Session) -> None:
    """An endless loop stops at the wall-clock timeout."""
    session.memory_write("9000", "18FE")  # JR $
    session.registers_set({"pc": 0x9000})
    assert session.run(timeout_s=0.2)["reason"] == "timeout"


def test_invalid_limits(session: Session) -> None:
    """Out-of-range limits are rejected."""
    for arguments in ({"timeout_s": 0}, {"timeout_s": 10_000}, {"max_instructions": 0}):
        with pytest.raises(SessionError):
            session.run(**arguments)  # type: ignore[arg-type]


def test_memory_operations(session: Session, tmp_path: Path) -> None:
    """write, read (hex dump with ASCII), search and save to a file."""
    session.memory_write("9000", "48 65 6C 6C 6F")
    dump = session.memory_read("9000", 5)
    assert dump["hex"] == "48656C6C6F"
    assert dump["lines"][0].endswith("Hello")
    assert session.memory_search("6C6C")["matches"] == ["9002"]
    saved = session.memory_save("9000", 5, "output/hello.bin")
    assert Path(saved["path"]) == tmp_path / "output" / "hello.bin"
    assert (tmp_path / "output" / "hello.bin").read_bytes() == b"Hello"


def test_memory_limits(session: Session) -> None:
    """Over-long reads, bad hex and out-of-range loads are rejected (FR-MCP-05)."""
    with pytest.raises(SessionError, match="memory_save"):
        session.memory_read(0, 5000)
    with pytest.raises(SessionError, match="hex"):
        session.memory_write(0, "xyz")
    with pytest.raises(SessionError, match="not found"):
        session.memory_load(0, "missing.bin")


def test_disassemble_uses_symbols_and_comments(session: Session) -> None:
    """Calls show the label of their target; user comments appear after the instruction."""
    session.symbol_add("800A", "count_down", "loops B times")
    lines = session.disassemble("8002", 1)["lines"]
    assert " ".join(lines[0].split()) == "8002 CD 0A 80 call count_down"
    lines = session.disassemble("800A", 1)["lines"]
    assert lines[0] == "count_down:"
    assert lines[1].endswith("; loops B times")


def test_immediates_named_only_when_used_as_addresses(session: Session) -> None:
    """`ld rr,nn` names its value as the source generator does (analysis/operands.py).

    A counter is a number, never the low restart of the same value; a pointer keeps its name;
    a value of unknown use loses only the low firmware names.
    """
    program = bytes.fromhex(
        "010800 10FE"  # ld bc,#0008 / djnz: a counter, not LOW_JUMP
        "210800 7E"  # ld hl,#0008 / ld a,(hl): an address
        "110800 C9"  # ld de,#0008 / ret: unknown use
        "115ABB C9"  # ld de,#BB5A / ret: unknown use, jumpblock name kept
    )
    session.memory_write("9000", program.hex())
    lines = [" ".join(line.split()[-2:]) for line in session.disassemble("9000", 8)["lines"]]
    assert lines[0] == "ld bc,#0008"
    assert lines[2] == "ld hl,LOW_JUMP"
    assert lines[4] == "ld de,#0008"
    assert lines[6] == "ld de,TXT_OUTPUT"
    session.symbol_add("0008", "my_vector", scope="")
    assert session.disassemble("9009", 1)["lines"][0].endswith("ld de,my_vector")


def test_trace(session: Session, tmp_path: Path) -> None:
    """The trace records executed instructions in the ring buffer and the text file."""
    session.trace_start("output/trace.txt", "8000", "8007")
    session.run()
    stopped = session.trace_stop()
    lines = session.trace_read(10)["lines"]
    assert lines[0].startswith("8000  3E 05")
    assert all(line[:4] <= "8007" for line in lines)  # the subroutine at &800A is filtered
    assert stopped["records"] == len(lines) == 4
    assert (tmp_path / "output" / "trace.txt").read_text().splitlines() == lines


def test_symbols_save_and_load(session: Session, tmp_path: Path) -> None:
    """User symbols are saved to project/annotations.txt and loaded by a new session."""
    session.symbol_add("8000", "start", "entry point")
    session.comment_add("9000", "buffer")
    assert session.symbols_save()["saved"] == 2
    fresh = Session(project_dir=tmp_path)  # loads project/annotations.txt automatically
    listed = fresh.symbol_list(source="user")["symbols"]
    assert [entry["label"] for entry in listed] == ["start", None]
    assert fresh.symbol_remove("8000")["removed"] == "start"


def test_symbol_list_limit(session: Session) -> None:
    """symbol_list searches the firmware names and limits the result."""
    result = session.symbol_list("km_", limit=3)
    assert len(result["symbols"]) == 3
    assert result["total"] > 3


def test_machine_status_and_reset(session: Session) -> None:
    """machine_status reports model, PC and clock; reset clears CPU state and clock."""
    session.step(3)
    status = session.machine_status()
    assert (status["model"], status["pc"]) == ("flat-z80", "800C")  # LD A, CALL, LD B
    assert status["clock_us"] > 0
    after = session.reset()
    assert (after["pc"], after["clock_us"]) == ("0000", 0)
    assert session.memory_read("8000", 2)["hex"] == "3E05"  # memory survives the reset


def test_stack_changed_registers_and_rows(session: Session) -> None:
    """stack_get follows SP loads; a step reports what it changed; rows carry name types."""
    session.memory_write("9000", "3100A0 C5 CD5ABB")  # ld sp,#A000 / push bc / call TXT_OUTPUT
    session.registers_set({"pc": 0x9000, "bc": 0x1234})
    result = session.step()
    assert result["changed"] == ["sp", "pc"] and session.last_stop_get()["stop"] == result
    session.step()  # push bc
    stack = session.stack_get(4)
    assert (stack["sp"], stack["top"], stack["source"]) == ("9FFE", "A000",
                                                             "ld sp at &9000 (RAM)")  # fmt: skip
    assert stack["words"] == [{"address": "9FFE", "value": "1234"}]  # up to the top only
    rows = session.disassemble("9004", 1)["rows"]
    assert rows == [{"kind": "instruction", "address": "9004", "bytes": "CD 5A BB",
                     "mnemonic": "call", "operands": "TXT_OUTPUT",
                     "name": "TXT_OUTPUT", "name_type": "FW"}]  # fmt: skip
    session.registers_set({"sp": 0x4000})
    assert session.stack_get()["source"] == "set by the debugger"
