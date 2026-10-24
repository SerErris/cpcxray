"""The simulated PAL frame shared by VSYNC and the Gate Array interrupt (design.md §5.3, D-05).

There is no CRTC timing (D-06): scan lines are 64 µs steps of the machine clock. Scan line n
runs from n * 64 to n * 64 + 63 µs, its frame line is n % 312, and VSYNC covers frame lines 0-7.
`SystemIO` (VSYNC bit of port B) and `GateArray` (line counter R52) both count from these
constants, so the interrupt can never drift away from VSYNC.
"""

LINE_US = 64  # one scan line
FRAME_LINES = 312  # PAL: 312 lines = 6 * 52, so six interrupts per frame
FRAME_US = FRAME_LINES * LINE_US  # 19,968 µs, 50.08 Hz
VSYNC_LINES = 8
VSYNC_US = VSYNC_LINES * LINE_US  # 512 µs
INTERRUPT_LINES = 52  # the Gate Array divides by 52: 300 Hz
INTERRUPT_PERIOD_US = INTERRUPT_LINES * LINE_US  # 3,328 µs
RESYNC_LINE = 2  # the 2nd HSYNC after VSYNC start resynchronises R52
