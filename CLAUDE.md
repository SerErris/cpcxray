# CPC X-Ray (`cpcxray`)

An Amstrad CPC 464 emulator built for reverse engineering. Its main job is to descramble and
disassemble the **Vortex X-Module ROM (VDOS)**: the emulator runs the real firmware, BASIC,
AMSDOS and VDOS ROMs, decodes the scrambled ROM on every read depending on M1 (opcode fetch) vs.
data read, and records the result. Outputs: a clear ROM image, an access map/ROM map, a
reassemblable sjasmplus source, traces, a debugger GUI and an MCP server. Software-compatible,
not cycle-exact.

## Design documents

- **`docs/design.md` is authoritative**: requirements (FR-*/NFR-*), decisions **D-01..D-33**,
  architecture (§6), test strategy (§8), implementation plan (§9). Read the relevant sections
  before working on a component.
- `docs/vortex_decrypt.md` (scrambling rule) and `docs/cpc_rst.md` (RST conventions) are
  referenced specifications. Where they differ from design.md, **design.md wins** (§5.5, §5.6).
- Reference decision/requirement IDs (e.g. `# D-11`) in code comments where they explain why.

## Layout (src layout, design.md §9.1)

```
config/machine.toml     machine configuration (§6.14); ROM paths relative to the project root
roms/                   ROM images — local only, never committed
output/                 generated dumps, maps, sources, traces — local only
src/cpcxray/
  cli.py                console script `cpcxray`
  cpu/                  Z80 core, tables, timing, disassembler
  machine/              bus, memory map, CPC 464, config loader (config.py)
  devices/              gate array, CRTC, SystemIO, keyboard, ROM select, FDC
  decoding/             vortex decoder, shadow ROM, M1 access map, exports
  analysis/             targets, provenance, CPC profile, database, source generator, trace
  disk/                 disk images
  debugger/             debugger, breakpoints, symbols, observers
  session/              Session façade (the only API to the machine, D-27)
  mcp_server/           MCP server, tools map 1:1 onto Session methods
  ui/                   Dear PyGui app, renderer, panels
tests/                  mirrors src (cpu/, machine/, devices/, decoding/, analysis/, disasm/,
                        session/, mcp_server/ - not mcp/, which would shadow the mcp package)
tools/                  helper scripts
```

## Commands

Use the existing virtualenv in `.venv` with pip. **Do not use uv.**

```bash
source .venv/bin/activate
pip install -e ".[dev]"          # extras: dev (pytest, ruff), gui (dearpygui, numpy), mcp (mcp)
pytest                            # all tests
ruff check                        # lint
ruff format                       # format (Markdown is excluded)
pylint src tests tools            # must rate 10/10; disables only with a stated reason
cpcxray config                    # show parsed config/machine.toml, missing ROMs, sjasmplus
cpcxray mcp                       # headless MCP server on stdio (Claude Code: .mcp.json)
CPCXRAY_UPDATE_GOLDEN=1 pytest tests/disasm   # regenerate the disassembler golden listing
python -m tools.extract_firmware_symbols      # regenerate the 464 firmware jumpblock names
```

`sjasmplus` must be on the PATH (round-trip check, D-33); `tests/test_tools.py` checks it.

External CPU test programs (not committed, pinned with SHA-256) live in `tests/data/external/`:
SingleStepTests/z80 (~1.4 GB), ZEXDOC/ZEXALL (agn453/ZEXALL), raxoft z80test 1.2a.

```bash
tools/fetch_test_data.sh               # download / verify all of them
python -m tools.run_singlestep         # 1.6 M per-opcode tests in parallel (~10 s); globs: 'ed b*'
python -m tools.run_cpu_validation     # ZEXDOC, ZEXALL, 6 z80test variants (~30 min, background)
python -m tools.run_cpu_validation --only zexall --group daa   # a single program / group
CPCXRAY_SST=all pytest tests/cpu       # all SingleStepTests via pytest (default: 25 per file)
pytest -m slow                         # the complete ZEX / z80test runs via pytest (sequential)
```

Tests that need external data skip when it is missing. Tests marked `slow` are excluded by
default (`addopts` in pyproject.toml). Harnesses: `tests/cpu/singlestep.py` (JSON vectors) and
`tests/cpu/exercisers.py` (CP/M BDOS and Spectrum ROM print traps on a flat 64 KB bus).

## Conventions

- Python 3.12+, type hints on all functions; dataclasses for plain data.
- **Every module, class and function has a docstring** (Google style: summary line, then
  Args/Returns/Raises where useful), including private helpers, test functions and the
  nested opcode-handler closures. Ruff's `D` rules enforce it for public items; keep the
  rest documented by hand. Docstrings explain behaviour and the "why" (bus order, flags,
  MEMPTR, design decision IDs), not the obvious.
