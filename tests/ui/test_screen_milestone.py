"""Phase 5 milestone: boot screen visible; MODE 0/1/2, INK, BORDER and scrolling correct.

After the real boot, tests/data/asm/screen_test.asm (assembled here with sjasmplus) calls the
firmware: SCR SET MODE, SCR SET BORDER (bright red), SCR SET INK for all 16 pens, one line of
blocks per pen, and optionally enough lines to scroll. The rendered image is checked at known
pixels; the PNGs are written to output/ for visual inspection.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from cpcxray.machine.config import load_config
from cpcxray.machine.cpc464 import CPC464
from cpcxray.session.session import Session
from cpcxray.ui.palette import HARDWARE_RGB
from cpcxray.ui.renderer import HEIGHT, WIDTH, render
from tests.vdos import run_until_ready

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "tests" / "data" / "asm" / "screen_test.asm"
CONFIG = load_config(REPO / "config" / "machine.toml")
pytestmark = pytest.mark.skipif(
    CONFIG.lower_rom is None or not CONFIG.lower_rom.exists or shutil.which("sjasmplus") is None,
    reason="ROMs or sjasmplus missing",
)
INK_TABLE = [1, 24, 20, 6, 26, 0, 2, 8, 10, 12, 14, 16, 18, 22, 3, 15]  # firmware colours
FIRMWARE_TO_HW = {6: 0x0C, 24: 0x0A, 20: 0x13, 1: 0x04}


@pytest.fixture(name="program", scope="module")
def fixture_program(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    """Assemble the screen test program with sjasmplus."""
    workdir = tmp_path_factory.mktemp("asm")
    shutil.copy(SOURCE, workdir)
    subprocess.run(
        ["sjasmplus", "--nologo", SOURCE.name], cwd=workdir, check=True, capture_output=True
    )
    return (workdir / "screen_test.bin").read_bytes()


def run_screen_test(program: bytes, mode: int, pens: int, lines: int) -> Session:
    """Boot, then call the program with A = mode, B = pens, C = scroll lines."""
    session = Session(CPC464(CONFIG))
    run_until_ready(session)
    session.memory_write("4000", program.hex())
    session.memory_write("4100", "18FE")  # return target: JR $
    stack = session.registers_get()["sp"] - 2
    session.memory_write(f"{stack:04X}", "0041")
    session.registers_set({"sp": stack, "pc": 0x4000, "a": mode, "b": pens, "c": lines})
    assert session.run_until("4100", max_us=5_000_000, timeout_s=60)["reason"] == "address_reached"
    session.screen_image(f"output/screen_mode{mode}.png")
    return session


def colour_at(session: Session, x: int, y: int) -> tuple[int, int, int]:
    """RGB of the rendered (not line-doubled) image at x, y."""
    image = render(session.machine.snapshot())  # type: ignore[attr-defined]
    return tuple(image[y, x, :3].tolist())  # type: ignore[return-value]


def test_mode0_inks_and_border(program: bytes) -> None:
    """MODE 0: 20 columns; pen 3's blocks are bright red, pen 1's bright yellow; red border."""
    session = run_screen_test(program, 0, 16, 0)
    assert session.machine.gate_array.mode == 0  # type: ignore[attr-defined]
    top, left = (HEIGHT - 200) // 2, (WIDTH - 640) // 2
    block_x = left + 9 * 32 + 16  # column 9 (after "Pen n"), mode 0 characters are 32 px wide
    assert colour_at(session, 0, 0) == HARDWARE_RGB[FIRMWARE_TO_HW[6]]  # border
    assert colour_at(session, block_x, top + 1 * 8 + 4) == HARDWARE_RGB[FIRMWARE_TO_HW[24]]
    assert colour_at(session, block_x, top + 3 * 8 + 4) == HARDWARE_RGB[FIRMWARE_TO_HW[6]]
    assert colour_at(session, block_x, top + 2 * 8 + 4) == HARDWARE_RGB[FIRMWARE_TO_HW[20]]


def test_mode1_scrolling(program: bytes) -> None:
    """MODE 1: 34 printed lines scroll the screen in hardware (R12/R13 change)."""
    session = run_screen_test(program, 1, 4, 30)
    crtc = session.machine.crtc.registers  # type: ignore[attr-defined]
    assert (crtc[12], crtc[13]) != (0x30, 0x00)
    lines = session.screen_text()["lines"]
    assert lines[0] == "Line 06" and lines[24] == "Line 30"


def test_mode2(program: bytes) -> None:
    """MODE 2: 80 columns, the narrow font is recognised."""
    session = run_screen_test(program, 2, 2, 0)
    assert session.machine.gate_array.mode == 2  # type: ignore[attr-defined]
    assert session.screen_text()["lines"][1].startswith("Pen 1")
