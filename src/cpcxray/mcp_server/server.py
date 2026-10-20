"""MCP server: every `Session` method as an MCP tool (design.md §6.16; D-27, D-28, D-29).

Tools are generated from the public methods of `Session`: same name, same parameters (with
their type hints, so the MCP client sees a JSON schema), the method docstring as description.
Each tool runs its method through the `SessionWorker` command queue in a worker thread, so the
server stays responsive and `pause` can interrupt a running `run`. Feature parity with the
session (FR-MCP-02) therefore holds by construction; tests/mcp checks it anyway.

Transports: stdio for headless mode (`cpcxray mcp`, started by Claude Code from .mcp.json);
streamable HTTP on 127.0.0.1 for GUI mode follows with the GUI (phase 5).

Requires the optional dependency `mcp` (`pip install -e ".[mcp]"`).
"""

from __future__ import annotations

import functools
import inspect
import typing
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import anyio.to_thread
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from cpcxray import __version__
from cpcxray.session.commands import SessionWorker
from cpcxray.session.session import Session, SessionError

SERVER_NAME = "cpcxray"
INSTRUCTIONS = """\
CPC X-Ray: an Amstrad CPC 464 emulator for reverse engineering (phase 3: a bare Z80 with 64 KB
RAM). Load a binary with memory_load, set the PC with registers_set, then step / step_over /
run / run_until and inspect with disassemble, memory_read and registers_get. Addresses can be
ints, hex strings ("BB5A", "&BB5A", "#BB5A") or labels; results use 4-digit hex strings. Run
tools are bounded (instruction/time limits, wall-clock timeout) and report why they stopped.
File paths are relative to the project directory."""


def session_methods() -> list[str]:
    """Return the names of the public `Session` methods (the tools to offer)."""
    return sorted(
        name
        for name, member in inspect.getmembers(Session, inspect.isfunction)
        if not name.startswith("_")
    )


def _tool_signature(method: Callable[..., Any]) -> inspect.Signature:
    """Return the method's signature without `self`, with annotations resolved to types."""
    hints = typing.get_type_hints(method)
    parameters = [
        parameter.replace(annotation=hints.get(name, Any))
        for name, parameter in inspect.signature(method).parameters.items()
        if name != "self"
    ]
    return inspect.Signature(parameters, return_annotation=hints.get("return", Any))


def make_tool(worker: SessionWorker, name: str) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Create the async tool function for the session method `name`."""
    method = getattr(Session, name)

    async def tool(**arguments: Any) -> Any:
        """Run the session method in a worker thread (through the command queue).

        A `SessionError` (bad argument, missing file, ...) becomes a `ToolError`, so the
        client sees its message; any other exception is reported as an internal error.
        """
        try:
            return await anyio.to_thread.run_sync(functools.partial(worker.call, name, **arguments))
        except SessionError as error:
            raise ToolError(str(error)) from error

    tool.__name__ = name
    tool.__doc__ = inspect.getdoc(method)
    tool.__signature__ = _tool_signature(method)  # type: ignore[attr-defined]
    return tool


def build_server(worker: SessionWorker) -> MCPServer:
    """Create the MCP server with one tool per session method."""
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=__version__)
    for name in session_methods():
        server.add_tool(
            make_tool(worker, name),
            name=name,
            description=inspect.getdoc(getattr(Session, name)),
            structured_output=False,
        )
    return server


def run_stdio(project_dir: str | Path = ".") -> None:
    """Run the headless MCP server on stdin/stdout until the client disconnects."""
    worker = SessionWorker(Session(project_dir=project_dir))
    try:
        build_server(worker).run("stdio")
    finally:
        worker.close()
