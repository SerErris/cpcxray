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
    assert "upper 5" in out
    assert "decoder=vortex (on) analyse" in out


def test_config_command_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`cpcxray config` with an invalid config exits with 1 and names the key."""
    bad = tmp_path / "bad.toml"
    bad.write_text('[machine]\nmodel = "6128"\n')
    assert main(["config", "--config", str(bad)]) == 1
    assert "machine.model" in capsys.readouterr().err
