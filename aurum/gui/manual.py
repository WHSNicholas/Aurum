# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     Gui - Manual                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum's manual-entry screen lists saved instructions and direct events, opening separate editors on request.

The form keeps a stable identity when correcting an earlier event. Saving submits a request to the controller;
it never calls AI or changes postings directly. Questions and responses belong to the shared Review queue.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from datetime import date
from uuid import uuid4

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTableWidget,
    QStyledItemDelegate, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.dates import parse_input_date
from aurum.gui.activity import ActivityPage, _posting_total, _postings_text, _status_text, _text
from aurum.gui.controls import DateField, HeaderActionButton
from aurum.gui.icons import icon
from aurum.gui.theme import gui_colour, monetary_font, monetary_text
from aurum.gui.viewers import DETAIL_SECTION_GAP, JournalDetails


# %% 1. Manual Instructions ----------------------------------------------------------------------------------
class ManualPage(ActivityPage):
    """
    Browse saved instructions and direct journal entries, opening a separate editor only on request.
    """

    def __init__(self, parent=None):
        """
        Keep creation prominent above saved history, with one named action for the selected entry.
        """
        super().__init__(
            "Manual", "Add an instruction or a journal entry, and inspect saved entries.",
            (("date", "Date"), ("summary", "Description"),
             ("display_amount", "Amount"), ("source_label", "Source"), ("status_label", "Status")),
            "manual_entry_id", parent,
        )
        self._workspace_available = False
        self._workspace_error = ""
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(12)
        self.search.setPlaceholderText("Search entries")
        self.search.setAccessibleName("Search saved manual entries")
        self.new_button = HeaderActionButton("New entry", icon_only=True)
        self.new_button.setIcon(icon("add", colour="#ffffff", size=16))
        self.new_button.setAccessibleName("New manual entry")
        self.new_button.setToolTip("Add a manual entry")
        self.new_button.clicked.connect(lambda: self._new("instruction"))
        self.subtitle_row.insertWidget(0, self.new_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.table.setObjectName("manualHistory")
        self.table.setMinimumHeight(160)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda _index: self._open_selected())
        self.layout.removeWidget(self.empty)
        self.layout.insertWidget(2, self.empty)
        self.empty.setObjectName("workspaceHint")
        self.empty.setWordWrap(True)
        self.detail_layout.setContentsMargins(0, DETAIL_SECTION_GAP - self.layout.spacing(), 0, 0)
        self.viewer = JournalDetails(self.detail, actions=False)
        self.viewer.action_requested.connect(self.action_requested.emit)
        self.viewer.dismissed.connect(self._hide_details)
        self.detail_layout.addWidget(self.viewer)
        self.instruction_detail = QWidget()
        self.instruction_detail.setObjectName("statementDetailPanel")
        self.instruction_detail.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        instruction_layout = QVBoxLayout(self.instruction_detail)
        instruction_layout.setContentsMargins(14, 12, 14, 12)
        instruction_layout.setSpacing(8)
        instruction_heading = QHBoxLayout()
        instruction_heading.setContentsMargins(10, 0, 0, 0)
        self.instruction_status = QLabel()
        self.instruction_status.setObjectName("workspaceSection")
        instruction_heading.addWidget(self.instruction_status)
        instruction_heading.addStretch()
        self.open_button = self._icon_button("Edit instruction", "pen")
        self.open_button.clicked.connect(self._open_selected)
        instruction_heading.addWidget(self.open_button)
        close = self._icon_button("Hide instruction details", "close")
        close.clicked.connect(self._hide_details)
        instruction_heading.addWidget(close)
        instruction_layout.addLayout(instruction_heading)
        self.instruction_text = QPlainTextEdit()
        self.instruction_text.setObjectName("statementDescription")
        self.instruction_text.setReadOnly(True)
        self.instruction_text.document().setDocumentMargin(0)
        self.instruction_text.setAccessibleName("Saved instruction")
        self.instruction_text.setFixedHeight(80)
        instruction_layout.addWidget(self.instruction_text)
        self.instruction_metadata = QLabel()
        self.instruction_metadata.setObjectName("workspaceHint")
        self.instruction_metadata.setContentsMargins(10, 0, 0, 0)
        self.instruction_metadata.setWordWrap(True)
        instruction_layout.addWidget(self.instruction_metadata)
        self.detail_layout.addWidget(self.instruction_detail)
        actions = QHBoxLayout()
        self.review_button = QPushButton("Open Review")
        self.review_button.setIcon(icon("review", size=16))
        self.review_button.setAccessibleName("Open this instruction in Review")
        self.review_button.clicked.connect(self._open_review)
        actions.addWidget(self.review_button)
        actions.addStretch()
        self.detail_layout.addLayout(actions)
        self.detail.hide()
        self.layout.removeWidget(self.splitter)
        self.table.setParent(self)
        self.detail.setParent(self)
        self.splitter.hide()
        self.detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.layout.addWidget(self.table)
        self.layout.addWidget(self.detail, 0, Qt.AlignmentFlag.AlignTop)
        self.layout.addStretch(1)
        self._update_actions()
        self._filter_changed()
        QWidget.setTabOrder(self.new_button, self.search)

    def refresh(self, snapshot: dict) -> None:
        """
        Read the current saved history without opening an editor or retaining stale actionable rows.
        """
        super().refresh(snapshot)
        self._workspace_available = snapshot.get("ready") is not False and not snapshot.get("error")
        self._workspace_error = str(snapshot.get("error") or "Check the workspace files in Setup.")
        rows = []
        if self._workspace_available:
            entries = {row.get("journal_entry_id"): row for row in snapshot.get("entries", [])}
            sources = {str(row["transaction_id"]): row for row in snapshot.get("transactions", [])}
            for record in snapshot.get("manuals", []):
                direct = record.get("entry_mode") == "direct"
                entry = entries.get(record.get("journal_entry_id"), {})
                postings = entry.get("postings") or record.get("postings", [])
                if direct:
                    summary = next((
                        line["description"] for line in postings
                        if str(line.get("description") or "").strip()
                    ), entry.get("description") or "Journal entry")
                else:
                    summary = record.get("instruction") or record.get("summary") or ""
                names = list(dict.fromkeys(
                    str(sources[str(identifier)]["account"])
                    for identifier in record.get("source_transactions", [])
                    if str(identifier) in sources and sources[str(identifier)].get("account")
                ))
                if not names:
                    names = list(dict.fromkeys(
                        str(line["account"]) for line in postings if line.get("account")
                    ))
                status = str(record.get("status") or "")
                status_label = (
                    "Posted" if record.get("journal_entry_id") and status in {"applied", "posted"} else
                    "Ready" if status in {"pending", "submitted", "ready"} else
                    "Review" if status in {
                        "needs_review", "unsupported", "failed", "error", "requesting",
                    } else
                    _status_text(status)
                )
                rows.append({
                    **record, "summary": summary, "status_label": status_label,
                    "source_label": "Manual journal" if direct else "Instruction",
                    "account_names": names, "display_amount": _posting_total(postings) or "—",
                })
        self._set_records(rows)
        for index, record in enumerate(rows):
            if record.get("entry_mode") == "direct" and record.get("instruction"):
                self.table.item(index, 1).setToolTip(
                    f"{record['summary']}\n\n{record['instruction']}"
                )
            self.table.item(index, 2).setToolTip(
                "Total journal debits; this amount has no cash-flow direction."
                if record["display_amount"] != "—" else "No journal amount is available yet."
            )
            self.table.item(index, 3).setToolTip("\n".join((
                record["source_label"], *record["account_names"],
            )))
            self.table.item(index, 4).setToolTip(str(
                record.get("question") or record.get("error") or _status_text(record.get("status"))
            ))
            for column in (2, 3, 4):
                self.table.item(index, column).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._size_columns()
        self._update_actions()

    def _size_columns(self) -> None:
        """
        Match statement-table proportions while reserving the remaining width for each saved description.
        """
        for column, width in ((0, 108), (2, 104), (3, 128), (4, 100)):
            self.table.setColumnWidth(column, width)

    def has_unsaved_changes(self) -> bool:
        """
        Keep editing state in the popup; this saved-history page never owns an unsaved form.
        """
        return False

    def set_busy(self, busy: bool) -> None:
        """
        Allow read-only history inspection while disabling new editors and competing processing jobs.
        """
        self._busy = busy
        self.viewer.set_busy(busy)
        self._update_actions()

    def action_failed(self, action: str, message: str) -> None:
        """
        Prevent opening stale history after an unsuccessful workspace refresh.
        """
        if action == "refresh":
            self._workspace_available = False
            self._workspace_error = message
            self._set_records([])
            self._update_actions()

    def select_record(self, identifier: str) -> bool:
        """
        Open a requested saved entry through the same popup route as the visible edit action.
        """
        if self._busy or not self._workspace_available:
            return False
        if not super().select_record(identifier):
            return False
        self._open_selected()
        return True

    def _filter_changed(self) -> None:
        """
        Count matching saved entries and close the outcome preview if its selected row disappears.
        """
        super()._filter_changed()
        shown = sum(not self.table.isRowHidden(index) for index in range(len(self.records)))
        if not self._workspace_available:
            message = "Saved entries are unavailable. " + self._workspace_error
        elif not shown:
            message = ""
        else:
            message = (
                f"{shown} entries" if shown == len(self.records) else f"{shown} of {len(self.records)} entries"
            )
        self.empty.setText(message)
        self.empty.setVisible(bool(message))
        selected = next((index for index, row in enumerate(self.records)
                         if self.current and row.get(self.identity) == self.current.get(self.identity)), None)
        if selected is None or self.table.isRowHidden(selected):
            self._loading = True
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
            self.current = None
            self._show_record(None)
            self._loading = False
        if hasattr(self, "open_button"):
            self._update_actions()
            self._fit_history()

    def _show_record(self, record: dict | None) -> None:
        """
        Inspect posted accounting or the saved instruction without borrowing an empty journal card.
        """
        if not hasattr(self, "viewer"):
            return
        self.detail.setVisible(bool(record))
        entry = None
        if record:
            entry_id = record.get("journal_entry_id")
            entry = next(
                (item for item in self.snapshot.get("entries", [])
                 if entry_id and item.get("journal_entry_id") == entry_id), None,
            )
            if entry is None and record.get("postings"):
                entry = {**record, "description": record.get("summary") or record.get("instruction") or ""}
        self.viewer.update_record(entry, self.snapshot)
        self.viewer.set_busy(self._busy)
        self.instruction_detail.setVisible(bool(record) and entry is None)
        if record:
            self.instruction_status.setText(
                "Needs answer" if record.get("status") == "needs_review"
                else _status_text(record.get("status"))
            )
            self.instruction_text.setPlainText(str(record.get("instruction") or record.get("summary") or ""))
            metadata = str(record.get("date") or "")
            metadata += " · Manual instruction"
            self.instruction_metadata.setText(metadata)
            self.instruction_metadata.setToolTip("\n".join(record.get("account_names", [])))
        self._fit_history()
        self._update_actions()

    def _fit_history(self) -> None:
        """
        Keep short saved lists beside their details while allowing longer histories to scroll normally.
        """
        if not hasattr(self, "instruction_detail"):
            return
        shown = [index for index in range(self.table.rowCount()) if not self.table.isRowHidden(index)]
        natural = self.table.horizontalHeader().sizeHint().height() + 2 * self.table.frameWidth()
        natural += sum(self.table.rowHeight(index) for index in shown[:8])
        natural += self.table.horizontalScrollBar().sizeHint().height()
        detail = self.detail.sizeHint().height() if self.current else 0
        available = self.height() - self.heading_block.height() - self.search.height() - detail - 80
        self.table.setFixedHeight(max(110, min(natural, max(110, available))))

    def resizeEvent(self, event) -> None:
        """
        Reserve room for the selected instruction when the workspace changes height.
        """
        super().resizeEvent(event)
        self._size_columns()
        self._fit_history()

    def _hide_details(self) -> None:
        """
        Clear selection without changing saved manual instructions or postings.
        """
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.current = None
        self._show_record(None)

    def _selected_index(self) -> int:
        """
        Read the highlighted row independently of Qt's later current-cell update on native clicks.
        """
        selected = self.table.selectionModel().selectedRows()
        return selected[0].row() if selected else -1

    def _selection_changed(self) -> None:
        """
        Preview the actual selected row without relying on the current-cell signal order.
        """
        if self._loading:
            return
        index = self._selected_index()
        self.current = self.records[index] if 0 <= index < len(self.records) else None
        self._show_record(self.current)

    def _icon_button(self, label: str, symbol: str) -> QToolButton:
        """
        Expose compact, named actions to mouse, keyboard and assistive technology.
        """
        button = QToolButton()
        button.setObjectName("manualIconAction")
        button.setAutoRaise(True)
        button.setFixedSize(32, 32)
        button.setIconSize(QSize(18, 18))
        button.setIcon(icon(symbol, size=18))
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        return button

    def _update_actions(self) -> None:
        """
        Name the selected entry's edit and Review destinations while preventing competing actions.
        """
        available = not self._busy and self._workspace_available
        self.new_button.setEnabled(available)
        index = self._selected_index()
        selected = 0 <= index < len(self.records) and not self.table.isRowHidden(index)
        record = self.records[index] if selected else {}
        direct = record.get("entry_mode") == "direct"
        label = "Edit journal entry" if direct else "Edit instruction"
        self.open_button.setToolTip(label)
        self.open_button.setAccessibleName(label)
        self.open_button.setEnabled(available and selected)
        self.viewer.set_header_action(
            label if selected else None, self._open_selected, symbol="pen", enabled=available and selected,
        )
        review = bool(record.get("question") or record.get("human_response") or record.get("error"))
        review = review or bool(record) and not direct and record.get("status") not in {
            "applied", "posted", "withdrawn", "superseded",
        }
        self.review_button.setVisible(review)
        self.review_button.setEnabled(available and selected)

    def _new(self, mode: str) -> None:
        """
        Request a fresh editor in the explicitly chosen instruction or direct-entry mode.
        """
        if not self._busy and self._workspace_available:
            self.action_requested.emit("open_manual_editor", {"mode": mode})

    def _open_selected(self) -> None:
        """
        Open the selected saved identity without guessing its entry mode from accounting status.
        """
        index = self._selected_index()
        if self._busy or not self._workspace_available or not 0 <= index < len(self.records):
            return
        if self.table.isRowHidden(index):
            return
        record = self.records[index]
        self.action_requested.emit("open_manual_editor", {
            "manual_entry_id": record["manual_entry_id"],
            "journal_entry_id": record.get("journal_entry_id") or None,
            "mode": "direct" if record.get("entry_mode") == "direct" else "instruction",
        })

    def _open_review(self) -> None:
        """
        Open the saved instruction's conversation in the shared Review queue.
        """
        if not self._busy and self._workspace_available and self.current:
            self.action_requested.emit("open_manual_review", {
                "manual_entry_id": self.current["manual_entry_id"],
            })

