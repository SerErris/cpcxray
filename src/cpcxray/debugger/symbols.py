"""Symbols and comments per address, and the annotation file (design.md §6.13, D-26, FR-DBG-09).

A symbol gives an address a label, a comment, or both. Symbols have a *scope*:

* "" (global): RAM addresses and firmware jumpblocks, valid whatever is paged in;
* "lower": the lower (OS) ROM;
* "rom0" … "rom15": an upper ROM slot, so ROM labels do not leak into other ROMs or RAM.

A lookup for an address in a scope first checks that scope, then the global symbols. Which
scope applies to an address at run time (ROM paged in or not) is decided by the caller; the
memory map provides it from phase 4.

The firmware jumpblock and restart names of the machine are preloaded (source "firmware") as
upper-case labels (TXT_OUTPUT, JP_HL): the 464, 664 and 6128 have different sets
(debugger/firmware_symbols.py); `set_firmware` switches them when the machine changes.
Everything the owner or the tools add has source "user" and is what the annotation file
stores. The firmware names are fixed by the machine (D-43): an owner label at a firmware
name's address is refused, the owner's comment there is joined to the firmware's
("TXT OUTPUT · hooked by my program"). Label lookups by name fall back to a case-insensitive
match.

Symbol types (task 5g, owner 2026-10-22): firmware names are "FW"; the owner's labels are
"sub" (a subroutine), "jump" (a jump target inside code) or "data" (a table, a string); a
comment without a label has no type. The owner's *variables* (`OwnVariable`, type "var") are
named byte ranges with a size: at an absolute address, or at an offset in the data area (IY)
of a background ROM, which the firmware assigns at boot - they are resolved like the known
OS/AMSDOS variables (debugger/variables.py) and used wherever those are.

Comment blocks (task 9, owner 2026-10-23): besides the comment at the end of its line, an
address can have a block of comment lines *before* it (a routine header) and one *after* it (a
longer explanation between two instructions). Blocks are the owner's only (`before`, `after`:
text with line breaks); the source generator writes them as `;` lines around the instruction.

Operand overrides (D-42): the owner decides for the word operand of the instruction at an
address whether it is a number ("value": always hex, `ld hl,#0010` and not `ld hl,SIDE_CALL`)
or an address ("address": named, even where the judgement of `ld rr,nn` says number).

Groups and contexts (task 10, D-43, D-44): the owner creates groups (name, description, order)
and puts their variables and global (RAM) labels in at most one; the built-in groups
"firmware" (the firmware names and RAM variables) and "amsdos" hold what cpcxray knows. A RAM
address may carry one owner label per group plus one ungrouped: the key of a global symbol is
(scope, address, group), that of a ROM symbol (scope, address). A context gives a code range
(or a whole scope, ROM or RAM) a group: where a RAM address is named, the item of the group of
the code's context is preferred (debugger/names.py `NameResolver`). A comment needs no label to
have a group: several programs copied to the same RAM each keep their own comments there, and
code sees only the comments of its context's group and the ungrouped ones (`owner_at`).
Without groups everything works as before.

The annotation file (project/annotations.txt) is read and written line by line in
debugger/annotation_file.py; `load` / `save` here apply and order its entries. Files of
earlier versions load unchanged (everything ungrouped, no contexts); an owner label they hold
at a firmware name's address is left out with a warning (`warnings`), its comment kept. The
data types are in debugger/symbol_types.py; this module re-exports them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from cpcxray.debugger.annotation_file import (
    FILE_HEADER,
    format_annotation_line,
    format_context_line,
    format_group_line,
    format_operand_line,
    format_symbol_lines,
    format_variable_line,
    parse_annotation_line,
    parse_variable_base,
)
from cpcxray.debugger.firmware_symbols import FIRMWARE_BY_MODEL
from cpcxray.debugger.symbol_types import (
    BLOCK_POSITIONS,
    BUILTIN_GROUPS,
    FIRMWARE_GROUP,
    GLOBAL,
    LABEL_PATTERN,
    OPERAND_ROLES,
    OWNER_TYPES,
    SCOPE_PATTERN,
    AnnotationEntry,
    BlockLine,
    Context,
    Group,
    OperandOverride,
    OwnVariable,
    Symbol,
    SymbolError,
    SymbolKey,
    block_lines,
    normalize_block,
    parse_address,
    validate_scope,
)

__all__ = [  # the names other modules import from here (the types live in symbol_types)
    "BLOCK_POSITIONS",
    "BUILTIN_GROUPS",
    "FILE_HEADER",
    "FIRMWARE_GROUP",
    "GLOBAL",
    "LABEL_PATTERN",
    "OPERAND_ROLES",
    "OWNER_TYPES",
    "SCOPE_PATTERN",
    "AnnotationEntry",
    "BlockLine",
    "Context",
    "Group",
    "OperandOverride",
    "OwnVariable",
    "Symbol",
    "SymbolError",
    "SymbolKey",
    "SymbolTable",
    "block_lines",
    "format_annotation_line",
    "format_context_line",
    "format_group_line",
    "format_operand_line",
    "format_symbol_lines",
    "format_variable_line",
    "merge",
    "normalize_block",
    "parse_address",
    "parse_annotation_line",
    "parse_variable_base",
    "validate_scope",
]


NOTE_PARTS = ("comment", "before", "after")  # what only a context's group shows (D-44)


def _where(scope: str, address: int) -> str:
    """An address with its scope as in messages: "rom5:C123" or "BE8F"."""
    return f"{scope + ':' if scope else ''}{address:04X}"


class SymbolTable:  # pylint: disable=too-many-instance-attributes,too-many-public-methods  # all owner knowledge
    """All symbols, variables, groups and contexts, with lookups by address and by label."""

    def __init__(self, preload_firmware: bool = True, model: str = "464") -> None:
        """Create the table, optionally with the firmware jumpblock names of a machine.

        Args:
            preload_firmware: Preload the firmware names.
            model: The machine whose firmware names are preloaded: "464", "664" or "6128".
        """
        self._symbols: dict[SymbolKey, Symbol] = {}  # the owner's layer
        self._firmware: dict[int, Symbol] = {}  # the firmware layer, all global
        self._variables: dict[str, OwnVariable] = {}  # the owner's variables by label
        self._operands: dict[tuple[str, int], OperandOverride] = {}  # by (scope, address)
        self._groups: list[Group] = []  # the owner's groups, in their order
        self._contexts: dict[tuple[str, int | None, int | None], Context] = {}  # by place
        self.firmware_model: str | None = None
        self.modified = False  # the owner's knowledge changed since load / save
        self.warnings: list[str] = []  # from loading annotation files
        if preload_firmware:
            self.set_firmware(model)

    def set_firmware(self, model: str) -> None:
        """Replace the preloaded firmware names by those of another machine (D-26).

        The owner's symbols stay; at a firmware address only their comments count (D-43).

        Raises:
            SymbolError: For an unknown model.
        """
        if model not in FIRMWARE_BY_MODEL:
            raise SymbolError(f"no firmware names for model {model!r}: 464, 664 or 6128")
        self._firmware = {}
        for entry in FIRMWARE_BY_MODEL[model]:
            comment = entry.name + (f" ({entry.note})" if entry.note else "")
            self._firmware[entry.address] = Symbol(
                entry.address, entry.label, comment, GLOBAL, "firmware", "FW", group=FIRMWARE_GROUP
            )
        self.firmware_model = model

    # -- the owner's labels and comments -----------------------------------------------------

    def add(  # pylint: disable=too-many-arguments  # the parts of a symbol
        self,
        address: int,
        label: str | None = None,
        comment: str | None = None,
        scope: str = GLOBAL,
        type: str | None = None,  # pylint: disable=redefined-builtin  # the MCP parameter name
        *,
        before: str | None = None,
        after: str | None = None,
        group: str | None = None,
        replaces: str | None = None,
    ) -> Symbol:
        """Add or change the owner's symbol at (scope, address[, group]).

        A label must be a valid identifier and unique among the global symbols, those of the
        same scope and the owner's variables. A part not given (None) keeps the owner's
        earlier one; an empty comment block ("") removes that block. A label's type is "sub",
        "jump" or "data" (default: the earlier one, else "sub"). At a firmware name's address
        the owner may add comments but no other label (D-43); a label or comment equal to the
        firmware's is not stored again (older annotation files may repeat it).

        Args:
            address: The address.
            label: The label, or None to keep the earlier one.
            comment: The comment at the end of the line, or None to keep the earlier one.
            scope: "" (global), "lower" or "romN".
            type: "sub", "jump" or "data" for a label.
            before: The comment block before the line (several lines); "" removes it.
            after: The comment block after the line; "" removes it.
            group: Global labels only (D-44): the owner's group of the label, "" for the
                ungrouped one, None for the one symbol at the address (an error if there are
                several).
            replaces: Moves a global label to `group`: the group it is in now ("" for the
                ungrouped one). The parts not given are taken over from it.

        Returns:
            The symbol as lookups now see it (both layers, see `at`); an empty one when
            removing a block left nothing at the address.

        Raises:
            SymbolError: On an invalid label, scope, type, group or address, a duplicate label,
                a label at a firmware name's address, a move without a symbol to move or onto
                another one, or if no part at all is given.
        """
        validate_scope(scope)
        if type is not None and type not in OWNER_TYPES:
            raise SymbolError(f"invalid type {type!r} (use {', '.join(OWNER_TYPES)})")
        if not 0 <= address <= 0xFFFF:
            raise SymbolError(f"address {address} out of range")
        key = self._key(address, scope, group)
        if key[2] is not None:
            self._check_owner_group(key[2])
        source = key if replaces is None else self._move_source(key, group, replaces)
        if source == key and label is None and comment is None and before is None and after is None:
            raise SymbolError("a symbol needs a label, a comment or a comment block")
        existing = self._symbols.get(source)
        if existing is not None:
            label = existing.label if label is None else label
            comment = existing.comment if comment is None else comment
            type = existing.type if type is None else type
            before = existing.before if before is None else before
            after = existing.after if after is None else after
        before, after = normalize_block(before), normalize_block(after)
        firmware = self._firmware.get(address)
        if firmware is not None:
            label = None if label == firmware.label else label
            comment = None if comment == firmware.comment else comment
            if label is not None:
                raise SymbolError(f"{address:04X} is the firmware name {firmware.label}: it "
                                  "cannot get another label (a comment can)")  # fmt: skip
        if label is not None:
            self._check_label(label, key, source)
        self._symbols.pop(source, None)  # a moved symbol leaves its old group
        if label is None and comment is None and before is None and after is None:
            self._symbols.pop(key, None)  # nothing of the owner's left
        else:
            type = (type or "sub") if label is not None else None
            self._symbols[key] = Symbol(
                address, label, comment, scope, "user", type, before, after, key[2]
            )
        self.modified = True
        found = self._with_firmware(address, self._symbols.get(key))
        return found if found is not None else Symbol(address, scope=scope)  # nothing left

    def remove(self, address: int, scope: str = GLOBAL, group: str | None = None) -> Symbol:
        """Remove the owner's symbol at (scope, address[, group]) and return it.

        A firmware name stays; firmware names themselves cannot be removed. `group` as in
        `add`.

        Raises:
            SymbolError: If the owner has no symbol there, or several and no group is given.
        """
        key = self._key(address, validate_scope(scope), group)
        try:
            removed = self._symbols.pop(key)
        except KeyError:
            where = _where(scope, address)
            if address in self._firmware:
                message = f"only the firmware name at {where}; it cannot be removed"
            else:
                message = f"no symbol at {where}" + (f" in group {key[2]}" if key[2] else "")
            raise SymbolError(message) from None
        self.modified = True
        return removed

    def _key(self, address: int, scope: str, group: str | None) -> SymbolKey:
        """The key of the owner's symbol addressed by (scope, address, group) - see `add`."""
        if scope != GLOBAL:
            if group:
                raise SymbolError(f"labels in scope {scope} cannot have a group (D-44)")
            return scope, address, None
        if group is not None:
            return GLOBAL, address, group or None
        keys = [key for key in self._symbols if key[0] == GLOBAL and key[1] == address]
        if len(keys) > 1:
            names = ", ".join(f"{self._symbols[k].label or '-'} ({k[2] or 'ungrouped'})"
                              for k in sorted(keys, key=lambda k: k[2] or ""))  # fmt: skip
            raise SymbolError(f"several symbols at {address:04X}: {names} - give the group")
        return keys[0] if keys else (GLOBAL, address, None)

    def _move_source(self, key: SymbolKey, group: str | None, replaces: str) -> SymbolKey:
        """The key of the global label `add` moves to `key` from group `replaces` (D-44).

        Raises:
            SymbolError: Outside the global scope, without a target group, without a symbol
                in the old group, or onto another symbol in the new one.
        """
        scope, address, _group = key
        if scope != GLOBAL:
            raise SymbolError(f"labels in scope {scope} have no group to move from (D-44)")
        if group is None:
            raise SymbolError('give the group to move the label to ("" for no group)')
        source = (GLOBAL, address, replaces or None)
        if replaces:
            self._check_owner_group(replaces)
        if source not in self._symbols:
            where = f"in group {replaces}" if replaces else "without a group"
            raise SymbolError(f"no symbol of yours at {address:04X} {where}")
        if source != key and key in self._symbols:
            where = f"group {key[2]}" if key[2] else "no group"
            raise SymbolError(f"{address:04X} already has a symbol in {where}: "
                              f"{self._symbols[key].label or '-'}")  # fmt: skip
        return source

    def _check_label(
        self, label: str, key: SymbolKey | None, moved: SymbolKey | None = None
    ) -> None:
        """Raise SymbolError unless `label` is valid and free (a symbol elsewhere, a variable).

        Args:
            label: The label.
            key: The symbol's key (its own label there is free); None for a variable.
            moved: The key of a symbol being moved to `key` (its label is free too).
        """
        if not LABEL_PATTERN.match(label):
            raise SymbolError(f"invalid label {label!r}")
        scope = key[0] if key is not None else GLOBAL
        for other in [*self._symbols.values(), *self._firmware.values()]:
            same = (other.label or "").lower() == label.lower()  # case-insensitive, as before
            if same and other.scope in (scope, GLOBAL) and other.key not in (key, moved):
                group = f" in group {other.group}" if other.group else ""
                raise SymbolError(f"label {label!r} already used at {other.address:04X}{group}")
        if key is not None and label.lower() in self._variables:
            raise SymbolError(f"label {label!r} already used by a variable")

    # -- the owner's variables ---------------------------------------------------------------

    def add_variable(self, variable: OwnVariable, replaces: str | None = None) -> OwnVariable:
        """Add or change one of the owner's variables (type "var").

        Args:
            variable: The variable; its label must be free (but for the one it replaces), its
                group one of the owner's.
            replaces: Label of the variable it replaces (a rename or change), or None.

        Raises:
            SymbolError: On an invalid label, size, address, ROM or group, or a label in use.
        """
        if not 1 <= variable.size <= 0x10000:
            raise SymbolError(f"invalid size {variable.size} (1-65536)")
        if not 0 <= variable.address <= 0xFFFF:
            raise SymbolError(f"address {variable.address} out of range")
        if variable.rom is not None and not 0 <= variable.rom <= 15:
            raise SymbolError(f"invalid ROM {variable.rom} (0-15)")
        if variable.group is not None:
            self._check_owner_group(variable.group)
        self._check_label(variable.label, None)
        key = variable.label.lower()
        if key in self._variables and key != (replaces or "").lower():
            raise SymbolError(f"label {variable.label!r} already used by a variable")
        if replaces is not None:
            self._variables.pop(replaces.lower(), None)
        self._variables[key] = variable
        self.modified = True
        return variable

    def remove_variable(self, label: str) -> OwnVariable:
        """Remove one of the owner's variables and return it.

        Raises:
            SymbolError: If there is no such variable.
        """
        try:
            removed = self._variables.pop(label.lower())
        except KeyError:
            raise SymbolError(f"no variable {label!r} of yours") from None
        self.modified = True
        return removed

    def variable(self, label: str) -> OwnVariable | None:
        """Return the owner's variable with this label (case-insensitive), or None."""
        return self._variables.get(label.lower())

    def own_variables(self) -> list[OwnVariable]:
        """The owner's variables, sorted by label."""
        return sorted(self._variables.values(), key=lambda variable: variable.label.lower())

    # -- groups (D-43) -------------------------------------------------------------------------

    def groups(self) -> list[Group]:
        """The owner's groups in their order (the built-in ones are not listed)."""
        return list(self._groups)

    def group(self, name: str) -> Group | None:
        """The owner's group of this name, or None."""
        return next((group for group in self._groups if group.name == name), None)

    def group_rank(self, name: str | None) -> int:
        """A group's position for the ranking (D-43): its index; unknown and None last."""
        names = [group.name for group in self._groups]
        return names.index(name) if name in names else len(names)

    def add_group(
        self, name: str, description: str | None = None, position: int | None = None
    ) -> Group:
        """Create a group, or change its description / position.

        Args:
            name: An identifier, not a built-in group's name.
            description: What it holds (None keeps the earlier one).
            position: Index in the list (None: keep its place, a new one goes last).

        Raises:
            SymbolError: On an invalid or built-in name, or a position out of range.
        """
        if not LABEL_PATTERN.match(name) or name in BUILTIN_GROUPS:
            raise SymbolError(f"invalid group name {name!r} (an identifier, not "
                              f"{' / '.join(BUILTIN_GROUPS)})")  # fmt: skip
        old = self.group(name)
        index = self._groups.index(old) if old is not None else len(self._groups)
        group = Group(name, description if description is not None else
                      (old.description if old is not None else None))  # fmt: skip
        if old is not None:
            self._groups.remove(old)
        target = index if position is None else position
        if not 0 <= target <= len(self._groups):
            raise SymbolError(f"position {position} out of range (0-{len(self._groups)})")
        self._groups.insert(target, group)
        self.modified = True
        return group

    def group_usage(self, name: str) -> dict[str, list[str]]:
        """What uses a group: "variables", "labels" (their names), "contexts" (their places)."""
        return {
            "variables": [v.label for v in self.own_variables() if v.group == name],
            "labels": [s.label or f"{s.address:04X}" for s in self.owner_symbols()
                       if s.group == name],
            "contexts": [c.text for c in self.contexts() if c.group == name],
        }  # fmt: skip

    def remove_group(self, name: str, force: bool = False) -> Group:
        """Remove a group; refused while items or contexts use it, unless `force`.

        With `force` its items become ungrouped and its contexts are deleted.

        Raises:
            SymbolError: On an unknown or built-in group, a group in use without `force`, or
                a label that cannot become ungrouped (an ungrouped label is at its address).
        """
        group = self._owner_group(name)
        usage = self.group_usage(name)
        if any(usage.values()) and not force:
            used = "; ".join(
                f"{kind}: {', '.join(names)}" for kind, names in usage.items() if names
            )
            raise SymbolError(f"group {name} is in use ({used}) - remove with force")
        for key in [k for k in self._symbols if k[2] == name]:
            if (GLOBAL, key[1], None) in self._symbols:
                raise SymbolError(f"{key[1]:04X} has an ungrouped label already: rename or "
                                  "remove one of them first")  # fmt: skip
        self._regroup(name, None)
        for place in [p for p, c in self._contexts.items() if c.group == name]:
            del self._contexts[place]
        self._groups.remove(group)
        self.modified = True
        return group

    def rename_group(self, old: str, new: str) -> Group:
        """Rename a group everywhere (its labels, variables and contexts).

        Raises:
            SymbolError: On an unknown, built-in or taken name.
        """
        group = self._owner_group(old)
        if self.group(new) is not None:
            raise SymbolError(f"group {new} exists already")
        renamed = self.add_group(new, group.description, self._groups.index(group))
        self._groups.remove(group)
        self._regroup(old, new)
        for place, context in list(self._contexts.items()):
            if context.group == old:
                self._contexts[place] = replace(context, group=new)
        self.modified = True
        return renamed

    def _regroup(self, old: str, new: str | None) -> None:
        """Move the labels and variables of group `old` to group `new` (None: ungrouped)."""
        for key in [k for k in self._symbols if k[2] == old]:
            symbol = self._symbols.pop(key)
            self._symbols[(key[0], key[1], new)] = replace(symbol, group=new)
        for label, variable in list(self._variables.items()):
            if variable.group == old:
                self._variables[label] = replace(variable, group=new)

    def _owner_group(self, name: str) -> Group:
        """The owner's group of this name (SymbolError if unknown or built-in)."""
        group = self.group(name)
        if group is None:
            built_in = name in BUILTIN_GROUPS
            raise SymbolError(f"group {name} is built in" if built_in else f"no group {name}")
        return group

    def _check_owner_group(self, name: str) -> None:
        """Raise SymbolError unless `name` is one of the owner's groups (items go there)."""
        if name in BUILTIN_GROUPS:
            raise SymbolError(f"group {name} is built in: it holds what cpcxray knows")
        if self.group(name) is None:
            raise SymbolError(f"no group {name} (create it with group_add)")

    # -- contexts (D-43) -----------------------------------------------------------------------

    def set_context(
        self, group: str, scope: str, first: int | None = None, last: int | None = None
    ) -> Context:
        """Give code a group: a range of a scope, or the whole scope (first and last None).

        Raises:
            SymbolError: On an invalid scope, range or group.
        """
        validate_scope(scope)
        self._check_owner_group(group)
        if (first is None) != (last is None):
            raise SymbolError("give first and last, or neither (the whole scope)")
        if first is not None and last is not None and not 0 <= first <= last <= 0xFFFF:
            raise SymbolError(f"invalid range {first:04X}-{last:04X}")
        context = Context(scope, first, last, group)
        self._contexts[context.place] = context
        self.modified = True
        return context

    def remove_context(
        self, scope: str, first: int | None = None, last: int | None = None
    ) -> Context:
        """Remove the context of exactly this place.

        Raises:
            SymbolError: If there is none.
        """
        try:
            removed = self._contexts.pop((validate_scope(scope), first, last))
        except KeyError:
            place = Context(scope, first, last, "").text
            raise SymbolError(f"no context at {place}") from None
        self.modified = True
        return removed

    def contexts(self, scope: str | None = None) -> list[Context]:
        """The contexts (of one scope, or all), by scope, whole scope first, then by range."""
        found = [c for c in self._contexts.values() if scope is None or c.scope == scope]
        return sorted(found, key=lambda c: (c.scope, -1 if c.first is None else c.first,
                                            -1 if c.last is None else c.last))  # fmt: skip

    def context_at(self, scope: str, address: int) -> Context | None:
        """The context of code at (scope, address): the narrowest range, else the whole scope."""
        ranges = [c for c in self._contexts.values()
                  if c.first is not None and c.contains(scope, address)]  # fmt: skip
        if ranges:
            return min(ranges, key=lambda c: (c.last or 0) - (c.first or 0))
        return self._contexts.get((scope, None, None))

    # -- the owner's operand overrides --------------------------------------------------------

    def set_operand(
        self, address: int, role: str, scope: str = GLOBAL, comment: str | None = None
    ) -> OperandOverride | None:
        """Decide the operand of the instruction at (scope, address).

        Args:
            address: The instruction's address.
            role: "value" (a number), "address" (named) or "auto" (remove the decision: the
                judgement applies again).
            scope: "" (global), "lower" or "romN".
            comment: Why, or None.

        Returns:
            The override, or None after "auto".

        Raises:
            SymbolError: On an invalid role, scope or address.
        """
        validate_scope(scope)
        if not 0 <= address <= 0xFFFF:
            raise SymbolError(f"address {address} out of range")
        if role not in (*OPERAND_ROLES, "auto"):
            raise SymbolError(f"invalid role {role!r} (use value, address or auto)")
        self.modified = True
        if role == "auto":
            self._operands.pop((scope, address), None)
            return None
        override = OperandOverride(address, scope, role, comment or None)
        self._operands[(scope, address)] = override
        return override

    def operand_at(self, address: int, scope: str = GLOBAL) -> str | None:
        """The owner's operand decision at `address` ("value" / "address"; scope, else global)."""
        override = self._operands.get((scope, address)) or self._operands.get((GLOBAL, address))
        return override.role if override is not None else None

    def operand_overrides(self) -> list[OperandOverride]:
        """The owner's operand overrides, sorted by scope and address."""
        return [self._operands[key] for key in sorted(self._operands)]

    # -- lookups -----------------------------------------------------------------------------

    def at(self, address: int, scope: str = GLOBAL, prefer: str | None = None) -> Symbol | None:
        """Return the symbol for `address` in `scope`, falling back to the global one.

        At a firmware name's address the firmware label wins (D-43); the owner's comment and
        blocks lie over it (comments joined firmware first, "TXT OUTPUT · my comment"; the
        result has source "user"). Several global owner's labels at the address: see
        `owner_at` (`prefer`: the group of the code's context).
        """
        return self._with_firmware(address, self.owner_at(address, scope, prefer))

    def _with_firmware(self, address: int, owner: Symbol | None) -> Symbol | None:
        """The owner's symbol at `address` laid over the firmware name there (see `at`)."""
        firmware = self._firmware.get(address)
        if owner is None or firmware is None:
            return owner or firmware
        comments = [c for c in (firmware.comment, owner.comment) if c]
        return Symbol(
            address, firmware.label, " · ".join(comments) or None, owner.scope,
            type=firmware.type, before=owner.before, after=owner.after, group=FIRMWARE_GROUP,
        )  # fmt: skip

    def owner_at(
        self, address: int, scope: str = GLOBAL, prefer: str | None = None, strict: bool = False
    ) -> Symbol | None:
        """Return only the owner's symbol for `address` (in `scope`, else global), or None.

        With several global entries at the address (D-44) the label comes from the best ranked
        one that has a label (D-43): the group `prefer` (the code's context), then the
        ungrouped one, then the groups in their order. The comment and each comment block come
        only from the entry of `prefer`'s group, else from the ungrouped one: a comment of
        another group describes other code copied to the same RAM. Without a context (`prefer`
        None) they come from the best ranked entry that has them, or with `strict` (the
        generated source) only from the ungrouped one.
        """
        if scope != GLOBAL:
            symbol = self._symbols.get((scope, address, None))
            if symbol is not None:
                return symbol
        candidates = self.owners_at(address, prefer)
        if prefer is not None or strict:  # rank 0: the context's group, 1: ungrouped
            candidates_with_notes = [s for s in candidates if self.rank(s.group, prefer)[0] < 2]
        else:
            candidates_with_notes = candidates
        notes = {part: next((getattr(s, part) for s in candidates_with_notes if getattr(s, part)),
                            None) for part in NOTE_PARTS}  # fmt: skip
        labelled = next((s for s in candidates if s.label), None)
        if labelled is None:
            if not any(notes.values()):
                return None  # only comments of other groups
            labelled = next(s for s in candidates_with_notes
                            if any(getattr(s, part) for part in NOTE_PARTS))  # fmt: skip
        if all(getattr(labelled, part) == text for part, text in notes.items()):
            return labelled
        return replace(labelled, **notes)

    def scope_symbol(self, address: int, scope: str) -> Symbol | None:
        """Only the owner's symbol of exactly this ROM scope (no global fallback), or None."""
        return self._symbols.get((scope, address, None)) if scope != GLOBAL else None

    def owners_at(self, address: int, prefer: str | None = None) -> list[Symbol]:
        """All the owner's global symbols at `address`, ranked (see `owner_at`)."""
        found = [s for key, s in self._symbols.items() if key[0] == GLOBAL and key[1] == address]
        return sorted(found, key=lambda s: self.rank(s.group, prefer))

    def rank(self, group: str | None, prefer: str | None) -> tuple[int, int]:
        """The ranking of D-43 for an item's group (smaller is better).

        (a) `prefer` (the group of the code's context), (b) built-in and ungrouped, (c) the
        other groups in their order.
        """
        if prefer is not None and group == prefer:
            return 0, 0
        if group is None or group in BUILTIN_GROUPS:
            return 1, 0
        return 2, self.group_rank(group)

    def firmware_at(self, address: int) -> Symbol | None:
        """Return only the firmware symbol at `address`, or None."""
        return self._firmware.get(address)

    def label_at(self, address: int, scope: str = GLOBAL, prefer: str | None = None) -> str | None:
        """Return the label for `address` (see `at`), or None."""
        symbol = self.at(address, scope, prefer)
        return symbol.label if symbol is not None else None

    def comment_at(
        self, address: int, scope: str = GLOBAL, prefer: str | None = None
    ) -> str | None:
        """Return the comment for `address` (see `at`), or None."""
        symbol = self.at(address, scope, prefer)
        return symbol.comment if symbol is not None else None

    def find(self, label: str, scope: str = GLOBAL) -> Symbol | None:
        """Return the symbol with this label in `scope` or globally, or None.

        An exact match wins; otherwise a label that differs only in case is accepted if it is
        unique, so "txt_output" still finds the firmware's TXT_OUTPUT.
        """
        candidates = [
            s
            for s in [*self._symbols.values(), *self._firmware.values()]
            if s.scope in (scope, GLOBAL) and s.label
        ]
        for symbol in candidates:
            if symbol.label == label:
                return self.at(symbol.address, symbol.scope, symbol.group)
        folded = [s for s in candidates if (s.label or "").lower() == label.lower()]
        if len(folded) == 1:
            return self.at(folded[0].address, folded[0].scope, folded[0].group)
        return None

    def symbols(
        self,
        text: str = "",
        scope: str | None = None,
        source: str | None = None,
        group: str | None = None,
    ) -> list[Symbol]:
        """List symbols sorted by scope, address and group, as lookups see them (see `at`).

        A firmware name with the owner's comment over it is listed once, with source "user".

        Args:
            text: Only symbols whose label, comment or a comment block contains this text
                (case-insensitive).
            scope: Only this scope, or None for all.
            source: Only "firmware" or "user", or None for both.
            group: Only this group ("" for ungrouped), or None for all.
        """
        needle = text.lower()
        layered = [self._with_firmware(key[1], symbol) for key, symbol in self._symbols.items()]
        owned = {key[1] for key in self._symbols if key[0] == GLOBAL}
        layered += [s for a, s in self._firmware.items() if a not in owned]
        result = [
            symbol
            for symbol in layered
            if symbol is not None
            and (scope is None or symbol.scope == scope)
            and (source is None or symbol.source == source)
            and (group is None or (symbol.group or "") == group)
            and (
                not needle
                or any(
                    needle in (text or "").lower()
                    for text in (symbol.label, symbol.comment, symbol.before, symbol.after)
                )
            )
        ]
        return sorted(result, key=lambda symbol: symbol.sort_key)

    def owner_symbols(self) -> list[Symbol]:
        """The owner's layer only, sorted by scope, address and group (the annotation file)."""
        return sorted(self._symbols.values(), key=lambda symbol: symbol.sort_key)

    # -- annotation file ---------------------------------------------------------------------

    def load(self, path: str | Path) -> int:
        """Merge the owner's knowledge from an annotation file into the table.

        Groups first (unknown names in `group=` create a group, with a warning), then the
        entries in file order; the blocks of one symbol are joined. Warnings (also for an
        owner label left out at a firmware name's address) go to `warnings`.

        Returns:
            The number of entries read.

        Raises:
            SymbolError: On a malformed line (the message gives file and line number).
        """
        was_modified = self.modified
        self.warnings = []
        parsed: list[tuple[int, AnnotationEntry]] = []
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            try:
                entry = parse_annotation_line(line)
            except SymbolError as error:
                raise SymbolError(f"{path}:{number}: {error}") from None
            if entry is not None:
                parsed.append((number, entry))
        for _number, entry in parsed:  # the groups first: entries may name them
            if isinstance(entry, Group):
                self.add_group(entry.name, entry.description)
        blocks: dict[tuple[str, int, str | None, str], list[str]] = {}  # lines per block
        for number, entry in parsed:
            try:
                self._load_entry(entry, blocks)
            except SymbolError as error:
                raise SymbolError(f"{path}:{number}: {error}") from None
        for (scope, address, group, position), lines in blocks.items():  # a file's block replaces
            self.add(address, scope=scope, group=group or "", **{position: "\n".join(lines)})
        self.modified = was_modified  # what the file holds is not an unsaved change
        return len(parsed)

    def _load_entry(
        self, entry: AnnotationEntry, blocks: dict[tuple[str, int, str | None, str], list[str]]
    ) -> None:
        """Apply one entry of an annotation file (blocks are collected in `blocks`)."""
        group = getattr(entry, "group", None)
        if not isinstance(entry, (Group, OperandOverride)) and group and self.group(group) is None:
            if group in BUILTIN_GROUPS:
                raise SymbolError(f"group {group} is built in: it holds what cpcxray knows")
            self.add_group(group)
            self.warnings.append(f"group {group} was not defined: created without a description")
        if isinstance(entry, BlockLine):
            key = (entry.scope, entry.address, entry.group, entry.position)
            blocks.setdefault(key, []).append(entry.text)
        elif isinstance(entry, OperandOverride):
            self.set_operand(entry.address, entry.role, entry.scope, entry.comment)
        elif isinstance(entry, Context):
            self.set_context(entry.group, entry.scope, entry.first, entry.last)
        elif isinstance(entry, Symbol):
            self._merge_entry(self._without_firmware_clash(entry))
        elif isinstance(entry, OwnVariable):
            self._merge_entry(entry)

    def _without_firmware_clash(self, symbol: Symbol) -> Symbol:
        """An older file's owner label at a firmware name's address is left out (D-43)."""
        firmware = self._firmware.get(symbol.address)
        if firmware is None or symbol.label is None or symbol.label == firmware.label:
            return symbol
        self.warnings.append(f"label {symbol.label} at {symbol.address:04X} left out: the "
                             f"address is the firmware name {firmware.label}")  # fmt: skip
        return replace(symbol, label=None, type=None, group=None)

    def _merge_entry(self, entry: Symbol | OwnVariable) -> None:
        """Add a symbol or variable read from a file or another table (it replaces its own)."""
        if isinstance(entry, OwnVariable):
            self.add_variable(entry, replaces=entry.label)
        elif entry.label or entry.comment or entry.before or entry.after:
            self.add(entry.address, entry.label, entry.comment, entry.scope, entry.type,
                     before=entry.before, after=entry.after,
                     group=(entry.group or "") if entry.scope == GLOBAL else None)  # fmt: skip

    def save(self, path: str | Path) -> int:
        """Write the owner's knowledge to an annotation file.

        Order: groups, contexts, symbols (with their blocks), variables, operand overrides.

        Returns:
            The number of entries written (groups and contexts included).
        """
        user = self.owner_symbols()
        variables = self.own_variables()
        operands = self.operand_overrides()
        contexts = self.contexts()
        text = (FILE_HEADER + "".join(format_group_line(g) for g in self._groups)
                + "".join(format_context_line(c) for c in contexts)
                + "".join(format_symbol_lines(s) for s in user)
                + "".join(format_variable_line(v) for v in variables)
                + "".join(format_operand_line(o) for o in operands))  # fmt: skip
        Path(path).write_text(text, encoding="utf-8")
        self.modified = False
        return len(self._groups) + len(contexts) + len(user) + len(variables) + len(operands)


def merge(tables: Iterable[SymbolTable]) -> SymbolTable:
    """A new table with the owner's knowledge of all `tables` (later ones win).

    Groups, contexts, symbols, variables and operand overrides.
    """
    tables = list(tables)
    model = next((table.firmware_model for table in tables if table.firmware_model), "464")
    merged = SymbolTable(preload_firmware=True, model=model)
    for table in tables:
        for group in table.groups():
            merged.add_group(group.name, group.description)
        for context in table.contexts():
            merged.set_context(context.group, context.scope, context.first, context.last)
        for entry in [*table.owner_symbols(), *table.own_variables()]:
            merged._merge_entry(entry)  # pylint: disable=protected-access  # same class
        for override in table.operand_overrides():
            merged.set_operand(override.address, override.role, override.scope, override.comment)
    merged.modified = False
    return merged
