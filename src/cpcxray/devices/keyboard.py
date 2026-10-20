"""The CPC keyboard matrix and timed typing (design.md §6.9; FR-MCH-09).

Ten rows of eight keys; a 0 bit means "pressed", so an idle row reads &FF. The firmware scans
the rows itself through the PSG's I/O port (see system_io.py), which is why the matrix is
emulated rather than the firmware keyboard calls (D-07). Keys are numbered `row * 8 + bit`.

Several sources can hold keys at the same time and are combined (a key is down if any source
holds it): "api" (key_press/key_release over MCP), "host" (the PC keyboard in the GUI, set every
frame) and "typing" (the scheduled presses of `type_text`).

Typing: the firmware notices a key only when it scans the keyboard (once per frame, 20 ms), and
it ignores a held key until it has been released. `schedule_typing` therefore holds every
character's keys for `hold_us` and releases them for `gap_us` (default 40 ms each, i.e. about
12 characters per emulated second); the machine calls `tick(clock_us)` after each instruction to
apply the events that are due.

SHIFT and CONTROL are pressed `MODIFIER_LEAD_US` before the character key and released the same
time after it. The firmware reads the ten rows one after another during its scan; if SHIFT and
the letter changed together while a scan is running, the scan could see the letter (row 4-8)
without SHIFT (row 2) and type a lower-case letter ("NexT" instead of "NEXT").
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

ROWS = 10
DEFAULT_HOLD_US = 40_000
DEFAULT_GAP_US = 40_000
MODIFIER_LEAD_US = 20_000  # one frame: a complete scan sees SHIFT/CONTROL before the key
MODIFIER_KEYS = frozenset({21, 23})  # SHIFT (row 2 bit 5), CONTROL (row 2 bit 7)


class Keyboard:
    """The key matrix with its sources and the typing schedule.

    Attributes:
        rows: One byte per row, bit = 0 for a pressed key (recomputed on every change).
    """

    def __init__(self) -> None:
        """Create the matrix with no key pressed."""
        self.rows = bytearray([0xFF] * ROWS)
        self._held: dict[str, set[int]] = {}
        self._events: deque[tuple[int, frozenset[int]]] = deque()  # (time, keys held from then)

    def reset(self) -> None:
        """Release all keys and drop pending typing."""
        self._held.clear()
        self._events.clear()
        self._update()

    # -- holding keys ----------------------------------------------------------------------------

    def press(self, key: int, source: str = "api") -> None:
        """Hold key number `key` (0-79) for `source`."""
        self._held.setdefault(source, set()).add(key)
        self._update()

    def release(self, key: int, source: str = "api") -> None:
        """Let go of a key held by `source`."""
        self._held.get(source, set()).discard(key)
        self._update()

    def set_held(self, source: str, keys: Iterable[int]) -> None:
        """Replace all keys held by `source` (the GUI calls this every frame)."""
        new_keys = set(keys)
        if new_keys != self._held.get(source, set()):
            self._held[source] = new_keys
            self._update()

    def release_all(self, source: str | None = None) -> None:
        """Release the keys of one source, or of all sources."""
        if source is None:
            self._held.clear()
        else:
            self._held.pop(source, None)
        self._update()

    def held_keys(self) -> list[int]:
        """Return all key numbers currently held, sorted."""
        return sorted(set().union(*self._held.values()))

    def _update(self) -> None:
        """Recompute the row bytes from all sources."""
        rows = bytearray([0xFF] * ROWS)
        for keys in self._held.values():
            for key in keys:
                rows[key >> 3] &= ~(1 << (key & 7)) & 0xFF
        self.rows[:] = rows

    def read_row(self, row: int) -> int:
        """Return a row's bits; rows above 9 read &FF (nothing connected)."""
        return self.rows[row] if row < ROWS else 0xFF

    # -- typing ----------------------------------------------------------------------------------

    @property
    def typing(self) -> bool:
        """True while scheduled typing events are pending."""
        return bool(self._events)

    def schedule_typing(
        self,
        key_groups: Iterable[tuple[int, ...]],
        start_us: int,
        hold_us: int = DEFAULT_HOLD_US,
        gap_us: int = DEFAULT_GAP_US,
    ) -> int:
        """Schedule pressing each group of keys (e.g. SHIFT + P) for `hold_us`, then a gap.

        Typing that is already scheduled continues first; new groups follow after it.

        Returns:
            The clock time at which the last key is released.
        """
        time_us = max(start_us, self._events[-1][0] if self._events else start_us)
        for keys in key_groups:
            modifiers = frozenset(keys) & MODIFIER_KEYS
            if modifiers:  # SHIFT/CONTROL first and last (see the module docstring)
                self._events.append((time_us, modifiers))
                time_us += MODIFIER_LEAD_US
            self._events.append((time_us, frozenset(keys)))
            time_us += hold_us
            if modifiers:
                self._events.append((time_us, modifiers))
                time_us += MODIFIER_LEAD_US
            self._events.append((time_us, frozenset()))
            time_us += gap_us
        return time_us - gap_us

    def tick(self, clock_us: int) -> None:
        """Apply the typing events that are due at `clock_us`."""
        while self._events and self._events[0][0] <= clock_us:
            _, keys = self._events.popleft()
            self.set_held("typing", keys)
