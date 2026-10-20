"""MCP server: every `Session` method as an MCP tool (design.md §6.16; D-27, D-28, D-29).

Tools are generated from the public methods of `Session`: same name, same parameters (with
their type hints, so the MCP client sees a JSON schema), the method docstring as description.
Each tool runs its method through a `SessionClient` command queue (drained by a worker thread
headless, by the GUI thread between frames in GUI mode), so the server stays responsive and
`pause` can interrupt a running `run`. Feature parity with the session (FR-MCP-02) therefore
holds by construction; tests/mcp_server checks it anyway. Results with PNG data become MCP
image content.

Transports: stdio for headless mode (`cpcxray mcp`, started by Claude Code from .mcp.json);
streamable HTTP on 127.0.0.1 in GUI mode (`cpcxray gui --mcp-port 8765`), so Claude can attach
to the instance the owner is using.

Requires the optional dependency `mcp` (`pip install -e ".[mcp]"`).
"""

from __future__ import annotations

import functools
import inspect
import json
import threading
import typing
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

import anyio.to_thread
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from cpcxray import __version__
from cpcxray.machine.flat import Machine
from cpcxray.session.commands import SessionClient, SessionWorker
from cpcxray.session.session import Session, SessionError

SERVER_NAME = "cpcxray"
INSTRUCTIONS = """\
CPC X-Ray: an Amstrad CPC 464 emulator for reverse engineering. The server starts with the CPC
464 of config/machine.toml (or a bare Z80 test machine). Typical use: run (e.g. max_us=3000000
to boot), screen_text to read the screen, step / step_over / run_until and inspect with
disassemble, memory_read, registers_get and devices_get. load_config / rom_load change ROMs.
Addresses can be ints, hex strings ("BB5A", "&BB5A", "#BB5A") or labels; results use 4-digit
hex strings. Run tools are bounded (instruction/time limits, wall-clock timeout) and report
why they stopped. File paths are relative to the project directory."""


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


def make_tool(client: SessionClient, name: str) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Create the async tool function for the session method `name`."""
    method = getattr(Session, name)

    async def tool(**arguments: Any) -> Any:
        """Run the session method in a worker thread (through the command queue).

        A `SessionError` (bad argument, missing file, ...) becomes a `ToolError`, so the
        client sees its message; any other exception is reported as an internal error.
        """
        try:
            result = await anyio.to_thread.run_sync(
                functools.partial(client.call, name, **arguments)
            )
        except SessionError as error:
            raise ToolError(str(error)) from error
        return to_mcp_content(result)

    tool.__name__ = name
    tool.__doc__ = inspect.getdoc(method)
    tool.__signature__ = _tool_signature(method)  # type: ignore[attr-defined]
    return tool


def to_mcp_content(result: Any) -> Any:
    """Convert a session result for MCP: a "png" entry becomes image content (FR-MCP-05).

    Other results are returned unchanged (the SDK serialises them as JSON text).
    """
    if isinstance(result, dict) and isinstance(result.get("png"), bytes):
        details = {key: value for key, value in result.items() if key != "png"}
        return [Image(data=result["png"], format="png"), json.dumps(details)]
    return result


def build_server(client: SessionClient) -> MCPServer:
    """Create the MCP server with one tool per session method, executed through `client`."""
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS, version=__version__)
    for name in session_methods():
        server.add_tool(
            make_tool(client, name),
            name=name,
            description=inspect.getdoc(getattr(Session, name)),
            structured_output=False,
        )
    return server


def run_stdio(project_dir: str | Path = ".", machine: Machine | None = None) -> None:
    """Run the headless MCP server on stdin/stdout until the client disconnects.

    Args:
        project_dir: Base directory for relative paths.
        machine: The machine to drive (default: the flat Z80 test machine).
    """
    worker = SessionWorker(Session(machine, project_dir=project_dir))
    try:
        build_server(worker).run("stdio")
    finally:
        worker.close()


def serve_http_in_background(
    client: SessionClient, port: int, host: str = "127.0.0.1"
) -> threading.Thread:
    """Serve MCP over streamable HTTP in a daemon thread (GUI mode, D-28).

    The tools run through `client`, whose queue the GUI thread drains between frames (D-29).
    Only localhost is offered: the server can load files and run code.

    Returns:
        The server thread (it ends with the process).
    """
    server = build_server(client)
    thread = threading.Thread(
        target=lambda: server.run("streamable-http", host=host, port=port),
        name="cpcxray-mcp-http",
        daemon=True,
    )
    thread.start()
    return thread
