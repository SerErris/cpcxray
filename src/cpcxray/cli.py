"""Command line entry point `cpcxray` (console script, see pyproject.toml).

Subcommands:

* `init [DIR]`: create a project folder (configs for 464/664/6128, the standard ROMs, disk/,
  project/, output/; nothing existing is overwritten) - the start after installing a release.
* `config`: load and check a machine configuration.
* `mcp`: the headless MCP server on stdin/stdout (design.md §6.16, D-28); Claude Code starts it
  from the project's .mcp.json. By default it drives the CPC (464, 664 or 6128) of
  config/machine.toml (`--machine flat` for the bare Z80). Needs the optional dependency `mcp`.

* `gui`: the Dear PyGui window with the CPC from the configuration. The machine starts
  running (`--paused` starts it stopped at &0000); by default the window also serves MCP over
  HTTP on 127.0.0.1:8765 so Claude can attach (`--mcp-port 0` turns that off).
* `source-verify`: the round-trip check (D-33) without a session: assemble a source with
  sjasmplus and compare it byte for byte with an image (e.g. a clear dump); lists differing
  addresses with the source line, exit code 1 if they differ.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from cpcxray import __version__
from cpcxray.machine.config import ConfigError, MachineConfig, load_config
from cpcxray.machine.cpc464 import CPC464, build_cpc464
from cpcxray.project_init import default_project_dir, init_project

DEFAULT_CONFIG = "config/machine.toml"
DESCRIPTION = "CPC X-Ray: an Amstrad CPC 464/664/6128 emulator for reverse engineering."
EPILOG = """start:  cpcxray init my_cpc && cd my_cpc && cpcxray gui
docs:   https://github.com/SerErris/cpcxray/blob/main/docs/cli.md (all commands and options)"""


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the command line and run the selected subcommand.

    Args:
        argv: Arguments without the program name; defaults to `sys.argv[1:]`.

    Returns:
        The process exit code (0 on success).
    """
    parser = argparse.ArgumentParser(
        prog="cpcxray", description=DESCRIPTION, epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )  # fmt: skip
    parser.add_argument("--version", action="version", version=f"cpcxray {__version__}")
    sub = parser.add_subparsers(dest="command")
    project_dir = str(default_project_dir())
    p_init = sub.add_parser("init", help="create a project folder (configs, standard ROMs)")
    p_init.add_argument("directory", nargs="?", default=project_dir, help="the folder")
    p_config = sub.add_parser("config", help="load and check a machine configuration")
    p_config.add_argument("--config", default=str(Path(project_dir) / DEFAULT_CONFIG),
                          help="path to machine.toml")  # fmt: skip
    p_mcp = sub.add_parser("mcp", help="run the headless MCP server on stdio")
    p_mcp.add_argument("--project-dir", default=project_dir,
                       help="base directory for relative paths")  # fmt: skip
    p_mcp.add_argument(
        "--machine", choices=("cpc464", "flat"), default="cpc464",
        help="cpc464 (any CPC model from --config) or flat (bare Z80, 64 KB RAM)",
    )  # fmt: skip
    p_mcp.add_argument("--config", default=DEFAULT_CONFIG, help="machine.toml for cpc464")

    p_gui = sub.add_parser("gui", help="open the emulator window (optionally with MCP over HTTP)")
    p_gui.add_argument("--project-dir", default=project_dir,
                       help="base directory for relative paths")  # fmt: skip
    p_gui.add_argument("--config", default=DEFAULT_CONFIG, help="machine.toml")
    p_gui.add_argument(
        "--mcp-port", type=int, default=8765, help="MCP over HTTP on 127.0.0.1 (0: off)"
    )
    p_gui.add_argument(
        "--paused", action="store_true", help="start stopped at the reset vector (debug the boot)"
    )
    p_verify = sub.add_parser("source-verify", help="assemble a source and compare with an image")
    p_verify.add_argument("source", help="the sjasmplus source (e.g. project/rom.asm)")
    p_verify.add_argument("expected", help="the image it must reproduce (e.g. a clear dump)")
    p_verify.add_argument("--start", default="C000", help="address of the first byte (hex)")

    args = parser.parse_args(argv)
    if args.command == "init":
        return _cmd_init(args.directory)
    if args.command == "config":
        return _cmd_config(args.config)
    if args.command == "gui":
        return _cmd_gui(args.project_dir, args.config, args.mcp_port, args.paused)
    if args.command == "mcp":
        return _cmd_mcp(args.project_dir, args.machine, args.config)
    if args.command == "source-verify":
        return _cmd_source_verify(args.source, args.expected, int(args.start.lstrip("&#"), 16))
    parser.print_help()
    return 0


def _cmd_init(directory: str) -> int:
    """Run `cpcxray init`: create or complete a project folder and say what was done."""
    result = init_project(directory)
    folder = Path(directory).resolve()
    print(f"project folder: {folder}")
    for name in result["created"]:
        print(f"  created  {name}")
    for name in result["kept"]:
        print(f"  kept     {name} (exists, not overwritten)")
    for name in result["missing"]:
        print(f"  missing  roms/{name} (not in this package - put your own copy there)")
    print(f"next: cd {folder} and run `cpcxray gui` (config/cpc664.toml, config/cpc6128.toml "
          "for the other models: `cpcxray gui --config config/cpc6128.toml`)")  # fmt: skip
    return 0


def _cmd_mcp(project_dir: str, machine_kind: str, config: str) -> int:
    """Run `cpcxray mcp`: serve MCP on stdio until the client disconnects.

    Args:
        project_dir: Base directory for relative paths (also for --config).
        machine_kind: "cpc464" (powered on with the configured ROMs) or "flat".
        config: The machine.toml for the CPC (464, 664 or 6128).

    Returns:
        0 when the client disconnects, 1 if `mcp` is missing or the config is invalid.
    """
    try:
        from cpcxray.mcp_server.server import run_stdio  # pylint: disable=import-outside-toplevel  # optional dependency
    except ImportError as error:
        print(f"error: {error}; install with: pipx install 'cpcxray[gui,mcp]'", file=sys.stderr)
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


def _cmd_gui(project_dir: str, config: str, mcp_port: int, paused: bool) -> int:
    """Run `cpcxray gui`: the emulator window with the CPC from the configuration.

    The machine starts running (it boots like a real CPC) unless `paused` is set.

    Returns:
        0 when the window is closed, 1 if the GUI packages are missing or the config is bad.
    """
    try:
        from cpcxray.session.session import Session  # pylint: disable=import-outside-toplevel
        from cpcxray.ui.app import App  # pylint: disable=import-outside-toplevel
    except ImportError as error:
        print(f"error: {error}; install with: pipx install 'cpcxray[gui,mcp]'", file=sys.stderr)
        return 1
    machine = _build_machine(project_dir, config)
    if machine is None:
        return 1
    App(Session(machine, project_dir=project_dir), mcp_port or None, autostart=not paused).run()
    return 0


def _build_machine(project_dir: str, config: str) -> CPC464 | None:
    """Power on the CPC of a config; print what is wrong or missing (None: cannot start).

    A missing config points to `cpcxray init`; missing ROM or disk files are listed - the
    machine starts anyway (the window names them too).
    """
    config_path = Path(config) if Path(config).is_absolute() else Path(project_dir) / config
    if not config_path.is_file():
        print(f"error: no configuration {config_path}; create a project folder with "
              f"`cpcxray init {project_dir}`", file=sys.stderr)  # fmt: skip
        return None
    try:
        machine = build_cpc464(config_path)
    except ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return None
    for skipped in machine.skipped_roms:
        print(f"warning: {skipped}", file=sys.stderr)
    return machine


def _cmd_source_verify(source: str, expected: str, start: int) -> int:
    """Run `cpcxray source-verify`: round-trip check of a source against an image (D-33).

    Returns:
        0 if identical, 1 if the bytes differ or sjasmplus fails.
    """
    from cpcxray.analysis.verify import (  # pylint: disable=import-outside-toplevel  # CLI only
        AssemblyError,
        verify_source,
    )

    try:
        result = verify_source(Path(source), Path(expected).read_bytes(), start)
    except (OSError, AssemblyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if result.identical:
        print(f"identical: {result.assembled_length} bytes")
        return 0
    print(f"{result.difference_count} differing bytes (assembled {result.assembled_length}, "
          f"expected {result.expected_length})")  # fmt: skip
    for entry in result.differences:
        print(f"{entry['address']}  expected {entry['expected']}  assembled {entry['assembled']}")
    return 1


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
    """Print the config: ROM slots, RAM targets, disk drives, files to load, sjasmplus."""
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
    for drive in cfg.drives.values():
        status = "ok" if drive.exists else "MISSING"
        mode = "writable" if drive.writable else "read-only"
        print(f"  drive {drive.drive}   {status:<8} {drive.file}  {mode}")
    for load in cfg.loads:
        status = "ok" if load.exists else "MISSING"
        print(f"  load      {status:<8} {load.file}  at #{load.address:04X}")
    sjasmplus = shutil.which("sjasmplus")
    print(f"sjasmplus:   {sjasmplus or 'NOT FOUND on PATH'}")


if __name__ == "__main__":
    sys.exit(main())
