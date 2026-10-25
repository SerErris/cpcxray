"""Session tools of the Symbols tab (task 5g): search, detail, the owner's variables, file status.

The test CPC is the 464 of tests/session/test_variables.py (no real ROMs needed); the owner's
variables in a ROM's data area find their base in KL_ROM_IY_TABLE, written here by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.analysis.source_gen import name_type
from cpcxray.session.errors import SessionError
from cpcxray.session.session import Session
from tests.analysis.helpers import make_test_cpc

OS = {0x0000: "01817F ED49 C300C0", 0x0038: "FB C9"}
# ROM 3: C000 ld a,(&A010) / C003 ld (&B189),a / C006 jr C006
UPPER = {0x0000: "3A10A0 3289B1 18FE"}
IY_TABLE_464 = 0xB1AA  # KL_ROM_IY_TABLE on the 464: ROM n's data area at +2n


def make_session(tmp_path: Path) -> Session:
    """A session on the test 464 with its project in `tmp_path`."""
    return Session(make_test_cpc(OS, UPPER), project_dir=tmp_path)


def test_own_variables_behave_like_known_ones(tmp_path: Path) -> None:
    """An owner's variable names operands, is searched, explained and found by name."""
    session = make_session(tmp_path)
    session.run(max_instructions=2)
    added = session.variable_add("frame_count", 2, "A00F", "frames since start")
    assert added == {"label": "frame_count", "size": 2, "base": "A00F",
                     "comment": "frames since start", "now": "A00F-A010",
                     "group": None}  # fmt: skip
    assert "ld a,(frame_count+1)" in session.disassemble("C000", 1)["lines"][0]
    row = session.disassemble("C000", 1)["rows"][0]
    assert row["name_type"] == "var"
    assert session.disassemble("C003", 1)["rows"][0]["name_type"] == "VAR"
    explained = session.address_explain("A010")["variables"][0]
    assert explained["name"] == "frame_count+1" and explained["owner"] == "owner"
    assert session.address_explain("frame_count")["address"] == "A00F"
    assert session.variable_list("frames since")["variables"][0]["label"] == "frame_count"
    with pytest.raises(SessionError, match="already used"):
        session.variable_add("frame_count", 1, "A100")
    assert session.variable_remove("frame_count") == {"removed": "frame_count"}
    with pytest.raises(SessionError, match="no variable"):
        session.variable_remove("frame_count")


def test_variable_in_a_rom_data_area_follows_it(tmp_path: Path) -> None:
    """romN+offset is placed from KL_ROM_IY_TABLE; without a data area it has no place."""
    session = make_session(tmp_path)
    assert session.variable_add("drives", 16, "rom3+0012")["now"] is None
    assert session.variable_locate("rom3+0012", 16) == {"first": None, "last": None}
    session.memory_write(IY_TABLE_464 + 2 * 3, "00 A7")  # ROM 3's data area at &A700
    assert session.variable_locate("rom3+0012", 16) == {"first": "A712", "last": "A721"}
    assert session.address_explain("A715")["variables"][0]["name"] == "drives+3"
    session.memory_write(IY_TABLE_464 + 2 * 3, "00 A6")  # another ROM took RAM first
    assert session.address_explain("A615")["variables"][0]["name"] == "drives+3"
    assert session.variable_locate("rom8+0000")["first"] is None  # the 464 has ROMs 0-7
    with pytest.raises(SessionError, match="invalid variable address"):
        session.variable_locate("rom3-1")


def test_symbol_search_types_counts_and_filters(tmp_path: Path) -> None:
    """One list of all names with counts per type; type, scope, text and "yours" filters."""
    session = make_session(tmp_path)
    session.symbol_add("8000", "start", "entry point")
    session.symbol_add("C006", "spin", scope="rom3", type="jump")
    session.symbol_add("BB5A", comment="hooked")
    session.variable_add("frame_count", 2, "A00F")
    everything = session.symbol_search()
    counts = everything["counts"]
    assert counts["sub"] == 1 and counts["jump"] == 1 and counts["var"] == 1
    assert counts["FW"] > 200 and counts["VAR"] > 100
    assert everything["total"] == sum(counts.values())
    yours = session.symbol_search(only_yours=True)["entries"]
    assert [(e["type"], e["label"]) for e in yours] == [
        ("sub", "start"),
        ("var", "frame_count"),
        ("FW", "TXT_OUTPUT"),
        ("jump", "spin"),
    ]
    assert all(e["mark"] == "yours" for e in yours)
    assert [e["label"] for e in session.symbol_search(scope="rom3")["entries"]] == ["spin"]
    found = session.symbol_search("cursor", types=["VAR"])
    assert found["entries"] and all(e["type"] == "VAR" for e in found["entries"])
    assert found["counts"]["FW"] > 0  # counts before the type filter
    with pytest.raises(SessionError, match="unknown types"):
        session.symbol_search(types=["own"])


