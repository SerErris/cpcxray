"""Upper ROM select latch (design.md §5.2, D-09; FR-MCH-03).

Writing to a port with A13 = 0 (&DFxx, `OUT (C),C` with BC = &DFnn) latches the ROM number;
the memory map pages in the matching slot (or slot 0 for unpopulated numbers, D-10). The latch
cannot be read.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cpcxray.devices import NOT_DRIVEN


class RomSelect:
    """The write-only ROM number latch.

    Attributes:
        selected: The last value written.
    """

    def __init__(self, select_upper: Callable[[int], None]) -> None:
        """Create the latch; `select_upper` is the memory map's selection method."""
        self._select_upper = select_upper
        self.selected = 0

    def reset(self) -> None:
        """Select ROM 0 (the firmware selects explicitly before using upper ROMs)."""
        self.selected = 0
        self._select_upper(0)

    @staticmethod
    def decodes(port: int) -> bool:
        """The latch responds to A13 = 0."""
        return port & 0x2000 == 0

    def io_out(self, port: int, value: int) -> None:
        """Latch the ROM number."""
        del port
        self.selected = value
        self._select_upper(value)

    def io_in(self, port: int) -> int | None:
        """The latch cannot be read."""
        del port
        return NOT_DRIVEN

    def state(self) -> dict[str, Any]:
        """Return the latch for the device view."""
        return {"selected": self.selected}
