"""Session methods for the machine configuration (task 5i, the Project tab's machine editor).

A mixin of `Session` (every public method is an MCP tool, D-27). A configuration travels as its
TOML document (the tables of a machine.toml as a dict, machine/config.py `config_data`), so a
client can read a file, change the document, write it or apply it:

* `config_get`: the configuration the machine was built from, with each ROM's header and the
  ROMs and disks that were skipped;
* `config_read` / `config_write`: a machine.toml to and from a document (validated; written as
  a fresh, commented file - comments of a hand-edited file are not kept);
* `config_apply`: power on a new machine from a document (model, ROMs and drives need a power
  cycle), as `load_config` does from a file;
* `rom_header`: what a ROM image's header says (type, version, first name), decoded for a
  scrambled ROM;
* `loads_get`: the configuration's binary files to load (`[[load]]`) and whether RAM holds
  each one was loaded since the last reset (`memory_file_check`) - they are loaded on command
  only (`memory_load`).
"""

from __future__ import annotations

import dataclasses
import tomllib
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cpcxray.decoding.vortex import DECODERS
from cpcxray.machine.config import (
    ROM_SIZE,
    ConfigError,
    MachineConfig,
    config_data,
    parse_config,
    save_config,
)
from cpcxray.session.errors import SessionError

if TYPE_CHECKING:
    from cpcxray.machine.cpc464 import CPC464
    from cpcxray.machine.flat import Machine

ROM_TYPES = {0: "foreground", 1: "background", 2: "extension", 0x80: "built-in"}
NAME_TABLE_BASE = 0xC000  # an upper ROM's name table pointer is a &C000-based address
MAX_NAME = 16


class ProjectMixin:
    """`config_*` and `rom_header` of `Session` (see the module docstring)."""

    machine: Machine
    project_dir: Path
    loaded_files: dict[tuple[Path, int], int]  # memory_load: (file, address) → mtime (Session)

    def _path(self, path: str) -> Path:
        """Resolve a project-relative path (implemented by Session)."""
        raise NotImplementedError

    def _address(self, value: int | str) -> int:
        """Parse an address or label (implemented by Session)."""
        raise NotImplementedError

    def _install_machine(self, machine: CPC464) -> None:
        """Make a new machine the session's (implemented by Session)."""
        raise NotImplementedError

    def config_get(self) -> dict[str, Any]:
        """The configuration of the running machine as a TOML document, with ROM headers.

        Returns:
            "path" (the config file, relative to the project, or None), "config" (the
            document; None for the bare Z80 machine), "roms" (per configured ROM: "slot",
            "file", "exists", the header as `rom_header` gives it) and "skipped" (ROMs and
            disks that were not installed, with the reason).
        """
        config: MachineConfig | None = getattr(self.machine, "config", None)
        if config is None:
            return {"path": None, "config": None, "roms": [], "skipped": []}
        roms = []
        for rom in config.roms():
            slot = "lower" if rom.slot == "lower" else str(rom.slot)
            roms.append({"slot": slot, "file": self._shown(rom.file), "exists": rom.exists,
                         **_header_of(str(rom.file), slot, rom.decoder)})  # fmt: skip
        return {"path": self._shown(config.source) if config.source else None,
                "config": config_data(config), "roms": roms,
                "skipped": list(getattr(self.machine, "skipped_roms", []))}  # fmt: skip

    def config_read(self, path: str) -> dict[str, Any]:
        """Read and validate a machine.toml without applying it; returns its document."""
        file = self._path(path)
        try:
            data = tomllib.loads(file.read_text(encoding="utf-8"))
            parse_config(data, self.project_dir, file)
        except (OSError, tomllib.TOMLDecodeError, ConfigError) as error:
            raise SessionError(f"{file}: {error}") from None
        return {"path": self._shown(file), "config": data}

    def config_write(self, config: dict[str, Any], path: str) -> dict[str, Any]:
        """Validate a configuration document and write it as a fresh, commented machine.toml."""
        try:
            file = save_config(config, self._path(path), self.project_dir)
        except ConfigError as error:
            raise SessionError(str(error)) from None
        return {"path": self._shown(file)}

    def config_apply(self, config: dict[str, Any], path: str | None = None) -> dict[str, Any]:
        """Power on a CPC from a configuration document (memory cleared, as `load_config`).

        Args:
            config: The document (tables of a machine.toml).
            path: The file it belongs to (shown as the machine's config file), if any.
        """
        from cpcxray.machine.cpc464 import CPC464  # pylint: disable=import-outside-toplevel

        source = self._path(path) if path else None
        try:
            machine_config = parse_config(config, self.project_dir, source)
        except ConfigError as error:
            raise SessionError(str(error)) from None
        self._install_machine(CPC464(dataclasses.replace(machine_config, source=source)))
        return self.config_get()

    def loads_get(self) -> dict[str, Any]:
        """The binary files of the configuration to load into RAM, and which are loaded now.

        Returns:
            "loads": per file "file", "address", "exists", "length" and "loaded" (loaded since
            the last reset, see `memory_file_check`).
        """
        config: MachineConfig | None = getattr(self.machine, "config", None)
        loads = config.loads if config is not None else ()
        return {"loads": [self.memory_file_check(load.address, str(load.file)) for load in loads]}

    def memory_file_check(self, address: int | str, path: str) -> dict[str, Any]:
        """Whether a file was loaded at `address` (`memory_load`) since the last reset.

        A reminder, not a comparison with RAM - a running program changes its own variables
        (owner, 2026-10-23). It counts as not loaded after a reset, a new machine (Apply,
        `load_config`), a snapshot load, or when the file changed since it was loaded.

        Returns:
            "file", "address", "exists", "length" and "loaded".
        """
        start = self._address(address)
        file = self._path(path)
        entry: dict[str, Any] = {
            "file": self._shown(file), "address": f"{start:04X}", "exists": file.is_file(),
            "length": 0, "loaded": False,
        }  # fmt: skip
        if entry["exists"]:
            status = file.stat()
            entry["length"] = status.st_size
            loaded_version = self.loaded_files.get((file.resolve(), start))
            entry["loaded"] = loaded_version == status.st_mtime_ns
        return entry

    def rom_header(self, path: str, decoder: str | None = None) -> dict[str, Any]:
        """What a ROM image's header says: "type", "version" and the first "name".

        Args:
            path: The ROM image.
            decoder: Its decoder ("vortex") for a scrambled ROM: the header is data, read
                decoded.

        Returns:
            The header, or "error" for a missing or wrong-sized file.
        """
        return _header_of(str(self._path(path)), "upper", decoder)

    def _shown(self, path: Path) -> str:
        """A path relative to the project directory where possible."""
        try:
            return path.resolve().relative_to(self.project_dir).as_posix()
        except ValueError:
            return str(path)


