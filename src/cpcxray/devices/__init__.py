"""I/O devices of the CPC (design.md §6.7-§6.9, §6.11; phase 4, FDC phase 10).

Gate Array, CRTC register file, upper ROM select, SystemIO (PPI/PSG replacement, D-07),
keyboard matrix and later the µPD765 FDC. Each device decodes its own port bits (D-08).
"""

NOT_DRIVEN = None
"""Returned by `io_in` of a device that does not drive the data bus (write-only ports)."""
