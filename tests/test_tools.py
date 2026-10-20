import shutil
import subprocess


def test_sjasmplus_on_path() -> None:
    # Required for the round-trip check (D-33)
    exe = shutil.which("sjasmplus")
    assert exe is not None, "sjasmplus must be installed and on the PATH"
    result = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=10)
    assert "SjASMPlus" in result.stdout + result.stderr
