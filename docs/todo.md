# To do (owner's list)

Open tasks for later sessions, in the owner's words, with notes on where they apply.

Order (2026-10-22): tasks that share code come one after the other, so no part is built twice.
The variable tables (1) were the base for the disassembly and memory view work (2, 3, 4);
the CPU and Memory tabs are changed once each (2, 3 done); the user documentation (7) is
written when the GUI and the MCP tools are stable; the Rust core (8) comes last, because it
freezes everything that runs per instruction or per bus access (breakpoints, traces,
observers).

5. **More graphical Devices page.** Today the Devices tab is a text dump of `devices_get`.
   Layout agreed with the owner on 2026-10-22 (mock-up: design canvas "cpcxray GUI tabs",
   artboard "B: two columns"):
   - Two columns of equal total height (fixed heights in Dear PyGui so the bottom edges line
     up). Left: Gate Array, ROM select, PPI 8255 (ROM select and PPI share the height left
     under the Gate Array). Right: CRTC.
   - Gate Array: header "mode n · colours fw/hw (hex)"; a line "[swatch] border fw/hw" with a
     swatch the size of a pen swatch (e.g. `border 2/15`); then the 16 pens in a 4 × 4 grid,
     each a swatch + `fw/hw` (firmware colour decimal, hardware colour hex, e.g. `20/13`); pens
     the mode does not use with grey numbers (their swatch keeps the true colour: a faded
     swatch read as another colour, owner 2026-10-22); the pen selected for ink writes ringed yellow; badges for
     lower/upper ROM enable, RAM configuration, pen select, interrupt timing (µs, R52).
   - ROM select: the selected number and ROM file, whether it is paged in, installed slots.
   - PPI: ports A, B, C with a short meaning each (port A: keyboard data via PSG; B: VSYNC,
     50 Hz, brand; C: keyboard line, motor, PSG function). No PSG box: the PSG is emulated
     only as far as the keyboard needs.
   - CRTC: R0-R17 with names and values, the selected register highlighted; footer with the
     screen start, characters per line × lines, scan lines per character.
   - Full width below, in this order: FDC (motor LED, selected drive, MSR meaning; one box per
     drive with activity LED, disk file, read-only/modified, head position bar "cylinder n /
     tracks"; the last FDC commands), then the keyboard matrix (10 lines × 8 bits labelled
     with the key names from the ROM key map, held keys highlighted, each line's value).
   - Where: ui/panels/tabs.py (Devices tab; Dear PyGui drawing / colour widgets); the data
     stays `Session.devices_get` (MCP parity), extended where the layout needs more (ROM file
     names, key names, interpreted bits).

5b. **CPU tab layout.** Agreed with the owner on 2026-10-22 (artboard "CPU 3: combined"):
   - Top box: registers as a table AF/BC/DE/HL main and alternate as byte pairs (`0D 9B`),
     IX, IY, SP, PC, WZ beside it; flags as eight letter chips (lit when set); I, R, IM,
     IFF1/2, halted; registers changed by the last step in yellow (the session remembers the
     registers before each step). Below in the box: set registers, Step out, run to.
   - Paging: four tiles (range + ROM file / RAM block), ROM tiles tinted, the quarter holding
     the PC outlined yellow (no extra text).
   - Below: the disassembly (all remaining width) beside a narrow stack column (about 150 px)
     of the same height. Disassembly: gutter (▶ PC, ● breakpoint), address, bytes (switchable),
     mnemonic, operands coloured by symbol type (5g: FW blue, VAR green, var teal, sub amber,
     jump orange, data purple; the operand lookup has to report the type), PC line on a
     yellow bar, the `; ----` quarter separators. Fix (done 2026-10-22): immediates that are plain numbers must
     not be named after the low firmware restarts (today `ld bc,#0000` shows as
     `ld bc,RESET_ENTRY`); use the source generator's rule (analysis/operands.py roles: low
     firmware addresses only where the value is used as an address).
     Stack: words from SP (SP line marked) up to the stack's top; footer two lines: "top &C000"
     and where it came from ("ld sp at &0092 (lower)").
   - Stack top rule: the emulator watches instructions that load SP (`ld sp,nn`, `ld sp,(nn)`,
     `ld sp,hl/ix/iy`, and the reset value) and keeps a short list of tops with the
     instruction that set them. A new SP value just below a known top (within its area,
     about 1 KB) is a return to that stack (e.g. the firmware restoring SP after an
     interrupt): the top and its source stay. Otherwise the value is a new top. Cheap: only
     on SP loads. The footer always names the instruction, so the estimate is visible.

