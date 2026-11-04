# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                    GUI - Controls                                                    #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Give Aurum's ordinary Qt controls consistent rounded menus and quiet, accessible focus feedback.

Combo boxes retain their native model, selection and keyboard behavior. A shared popup surface and delegate
handle presentation, while buttons use a background change instead of the macOS focus-ring outline.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from PySide6.QtCore import QEvent, QObject, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QApplication, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView,
    QMenu, QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QStyleFactory, QStyleOptionButton,
    QStyleOptionViewItem, QStylePainter, QTableWidget, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.icons import icon
from aurum.gui.theme import gui_colour


# %% 1. Shared Control Setup ---------------------------------------------------------------------------------
def install_controls(application: QApplication) -> None:
    """
    Install presentation defaults once, including controls created later in dialogs and configuration forms.
    """
    if getattr(application, "_aurum_controls", None) is not None:
        return
    styling = _ControlStyle(application)
    application._aurum_controls = styling
    application.installEventFilter(styling)
    for widget in application.allWidgets():
        styling.prepare(widget)


def prepare_combo_popup(combo: QComboBox) -> None:
    """
    Give a combo's real popup one rounded outer surface without changing its items or active selection.
    """
    listing = combo.view()
    popup = listing.window()
    if popup is combo.window():
        return
    combo._aurum_popup = popup
    popup.setProperty("aurumComboPopup", True)
    popup._aurum_combo = combo
    popup.setObjectName("aurumComboPopup")
    popup.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
    popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    popup.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    popup.setAutoFillBackground(False)
    if isinstance(popup, QFrame):
        popup.setFrameShape(QFrame.Shape.NoFrame)
    popup.setStyleSheet(
        "QFrame#aurumComboPopup { background: " + gui_colour(combo, "surface", "#ffffff")
        + "; border: none; border-radius: 12px; padding: 4px; }"
    )
    surface = getattr(popup, "_aurum_surface", None)
    if surface is None:
        surface = _PopupSurface(popup)
        popup._aurum_surface = surface
    surface.setGeometry(popup.rect())
    surface.lower()
    surface.show()
    # A native popup window can remain rectangular underneath a rounded stylesheet background.
    # Clip the window itself so neither its backing surface nor the highlight can leave square corners.
    path = QPainterPath()
    path.addRoundedRect(QRectF(popup.rect()), 12, 12)
    popup.setMask(QRegion(path.toFillPolygon().toPolygon()))


class PageActionRow(QHBoxLayout):
    """
    Align primary page actions optically with subtitles without moving the subtitle itself.
    """

    def setGeometry(self, rectangle: QRect) -> None:
        """
        Keep compact header actions clear of the date range and optically lift other buttons.
        """
        super().setGeometry(rectangle)
        top = rectangle.top() + self.contentsMargins().top()
        for index in range(self.count()):
            widget = self.itemAt(index).widget()
            if isinstance(widget, QAbstractButton):
                lift = 0 if widget.property("headerAction") else 4
                widget.move(widget.x(), top - lift)


class HeaderActionButton(QPushButton):
    """
    Give Process and Generate identical compact bounds and a consistently inset play icon.
    """

    def __init__(self, label: str, parent=None) -> None:
        """
        Keep the ordinary button signals, text and accessibility while sharing its visual dimensions.
        """
        super().__init__(label, parent)
        self.setProperty("headerAction", True)
        self.setProperty("primary", True)
        self.setFixedSize(120, 30)
        self.setIcon(icon("process", colour="#ffffff", size=18))
        self.setIconSize(QSize(18, 18))

    def paintEvent(self, event) -> None:
        """
        Retain styled button feedback while placing the play icon independently of the label's width.
        """
        option = QStyleOptionButton()
        self.initStyleOption(option)
        label, symbol = option.text, QIcon(option.icon)
        option.text, option.icon = "", QIcon()
        painter = QStylePainter(self)
        painter.drawControl(QStyle.ControlElement.CE_PushButton, option)
        mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
        option.text = label
        option.rect = QRect(31, 0, self.width() - 39, self.height())
        painter.drawControl(QStyle.ControlElement.CE_PushButtonLabel, option)
        symbol.paint(painter, QRect(8, 6, 18, 18), Qt.AlignmentFlag.AlignCenter, mode)


