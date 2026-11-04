# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     GUI - Icons                                                      #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Supply themed local icons, a compact refresh control and desktop activity indicators.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QApplication, QStyle, QStyleOptionToolButton, QStylePainter, QToolButton, QWidget,
)

ICON_NAMES = (
    "home", "settings", "chat", "document", "checklist", "pen", "ledger", "chart", "wallet", "cash",
    "arrow_down_left", "arrow_up_right", "vault", "import", "process", "refresh", "chevron_left",
    "chevron_right", "folder", "person", "check", "error", "clock", "details", "add", "remove",
    "save", "login", "copy", "chevron_up", "chevron_down", "search", "close", "fit",
)
# Switch to "outline" and restart Aurum to restore the unchanged original icons.
ICON_SET = "solar_bold"
_ICON_ROOT = Path(__file__).parent / "assets"
_ICON_FOLDERS = {"solar_bold": "solar_bold", "outline": "ui"}
_DISCLOSURE_ICONS = {"chevron_left", "chevron_right", "chevron_up", "chevron_down"}
_ICON_SOURCES = {}
_PIXMAP_SOURCES = {}
_ALIASES = {
    "overview": "home", "setup": "settings", "chatgpt": "chat", "connection": "chat",
    "statements": "document", "statement": "document", "review": "checklist",
    "manual": "pen", "manual_entries": "pen", "journal": "ledger", "reports": "chart", "report": "chart",
    "networth": "wallet", "net_worth": "wallet", "income": "arrow_down_left",
    "expenses": "arrow_up_right", "expense": "arrow_up_right", "savings": "vault",
    "progress": "clock", "left": "chevron_left", "right": "chevron_right", "workspace": "folder",
    "user": "person", "success": "check", "warning": "error", "info": "details",
    "up": "chevron_up", "down": "chevron_down",
}


# %% 1. Local Icons ------------------------------------------------------------------------------------------
def icon_path(name: str) -> Path:
    """
    Resolve a local asset while retaining the thin open chevrons used for month and field navigation.

    Solar Bold's alternative arrows are filled triangles, so these four controls retain Aurum's existing
    chevrons. The complete original set remains in assets/ui for an explicit switch back to outline.
    """
    if name not in ICON_NAMES:
        raise ValueError(f"Unknown desktop icon: {name}")
    if ICON_SET not in _ICON_FOLDERS:
        raise ValueError(f"Unknown desktop icon set: {ICON_SET}")
    folder = "ui" if name in _DISCLOSURE_ICONS else _ICON_FOLDERS[ICON_SET]
    return _ICON_ROOT / folder / f"{name}.svg"


def icon(name: str, colour: str = "#355653", size: int = 20) -> QIcon:
    """
    Return a tinted local icon with crisp standard and Retina representations.

    Names accept the asset names in ICON_NAMES and familiar page names such as overview or journal.
    A QApplication must already exist because Qt pixmaps belong to the desktop's GUI thread.
    """
    if not isinstance(QApplication.instance(), QApplication):
        raise RuntimeError("Create the desktop QApplication before requesting an icon.")
    canonical = name.strip().lower().replace("-", "_").replace(" ", "_")
    canonical = _ALIASES.get(canonical, canonical)
    if canonical not in ICON_NAMES:
        raise ValueError(f"Unknown desktop icon: {name}")
    if not isinstance(size, int) or size < 1:
        raise ValueError("Icon size must be a positive whole number of pixels.")
    tint = QColor(colour)
    if not tint.isValid():
        raise ValueError(f"Invalid icon colour: {colour}")
    return _LocalIcon(canonical, tint.name(QColor.NameFormat.HexArgb), size, colour)


def recolour_icon(value: QIcon, colour: str) -> QIcon | None:
    """
    Retint a registered local icon while retaining its asset name and original semantic colour.
    """
    source = _ICON_SOURCES.get(value.cacheKey())
    return _LocalIcon(source[0], colour, source[2], source[1]) if source else None


