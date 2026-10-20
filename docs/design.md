# CPC X-Ray — Reverse-Engineering Emulator for the Amstrad CPC

**Document:** docs/design.md
**Version:** 1.0 (2026-10-20)
**Target environment:** Linux, Python 3.12+, Dear PyGui, VS Code + Claude Code
**Related documents:** `docs/vortex_decrypt.md` (Vortex ROM scrambling), `docs/cpc_rst.md` (CPC RST
instructions and output format)

---

## 1. Purpose and scope

CPC X-Ray (binary and package: `cpcxray`) is a **software-compatible Amstrad CPC 464 emulator built for reverse engineering**. Its
primary job is to descramble and disassemble the **Vortex X-Module ROM (VDOS)**, a 16 KB upper
background ROM for the Vortex F1-X disk drive system. It can also analyse other code that the owner
selects (another ROM or a program in RAM).

The VDOS ROM is stored **scrambled**. The original hardware decodes bytes on the fly, depending on
whether the CPU performs an **opcode fetch (M1 cycle)** or an ordinary memory read. The emulator
runs the real CPC firmware, BASIC, AMSDOS and VDOS ROMs, applies the same decoding on every read of
the scrambled ROM, and records the result, so that a fully descrambled ROM image and a
reassemblable source can be produced.

**This is not** a cycle-exact or hardware-exact CPC emulator. Only those parts of the hardware are
modelled that the software needs to behave correctly, and only at the level of their effect on the
software.

### 1.1 Deliverables of the tool

1. A running CPC 464 environment that boots the original ROMs including VDOS.
2. A **descrambled (clear) VDOS ROM image**, dumpable as a binary file for a new ROM.
3. A **complete sjasmplus source** of the analysed code that reassembles byte-identically.
4. A **switchable live trace** of executed instructions.
5. A debugger: screen, registers, memory, disassembly, device state, bus/M1 trace, breakpoints.
6. An **MCP server**, so Claude Code can drive the emulator completely as a testbed.

---

## 2. Requirements

### 2.1 CPU

