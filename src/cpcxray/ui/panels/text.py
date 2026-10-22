"""Text formatting and input parsing for the debugger panels (no Dear PyGui; FR-DBG-02..09).

The panels (ui/panels/tabs.py) only arrange widgets; what they show comes from `Session` methods and
is formatted here, so it is tested without a window and is exactly what MCP clients get.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

FLAG_LETTERS = "SZ5H3PNC"


REGISTER_PAIRS = (("AF", "af", "af_"), ("BC", "bc", "bc_"), ("DE", "de", "de_"),
                  ("HL", "hl", "hl_"))  # fmt: skip
REGISTER_WORDS = (("IX", "ix"), ("IY", "iy"), ("SP", "sp"), ("PC", "pc"), ("WZ", "wz"))


def _byte_pair(value: int) -> str:
    """A register pair as its two bytes: "0D 9B"."""
    return f"{value >> 8:02X} {value & 0xFF:02X}"


def register_pairs(
    registers: dict[str, int], changed: list[str]
) -> list[tuple[str, str, bool, str, bool]]:
    """The register table: (name, main, main changed, alternate, alternate changed)."""
    return [(name, _byte_pair(registers[main]), main in changed,
             _byte_pair(registers[alternate]), alternate in changed)
            for name, main, alternate in REGISTER_PAIRS]  # fmt: skip


def register_words(registers: dict[str, int], changed: list[str]) -> list[tuple[str, str, bool]]:
    """IX, IY, SP, PC, WZ: (name, value, changed by the last step)."""
    return [(name, f"{registers[key]:04X}", key in changed) for name, key in REGISTER_WORDS]


def flag_bits(f: int) -> list[tuple[str, bool]]:
    """The flags as (letter, set) from bit 7 (S) to bit 0 (C)."""
    return [(letter, bool(f & (0x80 >> index))) for index, letter in enumerate(FLAG_LETTERS)]


def cpu_state_lines(registers: dict[str, int]) -> list[str]:
    """I, R, IM and the interrupt state: "I 00 · R 04 · IM 1", "IFF1 1 · IFF2 1 · not halted"."""
    r = registers
    halted = "halted" if r["halted"] else "not halted"
    return [f"I {r['i']:02X} \u00b7 R {r['r']:02X} \u00b7 IM {r['im']}",
            f"IFF1 {r['iff1']} \u00b7 IFF2 {r['iff2']} \u00b7 {halted}"]  # fmt: skip


def parse_assignments(text: str) -> dict[str, int]:
    """Parse "hl=4000, a=5" (hex values) into a register dict.

    Raises:
        ValueError: On malformed input.
    """
    values = {}
    for part in text.replace(";", ",").split(","):
        if not part.strip():
            continue
        name, separator, value = part.partition("=")
        if not separator:
            raise ValueError(f"expected name=value, got {part.strip()!r}")
        values[name.strip().lower()] = int(value.strip().lstrip("&#"), 16)
    if not values:
        raise ValueError("nothing to set")
    return values


def parse_memory_edit(text: str) -> tuple[str, str]:
    """Parse "4000: 3E 05 C9" into (address, hex bytes).

    Raises:
        ValueError: On malformed input.
    """
    address, separator, data = text.partition(":")
    if not separator or not address.strip() or not data.strip():
        raise ValueError("expected 'address: hex bytes', e.g. '4000: 3E 05'")
    return address.strip(), data.strip()


@dataclass(frozen=True)
class CodeRow:  # pylint: disable=too-many-instance-attributes  # one field per column
    """One row of the CPU tab's disassembly.

    Attributes:
        kind: "separator" (a 16 KB quarter starts: `text`), "label", "instruction" or
            "data" (an instruction's extra line, e.g. a RST's inline defw).
        text: The separator's or label's text.
        gutter: "▶" at the PC, "●" at a breakpoint, else "".
        address, data_bytes, mnemonic: The columns of an instruction ("" for others).
        before, name, after: The operands split around the name in them (`name` may be "").
        name_type: FW, VAR or own: the colour of `name` (and of a label row).
        comment: The comment, or "".
        pc: The row of the instruction at the PC (the yellow bar).
    """

    kind: str
    text: str = ""
    gutter: str = ""
    address: str = ""
    data_bytes: str = ""
    mnemonic: str = ""
    before: str = ""
    name: str = ""
    after: str = ""
    name_type: str = ""
    comment: str = ""
    pc: bool = False


def disassembly_view(listing: dict[str, Any], pc: int, breakpoints: set[int]) -> list[CodeRow]:
    """`disassemble`'s rows for the CPU tab: a separator row where a 16 KB quarter starts.

    Args:
        listing: The result of `Session.disassemble` ("rows", "paging").
        pc: The PC (its row gets the yellow bar and ▶).
        breakpoints: Addresses with a PC breakpoint (●).
    """
    texts = {int(q["start"], 16) >> 14: f"{q['start']}-{q['end']} {q['text']}"
             for q in listing["paging"]}  # fmt: skip
    rows: list[CodeRow] = []
    waiting: list[CodeRow] = []  # a label row waits for its instruction: the separator first
    quarter = None
    for row in listing["rows"]:
        if row["kind"] == "label":
            waiting.append(CodeRow("label", text=f"{row['text']}:", name_type=row["name_type"]))
            continue
        if row["kind"] == "instruction":
            address = int(row["address"], 16)
            if address >> 14 != quarter and address >> 14 in texts:
                quarter = address >> 14
                rows.append(CodeRow("separator", text=texts[quarter]))
            rows += waiting
            waiting = []
            at_pc = address == pc
            gutter = "\u25b6" if at_pc else ("\u25cf" if address in breakpoints else "")
        else:
            at_pc, gutter = False, ""
        operands, name = row["operands"], row.get("name", "")
        before, _, after = operands.partition(name) if name else (operands, "", "")
        rows.append(CodeRow(row["kind"], gutter=gutter, address=row["address"],
                            data_bytes=row["bytes"], mnemonic=row["mnemonic"], before=before,
                            name=name, after=after, name_type=row.get("name_type", ""),
                            comment=row.get("comment", ""), pc=at_pc))  # fmt: skip
    return rows + waiting


AREA = -1  # colour of a byte inside a pack's area but in no named variable
BYTES_PER_ROW = 16


@dataclass(frozen=True)
class MemoryRow:
    """One line of the memory panel's dump.

    Attributes:
        address: Address of the first byte, 4-digit hex.
        cells: Per byte its hex text ("--" if unknown) and colour: an index into the panel's
            palette for a known variable, AREA, or None.
        ascii: The bytes as characters ("." if not printable).
    """

    address: str
    cells: tuple[tuple[str, int | None], ...]
    ascii: str


def memory_layout(
    dump: dict[str, Any], colours: int
) -> tuple[list[MemoryRow], list[tuple[str, int | None]]]:
    """Lay out a `memory_view` result with its known variables highlighted (FR-DBG-11).

    Every byte gets the colour of the smallest named variable containing it; the variables
    take the colours 0 .. `colours` - 1 in turn, so neighbours differ. The legend lists these
    variables in the same colours, with range, size, the structure they belong to and their
    description; it starts with the pack areas in view.

    Returns:
        The rows and the legend lines (text, colour).
    """
    start = int(dump["address"], 16)
    variables = dump.get("variables", [])
    areas = [v for v in variables if v["area"]]
    named = [v for v in variables if not v["area"]]
    cells, colour_of = _coloured_bytes(start, dump["hex"], named, areas, colours)
    rows = []
    for row_start in range(0, len(cells), BYTES_PER_ROW):
        row_cells = tuple(cells[row_start : row_start + BYTES_PER_ROW])
        ascii_text = "".join(_printable(text) for text, _ in row_cells)
        rows.append(MemoryRow(f"{(start + row_start) & 0xFFFF:04X}", row_cells, ascii_text))
    return rows, _legend(areas, named, colour_of)


def _legend(
    areas: list[dict[str, Any]], named: list[dict[str, Any]], colour_of: dict[str, int]
) -> list[tuple[str, int | None]]:
    """The legend lines: the areas in view, then each coloured variable in its colour."""
    legend: list[tuple[str, int | None]] = []
    if areas:
        legend.append(("in " + ", ".join(a["label"] for a in areas), AREA))
    for label, colour in colour_of.items():
        variable = next(v for v in named if v["label"] == label)
        legend.append((_legend_text(variable, named), colour))
    return legend


def _contains(variable: dict[str, Any], address: int) -> bool:
    """True if `address` is one of the variable's bytes."""
    return int(variable["address"], 16) <= address <= int(variable["last"], 16)


