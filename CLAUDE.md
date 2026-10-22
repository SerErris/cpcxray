# CPC X-Ray (`cpcxray`)

An Amstrad CPC 464/664/6128 emulator built for reverse engineering: it runs the real firmware,
BASIC, AMSDOS and any expansion ROMs, and analyses the code the owner selects (any ROM or a RAM
range). Scrambled ROMs (e.g. the Vortex X-module scheme) are decoded on every read depending on
M1 (opcode fetch) vs. data read, and the result is recorded. Outputs: a clear ROM image, an
access map/ROM map, a reassemblable sjasmplus source, traces, a debugger GUI and an MCP server.
Software-compatible, not cycle-exact.

## Design documents

- **`docs/design.md` is authoritative**: requirements (FR-*/NFR-*), decisions **D-01..D-34**,
  architecture (§6), test strategy (§8), implementation plan (§9). Read the relevant sections
  before working on a component.
- `docs/vortex_decrypt.md` (scrambling rule) and `docs/cpc_rst.md` (RST conventions) are
  referenced specifications. Where they differ from design.md, **design.md wins** (§5.5, §5.6).
- Reference decision/requirement IDs (e.g. `# D-11`) in code comments where they explain why.

## Layout (src layout, design.md §9.1)

```
config/machine.toml     machine configuration (§6.14); ROM paths relative to the project root
roms/                   ROM images — local only, never committed
disk/                   disk images (owner's real images, read-only) — local only, never committed
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
cpcxray source-verify output/rom.asm output/rom_clear.rom   # round trip (D-33)
cpcxray gui                       # window, starts running (--paused: stopped at &0000); MCP over
                                  #   HTTP on 127.0.0.1:8765 (--mcp-port 0: off)
CPCXRAY_UPDATE_GOLDEN=1 pytest tests/disasm   # regenerate the disassembler golden listing
python -m tools.extract_firmware_symbols      # firmware names (464/664/6128); then ruff format
python -m tools.extract_memory_variables      # OS/AMSDOS variables (docs/memory.md); ruff format
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

**Status:** Phases 0-11 done: skeleton and config loader; Z80 core (all 1.6 M SingleStepTests
pass); CPU validation (ZEXDOC/ZEXALL, z80test); disassembler, CPC RST profile, symbols, Session,
MCP server; CPC 464 booting to BASIC; renderer and GUI (real time, MCP over HTTP); keyboard;
decoding and analysis of scrambled ROMs; analysis database, classifier, sjasmplus source
generator and round-trip check; full debugger (breakpoints with location qualifiers, bus trace,
memory views, `.sna`, GUI panels); µPD765 FDC with DSK/EDSK/raw images (AMSDOS reads and writes
the 3" disk in A:; Vortex 80-track format in B:); CPC 6128 with RAM banking and CPC 664 (both
boot to BASIC 1.1); firmware names per machine. Tests run on the plain machine ROMs only:
config/test_cpc464.toml / test_cpc664.toml / test_cpc6128.toml (OS, BASIC, AMSDOS) via
tests/machines.py. Open work: docs/todo.md - only with the owner's go-ahead.

Validation runtimes on CPython (8 cores): z80test ~1 min for all variants; ZEXDOC+ZEXALL ~30 min,
dominated by the "aluop a,<b,c,...>" group (2.5 billion instructions, ~26 min on one core).

## Disk notes (phase 10)

- disk/: 464_cpm_22_orig.dsk (3" CP/M 2.2 SYSTEM disk, A: in config/machine.toml and the
  tests), vortex_test.dsk (a fixed Vortex-format image for the tests). Real images: **never
  write to them** - they are inserted
  read-only (`[drive.X] writable = false`, the default); use `disk_new` drafts for writes and
  `disk_save` to another file.
- disk/dsk.py: `DiskImage` (tracks → sectors with C,H,R,N, ST1/ST2, data), EDSK/DSK/raw Vortex
  loading, EDSK saving, blank formats vortex/data/system. disk/cpm.py: host-side CP/M reader
  (formats system/data/vortex, extents, AMSDOS header) - the reference for what disk ROMs read.
- devices/fdc765.py: full µPD765 (read/write/format/read id/seek/...; sectors found by ID; MT;
  CPC end of cylinder ST0 &40/ST1 &80 after EOT; read-only → ST1 NW). A: 40 tracks 1 side
  (head select ignored), B: 80 tracks 2 sides; US0 only (units 2/3 alias 0/1). Log collapses
  repeats (a disk ROM may send SPECIFY constantly).
- Tests boot with `tests.machines.run_until_ready` (or `boot`), never a fixed time.
- Typing: SHIFT/CONTROL are pressed 20 ms before and released 20 ms after the key, otherwise a
  keyboard scan in progress can see the letter without SHIFT.
- session/disks.py `DiskMixin`: disk_list/insert/eject/new/save/catalog/read_file; GUI Disks tab.

## Debugger notes (phase 9)

- debugger/breakpoints.py: kinds pc, m1, read, write, access, io_in, io_out, io, rom_select,
  interrupt; location qualifiers any/rom/lower/rom:n/ram/ram:page (FR-DBG-07). PC breakpoints
  stop before the instruction (set lookup in the run loop); all others are a bus observer that
  stops *after* the accessing instruction, the stop result carries `breakpoint` = access, address,
  value, PC, location. Writes always count as `ram` (they go to RAM under a ROM).
- debugger/bus_trace.py: ring buffer of bus accesses (FR-DBG-08); `bus_trace_read` appends the
  variable of a RAM read/write (looked up on reading, cached per address). Both observers are
  registered only while needed (D-12); `load_config` moves them to the new bus. FlatMachine: PC
  only.
- session/debugger.py `DebuggerMixin`: breakpoint_*, bus_trace_*, memory_view (cpu, ram,
  lower/romN, raw:romN, shadow:romN), sna_load/sna_save. machine/sna.py: load v1-v3 (MEM0 RLE
  chunks), save v3 with a plain 64 KB dump; SystemIO now stores PSG register writes for it.
- Performance (owner's rule): the instruction trace is off for free runs unless switched on
  ("Trace runs" toggle / `trace_start`; ~5x slower); step/step_over/step_out always record into
  the ring buffer; a trace file is written only on request. Panels are frozen while running and
  refresh when stopped. The status line shows TRACE / BUS TRACE while one is on. Biggest fixed
  cost: the analysis bus observers (1.0x vs 2.3x without).
- Performance (owner's rule): the instruction trace is off for free runs unless switched on
  ("Trace runs" toggle / `trace_start`); step/step_over/step_out always record into the ring
  buffer; a trace file is written only on request. The trace captures bytes/registers only and
  disassembles on reading. Panels are frozen while running. Status line: speed (emulated/wall
  time), fps and fps/50, TRACE / BUS TRACE. Bus observers declare `handlers` (per access kind,
  called directly) or `cycles`; unwatched kinds use the fast bus methods. Biggest fixed cost:
  the analysis observers (headless 1.15x vs 2.3x without): `analysis_enable(False)` / the
  GUI's "Analysis" button takes them off the bus (collected data kept, decoding and access map
  continue). GUI indicators: ANALYSIS, one activity LED per drive (shared motor + drive
  addressed last, `Fdc765.drive_lights`).
- Paging (FR-DBG-12): `paging_get` (session/debugger.py) describes each 16 KB quarter: lower ROM,
  upper ROM n (file name) or RAM block (6128 banking), plus `ram_block` (writes) and the 6128's
  RAM configuration; `disassemble` returns `paging` for the quarters of its lines. CPU tab:
  paging lines above the disassembly, `; ----` separators per quarter (text.py `with_paging`).
- Memory view: `memory_view` returns `paging` (what the view shows in its range) and
  `variables` (known variables where the view shows RAM at their CPU address: "ram", "cpu"
  outside ROM quarters, "ram:N" where block N is mapped; ROM views none; `area` flags pack
  areas). Memory tab: one text item per byte, coloured per variable (text.py `memory_layout`,
  tabs.py `VARIABLE_COLOURS`), area-only bytes grey, colour-matched legend below.
- ui/panels/: tabs.py (`DebuggerPanels`: the tab bar), one module per tab (cpu, memory,
  devices, breakpoints, trace, bus_trace, symbols, disks, files) with a `Tab` subclass
  (base.py: `build`/`refresh`, `call` reports into the tab's message line, shared
  `PanelContext` with one `items` dict for all tabs); colours.py (the colour table of the
  agreed mock-ups: palette, badge styles, symbol type colours), widgets.py (`Styles` themes,
  section, badge, swatch, `Chips`, path field with ▾; DejaVu Sans Mono from the system fonts),
  scroll_list.py (`ListState` headless + `ScrollList`: clipper, keys while hovered, select vs
  activate), file_selector.py (own modal dialog: name/size/date, filters, "Select"), text.py
  (formatting/parsing, tested headless). Panels only call Session methods; the visible tab
  refreshes every 5th frame; PC keys go to a focused text input, a hovered list or an open file
  selector, not the CPC. EmulationLoop: run_to(address), step_out.

## Analysis database and source notes (phase 8)

- analysis/database.py `TargetDatabase`: kind + source (owner > observed > static) per byte,
  observations by CPU address, owner overrides. Kinds are rebuilt from the facts (overrides,
  access map, target flags, observations) by analysis/classifier.py before every use, so only
  facts are authoritative. File: project/<rom>.analysis.json (also target flags, relocations),
  merged at session start, saved by `analysis_save` and on GUI exit.
- analysis/observers.py `ExecutionObserver` (with the provenance observer): instruction starts
  executed directly in targets, RST 1/2/3/5, far address objects, where SIDE/FAR CALLs arrived.
- analysis/classifier.py `TargetView`: clear image, access map, relocated blocks (RAM
  coordinates). Never-read scrambled bytes: opcode reads see raw, data reads decoded;
  `final_image` per fill policy ("static" keeps opcode bytes of classified code raw). Static
  descent from observed code and the ROM header jumpblock; never into &FF or non-canonical code.
- Only relocated blocks that ran in RAM (or manual ones) become DISP blocks; unexecuted copies
  are data tables (e.g. a jumpblock copied to RAM). Of those, only position-dependent blocks
  (relative branch inside, or an absolute reference into the RAM copy) get DISP; the others stay
  at their ROM address with a comment (`TargetView.ram_copies`, classifier.position_dependent).
- RAM is mapped back to a ROM (EXEC_VIA_RAM, RSTs, far objects) only through trusted tags:
  exact, or value-matched inside a copied run (`AnalysisObserver.trusted_origin`).
- Owner symbols (project/annotations.txt): an owner label replaces generated names incl.
  `rsx_<name>`; owner comments also on data lines (a commented data byte starts a line).
- analysis/source_gen.py: one item per instruction / far object / header field, data merged
  into lines; labels only at line starts (else hex), firmware symbols outside via EQU; other
  outside references by known variables (`LABEL+n`, `variables=` from
  `Session._known_variables`, own EQU block); immediates of unknown role only at a variable's
  first byte. `_equ` keeps a space before EQU for labels of 24+ characters.
  analysis/verify.py runs sjasmplus (`--raw`, temp dir). CLI: `cpcxray source-verify SRC BIN`.
- Outputs: output/<rom>.asm (`source_generate`), checked by `source_verify` (fill policy read
  from the source's header line).

## Decoding and analysis notes (phase 7)

- decoding/vortex.py: the XOR rule; machine/memory.py `RomSlot` applies it to non-M1 reads when
  `decode_enabled` (D-11) and records every read in its `ShadowRom` (access map, clear values,
  first PC, hits). `peek(as_m1=...)` reads without recording: use it in debug views.
- The disassembler takes `read_opcode` for M1 bytes (raw in a scrambled ROM) and `read_byte`
  for operands (decoded); Session passes machine.peek_opcode / machine.peek.
- Access map: project/<rom>.m1map (16 KB, D-19), merged at session start, saved on GUI exit and
  with `m1map_save`. Dumps/maps go to output/<rom>_clear.rom, _rom_map.html/.txt, _coverage.csv.
  Fill policy "static" comes from the phase 8 classifier.
- analysis/provenance.py `AnalysisObserver` (bus observer, only while targets exist): RAM tags
  (exact via LDIR/LDDR, probable via value), EXEC_VIA_RAM/INSN_START on the origin bytes,
  relocated blocks, RAM-target capture. It slows emulation to ~1.2x real time.
- Session analysis tools live in session/analysis.py (`AnalysisMixin`); errors in
  session/errors.py (shared, avoids an import cycle).

## Keyboard notes (phase 6)

- devices/key_map.py reads the firmware's key translation tables (normal/shift/control, 80 keys
  each) from the installed OS ROM, found by the sequence "09oilkm,"; key = row * 8 + bit. Key
  names and characters therefore always match the ROM (no hand-typed key table).
- devices/keyboard.py: keys held per source ("api", "host", "typing"), combined; typing holds
  each character's keys 40 ms and releases 40 ms (the firmware scans once per frame).
- `type_text(text, wait=True)` runs the machine until typed; `wait=False` only queues (GUI).
- GUI (ui/host_keys.py): letters/digits by label, symbols by CPC position (owner: German
  layout), "Type text" box for exact characters. Dear PyGui 2.3 quirks: mvKey_Plus/Colon/Quote/
  Tilde have wrong (ASCII) values - use ImGui codes; only one dpg context per process.

## Renderer and GUI notes (phase 5)

- ui/renderer.py: snapshot → RGBA 768 x 272 (mode 2 pixels x scan lines, border included),
  numpy, no GUI dependency; `double_lines` for the 768 x 544 display. ui/palette.py computes
  RGB from firmware colour numbers; the firmware→hardware table is checked against the OS ROM.
- ui/emulation.py `EmulationLoop` (no Dear PyGui): per frame it runs queued commands (MCP over
  HTTP, D-29) and a real-time slice (or max-speed slices). A breakpoint or an MCP `pause` stops
  the free run (`Session.pause_generation`).
- ui/app.py: Dear PyGui window. Never call a dpg function before `dpg.create_context()` - it
  crashes the process (segfault). Tests open a real window under WSLg (skipped without display).
- Claude attaches to a running GUI with
  `claude mcp add --transport http cpcxray-gui http://127.0.0.1:8765/mcp` (not in .mcp.json,
  because it fails while no GUI is running). `screen_image` returns MCP image content.

