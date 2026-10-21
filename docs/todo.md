# To do (owner's list)

Open tasks for later sessions, in the owner's words, with notes on where they apply.

1. **Paging configuration in the CPU view.** The disassembly in the emulator's CPU view shall
   show the current page configuration: RAM or ROM, which ROM when the upper ROM is paged in,
   or which RAM bank (for the 6128).
   - Where: `ui/panels/tabs.py` (CPU tab), `Session.disassemble`; the memory map knows the
     paging (`MemoryMap.visible_rom`); RAM banks come with the 6128 (design.md D-14, phase 11).

2. **Paging configuration in the Memory view.** The same for the memory view: show what is
   paged in for the displayed addresses.
   - Where: Memory tab, `Session.memory_view` (view "cpu" follows the current paging).

3. **User documentation** (how to use the tool), e.g. `docs/user_guide.md`:
   - Setup: installation (`.venv`, `pip install -e ".[dev]"`), ROM and disk images
     (`roms/`, `disk/`, `config/machine.toml`), `cpcxray config` to check them, sjasmplus.
   - Using the GUI (`cpcxray gui`): tool bar, analysis switch and indicators, drive LEDs,
     keyboard and "Type text", all debugger panels (CPU, Memory, Devices, Breakpoints, Trace,
     Bus trace, Symbols, Disks, Files).
   - All MCP features: every tool by group (machine, execution, CPU, memory, disassembly,
     breakpoints, traces, analysis and source generation, scrambled ROMs, input, screen,
     devices, symbols, disks, snapshots), with parameters and examples.
   - Claude Code MCP integration: headless server over stdio via `.mcp.json`
     (`cpcxray mcp`), attaching to a running GUI over HTTP
     (`claude mcp add --transport http cpcxray-gui http://127.0.0.1:8765/mcp`), typical
     workflows (boot, type commands, breakpoints, decode and analyse VDOS, generate and
     verify the source, work with disk images).

4. **Remaining implementation phase: PyPy** (design.md §9 phase 11, "restart on PyPy"), to see
   what performance increase it brings.
   - Run the full test suite and the CPU validation (ZEXDOC/ZEXALL, z80test) under PyPy; the
     headless emulator and MCP server on PyPy, the GUI stays on CPython (Dear PyGui).
   - Measure against today's CPython figures (headless, VDOS as analysis target):
     analysis on 1.15x real time, analysis off 2.3x; instruction trace on 0.45x; bus trace
     0.69x. GUI (CPython): analysis on 0.98x at 10 fps (0.20 of 50 fps), analysis off 1.00x at
     34-41 fps (0.68-0.83 of 50 fps).
   - Earlier quick test of the Z80 core under PyPy (phase 1): considerably faster after
     warm-up; decide from the measurements whether a native CPU / Rust port is still needed.
   - Also in phase 11: 6128 RAM banking (D-14, FR-MCH-10).

5. **Low priority, later - first get the tool running: Rust inner loop** (only if PyPy, task 4,
   is not fast enough).
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
     (SingleStepTests, ZEXDOC/ZEXALL, z80test), the analysis and VDOS milestone tests check
     the Rust observers; the byte-identical VDOS source is the end-to-end check.
