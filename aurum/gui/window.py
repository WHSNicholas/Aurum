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

from PySide6.QtCore import QDate, QEvent, QRect, QSize, QThreadPool, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QStackedWidget,
    QStyledItemDelegate, QStyleOptionViewItem, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.activity import JournalPage, ReviewPage, StatementsPage
from aurum.gui.background import Job, error_message
from aurum.gui.controls import QuoteBlock
from aurum.gui.editor import JournalEditor
from aurum.gui.icons import RefreshButton, Spinner, icon
from aurum.gui.manual import ManualPage
from aurum.gui.manual_editor import ManualEditor
from aurum.gui.services import WorkspaceService
from aurum.gui.setup import SetupPage
from aurum.gui.theme import apply_gui_theme, gui_colour
from aurum.gui.workspace import OverviewPage, ReportsPage
from aurum.finance.reports import resolve_period


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
        self._active_action = ""
        self._result = None
        self._failed = False
        self._status_error = False
        self._continuation = None
        self._closing = False
        self._selected_page = 0
        self._selected_navigation_row = -1
        self._new_window = None
        self.editor = None
        self._last_message = "Workspace ready."
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
        tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_header.addWidget(tagline)
        side.addLayout(brand_header)
        side.addSpacing(16)
        self.navigation = _NavigationList()
        self.navigation.setObjectName("navigation")
        self.navigation.setItemDelegate(_NavigationDelegate(self.navigation))
        self.navigation.setIconSize(QSize(18, 18))
        self.navigation.setSpacing(2)
        self.names = [
            "Overview", "Statements", "Manual entries", "Review", "Journal", "Reports", "Setup",
        ]
        self._navigation_rows = {}
        self._setup_rows = {}
        for name in self.names:
            label = "Manual" if name == "Manual entries" else name
            item = QListWidgetItem(icon(name, colour="#cfe1da", size=18), label)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setSizeHint(QSize(0, 40))
            self._navigation_rows[name] = self.navigation.count()
            self.navigation.addItem(item)
            if name == "Setup":
                for kind, symbol in (
                    ("accounts", "wallet"), ("securities", "chart"),
                    ("budget", "savings"), ("reports", "document"), ("chatgpt", "chat"),
                ):
                    label = "ChatGPT" if kind == "chatgpt" else kind.title()
                    child = QListWidgetItem(icon(symbol, colour="#aac9bd", size=16), label)
                    child.setData(Qt.ItemDataRole.UserRole, "Setup")
                    child.setData(Qt.ItemDataRole.UserRole + 1, kind)
                    child.setForeground(QColor("#bdd6cc"))
                    child.setSizeHint(QSize(0, 32))
                    self._setup_rows[kind] = self.navigation.count()
                    self.navigation.addItem(child)
                    if kind == "chatgpt":
                        self._navigation_rows["ChatGPT"] = self._setup_rows[kind]
        side.addWidget(self.navigation)
        side.addSpacing(12)
        side.addStretch(1)
        side.addWidget(QuoteBlock(
            "For where your treasure is, there will your heart be also.", "Matthew 6:21",
            "https://www.biblegateway.com/passage/?search=Matthew+6%3A21&version=KJV", sidebar=True,
        ))
        side.addStretch(1)
        side.addSpacing(12)
        workspace_footer = QHBoxLayout()
        workspace_names = QVBoxLayout()
        workspace_names.setSpacing(2)
        self.owner_label = QLabel()
        self.owner_label.setObjectName("ownerLabel")
        self.owner_label.setWordWrap(True)
        self.owner_label.setMaximumHeight(40)
        self.owner_label.hide()
        workspace_names.addWidget(self.owner_label)
        self.workspace_label = QLabel(workspace.name)
        self.workspace_label.setObjectName("workspaceLabel")
        self.workspace_label.setToolTip(str(workspace))
        self.workspace_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        workspace_names.addWidget(self.workspace_label)
        workspace_footer.addLayout(workspace_names, 1)
        self.refresh_button = RefreshButton(colour="#cfe1da")
        self.refresh_button.clicked.connect(self.refresh)
        workspace_footer.addWidget(self.refresh_button)
        self.open_workspace = QToolButton()
        self.open_workspace.setObjectName("openWorkspace")
        self.open_workspace.setIcon(icon("folder", colour="#cfe1da", size=18))
        self.open_workspace.setIconSize(QSize(18, 18))
        self.open_workspace.setFixedSize(32, 32)
        self.open_workspace.setToolTip("Open another workspace")
        self.open_workspace.setAccessibleName("Open another workspace")
        self.open_workspace.clicked.connect(self._choose_workspace)
        workspace_footer.addWidget(self.open_workspace)
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
        self.stack = QStackedWidget()
        self.pages = {
            "Overview": OverviewPage(), "Setup": SetupPage(workspace),
            "Statements": StatementsPage(), "Review": ReviewPage(),
            "Manual entries": ManualPage(), "Journal": JournalPage(), "Reports": ReportsPage(),
        }
        for name in self.names:
            page = self.pages[name]
            self.stack.addWidget(page)
            if hasattr(page, "action_requested"):
                page.action_requested.connect(self._action)
        self.pages["ChatGPT"] = self.pages["Setup"].chatgpt
        self.pages["Statements"].set_statement_folder(str(self.service.data / "bank_statements"))
        self.pages["ChatGPT"].action_requested.connect(self._action)
        self.pages["Setup"].leave_guard = self._guard_changes
        self.pages["Setup"].changed.connect(self._configuration_saved)
        self.pages["Setup"].section_changed.connect(self._sync_setup_section)
        self.pages["Setup"].section_labels_changed.connect(self._setup_section_labels)
        self.pages["Setup"].set_reporting_month(self.month)
        self.pages["Overview"].navigate.connect(self.navigate)
        body.addWidget(self.stack, 1)
        self.navigation.currentRowChanged.connect(self._navigate_index)
        self.navigation.setCurrentRow(self._navigation_rows["Overview"])
        self.status_bar = QWidget()
        status = QHBoxLayout(self.status_bar)
        status.setContentsMargins(0, 0, 0, 0)
        self.busy_spinner = Spinner(size=22, label="Workspace operation in progress")
        self.busy_spinner.hide()
        status.addWidget(self.busy_spinner)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        status.addWidget(self.status, 1)
        self.stop_button = QPushButton("Stop after current step")
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
        status.addWidget(self.details_button)
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
        self._start("refresh", lambda progress, cancelled: self.service.snapshot(month))

    def _configuration_saved(self) -> None:
        """
        Mark existing reports for regeneration before refreshing the saved workspace settings.
        """
        self.pages["Reports"].mark_stale("Settings changed. Regenerate reports to include them.")
        QTimer.singleShot(0, self.refresh)

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
                self.pages["Statements"].defer_preparation()
                self._stop()
            event.ignore()
            return
        if self.editor is not None:
            self.editor.reject()
            if self.editor is not None:
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
        previous = self._selected_navigation_row
        if previous < 0:
            previous = self._navigation_rows["Overview"]
        if name not in self.pages:
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(previous)
            self.navigation.blockSignals(False)
            return
        if not self._guard_changes(lambda: self.navigation.setCurrentRow(index)):
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(self._selected_navigation_row)
            self.navigation.blockSignals(False)
            return
        if name == "Setup" and section and not self.pages["Setup"].select_section(section):
            self.navigation.blockSignals(True)
            self.navigation.setCurrentRow(previous)
            self.navigation.blockSignals(False)
            return
        self._selected_page = self.names.index(name)
        self._selected_navigation_row = index
        self.stack.setCurrentWidget(self.pages[name])
        self.pages[name].title_row.addWidget(self.month_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        self.month_bar.show()

    def _sync_setup_section(self, kind: str) -> None:
        """
        Reflect an accepted section change without prompting again or losing deferred save navigation.
        """
        if self.stack.currentWidget() is not self.pages["Setup"]:
            return
        row = self._setup_rows[kind]
        self._selected_navigation_row = row
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
        busy = self.job is not None if busy is None else busy
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
        symbols = {
            "accounts": "wallet", "securities": "chart", "budget": "savings",
            "reports": "document", "chatgpt": "chat",
        }
        for row in range(self.navigation.count()):
            item = self.navigation.item(row)
            section = item.data(Qt.ItemDataRole.UserRole + 1)
            role = "sidebar_muted" if section else "sidebar_text"
            colour = gui_colour(self, role, "#bdd6cc" if section else "#cfe1da")
            name = symbols[section] if section else item.data(Qt.ItemDataRole.UserRole)
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
            self._progress(str(error))
            return
        if not self._guard_changes(lambda: self._month_changed(value)):
            return
        self.month = requested
        self.pages["Setup"].set_reporting_month(self.month)
        self._update_month_controls()
        self.refresh()

    def _guard_changes(self, continuation=None) -> bool:
        """
        Offer save, discard and cancel for the current editable page before leaving it.
        """
        if self._continuation is not None:
            return False
        page = self.stack.currentWidget()
        if not hasattr(page, "has_unsaved_changes") or not page.has_unsaved_changes():
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes", "Save your changes before continuing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Discard:
            page.discard_changes()
            return True
        if answer == QMessageBox.StandardButton.Save:
            if isinstance(page, SetupPage):
                if not page.save_changes():
                    return False
                if not page.chatgpt.has_unsaved_changes():
                    return True
                self._continuation = continuation
                page.chatgpt.save_changes()
                if self.job is None:
                    self._continuation = None
                return False
            self._continuation = continuation
            page.save_changes()
            if self.job is None:
                self._continuation = None
        return False

    def _action(self, action: str, payload: dict) -> None:
        """
        Route page intent to navigation or one background operation.
        """
        if action in {"open_manual", "open_review"}:
            name = "Manual entries" if action == "open_manual" else "Review"
            key = "manual_entry_id" if action == "open_manual" else "transaction_id"
            self._open_record(name, payload[key])
            return
        if action in {"show_review", "show_manual", "show_journal"}:
            if action == "show_review" and payload.get("kind"):
                self._open_review_kind(payload["kind"])
                return
            self.navigate({
                "show_review": "Review", "show_manual": "Manual entries", "show_journal": "Journal",
            }[action])
            return
        if action == "open_report_settings":
            self._open_report_settings()
            return
        if self.job is not None:
            return
        if action not in {
            "save_review", "save_reviews", "save_manual", "discard_review", "apply_journal_edit",
            "reload_entry_editor",
            "reload_manual_editor",
            "connect_chatgpt", "save_model",
        }:
            continuation_payload = payload
            if action == "open_entry_editor" and payload.get("from_manual_form"):
                continuation_payload = {
                    **payload, "manual_entry_id": payload.get("draft_manual_entry_id"),
                }
            if not self._guard_changes(lambda: self._action(action, continuation_payload)):
                return
        if action == "open_entry_editor" and payload.get("from_manual_form"):
            current = self.pages["Manual entries"].current or {}
            manual_id = payload.get("manual_entry_id") or current.get("manual_entry_id")
            payload = {
                "manual_entry_id": manual_id,
                "journal_entry_id": current.get("journal_entry_id")
                if current.get("manual_entry_id") == manual_id else None,
            }
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

    def _open_record(self, name: str, identifier: str) -> None:
        """
        Retain a linked record's destination when saving the current form delays navigation.
        """
        if not self._guard_changes(lambda: self._open_record(name, identifier)):
            return
        self.navigate(name)
        if self.stack.currentWidget() is self.pages[name]:
            if not self.pages[name].select_record(identifier):
                item = "manual instruction" if name == "Manual entries" else "transaction"
                QMessageBox.information(
                    self, "Linked item unavailable",
                    f"The linked {item} is not available in this month's saved list. "
                    "Choose its reporting month and open it from the list there.",
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
        if action != "refresh":
            self._status_error = False
        self._result = None
        self.job = Job(operation)
        self.job.signals.progress.connect(self._progress)
        self.job.signals.succeeded.connect(self._succeeded)
        self.job.signals.failed.connect(self._failure)
        self.job.signals.finished.connect(self._finished)
        self._set_busy(True)
        if action != "refresh":
            self._progress("Working…")
        self.pool.start(self.job)

    def _set_busy(self, busy: bool) -> None:
        """
        Prevent overlapping writes while leaving screen navigation and report reading available.
        """
        refreshing = busy and self._active_action == "refresh"
        self.refresh_button.set_loading(refreshing)
        self.busy_spinner.set_running(busy and not refreshing)
        self.busy_spinner.setVisible(busy and not refreshing)
        self.stop_button.setVisible(busy and self._active_action != "refresh")
        self.stop_button.setEnabled(busy)
        self._update_month_controls(busy)
        for widget in (self.refresh_button, self.open_workspace):
            widget.setEnabled(not busy)
        for page in self.pages.values():
            if hasattr(page, "set_busy"):
                page.set_busy(busy)
        if self.editor is not None:
            self.editor.set_busy(busy)
        self._update_status_visibility()

    def _update_status_visibility(self) -> None:
        """
        Keep read-only refresh quiet while leaving errors and explicitly opened details visible.
        """
        refreshing = self.job is not None and self._active_action == "refresh"
        show_message = bool(self.status.text()) and (not refreshing or self._status_error)
        self.status.setVisible(show_message)
        self.status_bar.setVisible(
            show_message or not self.busy_spinner.isHidden() or not self.stop_button.isHidden()
            or self.details_button.isChecked()
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
        Show one current action and retain a short, inspectable history.
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
        self._status_error = True
        self._continuation = None
        self._progress(message)
        self.details_button.setChecked(True)
        if self._active_action == "refresh":
            self.pages["Overview"].refresh({"ready": False, "error": message})
        for page in self.pages.values():
            if hasattr(page, "action_failed"):
                page.action_failed(self._active_action, message)
        if self.editor is not None and self._active_action in {
            "apply_journal_edit", "reload_entry_editor", "save_manual", "reload_manual_editor",
        }:
            self.editor.show_error(message)

    def _finished(self) -> None:
        """
        Release controls, apply worker results, then schedule any deferred navigation or refresh.
        """
        action, result = self._active_action, self._result
        self.job = None
        if self._closing:
            self.pages["Statements"].defer_preparation()
        self._set_busy(False)
        if not self._failed and result is not None:
            if action == "refresh":
                self.snapshot = result
                theme = result.get("gui_theme") if result.get("ready") and not result.get("error") else None
                if theme and theme != self.property("aurumGuiTheme"):
                    apply_gui_theme(self, theme)
                owner = str(result.get("owner_name") or "").strip()
                self.owner_label.setText(owner)
                self.owner_label.setToolTip(owner)
                self.owner_label.setVisible(bool(owner))
                for name, page in self.pages.items():
                    if name == "Setup":
                        page.set_reporting_month(self.month)
                        page.refresh()
                    else:
                        page.refresh(result)
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
            elif action in {
                "open_entry_editor", "reload_entry_editor", "open_manual_editor", "reload_manual_editor",
            }:
                if not self._closing and result.get("editor"):
                    if action == "reload_manual_editor" and isinstance(self.editor, ManualEditor):
                        self.editor.reload_context(result["editor"])
                        QTimer.singleShot(0, self, self.refresh)
                    else:
                        self._show_editor(result["editor"])
                    self._progress(
                        "Save an instruction for processing, or post journal lines directly."
                        if result["editor"].get("manual_composer") else
                        "Edit the complete entry, then save to apply it without AI."
                    )
                elif not self._closing:
                    self._progress(result.get("message", "Stopped before opening the editor."))
            else:
                if action == "save_model" and not result.get("data", {}).get("models"):
                    self._continuation = None
                saved_identity = {
                    "save_review": "transaction_id", "discard_review": "transaction_id",
                    "save_reviews": "transaction_ids",
                    "save_manual": "manual_entry_id",
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
                self._last_message = result.get("message", "Saved.")
                self._progress(self._last_message)
                if not self._closing:
                    QTimer.singleShot(0, self.refresh)
        if action == "refresh" and self._continuation and not self._failed:
            continuation, self._continuation = self._continuation, None
            QTimer.singleShot(0, continuation)
        if self._closing:
            self._closing = False
            QTimer.singleShot(0, self.close)

    def _open_review_kind(self, kind: str) -> None:
        """
        Retain a report's Review destination when navigation waits for an unsaved answer to be saved.
        """
        if not self._guard_changes(lambda: self._open_review_kind(kind)):
            return
        self.navigate("Review")
        self.pages["Review"].select_kind(kind)

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

    def _choose_workspace(self) -> None:
        """
        Open another local workspace without moving or resetting the current one.
        """
        if not self._guard_changes(self._choose_workspace):
            return
        folder = QFileDialog.getExistingDirectory(self, "Open Aurum workspace", str(self.service.root))
        if folder and Path(folder).resolve() != self.service.root:
            self._new_window = MainWindow(Path(folder))
            self._new_window.show()
            self.close()


# %% 2. Helpers ----------------------------------------------------------------------------------------------
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
        Keep every painted pixel within the row Qt invalidates when hover or selection changes.
        """
        option = QStyleOptionViewItem(option)
        painter.save()
        painter.setClipRect(option.rect, Qt.ClipOperation.IntersectClip)
        if index.data(Qt.ItemDataRole.UserRole + 1):
            option.rect.adjust(18, 0, 0, 0)
        super().paint(painter, option, index)
        painter.restore()
