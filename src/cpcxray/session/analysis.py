"""Session methods for scrambled ROMs and analysis targets (design.md §6.5, §6.6, §6.16; phase 7).

A mixin of `Session`: every public method here is an MCP tool as well (D-27). Groups:

* Scrambled ROMs: decode switch, shadow status, conflicts, access map files and ranges, decoding
  from a map without a run, clear dump, ROM map, coverage CSV.
* Analysis targets: add/remove/list, RAM-target capture, relocated blocks.

Slots are given as "romN" / N / "lower"; when omitted, the only scrambled ROM is used. Default
file names derive from the ROM file: project/<rom>.m1map for the access map (loaded and merged
when the session starts), output/<rom>_clear.rom, output/<rom>_rom_map.html,
output/<rom>_coverage.csv.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cpcxray.analysis.provenance import AnalysisObserver, RelocatedBlock, capture
from cpcxray.analysis.targets import AnalysisTarget, ram_target, rom_target
from cpcxray.decoding import export
from cpcxray.decoding.m1map import (
    AccessMapError,
    access_ranges,
    decode_rom,
    load_m1map,
    ranges_json,
    ranges_text,
    save_m1map,
)
from cpcxray.decoding.shadow_rom import ShadowRom
from cpcxray.decoding.vortex import vortex_decode
from cpcxray.machine.memory import RomSlot
from cpcxray.session.errors import SessionError

if TYPE_CHECKING:
    from cpcxray.machine.cpc464 import CPC464


class AnalysisMixin:
    """Scrambled-ROM and analysis-target methods of `Session` (see the module docstring)."""

    analysis_targets: dict[str, AnalysisTarget]
    manual_relocations: list[RelocatedBlock]
    _analysis_observer: AnalysisObserver | None

    # -- helpers (provided by Session) -----------------------------------------------------------

    def _cpc(self) -> CPC464:
        """Return the machine as a CPC 464 (implemented by Session)."""
        raise NotImplementedError

    def _path(self, path: str) -> Path:
        """Resolve a project-relative path (implemented by Session)."""
        raise NotImplementedError

    def _address(self, value: int | str) -> int:
        """Parse an address or label (implemented by Session)."""
        raise NotImplementedError

    def _setup_analysis(self) -> None:
        """Create targets from the machine configuration and load saved access maps."""
        self.analysis_targets = {}
        self.manual_relocations = []
        self._analysis_observer = None
        config = getattr(self.machine, "config", None)  # type: ignore[attr-defined]
        if config is None:
            return
        for rom in config.analysis_targets():
            name = "lower" if rom.slot == "lower" else f"rom{rom.slot}"
            self.analysis_targets[name] = rom_target(name)
        for ram in config.ram_targets:
            target = ram_target(ram.name, ram.start, ram.length, ram.capture)
            self.analysis_targets[target.name] = target
        for slot_name, _ in self._cpc().memory.shadows():
            path = self._default_path(slot_name, "m1map")
            if path.exists():
                self._shadow(slot_name).merge_access_map(load_m1map(path))
        self._sync_observer()

    def _sync_observer(self) -> None:
        """Register the analysis observer while targets exist, remove it otherwise (D-12)."""
        machine = self._cpc()
        if self.analysis_targets and self._analysis_observer is None:
            self._analysis_observer = AnalysisObserver(machine.memory, self.analysis_targets)
            machine.bus.add_observer(self._analysis_observer)
        elif not self.analysis_targets and self._analysis_observer is not None:
            machine.bus.remove_observer(self._analysis_observer)
            self._analysis_observer = None

    def _slot_name(self, slot: str | int | None) -> str:
        """Normalise a slot ("rom5", 5, "5", "lower"); None = the only scrambled ROM."""
        if slot is None:
            scrambled = [name for name, _ in self._cpc().memory.shadows()]
            if len(scrambled) != 1:
                raise self._error(f"give the slot: scrambled ROMs installed: {scrambled or 'none'}")
            return scrambled[0]
        text = str(slot)
        if text == "lower" or text.startswith("rom"):
            return text
        return f"rom{int(text)}"

    def _rom_slot(self, slot_name: str) -> RomSlot:
        """Return the installed ROM of a slot name."""
        memory = self._cpc().memory
        rom = (
            memory.lower_rom if slot_name == "lower" else memory.upper_roms.get(int(slot_name[3:]))
        )
        if rom is None:
            raise self._error(f"no ROM in slot {slot_name}")
        return rom

    def _shadow(self, slot_name: str) -> ShadowRom:
        """Return the shadow of a scrambled slot."""
        rom = self._rom_slot(slot_name)
        if rom.shadow is None:
            raise self._error(f"slot {slot_name} is not a scrambled ROM (no decoder)")
        return rom.shadow

    def _default_path(self, slot_name: str, kind: str) -> Path:
        """Default file for a slot: project/<rom>.m1map or output/<rom>_<kind>."""
        stem = Path(self._rom_slot(slot_name).name).stem or slot_name
        if kind == "m1map":
            return self._path(f"project/{stem}.m1map")
        return self._path(f"output/{stem}_{kind}")

    @staticmethod
    def _error(message: str) -> SessionError:
        """Create a SessionError for a request that cannot be carried out."""
        return SessionError(message)

    # == scrambled ROMs ==========================================================================

    def rom_set_decode(self, slot: str | int, enabled: bool) -> dict[str, Any]:
        """Switch decoding of a scrambled ROM on or off, effective from the next read (D-11)."""
        name = self._slot_name(slot)
        rom = self._rom_slot(name)
        if rom.decoder is None:
            raise self._error(f"slot {name} has no decoder")
        rom.decode_enabled = enabled
        return {"slot": name, "decode_enabled": enabled}

    def shadow_status(self, slot: str | int | None = None) -> dict[str, Any]:
        """Return the access classes of a scrambled ROM (unknown / M1 / data / conflict)."""
        name = self._slot_name(slot)
        shadow = self._shadow(name)
        rom = self._rom_slot(name)
        undecoded = sum(1 for count in shadow.undecoded_reads if count)
        return {"slot": name, "rom": rom.name, "decode_enabled": rom.decode_enabled,
                "classes": shadow.counts(), "bytes_read_with_decoding_off": undecoded}  # fmt: skip

    def shadow_conflicts(self, slot: str | int | None = None, limit: int = 200) -> dict[str, Any]:
        """List bytes read both with M1 and as data (D-18); "real" ones need the owner's choice.

        A conflict is harmless where the decode mask is zero (both readings give the same value).
        """
        name = self._slot_name(slot)
        base = 0x0000 if name == "lower" else 0xC000
        conflicts = self._shadow(name).conflicts()
        entries = [
            {"address": f"{base + c.offset:04X}", "raw": f"{c.raw:02X}",
             "as_m1": f"{c.as_m1:02X}", "as_data": f"{c.as_data:02X}",
             "real": not c.harmless,
             "first_pc": None if c.first_pc is None else f"{c.first_pc:04X}"}
            for c in conflicts[:limit]
        ]  # fmt: skip
        real = sum(1 for c in conflicts if not c.harmless)
        return {"slot": name, "total": len(conflicts), "real": real, "conflicts": entries}

    def m1map_save(self, slot: str | int | None = None, path: str | None = None) -> dict[str, Any]:
        """Save the access map of a scrambled ROM (default project/<rom>.m1map, D-19)."""
        name = self._slot_name(slot)
        file = self._path(path) if path else self._default_path(name, "m1map")
        save_m1map(file, self._shadow(name).access_map)
        return {"slot": name, "path": str(file)}

    def m1map_load(self, slot: str | int | None = None, path: str | None = None) -> dict[str, Any]:
        """Merge a saved access map into the current one (unknown + X = X, M1 + data = conflict)."""
        name = self._slot_name(slot)
        file = self._path(path) if path else self._default_path(name, "m1map")
        try:
            self._shadow(name).merge_access_map(load_m1map(file))
        except (OSError, AccessMapError) as error:
            raise self._error(str(error)) from None
        return {"slot": name, "path": str(file), "classes": self._shadow(name).counts()}

    def m1map_ranges(
        self, slot: str | int | None = None, path: str | None = None, json_format: bool = False
    ) -> dict[str, Any]:
        """Export the access map as ranges ("C000-C003  data  4"); with `path` to a file."""
        name = self._slot_name(slot)
        base = 0x0000 if name == "lower" else 0xC000
        ranges = access_ranges(bytes(self._shadow(name).access_map))
        text = ranges_json(ranges, base) if json_format else ranges_text(ranges, base)
        if path:
            file = self._path(path)
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf-8")
            return {"slot": name, "ranges": len(ranges), "path": str(file)}
        if len(ranges) > 500:
            raise self._error(f"{len(ranges)} ranges: give a path to write them to a file")
        return {"slot": name, "ranges": len(ranges), "text": text}

    def rom_decode_from_map(
        self, raw_path: str, map_path: str, out_path: str, fill_policy: str = "raw",
        conflict: str = "m1",
    ) -> dict[str, Any]:  # fmt: skip
        """Decode a scrambled Vortex ROM file with a .m1map file, without running (FR-DEC-04).

        Args:
            raw_path: The scrambled ROM image.
            map_path: The access map.
            out_path: Where to write the clear ROM.
            fill_policy: Never-read bytes: "raw", "data" or "ff" ("static" in phase 8).
            conflict: Value for conflict bytes: "m1" (raw) or "data" (decoded).
        """
        try:
            clear = decode_rom(self._path(raw_path).read_bytes(), load_m1map(self._path(map_path)),
                               vortex_decode, fill_policy, conflict)  # fmt: skip
        except (OSError, AccessMapError) as error:
            raise self._error(str(error)) from None
        out = self._path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(clear)
        return {"path": str(out), "length": len(clear)}

    def rom_dump(
        self, slot: str | int | None = None, path: str | None = None, fill_policy: str = "raw",
        conflict: str = "m1",
    ) -> dict[str, Any]:  # fmt: skip
        """Write the clear ROM image for a plain replacement ROM (FR-DEC-05, D-20).

        Args:
            slot: The scrambled slot (default: the only one).
            path: Output file (default output/<rom>_clear.rom).
            fill_policy: Never-read bytes: "raw", "data" or "ff" ("static" in phase 8).
            conflict: Value for conflict bytes: "m1" (raw) or "data" (decoded).
        """
        name = self._slot_name(slot)
        file = self._path(path) if path else self._default_path(name, "clear.rom")
        try:
            export.rom_dump(file, self._shadow(name), fill_policy, conflict)
        except AccessMapError as error:
            raise self._error(str(error)) from None
        return {"slot": name, "path": str(file), "fill_policy": fill_policy,
                "classes": self._shadow(name).counts()}  # fmt: skip

    def rom_map(self, slot: str | int | None = None, path: str | None = None) -> dict[str, Any]:
        """Write the ROM map: HTML page (colour per byte) and text summary (FR-DEC-06)."""
        name = self._slot_name(slot)
        file = self._path(path) if path else self._default_path(name, "rom_map.html")
        target = self.analysis_targets.get(name)
        flags = bytes(target.flags) if target is not None else None
        title = f"{self._rom_slot(name).name} (slot {name})"
        base = 0x0000 if name == "lower" else 0xC000
        return export.rom_map(file, self._shadow(name), title, flags, base)

    def coverage_csv(
        self, slot: str | int | None = None, path: str | None = None
    ) -> dict[str, Any]:
        """Write one CSV row per ROM byte: class, raw and clear value, hits, first PC."""
        name = self._slot_name(slot)
        file = self._path(path) if path else self._default_path(name, "coverage.csv")
        target = self.analysis_targets.get(name)
        flags = bytes(target.flags) if target is not None else None
        base = 0x0000 if name == "lower" else 0xC000
        rows = export.coverage_csv(file, self._shadow(name), flags, base)
        return {"slot": name, "path": str(file), "rows": rows}

    # == analysis targets ========================================================================

    def analysis_target_add(
        self, target: str, start: int | str | None = None, length: int | None = None,
        capture_at: str = "first_m1",
    ) -> dict[str, Any]:  # fmt: skip
        """Add an analysis target (D-16): a ROM slot ("rom5", "lower") or a RAM range.

        Args:
            target: "romN" / "lower", or a name for a RAM range (with start and length).
            start: RAM range start.
            length: RAM range length.
            capture_at: RAM range: "first_m1" (capture at the first fetch inside) or "manual".
        """
        if start is None:
            name = self._slot_name(target)
            new = rom_target(name)
        else:
            if length is None or length < 1 or capture_at not in ("first_m1", "manual"):
                raise self._error("a RAM target needs length >= 1 and capture 'first_m1'/'manual'")
            new = ram_target(target, self._address(start), length, capture_at)
        self.analysis_targets[new.name] = new
        self._sync_observer()
        return self.analysis_target_list()

    def analysis_target_remove(self, target: str) -> dict[str, Any]:
        """Remove an analysis target by name ("rom5", "lower", "ram:<name>")."""
        if self.analysis_targets.pop(target, None) is None:
            raise self._error(f"no analysis target {target!r}")
        self._sync_observer()
        return self.analysis_target_list()

    def analysis_target_list(self) -> dict[str, Any]:
        """List the analysis targets with their flag counts and RAM-capture state."""
        targets = []
        for target in self.analysis_targets.values():
            entry: dict[str, Any] = {"name": target.name, "kind": target.kind,
                                     "start": f"{target.start:04X}", "length": target.length,
                                     "flags": target.flag_counts()}  # fmt: skip
            if target.kind == "ram":
                entry |= {"capture": target.capture, "captured": target.image is not None,
                          "modified_after_capture": len(target.modifications)}  # fmt: skip
            targets.append(entry)
        return {"targets": targets}

    def analysis_capture(self, target: str) -> dict[str, Any]:
        """Capture a RAM target's image now; later writes into it are recorded (D-23)."""
        found = self.analysis_targets.get(target)
        if found is None or found.kind != "ram":
            raise self._error(f"no RAM analysis target {target!r}")
        capture(found, self._cpc().memory)
        return {"target": target, "captured": found.length}

    def reloc_list(self, target: str | None = None) -> dict[str, Any]:
        """List relocated blocks: ROM code copied to RAM (detected, plus manual ones, D-22).

        For a ROM range copied to several places, `preferred` marks the copy with the most
        executed instructions (used for disassembly).
        """
        detected = self._analysis_observer.relocated_blocks() if self._analysis_observer else []
        blocks = [b for b in detected + self.manual_relocations if target in (None, b.target)]
        best: dict[tuple[str, int], RelocatedBlock] = {}
        for block in blocks:
            key = (block.target, block.rom_start)
            if key not in best or block.executed > best[key].executed:
                best[key] = block
        entries = [
            {**asdict(b), "rom_start": f"{b.rom_start:04X}", "ram_start": f"{b.ram_start:04X}",
             "preferred": best[(b.target, b.rom_start)] is b}
            for b in blocks
        ]  # fmt: skip
        return {"blocks": entries}

    def reloc_add(
        self, target: str, rom_start: int | str, length: int, ram_start: int | str
    ) -> dict[str, Any]:
        """Add a relocated block by hand (owner's correction of the detection, D-22)."""
        block = RelocatedBlock(target, self._address(rom_start), length, self._address(ram_start),
                               manual=True)  # fmt: skip
        self.manual_relocations.append(block)
        return self.reloc_list(target)

    def reloc_remove(self, target: str, ram_start: int | str) -> dict[str, Any]:
        """Remove a manually added relocated block (by target and RAM start)."""
        address = self._address(ram_start)
        before = len(self.manual_relocations)
        self.manual_relocations = [
            b for b in self.manual_relocations if (b.target, b.ram_start) != (target, address)
        ]
        if len(self.manual_relocations) == before:
            raise self._error(f"no manual block of {target} at {address:04X}")
        return self.reloc_list(target)
