"""Helpers for the analysis tests: a small test CPC, views, source round trips."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from cpcxray.analysis.classifier import TargetView, classify_target
from cpcxray.analysis.cpc_profile import CpcProfile
from cpcxray.analysis.database import TargetDatabase
from cpcxray.analysis.provenance import RelocatedBlock
from cpcxray.analysis.source_gen import GeneratedSource, generate_source
from cpcxray.analysis.targets import INSN_START
from cpcxray.analysis.verify import VerifyResult, verify_source
from cpcxray.debugger.symbols import SymbolTable
from cpcxray.machine.cpc464 import CPC464
from cpcxray.machine.memory import RomSlot


def plain_view(
    image: bytes, start: int = 0xC000, blocks: tuple[RelocatedBlock, ...] = (), name: str = "rom1"
) -> TargetView:
    """A view of a plain (not scrambled) target."""
    return TargetView(name, start, image, blocks=blocks)


def classified(
    view: TargetView,
    starts: Iterable[int] = (),
    observations: dict[str, dict[str, object]] | None = None,
    static: bool = True,
) -> TargetDatabase:
    """Build the database: observed starts (offsets), observations, optional static descent."""
    database = TargetDatabase(view.name, view.start, view.length)
    database.observations = dict(observations or {})
    flags = bytearray(view.length)
    for offset in starts:
        flags[offset] |= INSN_START
    profile = CpcProfile(image_range=(view.start, view.start + view.length - 1))
    classify_target(database, view, bytes(flags), profile, static)
    return database


def generate(
    view: TargetView, database: TargetDatabase, fill_policy: str = "static", **options: object
) -> GeneratedSource:
    """Generate the source of a classified view."""
    return generate_source(
        view.resolve(database, fill_policy), database, SymbolTable(), title="test",
        output_name="test.bin", fill_policy=fill_policy, **options,  # type: ignore[arg-type]
    )  # fmt: skip


def round_trip(
    tmp_path: Path, view: TargetView, database: TargetDatabase, fill_policy: str = "static"
) -> tuple[GeneratedSource, VerifyResult]:
    """Generate, write, assemble with sjasmplus and compare with the final image."""
    generated = generate(view, database, fill_policy)
    source = tmp_path / "test.asm"
    source.write_text(generated.text, encoding="utf-8")
    expected = view.final_image(database, fill_policy)
    return generated, verify_source(source, expected, view.start, generated.line_of_offset)


def _rom_image(code: dict[int, str]) -> bytes:
    """A 16 KB ROM image with hex code placed at the given offsets."""
    image = bytearray(0x4000)
    for offset, text in code.items():
        data = bytes.fromhex(text)
        image[offset : offset + len(data)] = data
    return bytes(image)


def make_test_cpc(os_code: dict[int, str], upper_code: dict[int, str]) -> CPC464:
    """A CPC 464 with a test OS ROM and a test ROM in slot 3 (selected), SP = &8000."""
    machine = CPC464()
    machine.memory.set_lower_rom(RomSlot(_rom_image(os_code), "os"))
    machine.memory.set_upper_rom(3, RomSlot(_rom_image(upper_code), "test"))
    machine.reset()
    machine.cpu.sp = 0x8000
    machine.rom_select.io_out(0xDF03, 3)
    return machine


def run_until_halt(machine: CPC464, limit: int = 200) -> None:
    """Step until HALT (fails after `limit` instructions)."""
    for _ in range(limit):
        machine.step()
        if machine.cpu.halted:
            return
    raise AssertionError("program did not halt")
