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
from collections.abc import Callable

from PySide6.QtCore import (
    QDate, QEasingCurve, QEvent, QObject, QPoint, QPointF, QPropertyAnimation, Property,
    QRect, QRectF, QSize, Qt, QTimer, Signal,
)
from PySide6.QtGui import (
    QAccessibilityHints, QColor, QFont, QIcon, QPainter, QPainterPath, QPalette, QRegion,
    QTextCharFormat, QTextLayout, QTextOption,
)
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QAbstractSpinBox, QApplication, QButtonGroup, QCalendarWidget,
    QComboBox, QDialog, QFrame, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QListView, QMenu, QPlainTextEdit,
    QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QStyleFactory, QStyleOptionButton,
    QStyleOptionComboBox,
    QStyleOptionViewItem, QStylePainter, QTableView, QTableWidget, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.dates import parse_input_date
from aurum.gui.icons import icon
from aurum.gui.theme import gui_colour

# Tool buttons also serve as text-field affordances and menu controls. Only standalone actions get pills.
_PILL_TOOL_ACTIONS = {
    "budgetAction", "budgetCopy", "catalogueAdd", "catalogueRemove",
    "jobDetails", "jobStop", "journalPostingAction", "manualIconAction", "monthArrow",
    "reportFolderButton", "securitySuggestionAction", "setupIconAction", "statementIconAction",
    "viewerIconAction", "workspaceRefresh",
}


# %% 1. Shared Control Setup ---------------------------------------------------------------------------------
def reduced_motion(widget: QWidget) -> bool:
    """
    Respect the desktop accessibility preference and explicit motion reductions on a control or its window.
    """
    preference = _motion_hints().motionPreference()
    return (preference == Qt.MotionPreference.ReducedMotion
            or bool(widget.property("reducedMotion") or widget.window().property("reducedMotion")))


def watch_reduced_motion(snap: Callable[[], None]) -> None:
    """
    Settle an active effect when the desktop motion preference changes. Pass a bound widget method.
    """
    _motion_hints().motionPreferenceChanged.connect(snap)


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


def show_tooltip(position: QPoint, text: str, owner: QWidget) -> None:
    """
    Display hover help on a translucent window shared by charts, editors and ordinary controls.
    """
    app = QApplication.instance()
    install_controls(app)
    app._aurum_controls.tip.present(position, text, owner)


def hide_tooltip() -> None:
    """
    Dismiss the current hover help when inspection ends.
    """
    style = getattr(QApplication.instance(), "_aurum_controls", None)
    if style is not None:
        style.tip.hide()


def confirm_discard(parent: QWidget, *, question: str = "Discard unsaved changes?") -> bool:
    """
    Ask for an explicit discard decision in one compact, centred dialog shared by every draft surface.
    """
    dialog = _DiscardDialog(parent, question=question)
    try:
        return dialog.exec() == QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()


def confirm_unsaved(parent: QWidget) -> str:
    """
    Return save, discard or cancel from the same centred draft decision surface.
    """
    dialog = _DiscardDialog(parent, question="Save changes before continuing?", allow_save=True)
    try:
        dialog.exec()
        return dialog.decision
    finally:
        dialog.deleteLater()


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