def recolour_pixmap(value: QPixmap, colour: str) -> QPixmap | None:
    """
    Retint a local icon used as a label pixmap without changing unrelated images or theme swatches.
    """
    source = _PIXMAP_SOURCES.get(value.cacheKey())
    if source is None:
        return None
    size = round(value.width() / value.devicePixelRatio())
    return _LocalIcon(source[0], colour, source[2], source[1]).pixmap(size, size)


def icon_source_colour(value: QIcon | QPixmap) -> str | None:
    """
    Identify the original tint of a registered icon independently of the last applied workspace theme.
    """
    sources = _PIXMAP_SOURCES if isinstance(value, QPixmap) else _ICON_SOURCES
    source = sources.get(value.cacheKey())
    return source[1] if source else None


class _LocalIcon(QIcon):
    """
    Retain local SVG provenance when application code turns an icon into a QLabel pixmap.
    """

    def __init__(self, name: str, colour: str, size: int, source_colour: str):
        """
        Share cached rendered assets while remembering the replaceable source and its original tint.
        """
        super().__init__(_cached_icon(name, colour, size))
        self._source = name, source_colour, size
        _ICON_SOURCES[self.cacheKey()] = self._source

    def pixmap(self, *args, **kwargs) -> QPixmap:
        """
        Remember generated label pixmaps so a later workspace refresh can recolour them safely.
        """
        result = super().pixmap(*args, **kwargs)
        _PIXMAP_SOURCES[result.cacheKey()] = self._source
        return result


