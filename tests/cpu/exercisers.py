"""Harnesses for the whole-CPU exercisers of phase 2 (design.md §8, D-15).

* ZEXDOC / ZEXALL (Frank Cringle): CP/M programs. The harness loads the `.com` file at &0100
  and intercepts the CP/M BDOS entry at &0005 (function 2 prints a character, function 9 a
  `$`-terminated string). The program ends with `JP 0` (warm boot).
* z80test (Patrik Rak): ZX Spectrum programs. The harness loads the CODE block of the `.tap`
  file at its load address (&8000) and intercepts the Spectrum ROM routines it calls:
  `RST &10` (print the character in A) and CHAN-OPEN at &1601. `IN` from an even port returns
  &BF (no key pressed, MIC bit 0), the value the test expects before it runs the IN tests. The
  program returns to BASIC at the end, i.e. to the return address the harness pushed.

No real CP/M or Spectrum is emulated: the CPU runs on a flat 64 KB `FlatBus`, and the harness
checks the PC before every instruction; when it hits an intercepted address, Python code does
the job of the routine and returns to the caller.

Both programs consist of independent test groups listed in a table of addresses. To run one
group only (in parallel processes, see `tools/run_cpu_validation.py`), the harness rewrites
that table in memory so that it contains just the selected group.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cpcxray.cpu.bus import FlatBus
from cpcxray.cpu.z80 import Z80

EXTERNAL_DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "external"
ZEX_DIR = EXTERNAL_DATA_DIR / "zexall"
Z80TEST_DIR = EXTERNAL_DATA_DIR / "z80test" / "z80test-1.2a"

ZEX_PROGRAMS = ("zexdoc", "zexall")
Z80TEST_VARIANTS = ("z80full", "z80doc", "z80flags", "z80docflags", "z80ccf", "z80memptr")

DEFAULT_INSTRUCTION_LIMIT = 50_000_000_000  # far more than any group needs; stops runaways


class ExerciserError(RuntimeError):
    """The program file has an unexpected layout, or the run did not finish."""


@dataclass
class RunResult:
    """Outcome of one exerciser run (a whole program or a selection of its groups).

    Attributes:
        output: Everything the program printed, as text.
        instructions: Number of instructions executed.
        emulated_us: Emulated time in µs (CPC NOPs).
        finished: True if the program reached its normal end.
    """

    output: str
    instructions: int
    emulated_us: int
    finished: bool


type Trap = Callable[[], bool]
"""An intercepted routine: does its work, returns True to stop the run."""


@dataclass
class TrapRunner:
    """Runs a CPU on a flat 64 KB bus and intercepts calls to selected addresses.

    Attributes:
        bus: The memory/I/O bus.
        cpu: The CPU (reused across runs; building one is slow).
        output: Characters printed by the program so far.
    """

    bus: FlatBus = field(default_factory=FlatBus)
    cpu: Z80 = field(init=False)
    output: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Create the CPU on the bus."""
        self.cpu = Z80(self.bus)

    def reset(self) -> None:
        """Clear memory, output and I/O handlers; reset the CPU."""
        self.bus.mem[:] = bytes(0x10000)
        self.bus.on_in = lambda port: 0xFF
        self.output.clear()
        self.cpu.reset()

    def return_to_caller(self) -> None:
        """Emulate RET: pop the return address into PC."""
        stack_pointer = self.cpu.sp
        low_byte = self.bus.mem[stack_pointer]
        high_byte = self.bus.mem[(stack_pointer + 1) & 0xFFFF]
        self.cpu.sp = (stack_pointer + 2) & 0xFFFF
        self.cpu.pc = high_byte << 8 | low_byte

    def run(self, traps: dict[int, Trap], instruction_limit: int) -> RunResult:
        """Execute instructions until a trap stops the run or the limit is reached.

        Args:
            traps: Intercepted addresses; the handler runs instead of the code at that address.
            instruction_limit: Maximum number of instructions to execute.

        Returns:
            The run result; `finished` is True if a trap stopped the run.
        """
        cpu = self.cpu
        instructions = 0
        emulated_us = 0
        finished = False
        while instructions < instruction_limit:
            trap = traps.get(cpu.pc)
            if trap is not None:
                if trap():
                    finished = True
                    break
                continue
            emulated_us += cpu.step()
            instructions += 1
        return RunResult("".join(self.output), instructions, emulated_us, finished)


