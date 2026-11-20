# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                    Gui - Activity                                                    #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum's activity screens let the user import statements and complete monthly accounting without editing files.

Tables show saved facts while detail panels collect human answers and manual instructions. Buttons emit
requests to the application controller; these widgets never open the database, call AI or change postings.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QTableWidget,
    QTableWidgetItem, QToolButton,
    QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.controls import (
    CentredComboBox, EntryTable, HeaderActionButton, PageActionRow, SearchField, confirm_discard,
)
from aurum.gui.icons import icon
from aurum.gui.theme import gui_colour, monetary_font, monetary_text
from aurum.gui.viewers import DETAIL_SECTION_GAP, DETAIL_TEXT_INSET, JournalDetails, TransactionDetails, _aud

# %% 1. Activity Tables --------------------------------------------------------------------------------------
class ActivityPage(QWidget):
    """
    Share a searchable saved-record table, a detail panel and safe selection handling across activity pages.
    """

    action_requested = Signal(str, object)
    dirty_changed = Signal(bool)

    def __init__(
        self, title: str, explanation: str, columns: tuple, identity: str, parent=None, *, spacing: int = 10,
    ):
        """
        Build the shared page layout and connect navigation without starting any workflow.
        """
        super().__init__(parent)
        self.identity = identity
        self.columns = columns
        self.records = []
        self.snapshot = {}
        self.current = None
        self._loading = False
        self._baseline = ""
        self._busy_buttons = {}
        self._busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(spacing)
        self.heading_block = QWidget()
        self.heading_block.setObjectName("activityHeading")
        heading_layout = QVBoxLayout(self.heading_block)
        heading_layout.setContentsMargins(0, 0, 0, 16)
        heading_layout.setSpacing(0)
        self.heading = QLabel(title)
        self.heading.setObjectName("workspacePageTitle")
        self.heading.setToolTip(explanation)
        self.heading.setAccessibleDescription(explanation)
        self.title_row = QHBoxLayout()
        self.title_row.setContentsMargins(0, 0, 0, 0)
        self.title_row.addWidget(self.heading)
        self.title_row.addStretch()
        heading_layout.addLayout(self.title_row)
        self.subtitle_row = PageActionRow()
        self.subtitle_row.setContentsMargins(0, 0, 0, 0)
        self.subtitle_row.addStrut(28)
        self.subtitle_row.addStretch(1)
        heading_layout.addLayout(self.subtitle_row)
        outer.addWidget(self.heading_block)
        self.activity_body = QStackedWidget()
        content = QWidget()
        self.activity_body.addWidget(content)
        self.activity_body.addWidget(QWidget())
        outer.addWidget(self.activity_body, 1)
        self.layout = QVBoxLayout(content)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(spacing)
        self.toolbar = QHBoxLayout()
        self.search = SearchField("Search this table", self)
        self.toolbar.addWidget(self.search, 1)
        self.layout.addLayout(self.toolbar)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = EntryTable(0, len(columns))
        self.table.setProperty("ledgerTable", True)
        self.table.setShowGrid(False)
        self.table.setHorizontalHeaderLabels([label for _, label in columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.splitter.addWidget(self.table)
        self.detail = QWidget()
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_layout.setContentsMargins(0, 12, 0, 0)
        self.splitter.addWidget(self.detail)
        self.splitter.setSizes([330, 300])
        self.layout.addWidget(self.splitter, 1)
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.hide()
        self.layout.addWidget(self.empty)
        self.search.textChanged.connect(self._filter_changed)
        self.table.itemSelectionChanged.connect(self._selection_changed)

    def refresh(self, snapshot: dict) -> None:
        """
        Retain unsaved edits when a background refresh supplies newer saved facts.
        """
        self.snapshot = snapshot

    def has_unsaved_changes(self) -> bool:
        """
        Report whether this page has an edited form that has not been saved.
        """
        return False

    def set_busy(self, busy: bool) -> None:
        """
        Suspend form edits and action buttons while leaving table inspection and navigation responsive.
        """
        self._busy = busy
        self.detail.setEnabled(not busy)
        if busy and not self._busy_buttons:
            self._busy_buttons = {
                button: button.isEnabled() for button in self.findChildren(QPushButton)
                if self.toolbar.indexOf(button) >= 0
            }
            for button in self._busy_buttons:
                button.setEnabled(False)
        elif not busy:
            for button, enabled in self._busy_buttons.items():
                button.setEnabled(enabled)
            self._busy_buttons = {}

    def discard_changes(self) -> None:
        """
        Restore the currently selected saved record.
        """
        self._show_record(self.current)
        self.dirty_changed.emit(False)

    def save_changes(self) -> None:
        """
        Submit an editable page's current form when the window offers Save before navigation.
        """
        save = getattr(self, "_save", None)
        if save is not None:
            save()

    def action_completed(self, action: str, result=None) -> None:
        """
        Let specialised pages acknowledge a successful save before their next snapshot refresh.
        """

    def select_record(self, identifier: str) -> bool:
        """
        Select a saved row by its stable identity, including a row hidden by the search text.
        """
        for index, record in enumerate(self.records):
            if str(record.get(self.identity, "")) == str(identifier):
                self.search.clear()
                self.table.setRowHidden(index, False)
                self.table.selectRow(index)
                return True
        return False

    def _set_records(self, records: list[dict]) -> None:
        """
        Replace table facts while preserving the selected form and any unsubmitted changes.
        """
        selected = str(self.current.get(self.identity, "")) if self.current else None
        dirty = self.has_unsaved_changes()
        self.records = records
        self._loading = True
        self.table.setRowCount(len(records))
        money_font = monetary_font(self.table.font())
        for row_index, record in enumerate(records):
            for column_index, (field, _) in enumerate(self.columns):
                value = record.get(field, "")
                if field in {"status", "review_status"}:
                    value = _status_text(value)
                display = _text(value).upper() if field in {"description", "summary"} else _text(value)
                monetary = field in {"amount", "display_amount", "debit", "credit", "total_debit",
                                     "total_credit"}
                if monetary:
                    display = monetary_text(value)
                item = QTableWidgetItem(display)
                item.setToolTip(display if monetary else _text(value))
                if monetary:
                    item.setFont(money_font)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_index, column_index, item)
        selected_index = next(
            (index for index, row in enumerate(records) if str(row.get(self.identity)) == selected), None,
        )
        if selected_index is not None:
            self.table.selectRow(selected_index)
            if not dirty:
                self.current = records[selected_index]
                self._show_record(self.current)
        elif not dirty:
            self.current = None
            self._show_record(None)
        self._loading = False
        self._filter_changed()

    def _filter_changed(self) -> None:
        """
        Filter table rows locally without discarding edits or sending an inference request.
        """
        query = self.search.text().casefold().strip()
        shown = 0
        for index, record in enumerate(self.records):
            visible = self._include_record(record) and (
                not query or query in " ".join(_text(value) for value in record.values()).casefold()
            )
            self.table.setRowHidden(index, not visible)
            shown += int(visible)
        self.table.set_empty(not shown)
        self.empty.hide()

    def _include_record(self, record: dict) -> bool:
        """
        Include every row unless a page supplies a more specific status filter.
        """
        return True

    def _selection_changed(self) -> None:
        """
        Protect unsaved form input before replacing it with another selected row.
        """
        if self._loading:
            return
        row_index = self.table.currentRow()
        if not 0 <= row_index < len(self.records):
            return
        record = self.records[row_index]
        if self.current and record.get(self.identity) == self.current.get(self.identity):
            return
        if not self._allow_discard():
            self._loading = True
            if self.current:
                self.select_record(str(self.current[self.identity]))
            else:
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)
            self._loading = False
            return
        self.current = record
        self._show_record(record)
        self.dirty_changed.emit(False)

    def _allow_discard(self) -> bool:
        """
        Keep pending edits unless the user explicitly chooses to discard them.
        """
        if not self.has_unsaved_changes():
            return True
        return confirm_discard(self)

    def _show_record(self, record: dict | None) -> None:
        """
        Provide a hook for the detail panel belonging to the selected record.
        """


class PostingTable(QTableWidget):
    """
    Display every account and amount in an event without requiring the user to read a prose summary.
    """

    def __init__(self, parent=None):
        """
        Prepare a read-only posting table with complete descriptions available in tooltips.
        """
        super().__init__(0, 6, parent)
        self.setHorizontalHeaderLabels([
            "Account", "Debit AUD", "Credit AUD", "Description", "Budget treatment", "Effective date",
        ])
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

    def set_postings(self, postings: list[dict]) -> None:
        """
        Show source-preserving rows, including each separate leg of a split or transfer.
        """
        self.setRowCount(len(postings))
        fields = ("account", "debit", "credit", "description", "budget_category", "effective_date")
        for index, line in enumerate(postings):
            for column, field in enumerate(fields):
                value = monetary_text(line.get(field)) if field in {"debit", "credit"} else (
                    _text(line.get(field))
                )
                item = QTableWidgetItem(value.upper() if field == "description" else value)
                item.setToolTip(value)
                if field in {"debit", "credit"}:
                    item.setFont(monetary_font(self.font()))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(index, column, item)
        for column in (0, 1, 2, 4, 5):
            self.resizeColumnToContents(column)
            self.setColumnWidth(column, max(110, min(230, self.columnWidth(column))))


# %% 2. Statement Imports ------------------------------------------------------------------------------------
class _TransactionDelegate(QStyledItemDelegate):
    """
    Present multiline source wording on one line while retaining the original text in the model.
    """

    def initStyleOption(self, option, index) -> None:
        """
        Flatten explicit line breaks before Qt elides the visible cell text.
        """
        super().initStyleOption(option, index)
        option.text = " ".join(option.text.splitlines())