# %% 2. Refresh Control --------------------------------------------------------------------------------------
class RefreshButton(QToolButton):
    """
    Show a circular refresh arrow at rest and an animated arc while the workspace is loading.
    """

    def __init__(self, parent=None, *, size: int = 32, colour: str = "#355653") -> None:
        """
        Create a keyboard-accessible icon button with a timer owned by the widget.
        """
        super().__init__(parent)
        if not isinstance(size, int) or size < 24:
            raise ValueError("Refresh button size must be at least 24 whole pixels.")
        self._colour = QColor(colour)
        if not self._colour.isValid():
            raise ValueError(f"Invalid refresh colour: {colour}")
        self._loading = False
        self._angle = 0
        self._icon_size = max(14, min(24, round(size * 0.56)))
        self.setObjectName("workspaceRefresh")
        self.setFixedSize(size, size)
        self.setIconSize(QSize(self._icon_size, self._icon_size))
        self.setIcon(icon("refresh", colour=colour, size=self._icon_size))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Refresh workspace")
        self.setAccessibleDescription("Reload the workspace without changing saved data.")
        self.setToolTip("Refresh workspace")
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)

    def set_loading(self, loading: bool) -> None:
        """
        Update the activity indicator while preserving the caller's enabled or disabled state.
        """
        self._loading = bool(loading)
        self.setToolTip("Refreshing workspace…" if self._loading else "Refresh workspace")
        self.setAccessibleDescription(
            "The workspace is refreshing." if self._loading
            else "Reload the workspace without changing saved data."
        )
        if not self._loading:
            self._angle = 0
        self._update_animation()
        self.update()

    def set_colour(self, colour: str) -> None:
        """
        Recolour both the resting icon and animated arc after a workspace theme refresh.
        """
        self._colour = QColor(colour)
        self.setIcon(icon("refresh", colour, self._icon_size))
        self.update()

    def showEvent(self, event) -> None:
        """
        Resume animation when an active refresh becomes visible again.
        """
        super().showEvent(event)
        self._update_animation()

    def hideEvent(self, event) -> None:
        """
        Stop animation immediately while the control or its window is hidden.
        """
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        """
        Draw the native button frame and a centred icon independent of stylesheet padding.
        """
        painter = QStylePainter(self)
        option = QStyleOptionToolButton()
        self.initStyleOption(option)
        option.icon = QIcon()
        option.text = ""
        painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton, option)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = self.rect().center()
        if not self._loading:
            left = round(centre.x() - self._icon_size / 2)
            top = round(centre.y() - self._icon_size / 2)
            mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
            self.icon().paint(
                painter, QRect(left, top, self._icon_size, self._icon_size),
                Qt.AlignmentFlag.AlignCenter, mode,
            )
            return
        radius = self._icon_size / 2 - 1
        ring = QRectF(centre.x() - radius, centre.y() - radius, radius * 2, radius * 2)
        track = QColor(self._colour)
        track.setAlphaF(track.alphaF() * 0.16)
        painter.setPen(QPen(track, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring)
        painter.setPen(QPen(
            self._colour, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
        ))
        painter.drawArc(ring, -self._angle * 16, 240 * 16)

    def _update_animation(self) -> None:
        """
        Run the timer only for visible loading controls; parent ownership handles destruction.
        """
        if self._loading and self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def _advance(self) -> None:
        """
        Rotate the activity arc without changing the button's text, size or action.
        """
        self._angle = (self._angle + 18) % 360
        self.update()


# %% 3. Activity Indicator -----------------------------------------------------------------------------------
class Spinner(QWidget):
    """
    Show a thin animated arc for ongoing work without presenting another clickable action.
    """

    def __init__(
        self, parent=None, *, size: int = 22, colour: str = "#355653", label: str = "Working",
    ) -> None:
        """
        Create a compact, named indicator whose timer belongs to the widget.
        """
        super().__init__(parent)
        if not isinstance(size, int) or size < 16:
            raise ValueError("Spinner size must be at least 16 whole pixels.")
        self._colour = QColor(colour)
        if not self._colour.isValid():
            raise ValueError(f"Invalid spinner colour: {colour}")
        self._running = False
        self._angle = 0
        self.setObjectName("workspaceSpinner")
        self.setFixedSize(size, size)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAccessibleName(label)
        self.setAccessibleDescription("Idle.")
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._advance)

    def set_running(self, running: bool) -> None:
        """
        Animate visible work and clear the indicator when that work finishes.
        """
        self._running = bool(running)
        self.setAccessibleDescription("Work is in progress." if self._running else "Idle.")
        if not self._running:
            self._angle = 0
        self._update_animation()
        self.update()

    def set_colour(self, colour: str) -> None:
        """
        Recolour an in-progress activity arc without changing its running state.
        """
        self._colour = QColor(colour)
        self.update()

    def showEvent(self, event) -> None:
        """
        Resume animation when ongoing work becomes visible again.
        """
        super().showEvent(event)
        self._update_animation()

    def hideEvent(self, event) -> None:
        """
        Pause immediately while the indicator or its parent window is hidden.
        """
        self._timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        """
        Paint a centred arc and quiet circular track while work is active.
        """
        if not self._running:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        centre = QRectF(self.rect()).center()
        radius = min(self.width(), self.height()) / 2 - 3
        ring = QRectF(centre.x() - radius, centre.y() - radius, radius * 2, radius * 2)
        track = QColor(self._colour)
        track.setAlphaF(track.alphaF() * 0.16)
        painter.setPen(QPen(track, 1.8))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(ring)
        painter.setPen(QPen(
            self._colour, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
        ))
        painter.drawArc(ring, -self._angle * 16, 240 * 16)

    def _update_animation(self) -> None:
        """
        Run only while visible and active; widget ownership cancels the timer on destruction.
        """
        if self._running and self.isVisible():
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()

    def _advance(self) -> None:
        """
        Rotate the arc without moving the widget or changing its size.
        """
        self._angle = (self._angle + 18) % 360
        self.update()


# %% 4. Helpers ----------------------------------------------------------------------------------------------
@lru_cache(maxsize=256)
def _cached_icon(name: str, colour: str, size: int) -> QIcon:
    """
    Render replaceable currentColor SVG assets once per colour and logical size.
    """
    tint = QColor(colour)
    source = icon_path(name).read_bytes()
    source = source.replace(b"currentColor", tint.name(QColor.NameFormat.HexRgb).encode("ascii"))
    renderer = QSvgRenderer(QByteArray(source))
    if not renderer.isValid():
        raise ValueError(f"The desktop icon is not a valid SVG: {name}")
    result = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(size * scale, size * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(tint.alphaF())
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        result.addPixmap(pixmap)
    return result
