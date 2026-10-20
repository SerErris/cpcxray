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
tests/                  mirrors src (cpu/, machine/, devices/, decoding/, analysis/, disasm/, mcp/)
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
cpcxray config                    # show parsed config/machine.toml, missing ROMs, sjasmplus
```

`sjasmplus` must be on the PATH (round-trip check, D-33); `tests/test_tools.py` checks it.

External CPU test vectors (not committed) live in `tests/data/external/`:

```bash
tools/fetch_test_data.sh          # SingleStepTests/z80, ~1.4 GB
python tools/run_singlestep.py    # all 1.6 M per-opcode tests in parallel (~3 min); globs: 'ed b*'
CPCXRAY_SST=all pytest tests/cpu  # same via pytest (slow); default pytest runs 25 per opcode file
```

Tests that need external data skip when it is missing.

## Conventions

- Python 3.12+, type hints on all functions; dataclasses for plain data.
- `ruff check` and `ruff format --check` must be clean; pytest for all tests.
- Every component testable in isolation and headless (NFR-04, NFR-06).
- **No ROM images in git** (NFR-07). ROM paths always come from the config; tests that need real
  ROMs must skip when the files are missing.
- Hex in CPC notation (`&C000` / `#C000`) in docs and comments; `0xC000` in Python.

## Phase rule

Implement strictly one phase of design.md §9 at a time. A phase is finished only when its
milestone tests pass together with the full suite, `ruff check` and `ruff format --check`.
Do not start the next phase without the owner's go-ahead.

**Status:** Phase 0 (skeleton, tooling, config loader) and Phase 1 (Z80 core, all 1.6 M
SingleStepTests pass) done. Next: Phase 2 (ZEXDOC/ZEXALL, raxoft z80test).

## Z80 core notes (`src/cpcxray/cpu/`)

- `z80.py`: handlers are closures built per CPU instance from the x/y/z opcode decoding; DD/FD
  tables come from the same factories with IX/IY in place of HL. Registers live in `cpu.r8`
  (indices B C D E H L F A IXH IXL IYH IYL); named properties (`cpu.hl`, `cpu.ix`, ...) and
  `registers()` / `set_registers()` for the debugger.
- `step()` returns the cost in CPC NOPs from `timing.py` (D-04). Building a `Z80` is expensive
  (~2000 closures): reuse instances in loops.
- After a bus swaps its methods (observers, D-12) call `cpu.rebind_bus()`.