class CentredComboBox(QComboBox):
    """
    Centre text-only choices in their field and menu while retaining native selection and keyboard controls.
    """

    def __init__(self, parent=None) -> None:
        """
        Opt this selector into centred popup rows without changing unrelated dropdowns.
        """
        super().__init__(parent)
        self.setProperty("centredChoices", True)
        self.setEditable(False)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._field_press = False

    def paintEvent(self, event) -> None:
        """
        Reserve equal space beside the label so the trailing arrow cannot offset its visual centre.
        """
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        rectangle = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self,
        )
        inset = max(rectangle.left(), self.width() - rectangle.right() - 1)
        rectangle.setLeft(inset)
        rectangle.setRight(self.width() - inset - 1)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(self.palette().color(group, QPalette.ColorRole.Text))
        text = self.fontMetrics().elidedText(
            option.currentText, Qt.TextElideMode.ElideRight, max(0, rectangle.width()),
        )
        painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, text)

    def mousePressEvent(self, event) -> None:
        """
        Start a click anywhere in the selection-only field without immediately dismissing its popup.
        """
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._field_press = True
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """
        Open choices after a complete field click while leaving native keyboard selection unchanged.
        """
        if event.button() == Qt.MouseButton.LeftButton and self._field_press:
            self._field_press = False
            if self.rect().contains(event.position().toPoint()):
                QTimer.singleShot(0, self.showPopup)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SegmentedControl(QWidget):
    """
    Select one of a short list of options with an accessible animated pill.
    """

    currentIndexChanged = Signal(int)

    def __init__(
        self, labels: tuple[str, ...], name: str, width: int, parent=None, *, label_suffix: str = "",
    ) -> None:
        """
        Give each option an equal share of the control and support keyboard and pointer selection.
        """
        super().__init__(parent)
        self._labels = labels
        self._preferred_width = width
        self.setAccessibleName(name)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(28)
        self.setMinimumWidth(width - 16)
        self._index = 0
        self._selection_rect = QRectF()
        self._buttons = []
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._animation = QPropertyAnimation(self, b"selectionRect", self)
        self._animation.setDuration(160)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(0)
        for index, label in enumerate(labels):
            button = _SegmentButton(label, self)
            button.setFixedHeight(22)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setStyleSheet(
                "QPushButton { background: transparent; border: none; padding: 0; min-height: 0; }"
            )
            button.setCheckable(True)
            button.setAccessibleName(label + label_suffix)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus if index == 0 else Qt.FocusPolicy.ClickFocus)
            button.clicked.connect(lambda _checked=False, selected=index: self.setCurrentIndex(selected))
            button.installEventFilter(self)
            self._buttons.append(button)
            self._group.addButton(button, index)
            row.addWidget(button, 1)
        self._buttons[0].setChecked(True)
        self.setFocusProxy(self._buttons[0])
        watch_reduced_motion(self._snap_indicator)

    def currentIndex(self) -> int:
        """
        Return the index of the selected option.
        """
        return self._index

    def currentText(self) -> str:
        """
        Return the visible label of the selected option.
        """
        return self._labels[self._index]

    def count(self) -> int:
        """
        Return the number of available options.
        """
        return len(self._labels)

    def setCurrentIndex(self, index: int) -> None:
        """
        Update content immediately while retargeting the moving capsule from its current position.
        """
        if not 0 <= index < self.count() or index == self._index:
            return
        self._index = index
        self._buttons[index].setChecked(True)
        for position, button in enumerate(self._buttons):
            button.setFocusPolicy(
                Qt.FocusPolicy.StrongFocus if position == index else Qt.FocusPolicy.ClickFocus
            )
            button.update()
        self.setFocusProxy(self._buttons[index])
        self._move_indicator(animate=True)
        self.currentIndexChanged.emit(index)

    def sizeHint(self) -> QSize:
        """
        Fit the supplied labels in equal, compact pointer targets.
        """
        return QSize(self._preferred_width, 28)

    def eventFilter(self, watched, event) -> bool:
        """
        Move through segments with arrow keys and keep ordinary button activation available.
        """
        if event.type() == QEvent.Type.KeyPress and watched in self._buttons:
            key = event.key()
            offset = -1 if key in {Qt.Key.Key_Left, Qt.Key.Key_Up} else (
                1 if key in {Qt.Key.Key_Right, Qt.Key.Key_Down} else 0
            )
            if offset:
                index = (self._index + offset) % self.count()
                self.setCurrentIndex(index)
                self._buttons[index].setFocus(Qt.FocusReason.OtherFocusReason)
                return True
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        """
        Snap the indicator to resized segment geometry rather than animating a layout change.
        """
        super().resizeEvent(event)
        self.layout().activate()
        self._move_indicator(animate=False)

    def showEvent(self, event) -> None:
        """
        Start from the selected segment without an entrance animation or a blank initial indicator.
        """
        super().showEvent(event)
        self.layout().activate()
        self._move_indicator(animate=False)

    def paintEvent(self, event) -> None:
        """
        Draw one theme-coloured capsule and its contrasting moving selection beneath native buttons.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(gui_colour(self, "accent", "#4d6a6d")))
        painter.drawRoundedRect(QRectF(self.rect()), self.height() / 2, self.height() / 2)
        painter.setBrush(QColor(gui_colour(self, "surface", "#ffffff")))
        rectangle = self._selection_rect
        painter.drawRoundedRect(rectangle, rectangle.height() / 2, rectangle.height() / 2)

    def _move_indicator(self, *, animate: bool) -> None:
        """
        Respect an explicit reduced-motion preference and coalesce rapid selections into one animation.
        """
        target = QRectF(self._buttons[self._index].geometry())
        self._animation.stop()
        if not animate or not self.isVisible() or reduced_motion(self):
            self._set_selection_rect(target)
            return
        self._animation.setStartValue(self._selection_rect if not self._selection_rect.isEmpty() else target)
        self._animation.setEndValue(target)
        self._animation.start()

    def _snap_indicator(self) -> None:
        """
        Settle the selection immediately when the system motion preference changes.
        """
        self._move_indicator(animate=False)

    def _get_selection_rect(self) -> QRectF:
        """
        Supply the current capsule rectangle to Qt's property animation.
        """
        return self._selection_rect

    def _set_selection_rect(self, rectangle: QRectF) -> None:
        """
        Repaint the capsule without changing button layout or selection.
        """
        self._selection_rect = rectangle
        self.update()

    selectionRect = Property(QRectF, _get_selection_rect, _set_selection_rect)


class ReportSegmentedControl(SegmentedControl):
    """
    Present the four saved report types in their established order and compact width.
    """

    LABELS = ("Balance", "Income", "Budget", "Investment")

    def __init__(self, parent=None) -> None:
        """
        Keep the report index and accessible report names used throughout the Reports page.
        """
        super().__init__(self.LABELS, "Report type", 376, parent, label_suffix=" report")
        self.setObjectName("reportSegments")


class _SegmentButton(QPushButton):
    """
    Retain native checkable-button semantics while drawing text over the shared capsule.
    """

    def paintEvent(self, event) -> None:
        """
        Keep labels readable against both selected and unselected theme surfaces without an outline.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        owner = self.parentWidget()
        if self.isEnabled() and (self.hasFocus() or self.underMouse()):
            fill = QColor(gui_colour(self, "hover" if self.isChecked() else "accent_hover", "#dceae2"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(fill)
            rectangle = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(rectangle, rectangle.height() / 2, rectangle.height() / 2)
        role = "text" if self.isChecked() else "on_accent"
        painter.setPen(QColor(gui_colour(owner, role, "#243b3b" if self.isChecked() else "#ffffff")))
        font = QFont("Geist")
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium if self.isChecked() else QFont.Weight.Normal)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())


