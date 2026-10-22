"""Session methods for scrambled ROMs and analysis targets (design.md §6.5, §6.6, §6.16; phase 7).

A mixin of `Session`: every public method here is an MCP tool as well (D-27). Groups:

* Scrambled ROMs: decode switch, shadow status, conflicts, access map files and ranges, decoding
  from a map without a run, clear dump, ROM map, coverage CSV.
* Analysis targets: add/remove/list, RAM-target capture, relocated blocks; `analysis_enable`
  switches the analysis observers on and off (they cost about half the emulation speed).
* Analysis database and source (phase 8, D-31..D-33): save/load (merge), owner
  classification, source generation, round-trip check.

Slots are given as "romN" / N / "lower"; when omitted, the only scrambled ROM is used (for
the database tools: the only analysis target). Default file names derive from the ROM file:
project/<rom>.m1map for the access map and project/<rom>.analysis.json for the database (both
loaded and merged when the session starts), output/<rom>_clear.rom, output/<rom>_rom_map.html,
output/<rom>_coverage.csv, output/<rom>.asm.
"""

from __future__ import annotations

import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cpcxray.analysis.classifier import (
    TargetView,
    classify_target,
    executed_instructions,
    position_dependent,
)
from cpcxray.analysis.cpc_profile import CpcProfile
from cpcxray.analysis.database import Kind, TargetDatabase
from cpcxray.analysis.observers import ExecutionObserver
from cpcxray.analysis.provenance import AnalysisObserver, RelocatedBlock, capture
from cpcxray.analysis.source_gen import generate_source
from cpcxray.analysis.targets import AnalysisTarget, ram_target, rom_target
from cpcxray.analysis.verify import AssemblyError, verify_source
from cpcxray.debugger.symbols import SymbolTable
from cpcxray.decoding import export
from cpcxray.decoding.m1map import (
    FILL_POLICIES,
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
    from cpcxray.debugger.variables import Variable, VariableMap
    from cpcxray.machine.cpc464 import CPC464


# One public method per MCP tool (D-27); the state of targets, databases and observers.
class AnalysisMixin:  # pylint: disable=too-many-public-methods,too-many-instance-attributes
    """Scrambled-ROM and analysis-target methods of `Session` (see the module docstring)."""

    analysis_targets: dict[str, AnalysisTarget]
    analysis_enabled: bool  # observers on the bus (set by Session, kept across load_config)
    analysis_databases: dict[str, TargetDatabase]
    manual_relocations: list[RelocatedBlock]
    saved_relocations: list[RelocatedBlock]
    symbols: SymbolTable
    _analysis_observer: AnalysisObserver | None
    _execution_observer: ExecutionObserver | None
    _analysis_observing: bool
    _line_maps: dict[Path, list[int]]

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
        self._reset_analysis_state()
        config = getattr(self.machine, "config", None)  # type: ignore[attr-defined]
        if config is None:
            return
        for rom in config.analysis_targets():
            name = "lower" if rom.slot == "lower" else f"rom{rom.slot}"
            self._add_target(rom_target(name))
        for ram in config.ram_targets:
            self._add_target(ram_target(ram.name, ram.start, ram.length, ram.capture))
        for slot_name, _ in self._cpc().memory.shadows():
            path = self._default_path(slot_name, "m1map")
            if path.exists():
                self._shadow(slot_name).merge_access_map(load_m1map(path))
        self._sync_observer()

    def _reset_analysis_state(self) -> None:
        """Start without targets, databases, observers or relocations."""
        self.analysis_targets = {}
        self.analysis_databases = {}
        self.manual_relocations = []
        self.saved_relocations = []
        self._analysis_observer = None
        self._execution_observer = None
        self._analysis_observing = False
        self._line_maps = {}

    def _add_target(self, target: AnalysisTarget) -> None:
        """Add a target with its database; merge its saved database file if there is one."""
        self.analysis_targets[target.name] = target
        self.analysis_databases[target.name] = TargetDatabase(
            target.name, target.start, target.length
        )
        path = self._database_path(target.name)
        if path.exists():
            self._merge_database(target.name, path)

    def _sync_observer(self) -> None:
        """Keep the analysis observers on the bus while targets exist and analysis is on (D-12).

        Switched off, the observers leave the bus but keep what they collected (provenance
        tags, detected relocated blocks), so switching on again continues seamlessly. Without
        targets they are dropped.
        """
        machine = self._cpc()
        if self.analysis_targets and self._analysis_observer is None:
            self._analysis_observer = AnalysisObserver(machine.memory, self.analysis_targets)
            self._execution_observer = ExecutionObserver(
                machine.memory, self.analysis_targets, self.analysis_databases,
                self._analysis_observer,
            )  # fmt: skip
        wanted = bool(self.analysis_targets) and self.analysis_enabled
        observers = (self._analysis_observer, self._execution_observer)
        if wanted and not self._analysis_observing:
            for observer in observers:
                machine.bus.add_observer(observer)
        elif not wanted and self._analysis_observing:
            for observer in observers:
                machine.bus.remove_observer(observer)
        self._analysis_observing = wanted
        if not self.analysis_targets:
            self._analysis_observer = None
            self._execution_observer = None

    def analysis_enable(self, enabled: bool = True) -> dict[str, Any]:
        """Switch the analysis observers on or off (decoding and the access map keep working).

        The observers record instruction starts, RST facts and the provenance of code copied
        to RAM; they watch every bus access and halve the emulation speed. Switch them on while
        exercising the analysed ROM (boot, disk access, RSX commands), off for normal use.
        What was collected is kept either way.
        """
        self.analysis_enabled = enabled
        self._sync_observer()
        return {"analysis_enabled": enabled, "observing": self._analysis_observing,
                "targets": list(self.analysis_targets)}  # fmt: skip

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

    def _known_variables(self) -> tuple[Variable, ...]:
        """The known OS/AMSDOS variables of the machine now (FR-DBG-11), for the source."""
        variables: VariableMap | None = getattr(self, "variables", None)
        return tuple(variables.variables(self.machine.peek)) if variables is not None else ()

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
        stem = self._stem(slot_name)
        if kind == "m1map":
            return self._path(f"project/{stem}.m1map")
        return self._path(f"output/{stem}_{kind}")

    def _stem(self, target: str) -> str:
        """File name stem of a target: the ROM file's stem, or ram_<name> for RAM targets."""
        if target.startswith("ram:"):
            return "ram_" + target[4:]
        try:
            return Path(self._rom_slot(target).name).stem or target
        except SessionError:
            return target

    def _database_path(self, target: str) -> Path:
        """Database file of a target: project/<stem>.analysis.json (D-31)."""
        return self._path(f"project/{self._stem(target)}.analysis.json")

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
            fill_policy: Never-read bytes: "raw", "data", "ff" or "static" (code/data guess
                by recursive disassembly from the ROM header; no run-time observations).
            conflict: Value for conflict bytes: "m1" (raw) or "data" (decoded).
        """
        try:
            raw, access_map = self._path(raw_path).read_bytes(), load_m1map(self._path(map_path))
            if fill_policy == "static":
                clear = self._static_from_map(raw, access_map, conflict)
            else:
                clear = decode_rom(raw, access_map, vortex_decode, fill_policy, conflict)
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
            fill_policy: Never-read bytes: "raw", "data", "ff" or "static" (opcode bytes of
                statically found code raw, all other never-read bytes decoded; D-20).
            conflict: Value for conflict bytes: "m1" (raw) or "data" (decoded).
        """
        name = self._slot_name(slot)
        file = self._path(path) if path else self._default_path(name, "clear.rom")
        try:
            if fill_policy == "static":
                view, database = self._classified(name, "static", conflict)
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(view.final_image(database, "static"))
            else:
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
        if new.name not in self.analysis_targets:
            self._add_target(new)
        self._sync_observer()
        return self.analysis_target_list()

    def analysis_target_remove(self, target: str) -> dict[str, Any]:
        """Remove an analysis target by name ("rom5", "lower", "ram:<name>")."""
        if self.analysis_targets.pop(target, None) is None:
            raise self._error(f"no analysis target {target!r}")
        self.analysis_databases.pop(target, None)
        self._sync_observer()
        return self.analysis_target_list()

    def analysis_target_list(self) -> dict[str, Any]:
        """List the analysis targets with their flag counts and RAM-capture state."""
        targets = []
        for target in self.analysis_targets.values():
            entry: dict[str, Any] = {"name": target.name, "kind": target.kind,
                                     "start": f"{target.start:04X}", "length": target.length,
                                     "flags": target.flag_counts()}  # fmt: skip
            database = self.analysis_databases.get(target.name)
            if database is not None:
                entry["kinds"] = database.counts()["kinds"]
            if target.kind == "ram":
                entry |= {"capture": target.capture, "captured": target.image is not None,
                          "modified_after_capture": len(target.modifications)}  # fmt: skip
            targets.append(entry)
        return {"targets": targets, "analysis_enabled": self.analysis_enabled}

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
        blocks = [b for b in self._all_relocations() if target in (None, b.target)]
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
        before = len(self.manual_relocations) + len(self.saved_relocations)

        def kept(block: RelocatedBlock) -> bool:
            """True for blocks other than the one to remove."""
            return (block.target, block.ram_start) != (target, address)

        self.manual_relocations = [b for b in self.manual_relocations if kept(b)]
        self.saved_relocations = [b for b in self.saved_relocations if kept(b)]
        if len(self.manual_relocations) + len(self.saved_relocations) == before:
            raise self._error(f"no manual or saved block of {target} at {address:04X}")
        return self.reloc_list(target)

    def _all_relocations(self) -> list[RelocatedBlock]:
        """Detected blocks of this run, manual ones, and saved ones not detected again."""
        detected = self._analysis_observer.relocated_blocks() if self._analysis_observer else []
        blocks = detected + self.manual_relocations
        keys = {(b.target, b.rom_start, b.ram_start) for b in blocks}
        return blocks + [
            b for b in self.saved_relocations if (b.target, b.rom_start, b.ram_start) not in keys
        ]

    # == analysis database and source (phase 8) ==================================================

    def _target_name(self, target: str | int | None) -> str:
        """Normalise a target name; None = the only analysis target."""
        if target is None:
            if len(self.analysis_targets) != 1:
                names = sorted(self.analysis_targets) or "none"
                raise self._error(f"give the target: analysis targets: {names}")
            return next(iter(self.analysis_targets))
        text = str(target)
        name = text if text.startswith("ram:") else self._slot_name(text)
        if name not in self.analysis_targets:
            raise self._error(f"no analysis target {name!r} (add it with analysis_target_add)")
        return name

    def _chosen_blocks(
        self, target: AnalysisTarget
    ) -> tuple[tuple[RelocatedBlock, ...], tuple[RelocatedBlock, ...]]:
        """Pick the relocated blocks shown at their RAM address (D-22).

        Only blocks that ran in RAM (or that the owner added) count: a copy that never runs is
        data, e.g. a table. Per ROM range the copy with the most executed instructions wins;
        blocks overlapping a chosen one in the ROM, or lying in the target's own address
        range, are left out. RAM ranges may overlap: a ROM may copy several routines to the
        same RAM address at different times (overlays); each keeps its own DISP block.

        Returns:
            (chosen blocks sorted by ROM start, all blocks of the target).
        """
        blocks = [b for b in self._all_relocations() if b.target == target.name]
        chosen: list[RelocatedBlock] = []
        for block in sorted(blocks, key=lambda b: (-b.executed, -b.length, b.rom_start)):
            rom = range(block.rom_start, block.rom_start + block.length)
            ram = range(block.ram_start, block.ram_start + block.length)
            if not (block.executed or block.manual):
                continue
            if rom.stop > target.length or ram.stop > 0x10000:
                continue
            if target.start < ram.stop and ram.start < target.start + target.length:
                continue  # RAM range inside the target's own addresses: labels would clash
            if any(c.rom_start < rom.stop and rom.start < c.rom_start + c.length for c in chosen):
                continue
            chosen.append(block)
        return tuple(sorted(chosen, key=lambda b: b.rom_start)), tuple(blocks)

    def _view(self, name: str, conflict: str = "m1") -> TargetView:
        """Build the view of a target: clear image, access map and relocated blocks."""
        if conflict not in ("m1", "data"):
            raise self._error("conflict must be 'm1' or 'data'")
        target = self.analysis_targets[name]
        if target.kind == "ram":
            start = target.start
            image = target.image or bytes(self._cpc().memory.ram[start : start + target.length])
            view = TargetView(name, start, image)
        else:
            rom = self._rom_slot(name)
            if rom.shadow is None or rom.decoder is None:
                view = TargetView(name, target.start, rom.data)
            else:
                access = bytes(rom.shadow.access_map)
                image = decode_rom(rom.data, access, rom.decoder, "data", conflict)
                view = TargetView(name, target.start, image, rom.data, access, rom.decoder)
        chosen, _ = self._chosen_blocks(target)
        profile = CpcProfile(image_range=(view.start, view.start + view.length - 1))
        instructions = executed_instructions(view, bytes(target.flags), profile) if chosen else []
        shown = tuple(
            b for b in chosen if b.manual or position_dependent(view, b, instructions)
        )  # D-22: only position-dependent copies are shown at their RAM address
        copies = tuple(b for b in chosen if b not in shown)
        return replace(view, blocks=shown, ram_copies=copies)

    def _classified(
        self, name: str, fill_policy: str, conflict: str = "m1"
    ) -> tuple[TargetView, TargetDatabase]:
        """Rebuild the classification of a target (owner, observed, static) and return it.

        Static guesses are made for plain targets always, for scrambled ROMs only with the
        "static" fill policy (other policies do not keep opcode bytes raw, so a guessed
        instruction would not survive in the image).
        """
        if fill_policy not in FILL_POLICIES:
            raise self._error(f"fill policy must be one of {', '.join(FILL_POLICIES)}")
        view = self._view(name, conflict)
        database = self.analysis_databases[name]
        profile = CpcProfile(image_range=(view.start, view.start + view.length - 1))
        flags = bytes(self.analysis_targets[name].flags)
        classify_target(
            database, view, flags, profile, fill_policy == "static" or not view.scrambled
        )
        return view, database

    @staticmethod
    def _static_from_map(raw: bytes, access_map: bytes, conflict: str) -> bytes:
        """Decode a Vortex ROM from its map with the static fill policy (no observations)."""
        image = decode_rom(raw, access_map, vortex_decode, "data", conflict)
        view = TargetView("rom", 0xC000, image, raw, access_map, vortex_decode)
        database = TargetDatabase("rom", 0xC000, len(raw))
        profile = CpcProfile(image_range=(0xC000, 0xC000 + len(raw) - 1))
        classify_target(database, view, bytes(len(raw)), profile)
        return view.final_image(database, "static")

    def _merge_database(self, name: str, path: Path) -> None:
        """Merge a database file: kinds, observations, overrides, flags, relocated blocks."""
        try:
            extra = self.analysis_databases[name].merge_file(path)
        except (OSError, ValueError, KeyError) as error:
            raise self._error(f"cannot load {path}: {error}") from None
        flags = bytes.fromhex(extra.get("flags", ""))
        target = self.analysis_targets[name]
        for offset, value in enumerate(flags[: target.length]):
            target.flags[offset] |= value
        for entry in extra.get("relocations", []):
            block = RelocatedBlock(**{**entry, "rom_start": int(entry["rom_start"], 16),
                                      "ram_start": int(entry["ram_start"], 16)})  # fmt: skip
            existing = self.manual_relocations if block.manual else self.saved_relocations
            if all((b.rom_start, b.ram_start) != (block.rom_start, block.ram_start)
                   or b.target != block.target for b in existing):  # fmt: skip
                existing.append(block)

    def analysis_save(self, target: str | None = None) -> dict[str, Any]:
        """Save the analysis state (D-19, D-31); called automatically when the GUI closes.

        Writes the access map of every scrambled ROM (project/<rom>.m1map) and the database
        of every analysis target (project/<rom>.analysis.json: classification, runtime
        observations, owner overrides, target flags, relocated blocks).

        Args:
            target: Only this target (and its access map); None saves everything.
        """
        names = list(self.analysis_targets) if target is None else [self._target_name(target)]
        saved = []
        for slot_name, _ in self._cpc().memory.shadows():
            if target is None or slot_name in names:
                saved.append(self.m1map_save(slot_name)["path"])
        for name in names:
            self._classified(name, "static")
            blocks = [b for b in self._all_relocations() if b.target == name]
            extra = {
                "flags": bytes(self.analysis_targets[name].flags).hex(),
                "relocations": [
                    {**asdict(b), "rom_start": f"{b.rom_start:04X}",
                     "ram_start": f"{b.ram_start:04X}"} for b in blocks
                ],
            }  # fmt: skip
            path = self._database_path(name)
            self.analysis_databases[name].save(path, extra)
            saved.append(str(path))
        return {"saved": saved}

    def analysis_load(self, target: str | None = None, path: str | None = None) -> dict[str, Any]:
        """Merge a saved database into the current one (stronger classification wins per byte).

        Args:
            target: The target (default: the only one).
            path: The file (default project/<rom>.analysis.json).
        """
        name = self._target_name(target)
        file = self._path(path) if path else self._database_path(name)
        if not file.exists():
            raise self._error(f"no database file {file}")
        self._merge_database(name, file)
        return {"target": name, "path": str(file),
                "kinds": self.analysis_databases[name].counts()["kinds"]}  # fmt: skip

    def classify(
        self, first: int | str, last: int | str, kind: str, target: str | None = None
    ) -> dict[str, Any]:
        """Classify an address range as the owner (strongest source, kept in the database).

        Args:
            first: First address (CPU address in the target, e.g. "C123").
            last: Last address (inclusive).
            kind: "code" (consecutive instructions from `first`), "string", "word_table",
                "byte_data", or "auto" to drop the owner's classification of exactly this
                range again.
            target: The target (default: the only one).
        """
        name = self._target_name(target)
        database = self.analysis_databases[name]
        first_offset = self._address(first) - database.start
        last_offset = self._address(last) - database.start
        if not 0 <= first_offset <= last_offset < database.length:
            raise self._error(f"range outside the target {name}")
        kinds = {"code": Kind.INSN_START, "string": Kind.STRING, "word_table": Kind.WORD_TABLE,
                 "byte_data": Kind.BYTE_DATA}  # fmt: skip
        if kind == "auto":
            database.remove_override(first_offset, last_offset)
        elif kind in kinds:
            database.add_override(first_offset, last_offset, kinds[kind])
        else:
            raise self._error(f"kind must be one of {', '.join([*kinds, 'auto'])}")
        self._classified(name, "static")
        return {"target": name, "overrides": database.overrides,
                "kinds": database.counts()["kinds"]}  # fmt: skip

    def source_generate(
        self, target: str | None = None, path: str | None = None, fill_policy: str = "static",
        conflict: str = "m1",
    ) -> dict[str, Any]:  # fmt: skip
        """Generate the sjasmplus source of a target from its database (D-31, §6.15).

        Args:
            target: The target (default: the only one).
            path: Output file (default output/<rom>.asm).
            fill_policy: Never-read bytes of a scrambled ROM: "static" (default; code guesses
                disassembled), "raw", "data" or "ff" (emitted as data).
            conflict: Value for conflict bytes: "m1" (raw) or "data" (decoded).

        Returns:
            Path, number of lines, statistics, and the kinds per byte.
        """
        name = self._target_name(target)
        view, database = self._classified(name, fill_policy, conflict)
        stem = self._stem(name)
        file = self._path(path) if path else self._path(f"output/{stem}.asm")
        _, all_blocks = self._chosen_blocks(self.analysis_targets[name])
        is_ram = name.startswith("ram:")
        title = f"RAM target {name}" if is_ram else f"{self._rom_slot(name).name} (slot {name})"
        generated = generate_source(
            view.resolve(database, fill_policy), database, self.symbols, title=title,
            output_name=f"{stem}.bin" if is_ram else f"{stem}_clear.rom",
            fill_policy=fill_policy, conflict=conflict, alternative_blocks=all_blocks,
            variables=self._known_variables(),
        )  # fmt: skip
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(generated.text, encoding="utf-8")
        self._line_maps[file.resolve()] = generated.line_of_offset
        return {"target": name, "path": str(file), "lines": generated.text.count("\n"),
                "statistics": generated.statistics,
                "kinds": database.counts()["kinds"]}  # fmt: skip

    def source_verify(self, path: str | None = None, target: str | None = None) -> dict[str, Any]:
        """Round-trip check (D-33): assemble a generated source, compare with the clear image.

        The expected image uses the fill policy and conflict choice recorded in the source's
        header line; differing addresses are listed with both values and the source line.

        Args:
            path: The source (default output/<rom>.asm).
            target: The target (default: the only one).
        """
        name = self._target_name(target)
        file = self._path(path) if path else self._path(f"output/{self._stem(name)}.asm")
        if not file.exists():
            raise self._error(f"no source {file} (generate it with source_generate)")
        header = re.search(r"fill policy: (\w+), conflicts: (\w+)",
                           file.read_text(encoding="utf-8"))  # fmt: skip
        fill_policy, conflict = header.groups() if header else ("static", "m1")
        view, database = self._classified(name, fill_policy, conflict)
        expected = view.final_image(database, fill_policy)
        try:
            result = verify_source(file, expected, view.start, self._line_maps.get(file.resolve()))
        except AssemblyError as error:
            raise self._error(str(error)) from None
        return {"target": name, "path": str(file), "fill_policy": fill_policy, **asdict(result)}
