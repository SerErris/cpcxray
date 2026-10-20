"""Tests of the command queue and the session worker thread (D-13, D-29)."""

from __future__ import annotations

import threading
import time

import pytest

from cpcxray.session.commands import CommandQueue, SessionWorker
from cpcxray.session.session import Session, SessionError


def test_run_pending_executes_in_calling_thread() -> None:
    """A GUI loop executes queued commands with run_pending, in its own thread."""
    commands = CommandQueue()
    futures = [commands.submit(threading.get_ident) for _ in range(3)]
    assert commands.run_pending() == 3
    assert {future.result() for future in futures} == {threading.get_ident()}


def test_exceptions_reach_the_caller() -> None:
    """An exception in a command is raised in the thread waiting for the result."""
    commands = CommandQueue()
    future = commands.submit(lambda: 1 // 0)
    commands.run_pending()
    with pytest.raises(ZeroDivisionError):
        future.result()


def test_worker_runs_session_methods_in_its_thread() -> None:
    """The worker executes session methods in its own thread and returns their results."""
    worker = SessionWorker(Session())
    try:
        assert worker.call("memory_write", address="8000", data="3E05")["length"] == 2
        with pytest.raises(SessionError):
            worker.call("memory_read", address="zz")
        with pytest.raises(AttributeError):
            worker.call("_run_loop")
    finally:
        worker.close()


def test_pause_interrupts_a_running_run() -> None:
    """Pause is executed immediately and stops a long run in the worker thread."""
    session = Session()
    session.memory_write("8000", "18FE")  # JR $: an endless loop
    session.registers_set({"pc": 0x8000})
    worker = SessionWorker(session)
    results: list[dict[str, object]] = []
    runner = threading.Thread(target=lambda: results.append(worker.call("run", timeout_s=30)))
    try:
        runner.start()
        time.sleep(0.3)
        worker.call("pause")
        runner.join(timeout=10)
        assert results and results[0]["reason"] == "paused"
    finally:
        worker.close()
