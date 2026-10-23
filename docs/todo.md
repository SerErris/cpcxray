# To do (owner's list)

Open tasks for later sessions, in the owner's words, with notes on where they apply.

Order (2026-10-22): tasks that share code come one after the other, so no part is built twice.
The variable tables (1) were the base for the disassembly and memory view work (2, 3, 4), the
GUI tabs (5) and the keyboard by character (6), all done; the user documentation (7) is
written now that the GUI and the MCP tools are stable; the Rust core (8) comes last, because
it freezes everything that runs per instruction or per bus access (breakpoints, traces,
observers).

7. **User documentation** (how to use the tool), e.g. `docs/user_guide.md`. After tasks 1-6,
   so it describes the final GUI and MCP tools:
   - Setup: installation (`.venv`, `pip install -e ".[dev]"`), ROM and disk images
     (`roms/`, `disk/`, `config/machine.toml`, the 464/664/6128 configurations), `cpcxray
     config` to check them, sjasmplus.
   - Using the GUI (`cpcxray gui`): tool bar, analysis switch and indicators, drive LEDs,
     keyboard and "Type text", all debugger panels (CPU, Memory, Devices, Breakpoints, Trace,
     Bus trace, Symbols, Disks, Project).
   - All MCP features: every tool by group (machine, execution, CPU, memory, disassembly,
     breakpoints, traces, analysis and source generation, scrambled ROMs, input, screen,
     devices, symbols, disks, snapshots), with parameters and examples.
   - Claude Code MCP integration: headless server over stdio via `.mcp.json`
     (`cpcxray mcp`), attaching to a running GUI over HTTP
     (`claude mcp add --transport http cpcxray-gui http://127.0.0.1:8765/mcp`), typical
     workflows (boot, type commands, breakpoints, decode and analyse a scrambled ROM, generate
     and verify the source, work with disk images).

8. **Low priority, later - first get the tool running: Rust inner loop** (only if CPython speed
   becomes a real blocker; PyPy was tried and dropped, 2026-10-21). Last: it moves the
   per-access code (breakpoints, traces, observers) to Rust, so the features that change that
   code (tasks 1 and 4) must be finished first.
   - Today's CPython figures (headless, a scrambled 16 KB ROM as analysis target): analysis on
     1.15x real time, analysis off 2.3x; instruction trace on 0.45x; bus trace 0.69x. GUI:
     analysis on 0.98x at 10 fps (0.20 of 50 fps), analysis off 1.00x at 34-41 fps (0.68-0.83
     of 50 fps).
   - Cut: everything that runs per instruction or per bus access goes to Rust as one unit -
     Z80 core; memory map, ROM paging, Vortex decoder, shadow ROM and access map; the
     provenance and execution observers; memory breakpoints, bus trace and the instruction
     trace capture (else switching them on brings the per-access Python calls back).
   - Stays in Python: session, MCP server, GUI; analysis database, classifier, source
     generator, round-trip check; disk images, CP/M reader, symbols, formatting; device logic
     reached through I/O (FDC, keyboard, Gate Array registers) via callbacks (I/O is rare).
   - Why not the analysis alone in Rust: the cost is the per-access call overhead, not the
     analysis logic; a Python CPU calling Rust per access would cost about the same.
   - Python reads the collected data from Rust as shared memory arrays (no copies); binding
     with PyO3. Expected 50-100x today's CPython speed, analysis always on.
   - Verification: the existing tests run against the Rust core through the binding
     (SingleStepTests, ZEXDOC/ZEXALL, z80test), the analysis and milestone tests check
     the Rust observers; a byte-identical generated source is the end-to-end check.

## Cleanup

1. **Need a machine toml for the CPC 464: `cpc464.toml`** (2026-10-23). The project folder has
   `cpc664.toml` and `cpc6128.toml` but the 464 only as `machine.toml`. Agreed approach: keep
   `machine.toml` as "your machine" (opened by default, changed in the Project tab - e.g. with
   VDOS added) and add `cpc464.toml` beside the 664 and 6128 files as the plain standard 464.
   Applies to `cpcxray init` / src/cpcxray/templates/, the repository's config/, the install
   and user guides (project folder table) and the CLI reference; `machine.toml` starts as a
   copy of `cpc464.toml`.

## Bugs

(none open)

## Done