class TransactionsPage(ActivityPage):
    """
    Browse bank movements and manual history together, with their original import and edit actions.
    """

    def __init__(self, parent=None):
        """
        Keep statement preparation, saved transactions and inline inspection in one monthly workspace.
        """
        super().__init__(
            "Transactions", "Inspect bank transactions and manual journal entries.",
            (
                ("date", "Date"), ("account", "Account"), ("description", "Description"),
                ("amount", "Amount"), ("source_file", "Source"), ("status", "Status"),
            ),
            "row_id", parent,
        )
        self.paths = []
        self._importing_paths = ()
        self._waiting_paths = []
        self._staging_paths = ()
        self._stage_automatic = set()
        self._stage_errors = {}
        self._statement_folder = ""
        self._refresh_error = ""
        self.setAcceptDrops(True)
        self.layout.removeItem(self.toolbar)
        self.layout.removeWidget(self.splitter)
        self.layout.removeWidget(self.empty)

        self.bank_sync = HeaderActionButton("Connect", icon_only=True)
        self.bank_sync.setIcon(icon("connect_bank", colour="#ffffff", size=16))
        self.bank_sync.setAccessibleName("Connect or sync bank accounts")
        self.bank_sync.clicked.connect(lambda: self.action_requested.emit(
            "redbark_sync" if self.snapshot.get("bank_feed", {}).get("configured")
            else "open_bank_settings", {},
        ))
        self.subtitle_row.setSpacing(8)
        self.subtitle_row.insertWidget(0, self.bank_sync, 0, Qt.AlignmentFlag.AlignLeft)
        self.new_button = HeaderActionButton("New entry", icon_only=True)
        self.new_button.setIcon(icon("add", colour="#ffffff", size=16))
        self.new_button.setAccessibleName("New manual entry")
        self.new_button.setToolTip("Add a manual entry")
        self.new_button.setEnabled(False)
        self.new_button.clicked.connect(lambda: self.action_requested.emit(
            "open_manual_editor", {"mode": "instruction"},
        ))
        self.subtitle_row.insertWidget(1, self.new_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.bank_status = QLabel()
        self.bank_status.setObjectName("workspaceHint")
        self.bank_status.setTextFormat(Qt.TextFormat.PlainText)
        self.bank_status.setWordWrap(True)
        self.bank_status.hide()
        self.layout.addWidget(self.bank_status)

        queue = QFrame()
        self.pdf_panel = queue
        queue.setObjectName("statementImportPanel")
        queue.setToolTip("Choose or drop PDFs")
        queue_layout = QVBoxLayout(queue)
        queue_layout.setContentsMargins(4, 10, 12, 10)
        queue_layout.setSpacing(6)
        queue_actions = QHBoxLayout()
        queue_actions.setSpacing(4)
        self.choose_button = self._icon_button("Choose statement PDFs", "choose_pdf")
        self.choose_button.clicked.connect(self._choose_files)
        queue_actions.addWidget(self.choose_button)
        self.queue_empty = QLabel("Choose PDFs")
        self.queue_empty.setObjectName("workspaceHint")
        queue_actions.addWidget(self.queue_empty, 0, Qt.AlignmentFlag.AlignVCenter)
        self.queue_count = QLabel()
        self.queue_count.setObjectName("workspaceStatus")
        queue_actions.addWidget(self.queue_count, 0, Qt.AlignmentFlag.AlignVCenter)
        queue_actions.addSpacing(16)
        self.retry_stage_button = self._icon_button("Retry PDF preparation", "retry")
        self.retry_stage_button.clicked.connect(self._retry_staging)
        queue_actions.addWidget(self.retry_stage_button)
        self.clear_button = self._icon_button("Clear the PDF queue", "undo")
        self.clear_button.clicked.connect(self._clear_files)
        queue_actions.addWidget(self.clear_button)
        self.import_button = QPushButton()
        self.import_button.setObjectName("statementImportAction")
        self.import_button.setFixedSize(36, 36)
        self.import_button.setIcon(icon("import", colour="#ffffff", size=20))
        self.import_button.setIconSize(QSize(20, 20))
        self.import_button.setProperty("primary", True)
        self.import_button.clicked.connect(self._import_files)
        queue_actions.addWidget(self.import_button)
        queue_actions.addStretch(1)
        queue_layout.addLayout(queue_actions)
        self.import_feedback = QLabel()
        self.import_feedback.setObjectName("workspaceHint")
        self.import_feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.import_feedback.setWordWrap(True)
        self.import_feedback.hide()
        feedback_row = QHBoxLayout()
        feedback_row.setContentsMargins(36, 0, 0, 0)
        self.import_feedback.setAlignment(Qt.AlignmentFlag.AlignLeft)
        feedback_row.addWidget(self.import_feedback)
        queue_layout.addLayout(feedback_row)
        self.layout.addWidget(queue)
        queue.hide()

        self.search.setPlaceholderText("Search transactions")
        self.search.setObjectName("statementSearch")
        self.search.setAccessibleName("Search transactions, accounts and statements")
        self.pdf_toggle = self._icon_button("Choose PDFs", "choose_pdf")
        self.pdf_toggle.setFixedSize(36, 36)
        self.pdf_toggle.clicked.connect(self._choose_files)
        self.toolbar.addWidget(self.pdf_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        self.layout.addLayout(self.toolbar)
        self.empty.setObjectName("statementCount")
        self.layout.addWidget(self.empty)

        self.table.setObjectName("statementTransactions")
        self.table.setItemDelegate(_TransactionDelegate(self.table))
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._column_widths = None
        self._column_timer = QTimer(self)
        self._column_timer.setSingleShot(True)
        self._column_timer.timeout.connect(self._size_columns)
        self.table.viewport().installEventFilter(self)
        self.table.horizontalHeaderItem(3).setToolTip("Amount in Australian dollars (AUD)")
        self.table.setMinimumHeight(120)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setMinimumSectionSize(36)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.layout.addWidget(self.table, 1)
        self.detail.hide()
        self.splitter.hide()
        self.transaction_details = TransactionDetails(self, manual_postings=True)
        self.table.doubleClicked.connect(lambda _index: self.transaction_details.edit_button.click())
        self.transaction_details.review_button.clicked.connect(lambda: self.action_requested.emit(
            "open_manual_review", {"manual_entry_id": self.current["manual_entry_id"]},
        ))
        self.transaction_details.action_requested.connect(self.action_requested.emit)
        self.transaction_details.dismissed.connect(self._clear_selection)
        self.detail_layout.setContentsMargins(0, DETAIL_SECTION_GAP - self.layout.spacing(), 0, 0)
        self.detail_layout.addWidget(self.transaction_details)
        self.detail.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.layout.addWidget(self.detail)
        self._render_queue()
        self._filter_changed()
        QWidget.setTabOrder(self.bank_sync, self.new_button)
        QWidget.setTabOrder(self.new_button, self.search)

    def refresh(self, snapshot: dict) -> None:
        """
        Show stored transactions and the latest import result without changing queued file choices.
        """
        super().refresh(snapshot)
        self._refresh_error = ""
        self._statement_folder = str(snapshot.get("statement_folder") or self._statement_folder)
        feed = snapshot.get("bank_feed", {})
        connected = bool(feed.get("configured"))
        self.bank_sync.setText("Sync" if connected else "Connect")
        self.bank_sync.setIcon(icon("sync_bank" if connected else "connect_bank",
                                   gui_colour(self, "on_accent", "#ffffff"), size=16))
        self.bank_sync.setAccessibleName("Sync bank accounts" if connected else "Connect bank accounts")
        self.bank_sync.setEnabled(not self._busy)
        mappings = [item for item in feed.get("mappings", []) if item.get("enabled")]
        errors = [str(item["error"]) for item in mappings if item.get("error")]
        if feed.get("connection_error"):
            errors.insert(0, str(feed["connection_error"]))
        checked = [str(item["last_success"]) for item in mappings if item.get("last_success")]
        self.bank_status.setText("Bank sync needs attention" if errors else "")
        self.bank_status.setVisible(bool(errors))
        self.bank_status.setToolTip("\n".join(errors) if errors else (
            "Last successful check: " + min(checked) + "\nRedbark may return cached bank activity."
            if checked else "Sync imports bank evidence. Process creates journal entries separately."
        ))
        self.bank_sync.setToolTip(
            "Sync bank accounts"
            if connected else "Connect Redbark in Setup → Accounts."
        )
        available = snapshot.get("ready") and not snapshot.get("error")
        self.new_button.setEnabled(bool(available and not self._busy))
        previous = self.current or {}
        self._set_records(_transaction_rows(snapshot))
        if self.current is None and previous.get("row_id", "").startswith("manual:"):
            for index, row in enumerate(self.records):
                if self.table.isRowHidden(index) or row.get("manual_entry_id") != previous["manual_entry_id"]:
                    continue
                self.table.selectRow(index)
                break
        for row, record in enumerate(self.records):
            source = str(record.get("source_file") or "")
            if record.get("source_label") == "Manual Journal":
                amount_cell = self.table.item(row, 3)
                amount_cell.setToolTip(
                    f"{amount_cell.text()} · Total journal debits" if record.get("postings")
                    else "Awaiting journal postings"
                )
            source_label = str(record.get("source_label") or Path(source).name)
            self.table.item(row, 4).setText(source_label)
            source_tip = f"{source_label}\n{source}" if source and source != source_label else source_label
            self.table.item(row, 4).setToolTip(source_tip)
            for column in (3, 4, 5):
                self.table.item(row, column).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._column_widths = None
        self._size_columns()

    def eventFilter(self, watched, event) -> bool:
        """
        Reflow after viewport changes, including the appearance of a vertical scrollbar.
        """
        if watched is self.table.viewport() and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self._column_timer.start(0)
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        """
        Let the table settle before sharing its new width among readable metadata and Description.
        """
        super().resizeEvent(event)
        self._column_timer.start(0)

    def _size_columns(self) -> None:
        """
        Fit date, amount and status first, then share the remaining space with account and source.
        """
        available = self.table.viewport().width()
        if self._column_widths is None:
            widths = {}
            header_metrics = QFontMetrics(self.table.horizontalHeader().font())
            option = QStyleOptionViewItem()
            self.table.initViewItemOption(option)
            for column in (0, 1, 3, 4, 5):
                width = header_metrics.horizontalAdvance(self.table.horizontalHeaderItem(column).text()) + 28
                if self.table.rowCount():
                    styled = QStyleOptionViewItem(option)
                    self.table.itemDelegate().initStyleOption(styled, self.table.model().index(0, column))
                    metrics = QFontMetrics(styled.font)
                    texts = {self.table.item(row, column).text() for row in range(self.table.rowCount())}
                    width = max(width, max(metrics.horizontalAdvance(text) for text in texts) + 28)
                widths[column] = width
            self._column_widths = widths
        widths = dict(self._column_widths)
        flexible = max(0, available - sum(widths[column] for column in (0, 3, 5)) - 140)
        natural = widths[1] + widths[4]
        if flexible < natural:
            minimum = min(96, flexible // 2)
            account = max(minimum, min(flexible - minimum, round(flexible * widths[1] / natural)))
            widths[1], widths[4] = account, flexible - account
        for column, width in widths.items():
            self.table.setColumnWidth(column, width)

    def set_statement_folder(self, folder: str) -> None:
        """
        Set the chooser's workspace folder without creating or inspecting any files.
        """
        self._statement_folder = str(folder)

    def defer_preparation(self) -> None:
        """
        Stop deferred preparation after close intent while retaining every selected source for retry.
        """
        self._stage_automatic.clear()
        self._update_queue_controls()

    def set_busy(self, busy: bool) -> None:
        """
        Keep read-only inspection and adding PDFs available while preventing competing queue actions.
        """
        self._busy = busy
        self.bank_sync.setEnabled(not busy)
        available = self.snapshot.get("ready") and not self._refresh_error and not self.snapshot.get("error")
        self.new_button.setEnabled(bool(available and not busy))
        self._update_queue_controls()
        self.transaction_details.set_busy(busy)
        if not busy:
            QTimer.singleShot(0, self._stage_next)

    def action_started(self, action: str, payload: dict) -> None:
        """
        Capture the accepted source order before the worker can return renamed managed copies.
        """
        if action == "stage_statements":
            self._staging_paths = tuple(dict.fromkeys(str(Path(path).resolve()) for path in payload["paths"]))
            for path in self._staging_paths:
                if path not in self._waiting_paths:
                    self._waiting_paths.append(path)
            self._stage_automatic.difference_update(self._staging_paths)
            self._render_queue()
            return
        if action != "import_statements":
            return
        self._importing_paths = tuple(dict.fromkeys(str(Path(path).resolve()) for path in payload["paths"]))
        for path in self._importing_paths:
            if path not in self.paths:
                self.paths.append(path)
        self._render_queue()

    def action_failed(self, action: str, message: str) -> None:
        """
        Keep the submitted files available after a worker failure, including any completed atomic imports.
        """
        if action == "refresh":
            self._refresh_error = message
            self.new_button.setEnabled(False)
            self.transaction_details.set_stale(True)
            self._filter_changed()
            return
        if action == "stage_statements":
            for path in self._staging_paths:
                self._stage_errors[path] = message
            self._staging_paths = ()
            self._set_result(
                f"PDF preparation could not finish: {message}", "Preparation stopped. Retry when ready.",
            )
            self._render_queue()
            return
        if action != "import_statements":
            return
        self._importing_paths = ()
        self._set_result(
            f"Import could not finish: {message}\n\n"
            "Queued PDFs are kept for retry. Any completed imports remain saved.",
            "Import stopped. Queued PDFs remain available for retry.",
        )
        self._render_queue()

    def action_completed(self, action: str, result=None) -> None:
        """
        Remove only successful source files while retaining failed, unprocessed and newly added PDFs.
        """
        if action == "stage_statements":
            self._staging_completed(result)
            return
        if action != "import_statements":
            return
        items = result if isinstance(result, list) else []
        if isinstance(result, dict):
            items = result.get("files", result.get("results", []))
        finished, messages = set(), []
        for index, item in enumerate(items):
            source = self._importing_paths[index] if index < len(self._importing_paths) else None
            status = item.get("status", "unknown")
            if source is not None and status in {"imported", "skipped"}:
                finished.add(source)
            path = item.get("path", item.get("name", "PDF"))
            name = Path(source or path).name
            if status == "imported":
                outcome = f"Imported: {item.get('inserted', 0)} added from {item.get('parsed', 0)} rows."
            elif status == "skipped":
                outcome = "Already imported."
            elif status == "failed":
                outcome = "Failed: " + (item.get("message") or "Check this statement before retrying.")
            else:
                outcome = item.get("message") or "No completed outcome; kept in the queue."
            messages.append(f"{name} — {outcome}")
        unfinished = set(self._importing_paths) - finished
        self.paths = [path for path in self.paths if path not in finished]
        self._importing_paths = ()
        if not items:
            messages.append("No files completed. Queued PDFs are kept for retry.")
        if self.paths:
            label = "PDF" if len(self.paths) == 1 else "PDFs"
            messages.append(f"\nRemaining queue: {len(self.paths)} {label}.")
        if any(item.get("status") == "imported" for item in items):
            messages.append("\nOpen Review to process the month when ready.")
        if unfinished:
            self._set_result("\n".join(messages), "Some PDFs need retrying.")
        else:
            self.import_feedback.hide()
        self._render_queue()

    def _icon_button(self, label: str, symbol: str) -> QToolButton:
        """
        Keep common statement actions compact and named for tooltips, keyboard and accessibility.
        """
        button = QToolButton(self)
        button.setObjectName("statementIconAction")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAutoRaise(True)
        button.setFixedSize(32, 32)
        button.setIconSize(QSize(18, 18))
        button.setIcon(icon(symbol, size=18))
        button.setAccessibleName(label)
        button.setToolTip(label)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return button

    def _set_result(self, detail: str, summary: str) -> None:
        """
        Show an actionable PDF issue with complete per-file details available on hover.
        """
        self.import_feedback.setText(summary)
        self.import_feedback.setToolTip(detail)
        self.import_feedback.setAccessibleDescription(detail)
        self.import_feedback.show()
        self.pdf_panel.show()

    def _stage_next(self) -> None:
        """
        Prepare only newly chosen or explicitly retried files when the shared worker becomes available.
        """
        if self._busy or self._staging_paths or self._importing_paths:
            return
        paths = [path for path in self._waiting_paths if path in self._stage_automatic]
        if paths:
            self.action_requested.emit("stage_statements", {"paths": paths})

    def _retry_staging(self) -> None:
        """
        Retry retained source files only after an explicit action, with no automatic error loop.
        """
        if self._busy or self._staging_paths or self._importing_paths:
            return
        self._stage_automatic.update(self._waiting_paths)
        self._stage_next()

    def _staging_completed(self, result) -> None:
        """
        Queue acknowledged managed copies while retaining failed and unattempted source paths.
        """
        items = result if isinstance(result, list) else []
        if isinstance(result, dict):
            items = result.get("data", result.get("files", result.get("results", []))) or []
        messages, attempted = [], set()
        for item in items:
            source = str(item.get("source_path") or "")
            if source not in self._staging_paths:
                continue
            attempted.add(source)
            status, destination = item.get("status"), str(item.get("path") or "")
            if status in {"staged", "unchanged"} and destination:
                self._waiting_paths = [path for path in self._waiting_paths if path != source]
                self._stage_errors.pop(source, None)
                if destination not in self.paths:
                    self.paths.append(destination)
                messages.append(f"{Path(source).name} — Ready as {Path(destination).name}.")
            else:
                message = item.get("message") or "Could not prepare this PDF."
                self._stage_errors[source] = message
                messages.append(f"{Path(source).name} — {message}")
        for source in self._staging_paths:
            if source not in attempted:
                self._stage_errors[source] = "Preparation stopped before this PDF completed."
        self._staging_paths = ()
        if not messages:
            messages.append("No PDFs were prepared. Selected files remain available for retry.")
        if self._waiting_paths:
            self._set_result("\n".join(messages), "Some PDFs need retrying.")
        else:
            self.import_feedback.hide()
        self._render_queue()
        QTimer.singleShot(0, self._stage_next)

    def _selection_changed(self) -> None:
        """
        Inspect the selected row even when macOS sends its selection signal before updating the current cell.
        """
        if self._loading:
            return
        selected = self.table.selectionModel().selectedRows()
        row = selected[0].row() if selected else -1
        self.current = self.records[row] if 0 <= row < len(self.records) else None
        self._show_record(self.current)

    def _filter_changed(self) -> None:
        """
        Show availability errors and clear details when the selected transaction is filtered out.
        """
        super()._filter_changed()
        if self._refresh_error or self.snapshot.get("error") or self.snapshot.get("ready") is False:
            message = "Transactions unavailable. Check Setup and refresh the workspace."
        else:
            message = ""
        self.empty.setText(message)
        self.empty.setVisible(bool(message))
        if self.current:
            selected = next((row for row, record in enumerate(self.records)
                             if record.get(self.identity) == self.current.get(self.identity)), None)
            if selected is None or self.table.isRowHidden(selected):
                self._loading = True
                self.table.clearSelection()
                self.table.setCurrentCell(-1, -1)
                self.current = None
                self._show_record(None)
                self._loading = False
        elif self.table.currentRow() >= 0:
            self._loading = True
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self._loading = False

    def dragEnterEvent(self, event) -> None:
        """
        Accept local PDF drops and leave unsupported drag types to their source application.
        """
        if event.mimeData().hasUrls() and any(
            url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() == ".pdf"
            for url in event.mimeData().urls()
        ):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """
        Request preparation of chosen PDFs without importing their transactions.
        """
        self._add_files([
            url.toLocalFile() for url in event.mimeData().urls()
            if url.isLocalFile() and Path(url.toLocalFile()).suffix.lower() == ".pdf"
        ])
        event.acceptProposedAction()

    def _choose_files(self) -> None:
        """
        Start the chooser in this workspace's statement folder without creating it on page opening.
        """
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Choose bank statements", self._statement_folder, "PDF statements (*.pdf)",
        )
        self._add_files(paths)

    def _add_files(self, paths: list[str]) -> None:
        """
        Retain selected source paths and ask the worker to prepare copies when it becomes available.
        """
        added = False
        for path in paths:
            if Path(path).suffix.lower() != ".pdf":
                continue
            resolved = str(Path(path).resolve())
            if resolved not in self.paths and resolved not in self._waiting_paths:
                self._waiting_paths.append(resolved)
                self._stage_automatic.add(resolved)
                added = True
        if added:
            self.import_feedback.hide()
        self._render_queue()
        QTimer.singleShot(0, self._stage_next)

    def _render_queue(self) -> None:
        """
        Summarise the selected PDFs without expanding a filename list into the transaction workspace.
        """
        count = len(self.paths) + len(self._waiting_paths)
        self.pdf_panel.setVisible(bool(
            count or self._staging_paths or self._importing_paths or self._stage_errors
            or not self.import_feedback.isHidden()
        ))
        self.queue_empty.setVisible(not count)
        self.queue_count.setVisible(bool(count))
        self._update_queue_controls()

    def _update_queue_controls(self) -> None:
        """
        Present one clear import action and prevent removal of files while a job owns the queue.
        """
        count, waiting = len(self.paths), len(self._waiting_paths)
        available = not self._busy and not self._importing_paths and not self._staging_paths
        import_label = f"Import {count} PDF" + ("s" if count != 1 else "")
        self.import_button.setToolTip(import_label)
        self.import_button.setAccessibleName(import_label)
        self.import_button.setVisible(bool(count))
        self.import_button.setEnabled(bool(count) and available)
        retry = any(path not in self._stage_automatic for path in self._waiting_paths)
        self.retry_stage_button.setVisible(bool(retry and not self._staging_paths))
        self.retry_stage_button.setEnabled(bool(retry) and available)
        self.clear_button.setVisible(bool(count + waiting))
        self.clear_button.setEnabled(bool(count + waiting) and available)
        total = count + waiting
        label = "PDF" if total == 1 else "PDFs"
        message = f"{total} {label}" if total else ""
        if total and not waiting and not self._importing_paths:
            message += " ready"
        self.queue_count.setText(message)

    def _clear_files(self) -> None:
        """
        Clear only the pending selection, leaving original files and imported data untouched.
        """
        if self._busy or self._importing_paths or self._staging_paths:
            return
        self.paths = []
        self._waiting_paths = []
        self._stage_automatic.clear()
        self._stage_errors.clear()
        self.import_feedback.hide()
        self._render_queue()

    def _import_files(self) -> None:
        """
        Ask the controller to import the selected PDFs in a background job.
        """
        if self.paths and not self._busy and not self._importing_paths and not self._staging_paths:
            self.action_requested.emit("import_statements", {"paths": list(self.paths)})

    def _clear_selection(self) -> None:
        """
        Close only the read-only transaction panel, retaining search and all import choices.
        """
        self._loading = True
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.current = None
        self._show_record(None)
        self._loading = False

    def select_record(self, identifier: str) -> bool:
        """
        Reveal the exact linked bank line and display its details below the saved-transaction table.
        """
        for row in self.records:
            if identifier in (row["row_id"], row.get("transaction_id"), row.get("manual_entry_id")):
                if super().select_record(row["row_id"]):
                    self._selection_changed()
                    return bool(self.current and self.current["row_id"] == row["row_id"])
        return False

    def _show_record(self, record: dict | None) -> None:
        """
        Keep the bottom detail panel aligned with the selected source and current snapshot state.
        """
        self.transaction_details.update_record(record, self.snapshot)
        manual = record if record and record.get("manual_entry_id") else {}
        direct = manual.get("entry_mode") == "direct"
        self.transaction_details.set_header_action(
            ("Edit journal entry" if direct else "Edit instruction") if manual else None,
            lambda: self.action_requested.emit("open_manual_editor", {
                "manual_entry_id": manual["manual_entry_id"],
                "journal_entry_id": manual.get("journal_entry_id"),
                "mode": "direct" if direct else "instruction",
            }),
        )
        self.transaction_details.set_stale(bool(self._refresh_error or self.snapshot.get("error")))
        self.detail.setVisible(record is not None)


# %% 3. Human Review -----------------------------------------------------------------------------------------
class ReviewPage(ActivityPage):
    """
    Bring bank questions, unfinished instructions and investment classifications into one review queue.
    """

    def __init__(self, parent=None):
        """
        Keep batch processing and saving together while the selected item supplies its own next action.
        """
        super().__init__(
            "Review", "Resolve bank questions, manual instructions and investment movements.",
            (("date", "Date"), ("description", "Description"), ("display_amount", "Amount"),
             ("status_label", "Status")),
            "review_id", parent,
        )
        self._pending_submission = None
        self._drafts = {}
        self._awaiting_refresh = False
        self._feedback = ""
        self._batch_feedback = ""
        self._refresh_error = ""
        self._processing = False
        self._bank_aliases = {}
        self._review_aliases = {}
        self.process_button = HeaderActionButton("Process", icon_only=True)
        self.process_button.clicked.connect(lambda: self.action_requested.emit("process_journal", {}))
        self.subtitle_row.setSpacing(8)
        self.subtitle_row.insertWidget(0, self.process_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.save_button = HeaderActionButton("Save all edited responses", icon_only=True)
        self.save_button.setIcon(icon("save", colour="#ffffff", size=16))
        self.save_button.clicked.connect(self._save)
        self.subtitle_row.insertWidget(1, self.save_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.draft_count = QLabel()
        self.draft_count.setObjectName("reviewDraftCount")
        self.subtitle_row.insertWidget(2, self.draft_count)
        self.discard_changes_button = HeaderActionButton("Discard all unsaved responses", icon_only=True)
        self.discard_changes_button.setProperty("primary", False)
        self.discard_changes_button.setIcon(icon("undo", size=16))
        self.discard_changes_button.clicked.connect(self._discard_local_changes)
        self.subtitle_row.insertWidget(3, self.discard_changes_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.save_status = QLabel()
        self.save_status.setObjectName("workspaceError")
        self.save_status.setWordWrap(True)
        self.save_status.setTextFormat(Qt.TextFormat.PlainText)
        self.save_status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.layout.insertWidget(0, self.save_status)
        self.layout.removeItem(self.toolbar)
        self.layout.removeWidget(self.empty)
        self.splitter.setOrientation(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("reviewSplit")
        self.splitter.setHandleWidth(16)
        self.splitter.setChildrenCollapsible(False)
        queue = QWidget()
        queue.setMinimumWidth(360)
        queue_layout = QVBoxLayout(queue)
        queue_layout.setContentsMargins(0, 0, 0, 0)
        queue_layout.setSpacing(8)
        self.search.setPlaceholderText("Search reviews")
        self.filter = CentredComboBox()
        self.filter.setObjectName("reviewFilter")
        self.filter.setView(QListView(self.filter))
        self.filter.setAccessibleName("Review work status")
        self.filter.addItems(["Posted", "Review", "Ready", "All"])
        self.filter.setCurrentIndex(1)
        self.filter.currentIndexChanged.connect(self._status_filter_changed)
        self.filter.setFixedWidth(112)
        self.toolbar.addWidget(self.filter)
        queue_layout.addLayout(self.toolbar)
        self.empty.setObjectName("workspaceStatus")
        self.empty.setWordWrap(True)
        queue_layout.addWidget(self.empty)
        self.visibility_warning = QLabel()
        self.visibility_warning.setObjectName("reviewVisibilityWarning")
        self.visibility_warning.setWordWrap(True)
        self.visibility_warning.hide()
        queue_layout.addWidget(self.visibility_warning)
        self.splitter.insertWidget(0, queue)
        queue_layout.addWidget(self.table, 1)
        self.table.setMinimumHeight(140)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeaderItem(2).setToolTip(
            "Journal entries show their unsigned total. Unposted bank rows show the signed source amount."
        )
        self.detail.setMinimumWidth(320)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_layout.setSpacing(8)
        self.empty_detail = QWidget()
        empty_layout = QVBoxLayout(self.empty_detail)
        empty_layout.setContentsMargins(28, 28, 28, 28)
        empty_layout.setSpacing(10)
        empty_layout.addStretch()
        empty_layout.addStretch()
        self.detail_layout.addWidget(self.empty_detail, 1)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.detail_layout.addWidget(self.detail_scroll, 1)
        content = QWidget()
        self.detail_scroll.setWidget(content)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 10, 0)
        content_layout.setSpacing(10)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.journal_details = JournalDetails(actions=False, dismissible=False, compact=True)
        self.transaction_details = TransactionDetails(dismissible=False, compact=True)
        content_layout.addWidget(self.journal_details)
        content_layout.addWidget(self.transaction_details)
        self.manual_details = QWidget()
        manual_layout = QVBoxLayout(self.manual_details)
        manual_layout.setContentsMargins(DETAIL_TEXT_INSET, 8, 14, 8)
        manual_layout.setSpacing(6)
        self.manual_instruction = QLabel()
        self.manual_instruction.setWordWrap(True)
        self.manual_instruction.setTextFormat(Qt.TextFormat.PlainText)
        self.manual_instruction.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.manual_instruction.setAccessibleName("Original manual instruction")
        manual_header = QHBoxLayout()
        manual_header.addWidget(self.manual_instruction, 1)
        self.manual_edit = QToolButton()
        self.manual_edit.setObjectName("viewerIconAction")
        self.manual_edit.setFixedSize(32, 32)
        self.manual_edit.setIconSize(QSize(18, 18))
        self.manual_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.manual_edit.clicked.connect(self._activate_header_action)
        manual_header.addWidget(self.manual_edit, 0, Qt.AlignmentFlag.AlignTop)
        manual_layout.addLayout(manual_header)
        self.manual_date = QLabel()
        self.manual_date.setObjectName("workspaceHint")
        manual_layout.addWidget(self.manual_date)
        content_layout.addWidget(self.manual_details)
        self.question_section = QWidget()
        section_layout = QVBoxLayout(self.question_section)
        section_layout.setContentsMargins(0, 14, 0, 0)
        section_layout.setSpacing(8)
        section_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.question = QLabel()
        self.question.setObjectName("reviewQuestion")
        self.question.setContentsMargins(DETAIL_TEXT_INSET, 0, 14, 0)
        self.question.setWordWrap(True)
        self.question.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.question.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.question.setTextFormat(Qt.TextFormat.PlainText)
        self.question.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        section_layout.addWidget(self.question)
        self.response_panel = QWidget()
        self.response_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        editor_layout = QVBoxLayout(self.response_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(6)
        editor_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.discard_button = QToolButton()
        self.discard_button.setObjectName("viewerIconAction")
        self.discard_button.setIcon(icon("remove", size=16))
        self.discard_button.setIconSize(QSize(16, 16))
        self.discard_button.setFixedSize(28, 28)
        self.discard_button.setAccessibleName("Discard saved pending response")
        self.discard_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.discard_button.clicked.connect(self._discard_pending)
        editor_layout.addWidget(self.discard_button, 0, Qt.AlignmentFlag.AlignRight)
        self.answer = QPlainTextEdit()
        self.answer.setObjectName("reviewResponse")
        self.answer.document().setDocumentMargin(0)
        self.answer.setFixedHeight(128)
        self.answer.setAccessibleName("Response")
        self._response_help = (
            "Save #remember followed by a reusable rule to share it with both AI workflows. "
            "A trailing #remember keeps the whole response. Edit or remove the tag to update memory."
        )
        self._set_response_prompt("")
        editor_layout.addWidget(self.answer)
        section_layout.addWidget(self.response_panel, 0, Qt.AlignmentFlag.AlignTop)
        self.other_panel = QWidget()
        self.other_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        other_layout = QVBoxLayout(self.other_panel)
        other_layout.setContentsMargins(DETAIL_TEXT_INSET, 0, 14, 0)
        other_layout.setSpacing(10)
        self.other_details = QLabel()
        self.other_details.setObjectName("workspaceHint")
        self.other_details.setWordWrap(True)
        self.other_details.setTextFormat(Qt.TextFormat.PlainText)
        self.other_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        other_layout.addWidget(self.other_details)
        self.other_action = QPushButton()
        self.other_action.clicked.connect(self._open_other)
        other_layout.addWidget(self.other_action, 0, Qt.AlignmentFlag.AlignLeft)
        self.other_panel.hide()
        section_layout.addWidget(self.other_panel)
        content_layout.addWidget(self.question_section, 0, Qt.AlignmentFlag.AlignTop)
        content_layout.addStretch()
        self.splitter.setSizes([450, 420])
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)

        self.answer.textChanged.connect(self._edited)
        self._show_record(None)
        self._filter_changed()
        QWidget.setTabOrder(self.process_button, self.save_button)
        QWidget.setTabOrder(self.save_button, self.discard_changes_button)
        QWidget.setTabOrder(self.discard_changes_button, self.search)

    def _queue_rows(self, snapshot: dict) -> list[dict]:
        """
        Combine saved review sources without replacing their original IDs, dates or revision evidence.
        """
        terminal = {"applied", "posted", "withdrawn", "superseded"}
        manuals = {
            str(row["manual_entry_id"]): row for row in snapshot.get("manuals", [])
            if not (row.get("retired") or row.get("is_savings"))
        }
        unfinished = {identifier for identifier, row in manuals.items() if row.get("status") not in terminal}
        self._bank_aliases = {}
        rows = []
        for source in snapshot.get("reviews", []):
            identifier = str(source["transaction_id"])
            manual = source.get("blocking_manual_entry_id") or source.get("manual_entry_id")
            owner = f"manual:{manual}" if str(manual) in unfinished else ""
            if owner:
                self._bank_aliases[identifier] = owner
            rows.append({
                **source, "review_id": f"bank:{identifier}", "kind": "bank", "type_label": "Bank",
                "suppressed_by": owner,
            })
        for identifier, source in manuals.items():
            rows.append({
                **source, "review_id": f"manual:{identifier}", "kind": "manual", "type_label": "Manual",
                "description": source.get("instruction") or source.get("summary") or "Manual instruction",
            })
        for source in snapshot.get("investment_reviews", []):
            rows.append({
                **source,
                "review_id": f"investment:{source['journal_entry_id']}:{source['line_number']}",
                "kind": "investment", "type_label": "Investment",
            })
        for source in snapshot.get("budget_reviews", []):
            rows.append({
                **source, "review_id": f"budget:{source['journal_entry_id']}:{source['issue_code']}",
                "kind": "budget", "type_label": "Budget",
            })
        feed = snapshot.get("bank_feed", {})
        for source in feed.get("review_conflicts", feed.get("conflicts", [])):
            rows.append({
                **source, "conflict_kind": source.get("kind"),
                "review_id": f"feed:{source['id']}", "kind": "feed", "type_label": "Bank import",
                "source_label": "Redbark · ANZ Plus",
                "account": source.get("account_name", ""), "bank_source": source.get("payload", {}),
                "status": "import_conflict",
            })
        entries = {str(row["journal_entry_id"]): row for row in snapshot.get("entries", [])}
        labels = {"needs_answer": "Review", "ready": "Ready", "retry": "Review", "complete": "Posted"}
        for row in rows:
            state = self._work_state(row)
            row["work_state"] = state
            row["status_label"] = (
                _status_text(row.get("status")) if state == "complete" and (
                    not row.get("journal_entry_id") or row.get("status") in {"withdrawn", "superseded"}
                )
                else labels[state]
            )
            entry = row.get("journal_entry") or entries.get(str(row.get("journal_entry_id"))) or row
            total = _posting_total(entry.get("postings", []))
            row["display_amount"] = total if total is not None else (
                row.get("amount") if row["kind"] in {"bank", "feed"} else "—"
            )
            row["amount_hint"] = (
                "Total journal debits; this amount has no cash-flow direction." if total is not None else
                "Signed bank transaction amount." if row["kind"] in {"bank", "feed"} else
                "No journal amount is available yet."
            )
        rows = self._completed_rows(rows, entries, snapshot)
        order = {"needs_answer": 0, "retry": 1, "ready": 2, "complete": 3}
        return sorted(rows, key=lambda row: (
            order[row["work_state"]], str(row.get("date", "")), row["review_id"],
        ))

    def _completed_rows(self, rows: list[dict], entries: dict, snapshot: dict) -> list[dict]:
        """
        Add one completed event per journal identity while retaining actionable work and response routes.
        """
        active = [row for row in rows if row["work_state"] != "complete"]
        history = [row for row in rows if row["work_state"] == "complete" and (
            not row.get("journal_entry_id") or row.get("status") in {"withdrawn", "superseded"}
        )]
        by_source = {}
        for identifier, entry in entries.items():
            sources = entry.get("source_transactions") or [
                line["transaction_id"] for line in entry.get("postings", []) if line.get("transaction_id")
            ]
            for source in sources:
                by_source[str(source)] = identifier
        blocked = {}
        for row in active:
            if row.get("journal_entry_id"):
                blocked.setdefault(str(row["journal_entry_id"]), row)
            sources = list(row.get("source_transactions", [])) + list(row.get("candidates", []))
            if row.get("transaction_id"):
                sources.append(row["transaction_id"])
            for source in sources:
                if str(source) in by_source:
                    blocked.setdefault(by_source[str(source)], row)
        completed = {}
        for row in rows:
            if row["work_state"] == "complete" and row.get("journal_entry_id") and row not in history:
                completed.setdefault(str(row["journal_entry_id"]), []).append(row)
        self._review_aliases = {}
        posted = []
        start, end = str(snapshot.get("period_start") or ""), str(snapshot.get("period_end") or "")
        for identifier, entry in entries.items():
            candidates = completed.get(identifier, [])
            if identifier in blocked:
                representative = blocked[identifier]
            elif not snapshot.get("activity_all") and (
                (start and str(entry.get("date") or "") < start)
                or (end and str(entry.get("date") or "") > end)
            ):
                continue
            else:
                representative = min(
                    candidates, key=lambda row: (row["kind"] != "manual", row["review_id"]),
                ) if candidates else {
                    **entry, "review_id": f"journal:{identifier}", "kind": "journal",
                    "status": "posted", "status_label": "Posted", "work_state": "complete",
                    "display_amount": _posting_total(entry.get("postings", [])) or "—",
                    "amount_hint": "Total journal debits; this amount has no cash-flow direction.",
                }
                representative["posted_complete"] = True
                posted.append(representative)
            for row in candidates:
                self._review_aliases[row["review_id"]] = representative["review_id"]
                if row.get("transaction_id"):
                    self._bank_aliases.setdefault(str(row["transaction_id"]), representative["review_id"])
        return active + history + posted

    def _work_state(self, record: dict) -> str:
        """
        Describe the next action from saved processing states, without treating old questions as new answers.
        """
        if record.get("kind") == "journal":
            return "complete"
        if record.get("kind") in {"investment", "feed", "budget"}:
            return "needs_answer"
        status = str(record.get("status", "")).casefold()
        review = str(record.get("review_status", "")).casefold()
        retry = {"failed", "error", "interrupted", "requesting", "processing"}
        questions = {"needs_review", "needs_answer", "question", "unsupported", "manual_required", "stale"}
        if review == "stale":
            return "needs_answer"
        if review in retry:
            return "retry"
        if record.get("kind") == "manual":
            if status in {"applied", "posted", "withdrawn", "superseded"}:
                return "complete"
            if status in retry:
                return "retry"
            if status in {"pending", "submitted", "ready"}:
                return "ready"
            return "needs_answer" if status in questions or record.get("question") else "ready"
        if review in {"pending", "submitted"} and str(record.get("human_review") or "").strip():
            return "ready"
        if review in questions:
            return "needs_answer"
        if status in retry:
            return "retry"
        if status != "posted" and (status in questions or record.get("question")):
            return "needs_answer"
        return "complete" if status == "posted" else "ready"

    def refresh(self, snapshot: dict) -> None:
        """
        Refresh saved facts while retaining every edited answer and its original revision.
        """
        self._capture_draft()
        super().refresh(snapshot)
        self._awaiting_refresh = False
        self._refresh_error = ""
        rows = self._queue_rows(snapshot)
        by_id = {row[self.identity]: row for row in rows}
        for identifier, draft in self._drafts.items():
            day = draft["record"].get("date", "")
            in_period = snapshot.get("period_start", "") <= day <= snapshot.get("period_end", "9999")
            if identifier in by_id or snapshot.get("activity_all") or in_period:
                by_id[identifier] = draft["record"]
        rows = list(by_id.values())
        selected = self.current.get(self.identity) if self.current else None
        if selected not in by_id and selected in self._drafts:
            self.current = None
            self._loading = True
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self._loading = False
            self._show_record(None)
        elif selected and selected not in self._drafts:
            selected = selected if selected in by_id else self._review_aliases.get(selected, selected)
            self.current = by_id.get(selected)
        self._set_records(rows)
        if selected and selected not in self._drafts:
            self._show_record(self.current)
        for column, width in ((0, 104), (2, 104), (3, 88)):
            self.table.setColumnWidth(column, width)
        for index, row in enumerate(self.records):
            if row[self.identity] in self._drafts:
                self.table.item(index, 3).setText("Unsaved")
            self.table.item(index, 2).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.item(index, 2).setToolTip(row.get("amount_hint", ""))
            self.table.item(index, 3).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            question = row.get("question") or row.get("reason") or row.get("message") or row.get("review_result")
            if not question and row["kind"] == "investment":
                question = (
                    f"Choose the investment treatment for line {row['line_number']}: {row.get('account', '')}."
                )
            self.table.item(index, 1).setToolTip(
                f"{row.get('account', '')}\n{row.get('description', '')}\n"
                f"{row.get('display_amount', '')} AUD\n{row['status_label']}\n{question or ''}"
            )
        month = snapshot.get("month") or "the selected reporting month"
        dates = f"{snapshot.get('period_start', '')} to {snapshot.get('period_end', '')}"
        self.process_button.setToolTip(f"Process {snapshot.get('month') or 'selected month'}")
        self.process_button.setAccessibleDescription(
            f"Process saved manual instructions, bank answers and bank transactions for {month} ({dates}), "
            "including complete linked events outside the month. Classify investment movements separately."
        )
        self._update_controls()

    def has_unsaved_changes(self) -> bool:
        """
        Report all retained local answers, including drafts outside the current selection or filter.
        """
        return bool(
            self._drafts or self.current and self.current.get("kind") in {"bank", "manual"}
            and hasattr(self, "answer")
            and self.answer.toPlainText() != self._baseline
        )

    def _capture_draft(self) -> None:
        """
        Retain the exact multiline answer with the facts and concurrency tokens originally displayed.
        """
        if (
            not self.current or self.current.get("kind") not in {"bank", "manual"}
            or not hasattr(self, "answer")
        ):
            return
        identifier = self.current[self.identity]
        text = self.answer.toPlainText()
        if text != self._baseline:
            self._drafts[identifier] = {"record": deepcopy(self.current), "text": text}
        else:
            self._drafts.pop(identifier, None)

    def discard_changes(self) -> None:
        """
        Restore the latest saved response only after an explicit decision to discard local input.
        """
        identifier = self.current.get(self.identity) if self.current else None
        self._drafts.clear()
        self._batch_feedback = ""
        self.current = next(
            (row for row in self._queue_rows(self.snapshot) if row.get(self.identity) == identifier), None,
        )
        self._show_record(self.current)
        self.refresh(self.snapshot)
        self.dirty_changed.emit(False)

    def set_busy(self, busy: bool) -> None:
        """
        Block writes while keeping the combined queue and saved evidence available for inspection.
        """
        self._busy = busy
        self._update_controls()

    def select_record(self, identifier: str) -> bool:
        """
        Keep raw bank deep links compatible, redirecting duplicate attention to its available manual owner.
        """
        raw = str(identifier)
        bank_id = f"bank:{raw}"
        target = bank_id if bank_id in self._drafts else self._bank_aliases.get(raw, bank_id)
        prefixes = ("bank:", "manual:", "investment:", "feed:", "budget:", "journal:")
        return self.select_item(raw if raw.startswith(prefixes) else target)

    def select_item(self, identifier: str) -> bool:
        """
        Reveal a stable typed review item while retaining every unsaved response.
        """
        identifier = str(identifier)
        if not any(row["review_id"] == identifier for row in self.records):
            identifier = self._review_aliases.get(identifier, identifier)
        target = next((row for row in self.records if row["review_id"] == identifier), None)
        if target is None:
            return False
        selected_filter = (
            3 if target.get("suppressed_by") else 0 if target.get("posted_complete") else
            3 if target["work_state"] == "complete" else 2 if target["work_state"] == "ready" else 1
        )
        self.filter.setCurrentIndex(selected_filter)
        self.search.clear()
        row = next(index for index, item in enumerate(self.records) if item["review_id"] == identifier)
        self.table.selectRow(row)
        return bool(self.current and self.current["review_id"] == identifier)

    def select_kind(self, kind: str) -> bool:
        """
        Select related unfinished work without filtering the unified queue or discarding edited answers.
        """
        kind = {"all": "", "investments": "investment"}.get(kind, kind)
        if kind not in {"", "bank", "manual", "investment", "budget", "feed"}:
            return False
        self.filter.setCurrentIndex(1)
        self.search.clear()
        target = next((row for row in self.records if (
            not kind or row["kind"] == kind
        ) and row["work_state"] != "complete" and not row.get("suppressed_by")), None)
        if target is not None:
            return self.select_item(target["review_id"])
        return True

    def _status_filter_changed(self) -> None:
        """
        Apply the chosen work state across the unified queue.
        """
        self._filter_changed()

    def _selection_changed(self) -> None:
        """
        Switch instantly while retaining each edited answer for the explicit batch save.
        """
        if self._loading:
            return
        selected = self.table.selectionModel().selectedRows()
        index = selected[0].row() if selected else -1
        if not 0 <= index < len(self.records):
            if self.has_unsaved_changes():
                self._filter_changed()
            elif self.current:
                self.current = None
                self._show_record(None)
            return
        record = self.records[index]
        if self.current and record["review_id"] == self.current.get("review_id"):
            return
        self._capture_draft()
        self.current = record
        self._show_record(record)
        self.dirty_changed.emit(self.has_unsaved_changes())

    def action_started(self, action: str, payload: dict) -> None:
        """
        Retain the accepted action's identity and exact answer for targeted completion handling.
        """
        if action == "process_journal":
            self._processing = True
            self._update_controls()
            return
        if action == "save_reviews":
            self._pending_submission = {
                "action": action, "answers": deepcopy(payload.get("answers", [])),
                "manual_answers": deepcopy(payload.get("manual_answers", [])),
            }
            self._feedback = ""
            self._batch_feedback = ""
            self._update_controls()
            return
        if action not in {"save_review", "discard_review"}:
            return
        self._pending_submission = {
            **payload, "action": action, "displayed_text": self.answer.toPlainText(),
        }
        self._feedback = ""
        self._update_controls()

    def action_failed(self, action: str, message: str) -> None:
        """
        Keep failed input dirty and associate local feedback only with the submitted review.
        """
        if action == "process_journal":
            self._processing = False
        if action == "refresh":
            self._refresh_error = message
            if self._awaiting_refresh:
                self._feedback = "Saved, but the review could not refresh. Refresh before editing."
            self._filter_changed()
            self._update_controls()
            return
        pending = self._pending_submission
        if pending is None or pending["action"] != action:
            return
        if action == "save_reviews":
            self._batch_feedback = message
            self._pending_submission = None
            self._update_controls()
            return
        if self.current and self.current.get("transaction_id") == pending["transaction_id"]:
            self._feedback = message
        self._pending_submission = None
        self._update_controls()

    def action_completed(self, action: str, result=None) -> None:
        """
        Mark only the acknowledged submitted answer clean; cancellation cannot acknowledge a save.
        """
        if action == "process_journal":
            self._processing = False
            self._update_controls()
        pending = self._pending_submission
        if pending is None or pending["action"] != action:
            return
        self._pending_submission = None
        if action == "save_reviews":
            submitted = {
                "bank:" + str(answer["transaction_id"]): answer for answer in pending["answers"]
            }
            submitted.update({
                "manual:" + str(answer["manual_entry_id"]): answer for answer in pending["manual_answers"]
            })
            acknowledged = isinstance(result, dict) and set(result.get("review_ids", [])) == set(submitted)
            if not acknowledged:
                self._batch_feedback = "The save did not complete. Your edited responses have been kept."
                self._update_controls()
                return
            for identifier, answer in submitted.items():
                draft = self._drafts.get(identifier)
                if draft and draft["text"] == answer["text"]:
                    self._drafts.pop(identifier)
                if self.current and self.current.get(self.identity) == identifier:
                    fields = (
                        {"human_response": answer["text"], "status": "pending"}
                        if self.current.get("kind") == "manual" else
                        {"human_review": answer["text"], "review_status": "pending"}
                    )
                    self.current = {**self.current, **fields}
                    self._baseline = answer["text"]
            self._awaiting_refresh = True
            self._feedback = ""
            self._batch_feedback = ""
            self.dirty_changed.emit(self.has_unsaved_changes())
            self._update_controls()
            return
        acknowledged = isinstance(result, dict) and result.get("transaction_id") == pending["transaction_id"]
        same_row = self.current and self.current.get("transaction_id") == pending["transaction_id"]
        if not acknowledged:
            if same_row:
                self._feedback = "The action stopped before completion. Your answer has been kept."
            self._update_controls()
            return
        self._awaiting_refresh = True
        if same_row and self.answer.toPlainText() == pending["displayed_text"]:
            text = pending["text"] if action == "save_review" else ""
            self._baseline = text
            self.current = {**self.current, "human_review": text,
                            "review_status": "pending" if action == "save_review" else "discarded"}
            self.answer.blockSignals(True)
            self.answer.setPlainText(text)
            self.answer.blockSignals(False)
            self._feedback = ""
            self.dirty_changed.emit(False)
        self._update_controls()

    def _include_record(self, record: dict) -> bool:
        """
        Filter by next action while retaining full history for deliberate corrections.
        """
        if not hasattr(self, "filter"):
            return True
        selected = self.filter.currentIndex()
        if selected == 3:
            return True
        if record.get("suppressed_by"):
            return False
        state = record.get("work_state")
        if selected == 1:
            return state in {"needs_answer", "retry"}
        if selected == 2:
            return state == "ready"
        return bool(record.get("posted_complete"))

    def _filter_changed(self) -> None:
        """
        Keep dirty responses visible across filters and disclose unavailable investment review evidence.
        """
        query = self.search.text().casefold().strip()
        dirty = self.has_unsaved_changes()
        selected = self.current.get(self.identity) if self.current else None
        counts = {"bank": 0, "manual": 0, "investment": 0, "feed": 0, "budget": 0, "journal": 0}
        retained, selected_row = False, None
        previous_loading, self._loading = self._loading, True
        for index, record in enumerate(self.records):
            matches = self._include_record(record) and (
                not query or query in " ".join(_text(value) for value in record.values()).casefold()
            )
            keep = dirty and record.get(self.identity) == selected
            self.table.setRowHidden(index, not (matches or keep))
            if matches:
                counts[record["kind"]] += 1
            retained = retained or (keep and not matches)
            if record.get(self.identity) == selected:
                selected_row = index
        if selected_row is not None and not self.table.isRowHidden(selected_row):
            self.table.selectRow(selected_row)
        elif not dirty:
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self.current = None
            self._show_record(None)
        self._loading = previous_loading
        self.table.set_empty(not sum(counts.values()) and not retained)
        if self._refresh_error or self.snapshot.get("error") or self.snapshot.get("ready") is False:
            message = "Reviews unavailable. Refresh the workspace after resolving the error."
        else:
            message = ""
        if retained:
            message = "\n".join(filter(None, (message, "Your edited response is kept visible.")))
        self.empty.setText(message)
        self.empty.setVisible(bool(message))
        warning = self._refresh_error
        if warning:
            warning = "Review could not refresh: " + warning
        elif self.snapshot.get("investment_reviews_error"):
            warning = "Investment movements unavailable: " + str(self.snapshot["investment_reviews_error"])
        self.visibility_warning.setText(warning)
        self.visibility_warning.setVisible(bool(warning))

    def _show_record(self, record: dict | None) -> None:
        """
        Show shared journal or bank facts above a plain status and the retained response draft.
        """
        if not hasattr(self, "answer"):
            return
        bank = bool(record and record.get("kind") == "bank")
        manual = bool(record and record.get("kind") == "manual")
        responds = bank or manual and record.get("entry_mode") != "direct"
        self.empty_detail.setVisible(record is None)
        self.detail_scroll.setVisible(record is not None)
        self.response_panel.setVisible(responds)
        self.other_panel.setVisible(bool(record) and not bank)
        self.question_section.setVisible(bool(record))
        field = "human_response" if manual else "human_review"
        self._baseline = str(record.get(field, "")) if bank or manual else ""
        self._feedback = ""
        self.answer.blockSignals(True)
        draft = self._drafts.get(record.get(self.identity)) if record else None
        self.answer.setPlainText(draft["text"] if draft else self._baseline)
        self.answer.blockSignals(False)
        self._show_detail_card(record)
        self.question.clear()
        self.question.setToolTip("")
        self.question.setVisible(bool(record) and not responds)
        self._set_response_prompt("")
        if record and not bank:
            self._show_other(record)
            if responds:
                self._set_response_prompt(str(record.get("question") or ""), self.question.text())
                self.question.hide()
            self._update_controls()
            return
        if not record:
            self.question.clear()
        else:
            owned = record.get("manual_entry_id")
            blocking = record.get("blocking_manual_entry_id")
            question = str(record.get("question") or "")
            if owned:
                question = "This event belongs to a manual instruction. Make its correction there."
            elif blocking:
                question = (
                    "A pending manual instruction claims this transaction. Resolve that instruction first."
                )
            self._set_response_prompt(question, str(record.get("review_result") or ""))
        self._update_controls()

    def _set_response_prompt(self, question: str, detail: str = "") -> None:
        """
        Show saved guidance as a placeholder while keeping it available after a response has been entered.
        """
        question = question.strip()
        self.answer.setPlaceholderText(question or "Review the transactions")
        self.answer.setAccessibleDescription(self.answer.placeholderText())
        guidance = "\n\n".join(dict.fromkeys(value for value in (question, detail) if value))
        self.answer.setToolTip("\n\n".join(value for value in (guidance, self._response_help) if value))

    def _show_detail_card(self, record: dict | None) -> None:
        """
        Show posted accounting in the shared card and unposted bank or manual input in its own view.
        """
        self.journal_details.update_record(None, self.snapshot)
        self.transaction_details.update_record(None, self.snapshot)
        self.manual_details.hide()
        self._active_details = None
        if record is None:
            return
        entry = record.get("journal_entry") or next((
            item for item in self.snapshot.get("entries", [])
            if item.get("journal_entry_id") == record.get("journal_entry_id")
        ), None)
        if entry is None and record.get("postings"):
            entry = {
                **record, "description": record.get("instruction") or record.get("description") or "",
                "date": record.get("journal_date") or "",
                "source_transactions": record.get("source_transactions") or [
                    line["transaction_id"] for line in record["postings"] if line.get("transaction_id")
                ],
            }
        if entry is not None:
            self.journal_details.update_record(entry, self.snapshot)
            self._active_details = self.journal_details
        elif record.get("kind") in {"bank", "feed"}:
            self.transaction_details.update_record(record, self.snapshot)
            self._active_details = self.transaction_details
            if record.get("kind") == "bank":
                self.transaction_details.status.setText(record.get("status_label", ""))
        else:
            self.manual_instruction.setText(str(
                record.get("instruction") or record.get("description") or ""
            ).upper())
            self.manual_date.setText("Accounting date · " + str(record.get("date") or "—"))
            self.manual_details.show()
            self._active_details = self.manual_details

    def _detail_actions_available(self) -> bool:
        """
        Keep every selected-record route behind the same fresh-snapshot and idle-work requirements.
        """
        return bool(
            self.snapshot.get("ready", True) and not self.snapshot.get("error")
            and not self._refresh_error and not self._busy and not self._awaiting_refresh
        )

    def _update_header_action(self, available: bool) -> None:
        """
        Place the selected review's exact editing route in its active evidence card's header.
        """
        self.journal_details.set_header_action(None, None)
        self.transaction_details.set_header_action(None, None)
        self.manual_edit.hide()
        self._header_callback = None
        record = self.current
        if not record or record.get("kind") == "feed" or self._active_details is None:
            return
        symbol = "edit"
        if record["kind"] == "bank":
            if record.get("manual_entry_id") or record.get("blocking_manual_entry_id"):
                label, callback = "Edit the owning manual instruction", self._open_manual
            else:
                label, callback = "Edit the complete journal entry", self._resolve_manually
        elif record["kind"] == "manual":
            label = (
                "Edit journal entry" if record.get("entry_mode") == "direct" else "Edit the original instruction"
            )
            callback = self._open_other
        elif record["kind"] == "budget" and record.get("issue_code") == "liability_category":
            label, callback, symbol = "Choose the expense category in Accounts", self._open_other, "settings"
        elif record["kind"] == "investment":
            label, callback = "Edit this investment posting", self._open_other
        else:
            label, callback = "Edit the complete journal entry", self._open_other
        if self._active_details is self.manual_details:
            self._header_callback = callback
            self.manual_edit.setIcon(icon(symbol, gui_colour(self, "icon", "#355653"), size=18))
            self.manual_edit.setAccessibleName(label)
            self.manual_edit.setToolTip(label)
            self.manual_edit.setEnabled(available)
            self.manual_edit.show()
        else:
            self._active_details.set_header_action(label, callback, symbol=symbol, enabled=available)

    def _activate_header_action(self) -> None:
        """
        Follow the unposted instruction's current header route only while its source context is usable.
        """
        if self._header_callback is not None and self._detail_actions_available():
            self._header_callback()

    def _show_other(self, record: dict) -> None:
        """
        Keep manual and investment next steps beside the same shared facts, without a second response editor.
        """
        feed = record["kind"] == "feed"
        if record["kind"] == "journal" or (
            record["kind"] == "manual" and record.get("entry_mode") == "direct"
        ):
            self.question_section.hide()
            self.other_panel.hide()
            return
        if feed:
            changed = record.get("conflict_kind") == "changed"
            self.question.setText(
                "Redbark returned changed facts for an imported transaction." if changed else
                "An earlier duplicate check held this transaction."
            )
            sections = [str(record.get("message") or record.get("reason") or (
                "Your saved transaction and posted entries have not changed. Keep the saved record only "
                "after checking the bank evidence; any accounting adjustment belongs in the journal."
                if changed else "Import this separate Redbark transaction now, or sync Transactions to "
                "import it when Redbark returns it again."
            ))]
            if changed:
                sections[:0] = [
                    f"{field}\nSaved: {before or '—'}\nIncoming: {after or '—'}"
                    for field, (before, after) in record.get("differences", {}).items()
                ]
            self.other_action.setText("Keep saved record" if changed else "Import transaction")
            self.other_action.setToolTip("Resolve this source conflict without overwriting posted entries.")
        elif record["kind"] == "manual":
            status = record.get("question") or record.get("error") or record["status_label"]
            self.question.setText(str(status))
            sections = []
            if record.get("error") and record["error"] != status:
                sections.append("Last error\n" + str(record["error"]))
        elif record["kind"] == "budget":
            self.question.setText(str(record.get("question") or "Check this cash flow."))
            sections = ["Set Asset account type and Expense category in Setup → Accounts."]
        else:
            self.question.setText("Investment treatment needed.")
            sections = [f"Choose the treatment for line {record.get('line_number', '')}: "
                        + str(record.get("account") or "") + "."]
            start = str(self.snapshot.get("period_start") or "")
            if start and str(record.get("date") or "") < start:
                sections.append("This earlier movement still affects investment cost.")
        self.other_details.setText("\n\n".join(sections))
        self.other_details.setVisible(bool(sections))
        self.other_panel.setVisible(bool(sections) or feed)

    def _open_other(self) -> None:
        """
        Route non-bank review work to its existing editor with all original identity fields intact.
        """
        if not self.current or not self._detail_actions_available():
            return
        if self.current.get("kind") == "feed":
            self._resolve_feed("keep" if self.current.get("conflict_kind") == "changed" else "import")
        elif self.current.get("kind") == "manual":
            self.action_requested.emit("open_manual_editor", {
                "manual_entry_id": self.current["manual_entry_id"],
                "journal_entry_id": self.current.get("journal_entry_id") or None,
                "mode": "direct" if self.current.get("entry_mode") == "direct" else "instruction",
            })
        elif self.current.get("kind") == "budget" and self.current.get("issue_code") == "liability_category":
            self.action_requested.emit("open_account_settings", {"account_key": self.current.get("account_key")})
        elif self.current.get("kind") in {"investment", "budget", "journal"}:
            self.action_requested.emit("open_entry_editor", {
                "journal_entry_id": self.current["journal_entry_id"],
                "line_number": self.current.get("line_number"),
            })

    def _resolve_feed(self, action: str) -> None:
        """
        Submit an explicit source reconciliation using the exact conflict revision shown to the user.
        """
        if not self.current or self.current.get("kind") != "feed" or not self._detail_actions_available():
            return
        self.action_requested.emit("redbark_resolve", {
            "id": self.current["id"], "revision": self.current["revision"],
            "action": action,
        })

    def _pending_answer(self) -> bool:
        """
        Identify a saved unresolved answer rather than an already applied review or a local draft.
        """
        return bool(
            self.current and self.current.get("kind") == "bank" and self._baseline.strip()
            and not self.current.get("manual_entry_id")
            and self.current.get("review_status") in {
                "submitted", "pending", "needs_review", "failed", "interrupted", "stale",
            }
        )

    def _update_controls(self) -> None:
        """
        Keep save availability, ownership routing and feedback consistent with the selected review.
        """
        if not hasattr(self, "answer"):
            return
        ready = (
            self.snapshot.get("ready", True) and not self.snapshot.get("error") and not self._refresh_error
        )
        self.journal_details.set_stale(not ready)
        self.transaction_details.set_stale(not ready)
        self.journal_details.set_busy(self._busy or self._awaiting_refresh)
        self.transaction_details.set_busy(self._busy or self._awaiting_refresh)
        available = ready and not self._busy and not self._awaiting_refresh
        bank = bool(self.current and self.current.get("kind") == "bank")
        manual_response = bool(self.current and self.current.get("kind") == "manual")
        completed_manual = manual_response and self.current.get("work_state") == "complete"
        owned = bool(bank and self.current.get("manual_entry_id"))
        blocked = bool(bank and self.current.get("blocking_manual_entry_id"))
        interrupted = bool(self.current and (
            self.current.get("review_status") == "requesting"
            or manual_response and self.current.get("status") == "requesting"
        ))
        editable = (
            (bank or manual_response) and not owned and not blocked
            and not interrupted and not completed_manual
        )
        dirty = self.has_unsaved_changes()
        self.answer.setReadOnly(not (editable and available))
        self.save_button.setEnabled(
            available and dirty and all(draft["text"].strip() for draft in self._drafts.values()),
        )
        pending = self._pending_answer()
        self.discard_changes_button.setVisible(dirty)
        self.discard_changes_button.setEnabled(not self._busy and not self._awaiting_refresh)
        self.discard_button.setVisible(pending and not dirty)
        self.discard_button.setEnabled(pending and available and not dirty)
        self.discard_button.setToolTip(
            "Discard the saved pending answer. Save or discard local edits first."
            if dirty else "Discard the pending answer without undoing posted accounting."
        )
        feed = bool(self.current and self.current.get("kind") == "feed")
        self.other_action.setVisible(feed)
        self.other_action.setEnabled(feed and available)
        self._update_header_action(available)
        self.process_button.setEnabled(ready and not self._busy)
        self.process_button.setText("Process")
        count = len(self._drafts)
        self.draft_count.setText(f"{count} unsaved response{'s' if count != 1 else ''}")
        self.draft_count.setVisible(dirty)
        if self._batch_feedback:
            message = self._batch_feedback
        elif self._feedback:
            message = self._feedback
        elif self._busy or self._awaiting_refresh:
            message = ""
        elif not ready:
            message = "Reviews unavailable. Check Setup and refresh."
        elif dirty and any(not draft["text"].strip() for draft in self._drafts.values()):
            message = "Complete each edited response before saving."
        else:
            message = ""
        self.save_status.setText(message)
        self.save_status.setVisible(bool(message))

    def _edited(self) -> None:
        """
        Update save availability without normalising or trimming the user's multiline answer.
        """
        self._feedback = ""
        self._capture_draft()
        for index, record in enumerate(self.records):
            item = self.table.item(index, 3)
            if item is not None:
                item.setText("Unsaved" if record[self.identity] in self._drafts else record["status_label"])
        self._update_controls()
        self._filter_changed()
        self.dirty_changed.emit(self.has_unsaved_changes())

    def _save(self) -> None:
        """
        Submit every retained answer together, keeping each original revision and answer token.
        """
        self._capture_draft()
        if (
            self._busy or self._awaiting_refresh
            or self._refresh_error or self.snapshot.get("error") or self.snapshot.get("ready") is False
            or not self._drafts
        ):
            return
        if any(not draft["text"].strip() for draft in self._drafts.values()):
            QMessageBox.information(self, "Response needed", "Complete each edited response before saving.")
            return
        answers, manual_answers = [], []
        for draft in self._drafts.values():
            record = draft["record"]
            manual = record.get("kind") == "manual"
            identity = "manual_entry_id" if manual else "transaction_id"
            payload = {
                identity: record[identity], "revision": record["context_revision"],
                "answer_token": record.get("answer_token", ""), "text": draft["text"],
            }
            (manual_answers if manual else answers).append(payload)
        self.action_requested.emit("save_reviews", {"answers": answers, "manual_answers": manual_answers})

    def _discard_local_changes(self) -> None:
        """
        Restore the latest saved answer only after the user confirms discarding local edits.
        """
        if not self._busy and not self._awaiting_refresh and self._allow_discard():
            self.discard_changes()

    def _discard_pending(self) -> None:
        """
        Withdraw only saved unresolved input after an explicit confirmation.
        """
        if not self.discard_button.isEnabled() or not self._pending_answer() or self.has_unsaved_changes():
            return
        answer = QMessageBox.question(
            self, "Discard pending answer",
            "Discard this pending answer? Existing journal postings will stay in place.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Discard:
            self.action_requested.emit("discard_review", self._identity_payload())

    def _identity_payload(self) -> dict:
        """
        Keep concurrency evidence attached to the displayed review rather than a refreshed replacement.
        """
        return {
            "transaction_id": self.current["transaction_id"],
            "revision": self.current["context_revision"],
            "answer_token": self.current.get("answer_token", ""),
        }

    def _open_manual(self) -> None:
        """
        Open the owning or blocking manual instruction using its stable saved identity.
        """
        if self.current and self._detail_actions_available():
            identifier = self.current.get("manual_entry_id") or self.current.get("blocking_manual_entry_id")
            if identifier:
                self.action_requested.emit("open_manual_editor", {
                    "manual_entry_id": identifier, "mode": "instruction",
                })

    def _resolve_manually(self) -> None:
        """
        Open direct accounting with complete source identity and any existing manual ownership intact.
        """
        if (
            self.current and self.current.get("kind") == "bank" and self._detail_actions_available()
            and not self.current.get("blocking_manual_entry_id")
        ):
            self.action_requested.emit("open_entry_editor", {
                "journal_entry_id": self.current.get("journal_entry_id") or None,
                "transaction_id": self.current["transaction_id"],
                "manual_entry_id": self.current.get("manual_entry_id") or None,
                "date": self.current.get("date"),
            })

# %% 4. Posted Journal ---------------------------------------------------------------------------------------
class JournalPage(ActivityPage):
    """
    Browse posted events with compact inline facts before any explicit edit or source action.
    """

    def __init__(self, parent=None):
        """
        Give monthly processing one prominent action above the saved journal and entry inspector.
        """
        super().__init__(
            "Journal", "Inspect posted entries and their sources. Direct edits do not call AI.",
            (("date", "Date"), ("description", "Event"),
             ("total_debit", "Debits AUD"), ("total_credit", "Credits AUD"), ("budget_status", "Budget")),
            "journal_entry_id", parent,
        )
        self._refresh_error = ""
        self._processing = False
        self.viewer = None
        self.process_button = HeaderActionButton("Process", icon_only=True)
        self.process_button.clicked.connect(lambda: self._request_action("process_journal"))
        self.subtitle_row.insertWidget(0, self.process_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.search.setPlaceholderText("Search journal")
        self.layout.removeWidget(self.empty)
        self.layout.insertWidget(1, self.empty)
        self.empty.setObjectName("workspaceHint")
        self.empty.setWordWrap(True)
        self.table.setObjectName("journalTransactions")
        self.table.setMinimumHeight(120)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.cellClicked.connect(self._open_selected_viewer)
        self.layout.removeWidget(self.splitter)
        self.layout.addWidget(self.table, 1)
        self.viewer = JournalDetails(self)
        self.viewer.action_requested.connect(self.action_requested.emit)
        self.viewer.dismissed.connect(self._hide_details)
        self.detail_layout.setContentsMargins(0, DETAIL_SECTION_GAP - self.layout.spacing(), 0, 0)
        self.detail_layout.addWidget(self.viewer)
        self.detail.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.layout.addWidget(self.detail)
        self.detail.hide()
        self.splitter.hide()
        self._filter_changed()
        QWidget.setTabOrder(self.process_button, self.search)

    def refresh(self, snapshot: dict) -> None:
        """
        Display exact debit and credit totals while retaining the selected event by its saved identity.
        """
        super().refresh(snapshot)
        self._refresh_error = ""
        rows = []
        for entry in snapshot.get("entries", []):
            postings = entry.get("postings", [])
            debit = sum((Decimal(str(row.get("debit") or 0)) for row in postings), Decimal(0))
            credit = sum((Decimal(str(row.get("credit") or 0)) for row in postings), Decimal(0))
            rows.append({
                **entry, "total_debit": f"{debit:.2f}", "total_credit": f"{credit:.2f}",
                "budget_status": entry.get("budget_status") or "Unavailable",
                "budget_tooltip": entry.get("budget_tooltip") or
                    "Budget status is unavailable in this snapshot. Refresh the workspace.",
                "debit_accounts": _posting_accounts(postings, "debit"),
                "credit_accounts": _posting_accounts(postings, "credit"),
            })
        self._set_records(rows)
        for column, width in ((0, 112), (2, 130), (3, 130), (4, 134)):
            self.table.setColumnWidth(column, width)
        for index, row in enumerate(self.records):
            self.table.item(index, 4).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.item(index, 4).setToolTip(row["budget_tooltip"])
            self.table.item(index, 1).setToolTip(
                f"{row.get('description', '')}\nDebit accounts: {row['debit_accounts'] or '-'}\n"
                f"Credit accounts: {row['credit_accounts'] or '-'}"
            )
        self.process_button.setToolTip(f"Process {snapshot.get('month') or 'selected month'}")
        self.process_button.setAccessibleDescription(
            f"Process bank transactions, saved answers and manual instructions for "
            f"{snapshot.get('month') or 'the selected month'}, including failed work "
            "and complete linked events."
        )
        self._update_controls()

    def set_busy(self, busy: bool) -> None:
        """
        Keep saved-table inspection available while disabling competing processing and editor actions.
        """
        self._busy = busy
        if self.viewer is not None:
            self.viewer.set_busy(busy)
        self._update_controls()

    def action_started(self, action: str, payload: dict) -> None:
        """
        Label an active monthly process without treating other desktop jobs as journal processing.
        """
        if action == "process_journal":
            self._processing = True
            self._update_controls()

    def action_completed(self, action: str, result=None) -> None:
        """
        Restore the processing action after the worker finishes; figures update only on snapshot refresh.
        """
        if action == "process_journal":
            self._processing = False
            self._update_controls()

    def action_failed(self, action: str, message: str) -> None:
        """
        Retain inspectable saved facts after failure while preventing actions from a stale snapshot.
        """
        if action == "process_journal":
            self._processing = False
        if action == "refresh":
            self._refresh_error = message
            if self.viewer is not None:
                self.viewer.set_stale(True)
            self._filter_changed()
        self._update_controls()

    def select_record(self, identifier: str) -> bool:
        """
        Reveal the exact linked event in the inline panel.
        """
        if not super().select_record(identifier):
            return False
        self._open_selected_viewer()
        return bool(self.current and str(self.current.get(self.identity)) == str(identifier))

    def _selection_changed(self) -> None:
        """
        Capture the actual highlighted row even if Qt has not yet updated its current cell.
        """
        if self._loading:
            return
        selected = self.table.selectionModel().selectedRows()
        row = selected[0].row() if selected else -1
        self.current = self.records[row] if 0 <= row < len(self.records) else None
        self._show_record(self.current)
        if self.current:
            self._open_selected_viewer()

    def _filter_changed(self) -> None:
        """
        Show availability errors and hide details when the selected event leaves the visible results.
        """
        super()._filter_changed()
        selected = next((index for index, row in enumerate(self.records)
                         if self.current and row.get(self.identity) == self.current.get(self.identity)), None)
        if selected is None or self.table.isRowHidden(selected):
            previous_loading, self._loading = self._loading, True
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self.current = None
            self._show_record(None)
            self._loading = previous_loading
        if self._refresh_error:
            message = "Journal could not refresh. The previous saved snapshot remains available to inspect."
        elif self.snapshot.get("error") or self.snapshot.get("ready") is False:
            message = "Journal unavailable. Check Setup and refresh the workspace."
        else:
            message = ""
        self.empty.setText(message)
        self.empty.setToolTip(self._refresh_error)
        self.empty.setVisible(bool(message))
        self._update_controls()

    def _open_selected_viewer(self, *_args) -> None:
        """
        Show the highlighted event below the table, without opening another window.
        """
        if self._loading:
            return
        selected = self.table.selectionModel().selectedRows()
        row = selected[0].row() if selected else -1
        if not 0 <= row < len(self.records) or self.table.isRowHidden(row):
            return
        record = self.records[row]
        self.current = record
        self._show_record(record)

    def _hide_details(self) -> None:
        """
        Clear selection when the user dismisses the inline inspector.
        """
        self._loading = True
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.current = None
        self._loading = False
        self._show_record(None)

    def _show_record(self, record: dict | None) -> None:
        """
        Keep the panel aligned with the selected saved event and current workspace freshness.
        """
        if self.viewer is None:
            return
        self.viewer.update_record(record, self.snapshot)
        self.viewer.set_stale(bool(self._refresh_error or self.snapshot.get("error")))
        self.detail.setVisible(record is not None)

    def _available(self) -> bool:
        """
        Require a current readable workspace and an idle worker before submitting another operation.
        """
        return bool(
            self.snapshot and self.snapshot.get("ready", True) and not self.snapshot.get("error")
            and not self._refresh_error and not self._busy
        )

    def _update_controls(self) -> None:
        """
        Keep the single processing action consistent with the current workspace state.
        """
        if not hasattr(self, "process_button"):
            return
        available = self._available()
        self.process_button.setEnabled(available)
        self.process_button.setText("Process")

    def _request_action(self, action: str) -> None:
        """
        Send the processing intent only when the shared worker can safely accept another job.
        """
        if self._available():
            self.action_requested.emit(action, {})

# %% 5. Helpers ----------------------------------------------------------------------------------------------
def _plain_text(layout: QVBoxLayout) -> QPlainTextEdit:
    """
    Add a selectable plain-text detail viewer without interpreting statement content as HTML.
    """
    widget = QPlainTextEdit()
    widget.setReadOnly(True)
    layout.addWidget(widget, 1)
    return widget


def _posting_accounts(postings: list[dict], side: str) -> str:
    """
    Name the distinct debit or credit accounts in their original posting order.
    """
    return "; ".join(dict.fromkeys(
        row.get("account", "") for row in postings if Decimal(str(row.get(side) or "0")) > 0
    ))


def _transaction_rows(snapshot: dict) -> list[dict]:
    """
    Show each bank movement once and add manual history that has no bank-backed posting.
    """
    sources = {row["transaction_id"]: row for row in snapshot.get("transactions", [])}
    reviews = {row["transaction_id"]: row for row in snapshot.get("reviews", [])}
    entries = {row["journal_entry_id"]: row for row in snapshot.get("entries", [])}
    manuals = {row["manual_entry_id"]: row for row in snapshot.get("manuals", [])}
    rows = []
    for identifier, source in sources.items():
        review = reviews.get(identifier, {})
        record = {**source, "row_id": f"bank:{identifier}", "status": review.get("status") or "imported",
                  "review_status": review.get("review_status") or ""}
        if review.get("manual_entry_id") and review.get("journal_entry_id"):
            record["show_review"] = _manual_review_available(manuals.get(review["manual_entry_id"], {}))
            record.update({key: review.get(key) for key in (
                "manual_entry_id", "entry_mode", "journal_entry_id", "postings",
            )})
        rows.append(record)

    for manual in manuals.values():
        entry = entries.get(manual.get("journal_entry_id"), {})
        postings = entry.get("postings") or manual.get("postings") or []
        if any(line.get("transaction_id") for line in postings):
            continue
        accounts = list(dict.fromkeys(line["account"] for line in postings if line.get("account")))
        if not accounts:
            accounts = list(dict.fromkeys(
                sources[key]["account"] for key in manual.get("source_transactions", []) if key in sources
            ))
        status = manual.get("status")
        posted = bool(manual.get("journal_entry_id") and postings)
        description = entry.get("description") or next(
            (line["description"] for line in postings if line.get("description")), "",
        )
        if manual.get("entry_mode") != "direct" and manual.get("instruction"):
            description = manual["instruction"]
        display_status = "posted" if posted else (
            "ready" if status in {"pending", "submitted", "ready"} else
            "review" if status in {"needs_review", "unsupported", "failed", "error", "requesting"} else status
        )
        rows.append({
            **manual, "row_id": "manual:" + manual["manual_entry_id"],
            "date": entry.get("date") or manual["date"],
            "description": description or manual.get("instruction") or "", "account": ", ".join(accounts),
            "amount": _posting_total(postings) or "—", "source_label": "Manual Journal",
            "status": display_status,
            "show_review": _manual_review_available(manual),
            "postings": postings if posted else [],
        })
    if not snapshot.get("activity_all") and snapshot.get("period_start") and snapshot.get("period_end"):
        rows = [row for row in rows if snapshot["period_start"] <= row["date"] <= snapshot["period_end"]]
    return sorted(rows, key=lambda row: row["date"])


def _manual_review_available(record: dict) -> bool:
    """
    Keep the Review conversation accessible for outstanding work and saved responses.
    """
    if record.get("question") or record.get("human_response") or record.get("error"):
        return True
    return bool(record and record.get("entry_mode") != "direct" and record.get("status") not in {
        "applied", "posted", "superseded", "withdrawn",
    })


def _posting_total(postings: list[dict]) -> str | None:
    """
    Derive an unsigned event amount from saved postings without inventing an amount for an instruction.
    """
    if not postings:
        return None
    try:
        amounts = [Decimal(str(line.get("debit") or "0")) for line in postings]
    except (ValueError, ArithmeticError):
        return None
    if any(not amount.is_finite() or amount < 0 for amount in amounts):
        return None
    return format(sum(amounts, Decimal(0)), ".2f")


def _text(value: object) -> str:
    """
    Keep missing table values empty and join sequence labels into readable text.
    """
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def _transaction_details(record: dict | None) -> str:
    """
    Present a statement's unchanged dates, description and source identity in a compact readable block.
    """
    if not record:
        return ""
    return "\n".join([
        f"Statement date: {_text(record.get('date'))}",
        f"Effective date: {_text(record.get('effective_date')) or '-'}",
        f"Account: {_text(record.get('account'))}",
        f"Amount: {_text(record.get('amount'))} AUD",
        f"Description: {_text(record.get('description'))}",
        f"Source: {_text(record.get('source_file')) or '-'}",
        f"Transaction: {_text(record.get('transaction_id'))}",
    ])


def _status_text(value: object) -> str:
    """
    Present saved status values as familiar words while preserving their internal values for filtering.
    """
    return {
        "requesting": "Processing", "submitted": "Answer saved", "pending": "Ready to process",
        "stale": "Needs refreshed answer", "interrupted": "Interrupted",
    }.get(str(value), _text(value).replace("_", " ").capitalize())


def _postings_text(postings: list[dict]) -> str:
    """
    Explain every posting in an event without hiding differing bank descriptions or budget treatment.
    """
    if not postings:
        return "No posted journal lines yet."
    lines = ["Journal postings"]
    for index, row in enumerate(postings, 1):
        debit = _text(row.get("debit")) or "0.00"
        credit = _text(row.get("credit")) or "0.00"
        lines.append(f"\n{index}. {row.get('account', row.get('account_name', ''))}")
        lines.append(f"    Debit {debit}    Credit {credit}")
        if row.get("description"):
            lines.append(f"    {row['description']}")
        if row.get("effective_date"):
            lines.append(f"    Effective date: {row['effective_date']}")
        if row.get("budget_category"):
            lines.append(f"    Budget category: {row['budget_category']}")
        if row.get("transaction_id"):
            lines.append(f"    Bank evidence: {row['transaction_id']}")
    return "\n".join(lines)
