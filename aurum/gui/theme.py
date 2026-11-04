# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     GUI - Theme                                                      #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Apply saved report colours to one desktop workspace without changing its configuration or accounting.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QAction, QColor, QPalette
from PySide6.QtWidgets import (
    QApplication, QAbstractButton, QAbstractSpinBox, QComboBox, QLabel, QLineEdit, QPlainTextEdit,
    QTextEdit, QWidget,
)

# Aurum imports
from aurum.finance.reports import ReportSettings
from aurum.gui.icons import (
    RefreshButton, Spinner, icon_path, icon_source_colour, recolour_icon, recolour_pixmap,
)

# Keep budget meanings recognisable across every workspace theme.
BUDGET_COLOURS = {"income": "#527ca3", "expense": "#ae6468", "savings": "#5e8a70"}

_ASSETS = {}
_OBSERVER = None
_COLOUR_ROLES = {
    "page": "#f5f7f6",
    "surface": "#ffffff",
    "panel": "#e7efeb #e3eeea #eaf1ed #edf3f0 #edf3ef #f5f8f6 #f0f4f1 #e9efeb #e6ede9",
    "text": "#243b3b #31584e #2e5147",
    "heading": "#235b58 #204e48 #285f53 #365850 #355f58 #426d64 #456a5c",
    "muted": "#637a78 #65766e #4d7069 #47665d #536f64 #587469 #41655d #425f59 #7b8d87",
    "disabled": "#9baaa6 #9ba8a1 #82958b #97aaa1 #9caea4 #a2b3ab",
    "disabled_bg": "#edf2ef #edf1ef #e6ede8 #f2f5f3 #dde3df",
    "border": "#d3ded9 #dde5e2 #e1e7e3 #738d85 #b9c6bf #d7e1dc #e0e8e4 #d0ddd5 #d4e0d9 "
              "#d1ddd6 #d9e4dd #dce5e1 #7da597 #82afa1 #ced8d2",
    "accent": "#2e6a61 #3e7c70 #367a6d #397a68 #3b896c",
    "accent_hover": "#24594f",
    "accent_disabled": "#a6beb6 #94afa5 #c5d7ce #b5c6bc",
    "hover": "#eaf2ee #e0ebe5 #e3eee7 #dceae2 #dce9e3 #e6f0e9 #f3f7f4 #d4e5dc",
    "selected": "#d6e6df #d1e1d8 #d1e4da #c9dfd3 #e2ede7 #cee2d8 #deebe4 #d4e7dc #dcebe3 "
                "#c8ded2 #d0e4da #d2e6da",
    "alternate": "#f0f5f2",
    "scroll": "#bdcec5 #bacdc3 #c3d1c9 #c5ceca",
    "scroll_hover": "#8eafa0 #87aa9b",
    "positive": "#276449",
    "success_bg": "#d8eadd #e0efe4",
    "negative": "#9b332c",
    "danger_bg": "#f8ebe8",
    "warning_bg": "#faf1df #f5eedf #f6eee5",
    "warning_text": "#77612f #8a4b2d #916222",
    "warning_border": "#e3ce9e",
    "icon": "#355653 #3f7168 #7c9a90",
}
_LEGACY_ROLES = {colour: role for role, colours in _COLOUR_ROLES.items() for colour in colours.split()}


