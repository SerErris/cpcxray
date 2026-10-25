"""Headless tests of the Symbols tab's text (task 5g, ui/panels/symbol_text.py)."""

from __future__ import annotations

from cpcxray.ui.panels.symbol_text import (
    UNGROUPED,
    context_text,
    counts_label,
    detail_rows,
    entry_key,
    group_choices,
    group_filter_choices,
    group_filter_value,
    group_line,
    groupable,
    now_text,
    scope_text,
    scope_value,
    symbol_row,
    variable_base,
)

FW_ENTRY = {"type": "FW", "address": "BB5A", "label": "TXT_OUTPUT", "comment": "TXT OUTPUT",
            "scope": "", "mark": "yours"}  # fmt: skip


def test_rows_and_keys() -> None:
    """A row: type, address, mark (✎ yours, ◇ analysis, blank), label or "-"."""
    assert symbol_row(FW_ENTRY) == "FW   BB5A ✎ TXT_OUTPUT"
    analysis = {"type": "jump", "address": "C1E8", "label": "l_c1e8", "mark": "analysis"}
    assert symbol_row(analysis) == "jump C1E8 ◇ l_c1e8"
    comment = {"type": None, "address": "9000", "label": None, "mark": "yours"}
    assert symbol_row(comment) == "     9000 ✎ -"
    assert entry_key(FW_ENTRY) == ("FW", "BB5A", "", "TXT_OUTPUT")


def test_scopes_bases_and_counts() -> None:
    """Scope names and filter values, the variable form's base, chip labels, "now at"."""
    assert [scope_text(s) for s in ("", "lower", "rom7")] == ["global", "lower ROM", "ROM 7"]
    assert [scope_value(c) for c in ("all", "global", "rom5")] == [None, "", "rom5"]
    assert variable_base("absolute", "&A612") == "A612"
    assert variable_base("rom5", "12") == "rom5+12"
    assert variable_base("rom5", "") == "rom5+0"
    assert counts_label("FW", {"FW": 278}) == "FW 278"
    assert now_text({"first": "A612", "last": "A621"}) == "→ now at A612-A621"
    assert now_text({"first": None, "last": None}) == "→ the ROM has no data area now"


def test_detail_rows() -> None:
    """Firmware: section and name, machines, code; a variable: its place and what it holds."""
    detail = {"address": "BB5A", "scope": "", "machines": ["464", "664", "6128"],
              "firmware": {"label": "TXT_OUTPUT", "name": "TXT OUTPUT", "section": "text vdu",
                           "note": ""},
              "variable": None, "code": {"address": "BB5A", "text": "rst #08"}}  # fmt: skip
    assert detail_rows(detail, FW_ENTRY) == [
        ("address", "BB5A  (global)"), ("from", "firmware: text vdu, TXT OUTPUT"),
        ("machines", "464 · 664 · 6128"), ("code", "rst #08")]  # fmt: skip
    own = {"label": "drives", "address": "A712", "last": "A721", "size": 16, "owner": "owner",
           "description": "one per drive", "base": "rom5+0012", "now": "A712-A721"}  # fmt: skip
    entry = {"type": "var", "address": "A712", "label": "drives", "scope": "", "mark": "yours"}
    var_detail = detail | {"address": "A712", "firmware": None, "variable": own, "machines": []}
    rows = detail_rows(var_detail, entry)
    assert rows == [("address", "A712  (global)"),
                    ("from", "your variable: rom5+0012, 16 bytes → A712-A721"),
                    ("holds", "one per drive")]  # fmt: skip
    known = own | {"owner": "amsdos", "label": "AMSDOS_RETRIES"}
    assert detail_rows(detail | {"variable": known}, entry | {"type": "VAR"})[1] == (
        "from", "AMSDOS variable: A712-A721, 16 bytes")  # fmt: skip


def test_groups_and_contexts() -> None:
    """Group filter and choices, which entries take a group, group lines, the context line."""
    assert group_filter_choices(["vdos"]) == ["all", "ungrouped", "firmware", "amsdos", "vdos"]
    assert [group_filter_value(c) for c in ("all", "ungrouped", "vdos")] == [None, "", "vdos"]
    assert group_choices(["vdos", "cpm"]) == [UNGROUPED, "vdos", "cpm"]
    own_variable = {"type": "var", "address": "BE8F", "label": "drive", "scope": "",
                    "mark": "yours", "group": "vdos"}  # fmt: skip
    assert symbol_row(own_variable) == "var  BE8F ✎ drive [vdos]"
    rom_label = {**own_variable, "type": "sub", "scope": "rom7"}
    assert groupable(own_variable) and not groupable(rom_label) and not groupable(None)
    assert groupable({**rom_label, "scope": ""}) and not groupable(FW_ENTRY)
    assert groupable({**own_variable, "type": None, "label": None})  # a RAM comment (D-44)
    assert group_line({"name": "firmware", "built_in": True, "description": "OS"}) == (
        "firmware (built in): OS"
    )
    usage = {"name": "cpm", "built_in": False, "description": None, "variables": 2,
             "labels": 1, "contexts": 3}  # fmt: skip
    assert group_line(usage) == "cpm  (2 variables · 1 labels · 3 contexts)"
    assert context_text(None) == "code context: none"
    assert context_text({"group": "cpm", "context": "rom7 9000-93FF"}) == (
        "code context: cpm (rom7 9000-93FF)"
    )