# %% 2. Instruction Editor ----------------------------------------------------------------------------------
class InstructionForm(QWidget):
    """
    Collect one revision-bound manual instruction inside the separate entry editor.
    """

    changed = Signal()

    def __init__(self, record: dict | None, snapshot: dict, parent=None, *, show_metadata: bool = True):
        """
        Load a stable saved identity or clean draft without emitting edits or changing accounting.
        """
        super().__init__(parent)
        self.record = deepcopy(record or {})
        self.snapshot = deepcopy(snapshot)
        self.manual_id = str(self.record.get("manual_entry_id") or self.record.get("id") or uuid4())
        self.record.setdefault("manual_entry_id", self.manual_id)
        self._sources = [str(value) for value in self.record.get("source_transactions", [])]
        self._busy = False
        self._loading = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 18, 4)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.metadata = QWidget()
        top = QHBoxLayout(self.metadata)
        top.setContentsMargins(0, 0, 0, 0)
        self.date = DateField(str(
            self.record.get("date") or snapshot.get("period_end") or date.today().isoformat()
        ))
        self.date.setAccessibleName("Instruction accounting date")
        self.date.setPlaceholderText("YYYY-MM-DD or DD-MM-YYYY")
        self.date.setMaximumWidth(190)
        top.addWidget(self.date)
        self.source_button = QPushButton("Bank transactions")
        self.source_button.setIcon(icon("statement", size=16))
        self.source_button.setAccessibleName("Choose linked bank transactions")
        self.source_button.clicked.connect(self._choose_sources)
        top.addWidget(self.source_button)
        top.addStretch()
        self.record_status = QLabel(_status_text(self.record.get("status")))
        self.record_status.setObjectName("workspaceHint")
        top.addWidget(self.record_status)
        self.metadata.setVisible(show_metadata)
        layout.addWidget(self.metadata)

        self.review_notice = QLabel("Questions and responses are in Review.")
        self.review_notice.setObjectName("workspaceHint")
        self.review_notice.setWordWrap(True)
        self.review_notice.setVisible(bool(
            self.record.get("question") or self.record.get("human_response") or self.record.get("error")
        ))
        layout.addWidget(self.review_notice)

        self.instruction = QPlainTextEdit(str(self.record.get("instruction") or ""))
        self.instruction.setAccessibleName("Manual instruction")
        self.instruction.setPlaceholderText("Describe the amount, accounts and what happened.")
        self.instruction.setToolTip(
            "Add #remember before a rule to keep for future entries. "
            "A trailing #remember keeps the whole instruction."
        )
        self.instruction.setFixedHeight(144)
        layout.addWidget(self.instruction)

        self._update_sources_tooltip()

        self.outcome_toggle = self._disclosure("Posted outcome")
        self.outcome_toggle.toggled.connect(self._show_outcome)
        layout.addWidget(self.outcome_toggle)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(100)
        self.details.setMaximumHeight(180)
        outcome = f"Status: {_status_text(self.record.get('status'))}"
        if self.record.get("postings"):
            if self.record.get("status") not in {"applied", "posted"}:
                outcome += "\n\nCurrent posted entry; correction is still pending."
            outcome += "\n\n" + _postings_text(self.record["postings"])
        self.details.setPlainText(outcome)
        layout.addWidget(self.details)
        self.outcome_toggle.setVisible(bool(self.record.get("postings")))
        expanded = bool(self.record.get("postings"))
        self.outcome_toggle.setChecked(expanded)
        self._show_outcome(expanded)

        self.error = QLabel()
        self.error.setObjectName("workspaceError")
        self.error.setWordWrap(True)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.hide()
        layout.addWidget(self.error)
        layout.addStretch()
        for field in (self.date, self.instruction):
            field.textChanged.connect(self._edited)
        self._baseline = self.values()
        self._loading = False

    def values(self) -> tuple:
        """
        Return exact editable state for change guards without normalising multiline text or evidence order.
        """
        return (
            self.date.text(), self.instruction.toPlainText(), tuple(self._sources),
        )

    def _values(self) -> tuple:
        """
        Retain the previous form's internal state accessor for controller compatibility.
        """
        return self.values()

    def payload(self) -> dict:
        """
        Validate input and return the complete instruction with its originally displayed revision.

        Raises:
            ValueError: The accounting date or instruction is missing or invalid.
        """
        entered_date = parse_input_date(self.date.text().strip())
        if entered_date is None:
            raise ValueError("Enter an accounting date.")
        if not self.instruction.toPlainText().strip():
            raise ValueError("Write an instruction before saving.")
        return {
            "id": self.manual_id, "date": entered_date.isoformat(),
            "instruction": self.instruction.toPlainText(),
            "source_transactions": list(self._sources),
            "human_response": str(self.record.get("human_response") or ""),
            "context_revision": self.record.get("context_revision", ""),
        }

    def has_unsaved_changes(self) -> bool:
        """
        Compare only editable input against the last loaded or explicitly acknowledged state.
        """
        return self.values() != self._baseline

    def set_busy(self, busy: bool) -> None:
        """
        Prevent input edits during a job while leaving the saved outcome available to inspect.
        """
        self._busy = busy
        self.date.setReadOnly(busy)
        self.instruction.setReadOnly(busy)
        self.source_button.setEnabled(not busy and self.snapshot.get("ready") is not False)

    def show_error(self, message: str) -> None:
        """
        Explain a failed or stale save without replacing entered values or their original revision.
        """
        text = str(message)
        self.error.setText(text)
        self.error.setVisible(bool(message))

    def mark_saved(self) -> None:
        """
        Acknowledge the current values after confirmed saving, without inventing a fresh server revision.
        """
        self._baseline = self.values()
        self.record.update({
            "instruction": self.instruction.toPlainText(),
            "source_transactions": list(self._sources),
        })
        self.record_status.setText("Saved")
        self.show_error("")
        self.changed.emit()

    def _edited(self) -> None:
        """
        Notify the popup about deliberate edits while ignoring initial field loading.
        """
        if not self._loading:
            self.changed.emit()

    def _disclosure(self, label: str) -> QToolButton:
        """
        Use a thin shared chevron for optional sections while retaining standard toggle semantics.
        """
        button = QToolButton()
        button.setObjectName("manualDisclosure")
        button.setText(label)
        button.setAccessibleName(label)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setIcon(icon("chevron_right", size=14))
        button.setIconSize(QSize(14, 14))
        return button

    def _show_outcome(self, visible: bool) -> None:
        """
        Expand complete saved posting details independently of editable instruction state.
        """
        self.details.setVisible(visible)
        self.outcome_toggle.setIcon(icon("chevron_down" if visible else "chevron_right", size=14))

    def _update_sources_tooltip(self) -> None:
        """
        Keep selected evidence discoverable without an extra disclosure or count row.
        """
        count = len(self._sources)
        records = {str(row["transaction_id"]): row for row in self.snapshot.get("transactions", [])}
        details = []
        for identifier in self._sources:
            row = records.get(identifier, {})
            details.append(" · ".join(
                str(row.get(key) or "") for key in ("date", "account", "description", "amount")
                if row.get(key)
            ) or identifier)
        message = f"{count} selected bank transaction{'s' if count != 1 else ''}." if count else (
            "Link bank transactions to this instruction."
        )
        self.source_button.setToolTip("\n".join([message, *details]))
        self.source_button.setAccessibleDescription(message)

    def _choose_sources(self) -> None:
        """
        Retain existing evidence order and out-of-month links when adding or removing visible transactions.
        """
        if self._busy or self.snapshot.get("ready") is False:
            return
        dialog = SourcePicker(self.snapshot.get("transactions", []), self._sources, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        chosen = dialog.selected_sources()
        updated = [value for value in self._sources if value in chosen]
        updated += [value for value in chosen if value not in updated]
        if updated != self._sources:
            self._sources = updated
            self._update_sources_tooltip()
            self._edited()


# %% 3. Bank Evidence Selection ------------------------------------------------------------------------------
class SourcePicker(QDialog):
    """
    Select imported bank evidence by readable facts while keeping stable transaction references internally.
    """

    def __init__(self, transactions: list[dict], selected: list[str], parent=None):
        """
        Build a searchable checklist and retain previously selected references outside its supplied snapshot.
        """
        super().__init__(parent)
        self.setWindowTitle("Choose bank evidence")
        self.resize(920, 480)
        self.records = transactions
        visible_ids = {str(row["transaction_id"]) for row in transactions}
        self.unlisted = [value for value in selected if value not in visible_ids]
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by account, date, description or amount")
        layout.addWidget(self.search)
        self.table = QTableWidget(len(transactions), 5)
        self.table.setHorizontalHeaderLabels(["Use", "Date", "Account", "Description", "Amount"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setItemDelegateForColumn(0, CheckDelegate(self.table))
        self.table.installEventFilter(self)
        self.table.verticalHeader().hide()
        for index, row in enumerate(transactions):
            item = QTableWidgetItem()
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable
            )
            item.setCheckState(
                Qt.CheckState.Checked if str(row["transaction_id"]) in selected else Qt.CheckState.Unchecked
            )
            self.table.setItem(index, 0, item)
            for column, field in enumerate(("date", "account", "description", "amount"), 1):
                value = monetary_text(row.get(field)) if field == "amount" else _text(row.get(field))
                item = QTableWidgetItem(value.upper() if field == "description" else value)
                if field == "amount":
                    item.setFont(monetary_font(self.table.font()))
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(index, column, item)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table, 1)
        self.count = QLabel("")
        layout.addWidget(self.count)
        if self.unlisted:
            label = QLabel(
                f"{len(self.unlisted)} existing selection(s) outside this month will also be retained."
            )
            label.setWordWrap(True)
            layout.addWidget(label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self._filter)
        self.table.cellClicked.connect(self._toggle_row)
        self.table.itemChanged.connect(self._update_count)
        self._update_count()

    def selected_sources(self) -> list[str]:
        """
        Return checked transaction identities together with existing evidence outside this month's table.
        """
        return self.unlisted + [
            str(record["transaction_id"]) for index, record in enumerate(self.records)
            if self.table.item(index, 0).checkState() == Qt.CheckState.Checked
        ]

    def eventFilter(self, watched, event) -> bool:
        """
        Toggle the selected row with Space even when focus is on one of its descriptive cells.
        """
        if watched is self.table and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select) and self.table.currentRow() >= 0:
                self._toggle_row(self.table.currentRow(), 1)
                return True
        return super().eventFilter(watched, event)

    def _filter(self, text: str) -> None:
        """
        Search readable source facts without changing any checked evidence.
        """
        query = text.strip().casefold()
        for index, record in enumerate(self.records):
            facts = " ".join(_text(value) for value in record.values()).casefold()
            self.table.setRowHidden(index, bool(query) and query not in facts)

    def _toggle_row(self, row: int, column: int) -> None:
        """
        Let clicking readable transaction facts toggle evidence as well as clicking its checkbox.
        """
        if column != 0:
            item = self.table.item(row, 0)
            item.setCheckState(
                Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked
                else Qt.CheckState.Checked
            )

    def _update_count(self, *_args) -> None:
        """
        Show selection feedback immediately, including checked rows hidden by search.
        """
        count = len(self.selected_sources())
        self.count.setText(f"{count} transaction{'s' if count != 1 else ''} selected")