# %% 1. Saved Theme Resolution -------------------------------------------------------------------------------
def resolve_gui_theme(settings: ReportSettings) -> dict[str, str]:
    """
    Derive serialisable GUI roles from the same selected appearance used by the PDF renderer.

    This calculation is safe in a read-only workspace worker. Fixed themes ignore the shared mode switch,
    while budget charts keep their blue/red/green meanings independently of report palette overrides.
    """
    theme, colours = settings.theme, settings.mode_colours
    if theme.fixed is not None:
        page, surface = theme.fixed.page, theme.fixed.surface
        divider, group = theme.fixed.divider, theme.fixed.group
    else:
        page = theme.dark_page if settings.dark_mode else theme.light_page
        surface = theme.dark_surface if settings.dark_mode else theme.light_surface
        divider = theme.dark_divider if settings.dark_mode else theme.light_divider
        group = theme.dark_group if settings.dark_mode else theme.light_group
    if settings.colour_scheme == "sunset_hues":
        # Desktop panels use the dusty rose palette shade; PDF group fills keep their saved colour.
        group = theme.palette[min(3, len(theme.palette) - 1)]
    primary = theme.primary or theme.palette[0]
    accent = theme.highlight or theme.palette[1]
    table_header = theme.table_header or theme.palette[2]
    dark = _luminance(page) < 0.18
    panel = _composite(group, surface) if group else _mix(surface, primary, 0.12 if dark else 0.07)
    text = _readable(colours.text, (page, surface, panel))
    muted = _readable(colours.muted, (page, surface, panel))
    heading = _readable(colours.heading_text or primary, (page, surface, panel))
    selected = _mix(surface, accent, 0.28 if dark else 0.18)
    sidebar = primary
    sidebar_text = _readable(colours.on_primary, (sidebar,))
    warning_bg = colours.draft_bg
    roles = {
        "name": settings.colour_scheme, "dark": "true" if dark else "false",
        "page": page, "surface": surface, "panel": panel, "text": text, "muted": muted,
        "heading": heading, "icon": _readable(colours.icon or heading, (page, surface, panel)),
        "accent": accent, "on_accent": _readable(colours.on_highlight, (accent,)),
        "accent_hover": _mix(accent, "#ffffff" if dark else "#000000", 0.12),
        "accent_disabled": _mix(surface, accent, 0.45),
        "border": divider, "hover": _mix(surface, accent, 0.14),
        "selected": selected, "on_selected": _readable(text, (selected,)),
        "alternate": _mix(surface, primary, 0.04),
        "sidebar": sidebar, "sidebar_text": sidebar_text,
        "sidebar_muted": _readable(_mix(sidebar_text, sidebar, 0.25), (sidebar,)),
        "sidebar_hover": _mix(sidebar, sidebar_text, 0.10),
        "sidebar_selected": _mix(sidebar, sidebar_text, 0.18),
        "table_header": table_header,
        "on_table_header": _readable(colours.on_table_header, (table_header,)),
        "disabled": _mix(text, surface, 0.52), "disabled_bg": _mix(surface, page, 0.55),
        "scroll": _mix(surface, text, 0.25), "scroll_hover": _mix(surface, text, 0.45),
        "positive": _readable(colours.positive, (page, surface)),
        "negative": _readable(colours.negative, (page, surface)),
        "success_bg": _mix(surface, colours.positive, 0.15),
        "danger_bg": _mix(surface, colours.negative, 0.15),
        "warning_bg": warning_bg, "warning_text": _readable(colours.draft_text, (warning_bg, page)),
        "warning_border": colours.draft_border,
        "budget_income": BUDGET_COLOURS["income"], "budget_expenses": BUDGET_COLOURS["expense"],
        "budget_savings": BUDGET_COLOURS["savings"],
    }
    return roles


def gui_colour(widget: QObject | None, role: str, fallback: str = "#355653") -> str:
    """
    Read a colour from the owning workspace, including parented dialogs and combo-box popup windows.
    """
    theme = _owning_theme(widget)
    return str(theme.get(role, fallback))