| ID | Requirement |
|---|---|
| FR-CPU-01 | Z80 emulation that is **100% software compatible** with a physical Z80 (same results and flags, including undocumented behaviour). It is not hardware/cycle compatible. |
| FR-CPU-02 | The CPU passes **raxoft/z80test** (https://github.com/raxoft/z80test), all variants, including undocumented flags, MEMPTR and SCF/CCF behaviour. |
| FR-CPU-03 | Each instruction executes as one atomic step. No T-cycle or M-cycle simulation. |
| FR-CPU-04 | Every bus access carries its signals **M1**, **MREQ**, **IORQ**, **RD**, **WR**, so observers can distinguish opcode fetches, memory reads/writes and I/O. **M1 is mandatory**: it drives the decoding of scrambled ROMs. |
| FR-CPU-05 | Maskable interrupts (INT line, IM 0/1/2, EI delay, HALT, RETI/RETN) and NMI. |
| FR-CPU-06 | All registers are readable and writable by the debugger: main and alternate sets, IX, IY, SP, PC, I, R, IFF1/2, IM, MEMPTR (WZ), Q. |
| FR-CPU-07 | IN and OUT are routed to the machine's I/O bus. |

### 2.2 Machine (CPC 464)

| ID | Requirement |
|---|---|
| FR-MCH-01 | 64 KB RAM, a lower ROM (OS firmware) and upper ROMs. |
| FR-MCH-02 | ROM enable/disable via the Gate Array (OUT &7Fxx, function 10: bit 2 = lower ROM off, bit 3 = upper ROM off). |
| FR-MCH-03 | Upper ROM select via `OUT (C),C` with `BC = &DFnn`. |
| FR-MCH-04 | ROM selection behaves like individual CE/OE lines per ROM chip: the selected ROM is read. ROMDIS and physical limits on the number of ROMs are not modelled. |
| FR-MCH-05 | Upper ROM slots 0–15 can each be loaded with a ROM file. Defaults: slot 0 = BASIC 1.0, slot 7 = AMSDOS; VDOS typically in slot 5. |
| FR-MCH-06 | Writes to ROM address ranges always go to the RAM underneath. |
| FR-MCH-07 | Gate Array: only the I/O-visible behaviour (pen, inks, border, screen mode, ROM enables, interrupt generation), stored as parameters in a GA object. |
| FR-MCH-08 | A 300 Hz interrupt and a VSYNC flag (readable at &F5xx bit 0), so the firmware can derive its 50 Hz frame-flyback events. |
| FR-MCH-09 | Keyboard input through the I/O path the firmware uses to read the key matrix. No sound chip, no PPI device. |
| FR-MCH-10 | 6128 RAM banking is later scope; the memory design allows adding it without restructuring. |
| FR-MCH-11 | No additional memory expansions. |

### 2.3 Decoding of scrambled ROMs

| ID | Requirement |
|---|---|
| FR-DEC-01 | A ROM slot can have a **decoder**. For VDOS: **M1 fetches pass unchanged; every other read is decoded** with `raw XOR mask`, where mask bit &20 is set if A2 = 1 and mask bit &08 is set if A4 = 1 (§5.5). |
| FR-DEC-02 | Decoding can be **switched on and off per ROM**, in the config and at runtime. |
| FR-DEC-03 | For scrambled ROMs only: a **shadow ROM** holds the clear value of every byte read, and an **M1/data access map** records for every byte whether it was fetched with M1 or read as data. |
| FR-DEC-04 | The access map can be saved, loaded and merged; the clear ROM can be **decoded from raw ROM + map** without running the emulator. Unknown and conflicting bytes are visible directly in the map. |
| FR-DEC-05 | The clear ROM can be **dumped** as a binary file usable in a plain ROM, with a selectable policy for never-read bytes. |
| FR-DEC-06 | A **ROM map** clearly highlights never-accessed (undecoded) areas, alongside M1, data, conflict and executed-via-RAM bytes. |

### 2.4 Analysis targets

| ID | Requirement |
|---|---|
| FR-ANA-01 | The owner defines **what is analysed**: any ROM slot (including the lower ROM) or a RAM range. **Nothing is analysed by default** except what the configuration marks; the standard ROMs (OS, BASIC, AMSDOS) are never analysed by default. |
| FR-ANA-02 | Targets can be set in the config and at runtime; several targets may be active at once. |
| FR-ANA-03 | Code that an analysis target copies into RAM and executes there is **attributed to its origin** and **disassembled as code at its RAM execution address**, so absolute jumps, calls, references and labels are correct. |
| FR-ANA-04 | RAM-range targets are analysed on a captured image; later changes to the range are detected and shown. |
| FR-ANA-05 | A **switchable instruction trace** (text file, CSV, JSON Lines, GUI panel), filterable by target and address range. |

### 2.5 CPC-aware disassembly and reassemblable source

| ID | Requirement |
|---|---|
| FR-DIS-01 | The disassembler follows the **Amstrad CPC RST conventions** (§6.15): inline bytes after RST 1, 2, 3 and 5 are data, never code, emitted as `defw` with explanatory comments. |
| FR-DIS-02 | **Far pointers:** for RST 3 the inline word points to a 3-byte far address object; the comment shows target and ROM selection; the object itself is rendered as `defw` + `defb` with comments and a label. |
| FR-DIS-03 | Comments decode the bit fields: RST 1 target and ROM state, RST 2 target and ROM selector, RST 5 target. RST 0, 4, 6, 7 carry the function name only. |
| FR-DIS-04 | A **complete sjasmplus source** of each analysis target, with labels and comments, covering every byte exactly once. |
| FR-DIS-05 | Assembling the source with **sjasmplus** reproduces the (clear) target **byte for byte**; a built-in round-trip check verifies this. |

### 2.6 Disk (later phase; the design must foresee it)

| ID | Requirement |
|---|---|
| FR-FDC-01 | DDI-1 interface: µPD765-compatible FDC at &FB7E/&FB7F, motor latch at &FA7E, driven by AMSDOS. |
| FR-FDC-02 | Drive A: 3", single sided, 40 tracks, `.dsk`/EDSK images. |
| FR-FDC-03 | Drive B: Vortex 5.25" DD, 80 tracks, **double sided (2 heads)**, 9 sectors per track; raw or (E)DSK images. |
| FR-FDC-04 | Disk images are readable and writable. |
| FR-FDC-05 | FDC register interface (MSR, data register; command, execution, result phases) and data transfer. A stub is acceptable in the early phases. |

### 2.7 Display and debugging

| ID | Requirement |
|---|---|
| FR-DBG-01 | Screen output of video memory in **MODE 0, 1, 2** with palette and border, honouring the CRTC start address (R12/R13). No raster timing. Rendering is **asynchronous** to execution. |
| FR-DBG-02 | Register view. |
| FR-DBG-03 | Memory view (CPU view, RAM, any ROM, shadow ROM), with editing. |
| FR-DBG-04 | Disassembly view around PC and at any address; raw and clear bytes for scrambled ROMs. |
| FR-DBG-05 | Device view: Gate Array, CRTC, SystemIO, ROM selection, interrupt state; later FDC. |
| FR-DBG-06 | Run, pause, step, step over, step out, run to address, reset. |
| FR-DBG-07 | Breakpoints: PC; M1 at address/range; memory read/write at address/range ("who accesses this byte": stops after the accessing instruction and reports its PC); I/O by mask; ROM-select change; interrupt accepted. Every address-based breakpoint takes a location qualifier: `any` (whatever is paged in), `rom` (any ROM), `rom:<n>` / `lower` (one ROM), `ram` (RAM only), `ram:<page>` (one RAM bank, for 6128 banking later). |
| FR-DBG-08 | Bus trace view of M1/MREQ/IORQ/RD/WR accesses (switchable ring buffer). |
| FR-DBG-09 | Symbols and comments (annotation file), with firmware jumpblock names preloaded. |
| FR-DBG-10 | Snapshots in `.sna` format (save and load). |

### 2.8 MCP interface

| ID | Requirement |
|---|---|
| FR-MCP-01 | The emulator provides an **MCP server**, so Claude Code / Claude can use it as a testbed and drive it completely. |
| FR-MCP-02 | **Feature parity:** everything possible in the emulator is available as an MCP tool. |
| FR-MCP-03 | Works **headless** (started by Claude Code) and **attached to a running GUI instance**. |
| FR-MCP-04 | Every call returns in bounded time; run commands take limits and report why they stopped. |
| FR-MCP-05 | Large results are paged or written to files, with the path returned. |

### 2.9 Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | Implementation language **Python**. |
| NFR-02 | GUI with **Dear PyGui**. |
| NFR-03 | Not cycle exact; timing only needs to be good enough for the firmware (interrupts, frame flyback, delay loops). |
| NFR-04 | The emulation core runs headless and is usable from scripts and pytest. |
| NFR-05 | Tracing and observers cost (nearly) nothing when switched off. |
| NFR-06 | Every component is testable in isolation. |
| NFR-07 | ROM images are not committed to the repository; paths come from the config. |
| NFR-08 | English BASIC 1.0 and the CPC 464 firmware. |
| NFR-09 | Development and target platform: **Linux**. |

---

## 3. Decisions

| ID | Decision | Rationale |
|---|---|---|
| D-01 | **Python 3.12+** throughout. The CPU is a pure-Python module behind a stable interface. | Owner preference; debugging time dominates, not speed. The CPU can later run under PyPy, be compiled (mypyc/Cython) or be replaced by a native module without touching other components (§7). |
| D-02 | **Dear PyGui** for the frontend. | Immediate mode, fast, suits many debug panels plus a screen texture. |
| D-03 | **Instruction-level stepping**; the bus accesses of each instruction are emitted in the correct order with their signals. | Satisfies FR-CPU-03/04. Decoding needs correct M1 attribution and ordering, not timing. |
| D-04 | **Time base: 1 µs = 1 "NOP" = 4 T-states.** Each instruction costs a whole number of NOPs from a per-opcode table (e.g. NOP 1, LD A,(HL) 2, CALL nn 5, LDIR 6 per repeat, extra cost when a condition is taken). Interrupts are accepted only at instruction boundaries. | On the CPC every instruction is stretched to whole NOPs. One tick per instruction would distort timing by a factor of 3–5; the table lookup costs almost nothing. |
| D-05 | **Simplified interrupt and VSYNC model:** INT is latched every 3,328 µs (300 Hz) and cleared on acknowledge; GA function 10 bit 4 clears a pending INT and restarts the period. VSYNC = `(clock_us % 19968) < 512`, computed when &F5xx is read. No scan lines, no GA line counter, no resynchronisation. | One frame (19,968 µs) is exactly 6 interrupt periods, so the first interrupt of each frame always sees VSYNC, which is all the firmware needs (frame flyback, `MC WAIT FLYBACK`). |
| D-06 | **CRTC is a register file only**, no timing. The renderer uses R1, R6, R9, R12, R13. | The firmware scrolls via R12/R13; ignoring them garbles the display. |
| D-07 | **One `SystemIO` device replaces PPI and sound chip.** It handles port B reads (configurable constant + VSYNC bit) and the keyboard read sequence (§6.9); the key matrix is a 10-byte row array. All other PPI/PSG writes are ignored. | About 20 lines of code. The firmware decodes key rows itself, so it must see row data; trapping firmware keyboard calls would be fragile because expansion ROMs patch jumpblocks. |
| D-08 | **Partial I/O decoding, broadcast to all devices**; each device checks its own address bits. | This is how the CPC decodes ports; one OUT can hit several devices. |
| D-09 | **ROM selection without ROMDIS:** a table `rom_number → RomSlot` for slots 0–15; the last value written to &DFxx selects the slot, the GA upper-ROM-enable bit decides visibility. Slots can be loaded/unloaded at runtime (followed by a reset). | Owner's CE/OE model. Slots 8–15 are for later 6128 use; the 464 firmware's ROM walk only covers 0–7, so 8–15 are reachable only when selected explicitly. |
| D-10 | **Unpopulated ROM numbers (and values above 15) → slot 0 (BASIC) answers**, as on a real 464. | The firmware's ROM scan must see the same result as on real hardware. |
| D-11 | **Per-slot decoder and decode switch.** The decoder is a pure function `decode(addr, raw)`; the slot applies the M1 rule (M1 → raw, otherwise decoded). `decode_enabled` is set in the config and can be toggled at runtime, effective from the next read. | The decode function knows nothing about M1; the decision is made where the access kind is known. Toggling lets the owner compare scrambled and clear behaviour live. |
| D-12 | **Bus observers are opt-in.** The bus has an unobserved fast path; observer variants are swapped in when observers are registered. | Satisfies NFR-05 without per-access checks. |
| D-13 | **Single-threaded emulation in time slices** (GUI mode: in the GUI thread, e.g. 20 ms emulated per frame). Other threads (MCP server) communicate only through a command queue (D-29). | No locking; the renderer reads a consistent snapshot. |
| D-14 | **6128 RAM banking designed in** (per-16 KB read/write page tables), not implemented now. | FR-MCH-10. |
| D-15 | **CPU verification in three layers:** per-opcode JSON tests, ZEXDOC/ZEXALL, raxoft z80test. | Fast, precise failure localisation first; the acceptance test last. |
| D-16 | **Analysis targets are explicit.** A target is a ROM slot (upper 0–15 or lower ROM) or a RAM range. Targets are marked in the config (`analyse = true`, `[[analysis.ram]]`) or set at runtime (GUI, debugger API, MCP). Several targets may be active; each has its own analysis database and source output. Everything else runs normally and is not analysed. The default config marks only VDOS. The live disassembly view works on any address. | The owner decides what is analysed. The standard ROMs are fully documented; analysing them would only cost time and produce noise. |
| D-17 | **Shadow ROM and M1/data access map exist only for scrambled ROMs** (targets with a decoder; in practice VDOS). Unscrambled targets are disassembled directly; their code/data distinction comes from the per-byte data kind in the analysis database. | Their purpose is decoding; for clear code they would only duplicate information. |
| D-18 | **Conflicts:** a byte of a scrambled ROM read both as M1 and as data is an error in the access map. It is a *real* conflict where the decode mask is non-zero (A2 or A4 set), because a plain replacement ROM can hold only one value; at mask-zero addresses it is harmless. Both values are kept and reported. | Such bytes cannot be represented in the clear ROM and need the owner's decision. |
| D-19 | **Access map format:** raw binary file `<rom>.m1map`, exactly 16,384 bytes, byte *i* describes ROM offset *i*: `&7F` unknown (initial), `&01` M1, `&00` data, `&FF` error. Optional range list export as text/JSON. Only observed facts are stored. | Smallest and fastest form; loads into a `bytearray` in one call, lines up 1:1 with the ROM in a hex editor, `cmp` shows differences. Per-address JSON would be ~25× larger. Guesses stay in the analysis database, so the map never mixes certainty with assumptions. |
| D-20 | **Dump fill policy is selectable per dump** for never-read bytes: `raw`, `data` (decoded as if data), `ff`, `static` (code/data guess by recursive disassembly). Every dump comes with a **ROM map** highlighting never-accessed areas. | Never-read bytes cannot be decoded with certainty, because decoding depends on the access type. |
| D-21 | **Automatic RAM provenance tracking for analysis targets.** A RAM byte written with a value read from a target carries the tag (target, offset, exact/probable). Block moves (LDI/LDIR/LDD/LDDR) give exact tags; copies via registers give probable tags. An M1 fetch from tagged RAM marks the origin bytes as executed via RAM and as instruction starts. | Code that only runs from RAM is never M1-fetched from the ROM; without provenance it would appear as data and could not be linked to ROM addresses, labels and comments. |
| D-22 | **Relocated code blocks:** consecutive provenance tags with a constant offset form a block (target, ROM start, length, RAM start), stored in the analysis database and editable by the owner. Views, listing and source generator disassemble the block **at its RAM address**; the source wraps it in sjasmplus `DISP ram_start` … `ENT`. | Absolute JP/CALL targets, pointers and labels in copied code are only meaningful in RAM coordinates. `DISP` assembles for the RAM address while placing the bytes at the ROM position, so the ROM still reassembles byte-identically. |
| D-23 | **RAM-range targets** are analysed on an image captured on command or at the first M1 fetch inside the range. Source `ORG` = range start. Later writes into the range are flagged with their runtime value; the owner can recapture. Provenance and relocation do not apply. | RAM code can be loaded, unpacked or patched at runtime; the bytes to disassemble must be fixed at a defined moment, and later changes must be visible. |
| D-24 | **Trace sinks:** text file, CSV, JSON Lines, GUI panel; each switchable. | Text for reading, CSV/JSON for scripts, panel for live work. |
| D-25 | **Snapshots in `.sna` format only.** Analysis state (access map, analysis database) is saved in its own files. | `.sna` holds machine state only and stays compatible with other emulators. |
| D-26 | **Symbols and annotations from the start:** a project file with labels and comments per address (ROM labels scoped to a slot); firmware jumpblock names preloaded; used by views, trace, listing and source. | Readable traces and listings from day one. |
| D-27 | **One command façade:** a `Session` class (the debugger API) is the only way to act on the machine. GUI, MCP server and scripts are thin clients; MCP tools map 1:1 onto `Session` methods. A parity test fails when a method has no MCP tool. | Guarantees FR-MCP-02: each feature exists once. |
| D-28 | **MCP transports:** stdio for headless mode (Claude Code starts `cpcxray mcp`); streamable HTTP on 127.0.0.1 for GUI mode. Official Python MCP SDK (`mcp`). | stdio is how Claude Code launches tool servers; HTTP lets Claude attach to the instance the owner is using. Localhost only, because the server can load files and run code. |
| D-29 | **MCP calls go through a command queue** and are executed between emulation time slices; a running machine pauses while a command needs a consistent state. | Keeps the emulator single-threaded (D-13). |
| D-30 | **CPC machine profile for the disassembler:** a data table per RST number (name, inline format, comment format, control-flow kind), selectable `generic`/`cpc` (§6.15). | All CPC knowledge in one place; the disassembler core stays generic. |
| D-31 | **The sjasmplus source is generated from a persistent analysis database, not from the trace.** One entry per target byte (clear value, access flags, data kind, classification source), plus symbols, comments, owner overrides, relocated blocks and runtime observations. Saved and merged across runs. | The trace is chronological, repeats loops and contains nothing about never-executed bytes; the database has exactly one entry per byte and accumulates knowledge from many runs. |
| D-32 | **Byte-exact encoding rule:** an instruction is emitted as a mnemonic only if sjasmplus re-encodes it to identical bytes; non-canonical encodings (e.g. `ED 6B nn` for `ld hl,(nn)`, duplicate `neg`/`retn`/`im`, redundant prefixes) are emitted as `defb` with the mnemonic in a comment. | Guarantees FR-DIS-05. |
| D-33 | **Built-in round-trip check** (`source_verify`): generate source → sjasmplus → compare byte for byte with the clear dump → report differing addresses. CLI, `Session` method and MCP tool. | Makes "reassemblable" a tested property. |

---

## 4. Items to verify during implementation

| Item | How |
|---|---|
| Exact port B constant (manufacturer bits, 50 Hz bit) expected by the 464 firmware | **Resolved (phase 4):** &1E boots to "Amstrad 64K Microcomputer" / BASIC 1.0; stays configurable. |
| RST control-flow kinds used by the static classifier (§6.15) | Only relevant for never-executed code; check against observed behaviour. |
| Firmware indirection KM SCAN KEYS (&BDF4) on the 464 | **Resolved (phase 4):** absent on the 464 (RAM at &BDF4 is empty after boot, &BDCD-&BDF1 hold JPs); dropped from the 464 symbols. |

---

## 5. Hardware background

### 5.1 Memory map (CPC 464)

| Range | Read | Write |
|---|---|---|
| &0000–&3FFF | Lower ROM (OS) if enabled, else RAM | RAM |
| &4000–&BFFF | RAM | RAM |
| &C000–&FFFF | Selected upper ROM if enabled, else RAM (screen) | RAM |

### 5.2 I/O ports (partially decoded)

| Device | Decode | Ports | Function |
|---|---|---|---|
| Gate Array | A15 = 0, A14 = 1 | &7Fxx | Data bits 7–6 select the function. 00: pen select (bit 4 = border). 01: ink colour (bits 0–4). 10 (RMR): bits 1–0 mode, bit 2 lower ROM disable, bit 3 upper ROM disable (1 = disabled), bit 4 interrupt control. 11: RAM config (6128, later). |
| CRTC | A14 = 0 | &BCxx select, &BDxx write, &BFxx read | 6845 register file |
| Upper ROM select | A13 = 0 | &DFxx | ROM number latch |
| PPI 8255 (emulated only as `SystemIO`, D-07) | A11 = 0 | &F4xx A, &F5xx B, &F6xx C, &F7xx control | A: PSG data bus (register number out, keyboard row in). B: bit 0 VSYNC, bits 1–3 manufacturer, bit 4 50 Hz, bit 5 /EXP, bit 6 printer busy, bit 7 cassette — emulated as constant + VSYNC. C: bits 0–3 keyboard row, bits 7–6 PSG function; bits 4–5 cassette (ignored). Control port ignored. |
| FDC (DDI-1, later) | specific | &FA7E motor, &FB7E MSR, &FB7F data | µPD765 |

### 5.3 Timing (D-04, D-05)

| Item | µs | T-states |
|---|---|---|
| Scan line | 64 | 256 |
| Interrupt period (52 lines, 300 Hz) | 3,328 | 13,312 |
| Frame (312 lines, 50.08 Hz) | 19,968 | 79,872 |

- INT: when `clock_us >= next_int_us`, INT is latched and `next_int_us += 3328`. It stays pending
  until accepted (one pending interrupt survives a long DI phase, as on the real machine).
- VSYNC: `(clock_us % 19968) < 512` (first 8 lines of a frame); no event, computed on read.
- Not modelled: GA line counter, resynchronisation at VSYNC, bit-5 clear on acknowledge.

### 5.4 Screen memory layout

For a byte at character row *r*, scan line *s* (0–7) and byte column *x*:

```
base   = (R12 & 0x30) << 10                 # 16K page, &C000 when bits = 11
offset = ((R12 & 0x03) << 8 | R13) * 2      # in bytes, hardware scroll
addr   = base + ((offset + r * R1 * 2 + x) & 0x07FF) + s * 0x0800
```

Defaults: R1 = 40 (80 bytes per line), R6 = 25 rows, R9 = 7 (8 lines per character).

| Mode | Resolution | Colours | Pixels per byte |
|---|---|---|---|
| 0 | 160×200 | 16 | 2 |
| 1 | 320×200 | 4 | 4 |
| 2 | 640×200 | 2 | 8 |

Bit interleaving follows the standard CPC layout; pens map to the 27-colour hardware palette.

### 5.5 VDOS ROM scrambling

Normative parts of `docs/vortex_decrypt.md`: the decoding routine and the M1 rules (the sections on
z80dasm are background only and not relevant to this implementation).

- **Opcodes are stored in clear; operands and data are encrypted.**
- `decode(addr, raw) = raw XOR mask`, mask = &20 if A2 = 1, plus &08 if A4 = 1. Only the low
  address bits matter; the pattern repeats every 32 bytes; stateless and self-inverse.

  | A4 | A2 | Mask |
  |---|---|---|
  | 0 | 0 | &00 |
  | 0 | 1 | &20 |
  | 1 | 0 | &08 |
  | 1 | 1 | &28 |

- M1 follows the real Z80: opcode bytes, prefix bytes (CB, DD, ED, FD) and the opcode after a
  prefix are M1; operands, displacements and immediate data are not. In `DD CB d op` /
  `FD CB d op`, both *d* and *op* are non-M1 and decoded.
- Everything read without M1 is decoded, including what the firmware reads from the ROM (header,
  name table, RSX names, far addresses) and the bytes after an RST.
- Code that VDOS copies to RAM is read without M1, so it arrives in RAM decoded: in the ROM it is
  stored fully encrypted.

### 5.6 CPC restart instructions

Normative: `docs/cpc_rst.md`, except the RST 1 ROM-state bits, which follow the 464 OS ROM source.

The RST 1 handler (ROM &0413, copied to and executed at RAM &B982) reads the inline low address
with `ld e,(hl)` / `ld d,(hl)`, clears bits 15–14 to get the target, rotates the high byte left
four times and merges the result into Gate Array RMR bits 3–2. Hence bit 15 → RMR bit 3 (upper ROM
disable) and bit 14 → RMR bit 2 (lower ROM disable); 1 = disabled. This matches the CPC Firmware
Guide.

| Bits 15–14 | Lower ROM | Upper ROM |
|---|---|---|
| `00` | on | on |
| `01` | off | on |
| `10` | on | off |
| `11` | off | off |

The same handler shows why relocated blocks must be disassembled at their RAM address: its
`call #b9a8` targets the `push de` at ROM &0439 inside the same copied block.

---

## 6. Architecture

### 6.1 Overview

```
┌──────────────────────── Frontend (Dear PyGui, UI thread) ────────────────────────┐
│ Screen │ Disassembly │ Registers │ Memory │ Devices │ Bus trace │ ROM map │ Log     │
└──────────────────────────────────┬────────────────────────────────────────────────┘
          Claude Code ── MCP ──►   │  Session (debugger API) ◄── scripts / pytest
┌──────────────────────────────────▼────────────────────────────────────────────────┐
│ Debugger: run/step, breakpoints, trace sinks, symbols                              │
│ Analysis: targets, access map, shadow ROM, provenance, database, source generator │
└──────────────────────────────────┬────────────────────────────────────────────────┘
┌──────────────────────────────────▼────────────────────────────────────────────────┐
│ Machine (CPC464) — owns everything below, clock in µs                             │
│                                                                                   │
│   Z80 CPU ──bus accesses──► Bus ──► MemoryMap ──► RAM 64K                         │
│     ▲ INT/NMI                │          │      ├─► Lower ROM (OS)                 │
│     └──── interrupt timer ◄──┤          │      └─► Upper ROM slots 0–15           │
│                              │          │              └─ RomSlot(VDOS)           │
│                              │          │                  ├─ decoder + switch    │
│                              │          │                  └─ shadow + M1 map     │
│                              ├──► I/O broadcast ─► GateArray, CRTC, RomSelect,    │
│                              │                     SystemIO (VSYNC, keys), FDC*   │
│                              └──► Bus observers (trace, breakpoints, analysis)    │
└───────────────────────────────────────────────────────────────────────────────────┘
Renderer (async): snapshot(RAM, GA palette/mode/border, CRTC R1/R6/R9/R12/R13) → texture
                                                                       * later phase
```

### 6.2 Z80 CPU (`cpu/`)

**Bus interface** — the CPU only calls these methods; each carries its signals:

```python
bus.fetch(addr) -> int       # M1 + MREQ + RD  (opcode fetch, incl. prefixes)
bus.read(addr) -> int        # MREQ + RD       (operands, data, displacement)
bus.write(addr, v)           # MREQ + WR
bus.io_in(port) -> int       # IORQ + RD
bus.io_out(port, v)          # IORQ + WR
bus.int_ack() -> int         # M1 + IORQ       (data bus for IM 0 / IM 2)
```

**M1 rules** (critical for decoding):

- Opcode and prefix bytes (CB, DD, ED, FD) are fetched with `fetch()`.
- In `DD CB d op` / `FD CB d op` only DD/FD and CB are M1; *d* and *op* use `read()`.
- Interrupt acknowledge is M1 + IORQ, not a memory fetch.
- R increments on every M1 (7 bits, bit 7 preserved); prefixes increment R twice.

**API:**

```python
cpu.step() -> int            # one instruction or interrupt acceptance; returns µs used
cpu.regs                     # register access (debugger read/write)
cpu.int_line / cpu.nmi_line  # set by the machine
cpu.reset()
```

**Implementation notes:**

- Plain int registers; flag lookup tables (SZ, SZP, SZXY …); dispatch tables per prefix group.
- MEMPTR (WZ) and Q tracked explicitly (z80test checks both).
- Undocumented behaviour: IXH/IXL/IYH/IYL, SLL, DDCB/FDCB register-copy forms, ED no-ops,
  block-instruction flags, IFF2 in `LD A,I`/`LD A,R`, EI shadow, HALT.
- Timing table in µs per instruction including the taken-condition surcharge (D-04).

### 6.3 Bus (`machine/bus.py`)

- Routes accesses to the `MemoryMap` and the I/O devices.
- Observers: objects with `on_cycle(kind, addr, value, pc)`,
  `kind ∈ {M1, MEM_RD, MEM_WR, IO_RD, IO_WR, INT_ACK}`.
- Fast path without observers; observer variants are swapped in when observers are registered
  (D-12).
- I/O broadcast to every device whose `decodes(port)` is true; an IN without responder returns &FF
  (D-08).

### 6.4 MemoryMap and ROM slots (`machine/memory.py`)

- `ram: bytearray(65536)`; `lower_rom: RomSlot`; `upper_roms: dict[int, RomSlot]` for slots 0–15;
  fallback to slot 0 for unpopulated numbers (D-10).
- Mapping state: `lower_enabled`, `upper_enabled` (GA RMR), `selected_upper` (&DFxx).
- Read path: a 4-entry table `read_bank[addr >> 14]` (RAM view or ROM slot), recomputed only when
  the GA configuration or the ROM selection changes. Writes always go to RAM; 6128 banking later
  adds a write table (D-14).
- The read path passes `is_m1` to the ROM slot: `bus.fetch()` → `True`, `bus.read()` → `False`.

```python
class RomSlot:
    data: bytes
    decoder: Callable[[int, int], int] | None   # e.g. vortex_decode (D-11)
    decode_enabled: bool                         # config default, runtime toggle
    shadow: ShadowRom | None                     # only for scrambled ROMs (D-17)

    def read(self, offset: int, is_m1: bool) -> int:
        raw = self.data[offset]
        decoding = self.decode_enabled and self.decoder is not None
        value = self.decoder(offset, raw) if decoding and not is_m1 else raw
        if self.shadow is not None:
            self.shadow.record(offset, value, is_m1, decoding)
        return value
```

### 6.5 Decoding, shadow ROM and access map (`decoding/`) — scrambled ROMs only

```python
def vortex_decode(addr: int, raw: int) -> int:
    mask = (0x20 if addr & 0x04 else 0) | (0x08 if addr & 0x10 else 0)
    return raw ^ mask
```

For speed it can be a precomputed 32 × 256 table indexed by `(offset & 0x1F, raw)`. ROM offsets and
CPU addresses share the low bits, because ROMs start at 16 KB boundaries.

**Shadow ROM (16 KB):** the clear value of every byte read with decoding enabled (raw for M1,
decoded for data), with `first_pc` and `hit_count` for diagnostics. For conflicts both values are
kept (D-18). Reads made with decoding switched off are recorded as a separate class and never
enter the clear values.

**M1/data access map (D-19):**

| Value | Meaning | Set when |
|---|---|---|
| `&7F` | unknown | initial value |
| `&01` | M1 (stored in clear) | fetched with M1 |
| `&00` | data (encrypted) | read without M1 |
| `&FF` | error | seen both as M1 and as data |

- Update rule per read: `&7F` → `&01`/`&00`; same class → unchanged; other class → `&FF`. The same
  rule merges maps (unknown + X = X; M1 + data = `&FF`; anything + `&FF` = `&FF`).
- The access class is independent of the decode switch.
- `&FF` is reported as harmless where A2 = A4 = 0, otherwise as a real conflict (D-18).
- Code copied to RAM is `&00` (read as data); its code nature is recorded via provenance (§6.6).
- Storage: `<rom>.m1map`, 16,384 bytes; loaded and merged at start; range export, e.g.

  ```
  C000-C003  data      4
  C004-C0FF  M1      252
  C100-C17F  unknown 128
  ```

- Decoding from the map, without an emulator run:

  ```python
  def decode_rom(raw: bytes, m1map: bytes, fill_policy) -> bytes:
      out = bytearray(raw)
      for i, cls in enumerate(m1map):
          if cls == 0x00:   out[i] = vortex_decode(i, raw[i])    # data
          elif cls == 0x01: pass                                  # M1: already clear
          else:             out[i] = fill_policy(i, raw[i], cls)  # unknown / error
      return bytes(out)
  ```

**Exports:**

- `rom_dump(path, fill_policy)`: 16 KB clear ROM; fill policy `raw` / `data` / `ff` / `static`
  for never-read bytes (D-20).
- `rom_map(path)`: colour-coded HTML page plus text summary; per byte: never accessed, M1, data,
  conflict, executed via RAM; never-accessed ranges listed with byte counts. Also a live GUI
  panel.
- `coverage_csv(path)`: per-byte flags and values for scripts.

### 6.6 Analysis targets, provenance and relocated blocks (`analysis/`)

**Targets (D-16):** ROM slots or RAM ranges, from the config or set at runtime
(`analysis_target_add/remove/list`). Each target has an analysis database and its source output;
scrambled ROM targets additionally have shadow ROM and access map (D-17).

**RAM-range targets (D-23):** an image is captured on command (`analysis_capture`) or at the first
M1 fetch in the range. Writes into the range after capture are flagged `MODIFIED_AFTER_CAPTURE`
with the runtime value; the owner can recapture.

**RAM provenance (D-21)** — for ROM targets:

- `ram_origin`: 64 K entries, each `None` or `(target, offset, exact)`.
- An observer pairs, within one instruction, a target data read with the following RAM write
  (block moves) → exact tag; a write storing the value of one of the last few target data reads →
  probable tag; any other write clears the tag.
- An M1 fetch from tagged RAM sets `EXEC_VIA_RAM` on the origin byte and marks it as an
  instruction start (mapped back from the RAM address); the trace shows `RAM &xxxx ← ROM5:&yyyy`.

**Relocated blocks (D-22):**

- Consecutive RAM addresses whose tags point to consecutive offsets of the same target form a
  block `(target, rom_start, length, ram_start)`; stored in the analysis database; the owner can
  add, split or correct blocks (`reloc_list/add/remove`).
- Same range copied to several RAM addresses: all destinations are listed; the one with the most
  executed instructions is used for disassembly, the others appear as comments.
- Different ROM ranges copied to the same RAM address at different times (overlays, e.g. VDOS
  uses &BF00 for several routines): each is its own `DISP` block; their labels name both
  addresses (`ram_bf00_ff4a`); a reference from outside to an overlaid RAM address stays hex.
- Only blocks that executed in RAM (or that the owner added) become `DISP` blocks; a copy that
  never runs is data (e.g. a jump table).
- Copied code modified in RAM before it runs: the tag is cleared, the bytes are flagged
  `MODIFIED_AFTER_COPY`, the listing shows the ROM version with the runtime value as a comment.
- Partially copied ranges: only the copied part becomes a block.
- Views offer "as in ROM" and "as run" (RAM addresses, default).

**Instruction trace (D-24):**

- An observer collects the cycles of one instruction and emits a record: PC; ROM slot or RAM (with
  provenance); raw bytes; clear bytes; mnemonic with symbols; optionally registers.
- Sinks: text, CSV, JSON Lines, GUI ring buffer; each switchable.
- Filters: target, address range, "analysis targets only" (including their RAM copies).

### 6.7 Gate Array (`devices/gate_array.py`)

- Decode: A15 = 0, A14 = 1.
- State: `pen`, `inks[16]`, `border`, `mode` (applied at the next frame for rendering),
  `lower_rom_enabled`, `upper_rom_enabled`, `int_pending`, `next_int_us`, `ram_config` (6128,
  later).
- Interrupt generation per D-05: `tick(clock_us)` latches INT, `int_ack()` clears it.
- Changing the ROM enables notifies the `MemoryMap`.

### 6.8 CRTC (`devices/crtc.py`)

Register select and register write/read only (read-back limited to the registers readable on real
hardware). No timing (D-06).

### 6.9 SystemIO and keyboard (`devices/system_io.py`, `devices/keyboard.py`)

The firmware reads a key row with: OUT &F4xx = 14 and OUT &F6xx with bits 7–6 = 11 (select PSG
register 14); OUT &F6xx = &40 + row; IN &F4xx → row bits (0 = pressed).

```python
class SystemIO:                                # decode: A11 = 0 (D-07)
    port_b_const = 0x1E                        # config: manufacturer + 50 Hz bits (§4)
    port_a = 0xFF                              # last value written to &F4xx
    psg_reg = 0                                # PSG register latched by "select register"
    row = 0                                    # keyboard row from &F6xx bits 0-3

    def io_out(self, port, v):
        sel = (port >> 8) & 3                  # 0=&F4, 1=&F5, 2=&F6, 3=&F7
        if sel == 0:
            self.port_a = v
        elif sel == 2:
            self.row = v & 0x0F
            if v & 0xC0 == 0xC0:
                self.psg_reg = self.port_a & 0x0F
        # &F5 and &F7 writes ignored

    def io_in(self, port):
        sel = (port >> 8) & 3
        if sel == 1:
            return self.port_b_const | self.vsync()
        if sel == 0 and self.psg_reg == 14:
            return self.keyboard.keys[self.row] if self.row < 10 else 0xFF
        return 0xFF
```

- `keys = bytearray([0xFF] * 10)`; a host-key → (row, bit) table sets and clears bits.
- A type-text helper queues key presses with hold/release timing in emulated µs (e.g. `|DIR`).

### 6.10 Machine and scheduler (`machine/cpc464.py`)

```python
def run(self, budget_us: int):
    while budget_us > 0 and not self.debugger.should_stop():
        us = self.cpu.step()
        budget_us -= us
        self.clock_us += us
        if self.clock_us >= self.ga.next_int_us:   # 300 Hz (D-05)
            self.ga.raise_int()
        for dev in self.tickers:                   # FDC later
            dev.tick(us)
```

Responsible for reset, configuration and wiring; provides `snapshot()` for renderer and views, and
`.sna` save/load (D-25).

### 6.11 FDC and disk images (later; `devices/fdc765.py`, `disk/`)

- µPD765 state machine: idle, command, execution, result; MSR bits RQM, DIO, EXM, CB; commands
  Specify, Recalibrate, Seek, Sense Interrupt Status, Sense Drive Status, Read ID, Read Data,
  Write Data, Format Track, others as needed.
- Non-DMA byte transfer; the CPU polls the MSR; no overrun timing (impossible with
  instruction-level stepping).
- Two drive units (US0/US1), each with a `DiskImage` (`heads`, `tracks`, `sectors(track, head)`);
  motor from &FA7E.
- `DskImage`: standard and EDSK, read/write.
- `RawImage` for Vortex: 80 tracks × 2 sides × 9 sectors × 512 bytes = 737,280 bytes; sector IDs
  &01–&09; sides interleaved per track, so sector *id* of track *t*, side *s* is at offset
  `((t * 2 + s) * 9 + (id - 1)) * 512`.
- Vortex format, confirmed on a real VDOS disk (phase 10): sector IDs carry the head number;
  physical interleave 1,6,2,7,3,8,4,9,5; CP/M layout with cylinder 0 (both heads) reserved,
  4 KB blocks with 8-bit block numbers, 128 directory entries in block 0 at cylinder 1, logical
  order cylinder → head 0 → head 1.
- The CPC has no terminal count: a read/write ends after sector EOT with ST0 &40, ST1 &80 ("end of
  cylinder"), which AMSDOS and VDOS expect.
- Real images are inserted read-only (the FDC reports write protection; VDOS: "Diskette hat
  Schreibschutz"); writes go to drafts (`disk_new`) and reach a file only via `disk_save`, never
  over a read-only image's file.
- VDOS's turnkey function reads the directory of A: at boot (looking for HELLO.BAS/HELLO.BIN), so
  the boot takes about 1 s longer with a disk in A:.

### 6.12 Renderer (`ui/renderer.py`)

- Per GUI frame: snapshot → decode per §5.4 → RGBA buffer (e.g. 640×200, lines doubled, plus
  border) → Dear PyGui dynamic texture.
- Precomputed per-mode byte → pixel tables; numpy if available.
- Independent of CPU timing.

### 6.13 Debugger (`debugger/`)

- Breakpoints as bus observers, except PC breakpoints (set lookup before each `step()`): PC; M1 at
  address/range (also per ROM number); memory read/write; I/O by mask/value; ROM-select change;
  interrupt accepted.
- Location qualifier per address-based breakpoint (FR-DBG-07), checked against what the memory
  map has paged in at the access: `any`, `rom`, `rom:<n>`, `lower`, `ram`, `ram:<page>` (464: only
  page 0; 6128 banking in phase 11). PC breakpoints check it before `step()`; memory-access
  breakpoints check it in the observer, so a write to RAM under a paged-in ROM is matched as
  `ram`, a read of the ROM as `rom:<n>`. A memory-access hit finishes the current instruction and
  stops with the accessing PC, access kind, address, value and location in the result.
- Stepping: step, step over (CALL/RST/DJNZ/LDIR → temporary breakpoint after the instruction), run
  to address, step out.
- Disassembler: table-driven, sharing opcode metadata with the CPU; works on any byte source (CPU
  view, RAM, ROM, shadow ROM); CPC profile (§6.15); symbols from the annotation file (D-26), ROM
  labels scoped to a slot.
- Scriptable through `Session` without the GUI (e.g. "boot, type `|CAT`, run until idle, dump").

### 6.14 Configuration (`config/machine.toml`)

```toml
[machine]
model = "464"
unpopulated_rom = 0          # D-10

[rom.lower]
file = "roms/os464.rom"

# Upper ROM slots 0-15 (D-09)
[rom.upper.0]
file = "roms/basic10.rom"

[rom.upper.5]                # typical VDOS slot
file = "roms/vortex_x.rom"
decoder = "vortex"           # M1 raw, data reads decoded (§5.5)
decode = true                # initial state of the decode switch (D-11)
analyse = true               # analysis target (D-16)

[rom.upper.7]
file = "roms/amsdos.rom"

# Optional RAM-range analysis targets (D-23)
# [[analysis.ram]]
# name    = "loader"
# start   = 0x8000
# length  = 0x1000
# capture = "first_m1"       # or "manual"
```

### 6.15 CPC profile, analysis database and source generator (`analysis/`)

**CPC RST profile (D-30):**

| RST | Mnemonic | Opcode | Name | Size | Inline data | Control flow |
|---|---|---|---|---|---|---|
| 0 | `rst #00` | &C7 | RESET | 1 | — | no return |
| 1 | `rst #08` | &CF | LOW JUMP | 3 | low address: bits 15–14 ROM state (§5.6), bits 13–0 target | jump |
| 2 | `rst #10` | &D7 | SIDE CALL | 3 | side address: bits 15–14 ROM selector 0–3 relative to the current ROM, bits 13–0 + &C000 = routine | call |
| 3 | `rst #18` | &DF | FAR CALL | 3 | pointer to a 3-byte far address object | call |
| 4 | `rst #20` | &E7 | RAM LAM | 1 | — (address in HL) | normal |
| 5 | `rst #28` | &EF | FIRM JUMP | 3 | jump target address | jump |
| 6 | `rst #30` | &F7 | USER RESTART | 1+ | user-defined; default none, configurable per target in the annotation file | normal |
| 7 | `rst #38` | &FF | INTERRUPT | 1 | — | must not be executed |

- The bytes after an RST are **always ordinary data reads** of the firmware handler (e.g.
  `ld e,(hl)` / `ld d,(hl)`), so they are always decoded when decoding is active and never treated
  as code. An observer that sees "RST *n* executed at X" tags X+1/X+2 as `RST_OPERAND(n)` and, for
  RST 3, the 3 bytes at the pointer as `FAR_ADDR`.
- The control-flow column is used only by the static classifier for never-executed code.
- `&FF` in unknown areas is never treated as code; an executed `rst #38` is flagged.

**Output format:**

```asm
        rst #00             ; RESET
        rst #08             ; LOW JUMP
        defw 08150h         ;   to #0150 [lower ROM on, upper ROM off]
        rst #10             ; SIDE CALL
        defw 04150h         ;   to #C150 [slot 1]  (observed: ROM 6)
        rst #18             ; FAR CALL
        defw far_c7f3       ;   to SCR_SET_MODE [ROM 7]
        rst #20             ; RAM LAM
        rst #28             ; FIRM JUMP
        defw sub_0038       ;   to #0038

far_c7f3:
        defw 0bb5ah         ; far address: to SCR_SET_MODE
        defb 007h           ;   ROM 7
```

- RST 1: the ROM state is spelled out in words (§5.6).
- RST 2: `[slot n]` as encoded, plus the absolute ROM once observed at runtime.
- RST 3: ROM select byte comment 0–251 → `ROM n`; 252 → `UR=on, LR=on`; 253 → `UR=on, LR=off`;
  254 → `UR=off, LR=on`; 255 → `UR=off, LR=off`. Pointer outside the target image →
  `(out of range)`, followed by the target and ROM select observed at runtime, if any.

**Analysis database (D-31)** — `project/<target>.analysis.json`:

- Per target byte: clear value, access flags (M1, data, conflict, read with decoding off, executed
  via RAM, modified after copy/capture), data kind (`INSN_START`, `INSN_BODY`, `RST_OPERAND`,
  `FAR_ADDR`, `STRING`, `WORD_TABLE`, `BYTE_DATA`, `UNKNOWN`) and its source (observed / static
  guess / owner).
- Symbols, comments and owner overrides from the annotation file (e.g. "&C123–&C140 is a string
  table"; RST 6 inline length).
- Relocated blocks and runtime observations (RST targets and ROM selections actually used).
- Saved on demand and on exit; merged when loading, so knowledge from several runs accumulates.

**Source generator** — walks the target range linearly and emits every byte exactly once:

| Byte kind | Emitted as |
|---|---|
| Instruction start (observed or static guess) | mnemonic + operands; symbols where they match an instruction boundary, else `label+n` or hex; static guesses marked `; [static]` |
| RST with inline bytes | `rst #nn ; NAME` + `defw … ;   to …` |
| Far address object | `defw` + `defb` with comments |
| Strings | `defb "TEXT"`; CPC terminator with bit 7 set as `defb "NAM","E"+#80` |
| Word tables | `defw label` |
| Other data | `defb`, 8 per line |
| Relocated block | `; ---- copied to RAM #rrrr-#ssss, executed there ----`, `DISP #rrrr` … `ENT`; labels `ram_xxxx` in RAM coordinates, also used by ROM code calling into the copy |
| Never accessed (scrambled ROMs) | `defb` per fill policy, in a marked block `; ---- never accessed: #xxxx-#yyyy (n bytes) ----` |
| Conflict bytes | per owner override, otherwise code with `; CONFLICT` |
| Non-canonical encodings | `defb` + mnemonic in the comment (D-32) |

A ROM header at &C000 (type, mark, version, modification, name-table pointer, jumpblock) gets
dedicated rendering and labels.

```asm
; VDOS (Vortex X-Module) — descrambled, generated by cpcxray
        OUTPUT "vdos_clear.rom"
        ORG #C000
rom_type:   defb 001h           ; background ROM
...
```

**Round-trip check (D-33):** `source_verify` runs sjasmplus on the generated file and compares the
result byte for byte with the clear dump (scrambled ROMs) or the target image; it lists every
differing address with both values and the source line.

The JSON trace is a chronological record for analysis and scripts; it is never the input of the
source generator.

### 6.16 Session façade and MCP server (`session/`, `mcp_server/`)

```
 Claude Code ──stdio──►┐                          ┌── GUI (Dear PyGui)
 Claude Code ──HTTP───►│ MCP server (own thread) ─┤
                       └──► command queue ──► Session (debugger API) ──► Machine
 Python scripts / pytest ──────────────────────────┘
```

**Modes (D-28):**

- `cpcxray mcp --config machine.toml`: headless, stdio; started by Claude Code from the project's
  `.mcp.json`.
- `cpcxray gui --mcp-port 8765`: GUI plus MCP over streamable HTTP on 127.0.0.1.

**Tool groups** (thin wrappers around `Session` methods; names indicative):

| Group | Tools |
|---|---|
| Machine | `machine_status`, `reset`, `load_config`, `rom_load(slot, path)`, `rom_unload(slot)`, `rom_list`, `rom_set_decode(slot, on)`, `memory_load(addr, path)`, `memory_save(addr, len, path)`, `sna_load`, `sna_save` |
| Execution | `run(max_us / max_instructions / timeout_s)`, `run_until(addr / condition, limits)`, `step(n)`, `step_over`, `step_out`, `pause` — each returns stop reason, PC and a register summary |
| CPU | `registers_get`, `registers_set` |
| Memory | `memory_read(addr, len)` (CPU view), `memory_view(addr, len, view)` (hex + ASCII; view = cpu, ram, lower/romN, raw:romN, shadow:romN), `memory_write`, `memory_search(pattern)` |
| Disassembly | `disassemble(addr, count, view)` with raw bytes, clear bytes and symbols |
| Breakpoints | `breakpoint_add(address, kind, end, location, port_mask, rom)`, `breakpoint_enable`, `breakpoint_remove`, `breakpoint_list` |
| Trace | `trace_start(sinks, filters)`, `trace_stop`, `trace_read(last_n / since)`; bus trace: `bus_trace_start(kinds, range)`, `bus_trace_stop`, `bus_trace_read` |
| Analysis | `analysis_target_add` / `analysis_target_remove` / `analysis_target_list`, `analysis_capture`, `analysis_save` / `analysis_load` (merge), `classify(range, kind)`, `reloc_list` / `reloc_add` / `reloc_remove`, `source_generate(path, fill_policy)`, `source_verify(path)` |
| Scrambled ROMs | `shadow_status`, `shadow_conflicts`, `m1map_save` / `m1map_load` (merge) / `m1map_ranges`, `rom_decode_from_map(raw, map, fill_policy)`, `rom_map(path)`, `rom_dump(path, fill_policy)` |
| Input | `type_text(text)`, `key_press(key)`, `key_release(key)` |
| Screen | `screen_image()` (PNG as MCP image content), `screen_text()` (characters recognised by matching screen cells against the firmware font) |
| Devices | `devices_get` |
| Symbols | `symbol_add`, `symbol_remove`, `symbol_list`, `comment_add` |
| Disk | `disk_list`, `disk_insert(drive, path, writable)`, `disk_eject`, `disk_new(drive, format)`, `disk_save(drive, path)`, `disk_catalog`, `disk_read_file` |

**Rules:**

- Run-type tools require limits and have a default wall-clock timeout (FR-MCP-04).
- Compact text/JSON results; above a size limit, output goes to `output/` and the tool returns
  path and summary (FR-MCP-05).
- File paths are relative to the project folder.
- MCP resources (read-only): config, symbol file, latest ROM map.

---

## 7. Language and tooling

| Option | Pros | Cons | Verdict |
|---|---|---|---|
| Python (CPython) | Owner's preference; fastest development; strong for scripting and analysis; pytest; Dear PyGui | Slowest; CPU between a fraction of and about real CPC speed | **Chosen** |
| Python on PyPy | Same code, measured ~15× faster on the Z80 core (PyPy 8.0, 2026-10-20) | Dear PyGui not available; second interpreter to maintain | **Deferred to the final phase** (owner decision 2026-10-20): development runs on CPython only; PyPy is taken up again in phase 11 |
| Python + native CPU core (Rust/PyO3, C++/pybind11, mypyc/Cython) | Native speed where it matters | Second toolchain; harder debugging | Fallback, enabled by the CPU interface (D-01) |
| C++ / Rust / C# / Go | Speed or safety | Slower development, no owner experience (Rust), no advantage for this tool | Not chosen |

Speed is not critical: most work is stepping and tracing; booting to BASIC takes about 1–2 emulated
seconds; z80test and ZEXALL run on CPython as long background runs until PyPy is taken up in
phase 11.

**Tooling:** `pyproject.toml` (uv or pip), `pytest`, `ruff`, optionally `mypy`, `dearpygui`,
`numpy` (renderer), `tomllib`, `mcp` (MCP server), **sjasmplus** (external, on the Linux host, for
the round-trip check).

---

## 8. Testing strategy

| Level | What | How |
|---|---|---|
| CPU unit | Every opcode, including undocumented ones | Per-opcode JSON tests (SingleStepTests/z80: initial → final state plus bus cycles); also verifies the **M1/read sequence** per instruction. |
| CPU integration | Overall correctness | ZEXDOC/ZEXALL in a minimal CP/M harness (trap BDOS 2 and 9 at &0005). |
| CPU acceptance | FR-CPU-02 | raxoft z80test in a minimal Spectrum-like harness (trap the print routine); all variants OK. |
| Disassembler | Mnemonics and lengths | Golden files covering every opcode. |
| Devices | GA, SystemIO, CRTC, ROM select | Unit tests driving OUT/IN sequences. |
| Memory map | ROM paging | All combinations of enable flags and ROM select, including unpopulated numbers. |
| Machine | Boot | Boot; screen RAM contains "BASIC 1.0" / "Ready" (character cells, not pixels). |
| Decoding | Rule and M1 handling | `vortex_decode` for all four mask cases; M1 reads raw, data reads decoded; `DD CB d op` decodes *d* and *op*; runtime toggle effective from the next read; access-map update and merge rules; decode-from-map equals emulator result. |
| Analysis | Targets, provenance, relocation | Only configured targets are analysed; provenance tags from block moves; relocated block detection; RAM-target capture and change flags. |
| Source generator | Reassembly | VDOS clear ROM: generated source → sjasmplus → identical bytes. Unit tests per RST rendering, far address objects, CPC strings, `DISP` blocks, every non-canonical encoding. |
| MCP | Parity and behaviour | Every public `Session` method has an MCP tool; in-process MCP client tests per tool group (e.g. `type_text("PRINT 1+1\r")`, then `screen_text()` contains "2"). |
| End to end | VDOS | Boot with all ROMs; VDOS initialises (sign-on appears), RSX commands respond; dump, ROM map and source produced. |

---

## 9. Implementation plan

Each phase ends with a demonstrable milestone and its tests. From phase 3 on, each phase adds its
MCP tools together with its `Session` methods, so Claude Code can use the emulator to debug the
next phase.

| Phase | Content | Milestone | Debug approach |
|---|---|---|---|
| 0 | Repository skeleton, pyproject, pytest, ruff, config loader, ROM path handling | `pytest` green on an empty suite | — |
| 1 | Z80 core: registers, bus interface with signals, all opcode groups, interrupts, NMI, timing table | Per-opcode JSON tests pass (state and bus-cycle sequence) | Run single opcodes from JSON; diff state and bus cycles |
| 2 | CPU validation | ZEXDOC/ZEXALL pass, then **z80test passes all variants** | Failing group → back to the JSON tests for that group |
| 3 | Disassembler with symbols (jumpblock names preloaded) and CPC profile; `Session` with minimal debugger (step, run, PC breakpoints, text trace); **headless MCP server (stdio)** | Golden tests pass; Claude Code can load a binary, step and disassemble via MCP | Golden files per opcode |
| 4 | Machine: MemoryMap and ROM slots, GA, ROM select, CRTC registers, SystemIO (port B + VSYNC), interrupt timer | **CPC 464 boots to "BASIC 1.0 / Ready"** (checked on screen RAM) | Trace from reset; firmware milestones (jumpblock setup, ROM scan, screen clear); breakpoints on GA and &F5xx |
| 5 | Renderer and Dear PyGui shell (screen, run/pause/step/reset) | Boot screen visible; MODE 0/1/2, INK, BORDER and scrolling correct | Compare with a reference emulator |
| 6 | Keyboard row array, host key mapping, type-text helper | BASIC commands can be typed | Watch &F4xx reads in the device view |
| 7 | Decoding and analysis: decoder and switch, shadow ROM, access map (save/load/merge, decode from map), conflicts, analysis targets (ROM and RAM), provenance, relocated blocks, trace sinks, dump, ROM map | **VDOS is found and initialised; conflict report reviewed; dump and ROM map produced** | Break on M1 inside the VDOS slot; trace the ROM header reads during the ROM scan |
| 8 | Analysis database (save/merge), RST-operand and far-address observers, sjasmplus source generator, round-trip check | **Generated VDOS source reassembles byte-identically** | `source_verify` lists differing addresses |
| 9 | Full debugger UI: all panels and breakpoint types; `.sna` snapshots | All FR-DBG requirements met | — |
| 10 | FDC µPD765 and disk images (DSK/EDSK/raw, read/write), drive A: 3", drive B: Vortex 80T DS | AMSDOS `CAT` on A:; Vortex format on B:; VDOS disk code paths reached and decoded | FDC command log in the device view |
| 11 | Later scope: 6128 RAM banking; **restart on PyPy** (headless runs, CPU validation; GUI stays on CPython), then the native CPU / Rust port if needed | Full test suite and CPU validation pass under PyPy | Compare CPython and PyPy results |

### 9.1 Repository layout

```
cpcxray/
├─ pyproject.toml
├─ docs/
│  ├─ design.md
│  ├─ vortex_decrypt.md
│  └─ cpc_rst.md
├─ .mcp.json                  # Claude Code: starts `cpcxray mcp`
├─ config/machine.toml
├─ roms/                      # not in git
├─ src/cpcxray/
│  ├─ cpu/        z80.py, tables.py, timing.py, disasm.py
│  ├─ machine/    bus.py, memory.py, cpc464.py, config.py
│  ├─ devices/    gate_array.py, crtc.py, system_io.py, keyboard.py, rom_select.py, fdc765.py
│  ├─ decoding/   vortex.py, shadow_rom.py, m1map.py, export.py
│  ├─ analysis/   targets.py, provenance.py, cpc_profile.py, database.py, classifier.py,
│  │              source_gen.py, verify.py, trace.py
│  ├─ disk/       image.py, dsk.py, edsk.py, raw.py
│  ├─ debugger/   debugger.py, breakpoints.py, symbols.py, observers.py
│  ├─ session/    session.py, commands.py
│  ├─ mcp_server/ server.py, tools_*.py
│  └─ ui/         app.py, renderer.py, panels/*.py
├─ tests/
│  ├─ cpu/  devices/  machine/  decoding/  analysis/  disasm/  mcp/
│  └─ data/       test vectors, golden files (ROMs referenced via config)
├─ output/        generated files (dumps, maps, sources, traces)
└─ tools/         run_headless.py, ...
```

---

## 10. References

- raxoft z80test: https://github.com/raxoft/z80test
- SingleStepTests (Z80 per-opcode JSON tests): https://github.com/SingleStepTests/z80
- ZEXDOC/ZEXALL (Frank Cringle's Z80 instruction exerciser)
- "The Undocumented Z80 Documented" (Sean Young): undocumented flags, MEMPTR
- Z80 instruction timing and M1 usage: https://floooh.github.io/2021/12/06/z80-instruction-timing.html
- CPCWiki, Gate Array (register RMR): https://www.cpcwiki.eu/index.php/Gate_Array#Register_RMR_(Control_Interrupt_counter,_ROM_mapping_and_Graphics_mode)
- CPCWiki: CRTC, 8255 PPI, AY-3-8912, µPD765, I/O port summary
- CPC Firmware Guide (SOFT 158/968): restarts (LOW JUMP, SIDE CALL, FAR CALL, FIRM JUMP), KL LOW PCHL
- sjasmplus documentation: https://z00m128.github.io/sjasmplus/documentation.html
- Z80 timings on Amstrad CPC, cheat sheet V1.3 (CPCWiki, 2023-07-09), basis of the NOP timing
  table (D-04): https://www.cpcwiki.eu/imgs/b/b4/Z80_CPC_Timings_cheat_sheet.20230709.pdf
- `docs/vortex_decrypt.md` — VDOS scrambling rule and M1 behaviour (normative parts: §5.5)
- `docs/cpc_rst.md` — CPC RST instructions and output format (RST 1 bits per §5.6)