class PageActionRow(QHBoxLayout):
    """
    Align primary actions within the compact row beneath each page title.
    """

    def setGeometry(self, rectangle: QRect) -> None:
        """
        Keep primary actions at the top of their row and optically lift other buttons.
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
    Give Sync, Process and Generate compact bounds beneath their page titles.
    """

    def __init__(self, label: str, parent=None, *, icon_only: bool = False) -> None:
        """
        Keep the ordinary button signals, text and accessibility while sharing its visual dimensions.
        """
        super().__init__(label, parent)
        self._icon_only = icon_only
        self.setProperty("headerAction", True)
        self.setProperty("primary", True)
        self.setFixedSize(32 if icon_only else 104, 28)
        self.setAccessibleName(label)
        self.setToolTip(label)
        self.setIcon(icon("process", colour="#ffffff", size=16))
        self.setIconSize(QSize(16, 16))

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
        if not self._icon_only:
            option.text = label
            option.rect = QRect(28, 0, self.width() - 36, self.height())
            painter.drawControl(QStyle.ControlElement.CE_PushButtonLabel, option)
        left = (self.width() - 16) // 2 if self._icon_only else 8
        symbol.paint(painter, QRect(left, (self.height() - 16) // 2, 16, 16), Qt.AlignmentFlag.AlignCenter, mode)


def wrapped_text_layout(
    text: str, font: QFont, width: int, alignment=Qt.AlignmentFlag.AlignLeft,
) -> QTextLayout:
    """
    Measure and draw complete metadata with identical wrapping, including long unbroken filenames.
    """
    layout = QTextLayout(text.replace("\n", "\u2028"), font)
    option = QTextOption(alignment & Qt.AlignmentFlag.AlignHorizontal_Mask)
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    height = 0.0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1, width))
        line.setPosition(QPointF(0, height))
        height += line.height()
    layout.endLayout()
    return layout


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
        self._corners = _EntryTableCorners(self)

    def set_empty(self, visible: bool) -> None:
        """
        Reflect the filtered row count while leaving real data and selection unchanged.
        """
        self.empty_message.setGeometry(self.viewport().rect())
        self.empty_message.setVisible(visible)
        self.empty_message.raise_()

    def resizeEvent(self, event) -> None:
        """
        Fit the outer corners and keep the empty message centred in the available viewport.
        """
        super().resizeEvent(event)
        self.empty_message.setGeometry(self.viewport().rect())
        self._corners.setGeometry(self.rect())
        self._corners.raise_()


class _EntryTableCorners(QWidget):
    """
    Smooth the four outer corners over the header, body and scrollbar at the display's pixel density.
    """

    RADIUS = 10

    def __init__(self, table: EntryTable) -> None:
        """
        Let pointer events reach the underlying header and scroll controls.
        """
        super().__init__(table)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setStyleSheet("background: transparent; border: none;")

    def resizeEvent(self, event) -> None:
        """
        Restrict this overlay to four small squares so scrolling can repaint the viewport directly.
        """
        rect = self.rect()
        radius = self.RADIUS
        corners = QRegion(rect).subtracted(QRegion(rect.adjusted(radius, 0, -radius, 0)))
        self.setMask(corners.subtracted(QRegion(rect.adjusted(0, radius, 0, -radius))))

    def paintEvent(self, event) -> None:
        """
        Paint the page outside one continuous rounded outline, including fractional edge pixels.
        """
        path = QPainterPath()
        bounds = QRectF(self.rect())
        path.addRect(bounds)
        path.addRoundedRect(bounds, self.RADIUS, self.RADIUS)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillPath(path, QColor(gui_colour(self, "page", "#f5f7f6")))