# %% 2. Workspace Application --------------------------------------------------------------------------------
def apply_gui_theme(window: QWidget, theme: dict[str, str]) -> None:
    """
    Apply saved colours to this workspace and its controls without touching another open workspace.

    Repeat calls refresh newly rebuilt Setup widgets without resetting input, selection or scroll position.
    Icons are retinted only when they came from Aurum's local SVG catalogue; report previews stay untouched.
    """
    global _OBSERVER
    if not theme:
        return
    previous = window.property("aurumGuiTheme")
    window.setProperty("aurumGuiTheme", dict(theme))
    if previous != theme:
        window.setPalette(_palette(theme))
        window.setStyleSheet(theme_stylesheet(theme))
    if _OBSERVER is None:
        app = QApplication.instance()
        if app is not None:
            _OBSERVER = _ThemeObserver(app)
            app.installEventFilter(_OBSERVER)
    for widget in [window, *window.findChildren(QWidget)]:
        _refresh_widget(widget, theme, template_owner=window)
    for action in window.findChildren(QAction):
        replacement = recolour_icon(action.icon(), theme["icon"])
        if replacement is not None:
            action.setIcon(replacement)
    window.update()


def theme_stylesheet(theme: dict[str, str]) -> str:
    """
    Reuse the desktop's complete spacing and typography while substituting semantic theme colours.
    """
    source = (Path(__file__).parent / "style.qss").read_text(encoding="utf-8")
    headings = (
        "pageTitle", "workspacePageTitle", "overviewTitle", "setupTitle", "viewerTitle",
        "overviewSection", "setupSectionTitle", "configCollectionTitle", "configDetailTitle",
        "catalogueDetailTitle", "budgetSection", "workspaceSection",
    )
    selectors = ", ".join(f"QLabel#{name}" for name in headings)
    return _themed_styles(source, theme) + f"\n{selectors} {{ color: {theme['heading']}; }}\n"


class _ThemeObserver(QObject):
    """
    Give dialogs and later-created controls their owning workspace's already-applied saved theme.
    """

    def __init__(self, parent: QObject):
        """
        Observe GUI show events without taking ownership of the inspected windows.
        """
        super().__init__(parent)
        self._updating = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """
        Retint a newly displayed control while allowing Qt to deliver its ordinary event unchanged.
        """
        if not self._updating and event.type() == QEvent.Type.Show and isinstance(watched, QWidget):
            theme = _owning_theme(watched)
            if theme:
                self._updating = True
                try:
                    _refresh_widget(watched, theme)
                finally:
                    self._updating = False
        return False


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _owning_theme(widget: QObject | None) -> dict[str, str]:
    """
    Walk ownership rather than active-window state so two workspaces can retain different appearances.
    """
    seen = set()
    while widget is not None and id(widget) not in seen:
        seen.add(id(widget))
        theme = widget.property("aurumGuiTheme")
        if isinstance(theme, dict):
            return theme
        owner = getattr(widget, "_aurum_combo", None)
        widget = owner if owner is not None else widget.parent()
    return {}


def _refresh_widget(widget: QWidget, theme: dict[str, str], *, template_owner: QWidget | None = None) -> None:
    """
    Refresh local stylesheet overrides and registered icons without changing displayed facts or swatches.
    """
    style = widget.styleSheet()
    if widget is not template_owner and widget.property("aurumGuiTheme") is None and style and any(
        name in style for name in ("QComboBox", "QListView", "QScrollBar", "QFrame#aurumComboPopup")
    ):
        original = getattr(widget, "_aurum_style_template", None)
        last = getattr(widget, "_aurum_themed_style", None)
        if original is None or style != last:
            original = style
            widget._aurum_style_template = original
        themed = _themed_styles(original, theme)
        if style != themed:
            widget._aurum_themed_style = themed
            widget.setStyleSheet(themed)
    if widget.isWindow() or isinstance(widget, (
        QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit,
    )):
        # Qt's stylesheet foreground can replace inherited placeholder colours on existing editors.
        widget.setPalette(_palette(theme))
    sidebar = False
    parent = widget
    while parent is not None:
        if parent.objectName() == "sidebar":
            sidebar = True
            break
        parent = parent.parentWidget()
    role = str(widget.property("aurumIconRole") or (
        "sidebar_text" if sidebar else "on_accent" if widget.property("primary") else "icon"
    ))
    tint = theme.get(role, theme["icon"])
    if isinstance(widget, (RefreshButton, Spinner)):
        widget.set_colour(tint)
    elif isinstance(widget, QAbstractButton):
        replacement = recolour_icon(widget.icon(), tint)
        if replacement is not None:
            widget.setIcon(replacement)
    elif isinstance(widget, QLabel):
        pixmap = widget.pixmap()
        if pixmap is not None and not pixmap.isNull():
            source = icon_source_colour(pixmap)
            if not widget.property("aurumIconRole") and source:
                source_role = _LEGACY_ROLES.get(source.lower(), "icon")
                tint = theme.get("icon" if source_role == "accent" else source_role, tint)
            replacement = recolour_pixmap(pixmap, tint)
            if replacement is not None:
                widget.setPixmap(replacement)
    widget.update()


