"""docs/mcp.md's tool reference matches the tools (python -m tools.generate_mcp_reference)."""

from __future__ import annotations

from tools.generate_mcp_reference import DOC, markdown, reference, signature, tools, updated


def test_reference_is_up_to_date() -> None:
    """Every tool is in a group, and the generated part of docs/mcp.md is current."""
    text = DOC.read_text(encoding="utf-8")
    assert updated(text) == text, "run: python -m tools.generate_mcp_reference"
    assert reference().count("\n#### `") == len(tools())


def test_markdown_of_a_docstring() -> None:
    """Args become a list of `name`: text, continuation lines join; Returns a paragraph."""
    docstring = (
        "Do it.\n\nArgs:\n    first: One\n        and more.\n    second: Two.\n\n"
        "Returns:\n    A value\n    on two lines."
    )
    assert markdown(docstring) == (
        "Do it.\n\n**Args**\n\n- `first`: One and more.\n- `second`: Two.\n\n**Returns**\n\n"
        "A value on two lines."
    )
    assert signature(tools()["memory_view"]) == (
        'address: int | str, length: int = 128, view: str = "cpu"')  # fmt: skip