def test_analysis_names_until_the_owner_renames(tmp_path: Path) -> None:
    """The source generator's names are listed as "analysis"; the owner's label replaces one."""
    session = make_session(tmp_path)
    session.analysis_names["rom3"] = [(0xC006, "rom3", "l_c006", "jump")]
    entry = session.symbol_search("l_c006")["entries"][0]
    assert (entry["type"], entry["mark"], entry["scope"]) == ("jump", "analysis", "rom3")
    assert session.symbol_detail("C006", "rom3", "l_c006")["analysis"] == {
        "label": "l_c006", "type": "jump"}  # fmt: skip
    session.symbol_add("C006", "spin", scope="rom3", type="jump")
    assert session.symbol_search("l_c006")["total"] == 0
    assert [name_type(n) for n in ("sub_c000", "rsx_dir", "l_c1e8", "d_c015", "rom_type")] == [
        "sub", "sub", "jump", "data", "data"]  # fmt: skip


def test_symbol_detail(tmp_path: Path) -> None:
    """Firmware section and machines, the owner's layer, a variable with its machines."""
    session = make_session(tmp_path)
    session.symbol_add("BB5A", comment="hooked")
    detail = session.symbol_detail("BB5A", "", "TXT_OUTPUT")
    assert detail["firmware"]["section"] == "text vdu"
    assert detail["machines"] == ["464", "664", "6128"]
    assert detail["yours"] == {"label": None, "comment": "hooked", "type": None, "before": None,
                              "after": None, "group": None}  # fmt: skip
    assert detail["code"]["address"] == "BB5A"
    time = session.symbol_detail("KL_TIME", "", "KL_TIME")
    assert time["variable"]["size"] == 4 and time["variable"]["owner"] == "firmware"
    assert time["machines"] == ["464", "664", "6128"]
    session.variable_add("frame_count", 2, "A00F", "frames")
    own = session.symbol_detail("A00F", "", "frame_count")["variable"]
    assert (own["base"], own["now"], own["description"]) == ("A00F", "A00F-A010", "frames")


def test_symbols_status_and_typed_add(tmp_path: Path) -> None:
    """The file box: entries of yours, unsaved changes; types in add, list and the file."""
    session = make_session(tmp_path)
    status = session.symbols_status()
    assert (status["exists"], status["symbols"], status["modified"]) == (False, 0, False)
    assert session.symbol_add("9000", "texts", type="data")["type"] == "data"
    with pytest.raises(SessionError, match="invalid type"):
        session.symbol_add("9100", "x", type="var")
    session.variable_add("frame_count", 2, "A00F")
    status = session.symbols_status()
    assert (status["symbols"], status["variables"], status["modified"]) == (1, 1, True)
    assert session.symbols_save()["saved"] == 2
    assert session.symbols_status()["modified"] is False
    text = (tmp_path / "project" / "annotations.txt").read_text()
    assert "data 9000" in text and "var  A00F" in text
    fresh = make_session(tmp_path)  # loads project/annotations.txt
    assert fresh.symbol_list("texts")["symbols"][0]["type"] == "data"
    assert fresh.variable_list("frame_count")["total"] == 1


