"""Building blocks shared by the tabs: themes, sections, badges, swatches, chips, path fields.

The agreed tab designs (2026-10-22) are built from a few recurring pieces:

* a *section*: a bordered box with a blue title (a selected entry's box is framed yellow);
* a *badge*: a small coloured label - "read-only", "changed" (lit orange), "identical ✓";
* a *swatch*: a colour square (pens, border, symbol type chips);
* *chips*: a row of buttons of which one (or several) is selected (drives, formats, types);
* a *path field*: a text input with a ▾ button (drawn like a combo box's arrow) that
  opens the file selector.

The window font is DejaVu Sans Mono (the mock-ups' font; monospaced, so columns line up, and
with the glyphs the designs use: ▾ ● ✎ ◇ ✓ ✕ · →), loaded from the system's fonts; without it
Dear PyGui's built-in font stays (ASCII only). All colours come from colours.py. `Styles`
creates the Dear PyGui themes once per GUI context (themes can only be created inside one) and
caches the per-colour ones.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import dearpygui.dearpygui as dpg

from cpcxray.ui.panels import colours
from cpcxray.ui.panels.colours import Colour
from cpcxray.ui.panels.file_selector import FileSelector

SWATCH_SIZE = 14
FONT_SIZE = 13
FONT_FILES = (  # system fonts, first found wins (no font file in the repository)
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/Library/Fonts/DejaVuSansMono.ttf",
    "C:/Windows/Fonts/DejaVuSansMono.ttf",
)


def load_font(files: Sequence[str] = FONT_FILES, size: int = FONT_SIZE) -> int | str | None:
    """Load the GUI font with the glyphs of the designs; None if no font file exists.

    Call within the Dear PyGui context; the caller binds it (`dpg.bind_font`).
    """
    path = next((Path(file) for file in files if Path(file).is_file()), None)
    if path is None:
        return None
    with dpg.font_registry():
        return dpg.add_font(str(path), size)  # Dear PyGui 2 loads the glyphs used on demand


class Styles:  # pylint: disable=too-many-instance-attributes  # one theme per look
    """The Dear PyGui themes of the GUI (create within a context, after `create_context`)."""

    def __init__(self) -> None:
        """Create the window theme and the fixed themes; per-colour ones come on demand."""
        self.window = _window_theme()
        self.section = _child_theme(colours.PANEL, colours.BORDER)
        self.section_here = _child_theme(colours.PANEL, colours.HERE)
        self.section_dim = _child_theme(colours.PANEL_DIM, colours.BORDER)
        self.primary_button = _button_theme(colours.ACCENT, colours.WHITE)
        self.plain_button = _button_theme(colours.BUTTON, colours.TEXT)
        self.remove_button = _button_theme(colours.PANEL, colours.ERROR)
        self._badges: dict[str, int | str] = {}
        self._rows: dict[tuple[Colour | None, bool], int | str] = {}
        self._texts: dict[Colour, int | str] = {}

    def badge(self, style: str) -> int | str:
        """The theme of a badge style (colours.BADGE_STYLES)."""
        if style not in self._badges:
            text, background, border = colours.BADGE_STYLES[style]
            self._badges[style] = _button_theme(background, text, border, small=True)
        return self._badges[style]

    def row_theme(self, colour: Colour | None, selected: bool) -> int | str:
        """The theme of a list row: its text colour; selected rows framed yellow."""
        key = (colour, selected)
        if key not in self._rows:
            with dpg.theme() as theme, dpg.theme_component(dpg.mvSelectable):
                if colour is not None:
                    dpg.add_theme_color(dpg.mvThemeCol_Text, colour)
                if selected:
                    dpg.add_theme_color(dpg.mvThemeCol_Header, colours.SELECTED_ROW)
                    dpg.add_theme_color(dpg.mvThemeCol_Border, colours.HERE)
                    dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1)
            self._rows[key] = theme
        return self._rows[key]

    def text(self, colour: Colour) -> int | str:
        """A theme that only sets the text colour (for buttons and other widgets)."""
        if colour not in self._texts:
            with dpg.theme() as theme, dpg.theme_component(dpg.mvAll):
                dpg.add_theme_color(dpg.mvThemeCol_Text, colour)
            self._texts[colour] = theme
        return self._texts[colour]


def _window_theme() -> int | str:
    """The palette for the whole window (bound once by the app)."""
    pairs = (
        (dpg.mvThemeCol_WindowBg, colours.BACKGROUND), (dpg.mvThemeCol_ChildBg, colours.BACKGROUND),
        (dpg.mvThemeCol_PopupBg, colours.PANEL), (dpg.mvThemeCol_Border, colours.BORDER),
        (dpg.mvThemeCol_Text, colours.TEXT), (dpg.mvThemeCol_TextDisabled, colours.TEXT_DIM),
        (dpg.mvThemeCol_FrameBg, colours.INPUT), (dpg.mvThemeCol_FrameBgHovered, colours.TRACK),
        (dpg.mvThemeCol_FrameBgActive, colours.TRACK), (dpg.mvThemeCol_Button, colours.BUTTON),
        (dpg.mvThemeCol_ButtonHovered, colours.ACCENT),
        (dpg.mvThemeCol_ButtonActive, colours.ACCENT),
        (dpg.mvThemeCol_Header, colours.SELECTED_ROW),
        (dpg.mvThemeCol_HeaderHovered, colours.ROW_ALTERNATE),
        (dpg.mvThemeCol_HeaderActive, colours.SELECTED_ROW), (dpg.mvThemeCol_Tab, colours.BUTTON),
        (dpg.mvThemeCol_TabHovered, colours.ACCENT), (dpg.mvThemeCol_TabSelected, colours.ACCENT),
        (dpg.mvThemeCol_TabSelectedOverline, colours.ACCENT),
        (dpg.mvThemeCol_TitleBg, colours.PANEL), (dpg.mvThemeCol_TitleBgActive, colours.BUTTON),
        (dpg.mvThemeCol_CheckMark, colours.HERE), (dpg.mvThemeCol_ScrollbarBg, colours.PANEL),
        (dpg.mvThemeCol_ScrollbarGrab, colours.TRACK),
        (dpg.mvThemeCol_ModalWindowDimBg, (0, 0, 0, 120)),
    )  # fmt: skip
    with dpg.theme() as theme, dpg.theme_component(dpg.mvAll):
        for target, colour in pairs:
            dpg.add_theme_color(target, colour)
        dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)
        dpg.add_theme_style(dpg.mvStyleVar_ChildRounding, 3)
        dpg.add_theme_style(dpg.mvStyleVar_TabRounding, 3)
    return theme


def _child_theme(background: Colour, border: Colour) -> int | str:
    """The theme of a section box."""
    with dpg.theme() as theme, dpg.theme_component(dpg.mvChildWindow):
        dpg.add_theme_color(dpg.mvThemeCol_ChildBg, background)
        dpg.add_theme_color(dpg.mvThemeCol_Border, border)
        dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 8, 6)
    return theme


def _button_theme(
    background: Colour, text: Colour, border: Colour | None = None, small: bool = False
) -> int | str:
    """The theme of a button or badge (hovering does not change a badge's look)."""
    with dpg.theme() as theme, dpg.theme_component(dpg.mvButton):
        dpg.add_theme_color(dpg.mvThemeCol_Button, background)
        dpg.add_theme_color(dpg.mvThemeCol_Text, text)
        if small:
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, background)
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, background)
            dpg.add_theme_style(dpg.mvStyleVar_FramePadding, 6, 0)
        if border is not None:
            dpg.add_theme_color(dpg.mvThemeCol_Border, border)
            dpg.add_theme_style(dpg.mvStyleVar_FrameBorderSize, 1)
    return theme


