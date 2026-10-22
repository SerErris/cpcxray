"""Session methods for the Memory tab's "Follow CPU" (task 5c).

A mixin of `Session` (every public method is an MCP tool, D-27):

* `memory_follow(True)` registers a write observer (debugger/memory_writes.py) that notes the
  memory written from then on; it is cleared when a run or step starts after a stop, so it
  always holds what was written "since the last stop" - the GUI's free run in time slices
  counts as one run until it stops. `memory_follow(False)` takes it off the bus (D-12).
* `memory_changes_get` returns that, and what the last step read and wrote (from the trace:
  stepped instructions carry their accesses, session/trace_view.py).
"""

from __future__ import annotations

from typing import Any

from cpcxray.debugger.memory_writes import MemoryWrites
from cpcxray.debugger.trace import Capture
from cpcxray.session.errors import SessionError
from cpcxray.session.trace_view import TraceViewMixin

MAX_WRITTEN = 4096  # addresses listed by memory_changes_get


class MemoryFollowMixin(TraceViewMixin):  # pylint: disable=abstract-method  # Session implements the stubs
    """`memory_follow` and `memory_changes_get` of `Session` (see the module docstring)."""

    def _setup_memory_follow(self) -> None:
        """Create the (unregistered) write observer."""
        self.memory_writes = MemoryWrites()
        self._following_memory = False
        self._free_run_stops = 0  # the GUI's free run ended (Pause): a stop for "since"
        self._writes_since: Any = None  # the stop the collection started after

    def memory_follow(self, enabled: bool = True) -> dict[str, Any]:
        """Note memory writes from now on ("Follow CPU" of the Memory tab), or stop noting them.

        The writes are what `memory_changes_get` reports as written since the last stop.
        """
        bus = self._observable_bus()
        if bus is None:
            raise SessionError("following memory writes needs a CPC machine (observable bus)")
        if enabled and not self._following_memory:
            self.memory_writes.clear()
            self._writes_since = self._stop_marker()
            bus.add_observer(self.memory_writes)
        elif not enabled and self._following_memory:
            bus.remove_observer(self.memory_writes)
        self._following_memory = enabled
        return {"following": enabled}

    def _stop_marker(self) -> tuple[int, int]:
        """Changes with every stop: a run's or step's result, or the GUI pausing its run."""
        return id(self.last_stop), self._free_run_stops

    def _free_run_stopped(self) -> None:
        """The GUI's free run (in time slices) ended - Pause, Step: it counts as a stop."""
        self._free_run_stops += 1

    def _run_starting(self) -> None:
        """A run or step starts: after a stop, the writes "since the last stop" start anew."""
        if self._following_memory and self._stop_marker() != self._writes_since:
            self.memory_writes.clear()
            self._writes_since = self._stop_marker()

    def memory_changes_get(self) -> dict[str, Any]:
        """What the last step accessed, and the memory written since the last stop.

        Returns:
            "following" (memory_follow is on), "last_step" - for a stop by a step: "pc" and its
            "accesses" (read / write, address, value, variable; its own operand bytes left
            out), else None - "written": the addresses written since the last stop with their
            values (at most 4096, while following), "written_count" and "last_written" (the
            latest write, address and value).
        """
        result: dict[str, Any] = {
            "following": self._following_memory, "last_step": None, "written": [],
            "written_count": 0, "last_written": None,
        }  # fmt: skip
        stop = self.last_stop or {}
        captures = list(self.trace.records)[-1:]
        if "changed" in stop and captures and isinstance(captures[0], Capture):
            record = self._trace_record(captures[0])
            if "accesses" in record:
                result["last_step"] = {"pc": record["address"], "accesses": record["accesses"]}
        if self._following_memory:
            written = self.memory_writes.written
            result["written"] = [{"address": f"{a:04X}", "value": f"{v:02X}"}
                                 for a, v in sorted(written.items())[:MAX_WRITTEN]]  # fmt: skip
            result["written_count"] = len(written)
            last = self.memory_writes.last
            if last is not None:
                result["last_written"] = {"address": f"{last[0]:04X}", "value": f"{last[1]:02X}"}
        return result
