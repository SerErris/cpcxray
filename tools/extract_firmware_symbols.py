"""Generate src/cpcxray/debugger/firmware_symbols.py: firmware names per machine (D-26).

Sources:
* docs/firmware.md (from the Firmware Guide SOFT 968, sections 14-18): low and high Kernel
  jumpblocks, the main jumpblock (entries 0-201) and the indirections, each with its
  availability - all machines, 664/6128 only (main 190-200), or 6128 only (201, KL BANK SWITCH).
* docs/firmware.md section 7: the maths jumpblock and TEXT INPUT, which SOFT 968 does not
  document, with one address per machine (compiled from Taylor/Defoe, the CPCWiki BIOS Function
  Summary and the Whole Memory Guide). They lie behind the main jumpblock, where the 464 has no
  entries 190-201, so each machine has its own addresses (664 = 6128 - 3) and its own table.

Correction: KM SCAN KEYS (&BDF4) is listed for all machines in the main tables, but absent on the
464: after a 464 boot the RAM at &BDF4 is empty while &BDCD-&BDF1 hold JP instructions (checked
in phase 4; the BIOS Function Summary also lists it as 664/6128 only).

Labels (owner's convention): firmware entries are upper-case snake case (KM_WAIT_CHAR), so
they stand out from the labels of analysed ROMs and RAM, which stay lower case. The restarts
that jump through a register pair get labels saying what they do, as an assembler-valid
resemblance of the instruction (same addresses on all machines):
PCBC INSTRUCTION (&000E) → JP_BC, PCDE INSTRUCTION (&0016) → JP_DE,
PCHL INSTRUCTION (&001E) → JP_HL. The Firmware Guide name stays the comment.

Usage (from the project root): python -m tools.extract_firmware_symbols
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_DOC = ROOT / "docs" / "firmware.md"
OUTPUT = ROOT / "src" / "cpcxray" / "debugger" / "firmware_symbols.py"
MODELS = ("464", "664", "6128")
AVAILABILITY = {"all": MODELS, "664/6128": ("664", "6128"), "6128": ("6128",)}

NOT_ON_464 = frozenset({"KM SCAN KEYS"})
LABEL_RENAMES = {
    "PCBC INSTRUCTION": "JP_BC",  # &000E: JP (BC)
    "PCDE INSTRUCTION": "JP_DE",  # &0016: JP (DE)
    "PCHL INSTRUCTION": "JP_HL",  # &001E: JP (HL)
}


class Entry(NamedTuple):
    """A firmware entry before rendering.

    Attributes:
        address: Address of the entry (on the machines in `models`).
        label: Label for listings, e.g. TXT_OUTPUT.
        section: Firmware pack or jumpblock, e.g. "text vdu".
        name: Name in the Firmware Guide, e.g. "TXT OUTPUT".
        note: Remark from the guide ("AMSDOS patch", "V1.0 differs"), or "".
        models: The machines that have the entry at this address.
    """

    address: int
    label: str
    section: str
    name: str
    note: str
    models: tuple[str, ...]


def label_for(name: str) -> str:
    """Turn a firmware name into a label: "KM WAIT CHAR" → KM_WAIT_CHAR (see LABEL_RENAMES)."""
    if name in LABEL_RENAMES:
        return LABEL_RENAMES[name]
    name = re.sub(r"\(RST \d\)", "", name)
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")


def table_rows(text: str, heading: str) -> list[dict[str, str]]:
    """Return the rows of the first table after a markdown heading, as column → cell dicts."""
    section = text.split(heading, 1)[1].split("\n## ", 1)[0]  # up to the next section
    lines = [line for line in section.splitlines() if line.startswith("|")]
    header = [cell.strip() for cell in lines[0].strip("|").split("|")]
    return [
        dict(zip(header, (cell.strip() for cell in line.strip("|").split("|")), strict=False))
        for line in lines[2:]  # after the header and the |---| line
    ]


def models_of(name: str, availability: str) -> tuple[str, ...]:
    """The machines that have an entry, from its availability (and the 464 correction)."""
    models = AVAILABILITY[availability]
    return tuple(model for model in models if not (model == "464" and name in NOT_ON_464))


def entries_from_doc() -> list[Entry]:
    """All entries of docs/firmware.md: kernel, main jumpblock, indirections, section 7."""
    text = FIRMWARE_DOC.read_text(encoding="utf-8")
    entries = entries_per_machine(text)
    for row in table_rows(text, "## 4. Low Kernel jumpblock"):
        name = row["Name"] + (f" ({row['Restart']})" if row["Restart"] else "")
        address = int(row["Address"].lstrip("#"), 16)
        entries.append(Entry(address, label_for(name), "low kernel", name, "",
                             models_of(row["Name"], row["Avail"])))  # fmt: skip
    for row in table_rows(text, "## 3. High Kernel jumpblock"):
        address = int(row["Address"].lstrip("#"), 16)
        entries.append(Entry(address, label_for(row["Name"]), "high kernel",
                             row["Name"], "", models_of(row["Name"], row["Avail"])))  # fmt: skip
    for row in table_rows(text, "## 1. Main firmware jumpblock"):
        address = int(row["Address"].lstrip("#"), 16)
        models = models_of(row["Name"], row["Avail"])
        entries.append(Entry(address, label_for(row["Name"]), row["Pack"].lower(),
                             row["Name"], row["Notes"], models))  # fmt: skip
    for row in table_rows(text, "## 2. Firmware indirections"):
        address = int(row["Address"].lstrip("#"), 16)
        models = models_of(row["Name"], row["Avail"])
        entries.append(Entry(address, label_for(row["Name"]), "indirections",
                             row["Name"], row["Notes"], models))  # fmt: skip
    return entries


def entries_per_machine(text: str) -> list[Entry]:
    """Maths jumpblock and TEXT INPUT (docs/firmware.md section 7): one entry per machine."""
    entries = []
    for row in table_rows(text, "## 7. Maths jumpblock"):
        for model in MODELS:
            if row[model] != "-":
                address = int(row[model].lstrip("#"), 16)
                entries.append(Entry(address, label_for(row["Name"]), row["Section"],
                                     row["Name"], "", (model,)))  # fmt: skip
    return entries


def tables(entries: list[Entry]) -> dict[str, list[Entry]]:
    """Per machine, its entries sorted by address (one per address)."""
    result = {}
    for model in MODELS:
        by_address = {entry.address: entry for entry in entries if model in entry.models}
        result[model] = [by_address[address] for address in sorted(by_address)]
    return result


def render(per_model: dict[str, list[Entry]]) -> str:
    """Render the data module."""
    lines = [
        '"""CPC firmware jumpblock symbols per machine (D-26), generated - do not edit.',
        "",
        "Generated by tools/extract_firmware_symbols.py from docs/firmware.md: SOFT 968 (Kernel",
        "jumpblocks, main jumpblock, indirections) and section 7 (maths jumpblock and TEXT INPUT,",
        "from Taylor/Defoe and CPCWiki). The 464 (V1.0) lacks main entries 190-201 and has TEXT",
        "INPUT and the maths jumpblock from &BD3A; the 664 (V1.1) has entries 190-200 and them",
        "from &BD5B; the 6128 (V1.2) has entry 201, KL BANK SWITCH, too and them from &BD5E.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from typing import NamedTuple",
        "",
        "",
        "class FirmwareEntry(NamedTuple):",
        '    """One firmware jumpblock entry.',
        "",
        "    Attributes:",
        "        address: Address of the entry on the machine of its table.",
        "        label: Label used in listings, e.g. TXT_OUTPUT.",
        "        section: Firmware pack or jumpblock, e.g. text vdu.",
        "        name: Name in the Firmware Guide, e.g. TXT OUTPUT.",
        '        note: Remark from the Firmware Guide, e.g. "AMSDOS patch", "V1.0 differs".',
        '    """',
        "",
        "    address: int",
        "    label: str",
        "    section: str",
        "    name: str",
        '    note: str = ""',
        "",
    ]
    for model in MODELS:
        lines += ["", f"FIRMWARE_{model}: tuple[FirmwareEntry, ...] = ("]
        for entry in per_model[model]:
            note = f", {entry.note!r}" if entry.note else ""
            lines.append(f"    FirmwareEntry(0x{entry.address:04X}, {entry.label!r}, "
                         f"{entry.section!r}, {entry.name!r}{note}),")  # fmt: skip
        lines.append(")")
    lines += [
        "",
        "FIRMWARE_BY_MODEL: dict[str, tuple[FirmwareEntry, ...]] = {",
        '    "464": FIRMWARE_464,',
        '    "664": FIRMWARE_664,',
        '    "6128": FIRMWARE_6128,',
        "}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    """Extract the entries and write the data module."""
    per_model = tables(entries_from_doc())
    OUTPUT.write_text(render(per_model), encoding="utf-8")
    counts = ", ".join(f"{model}: {len(entries)}" for model, entries in per_model.items())
    print(f"written to {OUTPUT} ({counts} entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
