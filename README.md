# CPC X-Ray

An Amstrad CPC 464 / 664 / 6128 emulator built for reverse engineering. It runs the real
firmware, BASIC, AMSDOS and any expansion ROM, and analyses the code you select (a ROM or a RAM
range) - including ROMs scrambled with the Vortex X-module scheme, which it decodes on the fly.
It produces a clear ROM image, an access map, a reassemblable sjasmplus source (checked byte
for byte), traces, a debugger window and an MCP server, so Claude Code can drive the machine.

## Install

Each release on the [releases page](https://github.com/SerErris/cpcxray/releases) offers:

- **The Python package** (`cpcxray-<version>-py3-none-any.whl`), for any system with
  Python 3.12 or newer. With [pipx](https://pipx.pypa.io):

  ```bash
  pipx install "cpcxray[gui,mcp] @ https://github.com/SerErris/cpcxray/releases/download/v0.1.0/cpcxray-0.1.0-py3-none-any.whl"
  ```

- **A ready-made program** for Linux (`…-linux-x86_64.tar.gz`) or Windows
  (`…-windows-x86_64.zip`), no Python needed: unpack it and run `cpcxray` (`cpcxray.exe`) in
  the unpacked folder - it is a project folder already.

## Start

```bash
cpcxray init my_cpc        # a project folder: configs, the standard ROMs, disk/, project/, output/
cd my_cpc
cpcxray gui                # the CPC 464 boots to BASIC
cpcxray gui --config config/cpc6128.toml   # or the 664 / 6128
```

The standard Amstrad ROMs (OS, BASIC, AMSDOS of the 464, 664 and 6128) come with cpcxray. Put
other ROMs into `roms/` and disk images into `disk/`; the Project tab's machine editor adds
them to the configuration. `cpcxray config` checks a configuration.

Type on your own keyboard layout: the character you type is typed on the CPC.

## Claude Code (MCP)

- Headless: add to the project's `.mcp.json`: `{"mcpServers": {"cpcxray": {"command":
  "cpcxray", "args": ["mcp"]}}}`.
- Attach to the running window: `claude mcp add --transport http cpcxray-gui
  http://127.0.0.1:8765/mcp`.

## Requirements

- [sjasmplus](https://github.com/z00m128/sjasmplus) on the PATH, only for the round-trip check
  of generated sources (`cpcxray source-verify`, the Verify phase).
- The window uses OpenGL (Dear PyGui); under WSL it runs with WSLg.

## Licence

GPL-3.0-or-later (see `LICENSE`). Third-party parts (the Amstrad ROMs, the DejaVu font):
see `THIRD_PARTY.md`.
