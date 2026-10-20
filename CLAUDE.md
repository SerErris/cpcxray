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
pylint src tests tools            # must rate 10/10; disables only with a stated reason
cpcxray config                    # show parsed config/machine.toml, missing ROMs, sjasmplus
```

`sjasmplus` must be on the PATH (round-trip check, D-33); `tests/test_tools.py` checks it.

External CPU test vectors (not committed) live in `tests/data/external/`:

```bash
tools/fetch_test_data.sh          # SingleStepTests/z80, ~1.4 GB
python -m tools.run_singlestep    # all 1.6 M per-opcode tests in parallel (~10 s); globs: 'ed b*'
CPCXRAY_SST=all pytest tests/cpu  # same via pytest (slow); default pytest runs 25 per opcode file
```

Tests that need external data skip when it is missing.

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

**Status:** Phase 0 (skeleton, tooling, config loader) and Phase 1 (Z80 core, all 1.6 M
SingleStepTests pass) done. Next: Phase 2 (ZEXDOC/ZEXALL, raxoft z80test).

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
