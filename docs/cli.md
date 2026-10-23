# cpcxray command line

The `cpcxray` command has five subcommands: `init`, `gui`, `mcp`, `config` and `source-verify`.
This page describes every option, the defaults, the output and the exit codes. Installing:
[install guide](install.md); the window: [user guide](user_guide.md); the MCP tools:
[MCP reference](mcp.md).

```text
cpcxray [-h] [--version] {init,config,mcp,gui,source-verify} ...
```

| Option | Meaning |
|---|---|
| `-h`, `--help` | help for the command, or for a subcommand (`cpcxray gui --help`) |
| `--version` | print `cpcxray <version>` and exit |

Without a subcommand `cpcxray` prints its help. The **ready-made programs** (Linux, Windows) are
the exception: started without arguments - e.g. by a double click - they run `cpcxray gui`.

## Paths and the project folder

cpcxray works in a **project folder** (created by `cpcxray init`): `config/`, `roms/`, `disk/`,
`project/`, `output/`. Relative paths - the `--config` file, ROM and disk paths inside the
configuration, and every file the window or the MCP tools read or write - start there.

| How cpcxray was installed | Default project folder |
|---|---|
| Python package (pipx / pip) | the current directory |
| ready-made program | the folder the program is in (wherever it was started from) |

`gui` and `mcp` take `--project-dir` to choose another one.

---

## `cpcxray init`

Create a project folder, or complete an existing one.

```text
cpcxray init [directory]
```

| Argument | Default | Meaning |
|---|---|---|
| `directory` | the default project folder | the folder; created if it does not exist |

It creates `config/machine.toml` (CPC 464), `config/cpc664.toml`, `config/cpc6128.toml`, the
folders `roms/`, `disk/`, `project/`, `output/`, and copies the standard Amstrad ROMs into
`roms/` (OS 464/664/6128, BASIC 1.0 and 1.1, AMSDOS). **Existing files are never overwritten**:
on an existing folder only what is missing is added - safe to run again after an update.

Output: one line per item, `created`, `kept` (existed, not overwritten) or `missing` (a standard
ROM not in this installation), then the next step.

```text
$ cpcxray init my_cpc
project folder: /home/me/my_cpc
  created  config/
  …
  created  config/machine.toml
  created  roms/os464.rom
  …
next: cd /home/me/my_cpc and run `cpcxray gui` …
```

Exit code: 0.

## `cpcxray gui`

Open the emulator window with the CPC of a configuration.

```text
cpcxray gui [--project-dir DIR] [--config FILE] [--mcp-port PORT] [--paused]
```

| Option | Default | Meaning |
|---|---|---|
| `--project-dir DIR` | the default project folder | the project folder |
| `--config FILE` | `config/machine.toml` | the machine configuration, relative to the project folder (or absolute) |
| `--mcp-port PORT` | `8765` | serve MCP over HTTP at `http://127.0.0.1:PORT/mcp` so Claude Code can attach; `0` turns it off |
| `--paused` | off | start stopped at the reset address &0000 instead of running (to debug the boot) |

The machine is powered on and starts running like a real CPC. A ROM or disk file named in the
configuration but missing does not stop the start: it is printed as `warning: …` and named in the
window's status line. When the window closes, the analysis state (analysis databases, access
maps) is saved into `project/`.

Examples:

```bash
cpcxray gui                                  # the CPC 464 of config/machine.toml
cpcxray gui --config config/cpc6128.toml     # the 6128
cpcxray gui --project-dir ~/cpc/my_rom --paused
cpcxray gui --mcp-port 0                     # no MCP server
```

Exit codes: 0 when the window is closed; 1 if the configuration is missing (`error: no
configuration …; create a project folder with cpcxray init …`) or invalid, or if the window's
libraries are not installed (the `gui` extra).

## `cpcxray mcp`

Run the MCP server on stdin / stdout, without a window - the way Claude Code starts cpcxray
(`.mcp.json`, see [Claude Code with cpcxray](claude_code.md)).

```text
cpcxray mcp [--project-dir DIR] [--config FILE] [--machine {cpc464,flat}]
```

| Option | Default | Meaning |
|---|---|---|
| `--project-dir DIR` | the default project folder | the project folder |
| `--config FILE` | `config/machine.toml` | the machine configuration (any CPC model) |
| `--machine cpc464` | yes | the CPC of the configuration |
| `--machine flat` | | a bare Z80 with 64 KB RAM and no hardware (for testing Z80 code) |

stdout carries the MCP protocol, so messages go to stderr. The server runs until the client
disconnects. The tools: [MCP reference](mcp.md).

Exit codes: 0 when the client disconnects; 1 if the configuration is invalid or the MCP library is
not installed (the `mcp` extra).

## `cpcxray config`

Load a machine configuration and show what it contains and whether everything is there.

```text
cpcxray config [--config FILE]
```

| Option | Default | Meaning |
|---|---|---|
| `--config FILE` | `config/machine.toml` in the default project folder | the configuration to check |

Output: the configuration file and project folder, the model, every ROM slot (`ok` or
`MISSING`, the file, decoder and analysis mark), RAM ranges to analyse, drives (`ok` /
`MISSING`, read-only or writable), binary files to load, and where sjasmplus was found.

```text
$ cpcxray config --config config/cpc6128.toml
config:      /home/me/my_cpc/config/cpc6128.toml
project dir: /home/me/my_cpc
model:       CPC 6128, unpopulated ROM -> slot 0
  lower     ok       /home/me/my_cpc/roms/os6128.rom
  upper 0   ok       /home/me/my_cpc/roms/basic11_6128.rom
  upper 7   ok       /home/me/my_cpc/roms/amsdos.rom
sjasmplus:   /usr/local/bin/sjasmplus
```

Exit codes: 0 if the configuration is valid (missing files are listed, not an error); 1 if it
cannot be read or is invalid - the message names the key, e.g. `rom.upper.5.decoder: unknown
decoder 'vortx'`.

## `cpcxray source-verify`

The round-trip check without the emulator: assemble a source with sjasmplus and compare the result
byte for byte with an image - typically a generated source against the clear ROM dump.

```text
cpcxray source-verify [--start ADDRESS] source expected
```

| Argument / option | Default | Meaning |
|---|---|---|
| `source` | | the sjasmplus source, e.g. `project/amsdos.asm` |
| `expected` | | the image it must reproduce, e.g. `project/amsdos_clear.rom` |
| `--start ADDRESS` | `C000` | address of the image's first byte, hex (`C000`, `&C000`, `#C000`); `0000` for a lower ROM |

Needs [sjasmplus](https://github.com/z00m128/sjasmplus) on the PATH.

```text
$ cpcxray source-verify project/amsdos.asm project/amsdos_clear.rom
identical: 16384 bytes

$ cpcxray source-verify project/amsdos.asm project/amsdos_clear.rom
1 differing bytes (assembled 16384, expected 16384)
C070  expected 76  assembled 00
```

Exit codes: 0 if identical; 1 if bytes differ, a file cannot be read, or sjasmplus is missing or
reports errors (its messages are printed).

---

## Exit codes at a glance

| Command | 0 | 1 |
|---|---|---|
| `init` | always | - |
| `gui` | window closed | no / invalid configuration, GUI libraries missing |
| `mcp` | client disconnected | invalid configuration, MCP library missing |
| `config` | configuration valid | configuration unreadable or invalid |
| `source-verify` | identical | different, unreadable file, sjasmplus missing or failing |

`--help` and `--version` exit with 0, wrong options with 2 (argparse).
