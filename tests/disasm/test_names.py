"""Names by the context of the code (task 10, D-43, debugger/names.py)."""

from __future__ import annotations

from dataclasses import dataclass

from cpcxray.debugger.names import best_name, context_group, ranked_names
from cpcxray.debugger.symbols import OwnVariable, SymbolTable
from cpcxray.debugger.variables import Variable


@dataclass(frozen=True)
class Tag:
    """A provenance tag: RAM code copied from a ROM (analysis/provenance.py `RamTag`)."""

    target: str
    offset: int


def table_and_variables() -> tuple[SymbolTable, list[Variable]]:
    """Groups vdos, cpm; variables of each, one ungrouped, one firmware one at &A010."""
    table = SymbolTable()
    table.add_group("vdos")
    table.add_group("cpm")
    variables = [
        Variable("FW_AREA", 0xA000, 0x40, "a firmware area", "firmware", group="firmware"),
        Variable("vdos_flag", 0xA010, 1, "", "owner", group="vdos"),
        Variable("cpm_flag", 0xA010, 1, "", "owner", group="cpm"),
        Variable("plain_word", 0xA010, 2, "", "owner"),
    ]
    return table, variables


def test_ranking_by_context() -> None:
    """(a) the context's group, (b) built-in and ungrouped (labels first, smallest), (c) others."""
    table, variables = table_and_variables()
    names = [n.text for n in ranked_names(table, variables, 0xA010)]
    assert names == ["plain_word", "FW_AREA+16", "vdos_flag", "cpm_flag"]  # no context
    names = [n.text for n in ranked_names(table, variables, 0xA010, prefer="cpm")]
    assert names == ["cpm_flag", "plain_word", "FW_AREA+16", "vdos_flag"]
    table.add(0xA010, "cpm_label", group="cpm")  # a label before a variable of its rank
    assert best_name(table, variables, 0xA010, prefer="cpm").text == "cpm_label"  # type: ignore[union-attr]
    table.add(0xA010, "plain_label", group="")  # ungrouped: before every variable of rank (b)
    assert best_name(table, variables, 0xA010).text == "plain_label"  # type: ignore[union-attr]


def test_no_groups_is_the_old_order() -> None:
    """Without groups: labels first, then the smallest variable (the order before task 10)."""
    table = SymbolTable()
    variables = [Variable("BIG", 0xA000, 0x40, "", "firmware", group="firmware"),
                 Variable("small", 0xA010, 1, "", "owner")]  # fmt: skip
    assert [n.text for n in ranked_names(table, variables, 0xA010)] == ["small", "BIG+16"]
    assert best_name(table, variables, 0xBB5A).text == "TXT_OUTPUT"  # type: ignore[union-attr]
    table.add(0xBB5A, comment="hooked")
    assert best_name(table, variables, 0xBB5A).type == "FW"  # type: ignore[union-attr]


def test_firmware_names_win() -> None:
    """A firmware name wins at its address, whatever the context (D-43)."""
    table, variables = table_and_variables()
    variables.append(Variable("cpm_jump", 0xBB5A, 3, "", "owner", group="cpm"))
    assert best_name(table, variables, 0xBB5A, prefer="cpm").text == "TXT_OUTPUT"  # type: ignore[union-attr]


def test_context_of_code() -> None:
    """The narrowest range, else the whole scope; RAM copies use their ROM origin's context."""
    table, _ = table_and_variables()
    table.set_context("vdos", "rom5")
    table.set_context("cpm", "rom5", 0xC3F6, 0xC4FF)
    table.set_context("cpm", "", 0x9000, 0x93FF)
    assert context_group(table, "rom5", 0xC000) == "vdos"
    assert context_group(table, "rom5", 0xC400) == "cpm"
    assert context_group(table, "", 0x9100) == "cpm"
    assert context_group(table, "", 0xA000) is None
    assert context_group(table, "", 0xA000, Tag("rom5", 0x0400)) == "cpm"  # copy of &C400
    assert context_group(table, "", 0xA000, Tag("rom5", 0x0000)) == "vdos"


def test_owner_variable_groups_reach_the_variable_map() -> None:
    """The owner's variables carry their group into `VariableMap` (variables.py)."""
    from cpcxray.debugger.variables import VariableMap  # pylint: disable=import-outside-toplevel

    table = SymbolTable()
    table.add_group("vdos")
    table.add_variable(OwnVariable("vdos_flag", 1, 0xA010, group="vdos"))
    variables = VariableMap("464")
    variables.own_variables = table.own_variables
    hits = variables.lookup(0xA010, lambda _address: 0)
    assert [(hit.variable.label, hit.variable.group) for hit in hits] == [("vdos_flag", "vdos")]
    assert variables.firmware[0].group == "firmware"
