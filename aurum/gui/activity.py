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
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.controls import EntryTable, HeaderActionButton, PageActionRow, SearchField
from aurum.gui.icons import icon
from aurum.gui.viewers import DETAIL_SECTION_GAP, DETAIL_TEXT_INSET, JournalDetails, TransactionDetails

# %% 1. Activity Tables --------------------------------------------------------------------------------------
class ActivityPage(QWidget):
    """
    Share a searchable saved-record table, a detail panel and safe selection handling across activity pages.
    """

    action_requested = Signal(str, object)
    dirty_changed = Signal(bool)

    def __init__(self, title: str, explanation: str, columns: tuple, identity: str, parent=None):
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
        self.layout = QVBoxLayout(self)
        self.heading_block = QWidget()
        self.heading_block.setObjectName("activityHeading")
        heading_layout = QVBoxLayout(self.heading_block)
        heading_layout.setContentsMargins(0, 0, 0, 24)
        heading_layout.setSpacing(0)
        self.heading = QLabel(title)
        self.heading.setObjectName("workspacePageTitle")
        self.title_row = QHBoxLayout()
        self.title_row.setContentsMargins(0, 0, 0, 0)
        self.title_row.addWidget(self.heading)
        self.title_row.addStretch()
        heading_layout.addLayout(self.title_row)
        self.subtitle = QLabel(explanation)
        self.subtitle.setWordWrap(False)
        self.subtitle.setToolTip(explanation)
        self.subtitle.setObjectName("workspacePageSubtitle")
        self.subtitle_row = PageActionRow()
        self.subtitle_row.setContentsMargins(0, 0, 0, 0)
        self.subtitle_row.addWidget(self.subtitle, 1, Qt.AlignmentFlag.AlignTop)
        heading_layout.addLayout(self.subtitle_row)
        self.layout.addWidget(self.heading_block)
        self.toolbar = QHBoxLayout()
        self.search = SearchField("Search this table", self)
        self.toolbar.addWidget(self.search, 1)
        self.layout.addLayout(self.toolbar)
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = EntryTable(0, len(columns))
        self.table.setHorizontalHeaderLabels([label for _, label in columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
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
        for row_index, record in enumerate(records):
            for column_index, (field, _) in enumerate(self.columns):
                value = record.get(field, "")
                if field in {"status", "review_status"}:
                    value = _status_text(value)
                item = QTableWidgetItem(_text(value))
                item.setToolTip(_text(value))
                if field in {"amount", "debit", "credit", "total_debit", "total_credit"}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row_index, column_index, item)
        self.table.resizeColumnsToContents()
        for index in range(len(self.columns)):
            self.table.setColumnWidth(index, min(360, max(90, self.table.columnWidth(index))))
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
        answer = QMessageBox.question(
            self, "Unsaved changes", "Discard these unsaved edits? You can cancel and save them first.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

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
                value = _text(line.get(field))
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                if field in {"debit", "credit"}:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.setItem(index, column, item)
        for column in (0, 1, 2, 4, 5):
            self.resizeColumnToContents(column)
            self.setColumnWidth(column, max(110, min(230, self.columnWidth(column))))


# %% 2. Statement Imports ------------------------------------------------------------------------------------
class StatementsPage(ActivityPage):
    """
    Collect local PDF files for import and show the immutable statement lines already stored.
    """

    def __init__(self, parent=None):
        """
        Keep statement preparation, saved transactions and inline inspection in one monthly workspace.
        """
        super().__init__(
            "Statements", "Import bank statements, then inspect the saved transactions.",
            (
                ("date", "Date"), ("account", "Account"), ("description", "Description"),
                ("amount", "Amount"), ("source_file", "Statement"), ("status", "Status"),
            ),
            "transaction_id", parent,
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
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(10)
        self.layout.removeItem(self.toolbar)
        self.layout.removeWidget(self.splitter)
        self.layout.removeWidget(self.empty)

        queue = QFrame()
        queue.setObjectName("statementImportPanel")
        queue.setToolTip("Choose PDFs or drop them here. Preparation keeps a copy in this workspace.")
        queue_layout = QVBoxLayout(queue)
        queue_layout.setContentsMargins(4, 10, 12, 10)
        queue_layout.setSpacing(6)
        queue_actions = QHBoxLayout()
        queue_actions.setSpacing(4)
        self.choose_button = self._icon_button("Choose statement PDFs", "folder")
        self.choose_button.clicked.connect(self._choose_files)
        queue_actions.addWidget(self.choose_button)
        self.queue_empty = QLabel("Choose PDFs")
        self.queue_empty.setObjectName("workspaceHint")
        queue_actions.addWidget(self.queue_empty, 0, Qt.AlignmentFlag.AlignVCenter)
        self.queue_count = QLabel()
        self.queue_count.setObjectName("workspaceStatus")
        queue_actions.addWidget(self.queue_count, 0, Qt.AlignmentFlag.AlignVCenter)
        queue_actions.addSpacing(16)
        self.retry_stage_button = self._icon_button("Retry PDF preparation", "refresh")
        self.retry_stage_button.clicked.connect(self._retry_staging)
        queue_actions.addWidget(self.retry_stage_button)
        self.clear_button = self._icon_button("Clear the PDF queue", "close")
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
        self.import_feedback.setWordWrap(True)
        self.import_feedback.hide()
        feedback_row = QHBoxLayout()
        feedback_row.setContentsMargins(36, 0, 0, 0)
        self.import_feedback.setAlignment(Qt.AlignmentFlag.AlignLeft)
        feedback_row.addWidget(self.import_feedback)
        queue_layout.addLayout(feedback_row)
        self.layout.addWidget(queue)

        self.search.setPlaceholderText("Search transactions")
        self.search.setObjectName("statementSearch")
        self.search.setAccessibleName("Search transactions, accounts and statements")
        self.history_button = self._icon_button("Show import history", "clock")
        self.history_button.setFixedSize(36, 36)
        self.history_button.clicked.connect(self._show_imports)
        self.toolbar.addWidget(self.history_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.layout.addLayout(self.toolbar)

        self.history_dialog = QDialog(self)
        self.history_dialog.setObjectName("statementHistory")
        self.history_dialog.setWindowTitle("Import history")
        self.history_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.history_dialog.setMinimumSize(680, 340)
        self.history_dialog.resize(860, 460)
        history_layout = QVBoxLayout(self.history_dialog)
        history_layout.setContentsMargins(18, 16, 18, 16)
        history_layout.setSpacing(10)
        history_title = QLabel("Import history")
        history_title.setObjectName("viewerTitle")
        history_layout.addWidget(history_title)
        history_scope = QLabel("All reporting months")
        history_scope.setObjectName("workspaceHint")
        history_layout.addWidget(history_scope)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(72)
        self.result.setObjectName("statementImportResult")
        self.result.hide()
        history_layout.addWidget(self.result)
        self.history_empty = QLabel("No statement imports have been saved yet.")
        self.history_empty.setObjectName("workspaceHint")
        history_layout.addWidget(self.history_empty)
        self.history_table = QTableWidget(0, 4)
        self.history_table.setHorizontalHeaderLabels(["Statement", "Last imported", "Transactions", "Added"])
        self.history_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.history_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.history_table.setShowGrid(False)
        self.history_table.verticalHeader().hide()
        self.history_table.verticalHeader().setDefaultSectionSize(32)
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        history_layout.addWidget(self.history_table, 1)
        history_footer = QHBoxLayout()
        history_footer.addStretch()
        history_close = QPushButton("Close")
        history_close.clicked.connect(self.history_dialog.reject)
        history_footer.addWidget(history_close)
        history_layout.addLayout(history_footer)
        self.empty.setObjectName("statementCount")
        self.layout.addWidget(self.empty)

        self.table.setObjectName("statementTransactions")
        self.table.horizontalHeaderItem(3).setToolTip("Amount in Australian dollars (AUD)")
        self.table.setMinimumHeight(120)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.layout.addWidget(self.table, 1)
        self.detail.hide()
        self.splitter.hide()
        self.transaction_details = TransactionDetails(self)
        self.transaction_details.action_requested.connect(self.action_requested.emit)
        self.transaction_details.dismissed.connect(self._clear_selection)
        self.detail_layout.setContentsMargins(0, DETAIL_SECTION_GAP - self.layout.spacing(), 0, 0)
        self.detail_layout.addWidget(self.transaction_details)
        self.detail.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.layout.addWidget(self.detail)
        self._render_queue()
        self._filter_changed()

    def refresh(self, snapshot: dict) -> None:
        """
        Show stored transactions and the latest import result without changing queued file choices.
        """
        super().refresh(snapshot)
        self._refresh_error = ""
        self._statement_folder = str(snapshot.get("statement_folder") or self._statement_folder)
        reviews = {row["transaction_id"]: row for row in snapshot.get("reviews", [])}
        rows = []
        for source in snapshot.get("transactions", []):
            review = reviews.get(source["transaction_id"], {})
            rows.append({
                **source, "status": review.get("status") or "imported",
                "review_status": review.get("review_status") or "",
            })
        self._set_records(rows)
        for row, record in enumerate(self.records):
            source = str(record.get("source_file") or "")
            self.table.item(row, 4).setText(Path(source).name if source else "")
        for column, width in ((0, 112), (1, 130), (3, 100), (4, 120), (5, 145)):
            self.table.setColumnWidth(column, width)
        self._refresh_history()

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
        self.paths = [path for path in self.paths if path not in finished]
        self._importing_paths = ()
        if not items:
            messages.append("No files completed. Queued PDFs are kept for retry.")
        if self.paths:
            label = "PDF" if len(self.paths) == 1 else "PDFs"
            messages.append(f"\nRemaining queue: {len(self.paths)} {label}.")
        if any(item.get("status") == "imported" for item in items):
            messages.append("\nOpen Review to process the month when ready.")
        successful = sum(item.get("status") in {"imported", "skipped"} for item in items)
        feedback = f"{successful} PDF" + ("s" if successful != 1 else "") + " completed."
        if self.paths:
            feedback += " Remaining PDFs are kept for retry."
        self._set_result(
            "\n".join(messages), feedback if items else "No PDFs completed. Queue kept for retry.",
        )
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
        Keep the latest summary visible and its complete file messages inside import history.
        """
        self.result.setPlainText(detail)
        self.result.setVisible(bool(detail))
        self.import_feedback.setText(summary + " Open import history for details.")
        self.import_feedback.show()

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
        messages, attempted, prepared = [], set(), 0
        for item in items:
            source = str(item.get("source_path") or "")
            if source not in self._staging_paths:
                continue
            attempted.add(source)
            status, destination = item.get("status"), str(item.get("path") or "")
            if status in {"staged", "unchanged"} and destination:
                prepared += 1
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
        summary = f"{prepared} PDF" + ("s" if prepared != 1 else "") + " ready to import."
        if self._waiting_paths:
            summary += " Some PDFs still need preparation."
        self._set_result("\n".join(messages), summary)
        if not self._waiting_paths:
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
        Show a useful result count and clear details when the selected transaction is filtered out.
        """
        super()._filter_changed()
        shown = sum(not self.table.isRowHidden(row) for row in range(len(self.records)))
        if self._refresh_error or self.snapshot.get("error") or self.snapshot.get("ready") is False:
            message = "Transactions unavailable. Check Setup and refresh the workspace."
        elif not shown:
            message = ""
        else:
            message = f"{shown} of {len(self.records)} transactions in this reporting month"
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
        if self._importing_paths:
            later = sum(path not in self._importing_paths for path in self.paths) + waiting
            label = "PDF" if len(self._importing_paths) == 1 else "PDFs"
            message = f"Importing {len(self._importing_paths)} {label}"
            if later:
                message += f" · {later} queued for later"
        elif self._staging_paths:
            count_staging = len(self._staging_paths)
            message = f"Preparing {count_staging} PDF" + ("s" if count_staging != 1 else "")
        else:
            label = "PDF" if count == 1 else "PDFs"
            message = f"{count} {label} ready" if count else ""
            if waiting:
                message += (" · " if message else "") + f"{waiting} to prepare"
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

    def _show_imports(self) -> None:
        """
        Open saved import history on demand without disturbing the selected transaction or queued PDFs.
        """
        self._refresh_history()
        if self.history_dialog.isVisible():
            self.history_dialog.raise_()
            self.history_dialog.activateWindow()
        else:
            self.history_dialog.open()

    def _refresh_history(self) -> None:
        """
        Display each filename's latest successful import without exposing every uploaded revision.
        """
        rows = self.snapshot.get("imports", [])
        self.history_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column, field in enumerate(("source_file", "imported_at", "parsed_count", "inserted_count")):
                value = _text(row.get(field))
                item = QTableWidgetItem(Path(value).name if field == "source_file" and value else value)
                item.setToolTip(value)
                self.history_table.setItem(row_index, column, item)
        for column in (1, 2, 3):
            self.history_table.resizeColumnToContents(column)
        self.history_empty.setVisible(not rows)
        self.history_table.setVisible(bool(rows))

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
        if not super().select_record(identifier):
            return False
        self._selection_changed()
        return bool(self.current and str(self.current.get(self.identity)) == str(identifier))

    def _show_record(self, record: dict | None) -> None:
        """
        Keep the bottom detail panel aligned with the selected source and current snapshot state.
        """
        self.transaction_details.update_record(record, self.snapshot)
        self.transaction_details.set_stale(bool(self._refresh_error or self.snapshot.get("error")))
        self.detail.setVisible(record is not None)


# %% 3. Human Review -----------------------------------------------------------------------------------------
class ReviewPage(ActivityPage):
    """
    Bring bank questions, unfinished instructions and investment classifications into one review queue.
    """

    def __init__(self, parent=None):
        """
        Separate monthly processing from the selected answer, with a persistent save bar.
        """
        super().__init__(
            "Review", "Resolve bank questions, manual instructions and investment movements.",
            (("type_label", "Type"), ("date", "Date"), ("description", "Item"),
             ("status_label", "Status")), "review_id", parent,
        )
        self._pending_submission = None
        self._drafts = {}
        self._awaiting_refresh = False
        self._feedback = ""
        self._refresh_error = ""
        self._processing = False
        self._bank_aliases = {}
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(10)
        self.process_button = HeaderActionButton("Process")
        self.process_button.clicked.connect(lambda: self.action_requested.emit("process_journal", {}))
        self.subtitle_row.addWidget(self.process_button, 0, Qt.AlignmentFlag.AlignRight)
        self.subtitle_row.setContentsMargins(0, 0, 14, 0)
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
        self.scope = QComboBox()
        self.scope.setObjectName("reviewScope")
        self.scope.setView(QListView(self.scope))
        self.scope.setAccessibleName("Review item type")
        for label, kind in (("All types", ""), ("Bank", "bank"), ("Manual", "manual"),
                            ("Investments", "investment")):
            self.scope.addItem(label, kind)
        self.filter = QComboBox()
        self.filter.setObjectName("reviewFilter")
        self.filter.setView(QListView(self.filter))
        self.filter.setAccessibleName("Review work status")
        self.filter.addItems(["To do", "Needs answer", "Ready to process", "All bank transactions"])
        arrow = (Path(__file__).parent / "assets" / "chevron_down.svg").as_posix()
        self.filter.setStyleSheet(
            "QComboBox { padding-right: 28px; }"
            "QComboBox::drop-down { width: 28px; border: none; }"
            f'QComboBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 12px; }}'
        )
        self.scope.setStyleSheet(self.filter.styleSheet())
        self.filter.currentIndexChanged.connect(self._status_filter_changed)
        self.scope.currentIndexChanged.connect(self._scope_changed)
        queue_layout.addLayout(self.toolbar)
        filters = QHBoxLayout()
        filters.addWidget(self.scope, 1)
        filters.addWidget(self.filter, 1)
        queue_layout.addLayout(filters)
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
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.viewport().installEventFilter(self)
        self.detail.setMinimumWidth(320)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_layout.setSpacing(8)
        self.empty_detail = QWidget()
        empty_layout = QVBoxLayout(self.empty_detail)
        empty_layout.setContentsMargins(28, 28, 28, 28)
        empty_layout.setSpacing(10)
        empty_layout.addStretch()
        empty_icon = QLabel()
        empty_icon.setPixmap(icon("review", size=38, colour="#7c9a90").pixmap(38, 38))
        empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_icon)
        empty_title = QLabel("Select an item to review")
        empty_title.setObjectName("workspaceSection")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_hint = QLabel("Choose an item from the queue to see its details and next action.")
        empty_hint.setObjectName("workspaceHint")
        empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_hint.setWordWrap(True)
        empty_layout.addWidget(empty_hint)
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
        status_row = QHBoxLayout()
        status_row.setContentsMargins(DETAIL_TEXT_INSET, 0, 14, 0)
        self.question = QLabel()
        self.question.setObjectName("workspaceHint")
        self.question.setWordWrap(True)
        self.question.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.question.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.question.setTextFormat(Qt.TextFormat.PlainText)
        self.question.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        status_row.addWidget(self.question, 1, Qt.AlignmentFlag.AlignTop)
        self.direct_button = QPushButton("Edit")
        self.direct_button.setIcon(icon("pen", size=16))
        self.direct_button.setToolTip("Edit the complete journal entry")
        self.direct_button.clicked.connect(self._resolve_manually)
        status_row.addWidget(self.direct_button, 0, Qt.AlignmentFlag.AlignTop)
        content_layout.addLayout(status_row)
        self.response_panel = QWidget()
        self.response_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        editor_layout = QVBoxLayout(self.response_panel)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(6)
        editor_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.manual_button = QPushButton("Open manual instruction")
        self.manual_button.clicked.connect(self._open_manual)
        editor_layout.addWidget(self.manual_button)
        label = QLabel("Response")
        label.setObjectName("workspaceSection")
        label.setContentsMargins(DETAIL_TEXT_INSET, 0, 0, 0)
        editor_layout.addWidget(label)
        self.answer = QPlainTextEdit()
        self.answer.setObjectName("reviewResponse")
        self.answer.document().setDocumentMargin(0)
        self.answer.setFixedHeight(128)
        self.answer.setPlaceholderText("Explain the treatment. Add #remember before a rule to keep.")
        self.answer.setToolTip(
            "Save #remember followed by a reusable rule to share it with both AI workflows. "
            "A trailing #remember keeps the whole response. Edit or remove the tag to update memory."
        )
        editor_layout.addWidget(self.answer)
        content_layout.addWidget(self.response_panel, 0, Qt.AlignmentFlag.AlignTop)
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
        self.other_action.setIcon(icon("pen", size=16))
        self.other_action.clicked.connect(self._open_other)
        status_row.addWidget(self.other_action, 0, Qt.AlignmentFlag.AlignTop)
        self.other_panel.hide()
        content_layout.addWidget(self.other_panel)
        content_layout.addStretch()
        self.splitter.setSizes([450, 420])
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)

        footer = QFrame()
        self.footer = footer
        footer.setObjectName("workspacePanel")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(12, 10, 12, 10)
        self.save_status = QLabel()
        self.save_status.setObjectName("workspaceStatus")
        self.save_status.setWordWrap(True)
        footer_layout.addWidget(self.save_status, 1)
        self.discard_changes_button = QPushButton("Discard changes")
        self.discard_changes_button.clicked.connect(self._discard_local_changes)
        footer_layout.addWidget(self.discard_changes_button)
        self.discard_button = QPushButton("Discard pending answer")
        self.discard_button.clicked.connect(self._discard_pending)
        footer_layout.addWidget(self.discard_button)
        self.save_button = QToolButton()
        self.save_button.setObjectName("viewerIconAction")
        self.save_button.setIcon(icon("save", size=20))
        self.save_button.setIconSize(QSize(20, 20))
        self.save_button.setFixedSize(36, 36)
        self.save_button.setAccessibleName("Save all edited answers")
        self.save_button.setToolTip("Save all edited answers")
        self.save_button.setAutoRaise(True)
        self.save_button.clicked.connect(self._save)
        footer_layout.addWidget(self.save_button)
        self.layout.addWidget(footer)
        self.answer.textChanged.connect(self._edited)
        self._show_record(None)
        self._filter_changed()

    def eventFilter(self, watched, event) -> bool:
        """
        Keep item descriptions readable in a narrow queue; dates remain in the selected item's details.
        """
        if watched is self.table.viewport() and event.type() == QEvent.Type.Resize:
            self.table.setColumnHidden(1, event.size().width() < 500)
        return super().eventFilter(watched, event)

    def _queue_rows(self, snapshot: dict) -> list[dict]:
        """
        Combine saved review sources without replacing their original IDs, dates or revision evidence.
        """
        terminal = {"applied", "posted", "withdrawn", "superseded"}
        manuals = {
            str(row["manual_entry_id"]): row for row in snapshot.get("manuals", [])
            if str(row.get("status", "")).casefold() not in terminal
        }
        self._bank_aliases = {}
        rows = []
        for source in snapshot.get("reviews", []):
            identifier = str(source["transaction_id"])
            manual = source.get("blocking_manual_entry_id") or source.get("manual_entry_id")
            owner = f"manual:{manual}" if str(manual) in manuals else ""
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
        labels = {"needs_answer": "Needs answer", "ready": "Ready to process",
                  "retry": "Retry needed", "complete": "Posted"}
        for row in rows:
            state = self._work_state(row)
            row["work_state"] = state
            row["status_label"] = "Classify" if row["kind"] == "investment" else labels[state]
        order = {"needs_answer": 0, "retry": 1, "ready": 2, "complete": 3}
        return sorted(rows, key=lambda row: (
            order[row["work_state"]], str(row.get("date", "")), row["review_id"],
        ))

    def _work_state(self, record: dict) -> str:
        """
        Describe the next action from saved processing states, without treating old questions as new answers.
        """
        if record.get("kind") == "investment":
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
            by_id[identifier] = draft["record"]
        rows = list(by_id.values())
        selected = self.current.get(self.identity) if self.current else None
        if selected and selected not in self._drafts:
            self.current = by_id.get(selected)
        self._set_records(rows)
        if selected and selected not in self._drafts:
            self._show_record(self.current)
        for column, width in ((0, 104), (1, 110), (3, 132)):
            self.table.setColumnWidth(column, width)
        for index, row in enumerate(self.records):
            if row[self.identity] in self._drafts:
                self.table.item(index, 3).setText("Unsaved")
            self.table.item(index, 2).setToolTip(
                f"{row.get('account', '')}\n{row.get('description', '')}\n"
                f"{row.get('amount', '')} AUD\n{row['status_label']}"
            )
        month = snapshot.get("month") or "the selected reporting month"
        dates = f"{snapshot.get('period_start', '')} to {snapshot.get('period_end', '')}"
        self.process_button.setToolTip(
            f"Process saved manual instructions, bank answers and bank transactions for {month} ({dates}), "
            "including complete linked events outside the month. Classify investment movements separately."
        )
        self._update_controls()

    def has_unsaved_changes(self) -> bool:
        """
        Report all retained local answers, including drafts outside the current selection or filter.
        """
        return bool(
            self._drafts or self.current and self.current.get("kind") == "bank" and hasattr(self, "answer")
            and self.answer.toPlainText() != self._baseline
        )

    def _capture_draft(self) -> None:
        """
        Retain the exact multiline answer with the facts and concurrency tokens originally displayed.
        """
        if not self.current or self.current.get("kind") != "bank" or not hasattr(self, "answer"):
            return
        identifier = self.current[self.identity]
        text = self.answer.toPlainText()
        if text != self._baseline:
            self._drafts[identifier] = {"record": deepcopy(self.current), "text": text}
        else:
            self._drafts.pop(identifier, None)

    def discard_changes(self) -> None:
        """
        Restore the latest saved bank revision only after an explicit decision to discard local input.
        """
        identifier = self.current.get(self.identity) if self.current else None
        self._drafts.clear()
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
        target = self._bank_aliases.get(raw, f"bank:{raw}")
        return self.select_item(raw if raw.startswith(("bank:", "manual:", "investment:")) else target)

    def select_item(self, identifier: str) -> bool:
        """
        Reveal a stable typed review item while respecting any unsaved bank answer before selection changes.
        """
        identifier = str(identifier)
        target = next((row for row in self.records if row["review_id"] == identifier), None)
        if target is None:
            return False
        self.scope.setCurrentIndex(self.scope.findData(target["kind"]))
        bank_history = target["kind"] == "bank" and (
            target["work_state"] == "complete" or target.get("suppressed_by")
        )
        self.filter.setCurrentIndex(3 if bank_history else 0)
        self.search.clear()
        row = next(index for index, item in enumerate(self.records) if item["review_id"] == identifier)
        self.table.selectRow(row)
        return bool(self.current and self.current["review_id"] == identifier)

    def select_kind(self, kind: str) -> bool:
        """
        Focus one review type without starting work or discarding an edited answer.
        """
        kind = {"all": "", "investments": "investment"}.get(kind, kind)
        index = self.scope.findData(kind)
        if index < 0:
            return False
        self.scope.setCurrentIndex(index)
        self.filter.setCurrentIndex(0)
        self.search.clear()
        return True

    def _status_filter_changed(self) -> None:
        """
        Make the complete bank-history filter explicit without conflicting with the selected type.
        """
        if self.filter.currentIndex() == 3:
            self.scope.setCurrentIndex(self.scope.findData("bank"))
        self._filter_changed()

    def _scope_changed(self) -> None:
        """
        Restore the unfinished-work filter when switching away from complete bank history.
        """
        if self.filter.currentIndex() == 3 and self.scope.currentData() != "bank":
            self.filter.setCurrentIndex(0)
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
            self._pending_submission = {"action": action, "answers": deepcopy(payload["answers"])}
            self._feedback = ""
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
            self._feedback = message
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
            expected = {str(answer["transaction_id"]) for answer in pending["answers"]}
            acknowledged = isinstance(result, dict) and set(result.get("transaction_ids", [])) == expected
            if not acknowledged:
                self._feedback = "The save did not complete. Your edited answers have been kept."
                self._update_controls()
                return
            for answer in pending["answers"]:
                identifier = "bank:" + str(answer["transaction_id"])
                draft = self._drafts.get(identifier)
                if draft and draft["text"] == answer["text"]:
                    self._drafts.pop(identifier)
                if self.current and self.current.get(self.identity) == identifier:
                    self.current = {**self.current, "human_review": answer["text"], "review_status": "pending"}
                    self._baseline = answer["text"]
            self._awaiting_refresh = True
            self._feedback = ""
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
        Filter work type and next action while retaining full bank history for deliberate corrections.
        """
        if not hasattr(self, "scope"):
            return True
        kind = self.scope.currentData()
        if kind and record.get("kind") != kind:
            return False
        selected = self.filter.currentIndex()
        if selected == 3:
            return record.get("kind") == "bank"
        if record.get("suppressed_by"):
            return False
        state = record.get("work_state")
        if selected == 1:
            return state == "needs_answer"
        if selected == 2:
            return state == "ready"
        return state != "complete"

    def _filter_changed(self) -> None:
        """
        Keep dirty bank input visible across filters and disclose unavailable investment review evidence.
        """
        query = self.search.text().casefold().strip()
        dirty = self.has_unsaved_changes()
        selected = self.current.get(self.identity) if self.current else None
        counts = {"bank": 0, "manual": 0, "investment": 0}
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
        elif not sum(counts.values()):
            message = ""
        else:
            message = (
                f"Bank {counts['bank']} · Manual {counts['manual']} · Investments {counts['investment']}"
            )
        if retained:
            message = "\n".join(filter(None, (message, "Your edited bank answer is kept visible.")))
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
        bank = not record or record.get("kind") == "bank"
        self.empty_detail.setVisible(record is None)
        self.detail_scroll.setVisible(record is not None)
        self.response_panel.setVisible(bool(record) and bank)
        self.other_panel.setVisible(not bank)
        self._baseline = str(record.get("human_review", "")) if record and bank else ""
        self._feedback = ""
        self.answer.blockSignals(True)
        draft = self._drafts.get(record.get(self.identity)) if record else None
        self.answer.setPlainText(draft["text"] if draft else self._baseline)
        self.answer.blockSignals(False)
        self._show_detail_card(record)
        self.question.setToolTip("")
        if not bank:
            self._show_other(record)
            self._update_controls()
            return
        if not record:
            self.question.clear()
        else:
            owned = record.get("manual_entry_id")
            blocking = record.get("blocking_manual_entry_id")
            question = str(record.get("question") or record.get("review_result") or "")
            if owned:
                question = "This event belongs to a manual instruction. Make its correction there."
            elif blocking:
                question = (
                    "A pending manual instruction claims this transaction. Resolve that instruction first."
                )
            elif not question:
                question = (
                    "Review the current postings. Explain a correction only if one is needed."
                    if record.get("status") == "posted" else
                    "Add guidance if needed, or process the month to categorise this transaction."
                )
            self.question.setText("Status: " + question)
            self.question.setToolTip(str(record.get("review_result") or ""))
        self._update_controls()

    def _show_detail_card(self, record: dict | None) -> None:
        """
        Reuse the complete journal card when postings exist; otherwise show original bank or instruction facts.
        """
        self.journal_details.update_record(None, self.snapshot)
        self.transaction_details.update_record(None, self.snapshot)
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
        elif record.get("kind") == "bank":
            self.transaction_details.update_record(record, self.snapshot)
        else:
            self.journal_details.update_record({
                **record, "description": record.get("instruction") or record.get("description") or "",
                "total_debit": None, "postings": [],
            }, self.snapshot)

    def _show_other(self, record: dict) -> None:
        """
        Keep manual and investment next steps beside the same shared facts, without a second response editor.
        """
        if record["kind"] == "manual":
            status = record.get("question") or record.get("error") or record["status_label"]
            self.question.setText("Status: " + str(status))
            sections = []
            for key, title in (("human_response", "Response"), ("error", "Last error")):
                if record.get(key):
                    sections.append(title + "\n" + str(record[key]))
            self.other_action.setText("Edit")
            self.other_action.setToolTip("Open this saved instruction without leaving Review.")
        else:
            self.question.setText("Status: Investment treatment needed.")
            sections = [f"Choose the treatment for line {record.get('line_number', '')}: "
                        + str(record.get("account") or "") + "."]
            start = str(self.snapshot.get("period_start") or "")
            if start and str(record.get("date") or "") < start:
                sections.append("This earlier movement still affects investment cost.")
            self.other_action.setText("Edit")
            self.other_action.setToolTip("Open the complete journal event and focus this investment posting.")
        self.other_details.setText("\n\n".join(sections))
        self.other_details.setVisible(bool(sections))

    def _open_other(self) -> None:
        """
        Route non-bank review work to its existing editor with all original identity fields intact.
        """
        if not self.current or not self.other_action.isEnabled():
            return
        if self.current.get("kind") == "manual":
            self.action_requested.emit("open_manual_editor", {
                "manual_entry_id": self.current["manual_entry_id"],
                "journal_entry_id": self.current.get("journal_entry_id") or None,
                "mode": "instruction",
            })
        elif self.current.get("kind") == "investment":
            self.action_requested.emit("open_entry_editor", {
                "journal_entry_id": self.current["journal_entry_id"],
                "line_number": self.current["line_number"],
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
        if not hasattr(self, "save_button"):
            return
        ready = (
            self.snapshot.get("ready", True) and not self.snapshot.get("error") and not self._refresh_error
        )
        self.journal_details.set_stale(not ready)
        self.transaction_details.set_stale(not ready)
        available = ready and not self._busy and not self._awaiting_refresh
        bank = bool(self.current and self.current.get("kind") == "bank")
        owned = bool(bank and self.current.get("manual_entry_id"))
        blocked = bool(bank and self.current.get("blocking_manual_entry_id"))
        interrupted = bool(self.current and self.current.get("review_status") == "requesting")
        editable = bank and not owned and not blocked and not interrupted
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
        manual = bank and (
            self.current.get("manual_entry_id") or self.current.get("blocking_manual_entry_id")
        )
        self.manual_button.setVisible(bool(manual))
        self.manual_button.setEnabled(bool(manual) and available)
        self.direct_button.setVisible(bank)
        self.direct_button.setEnabled(bank and available and not blocked)
        self.other_action.setVisible(bool(self.current) and not bank)
        self.other_action.setEnabled(bool(self.current) and not bank and available)
        self.footer.setVisible(bank or dirty)
        self.process_button.setEnabled(ready and not self._busy)
        self.process_button.setText("Processing…" if self._busy and self._processing else "Process")
        if self._feedback:
            message = self._feedback
        elif self._busy:
            message = "Work is running. Saved facts remain available to inspect."
        elif self._awaiting_refresh:
            message = "Saved. Refreshing the review."
        elif not ready:
            message = "Reviews unavailable. Check Setup and refresh."
        elif dirty:
            count = len(self._drafts)
            message = f"{count} unsaved answer{'s' if count != 1 else ''} · Save when ready"
        elif not self.current:
            message = "Select a transaction to review."
        elif owned:
            message = "Edit this event through its manual instruction."
        elif blocked:
            message = "Resolve the pending manual instruction before reviewing this transaction."
        elif interrupted:
            message = "Work was interrupted. Process the month to continue."
        elif pending:
            message = "Answer saved. Process the month to apply it."
        else:
            message = "No unsaved changes"
        self.save_status.setText(message)

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
            QMessageBox.information(self, "Answer needed", "Complete each edited answer before saving.")
            return
        answers = [{
            "transaction_id": draft["record"]["transaction_id"],
            "revision": draft["record"]["context_revision"],
            "answer_token": draft["record"].get("answer_token", ""), "text": draft["text"],
        } for draft in self._drafts.values()]
        self.action_requested.emit("save_reviews", {"answers": answers})

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
        if self.current and self.manual_button.isEnabled():
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
            self.current and self.current.get("kind") == "bank" and self.direct_button.isEnabled()
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
             ("total_debit", "Debits AUD"), ("total_credit", "Credits AUD")),
            "journal_entry_id", parent,
        )
        self._refresh_error = ""
        self._processing = False
        self.viewer = None
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(10)
        self.process_button = HeaderActionButton("Process")
        self.process_button.clicked.connect(lambda: self._request_action("process_journal"))
        self.subtitle_row.addWidget(self.process_button, 0, Qt.AlignmentFlag.AlignRight)
        self.subtitle_row.setContentsMargins(0, 0, 14, 0)
        self.search.setPlaceholderText("Search entries, accounts or posting descriptions")
        self.layout.removeWidget(self.empty)
        self.layout.insertWidget(2, self.empty)
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
                "debit_accounts": _posting_accounts(postings, "debit"),
                "credit_accounts": _posting_accounts(postings, "credit"),
            })
        self._set_records(rows)
        for column, width in ((0, 112), (2, 130), (3, 130)):
            self.table.setColumnWidth(column, width)
        for index, row in enumerate(self.records):
            self.table.item(index, 1).setToolTip(
                f"{row.get('description', '')}\nDebit accounts: {row['debit_accounts'] or '-'}\n"
                f"Credit accounts: {row['credit_accounts'] or '-'}"
            )
        self.process_button.setToolTip(
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
        Count matching entries and hide details when the selected event leaves the visible results.
        """
        super()._filter_changed()
        shown = sum(not self.table.isRowHidden(index) for index in range(len(self.records)))
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
        elif not shown:
            message = ""
        else:
            message = f"{shown} of {len(self.records)} posted entries in this reporting month"
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
        self.process_button.setText("Processing…" if self._busy and self._processing else "Process")

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