def _header_of(path: str, slot: str, decoder: str | None) -> dict[str, Any]:
    """The header of a ROM file (cached per file and modification time)."""
    file = Path(path)
    try:
        modified = file.stat().st_mtime_ns
    except OSError:
        return {"error": "file missing"}
    return dict(_cached_header(path, modified, slot, decoder))


@lru_cache(maxsize=64)
def _cached_header(
    path: str, _modified: int, slot: str, decoder: str | None
) -> tuple[tuple[str, Any], ...]:
    """The header of a ROM file as items (hashable for the cache)."""
    data = Path(path).read_bytes()
    if len(data) != ROM_SIZE:
        return (("error", f"{len(data)} bytes, not 16 KB"),)
    return tuple(describe_rom(data, slot, decoder).items())


def describe_rom(data: bytes, slot: str = "upper", decoder: str | None = None) -> dict[str, Any]:
    """What a 16 KB ROM image's header says (an OS ROM in the lower slot has none).

    The header (type, mark, version, modification, name table pointer) and the names are data,
    so a scrambled ROM's bytes are decoded first (D-11).
    """
    if slot == "lower":
        return {"type": "lower ROM (OS)", "version": "", "name": ""}
    decode = DECODERS.get(decoder or "")

    def byte(offset: int) -> int:
        """A header byte as the CPU reads it as data."""
        raw = data[offset & 0x3FFF]
        return decode(NAME_TABLE_BASE + offset, raw) if decode is not None else raw

    rom_type, mark, version, modification = (byte(n) for n in range(4))
    result = {"type": ROM_TYPES.get(rom_type, f"&{rom_type:02X}"),
              "version": f"{mark}.{version}{modification}", "name": ""}  # fmt: skip
    pointer = byte(4) | byte(5) << 8
    if rom_type in ROM_TYPES and NAME_TABLE_BASE + 8 <= pointer < NAME_TABLE_BASE + ROM_SIZE:
        result["name"] = _first_name(byte, pointer - NAME_TABLE_BASE)
    return result


def _first_name(byte: Callable[[int], int], offset: int) -> str:
    """The first name of a ROM's name table (its last character has bit 7 set); "" if odd."""
    characters = []
    for index in range(MAX_NAME):
        value = byte(offset + index)
        characters.append(chr(value & 0x7F))
        if value & 0x80:
            break
    name = "".join(characters)
    return name if name.isprintable() else ""
