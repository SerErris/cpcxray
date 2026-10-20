"""Checks for the external tools the project needs."""

import shutil
import subprocess


def test_sjasmplus_on_path() -> None:
    """Sjasmplus is installed and runs (needed for the round-trip check, D-33)."""
    exe = shutil.which("sjasmplus")
    assert exe is not None, "sjasmplus must be installed and on the PATH"
    result = subprocess.run(  # exit status ignored: only the banner matters
        [exe, "--version"], capture_output=True, text=True, timeout=10, check=False
    )
    assert "SjASMPlus" in result.stdout + result.stderr
