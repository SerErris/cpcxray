"""Command line entry point `cpcxray` (console script, see pyproject.toml).

Subcommands so far: `config` (load and check a machine configuration). Later phases add `mcp`
(headless MCP server over stdio) and `gui` (Dear PyGui frontend), see design.md §6.16.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence

from cpcxray import __version__
from cpcxray.machine.config import ConfigError, MachineConfig, load_config

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

    args = parser.parse_args(argv)
    if args.command == "config":
        return _cmd_config(args.config)
    parser.print_help()
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
