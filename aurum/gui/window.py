# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                    GUI - Window                                                      #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Run Aurum's desktop workspace with monthly navigation and one background operation at a time.

The window coordinates pages and shared services. It protects unsaved form edits, displays concise progress
and errors, and leaves completed accounting intact when the user asks to stop. Financial logic stays in the
existing workflows rather than being duplicated in buttons or table cells.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import date
from pathlib import Path
import sqlite3

from PySide6.QtCore import (
    QDate, QEasingCurve, QEvent, QRect, QSize, QThreadPool, QTimer, Qt, QVariantAnimation, Signal,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QStackedWidget,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.activity import ActivityPage, JournalPage, ReviewPage, TransactionsPage
from aurum.gui.analysis import AnalysisPage
from aurum.gui.background import Job, error_message
from aurum.gui.controls import (
    QuoteBlock, SegmentedControl, confirm_unsaved, reduced_motion, watch_reduced_motion,
)
from aurum.gui.editor import JournalEditor
from aurum.gui.icons import RefreshButton, icon
from aurum.gui.manual_editor import ManualEditor
from aurum.gui.services import WorkspaceService
from aurum.gui.setup import SetupPage
from aurum.gui.theme import apply_gui_theme, gui_colour
from aurum.gui.workspace import OverviewPage, ReportsPage
from aurum.finance.reports import ReportSettings, resolve_period
from aurum.workflows.configuration import read_configuration

_SETUP_ICONS = {
    "accounts": "accounts", "securities": "securities", "budget": "budget", "reports": "report_settings",
}


# %% 1. Desktop Window ---------------------------------------------------------------------------------------
class MainWindow(QMainWindow):
    """
    Keep the selected reporting month, workspace and background job shared across all desktop screens.
    """

    def __init__(self, workspace: Path) -> None:
        """
        Build the desktop without writing configuration, submitting answers or starting authentication.
        """
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.service = WorkspaceService(workspace)
        self.setWindowTitle("Aurum")
        self.resize(1320, 920)
        self.setMinimumSize(1050, 740)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.job = None
        self.snapshot = {}
        self._all_activity = False
        self._activity_history = None
        self._activity_link = None
        self._active_action = ""
        self._busy = False
        self._owner_document = None
        self._result = None
        self._failed = False
        self._status_error = False
        self._continuation = None
        self._closing = False
        self._selected_page = 0
        self.editor = None
        self._last_message = "Workspace ready."
        self._startup_sync_pending = True
        self._analysis_pending = False
        self._analysis_has_securities = False
        self._analysis_price_start = None
        try:
            self.month = self.service.default_month()
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            self.month = date.today().strftime("%Y-%m")
        container = QWidget()
        self.setCentralWidget(container)
        container.installEventFilter(self)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 24, 16, 16)
        side.setSpacing(8)
        brand_header = QVBoxLayout()
        brand_header.setContentsMargins(0, 0, 0, 0)
        brand_header.setSpacing(0)
        brand = QLabel("Aurum")
        brand.setObjectName("brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_header.addWidget(brand)
        tagline = QLabel("PERSONAL FINANCE")
        tagline.setObjectName("tagline")
        tagline.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        brand_header.addWidget(tagline)
        side.addLayout(brand_header)
        side.addSpacing(16)
        self.navigation = _NavigationList()
        self.navigation.setObjectName("navigation")
        self.navigation.setItemDelegate(_NavigationDelegate(self.navigation))
        self.navigation.setIconSize(QSize(18, 18))
        self.navigation.setSpacing(2)
        self.names = [
            "Overview", "Transactions", "Review", "Journal", "Reports", "Analysis", "Setup",
        ]
        self._navigation_rows = {}
        self._setup_rows = {}
        for name in self.names:
            item = QListWidgetItem(icon(name, colour="#cfe1da", size=18), name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setSizeHint(QSize(0, 40))
            self._navigation_rows[name] = self.navigation.count()
            self.navigation.addItem(item)
            if name == "Setup":
                for kind, symbol in _SETUP_ICONS.items():
                    label = kind.title()
                    child = QListWidgetItem(icon(symbol, colour="#aac9bd", size=16), label)
                    child.setData(Qt.ItemDataRole.UserRole, "Setup")
                    child.setData(Qt.ItemDataRole.UserRole + 1, kind)
                    child.setForeground(QColor("#bdd6cc"))
                    child.setSizeHint(QSize(0, 32))
                    self._setup_rows[kind] = self.navigation.count()
                    self.navigation.addItem(child)
        self._navigation_rows["ChatGPT"] = self._setup_rows["accounts"]
        side.addWidget(self.navigation)
        side.addSpacing(12)
        side.addStretch(1)
        side.addWidget(QuoteBlock(
            "For where your treasure is, there will your heart be also.", "Matthew 6:21",
            "https://www.biblegateway.com/passage/?search=Matthew+6%3A21&version=KJV", sidebar=True,
        ))
        side.addStretch(1)
        side.addSpacing(12)
        footer_divider = QFrame()
        footer_divider.setObjectName("sidebarFooterDivider")
        footer_divider.setFixedHeight(1)
        side.addWidget(footer_divider)
        workspace_footer = QHBoxLayout()
        workspace_footer.setContentsMargins(0, 0, 0, 0)
        workspace_footer.setSpacing(8)
        self.owner_label = _SidebarName()
        self.owner_label.edit_requested.connect(self._begin_owner_edit)
        self.owner_label.save_requested.connect(self._save_owner_name)
        self.owner_label.edit_cancelled.connect(self._cancel_owner_edit)
        workspace_footer.addWidget(self.owner_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.refresh_button = RefreshButton(colour="#cfe1da")
        self.refresh_button.clicked.connect(self.refresh)
        workspace_footer.addWidget(self.refresh_button, 0, Qt.AlignmentFlag.AlignVCenter)
        side.addLayout(workspace_footer)
        layout.addWidget(sidebar)
        body = QVBoxLayout()
        body.setContentsMargins(28, 24, 28, 18)
        body.setSpacing(16)
        self.month_bar = QWidget()
        self.month_bar.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        bar = _MonthLayout(self.month_bar)
        # Lift the compact month controls to the visual centre of the Cormorant heading.
        bar.setContentsMargins(0, 6, 0, 0)
        bar.setHorizontalSpacing(6)
        bar.setVerticalSpacing(0)
        self.month_range = QLabel()
        self.month_range.setObjectName("monthRange")
        self.month_range.setTextFormat(Qt.TextFormat.PlainText)
        self.month_range.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.month_range.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.month_label = QLabel()
        self.month_label.setObjectName("monthLabel")
        self.month_label.setTextFormat(Qt.TextFormat.RichText)
        self.month_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.month_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        bar.addWidget(self.month_label, 0, 0)
        self.previous_month = QToolButton()
        self.previous_month.setObjectName("monthArrow")
        self.previous_month.setIcon(icon("left", size=14))
        self.previous_month.setIconSize(QSize(14, 14))
        self.previous_month.setFixedSize(28, 28)
        self.previous_month.setToolTip("Previous reporting month")
        self.previous_month.setAccessibleName("Previous reporting month")
        self.previous_month.clicked.connect(lambda: self._step_month(-1))
        bar.addWidget(self.previous_month, 0, 1)
        self.next_month = QToolButton()
        self.next_month.setObjectName("monthArrow")
        self.next_month.setIcon(icon("right", size=14))
        self.next_month.setIconSize(QSize(14, 14))
        self.next_month.setFixedSize(28, 28)
        self.next_month.setToolTip("Next reporting month")
        self.next_month.setAccessibleName("Next reporting month")
        self.next_month.clicked.connect(lambda: self._step_month(1))
        bar.addWidget(self.next_month, 0, 2)
        self._update_month_controls()
        bar.addWidget(self.month_range, 1, 0, Qt.AlignmentFlag.AlignRight)
        self.activity_scope = SegmentedControl(("1M", "All"), "Activity date range", 108, self)
        self.activity_scope.setToolTip("View the selected reporting month or all saved activity.")
        self.activity_scope._buttons[0].setAccessibleName("Selected reporting month")
        self.activity_scope._buttons[1].setAccessibleName("All saved dates")
        self.activity_scope.currentIndexChanged.connect(self._activity_scope_changed)
        self.activity_scope.hide()
        self.stack = QStackedWidget()
        self.pages = {
            "Overview": OverviewPage(), "Analysis": AnalysisPage(), "Setup": SetupPage(workspace),
            "Transactions": TransactionsPage(), "Review": ReviewPage(),
            "Journal": JournalPage(), "Reports": ReportsPage(),
        }
        for name in self.names:
            page = self.pages[name]
            self.stack.addWidget(page)
            if hasattr(page, "action_requested"):
                page.action_requested.connect(self._action)
        self.pages["ChatGPT"] = self.pages["Setup"].chatgpt
        self.pages["Transactions"].set_statement_folder(str(self.service.data / "bank_statements"))
        self.pages["ChatGPT"].action_requested.connect(self._action)
        self.pages["ChatGPT"].cancel_requested.connect(self._stop)
        self.pages["Setup"].bank_connection.action_requested.connect(self._action)
        self.pages["Setup"].bank_connection.cancel_requested.connect(self._stop)
        self.pages["Setup"].leave_guard = self._guard_changes
        self.pages["Setup"].changed.connect(self._configuration_saved)
        self.pages["Setup"].section_changed.connect(self._sync_setup_section)
        self.pages["Setup"].section_labels_changed.connect(self._setup_section_labels)
        self.pages["Setup"].set_reporting_month(self.month)
        self.pages["Overview"].navigate.connect(self.navigate)
        self.pages["Analysis"].ranges.currentIndexChanged.connect(self._load_analysis_if_needed)
        body.addWidget(self.stack, 1)
        self.navigation.currentRowChanged.connect(self._navigate_index)
        self.navigation.setCurrentRow(self._navigation_rows["Overview"])
        self.status_bar = QWidget()
        status = QHBoxLayout(self.status_bar)
        status.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("")
        self.status.setObjectName("workspaceActivityStatus")
        self.status.setWordWrap(True)
        status.addWidget(self.status, 1)
        status.addWidget(self.pages["Setup"].footer, 1)
        self.pages["Setup"].footer.hide()
        self.stop_button = QToolButton()
        self.stop_button.setObjectName("jobStop")
        self.stop_button.setIcon(icon("stop", size=18))
        self.stop_button.setIconSize(QSize(18, 18))
        self.stop_button.setFixedSize(32, 32)
        self.stop_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.stop_button.setToolTip("Stop after current step")
        self.stop_button.setAccessibleName("Stop after current step")
        self.stop_button.clicked.connect(self._stop)
        self.stop_button.hide()
        status.addWidget(self.stop_button)
        self.details_button = QToolButton()
        self.details_button.setObjectName("jobDetails")
        self.details_button.setIcon(icon("details", size=18))
        self.details_button.setIconSize(QSize(18, 18))
        self.details_button.setFixedSize(32, 32)
        self.details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.details_button.setToolTip("Show activity details")
        self.details_button.setAccessibleName("Activity details")
        self.details_button.setCheckable(True)
        status.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignRight)
        body.addWidget(self.status_bar)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(200)
        self.details.setMaximumHeight(160)
        self.details.hide()
        self.details_button.toggled.connect(self._toggle_details)
        body.addWidget(self.details)
        self.status_bar.hide()
        layout.addLayout(body, 1)
        QTimer.singleShot(0, self.refresh)

    def eventFilter(self, watched, event) -> bool:
        """
        Keep fixed page content intact when activity details or validation messages need extra height.
        """
        if watched is self.centralWidget() and event.type() == QEvent.Type.LayoutRequest:
            minimum = watched.minimumSizeHint()
            self.setMinimumSize(max(1050, minimum.width()), max(740, minimum.height()))
        return super().eventFilter(watched, event)

    def navigate(self, name: str) -> None:
        """
        Navigate using a screen name from a checklist link or correction action.
        """
        if name in self._navigation_rows:
            self.navigation.setCurrentRow(self._navigation_rows[name])

    def refresh(self) -> None:
        """
        Read the current workspace in a worker without discarding unsaved forms.
        """
        if self.job is not None:
            return
        month = self.month
        self._analysis_pending = False
        self._analysis_has_securities = False
        self._analysis_price_start = None
        self._start("refresh", lambda progress, cancelled: self.service.snapshot(month))

    def _load_analysis_if_needed(self) -> None:
        """
        Present the cached history first and fill price gaps as the visible date range expands.
        """
        page = self.pages["Analysis"]
        if self.job is not None or self._closing or self.stack.currentWidget() is not page:
            return
        month = self.month
        if self._analysis_pending:
            self._analysis_pending = False
            if page.history is None:
                page.set_loading()
            self._start("load_analysis", lambda progress, cancelled: self.service.run(
                "load_analysis", {}, month, progress, cancelled,
            ))
            return
        if not self._analysis_has_securities or page.history is None or page.history.month != month:
            return
        bounds = page.history.daily_bounds(page.HORIZONS[page.ranges.currentIndex()])
        if not bounds or (self._analysis_price_start is not None and self._analysis_price_start <= bounds[0]):
            return
        start, _ = bounds
        self._analysis_price_start = start
        self._start("update_analysis_prices", lambda progress, cancelled: self.service.run(
            "update_analysis_prices", {"start": start}, month, progress, cancelled,
        ))

    def _activity_scope_changed(self, index: int) -> None:
        """
        Change the shared browsing scope, retaining the reporting month and any hidden Review drafts.
        """
        self._activity_link = None
        self._all_activity = bool(index)
        self._show_activity_page()

    def _show_activity_page(self) -> None:
        """
        Populate the visible activity page and load a full history only when first needed after refresh.
        """
        page = self.stack.currentWidget()
        if not isinstance(page, ActivityPage):
            return
        wants_history = self._all_activity and self.snapshot.get("ready") and not self.snapshot.get("error")
        reading = wants_history and self._activity_history is None
        page.activity_body.setCurrentIndex(1 if reading else 0)
        view = self._activity_history if wants_history else self.snapshot
        if self._active_action == "refresh" and self._failed:
            # Each page's failure handler retains its last inspectable facts and disables stale actions.
            view = page.snapshot
            wants_history = view.get("activity_all")
        self.activity_scope.blockSignals(True)
        self.activity_scope.setCurrentIndex(int(bool(wants_history)))
        self.activity_scope.blockSignals(False)
        if view and page.snapshot is not view:
            page.table.setUpdatesEnabled(False)
            try:
                page.refresh(view)
            finally:
                page.table.setUpdatesEnabled(True)
        if reading and self.job is None and not self._closing:
            month = self.month
            self._start("load_activity_history", lambda progress, cancelled: self.service.run(
                "load_activity_history", {}, month, progress, cancelled,
            ))

    def _configuration_saved(self) -> None:
        """
        Mark existing reports for regeneration before refreshing the saved workspace settings.
        """
        self.pages["Reports"].mark_stale("Settings changed. Regenerate reports to include them.")
        if not (self._busy and self._active_action == "save_setup"):
            QTimer.singleShot(0, self.refresh)

    def _begin_owner_edit(self) -> None:
        """
        Capture a saved configuration revision before entering the isolated sidebar name edit.
        """
        if self.job is not None:
            return
        try:
            document = read_configuration(self.service.root, "reports")
            name = ReportSettings(name=document.data.get("name", "Aurum")).owner_name
        except (OSError, ValueError, TypeError) as error:
            self._status_error = True
            self._progress(error_message(error))
            return
        self._owner_document = document
        self.owner_label.set_saved_name(name)
        self.owner_label.begin_edit()

    def _save_owner_name(self, value: str) -> None:
        """
        Save the name on Enter without committing another Setup draft or ignoring stale settings.
        """
        if self._owner_document is None or self.job is not None:
            return
        try:
            name = self.pages["Setup"].save_owner_name(self._owner_document, value)
        except (OSError, ValueError, TypeError) as error:
            self._status_error = True
            self._progress(error_message(error))
            return
        self._owner_document = None
        self.owner_label.finish_edit(name)
        self.snapshot["owner_name"] = name

    def _cancel_owner_edit(self) -> None:
        """
        Release the edit snapshot when Escape or focus loss cancels an unsaved name.
        """
        self._owner_document = None

    def closeEvent(self, event) -> None:
        """
        Let the current atomic step finish before closing, and protect unsaved responses.
        """
        if self.job is not None:
            answer = QMessageBox.question(
                self, "Work is running", "Stop after the current step and close when it is safe?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer == QMessageBox.StandardButton.Yes:
                self._closing = True
                self._continuation = None
                self.pages["Transactions"].defer_preparation()
                self._stop()
            event.ignore()
            return
        if self.editor is not None:
            self.editor.reject()
            if self.editor is not None:
                event.ignore()
                return
        setup = self.pages["Setup"]
        if not setup.guard_connection_changes():
            event.ignore()
            return
        if not self._guard_changes(self.close):
            event.ignore()
            return
        event.accept()

    def _navigate_index(self, index: int) -> None:
        """
        Keep navigation and the visible page consistent when an unsaved edit needs a decision.
        """
        if index < 0:
            return
        item = self.navigation.item(index)
        name = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        section = item.data(Qt.ItemDataRole.UserRole + 1) if item is not None else None
        previous = self.navigation.accepted_row
        if previous < 0:
            previous = self._navigation_rows["Overview"]
        if name not in self.pages:
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(previous)
            self.navigation.blockSignals(False)
            return
        if not self._guard_changes(lambda: self.navigation.setCurrentRow(index)):
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(self.navigation.accepted_row)
            self.navigation.blockSignals(False)
            return
        if name == "Setup" and section and not self.pages["Setup"].select_section(section):
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(previous)
            self.navigation.blockSignals(False)
            return
        self._selected_page = self.names.index(name)
        self.navigation.accept_row(index)
        self.stack.setCurrentWidget(self.pages[name])
        self.pages[name].title_row.addWidget(self.month_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        self.month_bar.show()
        page = self.pages[name]
        if isinstance(page, ActivityPage):
            page.subtitle_row.addWidget(self.activity_scope, 0, Qt.AlignmentFlag.AlignRight)
        self.activity_scope.setVisible(isinstance(page, ActivityPage))
        if self._activity_link and self._activity_link[0] != name:
            self._activity_link = None
        self._show_activity_page()
        if hasattr(self, "status_bar"):
            self._update_status_visibility()
        if name == "Analysis":
            if self._active_action != "update_analysis_prices" or self.job is None:
                self._analysis_price_start = None
            if not self._busy:
                self._load_analysis_if_needed()

    def _sync_setup_section(self, kind: str) -> None:
        """
        Reflect an accepted section change without prompting again or losing deferred save navigation.
        """
        if self.stack.currentWidget() is not self.pages["Setup"]:
            return
        row = self._setup_rows[kind]
        self.navigation.accept_row(row)
        self.navigation.blockSignals(True)
        self.navigation.setCurrentRow(row)
        self.navigation.blockSignals(False)

    def _setup_section_labels(self, labels: dict) -> None:
        """
        Keep unsaved-section indicators visible in the sidebar while the form's tab bar stays hidden.
        """
        for kind, label in labels.items():
            self.navigation.item(self._setup_rows[kind]).setText(label)

    def _step_month(self, offset: int) -> None:
        """
        Request the neighbouring reporting month through the existing unsaved-change guard.
        """
        year, month = map(int, self.month.split("-"))
        self._month_changed(QDate(year, month, 1).addMonths(offset))

    def _update_month_controls(self, busy: bool | None = None) -> None:
        """
        Show the accepted month and disable navigation outside available reporting periods.
        """
        busy = self._busy if busy is None else busy
        year, month = map(int, self.month.split("-"))
        selected = QDate(year, month, 1)
        self.month_label.setAccessibleName(selected.toString("MMMM yyyy"))
        self.month_label.setText(
            f'{selected.toString("MMMM")} '
            f'<span style="font-weight: 400; color: {gui_colour(self, "muted", "#6b8d80")};">'
            f'{selected.year():04d}</span>'
        )
        period = resolve_period([], month=self.month)
        date_range = (
            f"{period.start_date.day} {period.start_date:%b %Y} — "
            f"{period.end_date.day} {period.end_date:%b %Y}"
        )
        self.month_range.setText(
            f"{period.start_date.day} {period.start_date:%b} – "
            f"{period.end_date.day} {period.end_date:%b}"
        )
        self.month_range.setAccessibleName(f"Reporting dates: {date_range}, inclusive")
        explanation = (
            f"{date_range}, inclusive.\n"
            "Reporting months end on the last Monday–Friday day, without public-holiday adjustment.\n"
            "The next day begins the following reporting month; journal accounting dates select the period."
        )
        if period.is_partial:
            explanation += f"\nReports currently include entries through {period.cutoff:%d %b %Y}."
        self.month_range.setToolTip(explanation)
        self.month_label.setToolTip(explanation)
        self.month_label.setAccessibleDescription(date_range)
        maximum = QDate.currentDate().addMonths(1)
        for button, offset in ((self.previous_month, -1), (self.next_month, 1)):
            candidate = selected.addMonths(offset)
            available = candidate.isValid() and candidate.year() >= 1 and candidate <= maximum
            if available:
                try:
                    resolve_period([], month=candidate.toString("yyyy-MM"))
                except ValueError:
                    available = False
            button.setEnabled(available and not busy)

    def _refresh_theme_controls(self) -> None:
        """
        Update explicit sidebar item colours and the rich-text month from the saved window theme.
        """
        for row in range(self.navigation.count()):
            item = self.navigation.item(row)
            section = item.data(Qt.ItemDataRole.UserRole + 1)
            role = "sidebar_muted" if section else "sidebar_text"
            colour = gui_colour(self, role, "#bdd6cc" if section else "#cfe1da")
            name = _SETUP_ICONS[section] if section else item.data(Qt.ItemDataRole.UserRole)
            item.setForeground(QColor(colour))
            item.setIcon(icon(name, colour=colour, size=16 if section else 18))
        self._update_month_controls()

    def _month_changed(self, value: QDate) -> None:
        """
        Switch reporting periods only after handling unsaved input from the previous month.
        """
        requested = value.toString("yyyy-MM")
        if self.job is not None or requested == self.month:
            return
        try:
            if not value.isValid() or value.year() < 1 or value > QDate.currentDate().addMonths(1):
                raise ValueError("Choose an available reporting month")
            resolve_period([], month=requested)
        except ValueError as error:
            self._status_error = True
            self._progress(str(error))
            return
        if not self._guard_changes(lambda: self._month_changed(value)):
            return
        self.month = requested
        self.pages["Analysis"].clear_history()
        self.pages["Setup"].set_reporting_month(self.month)
        self._update_month_controls()
        self.refresh()

    def _guard_changes(self, continuation=None) -> bool:
        """
        Offer save, discard and cancel for the current editable page before leaving it.
        """
        if self._continuation is not None or (self.job is not None and self._active_action == "save_setup"):
            return False
        page = self.stack.currentWidget()
        if isinstance(page, SetupPage) and not page.guard_connection_changes():
            return False
        if hasattr(page, "guard_opening_changes") and not page.guard_opening_changes():
            return False
        if not hasattr(page, "has_unsaved_changes") or not page.has_unsaved_changes():
            return True
        answer = confirm_unsaved(self)
        if answer == "discard":
            page.discard_changes()
            return True
        if answer == "save":
            self._continuation = continuation
            saved = page.save_changes()
            if isinstance(page, SetupPage) and saved:
                self._continuation = None
                return True
            if self.job is None:
                self._continuation = None
        return False

    def _action(self, action: str, payload: dict) -> None:
        """
        Route page intent to navigation or one background operation.
        """
        if action == "redbark_release_session":
            self.service.release_bank_session(payload.get("connection_token"))
            return
        if action == "open_manual_review":
            self._open_record("Review", f"manual:{payload['manual_entry_id']}")
            return
        if action in {"open_manual", "open_review"}:
            name = "Transactions" if action == "open_manual" else "Review"
            key = "manual_entry_id" if action == "open_manual" else "transaction_id"
            self._open_record(name, payload[key])
            return
        if action in {"show_review", "show_manual", "show_journal"}:
            if action == "show_review" and payload.get("kind"):
                self._open_review_kind(payload["kind"])
                return
            self.navigate({
                "show_review": "Review", "show_manual": "Transactions", "show_journal": "Journal",
            }[action])
            return
        if action == "open_report_settings":
            self._open_report_settings()
            return
        if action in {"open_bank_settings", "open_account_settings", "open_budget_settings"}:
            if self._guard_changes(lambda: self._action(action, payload)):
                self.navigate("Setup")
                setup = self.pages["Setup"]
                section = "budget" if action == "open_budget_settings" else "accounts"
                if setup.select_section(section) and section == "accounts" and payload.get("account_key"):
                    form = setup.forms.get("accounts")
                    if form is not None:
                        state = form.view_state()
                        state["selection"] = {"key": payload["account_key"]}
                        form.restore_view_state(state)
            return
        if self.job is not None:
            return
        if action not in {
            "save_review", "save_reviews", "save_manual", "save_bank_opening", "discard_review",
            "save_setup",
            "apply_journal_edit",
            "reload_entry_editor",
            "reload_manual_editor",
            "connect_chatgpt", "save_model",
            "redbark_check", "redbark_enable",
            "redbark_disconnect",
        }:
            if not self._guard_changes(lambda: self._action(action, payload)):
                return
        if action == "process_journal":
            outside = self.snapshot.get("counts", {}).get("linked_outside_month", 0)
            if outside:
                answer = QMessageBox.question(
                    self, "Linked accounting",
                    f"This month includes {outside} linked source row(s) outside its date range. "
                    "Complete transfers and corrections will be processed together. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
        month = self.month
        for page in self.pages.values():
            if hasattr(page, "action_started"):
                page.action_started(action, payload)
        self._start(action, lambda progress, cancelled: self.service.run(
            action, payload, month, progress, cancelled,
        ))

    def _open_record(self, name: str, identifier: str, *, by_kind: bool = False) -> None:
        """
        Retain a linked record's destination when saving the current form delays navigation.
        """
        if not self._guard_changes(lambda: self._open_record(name, identifier, by_kind=by_kind)):
            return
        self.navigate(name)
        if self.stack.currentWidget() is self.pages[name]:
            self._activity_link = (name, identifier, by_kind)
            self._reveal_activity_link()

    def _reveal_activity_link(self) -> None:
        """
        Reveal a linked identity after loading its destination, widening the date range if needed.
        """
        if self._activity_link is None or self.job is not None:
            return
        name, identifier, by_kind = self._activity_link
        page = self.pages[name]
        if self.stack.currentWidget() is not page:
            self._activity_link = None
            return
        self._activity_link = None
        if by_kind:
            page.select_kind(identifier)
            return
        if page.select_record(identifier):
            return
        if not self._all_activity and self.snapshot.get("ready") and not self.snapshot.get("error"):
            self._all_activity = True
            self._activity_link = (name, identifier, False)
            self._show_activity_page()
            if self.job is None:
                self._reveal_activity_link()
            return
        QMessageBox.information(
            self, "Linked item unavailable", "The linked item is unavailable in saved activity.",
        )

    def _open_report_settings(self) -> None:
        """
        Open the report configuration section after protecting any unfinished form.
        """
        if not self._guard_changes(self._open_report_settings):
            return
        self.navigate("Setup")
        setup = self.pages["Setup"]
        if self.stack.currentWidget() is setup:
            setup.select_section("reports")

    def _start(self, action: str, operation) -> None:
        """
        Start one worker and connect its updates to the main thread.
        """
        if self.job is not None:
            return
        if action == "refresh":
            self.pages["Overview"].show_loading()
        self._active_action = action
        self._failed = False
        self._status_error = False
        self._result = None
        self.job = Job(operation)
        self.job.signals.progress.connect(self._progress)
        self.job.signals.succeeded.connect(self._succeeded)
        self.job.signals.failed.connect(self._failure)
        self.job.signals.finished.connect(self._finished)
        self._set_busy(True)
        self._progress("Refreshing…" if action == "refresh" else "Working…")
        self.pool.start(self.job)

    def _set_busy(self, busy: bool, *, keep_activity: bool = False) -> None:
        """
        Prevent overlapping writes while leaving screen navigation and report reading available.
        """
        self._busy = busy
        activity = {
            "refresh": "Refreshing workspace", "redbark_sync": "Syncing bank accounts",
            "process_journal": "Processing entries", "generate_reports": "Generating reports",
            "load_analysis": "Reading financial history",
            "load_activity_history": "Reading activity history",
            "update_analysis_prices": "Updating closing prices",
        }.get(self._active_action, "Workspace operation in progress")
        self.refresh_button.set_loading(busy or keep_activity, activity=activity)
        self.stop_button.setVisible(busy and self._active_action != "refresh")
        self.stop_button.setEnabled(busy)
        self._update_month_controls(busy)
        self.activity_scope.setEnabled(
            bool(self.snapshot.get("ready")) and (not busy or self._active_action == "load_activity_history")
        )
        self.refresh_button.setEnabled(not busy)
        self.owner_label.setEnabled(not busy)
        for page in self.pages.values():
            if hasattr(page, "set_busy"):
                page.set_busy(busy)
        if self.editor is not None:
            self.editor.set_busy(busy)
        self._update_status_visibility()

    def _update_status_visibility(self) -> None:
        """
        Keep activity in the log and spinner, reserving visible messages for errors.
        """
        has_details = not self.details.document().isEmpty()
        show_setup = self.stack.currentWidget() is self.pages["Setup"]
        self.pages["Setup"].footer.setVisible(show_setup)
        show_message = bool(self.status.text()) and self._status_error
        self.status.setVisible(show_message)
        self.details_button.setVisible(has_details)
        self.status_bar.setVisible(
            show_setup or show_message or not self.stop_button.isHidden()
            or has_details or self.details_button.isChecked()
        )

    def _toggle_details(self, visible: bool) -> None:
        """
        Retain the user's log visibility choice without leaving an empty status bar behind.
        """
        self.details.setVisible(visible)
        self.details_button.setToolTip("Hide activity details" if visible else "Show activity details")
        self._update_status_visibility()

    def _progress(self, message: str) -> None:
        """
        Retain an inspectable activity history without narrating routine work on the page.
        """
        self.status.setText(message)
        self.details.appendPlainText(message)
        self._update_status_visibility()

    def _succeeded(self, result) -> None:
        """
        Retain the result until the worker has fully finished.
        """
        self._result = result

    def _failure(self, message: str) -> None:
        """
        Keep entered answers in place and report why the action did not complete.
        """
        self._failed = True
        self._status_error = self._active_action != "update_analysis_prices"
        self._continuation = None
        self._progress(message)
        if self._active_action == "refresh":
            self.snapshot = {**self.snapshot, "ready": False, "error": message}
            self.pages["Overview"].refresh({"ready": False, "error": message})
        if self._active_action in {"load_activity_history", "refresh"}:
            self._activity_history = None
            self._activity_link = None
            if self._active_action == "load_activity_history":
                self._all_activity = False
        if self._active_action in {"refresh", "load_analysis"}:
            self.pages["Analysis"].show_error(message)
        if self._active_action.startswith("redbark_"):
            self.pages["Setup"].bank_connection.deliver_error(self._active_action, message)
            if self._active_action in {"redbark_sync", "redbark_disconnect"}:
                self._last_message = message
        for page in self.pages.values():
            if hasattr(page, "action_failed"):
                page.action_failed(self._active_action, message)
        if self.editor is not None and self._active_action in {
            "apply_journal_edit", "reload_entry_editor", "save_manual", "reload_manual_editor",
        }:
            self.editor.show_error(message)

    def _finished(self) -> None:
        """
        Apply worker results and keep one activity indicator through deferred follow-up work.
        """
        action, result = self._active_action, self._result
        next_step = None
        resume_controls = False
        self.job = None
        if action == "save_setup" and self._failed:
            self._closing = False
        if self._closing:
            self.pages["Transactions"].defer_preparation()
        if not self._failed and result is not None:
            if action == "refresh":
                self.snapshot = result
                self._activity_history = None
                self._analysis_pending = bool(result.get("ready") and not result.get("error"))
                theme = result.get("gui_theme") if result.get("ready") and not result.get("error") else None
                if theme and theme != self.property("aurumGuiTheme"):
                    apply_gui_theme(self, theme)
                owner = str(result.get("owner_name") or "").strip()
                self.owner_label.set_saved_name(owner)
                for name, page in self.pages.items():
                    if name == "Setup":
                        page.set_reporting_month(self.month)
                        page.refresh()
                    elif not (isinstance(page, ActivityPage) and self._all_activity and result.get("ready")):
                        page.refresh(result)
                self.pages["Setup"].bank_connection.set_snapshot(result)
                self.pages["Setup"].set_bank_openings(result.get("bank_openings", {}))
                if theme:
                    # Setup can rebuild controls during refresh; tint those after they exist as well.
                    apply_gui_theme(self, theme)
                    self._refresh_theme_controls()
                self._status_error = bool(result.get("error"))
                if self._status_error:
                    self._progress(result["error"])
                else:
                    self.status.setText(
                        self._last_message if self._last_message != "Workspace ready." else "",
                    )
                    self._update_status_visibility()
            elif action == "load_activity_history":
                if "activity" in result:
                    self._activity_history = {**self.snapshot, **result["activity"], "activity_all": True}
                else:
                    self._all_activity = False
                    self._activity_link = None
            elif action in {"load_analysis", "update_analysis_prices"}:
                page = self.pages["Analysis"]
                if "analysis" in result and result.get("month") == self.month:
                    history = result["analysis"]
                    self._analysis_has_securities = result["has_securities"]
                    page.set_history(history)
                    if action == "update_analysis_prices" and history.daily:
                        latest = history.daily[-1]
                        self.snapshot["financials"]["net_worth"] = latest.net_worth
                        self.snapshot["net_worth_unpriced_holdings"] = list(latest.provisional_holdings)
                        self.snapshot["net_worth_price_dates"] = [
                            {"name": close.name, "price_date": close.price_date.isoformat()}
                            for close in latest.price_dates
                        ]
                        self.pages["Overview"].refresh(self.snapshot)
                elif action == "load_analysis" and "analysis" not in result:
                    page.show_error(result.get("message", "Refresh to reload history."))
                elif action == "update_analysis_prices" and result.get("message"):
                    self._progress(result["message"])
            elif action in {
                "open_entry_editor", "reload_entry_editor", "open_manual_editor", "reload_manual_editor",
            }:
                if not self._closing and result.get("editor"):
                    if action == "reload_manual_editor" and isinstance(self.editor, ManualEditor):
                        self.editor.reload_context(result["editor"])
                        next_step = self.refresh
                    else:
                        self._show_editor(result["editor"])
                elif not self._closing:
                    self._progress(result.get("message", "Stopped before opening the editor."))
            else:
                if action.startswith("redbark_"):
                    self.pages["Setup"].bank_connection.deliver_result(action, result)
                saved_identity = {
                    "save_review": "transaction_id", "discard_review": "transaction_id",
                    "save_reviews": "review_ids",
                    "save_manual": "manual_entry_id",
                    "save_setup": "saved_setup",
                }.get(action)
                if saved_identity and not result.get(saved_identity):
                    self._continuation = None
                if (
                    action == "apply_journal_edit" and self.editor is not None
                    and result.get("journal_entry_id")
                ):
                    self.editor.mark_saved()
                elif action == "save_manual" and isinstance(self.editor, ManualEditor):
                    if result.get("manual_entry_id") == self.editor.context.get("manual_entry_id"):
                        self.editor.mark_saved()
                    else:
                        self.editor.show_error(result.get("message", "The instruction was not saved."))
                for page in self.pages.values():
                    if hasattr(page, "action_completed"):
                        data = result.get("data") if action in {
                            "import_statements", "stage_statements",
                        } else result
                        page.action_completed(action, data)
                if action == "save_setup":
                    if not self.pages["Setup"].save_acknowledged(result):
                        self._continuation = None
                        self._closing = False
                    elif "report_appearance" in result:
                        theme = result["gui_theme"]
                        self.snapshot.update(gui_theme=theme, report_appearance=result["report_appearance"])
                        apply_gui_theme(self, theme)
                        self._refresh_theme_controls()
                        self.pages["Reports"].apply_appearance(result["report_appearance"])
                if action == "redbark_sync":
                    self._show_sync_result(result)
                else:
                    self._last_message = result.get("message", "Saved.")
                    self._progress(self._last_message)
                if not self._closing and action != "redbark_check":
                    next_step = self.refresh
        if action == "refresh" and self._continuation and not self._failed:
            continuation, self._continuation = self._continuation, None
            next_step = continuation
            resume_controls = True
        elif action == "refresh" and not self._failed and not self._closing:
            next_step = self._startup_bank_sync
        if (
            action in {"redbark_sync", "redbark_disconnect", "save_bank_opening"}
            and self._failed and not self._closing
        ):
            next_step = self.refresh
        if self._closing:
            self._closing = False
            self._set_busy(False)
            QTimer.singleShot(0, self.close)
        elif next_step is not None:
            QTimer.singleShot(0, self, lambda: self._continue_work(next_step, resume_controls=resume_controls))
        else:
            self._resume_page()

    def _continue_work(self, operation, *, resume_controls: bool = False) -> None:
        """
        Hand off without flashing idle, releasing forms before deferred navigation or another explicit save.
        """
        if resume_controls:
            self._set_busy(False, keep_activity=True)
        try:
            operation()
        finally:
            self._resume_page()

    def _resume_page(self) -> None:
        """
        Finish any requested page read before restoring actions and following an identity-bound deep link.
        """
        self._show_activity_page()
        self._load_analysis_if_needed()
        if self.job is None:
            self._set_busy(False)
            self._reveal_activity_link()

    def _show_sync_result(self, result: dict) -> None:
        """
        Keep routine sync counts in the information log while leaving interrupted work and issues visible.
        """
        self.details.appendPlainText(str(result.get("message") or "Bank sync finished."))
        data = result.get("data") or {}
        failed = sum(item.get("status") == "failed" for item in data.get("results", []))
        conflicts = len(data.get("conflicts", []))
        messages = ["Sync stopped."] if data.get("cancelled") else []
        if failed:
            messages.append(
                f"{failed} {'account' if failed == 1 else 'accounts'} could not sync. "
                "Retry from Transactions."
            )
        if conflicts:
            messages.append(f"{conflicts} bank import {'issue' if conflicts == 1 else 'issues'} in Review.")
        self._last_message = " ".join(messages)
        self._status_error = bool(failed or conflicts)
        self.status.setText(self._last_message)
        self._update_status_visibility()

    def _startup_bank_sync(self) -> None:
        """
        Sync a connected workspace once after its initial read, ignoring the retired auto-sync preference.
        """
        if not self._startup_sync_pending or self.job is not None or self._closing:
            return
        if not self.snapshot.get("ready") or self.snapshot.get("error"):
            return
        self._startup_sync_pending = False
        feed = self.snapshot.get("bank_feed", {})
        if feed.get("configured"):
            month = self.month
            self._start("redbark_sync", lambda progress, cancelled: self.service.run(
                "redbark_sync", {}, month, progress, cancelled,
            ))

    def _open_review_kind(self, kind: str) -> None:
        """
        Retain a report's Review destination when navigation waits for an unsaved answer to be saved.
        """
        self._open_record("Review", kind, by_kind=True)

    def _show_editor(self, context: dict) -> None:
        """
        Open a revision-bound editor, replacing an older dialog only after an explicit successful reload.
        """
        if self.editor is not None:
            self.editor.done(0)
        editor_class = ManualEditor if context.get("manual_composer") else JournalEditor
        editor = editor_class(context, self)
        if not context.get("manual_composer") and context.get("focus_line_number"):
            number = context["focus_line_number"]
            if 1 <= number <= len(editor.rows):
                editor._select_row(editor.rows[number - 1])
                editor.table.scrollTo(editor.table.model().index(number - 1, 0))
        self.editor = editor
        editor.action_requested.connect(self._action)
        editor.finished.connect(lambda _result: self._editor_closed(editor))
        editor.open()

    def _editor_closed(self, editor: JournalEditor | ManualEditor) -> None:
        """
        Release a completed dialog without clearing a newer editor opened by a reload.
        """
        if self.editor is editor:
            self.editor = None
        editor.deleteLater()

    def _stop(self) -> None:
        """
        Request a stop without killing a thread or rolling back already completed events.
        """
        if self.job:
            self.job.stop()
            self.stop_button.setEnabled(False)
            self._progress("Stopping after the current safe step. Completed work remains saved.")

# %% 2. Helpers ----------------------------------------------------------------------------------------------
class _SidebarName(QLineEdit):
    """
    Present the owner's name as quiet text, with an explicit Enter-to-save edit mode.
    """

    edit_requested = Signal()
    save_requested = Signal(str)
    edit_cancelled = Signal()

    def __init__(self, parent=None) -> None:
        """
        Keep the footer editable by double-click or keyboard without an input-box background.
        """
        super().__init__(parent)
        self._editing = False
        self._saved_text = ""
        self.setObjectName("sidebarOwner")
        self.setFrame(False)
        self.setReadOnly(True)
        self.setMinimumWidth(0)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setTextMargins(0, 0, 0, 0)
        self.setPlaceholderText("Your name")
        self.setAccessibleName("Personal name")
        self.setAccessibleDescription("Double-click or press F2 to edit. Enter saves; Escape cancels.")
        self.set_saved_name("")

    def set_saved_name(self, name: str) -> None:
        """
        Refresh the saved value without overwriting text currently being edited.
        """
        self._saved_text = name
        self.setToolTip((name + "\n" if name else "") + self.accessibleDescription())
        if not self._editing:
            self.setText(name)
            self.setCursorPosition(0)

    def begin_edit(self) -> None:
        """
        Reveal the captured name as an editable value only after its revision has been read.
        """
        self._editing = True
        self.setReadOnly(False)
        self.setText(self._saved_text)
        self.setFocus(Qt.FocusReason.OtherFocusReason)
        self.selectAll()

    def finish_edit(self, name: str) -> None:
        """
        Return to plain display after a successful explicit save.
        """
        self._editing = False
        self.setReadOnly(True)
        self.set_saved_name(name)
        self.deselect()

    def cancel_edit(self) -> None:
        """
        Restore the saved display without persisting a partial name.
        """
        if self._editing:
            self.finish_edit(self._saved_text)
            self.edit_cancelled.emit()

    def mouseDoubleClickEvent(self, event) -> None:
        """
        Start editing from a deliberate double-click, retaining normal word selection while editing.
        """
        if not self._editing and event.button() == Qt.MouseButton.LeftButton:
            self.edit_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        """
        Support F2, explicit Enter-to-save and Escape-to-cancel without a silent focus-loss save.
        """
        if event.key() == Qt.Key.Key_F2 and not self._editing:
            self.edit_requested.emit()
        elif self._editing and event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.save_requested.emit(self.text())
        elif self._editing and event.key() == Qt.Key.Key_Escape:
            self.cancel_edit()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def focusOutEvent(self, event) -> None:
        """
        Cancel unfinished name edits when focus moves elsewhere.
        """
        self.cancel_edit()
        super().focusOutEvent(event)


class _MonthLayout(QGridLayout):
    """
    Raise the two month labels without changing the navigation or primary-action layout.
    """

    def setGeometry(self, rectangle: QRect) -> None:
        """
        Position labels from their allocated cells so repeated layout passes cannot accumulate an offset.
        """
        super().setGeometry(rectangle)
        for row in (0, 1):
            item = self.itemAtPosition(row, 0)
            widget = item.widget() if item is not None else None
            if isinstance(widget, QLabel):
                cell = self.cellRect(row, 0)
                top = cell.top() + (cell.height() - widget.height()) // 2
                widget.move(widget.x(), top - 4)


class _NavigationList(QListWidget):
    """
    Fit navigation to its rows so spare sidebar height can centre the quotation beneath them.
    """

    def __init__(self, parent=None):
        """
        Permit scrolling in short windows while keeping the quotation and workspace footer visible.
        """
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.accepted_row = -1
        self._previous_fills = {}
        self._progress = 1.0
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(120)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._set_progress)
        watch_reduced_motion(self._snap_selection)

    def accept_row(self, row: int) -> None:
        """
        Cross-fade only after the workspace accepts navigation, starting from the fills visible now.
        """
        if row == self.accepted_row:
            return
        visible = self._previous_fills.keys() | {self.accepted_row}
        self._previous_fills = {
            item: self.selection_opacity(item) for item in visible if item >= 0
        }
        self._fade.stop()
        self.accepted_row = row
        if not self._previous_fills or not self.isVisible() or reduced_motion(self):
            self._snap_selection()
        else:
            self._set_progress(0.0)
            self._fade.start()

    def selection_opacity(self, row: int) -> float:
        """
        Return the current selection fill for a row, independent of a pending native selection.
        """
        previous = self._previous_fills.get(row, 0.0)
        target = float(row == self.accepted_row)
        return previous + (target - previous) * self._progress

    def _snap_selection(self) -> None:
        """
        Finish the active selection immediately when the motion preference changes.
        """
        self._fade.stop()
        self._set_progress(1.0)

    def _set_progress(self, fraction: float) -> None:
        """
        Repaint the short sidebar transition and release the outgoing rows once it settles.
        """
        self._progress = fraction
        if fraction >= 1.0:
            self._previous_fills.clear()
        self.viewport().update()

    def sizeHint(self) -> QSize:
        """
        Measure the visible rows and their spacing instead of reserving an expanding blank list area.
        """
        hint = super().sizeHint()
        margins = self.contentsMargins()
        height = margins.top() + margins.bottom()
        height += sum(
            self.sizeHintForRow(row) + 2 * self.spacing()
            for row in range(self.count()) if not self.item(row).isHidden()
        )
        return QSize(hint.width(), height)


class _NavigationDelegate(QStyledItemDelegate):
    """
    Align main destinations with the brand and indent Setup's child destinations as one visual group.
    """

    def paint(self, painter, option, index) -> None:
        """
        Blend the themed selection in place while preserving indentation, native text and icon rendering.
        """
        option = QStyleOptionViewItem(option)
        painter.save()
        painter.setClipRect(option.rect, Qt.ClipOperation.IntersectClip)
        if index.data(Qt.ItemDataRole.UserRole + 1):
            option.rect.adjust(18, 0, 0, 0)
        navigation = self.parent()
        shape = option.rect.adjusted(0, 2, 0, -2)
        radius = shape.height() / 2
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if option.state & QStyle.StateFlag.State_MouseOver:
            painter.setBrush(QColor(gui_colour(navigation, "sidebar_hover", "#275251")))
            painter.drawRoundedRect(shape, radius, radius)
        opacity = navigation.selection_opacity(index.row())
        if opacity:
            fill = QColor(gui_colour(navigation, "sidebar_selected", "#32605e"))
            fill.setAlphaF(opacity)
            painter.setBrush(fill)
            painter.drawRoundedRect(shape, radius, radius)
        if index.row() != navigation.accepted_row:
            option.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)
        super().paint(painter, option, index)
        painter.restore()
