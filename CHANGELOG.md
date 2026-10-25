# Changelog

## Unreleased

- Fixed: a RAM copy of ROM code is no longer forgotten when another program overwrites it. The
  analysis kept only the copies still in RAM, so after running BASIC the source lost the DISP
  blocks of the copies CP/M had made at the same addresses (VDOS: &BE80 and &AC00 beside
  BASIC's &BE8F), and a leftover fragment could replace a saved copy. Every copy seen is now
  kept with its own execution count and saved; fragments join their copy. Copies lost before
  come back the next time the program that makes them runs.

## 0.9.2 - comment blocks, groups and contexts, interrupt timing

**Comments and names**
- New: comment blocks before and after a line - a routine header above it, a longer explanation
  below it, several lines each - besides the comment at the end of the line. Edit them in the
  Symbols tab or with `symbol_add` / `comment_add` over MCP; the CPU tab shows them, the Trace
  tab marks them (¶) and shows them for the selected instruction, and the generated source
  writes them as comment lines (still byte-identical). They are kept in
  `project/annotations.txt` as `before` / `after` lines; older files load unchanged.
- New: say what an instruction's operand is - right-click it in the CPU tab, or `operand_set`
  over MCP: a number (always hex, `call #0010` instead of `call SIDE_CALL`), an address (named),
  or judged by cpcxray again. The decision is kept in `project/annotations.txt` and holds in
  the disassembly, the trace and the generated source.
- New: groups for your variables and RAM labels, and code contexts - for programs that use the
  same RAM differently (VDOS and CP/M, for example). Put variables and global labels in groups
  (Symbols tab: Groups panel, or `group_add` / `variable_add(group=)` / `symbol_add(group=)`),
  give code a context (CPU tab: right-click a line's address - from/to, this routine, whole
  scope; or `context_set`), and every listing, the trace and the generated source name an
  address by the context of the code that uses it. One RAM address can have a label per group.
  The generated source defines the names in one section per group (still byte-identical). Kept
  in `project/annotations.txt` (`group`, `context`, `group=`); older files load unchanged.
- New: `symbol_add(..., group=, replaces=<old group>)` moves a RAM label to another group in one
  step, with its type, comment and comment blocks (as `variable_add(replaces=)` does for
  variables); the Symbols tab's editor uses it.
- Changed: the firmware's jumpblock and restart names are fixed - a label of yours on such an
  address is refused (a comment is fine); an older annotation file with one loads without it
  and the Symbols tab's status says so.

**Analysis and source**
- New: the data kind `word_data` (`classify`) for words that are numbers - parameters,
  offsets, time constants. They are always written in hex: a parameter &0010 no longer turns
  into `defw SIDE_CALL` (the name of RST 2), as it did in a `word_table`, whose words are
  addresses and keep their names. Existing analysis databases stay valid.
- Fixed: the generated source no longer labels the target of a FAR CALL into another ROM in
  the analysed ROM (VDOS: `rst #18` to &C1DC in ROM 7 made a `sub_c1dc` in ROM 5), nor that of
  a SIDE CALL to another slot; the far address stays `defw #C1DC ; ROM 7`.

**The machine**
- Fixed: BASIC could hang in `MC WAIT FLYBACK` (for example after a disc LOAD). The 300 Hz
  interrupt now follows the Gate Array's real line counter (R52): the first interrupt of a frame
  comes 2 scan lines into VSYNC, and the phase is resynchronised every frame, also after an RMR
  bit 4 write or a snapshot load. The Devices tab shows the frame line next to R52. Emulation
  is slightly faster as a side effect.

## 0.9.1 - bug fix

- Fixed: no drop-down list in the window could be opened - for example the slot of a new ROM in
  the Project tab's machine editor, the decoder of a ROM, or the view of the Memory tab
  (cpu, ram, ROMs, raw / decoded ROM). Taking the keyboard back for the CPC closed the list at
  once; now the window leaves the keys alone while a list is open and gives them back to the
  CPC right after the choice.

## 0.9.0 - first public release (beta)

The first public release. Everything planned for version 1 is in; what is missing is real-world
use. Expect rough edges - please report what you find on the
[issue tracker](https://github.com/SerErris/cpcxray/issues).

**The machine**
- CPC 464, 664 and 6128 (128 KB RAM banking) running the real firmware, BASIC and AMSDOS from
  the configuration (`machine.toml`); the seven standard Amstrad ROMs are included.
- A Z80 core written from scratch: documented and undocumented behaviour, every bus access in
  the real order with M1; passes all 1.6 million SingleStepTests, ZEXDOC, ZEXALL and z80test.
- Gate Array, CRTC (registers), ROM select, keyboard matrix and the µPD765 floppy controller:
  drive A 3" (DSK, EDSK), drive B 80 tracks; real images are inserted read-only.
- The PC keyboard types on the CPC by character, on any keyboard layout.

**The debugger window**
- Tabs: CPU (registers, paging, disassembly with names, stack), Memory (views of CPU, RAM
  blocks, ROMs and decoded ROMs, coloured by variables; Follow CPU), Devices, Breakpoints,
  Trace, Bus trace, Symbols, Disks and Project.
- Breakpoints of ten types (PC, M1, read, write, access, I/O in / out / both, ROM select,
  interrupt) with ranges, port masks and paging conditions.
- Instruction trace with call depth, changed registers and the history after a stop; bus trace
  of up to a million accesses, explained (variables, stack, ports); `.sna` snapshots.

**The analysis**
- Vortex-scrambled ROMs decoded on every access (opcode fetches raw, data reads decoded), with
  the access map, conflicts, clear ROM image, ROM map and coverage.
- Analysis of ROM and RAM targets: instruction starts, code copied to RAM and run there,
  relocated blocks, RST calls with inline data; an analysis database with your overrides.
- A reassemblable sjasmplus source with firmware names, variables and your labels and
  comments, checked byte for byte by a round trip through sjasmplus.
- The Project tab leads through the five phases: collect, decode, save, generate, verify.

**Claude Code**
- An MCP server (stdio, or HTTP from the open window) with 99 tools - every function of the
  debugger and the analysis.

**Getting started**
- `cpcxray init` creates a project folder with configurations for the 464, 664 and 6128 and
  the standard ROMs; `cpcxray gui` opens the window.
- Documentation: install guide, user guide, command line, MCP reference, Claude Code guide,
  developer guide and architecture diagrams (see README.md).