# -- ZEXDOC / ZEXALL --------------------------------------------------------------------------

CPM_LOAD_ADDRESS = 0x0100
CPM_STACK_TOP = 0xF000  # stored at &0006 (top of the TPA); ZEX sets SP from it
BDOS_ENTRY = 0x0005
WARM_BOOT = 0x0000


def zex_path(program: str) -> Path:
    """Return the path of zexdoc.com or zexall.com."""
    return ZEX_DIR / f"{program}.com"


def zex_test_table_address(image: bytes) -> int:
    """Find the address of the ZEX test table in the program image.

    The program starts with `JP start`; `start` begins with `LD HL,(6)`, `LD SP,HL`,
    `LD DE,msg1`, `LD C,9`, `CALL bdos`, `LD HL,tests`.

    Raises:
        ExerciserError: If the code does not have that shape.
    """
    if image[0] != 0xC3:
        raise ExerciserError("ZEX: expected JP start at &0100")
    start = (image[1] | image[2] << 8) - CPM_LOAD_ADDRESS
    load_table = start + 12  # LD HL,(6)=3, LD SP,HL=1, LD DE,nn=3, LD C,n=2, CALL nn=3
    if image[start : start + 4] != bytes([0x2A, 0x06, 0x00, 0xF9]) or image[load_table] != 0x21:
        raise ExerciserError("ZEX: unexpected start code, cannot find the test table")
    return image[load_table + 1] | image[load_table + 2] << 8


def zex_group_names(program: str) -> list[str]:
    """Return the names of the ZEX test groups in table order (from the program image).

    Each test case in the table starts with flags, 3x20 bytes of vectors and a 4-byte CRC,
    followed by the `$`-terminated name (see the ZEX source).
    """
    image = zex_path(program).read_bytes()
    memory = bytearray(0x10000)
    memory[CPM_LOAD_ADDRESS : CPM_LOAD_ADDRESS + len(image)] = image
    table = zex_test_table_address(image)
    names = []
    while True:
        case = memory[table] | memory[table + 1] << 8
        if case == 0:
            return names
        name_start = case + 1 + 20 + 20 + 20 + 4
        name_end = memory.index(ord("$"), name_start)
        names.append(memory[name_start:name_end].decode("ascii").rstrip("."))
        table += 2


def run_cpm_program(
    runner: TrapRunner,
    image: bytes,
    prepare: Callable[[bytearray], None] | None = None,
    instruction_limit: int = DEFAULT_INSTRUCTION_LIMIT,
) -> RunResult:
    """Run a CP/M `.com` program image with BDOS functions 2 and 9 intercepted.

    Args:
        runner: The runner to use (reset first).
        image: The program, loaded at &0100.
        prepare: Called with the memory after loading, e.g. to select test groups.
        instruction_limit: Maximum number of instructions.

    Returns:
        The run result; `finished` is True when the program jumped to &0000 (warm boot).
    """
    runner.reset()
    memory = runner.bus.mem
    memory[CPM_LOAD_ADDRESS : CPM_LOAD_ADDRESS + len(image)] = image
    # &0005 holds "JP bdos"; programs read the top of their memory from &0006
    memory[BDOS_ENTRY : BDOS_ENTRY + 3] = bytes([0xC3]) + struct.pack("<H", CPM_STACK_TOP)
    if prepare is not None:
        prepare(memory)

    def bdos() -> bool:
        """BDOS call: function 2 prints E, function 9 prints the string at DE up to `$`."""
        cpu = runner.cpu
        if cpu.c == 2:
            runner.output.append(chr(cpu.e))
        elif cpu.c == 9:
            address = cpu.de
            while memory[address] != ord("$"):
                runner.output.append(chr(memory[address]))
                address = (address + 1) & 0xFFFF
        runner.return_to_caller()
        return False

    runner.cpu.pc = CPM_LOAD_ADDRESS
    runner.cpu.sp = CPM_STACK_TOP
    traps = {BDOS_ENTRY: bdos, WARM_BOOT: lambda: True}
    return runner.run(traps, instruction_limit)


