# The cpcxray MCP server

cpcxray offers every function of its debugger as a tool of the
[Model Context Protocol](https://modelcontextprotocol.io) (MCP): an AI assistant or any MCP client
can power on the CPC, run and step it, type, read the screen and memory, set breakpoints, trace,
analyse ROMs and generate sources. The window uses the very same functions, so whatever you can
do with the mouse a client can do with a tool. How to use it from Claude Code:
[Claude Code with cpcxray](claude_code.md).

## Two ways to connect

| | Headless | Attached to the window |
|---|---|---|
| Start | the client starts `cpcxray mcp` | you start `cpcxray gui` |
| Transport | stdio (stdin / stdout) | streamable HTTP, `http://127.0.0.1:8765/mcp` |
| Machine | the CPC of `config/machine.toml` (`--config`), in the project folder (`--project-dir`) | the machine in the window - the client sees and drives what you see |
| Options | `--machine flat`: a bare Z80 with 64 KB RAM instead of a CPC | `--mcp-port N` another port, `0` none |

The HTTP server listens on 127.0.0.1 only (the tools can read and write files in the project
folder). In the window, tool calls run between frames; a running machine keeps running unless a
tool needs it stopped.

## Conventions

- **Addresses** are numbers, hex strings (`"BB5A"`, `"&BB5A"`, `"#BB5A"`, `"0xBB5A"`) or names:
  firmware jumpblock entries (`"TXT_OUTPUT"`), OS / AMSDOS variables (`"KL_TIME"`), your labels
  and variables. Results give addresses as 4-digit hex strings.
- **Every run is bounded**: an instruction limit, emulated time (`max_us`, 1 µs = 1 CPC NOP) or a
  wall-clock timeout (default 10 s, at most 600 s). A run returns why it stopped: `steps_done`,
  `breakpoint`, `address_reached`, `returned`, `instruction_limit`, `time_limit`, `timeout`,
  `paused` or `halted`.
- **Results** are JSON objects. Large results are limited (e.g. 4096 bytes of memory per call);
  write larger data to a file (`memory_save`, a trace file).
- **Files** are relative to the project folder; generated work goes to `project/`, screenshots,
  dumps and traces to `output/`.
- **Errors** (a bad address, an unknown name, a missing file) come back as tool errors with a
  message saying what is wrong.
- **`pause`** may be called while another call runs; it stops that run soon.

## Tool reference

The reference below is generated from the tools themselves (`python -m
tools.generate_mcp_reference`), so it always matches the program. "D-nn" and "FR-…" refer to
decisions and requirements in the design document.

<!-- begin generated: python -m tools.generate_mcp_reference -->

### Machine

#### `machine_status()`

Return machine model, PC, clock, CPU state, breakpoint count and trace state.

#### `reset()`

Reset the machine (CPU registers, clock); memory keeps its contents.

#### `load_config(path: str = "config/machine.toml")`

Power on a CPC with the model and ROMs of a machine.toml (default config/machine.toml).

Replaces the current machine (memory is cleared); the owner's symbols, breakpoints and
the trace are kept, the firmware names follow the new model (D-26); the breakpoint and
bus-trace observers move to the new bus.

#### `rom_list()`

List the installed ROMs (slot, file, header type/version) and the skipped ones.

#### `rom_load(slot: str | int, path: str)`

Load a 16 KB ROM image into "lower" or upper slot 0-15, then reset (D-09).

#### `rom_unload(slot: str | int)`

Empty "lower" or upper slot 0-15, then reset (D-09).

#### `devices_get()`

Return the state of Gate Array, CRTC, ROM select, PPI (SystemIO), FDC and keyboard.

Besides the registers: the inks as firmware colours and the pens the mode uses, the
selected ROM's file and whether it is paged in, the CRTC register names and screen
layout, the PPI ports and the FDC status in words, and `key_lines`: the keyboard
matrix lines 0-9 with the names of their 8 keys (from the OS ROM's key map), the
line's value (bit clear = pressed) and the held keys.

#### `paging_get()`

What the CPU reads in each 16 KB quarter now (FR-DBG-12).

Each quarter shows the ROM paged in - the lower ROM, or upper ROM n with its file
name - or else the RAM block mapped there (6128 banking, D-14). `ram_block` is the
block under the quarter in any case: writes always go to RAM. A 6128 also reports its
RAM configuration (Gate Array function 11).

#### `sna_load(path: str)`

Load a .sna snapshot (versions 1-3); the configured ROMs stay installed.

#### `sna_save(path: str)`

Save the machine state as a .sna snapshot (version 3, D-25).

### Configuration

#### `config_get()`

The configuration of the running machine as a TOML document, with ROM headers.

**Returns**

"path" (the config file, relative to the project, or None), "config" (the document; None for the bare Z80 machine), "roms" (per configured ROM: "slot", "file", "exists", the header as `rom_header` gives it) and "skipped" (ROMs and disks that were not installed, with the reason).

#### `config_read(path: str)`

Read and validate a machine.toml without applying it; returns its document.

#### `config_write(config: dict[str, Any], path: str)`

Validate a configuration document and write it as a fresh, commented machine.toml.

#### `config_apply(config: dict[str, Any], path: str | None = None)`

Power on a CPC from a configuration document (memory cleared, as `load_config`).

**Args**

- `config`: The document (tables of a machine.toml).
- `path`: The file it belongs to (shown as the machine's config file), if any.

#### `rom_header(path: str, decoder: str | None = None)`

What a ROM image's header says: "type", "version" and the first "name".

**Args**

- `path`: The ROM image.
- `decoder`: Its decoder ("vortex") for a scrambled ROM: the header is data, read decoded.

**Returns**

The header, or "error" for a missing or wrong-sized file.

#### `loads_get()`

The binary files of the configuration to load into RAM, and which are loaded now.

**Returns**

"loads": per file "file", "address", "exists", "length" and "loaded" (loaded since the last reset, see `memory_file_check`).

#### `memory_file_check(address: int | str, path: str)`

Whether a file was loaded at `address` (`memory_load`) since the last reset.

A reminder, not a comparison with RAM - a running program changes its own variables
(owner, 2026-10-23). It counts as not loaded after a reset, a new machine (Apply,
`load_config`), a snapshot load, or when the file changed since it was loaded.

**Returns**

"file", "address", "exists", "length" and "loaded".

### Running

#### `run(max_instructions: int | None = None, max_us: int | None = None, timeout_s: float | None = None)`

Run until a breakpoint or a limit.

**Args**

- `max_instructions`: Stop after this many instructions.
- `max_us`: Stop after this much emulated time in µs (1 µs = 1 CPC NOP).
- `timeout_s`: Wall-clock limit in seconds (default 10, max 600).

#### `run_until(address: int | str, max_instructions: int | None = None, max_us: int | None = None, timeout_s: float | None = None)`

Run until the PC reaches `address` (or a breakpoint or a limit).

**Args**

- `address`: Target address or label.
- `max_instructions`: Stop after this many instructions.
- `max_us`: Stop after this much emulated time in µs.
- `timeout_s`: Wall-clock limit in seconds (default 10, max 600).

#### `step(count: int = 1)`

Execute `count` instructions (1-100000); stops early at a breakpoint.

**Returns**

Stop reason ("steps_done", "breakpoint", ...), PC, next instruction, registers.

#### `step_over(timeout_s: float | None = None)`

Execute one instruction, running calls and loops to completion.

CALL, call-like RSTs (per the machine's RST profile), DJNZ and repeating block
instructions run until the PC reaches the following instruction; jumps and all other
instructions are single-stepped.

#### `step_out(timeout_s: float | None = None)`

Run until the current subroutine returns, then stop at the return address.

"Returns" means a RET/RETI/RETN that leaves SP above its value at the start.

#### `pause()`

Ask a running run/run_until/step_over/step_out to stop soon (reason "paused").

Safe to call from another thread; has no effect when nothing is running.

#### `last_stop_get()`

The last stop of a run or step, with what explains it.

A run that only used up its time budget ("time_limit") does not count: the GUI runs
in time slices, and its last stop is the breakpoint, step or pause before.

**Returns**

"stop": the run's result as it was returned (reason, PC, "breakpoint" detail), or None before the first stop; for a stop: "instruction", the disassembled lines of the instruction that stopped (a PC breakpoint: the one about to run; an access breakpoint: the one that made the access); for a breakpoint stop: "hits", its hit count now (None if removed since), and "label", the label of its address.

### CPU

#### `registers_get()`

Return all registers and CPU state flags (a, f, bc, ..., iff1, im, halted).

#### `registers_set(values: dict[str, int])`

Set registers by name, e.g. {"pc": 32768, "a": 5}; returns all registers.

#### `stack_get(count: int = 16)`

The stack: words from SP up to the stack's top (at most `count`, 1-256).

The top is where the stack started: the debugger watches the instructions that load SP
and keeps the tops they set (debugger/stack.py); "source" names the instruction, e.g.
"ld sp at &0092 (lower)". Without a known top, `count` words from SP are shown.

### Memory

#### `memory_read(address: int | str, length: int = 128)`

Read memory as the CPU sees it: hex dump lines with ASCII (max 4096 bytes).

#### `memory_view(address: int | str, length: int = 128, view: str = "cpu")`

Read memory through a view (FR-DBG-03): hex dump lines with ASCII.

**Args**

- `address`: Start address - a CPU address in every view: a ROM view shows its ROM where the CPU sees it (the lower ROM at &0000-&3FFF, an upper ROM at &C000-&FFFF) and nothing ("??", "--") elsewhere.
- `length`: Number of bytes (max 4096).
- `view`: "cpu" (what the CPU reads now), "ram" (RAM even under a paged-in ROM), "ram:N" (RAM block N, mapped or not, at offset address & &3FFF: 0-3, 0-7 on a 6128), "lower" / "romN" (the ROM image, decoded for data reads if scrambled), "raw:romN" (a scrambled ROM image as stored), "shadow:romN" (the clear values recorded by the shadow ROM; never-read bytes "--"; "shadow": the only scrambled ROM).

#### `memory_write(address: int | str, data: str)`

Write bytes given as hex (e.g. "3E 05 C9") to RAM at `address`.

#### `memory_search(pattern: str, start: int | str = 0, end: int | str = 65535)`

Find a byte pattern (hex, e.g. "CD5ABB") between `start` and `end` (inclusive).

#### `memory_load(address: int | str, path: str)`

Load a binary file into memory (RAM) at `address`.

**Args**

- `address`: Load address.
- `path`: File path, relative to the project directory.

**Returns**

Load address, length and the address of the last byte.

#### `memory_save(address: int | str, length: int, path: str)`

Save `length` bytes of the CPU's view of memory from `address` to a binary file.

#### `memory_follow(enabled: bool = True)`

Note memory writes from now on ("Follow CPU" of the Memory tab), or stop noting them.

The writes are what `memory_changes_get` reports as written since the last stop.

#### `memory_changes_get()`

What the last step accessed, and the memory written since the last stop.

**Returns**

"following" (memory_follow is on), "last_step" - for a stop by a step: "pc" and its "accesses" (read / write, address, value, variable; its own operand bytes left out), else None - "written": the addresses written since the last stop with their values (at most 4096, while following), "written_count" and "last_written" (the latest write, address and value).

### Disassembly

#### `disassemble(address: int | str | None = None, count: int = 16)`

Disassemble `count` instructions (max 256) from `address` (default: PC).

Lines show address, bytes, instruction with symbols and comments; labels get their
own line, the owner's comment blocks `;` lines before the label / after the
instruction (task 9). Non-canonical encodings are marked "[nc]". `paging` describes
the 16 KB quarters the lines come from: the ROM or RAM block the CPU reads there now
(FR-DBG-12).
`rows` gives the same split into fields, with the type of each name (the CPU tab).

### Breakpoints

#### `breakpoint_add(address: int | str | None = None, kind: str = "pc", end: int | str | None = None, location: str = "any", port_mask: int = 65535, rom: int | None = None)`

Add a breakpoint (FR-DBG-07); returns it with its id.

**Args**

- `address`: Address (pc, m1, read, write, access) or port (io_in, io_out, io).
- `kind`: "pc" (before the instruction), "m1", "read", "write", "access" (data read or write), "io_in", "io_out", "io", "rom_select", "interrupt". All but "pc" stop after the instruction that made the access and report its PC.
- `end`: Last address of a range (address kinds). Without it, a read, write or access breakpoint on the name of a known OS/AMSDOS variable covers the whole variable (FR-DBG-11).
- `location`: What must be paged in at the access: "any", "rom", "lower", "rom:<n>", "ram" or "ram:<block>" (the RAM block mapped at the address: 0-3 by address on a 464, 0-7 by banking on a 6128). Writes always go to RAM.
- `port_mask`: Port bits compared with `address` (I/O kinds), e.g. &FF00.
- `rom`: "rom_select": only when this ROM number is selected.

#### `breakpoint_list()`

List all breakpoints: id, kind, address/port, location, enabled, hits.

#### `breakpoint_enable(breakpoint_id: int, enabled: bool = True)`

Enable or disable a breakpoint (disabled ones are kept but ignored).

#### `breakpoint_remove(breakpoint_id: int)`

Remove a breakpoint by id.

### Instruction trace

#### `trace_start(path: str | None = None, start: int | str = 0, end: int | str = 65535, file_format: str = "text", targets_only: bool = False)`

Switch the instruction trace on for runs (ring buffer, plus a file if `path`, D-24).

Tracing slows the emulation to about 1/5; stepping (step, step_over, step_out) is
recorded into the ring buffer anyway, without switching the trace on.

Records show where code runs (ROM slot or RAM, with the ROM origin of copied code)
and, for scrambled ROMs, the raw bytes next to the clear ones.

**Args**

- `path`: File, relative to the project directory (e.g. "output/trace.txt").
- `start`: First PC to record.
- `end`: Last PC to record.
- `file_format`: "text", "csv" or "jsonl" (JSON Lines).
- `targets_only`: Record only analysis-target code (incl. its RAM copies).

#### `trace_stop()`

Stop the trace; the ring buffer keeps the records for trace_read.

#### `trace_read(last: int = 50)`

Return the last `last` trace records (max 1000) as text lines.

#### `trace_records(last: int = 50)`

The last `last` trace records (max 1000) split into fields, oldest first.

A gap - instructions that ran untraced (a free run with the trace off) - is a record
{"kind": "gap", "sequence", "instructions", "emulated_us"}. Per instruction record
("kind" "instruction"): "sequence", "address", "bytes", "mnemonic", "operands" (with
"name" and "name_type" FW / VAR / own where an operand is named, "comment"), "registers"
before
it (af, bc, de, hl, ix, iy, sp as 4-digit hex), "where" ("lower", "romN", "RAM") and
"where_text", "flow" ("call", "return" or ""), "next" (the address after it), the
owner's comment blocks "comment_before" / "comment_after" the line where it has them
(task 9, looked up where it ran), and for stepped instructions "accesses" (access,
address, value, variable). "after" (of the result): the registers after the last record
(including "pc"), or None if something ran since.

### Bus trace

#### `bus_trace_start(kinds: list[str] | None = None, start: int | str = 0, end: int | str = 65535, capacity: int = 10000)`

Record every bus access into a ring buffer (FR-DBG-08); clears the buffer.

**Args**

- `kinds`: Accesses to record: "M1", "read", "write", "in", "out", "interrupt" (default all).
- `start`: First address/port recorded.
- `end`: Last address/port recorded.
- `capacity`: Ring buffer size (1-1,000,000).

#### `bus_trace_stop()`

Stop the bus trace (the buffer is kept for bus_trace_read).

#### `bus_trace_read(last: int = 100)`

Return the last accesses: sequence, PC, access, address, value, location, variable.

A read or write that went to RAM ends with the known OS/AMSDOS variable it touched
(`NAME` or `NAME+n`, FR-DBG-11), looked up when reading - recording stays as fast as
before. AMSDOS's names use its workspace as it is now.

#### `bus_trace_view(top: int | None = None, count: int = 300, fetches: bool = False, combine: bool = True, types: list[str] | None = None)`

A window of the bus trace grouped per instruction, with roles and meanings.

**Args**

- `top`: Index of the window's first record in the buffer (0 = the oldest kept); None for the newest `count` records.
- `count`: Records in the window (1-2000).
- `fetches`: Also list the instructions' own opcode fetches and operand bytes.
- `combine`: Show adjacent byte reads / writes of one kind as one word access.
- `types`: Show only these access types ("M1", "read", "write", "in", "out", "interrupt") and only the instructions that made one of them; None for all. A filter of the view: the trace records everything it was started for.

**Returns**

"groups" (a gap - a free run that was not recorded - as {"gap": {"instructions", "emulated_us"}, "first"}; per instruction: "pc", "first" (its first record's index), its text as mnemonic/operands/name/name_type, "where" it ran, "accesses" each with "index" in the buffer, "access", "address", "value", "role" fetch / operand / data, "meaning", "word"), "top", "count", "total" (records kept) and "recording".

#### `bus_trace_clear()`

Empty the bus trace's buffer (recording, if on, goes on with the same filters).

### Keyboard and screen

#### `type_text(text: str, wait: bool = True, timeout_s: float | None = None)`

Type text on the CPC keyboard, e.g. "PRINT 1+1\r" (\r or \n = RETURN).

Each character's keys (with SHIFT or CONTROL where needed) are held for 40 ms and
released for 40 ms of emulated time, so the firmware sees every key (about 12
characters per emulated second).

**Args**

- `text`: The characters to type.
- `wait`: Run the machine until everything is typed (plus 0.1 s for the firmware to react); False only queues the keys (the GUI keeps running and types them).
- `timeout_s`: Wall-clock limit when waiting (default 10 s).

#### `key_press(key: str)`

Hold a CPC key until key_release.

Key names come from the OS ROM tables: characters like "a", "1", "," and names like
"return", "shift", "control", "cursor_up", "f1", "space", "esc", "copy", "del",
"joy_fire1".

#### `key_release(key: str)`

Release a key held by key_press; "all" releases every key held by key_press.

#### `screen_text()`

Return the text on the CPC screen, recognised from screen RAM with the ROM font.

One string per character row; unknown cells show "?".

#### `screen_image(path: str | None = None)`

Render the CPC screen (with border, scan lines doubled: 768 x 544) as PNG.

Over MCP the image is returned as image content. With `path` (relative to the project
directory, e.g. "output/screen.png") it is also written to that file.

### Symbols and variables

#### `symbol_add(address: int | str, label: str | None = None, comment: str | None = None, scope: str = "", type: str | None = None, before: str | None = None, after: str | None = None, group: str | None = None, replaces: str | None = None)`

Add or change a label, comment or comment block at `address`.

Parts not given keep what is there. The generated source writes the blocks as comment
lines before / after the instruction (task 9).

**Args**

- `address`: The address.
- `label`: Identifier, unique within the scope, the global symbols and the variables.
- `comment`: Free text at the end of the line.
- `scope`: "" for global (RAM, firmware), "lower" (OS ROM) or "rom0".."rom15".
- `type`: The label's type: "sub" (subroutine, the default), "jump" (a jump target inside code) or "data" (a table, a string).
- `before`: Comment block before the line, e.g. a routine header; several lines separated by line breaks; "" removes it.
- `after`: Comment block after the line, e.g. a longer explanation; "" removes it.
- `group`: Global (RAM) labels only (D-44): your group of the label - one RAM address can carry one label per group; "" for the ungrouped one. Without it the one symbol at the address is meant (an error that names them if there are several). Not allowed in ROM scopes, nor a label at a firmware name's address.
- `replaces`: Moves a global label into `group` from the group it is in now (its name, "" for no group): label, type, comment and blocks go with it unless given - e.g. `symbol_add("BE8F", group="cpm", replaces="vdos")`.

#### `comment_add(address: int | str, comment: str, scope: str = "", position: str = "line", group: str | None = None)`

Set a comment at `address` (keeps an existing label and the other comments).

**Args**

- `address`: The address.
- `comment`: The text; for a block several lines separated by line breaks; "" removes a block (`symbol_remove` removes everything at the address).
- `scope`: "" for global, "lower" or "rom0".."rom15".
- `position`: "line" (at the end of the line), "before" (a block before the line, e.g. a routine header) or "after" (a block after the line).
- `group`: Which RAM label's comment, where several are at the address (D-44).

#### `operand_set(address: int | str, role: str, scope: str | None = None, comment: str | None = None)`

Decide whether the word operand of the instruction at `address` is a number.

`ld hl,#0010` stays a number instead of `ld hl,SIDE_CALL` (the name of RST 2) with
"value"; "address" names it even where the judgement of `ld rr,nn` says number. The
decision wins in the disassembly, the trace and the generated source, and is kept in
the annotation file (`symbols_save`).

**Args**

- `address`: The instruction's address.
- `role`: "value" (a number: always hex), "address" (named) or "auto" (remove the decision: cpcxray judges again).
- `scope`: "" (global, RAM), "lower" or "rom0".."rom15"; default: what is paged in at the address now.
- `comment`: Why (kept in the annotation file).

#### `symbol_remove(address: int | str, scope: str = "", group: str | None = None)`

Remove the owner's symbol at `address` in `scope` (a firmware name stays).

`group`: which RAM label, where several are at the address (D-44; "" the ungrouped).

#### `symbol_list(text: str = "", scope: str | None = None, source: str | None = None, limit: int = 100, group: str | None = None)`

List symbols whose label, comment or block contains `text` (see `symbol_search`).

**Args**

- `text`: Search text (case-insensitive); empty lists all.
- `scope`: Only this scope ("", "lower", "romN"), or all.
- `source`: "firmware" (preloaded jumpblock names) or "user", or both.
- `limit`: Maximum number of entries (max 1000).
- `group`: Only this group (D-43; "" ungrouped, "firmware" the firmware names), or all.

#### `symbol_search(text: str = "", types: list[str] | None = None, scope: str | None = None, only_yours: bool = False, limit: int = 2000, group: str | None = None)`

Every name for the Symbols tab: symbols, variables and the analysis's names.

**Args**

- `text`: Search text (case-insensitive) in label, comment, comment blocks or a variable's description.
- `types`: Only these types (FW, VAR, var, sub, jump, data); None for all. Comments without a label (type None) are listed unless a type filter is given.
- `scope`: Only this scope ("" global, "lower", "romN"); None for all.
- `only_yours`: Only entries of the owner's (labels, comments, variables).
- `limit`: Maximum number of entries (max 5000).
- `group`: Only this group (D-43): one of yours, "firmware", "amsdos", or "" for ungrouped; None for all.

**Returns**

"entries" by address (each "type", "address", "label", "comment", "scope", "group", "mark": "yours" / "analysis" / None, for variables "size"), "counts" per type (before the type filter) and "total" (after it).

#### `symbol_detail(address: int | str, scope: str = "", label: str | None = None, group: str | None = None)`

Everything about one name of `symbol_search` (the Symbols tab's detail panel).

**Args**

- `address`: Its address.
- `scope`: Its scope.
- `label`: Its label, to find a variable (variables can share an address) or one of several RAM labels at the address.
- `group`: Which RAM label, where several are at the address (D-44; "" ungrouped).

**Returns**

"address", "scope"; "firmware" (address, label, section, name, note), "machines" it exists on; "yours" (the owner's label, comment, type, comment blocks "before", "after" and "group" in this scope); "variable" (a known or owner's variable of that label: size, description, owner, and for the owner's "base" and "now"); "analysis" (the source generator's name and type); "code" (the instruction at the address, names as in the disassembly).

#### `variable_add(label: str, size: int, base: int | str, comment: str | None = None, replaces: str | None = None, group: str | None = None)`

Add or change one of your variables (type "var"): a named byte range in RAM.

It behaves like a known OS/AMSDOS variable: `LABEL+offset` in disassembly, traces and
the generated source, colours in the memory view, breakpoints on its name.

**Args**

- `label`: Its label (unique among all labels).
- `size`: Size in bytes.
- `base`: An absolute address ("A612") or an offset in the data area (IY) of a background ROM, "rom5+0012": it follows that ROM's data area, which the firmware assigns at boot (KL_ROM_IY_TABLE).
- `comment`: What it holds.
- `replaces`: The label of the variable it replaces (to change or rename one).
- `group`: Your group (D-43; `group_add`); "" takes it out of its group; None keeps the group of the variable it replaces (else ungrouped).

**Returns**

The variable with "now" - where it is at the moment ("A612-A621"), or None while its ROM has no data area.

#### `variable_remove(label: str)`

Remove one of your variables.

#### `variable_locate(base: str, size: int = 1)`

Where a variable at `base` ("A612" or "rom5+0012") of `size` bytes is now.

**Returns**

"first" and "last" address, or None for both while the ROM has no data area.

#### `variable_list(text: str = "", limit: int = 100, group: str | None = None)`

List the known variables (FR-DBG-11) whose label or description has `text`.

The firmware's and AMSDOS's (when installed), and the owner's (owner "owner").

**Args**

- `text`: Search text (case-insensitive); empty lists all.
- `limit`: Maximum number of entries (max 1000).
- `group`: Only this group (D-43): "firmware", "amsdos", one of yours, or "" for your ungrouped ones; None for all.

#### `address_explain(address: int | str, code_address: int | str | None = None)`

Tell what an address is: its symbol, and every name it has, the winner first.

Variables (FR-DBG-11) are the firmware's of this machine (464/664/6128), AMSDOS's
when it is installed (its workspace where AMSDOS put it) and the owner's. With groups
(D-43) one RAM address can have several labels and variables; `names` lists them all
with their group, ranked as code sees them: the firmware name first, then the group
of the context of `code_address` (if given), built-in and ungrouped, other groups.

**Args**

- `address`: The address.
- `code_address`: Code that uses the address (its context decides; default: none).

**Returns**

"address", "label" (the winner's label), "context" (the code's group or None), "names" (text, type, group each, the winner first), "variables" (the variables containing it, ranked, with their group).

#### `symbols_load(path: str = "project/annotations.txt")`

Merge an annotation file (default project/annotations.txt) into the symbols.

#### `symbols_save(path: str = "project/annotations.txt")`

Save your symbols and variables to an annotation file (project/annotations.txt).

#### `symbols_status(path: str = "project/annotations.txt")`

The annotation file: its path, whether it exists, your entries, unsaved changes.

**Returns**

"path", "exists", "symbols", "variables", "operands", "groups" and "contexts" (your entries now: labels and comments, variables, operand overrides, groups, contexts), "operand_overrides" (address, scope, role, comment each), "group_names" (your groups in their order), "context_list" (as in the file), "warnings" (from loading: undefined groups, labels left out at firmware names' addresses), "modified" (changed since the last load or save).

### Groups and contexts

#### `group_add(name: str, description: str | None = None, position: int | None = None)`

Create one of your groups, or change its description or position (D-43).

Your variables and RAM labels go into groups (`variable_add` / `symbol_add` with
`group`); a context (`context_set`) tells which group code works with, so a RAM
address shows the name of that group there. The source lists the names by group.

**Args**

- `name`: An identifier (not "firmware" / "amsdos": the built-in groups).
- `description`: What it holds (the heading of its section in the source); None keeps the earlier one.
- `position`: Its index in the list (the order of the ranking and of the sections); None keeps its place, a new group goes last.

#### `group_rename(old: str, new: str)`

Rename one of your groups everywhere (its variables, labels and contexts).

#### `group_remove(name: str, force: bool = False)`

Remove one of your groups; refused while it is used, unless `force`.

With `force` its variables and labels become ungrouped and its contexts are deleted.

#### `group_list()`

The groups in their order: the built-in ones, then yours with what uses them.

**Returns**

"groups": each "name", "description", "built_in", and for yours "variables", "labels", "contexts" (counts).

#### `context_set(group: str, scope: str | None = None, first: int | str | None = None, last: int | str | None = None)`

Tell which group code works with (D-43): a range of a scope, or the whole scope.

Where that code names a RAM address - an operand, a variable it reads or writes - the
name of this group wins; the narrowest range wins over a wider one and over the whole
scope. RAM code copied from a ROM takes its ROM origin's context.

**Args**

- `group`: One of your groups.
- `scope`: "lower", "rom0".."rom15" or "" (RAM code); default: what is paged in at `first`.
- `first`: First address of the range; without first and last: the whole scope.
- `last`: Last address (inclusive).

#### `context_remove(scope: str, first: int | str | None = None, last: int | str | None = None)`

Remove the context of exactly this place (a range, or the whole scope).

#### `context_list(scope: str | None = None)`

The contexts of one scope, or all.

**Returns**

"contexts": each "context" (as in the annotation file), "scope", "first", "last" (None for a whole scope), "group".

### Analysis

#### `analysis_target_add(target: str, start: int | str | None = None, length: int | None = None, capture_at: str = "first_m1")`

Add an analysis target (D-16): a ROM slot ("rom5", "lower") or a RAM range.

**Args**

- `target`: "romN" / "lower", or a name for a RAM range (with start and length).
- `start`: RAM range start.
- `length`: RAM range length.
- `capture_at`: RAM range: "first_m1" (capture at the first fetch inside) or "manual".

#### `analysis_target_remove(target: str)`

Remove an analysis target by name ("rom5", "lower", "ram:<name>").

#### `analysis_target_list()`

List the analysis targets with their flag counts and RAM-capture state.

#### `analysis_enable(enabled: bool = True)`

Switch the analysis observers on or off (decoding and the access map keep working).

The observers record instruction starts, RST facts and the provenance of code copied
to RAM; they watch every bus access and halve the emulation speed. Switch them on while
exercising the analysed ROM (boot, disk access, RSX commands), off for normal use.
What was collected is kept either way.

#### `analysis_status(target: str | None = None, classify: bool = True)`

Where each analysis phase of a target stands (the Project tab's phases).

**Args**

- `target`: The target ("rom7", "lower", "ram:<name>"); None for the first one.
- `classify`: Bring the coverage up to date (classifies the target if what was collected changed); False gives the coverage of the last classification (made now if there is none yet) - cheap enough for a GUI while the machine runs.

**Returns**

"targets" (all names), "analysis_enabled", "observing" and, for the target, "target", "kind" ("rom" / "ram"), "file", "decoder", "collect" (seen, coverage code / data / unknown, for RAM targets "captured"), "decode" (None without a decoder; else the access classes, "conflicts" and "real_conflicts", "map_path"), "save" ("changed", "database_path", "exists"), "source" and "verify" (the last results, or None).

#### `analysis_capture(target: str)`

Capture a RAM target's image now; later writes into it are recorded (D-23).

#### `analysis_save(target: str | None = None)`

Save the analysis state (D-19, D-31); called automatically when the GUI closes.

Writes the access map of every scrambled ROM (project/<rom>.m1map) and the database
of every analysis target (project/<rom>.analysis.json: classification, runtime
observations, owner overrides, target flags, relocated blocks).

**Args**

- `target`: Only this target (and its access map); None saves everything.

#### `analysis_load(target: str | None = None, path: str | None = None)`

Merge a saved database into the current one (stronger classification wins per byte).

**Args**

- `target`: The target (default: the only one).
- `path`: The file (default project/<rom>.analysis.json).

#### `classify(first: int | str, last: int | str, kind: str, target: str | None = None)`

Classify an address range as the owner (strongest source, kept in the database).

**Args**

- `first`: First address (CPU address in the target, e.g. "C123").
- `last`: Last address (inclusive).
- `kind`: "code" (consecutive instructions from `first`), "string", "word_table" (words that are addresses: written with their names), "word_data" (words that are numbers - parameters, offsets, constants: always written in hex), "byte_data", or "auto" to drop the owner's classification of exactly this range again.
- `target`: The target (default: the only one).

#### `reloc_list(target: str | None = None)`

List relocated blocks: ROM code copied to RAM (detected, plus manual ones, D-22).

For a ROM range copied to several places, `preferred` marks the copy with the most
executed instructions (used for disassembly).

#### `reloc_add(target: str, rom_start: int | str, length: int, ram_start: int | str)`

Add a relocated block by hand (owner's correction of the detection, D-22).

#### `reloc_remove(target: str, ram_start: int | str)`

Remove a manually added relocated block (by target and RAM start).

#### `source_generate(target: str | None = None, path: str | None = None, fill_policy: str = "static", conflict: str = "m1")`

Generate the sjasmplus source of a target from its database (D-31, §6.15).

**Args**

- `target`: The target (default: the only one).
- `path`: Output file (default project/<rom>.asm).
- `fill_policy`: Never-read bytes of a scrambled ROM: "static" (default; code guesses disassembled), "raw", "data" or "ff" (emitted as data).
- `conflict`: Value for conflict bytes: "m1" (raw) or "data" (decoded).

**Returns**

Path, number of lines, statistics, and the kinds per byte.

#### `source_verify(path: str | None = None, target: str | None = None)`

Round-trip check (D-33): assemble a generated source, compare with the clear image.

The expected image uses the fill policy and conflict choice recorded in the source's
header line; differing addresses are listed with both values and the source line.

**Args**

- `path`: The source (default project/<rom>.asm).
- `target`: The target (default: the only one).

### Scrambled ROMs

#### `shadow_status(slot: str | int | None = None)`

Return the access classes of a scrambled ROM (unknown / M1 / data / conflict).

#### `shadow_conflicts(slot: str | int | None = None, limit: int = 200)`

List bytes read both with M1 and as data (D-18); "real" ones need the owner's choice.

A conflict is harmless where the decode mask is zero (both readings give the same value).

#### `m1map_save(slot: str | int | None = None, path: str | None = None)`

Save the access map of a scrambled ROM (default project/<rom>.m1map, D-19).

#### `m1map_load(slot: str | int | None = None, path: str | None = None)`

Merge a saved access map into the current one (unknown + X = X, M1 + data = conflict).

#### `m1map_ranges(slot: str | int | None = None, path: str | None = None, json_format: bool = False)`

Export the access map as ranges ("C000-C003  data  4"); with `path` to a file.

#### `rom_decode_from_map(raw_path: str, map_path: str, out_path: str, fill_policy: str = "raw", conflict: str = "m1")`

Decode a scrambled Vortex ROM file with a .m1map file, without running (FR-DEC-04).

**Args**

- `raw_path`: The scrambled ROM image.
- `map_path`: The access map.
- `out_path`: Where to write the clear ROM.
- `fill_policy`: Never-read bytes: "raw", "data", "ff" or "static" (code/data guess by recursive disassembly from the ROM header; no run-time observations).
- `conflict`: Value for conflict bytes: "m1" (raw) or "data" (decoded).

#### `rom_dump(slot: str | int | None = None, path: str | None = None, fill_policy: str = "raw", conflict: str = "m1")`

Write the clear ROM image for a plain replacement ROM (FR-DEC-05, D-20).

**Args**

- `slot`: The scrambled slot (default: the only one).
- `path`: Output file (default project/<rom>_clear.rom).
- `fill_policy`: Never-read bytes: "raw", "data", "ff" or "static" (opcode bytes of statically found code raw, all other never-read bytes decoded; D-20).
- `conflict`: Value for conflict bytes: "m1" (raw) or "data" (decoded).

#### `rom_map(slot: str | int | None = None, path: str | None = None)`

Write the ROM map: HTML page (colour per byte) and text summary (FR-DEC-06).

#### `coverage_csv(slot: str | int | None = None, path: str | None = None)`

Write one CSV row per ROM byte: class, raw and clear value, hits, first PC.

#### `rom_set_decode(slot: str | int, enabled: bool)`

Switch decoding of a scrambled ROM on or off, effective from the next read (D-11).

### Disks

#### `disk_list()`

Show both drives: inserted image, write permission, unsaved changes, head position.

#### `disk_insert(drive: str, path: str, writable: bool = False)`

Insert a DSK, EDSK or raw Vortex image into drive A or B.

**Args**

- `drive`: "A" or "B".
- `path`: The image file.
- `writable`: Let the CPC write to it (in memory only; `disk_save` writes a file). Default read-only: the FDC reports the disk write protected.

#### `disk_eject(drive: str)`

Remove the disk from a drive (unsaved changes of a draft are lost).

#### `disk_new(drive: str, disk_format: str = "vortex")`

Insert a blank, formatted, writable draft image ("vortex", "data" or "system").

Use it for everything that writes; save it with `disk_save`.

#### `disk_save(drive: str, path: str | None = None)`

Save the disk in a drive as EDSK.

**Args**

- `drive`: "A" or "B".
- `path`: Target file. None saves a writable image back over the file it was loaded from; a read-only image is never overwritten, a draft needs a path.

Both clear the "changed" state. Save as (a path) makes the new file the drive's image
from now on, writable because it is the owner's copy; the old file stays untouched.

#### `disk_catalog(drive: str | None = "A", path: str | None = None, include_system: bool = True)`

List the CP/M directory of a drive's disk (or of an image file), read directly.

**Args**

- `drive`: "A" or "B" (ignored when `path` is given).
- `path`: An image file instead of a drive.
- `include_system`: Also list files with the SYS attribute (CAT hides them).

#### `disk_read_file(name: str, drive: str | None = "A", path: str | None = None, user: int = 0, out_path: str | None = None)`

Read a file from a disk directly (no emulation): AMSDOS header and contents.

**Args**

- `name`: "NAME.EXT".
- `drive`: "A" or "B" (ignored when `path` is given).
- `path`: An image file instead of a drive.
- `user`: CP/M user number.
- `out_path`: Write the file there; otherwise up to 4 KB are returned (text and hex).

"program" says where the file goes in memory: from the AMSDOS header; for a CP/M
program (.COM, no header) &0100, where CP/M loads and starts every transient program;
for a BASIC program saved as ASCII text (.BAS, no header) &0170, where Locomotive
BASIC keeps its program. For a file with an entry address inside it (a binary with a
header, a .COM file), "entry" gives the 16 bytes there and their disassembly as at
that address ("code": address, text, and the symbol name in it with its type: FW,
VAR or own).

<!-- end generated -->
