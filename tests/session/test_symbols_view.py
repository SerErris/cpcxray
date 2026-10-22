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
                     "comment": "frames since start", "now": "A00F-A010"}  # fmt: skip
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
    assert detail["yours"] == {"label": None, "comment": "hooked", "type": None}
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
