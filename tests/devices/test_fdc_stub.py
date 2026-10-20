"""Tests of the µPD765 stub with no drive ready (design.md §6.11)."""

from __future__ import annotations

from cpcxray.devices.fdc765 import FdcStub


def send(fdc: FdcStub, *command: int) -> list[int]:
    """Send a command through &FB7F and read all result bytes while DIO is set."""
    assert fdc.io_in(0xFB7E) == 0x80  # RQM: ready for a command
    for value in command:
        fdc.io_out(0xFB7F, value)
    results = []
    while (fdc.io_in(0xFB7E) or 0) & 0x40:
        results.append(fdc.io_in(0xFB7F))
    return results


def test_decoding_and_motor() -> None:
    """The FDC answers A10 = A7 = 0; &FA7E is the write-only motor latch."""
    fdc = FdcStub()
    assert fdc.decodes(0xFB7E) and fdc.decodes(0xFA7E) and not fdc.decodes(0xF57E)
    fdc.io_out(0xFA7E, 1)
    assert fdc.motor_on and fdc.io_in(0xFA7E) is None


def test_results_for_drive_not_ready() -> None:
    """Data commands end abnormally with "not ready"; ST3 shows no ready drive."""
    fdc = FdcStub()
    assert not send(fdc, 0x03, 0xA1, 0x03)  # SPECIFY: no result phase
    assert send(fdc, 0x04, 0x01) == [0x01]  # SENSE DRIVE STATUS: ST3 = unit only
    assert send(fdc, 0x46, 0x00, 0x05, 0x00, 0xC1, 0x02, 0xC9, 0x2A, 0xFF) == [
        0x48,
        0x00,
        0x00,
        0x05,
        0x00,
        0xC1,
        0x02,
    ]  # READ DATA: ST0 = abnormal + not ready, ST1, ST2, C H R N


def test_seek_interrupt_and_invalid() -> None:
    """RECALIBRATE leaves an interrupt for SENSE INTERRUPT STATUS; otherwise it is invalid."""
    fdc = FdcStub()
    assert send(fdc, 0x08) == [0x80]
    assert not send(fdc, 0x07, 0x01)  # RECALIBRATE: no result phase
    assert send(fdc, 0x08) == [0x69, 0x00]  # abnormal, seek end, not ready, unit 1; PCN 0
    assert send(fdc, 0x1F) == [0x80]  # unknown command
