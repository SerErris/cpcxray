"""Machine configuration (design.md §6.14): loading, validation and ROM path handling.

The configuration is a TOML file, normally `config/machine.toml`:

* `[machine]`: model ("464") and the slot that answers for unpopulated ROM numbers (D-10).
* `[rom.lower]` and `[rom.upper.N]` (N = 0-15): ROM image per slot, optional decoder and
  decode switch for scrambled ROMs (D-11), `analyse` to make the slot an analysis target
  (D-16).
* `[[analysis.ram]]`: RAM-range analysis targets (D-23).
* `[drive.A]`, `[drive.B]`: disk image inserted at power-on (`file`), read-only unless
  `writable = true` (real images are never written, phase 10).

Loading validates everything strictly (unknown keys are errors, to catch typos such as
`analyze`) and resolves ROM paths. Missing ROM files are not an error: ROM images are not in
git (NFR-07), so a fresh checkout must still load its config; `MachineConfig.missing_roms()`
reports them and `read_rom_image()` fails when one is actually needed. Disk images are handled
the same way (`missing_disks()`).
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ROM_SIZE = 0x4000  # every CPC ROM image is 16 KB
UPPER_SLOTS = range(16)  # upper ROM numbers 0-15 (D-09)
MEMORY_SIZE = 0x10000

DECODERS = frozenset({"vortex"})  # known decoders for scrambled ROMs (§5.5)
CAPTURE_MODES = frozenset({"first_m1", "manual"})  # RAM-target capture moments (D-23)
MODELS = frozenset({"464"})  # 6128 is later scope (FR-MCH-10)
DRIVES = ("A", "B")  # A: 3" (unit 0), B: Vortex 5.25" (unit 1), FR-FDC-02/03

type Slot = Literal["lower"] | int
"""A ROM slot: "lower" for the OS ROM, or an upper ROM number 0-15."""


class ConfigError(ValueError):
    """Invalid configuration or unreadable file; the message names the offending key or path."""


@dataclass(frozen=True)
class RomConfig:
    """Configuration of one ROM slot.

    Attributes:
        slot: "lower" or the upper ROM number.
        file: Absolute path of the ROM image (resolved against the project directory).
        decoder: Name of the decoder for a scrambled ROM (e.g. "vortex"), or None (D-11).
        decode: Initial state of the decode switch; can be toggled at runtime (D-11).
        analyse: The slot is an analysis target (D-16).
    """

    slot: Slot
    file: Path
    decoder: str | None = None
    decode: bool = True
    analyse: bool = False

    @property
    def exists(self) -> bool:
        """True if the ROM image file exists."""
        return self.file.is_file()

    @property
    def label(self) -> str:
        """Human-readable slot name: "lower" or "upper N"."""
        return "lower" if self.slot == "lower" else f"upper {self.slot}"


@dataclass(frozen=True)
class RamTargetConfig:
    """A RAM-range analysis target (D-23).

    Attributes:
        name: Name of the target, used for its analysis files.
        start: First address of the range.
        length: Length in bytes; the range ends within 64 KB.
        capture: When the image is captured: at the first M1 fetch inside the range
            ("first_m1") or only on command ("manual").
    """

    name: str
    start: int
    length: int
    capture: Literal["first_m1", "manual"] = "first_m1"


@dataclass(frozen=True)
class DriveConfig:
    """A disk image inserted at power-on.

    Attributes:
        drive: "A" or "B".
        file: Absolute path of the image.
        writable: Allow the emulated CPC to write to it (in memory; saved only on request).
    """

    drive: str
    file: Path
    writable: bool = False

    @property
    def exists(self) -> bool:
        """True if the image file exists."""
        return self.file.is_file()


@dataclass(frozen=True)
class MachineConfig:
    """A loaded and validated machine configuration.

    Attributes:
        project_dir: Directory that relative paths are resolved against.
        model: Machine model; only "464" for now.
        unpopulated_rom: Slot that answers when an unpopulated ROM number is selected (D-10).
        lower_rom: The OS ROM, or None if not configured.
        upper_roms: Upper ROM slots by number.
        ram_targets: RAM-range analysis targets.
        drives: Disk images inserted at power-on, by drive letter.
        source: Path of the config file this was loaded from, if any.
    """

    project_dir: Path
    model: str = "464"
    unpopulated_rom: int = 0
    lower_rom: RomConfig | None = None
    upper_roms: dict[int, RomConfig] = field(default_factory=dict)
    ram_targets: tuple[RamTargetConfig, ...] = ()
    drives: dict[str, DriveConfig] = field(default_factory=dict)
    source: Path | None = None

    def roms(self) -> list[RomConfig]:
        """Return all configured ROMs: the lower ROM first, then upper slots by number."""
        lower = [self.lower_rom] if self.lower_rom is not None else []
        return lower + [self.upper_roms[n] for n in sorted(self.upper_roms)]

    def analysis_targets(self) -> list[RomConfig]:
        """Return the ROM slots marked `analyse = true`; nothing else is analysed (FR-ANA-01)."""
        return [rom for rom in self.roms() if rom.analyse]

    def missing_roms(self) -> list[RomConfig]:
        """Return the configured ROMs whose file does not exist (ROMs are not in git, NFR-07)."""
        return [rom for rom in self.roms() if not rom.exists]

    def missing_disks(self) -> list[DriveConfig]:
        """Return the configured disk images whose file does not exist (not in git either)."""
        return [drive for drive in self.drives.values() if not drive.exists]


def load_config(path: str | Path, project_dir: str | Path | None = None) -> MachineConfig:
    """Load and validate a machine.toml.

    Args:
        path: The config file.
        project_dir: Directory for relative ROM paths. Defaults to the parent of the config
            file's directory, because the config lives in `<project>/config/`.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: If the file cannot be read, is not valid TOML or fails validation.
    """
    path = Path(path).resolve()
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"cannot read config {path}: {e}") from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e
    base = Path(project_dir).resolve() if project_dir is not None else path.parent.parent
    return parse_config(data, base, source=path)


def parse_config(
    data: Mapping[str, Any], project_dir: Path, source: Path | None = None
) -> MachineConfig:
    """Validate parsed TOML data and build a `MachineConfig`.

    Args:
        data: The parsed TOML document.
        project_dir: Directory for relative ROM paths.
        source: Config file path, recorded in the result for messages.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: On unknown keys, wrong types or values out of range.
    """
    _check_keys(data, {"machine", "rom", "analysis", "drive"}, "")
    model, unpopulated_rom = _parse_machine(_table(data, "machine", ""))
    rom = _table(data, "rom", "")
    _check_keys(rom, {"lower", "upper"}, "rom")
    lower_rom = None
    if "lower" in rom:
        lower_rom = _parse_rom(_table(rom, "lower", "rom"), "lower", "rom.lower", project_dir)
    return MachineConfig(
        project_dir=project_dir,
        model=model,
        unpopulated_rom=unpopulated_rom,
        lower_rom=lower_rom,
        upper_roms=_parse_upper_roms(_table(rom, "upper", "rom"), project_dir),
        ram_targets=_parse_ram_targets(_table(data, "analysis", "")),
        drives=_parse_drives(_table(data, "drive", ""), project_dir),
        source=source,
    )


def _parse_machine(machine: Mapping[str, Any]) -> tuple[str, int]:
    """Validate the `[machine]` table.

    Returns:
        (model, unpopulated_rom).

    Raises:
        ConfigError: On unknown keys, an unsupported model or a slot outside 0-15.
    """
    _check_keys(machine, {"model", "unpopulated_rom"}, "machine")
    model = _get(machine, "model", str, "machine", "464")
    if model not in MODELS:
        raise ConfigError(f"machine.model: unsupported model {model!r}")
    unpopulated_rom = _get(machine, "unpopulated_rom", int, "machine", 0)
    if unpopulated_rom not in UPPER_SLOTS:
        raise ConfigError(f"machine.unpopulated_rom: {unpopulated_rom} not in 0-15")
    return model, unpopulated_rom


def _parse_upper_roms(upper: Mapping[str, Any], project_dir: Path) -> dict[int, RomConfig]:
    """Validate the `[rom.upper.N]` tables.

    Returns:
        The upper ROM slots by number.

    Raises:
        ConfigError: On a slot key that is not a number 0-15 or an invalid slot table.
    """
    upper_roms: dict[int, RomConfig] = {}
    for key, value in upper.items():
        where = f"rom.upper.{key}"
        try:
            slot = int(key)  # TOML table keys are strings
        except ValueError:
            raise ConfigError(f"{where}: slot must be a number 0-15") from None
        if slot not in UPPER_SLOTS:
            raise ConfigError(f"{where}: slot must be in 0-15")
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: expected a table")
        upper_roms[slot] = _parse_rom(value, slot, where, project_dir)
    return upper_roms


def _parse_ram_targets(analysis: Mapping[str, Any]) -> tuple[RamTargetConfig, ...]:
    """Validate the `[analysis]` table and its `[[analysis.ram]]` entries.

    Raises:
        ConfigError: On unknown keys or invalid RAM targets.
    """
    _check_keys(analysis, {"ram"}, "analysis")
    entries = analysis.get("ram", [])
    if not isinstance(entries, list):
        raise ConfigError("analysis.ram: expected an array of tables")
    return tuple(_parse_ram(entry, f"analysis.ram[{i}]") for i, entry in enumerate(entries))


def _parse_drives(drives: Mapping[str, Any], project_dir: Path) -> dict[str, DriveConfig]:
    """Validate the `[drive.A]` / `[drive.B]` tables.

    Raises:
        ConfigError: On an unknown drive letter, unknown keys or a missing `file`.
    """
    result = {}
    for letter, table in drives.items():
        where = f"drive.{letter}"
        if letter not in DRIVES:
            raise ConfigError(f"{where}: drive must be one of {', '.join(DRIVES)}")
        if not isinstance(table, dict):
            raise ConfigError(f"{where}: expected a table")
        _check_keys(table, {"file", "writable"}, where)
        if "file" not in table:
            raise ConfigError(f"{where}.file: missing")
        file = Path(_get(table, "file", str, where, ""))
        result[letter] = DriveConfig(
            letter, file if file.is_absolute() else project_dir / file,
            _get(table, "writable", bool, where, False),
        )  # fmt: skip
    return result


def read_rom_image(path: str | Path) -> bytes:
    """Read a ROM image and check that it is exactly 16 KB.

    Args:
        path: The ROM file.

    Returns:
        The 16,384 bytes of the image.

    Raises:
        ConfigError: If the file cannot be read or has the wrong size.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ConfigError(f"cannot read ROM {path}: {e}") from e
    if len(data) != ROM_SIZE:
        raise ConfigError(f"ROM {path}: size {len(data)} bytes, expected {ROM_SIZE}")
    return data


