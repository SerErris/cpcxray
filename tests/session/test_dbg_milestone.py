"""Phase 9 milestone: all FR-DBG requirements are met (design.md §2.7, §9).

Each requirement is checked by the tests named here; this module adds the checklist (every
requirement has its Session tools, hence its MCP tools, D-27) and an end-to-end case on the real
VDOS: "who reads this byte of the scrambled ROM?".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.session.session import Session
from tests.vdos import boot_vdos, requires_roms

# requirement → Session methods (GUI panels call the same methods, ui/panels/tabs.py)
REQUIREMENTS = {
    "FR-DBG-01 screen MODE 0/1/2": ["screen_image", "screen_text"],  # tests/ui/test_renderer.py
    "FR-DBG-02 registers": ["registers_get", "registers_set"],
    "FR-DBG-03 memory views + editing": ["memory_view", "memory_read", "memory_write"],
    "FR-DBG-04 disassembly (raw/clear)": ["disassemble"],
    "FR-DBG-05 devices": ["devices_get"],
    "FR-DBG-06 run control": [
        "run",
        "pause",
        "step",
        "step_over",
        "step_out",
        "run_until",
        "reset",
    ],
    "FR-DBG-07 breakpoints": [
        "breakpoint_add",
        "breakpoint_remove",
        "breakpoint_enable",
        "breakpoint_list",
    ],
    "FR-DBG-08 bus trace": ["bus_trace_start", "bus_trace_stop", "bus_trace_read"],
    "FR-DBG-09 symbols": [
        "symbol_add",
        "symbol_remove",
        "symbol_list",
        "comment_add",
        "symbols_load",
        "symbols_save",
    ],
    "FR-DBG-10 .sna snapshots": ["sna_load", "sna_save"],
}


@pytest.mark.parametrize("requirement", REQUIREMENTS)
def test_requirement_has_tools(requirement: str) -> None:
    """Every FR-DBG requirement is reachable through Session (and so through MCP)."""
    for name in REQUIREMENTS[requirement]:
        assert callable(getattr(Session, name, None)), f"{requirement}: {name}"


@requires_roms
def test_who_reads_the_vdos_name_table(tmp_path: Path) -> None:
    """A ROM read breakpoint finds the firmware routine that walks VDOS's RSX names."""
    session = boot_vdos(tmp_path)
    session.breakpoint_add("EA9C", kind="read", location="rom:5")  # hidden name &89
    session.type_text("|NOSUCH\r", wait=False)
    result = session.run(max_us=3_000_000)
    assert result["reason"] == "breakpoint"
    assert result["breakpoint"] == {
        "id": 1, "kind": "read", "access": "read", "address": "EA9C", "value": "89",
        "pc": "0321", "location": "rom5",  # KL FIND COMMAND's compare loop in the OS ROM
    }  # fmt: skip