- Bug: no drop-down (combo) in the window opened (found 2026-10-23 in a fresh 0.9.0 install;
  e.g. the Project tab's slot for a new ROM, the Memory tab's view). Cause: the keyboard focus
  rule of task 6 gave the focus back to the hidden key input on the frame after the click,
  which closed the combo's list. Fixed: ui/host_keys.py `ComboWatch` follows the clicks on
  combos and keeps the keys with the window while a list is open; checked with real (XTest)
  clicks on the Memory view, the breakpoint location, the trace format and the new ROM slot
  (slot 5), and the keys go back to the CPC after the choice; headless tests in
  tests/ui/test_host_keys.py.
- Task 6, PC keyboard by character (2026-10-23, design.md D-40): the character the PC's layout
  produces (AltGr+8 = `[` on a German keyboard) is typed on the CPC with the combination from
  the firmware's tables, held while the PC key is down (ui/host_keys.py); keys without a
  character by function, Ctrl + letter, Shift/Ctrl alone; characters the CPC lacks are named
  in the status line. The matrix analysis: 10 rows × 8 bits, the ROM tables cover every
  printable ASCII character.

- Task 5, the GUI tabs (2026-10-22/23, design.md D-35..D-39; layouts agreed on the design canvas,
  described in each tab module's docstring): shared foundation (colours.py, widgets.py `Styles`,
  `Chips`, sections with equal gaps, scroll lists with keys, own file selector); Devices
  (graphical Gate Array, CRTC, ROM select, PPI, FDC, keyboard matrix; live once a second); CPU
  (register table, flags, paging tiles, disassembly coloured by symbol type, stack with its top
  from SP loads, D-36); Memory (page grid, variable table, Follow CPU); Breakpoints (type
  chips, last stop); Trace (call depth, changed registers, step accesses, gaps); Bus trace
  (grouped per instruction, roles and meanings, type filter, steps always recorded, gaps);
  Symbols (six symbol types, own variables, search with counts, detail, annotation file,
  D-35); Disks (drive chips and card, catalogue and file detail with load/entry addresses and
  code); Project (machine summary and editor writing machine.toml, `[[load]]` files loaded on
  command with a reminder light, analysis phases Collect / Decode / Save / Generate / Verify,
  snapshot; source and exports in project/, D-37, D-38). The GUI's decode switch was dropped
  (D-11).

- Task 4, variables in the bus trace and the source (FR-DBG-11): bus trace lines end with the
  variable a RAM read/write touched; the source generator names outside references by known
  variables (`LABEL+n`) with an EQU block (the AMSDOS source still reassembles byte for byte).
- Task 3, memory view (FR-DBG-11, -12): `memory_view` reports what the view shows (paging)
  and the known variables in its range; the Memory tab shows the paging, colours each
  variable's bytes (area-only bytes grey) and lists them in a colour-matched legend.
- Task 2, paging in the CPU view (FR-DBG-12): `paging_get` (ROM or RAM block per 16 KB quarter,
  file names, 6128 RAM configuration), `disassemble` reports the quarters of its lines; the CPU
  tab shows the paging and marks each quarter in the disassembly. Task 3 can reuse
  `paging_get` for the memory view.
- Task 1, known OS and AMSDOS variables (FR-DBG-11, D-34): docs/memory.md (firmware variables
  per machine from Taylor/Defoe and Bread80, the 664 column derived from the ROM code; AMSDOS
  from CPCWiki) → debugger/variable_tables.py (tools/extract_memory_variables.py);
  debugger/variables.py (lookup `NAME+offset`, AMSDOS base from &BE7D checked against the ROM
  chain); `address_explain`, `variable_list`, variable names in disassembly/trace operands and
  as addresses, breakpoints covering a variable and naming it. Tests: tables, lookups, values
  after boot and BASIC commands on 464/664/6128, ROM-code alignment of the 664 and 464 columns.
- Phase 11: 6128 RAM banking (design.md §9, D-14, FR-MCH-10; config/cpc6128.toml). Firmware
  names per machine (464/664/6128, D-26, incl. TEXT INPUT and the maths jumpblock from
  docs/firmware.md section 7) and the 664 model (FR-MCH-12, with its own BASIC 1.1:
  basic11_664.rom / basic11_6128.rom); `.sna` CPC type per model (0/1/2), AMSDOS disk catalogue
  tested on 664 and 6128.
