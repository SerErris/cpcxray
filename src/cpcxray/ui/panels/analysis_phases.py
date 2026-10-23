"""The Project tab's analysis section: target chips and the numbered phases (task 5i, part 2).

Agreed with the owner on 2026-10-22 (artboard "Project 3"): chips for the analysis targets (ROM
slots and RAM ranges) beside the analysis switch; for the selected target its phases, each with
a status badge, its buttons and a line of detail:

1. Collect - run the machine with analysis on; a bar of the coverage (code / data / unknown)
   and what the run has seen; a RAM range can take its copy now;
2. Decode - only for a ROM with a decoder: a bar of how its bytes were read (opcode M1, data,
   both = conflict, never), Save map, Load map, Conflicts…; the exports with a fill policy:
   clear ROM, ROM map, coverage CSV, decode from the map;
3. Save analysis - "changed" while what was collected is not saved (analysis file and map);
4. Generate source - output file and fill policy; lines, labels, outside names;
5. Verify - the sjasmplus round trip: "identical ✓", or the number of differing bytes and the
   first of them (address, expected and assembled value, the source line).

Without a decoder the phases are numbered 1-4. Everything comes from `analysis_status` and the
analysis tools of the session (D-27).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels import colours
from cpcxray.ui.panels.project_text import (
    FILL_POLICIES,
    PHASES,
    collect_text,
    phases,
    source_text,
    verify_badge,
    verify_lines,
)
from cpcxray.ui.panels.widgets import Chips, badge, separator, set_badge

if TYPE_CHECKING:
    from cpcxray.ui.panels.base import Tab

BAR_WIDTH = 260
BAR_HEIGHT = 9
COVERAGE_COLOURS = {
    "code": colours.SYMBOL_TYPE_COLOURS["sub"][0],
    "data": colours.SYMBOL_TYPE_COLOURS["data"][0],
    "unknown": colours.TRACK,
}
ACCESS_COLOURS = {"M1": colours.SYMBOL_TYPE_COLOURS["FW"][0],
                  "data": colours.SYMBOL_TYPE_COLOURS["VAR"][0], "conflict": colours.CHANGED,
                  "unknown": colours.TRACK}  # fmt: skip
ACCESS_NAMES = {"M1": "opcode (M1)", "data": "data", "conflict": "both", "unknown": "never read"}
MAX_CONFLICTS_SHOWN = 8
CLASSIFY_SECONDS = 2.0  # coverage updates while the machine runs
NO_TARGETS = ('no analysis targets: mark a ROM "analyse" or add RAM code to analyse in the '
              "machine editor")  # fmt: skip


class AnalysisPhases:  # pylint: disable=too-many-instance-attributes  # widgets of five phases
    """The analysis section of the Project tab (see the module docstring)."""

    def __init__(self, tab: Tab) -> None:
        """Remember the tab (its widget helpers, items and session)."""
        self.tab = tab
        self.session = tab.session
        self.items = tab.items
        self.target: str | None = None
        self.chips: Chips | None = None
        self._targets_shown: list[str] | None = None
        self._classified_at = 0.0  # when the coverage was last brought up to date (running)

    # -- building --------------------------------------------------------------------------------

    def build(self) -> None:
        """Chips and switch, the "no targets" hint, the five phase blocks."""
        tab = self.tab
        with dpg.group(horizontal=True):
            self.items["analysis_chips"] = dpg.add_group(horizontal=True)
            self.items["analysis_switch"] = tab.button("", self.toggle_analysis)
        tab.text("analysis_none", color=colours.TEXT_DIM, show=False)
        with dpg.group() as body:
            self.items["analysis_body"] = body
            for key in PHASES:
                self._build_phase(key)

    def _build_phase(self, key: str) -> None:
        """One phase: title, badge and buttons; a bar (Collect, Decode); a detail line."""
        tab = self.tab
        with dpg.group() as phase:
            self.items[f"phase_{key}"] = phase
            if key != PHASES[0]:
                separator(tab.styles)  # the phases apart, as the machine editor's parts
            with dpg.group(horizontal=True):
                tab.text(f"phase_title_{key}", color=colours.HEADING)
                self.items[f"phase_badge_{key}"] = badge(tab.styles, "")
                self._build_controls(key)
            if key in ("collect", "decode"):
                with dpg.group(horizontal=True):
                    self.items[f"phase_bar_{key}"] = dpg.add_drawlist(BAR_WIDTH, BAR_HEIGHT)
                    self.items[f"phase_legend_{key}"] = dpg.add_group(horizontal=True)
            if key == "decode":
                self._build_exports()
            tab.text(f"phase_detail_{key}", color=colours.TEXT_DIM, wrap=560)
            if key == "decode":
                tab.text("decode_conflicts", color=colours.TEXT_DIM)

    def _build_controls(self, key: str) -> None:
        """A phase's buttons and fields on its title line."""
        tab, session = self.tab, self.session
        if key == "collect":
            self.items["collect_capture"] = tab.button(
                "Take copy now", lambda: session.analysis_capture(self._name()), show=False
            )
        elif key == "decode":
            tab.button("Save map", lambda: session.m1map_save(self._name()))
            tab.button("Load map", lambda: session.m1map_load(self._name()))
            tab.button("Conflicts…", self.show_conflicts)
        elif key == "save":
            tab.button("Save", lambda: session.analysis_save(self._name()))
            tab.button("Load", lambda: session.analysis_load(self._name()))
        elif key == "generate":
            tab.input("source_path", "project/<rom>.asm", 200)
            self.items["source_fill"] = dpg.add_combo(FILL_POLICIES, default_value="static",
                                                      width=70)  # fmt: skip
            tab.button("Generate", self.generate)
        elif key == "verify":
            tab.button("Verify", lambda: session.source_verify(target=self._name()))

    def _build_exports(self) -> None:
        """The Decode phase's exports: fill policy, clear ROM, ROM map, coverage CSV, from map."""
        tab, session = self.tab, self.session
        with dpg.group(horizontal=True):
            dpg.add_text("exports  fill", color=colours.TEXT_DIM)
            self.items["decode_fill"] = dpg.add_combo(FILL_POLICIES, default_value="static",
                                                      width=70)  # fmt: skip
            tab.button("Clear ROM", lambda: session.rom_dump(
                self._name(), fill_policy=tab.value("decode_fill")))  # fmt: skip
            tab.button("ROM map", lambda: session.rom_map(self._name()))
            tab.button("Coverage CSV", lambda: session.coverage_csv(self._name()))
            tab.button("Decode from map", self.decode_from_map)

    # -- actions ---------------------------------------------------------------------------------

    def _name(self) -> str:
        """The selected target (ValueError without one: shown in the message line)."""
        if self.target is None:
            raise ValueError("no analysis target")
        return self.target

    def toggle_analysis(self) -> Any:
        """Switch the analysis observers on or off (as the Analysis button above the screen)."""
        return self.session.analysis_enable(not self.session.analysis_enabled)

    def select(self, chosen: list[str]) -> None:
        """A target chip was clicked."""
        self.target = chosen[0]
        dpg.set_value(self.items["source_path"], "")
        dpg.set_value(self.items["decode_conflicts"], "")
        self.refresh()

    def generate(self) -> Any:
        """Generate the target's source (the output file and fill policy of the fields)."""
        path = self.tab.value("source_path").strip() or None
        return self.session.source_generate(self._name(), path, self.tab.value("source_fill"))

    def show_conflicts(self) -> Any:
        """List the first real conflicts (read both as opcode and as data) in the detail line."""
        result = self.session.shadow_conflicts(self._name())
        real = [c for c in result["conflicts"] if c["real"]][:MAX_CONFLICTS_SHOWN]
        lines = [f"{c['address']}: raw {c['raw']}, as opcode {c['as_m1']}, as data "
                 f"{c['as_data']} (first PC {c['first_pc']})" for c in real]  # fmt: skip
        text = "\n".join(lines) if lines else "no real conflicts"
        dpg.set_value(self.items["decode_conflicts"],
                      f"{result['real']} real of {result['total']} conflicts:\n{text}")  # fmt: skip
        return result

    def decode_from_map(self) -> Any:
        """Decode the ROM file with its saved access map, without a run (FR-DEC-04)."""
        name = self._name()
        slot = name[3:] if name.startswith("rom") else name
        rom = next((r for r in self.session.config_get()["roms"] if r["slot"] == slot), None)
        if rom is None:
            raise ValueError(f"no ROM file configured for {name}")
        status = self.session.analysis_status(name)
        stem = status["file"]
        return self.session.rom_decode_from_map(
            rom["file"], status["decode"]["map_path"], f"project/{stem}_clear.rom",
            self.tab.value("decode_fill"),
        )  # fmt: skip

    # -- refreshing ------------------------------------------------------------------------------

    def refresh(self, running: bool = False) -> None:
        """Chips, switch and the phases of the selected target.

        Args:
            running: The machine runs: the coverage (a classification, ~50 ms) is brought up
                to date at most every CLASSIFY_SECONDS - at once when there is none yet, so
                the code that ran (e.g. the ROM's init) shows as code right away (owner,
                2026-10-23); stopped, it is always up to date.
        """
        if self.target not in self.session.analysis_targets:
            self.target = None  # a new machine: its first target
        now = time.monotonic()
        classify = not running or now - self._classified_at >= CLASSIFY_SECONDS
        if classify:
            self._classified_at = now
        status = self.session.analysis_status(self.target, classify)
        self.target = status["target"]
        self._refresh_chips(status["targets"])
        enabled = status["analysis_enabled"]
        switch = self.items["analysis_switch"]
        dpg.set_item_label(switch, "● Analysis on" if enabled else "○ Analysis off")
        dpg.bind_item_theme(switch, self.tab.styles.badge("on" if enabled else "plain"))
        has_target = status["target"] is not None
        dpg.configure_item(self.items["analysis_none"], show=not has_target)
        dpg.configure_item(self.items["analysis_body"], show=has_target)
        if not has_target:
            dpg.set_value(self.items["analysis_none"], NO_TARGETS)
            return
        shown = dict(phases(status["decode"] is not None))
        for key in PHASES:
            dpg.configure_item(self.items[f"phase_{key}"], show=key in shown)
            if key in shown:
                dpg.set_value(self.items[f"phase_title_{key}"], shown[key])
        self._refresh_collect(status)
        if status["decode"] is not None:
            self._refresh_decode(status["decode"])
        self._refresh_save(status["save"])
        self._refresh_source(status)

    def _refresh_chips(self, targets: list[str]) -> None:
        """Rebuild the target chips when the targets changed (a new machine)."""
        if targets == self._targets_shown:
            return
        self._targets_shown = list(targets)
        container = self.items["analysis_chips"]
        dpg.delete_item(container, children_only=True)
        if targets:
            dpg.push_container_stack(container)
            selected = [self.target] if self.target in targets else [targets[0]]
            self.chips = Chips(self.tab.styles, targets, selected, on_change=self.select)
            dpg.pop_container_stack()

    def _set_badge(self, key: str, text: str, style: str) -> None:
        """A phase's status badge."""
        set_badge(self.tab.styles, self.items[f"phase_badge_{key}"], text, style)

    def _refresh_collect(self, status: dict[str, Any]) -> None:
        """Collect: on/off, coverage bar, what was seen."""
        collect = status["collect"]
        observing = status["observing"]
        self._set_badge("collect", "collecting" if observing else "analysis off",
                        "on" if observing else "plain")  # fmt: skip
        dpg.configure_item(self.items["collect_capture"], show=status["kind"] == "ram")
        coverage = collect["coverage"]
        parts = [(name, coverage[name], COVERAGE_COLOURS[name], name) for name in coverage]
        self._draw_bar("collect", parts, collect["length"])
        detail = collect_text(collect)
        if not collect["coverage_current"]:
            detail += " \u00b7 coverage follows every few seconds"
        dpg.set_value(self.items["phase_detail_collect"], detail)

    def _refresh_decode(self, decode: dict[str, Any]) -> None:
        """Decode: how the bytes were read, conflicts, the access map file."""
        classes = decode["classes"]
        parts = [(name, classes[name], ACCESS_COLOURS[name], ACCESS_NAMES[name])
                 for name in ("M1", "data", "conflict", "unknown")]  # fmt: skip
        self._draw_bar("decode", parts, sum(classes.values()))
        real = decode["real_conflicts"]
        self._set_badge("decode", f"{real} real conflicts" if real else "no real conflicts",
                        "error" if real else "ok")  # fmt: skip
        saved = "saved" if decode["map_exists"] else "not saved yet"
        dpg.set_value(self.items["phase_detail_decode"],
                      f"access map {decode['map_path']} ({saved}) \u00b7 "
                      f"{decode['conflicts']} conflicts, {real} real")  # fmt: skip

    def _refresh_save(self, save: dict[str, Any]) -> None:
        """Save analysis: changed / saved, the files."""
        if save["changed"]:
            self._set_badge("save", "changed", "changed")
        else:
            self._set_badge("save", "saved" if save["exists"] else "nothing collected",
                            "ok" if save["exists"] else "plain")  # fmt: skip
        dpg.set_value(self.items["phase_detail_save"], save["database_path"])

    def _refresh_source(self, status: dict[str, Any]) -> None:
        """Generate and Verify: the last source and its round trip."""
        source = status["source"]
        self._set_badge("generate", "written" if source else "not yet",
                        "ok" if source else "plain")  # fmt: skip
        dpg.set_value(self.items["phase_detail_generate"], source_text(source))
        text, style = verify_badge(status["verify"], source is not None)
        self._set_badge("verify", text, style)
        dpg.set_value(self.items["phase_detail_verify"], "\n".join(verify_lines(status["verify"])))

    def _draw_bar(self, key: str, parts: list[tuple[str, int, Any, str]], total: int) -> None:
        """A stacked bar of byte counts and its colour-matched legend."""
        drawing = self.items[f"phase_bar_{key}"]
        legend = self.items[f"phase_legend_{key}"]
        dpg.delete_item(drawing, children_only=True)
        dpg.delete_item(legend, children_only=True)
        x = 0.0
        for _name, count, colour, _label in parts:
            width = BAR_WIDTH * count / total if total else 0
            if width:
                dpg.draw_rectangle((x, 0), (x + width, BAR_HEIGHT), color=colour, fill=colour,
                                   parent=drawing)  # fmt: skip
            x += width
        for _name, count, colour, label in parts:
            text_colour = colours.TEXT_DIM if colour == colours.TRACK else colour  # readable
            dpg.add_text(f"{label} {count}", color=text_colour, parent=legend)
