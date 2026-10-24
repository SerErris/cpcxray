"""Write the MCP tool reference of docs/mcp.md from the `Session` methods (one per tool, D-27).

Run `python -m tools.generate_mcp_reference` after adding or changing a tool; the part of
docs/mcp.md between the two markers is replaced. tests/test_mcp_reference.py fails while the file
is out of date, and also when a tool belongs to no group below.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from cpcxray.session.session import Session

DOC = Path(__file__).resolve().parents[1] / "docs" / "mcp.md"
BEGIN = "<!-- begin generated: python -m tools.generate_mcp_reference -->"
END = "<!-- end generated -->"

GROUPS: dict[str, tuple[str, ...]] = {
    "Machine": ("machine_status", "reset", "load_config", "rom_list", "rom_load", "rom_unload",
                "devices_get", "paging_get", "sna_load", "sna_save"),
    "Configuration": ("config_get", "config_read", "config_write", "config_apply", "rom_header",
                      "loads_get", "memory_file_check"),
    "Running": ("run", "run_until", "step", "step_over", "step_out", "pause", "last_stop_get"),
    "CPU": ("registers_get", "registers_set", "stack_get"),
    "Memory": ("memory_read", "memory_view", "memory_write", "memory_search", "memory_load",
               "memory_save", "memory_follow", "memory_changes_get"),
    "Disassembly": ("disassemble",),
    "Breakpoints": ("breakpoint_add", "breakpoint_list", "breakpoint_enable",
                    "breakpoint_remove"),
    "Instruction trace": ("trace_start", "trace_stop", "trace_read", "trace_records"),
    "Bus trace": ("bus_trace_start", "bus_trace_stop", "bus_trace_read", "bus_trace_view",
                  "bus_trace_clear"),
    "Keyboard and screen": ("type_text", "key_press", "key_release", "screen_text",
                            "screen_image"),
    "Symbols and variables": ("symbol_add", "comment_add", "operand_set", "symbol_remove",
                              "symbol_list", "symbol_search", "symbol_detail", "variable_add",
                              "variable_remove", "variable_locate", "variable_list",
                              "address_explain", "symbols_load", "symbols_save",
                              "symbols_status"),
    "Analysis": ("analysis_target_add", "analysis_target_remove", "analysis_target_list",
                 "analysis_enable", "analysis_status", "analysis_capture", "analysis_save",
                 "analysis_load", "classify", "reloc_list", "reloc_add", "reloc_remove",
                 "source_generate", "source_verify"),
    "Scrambled ROMs": ("shadow_status", "shadow_conflicts", "m1map_save", "m1map_load",
                       "m1map_ranges", "rom_decode_from_map", "rom_dump", "rom_map",
                       "coverage_csv", "rom_set_decode"),
    "Disks": ("disk_list", "disk_insert", "disk_eject", "disk_new", "disk_save", "disk_catalog",
              "disk_read_file"),
}  # fmt: skip


def tools() -> dict[str, object]:
    """Every MCP tool: the public methods of `Session` by name."""
    return {name: method for name, method in inspect.getmembers(Session, inspect.isfunction)
            if not name.startswith("_")}  # fmt: skip


def signature(method: object) -> str:
    """A tool's parameters as `name: type = default`, without `self` and the return type."""
    parts = []
    for parameter in list(inspect.signature(method).parameters.values())[1:]:  # type: ignore[arg-type]
        text = parameter.name
        if parameter.annotation is not inspect.Parameter.empty:
            text += f": {parameter.annotation}"  # a string: annotations are not evaluated
        if parameter.default is not inspect.Parameter.empty:
            default = parameter.default
            text += f' = "{default}"' if isinstance(default, str) else f" = {default}"
        parts.append(text)
    return ", ".join(parts)


def markdown(docstring: str) -> str:
    """A Google-style docstring as Markdown: "Args:" etc. in bold.

    Under Args / Raises an entry ("name: text", indented 4) becomes "- `name`: text" and its
    continuation lines join it; under Returns the indented text becomes one paragraph; other
    lines stay as they are.
    """
    out: list[str] = []
    section = ""
    for line in docstring.strip().splitlines():
        stripped = line.strip()
        if line in ("Args:", "Returns:", "Raises:"):
            section = line[:-1]
            if out and out[-1]:
                out.append("")
            out += [f"**{section}**", ""]
        elif not stripped:
            if out and out[-1]:
                out.append("")
        elif section and line.startswith("    "):
            entry_start = not line.startswith("     ") and section != "Returns"
            if entry_start:
                name, colon, text = stripped.partition(": ")
                out.append(f"- `{name}`: {text}" if colon and " " not in name else f"- {stripped}")
            elif out and out[-1]:
                out[-1] += " " + stripped
            else:
                out.append(stripped)
        else:
            section = ""
            out.append(line)
    return "\n".join(out).strip()


def reference() -> str:
    """The generated part of docs/mcp.md.

    Raises:
        ValueError: If a tool is in no group or a group names a tool that does not exist.
    """
    methods = tools()
    grouped = [name for names in GROUPS.values() for name in names]
    if missing := sorted(set(methods) - set(grouped)):
        raise ValueError(f"tools in no group of {__file__}: {', '.join(missing)}")
    if unknown := sorted(set(grouped) - set(methods)):
        raise ValueError(f"no such tools: {', '.join(unknown)}")
    lines = [BEGIN, ""]
    for group, names in GROUPS.items():
        lines += [f"### {group}", ""]
        for name in names:
            method = methods[name]
            lines += [f"#### `{name}({signature(method)})`", ""]
            lines += [markdown(inspect.getdoc(method) or ""), ""]
    lines.append(END)
    return "\n".join(lines)


def updated(text: str) -> str:
    """docs/mcp.md's text with the generated part replaced."""
    start, end = text.index(BEGIN), text.index(END) + len(END)
    return text[:start] + reference() + text[end:]


def main() -> None:
    """Rewrite the generated part of docs/mcp.md."""
    DOC.write_text(updated(DOC.read_text(encoding="utf-8")), encoding="utf-8")
    print(f"written: {DOC}")


if __name__ == "__main__":
    main()
