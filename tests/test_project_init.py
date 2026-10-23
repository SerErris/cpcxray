"""Tests of `cpcxray init` and the package data a release ships (templates, ROMs, font)."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import pytest

from cpcxray.cli import main
from cpcxray.machine.config import load_config
from cpcxray.project_init import (
    FOLDERS,
    STANDARD_ROMS,
    TEMPLATES,
    default_project_dir,
    init_project,
)

PACKAGE_ROMS = files("cpcxray") / "roms"
ROMS_SHIPPED = all((PACKAGE_ROMS / name).is_file() for name in STANDARD_ROMS)


def test_init_creates_a_project_folder(tmp_path: Path) -> None:
    """Folders, the three configs and (if the package has them) the standard ROMs."""
    folder = tmp_path / "my_cpc"
    result = init_project(folder)
    assert all((folder / name).is_dir() for name in FOLDERS)
    assert {f"config/{name}" for name in TEMPLATES} <= set(result["created"])
    models = {name: load_config(folder / "config" / name).model for name in TEMPLATES}
    assert models == {"machine.toml": "464", "cpc664.toml": "664", "cpc6128.toml": "6128"}
    if ROMS_SHIPPED:
        assert all((folder / "roms" / name).is_file() for name in STANDARD_ROMS)
        assert not load_config(folder / "config" / "machine.toml").missing_roms()
    else:
        assert result["missing"] == list(STANDARD_ROMS)
    assert not any((folder / "disk").iterdir())  # no disk image is shipped


def test_init_never_overwrites(tmp_path: Path) -> None:
    """A second init keeps every existing file (the owner's edits survive)."""
    init_project(tmp_path)
    config = tmp_path / "config" / "machine.toml"
    config.write_text("# mine\n", encoding="utf-8")
    result = init_project(tmp_path)
    assert config.read_text(encoding="utf-8") == "# mine\n"
    assert "config/machine.toml" in result["kept"] and not result["created"]


def test_cli_init_and_version(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`cpcxray init DIR` reports what it made; `--version` names the version."""
    assert main(["init", str(tmp_path / "p")]) == 0
    assert "created  config/machine.toml" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--version"])
    assert capsys.readouterr().out.startswith("cpcxray 0.")


def test_gui_without_a_config_points_to_init(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`cpcxray gui` in a folder without a project: an error naming `cpcxray init`."""
    pytest.importorskip("dearpygui")
    assert main(["gui", "--project-dir", str(tmp_path)]) == 1
    assert "cpcxray init" in capsys.readouterr().err


def test_package_data_and_default_folder() -> None:
    """The font ships in the package; outside a frozen app the project is the current folder."""
    assert (files("cpcxray") / "fonts" / "DejaVuSansMono.ttf").is_file()
    assert (files("cpcxray") / "fonts" / "LICENSE-DejaVu.txt").is_file()
    assert default_project_dir() == Path(".")
