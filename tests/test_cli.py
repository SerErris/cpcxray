"""Tests for the cpcxray command line."""

from pathlib import Path

import pytest

from cpcxray import __version__
from cpcxray.cli import main

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "machine.toml"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    """--version prints the package version."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_config_command(capsys: pytest.CaptureFixture[str]) -> None:
    """`cpcxray config` prints the slots of the repository config."""
    assert main(["config", "--config", str(REPO_CONFIG)]) == 0
    out = capsys.readouterr().out
    assert "upper 0" in out and "upper 7" in out and "amsdos.rom" in out


def test_config_command_shows_decoder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A scrambled ROM slot is listed with its decoder, decode switch and analysis mark."""
    config = tmp_path / "config" / "machine.toml"
    config.parent.mkdir()
    config.write_text(
        '[rom.upper.5]\nfile = "roms/scrambled.rom"\ndecoder = "vortex"\nanalyse = true\n',
        encoding="utf-8",
    )
    assert main(["config", "--config", str(config)]) == 0
    assert "decoder=vortex (on) analyse" in capsys.readouterr().out


def test_config_command_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`cpcxray config` with an invalid config exits with 1 and names the key."""
    bad = tmp_path / "bad.toml"
    bad.write_text('[machine]\nmodel = "472"\n')
    assert main(["config", "--config", str(bad)]) == 1
    assert "machine.model" in capsys.readouterr().err
