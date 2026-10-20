"""Tests of the CPC 464 machine: reset sequence, scheduler, bus observers, screen text."""

from __future__ import annotations

from pathlib import Path

import pytest

from cpcxray.cpu.bus import Cycle
from cpcxray.devices.gate_array import INTERRUPT_PERIOD_US
from cpcxray.machine.config import MachineConfig, RomConfig
from cpcxray.machine.cpc464 import CPC464
from cpcxray.machine.memory import RomSlot
from cpcxray.machine.screen import screen_text


def os_rom_with(code: bytes, at: int = 0) -> RomSlot:
    """A synthetic lower ROM: `code` at `at`, the rest NOP."""
    data = bytearray(0x4000)
    data[at : at + len(code)] = code
    return RomSlot(bytes(data), "test-os")


def test_reset_sequence_starts_in_lower_rom() -> None:
    """Reset: devices first (lower ROM on, upper off), Z80 last; first fetch from &0000."""
    machine = CPC464()
    machine.memory.set_lower_rom(os_rom_with(bytes.fromhex("01897F ED49 C30005")))
    machine.memory.ram[0x0000] = 0x76  # RAM under the ROM holds HALT: must not be executed
    machine.cpu.pc = 0x1234
    machine.reset()
    assert (machine.cpu.pc, machine.cpu.iff1, machine.cpu.im, machine.clock_us) == (0, False, 0, 0)
    assert (machine.memory.lower_enabled, machine.memory.upper_enabled) == (True, False)
    machine.step()
    machine.step()  # LD BC,&7F89 / OUT (C),C
    assert machine.gate_array.mode == 1
    machine.step()
    assert machine.cpu.pc == 0x0500


def test_interrupt_reaches_cpu_at_300_hz() -> None:
    """With IM 1 and EI, the CPU takes the Gate Array interrupt every 3,328 µs."""
    machine = CPC464()
    # &0000: IM 1 / EI / loop: JR loop; &0038: EI / RET (the handler counts nothing, we watch PC)
    code = bytearray(0x4000)
    code[0:5] = bytes.fromhex("ED56 FB 18FE")
    code[0x38:0x3A] = bytes.fromhex("FB C9")
    machine.memory.set_lower_rom(RomSlot(bytes(code), "test-os"))
    machine.reset()
    machine.cpu.sp = 0x8000
    entries = []
    while machine.clock_us < 4 * INTERRUPT_PERIOD_US:
        pc_before = machine.cpu.pc
        machine.step()
        if machine.cpu.pc == 0x38 and pc_before != 0x38:
            entries.append(machine.clock_us)
    assert len(entries) == 3  # at 3328, 6656, 9984 (+ the acceptance cost)
    assert all(0 <= entry - n * INTERRUPT_PERIOD_US < 10 for n, entry in enumerate(entries, 1))


def test_bus_observers_swap_methods() -> None:
    """Observers see every access with the instruction's PC; removing them restores speed."""
    machine = CPC464()
    machine.memory.set_lower_rom(os_rom_with(bytes.fromhex("3A0080")))  # LD A,(&8000)
    machine.reset()
    seen: list[tuple[Cycle, int, int, int]] = []

    class Recorder:  # pylint: disable=too-few-public-methods  # the observer protocol has one method
        """Collects the bus cycles."""

        def on_cycle(self, kind: Cycle, address: int, value: int, pc: int) -> None:
            """Record one access."""
            seen.append((kind, address, value, pc))

    recorder = Recorder()
    fast_read = machine.bus.read
    machine.bus.add_observer(recorder)
    machine.step()
    assert [entry[0] for entry in seen] == [Cycle.M1, Cycle.MEM_RD, Cycle.MEM_RD, Cycle.MEM_RD]
    assert {entry[3] for entry in seen} == {0x0000}
    machine.bus.remove_observer(recorder)
    assert machine.bus.read == fast_read


def test_decoder_slots_get_decoder_and_shadow(tmp_path: Path) -> None:
    """A slot with a decoder is installed scrambled, with a shadow ROM (phase 7, D-17)."""
    rom = tmp_path / "vdos.rom"
    rom.write_bytes(bytes(0x4000))
    config = MachineConfig(
        project_dir=tmp_path,
        upper_roms={5: RomConfig(5, rom, decoder="vortex", analyse=True)},
    )
    machine = CPC464(config)
    slot = machine.memory.upper_roms[5]
    assert slot.scrambled and slot.decode_enabled and slot.shadow is not None
    assert not machine.skipped_roms


@pytest.mark.parametrize("mode", [0, 1, 2])
def test_screen_text_in_all_modes(mode: int) -> None:
    """Text written into screen RAM as glyph pixels is recognised in every mode."""
    font = bytearray(0x800)
    font[ord("A") * 8 : ord("A") * 8 + 8] = bytes([0x18, 0x24, 0x42, 0x7E, 0x42, 0x42, 0x42, 0])
    registers = [0] * 18
    registers[1], registers[6], registers[9], registers[12] = 40, 1, 7, 0x30
    memory = bytearray(0x10000)
    cell_bytes = {0: 4, 1: 2, 2: 1}[mode]
    pixels_per_byte = 8 // cell_bytes
    for line in range(8):
        glyph = font[ord("A") * 8 + line]
        for byte_index in range(cell_bytes):
            value = 0
            for pixel in range(pixels_per_byte):
                if glyph & (0x80 >> (byte_index * pixels_per_byte + pixel)):
                    value |= _pen1_bits(mode, pixel)
            memory[0xC000 + 2 * cell_bytes + line * 0x800 + byte_index] = value  # column 2
    lines = screen_text(lambda a: memory[a], bytes(font), registers, mode)
    assert lines == ["  A"]


def _pen1_bits(mode: int, pixel: int) -> int:
    """Byte bits that give `pixel` pen 1 in `mode` (§5.4 layout)."""
    return {2: 0x80 >> pixel, 1: 0x80 >> pixel, 0: 0x80 >> pixel}[mode]
