"""Tests of the MCP server (design.md §6.16, §8; FR-MCP-01..05, D-27..D-29).

* Parity: every public `Session` method is an MCP tool with a description (FR-MCP-02).
* Tool groups through the in-process MCP client: machine, execution, CPU, memory, disassembly,
  breakpoints, trace, symbols; errors arrive as tool errors with the session's message.
* `pause` interrupts a running `run` (D-29).
* Phase 3 milestone: the real `cpcxray mcp` process over stdio loads a binary, steps and
  disassembles.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from cpcxray.mcp_server.server import build_server, session_methods
from cpcxray.session.commands import SessionWorker
from cpcxray.session.session import Session

PROGRAM = bytes.fromhex("3E05 CD0A80 3C 76 000000 0603 10FE C9")  # as in tests/session

type ToolCall = Callable[..., Awaitable[Any]]


@pytest.fixture(name="worker")
def fixture_worker(tmp_path: Path) -> Iterator[SessionWorker]:
    """A session worker on a fresh session whose project directory holds prog.bin."""
    (tmp_path / "prog.bin").write_bytes(PROGRAM)
    worker = SessionWorker(Session(project_dir=tmp_path))
    yield worker
    worker.close()


def with_client(worker: SessionWorker, body: Callable[[ToolCall], Awaitable[None]]) -> None:
    """Run `body(call)` with an in-process MCP client.

    `call(name, **args)` returns the decoded JSON result of a tool, or raises AssertionError
    carrying the error text for failed calls.
    """

    async def main() -> None:
        """Connect the client and run the test body."""
        async with Client(build_server(worker)) as client:

            async def call(name: str, **arguments: Any) -> Any:
                """Call a tool and decode its JSON result."""
                result = await client.call_tool(name, arguments)
                text = result.content[0].text if result.content else ""
                if result.is_error:
                    raise AssertionError(text)
                return json.loads(text)

            await body(call)

    anyio.run(main)


def test_parity_every_session_method_is_a_tool(worker: SessionWorker) -> None:
    """The tool list equals the public Session methods, each with a description (FR-MCP-02)."""

    async def main() -> None:
        """List the tools of the server."""
        async with Client(build_server(worker)) as client:
            tools = (await client.list_tools()).tools
        assert sorted(tool.name for tool in tools) == session_methods()
        assert all(tool.description for tool in tools)
        assert "run_until" in session_methods() and "pause" in session_methods()

    anyio.run(main)


def test_machine_memory_execution_and_disassembly(worker: SessionWorker) -> None:
    """Load a binary, set the PC, step, step over, run and disassemble via MCP tools."""

    async def body(call: ToolCall) -> None:
        """The tool sequence."""
        assert (await call("memory_load", address="8000", path="prog.bin"))["length"] == 15
        await call("registers_set", values={"pc": 0x8000, "sp": 0xF000})
        lines = (await call("disassemble", address="8000", count=2))["lines"]
        assert " ".join(lines[1].split()) == "8002 CD 0A 80 call #800A"
        assert (await call("step"))["pc"] == "8002"
        assert (await call("step_over"))["pc"] == "8005"
        assert (await call("run", timeout_s=5))["reason"] == "halted"
        assert (await call("registers_get"))["a"] == 6
        assert (await call("memory_search", pattern="10FE"))["matches"] == ["800C"]
        assert (await call("machine_status"))["model"] == "flat-z80"

    with_client(worker, body)


def test_breakpoints_and_trace(worker: SessionWorker) -> None:
    """Breakpoints stop a run; the trace records the executed instructions."""

    async def body(call: ToolCall) -> None:
        """The tool sequence."""
        await call("memory_write", address="8000", data=PROGRAM.hex())
        await call("registers_set", values={"pc": 0x8000, "sp": 0xF000})
        added = await call("breakpoint_add", address="800C")
        await call("trace_start")
        assert (await call("run"))["reason"] == "breakpoint"
        assert (await call("breakpoint_list"))["breakpoints"][0]["hits"] == 1
        assert len((await call("trace_read", last=10))["lines"]) == 3
        await call("breakpoint_remove", breakpoint_id=added["id"])
        assert (await call("trace_stop"))["records"] >= 3

    with_client(worker, body)


def test_symbols(worker: SessionWorker) -> None:
    """Symbols can be added, used as addresses, listed, saved and removed."""

    async def body(call: ToolCall) -> None:
        """The tool sequence."""
        await call("symbol_add", address="8000", label="start", comment="entry")
        await call("comment_add", address="8002", comment="calls the loop")
        assert (await call("memory_read", address="start", length=2))["hex"] == "0000"
        assert (await call("symbol_list", text="start", source="user"))["symbols"][0][
            "label"
        ] == "start"
        assert (await call("symbols_save"))["saved"] == 2
        assert (await call("symbols_load"))["loaded"] == 2
        assert (await call("symbol_remove", address="start"))["removed"] == "start"
        found = (await call("symbol_list", text="txt_output", source="firmware"))["symbols"]
        assert found[0]["address"] == "BB5A"

    with_client(worker, body)


def test_errors_carry_the_session_message(worker: SessionWorker) -> None:
    """A failing tool call is an MCP tool error with the session's explanation."""

    async def body(call: ToolCall) -> None:
        """Calls that must fail."""
        with pytest.raises(AssertionError, match="neither a hex address nor a known label"):
            await call("memory_read", address="nowhere")
        with pytest.raises(AssertionError, match="file not found"):
            await call("memory_load", address=0, path="missing.bin")

    with_client(worker, body)


