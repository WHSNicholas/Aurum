# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                   GUI - Theme Picker                                                 #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Present report themes in a compact selector with readable palette previews and native combo-box behavior.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Mapping, Sequence

from PySide6.QtCore import QEvent, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QListView, QSizePolicy, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QToolTip,
)

# Aurum imports
from aurum.gui.icons import icon
from aurum.gui.controls import prepare_combo_popup
from aurum.gui.theme import gui_colour


# %% 1. Theme Selector ---------------------------------------------------------------------------------------
class ThemeComboBox(QComboBox):
    """
    Keep ordinary combo-box data and keyboard controls while drawing a centred label and theme menu.
    """

    def __init__(self, parent=None) -> None:
        """
        Build a borderless selector and a bounded Qt list popup without choosing or loading any theme.
        """
        super().__init__(parent)
        self._palettes = {}
        self.setObjectName("themePicker")
        self.setAccessibleName("Report theme")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(26)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMaxVisibleItems(9)
        self.setStyleSheet(
            "QComboBox { border: none; background: transparent; "
            "padding: 0; combobox-popup: 0; }"
        )
        # A Qt list popup keeps scrolling and row painting consistent when macOS prefers native menus.
        listing = QListView(self)
        listing.setObjectName("themePickerList")
        listing.setAccessibleName("Report themes")
        listing.setFrameShape(QFrame.Shape.NoFrame)
        listing.setMouseTracking(True)
        listing.setUniformItemSizes(True)
        listing.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        listing.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        listing.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        listing.setMinimumWidth(280)
        listing.setMaximumHeight(9 * 36 + 12)
        listing.setStyleSheet(
            "QListView#themePickerList { border: none; background: transparent; "
            "padding: 4px; outline: none; }"
            "QListView#themePickerList QScrollBar:vertical { width: 7px; background: transparent; }"
            "QListView#themePickerList QScrollBar::handle:vertical { border-radius: 3px; min-height: 24px; }"
            "QListView#themePickerList QScrollBar::add-line:vertical, "
            "QListView#themePickerList QScrollBar::sub-line:vertical { height: 0; }"
        )
        self.setView(listing)
        listing.setItemDelegate(_ThemeDelegate(self))
        self.currentIndexChanged.connect(self._refresh_selection)

    def set_theme_palettes(self, palettes: Mapping[str, Sequence[str]]) -> None:
        """
        Replace preview colours without adding items, changing their data or selecting a different theme.
        """
        self._palettes = {
            key: tuple(colour for colour in values if QColor(colour).isValid())
            for key, values in palettes.items()
        }
        self.view().viewport().update()

    def sizeHint(self) -> QSize:
        """
        Prefer the compact width used by Setup's summary card.
        """
        return QSize(216, 26)

    def minimumSizeHint(self) -> QSize:
        """
        Allow a narrow card to elide a long theme name while retaining the full text in the model.
        """
        return QSize(120, 26)

    def showPopup(self) -> None:
        """
        Style the normal Qt popup and let Qt place it within the available screen.
        """
        prepare_combo_popup(self)
        super().showPopup()
        prepare_combo_popup(self)
        self.view().scrollTo(self.view().currentIndex(), QAbstractItemView.ScrollHint.EnsureVisible)

    def paintEvent(self, event) -> None:
        """
        Centre the displayed name over the whole control with equal space beside the right-hand chevron.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isEnabled() and (self.underMouse() or self.hasFocus()):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(gui_colour(self, "hover", "#e3eee7")))
            painter.drawRoundedRect(background, 6, 6)
        text_rect = self.rect().adjusted(24, 0, -24, 0)
        text = self.fontMetrics().elidedText(
            self.currentText(), Qt.TextElideMode.ElideRight, text_rect.width(),
        )
        colour = gui_colour(self, "text" if self.isEnabled() else "disabled", "#31584e")
        painter.setPen(QColor(colour))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, text)
        arrow = QRect(self.width() - 20, (self.height() - 12) // 2, 12, 12)
        icon("chevron_down", colour, 12).paint(painter, arrow)

    def _refresh_selection(self) -> None:
        """
        Refresh the persistent menu checkmark and full-name tooltip after an ordinary selection change.
        """
        self.setToolTip(self.currentText())
        self.update()
        self.view().viewport().update()


# %% 2. Theme Menu Rows --------------------------------------------------------------------------------------
class _ThemeDelegate(QStyledItemDelegate):
    """
    Distinguish the active theme from the hovered menu row and preview each theme's stored palette.
    """

    def __init__(self, combo: ThemeComboBox) -> None:
        """
        Read selection and preview colours from the owning combo without mutating its model.
        """
        super().__init__(combo.view())
        self.combo = combo

    def sizeHint(self, option, index) -> QSize:
        """
        Give every theme a consistent touch-friendly row height and room for its palette preview.
        """
        return QSize(280, 36)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """
        Draw an inset hover row, an independent active checkmark, a readable name and up to five swatches.
        """
        active = index == self.combo.model().index(
            self.combo.currentIndex(), self.combo.modelColumn(), self.combo.rootModelIndex(),
        )
        hover_states = QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Selected
        highlighted = bool(option.state & hover_states)
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        row = QRectF(option.rect).adjusted(3, 2, -3, -2)
        painter.setPen(Qt.PenStyle.NoPen)
        role = "selected" if highlighted and enabled else "hover" if active else "surface"
        painter.setBrush(QColor(gui_colour(self.combo, role, "#ffffff")))
        painter.drawRoundedRect(row, 7, 7)
        role = "on_selected" if highlighted and enabled else "text" if enabled else "disabled"
        colour = gui_colour(self.combo, role, "#2e5147" if enabled else "#9caea4")
        if active:
            check = QRect(option.rect.left() + 11, option.rect.center().y() - 8, 16, 16)
            icon("check", colour, 16).paint(painter, check)
        key = index.data(Qt.ItemDataRole.UserRole)
        palette = self.combo._palettes.get(key, ()) if isinstance(key, str) else ()
        count = min(5, len(palette))
        preview_width = count * 9 + max(0, count - 1) * 3
        right = option.rect.right() - 13
        for number in range(count):
            position = round(number * (len(palette) - 1) / (count - 1)) if count > 1 else 0
            painter.setBrush(QColor(palette[position]))
            swatch = QRectF(right - preview_width + number * 12, option.rect.center().y() - 6, 9, 12)
            painter.drawRoundedRect(swatch, 3, 3)
        text_rect = option.rect.adjusted(35, 0, -(preview_width + 24 if count else 12), 0)
        font = option.font
        font.setWeight(font.Weight.DemiBold if active else font.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(colour))
        text = painter.fontMetrics().elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""), Qt.TextElideMode.ElideRight,
            text_rect.width(),
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()

    def helpEvent(self, event, view, option, index) -> bool:
        """
        Keep the complete theme name available when a narrow menu row elides its visible text.
        """
        if event.type() == QEvent.Type.ToolTip and index.isValid():
            QToolTip.showText(event.globalPos(), str(index.data(Qt.ItemDataRole.DisplayRole)), view)
            return True
        return super().helpEvent(event, view, option, index)