def _palette(theme: dict[str, str]) -> QPalette:
    """
    Supply readable native editors, placeholders and selection colours beneath the stylesheet.
    """
    palette = QPalette()
    roles = {
        "Window": "page", "WindowText": "text", "Base": "surface", "AlternateBase": "alternate",
        "Text": "text", "Button": "surface", "ButtonText": "text", "Highlight": "selected",
        "HighlightedText": "on_selected", "ToolTipBase": "sidebar", "ToolTipText": "sidebar_text",
        "PlaceholderText": "muted", "Link": "heading", "LinkVisited": "muted",
        "Light": "surface", "Midlight": "panel", "Mid": "border", "Dark": "border", "Shadow": "border",
    }
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        for name, role in roles.items():
            colour = theme["disabled"] if group == QPalette.ColorGroup.Disabled and name in {
                "Text", "WindowText", "ButtonText", "PlaceholderText",
            } else theme[role]
            palette.setColor(group, getattr(QPalette.ColorRole, name), QColor(colour))
    return palette


def _themed_styles(source: str, theme: dict[str, str]) -> str:
    """
    Substitute complete colour tokens by selector context, retaining dimensions, assets and typography.
    """
    def rule(match):
        """
        Resolve each declaration using its selector's foreground and background roles.
        """
        selector, body = match.groups()

        def declaration(item):
            """
            Replace colour values without mistaking object names or filenames for colour literals.
            """
            name, separator, value = item.groups()
            value = re.sub(
                r"#[0-9a-fA-F]{6}\b|\bwhite\b",
                lambda colour: _style_colour(selector, name, colour.group(), theme), value,
            )
            return name + separator + value + ";"

        return selector + "{" + re.sub(r"([\w-]+)(\s*:\s*)([^;]+);", declaration, body) + "}"

    result = re.sub(r"([^{}]+)\{([^{}]*)\}", rule, source)
    assets = _theme_assets(theme)
    return re.sub(
        r"url\([\"']?[^)\"']*/?(chevron_down|check)\.svg[\"']?\)",
        lambda match: f'url("{assets[match.group(1)]}")', result,
    )


