"""Session methods for reading the bus trace grouped and explained (task 5f, the Bus trace tab).

A mixin of `Session` (every public method is an MCP tool, D-27). `bus_trace_view` reads a
window of the bus trace's ring buffer - anywhere in its history - grouped per instruction, each
access with its role (the instruction's own fetches and operand bytes, or data) and a meaning:
the known variable it touched, the stack (a return address pushed by a call, the address a
return goes to), or for a port the device and what the value does there (debugger/ports.py).
Adjacent byte accesses of one kind can be combined into words ("W BFF0 = 7F85").

The meanings are worked out here, when reading; recording stays as fast as before.
"""

from __future__ import annotations

from typing import Any

from cpcxray.cpu.bus import Cycle
from cpcxray.cpu.disasm import Flow
from cpcxray.debugger.breakpoints import CYCLE_NAMES
from cpcxray.debugger.ports import PortMeanings
from cpcxray.debugger.stack import STACK_AREA
from cpcxray.session.debugger import DebuggerMixin
from cpcxray.session.errors import SessionError

MAX_BUS_VIEW = 2000  # records per window
CALL_FLOWS = (Flow.CALL, Flow.CONDITIONAL_CALL)
RETURN_FLOWS = (Flow.RETURN, Flow.CONDITIONAL_RETURN)


class BusViewMixin(DebuggerMixin):  # pylint: disable=abstract-method  # Session implements the stubs
    """`bus_trace_view` and `bus_trace_clear` of `Session` (see the module docstring)."""

    def bus_trace_clear(self) -> dict[str, Any]:
        """Empty the bus trace's buffer (recording, if on, goes on with the same filters)."""
        self.bus_trace.records.clear()
        return {"cleared": True, "tracing": self.bus_trace.enabled}

    def bus_trace_view(  # pylint: disable=too-many-arguments  # window, switches, filter
        self,
        top: int | None = None,
        count: int = 300,
        *,
        fetches: bool = False,
        combine: bool = True,
        types: list[str] | None = None,
    ) -> dict[str, Any]:
        """A window of the bus trace grouped per instruction, with roles and meanings.

        Args:
            top: Index of the window's first record in the buffer (0 = the oldest kept);
                None for the newest `count` records.
            count: Records in the window (1-2000).
            fetches: Also list the instructions' own opcode fetches and operand bytes.
            combine: Show adjacent byte reads / writes of one kind as one word access.
            types: Show only these access types ("M1", "read", "write", "in", "out",
                "interrupt") and only the instructions that made one of them; None for all.
                A filter of the view: the trace records everything it was started for.

        Returns:
            "groups" (a gap - a free run that was not recorded - as {"gap": {"instructions",
            "emulated_us"}, "first"}; per instruction: "pc", "first" (its first record's
            index), its text as mnemonic/operands/name/name_type, "where" it ran, "accesses"
            each with "index" in the buffer, "access", "address", "value", "role" fetch /
            operand / data, "meaning", "word"), "top", "count", "total" (records kept) and
            "recording".
        """
        if not 1 <= count <= MAX_BUS_VIEW:
            raise SessionError(f"count must be 1-{MAX_BUS_VIEW}")
        records = list(self.bus_trace.records)
        total = len(records)
        first = max(0, total - count) if top is None else max(0, min(top, total - 1))
        window = records[first : first + count]
        groups = self._bus_groups(window, first)
        meanings = PortMeanings()
        for group in groups:
            if "gap" not in group:
                self._explain(group, meanings, fetches=fetches, combine=combine)
        if types is not None:  # the filter: after explaining, so port meanings keep their state
            unknown = set(types) - set(CYCLE_NAMES.values())
            if unknown:
                raise SessionError(f"unknown access types: {', '.join(sorted(unknown))}")
            for group in groups:
                group["accesses"] = [a for a in group["accesses"] if a["access"] in types]
            groups = [group for group in groups if group["accesses"] or "gap" in group]
        return {"groups": groups, "top": first, "count": len(window), "total": total,
                "recording": self.bus_trace.enabled}  # fmt: skip

    def _bus_groups(self, window: list[tuple[Any, ...]], first: int) -> list[dict[str, Any]]:
        """Split records into instructions: a new one at a fetch of its PC or a new PC."""
        groups: list[dict[str, Any]] = []
        for offset, (_sequence, kind, address, value, pc, location) in enumerate(window):
            if kind is None:  # a gap: a free run that was not recorded
                groups.append({"gap": {"instructions": address, "emulated_us": value},
                               "first": first + offset, "pc": "", "accesses": []})  # fmt: skip
                continue
            starts = kind == Cycle.M1 and address == pc
            if (not groups or "gap" in groups[-1] or groups[-1]["pc"] != f"{pc:04X}"
                    or (starts and groups[-1]["raw"])):  # fmt: skip
                groups.append({"pc": f"{pc:04X}", "where": location, "raw": [],
                               "first": first + offset})  # fmt: skip
            if kind == Cycle.M1 and address == pc:
                groups[-1]["where"] = location
            groups[-1]["raw"].append((first + offset, kind, address, value))
        return groups

    def _explain(
        self, group: dict[str, Any], meanings: PortMeanings, *, fetches: bool, combine: bool
    ) -> None:
        """Fill a group's instruction text and its accesses (roles, meanings, words)."""
        pc = int(group["pc"], 16)
        instruction = self._disassemble(pc)
        row = self._disassembly_rows(instruction, None)[0]
        group |= {key: row[key] for key in ("mnemonic", "operands", "name", "name_type")
                  if key in row}  # fmt: skip
        group["where"] = self._where_text(group["where"])
        accesses = self._accesses_of(group.pop("raw"), pc, instruction.length, meanings,
                                     fetches=fetches)  # fmt: skip
        if combine:
            accesses = _combine_words(accesses)
        _stack_roles(accesses, instruction.flow)
        group["accesses"] = accesses

    def _accesses_of(  # pylint: disable=too-many-arguments  # the instruction and a switch
        self,
        raw: list[tuple[int, Cycle, int, int]],
        pc: int,
        length: int,
        meanings: PortMeanings,
        *,
        fetches: bool,
    ) -> list[dict[str, Any]]:
        """An instruction's accesses with role and meaning (fetches and operands if asked)."""
        accesses = []
        for index, kind, address, value in raw:
            access = CYCLE_NAMES[kind]
            role = _role(kind, address, pc, length)
            if role != "data" and not fetches:
                continue
            meaning = (
                self._access_meaning(access, address, value, meanings) if role == "data" else role
            )
            accesses.append({"index": index, "access": access, "address": f"{address:04X}",
                             "value": f"{value:02X}", "role": role, "meaning": meaning,
                             "word": False})  # fmt: skip
        return accesses

    def _access_meaning(self, access: str, address: int, value: int, meanings: PortMeanings) -> str:
        """A data access in words: a variable, the stack, a port's device, an interrupt."""
        if access in ("in", "out"):
            return meanings.meaning(access, address, value)
        if access == "interrupt":
            return "interrupt acknowledge"
        if hits := self._variable_hits(address):
            return hits[0].name
        if any((top.top - address - 1) & 0xFFFF < STACK_AREA for top in self.stacks.tops):
            return "stack"
        return ""

    def _where_text(self, location: str) -> str:
        """Where an instruction ran: "lower ROM (os6128.rom)", "upper ROM 7 (…)", "RAM block 2"."""
        if location.startswith("ram:"):
            return f"RAM block {location[4:]}"
        if location in ("lower",) or location.startswith("rom"):
            return self._rom_text(location)
        return location