class SearchField(QLineEdit):
    """
    Keep the search icon and native text left-aligned with balanced space inside the field.
    """

    def __init__(self, prompt: str, parent=None) -> None:
        """
        Inset a noninteractive icon while retaining native placeholder, cursor and clearing behaviour.
        """
        super().__init__(parent)
        self.setProperty("searchField", True)
        self.setFrame(False)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
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


class DateField(QLineEdit):
    """
    Keep typed accounting dates editable while offering an explicit, non-mutating calendar preview.
    """

    def __init__(self, text: str = "", parent=None) -> None:
        """
        Retain QLineEdit's text API and attach one accessible calendar disclosure.
        """
        super().__init__(text, parent)
        self.setAccessibleName("Accounting date")
        self.setPlaceholderText("YYYY-MM-DD or DD-MM-YYYY")
        self.setToolTip("Accounting date · YYYY-MM-DD or DD-MM-YYYY")
        self.setTextMargins(0, 0, 28, 0)
        self.calendar_button = QToolButton(self)
        self.calendar_button.setObjectName("manualIconAction")
        self.calendar_button.setFixedSize(28, 28)
        self.calendar_button.setIcon(icon("calendar", size=16))
        self.calendar_button.setIconSize(QSize(16, 16))
        self.calendar_button.setToolTip("Choose accounting date")
        self.calendar_button.setAccessibleName("Choose accounting date")
        self.calendar_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.calendar_button.clicked.connect(self.show_calendar)
        self.calendar_popup = _DatePopup(self)

    def show_calendar(self) -> None:
        """
        Preview the entered day without changing text until a day is explicitly chosen.
        """
        if not self.isEnabled() or self.isReadOnly():
            return
        try:
            entered = parse_input_date(self.text())
        except ValueError:
            entered = None
        selected = QDate(entered.year, entered.month, entered.day) if entered else QDate.currentDate()
        self.calendar_popup.prepare(selected)
        position = self.mapToGlobal(QPoint(0, self.height() + 4))
        screen = self.screen().availableGeometry()
        width, height = self.calendar_popup.width(), self.calendar_popup.height()
        position.setX(max(screen.left(), min(position.x(), screen.right() - width + 1)))
        if position.y() + height > screen.bottom():
            position.setY(self.mapToGlobal(QPoint(0, 0)).y() - height - 4)
        position.setY(max(screen.top(), position.y()))
        self.calendar_popup.move(position)
        self.calendar_popup.show()
        self.calendar_popup.calendar.setFocus(Qt.FocusReason.PopupFocusReason)

    def setReadOnly(self, read_only: bool) -> None:
        """
        Apply the same edit guard to typed dates and calendar selection.
        """
        super().setReadOnly(read_only)
        self.calendar_button.setEnabled(not read_only)
        if read_only:
            self.calendar_popup.hide()

    def changeEvent(self, event) -> None:
        """
        Dismiss a calendar when the owning editor disables this field during a job.
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and not self.isEnabled():
            if hasattr(self, "calendar_popup"):
                self.calendar_popup.hide()

    def keyPressEvent(self, event) -> None:
        """
        Provide a keyboard calendar shortcut while retaining ordinary date editing.
        """
        if event.key() == Qt.Key.Key_Down and event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self.show_calendar()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        """
        Keep the disclosure inside the trailing inset without displacing typed dates.
        """
        super().resizeEvent(event)
        self.calendar_button.move(self.width() - 32, (self.height() - 28) // 2)

    def _choose_date(self, value: QDate) -> None:
        """
        Commit one selected date through the same signal path as deliberate typing.
        """
        if self.isEnabled() and not self.isReadOnly():
            self.setText(value.toString("yyyy-MM-dd"))
        self.calendar_popup.hide()
        self.setFocus(Qt.FocusReason.PopupFocusReason)


class _DatePopup(QFrame):
    """
    Offer a quiet month grid with a custom header and no implicit date commits.
    """

    def __init__(self, field: DateField) -> None:
        """
        Keep month navigation separate from the single explicit day-selection action.
        """
        super().__init__(field, Qt.WindowType.Popup)
        self.setObjectName("datePopup")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.previous = self._navigation_button("Previous month", "chevron_left")
        header.addWidget(self.previous)
        self.month = QLabel()
        self.month.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.month, 1)
        self.next = self._navigation_button("Next month", "chevron_right")
        header.addWidget(self.next)
        layout.addLayout(header)
        self.calendar = _DateCalendar(self)
        self.calendar.setNavigationBarVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.calendar.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.calendar.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.calendar.setGridVisible(False)
        self.calendar.setDateEditEnabled(False)
        self.calendar.setFixedSize(294, 226)
        self.calendar.setAccessibleName("Choose accounting date")
        self.calendar.clicked.connect(field._choose_date)
        self.calendar.activated.connect(field._choose_date)
        self.calendar.currentPageChanged.connect(self._show_month)
        self.previous.clicked.connect(self.calendar.showPreviousMonth)
        self.next.clicked.connect(self.calendar.showNextMonth)
        layout.addWidget(self.calendar)
        self.setFixedSize(318, 292)

    def prepare(self, selected: QDate) -> None:
        """
        Reset the preview to the entered month and resolve the owning workspace's current palette.
        """
        surface = gui_colour(self, "surface", "#ffffff")
        foreground = gui_colour(self, "text", "#243b3b")
        muted = gui_colour(self, "muted", "#637a78")
        self.setStyleSheet(
            f"QFrame#datePopup {{ background: {surface}; border: none; border-radius: 12px; }}"
            f"QFrame#datePopup QLabel {{ background: transparent; color: {foreground}; font-weight: 600; }}"
            f"QCalendarWidget {{ background: {surface}; color: {foreground}; }}"
            f"QCalendarWidget QAbstractItemView {{ background: {surface}; color: {muted}; "
            "border: none; outline: none; selection-background-color: transparent; }"
        )
        self.month.setStyleSheet(f"background: transparent; color: {foreground}; font-weight: 600;")
        weekday_format = QTextCharFormat()
        weekday_format.setForeground(QColor(foreground))
        weekday_format.setBackground(QColor(surface))
        self.calendar.setHeaderTextFormat(weekday_format)
        for weekday in (
            Qt.DayOfWeek.Monday, Qt.DayOfWeek.Tuesday, Qt.DayOfWeek.Wednesday, Qt.DayOfWeek.Thursday,
            Qt.DayOfWeek.Friday, Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday,
        ):
            self.calendar.setWeekdayTextFormat(weekday, weekday_format)
        self.calendar.setSelectedDate(selected)
        self.calendar.setCurrentPage(selected.year(), selected.month())
        self._show_month(selected.year(), selected.month())
        self.update()

    def paintEvent(self, event) -> None:
        """
        Paint the native popup's whole backing so translucent header and margin areas remain readable.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(gui_colour(self, "surface", "#ffffff")))
        painter.drawRoundedRect(QRectF(self.rect()), 12, 12)

    def resizeEvent(self, event) -> None:
        """
        Round the native popup window as well as its painted backing surface.
        """
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _show_month(self, year: int, month: int) -> None:
        """
        Update the visible month label without changing the date field.
        """
        self.month.setText(self.calendar.locale().toString(QDate(year, month, 1), "MMMM yyyy"))

    def _navigation_button(self, label: str, symbol: str) -> QToolButton:
        """
        Reuse thin local chevrons with clear keyboard and assistive-technology names.
        """
        button = QToolButton(self)
        button.setObjectName("manualIconAction")
        button.setFixedSize(28, 28)
        button.setIcon(icon(symbol, size=14))
        button.setIconSize(QSize(14, 14))
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return button


