from pathlib import Path

import pytest

from cpcxray import __version__
from cpcxray.cli import main

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "machine.toml"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_config_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["config", "--config", str(REPO_CONFIG)]) == 0
    out = capsys.readouterr().out
    assert "upper 5" in out
    assert "decoder=vortex (on) analyse" in out


def test_config_command_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('[machine]\nmodel = "6128"\n')
    assert main(["config", "--config", str(bad)]) == 1
    assert "machine.model" in capsys.readouterr().err
