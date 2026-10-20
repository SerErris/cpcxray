"""Machine configuration (design.md §6.14): loading, validation and ROM path handling."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ROM_SIZE = 0x4000
UPPER_SLOTS = range(16)
MEMORY_SIZE = 0x10000

DECODERS = frozenset({"vortex"})
CAPTURE_MODES = frozenset({"first_m1", "manual"})
MODELS = frozenset({"464"})

type Slot = Literal["lower"] | int


class ConfigError(ValueError):
    """Invalid configuration; the message names the offending key."""


@dataclass(frozen=True)
class RomConfig:
    slot: Slot
    file: Path  # absolute, resolved against the project directory
    decoder: str | None = None  # D-11
    decode: bool = True  # initial state of the decode switch (D-11)
    analyse: bool = False  # analysis target (D-16)

    @property
    def exists(self) -> bool:
        return self.file.is_file()

    @property
    def label(self) -> str:
        return "lower" if self.slot == "lower" else f"upper {self.slot}"


@dataclass(frozen=True)
class RamTargetConfig:
    """RAM-range analysis target (D-23)."""

    name: str
    start: int
    length: int
    capture: Literal["first_m1", "manual"] = "first_m1"


@dataclass(frozen=True)
class MachineConfig:
    project_dir: Path
    model: str = "464"
    unpopulated_rom: int = 0  # D-10
    lower_rom: RomConfig | None = None
    upper_roms: dict[int, RomConfig] = field(default_factory=dict)
    ram_targets: tuple[RamTargetConfig, ...] = ()
    source: Path | None = None

    def roms(self) -> list[RomConfig]:
        lower = [self.lower_rom] if self.lower_rom is not None else []
        return lower + [self.upper_roms[n] for n in sorted(self.upper_roms)]

    def analysis_targets(self) -> list[RomConfig]:
        """ROM slots marked `analyse = true`; nothing else is analysed (FR-ANA-01)."""
        return [rom for rom in self.roms() if rom.analyse]

    def missing_roms(self) -> list[RomConfig]:
        """Configured ROMs whose file does not exist (ROMs are not in git, NFR-07)."""
        return [rom for rom in self.roms() if not rom.exists]


def load_config(path: str | Path, project_dir: str | Path | None = None) -> MachineConfig:
    """Load a machine.toml.

    Relative ROM paths resolve against `project_dir`, which defaults to the parent of the
    config file's directory (the config lives in `<project>/config/`).
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
    _check_keys(data, {"machine", "rom", "analysis"}, "")

    machine = _table(data, "machine", "")
    _check_keys(machine, {"model", "unpopulated_rom"}, "machine")
    model = _get(machine, "model", str, "machine", "464")
    if model not in MODELS:
        raise ConfigError(f"machine.model: unsupported model {model!r}")
    unpopulated = _get(machine, "unpopulated_rom", int, "machine", 0)
    if unpopulated not in UPPER_SLOTS:
        raise ConfigError(f"machine.unpopulated_rom: {unpopulated} not in 0-15")

    rom = _table(data, "rom", "")
    _check_keys(rom, {"lower", "upper"}, "rom")
    lower = None
    if "lower" in rom:
        lower = _parse_rom(_table(rom, "lower", "rom"), "lower", "rom.lower", project_dir)

    upper: dict[int, RomConfig] = {}
    for key, value in _table(rom, "upper", "rom").items():
        where = f"rom.upper.{key}"
        try:
            slot = int(key)
        except ValueError:
            raise ConfigError(f"{where}: slot must be a number 0-15") from None
        if slot not in UPPER_SLOTS:
            raise ConfigError(f"{where}: slot must be in 0-15")
        if not isinstance(value, dict):
            raise ConfigError(f"{where}: expected a table")
        upper[slot] = _parse_rom(value, slot, where, project_dir)

    analysis = _table(data, "analysis", "")
    _check_keys(analysis, {"ram"}, "analysis")
    ram_list = analysis.get("ram", [])
    if not isinstance(ram_list, list):
        raise ConfigError("analysis.ram: expected an array of tables")
    ram_targets = tuple(_parse_ram(t, f"analysis.ram[{i}]") for i, t in enumerate(ram_list))

    return MachineConfig(
        project_dir=project_dir,
        model=model,
        unpopulated_rom=unpopulated,
        lower_rom=lower,
        upper_roms=upper,
        ram_targets=ram_targets,
        source=source,
    )


def read_rom_image(path: str | Path) -> bytes:
    """Read a 16 KB ROM image."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ConfigError(f"cannot read ROM {path}: {e}") from e
    if len(data) != ROM_SIZE:
        raise ConfigError(f"ROM {path}: size {len(data)} bytes, expected {ROM_SIZE}")
    return data


def _parse_rom(t: Mapping[str, Any], slot: Slot, where: str, project_dir: Path) -> RomConfig:
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
    value = t.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{_join(where, key)}: expected a table")
    return value


def _get[T](t: Mapping[str, Any], key: str, kind: type[T], where: str, default: T) -> T:
    value = t.get(key, default)
    # bool is a subclass of int; reject it where an int is expected
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ConfigError(f"{_join(where, key)}: expected {kind.__name__}, got {value!r}")
    return value


def _check_keys(t: Mapping[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(t) - allowed)
    if unknown:
        raise ConfigError(f"{where or 'top level'}: unknown key(s) {', '.join(unknown)}")


def _join(where: str, key: str) -> str:
    return f"{where}.{key}" if where else key