- `ruff check` and `ruff format --check` must be clean; pytest for all tests.
- Every component testable in isolation and headless (NFR-04, NFR-06).
- **No ROM images in git** (NFR-07). ROM paths always come from the config; tests that need real
  ROMs must skip when the files are missing.
- Hex in CPC notation (`&C000` / `#C000`) in docs and comments; `0xC000` in Python.

## Phase rule

Implement strictly one phase of design.md §9 at a time. A phase is finished only when its
milestone tests pass together with the full suite, `ruff check` and `ruff format --check`.
Do not start the next phase without the owner's go-ahead.

**Status:** Phases 0-4 done: skeleton and config loader; Z80 core (all 1.6 M SingleStepTests
pass); CPU validation (ZEXDOC/ZEXALL, z80test); disassembler, CPC RST profile, symbols, Session,
MCP server; CPC 464 machine booting to "BASIC 1.0 / Ready" (OS, BASIC, AMSDOS). Next: Phase 5
(renderer and Dear PyGui shell). VDOS (decoder slot) is skipped until phase 7 (owner decision).

Validation runtimes on CPython (8 cores): z80test ~1 min for all variants; ZEXDOC+ZEXALL ~30 min,
dominated by the "aluop a,<b,c,...>" group (2.5 billion instructions, ~26 min on one core).

## CPC 464 notes (phase 4)

- machine/cpc464.py wires memory map, devices and bus. Power-on clears RAM and installs ROMs;
  `reset()` resets the devices first (Gate Array: mode 1, lower ROM on, upper ROM off -
  researched: MAME and the OS ROM's first instruction `ld bc,#7F89 / out (c),c`), the Z80 last.
- Devices decode their own port bits; the bus broadcasts OUT and ANDs IN values (D-08).
  Write-only devices return `NOT_DRIVEN` from `io_in`. Bus observers swap in observed methods.
- 300 Hz interrupt and VSYNC per D-05; port B constant &1E confirmed by the boot.
- `cpcxray mcp` drives a CPC 464 from config/machine.toml by default (`--machine flat` for the
  bare Z80). `screen_text` reads text from screen RAM using the ROM font (§5.4).

## Session, debugger and MCP notes (phase 3)

- `Session` (src/cpcxray/session/session.py) is the only API (D-27). Every public method is an
  MCP tool with the same name and signature, generated in mcp_server/server.py; adding a public
  method adds a tool. Methods return dicts (a list would become several MCP content blocks),
  raise `SessionError` for bad requests (shown to the MCP client), take addresses as int, hex
  string or label, and are bounded (limits + wall-clock timeout, FR-MCP-04).
- Threads (D-29): `SessionWorker` runs the session in its own thread and executes commands from
  a queue; only `pause` bypasses the queue. The GUI (phase 5) will call `run_pending()` instead.
- The phase 3 machine is `FlatMachine` (Z80 + 64 KB RAM) behind the `Machine` protocol in
  machine/flat.py; the CPC 464 implements the same protocol (CPC RST profile, symbol scopes).
- Disassembler (cpu/disasm.py): sjasmplus syntax, golden test reassembles every opcode form
  with sjasmplus; non-canonical encodings flagged (D-32). RSTs via a profile (D-30,
  analysis/cpc_profile.py) with inline length and flow (call-like RST 2/3, jump-like 1/5).
- Symbols (debugger/symbols.py): firmware names preloaded from debugger/firmware_symbols.py
  (generated); user symbols in project/annotations.txt (`[scope:]address label ; comment`).

## Z80 core notes (`src/cpcxray/cpu/`)

- **CPython only** until the final phase: PyPy is deferred to phase 11 (design.md §7, §9), so
  don't add PyPy environments or PyPy-specific code before then.
- **Readability over speed**: the Python code is the reference for a later Rust port. Use long,
  descriptive names that say what a variable holds (`displacement`, `target_address`), no
  cryptic micro-optimisations. Hardware register names (`a`, `hl`, `wz`, `i`, `r`) stay.
- `z80.py`: handlers are closures built per CPU instance by decoding the opcode bit fields
  (`group`, `bits_5_3`, `bits_2_0`); DD/FD tables come from the same factories with IX/IY in
  place of HL. 8-bit registers live in the list `cpu.byte_registers` (indices B C D E H L F A
  IXH IXL IYH IYL); named properties (`cpu.hl`, `cpu.ix`, ...) and `registers()` /
  `set_registers()` serve the debugger.
- `step()` returns the cost in CPC NOPs from `timing.py` (D-04), verified against
  the CPC timing cheat sheet (https://www.cpcwiki.eu/imgs/b/b4/Z80_CPC_Timings_cheat_sheet.20230709.pdf;
  third-party, not committed). Building a `Z80` is expensive (~2000 closures): reuse
  instances in loops.
- Handlers call `cpu.bus.read(...)` etc. on every access (no cached methods), so a bus may swap
  in observed variants of its methods (D-12) at any time.
