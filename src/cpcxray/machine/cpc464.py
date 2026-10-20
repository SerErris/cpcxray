"""The CPC 464 machine: wiring, power-on, reset and the scheduler (design.md §6.10).

Power-on (constructor): create memory (RAM cleared), devices, bus and CPU; install the ROMs
from the configuration; then reset.

Reset (researched for phase 4, see devices/gate_array.py): the devices are reset first - Gate
Array (mode 1, lower ROM enabled, upper ROM disabled, interrupt timer restarted), ROM select
latch, CRTC, SystemIO, keyboard - and the Z80 last (PC = &0000, interrupts disabled, IM 0). The
first instruction is therefore fetched from the OS ROM at &0000; the 464 OS starts with
`ld bc,#7F89 / out (c),c / jp #0580`, confirming the same ROM state. RAM keeps its contents.

Scheduler (D-04, D-05): `step()` executes one instruction, advances the clock by its cost in
µs and lets the Gate Array latch the 300 Hz interrupt; the INT line follows the Gate Array's
request before every instruction. `run(budget_us)` repeats that for a time slice.

ROM slots with a decoder (the scrambled VDOS) get the decode rule and a shadow ROM (phase 7,
D-11, D-17). Configured ROMs whose file is missing are listed in `skipped_roms`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from cpcxray.analysis.cpc_profile import CpcProfile
from cpcxray.cpu.disasm import DisassemblyProfile
from cpcxray.cpu.z80 import Z80
from cpcxray.debugger.symbols import GLOBAL
from cpcxray.decoding.shadow_rom import ShadowRom
from cpcxray.decoding.vortex import DECODERS
from cpcxray.devices.crtc import Crtc
from cpcxray.devices.fdc765 import FdcStub
from cpcxray.devices.gate_array import GateArray
from cpcxray.devices.key_map import KeyMap, KeyMapError
from cpcxray.devices.keyboard import Keyboard
from cpcxray.devices.rom_select import RomSelect
from cpcxray.devices.system_io import DEFAULT_PORT_B, SystemIO
from cpcxray.machine.bus import CpcBus
from cpcxray.machine.config import ConfigError, MachineConfig, RomConfig, read_rom_image
from cpcxray.machine.memory import MemoryMap, RomSlot
from cpcxray.ui.renderer import ScreenSnapshot

type SlotName = str | int  # "lower" or an upper slot number 0-15


class CPC464:  # pylint: disable=too-many-instance-attributes  # the machine's components
    """An Amstrad CPC 464 (implements the `Machine` protocol used by `Session`).

    Attributes:
        clock_us: Emulated time since reset in µs.
        memory: RAM, ROM slots and paging.
        keyboard: The key matrix.
        gate_array: Mode, inks, ROM enables, interrupts.
        crtc: The CRTC register file.
        rom_select: The upper ROM select latch.
        system_io: PPI/PSG replacement (keyboard port, VSYNC).
        fdc: µPD765 stub with no drive ready (full controller in phase 10).
        bus: Connects CPU, memory and devices.
        cpu: The Z80.
        skipped_roms: Configured ROMs that were not installed, with the reason.
    """

    model = "cpc464"

    def __init__(
        self, config: MachineConfig | None = None, port_b_constant: int = DEFAULT_PORT_B
    ) -> None:
        """Power on: build the machine, install the configured ROMs, reset.

        Args:
            config: ROM configuration; None leaves all slots empty.
            port_b_constant: PPI port B bits 7-1 (manufacturer, 50 Hz, ...).
        """
        self.clock_us = 0
        self.memory = MemoryMap(config.unpopulated_rom if config else 0)
        self.keyboard = Keyboard()
        self.gate_array = GateArray(self.memory.set_rom_enables, self._clock)
        self.crtc = Crtc()
        self.rom_select = RomSelect(self.memory.select_upper)
        self.system_io = SystemIO(self.keyboard, self._clock, port_b_constant)
        self.fdc = FdcStub()
        self.bus = CpcBus(
            self.memory, [self.gate_array, self.crtc, self.rom_select, self.system_io, self.fdc],
            self.gate_array,
        )  # fmt: skip
        self.cpu = Z80(self.bus)
        self.skipped_roms: list[str] = []
        self.config = config
        self._profile = CpcProfile()
        self._shadows: list[ShadowRom] = []  # of installed scrambled ROMs, updated on changes
        self._key_map: KeyMap | None = None
        self._key_map_source: RomSlot | None = None
        if config is not None:
            self.apply_config(config)
        self.reset()

    def _clock(self) -> int:
        """Return the clock (for devices that need the time)."""
        return self.clock_us

    # -- ROMs ------------------------------------------------------------------------------------

    def apply_config(self, config: MachineConfig) -> None:
        """Install the ROMs of a configuration (missing files and decoder slots are skipped)."""
        self.memory.unpopulated_rom = config.unpopulated_rom
        self.skipped_roms = []
        for rom in config.roms():
            self._install_configured(rom)

    def _install_configured(self, rom: RomConfig) -> None:
        """Install one configured ROM (with its decoder), or record why it is skipped."""
        if not rom.exists:
            self.skipped_roms.append(f"{rom.label}: file missing ({rom.file})")
            return
        self.load_rom(rom.slot, rom.file, rom.decoder, rom.decode)

    def load_rom(
        self,
        slot: SlotName,
        path: str | Path,
        decoder: str | None = None,
        decode: bool = True,
    ) -> RomSlot:
        """Load a 16 KB ROM image into "lower" or an upper slot 0-15 (no reset).

        Args:
            slot: "lower" or 0-15.
            path: The image file.
            decoder: Decoder name for a scrambled ROM (e.g. "vortex"), or None.
            decode: Initial state of the decode switch (D-11).

        Raises:
            ConfigError: If the file cannot be read or is not 16 KB.
            ValueError: For an invalid slot or an unknown decoder.
        """
        if decoder is not None and decoder not in DECODERS:
            raise ValueError(f"unknown decoder {decoder!r}")
        decode_function = DECODERS[decoder] if decoder else None
        rom = RomSlot(read_rom_image(path), Path(path).name, decode_function, decode)
        if slot == "lower":
            self.memory.set_lower_rom(rom)
        else:
            self.memory.set_upper_rom(int(slot), rom)
        self._shadows = [shadow for _, shadow in self.memory.shadows()]
        return rom

    def unload_rom(self, slot: SlotName) -> None:
        """Empty the lower ROM or an upper slot (no reset)."""
        if slot == "lower":
            self.memory.set_lower_rom(None)
        else:
            self.memory.set_upper_rom(int(slot), None)
        self._shadows = [shadow for _, shadow in self.memory.shadows()]

    def rom_list(self) -> list[dict[str, Any]]:
        """Return the installed ROMs: slot, file name and ROM header type/version."""
        entries = []
        if self.memory.lower_rom is not None:
            entries.append({"slot": "lower", "name": self.memory.lower_rom.name})
        for number in sorted(self.memory.upper_roms):
            rom = self.memory.upper_roms[number]
            entries.append({"slot": number, "name": rom.name, **_rom_header(rom)})
        return entries

    # -- execution -------------------------------------------------------------------------------

    def reset(self) -> None:
        """Reset: devices first (lower ROM on, upper ROM off), then the Z80 (PC = 0)."""
        self.clock_us = 0
        self.gate_array.reset()
        self.rom_select.reset()
        self.crtc.reset()
        self.system_io.reset()
        self.fdc.reset()
        self.keyboard.reset()
        self.cpu.reset()
        self.cpu.int_line = False

    def step(self) -> int:
        """Execute one instruction (or accept an interrupt); return its cost in µs."""
        cpu = self.cpu
        cpu.int_line = self.gate_array.interrupt_pending
        if self.bus.observed:
            self.bus.instruction_pc = cpu.pc
        for shadow in self._shadows:
            shadow.instruction_pc = cpu.pc
        cost = cpu.step()
        self.clock_us += cost
        self.gate_array.tick(self.clock_us)
        if self.keyboard.typing:
            self.keyboard.tick(self.clock_us)
        return cost

    def run(self, budget_us: int) -> int:
        """Run for at least `budget_us` µs of emulated time; return the time used."""
        used = 0
        while used < budget_us:
            used += self.step()
        return used

    # -- Machine protocol (for Session) ----------------------------------------------------------

    def peek(self, address: int) -> int:
        """Read a byte as the CPU's data reads see it, without recording or observers."""
        return self.memory.peek(address & 0xFFFF, as_m1=False)

    def peek_opcode(self, address: int) -> int:
        """Read a byte as an M1 fetch would see it (raw in a scrambled ROM), no side effects."""
        return self.memory.peek(address & 0xFFFF, as_m1=True)

    def poke(self, address: int, value: int) -> None:
        """Write a byte to RAM."""
        self.memory.write(address & 0xFFFF, value & 0xFF)

    def disassembly_profile(self) -> DisassemblyProfile | None:
        """The CPC RST profile (D-30)."""
        return self._profile

    def symbol_scope(self, address: int) -> str:
        """Scope of the ROM paged in at `address` ("lower", "romN"), else global (RAM)."""
        return self.memory.visible_rom(address & 0xFFFF) or GLOBAL

    def status(self) -> dict[str, Any]:
        """Return model, ROM paging and the ROMs not installed."""
        memory = self.memory
        return {
            "model": self.model,
            "lower_rom_enabled": memory.lower_enabled,
            "upper_rom_enabled": memory.upper_enabled,
            "selected_rom": memory.selected_upper,
            "answering_rom": memory.effective_upper_slot(),
            "screen_mode": self.gate_array.mode,
            "skipped_roms": list(self.skipped_roms),
        }

    def key_map(self) -> KeyMap:
        """Return the key names and characters, read from the installed OS ROM.

        Raises:
            KeyMapError: If no OS ROM is installed or it has no keyboard tables.
        """
        if self.memory.lower_rom is None:
            raise KeyMapError("no OS ROM installed: the keyboard tables come from it")
        if self._key_map is None or self._key_map_source is not self.memory.lower_rom:
            self._key_map = KeyMap.from_rom(self.memory.lower_rom.data)
            self._key_map_source = self.memory.lower_rom
        return self._key_map

    def snapshot(self) -> ScreenSnapshot:
        """Copy RAM, mode, inks and CRTC registers for the renderer (D-13, §6.10)."""
        return ScreenSnapshot(
            ram=bytes(self.memory.ram),
            mode=self.gate_array.mode,
            inks=tuple(self.gate_array.inks),
            crtc=tuple(self.crtc.registers),
        )

    def devices(self) -> dict[str, Any]:
        """Return the state of every device (FR-DBG-05)."""
        return {
            "gate_array": self.gate_array.state(),
            "crtc": self.crtc.state(),
            "rom_select": self.rom_select.state(),
            "system_io": self.system_io.state(),
            "fdc": self.fdc.state(),
            "keyboard_rows": self.keyboard.rows.hex(" ").upper(),
            "keys_held": self.keyboard.held_keys(),
            "typing": self.keyboard.typing,
        }


def build_cpc464(config_path: str | Path) -> CPC464:
    """Load a machine.toml and power on a CPC 464 with its ROMs.

    Raises:
        ConfigError: If the configuration is invalid.
    """
    from cpcxray.machine.config import load_config  # pylint: disable=import-outside-toplevel

    return CPC464(load_config(config_path))


def _rom_header(rom: RomSlot) -> dict[str, Any]:
    """Decode the upper ROM header: type (0 foreground, 1 background, 2 extension), version."""
    rom_type, mark, version, modification = rom.data[0:4]
    names = {0: "foreground", 1: "background", 2: "extension", 0x80: "built-in"}
    return {
        "type": names.get(rom_type, f"&{rom_type:02X}"),
        "version": f"{mark}.{version}{modification}",
    }


__all__ = ["CPC464", "ConfigError", "build_cpc464"]