def test_pause_interrupts_run(worker: SessionWorker) -> None:
    """A pause call stops a run that is in progress in the worker (D-29)."""

    async def body(call: ToolCall) -> None:
        """Start an endless run, pause it from a second call."""
        await call("memory_write", address="8000", data="18FE")  # JR $
        await call("registers_set", values={"pc": 0x8000})
        results: list[Any] = []

        async def run() -> None:
            """The long run."""
            results.append(await call("run", timeout_s=30))

        async with anyio.create_task_group() as group:
            group.start_soon(run)
            await anyio.sleep(0.5)
            await call("pause")
        assert results[0]["reason"] == "paused"

    with_client(worker, body)


def test_milestone_cpcxray_mcp_over_stdio(tmp_path: Path) -> None:
    """Phase 3 milestone: the real `cpcxray mcp` process loads a binary, steps, disassembles."""
    (tmp_path / "prog.bin").write_bytes(PROGRAM)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cpcxray.cli", "mcp", "--project-dir", str(tmp_path), "--machine", "flat"],
    )

    async def main() -> None:
        """Drive the server process like Claude Code would."""
        async with Client(parameters) as client:

            async def call(name: str, **arguments: Any) -> Any:
                """Call a tool and decode its JSON result."""
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result.content
                return json.loads(result.content[0].text)

            await call("memory_load", address="8000", path="prog.bin")
            await call("registers_set", values={"pc": 0x8000, "sp": 0xF000})
            assert (await call("step", count=2))["pc"] == "800A"
            lines = (await call("disassemble", count=3))["lines"]
            assert " ".join(lines[0].split()) == "800A 06 03 ld b,#03"

    anyio.run(main)


def test_screen_image_is_image_content(tmp_path: Path) -> None:
    """screen_image returns MCP image content (PNG) plus its details as text."""
    from cpcxray.machine.cpc464 import CPC464  # pylint: disable=import-outside-toplevel

    worker = SessionWorker(Session(CPC464(), project_dir=tmp_path))

    async def main() -> None:
        """Request a screen image."""
        async with Client(build_server(worker)) as client:
            result = await client.call_tool("screen_image", {"path": "shot.png"})
        assert not result.is_error
        image, details = result.content
        assert (image.type, image.mime_type) == ("image", "image/png")
        assert json.loads(details.text)["width"] == 768
        assert (tmp_path / "shot.png").read_bytes()[:4] == b"\x89PNG"

    try:
        anyio.run(main)
    finally:
        worker.close()


def test_http_transport(tmp_path: Path) -> None:
    """GUI mode: tools over streamable HTTP, executed by the thread that drains the queue."""
    import socket  # pylint: disable=import-outside-toplevel
    import threading  # pylint: disable=import-outside-toplevel

    from cpcxray.mcp_server.server import (  # pylint: disable=import-outside-toplevel
        serve_http_in_background,
    )
    from cpcxray.session.commands import SessionClient  # pylint: disable=import-outside-toplevel

    with socket.socket() as probe:  # find a free port
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    client = SessionClient(Session(project_dir=tmp_path))
    serve_http_in_background(client, port)
    stop = threading.Event()

    def drain() -> None:
        """Stand-in for the GUI thread: execute queued commands between 'frames'."""
        while not stop.is_set():
            client.queue.run_pending()
            stop.wait(0.01)

    threading.Thread(target=drain, daemon=True).start()

    async def main() -> None:
        """Connect over HTTP and call tools."""
        for _ in range(50):  # wait for the server to listen
            try:
                async with Client(f"http://127.0.0.1:{port}/mcp") as mcp_client:
                    await mcp_client.call_tool("memory_write", {"address": "8000", "data": "3E05"})
                    result = await mcp_client.call_tool(
                        "memory_read", {"address": "8000", "length": 2}
                    )
                    assert json.loads(result.content[0].text)["hex"] == "3E05"
                    return
            except OSError:
                await anyio.sleep(0.1)
        raise AssertionError("HTTP server did not start")

    try:
        anyio.run(main)
    finally:
        stop.set()


def test_type_text_and_screen_text_over_mcp(tmp_path: Path) -> None:
    """The design's MCP example: type PRINT 1+1 and RETURN, then screen_text contains 2."""
    from cpcxray.machine.cpc464 import CPC464  # pylint: disable=import-outside-toplevel
    from tests.machines import CONFIG_464  # pylint: disable=import-outside-toplevel

    config = CONFIG_464
    if config.lower_rom is None or not config.lower_rom.exists:
        pytest.skip("ROMs missing")
    worker = SessionWorker(Session(CPC464(config), project_dir=tmp_path))

    async def main() -> None:
        """Boot, type, read the screen."""
        async with Client(build_server(worker)) as client:
            await client.call_tool("run", {"max_us": 3_000_000, "timeout_s": 60})
            typed = await client.call_tool("type_text", {"text": "PRINT 1+1\r"})
            assert json.loads(typed.content[0].text)["reason"] == "typed"
            screen = await client.call_tool("screen_text", {})
            assert " 2" in json.loads(screen.content[0].text)["lines"]

    try:
        anyio.run(main)
    finally:
        worker.close()
