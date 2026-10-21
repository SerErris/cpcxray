"""Tests of the µPD765 with two drives (design.md §6.11; FR-FDC-01..05).

Commands are sent through the ports exactly as AMSDOS/VDOS do: command bytes to &FB7F while
the MSR shows RQM, execution data and results read while DIO is set.
"""

from __future__ import annotations

from cpcxray.devices.fdc765 import Fdc765
from cpcxray.disk.dsk import DiskImage, blank_image

MSR, DATA = 0xFB7E, 0xFB7F


def send(fdc: Fdc765, *command: int) -> list[int]:
    """Send a command and read everything the FDC offers (execution data, then results)."""
    assert fdc.io_in(MSR) == 0x80  # RQM: ready for a command
    for value in command:
        fdc.io_out(DATA, value)
    received = []
    while (fdc.io_in(MSR) or 0) & 0x40:
        received.append(fdc.io_in(DATA))
    return received


def vortex_fdc() -> tuple[Fdc765, DiskImage]:
    """An FDC with a blank Vortex draft in B: whose sector C1 H0 R3 holds 0..255, 0..255."""
    fdc = Fdc765()
    image = blank_image("vortex")
    sector = image.find_sector(1, 0, (1, 0, 3, 2))
    assert sector is not None
    sector.data[:] = bytes(range(256)) * 2
    fdc.drives[1].image = image
    return fdc, image


def test_decoding_and_motor() -> None:
    """The FDC answers A10 = A7 = 0; &FA7E is the write-only motor latch."""
    fdc = Fdc765()
    assert fdc.decodes(0xFB7E) and fdc.decodes(0xFA7E) and not fdc.decodes(0xF57E)
    fdc.io_out(0xFA7E, 1)
    assert fdc.motor_on and fdc.io_in(0xFA7E) is None


def test_no_disk_is_not_ready() -> None:
    """Without a disk, data commands end with "not ready"; seek interrupts say so too."""
    fdc = Fdc765()
    assert not send(fdc, 0x03, 0xA1, 0x03)  # SPECIFY: no result phase
    assert send(fdc, 0x04, 0x00) == [0x10]  # ST3: track 0 only, not ready
    assert send(fdc, 0x46, 0x00, 0, 0, 0xC1, 2, 0xC1, 0x2A, 0xFF) == [0x48, 0, 0, 0, 0, 0xC1, 2]
    assert not send(fdc, 0x07, 0x00)  # RECALIBRATE
    assert send(fdc, 0x08) == [0x68, 0]
    assert send(fdc, 0x08) == [0x80]  # nothing pending: invalid


def test_seek_and_sense_interrupt() -> None:
    """SEEK moves the head of the selected unit; SENSE INTERRUPT STATUS reports it."""
    fdc, _ = vortex_fdc()
    assert not send(fdc, 0x0F, 0x01, 12)
    assert send(fdc, 0x08) == [0x21, 12]  # seek end, unit 1
    assert fdc.drives[1].cylinder == 12 and fdc.drives[0].cylinder == 0
    assert send(fdc, 0x04, 0x01) == [0x01 | 0x20 | 0x08]  # ready, two-sided, writable draft


def test_read_data_ends_with_end_of_cylinder() -> None:
    """A read of R..EOT transfers the data and ends with ST0 &40, ST1 &80 (CPC behaviour)."""
    fdc, _ = vortex_fdc()
    send(fdc, 0x0F, 0x01, 1)
    send(fdc, 0x08)
    received = send(fdc, 0x46, 0x01, 1, 0, 3, 2, 3, 0x2A, 0xFF)
    assert received[:512] == list(range(256)) * 2
    assert received[512:] == [0x41, 0x80, 0x00, 2, 0, 1, 2]  # C+1, R = 1 (datasheet)


def test_msr_phases() -> None:
    """MSR: command (&90), execution read (&F0), result (&D0), idle (&80)."""
    fdc, _ = vortex_fdc()
    send(fdc, 0x0F, 0x01, 1)
    send(fdc, 0x08)
    fdc.io_out(DATA, 0x46)
    assert fdc.io_in(MSR) == 0x90
    for value in (0x01, 1, 0, 3, 2, 3, 0x2A, 0xFF):
        fdc.io_out(DATA, value)
    assert fdc.io_in(MSR) == 0xF0
    for _ in range(512):
        fdc.io_in(DATA)
    assert fdc.io_in(MSR) == 0xD0
    for _ in range(7):
        fdc.io_in(DATA)
    assert fdc.io_in(MSR) == 0x80


