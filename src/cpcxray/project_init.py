"""A new project folder for `cpcxray init` (release packaging, owner 2026-10-23).

A release installs the program only (wheel or ready-made app); the user works in a project
folder that `init` creates:

* `config/machine.toml` (CPC 464), `config/cpc664.toml`, `config/cpc6128.toml` - templates from
  the package (no drives; a commented example shows how to add a disk image);
* `roms/` with the standard Amstrad ROMs shipped in the package (OS 464/664/6128, BASIC 1.0 and
  the two BASIC 1.1, AMSDOS) - other ROMs the user adds there;
* `disk/` (empty: no disk images are shipped), `project/` (annotations, analysis, source),
  `output/` (screenshots, dumps, traces, snapshots).

Existing files are never overwritten: `init` on an existing folder only adds what is missing.
"""

from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path

STANDARD_ROMS = ("os464.rom", "os664.rom", "os6128.rom", "basic10.rom", "basic11_664.rom",
                 "basic11_6128.rom", "amsdos.rom")  # fmt: skip
TEMPLATES = ("machine.toml", "cpc664.toml", "cpc6128.toml")
FOLDERS = ("config", "roms", "disk", "project", "output")


def default_project_dir() -> Path:
    """The project folder when none is given: next to the program in a ready-made app.

    A PyInstaller build is "frozen"; its archive holds the program and a project folder side
    by side, so it runs from wherever it was unpacked. Otherwise the current directory.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(".")


def init_project(directory: str | Path) -> dict[str, list[str]]:
    """Create (or complete) a project folder.

    Args:
        directory: The folder; created if it does not exist.

    Returns:
        "created" (files and folders made, relative to the folder), "kept" (files that
        existed and were left alone) and "missing" (standard ROMs not in this package).
    """
    root = Path(directory)
    result: dict[str, list[str]] = {"created": [], "kept": [], "missing": []}
    for folder in FOLDERS:
        path = root / folder
        if not path.is_dir():
            path.mkdir(parents=True)
            result["created"].append(f"{folder}/")
    for name in TEMPLATES:
        _copy(files("cpcxray") / "templates" / name, root / "config" / name, result)
    roms = files("cpcxray") / "roms"
    for name in STANDARD_ROMS:
        source = roms / name
        if not source.is_file():
            result["missing"].append(name)
            continue
        _copy(source, root / "roms" / name, result)
    return result


def _copy(source: object, target: Path, result: dict[str, list[str]]) -> None:
    """Copy a package file unless the target exists (then it is kept)."""
    shown = f"{target.parent.name}/{target.name}"
    if target.exists():
        result["kept"].append(shown)
        return
    target.write_bytes(source.read_bytes())  # type: ignore[attr-defined]  # a Traversable
    result["created"].append(shown)
