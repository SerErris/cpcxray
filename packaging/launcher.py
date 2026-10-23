"""Entry point of the ready-made app (PyInstaller): the `cpcxray` command line.

Started without arguments (e.g. by a double click) it opens the window, as `cpcxray gui`; the
project folder of a ready-made app is the folder it was unpacked to
(`cpcxray.project_init.default_project_dir`).
"""

import sys

from cpcxray.cli import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["gui"]))