def _coloured_bytes(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # the parts of one dump
    start: int, hex_text: str, named: list[dict[str, Any]], areas: list[dict[str, Any]],
    colours: int,
) -> tuple[list[tuple[str, int | None]], dict[str, int]]:  # fmt: skip
    """Per byte its text and colour (see `memory_layout`), and the colour of each variable."""
    colour_of: dict[str, int] = {}
    cells = []
    for offset in range(len(hex_text) // 2):
        address, byte = start + offset, hex_text[2 * offset : 2 * offset + 2]
        containing = [v for v in named if _contains(v, address)]
        if containing:
            label = min(containing, key=lambda v: v["size"])["label"]  # the smallest
            colour: int | None = colour_of.setdefault(label, len(colour_of) % colours)
        else:
            colour = AREA if any(_contains(area, address) for area in areas) else None
        cells.append(("--" if byte == "??" else byte, colour))
    return cells, colour_of


def _printable(hex_byte: str) -> str:
    """The character of a hex byte for the ASCII column ("." if unknown or not printable)."""
    if hex_byte == "--":
        return "."
    value = int(hex_byte, 16)
    return chr(value) if 32 <= value < 127 else "."


def _legend_text(variable: dict[str, Any], named: list[dict[str, Any]]) -> str:
    """A legend line, e.g. "B726-B726  TXT_CURSOR_ROW (1) in TXT_CURRENT_WINDOW: cursor ..."."""
    first, last = int(variable["address"], 16), int(variable["last"], 16)
    parents = [v for v in named if v is not variable and v["size"] > variable["size"]
               and int(v["address"], 16) <= first and last <= int(v["last"], 16)]  # fmt: skip
    parent = f" in {min(parents, key=lambda v: v['size'])['label']}" if parents else ""
    return (f"{variable['address']}-{variable['last']}  {variable['label']} "
            f"({variable['size']}){parent}: {variable['description']}")  # fmt: skip


def colour_numbers(firmware: int, hardware: int) -> str:
    """A colour as "firmware/hardware": decimal firmware number, hardware number in hex."""
    return f"{firmware}/{hardware:02X}"


def gate_array_lines(gate_array: dict[str, Any]) -> list[str]:
    """Header and footer texts of the Gate Array box (mode, pen select, interrupt timing)."""
    pen = gate_array["pen"]
    pen_text = "border" if pen == "border" else f"P{pen}"
    return [
        f"mode {gate_array['mode']} \u00b7 colours fw/hw (hex)",
        f"pen select {pen_text} \u00b7 IRQ in {gate_array['interrupt_in_us']} \u00b5s "
        f"\u00b7 R52 {gate_array['line_counter_r52']}",
    ]


def rom_select_lines(rom_select: dict[str, Any]) -> list[str]:
    """The ROM select box: selected number and file, paged in or not, installed slots."""
    number = rom_select["selected"]
    file = rom_select.get("file") or "no ROM"
    answering = rom_select.get("answering", number)
    selected = (
        f"{number} {file}" if answering == number else f"{number} (empty: ROM {answering} {file})"
    )
    paged = "paged in" if rom_select.get("paged_in") else "not paged in (upper ROM off)"
    return [
        f"selected {selected}",
        paged,
        f"installed: {', '.join(rom_select.get('installed', []))}",
    ]


def ppi_lines(system_io: dict[str, Any]) -> list[str]:
    """The PPI box: ports A, B, C with their meaning."""
    meanings = system_io.get("meanings", {})
    return [
        f"{port.upper()} &{system_io[f'port_{port}']:02X} \u00b7 {meanings.get(port, '')}"
        for port in ("a", "b", "c")
    ]


def crtc_footer(crtc: dict[str, Any]) -> str:
    """The CRTC footer: screen start, characters per line by lines, scan lines per character."""
    screen = crtc["screen"]
    return (f"&{screen['start']} \u00b7 {screen['characters']} \u00d7 {screen['lines']} "
            f"\u00b7 {screen['scan_lines']} lines")  # fmt: skip


def drive_flags(drive: dict[str, Any]) -> str:
    """A drive's state in words: "read-only · unchanged", "writable · changed", or ""."""
    if drive.get("disk") is None:
        return ""
    mode = "writable" if drive["writable"] else "read-only"
    return f"{mode} \u00b7 {'changed' if drive['modified'] else 'unchanged'}"


def keyboard_summary(key_lines: list[dict[str, Any]], typing: bool) -> str:
    """The keyboard box's header: the held keys ("Shift + A held") and whether text is typed."""
    held = [line["keys"][bit] or f"{line['line']}.{bit}" for line in key_lines
            for bit in line["held"]]  # fmt: skip
    keys = f"{' + '.join(held)} held" if held else "no key held"
    return f"{keys} \u00b7 {'typing' if typing else 'not typing'}"


STOP_REASONS = {
    "steps_done": "step", "address_reached": "address reached", "returned": "returned",
    "instruction_limit": "instruction limit", "time_limit": "time limit", "halted": "HALT",
    "paused": "paused", "timeout": "wall-clock limit",
}  # fmt: skip


def location_text(location: str) -> str:
    """A breakpoint/access location in words: "lower ROM", "ROM 7", "RAM block 2", "" for any."""
    if location in ("any", "io", ""):
        return ""
    if location == "lower":
        return "lower ROM"
    if location in ("rom", "ram"):
        return {"rom": "a ROM", "ram": "RAM"}[location]
    if location.startswith("ram:"):
        return f"RAM block {location[4:]}"
    return f"ROM {location.removeprefix('rom').lstrip(':')}"


def ordinal(number: int) -> str:
    """1st, 2nd, 3rd, 4th, 11th, 21st ..."""
    teens = 10 <= number % 100 <= 20
    suffix = "th" if teens else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def breakpoint_where(entry: dict[str, Any]) -> tuple[str, str]:
    """A breakpoint's "where" column: (address, range, port or ROM; its name or "")."""
    if "address" in entry:
        where = entry["address"] + (f"-{entry['end']}" if "end" in entry else "")
        return where, entry.get("label") or entry.get("variable") or ""
    if "port" in entry:
        return f"port {entry['port']}", ""
    if entry["kind"] == "rom_select":
        return (f"ROM {entry['rom']}", "") if "rom" in entry else ("any ROM", "")
    return "\u2014", ""


def breakpoint_condition(entry: dict[str, Any]) -> str:
    """A breakpoint's "condition" column: the location qualifier, or the port mask."""
    if "mask" in entry:
        return f"mask {entry['mask']}"
    location = entry.get("location", "any")
    return "" if location == "any" else location


def stop_texts(last: dict[str, Any]) -> dict[str, Any]:
    """The "Last stop" box: title, type chip, headline and instruction lines (`last_stop_get`).

    Returns:
        "title" (e.g. "breakpoint #1 · 4th hit"), "kind" (the breakpoint type, or None),
        "headline" (what happened, where), "instruction" (disassembled lines).
    """
    stop = last.get("stop")
    if stop is None:
        return {"title": "", "kind": None, "headline": "no stop yet", "instruction": []}
    hit = stop.get("breakpoint")
    if hit is None:
        reason = STOP_REASONS.get(stop["reason"], stop["reason"])
        label = f" {stop['label']}" if stop.get("label") else ""
        return {"title": reason, "kind": None, "headline": f"at {stop['pc']}{label}",
                "instruction": last.get("instruction", [])}  # fmt: skip
    hits = last.get("hits")
    count = f" \u00b7 {ordinal(hits)} hit" if hits else " (removed)"
    where = location_text(hit.get("location", ""))
    if hit["kind"] == "pc":
        label = f" {last['label']}" if last.get("label") else ""
        headline = f"before the instruction at {hit['pc']}{label}"
    else:
        name = hit.get("variable") or last.get("label")
        named = f" ({name})" if name else ""
        headline = (f"{hit['access']} {hit['address']} = {hit['value']}{named} by the "
                    f"instruction at {hit['pc']}")  # fmt: skip
    if where:
        headline += f" \u00b7 {where}"
    return {"title": f"breakpoint #{hit['id']}{count}", "kind": hit["kind"],
            "headline": headline, "instruction": last.get("instruction", [])}  # fmt: skip


def variable_range_hint(name: str, variables: list[dict[str, Any]]) -> str:
    """For a read/write/access breakpoint without an end: the variable it will cover, or ""."""
    match = next((v for v in variables if v["label"].lower() == name.strip().lower()), None)
    if match is None:
        return ""
    size = f"{match['size']} byte{'s' if match['size'] != 1 else ''}"
    return (f"end empty: the whole variable {match['label']} ({match['address']}-"
            f"{match['last']}, {size})")  # fmt: skip


def disk_name(disk: str | None) -> str:
    """The file name of a drive's image ("draft", "no disk")."""
    if disk is None:
        return "no disk"
    return disk.replace("\\", "/").rsplit("/", 1)[-1]


def drive_chip(drive: dict[str, Any]) -> str:
    """A drive chip: "● A: 464_cpm_22_orig.dsk" with a disk, "○ B: no disk" without."""
    mark = "●" if drive["disk"] is not None else "○"
    return f"{mark} {drive['drive']}: {disk_name(drive['disk'])}"


def drive_geometry(drive: dict[str, Any]) -> str:
    """The drive mechanism: '3" · 40 tracks · 1 side' (A:), '80 tracks · 2 sides' (B:)."""
    sides = 2 if drive["double_sided"] else 1
    size = '3" · ' if drive["cylinders"] == 40 and sides == 1 else ""
    return f"{size}{drive['cylinders']} tracks · {sides} side{'s' if sides > 1 else ''}"


def catalog_header(catalog: dict[str, Any]) -> str:
    """The file list's header: "26 files · 119K used · 50K free"."""
    count = len(catalog["files"])
    return (f"{count} file{'s' if count != 1 else ''} · {catalog['used_kb']}K used "
            f"· {catalog['free_kb']}K free")  # fmt: skip


def _hex_ascii(data: bytes) -> list[str]:
    """16 bytes in one line: hex in two groups of 8, then ASCII (dots for unprintable bytes).

    Compact, so the line fits the detail panel as in the mock-up.
    """
    text = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in data)
    hex_text = data.hex().upper()
    return [" ".join(part for part in (hex_text[:16], hex_text[16:], text) if part)]


