# CPC X-Ray architecture diagrams

Three diagrams, from the outside in. Each level shows the level below it as a single box (dashed,
"see diagram n"); the [developer guide](developer_guide.md) explains the parts in words.

1. [The session layer](#1-the-session-layer) - the clients, the Session API, its mixins and
   services, the machine and the project folder below
2. [The machine](#2-the-machine) - the Z80, the buses, memory and I/O devices of the CPC
3. [The Z80 core](#3-the-z80-core) - how one instruction is executed

## 1. The session layer

![The session layer: the clients above the Session API, the mixins hanging on it, the services below, the machine and the project folder at the bottom](img/session.svg)

The session layer (drawn by `python -m tools.draw_session_diagram`) is the only way to act on
the machine (D-27). It owns the machine, runs it, looks into it, and keeps everything a
reverse-engineering session collects about it: breakpoints, traces, symbols and the analysis.
The window, Claude Code, the command line and scripts never touch the machine themselves; they
all ask the Session. A feature therefore exists once and works the same everywhere.

There are no buses here; the **Session API** takes their place as the spine everything hangs
on. Every public method of `Session` is one MCP tool with the same name and parameters - adding
a method adds a tool. The methods follow common rules, whoever calls them:

- **Addresses** may be numbers, hex strings (`BB5A`, `&BB5A`, `#BB5A`) or labels
  (`TXT_OUTPUT`, a known variable); results give addresses as 4-digit hex.
- **Results** are plain dicts that the MCP server passes on unchanged; a bad request raises
  `SessionError`, which the client sees as an error message.
- **Runs are bounded**: every run, step or `run_until` stops at an instruction limit, an emulated
  time limit or a wall-clock timeout at the latest, and says why it stopped (breakpoint, address
  reached, returned, paused, …), where, and which breakpoint fired.
- **File paths** are relative to the project folder.

**The clients** (their own dashed frame) call the Session API. The window and the command line run
in the same thread as the emulation and call it directly. Claude Code talks to the MCP server
over stdio or HTTP; the server runs in other threads, so it hands each call to the **command
queue**, which executes it in the emulation thread between two time slices (D-13, D-29). Only
`pause` skips the queue - it would otherwise wait behind the very run it is meant to stop.

**The Session core** (`session/session.py`) does what every client needs: run, step, step over
and step out (the run loop checks PC breakpoints, limits and the trace before each instruction
and calls the machine's clock, `CPC464.step()`, see diagram 2), registers and memory, the
disassembly with names, the instruction trace, installing a new machine (`load_config`), and
resolving addresses and paths.

**The mixins** hang below the spine, one per group of tools:

| Mixin | What it does |
|---|---|
| `DebuggerMixin` | breakpoints of every type with location qualifiers, the bus trace, memory views (CPU, RAM, ROM, shadow ROM) and the paging, devices, the stack, `.sna` snapshots |
| Views (`TraceViewMixin`, `BusViewMixin`, `MemoryFollowMixin`) | the instruction trace and the bus trace split into fields and explained (variables, stack, ports); "Follow CPU": what was written since the last stop |
| `SymbolsMixin` | the owner's labels, comments and variables, the search over every name (firmware, variables, labels, generated names), the annotation file |
| `DiskMixin` | the two drives: insert, eject, blank drafts, saving an image, reading the CP/M catalogue and files on the host side |
| `ProjectMixin` | the machine configuration as a document (read, write, apply), ROM headers, binary files to load into RAM |
| `AnalysisMixin` | scrambled ROMs (decoding, access map, clear dump, ROM map), analysis targets, the analysis database, the generated source and its round-trip check, the analysis phases |

**The services** below are the plain workers the mixins use - debugger/ (breakpoints, traces,
stack, ports), the symbol table and the variable map, disk/ (images and the CP/M reader), the
configuration loader, and analysis/ and decoding/. They know nothing of clients or MCP.

**Towards the machine** the Session drives it (step, run, read and write memory) and puts disk
images into its drives. Breakpoints, traces, Follow CPU and the analysis watch the machine as
**bus observers**: they are registered on its bus only while they are needed, so an unwatched
machine runs at full speed (D-12). **Towards the project folder** the services read and write
the files of a session: the configuration, ROMs and disks, the annotations, the analysis
database and access map (merged in at the start, saved on request and when the window closes),
the generated source and the exports.

| Line | Meaning |
|---|---|
| blue - calls | the clients calling the Session's methods |
| amber - the Session API | the Session class and its mixins |
| violet - uses | a mixin uses a service |
| orange - machine calls | the Session drives the machine (step, run, memory); disk images go into its drives |
| red dashed - bus observers | breakpoints, traces, Follow CPU and the analysis watch the machine's bus (D-12) |
| green - files | services read and write the project folder |

## 2. The machine

![The CPC machine: the Z80 and the bus logic on the control, address and data buses, the I/O devices above with their ports, the memory below](img/machine.svg)

The machine as a hardware block diagram (drawn by `python -m tools.draw_machine_diagram`). It
is the emulated Amstrad CPC: `CPC464` (machine/cpc464.py) is a 464, 664 or 6128, as
`[machine] model` in `machine.toml` says. It runs the real firmware, BASIC, AMSDOS and any
expansion ROM from the configuration. The model is **instruction-level and software
compatible**, not cycle-exact: it reproduces what software can see through memory and the I/O
ports, not the video signal or the analogue timing. The machine knows nothing of debugging or
analysis; the Session one layer above drives it and watches it (diagram 1).

**Power-on and reset.** Power-on builds memory (RAM cleared), devices, bus and CPU and installs
the ROMs of the configuration (`machine.toml`; a missing ROM file is listed and skipped). Reset
puts the devices into their power-on state first - the Gate Array in mode 1 with the lower ROM
on and the upper ROM off - and the Z80 last, so the first instruction comes from the OS ROM at
&0000. RAM keeps its contents on a reset.

**The clock** is `CPC464.step()`: the Session's run loop calls it once per instruction (the
Session decides when and how many - in the window, as much as real time allows). Each call
sets the Z80's INT line from the Gate Array, runs the Z80 for exactly one instruction
(`Z80.step()`, diagram 3), adds its cost to the emulated time (in µs, D-04) and ticks the Gate
Array (interrupt timer) and the keyboard's typing schedule. The CPU never runs by itself.

**The CPU bus and the bus logic.** The Z80 sees only the CPU bus: opcode fetch (M1), memory
read and write, I/O in and out, interrupt acknowledge. `CpcBus` (machine/bus.py) connects it to
the three system buses and decides where each access goes:

- **Memory** goes to the memory map: a read of &0000-&3FFF sees the lower ROM (OS) when it is
  enabled, &C000-&FFFF the selected upper ROM when enabled, everything else RAM; **writes always
  go to RAM**, also under a ROM. An opcode fetch is marked as M1, so a scrambled ROM can return
  its bytes raw to the CPU and decoded to data reads (D-11).
- **I/O** is broadcast (D-08): every device decodes its own port bits from the address, as on
  the real board. An OUT reaches every device that decodes the port; for an IN, the values of
  all answering devices are ANDed, and with no answer the bus reads &FF.
- **Bus observers** of the Session (breakpoints, bus trace, analysis) hook into `CpcBus` only
  for the access types they ask for; the others keep the fast path (D-12).

**The memory** (machine/memory.py):

| Part | What it is |
|---|---|
| RAM | 64 KB; on the 6128 128 KB in eight 16 KB blocks - the Gate Array's RAM configuration chooses which block appears in each 16 KB quarter (D-14) |
| Lower ROM | the OS, 16 KB at &0000-&3FFF |
| Upper ROMs 0-15 | BASIC, AMSDOS and expansion ROMs at &C000-&FFFF; ROM select chooses one, an unused number shows ROM 0 as on a real CPC (D-09, D-10). A scrambled ROM gets a decoder and a shadow ROM that records every read (the access map) |

**The I/O devices** (devices/), with the ports they decode:

| Device | Port | What it does here |
|---|---|---|
| Gate Array | &7Fxx | screen mode, pens and inks, border, ROM enables, the 300 Hz interrupt; the 6128's RAM configuration |
| CRTC 6845 | &BCxx-&BFxx | a register file only, no timing (D-06); the renderer uses its screen size and start address |
| ROM select | &DFxx | the number of the upper ROM to show |
| PPI 8255 + PSG | &F4xx-&F7xx | one `SystemIO` device instead of both chips (D-07): the keyboard rows and the VSYNC bit; sound is not emulated |
| FDC µPD765 | &FB7E-&FB7F | the floppy controller with drive A (3", 40 tracks, one side) and B (80 tracks, two sides) |

**Outside the bus**: the keyboard is a 10 × 8 matrix that the firmware scans through the PSG
port; keys come from the window, from MCP or from typed text. The drives hold disk images
(DSK, EDSK, raw) as the inserted disks. The **renderer** (ui/renderer.py) is not part of the
machine: once per window frame it draws screen RAM with the Gate Array's inks and the CRTC
registers.

**Simplified on purpose:** the interrupt is latched every 3,328 µs (300 Hz) and VSYNC is
computed from the clock (D-05), there are no scan lines, the CRTC has no timing, sound and
cassette are not emulated, and the FDC has no overrun timing. The firmware and the ROMs we
analyse work with this; demos that race the video beam do not.

| Line | Meaning |
|---|---|
| green - **control bus** | M1 (opcode fetch), MREQ (memory), IORQ (I/O), RD, WR |
| red - **address bus** | A0-A15; an I/O device decodes its own port bits (D-08) |
| blue - **data bus** | D0-D7; an OUT reaches every device, IN values are ANDed |
| black - clock | `step · run` from the Session, `Z80.step()` to the CPU, the Gate Array's tick |
| orange - INT | the Gate Array's 300 Hz interrupt to the Z80 |
| purple - ROM enables, RAM configuration | the Gate Array switches the lower and upper ROM in and out and, on a 6128, the RAM banks |
| magenta - upper ROM number | ROM select chooses which of the 16 upper ROMs is visible |

Each box names the module that implements it, where a hardware diagram prints the IC number.

## 3. The Z80 core

![The Z80 core: control, opcode fetch, handler tables and bus interface above, timing, register file and flag tables below; numbered arrows show one instruction's way through them](img/z80.svg)

The Z80 core as a block diagram (drawn by `python -m tools.draw_z80_diagram`). `Z80`
(cpu/z80.py) is a complete Z80 in Python, documented and undocumented behaviour included,
**implemented from scratch** for cpcxray - no code is taken from another emulator. It is built
from the published descriptions of the CPU (the opcode bit fields as in "Decoding Z80 Opcodes",
the undocumented behaviour, the CPC timing sheet) and checked against independent test suites
(below). It knows nothing of the CPC: it talks to the outside only through the six methods of the CPU bus
(`CpuBus`, cpu/bus.py) and two input lines, INT and NMI. The machine (diagram 2) gives it its
bus and calls it once per instruction.

### The model: simplified inside, identical outside

The core works at **instruction level** (D-03). One call of `Z80.step()` executes one complete
instruction - or accepts one interrupt - in one go and returns its cost. Inside an instruction
there are **no T-states and no M-cycles**: no clock edges, no WAIT states, no refresh cycles,
no bus request. This simplifies *how* the CPU is modelled, not *what software sees*: for a
program the core is indistinguishable from a real NMOS Z80. It produces the same results in
registers, flags and memory, and the same accesses on the bus:

- **Every bus access, in the real order.** An instruction issues the same reads and writes, to
  the same addresses with the same values, in the same order as a real Z80 - the opcode and
  prefix bytes, operands, memory reads and writes, the stack, I/O, the interrupt acknowledge.
- **With its signals.** The bus method used carries the access type: `fetch` is an opcode or
  prefix fetch (M1), `read` / `write` are memory accesses, `io_in` / `io_out` are I/O, `int_ack`
  is the interrupt acknowledge. `DD CB d op` reads *d* and *op* without M1, as the real CPU
  does. This M1 attribution is what decoding scrambled ROMs relies on (D-11, design.md §5.5).
- **Interrupts at the same points as the hardware.** A real Z80 samples INT at the end of each
  instruction, just before the next opcode fetch (M1); if it accepts the interrupt, that M1
  becomes the interrupt acknowledge. An instruction is never interrupted on the real CPU
  either. The core does the same: each `step()` first checks the interrupt lines and then
  either accepts the interrupt or runs the next instruction completely.

The one thing the model leaves out is **when within an instruction** an access happens: all
accesses of one instruction count as one moment for the rest of the machine, and the clock
moves on by the instruction's whole cost afterwards. Software cannot observe this on a CPC
(see "Timing" below); only hardware tricks that race the video beam within an instruction
could.

**Verified by the acid tests.** All 1.6 million SingleStepTests/z80 vectors pass - register
state, memory, and the order and type (M1 or not) of every bus access - and so do ZEXDOC,
ZEXALL (every instruction and flag, documented and undocumented) and the z80test suites
(tests/cpu/, tools/run_singlestep.py, tools/run_cpu_validation.py).

How each aspect is modelled:

| Aspect | How the core handles it | As on the hardware? |
|---|---|---|
| Timing | each instruction costs a whole number of NOPs (1 NOP = 1 µs = 4 T-states), from a table per opcode (cpu/timing.py, D-04), checked against the CPC timing sheet; a taken condition and a repeating block instruction have their own cost | yes per instruction: on the CPC the Gate Array stretches every instruction to whole NOPs, so delay loops and interrupt spacing match; the position of an access within the instruction is not modelled |
| Memory contention | the per-opcode cost already contains the CPC's stretching | yes - no per-access WAIT states needed |
| Interrupts | checked before the next opcode fetch; not after EI or a DD/FD prefix; NMI before INT; between the iterations of a block instruction | yes - the same points as on the real CPU; the CPC's Gate Array holds INT until acknowledged, so none is lost |
| Interrupt modes | IM 1 calls &0038, IM 2 reads the vector at I × 256 + data byte (&FF on a CPC); IM 0 executes the data byte as an instruction | yes for one-byte instructions (in practice an RST); multi-byte instructions on the data bus in IM 0 are not supported - a CPC never puts one there |
| Block instructions | LDIR, CPIR, INIR, OTIR … do one iteration per `step()`; a repeat rewinds PC to the prefix | yes - the real CPU repeats the same way, and interrupts can come between iterations |
| HALT | each `step()` while halted does a dummy M1 fetch of the next byte and costs one NOP, until an interrupt | yes |
| R register | incremented on every M1 (opcode, prefix, interrupt acknowledge) | yes for R; the refresh cycle itself does not appear on the bus (nothing on a CPC uses it) |
| Undocumented behaviour | X/Y flags, MEMPTR (WZ), the hidden flag latch Q (SCF/CCF), IXH/IXL/IYH/IYL, SLL, DD CB register copies, ED duplicates, OUT (C),0, the P/V quirk of LD A,I / LD A,R | yes, as on an NMOS Z80 |

**Readability before speed.** The handlers are closures built once per CPU instance from the
opcode's bit fields; DD/FD use the same factories with IX/IY in place of HL, so every
instruction exists exactly once. The code favours long, descriptive names over speed tricks: it
is the reference for a later port to a faster language. Building a `Z80` creates about 2000
closures, so it is built once and reused.

### One instruction

The numbered black arrows are one instruction's way through the core:

1. The clock (`CPC464.step()`, diagram 2) calls `Z80.step()`. **Control** first accepts a
   pending NMI, then an interrupt if INT is set, IFF1 is on and the last instruction was not
   EI (IM 0 / 1 / 2) - an accepted interrupt is the whole step, and the next instruction
   follows in the next `step()`. A halted CPU only makes a dummy M1 fetch. This check is the
   only place where interrupts are handled; steps 2-6 run without interruption.
2. **Opcode fetch**: an M1 cycle on the bus (`bus.fetch`), PC + 1, R incremented.
3. The opcode selects its **handler** in the unprefixed table; a prefix byte (CB, ED, DD, FD)
   continues in the matching table, still within the same `step()`. The tables are closures
   built once per CPU; DD/FD use the same factories with IX/IY in place of HL.
4. The handler (the ALU is part of the handlers) reads its operands from the **register file**,
   writes the results and the new F back, and looks up S, Z, X, Y and parity of a result in the
   read-only **flag tables** (`cpu/tables.py`).
5. Operands, memory and I/O go through the **bus interface**, every access in the order of the
   real bus cycles.
6. The cost in NOPs (= µs, D-04) from **timing** goes back to the clock.

| Line | Meaning |
|---|---|
| black - steps 1-6 | one instruction, from `step()` to the cost |
| orange - INT | the Gate Array's interrupt line |
| green - M1 fetch | the opcode fetch on the control bus |
| green · red · blue | the CPU bus: control, address, data (diagram 2) |
| teal dashed | the Session reads and sets the registers between two instructions |

Two things watch the core from outside, at different places. The **bus observers** (breakpoints
on accesses, bus trace, analysis; D-12) only see the CPU bus. The **Session's run loop** looks at
the register file between instructions: it checks PC breakpoints and records the instruction
trace before each step, and reports which registers a step changed; `registers_get/set` and
`.sna` read and set the registers. Nothing outside reads the flag tables - F is a register.

The **disassembler** (`cpu/disasm.py` with the CPC RST profile) decodes the same opcode
structure from bytes but never executes anything; it sits beside the core.
