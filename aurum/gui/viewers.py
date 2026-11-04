# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                    GUI - Viewers                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Inspect statement facts and journal events inline, with separate explicit editing actions.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from decimal import Decimal
import json

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QPlainTextEdit, QSizePolicy, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.icons import icon

DETAIL_TEXT_INSET = 24
DETAIL_SECTION_GAP = 18


# %% 1. Shared Viewer State ----------------------------------------------------------------------------------
class _RecordPanel(QWidget):
    """
    Keep read-only snapshot inspection separate from guarded navigation and accounting actions.
    """

    action_requested = Signal(str, object)

    def __init__(self, title: str, width: int, height: int, parent=None):
        """
        Keep a compact inline inspector with an explicit hide action.
        """
        super().__init__(parent)
        self.setObjectName("statementDetailPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.record = {}
        self.snapshot = {}
        self._busy = False
        self._stale = False
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(14, 12, 14, 12)
        self.layout.setSpacing(8)
        self.heading = QLabel(title)
        self.heading.setObjectName("viewerTitle")
        self.heading.hide()
        self.notice = QLabel()
        self.notice.setObjectName("workspaceHint")
        self.notice.setWordWrap(True)
        self.notice.hide()
        self.layout.addWidget(self.notice)

    def set_busy(self, busy: bool) -> None:
        """
        Keep inspection and copying available while a worker temporarily blocks further actions.
        """
        self._busy = busy
        self._update_actions()

    def set_stale(self, stale: bool) -> None:
        """
        Mark a failed-refresh snapshot as read-only until fresh evidence is available.
        """
        self._stale = stale
        self.notice.setText("Showing the previous saved snapshot. Refresh before taking another action.")
        self.notice.setVisible(stale)
        self._update_actions()

    def _available(self) -> bool:
        """
        Require current, readable saved facts and an idle controller before routing another operation.
        """
        return bool(
            not self._busy and not self._stale and self.snapshot.get("ready", True)
            and not self.snapshot.get("error")
        )

    def _update_actions(self) -> None:
        """
        Let the concrete viewer enable its available record actions.
        """

    def _request(self, action: str, payload: dict) -> None:
        """
        Open a separate editor only after the user requests it.
        """
        if self._available():
            self.action_requested.emit(action, payload)


# %% 2. Statement Transaction Details ------------------------------------------------------------------------
class TransactionDetails(QWidget):
    """
    Show a selected bank line's original facts in a compact panel beneath the transaction table.
    """

    action_requested = Signal(str, object)
    dismissed = Signal()

    def __init__(self, parent=None, *, dismissible: bool = True, compact: bool = False):
        """
        Build an inline inspector that leaves remaining page space for the transaction table.
        """
        super().__init__(parent)
        self.setObjectName("statementDetailPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.record, self.snapshot = {}, {}
        self._busy = self._stale = False
        self._compact = compact
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(14, 12, 14, 12)
        self.layout.setSpacing(8)
        summary = QHBoxLayout()
        summary.setContentsMargins(10, 0, 0, 0)
        self.amount = _value("", "viewerAmount")
        if compact:
            self.amount.setWordWrap(False)
            self.amount.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        summary.addWidget(self.amount)
        self.status = _value("", "viewerStatus")
        summary.addWidget(self.status)
        summary.addStretch()
        self.copy_button = _icon_button("Copy complete transaction facts", "copy")
        self.copy_button.clicked.connect(self._copy)
        summary.addWidget(self.copy_button)
        close = _icon_button("Hide transaction details", "close")
        close.clicked.connect(self.dismissed.emit)
        close.setVisible(dismissible)
        summary.addWidget(close)
        self.layout.addLayout(summary)
        self.notice = _value("", "workspaceHint")
        self.notice.hide()
        self.layout.addWidget(self.notice)
        self.description = QPlainTextEdit()
        self.description.setObjectName("statementDescription")
        self.description.setReadOnly(True)
        self.description.document().setDocumentMargin(0)
        self.description.setAccessibleName("Full original bank description")
        self.description.setFixedHeight(46)
        self.layout.addWidget(self.description)
        facts = QGridLayout()
        facts.setContentsMargins(10, 0, 0, 0)
        facts.setHorizontalSpacing(24)
        facts.setVerticalSpacing(3)
        self.fields = {}
        for index, (key, label) in enumerate((
            ("date", "Statement date"), ("effective_date", "Effective date"),
            ("account", "Account"), ("transfer_reference", "Reference"),
        )):
            columns = 2 if compact else 5
            column, row = index % columns, (index // columns) * 2
            facts.addWidget(_label(label), row, column)
            value = _value()
            if compact and key in {"date", "effective_date"}:
                value.setWordWrap(False)
            facts.addWidget(value, row + 1, column)
            self.fields[key] = value
        if compact:
            facts.setColumnMinimumWidth(0, 144)
            facts.setColumnMinimumWidth(1, 144)
            facts.setColumnStretch(2, 1)
        else:
            for column, width in enumerate((108, 108, 136, 96, 200)):
                facts.setColumnMinimumWidth(column, width)
            facts.setColumnStretch(5, 1)
        source_row, source_column = (4, 0) if compact else (0, 4)
        span = 2 if compact else 1
        facts.addWidget(_label("Statement"), source_row, source_column, 1, span)
        self.source = _value()
        if not compact:
            self.source.setMaximumWidth(240)
        facts.addWidget(self.source, source_row + 1, source_column, 1, span)
        self.layout.addLayout(facts)
        self.update_record(None, {})

    def set_busy(self, busy: bool) -> None:
        """
        Keep inspection available while a worker temporarily blocks review navigation.
        """
        self._busy = busy
        self._update_actions()

    def set_stale(self, stale: bool) -> None:
        """
        Retain readable saved facts after a failed refresh while preventing actions on stale evidence.
        """
        self._stale = stale
        self.notice.setText("Showing the previous saved snapshot. Refresh before taking another action.")
        self.notice.setVisible(stale)
        self._update_actions()

    def update_record(self, record: dict | None, snapshot: dict) -> None:
        """
        Follow the selected transaction without editing any saved source fact.
        """
        self.setVisible(record is not None)
        record = record or {}
        self.record, self.snapshot = deepcopy(record), snapshot
        self.amount.setText(_aud(record.get("amount")))
        self.description.setPlainText(str(record.get("description") or ""))
        status = _status(record.get("status") or "imported")
        review = record.get("review_status")
        if review in {"submitted", "stale", "needs_review", "failed", "interrupted"}:
            addition = _status(review)
            if addition != status:
                status += " · " + addition
        self.status.setText(status)
        for key, value in self.fields.items():
            text = str(record.get(key) or "—")
            if key == "transfer_reference" and text != "—":
                text = "#" + text
            value.setText(text)
            value.setToolTip(text)
        self.fields["account"].setToolTip(
            str(record.get("account") or "") + "\nAccount ID: " + str(record.get("account_id") or "")
        )
        self.source.setText(_statement_name(record.get("source_file")))
        self.source.setToolTip(str(record.get("source_file") or ""))
        self.copy_button.setToolTip(
            "Copy complete transaction facts\n" + str(record.get("transaction_id") or "")
        )
        self.copy_button.setEnabled(bool(record))
        self._update_actions()
        if self._compact:
            self.layout.invalidate()
            self.layout.activate()

    def _available(self) -> bool:
        """
        Require a selected transaction, a current snapshot and an idle controller for review navigation.
        """
        return bool(
            self.record and not self._busy and not self._stale and self.snapshot.get("ready", True)
            and not self.snapshot.get("error")
        )

    def _update_actions(self) -> None:
        """
        Keep copying available independently of current processing work.
        """
        self.copy_button.setEnabled(bool(self.record))

    def _copy(self) -> None:
        """
        Copy every available original field only in response to the explicit copy action.
        """
        if self.record:
            QApplication.clipboard().setText(_facts(self.record))


# %% 3. Journal Event Viewer ---------------------------------------------------------------------------------
class JournalDetails(_RecordPanel):
    """
    Show one saved event with its amount, accounting facts and ordered debit and credit postings.
    """

    dismissed = Signal()

    def __init__(self, parent=None, *, actions: bool = True, dismissible: bool = True, compact: bool = False):
        """
        Build the shared inline journal card used by Journal, Manual and Review.
        """
        super().__init__("Journal entry", 960, 300, parent)
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self._show_actions = actions
        self._compact = compact
        self._source_rows = []
        self._owner = None
        summary = QHBoxLayout()
        summary.setContentsMargins(10, 0, 0, 0)
        self.amount = _value()
        self.amount.setObjectName("viewerAmount")
        self.amount.setWordWrap(False)
        self.summary = self.amount
        summary.addWidget(self.amount)
        self.status = QLabel()
        self.status.setObjectName("viewerStatus")
        summary.addWidget(self.status)
        summary.addStretch()
        self.edit_button = _icon_button("Edit journal entry", "pen")
        self.edit_button.clicked.connect(self._edit)
        self.edit_button.setVisible(actions)
        summary.addWidget(self.edit_button)
        self.copy_button = _icon_button("Copy complete journal facts", "copy")
        self.copy_button.clicked.connect(self._copy)
        summary.addWidget(self.copy_button)
        close = _icon_button("Hide journal details", "close")
        close.clicked.connect(self.dismissed.emit)
        close.setVisible(dismissible)
        summary.addWidget(close)
        self.layout.insertLayout(0, summary)
        content = QVBoxLayout() if compact else QHBoxLayout()
        self._content_layout = content
        content.setSpacing(24)
        metadata = QWidget()
        metadata.setObjectName("journalMetadata")
        self._metadata = metadata
        metadata.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        metadata_layout = QVBoxLayout(metadata)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(8)
        self.description = QPlainTextEdit()
        self.description.setReadOnly(True)
        self.description.setObjectName("statementDescription")
        self.description.document().setDocumentMargin(0)
        self.description.setFixedHeight(42)
        metadata_layout.addWidget(self.description)
        facts = QGridLayout()
        facts.setContentsMargins(10, 0, 0, 0)
        facts.setHorizontalSpacing(16 if compact else 20)
        facts.setVerticalSpacing(3)
        self.fields = {}
        for index, (key, caption) in enumerate((
            ("date", "Accounting date"), ("sources", "Bank transactions"), ("budget", "Budget treatment"),
        )):
            facts.addWidget(_label(caption), 0, index)
            value = _value()
            value.setMinimumWidth(0)
            facts.addWidget(value, 1, index)
            self.fields[key] = value
        facts.setColumnStretch(3, 1)
        metadata_layout.addLayout(facts)
        content.addWidget(metadata, 0 if compact else 3, Qt.AlignmentFlag.AlignTop)
        self.postings = _table(("Account", "Debit", "Credit"))
        self.postings.setObjectName("journalPostingPreview")
        self.postings.setWordWrap(False)
        self.postings.verticalHeader().setMinimumSectionSize(28)
        self.postings.setMaximumWidth(720 if compact else 480)
        self.postings.setMinimumWidth(0)
        self.postings.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.postings.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        header = self.postings.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.postings.setColumnWidth(column, 94)
        content.addWidget(self.postings, 0 if compact else 2, Qt.AlignmentFlag.AlignTop)
        self.layout.addLayout(content)
        self.update_record(None, {})

    def update_record(self, record: dict | None, snapshot: dict) -> None:
        """
        Follow the selected event without changing its source facts, postings or edit identity.
        """
        self.setVisible(record is not None)
        self.record, self.snapshot = deepcopy(record or {}), snapshot
        self._source_rows, self._owner = [], None
        if record is None:
            self._update_actions()
            return
        lines = record.get("postings", [])
        total = record.get("total_debit")
        if total is None and lines:
            total = sum((Decimal(str(line.get("debit") or 0)) for line in lines), Decimal(0))
        self.amount.setText(_aud(abs(Decimal(str(total)))) if total is not None else "—")
        self.amount.setToolTip("Event amount: the total debit, equal to the total credit for a balanced entry.")
        self.status.setText(_status(record.get("status") or ("posted" if lines else "pending")))
        self.description.setPlainText(str(
            record.get("description") or record.get("instruction") or record.get("summary") or ""
        ))
        self.fields["date"].setText(str(record.get("date") or "—"))
        source_ids = record.get("source_transactions") or [
            line["transaction_id"] for line in lines if line.get("transaction_id")
        ]
        transactions = {str(row["transaction_id"]): row for row in snapshot.get("transactions", [])}
        self._source_rows = [
            transactions.get(str(identifier), {
                "transaction_id": str(identifier), "description": "Source facts unavailable in this snapshot.",
            }) for identifier in dict.fromkeys(source_ids)
        ]
        self.fields["sources"].setText(str(len(self._source_rows)))
        self.fields["sources"].setToolTip(
            "\n\n".join(_facts(source, internal=False) for source in self._source_rows)
        )
        entry_id = record.get("journal_entry_id")
        if entry_id:
            self._owner = next(
                (row for row in snapshot.get("manuals", []) if row.get("journal_entry_id") == entry_id), None,
            )
        if self._owner is None and record.get("manual_entry_id"):
            self._owner = record
        self.fields["budget"].setText(_budget_treatment(lines))
        self.fields["budget"].setToolTip("\n".join(
            f"{line.get('account', 'Posting')}: {_budget_label(line.get('budget_category'))}" for line in lines
        ))
        self.postings.setRowCount(len(lines))
        for row, line in enumerate(lines):
            for column, field in enumerate(("account", "debit", "credit")):
                value = line.get(field)
                text = str(value or "") if field == "account" else (
                    _aud(value, code=False) if Decimal(str(value or 0)) else "—"
                )
                item = QTableWidgetItem(text)
                item.setToolTip(_facts(line, internal=False))
                if column:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.postings.setItem(row, column, item)
        self.postings.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff if len(lines) <= 5 else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.postings.setVisible(bool(lines))
        self.copy_button.setToolTip("Copy complete journal facts\n" + str(entry_id or ""))
        self._update_actions()
        self._size_to_content()

    def set_stale(self, stale: bool) -> None:
        """
        Retain room for the freshness warning without obscuring accounting facts.
        """
        super().set_stale(stale)
        self._size_to_content()

    def _size_to_content(self) -> None:
        """
        Keep the inline panel only as tall as its visible metadata and ordinary posting rows.
        """
        self.ensurePolished()
        self.postings.ensurePolished()
        row_height = max(28, self.postings.fontMetrics().height() + 8)
        for row in range(self.postings.rowCount()):
            self.postings.setRowHeight(row, row_height)
        header = self.postings.horizontalHeader()
        # Qt's styled row minimum can exceed a requested row height; measure the actual visible sections.
        height = max(header.height(), header.sizeHint().height()) + 2 * self.postings.frameWidth()
        height += sum(self.postings.rowHeight(row) for row in range(min(5, self.postings.rowCount())))
        self.postings.setFixedHeight(height)
        self._metadata.layout().invalidate()
        self._metadata.layout().activate()
        self._metadata.updateGeometry()
        self._content_layout.invalidate()
        self.layout.invalidate()
        self.setFixedHeight(max(self.layout.sizeHint().height(), self.layout.totalHeightForWidth(self.width())))
        self.layout.activate()

    def resizeEvent(self, event) -> None:
        """
        Keep wrapped metadata visible when the surrounding pane changes width.
        """
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width() and hasattr(self, "postings"):
            self._size_to_content()

    def _update_actions(self) -> None:
        """
        Permit editing only when this exact saved record belongs to a fresh, idle snapshot.
        """
        if not hasattr(self, "edit_button"):
            return
        editable = bool(self.record.get("journal_entry_id") or self.record.get("manual_entry_id"))
        self.edit_button.setVisible(self._show_actions)
        self.edit_button.setEnabled(self._available() and editable)
        self.copy_button.setEnabled(bool(self.record))

    def _edit(self) -> None:
        """
        Route a saved event to its complete editor, or an unposted instruction to its existing form.
        """
        if self.record.get("journal_entry_id"):
            self._request("open_entry_editor", {"journal_entry_id": self.record["journal_entry_id"]})
        elif self.record.get("manual_entry_id"):
            self._request("open_manual_editor", {
                "manual_entry_id": self.record["manual_entry_id"], "mode": "instruction",
            })

    def _copy(self) -> None:
        """
        Copy exact postings, linked source facts and the original instruction on explicit request.
        """
        if not self.record:
            return
        sections = ["Journal entry\n" + _facts(self.record)]
        sections.extend("Bank transaction\n" + _facts(source) for source in self._source_rows)
        if self._owner:
            sections.append("Manual entry\n" + _facts(self._owner))
        QApplication.clipboard().setText("\n\n".join(sections))


# %% 4. Helpers ----------------------------------------------------------------------------------------------
def _budget_label(value) -> str:
    """
    Give explicit posting labels a concise readable name without inferring a new budget classification.
    """
    if value in {None, "", "default"}:
        return "Normal rules"
    if value == "exclude":
        return "Excluded"
    return str(value).replace("_", " ").title()


def _budget_treatment(lines: list[dict]) -> str:
    """
    Summarise the saved posting labels and retain mixed treatment rather than calling an event included.
    """
    labels = set(_budget_label(line.get("budget_category")) for line in lines)
    return next(iter(labels)) if len(labels) == 1 else "Custom by line" if labels else "—"


def _aud(value, *, code: bool = True) -> str:
    """
    Format exact signed AUD amounts with the minus before the dollar sign and narrow-space grouping.
    """
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            return "—"
    except (ValueError, ArithmeticError):
        return "—"
    text = format(abs(amount), ",.2f").replace(",", "\u202f")
    return ("-" if amount < 0 else "") + "$" + text + (" AUD" if code else "")


def _label(text: str) -> QLabel:
    """
    Identify a fact without giving its caption the visual emphasis of its value.
    """
    label = QLabel(text)
    label.setObjectName("viewerLabel")
    return label


def _statement_name(value) -> str:
    """
    Show a PDF filename while retaining the original stored stem in the underlying snapshot and copy text.
    """
    text = str(value or "")
    return text if not text or text.lower().endswith(".pdf") else text + ".pdf"


def _value(text: str = "", name: str = "viewerValue") -> QLabel:
    """
    Keep exact fact values selectable and wrapped inside the fixed-height viewer.
    """
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def _icon_button(label: str, symbol: str) -> QToolButton:
    """
    Expose a compact named viewer action without relying on an unexplained glyph alone.
    """
    button = QToolButton()
    button.setObjectName("viewerIconAction")
    button.setAutoRaise(True)
    button.setFixedSize(32, 32)
    button.setIconSize(QSize(18, 18))
    button.setIcon(icon(symbol, size=18))
    button.setAccessibleName(label)
    button.setToolTip(label)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return button


def _table(labels: tuple[str, ...]) -> QTableWidget:
    """
    Create a read-only single-row-selection table for ordered saved facts.
    """
    table = QTableWidget(0, len(labels))
    table.setHorizontalHeaderLabels(labels)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(36)
    table.setShowGrid(False)
    return table


def _selected_row(table: QTableWidget) -> int:
    """
    Read native selection independently of a delayed current-cell update.
    """
    rows = table.selectionModel().selectedRows()
    return rows[0].row() if rows else -1


def _status(value) -> str:
    """
    Present a saved status in ordinary words without inferring a new processing state.
    """
    return {"submitted": "Answer saved", "stale": "Needs refreshed answer"}.get(
        str(value), str(value).replace("_", " ").capitalize(),
    )


def _facts(record: dict, *, internal: bool = True) -> str:
    """
    Keep complete copyable evidence while showing readable values and hiding storage IDs in the viewer.
    """
    lines = []
    for key, value in record.items():
        if not internal and (
            key.endswith(("_id", "_ids")) or key in {
                "context_revision", "answer_token", "source_transactions", "postings", "entry_mode",
            }
        ):
            continue
        if isinstance(value, dict) and not internal:
            text = "\n" + _facts(value, internal=False)
        elif isinstance(value, (dict, list, tuple)):
            text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
        elif not internal and key in {"amount", "debit", "credit", "cost_basis", "statement_balance"}:
            text = _aud(value)
        elif not internal and key == "source_file":
            text = _statement_name(value) or "—"
        else:
            text = str(value) if value is not None else "—"
        lines.append(key.replace("_", " ").capitalize() + ": " + text)
    return "\n".join(lines)
