"""Tests for the machine configuration loader (design.md §6.14)."""

from pathlib import Path

import pytest

from cpcxray.machine.config import (
    ROM_SIZE,
    ConfigError,
    RamTargetConfig,
    load_config,
    read_rom_image,
)

REPO = Path(__file__).resolve().parents[2]
REPO_CONFIG = REPO / "config" / "machine.toml"

MINIMAL = """
[rom.lower]
file = "roms/os.rom"
"""


def write_config(tmp_path: Path, text: str) -> Path:
    """Write `text` to `<tmp_path>/config/machine.toml` and return its path."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    path = cfg_dir / "machine.toml"
    path.write_text(text)
    return path


def test_repo_config_loads() -> None:
    """The repository's config/machine.toml loads with the slots of §6.14."""
    cfg = load_config(REPO_CONFIG)
    assert cfg.model == "464"
    assert cfg.unpopulated_rom == 0
    assert cfg.project_dir == REPO
    assert cfg.lower_rom is not None
    assert cfg.lower_rom.file == REPO / "roms" / "os464.rom"
    assert sorted(cfg.upper_roms) == [0, 5, 7]
    vdos = cfg.upper_roms[5]
    assert (vdos.decoder, vdos.decode, vdos.analyse) == ("vortex", True, True)
    for slot in (0, 7):
        rom = cfg.upper_roms[slot]
        assert (rom.decoder, rom.analyse) == (None, False)
    assert cfg.ram_targets == ()


def test_only_marked_targets_are_analysed() -> None:
    """Only slots marked analyse = true are analysis targets (FR-ANA-01, D-16)."""
    cfg = load_config(REPO_CONFIG)
    assert [rom.slot for rom in cfg.analysis_targets()] == [5]


def test_defaults(tmp_path: Path) -> None:
    """Omitted settings get their defaults."""
    cfg = load_config(write_config(tmp_path, MINIMAL))
    assert cfg.model == "464"
    assert cfg.unpopulated_rom == 0
    assert cfg.upper_roms == {}
    assert cfg.lower_rom is not None
    assert cfg.lower_rom.slot == "lower"
    assert (cfg.lower_rom.decoder, cfg.lower_rom.decode, cfg.lower_rom.analyse) == (
        None,
        True,
        False,
    )


def test_relative_paths_resolve_against_project_dir(tmp_path: Path) -> None:
    """Relative ROM paths resolve against the parent of the config directory."""
    cfg = load_config(write_config(tmp_path, MINIMAL))
    assert cfg.project_dir == tmp_path
    assert cfg.lower_rom is not None
    assert cfg.lower_rom.file == tmp_path / "roms" / "os.rom"


def test_project_dir_override(tmp_path: Path) -> None:
    """An explicit project_dir overrides the default base for ROM paths."""
    other = tmp_path / "elsewhere"
    cfg = load_config(write_config(tmp_path, MINIMAL), project_dir=other)
    assert cfg.lower_rom is not None
    assert cfg.lower_rom.file == other / "roms" / "os.rom"


def test_absolute_path_kept(tmp_path: Path) -> None:
    """Absolute ROM paths are used unchanged."""
    rom = tmp_path / "abs.rom"
    cfg = load_config(write_config(tmp_path, f'[rom.upper.3]\nfile = "{rom}"\n'))
    assert cfg.upper_roms[3].file == rom


def test_missing_roms_reported_not_raised(tmp_path: Path) -> None:
    """A missing ROM file does not fail loading; missing_roms() lists it."""
    (tmp_path / "roms").mkdir()
    (tmp_path / "roms" / "basic.rom").write_bytes(bytes(ROM_SIZE))
    text = MINIMAL + '[rom.upper.0]\nfile = "roms/basic.rom"\n'
    cfg = load_config(write_config(tmp_path, text))
    assert cfg.upper_roms[0].exists
    assert [rom.slot for rom in cfg.missing_roms()] == ["lower"]


def test_ram_target(tmp_path: Path) -> None:
    """RAM targets parse hex integers and default capture to first_m1."""
    text = """
[[analysis.ram]]
name = "loader"
start = 0x8000
length = 0x1000

[[analysis.ram]]
name = "patch"
start = 0xA000
length = 0x10
capture = "manual"
"""
    cfg = load_config(write_config(tmp_path, text))
    assert cfg.ram_targets == (
        RamTargetConfig("loader", 0x8000, 0x1000, "first_m1"),
        RamTargetConfig("patch", 0xA000, 0x10, "manual"),
    )


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ('[machine]\nmodel = "6128"\n', r"machine\.model"),
        ("[machine]\nunpopulated_rom = 16\n", r"machine\.unpopulated_rom"),
        ("[machine]\nunpopulated_rom = true\n", r"machine\.unpopulated_rom"),
        ('[rom.upper.16]\nfile = "x.rom"\n', r"rom\.upper\.16"),
        ('[rom.upper.basic]\nfile = "x.rom"\n', r"rom\.upper\.basic"),
        ('[rom.upper.5]\nfile = "x.rom"\ndecoder = "foo"\n', r"rom\.upper\.5\.decoder"),
        ('[rom.upper.5]\nfile = "x.rom"\nanalyze = true\n', r"unknown key.*analyze"),
        ('[rom.upper.5]\nfile = "x.rom"\ndecode = "yes"\n', r"rom\.upper\.5\.decode"),
        ("[rom.upper.5]\ndecode = true\n", r"rom\.upper\.5\.file"),
        ("[machines]\n", r"unknown key.*machines"),
        (
            '[[analysis.ram]]\nname = "x"\nstart = 0xFFF0\nlength = 0x20\n',
            r"analysis\.ram\[0\]\.length",
        ),
        (
            '[[analysis.ram]]\nname = "x"\nstart = 0x10000\nlength = 1\n',
            r"analysis\.ram\[0\]\.start",
        ),
        ('[[analysis.ram]]\nname = "x"\nstart = 0\nlength = 0\n', r"length"),
        (
            '[[analysis.ram]]\nname = "x"\nstart = 0\nlength = 1\ncapture = "now"\n',
            r"capture",
        ),
        ("[[analysis.ram]]\nstart = 0\nlength = 1\n", r"analysis\.ram\[0\]\.name"),
    ],
)
def test_validation_errors(tmp_path: Path, text: str, match: str) -> None:
    """Invalid settings raise ConfigError naming the offending key."""
    with pytest.raises(ConfigError, match=match):
        load_config(write_config(tmp_path, text))


def test_invalid_toml(tmp_path: Path) -> None:
    """A TOML syntax error raises ConfigError."""
    with pytest.raises(ConfigError):
        load_config(write_config(tmp_path, "[machine\n"))


def test_missing_config_file(tmp_path: Path) -> None:
    """A missing config file raises ConfigError."""
    with pytest.raises(ConfigError, match="cannot read config"):
        load_config(tmp_path / "nope.toml")


def test_read_rom_image(tmp_path: Path) -> None:
    """read_rom_image accepts exactly 16 KB and reports wrong sizes and missing files."""
    good = tmp_path / "good.rom"
    good.write_bytes(bytes(range(256)) * (ROM_SIZE // 256))
    assert read_rom_image(good) == good.read_bytes()

    short = tmp_path / "short.rom"
    short.write_bytes(bytes(100))
    with pytest.raises(ConfigError, match="size 100"):
        read_rom_image(short)

    with pytest.raises(ConfigError, match="cannot read ROM"):
        read_rom_image(tmp_path / "missing.rom")