def test_comment_blocks(tmp_path: Path) -> None:
    """symbol_add / comment_add with blocks; the disassembly and the detail show them (task 9)."""
    session = make_session(tmp_path)
    added = session.symbol_add("C000", "load_count", scope="rom3",
                               before="load_count: reads the counter\n  out: A")  # fmt: skip
    assert added["before"] == "load_count: reads the counter\n  out: A"
    session.comment_add("C000", "stored next", "rom3", position="after")
    session.comment_add("C000", "the counter", "rom3")  # at the end of the line
    with pytest.raises(SessionError, match="position"):
        session.comment_add("C000", "x", "rom3", position="above")
    session.run(max_instructions=2)  # ROM 3 paged in at &C000
    lines = session.disassemble("C000", 2)["lines"]
    assert lines[:3] == ["; load_count: reads the counter", ";   out: A", "load_count:"]
    assert lines[3].startswith("C000") and lines[3].endswith("; the counter")
    assert lines[4] == "; stored next"
    kinds = [row["kind"] for row in session.disassemble("C000", 1)["rows"]]
    assert kinds == ["note", "note", "label", "instruction", "note"]
    yours = session.symbol_detail("C000", "rom3")["yours"]
    assert (yours["before"], yours["after"]) == ("load_count: reads the counter\n  out: A",
                                                 "stored next")  # fmt: skip
    assert session.symbol_search("reads the counter")["total"] == 1
    session.comment_add("C000", "", "rom3", position="before")  # "" removes the block
    assert session.symbol_detail("C000", "rom3")["yours"]["before"] is None


def test_operand_set(tmp_path: Path) -> None:
    """operand_set: the disassembly follows it; the scope defaults to what is paged in."""
    session = make_session(tmp_path)
    session.memory_write("9000", "CD 10 00")  # call &0010 in RAM
    assert session.disassemble("9000", 1)["lines"][0].endswith("call SIDE_CALL")
    result = session.operand_set("9000", "value", comment="a parameter")
    assert result == {"address": "9000", "scope": "", "role": "value", "comment": "a parameter"}
    assert session.disassemble("9000", 1)["lines"][0].endswith("call #0010")
    status = session.symbols_status()
    assert status["operands"] == 1 and status["operand_overrides"][0]["role"] == "value"
    assert session.operand_set("9000", "auto")["role"] == "auto"
    assert session.disassemble("9000", 1)["lines"][0].endswith("call SIDE_CALL")
    with pytest.raises(SessionError, match="invalid role"):
        session.operand_set("9000", "hex")


def test_names_follow_the_code_context(tmp_path: Path) -> None:
    """RAM code in two contexts names the same variable differently (task 10, D-43).

    The disassembly, the trace's accesses and `address_explain` with a code address follow the
    context; without one the old order applies.
    """
    session = make_session(tmp_path)
    session.memory_write("9000", "3A10A0 3210A0 18FE")  # ld a,(&A010) / ld (&A010),a / jr $
    session.group_add("vdos", "VDOS RAM")
    session.group_add("cpm", "CP/M RAM")
    session.variable_add("vdos_flag", 1, "A010", group="vdos")
    session.variable_add("cpm_flag", 1, "A010", group="cpm")
    lines = session.disassemble("9000", 2)["lines"]
    assert lines[0].endswith("ld a,(vdos_flag)")  # no context: the groups in their order
    session.context_set("vdos", scope="", first="9000", last="93FF")
    session.context_set("cpm", scope="", first="9003", last="9005")  # narrower: wins
    lines = session.disassemble("9000", 2)["lines"]
    assert lines[0].endswith("ld a,(vdos_flag)") and lines[1].endswith("ld (cpm_flag),a")
    session.registers_set({"pc": 0x9000, "sp": 0x8000})
    session.step(2)
    load, store = session.trace_records(2)["records"]
    assert load["accesses"][0]["variable"] == "vdos_flag"
    assert store["accesses"][0]["variable"] == "cpm_flag"
    explained = session.address_explain("A010", code_address="9003")
    assert explained["context"] == "cpm" and explained["names"][0]["name"] == "cpm_flag"
    assert [n["group"] for n in explained["names"]] == ["cpm", "vdos"]