def run_zex(
    runner: TrapRunner,
    program: str,
    groups: list[int] | None = None,
    instruction_limit: int = DEFAULT_INSTRUCTION_LIMIT,
) -> RunResult:
    """Run ZEXDOC or ZEXALL, optionally only some test groups.

    Args:
        runner: The runner to use (reset first).
        program: "zexdoc" or "zexall".
        groups: Indices of the groups to run (table order), or None for all.
        instruction_limit: Maximum number of instructions.

    Returns:
        The run result with the program's printout.
    """
    image = zex_path(program).read_bytes()

    def select_groups(memory: bytearray) -> None:
        """Keep only the selected groups in the ZEX test table."""
        if groups is not None:
            _select_table_entries(memory, zex_test_table_address(image), groups)

    return run_cpm_program(runner, image, select_groups, instruction_limit)


def zex_failures(output: str) -> list[str]:
    """Return the output lines that report a failed ZEX group (they contain "ERROR")."""
    return [line.strip() for line in output.splitlines() if "ERROR" in line]


# -- z80test ----------------------------------------------------------------------------------

SPECTRUM_PRINT = 0x0010  # RST &10: print the character in A
SPECTRUM_CHAN_OPEN = 0x1601  # open a channel; nothing to do here
SPECTRUM_RETURN_ADDRESS = 0x0000  # pushed as return address: reaching it ends the run
SPECTRUM_STACK_TOP = 0x7FF0  # below the code at &8000 (BASIC: CLEAR 32767)
SPECTRUM_KEYBOARD_IDLE = 0xBF  # IN from an even port with no key pressed, MIC bit 0


@dataclass(frozen=True)
class TapeCode:
    """The CODE block of a Spectrum `.tap` file.

    Attributes:
        load_address: Address the code is loaded to.
        data: The code bytes.
    """

    load_address: int
    data: bytes


def z80test_path(variant: str) -> Path:
    """Return the path of a z80test `.tap` file, e.g. z80full.tap."""
    return Z80TEST_DIR / f"{variant}.tap"


def read_tape_code(path: Path) -> TapeCode:
    """Read the first CODE block of a `.tap` file.

    A `.tap` file is a sequence of blocks, each a 16-bit length followed by a flag byte, the
    data and a checksum. A CODE header (flag 0, type 3) gives the load address of the data
    block that follows it.

    Raises:
        ExerciserError: If the file has no CODE block.
    """
    tape = path.read_bytes()
    position = 0
    load_address = None
    while position + 2 <= len(tape):
        length = struct.unpack_from("<H", tape, position)[0]
        block = tape[position + 2 : position + 2 + length]
        position += 2 + length
        is_header = block[0] == 0x00
        if is_header and block[1] == 3:  # CODE header
            load_address = struct.unpack_from("<H", block, 14)[0]
        elif not is_header and load_address is not None:
            return TapeCode(load_address, bytes(block[1:-1]))  # without flag and checksum
    raise ExerciserError(f"{path}: no CODE block")


def z80test_table_address(code: TapeCode) -> int:
    """Find the address of the z80test test table.

    The main loop is entered with `LD BC,0`, `LD HL,testtable`, `JR .entry`.

    Raises:
        ExerciserError: If that code is not found.
    """
    offset = code.data.find(bytes([0x01, 0x00, 0x00, 0x21]))
    if offset < 0 or code.data[offset + 6] != 0x18:
        raise ExerciserError("z80test: cannot find the test table")
    return code.data[offset + 4] | code.data[offset + 5] << 8


def z80test_count(variant: str) -> int:
    """Return the number of tests in a z80test variant (entries in its test table)."""
    code = read_tape_code(z80test_path(variant))
    table_offset = z80test_table_address(code) - code.load_address
    count = 0
    while struct.unpack_from("<H", code.data, table_offset + 2 * count)[0] != 0:
        count += 1
    return count