@contextmanager
def section(
    styles: Styles, title: str = "", height: int = 0, width: int = -1, style: str = "normal"
) -> Iterator[int | str]:
    """A bordered box with a blue title; the body is built inside the `with` block.

    Args:
        styles: The GUI's themes.
        title: Section title ("" for none).
        height: Fixed height in pixels (0: fits its content), e.g. for columns of equal height.
        width: Width in pixels (-1: the container's width).
        style: "normal", "here" (framed yellow: the selected entry) or "dim" (no content).

    Yields:
        The box (a child window).
    """
    theme = {"normal": styles.section, "here": styles.section_here, "dim": styles.section_dim}
    with dpg.child_window(
        height=height, width=width, border=True, auto_resize_y=height == 0
    ) as box:  # fmt: skip
        dpg.bind_item_theme(box, theme[style])
        if title:
            dpg.add_text(title, color=colours.HEADING)
        yield box


def badge(styles: Styles, text: str, style: str = "plain", **options: object) -> int | str:
    """Add a badge (a small coloured label, see colours.BADGE_STYLES)."""
    item = dpg.add_button(label=text, **options)  # type: ignore[arg-type]
    dpg.bind_item_theme(item, styles.badge(style))
    return item


def set_badge(styles: Styles, item: int | str, text: str, style: str) -> None:
    """Change a badge's text and style (e.g. "changed" lit or dim)."""
    dpg.set_item_label(item, text)
    dpg.bind_item_theme(item, styles.badge(style))


