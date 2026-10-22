"""`.sna` snapshots of the CPC 464/664/6128 (design.md §6.10; FR-DBG-10, D-25).

The format is the de-facto standard of CPC emulators (CPCWiki "SNA snapshot file format"): a
256-byte header with CPU and device state, then the RAM. Version 3 may append chunks; "MEM0" ..
"MEM8" hold 64 KB RAM blocks, run-length compressed (`E5 count value`, `E5 00` = a literal &E5)
when the dump size in the header is 0.

Loading accepts versions 1, 2 and 3 (memory as plain dump, or MEM0/MEM1 chunks). The dump holds
the physical RAM blocks in order (block 0 first), not the CPU's banked view; a 6128 snapshot has
128 KB and its RAM configuration in the header (FR-MCH-10). A 464 ignores RAM beyond 64 KB; a
6128 loading a 64 KB snapshot gets blocks 4-7 cleared. Saving writes version 3 with a plain
dump - 64 KB and CPC type 0 (464) or 1 (664), or 128 KB and type 2 (6128) - with the Gate Array's
interrupt line counter and request flag, so the 300 Hz interrupt continues in phase.

Only machine state goes into the file (D-25): ROM images stay in the configuration, analysis
state (access maps, databases) in its own files.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from cpcxray.devices.gate_array import INTERRUPT_PERIOD_US, LINE_US

if TYPE_CHECKING:
    from cpcxray.machine.cpc464 import CPC464

SIGNATURE = b"MV - SNA"
HEADER_SIZE = 0x100
RAM_SIZE = 0x10000
BLOCK_SIZE = 0x4000
SAVE_VERSION = 3
CPC_TYPES = {"464": 0, "664": 1, "6128": 2}  # header byte &6D per machine

# Byte offsets of the 8-bit registers in the header, in file order
_BYTE_REGISTERS = {0x11: "f", 0x12: "a", 0x13: "c", 0x14: "b", 0x15: "e", 0x16: "d",
                   0x17: "l", 0x18: "h", 0x19: "r", 0x1A: "i"}  # fmt: skip
_WORD_REGISTERS = {0x1D: "ix", 0x1F: "iy", 0x21: "sp", 0x23: "pc"}
_ALTERNATE = {0x26: ("af_", 0), 0x27: ("af_", 8), 0x28: ("bc_", 0), 0x29: ("bc_", 8),
              0x2A: ("de_", 0), 0x2B: ("de_", 8), 0x2C: ("hl_", 0), 0x2D: ("hl_", 8)}  # fmt: skip
OFFSET_IFF1, OFFSET_IFF2, OFFSET_IM = 0x1B, 0x1C, 0x25
OFFSET_PEN, OFFSET_INKS, OFFSET_RMR, OFFSET_RAM_CONFIG = 0x2E, 0x2F, 0x40, 0x41
OFFSET_CRTC_SELECTED, OFFSET_CRTC = 0x42, 0x43
OFFSET_ROM_SELECT, OFFSET_PPI = 0x55, 0x56
OFFSET_PSG_SELECTED, OFFSET_PSG = 0x5A, 0x5B
OFFSET_DUMP_SIZE, OFFSET_CPC_TYPE = 0x6B, 0x6D
OFFSET_INTERRUPT_LINES, OFFSET_INTERRUPT_REQUEST = 0xB3, 0xB4


class SnapshotError(ValueError):
    """Not a usable .sna file."""


def save_sna(machine: CPC464, path: str | Path) -> int:
    """Write the machine state as a version 3 snapshot; return the file size."""
    header = bytearray(HEADER_SIZE)
    header[0:8] = SIGNATURE
    header[0x10] = SAVE_VERSION
    registers = machine.cpu.registers()
    for offset, name in _BYTE_REGISTERS.items():
        header[offset] = registers[name]
    for offset, name in _WORD_REGISTERS.items():
        header[offset : offset + 2] = registers[name].to_bytes(2, "little")
    for offset, (name, shift) in _ALTERNATE.items():
        header[offset] = registers[name] >> shift & 0xFF
    header[OFFSET_IFF1], header[OFFSET_IFF2] = registers["iff1"], registers["iff2"]
    header[OFFSET_IM] = registers["im"]
    _save_devices(machine, header)
    memory = machine.memory
    ram = b"".join(memory.block(number) for number in range(memory.ram_blocks))
    header[OFFSET_DUMP_SIZE : OFFSET_DUMP_SIZE + 2] = (len(ram) // 1024).to_bytes(2, "little")
    header[OFFSET_CPC_TYPE] = CPC_TYPES[machine.firmware_model]
    data = bytes(header) + ram
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)
    return len(data)


def _save_devices(machine: CPC464, header: bytearray) -> None:
    """Gate Array, CRTC, ROM select, PPI and PSG into the header."""
    gate_array = machine.gate_array
    header[OFFSET_PEN] = gate_array.pen
    header[OFFSET_INKS : OFFSET_INKS + 17] = bytes(ink & 0x1F for ink in gate_array.inks)
    header[OFFSET_RMR] = (
        0x80
        | gate_array.mode
        | (0 if gate_array.lower_rom_enabled else 0x04)
        | (0 if gate_array.upper_rom_enabled else 0x08)
    )
    header[OFFSET_RAM_CONFIG] = gate_array.ram_config
    header[OFFSET_CRTC_SELECTED] = machine.crtc.selected
    header[OFFSET_CRTC : OFFSET_CRTC + 18] = bytes(v & 0xFF for v in machine.crtc.registers[:18])
    header[OFFSET_ROM_SELECT] = machine.rom_select.selected
    system_io = machine.system_io
    port_b = system_io.io_in(0xF500) or 0
    header[OFFSET_PPI : OFFSET_PPI + 4] = bytes([system_io.port_a, port_b, system_io.port_c, 0x82])
    header[OFFSET_PSG_SELECTED] = system_io.psg_register
    header[OFFSET_PSG : OFFSET_PSG + 16] = bytes(system_io.psg_registers)
    header[OFFSET_INTERRUPT_LINES] = min(gate_array.line_counter, 51)
    header[OFFSET_INTERRUPT_REQUEST] = 1 if gate_array.interrupt_pending else 0


def load_sna(machine: CPC464, path: str | Path) -> dict[str, Any]:
    """Load a snapshot into the machine (ROMs stay as configured).

    Returns:
        Version, CPC type and PC of the loaded state.

    Raises:
        SnapshotError: If the file is not a .sna snapshot or has no 64 KB of RAM.
    """
    data = Path(path).read_bytes()
    if len(data) < HEADER_SIZE or data[0:8] != SIGNATURE:
        raise SnapshotError(f"{path}: not a .sna snapshot")
    version = data[0x10]
    ram = _ram_from_snapshot(data, version)
    memory = machine.memory
    machine.gate_array.write_ram_config(0)  # blocks in their home pages while loading
    for number in range(memory.ram_blocks):
        block = ram[number * BLOCK_SIZE : (number + 1) * BLOCK_SIZE]
        memory.set_block(number, block if len(block) == BLOCK_SIZE else bytes(BLOCK_SIZE))
    _load_cpu(machine, data)
    _load_devices(machine, data, version)
    return {"version": version, "cpc_type": data[OFFSET_CPC_TYPE] if version >= 2 else 0,
            "pc": machine.cpu.pc}  # fmt: skip


def _ram_from_snapshot(data: bytes, version: int) -> bytes:
    """Return the RAM blocks in order: 64 or 128 KB, from the plain dump or MEM0/MEM1 chunks."""
    dump_size = int.from_bytes(data[OFFSET_DUMP_SIZE : OFFSET_DUMP_SIZE + 2], "little") * 1024
    if dump_size >= RAM_SIZE and len(data) >= HEADER_SIZE + RAM_SIZE:
        dump = data[HEADER_SIZE : HEADER_SIZE + dump_size]
        return dump[: 2 * RAM_SIZE] if len(dump) >= 2 * RAM_SIZE else dump[:RAM_SIZE]
    chunks: dict[bytes, bytes] = {}
    if version >= 3:
        position = HEADER_SIZE + dump_size
        while position + 8 <= len(data):
            name = data[position : position + 4]
            length = int.from_bytes(data[position + 4 : position + 8], "little")
            content = data[position + 8 : position + 8 + length]
            if name in (b"MEM0", b"MEM1"):
                chunks[name] = content if length == RAM_SIZE else decompress_chunk(content)
            position += 8 + length
    if len(chunks.get(b"MEM0", b"")) < RAM_SIZE:
        raise SnapshotError("snapshot holds no 64 KB RAM dump")
    ram = chunks[b"MEM0"][:RAM_SIZE]
    extra = chunks.get(b"MEM1", b"")
    return ram + extra[:RAM_SIZE] if len(extra) >= RAM_SIZE else ram


def decompress_chunk(content: bytes) -> bytes:
    """Expand a version 3 memory chunk: E5 nn bb = nn times bb; E5 00 = one E5."""
    result = bytearray()
    index = 0
    while index < len(content):
        value = content[index]
        if value != 0xE5 or index + 1 >= len(content):
            result.append(value)
            index += 1
            continue
        count = content[index + 1]
        if count == 0:
            result.append(0xE5)
            index += 2
        else:
            result += bytes([content[index + 2]]) * count
            index += 3
    return bytes(result)


def _load_cpu(machine: CPC464, data: bytes) -> None:
    """Set the CPU registers from the header; the CPU is not halted afterwards."""
    values: dict[str, int] = {name: data[offset] for offset, name in _BYTE_REGISTERS.items()}
    for offset, name in _WORD_REGISTERS.items():
        values[name] = int.from_bytes(data[offset : offset + 2], "little")
    for offset, (name, shift) in _ALTERNATE.items():
        values[name] = values.get(name, 0) | data[offset] << shift
    interrupt_mode = data[OFFSET_IM] if data[OFFSET_IM] <= 2 else 1
    values |= {"iff1": data[OFFSET_IFF1] & 1, "iff2": data[OFFSET_IFF2] & 1,
               "im": interrupt_mode, "halted": 0, "ei_delay": 0}  # fmt: skip
    machine.cpu.set_registers(values)


def _load_devices(machine: CPC464, data: bytes, version: int) -> None:
    """Gate Array, CRTC, ROM select, PPI and PSG from the header."""
    gate_array = machine.gate_array
    gate_array.pen = data[OFFSET_PEN] if data[OFFSET_PEN] <= 16 else 0
    gate_array.inks = [value & 0x1F for value in data[OFFSET_INKS : OFFSET_INKS + 17]]
    gate_array.write_rmr(data[OFFSET_RMR] & 0x0F)  # mode and ROM enables
    gate_array.write_ram_config(data[OFFSET_RAM_CONFIG] & 0x3F)  # banking (6128), D-14
    machine.crtc.selected = data[OFFSET_CRTC_SELECTED] & 0x1F
    machine.crtc.registers[:18] = list(data[OFFSET_CRTC : OFFSET_CRTC + 18])
    machine.rom_select.io_out(0xDF00, data[OFFSET_ROM_SELECT])
    system_io = machine.system_io
    system_io.port_a = data[OFFSET_PPI]
    system_io.port_c = data[OFFSET_PPI + 2]
    system_io.psg_register = data[OFFSET_PSG_SELECTED] & 0x0F
    system_io.psg_registers = list(data[OFFSET_PSG : OFFSET_PSG + 16])
    lines_since = data[OFFSET_INTERRUPT_LINES] if version >= 3 else 0
    gate_array.interrupt_in_us = INTERRUPT_PERIOD_US - min(lines_since, 51) * LINE_US
    gate_array.interrupt_pending = version >= 3 and bool(data[OFFSET_INTERRUPT_REQUEST] & 1)
