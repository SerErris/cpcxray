"""sjasmplus source generator (design.md §6.15; D-22, D-31, D-32, FR-DIS-05).

Walks a target linearly and emits every byte exactly once, from the analysis database (never
from the trace, D-31) and the final clear image (`TargetView.resolve`):

| Bytes                         | Emitted as                                                   |
|-------------------------------|--------------------------------------------------------------|
| instruction (owner/observed/  | mnemonic with labels; `; [static]` for static guesses,       |
| static)                       | `; CONFLICT` if a byte was read both with M1 and as data     |
| RST 1/2/3/5                   | `rst #nn ; NAME` + `defw … ;   to …` (CPC profile, D-30),    |
|                               | plus "(observed: rom6)" where the call was seen arriving     |
| non-canonical encoding        | `defb` + the mnemonic as comment (D-32)                      |
| far address object            | `defw target` + `defb rom_select` with comments              |
| strings                       | `defb "TEXT"`, CPC terminator as `"E"+#80`                   |
| word table (owner)            | `defw label` (words are addresses)                           |
| word data (owner)             | `defw #0010` (words are numbers: never a name)               |
| other data                    | `defb`, 8 per line                                           |
| relocated block               | `DISP #rrrr` … `ENT`, labels in RAM coordinates (D-22)       |
| position-independent RAM copy | at its ROM address, a comment line where it starts (D-22)    |
| never-read bytes (scrambled)  | inside `; ---- never accessed: … ----` markers               |

A ROM header at &C000 gets its own labels (`rom_type` … `rom_name_table`, `rom_jumpblock`)
and the jumpblock entries the RSX names as comments and labels (`rom_init`, `rsx_<name>`).

Labels exist only where a line starts: the owner's label of the address first (it replaces an
`rsx_<name>`; the jumpblock comment keeps the RSX name), then header names and firmware symbols,
else names by use: `ram_xxxx` inside a relocated block, `far_xxxx` for far address objects,
`sub_xxxx` for call targets, `l_xxxx` for jump targets, `d_xxxx` for other referenced addresses.
Owner comments go to the end of the line; a commented data byte starts a new data line. The
owner's comment blocks (task 9) become `;` lines at column 0: the block before an address above
its label (a routine header), the block after it below its line; a data run starts at a block
before and ends at a block after, so each block sits exactly at its byte. A
reference to an address that is not a line start is written in hex; a reference to an address
outside the source that has a global (firmware or owner) symbol uses it, defined by an `EQU`. A
label always has the value of the number it replaces, so the bytes stay the same.

Known RAM variables of the firmware and AMSDOS (FR-DBG-11, D-34; passed in by the session for
the machine and, for AMSDOS, where it is installed) name referenced addresses outside the source
that have no owner or firmware symbol: the smallest variable containing the address, written
`LABEL` or `LABEL+n`, with one `EQU` per variable in its own block of the header.

Immediate loads `ld rr,nn` get a label only where the value is an address (analysis/operands.py):
the code after the load uses it as one (LDIR's HL/DE, `(hl)`, ...), or - without evidence - it
is a line start of the source, lies in the target's own address range, is a firmware
jumpblock entry (&B900 and up) or the first byte of a known variable. Counters (LDIR's BC,
DJNZ, `dec bc` loops) and other plain numbers stay hex: `ld bc,#0016`, not `ld bc,JP_DE`.
The owner's operand override at an instruction (annotation file `value` / `address`) decides
instead of this judgement, for any instruction with a word operand.

A far address (RST 3) that selects another upper ROM points into that ROM's code: it never
gets a label of this target (`defw #C1DC ; ROM 7` for an AMSDOS routine called from another ROM,
not that ROM's own `sub_c1dc`), neither in the far address nor as the FAR CALL's target. The same
holds for a SIDE CALL (RST 2) to a slot other than 0. Select bytes &FC-&FF keep the current
upper ROM, so their target is this ROM's.

The file starts with a reference block of all low-kernel firmware entries (the restarts
RESET_ENTRY … EXT_INTERRUPT at &0000-&003B, the same on 464, 664 and 6128), whether the code
uses them or not, so the names are at hand when the source is edited; then the EQUs of the
other firmware entries the code refers to. Reference names are never used for plain numbers.

`GeneratedSource.line_of_offset` maps every target byte to its source line, for the
round-trip report (D-33).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cpcxray.analysis.classifier import (
    RomHeader,
    TargetView,
    far_object_offset,
    find_rom_header,
)
from cpcxray.analysis.cpc_profile import CpcProfile, rom_select_text
from cpcxray.analysis.database import Kind, Source, TargetDatabase
from cpcxray.analysis.operands import Role, immediate_role, loaded_pair
from cpcxray.analysis.provenance import RelocatedBlock
from cpcxray.analysis.source_format import (
    COMMENT_COLUMN,
    INDENT,
    _equ,
    _excluding,
    _format_line,
    _identifier,
    _no_label,
    name_type,
    split_data,
    string_operands,
)
from cpcxray.cpu.disasm import Flow, Instruction, hex_byte, hex_word
from cpcxray.debugger.firmware_symbols import FIRMWARE_464
from cpcxray.debugger.names import best_name, context_group
from cpcxray.debugger.symbols import BUILTIN_GROUPS, GLOBAL, Symbol, SymbolTable
from cpcxray.debugger.variables import Variable
from cpcxray.decoding.shadow_rom import ERROR, UNKNOWN

ROM_TYPES = {0: "foreground ROM", 1: "background ROM", 2: "extension ROM"}
HEADER_LABELS = ("rom_type", "rom_mark", "rom_version", "rom_modification")
_CALL_FLOWS = frozenset({Flow.CALL, Flow.CONDITIONAL_CALL})
_JUMP_FLOWS = frozenset({Flow.JUMP, Flow.CONDITIONAL_JUMP})
_PREFIX_BY_USE = {"ram": "ram_", "far": "far_", "call": "sub_", "jump": "l_", "data": "d_"}
_USE_PRIORITY = ("ram", "far", "call", "jump", "data")
FIRMWARE_JUMPBLOCKS = 0xB900  # high kernel, key manager, ... jumpblocks up to &BDFF
LOW_KERNEL = tuple(entry for entry in FIRMWARE_464 if entry.section == "low kernel")
STATISTICS = ("instructions_observed", "instructions_static", "instructions_owner",
              "non_canonical", "data_bytes", "labels", "externals")  # fmt: skip


@dataclass
class _Item:  # pylint: disable=too-many-instance-attributes  # one record per emitted unit
    """A unit of output covering `length` bytes from `offset`.

    Attributes:
        offset: First byte.
        length: Number of bytes.
        kind: "insn", "far", "data" (one byte, merged into lines later) or "header".
        instruction: The decoded instruction ("insn").
        source: Classification source of the instruction.
        group: Data group: "string", "words" (addresses), "values" (numbers) or "bytes".
        comment: Extra comment (header entries, jumpblock RSX names).
        forced_label: A label the layout requires (header).
    """

    offset: int
    length: int
    kind: str
    instruction: Instruction | None = None
    source: Source = Source.NONE
    group: str = "bytes"
    comment: str = ""
    forced_label: str | None = None


@dataclass
class GeneratedSource:
    """The generated source text and how it maps to the target.

    Attributes:
        text: The sjasmplus source.
        line_of_offset: Source line (1-based) of every target byte.
        statistics: Counts for the report (instructions per source, data bytes, labels, ...).
        names: The names the analysis gave (not the owner's or firmware ones), for the
            Symbols tab (task 5g): (address, scope, label, type "sub" / "jump" / "data").
    """

    text: str
    line_of_offset: list[int]
    statistics: dict[str, int] = field(default_factory=dict)
    names: list[tuple[int, str, str, str]] = field(default_factory=list)


# One entry point (generate); the state is shared by the passes.
class SourceGenerator:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Generates the source of one target (see the module docstring)."""

    def __init__(  # pylint: disable=too-many-arguments  # every input of the generator
        self,
        view: TargetView,
        database: TargetDatabase,
        symbols: SymbolTable,
        *,
        title: str,
        output_name: str,
        fill_policy: str,
        conflict: str = "m1",
        alternative_blocks: tuple[RelocatedBlock, ...] = (),
        variables: tuple[Variable, ...] = (),
    ) -> None:
        """Prepare the generator.

        Args:
            view: The target with its final image (`TargetView.resolve`).
            database: The classification.
            symbols: Owner and firmware symbols.
            title: First comment line, e.g. the ROM file name.
            output_name: File name for the OUTPUT directive.
            fill_policy: Fill policy of never-read bytes (recorded in the header).
            conflict: Conflict choice (recorded in the header).
            alternative_blocks: Further RAM copies of the blocks, listed as comments.
            variables: Known firmware/AMSDOS RAM variables of the machine (FR-DBG-11).
        """
        self.view = view
        self.database = database
        self.symbols = symbols
        self.title = title
        self.output_name = output_name
        self.fill_policy = fill_policy
        self.conflict = conflict
        self.alternative_blocks = alternative_blocks
        self.profile = CpcProfile(image_range=(view.start, view.start + view.length - 1))
        self.header: RomHeader | None = None
        self.items: list[_Item] = []
        self.labels: dict[int, str] = {}  # offset → label
        self.names: list[tuple[int, str, str, str]] = []  # the analysis's names (GeneratedSource)
        self._context: RelocatedBlock | None = None  # block of the line being emitted
        self.externals: dict[tuple[int, str | None], str] = {}  # (address, context) → name
        self._external_names: dict[int, str] = {}  # address → its first name (data words)
        self.label_equs: dict[str, tuple[int, str | None, bool]] = {}  # → address, group, FW
        self._code_scope = view.name if view.name == "lower" or view.name.startswith("rom") else ""
        self._contexts: dict[int, str | None] = {}  # offset → group of its code's context
        self._emit_group: str | None = None  # the context of the line being emitted
        self._boundaries: set[int] = set()
        self._far_starts: set[int] = set()
        self._copy_marks: dict[int, RelocatedBlock] = {}
        self._roles: dict[int, Role] = {}  # immediate loads: offset → address or number
        self.references: dict[int, tuple[str, str]] = {}  # unused low-kernel EQUs: label, name
        self.variables = variables
        self._variable_starts = {v.address for v in variables if not v.area}
        self.variable_equs: dict[str, Variable] = {}  # variables used: label → variable
        self._access_edges: set[int] = set()
        self._in_never_read = False
        self._lines: list[str] = []
        self._line_of_offset: list[int] = []
        self._statistics: dict[str, int] = {}

    # -- pass 1: items ---------------------------------------------------------------------------

    def _scope(self, offset: int) -> str:
        """Symbol scope of a byte: the target slot, or global (RAM) inside a block."""
        if self.view.block_at(offset) is not None or self.view.name.startswith("ram:"):
            return GLOBAL
        return self.view.name

    def _collect_boundaries(self) -> None:
        """Collect the offsets no item may span: block edges and header fields.

        Data lines also split at the edges between read and never-read bytes; instructions
        may span them (an operand read as data, the opcode never fetched).
        """
        view = self.view
        for block in view.blocks:
            self._boundaries |= {block.rom_start, block.rom_start + block.length}
        if view.access is not None:
            for offset in range(1, view.length):
                if (view.access[offset] == UNKNOWN) != (view.access[offset - 1] == UNKNOWN):
                    self._access_edges.add(offset)
        if self.header is not None:
            self._boundaries |= {0, 1, 2, 3, 4, 6, self.header.table_end}
            self._boundaries |= {offset for offset, _, _ in self.header.names}
            self._boundaries |= {offset + length for offset, length, _ in self.header.names}

    def _crosses_boundary(self, offset: int, length: int) -> bool:
        """True if a boundary lies strictly inside offset..offset+length."""
        return any(offset < boundary < offset + length for boundary in self._boundaries)

    def _instruction_item(self, offset: int) -> _Item | None:
        """Return the instruction starting at an offset if it can be emitted as such."""
        view, kinds = self.view, self.database.kinds
        instruction = view.disassemble(offset, self.profile)
        length = instruction.length
        if not view.fits(offset, length) or self._crosses_boundary(offset, length):
            return None
        if any(
            kinds[offset + i] not in (Kind.INSN_BODY, Kind.RST_OPERAND) for i in range(1, length)
        ):
            return None
        return _Item(offset, length, "insn", instruction, Source(self.database.sources[offset]))

    def _far_object_starts(self) -> set[int]:
        """Offsets of far address objects: recorded at run time or named by a FAR CALL."""
        starts = set()
        for offset in range(self.view.length):
            if self.database.observations_at(offset).get("far_object"):
                starts.add(offset)
            if self.database.kinds[offset] == Kind.INSN_START and self.view.image[offset] == 0xDF:
                far = far_object_offset(self.view, self.view.disassemble(offset, self.profile))
                if far is not None:
                    starts.add(far)
        return starts

    def _far_item(self, offset: int) -> _Item | None:
        """Return a far address object starting at an offset, if one is known there."""
        kinds = self.database.kinds
        if offset not in self._far_starts:
            return None
        if offset + 3 > self.view.length or not self.view.fits(offset, 3):
            return None
        if any(kinds[offset + i] != Kind.FAR_ADDR for i in range(3)):
            return None
        return None if self._crosses_boundary(offset, 3) else _Item(offset, 3, "far")

    def _data_group(self, offset: int) -> str:
        """Data group of a byte: owner strings and header names, owner word tables, bytes.

        A word table holds addresses (written with their names), word data holds numbers
        (always hex: a parameter &0010 must not become SIDE_CALL, the name of RST 2).
        """
        kind = self.database.kinds[offset]
        if kind == Kind.STRING:
            return "string"
        if kind == Kind.WORD_TABLE:
            return "words"
        if kind == Kind.WORD_DATA:
            return "values"
        header = self.header
        if header is not None and header.name_table <= offset < header.table_end:
            return "string"
        return "bytes"

    def _build_items(self) -> None:
        """Cut the target into items (pass 1)."""
        offset = 0
        while offset < self.view.length:
            item = self._header_item(offset)
            if item is None and self.database.kinds[offset] == Kind.INSN_START:
                item = self._instruction_item(offset)
            if item is None and self.database.kinds[offset] == Kind.FAR_ADDR:
                item = self._far_item(offset)
            if item is None:
                item = _Item(offset, 1, "data", group=self._data_group(offset))
            self.items.append(item)
            offset += item.length

    def _header_item(self, offset: int) -> _Item | None:
        """Return the header field at an offset (type, mark, version, modification, pointer)."""
        if self.header is None or offset > 4:
            return None
        if offset == 4:
            return _Item(4, 2, "header", comment="name table", forced_label="rom_name_pointer")
        comment = ROM_TYPES.get(self.header.rom_type & 0x7F, "") if offset == 0 else ""
        if offset == 0 and self.header.rom_type & 0x80:
            comment += " (built in)"
        return _Item(offset, 1, "header", comment=comment, forced_label=HEADER_LABELS[offset])

    # -- pass 2: references and labels -----------------------------------------------------------

    def _judge_immediates(self) -> None:
        """Decide for every `ld rr,nn` whether nn is an address or a number."""
        view = self.view
        for item in self.items:
            if item.instruction is None or loaded_pair(item.instruction) is None:
                continue
            block = view.block_at(item.offset)

            def following(address: int, block: RelocatedBlock | None = block) -> Instruction:
                """Decode the instruction at an address in the load's coordinates."""
                offset = view.offset_for_read(address, block)
                if offset is None:
                    return Instruction(address, b"\x76", "halt", Flow.HALT)  # outside: stop
                return view.disassemble(offset, self.profile)

            self._roles[item.offset] = immediate_role(item.instruction, following)

    def _wants_label(self, item: _Item, word: int) -> bool:
        """True if the word operand of an item should be shown as a label (module docstring).

        The owner's operand override ("value" / "address") wins over the judgement.
        """
        decided = self.symbols.operand_at(self.view.address(item.offset), self._scope(item.offset))
        if decided is not None:
            return decided == "address"
        role = self._roles.get(item.offset)
        if role is None or role == Role.ADDRESS:
            return True
        if role == Role.NUMBER:
            return False
        context = self.view.block_at(item.offset)
        return (self.view.offset_of_target(word, context) is not None
                or word >= FIRMWARE_JUMPBLOCKS or word in self._variable_starts)  # fmt: skip

    def _item_references(self, item: _Item) -> list[tuple[int, str]]:
        """Addresses an item refers to, with their use ("call", "jump", "far", "data")."""
        references = []
        if item.kind == "far" and self._same_rom(self._word(item.offset),
                                                 self.view.image[item.offset + 2]):  # fmt: skip
            references.append((self._word(item.offset), "data"))
        instruction = item.instruction
        if instruction is None:
            return references
        target = instruction.target
        if self._foreign_call_target(item) is not None:  # FAR / SIDE CALL into another ROM
            target = None
        if target is not None and instruction.flow in _CALL_FLOWS:
            references.append((target, "call"))
        elif target is not None and instruction.flow in _JUMP_FLOWS:
            references.append((target, "jump"))
        word = instruction.word_operand
        if word is not None and word != instruction.target and self._wants_label(item, word):
            references.append((word, "data"))
        for line in instruction.extra_lines:
            if line.word is not None:
                references.append((line.word, "far" if instruction.raw[0] == 0xDF else "jump"))
        return references

    def _references(self) -> set[tuple[int, str | None]]:
        """All referenced addresses with the context of the code that refers to them."""
        return {(address, self._context_of(item.offset))
                for item in self.items for address, _ in self._item_references(item)}  # fmt: skip

    def _word(self, offset: int) -> int:
        """Return the little-endian word of the final image at an offset."""
        return self.view.image[offset] | self.view.image[offset + 1] << 8

    def _assign_labels(self) -> None:
        """Name every referenced line start, plus symbols and header labels at line starts."""
        uses = self._uses_by_offset()
        names = self._header_names()
        used_names: set[str] = set()
        for item in self.items:
            owner = self._owner_symbol(item.offset)  # the owner's name wins over RSX names
            name = item.forced_label or (owner.label if owner is not None else None)
            from_analysis = item.forced_label is not None  # a header field's name
            if name is None and item.offset in names:
                name, from_analysis = names[item.offset], True
            name = name or self._symbol_label(item.offset)
            if name is None:
                name, from_analysis = self._generated_label(item, uses), True
            if name is None:
                continue
            unique, number = name, 2
            while unique in used_names:
                unique, number = f"{name}_{number}", number + 1
            used_names.add(unique)
            self.labels[item.offset] = unique
            if from_analysis:
                self.names.append((self.view.address(item.offset), self._scope(item.offset),
                                   unique, name_type(name)))  # fmt: skip
        self._assign_externals(used_names)

    def _uses_by_offset(self) -> dict[int, set[str]]:
        """Map referenced line starts (offsets) to their uses ("call", "jump", ...).

        A reference is resolved in the coordinates of the block it is made from, so code in
        an overlay refers to its own copy.
        """
        starts = {item.offset for item in self.items}
        uses: dict[int, set[str]] = {}
        for item in self.items:
            context = self.view.block_at(item.offset)
            for address, use in self._item_references(item):
                offset = self.view.offset_of_target(address, context)
                if offset is not None and offset in starts:
                    uses.setdefault(offset, set()).add(use)
        return uses

    def _owner_symbol(self, offset: int) -> Symbol | None:
        """Return the owner's (user) symbol of a byte's address, if any (by its context)."""
        return self.symbols.owner_at(self.view.address(offset), self._scope(offset),
                                     self._context_of(offset))  # fmt: skip

    def _owner_comment(self, offset: int) -> str:
        """Return the owner's comment at a byte's address, or ""."""
        symbol = self._owner_symbol(offset)
        return symbol.comment or "" if symbol is not None else ""

    def _owner_block(self, offset: int, position: str) -> list[str]:
        """The owner's comment block before / after a byte's line as `;` lines (task 9)."""
        symbol = self._owner_symbol(offset)
        block = getattr(symbol, position) if symbol is not None else None
        return [f"; {text}".rstrip() for text in block.split("\n")] if block else []

    def _symbol_label(self, offset: int) -> str | None:
        """Return the owner/firmware label of a byte's address, if any (by its context)."""
        symbol = self.symbols.at(self.view.address(offset), self._scope(offset),
                                 self._context_of(offset))  # fmt: skip
        return symbol.label if symbol is not None and symbol.label else None

    def _context_of(self, offset: int) -> str | None:
        """The group of the context of the code at an offset (D-43).

        Looked up at the byte's ROM address in the target's scope - so a RAM copy (DISP
        block) has its ROM origin's context.
        """
        if offset not in self._contexts:
            self._contexts[offset] = context_group(self.symbols, self._code_scope,
                                                   self.view.start + offset)  # fmt: skip
        return self._contexts[offset]

    def _generated_label(self, item: _Item, uses: dict[int, set[str]]) -> str | None:
        """Name a referenced line start by its use: ram_, far_, sub_, l_ or d_ + address."""
        offset = item.offset
        if offset not in uses:
            return None
        block = self.view.block_at(offset)
        address = self.view.address(offset)
        if block is not None and self.view.overlaid(block):  # same RAM, other ROM routines
            return f"ram_{address:04x}_{self.view.start + offset:04x}"
        use = "ram" if block is not None else next(u for u in _USE_PRIORITY if u in uses[offset])
        return f"{_PREFIX_BY_USE[use]}{address:04x}"

    def _header_names(self) -> dict[int, str]:
        """Labels from the ROM header: name table, jumpblock and the RSX routines."""
        header = self.header
        if header is None:
            return {}
        names = {header.name_table: "rom_name_table", 6: "rom_jumpblock"}
        for index, entry in enumerate(header.jumpblock[: len(header.names)]):
            item = self._item_at(entry)
            if item is None or item.instruction is None or item.instruction.target is None:
                continue
            target = self.view.offset_of_target(item.instruction.target)
            if target is None or target in names:
                continue
            part = _identifier(header.names[index][2])
            names[target] = "rom_init" if index == 0 else f"rsx_{part or index}"
        return names

    def _item_at(self, offset: int) -> _Item | None:
        """Return the item starting at an offset (linear search, header use only)."""
        return next((item for item in self.items if item.offset == offset), None)

    def _inside_source(self, address: int) -> bool:
        """True if the address belongs to the target's own address space (ROM or block RAM)."""
        view = self.view
        if view.start <= address < view.start + view.length:
            return True
        return any(b.ram_start <= address < b.ram_start + b.length for b in view.blocks)

    def _assign_externals(self, used_names: set[str]) -> None:
        """Names for referenced addresses outside the source (EQU lines).

        Each reference is named as its code's context sees it (D-43, debugger/names.py): the
        firmware name, else the owner's RAM label or variable of the context's group, built-in
        and ungrouped ones, other groups. Every name used gets its EQU.
        """
        for address, group in sorted(self._references(), key=lambda ref: (ref[0], ref[1] or "")):
            if self._inside_source(address):
                continue
            name = best_name(self.symbols, list(self.variables), address, GLOBAL, group)
            if name is None:
                continue
            if name.variable is not None:
                self._name_by_variable(address, group, name.variable, used_names)
                continue
            if name.text not in self.label_equs:
                if name.text in used_names:
                    continue  # the name is taken by a label of the source
                used_names.add(name.text)
                self.label_equs[name.text] = (address, name.group, name.type == "FW")
            self._name_external(address, group, name.text)
        for entry in LOW_KERNEL:  # the reference block (module docstring)
            symbol = self.symbols.at(entry.address, GLOBAL)
            label = symbol.label if symbol is not None and symbol.label else entry.label
            if self._inside_source(entry.address) or entry.address in self._external_names:
                continue
            if label not in used_names:
                self.references[entry.address] = (label, entry.name)
                used_names.add(label)

    def _name_by_variable(
        self, address: int, group: str | None, variable: Variable, used_names: set[str]
    ) -> None:
        """Name an outside address by a variable containing it (`LABEL+n`), with its EQU."""
        if variable.label not in self.variable_equs:
            if variable.label in used_names:
                return  # the name is taken by a label of the source
            used_names.add(variable.label)
            self.variable_equs[variable.label] = variable
        offset = address - variable.address
        self._name_external(address, group, variable.label + (f"+{offset}" if offset else ""))

    def _name_external(self, address: int, group: str | None, name: str) -> None:
        """Remember the name of an outside address for code of one context."""
        self.externals[(address, group)] = name
        self._external_names.setdefault(address, name)

    def _same_rom(self, address: int, select: int) -> bool:
        """True unless a far address selects another upper ROM for an upper-ROM address.

        Select bytes 252-255 only set the ROM enables (the current upper ROM stays), so they
        count as this ROM; a lower-ROM target has no own upper ROM number.
        """
        if address < 0xC000 or select >= 252:
            return True
        name = self.view.name
        return name.startswith("rom") and name[3:].isdigit() and int(name[3:]) == select

    def _foreign_call_target(self, item: _Item) -> int | None:
        """The target of a call into another ROM's code, else None.

        A FAR CALL whose far object selects another upper ROM, or a SIDE CALL to a slot other
        than 0 (the next ROMs of a multi-ROM program): the target address means a byte of that
        ROM, never one of this target (as the classifier's `successors` sees it).
        """
        instruction = item.instruction
        if instruction is None:
            return None
        raw = instruction.raw
        if raw[0] == 0xD7 and len(raw) == 3 and raw[2] >> 6 != 0:
            return instruction.target
        if raw[0] != 0xDF:
            return None
        far = far_object_offset(self.view, instruction)
        if far is None:
            return None
        target, select = self._word(far), self.view.image[far + 2]
        return None if self._same_rom(target, select) else target

    def _lookup(self, address: int) -> str | None:
        """Label for an operand (in the coordinates of the line being emitted), or None."""
        offset = self.view.offset_of_target(address, self._context)
        if offset is not None:
            return self.labels.get(offset)
        if self._inside_source(address):
            return None  # inside the target but not a line start, or an ambiguous overlay
        return self.externals.get((address, self._emit_group)) or self._external_names.get(address)

    # -- pass 3: text ----------------------------------------------------------------------------

    def generate(self) -> GeneratedSource:
        """Generate the source."""
        self.header = find_rom_header(self.view)
        self._collect_boundaries()
        self._far_starts = self._far_object_starts()
        self._build_items()
        self._judge_immediates()
        self._assign_labels()
        self._copy_marks = {  # item offset → RAM copy starting inside the item (D-22)
            item.offset: copy
            for copy in self.view.ram_copies
            for item in self.items
            if item.offset <= copy.rom_start < item.offset + item.length
        }
        self._lines = self._file_header()
        self._line_of_offset = [0] * self.view.length
        self._statistics = dict.fromkeys(STATISTICS, 0)
        self._statistics |= {"labels": len(self.labels), "externals": len(self.externals)}
        index = 0
        while index < len(self.items):
            index = self._emit_from(index)
        text = "\n".join(self._lines) + "\n"
        return GeneratedSource(text, self._line_of_offset, self._statistics, self.names)

    def _emit_from(self, index: int) -> int:
        """Emit the item at `index` (a whole data run for data); return the next index."""
        item = self.items[index]
        self._context = self.view.block_at(item.offset)
        self._emit_group = self._context_of(item.offset)  # D-43: names as this code sees them
        self._lines += self._before(item.offset)
        self._lines += self._open_never_read(index)
        self._lines += self._owner_block(item.offset, "before")
        if item.kind == "data":
            end = index
            while (
                end + 1 < len(self.items)
                and not self._owner_block(self.items[end].offset, "after")
                and self._continues_data(self.items[end + 1], item)
            ):
                end += 1
            self._emit_data(item.offset, self.items[end].offset + 1, item.group)
            following = end + 1
        else:
            start_line = len(self._lines) + 1 + (1 if item.offset in self.labels else 0)
            self._lines += self._item_lines(item)
            for offset in range(item.offset, item.offset + item.length):
                self._line_of_offset[offset] = start_line
            following = index + 1
        last = self.items[following - 1]
        self._lines += self._owner_block(last.offset, "after")
        self._lines += self._close_never_read(following)
        self._lines += self._after(last.offset + last.length)
        return following

    def _never_read_item(self, index: int) -> bool:
        """True if every byte of the item at `index` was never read (scrambled ROMs)."""
        item = self.items[index]
        return all(self.view.never_read(o) for o in range(item.offset, item.offset + item.length))

    def _starts_block_section(self, offset: int) -> bool:
        """True at the start or end of a relocated block (markers do not cross them)."""
        return any(offset in (b.rom_start, b.rom_start + b.length) for b in self.view.blocks)

    def _open_never_read(self, index: int) -> list[str]:
        """Open a never-accessed marker before a run of items whose bytes were all unread."""
        if self._in_never_read or not self._never_read_item(index):
            return []
        end = index + 1
        while (
            end < len(self.items)
            and self._never_read_item(end)
            and not self._starts_block_section(self.items[end].offset)
        ):
            end += 1
        first = self.items[index].offset
        last = self.items[end - 1].offset + self.items[end - 1].length
        self._in_never_read = True
        start, stop = self.view.start + first, self.view.start + last - 1
        return ["", f"; ---- never accessed: {hex_word(start)}-{hex_word(stop)} "
                f"({last - first} bytes, fill: {self.fill_policy}) ----"]  # fmt: skip

    def _close_never_read(self, following: int) -> list[str]:
        """Close the marker when the next item was (partly) read or starts a block section."""
        if not self._in_never_read:
            return []
        at_end = following >= len(self.items)
        if (
            at_end
            or not self._never_read_item(following)
            or self._starts_block_section(self.items[following].offset)
        ):
            self._in_never_read = False
            return ["; ---- end of never accessed ----", ""]
        return []

    def _continues_data(self, item: _Item, first: _Item) -> bool:
        """True if a data item joins the data run that started with `first`."""
        return (
            item.kind == "data"
            and item.group == first.group
            and item.offset not in self.labels
            and item.offset not in self._boundaries
            and item.offset not in self._access_edges
            and not self._owner_comment(item.offset)  # a commented byte starts a line
            and not self._owner_block(item.offset, "before")
            and item.offset not in self._copy_marks
        )

    def _file_header(self) -> list[str]:
        """Title, parameters, EQUs, OUTPUT and ORG."""
        view = self.view
        lines = [f"; {self.title} - generated by cpcxray",
                 f"; target {view.name}, fill policy: {self.fill_policy}, "
                 f"conflicts: {self.conflict}"]  # fmt: skip
        if view.access is not None:
            counts = {name: 0 for name in ("M1", "data", "unknown", "conflict")}
            for access_class in view.access:
                counts[
                    {0x01: "M1", 0x00: "data", UNKNOWN: "unknown"}.get(access_class, "conflict")
                ] += 1
            lines.append("; access: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        lines.append("")
        low_kernel = {e.address: e.name for e in LOW_KERNEL if not self._inside_source(e.address)}
        if low_kernel:
            lines.append("; firmware restarts and low kernel entries (464/664/6128), for reference")
            for address, name in sorted(low_kernel.items()):
                label = (self._external_names.get(address)
                         or self.references.get(address, ("",))[0])  # fmt: skip
                if label:
                    lines.append(f"{_equ(label, address):<{COMMENT_COLUMN}}; {name}")
            lines.append("")
        used = sorted((address, label) for label, (address, _group, firmware)
                      in self.label_equs.items()
                      if firmware and address not in low_kernel)  # fmt: skip
        if used:
            lines.append("; firmware entries used by this code")
        for address, label in used:
            lines.append(_equ(label, address))
        if used:
            lines.append("")
        lines += self._group_sections()
        lines += [f'{INDENT}OUTPUT "{self.output_name}"', f"{INDENT}ORG {hex_word(view.start)}", ""]
        return lines

    def _group_sections(self) -> list[str]:
        """The EQUs of the variables and RAM labels used, one section per group (D-43).

        In group order: built-in "firmware" and "amsdos", the owner's groups, then "other"
        (the owner's ungrouped labels and variables); within a section by address.
        """
        headings = {"firmware": "firmware variables", "amsdos": "AMSDOS variables"}
        order: list[str | None] = [*BUILTIN_GROUPS]
        for group in self.symbols.groups():
            order.append(group.name)
            headings[group.name] = group.description or ""
        order.append(None)
        lines: list[str] = []
        for group in order:
            entries: list[tuple[int, str]] = []
            for variable in self.variable_equs.values():
                if variable.group == group or (group is None and variable.group not in order):
                    size = f"{variable.size} byte" + ("s" if variable.size > 1 else "")
                    equ = _equ(variable.label, variable.address)
                    text = f"{equ:<{COMMENT_COLUMN}}; {size}: {variable.description}"
                    entries.append((variable.address, text))
            for label, (address, label_group, firmware) in self.label_equs.items():
                if not firmware and label_group == group:
                    entries.append((address, _equ(label, address)))
            if not entries:
                continue
            if group is None:
                lines.append("; ---- other ----")
            else:
                heading = headings.get(group, "")
                lines.append(
                    f"; ---- {group}: {heading} ----" if heading else f"; ---- {group} ----"
                )
            lines += [text for _address, text in sorted(entries, key=lambda entry: entry[0])]
            lines.append("")
        return lines

    def _before(self, offset: int) -> list[str]:
        """Directives that open at an offset (start of a relocated block)."""
        lines = []
        view = self.view
        for block in view.blocks:
            if block.rom_start == offset:
                end = block.ram_start + block.length - 1
                origin = hex_word(view.start + offset)
                lines += ["", f"; ---- copied to RAM {hex_word(block.ram_start)}-{hex_word(end)}"
                          f" (from {origin}), executed there ----"]  # fmt: skip
                for other in self.alternative_blocks:
                    if other.rom_start == block.rom_start and other.ram_start != block.ram_start:
                        lines.append(f"; ---- also copied to RAM {hex_word(other.ram_start)} ----")
                lines.append(f"{INDENT}DISP {hex_word(block.ram_start)}")
        copy = self._copy_marks.get(offset)  # position-independent: stays in ROM (D-22)
        if copy is not None:
            end = copy.ram_start + copy.length - 1
            lines += ["", f"; ---- {hex_word(view.start + copy.rom_start)}-"
                      f"{hex_word(view.start + copy.rom_start + copy.length - 1)} copied to RAM "
                      f"{hex_word(copy.ram_start)}-{hex_word(end)} and executed there "
                      "(position-independent) ----"]  # fmt: skip
        return lines

    def _after(self, end: int) -> list[str]:
        """Directives that close before offset `end` (end of a relocated block)."""
        lines = []
        for block in self.view.blocks:
            if block.rom_start + block.length == end:
                ram_end = block.ram_start + block.length - 1
                rom_first = self.view.start + block.rom_start
                ent = f"{INDENT}ENT"
                lines += [f"{ent:<{COMMENT_COLUMN}}; end of DISP {hex_word(block.ram_start)}",
                          f"; ---- end of RAM copy {hex_word(block.ram_start)}-"
                          f"{hex_word(ram_end)} (from {hex_word(rom_first)}) ----", ""]  # fmt: skip
        return lines

    def _item_lines(self, item: _Item) -> list[str]:
        """Lines of an instruction, far address object or header field."""
        label = self.labels.get(item.offset)
        owner = self._owner_comment(item.offset)
        if item.kind == "header":
            comment = "; ".join(c for c in (item.comment, owner) if c)
            if item.length == 2:
                word = self._word(item.offset)
                return _format_line(f"defw {self._lookup(word) or hex_word(word)}", comment, label)
            return _format_line(f"defb {hex_byte(self.view.image[item.offset])}", comment, label)
        if item.kind == "far":
            word = self._word(item.offset)
            select = self.view.image[item.offset + 2]
            own = self._same_rom(word, select)
            target = (self._lookup(word) if own else None) or hex_word(word)
            target_name = target if own else hex_word(word)
            comment = "; ".join(c for c in (f"far address: to {target_name}", owner) if c)
            return _format_line(f"defw {target}", comment, label) + (
                _format_line(f"defb {hex_byte(select)}", f"  {rom_select_text(select)}")
            )
        return self._instruction_lines(item, label)

    def _instruction_lines(self, item: _Item, label: str | None) -> list[str]:
        """Lines of an instruction (defb for non-canonical encodings, D-32)."""
        lookup = self._lookup
        word = item.instruction.word_operand if item.instruction else None
        if word is not None and not self._wants_label(item, word):
            lookup = _no_label
        foreign = self._foreign_call_target(item)
        if foreign is not None:  # RST 3 into another ROM: no label of this target for it
            lookup = _excluding(lookup, foreign)
        instruction = self.view.disassemble(item.offset, self.profile, lookup)
        self._statistics[f"instructions_{item.source.name.lower()}"] = (
            self._statistics.get(f"instructions_{item.source.name.lower()}", 0) + 1
        )
        notes = self._notes(item)
        if instruction.non_canonical:
            self._statistics["non_canonical"] += 1
            data = ",".join(hex_byte(value) for value in instruction.raw)
            comment = "; ".join([instruction.text(lookup), *notes])
            return _format_line(f"defb {data}", comment, label)
        result: list[str] = []
        observed = self._observed_comment(item.offset)
        for number, (text, comment) in enumerate(instruction.lines(lookup)):
            if number == 0:
                comment = "; ".join(c for c in (comment.strip(), *notes) if c)
                result += _format_line(text, comment, label)
            else:
                result += _format_line(text, comment + observed if comment else observed.strip())
        return result

    def _notes(self, item: _Item) -> list[str]:
        """Comments for an instruction: owner comment, header role, static guess, conflict."""
        notes = []
        if user := self._owner_comment(item.offset):
            notes.append(user)
        if self.header is not None and item.offset in self.header.jumpblock:
            index = self.header.jumpblock.index(item.offset)
            if index >= len(self.header.names):
                notes.append(f"jumpblock entry {index} (no name)")
            else:
                name = self.header.names[index][2]
                notes.append(f'ROM init "{name}"' if index == 0 else f'RSX "{name}"')
        if item.source == Source.STATIC:
            notes.append("[static]")
        access = self.view.access
        if access is not None and any(
            access[o] == ERROR for o in range(item.offset, item.offset + item.length)
        ):
            notes.append("CONFLICT")
        return notes

    def _observed_comment(self, offset: int) -> str:
        """Return the "(observed: …)" note of an RST seen arriving or with a far target."""
        facts = self.database.observations_at(offset)
        parts = []
        if "far_target" in facts:
            parts.append(
                f"to {hex_word(facts['far_target'])} [{rom_select_text(facts['far_select'])}]"
            )
        if "reached" in facts:
            parts.append(f"reached {facts['reached']}")
        return f"  (observed: {', '.join(parts)})" if parts else ""

    def _emit_data(self, first: int, last: int, group: str) -> None:
        """Emit the data bytes first..last-1 of one group (label first, if any)."""
        self._statistics["data_bytes"] += last - first
        label = self.labels.get(first)
        if label:
            self._lines.append(f"{label}:")
        comment = self._owner_comment(first)
        for start, length, text in self._data_pieces(first, last, group):
            self._lines += _format_line(text, comment)
            comment = ""
            for offset in range(start, start + length):
                self._line_of_offset[offset] = len(self._lines)

    def _data_pieces(self, first: int, last: int, group: str) -> list[tuple[int, int, str]]:
        """Cut data into lines: (start offset, length, text)."""
        image = self.view.image
        pieces = []
        if group in ("words", "values"):  # addresses with their names, numbers in hex
            for offset in range(first, last - 1, 2):
                word = self._word(offset)
                name = self._lookup(word) if group == "words" else None
                pieces.append((offset, 2, f"defw {name or hex_word(word)}"))
            if (last - first) % 2:
                pieces.append((last - 1, 1, f"defb {hex_byte(image[last - 1])}"))
        elif group == "string":  # one line per CPC string (ends at the bit-7 character)
            start = first
            for offset in range(first, last):
                if image[offset] & 0x80 or offset == last - 1:
                    text = "defb " + string_operands(image[start : offset + 1])
                    pieces.append((start, offset + 1 - start, text))
                    start = offset + 1
        else:
            for start, length, is_string in split_data(image[first:last]):
                chunk = image[first + start : first + start + length]
                operands = string_operands(chunk) if is_string else ",".join(map(hex_byte, chunk))
                pieces.append((first + start, length, f"defb {operands}"))
        return pieces


def generate_source(  # pylint: disable=too-many-arguments  # forwards the generator inputs
    view: TargetView,
    database: TargetDatabase,
    symbols: SymbolTable,
    *,
    title: str,
    output_name: str,
    fill_policy: str,
    conflict: str = "m1",
    alternative_blocks: tuple[RelocatedBlock, ...] = (),
    variables: tuple[Variable, ...] = (),
) -> GeneratedSource:
    """Generate the sjasmplus source of a target (see `SourceGenerator`)."""
    return SourceGenerator(
        view, database, symbols, title=title, output_name=output_name, fill_policy=fill_policy,
        conflict=conflict, alternative_blocks=alternative_blocks, variables=variables,
    ).generate()  # fmt: skip
