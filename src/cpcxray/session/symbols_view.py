"""Session methods for symbols and variables (FR-DBG-09, FR-DBG-11; task 5g, the Symbols tab).

A mixin of `Session` (every public method is an MCP tool, D-27):

* the owner's labels and comments (`symbol_add` with a type "sub" / "jump" / "data",
  `symbol_remove`) - a comment at the end of the line and comment blocks before / after it
  (task 9: routine headers, longer explanations; `comment_add` with a position);
* the owner's operand overrides (`operand_set`): the word operand of an instruction is a number
  ("value": always hex) or an address ("address": named), over the judgement of `ld rr,nn`;
* the owner's variables (`variable_add`, `variable_remove`): a named byte range at an absolute
  address or in a background ROM's data area; they behave like the known OS/AMSDOS variables
  everywhere (debugger/variables.py);
* `symbol_search` for the Symbols tab: one list of every name - firmware (FW), known variables
  (VAR), the owner's variables (var) and labels (sub, jump, data), and the names the source
  generator gave (marked "analysis", until the owner renames them) - with counts per type;
  `symbol_detail` tells about one entry;
* the annotation file (`symbols_load`, `symbols_save`, `symbols_status`).

`symbol_list`, `variable_list` and `address_explain` are the older, narrower lookups (symbols
only, variables only, one address); MCP clients use them.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from cpcxray.debugger.firmware_symbols import FIRMWARE_BY_MODEL
from cpcxray.debugger.names import ranked_names
from cpcxray.debugger.symbols import (
    GLOBAL,
    OWNER_TYPES,
    OwnVariable,
    SymbolError,
    parse_variable_base,
)
from cpcxray.debugger.variable_tables import OS_VARIABLES_BY_MODEL
from cpcxray.session.debugger import DebuggerMixin
from cpcxray.session.errors import SessionError

if TYPE_CHECKING:
    from cpcxray.debugger.variables import Variable, VariableMap

ANNOTATIONS_FILE = Path("project") / "annotations.txt"
SYMBOL_TYPES = ("FW", "VAR", "var", "sub", "jump", "data")  # task 5g, in the tab's order
MAX_SEARCH = 5000
MODELS = ("464", "664", "6128")


def hex4(value: int) -> str:
    """Format a 16-bit value as 4 hex digits."""
    return f"{value:04X}"


class SymbolsMixin(DebuggerMixin):  # pylint: disable=abstract-method,too-many-public-methods  # Session implements the stubs; one method per MCP tool
    """Symbols, variables and the annotation file of `Session` (see the module docstring)."""

    variables: VariableMap | None
    analysis_names: dict[str, list[tuple[int, str, str, str]]]  # target → source_gen names

    # -- the owner's labels and comments ----------------------------------------------------

    def symbol_add(  # pylint: disable=too-many-arguments  # the parts of a symbol
        self,
        address: int | str,
        label: str | None = None,
        comment: str | None = None,
        scope: str = GLOBAL,
        type: str | None = None,  # pylint: disable=redefined-builtin  # the tool's word (5g)
        *,
        before: str | None = None,
        after: str | None = None,
        group: str | None = None,
    ) -> dict[str, Any]:
        """Add or change a label, comment or comment block at `address`.

        Parts not given keep what is there. The generated source writes the blocks as comment
        lines before / after the instruction (task 9).

        Args:
            address: The address.
            label: Identifier, unique within the scope, the global symbols and the variables.
            comment: Free text at the end of the line.
            scope: "" for global (RAM, firmware), "lower" (OS ROM) or "rom0".."rom15".
            type: The label's type: "sub" (subroutine, the default), "jump" (a jump target
                inside code) or "data" (a table, a string).
            before: Comment block before the line, e.g. a routine header; several lines
                separated by line breaks; "" removes it.
            after: Comment block after the line, e.g. a longer explanation; "" removes it.
            group: Global (RAM) labels only (D-44): your group of the label - one RAM address
                can carry one label per group; "" for the ungrouped one. Without it the one
                symbol at the address is meant (an error that names them if there are
                several). Not allowed in ROM scopes, nor a label at a firmware name's address.
        """
        try:
            symbol = self.symbols.add(self._address(address), label, comment, scope, type,
                                      before=before, after=after, group=group)  # fmt: skip
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"address": hex4(symbol.address), "label": symbol.label,
                "comment": symbol.comment, "scope": symbol.scope, "type": symbol.type,
                "before": symbol.before, "after": symbol.after,
                "group": symbol.group}  # fmt: skip

    def comment_add(
        self,
        address: int | str,
        comment: str,
        scope: str = GLOBAL,
        position: str = "line",
        group: str | None = None,
    ) -> dict[str, Any]:
        """Set a comment at `address` (keeps an existing label and the other comments).

        Args:
            address: The address.
            comment: The text; for a block several lines separated by line breaks; "" removes
                a block (`symbol_remove` removes everything at the address).
            scope: "" for global, "lower" or "rom0".."rom15".
            position: "line" (at the end of the line), "before" (a block before the line, e.g.
                a routine header) or "after" (a block after the line).
            group: Which RAM label's comment, where several are at the address (D-44).
        """
        if position == "line":
            return self.symbol_add(address, comment=comment, scope=scope, group=group)
        if position not in ("before", "after"):
            raise SessionError("position must be line, before or after")
        return self.symbol_add(address, scope=scope, group=group, **{position: comment})

    def symbol_remove(
        self, address: int | str, scope: str = GLOBAL, group: str | None = None
    ) -> dict[str, Any]:
        """Remove the owner's symbol at `address` in `scope` (a firmware name stays).

        `group`: which RAM label, where several are at the address (D-44; "" the ungrouped).
        """
        try:
            removed = self.symbols.remove(self._address(address), scope, group)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"removed": removed.label or hex4(removed.address), "scope": removed.scope,
                "group": removed.group}  # fmt: skip

    def operand_set(
        self,
        address: int | str,
        role: str,
        scope: str | None = None,
        comment: str | None = None,
    ) -> dict[str, Any]:
        """Decide whether the word operand of the instruction at `address` is a number.

        `ld hl,#0010` stays a number instead of `ld hl,SIDE_CALL` (the name of RST 2) with
        "value"; "address" names it even where the judgement of `ld rr,nn` says number. The
        decision wins in the disassembly, the trace and the generated source, and is kept in
        the annotation file (`symbols_save`).

        Args:
            address: The instruction's address.
            role: "value" (a number: always hex), "address" (named) or "auto" (remove the
                decision: cpcxray judges again).
            scope: "" (global, RAM), "lower" or "rom0".."rom15"; default: what is paged in at
                the address now.
            comment: Why (kept in the annotation file).
        """
        value = self._address(address)
        where = self.machine.symbol_scope(value) if scope is None else scope
        try:
            override = self.symbols.set_operand(value, role, where, comment)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"address": hex4(value), "scope": where,
                "role": override.role if override is not None else "auto",
                "comment": override.comment if override is not None else None}  # fmt: skip

    # -- the owner's variables --------------------------------------------------------------

    def variable_add(  # pylint: disable=too-many-arguments  # the parts of a variable
        self,
        label: str,
        size: int,
        base: int | str,
        comment: str | None = None,
        replaces: str | None = None,
        *,
        group: str | None = None,
    ) -> dict[str, Any]:
        """Add or change one of your variables (type "var"): a named byte range in RAM.

        It behaves like a known OS/AMSDOS variable: `LABEL+offset` in disassembly, traces and
        the generated source, colours in the memory view, breakpoints on its name.

        Args:
            label: Its label (unique among all labels).
            size: Size in bytes.
            base: An absolute address ("A612") or an offset in the data area (IY) of a
                background ROM, "rom5+0012": it follows that ROM's data area, which the
                firmware assigns at boot (KL_ROM_IY_TABLE).
            comment: What it holds.
            replaces: The label of the variable it replaces (to change or rename one).
            group: Your group (D-43; `group_add`); "" takes it out of its group; None keeps
                the group of the variable it replaces (else ungrouped).

        Returns:
            The variable with "now" - where it is at the moment ("A612-A621"), or None while
            its ROM has no data area.
        """
        if group is None and replaces is not None:
            old = self.symbols.variable(replaces)
            group = old.group if old is not None else None
        try:
            rom, address = (None, base) if isinstance(base, int) else parse_variable_base(base)
            variable = OwnVariable(label, size, address, rom, comment or None, group or None)
            self.symbols.add_variable(variable, replaces)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return self._own_variable(variable)

    def variable_remove(self, label: str) -> dict[str, Any]:
        """Remove one of your variables."""
        try:
            removed = self.symbols.remove_variable(label)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"removed": removed.label}

    def variable_locate(self, base: str, size: int = 1) -> dict[str, Any]:
        """Where a variable at `base` ("A612" or "rom5+0012") of `size` bytes is now.

        Returns:
            "first" and "last" address, or None for both while the ROM has no data area.
        """
        try:
            rom, address = parse_variable_base(base)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return self._where_now(OwnVariable("_", max(size, 1), address, rom))

    def _where_now(self, variable: OwnVariable) -> dict[str, Any]:
        """{"first", "last"}: where one of the owner's variables is now (None, None if not)."""
        first = None
        if self.variables is not None:
            first = self.variables.own_address(variable, self.machine.peek)
        elif variable.rom is None:
            first = variable.address
        if first is None:
            return {"first": None, "last": None}
        return {"first": hex4(first), "last": hex4(min(first + variable.size - 1, 0xFFFF))}

    def _own_variable(self, variable: OwnVariable) -> dict[str, Any]:
        """One of the owner's variables as a dict, with where it is now."""
        where = self._where_now(variable)
        now = f"{where['first']}-{where['last']}" if where["first"] is not None else None
        return {"label": variable.label, "size": variable.size, "base": variable.base_text,
                "comment": variable.comment, "now": now, "group": variable.group}  # fmt: skip

    # -- groups and contexts (task 10, D-43) -----------------------------------------------

    def group_add(
        self, name: str, description: str | None = None, position: int | None = None
    ) -> dict[str, Any]:
        """Create one of your groups, or change its description or position (D-43).

        Your variables and RAM labels go into groups (`variable_add` / `symbol_add` with
        `group`); a context (`context_set`) tells which group code works with, so a RAM
        address shows the name of that group there. The source lists the names by group.

        Args:
            name: An identifier (not "firmware" / "amsdos": the built-in groups).
            description: What it holds (the heading of its section in the source); None keeps
                the earlier one.
            position: Its index in the list (the order of the ranking and of the sections);
                None keeps its place, a new group goes last.
        """
        try:
            self.symbols.add_group(name, description, position)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return self.group_list()

    def group_remove(self, name: str, force: bool = False) -> dict[str, Any]:
        """Remove one of your groups; refused while it is used, unless `force`.

        With `force` its variables and labels become ungrouped and its contexts are deleted.
        """
        try:
            self.symbols.remove_group(name, force)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return self.group_list()

    def group_rename(self, old: str, new: str) -> dict[str, Any]:
        """Rename one of your groups everywhere (its variables, labels and contexts)."""
        try:
            self.symbols.rename_group(old, new)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return self.group_list()

    def group_list(self) -> dict[str, Any]:
        """The groups in their order: the built-in ones, then yours with what uses them.

        Returns:
            "groups": each "name", "description", "built_in", and for yours "variables",
            "labels", "contexts" (counts).
        """
        groups: list[dict[str, Any]] = [
            {"name": "firmware", "description": "firmware names and variables", "built_in": True},
            {"name": "amsdos", "description": "AMSDOS variables", "built_in": True},
        ]
        for group in self.symbols.groups():
            usage = self.symbols.group_usage(group.name)
            groups.append({"name": group.name, "description": group.description,
                           "built_in": False, **{k: len(v) for k, v in usage.items()}})  # fmt: skip
        return {"groups": groups}

    def context_set(
        self,
        group: str,
        scope: str | None = None,
        first: int | str | None = None,
        last: int | str | None = None,
    ) -> dict[str, Any]:
        """Tell which group code works with (D-43): a range of a scope, or the whole scope.

        Where that code names a RAM address - an operand, a variable it reads or writes - the
        name of this group wins; the narrowest range wins over a wider one and over the whole
        scope. RAM code copied from a ROM takes its ROM origin's context.

        Args:
            group: One of your groups.
            scope: "lower", "rom0".."rom15" or "" (RAM code); default: what is paged in at
                `first`.
            first: First address of the range; without first and last: the whole scope.
            last: Last address (inclusive).
        """
        start = self._address(first) if first is not None else None
        end = self._address(last) if last is not None else None
        if scope is None:
            if start is None:
                raise SessionError("give a scope for a context of the whole scope")
            scope = self.machine.symbol_scope(start)
        try:
            context = self.symbols.set_context(group, scope, start, end)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"context": context.text, "group": context.group}

    def context_remove(
        self, scope: str, first: int | str | None = None, last: int | str | None = None
    ) -> dict[str, Any]:
        """Remove the context of exactly this place (a range, or the whole scope)."""
        start = self._address(first) if first is not None else None
        end = self._address(last) if last is not None else None
        try:
            removed = self.symbols.remove_context(scope, start, end)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"removed": removed.text, "group": removed.group}

    def context_list(self, scope: str | None = None) -> dict[str, Any]:
        """The contexts of one scope, or all.

        Returns:
            "contexts": each "context" (as in the annotation file), "scope", "first", "last"
            (None for a whole scope), "group".
        """
        return {"contexts": [{"context": c.text, "scope": c.scope,
                              "first": hex4(c.first) if c.first is not None else None,
                              "last": hex4(c.last) if c.last is not None else None,
                              "group": c.group} for c in self.symbols.contexts(scope)]}  # fmt: skip

    # -- lookups ----------------------------------------------------------------------------

    def symbol_list(  # pylint: disable=too-many-arguments  # the filters
        self,
        text: str = "",
        scope: str | None = None,
        source: str | None = None,
        limit: int = 100,
        group: str | None = None,
    ) -> dict[str, Any]:
        """List symbols whose label, comment or block contains `text` (see `symbol_search`).

        Args:
            text: Search text (case-insensitive); empty lists all.
            scope: Only this scope ("", "lower", "romN"), or all.
            source: "firmware" (preloaded jumpblock names) or "user", or both.
            limit: Maximum number of entries (max 1000).
            group: Only this group (D-43; "" ungrouped, "firmware" the firmware names), or all.
        """
        if not 1 <= limit <= 1000:
            raise SessionError("limit must be 1-1000")
        found = self.symbols.symbols(text, scope, source, group)
        entries = [
            {"address": hex4(s.address), "label": s.label, "comment": s.comment,
             "scope": s.scope, "source": s.source, "type": s.type, "before": s.before,
             "after": s.after, "group": s.group}
            for s in found[:limit]
        ]  # fmt: skip
        return {"symbols": entries, "total": len(found)}

    def symbol_search(  # pylint: disable=too-many-arguments  # the Symbols tab's filters
        self,
        text: str = "",
        types: list[str] | None = None,
        scope: str | None = None,
        only_yours: bool = False,
        limit: int = 2000,
        *,
        group: str | None = None,
    ) -> dict[str, Any]:
        """Every name for the Symbols tab: symbols, variables and the analysis's names.

        Args:
            text: Search text (case-insensitive) in label, comment, comment blocks or a
                variable's description.
            types: Only these types (FW, VAR, var, sub, jump, data); None for all. Comments
                without a label (type None) are listed unless a type filter is given.
            scope: Only this scope ("" global, "lower", "romN"); None for all.
            only_yours: Only entries of the owner's (labels, comments, variables).
            limit: Maximum number of entries (max 5000).
            group: Only this group (D-43): one of yours, "firmware", "amsdos", or "" for
                ungrouped; None for all.

        Returns:
            "entries" by address (each "type", "address", "label", "comment", "scope", "group",
            "mark": "yours" / "analysis" / None, for variables "size"), "counts" per type
            (before the type filter) and "total" (after it).
        """
        if not 1 <= limit <= MAX_SEARCH:
            raise SessionError(f"limit must be 1-{MAX_SEARCH}")
        unknown = set(types or ()) - set(SYMBOL_TYPES)
        if unknown:
            raise SessionError(f"unknown types: {', '.join(sorted(unknown))}")
        needle = text.lower()
        matching = [entry for entry in self._all_names()
                    if (scope is None or entry["scope"] == scope)
                    and (not only_yours or entry["mark"] == "yours")
                    and (group is None or (entry.get("group") or "") == group)
                    and (not needle or any(needle in (entry.get(key) or "").lower()
                                           for key in ("label", "comment", "before", "after")))
                    ]  # fmt: skip
        counts = dict.fromkeys(SYMBOL_TYPES, 0)
        for entry in matching:
            if entry["type"] in counts:
                counts[entry["type"]] += 1
        if types is not None:
            matching = [entry for entry in matching if entry["type"] in types]
        matching.sort(key=lambda entry: (entry["address"], entry["scope"], entry["label"] or ""))
        return {"entries": matching[:limit], "counts": counts, "total": len(matching)}

    def _all_names(self) -> list[dict[str, Any]]:
        """Every entry `symbol_search` filters (symbols, variables, analysis names)."""
        entries = [
            {"type": s.type, "address": hex4(s.address), "label": s.label, "comment": s.comment,
             "scope": s.scope, "mark": "yours" if s.source == "user" else None,
             "before": s.before, "after": s.after, "group": s.group}
            for s in self.symbols.symbols()
        ]  # fmt: skip
        if self.variables is not None:
            for variable in self.variables.variables(self.machine.peek):
                own = variable.owner == "owner"
                entries.append({"type": "var" if own else "VAR", "address": hex4(variable.address),
                                "label": variable.label, "comment": variable.description,
                                "scope": GLOBAL, "mark": "yours" if own else None,
                                "size": variable.size, "group": variable.group})  # fmt: skip
        seen: set[tuple[int, str]] = set()
        for names in self.analysis_names.values():
            for address, scope, label, name_type in names:
                owner = self.symbols.owner_at(address, scope)
                if (address, scope) in seen or (owner is not None and owner.label):
                    continue  # the owner renamed it: it is the owner's now
                seen.add((address, scope))
                entries.append({"type": name_type, "address": hex4(address), "label": label,
                                "comment": None, "scope": scope, "mark": "analysis",
                                "group": None})  # fmt: skip
        return entries

    def symbol_detail(
        self,
        address: int | str,
        scope: str = GLOBAL,
        label: str | None = None,
        *,
        group: str | None = None,
    ) -> dict[str, Any]:
        """Everything about one name of `symbol_search` (the Symbols tab's detail panel).

        Args:
            address: Its address.
            scope: Its scope.
            label: Its label, to find a variable (variables can share an address) or one of
                several RAM labels at the address.
            group: Which RAM label, where several are at the address (D-44; "" ungrouped).

        Returns:
            "address", "scope"; "firmware" (address, label, section, name, note), "machines" it
            exists on; "yours" (the owner's label, comment, type, comment blocks "before",
            "after" and "group" in this scope); "variable"
            (a known or owner's variable of that label: size, description, owner, and for the
            owner's "base" and "now"); "analysis" (the source generator's name and type); "code"
            (the instruction at the address, names as in the disassembly).
        """
        value = self._address(address)
        model = getattr(self.symbols, "firmware_model", None) or "464"
        firmware = next((e for e in FIRMWARE_BY_MODEL.get(model, ()) if e.address == value), None)
        owner = self.symbols.owner_at(value, scope)
        if scope == GLOBAL:  # several RAM labels at the address (D-44): the one meant
            candidates = self.symbols.owners_at(value)
            if group is not None:
                owner = next((s for s in candidates if (s.group or "") == group), None)
            elif label and any(s.label == label for s in candidates):
                owner = next(s for s in candidates if s.label == label)
        variable = self._variable_named(label) if label else None
        machines: list[str] = []
        if firmware is not None:
            labels = {m: {e.label for e in FIRMWARE_BY_MODEL[m]} for m in MODELS}
            machines = [m for m in MODELS if firmware.label in labels[m]]
        if variable is not None:
            machines = self._variable_machines(variable)
        return {
            "address": hex4(value), "scope": scope, "machines": machines,
            "firmware": firmware._asdict() if firmware is not None else None,
            "yours": ({"label": owner.label, "comment": owner.comment, "type": owner.type,
                       "before": owner.before, "after": owner.after, "group": owner.group}
                      if owner is not None and owner.scope == scope else None),
            "variable": self._variable_detail(variable) if variable is not None else None,
            "analysis": self._analysis_name(value, scope),
            "code": self._code_lines(self.machine.peek, value, 1)[0],
        }  # fmt: skip

    def _analysis_name(self, address: int, scope: str) -> dict[str, str] | None:
        """The source generator's name at (scope, address) with its type, if it gave one."""
        for names in self.analysis_names.values():
            for name_address, name_scope, name, name_type in names:
                if (name_address, name_scope) == (address, scope):
                    return {"label": name, "type": name_type}
        return None

    def _variable_detail(self, variable: Variable) -> dict[str, Any]:
        """A variable for `symbol_detail`; the owner's with its base and where it is now."""
        detail = variable.describe()
        own = self.symbols.variable(variable.label) if variable.owner == "owner" else None
        if own is not None:
            detail |= self._own_variable(own)
        return detail

    @staticmethod
    def _variable_machines(variable: Variable) -> list[str]:
        """The machines a variable exists on: all for AMSDOS's and the owner's."""
        if variable.owner != "firmware":
            return list(MODELS)
        return [m for m in MODELS
                if any(e.label == variable.label for e in OS_VARIABLES_BY_MODEL[m])]  # fmt: skip

    def address_explain(
        self, address: int | str, code_address: int | str | None = None
    ) -> dict[str, Any]:
        """Tell what an address is: its symbol, and every name it has, the winner first.

        Variables (FR-DBG-11) are the firmware's of this machine (464/664/6128), AMSDOS's
        when it is installed (its workspace where AMSDOS put it) and the owner's. With groups
        (D-43) one RAM address can have several labels and variables; `names` lists them all
        with their group, ranked as code sees them: the firmware name first, then the group
        of the context of `code_address` (if given), built-in and ungrouped, other groups.

        Args:
            address: The address.
            code_address: Code that uses the address (its context decides; default: none).

        Returns:
            "address", "label" (the winner's label), "context" (the code's group or None),
            "names" (text, type, group each, the winner first), "variables" (the variables
            containing it, ranked, with their group).
        """
        value = self._address(address)
        context = (self._code_context(self._address(code_address))
                   if code_address is not None else None)  # fmt: skip
        result: dict[str, Any] = {"address": hex4(value), "label": self._label(value, context),
                                  "context": context, "variables": []}  # fmt: skip
        names = ranked_names(self.symbols, self._known_variable_list(), value,
                             self.machine.symbol_scope(value), context)  # fmt: skip
        result["names"] = [{"name": n.text, "type": n.type, "group": n.group} for n in names]
        if self.variables is not None:
            result["model"] = self.variables.model
            result["variables"] = [{"name": hit.name, **hit.variable.describe()}
                                   for hit in self._variable_hits(value, context)]  # fmt: skip
        return result

    def variable_list(
        self, text: str = "", limit: int = 100, group: str | None = None
    ) -> dict[str, Any]:
        """List the known variables (FR-DBG-11) whose label or description has `text`.

        The firmware's and AMSDOS's (when installed), and the owner's (owner "owner").

        Args:
            text: Search text (case-insensitive); empty lists all.
            limit: Maximum number of entries (max 1000).
            group: Only this group (D-43): "firmware", "amsdos", one of yours, or "" for your
                ungrouped ones; None for all.
        """
        if not 1 <= limit <= 1000:
            raise SessionError("limit must be 1-1000")
        if self.variables is None:
            return {"variables": [], "total": 0}
        wanted = text.lower()
        found = [v for v in self.variables.variables(self.machine.peek)
                 if (wanted in v.label.lower() or wanted in v.description.lower())
                 and (group is None or (v.group or "") == group)]  # fmt: skip
        return {"model": self.variables.model, "variables": [v.describe() for v in found[:limit]],
                "total": len(found)}  # fmt: skip

    # -- the annotation file ----------------------------------------------------------------

    def symbols_load(self, path: str = str(ANNOTATIONS_FILE)) -> dict[str, Any]:
        """Merge an annotation file (default project/annotations.txt) into the symbols."""
        file = self._path(path)
        if not file.is_file():
            raise SessionError(f"file not found: {file}")
        try:
            count = self.symbols.load(file)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"loaded": count, "path": str(file)}

    def symbols_save(self, path: str = str(ANNOTATIONS_FILE)) -> dict[str, Any]:
        """Save your symbols and variables to an annotation file (project/annotations.txt)."""
        file = self._path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        return {"saved": self.symbols.save(file), "path": str(file)}

    def symbols_status(self, path: str = str(ANNOTATIONS_FILE)) -> dict[str, Any]:
        """The annotation file: its path, whether it exists, your entries, unsaved changes.

        Returns:
            "path", "exists", "symbols", "variables", "operands", "groups" and "contexts" (your
            entries now: labels and comments, variables, operand overrides, groups, contexts),
            "operand_overrides" (address, scope, role, comment each), "group_names" (your
            groups in their order), "context_list" (as in the file), "warnings" (from loading:
            undefined groups, labels left out at firmware names' addresses), "modified"
            (changed since the last load or save).
        """
        file = self._path(path)
        operands = self.symbols.operand_overrides()
        contexts = self.symbols.contexts()
        return {"path": str(file), "exists": file.is_file(),
                "symbols": len(self.symbols.owner_symbols()),
                "variables": len(self.symbols.own_variables()), "operands": len(operands),
                "groups": len(self.symbols.groups()), "contexts": len(contexts),
                "operand_overrides": [{"address": hex4(o.address), "scope": o.scope,
                                       "role": o.role, "comment": o.comment} for o in operands],
                "group_names": [g.name for g in self.symbols.groups()],
                "context_list": [f"{c.text} {c.group}" for c in contexts],
                "warnings": list(self.symbols.warnings),
                "modified": self.symbols.modified, "types": list(OWNER_TYPES)}  # fmt: skip
