# Changelog

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
