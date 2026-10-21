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
cpcxray source-verify output/vdos211x.asm output/vdos211x_clear.rom   # round trip (D-33)
cpcxray gui                       # window, starts running (--paused: stopped at &0000); MCP over
                                  #   HTTP on 127.0.0.1:8765 (--mcp-port 0: off)
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

**Status:** Phases 0-10 done: skeleton and config loader; Z80 core (all 1.6 M SingleStepTests
pass); CPU validation (ZEXDOC/ZEXALL, z80test); disassembler, CPC RST profile, symbols, Session,
MCP server; CPC 464 booting to BASIC; renderer and GUI (real time, MCP over HTTP); keyboard;
decoding and analysis (VDOS decoded and initialised); analysis database, classifier, sjasmplus
source generator and round-trip check (VDOS source reassembles byte-identically); full debugger
(breakpoints with location qualifiers, bus trace, memory views, `.sna`, GUI panels); µPD765 FDC
with DSK/EDSK/raw images (VDOS reads the 3" CP/M disk in A: and the Vortex disk in B:).
Next: Phase 11 (later scope: 6128 banking; restart on PyPy) - only with the owner's go-ahead.

Validation runtimes on CPython (8 cores): z80test ~1 min for all variants; ZEXDOC+ZEXALL ~30 min,
dominated by the "aluop a,<b,c,...>" group (2.5 billion instructions, ~26 min on one core).

## Disk notes (phase 10)

- disk/: 464_cpm_22_vdos.dsk (3" CP/M 2.2 SYSTEM disk, A:), Vortex_init.dsk (VDOS system disk, B:),
  464_CPM_2.2_work.dsk (for later). Real images: **never write to them** - they are inserted
  read-only (`[drive.X] writable = false`, the default); use `disk_new` drafts for writes and
  `disk_save` to another file.
- disk/dsk.py: `DiskImage` (tracks → sectors with C,H,R,N, ST1/ST2, data), EDSK/DSK/raw Vortex
  loading, EDSK saving, blank formats vortex/data/system. disk/cpm.py: host-side CP/M reader
  (formats system/data/vortex, extents, AMSDOS header) - the reference for what VDOS reads.
- devices/fdc765.py: full µPD765 (read/write/format/read id/seek/...; sectors found by ID; MT;
  CPC end of cylinder ST0 &40/ST1 &80 after EOT; read-only → ST1 NW). A: 40 tracks 1 side
  (head select ignored), B: 80 tracks 2 sides; US0 only (units 2/3 alias 0/1). Log collapses
  repeats (VDOS sends SPECIFY constantly).
- VDOS turnkey: at boot it reads A:'s directory looking for HELLO.BAS/BIN: boot to Ready ~3.8 s
  with a disk in A:. Tests boot with `tests.vdos.run_until_ready`, never a fixed time.
- Typing: SHIFT/CONTROL are pressed 20 ms before and released 20 ms after the key, otherwise a
  keyboard scan in progress can see the letter without SHIFT.
- session/disks.py `DiskMixin`: disk_list/insert/eject/new/save/catalog/read_file; GUI Disks tab.

## Debugger notes (phase 9)

- debugger/breakpoints.py: kinds pc, m1, read, write, access, io_in, io_out, io, rom_select,
  interrupt; location qualifiers any/rom/lower/rom:n/ram/ram:page (FR-DBG-07). PC breakpoints
  stop before the instruction (set lookup in the run loop); all others are a bus observer that
  stops *after* the accessing instruction, the stop result carries `breakpoint` = access, address,
  value, PC, location. Writes always count as `ram` (they go to RAM under a ROM).
- debugger/bus_trace.py: ring buffer of bus accesses (FR-DBG-08). Both observers are registered
  only while needed (D-12); `load_config` moves them to the new bus. FlatMachine: PC only.
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
- ui/panels/: tabs.py (Dear PyGui tab bar: CPU, Memory, Devices, Breakpoints, Trace, Bus trace,
  Symbols, Files), text.py (formatting/parsing, tested headless). Panels only call Session
  methods; the visible tab refreshes every 5th frame; PC keys go to a focused text input, not the
  CPC. EmulationLoop: run_to(address), step_out.

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
  are data tables (VDOS copies its jumpblock to RAM).
- analysis/source_gen.py: one item per instruction / far object / header field, data merged
  into lines; labels only at line starts (else hex), firmware symbols outside via EQU.
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
- devices/fdc765.py is a µPD765 *stub* (all drives not ready): without it VDOS's result loop
  reads &FF forever and overwrites RAM. The full FDC with disk images is phase 10.
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
