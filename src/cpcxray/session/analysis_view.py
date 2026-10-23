"""Session method for the Project tab's analysis phases (task 5i, part 2).

A mixin of `Session` (every public method is an MCP tool, D-27). `analysis_status` tells, for
one analysis target, what each phase of the work has reached:

1. Collect - analysis on or off, what the run has seen (instruction starts, code executed via
   RAM, relocated blocks, observations) and the coverage: bytes of code, data and unknown;
2. Decode - only for a ROM with a decoder: how its bytes were read (opcode M1, data, both =
   conflict, never), the real conflicts, the access map file;
3. Save - whether the collected state differs from the one saved or loaded last;
4. Generate - the last source written (file, fill policy, lines, labels, outside names);
5. Verify - the last round trip of that source.

The coverage needs a classification of the target, which is rebuilt only when what was
collected has changed (`_analysis_fingerprint`), so asking often costs nothing while stopped.
"""

from __future__ import annotations

from typing import Any

from cpcxray.decoding.vortex import DECODERS
from cpcxray.session.analysis import AnalysisMixin

CODE_KINDS = ("insn_start", "insn_body", "rst_operand")
DATA_KINDS = ("far_addr", "string", "word_table", "byte_data")


class AnalysisViewMixin(AnalysisMixin):  # pylint: disable=abstract-method  # Session implements the stubs
    """`analysis_status` of `Session` (see the module docstring)."""

    def analysis_status(self, target: str | None = None, classify: bool = True) -> dict[str, Any]:
        """Where each analysis phase of a target stands (the Project tab's phases).

        Args:
            target: The target ("rom7", "lower", "ram:<name>"); None for the first one.
            classify: Bring the coverage up to date (classifies the target if what was
                collected changed); False gives the coverage of the last classification (made
                now if there is none yet) - cheap enough for a GUI while the machine runs.

        Returns:
            "targets" (all names), "analysis_enabled", "observing" and, for the target,
            "target", "kind" ("rom" / "ram"), "file", "decoder", "collect" (seen, coverage
            code / data / unknown, for RAM targets "captured"), "decode" (None without a
            decoder; else the access classes, "conflicts" and "real_conflicts", "map_path"),
            "save" ("changed", "database_path", "exists"), "source" and "verify" (the last
            results, or None).
        """
        names = list(self.analysis_targets)
        result: dict[str, Any] = {
            "targets": names, "analysis_enabled": self.analysis_enabled,
            "observing": self._analysis_observing, "target": None,
        }  # fmt: skip
        if not names:
            return result
        name = self._target_name(target) if target is not None else names[0]
        analysis_target = self.analysis_targets[name]
        fingerprint = self._analysis_fingerprint(name)
        result |= {"target": name, "kind": analysis_target.kind, "file": self._stem(name),
                   "decoder": None, "decode": None}  # fmt: skip
        result["collect"] = self._collect_status(name, fingerprint, classify)
        rom = self._rom_slot(name) if analysis_target.kind == "rom" else None
        if rom is not None and rom.decoder is not None and rom.shadow is not None:
            decoder = next((key for key, value in DECODERS.items() if value is rom.decoder), "?")
            result |= {"decoder": decoder, "decode": self._decode_status(name)}
        database_path = self._database_path(name)
        result["save"] = {"changed": fingerprint != self._saved_states.get(name),
                          "database_path": self._project_path(database_path),
                          "exists": database_path.exists()}  # fmt: skip
        result["source"] = self.source_results.get(name)
        result["verify"] = self.verify_results.get(name)
        return result

    def _collect_status(self, name: str, fingerprint: str, classify: bool) -> dict[str, Any]:
        """What the run has seen in a target and its coverage (classified when it changed)."""
        target = self.analysis_targets[name]
        flags = target.flag_counts()
        cached = self._coverage.get(name)
        if cached is None or (classify and cached[0] != fingerprint):
            self._classified(name, "static")
            kinds = self.analysis_databases[name].counts()["kinds"]
            coverage = {
                "code": sum(kinds[k] for k in CODE_KINDS),
                "data": sum(kinds[k] for k in DATA_KINDS), "unknown": kinds["unknown"],
            }  # fmt: skip
            self._coverage[name] = (fingerprint, coverage)
        status: dict[str, Any] = {
            "coverage": self._coverage[name][1],
            "coverage_current": self._coverage[name][0] == fingerprint,
            "instruction_starts": flags["instruction start"],
            "executed_via_ram": flags["executed via RAM"],
            "relocated_blocks": sum(1 for b in self._all_relocations() if b.target == name),
            "observations": len(self.analysis_databases[name].observations),
            "length": target.length,
        }
        if target.kind == "ram":
            status["captured"] = target.image is not None
        return status

    def _decode_status(self, name: str) -> dict[str, Any]:
        """How a scrambled ROM's bytes were read, its conflicts, its access map file."""
        shadow = self._shadow(name)
        conflicts = shadow.conflicts()
        map_path = self._default_path(name, "m1map")
        return {
            "classes": shadow.counts(), "conflicts": len(conflicts),
            "real_conflicts": sum(1 for c in conflicts if not c.harmless),
            "map_path": self._project_path(map_path), "map_exists": map_path.exists(),
        }  # fmt: skip
