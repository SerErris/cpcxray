# Changelog

## 0.1.0 - first release

- CPC 464, 664 and 6128 (RAM banking) booting the real firmware, BASIC and AMSDOS; µPD765
  floppy controller with DSK / EDSK images (3" drive A, 80-track drive B).
- Decoding of Vortex-scrambled ROMs (M1 raw, data reads decoded), access map, conflicts, clear
  ROM, ROM map, coverage CSV.
- Analysis of ROM and RAM targets, provenance of code copied to RAM, analysis database and a
  reassemblable sjasmplus source with a byte-for-byte round-trip check.
- Debugger window: CPU, Memory, Devices, Breakpoints, Trace, Bus trace, Symbols, Disks and
  Project tabs; the PC keyboard types by character on any layout.
- MCP server (stdio and HTTP) with every debugger function as a tool.
- `cpcxray init` creates a project folder with the standard ROMs.