## CPC 664 / 6128 notes (phase 11)

- Models (`[machine] model`, FR-MCH-12): "464", "664", "6128"; `CPC464.model` "cpc664" etc.,
  `firmware_model` "664" selects the firmware names. The 664 is a 464 architecture (64 KB, no
  banking) with firmware V1.1: config/cpc664.toml / test_cpc664.toml (os664.rom,
  basic11_664.rom, amsdos.rom). Locomotive BASIC jumps into the lower ROM directly (RST 1 to
  fixed addresses), so a BASIC 1.1 only works with the OS it was built with: the 6128's
  BASIC 1.1 (basic11_6128.rom) loops on "Syntax error" under os664.rom.
- Firmware names: tools/extract_firmware_symbols.py reads docs/firmware.md only (kernel, main
  jumpblock 0-201 with availability, indirections; section 7: TEXT INPUT and the maths
  jumpblock, not in SOFT 968, one address per machine: 464 from &BD3A, 664 from &BD5B, 6128
  from &BD5E) → FIRMWARE_464/664/6128. tests/machine/test_firmware_jumpblocks.py checks the
  names against the jumpblocks of the booted firmware.

- `model = "6128"` (config/cpc6128.toml: os6128.rom, basic11_6128.rom; AMSDOS as on the 464) makes
  `CPC464` a 6128 (`model` "cpc6128"): `MemoryMap(ram_blocks=8)`. Gate Array function 11
  (`OUT &7Fxx`, &C0 + o) sets the configuration; a 464 ignores it; reset restores 0.
