# CPC X-Ray user guide

CPC X-Ray (`cpcxray`) is an Amstrad CPC 464 / 664 / 6128 emulator built for reverse
engineering. It runs the real firmware, BASIC, AMSDOS and any expansion ROM, and it analyses the
code you choose - a ROM slot or a range of RAM - while the machine runs. From what it observed it
writes a clear ROM image, an access map, a ROM map and a reassemblable sjasmplus source that is
checked byte for byte against the original. ROMs scrambled with the Vortex X-module scheme are
decoded on every read.

This guide covers the window and the analysis. Installing and starting: [install guide](install.md).
Everything the window does is also available to scripts and to Claude Code as MCP tools:
[MCP reference](mcp.md), [Claude Code with cpcxray](claude_code.md).

Contents: [1 Your project folder](#1-your-project-folder) · [2 The window](#2-the-window) ·
[3 The tabs](#3-the-tabs) · [4 A typical analysis session](#4-a-typical-analysis-session) ·
[5 File formats](#5-file-formats) · [6 Troubleshooting](#6-troubleshooting)

---

## 1. Your project folder

Everything cpcxray reads and writes lives in one **project folder**. `cpcxray init` creates it
(see the [install guide](install.md)); a ready-made program's own folder is one already. All
relative paths - in the configuration, in the window's path fields, in MCP calls - start there.
Use one folder per project, e.g. one per ROM you analyse; copy a folder to start a variant.

```text
my_cpc/
├── config/                 machine configurations (you choose one per start)
│   ├── machine.toml        CPC 464 - the default
│   ├── cpc664.toml         CPC 664
│   └── cpc6128.toml        CPC 6128 (128 KB, RAM banking)
├── roms/                   ROM images (16 KB each)
│   ├── os464.rom …         the standard Amstrad ROMs (shipped)
│   └── myrom.rom           your own ROMs
├── disk/                   disk images (.dsk, EDSK) - yours; none are shipped
├── project/                your work: what the analysis and you know about the code
│   ├── annotations.txt     your labels, comments and variables
│   ├── myrom.analysis.json the analysis database of a target
│   ├── myrom.m1map         the access map of a scrambled ROM
│   ├── myrom.asm           the generated sjasmplus source
│   └── myrom_clear.rom …   exports: clear ROM, ROM map, coverage CSV
└── output/                 throw-away results: screenshots, dumps, traces, snapshots
```

### What goes where

| Folder | Put here / find here | Written by cpcxray |
|---|---|---|
| `config/` | machine configurations: model, which ROM in which slot, decoders, analysis targets, disks in the drives at power-on, binary files to load. Edit them in the Project tab or by hand ([format](#5-file-formats)); start one with `cpcxray gui --config config/<file>.toml`. | only when you Save in the Project tab's editor |
| `roms/` | ROM images: the shipped standard ROMs and your own (expansion ROMs, a ROM to analyse). A configuration refers to them as `roms/<file>.rom`. | never |
| `disk/` | your disk images. Real images are inserted **read-only** unless you tick "writable"; the image file itself is only written with Save / Save as… in the Disks tab. Use a new draft (Disks tab) for anything that writes. | only with Save / Save as… |
| `project/` | the knowledge about your code - keep it, back it up, put it under version control. | analysis: on Save analysis and when the window closes; annotations: on Save in the Symbols tab; source and exports: when you generate them |
| `output/` | screenshots (`screen_image`), memory dumps (`memory_save`), traces (`output/trace.txt`), snapshots (`output/state.sna`). Safe to delete. | when you ask for it |

### Files named after the ROM

The analysis files of a target are named after its ROM **file**: a ROM `roms/myrom.rom` in any
slot gives `project/myrom.analysis.json`, `project/myrom.m1map`, `project/myrom.asm`,
`project/myrom_clear.rom`, `project/myrom_rom_map.html` / `.txt` and
`project/myrom_coverage.csv`. A RAM range named `loader` gives `project/ram_loader.*`. So keep a
ROM's file name: renamed, it starts a new, empty analysis (rename the files in `project/` with
it).

### What is kept between sessions

- **Loaded at every start**: the configuration you start with, `project/annotations.txt`, and
  for every analysis target its analysis database and access map - so the analysis grows over
  many sessions.
- **Saved automatically**: the analysis databases and access maps, when the window closes.
- **Saved only when you say so**: your labels, comments and variables (Symbols tab → Save, or
  `symbols_save`) - an unsaved change shows "not saved" there; configuration edits (Project tab
  → Save); a disk the CPC wrote to (Disks tab → Save / Save as…, "changed" until then).
- **Never kept**: the machine state itself (RAM, registers). Save a snapshot (Project tab →
  Snapshot, `.sna`) to come back to a moment.

---

## 2. The window

**Left: the CPC.** A tool bar - Run / Pause, Step, Step over, Step out, Reset, Max speed (as fast
as the PC allows instead of real time), Analysis on / off - then the screen, the indicators
(ANALYSIS while the analysis records, an activity light per drive), the status line (state, PC,
emulated time, speed) and the "Type text" box.

**Typing.** Type on your own keyboard layout: the character your keyboard produces is typed on the
CPC with the keys that give it there (`[` with AltGr+8 on a German keyboard is `[` on the CPC,
`A` is SHIFT + A). A key stays held on the CPC while you hold it, so the CPC's own key repeat and
games work. Keys without a character work by function:

| PC key | CPC key |
|---|---|
| Return / keypad Enter | RETURN / ENTER |
| Backspace / Delete | DEL / CLR |
| Esc, Tab | ESC, TAB |
| left Alt | COPY |
| cursor keys | cursor keys |
| keypad 0-9 and `.` | f0-f9 and f. |
| Ctrl + letter | CONTROL + letter |

Shift or Ctrl alone (e.g. Shift + cursor for COPY editing) reach the CPC while no character key is
held. Characters the CPC has no key for (ä, €) are named in the status line and ignored.

The "Type text" box types a whole line exactly (Enter adds RETURN) - handy for long commands.
While you type into a box in the panels, the keys go there; click on the screen or an empty place
to give the keyboard back to the CPC.

**Right: the debugger tabs.** They show the machine while it is stopped (pause, breakpoint, step)
and are frozen while it runs, which keeps the emulation fast. Devices (once a second) and Project
also follow a running machine.

## 3. The tabs

Names everywhere are coloured by their type: **FW** firmware jumpblock (blue), **VAR** OS / AMSDOS
variable (green), **var** your variable (teal), **sub** subroutine (amber), **jump** jump target
(orange), **data** table or text (purple). The selected thing is yellow.

### CPU
Registers (main and alternate pairs, IX, IY, SP, PC, WZ, the flags as chips, I, R, IM, IFF) -
those the last step changed in yellow; set registers, Step out, run to an address. Four paging
tiles show what the CPU sees in each 16 KB quarter (lower ROM, upper ROM n with its file, or RAM
block). The disassembly (▶ PC, ● breakpoint, bytes switchable) follows the PC; type an address
to look elsewhere. Beside it the stack from SP up to its top, with the instruction that set the
stack.

### Memory
16 lines of 16 bytes through a view: `cpu` (what the CPU reads now), `ram` (RAM, also under a
paged-in ROM), `ram:N` (RAM block N), `lower` / `romN` (a ROM image, decoded for a scrambled
ROM), `raw:romN` (a scrambled ROM as stored), `shadow:romN` (what was actually read). Bytes of
known variables are coloured; the table below lists the variables in view. Write bytes with
"address: bytes" (always into RAM). **Follow CPU** marks what the last step wrote and read and
everything written since the last stop, and jumps there at every stop.

### Devices
Gate Array (mode, border, the 16 pens as colour swatches), ROM select, PPI, CRTC registers with
the screen layout, the floppy controller (motor, drives, head position, last commands) and the
keyboard matrix with the keys held.

### Breakpoints
The last stop and what triggered it; the breakpoint list (on/off, hits, remove). Ten types:
**pc** (before the instruction), **m1** (opcode fetch), **read**, **write**, **access**,
**io_in**, **io_out**, **io**, **rom_select**, **interrupt**. All but pc stop after the
instruction that made the access and name its PC. Address breakpoints take a location: `any`,
`rom`, `lower`, `rom:N`, `ram`, `ram:N` (e.g. "a write into RAM block 2 only"). A read / write /
access breakpoint on a variable name (`TXT_CURSOR_COLUMN`) covers the whole variable.

### Trace
The instructions executed, indented by call depth, with the registers each one changed and,
for steps, its memory accesses. Steps are always recorded; "Trace runs" records free runs too
(about 5x slower), optionally only analysis-target code, optionally into a file (text, CSV or
JSON Lines). Gaps mark runs that were not recorded. A ¶ after an instruction marks your comment
block before or after it; select the row to read the block in the detail box.

### Bus trace
Every bus access (opcode fetch, read, write, in, out, interrupt) grouped by instruction, with
its meaning: the variable touched, a return address on the stack, what an I/O write does to the
Gate Array, CRTC, PPI or FDC. Steps are always recorded; "Recording" records free runs. The type
chips filter the view.

### Symbols
Every name in one searchable list: firmware names, OS / AMSDOS variables, your labels and
variables, and the names the source generator gave (◇) - with counts per type. Select one for its
detail (where it comes from, the machines it exists on, the instruction there) and to set your
own label, type and comment, and the comment blocks **before** and **after** its line - several
lines each, e.g. a routine header above it or a longer explanation below it; they appear in the
CPU tab's disassembly and as `;` lines in the generated source. "Your symbols" adds labels (sub / jump / data) and variables
(name, size, base: an absolute address or the data area of ROM n, so it follows that ROM).
Save them to the annotation file (`project/annotations.txt`, loaded at the start).

### Disks
Drive chips for A (3", 40 tracks) and B (5.25", 80 tracks, double-sided); per drive the disk,
read-only or writable, "changed" while the CPC's writes are not saved, the head position;
Insert / Eject, Save, Save as…, and new blank drafts (system, data or vortex format). Real images
are opened read-only unless you tick "writable"; Save as… writes a copy. The file list shows the
CP/M directory; a file's detail shows its AMSDOS header, load and entry address and the code at
the entry.

### Project
- **Machine**: model, configuration file, ROMs, drives. **Edit** opens the editor for everything
  in a machine.toml: model, ROM slots (file, decoder, analyse, what the ROM header says), drives,
  RAM ranges to analyse, binary files to load. Load / Save / Save as… write a commented
  machine.toml; **Apply** starts a new machine from it (only active when something that makes the
  machine changed).
- **Binary files to load**: files from your PC with their RAM address, loaded with their **Load**
  button whenever it suits (the boot clears RAM, so they are never loaded automatically). The
  light turns green when loaded and red again after a reset or a new machine.
- **Analysis**: the targets and their phases (section 4).
- **Snapshot**: save and load `.sna` files (versions 1-3; compatible with other emulators).

## 4. A typical analysis session

The Project tab's **Analysis** section leads through an analysis in numbered phases. Each phase
has a status badge, its buttons and a line of detail; you work through them from top to bottom,
and come back to the earlier ones as you learn more:

| # | Phase | Badge | Question it answers |
|---|---|---|---|
| 1 | Collect | collecting / analysis off | What has the code done while it ran? |
| 2 | Decode | no real conflicts / n real conflicts | How was each byte of a scrambled ROM read? (only for a ROM with a decoder) |
| 3 | Save analysis | changed / saved / nothing collected | Is what was collected safe in `project/`? |
| 4 | Generate source | written / not yet | What does the source look like now? |
| 5 | Verify | identical ✓ / n differences / not verified | Does the source rebuild the ROM byte for byte? |

A ROM without a decoder has no Decode phase; its phases are numbered 1-4.

### Before you start: choose the target

A target is what you analyse: a **ROM slot** or a **range of RAM**. Everything else runs
normally and is not analysed.

- **A ROM**: put the image into `roms/`, then in Project → Edit add it to a slot ("+ ROM in
  slot"), tick **analyse** - and choose the decoder **vortex** if the ROM is scrambled with the
  Vortex X-module scheme - and press **Apply**. The machine restarts with the ROM installed.
- **Code in RAM** (a program loaded from disk, a loader, code a ROM copies or unpacks): add a
  "RAM code to analyse" range in the editor (name, from, length) and Apply. Its bytes are captured
  when the CPU first runs code inside the range ("when it first runs"), or when you press **Take
  copy now** in the Collect phase ("on command") - choose the latter if the code is loaded in
  several steps.

The target appears as a chip at the top of the Analysis section; with several targets, the chip
selects whose phases are shown.

### Phase 1 - Collect

Make the code run, as completely as you can. With **Analysis on** (the switch next to the target
chips, or the tool bar) every instruction executed in the target is recorded, and so is code the
ROM copies into RAM and runs there, the ROM's calling conventions (inline data after restarts,
far calls) and where it reads data.

What to do: boot the machine (the ROM initialises), then use everything the ROM offers - its
commands (RSX `|NAME` commands of an expansion ROM), disk access, error cases, different models
(another configuration). Each path you trigger becomes known code.

What you see: the **coverage bar** - code (amber), data (purple), unknown (grey) bytes of the
target - and a line of what was seen: instruction starts, bytes run from RAM copies, blocks copied
to RAM. Code is also found statically: from what ran and from the ROM's header, cpcxray follows
jumps and calls into code that has not run yet. While the machine runs, the bar follows every
couple of seconds.

Analysis on costs about half the emulation speed; switch it off when you only use the machine.

### Phase 2 - Decode (scrambled ROMs only)

A scrambled ROM stores its opcodes as they are but its data scrambled; the CPU decodes data reads.
So a byte's clear value depends on *how* it is read, and cpcxray records that for every byte in
the **access map**.

What you see: a bar of how the bytes were read - **opcode (M1)** (stored raw), **data** (decoded),
**both** (a conflict) and **never read** - and the number of conflicts. A *real* conflict is a
byte read both ways where the two readings differ: a plain replacement ROM can hold only one value
there. **Conflicts…** lists them with the address, both values and the first PC that read them.

What to do: collect until "never read" is as small as you can get it; look at the real conflicts
(often a table read as data that also happens to be executed, or self-modifying tricks). Then:

- **Save map** / **Load map**: the access map in `project/<rom>.m1map` (it is also merged at
  every start); Load map merges a map from another run or machine.
- **Exports** with a **fill policy** for the never-read bytes: `static` (guess from the code's
  structure: opcode bytes of code found statically stay raw, everything else is decoded - the
  usual choice), `raw`, `data` (decode everything) or `ff`:
  **Clear ROM** (`project/<rom>_clear.rom`, a plain ROM that runs without decoding),
  **ROM map** (`project/<rom>_rom_map.html` and `.txt`: every byte coloured by how it was read),
  **Coverage CSV** (one row per byte), **Decode from map** (decode the ROM file with the saved map,
  without running).

### Phase 3 - Save analysis

Everything collected is kept in `project/`: the analysis database (`<rom>.analysis.json`: what
each byte is, what was observed, your own classifications, relocated blocks) and the access map.
The badge says **changed** while there is something new; **Save** writes it, **Load** merges a
saved database (e.g. from another project folder). The window also saves when it closes.

The next session loads it again and adds to it - so you can collect in many short sessions, with
different models and disks, and the knowledge accumulates.

### Phase 4 - Generate source

**Generate** writes a sjasmplus source of the target, by default `project/<rom>.asm` (another file
in the field; the fill policy as in Decode). It contains:

- instructions where code was seen or found, data as bytes, words and strings;
- labels by use - `sub_c2a0` (called), `l_c1e8` (jumped to), `d_c015` (data), `rsx_dir` and
  `rom_init` (the ROM header's commands) - and the ROM header itself;
- firmware names (`TXT_OUTPUT`) and OS / AMSDOS variables (`TXT_CURSOR_COLUMN+1`) for addresses
  outside the target, each with an `EQU`;
- code the ROM copies into RAM and runs there, assembled at its RAM address (`DISP`) so its jumps
  and pointers are readable;
- your labels, comments and variables (Symbols tab) - they replace the generated names.

The detail line counts lines, labels and outside names.

### Phase 5 - Verify

**Verify** assembles the source with sjasmplus and compares the result byte for byte with the
clear image of the target. **identical ✓** means the source is a faithful, reassemblable copy of
the ROM. Otherwise the badge shows the number of differing bytes and the detail lists the first
ones: address, expected and assembled value, and the source line. Needs sjasmplus on the PATH.

### Refine and repeat

A first source is rarely the end. Typical next steps, then Generate and Verify again:

- **Name things** in the Symbols tab: give `sub_c4d8` a real name (`read_sector`, type sub), name
  jump targets and tables, add comments - a short one at the end of the line, a header block
  before a routine, an explanation after a line - define the ROM's own RAM variables (a size and a base -
  for a background ROM best "the data area of ROM n", which moves with the ROMs installed). Save
  them (Symbols tab → Save): they go into `project/annotations.txt`.
- **Collect more** where the coverage bar still shows unknown bytes: trigger the paths that have
  not run yet.
- **Correct a classification** where the automatic one is wrong (e.g. a table taken for code):
  the MCP tool `classify` sets code, string, word table (addresses, written with their names),
  word data (numbers such as parameters or offsets, always in hex) or byte data for a range -
  your choice always wins over the observed and the guessed one.

A session therefore looks like: start `cpcxray gui` in the project folder → the target's phases
show where you left off → collect (1), check the decoding (2), save (3) → generate (4) and verify
(5) → name and correct → generate and verify again → close the window (the analysis is saved;
save your symbols first).

## 5. File formats

**machine.toml** (in `config/`; the Project tab's editor writes it):

```toml
[machine]
model = "6128"              # 464, 664 or 6128
unpopulated_rom = 0         # the slot that answers for empty ROM numbers

[rom.lower]
file = "roms/os6128.rom"

[rom.upper.7]               # upper slots 0-15
file = "roms/amsdos.rom"
# decoder = "vortex"        # a scrambled ROM (always decoded)
# analyse = true            # an analysis target

[drive.A]                   # A: 3" (40 tracks), B: 5.25" (80 tracks, 2 sides)
file = "disk/my_disk.dsk"
writable = false            # the image file itself is only written with Save

[[analysis.ram]]            # RAM code to analyse
name = "loader"
start = 0x8000
length = 0x1000
capture = "first_m1"        # or "manual"

[[load]]                    # a binary file to load into RAM (with its Load button)
file = "bin/game.bin"
address = 0x8000
```

**Annotation file** (`project/annotations.txt`): one entry per line, `#` starts a comment.

```text
sub  BB5A        my_output       ; a label of type sub (sub, jump or data), a comment
data rom5:C015   messages        ; scoped to upper ROM 5 (or "lower")
8000             -               ; a comment only
var  A000        frame_count  2  ; a variable: address, label, size
var  rom5+0012   drives      16  ; at an offset in ROM 5's data area
before rom5:C4D8 ; read_sector: reads one sector into the buffer
before rom5:C4D8 ;   in:  D = track, E = sector
after  rom5:C4D8 ; a comment block after the line
```

A comment block is one `before` or `after` line per text line, in order; the text after `; ` is
kept as it is (indentation too), `;` alone is an empty line. Older files load unchanged.

**Outputs** in `project/`: `<rom>.analysis.json` (analysis database), `<rom>.m1map` (access map,
16 KB, one byte per ROM byte), `<rom>.asm` (the source), `<rom>_clear.rom`, `<rom>_rom_map.html`
/ `.txt`, `<rom>_coverage.csv`.

## 6. Troubleshooting

- **Keys do not reach the CPC**: a text box has the keyboard; click on the screen or an empty
  place.
- **A character is not typed**: the CPC has no key for it (the status line names it).
- **The panels do not change while the machine runs**: they are frozen while running to keep the
  emulation fast; pause or step. Devices and Project follow a running machine.
- **Verify reports "sjasmplus is not on the PATH"**: install sjasmplus (install guide, section 5).
- **Slow**: switch Analysis off when you do not analyse (about twice as fast), and Trace runs /
  Recording off; "Max speed" runs faster than real time.
- **Installation and start problems**: see the install guide.
