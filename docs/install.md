# Installing CPC X-Ray

This guide gets cpcxray onto your computer and running. What to do with it then: the
[user guide](user_guide.md); driving it from Claude Code: [Claude Code with cpcxray](claude_code.md).

## 1. Choose a download

Every release on [github.com/SerErris/cpcxray/releases](https://github.com/SerErris/cpcxray/releases)
offers:

| File | For | Needs |
|---|---|---|
| `cpcxray-<version>-py3-none-any.whl` | any system | Python 3.12 or newer |
| `cpcxray-<version>-linux-x86_64.tar.gz` | Linux (incl. WSL) | nothing else |
| `cpcxray-<version>-windows-x86_64.zip` | Windows | nothing else |

The Python package is small and updates easily; the ready-made programs need no Python.

## 2a. The Python package

Install it with [pipx](https://pipx.pypa.io) - it keeps cpcxray and its libraries in their own
environment and puts the `cpcxray` command on your PATH:

```bash
pipx install "cpcxray[gui,mcp] @ https://github.com/SerErris/cpcxray/releases/download/v0.9.4/cpcxray-0.9.4-py3-none-any.whl"
```

(Replace `0.9.4` with the release you want.) The extras: `gui` adds the window (Dear PyGui,
numpy), `mcp` the MCP server for Claude Code. `pip install` into a virtual environment works the
same way. To update, run `pipx install --force` with the new release's URL.

## 2b. A ready-made program

Unpack the archive anywhere you like (e.g. your home folder or `C:\cpcxray`). The folder holds
the program - `cpcxray` (Linux) or `cpcxray.exe` (Windows) with its libraries in `_internal/` -
and is a complete project folder already (section 3). Start the program without arguments, or on
Windows by double click: the window opens and the CPC 464 boots. A console window opens beside it
on Windows; it shows messages and closes with the program.

From a terminal the program takes the same commands as the Python package (`./cpcxray init`,
`cpcxray.exe mcp`, …). Its project folder is always its own folder.

## 3. The project folder

cpcxray works in a project folder; all relative paths start there. Create one with:

```bash
cpcxray init my_cpc
cd my_cpc
```

| Path | Contents |
|---|---|
| `config/machine.toml` | CPC 464: OS, BASIC 1.0, AMSDOS |
| `config/cpc664.toml`, `config/cpc6128.toml` | the 664 and the 6128 (128 KB, banking) |
| `roms/` | the standard Amstrad ROMs (OS 464/664/6128, BASIC 1.0 and 1.1, AMSDOS); add your own ROMs here |
| `disk/` | your disk images (`.dsk`, EDSK) - none are shipped |
| `project/` | your work: annotations, analysis, access maps, generated sources and exports |
| `output/` | screenshots, memory dumps, traces, snapshots |

`init` never overwrites a file; on an existing folder it adds only what is missing (e.g. after an
update). The standard ROMs are shipped with cpcxray (see `THIRD_PARTY.md`); no other ROMs and no
disk images are.

## 4. Starting

| Command | What it does |
|---|---|
| `cpcxray gui` | the window with the CPC of `config/machine.toml`, running |
| `cpcxray gui --config config/cpc6128.toml` | another model (664, 6128) or your own configuration |
| `cpcxray gui --paused` | start stopped at the reset address &0000 |
| `cpcxray gui --mcp-port 0` | without the MCP server in the window (default port 8765, 127.0.0.1 only) |
| `cpcxray mcp` | the MCP server without a window, for Claude Code |
| `cpcxray config` | check a configuration: are the ROMs and disk images there, is sjasmplus found |
| `cpcxray init [DIR]` | create or complete a project folder |
| `cpcxray source-verify SRC IMAGE` | assemble a source with sjasmplus and compare it with an image |
| `cpcxray --version` | the version |

`gui` and `mcp` take `--project-dir DIR` (default: the current folder; for a ready-made program
its own folder) and `--config FILE` (relative to the project folder). Every option, the output and the
exit codes: [command line reference](cli.md).

## 5. Optional: sjasmplus

The round-trip check of generated sources (the Verify phase, `source_verify`) assembles them
with [sjasmplus](https://github.com/z00m128/sjasmplus). Install it and put it on the PATH if you
want to verify sources; nothing else needs it. `cpcxray config` shows whether it is found.

## 6. Troubleshooting

- **"no configuration …"**: run `cpcxray init` in the folder (or start from your project folder).
- **A black screen, "missing: …" in the status line**: a configured ROM file is not in `roms/`;
  `cpcxray config` lists what is missing.
- **The window does not open under WSL**: WSLg is needed (Windows 11, or Windows 10 with WSLg);
  `echo $DISPLAY` must not be empty.
- **"No module named dearpygui" / "… mcp"**: install the package with the extras `[gui,mcp]`.
- **Port 8765 in use**: another cpcxray window runs; close it or start with `--mcp-port 8766`.