def test_multi_sector_and_multi_track() -> None:
    """R..EOT reads several sectors; with MT the read continues on head 1."""
    fdc, image = vortex_fdc()
    received = send(fdc, 0x46, 0x01, 0, 0, 8, 2, 9, 0x2A, 0xFF)
    assert len(received) == 2 * 512 + 7
    received = send(fdc, 0xC6, 0x01, 0, 0, 9, 2, 9, 0x2A, 0xFF)  # MT: 0/9 then 1/1..1/9
    assert len(received) == 10 * 512 + 7
    assert received[-7:] == [0x41, 0x80, 0, 1, 0, 1, 2]
    del image


def test_missing_sector_and_read_id() -> None:
    """A sector that is not on the track gives "no data"; READ ID returns IDs in disk order."""
    fdc, _ = vortex_fdc()
    assert send(fdc, 0x46, 0x01, 0, 0, 0x41, 2, 0x41, 0x2A, 0xFF) == [0x41, 0x04, 0, 0, 0, 0x41, 2]
    ids = [send(fdc, 0x4A, 0x01)[5] for _ in range(4)]
    assert ids == [1, 6, 2, 7]  # the Vortex interleave


def test_single_sided_drive_ignores_head() -> None:
    """Drive A: has one head: a command for head 1 reads head 0."""
    fdc = Fdc765()
    fdc.drives[0].image = blank_image("data")
    received = send(fdc, 0x46, 0x04, 0, 0, 0xC1, 2, 0xC1, 0x2A, 0xFF)
    assert len(received) == 512 + 7 and received[512] == 0x44  # head bit echoed, abnormal (EN)


def test_write_protected_real_image() -> None:
    """A read-only image refuses writes and formats with ST1 "not writable"."""
    fdc, image = vortex_fdc()
    image.writable = False
    assert send(fdc, 0x45, 0x01, 0, 0, 1, 2, 1, 0x2A, 0xFF) == [0x41, 0x02, 0, 0, 0, 1, 2]
    assert send(fdc, 0x4D, 0x01, 2, 9, 0x52, 0xE5) == [0x41, 0x02, 0, 0, 0, 0, 2]
    assert send(fdc, 0x04, 0x01) == [0x01 | 0x20 | 0x08 | 0x40 | 0x10]
    assert not image.modified


def test_write_and_format_on_a_draft() -> None:
    """WRITE DATA stores a sector; FORMAT TRACK rewrites a track with the given IDs."""
    fdc, image = vortex_fdc()
    for value in (0x45, 0x01, 0, 0, 1, 2, 1, 0x2A, 0xFF):
        fdc.io_out(DATA, value)
    assert fdc.io_in(MSR) == 0xB0  # execution: the FDC wants data
    for index in range(512):
        fdc.io_out(DATA, index & 0xFF)
    assert [fdc.io_in(DATA) for _ in range(7)] == [0x41, 0x80, 0, 1, 0, 1, 2]
    sector = image.find_sector(0, 0, (0, 0, 1, 2))
    assert sector is not None and sector.read() == bytes(range(256)) * 2 and image.modified
    for value in (0x4D, 0x01, 2, 2, 0x52, 0xAA):
        fdc.io_out(DATA, value)
    for value in (0, 0, 0x41, 2, 0, 0, 0x42, 2):
        fdc.io_out(DATA, value)
    assert [fdc.io_in(DATA) for _ in range(7)] == [0x01, 0, 0, 0, 0, 0x42, 2]
    track = image.track(0, 0)
    assert track is not None and [s.sector_id for s in track.sectors] == [0x41, 0x42]
    assert track.sectors[0].read() == b"\xaa" * 512


def test_invalid_command_and_log() -> None:
    """Unknown commands give &80; the log collapses repeats."""
    fdc = Fdc765()
    assert send(fdc, 0x1F) == [0x80]
    send(fdc, 0x03, 0xA1, 0x03)
    send(fdc, 0x03, 0xA1, 0x03)
    assert fdc.state()["recent_commands"][-1] == "SPECIFY 03 a1 03 ->   (x2)"


def test_drive_lights() -> None:
    """The light of the drive addressed last is on while the (shared) motor runs."""
    fdc, _ = vortex_fdc()
    assert fdc.drive_lights() == [False, False]
    fdc.io_out(0xFA7E, 1)
    send(fdc, 0x04, 0x01)  # SENSE DRIVE STATUS of B:
    assert fdc.drive_lights() == [False, True]
    send(fdc, 0x03, 0xA1, 0x03)  # SPECIFY has no unit: the selection stays
    assert fdc.drive_lights() == [False, True] and fdc.state()["selected_drive"] == "B"
    send(fdc, 0x04, 0x00)
    assert fdc.drive_lights() == [True, False]
    fdc.io_out(0xFA7E, 0)
    assert fdc.drive_lights() == [False, False]
