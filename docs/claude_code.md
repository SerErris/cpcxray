# Claude Code with cpcxray

[Claude Code](https://claude.com/claude-code) can drive cpcxray through its MCP server: boot the
CPC, type commands, read the screen, set breakpoints, step through code, explain what a routine
does, and run the whole analysis of a ROM up to a verified source. This guide shows how to connect
the two and how to work with them. The tools themselves are listed in the
[MCP reference](mcp.md); installing cpcxray: [install guide](install.md).

## 1. Connect

There are two ways; you can set up both.

### Headless: Claude Code starts its own emulator

Claude Code starts `cpcxray mcp` in your project folder and talks to it over stdin/stdout. Put a
`.mcp.json` into the project folder (the one `cpcxray init` created):

```json
{
  "mcpServers": {
    "cpcxray": { "command": "cpcxray", "args": ["mcp"] }
  }
}
```

- With the Python package, `cpcxray` is on the PATH (pipx puts it there).
- With a ready-made program, give its full path, e.g.
  `"command": "/home/me/cpcxray-0.9.0/cpcxray"` or
  `"command": "C:\\cpcxray-0.9.0\\cpcxray.exe"`; it uses its own folder as the project folder.
- Another model or configuration: `"args": ["mcp", "--config", "config/cpc6128.toml"]`.

Start Claude Code in the project folder (`claude`); it asks once whether to use the project's MCP
server. `/mcp` in Claude Code shows the connection and the tools.

### Attached: Claude Code works in your window

Start the window yourself (`cpcxray gui`; it serves MCP on port 8765) and register it once:

```bash
claude mcp add --transport http cpcxray-gui http://127.0.0.1:8765/mcp
```

Now Claude sees and drives exactly the machine on your screen: you watch what it does, and you can
take over at any time. This connection only works while the window is open, which is why it is not
in `.mcp.json`.

## 2. First steps

Ask in plain words; Claude picks the tools. For example:

- "Boot the CPC (run 3 seconds) and show me the screen text."
- "Type `CAT` and RETURN, then show the screen." - with the AMSDOS ROM and a disk in A:
- "Take a screenshot of the CPC screen." - `screen_image` returns the picture itself.
- "Which ROMs are installed, and what does each header say?"
- "What is at &B8D9?" - `address_explain` names the variable (here a firmware variable).

## 3. Debugging with Claude

- **Find who changes something**: "Put a write breakpoint on TXT_CURSOR_COLUMN, type `PRINT 1`,
  and explain the code that changed it." Memory breakpoints stop after the instruction and report
  its PC; on a variable name they cover the whole variable.
- **Follow a routine**: "Run until TXT_OUTPUT, then step over the next 20 instructions and
  describe what they do." `step`, `step_over` and `step_out` record into the trace, which Claude
  can read back with `trace_records`.
- **Watch the hardware**: "Record the bus trace for I/O while the firmware sets MODE 2, and
  explain the Gate Array and CRTC writes." `bus_trace_view` gives each port access its meaning.
- **Look at memory and paging**: "Show &C000-&C0FF through the RAM view and tell me which ROM is
  paged in there." (`memory_view`, `paging_get`)

## 4. Analysing a ROM with Claude

A typical session for a ROM in slot 5 (for a scrambled one, set `decoder = "vortex"` in its
configuration):

1. "Make ROM 5 an analysis target and switch the analysis on." (`analysis_target_add`,
   `analysis_enable`)
2. "Boot, then use the ROM: call its commands (`|HELP`, …), access the disk." - every instruction
   that runs is recorded.
3. "How far is the analysis?" (`analysis_status`: coverage, what was seen, conflicts of a
   scrambled ROM)
4. "Save the analysis, generate the source and verify it." (`analysis_save`, `source_generate`,
   `source_verify`) - the source is `project/<rom>.asm`; Verify reports "identical" or the
   differing addresses with their source lines.
5. Improve it together: "This table at &C015 is a list of strings - classify it" (`classify`),
   "Name the routine at &C4D8 `read_sector`" (`symbol_add` with type `sub`), "Add a 16-byte
   variable `drives` at offset &12 in ROM 5's data area" (`variable_add` with base `rom5+0012`),
   then generate again. Your names and comments are kept in `project/annotations.txt`
   (`symbols_save`).

The analysis accumulates: everything is merged at the next start, so several sessions build on
each other.

## 5. Tips

- **Runs are bounded.** Claude gives each run a limit (instructions, emulated µs, or seconds); a
  boot takes about 2-3 emulated seconds. If a run ends with `timeout`, ask for a longer one.
- **Typing waits.** `type_text` runs the machine until the text is typed, about 12 characters per
  emulated second; `\r` is RETURN.
- **Disks**: real images are read-only; ask Claude to insert a writable draft (`disk_new`) for
  anything that writes, and to save it under a new name (`disk_save`).
- **Snapshots**: "Save a snapshot to output/before_load.sna" lets you return to a moment
  (`sna_save`, `sna_load`).
- **In the window**, Claude's commands run between frames; the panels refresh at the next stop.

## 6. Troubleshooting

- **`/mcp` shows cpcxray as failed**: run `cpcxray mcp` in a terminal in the project folder - it
  prints the error (e.g. no configuration: run `cpcxray init`).
- **The `cpcxray-gui` server is not reachable**: the window must be open, with MCP on (not
  `--mcp-port 0`); check the port it shows above the screen.
- **Claude's run stops at once**: a breakpoint is still set (`breakpoint_list`); remove or disable
  it.
