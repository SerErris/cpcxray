"""Phase 1 milestone: SingleStepTests/z80 per-opcode tests (state and bus-cycle sequence).

Runs the first 25 tests of each of the 1604 opcode files by default; set CPCXRAY_SST=all for
all 1.6 M tests (or use tools/run_singlestep.py, which runs them in parallel).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from singlestep import Runner, data_files, limit_per_file, load

FILES = data_files()

pytestmark = pytest.mark.skipif(
    not FILES, reason="SingleStepTests data missing; run tools/fetch_test_data.sh"
)

_runner = Runner() if FILES else None


@pytest.mark.parametrize("path", FILES, ids=[f.stem for f in FILES])
def test_opcode_file(path: Path) -> None:
    """All (or the first CPCXRAY_SST) tests of one opcode file pass: state, RAM, bus accesses."""
    assert _runner is not None
    failures = []
    for test in load(path)[: limit_per_file()]:
        errors = _runner.run(test)
        if errors:
            failures.append(f"{test['name']}: " + "; ".join(errors))
    assert not failures, f"{len(failures)} failed:\n" + "\n".join(failures[:5])