- machine/memory.py: `ram` is the CPU's 64 KB view; a configuration change swaps the changed
  pages with the block store (D-14), so read/write paths are as on the 464. `ram_block_at`,
  `block(n)`, `set_block`, `base_ram()` (blocks 0-3: renderer and `screen_text`, the CRTC ignores
  banking). Breakpoint locations / memory views: `ram:<block>` (464: block 0-3 by address).
- `.sna`: CPC type per model (464: 0, 664: 1, 6128: 2); a 6128 saves 128 KB + RAM
  configuration; MEM0/MEM1 chunks load. Provenance and RAM analysis targets key on CPU
  addresses and do not follow bank switches.

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
- Symbols (debugger/symbols.py): firmware names preloaded per machine (`SymbolTable(model=)`,
  `set_firmware` on `load_config`) from debugger/firmware_symbols.py
  (generated); user symbols in project/annotations.txt (`[scope:]address label ; comment`).
- Variables (FR-DBG-11, D-34; debugger/variables.py `VariableMap`): firmware and AMSDOS RAM
  variables from docs/memory.md (generated debugger/variable_tables.py), sized, one address per
  machine, pack areas (`*_WORKSPACE`) for unknown bytes; lookup gives `NAME+offset`, smallest
  first. AMSDOS only when installed: base from &BE7D, valid if its RSX block is in the ROM chain.
  Session: `address_explain`, `variable_list`; `_operand_label` (symbol label, else variable)
  names disassembly/trace operands; labels of variables work as addresses; a read/write/access
  breakpoint on a variable name without `end` covers it; stops and breakpoints carry
  `variable`. The 664 column was derived by aligning os6128/os664 code
  (tests/machine/test_memory_variables.py repeats it, also against the 464 column).

## Z80 core notes (`src/cpcxray/cpu/`)

- **CPython only**: PyPy was tried in phase 11 and dropped (owner decision 2026-10-21,
  design.md §7); no PyPy environments or PyPy-specific code.
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
