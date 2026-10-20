"""The CPC 464 machine (design.md §6.3, §6.4, §6.10, §6.14).

* `config`: loading and validating `config/machine.toml`, ROM path handling (phase 0).
* `bus`, `memory`, `cpc464` (phase 4): bus with observers, memory map and ROM slots, machine
  and scheduler.
"""
