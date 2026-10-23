# PyInstaller build of the ready-made app (release packaging; run from the repository root):
#     pyinstaller packaging/cpcxray.spec --noconfirm
# One folder (dist/cpcxray/): the console program `cpcxray` (needed for `cpcxray mcp` over
# stdio) and its libraries in _internal/. The release script then runs `cpcxray init` in that
# folder, so the unpacked archive is a project folder (configs, standard ROMs, disk/, ...).
# pylint: skip-file
# ruff: noqa

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hidden = (
    collect_submodules("cpcxray")  # the GUI and MCP modules are imported inside functions
    + collect_submodules("mcp", filter=lambda name: not name.startswith("mcp.cli"))
    + collect_submodules("uvicorn")
)
datas = collect_data_files("cpcxray") + collect_data_files("dearpygui")

analysis = Analysis(
    ["launcher.py"],
    pathex=["../src"],
    datas=datas,
    hiddenimports=hidden,
    excludes=["tkinter", "pytest", "pylint", "ruff"],
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="cpcxray",
    console=True,
)
COLLECT(exe, analysis.binaries, analysis.datas, name="cpcxray")