def _is_text(data: bytes) -> bool:
    """True if the bytes look like a text file (printable ASCII, tabs and line ends)."""
    sample = data.split(b"\x1a", 1)[0][:512]
    return bool(sample) and all(0x20 <= b < 0x7F or b in (9, 10, 13) for b in sample)


def file_detail(info: dict[str, Any], user: int) -> dict[str, Any]:
    """The detail panel of a disk file (`disk_read_file`): key/value rows and text blocks.

    Returns:
        "rows": (label, value) pairs - size and user, the AMSDOS header, where the program goes
        (load, entry: from the header, or CP/M's &0100 for .COM, BASIC's &0170 for ASCII .BAS);
        "blocks": (title, lines) - the bytes after the header, the bytes at the entry, or the
        first lines (text) or bytes; "code": the disassembly at the entry (address, text, label
        and its type), shown under the last block.
    """
    data = bytes.fromhex(info.get("hex", ""))
    rows = [("on disk", f"{info['size']} bytes \u00b7 user {user}")]
    header = info.get("amsdos_header")
    program = info.get("program")
    blocks: list[tuple[str, list[str]]] = []
    if header is not None:
        rows += [("header", f"AMSDOS, {header['type']}"), ("name", header["name"])]
    else:
        rows.append(("header", f"none \u00b7 {program['type']}" if program else "none"))
    if program is not None:
        load, entry = program["load_address"], program["entry_address"]
        where = {"CP/M": " (CP/M)", "BASIC": " (BASIC program)"}.get(program["source"], "")
        rows.append(("load", f"&{load:04X}{where}"))
        if entry is not None:
            rows.append(("entry", f"&{entry:04X}"))
    if header is not None:
        rows.append(("length", f"{header['length']} (&{header['length']:04X})"))
        blocks.append((f"after the header (load &{header['load_address']:04X})",
                       _hex_ascii(data[128:144])))  # fmt: skip
    elif _is_text(data):
        lines = info.get("text", "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        blocks.append(("first lines", lines[:8]))
    elif info.get("entry") is None:
        blocks.append(("first bytes", _hex_ascii(data[:16])))
    code = info.get("entry")
    if code is not None and program is not None:
        same = " (entry = load)" if program["entry_address"] == program["load_address"] else ""
        blocks.append((f"at the entry &{program['entry_address']:04X}{same}", [code["bytes"]]))
    return {"rows": rows, "blocks": blocks, "code": code["code"] if code else []}


def split_label(line: dict[str, Any]) -> tuple[str, str, str]:
    """A code line's text around its name: (before, name, after); the name may be ""."""
    label = line.get("label")
    text = line["text"]
    if not label or label not in text:
        return text, "", ""
    before, _, after = text.partition(label)
    return before, label, after


TRACE_PAIRS = ("af", "bc", "de", "hl", "ix", "iy", "sp")
DETAIL_ORDER = (("AF", "IX"), ("BC", "IY"), ("DE", "SP"), ("HL", "PC"))


def register_changes(before: dict[str, str], after: dict[str, str] | None) -> str:
    """What an instruction changed: "A=0D F=44 BC=000D SP=BFE4" (AF split, PC left out)."""
    if after is None:
        return ""
    changes = []
    for name in TRACE_PAIRS:
        if before[name] == after[name]:
            continue
        if name == "af":
            if before["af"][:2] != after["af"][:2]:
                changes.append(f"A={after['af'][:2]}")
            if before["af"][2:] != after["af"][2:]:
                changes.append(f"F={after['af'][2:]}")
        else:
            changes.append(f"{name.upper()}={after[name]}")
    return " ".join(changes)


@dataclass(frozen=True)
class TraceRow:  # pylint: disable=too-many-instance-attributes  # one field per column
    """One row of the Trace tab's list.

    Attributes:
        kind: "separator" (execution moves to `where`) or "instruction".
        index: The record's index in `trace_records` (-1 for a separator).
        address: The instruction's address.
        depth: Call depth (calls and returns taken inside the trace; a reading aid).
        marker: "▸" for a call, "◂" for a return, else "".
        mnemonic, before, name, after: The instruction, split around the name in it.
        name_type: FW, VAR or own: the colour of `name`.
        changes: The registers it changed (`register_changes`).
        where: Where it ran (for a separator: its text).
    """

    kind: str
    index: int = -1
    address: str = ""
    depth: int = 0
    marker: str = ""
    mnemonic: str = ""
    before: str = ""
    name: str = ""
    after: str = ""
    name_type: str = ""
    changes: str = ""
    where: str = ""


def registers_after(records: list[dict[str, Any]], after: dict[str, str] | None,
                    index: int) -> dict[str, str] | None:  # fmt: skip
    """The registers after record `index`: those before the next one (or `after`)."""
    if index + 1 < len(records):
        return records[index + 1]["registers"] | {"pc": records[index + 1]["address"]}
    return after


def trace_rows(records: list[dict[str, Any]], after: dict[str, str] | None) -> list[TraceRow]:
    """The Trace tab's list: rows with call depth and changes, separators where code moves.

    A call or return only changes the depth when it was taken: the next record is not the
    instruction after it. Depths are relative to the shallowest point of the trace, so a trace
    that starts inside a subroutine and returns from it still starts at the left.
    """
    rows: list[TraceRow] = []
    depth = 0
    where = None
    for index, record in enumerate(records):
        if record["where_text"] != where:
            where = record["where_text"]
            rows.append(TraceRow("separator", where=f"; ---- {where}"))
        following = registers_after(records, after, index)
        taken = following is not None and following["pc"] != record["next"]
        name = record.get("name", "")
        operands = record["operands"]
        before, _, rest = operands.partition(name) if name else (operands, "", "")
        marker = {"call": "▸", "return": "◂"}.get(record["flow"], "")
        rows.append(TraceRow("instruction", index, record["address"], depth, marker,
                             record["mnemonic"], before, name, rest,
                             record.get("name_type", ""),
                             register_changes(record["registers"], following), where))  # fmt: skip
        if record["flow"] == "call" and taken:
            depth += 1
        elif record["flow"] == "return" and taken:
            depth -= 1
    shallowest = min((row.depth for row in rows if row.kind == "instruction"), default=0)
    return [replace(row, depth=row.depth - shallowest) if row.kind == "instruction" else row
            for row in rows]  # fmt: skip


def trace_detail(records: list[dict[str, Any]], after: dict[str, str] | None,
                 row: TraceRow) -> dict[str, Any]:  # fmt: skip
    """The detail box of a selected row: position, where/depth/accesses, registers table.

    Returns:
        "position" ("27 / 30"), "bytes" ("140B  3A 33 B7"), "line" (where · depth · accesses),
        "registers": (name, before, after, changed) in the order of the box (two per line).
    """
    record = records[row.index]
    following = registers_after(records, after, row.index)
    accesses = [f"{a['access']}s {a['address']}" + (f" ({a['variable']})" if "variable" in a
                else "") + f" = {a['value']}" for a in record.get("accesses", [])]  # fmt: skip
    parts = [record["where_text"], f"depth {row.depth}", *accesses]
    if "accesses" not in record:
        parts.append("memory accesses: stepped instructions only")
    before = record["registers"] | {"pc": record["address"]}
    registers = []
    for left, right in DETAIL_ORDER:
        for name in (left, right):
            value_after = following[name.lower()] if following is not None else ""
            changed = bool(value_after) and value_after != before[name.lower()] and name != "PC"
            registers.append((name, before[name.lower()], value_after, changed))
    return {"position": f"{row.index + 1} / {len(records)}",
            "bytes": f"{record['address']}  {record['bytes']}", "line": " · ".join(parts),
            "registers": registers}  # fmt: skip