def _parse_rom(t: Mapping[str, Any], slot: Slot, where: str, project_dir: Path) -> RomConfig:
    """Validate one `[rom.*]` table.

    Args:
        t: The table.
        slot: The slot it configures.
        where: Key path for error messages, e.g. "rom.upper.5".
        project_dir: Directory for a relative `file`.

    Returns:
        The ROM slot configuration; `decode` defaults to true, `analyse` to false.

    Raises:
        ConfigError: If `file` is missing, a key is unknown or a value has the wrong type.
    """
    _check_keys(t, {"file", "decoder", "decode", "analyse"}, where)
    if "file" not in t:
        raise ConfigError(f"{where}.file: missing")
    file = Path(_get(t, "file", str, where, ""))
    if not file.is_absolute():
        file = project_dir / file
    decoder = t.get("decoder")
    if decoder is not None and decoder not in DECODERS:
        raise ConfigError(f"{where}.decoder: unknown decoder {decoder!r}")
    return RomConfig(
        slot=slot,
        file=file,
        decoder=decoder,
        decode=_get(t, "decode", bool, where, True),
        analyse=_get(t, "analyse", bool, where, False),
    )


def _parse_ram(t: object, where: str) -> RamTargetConfig:
    """Validate one `[[analysis.ram]]` entry.

    Args:
        t: The entry (should be a table).
        where: Key path for error messages, e.g. "analysis.ram[0]".

    Returns:
        The RAM target; `capture` defaults to "first_m1".

    Raises:
        ConfigError: On missing keys, wrong types, or a range outside 64 KB.
    """
    if not isinstance(t, dict):
        raise ConfigError(f"{where}: expected a table")
    _check_keys(t, {"name", "start", "length", "capture"}, where)
    for key in ("name", "start", "length"):
        if key not in t:
            raise ConfigError(f"{where}.{key}: missing")
    name = _get(t, "name", str, where, "")
    start = _get(t, "start", int, where, 0)
    length = _get(t, "length", int, where, 0)
    capture = _get(t, "capture", str, where, "first_m1")
    if not 0 <= start < MEMORY_SIZE:
        raise ConfigError(f"{where}.start: {start:#x} outside 0x0000-0xFFFF")
    if length <= 0 or start + length > MEMORY_SIZE:
        raise ConfigError(f"{where}.length: {length:#x} must be > 0 and end within 64 KB")
    if capture not in CAPTURE_MODES:
        raise ConfigError(f"{where}.capture: must be one of {sorted(CAPTURE_MODES)}")
    return RamTargetConfig(name=name, start=start, length=length, capture=capture)  # type: ignore[arg-type]


