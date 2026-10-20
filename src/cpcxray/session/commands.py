"""Command queue between clients and the session (design.md §6.16, D-13, D-29).

The emulation is single-threaded: only one thread may touch the session and its machine.
Clients in other threads (the MCP server, later the GUI's MCP-over-HTTP) submit commands to a
queue and wait for the result:

* headless mode: `SessionWorker` owns a thread that executes queued commands one by one;
* GUI mode (phase 5): the GUI thread calls `CommandQueue.run_pending()` between emulation
  time slices instead.

`pause` is the one exception: it only sets a flag that the running command checks, so it is
executed immediately in the caller's thread; otherwise it would wait behind the very `run` it
is meant to stop.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

from cpcxray.session.session import Session

IMMEDIATE_METHODS = frozenset({"pause"})  # thread-safe, not queued


class CommandQueue:
    """A queue of callables, executed by whichever thread owns the session."""

    def __init__(self) -> None:
        """Create an empty queue."""
        self._queue: queue.Queue[tuple[Callable[[], Any], Future[Any]] | None] = queue.Queue()

    def submit(self, command: Callable[[], Any]) -> Future[Any]:
        """Queue `command`; the returned future receives its result or exception."""
        future: Future[Any] = Future()
        self._queue.put((command, future))
        return future

    def run_pending(self) -> int:
        """Execute all queued commands now (for a GUI loop); returns how many ran."""
        executed = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                return executed
            if item is not None:
                _execute(*item)
                executed += 1

    def run_until_closed(self) -> None:
        """Execute commands as they arrive until `close()` (for a worker thread)."""
        while (item := self._queue.get()) is not None:
            _execute(*item)

    def close(self) -> None:
        """Make `run_until_closed` return after the commands already queued."""
        self._queue.put(None)


def _execute(command: Callable[[], Any], future: Future[Any]) -> None:
    """Run a command and deliver its result or exception to the future."""
    if not future.set_running_or_notify_cancel():
        return
    try:
        future.set_result(command())
    except Exception as error:  # pylint: disable=broad-exception-caught  # passed to caller
        future.set_exception(error)


class SessionClient:  # pylint: disable=too-few-public-methods  # one job: call methods
    """Calls session methods from any thread through a command queue.

    The commands run in whichever thread drains `queue`: the worker thread of `SessionWorker`
    (headless) or the GUI thread calling `queue.run_pending()` between frames (GUI mode).
    """

    def __init__(self, session: Session, commands: CommandQueue | None = None) -> None:
        """Create a client for `session` using `commands` (a new queue by default)."""
        self.session = session
        self.queue = commands if commands is not None else CommandQueue()

    def call(self, method: str, **arguments: Any) -> Any:
        """Call a public session method with keyword arguments and wait for its result.

        Raises:
            AttributeError: If `method` is not a public session method.
            Exception: Whatever the session method raised.
        """
        if method.startswith("_") or not callable(getattr(self.session, method, None)):
            raise AttributeError(f"no session method {method!r}")
        bound = getattr(self.session, method)
        if method in IMMEDIATE_METHODS:
            return bound(**arguments)
        return self.queue.submit(lambda: bound(**arguments)).result()


class SessionWorker(SessionClient):
    """A `SessionClient` with its own thread that executes the queued commands (headless)."""

    def __init__(self, session: Session) -> None:
        """Start the worker thread for `session`."""
        super().__init__(session)
        self._thread = threading.Thread(
            target=self.queue.run_until_closed, name="cpcxray-session", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        """Stop the worker thread after the queued commands."""
        self.queue.close()
        self._thread.join(timeout=5)