def _role(kind: Cycle, address: int, pc: int, length: int) -> str:
    """An access's role: the instruction's own "fetch" / "operand" bytes, or "data"."""
    if kind == Cycle.M1:
        return "fetch"
    if kind == Cycle.MEM_RD and (address - pc) & 0xFFFF < length:
        return "operand"
    return "data"


def _combine_words(accesses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge adjacent data byte reads / writes of one kind into words (low address, value)."""
    result: list[dict[str, Any]] = []
    index = 0
    while index < len(accesses):
        access = accesses[index]
        following = accesses[index + 1] if index + 1 < len(accesses) else None
        if following is not None and _word_pair(access, following):
            low, high = sorted((access, following), key=lambda a: int(a["address"], 16))
            result.append(low | {"value": high["value"] + low["value"], "word": True,
                                 "meaning": access["meaning"] or following["meaning"]})  # fmt: skip
            index += 2
            continue
        result.append(access)
        index += 1
    return result


def _word_pair(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Two data reads or two data writes of neighbouring bytes: one word access."""
    return (first["role"] == second["role"] == "data" and first["access"] == second["access"]
            and first["access"] in ("read", "write")
            and abs(int(first["address"], 16) - int(second["address"], 16)) == 1)  # fmt: skip


def _stack_roles(accesses: list[dict[str, Any]], flow: Flow) -> None:
    """A call's stack writes are its return address; a return's stack reads say where it goes."""
    for access in accesses:
        if access["meaning"] != "stack" or not access["word"]:
            continue
        if flow in CALL_FLOWS and access["access"] == "write":
            access["meaning"] = "stack: return address"
        elif flow in RETURN_FLOWS and access["access"] == "read":
            access["meaning"] = f"stack: returns to {access['value']}"