def swatch(colour: Colour, size: int = SWATCH_SIZE, **options: object) -> int | str:
    """Add a colour square (no picker, no tooltip)."""
    return dpg.add_color_button(
        default_value=(*colour, 255), width=size, height=size, no_alpha=True, no_tooltip=True,
        no_drag_drop=True, no_border=True, **options,  # type: ignore[arg-type]
    )  # fmt: skip


def set_swatch(item: int | str, colour: Colour) -> None:
    """Change a swatch's colour."""
    dpg.set_value(item, (*colour, 255))


def button(
    styles: Styles, label: str, callback: Callable[[], object], primary: bool = False,
    **options: object,
) -> int | str:  # fmt: skip
    """Add a button in the palette's look: blue (primary) or plain."""
    item = dpg.add_button(label=label, callback=callback, **options)  # type: ignore[arg-type]
    dpg.bind_item_theme(item, styles.primary_button if primary else styles.plain_button)
    return item


class Chips:
    """A row of chips; `multiple` allows several to be selected (type filters), else one."""

    def __init__(  # pylint: disable=too-many-arguments  # look, options, mode, callback
        self,
        styles: Styles,
        options: Sequence[str],
        selected: Sequence[str] = (),
        multiple: bool = False,
        on_change: Callable[[list[str]], None] | None = None,
    ) -> None:
        """Add the chips in the current container (they lay out horizontally).

        Args:
            styles: The GUI's themes.
            options: Chip labels (also their values).
            selected: Initially selected chips (the first one if empty and not `multiple`).
            multiple: Several chips may be selected; else exactly one.
            on_change: Called with the selected chips after a click.
        """
        self.styles = styles
        self.options = list(options)
        self.multiple = multiple
        self.on_change = on_change
        self.selected = list(selected) or ([] if multiple or not options else [options[0]])
        self.labels = dict.fromkeys(self.options, "")
        self.items: dict[str, int | str] = {}
        with dpg.group(horizontal=True):
            for option in self.options:
                self.items[option] = dpg.add_button(
                    label=option, callback=lambda _s, _v, chosen: self.click(chosen),
                    user_data=option,
                )  # fmt: skip
        self._restyle()

    def click(self, option: str) -> None:
        """Select a chip (toggle it when `multiple`) and report the selection."""
        if self.multiple:
            if option in self.selected:
                self.selected.remove(option)
            else:
                self.selected.append(option)
        else:
            self.selected = [option]
        self._restyle()
        if self.on_change is not None:
            self.on_change(list(self.selected))

    def set_label(self, option: str, label: str) -> None:
        """Show another text on a chip (e.g. a count: "FW 275"); its value stays."""
        dpg.set_item_label(self.items[option], label)

    def _restyle(self) -> None:
        """Selected chips blue, the others plain."""
        for option, item in self.items.items():
            selected = option in self.selected
            theme = self.styles.primary_button if selected else self.styles.plain_button
            dpg.bind_item_theme(item, theme)


@contextmanager
def path_field(  # pylint: disable=too-many-arguments  # field, folders, filters, look
    styles: Styles,
    width: int,
    project_dir: Path,
    default_folder: str,
    extensions: Sequence[str],
    **options: object,
) -> Iterator[tuple[int | str, FileSelector]]:
    """A path input with a ▾ button that opens the file selector (module docstring).

    The `with` block may add the field's buttons on the same row.

    Yields:
        (the text input, its file selector).
    """
    with dpg.group(horizontal=True, horizontal_spacing=0):
        field = dpg.add_input_text(width=width, **options)  # type: ignore[arg-type]
        selector = FileSelector(field, project_dir, default_folder, extensions,
                                row_theme=styles.row_theme)  # fmt: skip
        # the same arrow button as a combo box's (owner, 2026-10-22), in the combos' colour
        dpg.add_button(arrow=True, direction=dpg.mvDir_Down, callback=selector.open)
        dpg.add_spacer(width=8)
        yield field, selector