class _DateCalendar(QCalendarWidget):
    """
    Retain native calendar interaction while painting clear, rounded day selections.
    """

    def paintCell(self, painter: QPainter, rectangle: QRect, value: QDate) -> None:
        """
        Distinguish the chosen day and today using the owning workspace's colours.
        """
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = value == self.selectedDate()
        current = value.month() == self.monthShown() and value.year() == self.yearShown()
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(gui_colour(self, "accent", "#2e6a61")))
            painter.drawRoundedRect(QRectF(rectangle).adjusted(3, 2, -3, -2), 7, 7)
        role = "on_accent" if selected else "text" if current else "muted"
        painter.setPen(QColor(gui_colour(self, role, "#ffffff" if selected else "#243b3b")))
        font = painter.font()
        font.setBold(selected or value == QDate.currentDate())
        painter.setFont(font)
        painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, str(value.day()))
        painter.restore()


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


class _HoverTip(QLabel):
    """
    Paint a rounded, non-activating tooltip with transparent corners on macOS.
    """

    def __init__(self) -> None:
        """
        Create the transparent native backing before its first show.
        """
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint | Qt.WindowType.WindowTransparentForInput)
        self.setObjectName("aurumHoverTip")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAutoFillBackground(False)
        self.setContentsMargins(10, 6, 10, 6)
        self.setWordWrap(True)
        self.owner = None
        self.surface = QColor()
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def present(self, position: QPoint, text: str, owner: QWidget) -> None:
        """
        Size the text and keep the entire bubble inside the pointer's screen.
        """
        if not text:
            self.hide()
            return
        self.owner = owner
        self.position = position
        self.surface = QColor(gui_colour(owner, "sidebar", "#183f42"))
        self.surface.setAlpha(242)
        ink = gui_colour(owner, "sidebar_text", "#ffffff")
        style = f"QLabel#aurumHoverTip {{ background: transparent; border: none; color: {ink}; }}"
        if self.styleSheet() != style:
            self.setStyleSheet(style)
        self.setFont(QApplication.font("QToolTip"))
        self.setText(text)
        screen = QApplication.screenAt(position) or owner.screen()
        bounds = screen.availableGeometry()
        limit = min(420, bounds.width())
        self.setMaximumWidth(limit)
        self.setWordWrap(False)
        if self.sizeHint().width() >= limit:
            self.setWordWrap(True)
        self.adjustSize()
        x = min(position.x() + 12, bounds.right() - self.width() + 1)
        y = position.y() + 18
        if y + self.height() > bounds.bottom():
            y = position.y() - self.height() - 6
        self.move(max(bounds.left(), x), max(bounds.top(), y))
        self.show()
        self.timer.start(9000)

    def hideEvent(self, event) -> None:
        """
        Release the source widget after the tooltip closes.
        """
        self.owner = None
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        """
        Draw the curved surface once and let the label lay out plain or rich text.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.surface)
        painter.drawRoundedRect(QRectF(self.rect()), 9, 9)
        painter.end()
        super().paintEvent(event)


class _ControlStyle(QObject):
    """
    Apply shared presentation when Qt finishes constructing controls, without replacing their behavior.
    """

    def __init__(self, application: QApplication) -> None:
        """
        Keep the presentation filter alive without sharing widget-owned Qt style objects.
        """
        super().__init__(application)
        self.tip = _HoverTip()

    def eventFilter(self, watched, event) -> bool:
        """
        Prepare newly polished controls and refresh rounded popup masks after geometry changes.
        """
        if watched is self.tip:
            return False
        if event.type() == QEvent.Type.ToolTip and isinstance(watched, QWidget):
            tip = watched.toolTip()
            view = watched.parentWidget()
            if isinstance(view, QHeaderView) and watched is view.viewport():
                index = view.logicalIndexAt(event.pos())
                if index >= 0 and view.model() is not None:
                    tip = view.model().headerData(index, view.orientation(), Qt.ItemDataRole.ToolTipRole) or tip
            elif isinstance(view, QAbstractItemView) and watched is view.viewport():
                tip = view.indexAt(event.pos()).data(Qt.ItemDataRole.ToolTipRole) or tip
            if tip and watched.objectName() != "budgetAllocationChart":
                self.tip.present(event.globalPos(), str(tip), watched)
                return True
        if self.tip.owner is watched and event.type() == QEvent.Type.ToolTipChange and watched.toolTip():
            self.tip.present(self.tip.position, watched.toolTip(), watched)
            return False
        if self.tip.owner is not None and (
            event.type() in {
                QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress, QEvent.Type.ApplicationDeactivate,
            }
            or watched is self.tip.owner and event.type() in {
                QEvent.Type.Hide, QEvent.Type.Leave, QEvent.Type.ToolTipChange, QEvent.Type.Close,
            }
        ):
            self.tip.hide()
        if event.type() in {QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Hide}:
            if isinstance(watched, QTableView) and event.type() in {QEvent.Type.Show, QEvent.Type.Resize}:
                if watched.property("roundedTable"):
                    _round_popup_window(watched, 10)
                _align_table_scrollbar(watched)
            elif isinstance(watched, QWidget) and isinstance(watched.parentWidget(), QTableView):
                table = watched.parentWidget()
                if watched is table.viewport() or watched is table.horizontalHeader():
                    _align_table_scrollbar(table)
        if event.type() in {QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.StyleChange}:
            if isinstance(watched, (QPushButton, QToolButton, QComboBox)):
                self._fit_pill(watched)
            elif isinstance(watched, (QLineEdit, QPlainTextEdit, QTextEdit)):
                self._fit_field(watched)
        if event.type() == QEvent.Type.Polish:
            self.prepare(watched)
        elif event.type() in {QEvent.Type.Show, QEvent.Type.Resize} and (
            isinstance(watched, QMenu) or isinstance(watched, QLabel) and watched.isWindow()
            and watched.windowType() == Qt.WindowType.ToolTip
        ):
            radius = 12 if isinstance(watched, QMenu) else 9
            _round_popup_window(watched, radius)
            if event.type() == QEvent.Type.Show:
                QTimer.singleShot(0, watched, lambda: _round_popup_window(watched, radius))
        elif event.type() in {QEvent.Type.Show, QEvent.Type.Resize} and watched.property("aurumComboPopup"):
            combo = getattr(watched, "_aurum_combo", None)
            if combo is not None:
                if event.type() == QEvent.Type.Show and combo.view().objectName() != "themePickerList":
                    width = max((combo.fontMetrics().horizontalAdvance(combo.itemText(index))
                                 for index in range(combo.count())), default=0)
                    padding = 78 if combo.property("centredChoices") else 58
                    combo.view().setMinimumWidth(max(combo.width(), min(420, width + padding)))
                prepare_combo_popup(combo)
                if event.type() == QEvent.Type.Show:
                    # Qt's own show handler may reset the window mask after the event filter returns.
                    QTimer.singleShot(0, combo, lambda: prepare_combo_popup(combo))
        return False

    def prepare(self, widget) -> None:
        """
        Remove native button outlines and give every ordinary combo the shared menu rows.
        """
        if isinstance(widget, (
            QAbstractButton, QAbstractSpinBox, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit,
        )):
            widget.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        if isinstance(widget, (QPushButton, QToolButton, QComboBox)):
            self._fit_pill(widget)
        if isinstance(widget, QLabel) and widget.isWindow() and widget.windowType() == Qt.WindowType.ToolTip:
            widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            widget.setAutoFillBackground(False)
            return
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
        if listing.objectName() != "themePickerList":
            listing.setItemDelegate(_ChoiceDelegate(widget))
        prepare_combo_popup(widget)

    @staticmethod
    def _fit_field(control: QLineEdit | QPlainTextEdit | QTextEdit) -> None:
        """
        Fit search, Setup and single-line entry fields to their actual bounds.
        """
        if not control.isVisible() or control.objectName() == "budgetAmount":
            return
        if not control.property("searchField") and control.objectName() != "connectionControl":
            parent = control.parentWidget()
            surfaces = {"setupTabs", "reportGroupsDialog", "budgetAccountsPopup"}
            if isinstance(control, QLineEdit):
                surfaces.update({"journalEntryEditor", "manualEntryEditor", "bankEvidencePicker"})
            while parent and parent.objectName() not in surfaces:
                parent = parent.parentWidget()
            if parent is None:
                return
        radius = min(control.width(), control.height()) // 2
        selector = "QLineEdit" if isinstance(control, QLineEdit) else control.metaObject().className()
        padding = "" if isinstance(control, QLineEdit) else f" padding: 8px {max(12, radius // 2)}px;"
        rule = f"\n{selector} {{ border-radius: {radius}px;{padding} }}"
        _replace_pill_rule(control, rule)

    @staticmethod
    def _fit_pill(control: QPushButton | QToolButton | QComboBox) -> None:
        """
        Fit a standalone action or closed selector's fill to its bounds without changing the hit area.
        """
        if not control.isVisible():
            return
        selector = "QAbstractButton"
        if isinstance(control, QComboBox):
            if control.view().objectName() == "themePickerList" or control.isEditable():
                return
            selector = "QComboBox"
        elif type(control) is not QPushButton and not isinstance(control, HeaderActionButton):
            # Custom push buttons also render workflow cards and selector segments.
            if control.objectName() not in _PILL_TOOL_ACTIONS or isinstance(
                control.parent(), (DateField, _DatePopup),
            ):
                return
        radius = min(control.width(), control.height()) // 2
        rule = f"\n{selector} {{ border-radius: {radius}px; }}"
        _replace_pill_rule(control, rule)


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
        padding = 78 if self.combo.property("centredChoices") else 58
        return QSize(option.fontMetrics.horizontalAdvance(str(index.data() or "")) + padding, 34)

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
        centred = bool(self.combo.property("centredChoices"))
        text_rect = option.rect.adjusted(left, 0, -left if centred else -12, 0)
        text = painter.fontMetrics().elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""), Qt.TextElideMode.ElideRight, text_rect.width(),
        )
        alignment = Qt.AlignmentFlag.AlignCenter if centred else (
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        painter.drawText(text_rect, alignment, text)
        painter.restore()


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _motion_hints() -> QAccessibilityHints:
    """
    Keep the parent Qt style hints wrapper alive for the lifetime of its accessibility signals.
    """
    application = QApplication.instance()
    if not hasattr(application, "_aurum_style_hints"):
        application._aurum_style_hints = application.styleHints()
    return application._aurum_style_hints.accessibility()


class _DiscardDialog(QDialog):
    """
    Centre a short draft question and explicit decisions without native message-box icon spacing.
    """

    def __init__(
        self, parent: QWidget, *, question: str = "Discard unsaved changes?", allow_save: bool = False,
    ) -> None:
        """
        Use the owning workspace's colours and make cancellation the initial keyboard action.
        """
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("discardConfirmation")
        self.decision = "cancel"
        self.setWindowTitle(question)
        self.setAccessibleName(question)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        surface = QFrame()
        surface.setObjectName("discardSurface")
        outer.addWidget(surface)
        layout = QVBoxLayout(surface)
        layout.setContentsMargins(24, 26, 24, 22)
        layout.setSpacing(22)
        prompt = QLabel(question)
        prompt.setObjectName("discardQuestion")
        prompt.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prompt.setTextFormat(Qt.TextFormat.PlainText)
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(12)
        actions.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("discardCancel")
        self.cancel_button.setAccessibleName("Cancel discard")
        self.cancel_button.setFixedSize(108, 32)
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.discard_button = QPushButton("Discard")
        self.discard_button.setObjectName("discardAccept")
        self.discard_button.setAccessibleName("Discard unsaved changes")
        self.discard_button.setFixedSize(108, 32)
        self.discard_button.setAutoDefault(False)
        self.discard_button.clicked.connect(lambda: self._choose("discard"))
        actions.addWidget(self.discard_button)
        if allow_save:
            self.save_button = QPushButton("Save")
            self.save_button.setObjectName("discardSave")
            self.save_button.setAccessibleName("Save changes")
            self.save_button.setFixedSize(108, 32)
            self.save_button.setAutoDefault(False)
            self.save_button.clicked.connect(lambda: self._choose("save"))
            actions.addWidget(self.save_button)
        actions.addStretch()
        layout.addLayout(actions)
        colour = lambda role, fallback: gui_colour(parent, role, fallback)
        self.setStyleSheet(
            "QDialog#discardConfirmation { background: transparent; }"
            "QFrame#discardSurface { background: " + colour("surface", "#ffffff")
            + "; border: 1px solid " + colour("border", "#d3ded9") + "; border-radius: 16px; }"
            "QLabel#discardQuestion { color: " + colour("text", "#243b3b")
            + "; background: transparent; font-size: 14px; font-weight: 400; padding: 0; }"
            "QPushButton#discardCancel, QPushButton#discardAccept, QPushButton#discardSave { color: "
            + colour("text", "#243b3b")
            + "; background: " + colour("panel", "#e3eeea")
            + "; border: none; padding: 0; font-size: 13px; font-weight: 400; }"
            "QPushButton#discardCancel:focus, QPushButton#discardAccept:focus, "
            "QPushButton#discardSave:focus { background: "
            + colour("focus", "#d6e6df") + "; }"
            "QPushButton#discardCancel:hover, QPushButton#discardAccept:hover, "
            "QPushButton#discardSave:hover { background: "
            + colour("hover", "#eaf2ee") + "; }"
            "QPushButton#discardCancel:pressed, QPushButton#discardAccept:pressed, "
            "QPushButton#discardSave:pressed { background: "
            + colour("selected", "#d6e6df") + "; }"
        )
        self.setFixedSize(420 if allow_save else 360, self.sizeHint().height())

    def _choose(self, decision: str) -> None:
        """
        Acknowledge only the clicked explicit action; closing and Escape remain cancellation.
        """
        self.decision = decision
        self.accept()

    def showEvent(self, event) -> None:
        """
        Centre the native window over its parent while keeping it within that screen's available area.
        """
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            centre = parent.mapToGlobal(parent.rect().center())
            position = centre - QPoint(self.width() // 2, self.height() // 2)
            screen = parent.screen()
            if screen is not None:
                available = screen.availableGeometry()
                position.setX(max(available.left(), min(position.x(), available.right() - self.width() + 1)))
                position.setY(max(available.top(), min(position.y(), available.bottom() - self.height() + 1)))
            self.move(position)
        self.cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)


def _align_table_scrollbar(table: QTableView) -> None:
    """
    Reserve the header's actual height above a table's scroll track, including after header resizing.
    """
    if not table.isVisible() or table.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
        return
    bar = table.verticalScrollBar()
    container = bar.parentWidget()
    if container is None:
        return
    inset = max(0, table.viewport().mapTo(table, QPoint()).y() - container.mapTo(table, QPoint()).y())
    spacer = getattr(table, "_header_spacer", None)
    if spacer is None:
        if not inset:
            return
        spacer = QWidget(table)
        spacer.setObjectName("tableHeaderGutter")
        spacer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        spacer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        table._header_spacer = spacer
        spacer.setFixedHeight(inset)
        table.addScrollBarWidget(spacer, Qt.AlignmentFlag.AlignTop)
    elif spacer.height() != inset:
        spacer.setFixedHeight(inset)


def _replace_pill_rule(control: QWidget, rule: str) -> None:
    """
    Update only the measured corner treatment, preserving widget-specific style rules.
    """
    style = control.styleSheet()
    if not style.endswith(rule):
        previous = control.property("aurumPillRule") or ""
        control.setProperty("aurumPillRule", rule)
        control.setStyleSheet(style.replace(previous, "", 1) + rule)


def _round_popup_window(widget: QWidget, radius: int) -> None:
    """
    Clip a table or popup and its child surfaces at their shared outer corners.
    """
    path = QPainterPath()
    path.addRoundedRect(QRectF(widget.rect()), radius, radius)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))
    if widget.windowType() == Qt.WindowType.ToolTip:
        widget.setWindowOpacity(1.0)
