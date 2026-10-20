"""Phase 2 milestone: ZEXDOC/ZEXALL and z80test pass (design.md §8, §9; FR-CPU-02).

Three levels:

* Harness tests with tiny hand-written programs (always run, no external data).
* Smoke tests with the real programs: one ZEX group, two z80test tests (fast; skipped when the
  programs are not fetched).
* The complete runs, marked `slow` and excluded by default: `pytest -m slow` (one test per ZEX
  group and per z80test variant). `python -m tools.run_cpu_validation` runs the same in
  parallel and is much faster.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from tests.cpu import exercisers
from tests.cpu.exercisers import (
    CPM_STACK_TOP,
    SPECTRUM_KEYBOARD_IDLE,
    TapeCode,
    TrapRunner,
    run_cpm_program,
    run_spectrum_code,
)

HAVE_ZEX = all(exercisers.zex_path(program).exists() for program in exercisers.ZEX_PROGRAMS)
HAVE_Z80TEST = all(
    exercisers.z80test_path(variant).exists() for variant in exercisers.Z80TEST_VARIANTS
)
needs_zex = pytest.mark.skipif(not HAVE_ZEX, reason="ZEX missing; run tools/fetch_test_data.sh")
needs_z80test = pytest.mark.skipif(
    not HAVE_Z80TEST, reason="z80test missing; run tools/fetch_test_data.sh"
)


@pytest.fixture(name="runner", scope="module")
def fixture_runner() -> TrapRunner:
    """One runner for the whole module (building a CPU is slow)."""
    return TrapRunner()


# -- harness -----------------------------------------------------------------------------------


def test_cpm_bdos_printing(runner: TrapRunner) -> None:
    """BDOS function 9 prints a $-terminated string, function 2 one character; JP 0 ends."""
    program = (
        bytes.fromhex(
            "0E09"  # LD C,9
            "111201"  # LD DE,&0112
            "CD0500"  # CALL 5
            "0E02"  # LD C,2
            "1E21"  # LD E,"!"
            "CD0500"  # CALL 5
            "C30000"  # JP 0
        )
        + b"Hi$"
    )
    result = run_cpm_program(runner, program)
    assert result.finished
    assert result.output == "Hi!"


def test_cpm_top_of_memory_at_address_6(runner: TrapRunner) -> None:
    """Programs that set SP from the word at &0006 (as ZEX does) get the harness stack top."""
    program = bytes.fromhex("2A0600F9C30000")  # LD HL,(6); LD SP,HL; JP 0
    assert run_cpm_program(runner, program).finished
    assert runner.cpu.sp == CPM_STACK_TOP


def test_instruction_limit_stops_a_runaway_program(runner: TrapRunner) -> None:
    """A program that never ends stops at the instruction limit, reported as not finished."""
    result = run_cpm_program(runner, bytes.fromhex("18FE"), instruction_limit=1000)  # JR $
    assert not result.finished
    assert result.instructions == 1000


def test_spectrum_print_in_and_return(runner: TrapRunner) -> None:
    """RST &10 prints A, IN from an even port reads &BF, RET to BASIC ends the run."""
    code = bytes.fromhex(
        "3E58"  # LD A,"X"
        "D7"  # RST &10
        "DBFE"  # IN A,(&FE)
        "320090"  # LD (&9000),A
        "C9"  # RET
    )
    result = run_spectrum_code(runner, TapeCode(0x8000, code))
    assert result.finished
    assert result.output == "X"
    assert runner.bus.mem[0x9000] == SPECTRUM_KEYBOARD_IDLE


def test_spectrum_printer_control_codes() -> None:
    """Code 23 (TAB) and its two parameters become a space, 13 a newline, 127 a ©."""
    output: list[str] = []
    printer = exercisers._SpectrumPrinter(output)  # pylint: disable=protected-access
    for code in b"A\x17\x05\x01B\x0d\x7f":
        printer.put(code)
    assert "".join(output) == "A B\n©"


def test_read_tape_code(tmp_path: Path) -> None:
    """The CODE block of a .tap file is found with its load address from the header."""

    def block(payload: bytes) -> bytes:
        """Return a tape block: length, payload, checksum (not checked by the reader)."""
        return struct.pack("<H", len(payload) + 1) + payload + b"\x00"

    data = bytes(range(10))
    header = bytes([0x00, 3]) + b"test      " + struct.pack("<HHH", len(data), 0x8000, 0)
    tape = tmp_path / "test.tap"
    tape.write_bytes(block(header) + block(b"\xff" + data))
    assert exercisers.read_tape_code(tape) == TapeCode(0x8000, data)


def test_select_table_entries() -> None:
    """Rewriting a test table keeps only the selected entries, zero-terminated."""
    memory = bytearray(0x10000)
    struct.pack_into("<5H", memory, 0x1000, 0x2000, 0x2100, 0x2200, 0x2300, 0)
    exercisers._select_table_entries(memory, 0x1000, [2, 0])  # pylint: disable=protected-access
    assert struct.unpack_from("<3H", memory, 0x1000) == (0x2200, 0x2000, 0)


# -- real programs: discovery and smoke tests --------------------------------------------------


@needs_zex
def test_zex_groups_found() -> None:
    """Both ZEX programs list the same 67 test groups."""
    zexdoc = exercisers.zex_group_names("zexdoc")
    assert len(zexdoc) == 67
    assert exercisers.zex_group_names("zexall") == zexdoc
    assert zexdoc[0] == "<adc,sbc> hl,<bc,de,hl,sp>"


@needs_z80test
def test_z80test_tests_found() -> None:
    """Every z80test variant has 160 tests."""
    for variant in exercisers.Z80TEST_VARIANTS:
        assert exercisers.z80test_count(variant) == 160


@needs_zex
def test_zexdoc_smoke(runner: TrapRunner) -> None:
    """One short ZEXDOC group passes."""
    names = exercisers.zex_group_names("zexdoc")
    result = exercisers.run_zex(runner, "zexdoc", groups=[names.index("ld (<bc,de>),a")])
    assert result.finished
    assert "OK" in result.output
    assert not exercisers.zex_failures(result.output)


@needs_z80test
def test_z80test_smoke(runner: TrapRunner) -> None:
    """The z80test self test and the SCF test pass."""
    result = exercisers.run_z80test(runner, "z80full", tests=[0, 1])
    assert result.finished
    assert "Result: all tests passed." in result.output


# -- complete runs (pytest -m slow) ------------------------------------------------------------

ZEX_GROUPS = [
    pytest.param(program, index, id=f"{program}-{name}")
    for program in exercisers.ZEX_PROGRAMS
    for index, name in (enumerate(exercisers.zex_group_names(program)) if HAVE_ZEX else [])
]


@pytest.mark.slow
@needs_zex
@pytest.mark.parametrize(("program", "group"), ZEX_GROUPS)
def test_zex_group(runner: TrapRunner, program: str, group: int) -> None:
    """One ZEXDOC/ZEXALL test group passes (CRC over all flag and register results)."""
    result = exercisers.run_zex(runner, program, groups=[group])
    assert result.finished
    assert not exercisers.zex_failures(result.output), result.output


@pytest.mark.slow
@needs_z80test
@pytest.mark.parametrize("variant", exercisers.Z80TEST_VARIANTS)
def test_z80test_variant(runner: TrapRunner, variant: str) -> None:
    """A complete z80test variant reports "all tests passed"."""
    result = exercisers.run_z80test(runner, variant)
    assert result.finished
    assert "Result: all tests passed." in result.output, result.output