class EntryTable(QTableWidget):
    """
    Show an unselectable empty-state message inside the table instead of above its column headings.
    """

    def __init__(self, rows: int, columns: int, parent=None) -> None:
        """
        Overlay the viewport with a quiet message without creating a synthetic table record.
        """
        super().__init__(rows, columns, parent)
        self.empty_message = QLabel("No entries", self.viewport())
        self.empty_message.setObjectName("tableEmptyState")
        self.empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_message.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.empty_message.hide()

    def set_empty(self, visible: bool) -> None:
        """
        Reflect the filtered row count while leaving real data and selection unchanged.
        """
        self.empty_message.setGeometry(self.viewport().rect())
        self.empty_message.setVisible(visible)
        self.empty_message.raise_()

    def resizeEvent(self, event) -> None:
        """
        Keep the empty message centred in the available table viewport as the page changes size.
        """
        super().resizeEvent(event)
        self.empty_message.setGeometry(self.viewport().rect())


class SearchField(QLineEdit):
    """
    Keep the search icon and native text left-aligned with balanced space inside the field.
    """

    def __init__(self, prompt: str, parent=None) -> None:
        """
        Inset a noninteractive icon while retaining native placeholder, cursor and clearing behaviour.
        """
        super().__init__(parent)
        self.setClearButtonEnabled(True)
        self.setAccessibleName(prompt)
        self.setPlaceholderText(prompt)
        self.setTextMargins(28, 0, 0, 0)
        self._symbol = QLabel(self)
        self._symbol.setObjectName("searchFieldIcon")
        self._symbol.setProperty("aurumIconRole", "muted")
        self._symbol.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._symbol.setPixmap(icon("search", "#637a78", size=16).pixmap(16, 16))
        self._symbol.setFixedSize(16, 16)

    def resizeEvent(self, event) -> None:
        """
        Vertically centre the icon at the same left inset for empty and active searches.
        """
        super().resizeEvent(event)
        self._symbol.move(12, (self.height() - self._symbol.height()) // 2)


class QuoteBlock(QWidget):
    """
    Present a short, sourced quotation with a quiet attribution beneath it.
    """

    def __init__(self, text: str, attribution: str, source: str, *, sidebar: bool = False, parent=None):
        """
        Centre selectable text while allowing natural wrapping at the available page or sidebar width.
        """
        super().__init__(parent)
        prefix = "sidebarQuote" if sidebar else "overviewQuote"
        self.setObjectName(prefix)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(8)
        self.quote = QLabel(f"“{text}”")
        self.quote.setObjectName(prefix + "Text")
        self.quote.setWordWrap(True)
        self.attribution = QLabel(attribution)
        self.attribution.setObjectName(prefix + "Attribution")
        self.attribution.setWordWrap(True)
        for label in (self.quote, self.attribution):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setToolTip(source)
            layout.addWidget(label)


# %% 2. Automatic Styling ------------------------------------------------------------------------------------
class _PopupSurface(QWidget):
    """
    Paint the entire menu backing, including the spaces between rounded choice rows.
    """

    def __init__(self, popup: QWidget) -> None:
        """
        Sit below the popup list and leave mouse and keyboard input with the original Qt controls.
        """
        super().__init__(popup)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event) -> None:
        """
        Draw one themed rounded surface without a rectangular native frame underneath it.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(gui_colour(self, "surface", "#ffffff")))
        painter.drawRoundedRect(QRectF(self.rect()), 12, 12)


class _ControlStyle(QObject):
    """
    Apply shared presentation when Qt finishes constructing controls, without replacing their behavior.
    """

    def __init__(self, application: QApplication) -> None:
        """
        Keep the presentation filter alive without sharing widget-owned Qt style objects.
        """
        super().__init__(application)

    def eventFilter(self, watched, event) -> bool:
        """
        Prepare newly polished controls and refresh rounded popup masks after geometry changes.
        """
        if event.type() == QEvent.Type.Polish:
            self.prepare(watched)
        elif event.type() in {QEvent.Type.Show, QEvent.Type.Resize} and isinstance(watched, QMenu):
            _round_menu(watched)
            if event.type() == QEvent.Type.Show:
                QTimer.singleShot(0, watched, lambda: _round_menu(watched))
        elif event.type() in {QEvent.Type.Show, QEvent.Type.Resize} and watched.property("aurumComboPopup"):
            combo = getattr(watched, "_aurum_combo", None)
            if combo is not None:
                if event.type() == QEvent.Type.Show and combo.objectName() != "themePicker":
                    width = max((combo.fontMetrics().horizontalAdvance(combo.itemText(index))
                                 for index in range(combo.count())), default=0)
                    combo.view().setMinimumWidth(max(combo.width(), min(420, width + 58)))
                prepare_combo_popup(combo)
                if event.type() == QEvent.Type.Show:
                    # Qt's own show handler may reset the window mask after the event filter returns.
                    QTimer.singleShot(0, combo, lambda: prepare_combo_popup(combo))
        return False

    def prepare(self, widget) -> None:
        """
        Remove native button outlines and give every ordinary combo the shared menu rows.
        """
        if isinstance(widget, (QAbstractButton, QComboBox)):
            widget.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        if isinstance(widget, QMenu):
            widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            widget.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
            widget.setAutoFillBackground(False)
            return
        if not isinstance(widget, QComboBox) or widget.property("aurumControlReady"):
            return
        widget.setProperty("aurumControlReady", True)
        # Stylesheet proxies can take ownership of the base style. A destroyed configuration field must
        # never dispose of the style another live selector uses, so each styled widget has its own instance.
        widget._aurum_popup_style = QStyleFactory.create("Fusion")
        widget._aurum_popup_style.setParent(widget)
        widget.setStyle(widget._aurum_popup_style)
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        widget.setMaxVisibleItems(10)
        if not isinstance(widget.view(), QListView):
            widget.setView(QListView(widget))
        listing = widget.view()
        listing._aurum_popup_style = QStyleFactory.create("Fusion")
        listing._aurum_popup_style.setParent(listing)
        listing.setStyle(listing._aurum_popup_style)
        listing.setFrameShape(QFrame.Shape.NoFrame)
        listing.setMouseTracking(True)
        listing.setUniformItemSizes(True)
        listing.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        listing.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        listing.setStyleSheet(
            "QListView { background: transparent; border: none; padding: 4px; outline: none; }"
            "QListView::item { padding: 0; border: none; }"
            "QListView QScrollBar:vertical { background: transparent; width: 7px; margin: 4px 0; }"
            "QListView QScrollBar::handle:vertical { border-radius: 3px; "
            "min-height: 28px; }"
            "QListView QScrollBar::add-line:vertical, QListView QScrollBar::sub-line:vertical { height: 0; }"
        )
        listing.viewport().setAutoFillBackground(False)
        if widget.objectName() != "themePicker":
            listing.setItemDelegate(_ChoiceDelegate(widget))
        prepare_combo_popup(widget)


class _ChoiceDelegate(QStyledItemDelegate):
    """
    Paint rounded hover rows and a persistent selected-item tick for ordinary dropdown choices.
    """

    def __init__(self, combo: QComboBox) -> None:
        """
        Read the current index without taking ownership of or changing the combo's model.
        """
        super().__init__(combo.view())
        self.combo = combo

    def sizeHint(self, option, index) -> QSize:
        """
        Reserve comfortable menu rows and enough width for the choice name and selection tick.
        """
        return QSize(option.fontMetrics.horizontalAdvance(str(index.data() or "")) + 58, 34)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """
        Keep hover, active and disabled states readable without borders around individual menu rows.
        """
        active = index == self.combo.model().index(
            self.combo.currentIndex(), self.combo.modelColumn(), self.combo.rootModelIndex(),
        )
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        highlighted = bool(option.state & (QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_Selected))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        role = "selected" if highlighted and enabled else "hover" if active else "surface"
        painter.setBrush(QColor(gui_colour(self.combo, role, "#ffffff")))
        painter.drawRoundedRect(QRectF(option.rect).adjusted(2, 2, -2, -2), 7, 7)
        role = "on_selected" if highlighted and enabled else "text" if enabled else "disabled"
        colour = gui_colour(self.combo, role, "#2e5147" if enabled else "#9caea4")
        if active:
            icon("check", colour, 15).paint(
                painter, QRect(option.rect.left() + 10, option.rect.center().y() - 7, 15, 15),
            )
        left = 33
        decoration = index.data(Qt.ItemDataRole.DecorationRole)
        if isinstance(decoration, QIcon) and not decoration.isNull():
            decoration.paint(painter, QRect(option.rect.left() + left, option.rect.center().y() - 8, 16, 16))
            left += 23
        font = option.font
        font.setWeight(font.Weight.DemiBold if active else font.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(colour))
        text_rect = option.rect.adjusted(left, 0, -12, 0)
        text = painter.fontMetrics().elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""), Qt.TextElideMode.ElideRight, text_rect.width(),
        )
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        painter.restore()


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _round_menu(menu: QMenu) -> None:
    """
    Clip the native action-menu window to its rounded stylesheet surface without changing its actions.
    """
    path = QPainterPath()
    path.addRoundedRect(QRectF(menu.rect()), 12, 12)
    menu.setMask(QRegion(path.toFillPolygon().toPolygon()))