def _table(t: Mapping[str, Any], key: str, where: str) -> Mapping[str, Any]:
    """Return the sub-table `t[key]`, or an empty table if absent.

    Raises:
        ConfigError: If the value exists but is not a table.
    """
    value = t.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{_join(where, key)}: expected a table")
    return value


def _get[T](t: Mapping[str, Any], key: str, kind: type[T], where: str, default: T) -> T:
    """Return `t[key]` (or `default`) after checking that it has type `kind`.

    Booleans are rejected where an int is expected, although bool is a subclass of int.

    Raises:
        ConfigError: If the value has the wrong type.
    """
    value = t.get(key, default)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ConfigError(f"{_join(where, key)}: expected {kind.__name__}, got {value!r}")
    return value


def _check_keys(t: Mapping[str, Any], allowed: set[str], where: str) -> None:
    """Reject keys of `t` that are not in `allowed` (catches typos in the config).

    Raises:
        ConfigError: Listing the unknown keys.
    """
    unknown = sorted(set(t) - allowed)
    if unknown:
        raise ConfigError(f"{where or 'top level'}: unknown key(s) {', '.join(unknown)}")


def _join(where: str, key: str) -> str:
    """Join a key path and a key for error messages ("rom" + "lower" → "rom.lower")."""
    return f"{where}.{key}" if where else key