class CheckDelegate(QStyledItemDelegate):
    """
    Paint evidence checkboxes consistently instead of relying on macOS native indicator rendering.
    """

    def paint(self, painter: QPainter, option, index) -> None:
        """
        Draw a visible unchecked outline or a themed checked box in the centre of the cell.
        """
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(option.rect, QColor(gui_colour(option.widget, "surface", "#ffffff")))
        rectangle = QRectF(
            option.rect.center().x() - 9, option.rect.center().y() - 9, 18, 18,
        )
        state = index.data(Qt.ItemDataRole.CheckStateRole)
        checked = state in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        outline = gui_colour(option.widget, "accent" if checked else "border", "#738d85")
        painter.setPen(QPen(QColor(outline), 1.5))
        painter.setBrush(QColor(gui_colour(option.widget, "accent" if checked else "surface", "#ffffff")))
        painter.drawRoundedRect(rectangle, 3, 3)
        if checked:
            path = QPainterPath()
            path.moveTo(rectangle.left() + 4, rectangle.top() + 9)
            path.lineTo(rectangle.left() + 8, rectangle.top() + 13)
            path.lineTo(rectangle.left() + 14, rectangle.top() + 5)
            painter.setPen(QPen(QColor(gui_colour(option.widget, "on_accent", "#ffffff")), 2.2))
            painter.drawPath(path)
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:
        """
        Toggle checked state with a click anywhere in the Use cell or with Space on that cell.
        """
        clicked = (
            event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton
        )
        keyboard = (
            event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Select)
        )
        if clicked or keyboard:
            value = index.data(Qt.ItemDataRole.CheckStateRole)
            checked = value in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
            state = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
            return model.setData(index, state, Qt.ItemDataRole.CheckStateRole)
        return False
