"""Z80 CPU (design.md §6.2, phase 1).

* `z80`: the instruction-level Z80 core (`Z80`).
* `bus`: the bus interface the CPU calls (`CpuBus`), access kinds (`Cycle`), `FlatBus`.
* `tables`: flag bit constants and flag lookup tables.
* `timing`: CPC instruction costs in NOPs (D-04).
* `disasm` (phase 3): table-driven disassembler.
"""