def _style_colour(selector: str, declaration: str, colour: str, theme: dict[str, str]) -> str:
    """
    Distinguish shared white/teal literals used on buttons, sidebars, tables and ordinary surfaces.
    """
    foreground = declaration in {"color", "selection-color"}
    if declaration == "selection-background-color":
        return theme["selected"]
    if declaration == "selection-color":
        return theme["on_selected"]
    if '#overviewNext[complete="true"]' in selector or '#overviewStage[tone="done"]' in selector:
        return theme["sidebar_text" if foreground else "sidebar"]
    sidebar = any(name in selector for name in (
        "#sidebar", "#navigation", "#brand", "#tagline", "#workspaceLabel", "#ownerLabel", "#openWorkspace",
    ))
    if sidebar or "QToolTip" in selector:
        if foreground:
            return theme["sidebar_muted" if any(name in selector for name in (
                "#tagline", "#workspaceLabel", "#sidebarQuoteAttribution", ":disabled",
            )) else "sidebar_text"]
        return theme["sidebar_selected" if ":selected" in selector or ":pressed" in selector else
                     "sidebar_hover" if ":hover" in selector or ":focus" in selector else "sidebar"]
    if "QHeaderView" in selector:
        role = "on_table_header" if foreground else "border" if "border" in declaration else "table_header"
        return theme[role]
    primary = 'primary="true"' in selector or selector.strip() == "QTabBar::tab:selected" or (
        "QCheckBox::indicator:checked" in selector
    )
    if primary:
        return theme["on_accent" if foreground else "accent_disabled" if ":disabled" in selector else
                     "accent_hover" if ":hover" in selector or ":focus" in selector else "accent"]
    key = colour.lower()
    if key in {"white", "#ffffff"}:
        return theme["text" if foreground else "surface"]
    role = _LEGACY_ROLES.get(key)
    if foreground and ":selected" in selector:
        if "QTabBar" in selector and any(name in selector for name in (
            "#setupTabs", "#statementTabs", "#manualTabs", "#caseTabs", "#reportsTabs",
        )):
            return theme["heading"]
        return theme["on_selected"]
    if role == "accent" and foreground:
        role = "heading"
    return theme.get(role, colour) if role else colour


def _theme_assets(theme: dict[str, str]) -> dict[str, str]:
    """
    Keep small tinted Qt stylesheet arrows and checkbox ticks in temporary, process-owned files.
    """
    assets = tuple(
        (name, icon_path(name), colour)
        for name, colour in (("chevron_down", theme["icon"]), ("check", theme["on_accent"]))
    )
    key = tuple((str(path), colour) for _, path, colour in assets)
    if key not in _ASSETS:
        temporary = TemporaryDirectory(prefix="aurum-theme-icons-")
        directory = Path(temporary.name)
        for name, path, colour in assets:
            source = path.read_text(encoding="utf-8")
            (directory / f"{name}.svg").write_text(source.replace("currentColor", colour), encoding="utf-8")
        _ASSETS[key] = temporary, {name: str(directory / f"{name}.svg") for name in ("chevron_down", "check")}
    return _ASSETS[key][1]


def _channels(colour: str) -> tuple[int, int, int]:
    """
    Read validated six-digit report colours without creating any GUI object in the workspace worker.
    """
    return tuple(int(colour[index:index + 2], 16) for index in (1, 3, 5))


def _mix(first: str, second: str, weight: float) -> str:
    """
    Blend a surface and accent for quiet interactive states while retaining the selected hue.
    """
    return "#" + "".join(f"{round(a * (1 - weight) + b * weight):02x}" for a, b in zip(
        _channels(first), _channels(second),
    ))


def _composite(colour: str, background: str) -> str:
    """
    Composite report alpha colours into an opaque Qt background with the same visible appearance.
    """
    return _mix(background, colour[:7], int(colour[7:9], 16) / 255) if len(colour) == 9 else colour


def _luminance(colour: str) -> float:
    """
    Measure relative luminance for text contrast decisions on light and fixed dark themes.
    """
    channels = [value / 255 for value in _channels(colour)]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))


def _contrast(first: str, second: str) -> float:
    """
    Compare two opaque colours using the standard relative-luminance contrast ratio.
    """
    a, b = _luminance(first), _luminance(second)
    return (max(a, b) + .05) / (min(a, b) + .05)


def _readable(colour: str, backgrounds: tuple[str, ...]) -> str:
    """
    Retain configured text where readable, otherwise move its shade toward the better neutral endpoint.
    """
    if min(_contrast(colour, background) for background in backgrounds) >= 4.5:
        return colour
    target = max(("#ffffff", "#000000"), key=lambda value: min(
        _contrast(value, background) for background in backgrounds
    ))
    for step in range(1, 21):
        candidate = _mix(colour, target, step / 20)
        if min(_contrast(candidate, background) for background in backgrounds) >= 4.5:
            return candidate
    return target
