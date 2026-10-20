"""Tests of the analysis database: source priority, overrides, save and merge (D-31)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cpcxray.analysis.database import Kind, Source, TargetDatabase


def make_database() -> TargetDatabase:
    """A 16-byte database of target "rom5" at &C000."""
    return TargetDatabase("rom5", 0xC000, 16)


def test_stronger_or_equal_source_wins() -> None:
    """Owner > observed > static: a weaker source never replaces a stronger one."""
    database = make_database()
    assert database.classify(0, Kind.BYTE_DATA, Source.OBSERVED)
    assert not database.classify(0, Kind.INSN_START, Source.STATIC)
    assert database.kind_at(0) == Kind.BYTE_DATA
    assert database.classify(0, Kind.STRING, Source.OWNER)
    assert not database.classify(0, Kind.INSN_START, Source.OBSERVED)
    assert database.kind_at(0) == Kind.STRING
    assert not database.classify(99, Kind.STRING, Source.OWNER)  # outside: ignored


def test_classify_instruction_marks_rst_operands() -> None:
    """Instruction start, body bytes and inline RST operand bytes."""
    database = make_database()
    database.classify_instruction(2, 3, Source.OBSERVED, operand_offsets=(1, 2))
    assert [database.kind_at(o) for o in (2, 3, 4)] == [
        Kind.INSN_START, Kind.RST_OPERAND, Kind.RST_OPERAND,
    ]  # fmt: skip


def test_reset_classification() -> None:
    """Before a rebuild every byte is unclassified again (overrides are kept separately)."""
    database = make_database()
    database.classify(0, Kind.STRING, Source.OWNER)
    database.classify(2, Kind.INSN_START, Source.STATIC)
    database.add_override(0, 0, Kind.STRING)
    database.reset_classification()
    assert database.kind_at(0) == database.kind_at(2) == Kind.UNKNOWN
    assert database.counts()["sources"] == {"none": 16, "static": 0, "observed": 0, "owner": 0}
    assert database.override_ranges() == [(0, 0, Kind.STRING)]


def test_overrides_replace_and_remove() -> None:
    """An override for the same range replaces the old one; remove drops it."""
    database = make_database()
    database.add_override(2, 5, Kind.STRING)
    database.add_override(2, 5, Kind.WORD_TABLE)
    database.add_override(8, 9, Kind.INSN_START)
    assert database.override_ranges() == [(2, 5, Kind.WORD_TABLE), (8, 9, Kind.INSN_START)]
    assert database.overrides[0] == {"first": "C002", "last": "C005", "kind": "word_table"}
    database.remove_override(2, 5)
    assert database.override_ranges() == [(8, 9, Kind.INSN_START)]


def test_save_and_merge(tmp_path: Path) -> None:
    """Saving and merging: stronger source per byte wins, facts accumulate, extras returned."""
    first = make_database()
    first.classify(0, Kind.INSN_START, Source.OBSERVED)
    first.classify(1, Kind.STRING, Source.OWNER)
    first.observe(0xC000, "rst", 2)
    first.add_override(1, 1, Kind.STRING)
    path = tmp_path / "project" / "vdos.analysis.json"
    first.save(path, {"flags": "00ff"})
    assert json.loads(path.read_text(encoding="utf-8"))["kinds"].startswith("0105")

    second = make_database()
    second.classify(0, Kind.BYTE_DATA, Source.STATIC)  # weaker: replaced
    second.classify(1, Kind.BYTE_DATA, Source.OWNER)  # equal: the file wins
    second.classify(2, Kind.INSN_START, Source.OBSERVED)  # not in the file: kept
    second.observe(0xC000, "reached", "rom6")
    extra = second.merge_file(path)
    assert extra == {"flags": "00ff"}
    assert [second.kind_at(o) for o in (0, 1, 2)] == [Kind.INSN_START, Kind.STRING, Kind.INSN_START]
    assert second.observations["C000"] == {"rst": 2, "reached": "rom6"}
    assert second.observations_at(0) == {"rst": 2, "reached": "rom6"}
    assert second.overrides == first.overrides


def test_merge_rejects_other_target(tmp_path: Path) -> None:
    """A database of another target or size is refused."""
    other = TargetDatabase("rom7", 0xC000, 16)
    path = tmp_path / "other.json"
    other.save(path)
    with pytest.raises(ValueError, match="rom7"):
        make_database().merge_file(path)
