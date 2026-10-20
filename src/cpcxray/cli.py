"""Command line entry point `cpcxray` (console script, see pyproject.toml).

Subcommands:

* `config`: load and check a machine configuration.
* `mcp`: the headless MCP server on stdin/stdout (design.md §6.16, D-28); Claude Code starts it
  from the project's .mcp.json. By default it drives a CPC 464 with the ROMs of
  config/machine.toml (`--machine flat` for the bare Z80). Needs the optional dependency `mcp`.

The `gui` subcommand (Dear PyGui frontend) follows in phase 5.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from cpcxray import __version__
from cpcxray.machine.config import ConfigError, MachineConfig, load_config
from cpcxray.machine.cpc464 import build_cpc464

DEFAULT_CONFIG = "config/machine.toml"


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the selected subcommand.

    Args:
        argv: Arguments without the program name; defaults to `sys.argv[1:]`.

    Returns:
        The process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(prog="cpcxray", description=__doc__)
    parser.add_argument("--version", action="version", version=f"cpcxray {__version__}")
    sub = parser.add_subparsers(dest="command")
    p_config = sub.add_parser("config", help="load and check a machine configuration")
    p_config.add_argument("--config", default=DEFAULT_CONFIG, help="path to machine.toml")
    p_mcp = sub.add_parser("mcp", help="run the headless MCP server on stdio")
    p_mcp.add_argument("--project-dir", default=".", help="base directory for relative paths")
    p_mcp.add_argument(
        "--machine", choices=("cpc464", "flat"), default="cpc464",
        help="cpc464 (ROMs from --config) or flat (bare Z80, 64 KB RAM)",
    )  # fmt: skip
    p_mcp.add_argument("--config", default=DEFAULT_CONFIG, help="machine.toml for cpc464")

    args = parser.parse_args(argv)
    if args.command == "config":
        return _cmd_config(args.config)
    if args.command == "mcp":
        return _cmd_mcp(args.project_dir, args.machine, args.config)
    parser.print_help()
    return 0


def _cmd_mcp(project_dir: str, machine_kind: str, config: str) -> int:
    """Run `cpcxray mcp`: serve MCP on stdio until the client disconnects.

    Args:
        project_dir: Base directory for relative paths (also for --config).
        machine_kind: "cpc464" (powered on with the configured ROMs) or "flat".
        config: The machine.toml for the CPC 464.

    Returns:
        0 when the client disconnects, 1 if `mcp` is missing or the config is invalid.
    """
    try:
        from cpcxray.mcp_server.server import run_stdio  # pylint: disable=import-outside-toplevel  # optional dependency
    except ImportError as error:
        print(f"error: {error}; install with: pip install -e '.[mcp]'", file=sys.stderr)
        return 1
    machine = None
    if machine_kind == "cpc464":
        config_path = Path(config) if Path(config).is_absolute() else Path(project_dir) / config
        try:
            machine = build_cpc464(config_path)
        except ConfigError as error:
            print(f"error: {error}", file=sys.stderr)
            return 1
    run_stdio(project_dir, machine)
    return 0


def _cmd_config(path: str) -> int:
    """Run `cpcxray config`: load the config and print a summary.

    Args:
        path: The config file.

    Returns:
        0 on success, 1 if the config is invalid (the error goes to stderr).
    """
    try:
        cfg = load_config(path)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _print_config(cfg)
    return 0


def _print_config(cfg: MachineConfig) -> None:
    """Print the config: slots with file status and flags, RAM targets, sjasmplus location."""
    print(f"config:      {cfg.source}")
    print(f"project dir: {cfg.project_dir}")
    print(f"model:       CPC {cfg.model}, unpopulated ROM -> slot {cfg.unpopulated_rom}")
    for rom in cfg.roms():
        flags = []
        if rom.decoder:
            flags.append(f"decoder={rom.decoder} ({'on' if rom.decode else 'off'})")
        if rom.analyse:
            flags.append("analyse")
        state = "ok" if rom.exists else "MISSING"
        print(f"  {rom.label:<9} {state:<8} {rom.file}  {' '.join(flags)}".rstrip())
    for t in cfg.ram_targets:
        end = t.start + t.length - 1
        print(f"  ram       {t.name}: #{t.start:04X}-#{end:04X} capture={t.capture}")
    sjasmplus = shutil.which("sjasmplus")
    print(f"sjasmplus:   {sjasmplus or 'NOT FOUND on PATH'}")


if __name__ == "__main__":
    sys.exit(main())
