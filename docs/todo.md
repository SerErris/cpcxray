# To do (owner's list)

Open tasks for later sessions, in the owner's words, with notes on where they apply.

Order (2026-10-22): tasks that share code come one after the other, so no part is built twice.
The variable tables (1, done) are the base for the disassembly and memory view work (2, 3, 4);
the CPU and Memory tabs are changed once each (2 done, 3); the user documentation (7) is
written when the GUI and the MCP tools are stable; the Rust core (8) comes last, because it
freezes everything that runs per instruction or per bus access (breakpoints, traces,
observers).

3. **Memory view: paging configuration and known variables.** Show what is paged in for the
   displayed addresses (ROM / which ROM / RAM bank), and highlight and name the known OS and
   AMSDOS variables of task 1 next to their bytes.
   - Where: Memory tab, `Session.memory_view` (view "cpu" follows the current paging).
   - One task, so the Memory tab is reworked once.

4. **Known variables in the bus trace and the generated source.** Bus trace entries name the
   variable they access; the source generator defines the OS/AMSDOS variables a ROM uses with
   `EQU` (like the firmware entries today). Small; finishes task 1.

5. **More graphical Devices page.** Today the Devices tab is a text dump of `devices_get`.
   - Draw a framed box per device (Gate Array, CRTC, ROM select, SystemIO/PSG, keyboard, FDC
     with both drives) with its registers laid out inside.
   - Gate Array: show the 16 pens and the border as colour swatches in their actual colours
     (hardware colour → RGB via ui/palette.py), with the colour numbers; mark the selected pen;
     screen mode, ROM enables, interrupt state.
   - Further ideas: CRTC registers with names (R0-R17) and the screen start address, ROM
     select with the ROM name, the keyboard matrix as a grid of keys (held keys highlighted),
     FDC drive state with motor/activity LED and head position.
   - Where: ui/panels/tabs.py (Devices tab; Dear PyGui drawing / colour widgets); the data
     stays `Session.devices_get` (MCP parity).

6. **Full keyboard translation (character-based).** A key pressed on the PC keyboard shall give
   the character printed on it on the CPC: on the German keyboard, the key for `|` must type
   `|` on the CPC. Today the PC keys map by position (ui/host_keys.py, owner's choice in
   phase 6), so that key gives `Ü`'s position on the CPC keyboard - physically right, but
   unusable.
   - How: translate the character the PC produces into the CPC key combination that types it
     (the firmware's translation tables, as `type_text` already does: devices/key_map.py), and
     hold those matrix keys (including SHIFT/CONTROL) while the PC key is down.
   - Note: Dear PyGui reports key codes, not the characters of the active layout; the
     character has to come from the text input events (or the OS layout). Keys without a
     character (cursor keys, COPY, ESC, function keys) stay mapped by position.

7. **User documentation** (how to use the tool), e.g. `docs/user_guide.md`. After tasks 1-6,
   so it describes the final GUI and MCP tools:
   - Setup: installation (`.venv`, `pip install -e ".[dev]"`), ROM and disk images
     (`roms/`, `disk/`, `config/machine.toml`, the 464/664/6128 configurations), `cpcxray
     config` to check them, sjasmplus.
   - Using the GUI (`cpcxray gui`): tool bar, analysis switch and indicators, drive LEDs,
     keyboard and "Type text", all debugger panels (CPU, Memory, Devices, Breakpoints, Trace,
     Bus trace, Symbols, Disks, Files).
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

## Done

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
