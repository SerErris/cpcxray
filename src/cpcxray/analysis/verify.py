"""Round-trip check: assemble a generated source and compare it byte for byte (D-33, §6.15).

`verify_source` runs sjasmplus on the source in a temporary directory (the OUTPUT file of the
source lands there too, so nothing in the project is overwritten), reads the machine code via
`--raw` and compares it with the expected image: the clear dump for a scrambled ROM, the image
for a plain ROM or RAM target. Every differing address is reported with both values and, if a
line map is given, the source line that produced it.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SJASMPLUS = "sjasmplus"
MAX_REPORTED = 200
TIMEOUT_S = 60


class AssemblyError(RuntimeError):
    """sjasmplus is missing or reported errors."""


@dataclass
class VerifyResult:
    """Outcome of a round-trip check.

    Attributes:
        identical: Assembled bytes equal the expected image.
        assembled_length: Number of bytes sjasmplus produced.
        expected_length: Number of bytes expected.
        differences: Differing addresses (at most MAX_REPORTED), each with expected and
            assembled value and the source line.
        difference_count: Total number of differing bytes (incl. a length mismatch).
    """

    identical: bool
    assembled_length: int
    expected_length: int
    differences: list[dict[str, Any]] = field(default_factory=list)
    difference_count: int = 0


def assemble(source: Path) -> bytes:
    """Assemble a source with sjasmplus and return the machine code.

    Raises:
        AssemblyError: If sjasmplus is not on the PATH or the source has errors.
    """
    if shutil.which(SJASMPLUS) is None:
        raise AssemblyError("sjasmplus is not on the PATH (needed for the round-trip check)")
    with tempfile.TemporaryDirectory(prefix="cpcxray_verify_") as directory:
        output = Path(directory) / "assembled.bin"
        completed = subprocess.run(  # fixed program, our own arguments
            [SJASMPLUS, "--nologo", "--msg=err", f"--raw={output}", str(source.resolve())],
            cwd=directory, capture_output=True, text=True, timeout=TIMEOUT_S, check=False,
        )  # fmt: skip
        if completed.returncode != 0 or not output.exists():
            messages = (completed.stdout + completed.stderr).strip().splitlines()
            raise AssemblyError("sjasmplus failed: " + " | ".join(messages[:20]))
        return output.read_bytes()


def compare(
    assembled: bytes,
    expected: bytes,
    start: int,
    source_lines: list[str] | None = None,
    line_of_offset: list[int] | None = None,
) -> VerifyResult:
    """Compare assembled bytes with the expected image.

    Args:
        assembled: Output of sjasmplus.
        expected: The image the source must reproduce.
        start: CPU address of the first byte (for the report).
        source_lines: The source, to quote the line of a difference.
        line_of_offset: Source line (1-based) per byte, from the generator.
    """
    differing = [
        offset
        for offset in range(max(len(assembled), len(expected)))
        if offset >= len(assembled)
        or offset >= len(expected)
        or assembled[offset] != expected[offset]
    ]
    differences = []
    for offset in differing[:MAX_REPORTED]:
        entry: dict[str, Any] = {
            "address": f"{start + offset:04X}",
            "expected": f"{expected[offset]:02X}" if offset < len(expected) else None,
            "assembled": f"{assembled[offset]:02X}" if offset < len(assembled) else None,
        }
        if line_of_offset is not None and offset < len(line_of_offset):
            number = line_of_offset[offset]
            entry["line"] = number
            if source_lines is not None and 0 < number <= len(source_lines):
                entry["source"] = source_lines[number - 1].strip()
        differences.append(entry)
    return VerifyResult(
        identical=not differing,
        assembled_length=len(assembled),
        expected_length=len(expected),
        differences=differences,
        difference_count=len(differing),
    )


def verify_source(
    source: Path, expected: bytes, start: int, line_of_offset: list[int] | None = None
) -> VerifyResult:
    """Assemble `source` and compare it with `expected` (see `compare`).

    Raises:
        AssemblyError: If sjasmplus is missing or fails.
    """
    assembled = assemble(source)
    lines = source.read_text(encoding="utf-8").splitlines()
    return compare(assembled, expected, start, lines, line_of_offset)