5c. **Memory tab layout.** Agreed with the owner on 2026-10-22 (artboard "Memory 1: tidy grid +
   variable table + follow CPU"):
   - Controls box: address ("at"), ◀ ▶ (one page back/forward), view selector, go to PC / SP /
     HL, a "Follow CPU" toggle, the write field ("always to RAM").
   - 16 rows of 16 bytes, every row starting at &xxx0. A vertical scroll bar beside the grid
     moves line by line (16 bytes) through the whole 64 KB (mouse wheel too). Jumps land on a
     full page &xx00-&xxFF (e.g. &B700-&B7FF): a typed address, go to PC/SP/HL, Follow CPU
     and ◀ ▶ (one page) show the page containing the address, the address itself framed.
   - Paging tiles as in the CPU tab; the quarter in view outlined yellow.
   - Hex grid: column header 00-0F, a gap after 8 bytes, ASCII column; bytes coloured per
     known variable as today; the selected variable's bytes framed yellow.
   - Variables in view: a compact table of fixed height (scrolls): colour chip, range, name
     (fields of a structure indented under it), size; areas in one grey line above. The
     description only for the selected variable, in a framed detail line (click a table row
     or a byte to select; hovering a byte names its variable).
   - Follow CPU: while on, at every stop (step, breakpoint, pause) the view shows the page of
     the last access (writes before reads) and selects the variable written last. Status
     line: "last step at PC xxxx: write addr = value (NAME) · read addr = value (NAME)" and
     the number of bytes written since the last stop. Highlights: written by the last step
     (strong red), written since the last stop (dim red), read by the last step (blue frame).
     Writes since the last stop come from a write observer installed only while Follow is on
     (D-12); reads only for the last step.

5d. **Breakpoints tab layout.** Agreed with the owner on 2026-10-22 (artboard "Breakpoints 3:
   combined"), top to bottom:
   - Last stop (yellow frame): type chip and what triggered ("pc · before the instruction at
     BB5A TXT_OUTPUT · RAM block 2"), the instruction itself (disassembled, with the RST
     comment), breakpoint number and hit count. For a memory breakpoint: access, address =
     value, PC of the instruction, location, variable.
   - Breakpoints · n: a table with on/off checkbox, #, type chip, where (address or range,
     port, ROM; the firmware label or variable name in colour), condition (location, mask),
     hits, ✕ remove; disabled rows dimmed, the breakpoint of the last stop on a yellow bar;
     clicking a row shows its address in the CPU or Memory tab. Enable all / Disable all /
     Remove all below.
   - New breakpoint: the ten breakpoint types as chips coloured by category (code pc/m1
     yellow, memory read/write/access green, I/O blue, system rom_select/interrupt purple), a
     one-line explanation of the chosen type, only the fields that type needs (address, end,
     location;
     port, mask; ROM), Add. A variable name with an empty end shows the range it will cover.

5e. **Trace tab layout.** Agreed with the owner on 2026-10-22 (artboard "Trace 3: indentation
   + changes + detail"):
   - Controls box: "Trace runs" toggle (styled like Follow CPU) with the speed note (off:
     stepping still recorded; on: free runs about 5x slower); analysis targets only, write
     file, path, format in one row.
   - Trace list: address; instruction indented by call depth (▸ call, ◂ return; depth from
     calls and returns inside the trace, a reading aid), operands coloured by symbol type (5g);
     a "changes" column further right (about 360 px for the instruction part) with the
     registers the instruction changed (A=.., F=.., BC=.., SP=..; PC left out); `; ----`
     separators where execution moves to another ROM / RAM block; the selected row on the
     yellow bar. ↑/↓ move the selection while the mouse is over the list (otherwise keys
     still go to the CPC); click selects.
   - Detail box (yellow frame) for the selected row: address, bytes, instruction, position
     ("27 / 30") with ▲ ▼ buttons, where it ran and the depth, registers before / after with
     the changes in yellow. Memory accesses ("reads B733 (TXT_GRAPHICS_CHARACTERS) = 00") for
     stepped instructions only, from the same per-step data as Follow CPU; traced free runs
     do not record them (would cost speed; to decide later).

5f. **Bus trace tab layout.** Agreed with the owner on 2026-10-22 (artboard "Bus trace 3:
   grouped, detail switches"):
   - Controls box: "Recording" toggle (styled like Follow CPU) with the record count and the
     speed cost; the access types to record as clickable chips (fetch M1 grey, read blue, write red,
     in/out purple, interrupt yellow - the same colours as Follow CPU); from / to address and
     capacity; Clear. Below: "show" checkboxes "fetches and operands" (default off) and
     "combine word accesses" (default on).
   - List grouped per instruction: PC, instruction, then its accesses as lines with a type
     chip (F, R, W, IN, OUT, INT), "address = value" and a meaning: variable names, "stack"
     (between SP and the stack top of 5b), "stack: return address" for a call,
     "stack: returns to xxxx" for ret, ports decoded by device and value (e.g. "Gate Array:
     mode 1, lower ROM on, upper ROM off"); fetches and the instruction's own operand bytes
     greyed. Combined words show the low address and the 16-bit value (W BFF0 = 7F85).
   - With both switches set to full detail every bus access appears (the detail of a plain
     access table), still grouped. `; ----` separators where the ROM / RAM context changes.
   - The meanings are worked out when reading, from the recorded accesses: recording stays as
     fast as today.
   - History: the list scrolls through everything recorded (up to the capacity) with a scroll
     bar, the mouse wheel and, while the mouse is over the list, ↑ ↓ (line), Page Up / Page
     Down (screen), Home / End (oldest / newest); a position line ("records 9,973-10,000 of
     10,000"). At a stop the list shows the newest records unless the owner has scrolled back
     into the history; then it stays and "↓ newest" returns. The Trace tab (5e) scrolls the
     same way through its ring buffer.
   - Note: in the mock-up the scroll bar is only drawn (28 sample records, no history), so the
     scrolling could not be tried there; check it in the real GUI when implementing (scroll
     bar, wheel, keys over the list, staying in the history at a stop, "↓ newest").

5g. **Symbols tab layout.** Agreed with the owner on 2026-10-22 (artboard "Symbols 3:
   combined"), top to bottom:
   - Symbol types (owner, 2026-10-22; the word is "type"): every symbol has one of six types,
     the case shows where it comes from:
     - FW (upper case): Amstrad firmware jumpblock entries and restarts (TXT_OUTPUT);
     - VAR (upper case): Amstrad OS / AMSDOS variables (TXT_CURSOR_COLUMN, AMSDOS_RETRIES);
     - var (lower case): the owner's variables or ones from the analysis of a ROM (VDOS);
     - sub: subroutines, call targets (sub_c2a0, or renamed: read_sector);
     - jump: jump targets inside code - if/then, loops, choices (l_c1e8);
     - data: tables, strings, other data (d_c015, disk_messages).
     sub/jump/data/var come from the analysis (the source generator's sub_ / l_ / d_ names,
     shown as symbols marked ◇ "from analysis") or from the owner (marked ✎); renaming an
     analysis label makes it the owner's, its type stays. The type sets the colour everywhere
     (Symbols list, disassembly and trace operands, Memory tab): FW blue, VAR green, var teal,
     sub amber, jump orange, data purple.
   - Search box: search text (label, comment or variable description), scope filter, "only
     yours", type chips with counts to show/hide each type; a legend line for the types and
     markers. Variables (FR-DBG-11) are searched too (today only symbols are).
   - List and detail side by side: a compact list (type chip, address, label in the type's
     colour, ✎/◇ marker; scrolls) and a detail panel (yellow frame) for the selected entry:
     address and scope, where the name comes from (e.g. "firmware: main jumpblock, text
     VDU", or a variable with size and description), the machines it exists on, the
     instruction at the address; your
     comment and your label as editable fields with Save / Remove yours; Show in CPU, Show in
     Memory, Breakpoint here.
   - Your symbols: a form with the type chips var / sub / jump / data (owner, 2026-10-22:
     needed for disassembly and analysis). sub, jump, data: address, name, scope, comment.
     var: name, size, base (absolute address, or the data area (IY) of ROM n from
     KL_ROM_IY_TABLE) + offset, comment; the form shows where it is now ("→ now at
     A612-A621") and it follows a moving data area like the AMSDOS variables. Own variables
     behave like the known variables (FR-DBG-11): name+offset in disassembly, trace, bus trace
     and the source (EQU), memory view colours and table, breakpoints on the name cover them.
     Stored in the annotation file with their type (format extension needed, e.g.
     `var rom5+0012 vd_drive_table 16 ; comment`, `sub C4D8 read_sector ; comment`). Below:
     the annotation file (path, number of entries, saved or not, Load, Save).
   - Fix (done 2026-10-22): a comment (or label) of the owner at a firmware or variable address is stored as the
     owner's layer and shown beside the firmware name and comment; it must not replace them
     (today `symbol_add("BB5A", comment=...)` turns TXT_OUTPUT into a user symbol and the
     firmware comment "TXT OUTPUT" is lost).

5h. **Disks tab layout.** Agreed with the owner on 2026-10-22 (artboard "Disks 2: drive chips,
   list and detail"), top to bottom:
   - Drive chips ("● A: 464_cpm_22_orig.dsk", "○ B: draft") select the drive to work with.
   - One full-width card for that drive (yellow frame): activity LED, disk name, badges
     (read-only / writable, draft, changed), geometry ("3" · 40 tracks · 1
     side"), head position bar with the cylinder; the image path with a ▾ button that opens a
     file selector (Dear PyGui file dialog) in the folder of the current image (disk/),
     filtered to disk images (.dsk, also "all files"), showing name, size and date; Select
     puts the file into the path field; writable checkbox (off: write-protected for the CPC,
     the image file never changes) and, on the same row right of it, the buttons as in the
     "Disks 1" cards: one Insert / Eject button (Eject while a disk is in, Insert - the
     selected file - when the drive is empty; never both), Save (greyed while nothing to
     save), Save as… Then new draft with format chips system / data / vortex.
   - "changed" badge (owner, 2026-10-22): in the style of "read-only"; lit as an orange
     warning light (dark orange background, bright orange text and border) as soon as the CPC
     has written to the disk and the change is not saved, dim grey otherwise; also shown in
     the drive chips; Save and Save as… turn it off.
   - Save as… writes the new file and the drive then uses it instead of the old image (the
     old file stays untouched); the new file is the owner's copy, so it is inserted writable
     (agreed). Done 2026-10-22 in `disk_save` (the GUI buttons follow with the tab).
   - File list (name, size; files, K used / free; scrolls) beside a detail panel (yellow
     frame) for the selected file: size on disk and user; the AMSDOS header if there is one
     (type, name, load, entry, length); then
     - "after the header (load &xxxx)": the first 16 bytes as one line, hex and ASCII;
     - "at the entry &xxxx": the 16 bytes at the entry address (hex, no ASCII) and their
       disassembly with symbol names (a few lines); "entry = load" noted when they coincide;
     - text files (no header, ASCII) show their first lines instead; files without a header
       show the first bytes.

5i. **Project tab (was "Files").** Agreed with the owner on 2026-10-22 (artboard "Project 3:
   machine summary / Edit + analysis phases"); the tab is renamed "Project". Top to bottom:
   - Machine, collapsed by default to a summary: model and RAM, the config file, "changed"
     badge (orange, as in Disks) while edits are unsaved, an Edit button; the ROMs as tags
     (scrambled and analysed ones marked, e.g. "5 · scrambled.rom · vortex · analysed"); one
     line with the drives and RAM targets.
   - Edit expands the machine editor (Close collapses it), covering everything a
     machine.toml holds (design.md §6.14), so a config can be built in the GUI or loaded:
     config file path with ▾ (file selector), Load, Save, Save as… (writes a fresh,
     commented TOML; comments of a hand-edited file are not kept), "changed" badge; model
     chips 464 · 64K / 664 · 64K / 6128 · 128K and the slot for unpopulated ROM numbers;
     tables with a heading per column (owner, 2026-10-23): ROMs (file with ▾, decoder
     none / vortex, analyse, description = what the ROM header says, ✕) with "+ ROM in
     slot"; drives A and B (image with ▾, writable); "RAM code to analyse" (RAM targets:
     name, from, length, copy taken "when it first runs" / "on command", ✕, "+ RAM range");
     "Apply (resets the machine)"
     because model, ROM and drive changes need a power cycle.
   - Analysis: target chips (ROM and RAM targets) and the "Analysis on" switch; the selected
     target as numbered phases, each with status badge and button:
     1. Collect (running with analysis on; coverage bar code / data / unknown, what was seen);
     2. Decode - only for a ROM with a decoder (scrambled): a bar of how bytes were read
        (opcode M1, data read and decoded, both = conflict with address, never read), Save
        access map, Load map, Decode from map, Conflicts…; exports: clear ROM
        dump (fill policy), ROM map, coverage CSV;
     3. Save analysis ("changed" while collected data is unsaved; analysis file and access
        map);
     4. Generate source (output file, fill policy, lines / labels / outside names);
     5. Verify (sjasmplus round trip; "identical ✓" or the differences).
     Without a decoder the phases are 1, 3, 4, 5 (shown as 1-4).
   - Snapshot: one row with the .sna path, ▾, Save, Load.
   - Decode switch (owner, 2026-10-23): the GUI has none - a ROM with a decoder is always
     decoded (it cannot run otherwise). The switch stays for MCP clients (`rom_set_decode`,
     `decode` in machine.toml); comparing raw and decoded bytes works with the memory views
     (raw:romN, romN, shadow:romN). design.md D-11 to be updated at the end of task 5.
   - New behaviour needed: writing a MachineConfig as TOML (the loader only reads), and the
     GUI editing a config in memory before Apply.

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

## Done

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