def run_spectrum_code(
    runner: TrapRunner,
    code: TapeCode,
    prepare: Callable[[bytearray], None] | None = None,
    instruction_limit: int = DEFAULT_INSTRUCTION_LIMIT,
) -> RunResult:
    """Run Spectrum machine code with the ROM print routines intercepted.

    The code is called like `RANDOMIZE USR address` from BASIC: it starts at its load address
    and ends by returning to the caller.

    Args:
        runner: The runner to use (reset first).
        code: The code and its load address.
        prepare: Called with the memory after loading, e.g. to select tests.
        instruction_limit: Maximum number of instructions.

    Returns:
        The run result with the printout (Spectrum control codes translated); `finished` is
        True when the code returned.
    """
    runner.reset()
    memory = runner.bus.mem
    memory[code.load_address : code.load_address + len(code.data)] = code.data
    if prepare is not None:
        prepare(memory)
    runner.bus.on_in = lambda port: SPECTRUM_KEYBOARD_IDLE if port & 1 == 0 else 0xFF
    printer = _SpectrumPrinter(runner.output)

    def print_character() -> bool:
        """RST &10: print the character in A."""
        printer.put(runner.cpu.a)
        runner.return_to_caller()
        return False

    def channel_open() -> bool:
        """CHAN-OPEN: nothing to do."""
        runner.return_to_caller()
        return False

    cpu = runner.cpu
    cpu.sp = SPECTRUM_STACK_TOP
    cpu.push_word(SPECTRUM_RETURN_ADDRESS)
    cpu.pc = code.load_address
    traps = {
        SPECTRUM_PRINT: print_character,
        SPECTRUM_CHAN_OPEN: channel_open,
        SPECTRUM_RETURN_ADDRESS: lambda: True,
    }
    return runner.run(traps, instruction_limit)


def run_z80test(
    runner: TrapRunner,
    variant: str,
    tests: list[int] | None = None,
    instruction_limit: int = DEFAULT_INSTRUCTION_LIMIT,
) -> RunResult:
    """Run a z80test variant, optionally only some tests.

    Args:
        runner: The runner to use (reset first).
        variant: One of `Z80TEST_VARIANTS`.
        tests: Indices of the tests to run (table order), or None for all.
        instruction_limit: Maximum number of instructions.

    Returns:
        The run result with the program's printout.
    """
    code = read_tape_code(z80test_path(variant))

    def select_tests(memory: bytearray) -> None:
        """Keep only the selected tests in the z80test test table."""
        if tests is not None:
            _select_table_entries(memory, z80test_table_address(code), tests)

    return run_spectrum_code(runner, code, select_tests, instruction_limit)


def z80test_failures(output: str) -> list[str]:
    """Return the output lines that report a failed z80test test (they contain "FAILED")."""
    return [line.strip() for line in output.splitlines() if "FAILED" in line]


class _SpectrumPrinter:  # pylint: disable=too-few-public-methods  # one job: translate codes
    """Translates the Spectrum print codes z80test uses into plain text.

    Code 13 is a newline; code 23 (TAB) takes two parameter bytes, rendered as a space;
    code 127 is the copyright sign.
    """

    def __init__(self, output: list[str]) -> None:
        """Print into `output`."""
        self.output = output
        self.parameter_bytes_to_skip = 0

    def put(self, code: int) -> None:
        """Print one character code."""
        if self.parameter_bytes_to_skip:
            self.parameter_bytes_to_skip -= 1
            return
        if code == 23:
            self.parameter_bytes_to_skip = 2
            self.output.append(" ")
        elif code == 13:
            self.output.append("\n")
        elif code == 127:
            self.output.append("©")
        else:
            self.output.append(chr(code))


def _select_table_entries(memory: bytearray, table_address: int, indices: list[int]) -> None:
    """Rewrite a zero-terminated table of 16-bit addresses to contain only `indices`.

    Args:
        memory: The 64 KB memory holding the table.
        table_address: Address of the first table entry.
        indices: Entries to keep, in the order given.
    """
    entries = []
    address = table_address
    while (entry := memory[address] | memory[address + 1] << 8) != 0:
        entries.append(entry)
        address += 2
    selected = [entries[index] for index in indices] + [0]
    for number, entry in enumerate(selected):
        struct.pack_into("<H", memory, table_address + 2 * number, entry)
