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

7. **More graphical Devices page.** Today the Devices tab is a text dump of `devices_get`.
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
