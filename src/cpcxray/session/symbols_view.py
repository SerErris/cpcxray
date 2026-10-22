"""Session methods for symbols and variables (FR-DBG-09, FR-DBG-11; task 5g, the Symbols tab).

A mixin of `Session` (every public method is an MCP tool, D-27):

* the owner's labels and comments (`symbol_add` with a type "sub" / "jump" / "data",
  `symbol_remove`), and the owner's variables (`variable_add`, `variable_remove`): a named
  byte range at an absolute address or in a background ROM's data area; they behave like the
  known OS/AMSDOS variables everywhere (debugger/variables.py);
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


class SymbolsMixin(DebuggerMixin):  # pylint: disable=abstract-method  # Session implements the stubs
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
    ) -> dict[str, Any]:
        """Add or change a label and/or comment at `address`.

        Args:
            address: The address.
            label: Identifier, unique within the scope, the global symbols and the variables.
            comment: Free text.
            scope: "" for global (RAM, firmware), "lower" (OS ROM) or "rom0".."rom15".
            type: The label's type: "sub" (subroutine, the default), "jump" (a jump target
                inside code) or "data" (a table, a string).
        """
        try:
            symbol = self.symbols.add(self._address(address), label, comment, scope, type)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"address": hex4(symbol.address), "label": symbol.label,
                "comment": symbol.comment, "scope": symbol.scope, "type": symbol.type}  # fmt: skip

    def comment_add(self, address: int | str, comment: str, scope: str = GLOBAL) -> dict[str, Any]:
        """Set the comment at `address` (keeps an existing label)."""
        return self.symbol_add(address, comment=comment, scope=scope)

    def symbol_remove(self, address: int | str, scope: str = GLOBAL) -> dict[str, Any]:
        """Remove the owner's symbol at `address` in `scope` (a firmware name stays)."""
        try:
            removed = self.symbols.remove(self._address(address), scope)
        except SymbolError as error:
            raise SessionError(str(error)) from None
        return {"removed": removed.label or hex4(removed.address), "scope": removed.scope}

    # -- the owner's variables --------------------------------------------------------------

    def variable_add(  # pylint: disable=too-many-arguments  # the parts of a variable
        self,
        label: str,
        size: int,
        base: int | str,
        comment: str | None = None,
        replaces: str | None = None,
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

        Returns:
            The variable with "now" - where it is at the moment ("A612-A621"), or None while
            its ROM has no data area.
        """
        try:
            rom, address = (None, base) if isinstance(base, int) else parse_variable_base(base)
            variable = OwnVariable(label, size, address, rom, comment or None)
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
                "comment": variable.comment, "now": now}  # fmt: skip

    # -- lookups ----------------------------------------------------------------------------

    def symbol_list(
        self, text: str = "", scope: str | None = None, source: str | None = None, limit: int = 100
    ) -> dict[str, Any]:
        """List symbols whose label or comment contains `text` (see also `symbol_search`).

        Args:
            text: Search text (case-insensitive); empty lists all.
            scope: Only this scope ("", "lower", "romN"), or all.
            source: "firmware" (preloaded jumpblock names) or "user", or both.
            limit: Maximum number of entries (max 1000).
        """
        if not 1 <= limit <= 1000:
            raise SessionError("limit must be 1-1000")
        found = self.symbols.symbols(text, scope, source)
        entries = [
            {"address": hex4(s.address), "label": s.label, "comment": s.comment,
             "scope": s.scope, "source": s.source, "type": s.type}
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
    ) -> dict[str, Any]:
        """Every name for the Symbols tab: symbols, variables and the analysis's names.

        Args:
            text: Search text (case-insensitive) in label, comment or a variable's description.
            types: Only these types (FW, VAR, var, sub, jump, data); None for all. Comments
                without a label (type None) are listed unless a type filter is given.
            scope: Only this scope ("" global, "lower", "romN"); None for all.
            only_yours: Only entries of the owner's (labels, comments, variables).
            limit: Maximum number of entries (max 5000).

        Returns:
            "entries" by address (each "type", "address", "label", "comment", "scope", "mark":
            "yours" / "analysis" / None, for variables "size"), "counts" per type (before the
            type filter) and "total" (after it).
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
                    and (not needle or any(needle in (entry.get(key) or "").lower()
                                           for key in ("label", "comment")))]  # fmt: skip
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
             "scope": s.scope, "mark": "yours" if s.source == "user" else None}
            for s in self.symbols.symbols()
        ]  # fmt: skip
        if self.variables is not None:
            for variable in self.variables.variables(self.machine.peek):
                own = variable.owner == "owner"
                entries.append({"type": "var" if own else "VAR", "address": hex4(variable.address),
                                "label": variable.label, "comment": variable.description,
                                "scope": GLOBAL, "mark": "yours" if own else None,
                                "size": variable.size})  # fmt: skip
        seen: set[tuple[int, str]] = set()
        for names in self.analysis_names.values():
            for address, scope, label, name_type in names:
                owner = self.symbols.owner_at(address, scope)
                if (address, scope) in seen or (owner is not None and owner.label):
                    continue  # the owner renamed it: it is the owner's now
                seen.add((address, scope))
                entries.append({"type": name_type, "address": hex4(address), "label": label,
                                "comment": None, "scope": scope, "mark": "analysis"})  # fmt: skip
        return entries

    def symbol_detail(
        self, address: int | str, scope: str = GLOBAL, label: str | None = None
    ) -> dict[str, Any]:
        """Everything about one name of `symbol_search` (the Symbols tab's detail panel).

        Args:
            address: Its address.
            scope: Its scope.
            label: Its label, to find a variable (variables can share an address).

        Returns:
            "address", "scope"; "firmware" (address, label, section, name, note), "machines" it
            exists on; "yours" (the owner's label, comment, type in this scope); "variable"
            (a known or owner's variable of that label: size, description, owner, and for the
            owner's "base" and "now"); "analysis" (the source generator's name and type); "code"
            (the instruction at the address, names as in the disassembly).
        """
        value = self._address(address)
        model = getattr(self.symbols, "firmware_model", None) or "464"
        firmware = next((e for e in FIRMWARE_BY_MODEL.get(model, ()) if e.address == value), None)
        owner = self.symbols.owner_at(value, scope)
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
            "yours": ({"label": owner.label, "comment": owner.comment, "type": owner.type}
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

    def address_explain(self, address: int | str) -> dict[str, Any]:
        """Tell what an address is: its symbol and the known OS/AMSDOS variables containing it.

        Variables (FR-DBG-11) are the firmware's of this machine (464/664/6128), AMSDOS's
        when it is installed (its workspace where AMSDOS put it) and the owner's; the smallest
        one comes first, named `LABEL` or `LABEL+offset`.
        """
        value = self._address(address)
        result: dict[str, Any] = {"address": hex4(value), "label": self._label(value),
                                  "variables": []}  # fmt: skip
        if self.variables is not None:
            result["model"] = self.variables.model
            result["variables"] = [{"name": hit.name, **hit.variable.describe()}
                                   for hit in self._variable_hits(value)]  # fmt: skip
        return result

    def variable_list(self, text: str = "", limit: int = 100) -> dict[str, Any]:
        """List the known variables (FR-DBG-11) whose label or description has `text`.

        The firmware's and AMSDOS's (when installed), and the owner's (owner "owner").

        Args:
            text: Search text (case-insensitive); empty lists all.
            limit: Maximum number of entries (max 1000).
        """
        if not 1 <= limit <= 1000:
            raise SessionError("limit must be 1-1000")
        if self.variables is None:
            return {"variables": [], "total": 0}
        wanted = text.lower()
        found = [v for v in self.variables.variables(self.machine.peek)
                 if wanted in v.label.lower() or wanted in v.description.lower()]  # fmt: skip
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
            "path", "exists", "symbols" and "variables" (your entries now), "modified"
            (changed since the last load or save).
        """
        file = self._path(path)
        return {"path": str(file), "exists": file.is_file(),
                "symbols": len(self.symbols.owner_symbols()),
                "variables": len(self.symbols.own_variables()),
                "modified": self.symbols.modified, "types": list(OWNER_TYPES)}  # fmt: skip
