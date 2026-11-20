# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     Gui - Manual                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum's manual instruction form and source picker support the shared manual-entry dialog.

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
from aurum.gui.activity import _postings_text, _status_text, _text
from aurum.gui.controls import DateField
from aurum.gui.icons import icon
from aurum.gui.theme import gui_colour, monetary_font, monetary_text


# %% 1. Instruction Editor -----------------------------------------------------------------------------------
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
        self.source_button.setIcon(icon("attach_evidence", size=16))
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


# %% 2. Bank Evidence Selection ------------------------------------------------------------------------------
class SourcePicker(QDialog):
    """
    Select imported bank evidence by readable facts while keeping stable transaction references internally.
    """

    def __init__(self, transactions: list[dict], selected: list[str], parent=None):
        """
        Build a searchable checklist and retain previously selected references outside its supplied snapshot.
        """
        super().__init__(parent)
        self.setObjectName("bankEvidencePicker")
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
        self.table.setProperty("roundedTable", True)
        self.table.setShowGrid(False)
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
