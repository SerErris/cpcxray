"""What an IN or OUT means on a CPC: the Bus trace tab's port meanings (task 5f).

The CPC decodes ports by single address lines (design.md §6.7-§6.11), so a port is named by
the device whose line is low - checked in the order the devices are wired: Gate Array (A15 = 0,
A14 = 1), CRTC (A14 = 0), ROM select (A13 = 0), printer (A12 = 0), PPI 8255 (A11 = 0), FDC
(A10 = A7 = 0). A port can select several devices at once (the hardware lets it); the first one
names it.

`PortMeanings` reads a sequence of accesses in order and keeps what a meaning depends on from
earlier ones (the CRTC register selected last), so "CRTC R12 = 30" can be told. Accesses before
the first select in the sequence say "CRTC register write".
"""

from __future__ import annotations

from cpcxray.devices.crtc import REGISTER_NAMES
from cpcxray.devices.system_io import port_c_meaning

GATE_ARRAY_FUNCTIONS = ("pen select", "colour", "mode and ROMs", "RAM configuration")
PPI_PORTS = ("PPI port A (PSG data)", "PPI port B", "PPI port C", "PPI control")


def gate_array_meaning(value: int) -> str:
    """A Gate Array write: pen select, colour, mode and ROM enables, or RAM configuration."""
    function = value >> 6
    if function == 0:
        return f"Gate Array: select {'border' if value & 0x10 else f'pen {value & 0x0F}'}"
    if function == 1:
        return f"Gate Array: colour &{value & 0x1F:02X} (hardware) for the selected pen"
    if function == 2:
        lower = "off" if value & 0x04 else "on"
        upper = "off" if value & 0x08 else "on"
        reset = ", interrupt reset" if value & 0x10 else ""
        return f"Gate Array: mode {value & 0x03}, lower ROM {lower}, upper ROM {upper}{reset}"
    return f"Gate Array: RAM configuration {value & 0x07}"


class PortMeanings:  # pylint: disable=too-few-public-methods  # one question: what does it mean
    """Port meanings for a sequence of accesses (see the module docstring)."""

    def __init__(self) -> None:
        """Start without a known CRTC register selection."""
        self.crtc_selected: int | None = None

    def meaning(self, access: str, port: int, value: int) -> str:  # pylint: disable=too-many-return-statements  # one per device
        """The meaning of an IN ("in") or OUT ("out") of `value` at `port`."""
        if port & 0xC000 == 0x4000:
            return gate_array_meaning(value) if access == "out" else "Gate Array (no read)"
        if port & 0x4000 == 0:
            return self._crtc(access, port >> 8 & 0x03, value)
        if port & 0x2000 == 0:
            return f"ROM select: ROM {value}" if access == "out" else "ROM select"
        if port & 0x1000 == 0:
            return "printer"
        if port & 0x0800 == 0:
            return self._ppi(access, port >> 8 & 0x03, value)
        if port & 0x0480 == 0:
            return _fdc(access, port, value)
        return "no device"

    def _crtc(self, access: str, function: int, value: int) -> str:
        """CRTC: select a register (&BCxx), write it (&BDxx), status / read (&BExx/&BFxx)."""
        if function == 0 and access == "out":
            self.crtc_selected = value & 0x1F
            return f"CRTC: select R{self.crtc_selected}"
        register = self.crtc_selected
        name = (f"R{register} {REGISTER_NAMES[register]}" if register is not None
                and register < len(REGISTER_NAMES) else "register")  # fmt: skip
        if function == 1 and access == "out":
            return f"CRTC: {name} = {value}"
        return f"CRTC: read {name}" if function == 3 else "CRTC: status"

    @staticmethod
    def _ppi(access: str, port_index: int, value: int) -> str:
        """PPI 8255: port C in words (keyboard line, motor, PSG function), the others named."""
        if port_index == 2 and access == "out":
            return f"PPI port C: {port_c_meaning(value)}"
        if port_index == 1 and access == "in":
            return f"PPI port B: VSYNC {value & 1}"
        if port_index == 0 and access == "in":
            return "PPI port A: keyboard line (via PSG)"
        return PPI_PORTS[port_index]


def _fdc(access: str, port: int, value: int) -> str:
    """FDC µPD765: the drive motor latch (&FA7E), main status (&FB7E), data (&FB7F)."""
    if not port & 0x0100:
        return f"FDC: motor {'on' if value & 1 else 'off'}" if access == "out" else "FDC"
    if not port & 0x0001:
        return "FDC: main status"
    return "FDC: data"