def test_group_and_context_tools(tmp_path: Path) -> None:
    """group_* and context_* over the session; the status lists them; the file keeps them."""
    session = make_session(tmp_path)
    session.group_add("vdos", "VDOS RAM")
    session.group_add("cpm", "CP/M RAM", position=0)
    names = [g["name"] for g in session.group_list()["groups"]]
    assert names == ["firmware", "amsdos", "cpm", "vdos"]  # built-in first, then yours
    session.variable_add("drives_swapped", 1, "BE7F", group="vdos")
    session.symbol_add("BE8F", "dos_seldsk", type="jump", group="vdos")
    session.symbol_add("BE8F", "cpm_seldsk", type="jump", group="cpm")
    with pytest.raises(SessionError, match="several symbols at BE8F"):
        session.symbol_remove("BE8F")
    assert session.symbol_detail("BE8F", group="cpm")["yours"]["label"] == "cpm_seldsk"
    session.comment_add("BF03", "HL = its address", group="vdos")  # no label needed (D-44)
    session.comment_add("BF03", "HELLO loader", position="before", group="vdos")
    yours = session.symbol_detail("BF03", group="vdos")["yours"]
    assert (yours["label"], yours["comment"], yours["before"], yours["group"]) == (
        None, "HL = its address", "HELLO loader", "vdos")  # fmt: skip
    assert session.context_set("vdos", scope="rom3")["context"] == "rom3"
    session.run(max_instructions=2)  # ROM 3 paged in at &C000
    assert session.context_set("cpm", first="C003", last="C005")["context"] == "rom3:C003-C005"
    contexts = [c["context"] for c in session.context_list()["contexts"]]
    assert contexts == ["rom3", "rom3:C003-C005"]
    vdos = next(g for g in session.group_list()["groups"] if g["name"] == "vdos")
    assert (vdos["variables"], vdos["labels"], vdos["contexts"]) == (1, 2, 1)  # + BF03
    with pytest.raises(SessionError, match="in use"):
        session.group_remove("cpm")
    session.group_rename("cpm", "cpm22")
    status = session.symbols_status()
    assert status["group_names"] == ["cpm22", "vdos"] and status["contexts"] == 2
    assert "rom3:C003-C005 cpm22" in status["context_list"]
    assert session.symbol_list("seldsk", group="vdos")["symbols"][0]["label"] == "dos_seldsk"
    assert session.variable_list(group="vdos")["variables"][0]["label"] == "drives_swapped"
    session.symbols_save()
    session.context_remove("rom3", "C003", "C005")
    session.group_remove("cpm22", force=True)  # its label becomes ungrouped
    assert session.symbol_detail("BE8F", group="")["yours"]["label"] == "cpm_seldsk"
    fresh = make_session(tmp_path)  # loads project/annotations.txt
    assert fresh.symbols_status()["group_names"] == ["cpm22", "vdos"]
    assert fresh.context_list("rom3")["contexts"][1]["group"] == "cpm22"


def test_symbol_moves_to_another_group(tmp_path: Path) -> None:
    """symbol_add(replaces=) moves a RAM label between groups with all its parts (D-44)."""
    session = make_session(tmp_path)
    session.group_add("vdos")
    session.group_add("cpm")
    session.symbol_add("BE8F", "dos_seldsk", "SELDSK", type="jump", before="header",
                       after="tail", group="vdos")  # fmt: skip
    with pytest.raises(SessionError, match="already used at BE8F in group vdos"):
        session.symbol_add("BE8F", "dos_seldsk", group="cpm")  # without replaces: a second one
    moved = session.symbol_add("BE8F", group="cpm", replaces="vdos")
    parts = ("label", "comment", "type", "before", "after", "group")
    assert [moved[part] for part in parts] == ["dos_seldsk", "SELDSK", "jump", "header", "tail",
                                               "cpm"]  # fmt: skip
    assert [s["group"] for s in session.symbol_list(source="user")["symbols"]] == ["cpm"]
    renamed = session.symbol_add("BE8F", "seldsk", group="", replaces="cpm")  # out, renamed
    assert (renamed["label"], renamed["group"], renamed["comment"]) == ("seldsk", None, "SELDSK")
    session.symbols_save()
    fresh = make_session(tmp_path)
    assert [(s["label"], s["group"]) for s in fresh.symbol_list(source="user")["symbols"]] == [
        ("seldsk", None)
    ]
    session.symbol_add("BE8F", "cpm_seldsk", group="cpm")
    for arguments, message in (
        ({"group": "cpm", "replaces": "vdos"}, "no symbol of yours at BE8F in group vdos"),
        ({"group": "cpm", "replaces": ""}, "already has a symbol in group cpm: cpm_seldsk"),
        ({"replaces": ""}, "give the group"),
        ({"group": "cpm", "replaces": "basic"}, "basic"),
    ):
        with pytest.raises(SessionError, match=message):
            session.symbol_add("BE8F", **arguments)
    with pytest.raises(SessionError, match="no group to move from"):
        session.symbol_add("C000", "x", scope="rom7", replaces="vdos")
