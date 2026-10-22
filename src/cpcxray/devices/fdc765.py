"""µPD765 floppy disk controller with two drives (design.md §6.11; FR-FDC-01..05).

Ports (DDI-1 decoding: A10 = 0 and A7 = 0):

| Port  | A8 A0 | Function                                     |
|-------|-------|----------------------------------------------|
| &FA7E | 0 x   | motor on/off latch (write)                   |
| &FB7E | 1 0   | main status register MSR (read)              |
| &FB7F | 1 1   | data register: command bytes, data, results  |

Every command passes three phases; the CPU polls the MSR (no DMA, no interrupts, and no overrun
timing - impossible with instruction-level stepping, design.md §6.11):

| Phase     | MSR  | Meaning                                                     |
|-----------|------|-------------------------------------------------------------|
| idle      | &80  | RQM: ready for a command byte                               |
| command   | &90  | RQM + CB: more command bytes expected                       |
| execution | &F0  | RQM + DIO + EXM + CB: a data byte for the CPU (read)        |
|           | &B0  | RQM + EXM + CB: the FDC wants a data byte (write, format)   |
| result    | &D0  | RQM + DIO + CB: result bytes for the CPU                    |

Commands: READ DATA, READ DELETED DATA, READ TRACK, WRITE DATA, WRITE DELETED DATA, FORMAT
TRACK, READ ID, SPECIFY, SENSE DRIVE STATUS, RECALIBRATE, SEEK, SENSE INTERRUPT STATUS; SCAN
and unknown codes give the "invalid command" result (ST0 = &80).

Sectors are found by their ID (C, H, R, N) on the track under the head, as on the real chip.
The CPC has no terminal count line: a read or write ends after sector EOT with "abnormal
termination" and ST1 "end of cylinder" (ST0 &40, ST1 &80) - AMSDOS expects exactly that.
With MT set, a transfer continues from head 0 to sector 1 of head 1.

Drives: unit 0 = A:, unit 1 = B: (the DDI-1 decodes US0 only, so units 2 and 3 are A: and B:
again). Both drives share the motor line (&FA7E); a drive's activity light (`drive_lights`) is
on while the motor runs and that drive was the last one a command addressed. A drive is ready
when a disk image is inserted. A single-sided drive ignores the head select. A read-only image
is reported write protected: writes and formats fail with ST1 NW.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from cpcxray.devices import NOT_DRIVEN
from cpcxray.disk.dsk import DiskImage, Sector

MSR_RQM = 0x80
MSR_DIO = 0x40
MSR_EXM = 0x20
MSR_CB = 0x10
ST0_ABNORMAL = 0x40
ST0_INVALID = 0x80
ST0_SEEK_END = 0x20
ST0_NOT_READY = 0x08
ST0_HEAD = 0x04
ST1_END_OF_CYLINDER = 0x80
ST1_DATA_ERROR = 0x20
ST1_NO_DATA = 0x04
ST1_NOT_WRITABLE = 0x02
ST1_MISSING_ADDRESS_MARK = 0x01
ST2_CONTROL_MARK = 0x40
ST2_DATA_ERROR_IN_DATA = 0x20
ST3_WRITE_PROTECTED = 0x40
ST3_READY = 0x20
ST3_TRACK_0 = 0x10
ST3_TWO_SIDE = 0x08

# command code (low 5 bits) → number of command bytes including the command itself
COMMAND_LENGTHS = {
    0x02: 9, 0x03: 3, 0x04: 2, 0x05: 9, 0x06: 9, 0x07: 2, 0x08: 1, 0x09: 9,
    0x0A: 2, 0x0C: 9, 0x0D: 6, 0x0F: 3, 0x11: 9, 0x19: 9, 0x1D: 9,
}  # fmt: skip
NAMES = {
    0x02: "READ TRACK", 0x03: "SPECIFY", 0x04: "SENSE DRIVE STATUS", 0x05: "WRITE DATA",
    0x06: "READ DATA", 0x07: "RECALIBRATE", 0x08: "SENSE INTERRUPT STATUS",
    0x09: "WRITE DELETED DATA", 0x0A: "READ ID", 0x0C: "READ DELETED DATA", 0x0D: "FORMAT TRACK",
    0x0F: "SEEK", 0x11: "SCAN EQUAL", 0x19: "SCAN LOW OR EQUAL", 0x1D: "SCAN HIGH OR EQUAL",
}  # fmt: skip
READ_COMMANDS = frozenset({0x02, 0x06, 0x0C})
UNIT_COMMANDS = frozenset(COMMAND_LENGTHS) - {0x03, 0x08}  # byte 1 holds US/HD
WRITE_COMMANDS = frozenset({0x05, 0x09})


@dataclass
class Drive:
    """A floppy drive unit.

    Attributes:
        name: "A" or "B".
        cylinders: Cylinders the mechanism can reach (40 for the 3" drive, 80 for Vortex).
        double_sided: The drive has two heads (Vortex) or one (3" CPC drive).
        image: The inserted disk, or None.
        cylinder: Current head position.
        rotation: Sector index under the head next (for READ ID).
    """

    name: str
    cylinders: int
    double_sided: bool
    image: DiskImage | None = None
    cylinder: int = 0
    rotation: int = 0

    @property
    def ready(self) -> bool:
        """A disk is inserted."""
        return self.image is not None

    def physical_head(self, head: int) -> int:
        """The head that actually reads: a single-sided drive ignores the side select."""
        return head if self.double_sided else 0


@dataclass
class _Transfer:  # pylint: disable=too-many-instance-attributes  # one field per command byte
    """State of a READ/WRITE DATA command in its execution phase.

    Attributes:
        code: Command code.
        unit_and_head: Command byte 1 (US and HD bits).
        id_field: C, H, R, N of the sector being transferred.
        end_of_track: EOT, the last sector number.
        multi_track: MT bit: continue on head 1.
        skip_deleted: SK bit.
        data_length: DTL (bytes per sector when N = 0).
        buffer: Bytes collected for the current sector (writes).
        sector: The sector being written.
    """

    code: int
    unit_and_head: int
    id_field: list[int]
    end_of_track: int
    multi_track: bool
    skip_deleted: bool
    data_length: int
    buffer: bytearray = field(default_factory=bytearray)
    sector: Sector | None = None


class Fdc765:  # pylint: disable=too-many-instance-attributes  # controller and drive state
    """The µPD765 and its two drives (see the module docstring).

    Attributes:
        drives: Unit 0 (A:) and unit 1 (B:).
        motor_on: Last value written to the motor latch (bit 0); both drives share it.
        selected_unit: The drive (0 = A:, 1 = B:) the last command addressed.
        command: Command bytes received so far.
        data_out: Bytes waiting for the CPU (execution data of a read, then results).
        results: Result bytes waiting to be read after the data.
        pending_interrupts: (ST0, PCN) of finished SEEK/RECALIBRATE commands.
        log: The last commands with their results and a repeat count (disk ROMs may send
            SPECIFY over and over), for the device view.
    """

    def __init__(self) -> None:
        """Create an idle controller with A: (3", 40 tracks, 1 side) and B: (Vortex, 80, 2)."""
        self.drives = [Drive("A", 40, False), Drive("B", 80, True)]
        self.selected_unit = 0
        self._reset_state()

    def _reset_state(self) -> None:
        """Controller state after a reset (inserted disks stay, heads keep their position)."""
        self.motor_on = False
        self.command: list[int] = []
        self.data_out: deque[int] = deque()
        self.results: list[int] = []
        self.pending_interrupts: deque[tuple[int, int]] = deque()
        self.log: deque[list[Any]] = deque(maxlen=32)  # [name, command, results, count]
        self._transfer: _Transfer | None = None
        self._format: dict[str, Any] | None = None
        self._last_command: tuple[str, list[int]] | None = None
        self._pending_error: tuple[int, int, int] | None = None  # ends a read after the sector

    def reset(self) -> None:
        """Back to idle, motor off; disks stay inserted."""
        self._reset_state()

    # -- ports -----------------------------------------------------------------------------------

    @staticmethod
    def decodes(port: int) -> bool:
        """FDC and motor latch respond to A10 = 0 and A7 = 0."""
        return port & 0x0480 == 0

    def main_status(self) -> int:
        """Return the MSR for the current phase (see the module docstring)."""
        if self.data_out:
            executing = MSR_EXM if self._transfer is not None else 0
            return MSR_RQM | MSR_DIO | MSR_CB | executing
        if self._transfer is not None or self._format is not None:
            return MSR_RQM | MSR_EXM | MSR_CB  # waiting for data from the CPU
        if self.command:
            return MSR_RQM | MSR_CB
        return MSR_RQM

    def io_in(self, port: int) -> int | None:
        """Read the MSR (&FB7E) or the data register (&FB7F)."""
        if not port & 0x0100:
            return NOT_DRIVEN  # the motor latch cannot be read
        if not port & 0x0001:
            return self.main_status()
        return self._read_data()

    def io_out(self, port: int, value: int) -> None:
        """Write the motor latch (&FA7E) or the data register (&FB7F)."""
        if not port & 0x0100:
            self.motor_on = bool(value & 1)
        elif port & 0x0001:
            self._write_data(value)

    def _read_data(self) -> int:
        """Next execution or result byte; a read transfer moves on to the next sector."""
        if not self.data_out:
            return 0xFF
        value = self.data_out.popleft()
        if not self.data_out and self._transfer is not None:
            self._continue_read()
        return value

    def _write_data(self, value: int) -> None:
        """A command byte, or a data byte during a write/format execution phase."""
        if self._transfer is not None:
            self._write_byte(value)
        elif self._format is not None:
            self._format_byte(value)
        else:
            self._command_byte(value)

    # -- command phase ---------------------------------------------------------------------------

    def _command_byte(self, value: int) -> None:
        """Collect a command; when complete, execute it."""
        if self.data_out:  # a write during the result phase restarts (lenient)
            self.data_out.clear()
        self.command.append(value)
        code = self.command[0] & 0x1F
        if len(self.command) < COMMAND_LENGTHS.get(code, 1):
            return
        command, self.command = self.command, []
        self._last_command = (NAMES.get(code, f"invalid &{code:02X}"), command)
        if code in UNIT_COMMANDS:
            self.selected_unit = command[1] & 0x01
        self._execute(code, command)

    def _execute(self, code: int, command: list[int]) -> None:  # pylint: disable=too-many-branches  # one case per command
        """Start a complete command."""
        if code in READ_COMMANDS:
            self._start_read(code, command)
        elif code in WRITE_COMMANDS:
            self._start_write(code, command)
        elif code == 0x0D:
            self._start_format(command)
        elif code == 0x0A:
            self._read_id(command)
        elif code == 0x04:
            self._finish([self._status_3(command[1])])
        elif code in (0x07, 0x0F):
            self._seek(command, 0 if code == 0x07 else command[2])
        elif code == 0x08:
            if self.pending_interrupts:
                status_0, cylinder = self.pending_interrupts.popleft()
                self._finish([status_0, cylinder])
            else:
                self._finish([ST0_INVALID])
        elif code == 0x03:
            self._finish([])
        else:
            self._finish([ST0_INVALID])

    def _finish(self, results: list[int]) -> None:
        """Enter the result phase (or idle when there are no results)."""
        self._transfer = None
        self._format = None
        self.data_out.extend(results)
        if self._last_command is not None:
            name, command = self._last_command
            if self.log and self.log[-1][:3] == [name, command, results]:
                self.log[-1][3] += 1
            else:
                self.log.append([name, command, results, 1])
            self._last_command = None

    # -- helpers ---------------------------------------------------------------------------------

    def _drive(self, unit_and_head: int) -> Drive:
        """The drive selected by US0 (units 2 and 3 alias 0 and 1 on the DDI-1)."""
        return self.drives[unit_and_head & 0x01]

    @staticmethod
    def _status_0(unit_and_head: int, flags: int) -> int:
        """ST0 with the unit and head bits of the command."""
        return flags | (unit_and_head & 0x07)

    def _status_3(self, unit_and_head: int) -> int:
        """ST3 of SENSE DRIVE STATUS: ready, track 0, two-sided, write protection."""
        drive = self._drive(unit_and_head)
        status = unit_and_head & 0x07
        if drive.ready:
            status |= ST3_READY
            if drive.image is not None and not drive.image.writable:
                status |= ST3_WRITE_PROTECTED
        if drive.cylinder == 0:
            status |= ST3_TRACK_0
        if drive.double_sided:
            status |= ST3_TWO_SIDE
        return status

    def _not_ready(self, unit_and_head: int, id_field: list[int]) -> None:
        """Result of a data command on a drive without a disk."""
        status_0 = self._status_0(unit_and_head, ST0_ABNORMAL | ST0_NOT_READY)
        self._finish([status_0, 0, 0, *id_field])

    # -- seek, recalibrate, read id --------------------------------------------------------------

    def _seek(self, command: list[int], target: int) -> None:
        """SEEK / RECALIBRATE: move the head at once; SENSE INTERRUPT STATUS reports it."""
        drive = self._drive(command[1])
        unit = command[1] & 0x03
        if not drive.ready:
            status_0 = ST0_ABNORMAL | ST0_SEEK_END | ST0_NOT_READY | unit
        else:
            status_0 = ST0_SEEK_END | unit
        drive.cylinder = min(target, drive.cylinders + 2)  # the mechanism has a few spare tracks
        drive.rotation = 0
        self.pending_interrupts.append((status_0, drive.cylinder))
        self._finish([])

    def _read_id(self, command: list[int]) -> None:
        """READ ID: the ID of the next sector passing the head."""
        unit_and_head = command[1]
        drive = self._drive(unit_and_head)
        if drive.image is None:
            self._not_ready(unit_and_head, [0, 0, 0, 0])
            return
        track = drive.image.track(drive.cylinder, drive.physical_head(unit_and_head >> 2 & 1))
        if track is None:
            status_0 = self._status_0(unit_and_head, ST0_ABNORMAL)
            self._finish([status_0, ST1_MISSING_ADDRESS_MARK, 0, 0, 0, 0, 0])
            return
        sector = track.sectors[drive.rotation % len(track.sectors)]
        drive.rotation += 1
        self._finish([self._status_0(unit_and_head, 0), 0, 0, *sector.id_field()])

    # -- read ------------------------------------------------------------------------------------

    def _start_read(self, code: int, command: list[int]) -> None:
        """READ DATA / READ DELETED DATA / READ TRACK: transfer sectors R..EOT."""
        unit_and_head = command[1]
        if self._drive(unit_and_head).image is None:
            self._not_ready(unit_and_head, command[2:6])
            return
        self._transfer = _Transfer(
            code=code, unit_and_head=unit_and_head, id_field=list(command[2:6]),
            end_of_track=command[6], multi_track=bool(command[0] & 0x80),
            skip_deleted=bool(command[0] & 0x20), data_length=command[8],
        )  # fmt: skip
        if code == 0x02:
            self._transfer.id_field[2] = 0  # READ TRACK counts sectors from the index hole
        self._next_read_sector()

    def _locate(self, transfer: _Transfer) -> tuple[Drive, int, Sector | None, int]:
        """Find the sector of the transfer: (drive, physical head, sector, ST1 if missing)."""
        drive = self._drive(transfer.unit_and_head)
        assert drive.image is not None
        head = drive.physical_head(transfer.id_field[1] if transfer.multi_track
                                   else transfer.unit_and_head >> 2 & 1)  # fmt: skip
        track = drive.image.track(drive.cylinder, head)
        if track is None:
            return drive, head, None, ST1_MISSING_ADDRESS_MARK | ST1_NO_DATA
        if transfer.code == 0x02:  # READ TRACK: physical order, IDs not compared
            index = transfer.id_field[2]
            sector = track.sectors[index] if index < len(track.sectors) else None
            return drive, head, sector, 0 if sector else ST1_NO_DATA
        sector = drive.image.find_sector(drive.cylinder, head, tuple(transfer.id_field))  # type: ignore[arg-type]
        return drive, head, sector, 0 if sector else ST1_NO_DATA

    def _next_read_sector(self) -> None:
        """Queue the data of the current sector, or finish with an error result."""
        transfer = self._transfer
        assert transfer is not None
        _, _, sector, missing = self._locate(transfer)
        if sector is None:
            self._end_transfer(ST0_ABNORMAL, missing, 0)
            return
        deleted = bool(sector.status2 & ST2_CONTROL_MARK)
        wants_deleted = transfer.code == 0x0C
        if deleted != wants_deleted and transfer.code != 0x02:
            if transfer.skip_deleted:
                self._advance_or_end(transfer)
                return
            self._queue_sector_data(transfer, sector)
            self._pending_error = (ST0_ABNORMAL, 0, ST2_CONTROL_MARK)
            return
        self._queue_sector_data(transfer, sector)
        error = sector.status1 & ST1_DATA_ERROR
        self._pending_error = (ST0_ABNORMAL, error, sector.status2) if error else None

    def _queue_sector_data(self, transfer: _Transfer, sector: Sector) -> None:
        """Put a sector's data (DTL bytes if N = 0) into the output queue."""
        data = sector.read()
        if transfer.id_field[3] == 0:
            data = data[: transfer.data_length]
        self.data_out.extend(data)

    def _continue_read(self) -> None:
        """All bytes of a sector were read: next sector, or the result."""
        transfer = self._transfer
        assert transfer is not None
        error, self._pending_error = self._pending_error, None
        if error is not None:
            self._end_transfer(*error)
            return
        self._advance_or_end(transfer)
        if self._transfer is not None and not self.data_out:
            self._next_read_sector()

    def _advance_or_end(self, transfer: _Transfer) -> None:
        """Step to the next sector, or end at EOT (the CPC's normal "end of cylinder")."""
        if transfer.id_field[2] == transfer.end_of_track:
            if transfer.multi_track and transfer.id_field[1] == 0:
                transfer.id_field[1] = 1
                transfer.id_field[2] = 1
                return
            self._end_transfer(ST0_ABNORMAL, ST1_END_OF_CYLINDER, 0, end_of_cylinder=True)
            return
        transfer.id_field[2] += 1

    def _end_transfer(
        self, status_0_flags: int, status_1: int, status_2: int, end_of_cylinder: bool = False
    ) -> None:
        """Result phase of a read/write: ST0, ST1, ST2, C, H, R, N."""
        transfer = self._transfer
        assert transfer is not None
        cylinder, head, record, size = transfer.id_field
        if end_of_cylinder:  # datasheet: after EOT the ID points to the next cylinder
            cylinder, record = cylinder + 1, 1
            if transfer.multi_track:
                head = 0
        status_0 = self._status_0(transfer.unit_and_head, status_0_flags)
        self._transfer = None
        self._finish([status_0, status_1, status_2, cylinder & 0xFF, head, record & 0xFF, size])

    # -- write -----------------------------------------------------------------------------------

    def _start_write(self, code: int, command: list[int]) -> None:
        """WRITE DATA / WRITE DELETED DATA: receive sectors R..EOT."""
        unit_and_head = command[1]
        drive = self._drive(unit_and_head)
        if drive.image is None:
            self._not_ready(unit_and_head, command[2:6])
            return
        self._transfer = _Transfer(
            code=code, unit_and_head=unit_and_head, id_field=list(command[2:6]),
            end_of_track=command[6], multi_track=bool(command[0] & 0x80), skip_deleted=False,
            data_length=command[8],
        )  # fmt: skip
        if not drive.image.writable:
            self._end_transfer(ST0_ABNORMAL, ST1_NOT_WRITABLE, 0)
            return
        self._next_write_sector()

    def _next_write_sector(self) -> None:
        """Find the sector to write next, or end with "no data"."""
        transfer = self._transfer
        assert transfer is not None
        _, _, sector, missing = self._locate(transfer)
        if sector is None:
            self._end_transfer(ST0_ABNORMAL, missing, 0)
            return
        transfer.sector = sector
        transfer.buffer.clear()

    def _write_byte(self, value: int) -> None:
        """Collect a data byte; a full sector is stored in the image."""
        transfer = self._transfer
        assert transfer is not None and transfer.sector is not None
        transfer.buffer.append(value)
        size = len(transfer.sector.read()) if transfer.id_field[3] else transfer.data_length
        if len(transfer.buffer) < size:
            return
        sector = transfer.sector
        sector.data[:size] = transfer.buffer
        sector.status2 = ST2_CONTROL_MARK if transfer.code == 0x09 else 0
        drive = self._drive(transfer.unit_and_head)
        assert drive.image is not None
        drive.image.modified = True
        self._advance_or_end(transfer)
        if self._transfer is not None:
            self._next_write_sector()

    # -- format ----------------------------------------------------------------------------------

    def _start_format(self, command: list[int]) -> None:
        """FORMAT TRACK: receive C, H, R, N for each sector, then rewrite the track."""
        unit_and_head = command[1]
        drive = self._drive(unit_and_head)
        if drive.image is None:
            self._not_ready(unit_and_head, [0, 0, 0, command[2]])
            return
        if not drive.image.writable:
            status_0 = self._status_0(unit_and_head, ST0_ABNORMAL)
            self._finish([status_0, ST1_NOT_WRITABLE, 0, 0, 0, 0, command[2]])
            return
        self._format = {"unit_and_head": unit_and_head, "size_code": command[2],
                        "sectors": command[3], "gap3": command[4], "filler": command[5],
                        "ids": [], "bytes": []}  # fmt: skip
        if command[3] == 0:
            self._finish_format()

    def _format_byte(self, value: int) -> None:
        """Collect one byte of the ID list; after the last sector, format the track."""
        state = self._format
        assert state is not None
        state["bytes"].append(value)
        if len(state["bytes"]) == 4:
            state["ids"].append(tuple(state["bytes"]))
            state["bytes"] = []
            if len(state["ids"]) == state["sectors"]:
                self._finish_format()

    def _finish_format(self) -> None:
        """Rewrite the track with the received IDs."""
        state = self._format
        assert state is not None
        unit_and_head = state["unit_and_head"]
        drive = self._drive(unit_and_head)
        assert drive.image is not None
        head = drive.physical_head(unit_and_head >> 2 & 1)
        drive.image.format_track(drive.cylinder, head, state["ids"], state["filler"],
                                 state["gap3"])  # fmt: skip
        last = state["ids"][-1] if state["ids"] else (0, 0, 0, state["size_code"])
        self._finish([self._status_0(unit_and_head, 0), 0, 0, *last])

    # -- device view -----------------------------------------------------------------------------

    def drive_lights(self) -> list[bool]:
        """Activity light per drive (A:, B:): motor on and the drive addressed last."""
        return [self.motor_on and self.selected_unit == unit for unit in range(len(self.drives))]

    def state(self) -> dict[str, Any]:
        """Return the state for the device view (FR-DBG-05)."""
        drives = {}
        for drive in self.drives:
            image = drive.image
            drives[drive.name] = {
                "disk": str(image.source)
                if image and image.source
                else ("draft" if image else None),
                "writable": bool(image and image.writable),
                "modified": bool(image and image.modified),
                "cylinder": drive.cylinder,
                "cylinders": drive.cylinders,
            }
        msr = self.main_status()
        return {
            "motor_on": self.motor_on,
            "selected_drive": self.drives[self.selected_unit].name,
            "msr": msr,
            "msr_meaning": msr_meaning(msr),
            "drives": drives,
            "recent_commands": [
                f"{name} {bytes(command).hex(' ')} -> {bytes(results).hex(' ')}"
                + (f"  (x{count})" if count > 1 else "")
                for name, command, results, count in self.log
            ],
        }


def msr_meaning(msr: int) -> str:
    """The main status register in words (RQM, DIO, EXM, CB; bits 3-0: drives seeking)."""
    if msr == MSR_RQM:
        return "ready for a command · not busy"
    parts = ["ready" if msr & MSR_RQM else "not ready"]
    if msr & MSR_RQM:
        parts.append("data to the CPU" if msr & MSR_DIO else "data from the CPU")
    if msr & MSR_EXM:
        parts.append("execution phase")
    parts.append("busy" if msr & MSR_CB else "not busy")
    seeking = [str(unit) for unit in range(4) if msr >> unit & 1]
    if seeking:
        parts.append(f"seeking {','.join(seeking)}")
    return " · ".join(parts)
