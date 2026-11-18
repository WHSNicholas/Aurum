# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     Gui - Editor                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Edit complete journal events directly, without using AI to interpret accounting choices.

The form preserves imported bank facts and existing security-trade economics. Account selections and exact
AUD amounts become a complete replacement entry only after validation; the workflow checks saved revisions
again before committing. Opening this dialog or changing its fields never changes stored accounting.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from PySide6.QtCore import QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView,
    QPushButton, QStyledItemDelegate, QTableWidget, QToolButton, QToolTip, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.dates import parse_input_date
from aurum.finance.journal import JournalEntry
from aurum.gui.controls import DateField, confirm_discard
from aurum.gui.icons import icon
from aurum.gui.investment_disposal import InvestmentDisposalDialog
from aurum.gui.manual import SourcePicker


# %% 1. Direct Journal Editing -------------------------------------------------------------------------------
class JournalEditor(QDialog):
    """
    Collect a complete balanced event while retaining the revision and protected facts originally displayed.
    """

    action_requested = Signal(str, object)
    changed = Signal()

    def __init__(self, context: dict, parent=None, *, embedded: bool = False):
        """
        Build the posting form either as its own dialog or inside a parent-owned manual-entry composer.
        """
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle("Edit entry")
        self.setModal(not embedded)
        if not embedded:
            self.resize(1320, 800)
            self.setMinimumSize(1040, 570)
        self.context = deepcopy(context)
        self._opening = bool(context.get("opening"))
        self.entry = deepcopy(context.get("entry") or {})
        self.entry.setdefault("id", str(uuid4()))
        self.accounts = {str(row["id"]): row for row in context.get("accounts", [])}
        self.rows = []
        self._building = True
        self._busy = False
        self._saved = False
        self._baseline = None
        self._validation_target = None
        self._disposal_dialog = None
        self.protected_trade = bool(context.get("protected_trade"))
        layout = QVBoxLayout(self)
        if embedded:
            layout.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel("Edit entry")
        self.heading.setObjectName("pageTitle")
        self.heading.setVisible(not embedded)
        layout.addWidget(self.heading)
        if self.protected_trade:
            trade = QLabel(
                "This event has protected investment accounting. Its date, accounts and amounts are protected; "
                "you can edit non-bank descriptions."
            )
            trade.setWordWrap(True)
            trade.setObjectName("muted")
            layout.addWidget(trade)
        self.metadata = QWidget()
        top = QHBoxLayout(self.metadata)
        top.setContentsMargins(0, 0, 0, 0)
        self.date = DateField(str(self.entry.get("date") or ""))
        self.date.setAccessibleName("Accounting date")
        self.date.setPlaceholderText("YYYY-MM-DD or DD-MM-YYYY")
        self.date.setMaximumWidth(190)
        self.date.setEnabled(not self.protected_trade)
        top.addWidget(self.date)
        self.sources_button = QPushButton("Bank transactions")
        self.sources_button.setIcon(icon("statement", size=16))
        self.sources_button.setAccessibleName("Choose linked bank transactions")
        self.sources_button.clicked.connect(self._choose_sources)
        self.sources_button.setEnabled(not context.get("journal_entry_id"))
        self.sources_button.setToolTip(
            "Existing journal entries retain their complete bank transaction links."
            if context.get("journal_entry_id") else "Choose linked bank transactions"
        )
        top.addWidget(self.sources_button)
        top.addStretch()
        self.add_button = _posting_action("Add posting", "add")
        self.add_button.clicked.connect(self._add_line)
        self.add_button.setEnabled(not self.protected_trade)
        self.metadata.setVisible(not embedded)
        layout.addWidget(self.metadata)
        self.disposal_actions = QWidget()
        posting_actions = QHBoxLayout(self.disposal_actions)
        posting_actions.setContentsMargins(0, 0, 0, 0)
        self.disposal_button = _posting_action("Investment disposal", "chart")
        self.disposal_button.clicked.connect(self._open_disposal)
        disposal_sources = context.get("investment_disposals", {}).get("sources", [])
        self.disposal_actions.setVisible(
            len(disposal_sources) == 1 and Decimal(str(disposal_sources[0]["amount"])) > 0
            and bool(context.get("investment_disposals", {}).get("investment_accounts"))
            and not self.protected_trade
        )
        posting_actions.addWidget(self.disposal_button)
        posting_actions.addStretch()
        layout.addWidget(self.disposal_actions)
        self.table = QTableWidget(0, 5)
        self.table.setItemDelegateForColumn(4, _PostingActionDelegate(self.table))
        self.posting_header = _PostingHeader(self.add_button, self.table)
        self.table.setHorizontalHeader(self.posting_header)
        self.table.setObjectName("journalPostingTable")
        self.table.setShowGrid(False)
        self.table.setStyleSheet(
            "QTableWidget#journalPostingTable QHeaderView::section { border: none; }"
        )
        self.table.setHorizontalHeaderLabels([
            "Account", "Description", "Debit", "Credit", "",
        ])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        widths = (230, 220, 100, 100, 48) if embedded else (250, 235, 110, 110, 48)
        for column, width in enumerate(widths):
            self.table.setColumnWidth(column, width)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.horizontalScrollBar().valueChanged.connect(self.posting_header.position_action)
        layout.addWidget(self.table)
        self.investment_options = QWidget()
        self.investment_layout = QVBoxLayout(self.investment_options)
        self.investment_layout.setContentsMargins(0, 0, 0, 0)
        self.investment_layout.setSpacing(8)
        layout.addWidget(self.investment_options)
        self.validation = QLabel("")
        self.validation.setWordWrap(True)
        self.validation.setObjectName("editorError")
        self.validation.hide()
        layout.addWidget(self.validation)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setObjectName("editorError")
        layout.addWidget(self.error)
        layout.addStretch()
        self.actions_widget = QWidget()
        self.actions_widget.setObjectName("journalEditorActions")
        actions = QHBoxLayout(self.actions_widget)
        actions.setContentsMargins(0, 0, 0, 0)
        self.reload_button = _editor_action("Reload saved entry", "refresh")
        self.reload_button.clicked.connect(self._reload)
        self.reload_button.hide()
        actions.addWidget(self.reload_button)
        actions.addStretch()
        self.cancel_button = _editor_action("Cancel journal edits", "close")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = _editor_action("Save entry", "save")
        self.save_button.clicked.connect(self.save_changes)
        actions.addWidget(self.save_button)
        self.actions_widget.setVisible(not embedded)
        layout.addWidget(self.actions_widget)
        for line in self.entry.get("lines", []):
            self._append_row(line)
        while len(self.rows) < 2:
            self._append_row({})
        self.date.textChanged.connect(self._changed)
        self._building = False
        self._resize_postings()
        self._changed()
        self._baseline = self._values()
        if not embedded:
            self.setMinimumHeight(max(570, self.minimumSizeHint().height()))

    def has_unsaved_changes(self) -> bool:
        """
        Compare editable form values with the original displayed event.
        """
        return self._baseline is not None and self._values() != self._baseline

    def has_posting_changes(self) -> bool:
        """
        Detect edited rows separately from the date retained when bank evidence is replaced.
        """
        return self._baseline is not None and self._values()[2] != self._baseline[2]

    def set_busy(self, busy: bool) -> None:
        """
        Keep edits and cancellation suspended while an atomic save or reload is running.
        """
        self._busy = busy
        self.table.setEnabled(not busy)
        self.investment_options.setEnabled(not busy)
        self.date.setEnabled(not busy and not self.protected_trade)
        self.add_button.setEnabled(not busy and not self.protected_trade)
        self.sources_button.setEnabled(not busy and not self.context.get("journal_entry_id"))
        self.disposal_button.setEnabled(not busy)
        if self._disposal_dialog is not None:
            self._disposal_dialog.set_busy(busy)
        self.cancel_button.setEnabled(not busy)
        self.reload_button.setEnabled(not busy)
        self._changed()

    def show_error(self, message: str) -> None:
        """
        Retain the user's edits after a failed or stale save and offer an explicit reload.
        """
        self.error.setText(message)
        if self._disposal_dialog is not None:
            self._disposal_dialog.show_error(message)
        self.reload_button.setVisible(True)

    def mark_saved(self) -> None:
        """
        Close only after the controller confirms the entire entry was saved.
        """
        self._saved = True
        if self._disposal_dialog is not None:
            self._disposal_dialog.accept()
        self.accept()

    def reject(self) -> None:
        """
        Protect incomplete changes when closing the editor or pressing Escape.
        """
        if self._busy:
            return
        if not self._saved and self.has_unsaved_changes() and not confirm_discard(self):
            return
        super().reject()

    def save_changes(self) -> None:
        """
        Submit a validated complete event with the exact revision that the user edited.
        """
        if self._busy:
            return
        try:
            payload = self.payload()
        except (ValueError, ValidationError) as error:
            self._show_validation(error)
            return
        self.error.clear()
        self.action_requested.emit("apply_journal_edit", payload)

    def payload(self) -> dict:
        """
        Validate exact amounts and return the whole event, preserving every hidden source and trade field.
        """
        event = self._draft_event()
        validated = JournalEntry.model_validate(event)
        return {
            "journal_entry_id": self.context.get("journal_entry_id"),
            "revision": self.context.get("revision", ""),
            "manual_entry_id": self.context.get("manual_entry_id"),
            "entry": validated.model_dump(mode="json"),
            "opening": self._opening,
            "replace_unposted_sources": bool(self.context.get("replace_unposted_sources")),
            "manual_context_revision": self.context.get("manual_context_revision", ""),
        }

    def _draft_event(self) -> dict:
        """
        Read complete posting facts without inferring classifications or changing any saved values.
        """
        if len(self.rows) < 2:
            raise _EntryInputError("Add another posting.", self.add_button)
        try:
            entered_date = parse_input_date(self.date.text())
        except ValueError:
            raise _EntryInputError("Use YYYY-MM-DD or DD-MM-YYYY.", self.date) from None
        if entered_date is None:
            raise _EntryInputError("Enter a date.", self.date)
        lines = []
        for number, row in enumerate(self.rows, 1):
            account_id = row["account"].currentData()
            if account_id is None or row["account"].currentText() != row["account"].itemText(
                row["account"].currentIndex()
            ):
                raise _EntryInputError("Choose an account.", row["account"])
            if not row["description"].text().strip():
                raise _EntryInputError("Enter a description.", row["description"])
            amounts = []
            for field in ("debit", "credit"):
                try:
                    amounts.append(_money(row[field].text(), number))
                except ValueError:
                    raise _EntryInputError(
                        "Use a positive amount with up to two decimals.", row[field],
                    ) from None
            debit, credit = amounts
            if (debit > 0) == (credit > 0):
                raise _EntryInputError("Enter either a debit or a credit.", row["debit"])
            item = deepcopy(row["original"])
            item.update(
                account_id=account_id, description=row["description"].text(),
                debit=str(debit), credit=str(credit),
            )
            original = row["original"]
            unchanged_bank = bool(self.context.get("journal_entry_id")) and (
                original.get("account_id") == account_id
                and Decimal(str(original.get("debit", 0))) == debit
                and Decimal(str(original.get("credit", 0))) == credit
            )
            if (_is_bank(self.accounts[account_id]) and not item.get("transaction_id")
                    and not self._opening and not unchanged_bank):
                raise _EntryInputError(
                    "Link a bank transaction, or enter its opening balance in Setup > Accounts.",
                    row["account"],
                )
            category = row["budget_value"]
            if category is None:
                item.pop("budget_category", None)
            else:
                item["budget_category"] = category
            treatment = row["treatment_value"]
            if self.protected_trade:
                pass
            elif treatment is not None:
                account = self.accounts[account_id]
                if item.get("transaction_id") or item.get("investment"):
                    raise _EntryInputError("Use a separate non-bank asset line.", row["treatment"])
                if account.get("account_type") != "asset" or _is_bank(account):
                    raise _EntryInputError("Choose a non-bank asset.", row["account"])
                if treatment == "opening" and not row["cost"].text().strip():
                    raise _EntryInputError("Enter the opening cost.", row["cost"])
                try:
                    cost = _money(row["cost"].text(), number) if treatment == "opening" else None
                except ValueError:
                    raise _EntryInputError("Use a cost with up to two decimals.", row["cost"]) from None
                item["account_investment"] = {
                    "kind": treatment, "cost_basis": str(cost) if cost is not None else None,
                }
            elif "account_investment" in item:
                item["account_investment"] = None
            lines.append(item)
        if sum(Decimal(line["debit"]) - Decimal(line["credit"]) for line in lines) != 0:
            target = next((
                row["credit"] for row in reversed(self.rows) if row["credit"].isEnabled()
            ), self.date)
            raise _EntryInputError("Debits and credits must balance.", target)
        return {"id": self.entry["id"], "date": entered_date.isoformat(), "lines": lines}

    def _append_row(self, line: dict) -> None:
        """
        Add controls for one posting and disable edits to imported facts and protected trade economics.
        """
        original = deepcopy(line)
        row: dict = {"original": original}
        account = _posting_combo(f"Account for posting {len(self.rows) + 1}", account_choices=True)
        account.addItem("Choose account", None)
        for item in self.accounts.values():
            label = f"{item['name']} · {item.get('account_type', '')}"
            account.addItem(label, str(item["id"]))
        account.setCurrentIndex(max(0, account.findData(str(line.get("account_id", "")))))
        row["account"] = account
        row["description"] = QLineEdit(str(line.get("description", "")))
        row["debit"] = QLineEdit(str(line.get("debit", "0")))
        row["credit"] = QLineEdit(str(line.get("credit", "0")))
        row["budget_value"] = line.get("budget_category")
        row["treatment"] = _posting_combo(f"Investment treatment for posting {len(self.rows) + 1}")
        for label, value in (
            ("Unclassified", "unclassified"), ("Capital in / out", "contribution"),
            ("Return or fee", "earnings"), ("Opening balance", "opening"),
        ):
            row["treatment"].addItem(label, value)
        classification = line.get("account_investment") or {}
        row["treatment_value"] = classification.get("kind")
        row["treatment"].setCurrentIndex(
            max(0, row["treatment"].findData(classification.get("kind") or "unclassified"))
        )
        cost = classification.get("cost_basis")
        row["cost"] = QLineEdit(str(cost) if cost is not None else "")
        for field in ("debit", "credit", "cost"):
            row[field].setProperty("monetary", True)
        row["cost"].setPlaceholderText("Historical cost in AUD")
        row["remove"] = _posting_action("Remove this posting", "remove")
        row["remove_cell"] = QWidget()
        row["remove_cell"].setObjectName("journalPostingActionCell")
        row["remove_cell"].setStyleSheet("QWidget#journalPostingActionCell { background: transparent; }")
        remove_layout = QHBoxLayout(row["remove_cell"])
        remove_layout.setContentsMargins(9, 0, 9, 0)
        remove_layout.setSpacing(0)
        remove_layout.addWidget(row["remove"], 0, Qt.AlignmentFlag.AlignCenter)
        row["investment_group"] = QWidget()
        row["investment_group"].setObjectName("journalInvestmentOptions")
        investment_layout = QVBoxLayout(row["investment_group"])
        investment_layout.setContentsMargins(0, 0, 0, 0)
        investment_layout.setSpacing(4)
        investment_controls = QHBoxLayout()
        row["investment_label"] = QLabel()
        investment_controls.addWidget(row["investment_label"])
        row["treatment"].setFixedWidth(260)
        investment_controls.addWidget(row["treatment"])
        row["cost_label"] = QLabel("Opening cost AUD")
        investment_controls.addWidget(row["cost_label"])
        row["cost"].setMaximumWidth(180)
        investment_controls.addWidget(row["cost"])
        investment_controls.addStretch()
        investment_layout.addLayout(investment_controls)
        self.investment_layout.addWidget(row["investment_group"])
        source = bool(line.get("transaction_id"))
        for field in ("account", "debit", "credit"):
            row[field].setEnabled(not source and not self.protected_trade)
        row["description"].setReadOnly(source)
        row["remove"].setEnabled(not source and not self.protected_trade)
        if source:
            tooltip = (
                f"Imported transaction {line['transaction_id']}. "
                f"Effective date: {line.get('effective_date') or 'not supplied'}. Bank facts are protected."
            )
            for field in ("account", "description", "debit", "credit"):
                row[field].setToolTip(tooltip)
        if line.get("investment"):
            row["description"].setToolTip("Security units and their cost remain unchanged by this edit.")
        index = self.table.rowCount()
        self.table.insertRow(index)
        for column, field in enumerate(
            ("account", "description", "debit", "credit", "remove_cell")
        ):
            self.table.setCellWidget(index, column, row[field])
        row["remove_cell"].setAutoFillBackground(False)
        self.table.setRowHeight(index, 44)
        self.rows.append(row)
        self._resize_postings()
        row["remove"].clicked.connect(lambda: self._remove_row(row))
        for field in ("description", "debit", "credit", "cost"):
            row[field].textChanged.connect(self._changed)
        account.currentIndexChanged.connect(self._changed)
        row["treatment"].currentIndexChanged.connect(lambda: self._treatment_changed(row))

    def _add_line(self) -> None:
        """
        Append a new ordinary posting without manufacturing statement evidence or security units.
        """
        self._building = True
        self._append_row({})
        self._building = False
        self._changed()

    def _remove_row(self, row: dict) -> None:
        """
        Remove one editable posting while retaining the order and identity of all remaining lines.
        """
        if row not in self.rows or row["original"].get("transaction_id") or self.protected_trade:
            return
        index = self.rows.index(row)
        self.rows.pop(index)
        self.table.removeRow(index)
        self._resize_postings()
        row["investment_group"].deleteLater()
        self._changed()

    def _select_row(self, row: dict | None) -> None:
        """
        Focus the requested investment classification without introducing another details panel.
        """
        if row is not None:
            control = row["treatment"] if not row["investment_group"].isHidden() else row["account"]
            control.setFocus(Qt.FocusReason.OtherFocusReason)

    def _resize_postings(self) -> None:
        """
        Give every posting its full row height instead of scrolling inside the short journal table.
        """
        height = self.table.horizontalHeader().sizeHint().height() + 2 * self.table.frameWidth()
        height += sum(self.table.rowHeight(index) for index in range(self.table.rowCount()))
        height += self.table.horizontalScrollBar().sizeHint().height()
        self.table.setFixedHeight(height)
        if not self.embedded and not self._building:
            self.setMinimumHeight(max(570, self.minimumSizeHint().height()))

    def _treatment_changed(self, row: dict) -> None:
        """
        Record an explicit investment treatment while preserving absent metadata until deliberately changed.
        """
        if not self._building:
            row["treatment_value"] = row["treatment"].currentData()
            self._changed()

    def _values(self) -> tuple:
        """
        Capture form state independently of whether the current amounts already balance.
        """
        values = []
        for row in self.rows:
            values.append((
                row["account"].currentData(), row["account"].currentText(), row["description"].text(),
                row["debit"].text(), row["credit"].text(), row["budget_value"], row["treatment_value"],
                row["cost"].text(),
            ))
        return self.date.text(), self._opening, tuple(values)

    def _changed(self, *_args) -> None:
        """
        Keep contextual controls current while leaving incomplete drafts quiet until a save attempt.
        """
        if self._building:
            return
        self._clear_validation()
        investment_visible = False
        for number, row in enumerate(self.rows, 1):
            account = self.accounts.get(row["account"].currentData(), {})
            relevant = (
                row["account"].currentData() in self.context.get("account_investments", [])
                or row["treatment_value"] is not None
            )
            row["investment_group"].setVisible(relevant)
            investment_visible = investment_visible or relevant
            row["investment_label"].setText(f"Investment cost · {account.get('name', f'Line {number}')}")
            eligible = (
                relevant and account.get("account_type") == "asset" and not _is_bank(account)
                and not row["original"].get("transaction_id")
                and not row["original"].get("investment") and not self.protected_trade and not self._busy
            )
            row["treatment"].setEnabled(eligible)
            opening = row["treatment_value"] == "opening"
            row["cost"].setVisible(opening)
            row["cost_label"].setVisible(opening)
            row["cost"].setEnabled(eligible and opening)
            effect = {
                "contribution": "Debits add invested capital; credits release recorded cost.",
                "earnings": "Changes value without changing contributed cost.",
                "opening": "Use the historical cost, which may differ from the opening value.",
            }.get(row["treatment_value"], "Choose how this movement affects the investment's cost.")
            row["treatment"].setToolTip(effect)
        self.investment_options.setVisible(investment_visible)
        meaningful = bool(self.context.get("journal_entry_id")) or any(
            row["account"].currentData() is not None or row["description"].text().strip()
            or row["debit"].text().strip() not in {"", "0", "0.00"}
            or row["credit"].text().strip() not in {"", "0", "0.00"}
            for row in self.rows
        )
        self.save_button.setEnabled(not self._busy and meaningful)
        self._resize_postings()
        self.changed.emit()

    def _clear_validation(self) -> None:
        """
        Restore a field's normal help once the user resumes editing after a failed save attempt.
        """
        if self._validation_target is not None:
            control, tooltip, description = self._validation_target
            control.setToolTip(tooltip)
            control.setAccessibleDescription(description)
            self._validation_target = None
            QToolTip.hideText()
        self.validation.clear()
        self.validation.hide()

    def _show_validation(self, error: Exception) -> None:
        """
        Focus the first field needing attention and expose concise guidance without model paths.
        """
        self._clear_validation()
        control = getattr(error, "control", None)
        if control is None and isinstance(error, ValidationError):
            location = error.errors(include_input=False, include_url=False)[0]["loc"]
            if len(location) > 2 and location[0] == "lines" and isinstance(location[1], int):
                row = self.rows[location[1]]
                control = row.get(str(location[2]), row["description"])
        if not isinstance(control, QWidget) or not control.isEnabled():
            control = self.date
        message = _validation_message(error)
        self._validation_target = (control, control.toolTip(), control.accessibleDescription())
        control.setToolTip(message)
        control.setAccessibleDescription(message)
        control.setFocus(Qt.FocusReason.OtherFocusReason)
        QToolTip.showText(control.mapToGlobal(QPoint(0, control.height())), message, control)

    def _reload(self) -> None:
        """
        Request fresh accounting only after the user explicitly discards the unsaved editor state.
        """
        if self.has_unsaved_changes() and not confirm_discard(self):
            return
        self.action_requested.emit("reload_entry_editor", {
            "journal_entry_id": self.context.get("journal_entry_id"),
            "manual_entry_id": self.context.get("manual_entry_id"),
            "source_ids": self.context.get("source_ids", []),
            "line_number": self.context.get("focus_line_number"),
            "reload_saved": True,
        })

    def _open_disposal(self) -> None:
        """
        Preview an explicit disposal separately while preserving every current journal draft field.
        """
        if self._busy:
            return
        if self._disposal_dialog is not None:
            self._disposal_dialog.raise_()
            return
        try:
            when = parse_input_date(self.date.text())
            if when is None:
                raise ValueError("Enter a date.")
        except ValueError as error:
            self._show_validation(_EntryInputError(str(error), self.date))
            return
        seed = {**self.context["investment_disposals"], "date": when.isoformat(), "entry_id": self.entry["id"]}
        dialog = InvestmentDisposalDialog(seed, self)
        self._disposal_dialog = dialog
        dialog.post_requested.connect(self._submit_disposal)
        dialog.finished.connect(self._disposal_closed)
        dialog.open()

    def _submit_disposal(self, result: dict) -> None:
        """
        Request one atomic disposal save without replacing or clearing the underlying draft beforehand.
        """
        self.action_requested.emit("apply_journal_edit", {
            "journal_entry_id": self.context.get("journal_entry_id"),
            "revision": self.context.get("revision", ""),
            "manual_entry_id": self.context.get("manual_entry_id"), "opening": False, **result,
        })

    def _disposal_closed(self, _result: int) -> None:
        """
        Release only the temporary disposal form when it is cancelled or its save is acknowledged.
        """
        if self._disposal_dialog is not None:
            self._disposal_dialog.deleteLater()
            self._disposal_dialog = None

    def _choose_sources(self) -> None:
        """
        Rebuild a new draft from explicitly selected immutable statement facts before entering counterparts.
        """
        dialog = SourcePicker(
            self.context.get("transactions", []), self.context.get("source_ids", []), self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if self.has_posting_changes() and not confirm_discard(self):
            return
        self.action_requested.emit("reload_entry_editor", {
            "manual_entry_id": self.context.get("manual_entry_id"),
            "source_ids": dialog.selected_sources(), "date": self.date.text(),
        })


# %% 2. Helpers ----------------------------------------------------------------------------------------------
class _EntryInputError(ValueError):
    """
    Attach a recoverable input problem to the control that can resolve it.
    """

    def __init__(self, message: str, control: QWidget):
        """
        Retain a concise user-facing message and its editable field.
        """
        super().__init__(message)
        self.control = control


class _PostingActionDelegate(QStyledItemDelegate):
    """
    Give row-action wrappers the full cell bounds without the text cells' style padding.
    """

    def updateEditorGeometry(self, editor: QWidget, option, index) -> None:
        """
        Let the wrapper's nine-pixel inset centre its button in the complete action column.
        """
        editor.setGeometry(option.rect)


class _PostingHeader(QHeaderView):
    """
    Align the add action with the remove actions in the table's final column.
    """

    def __init__(self, action: QToolButton, parent=None):
        """
        Keep the ordinary header while centring its keyboard-accessible add button over the action column.
        """
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.action = action
        action.setParent(self.viewport())
        self.setMinimumHeight(36)
        self.sectionResized.connect(self.position_action)
        self.sectionMoved.connect(self.position_action)
        self.geometriesChanged.connect(self.position_action)

    def position_action(self, *_args) -> None:
        """
        Follow native section geometry when columns, the viewport or horizontal scrolling change.
        """
        if not self.count():
            return
        column = self.count() - 1
        self.action.move(
            self.sectionViewportPosition(column) + (self.sectionSize(column) - self.action.width()) // 2,
            max(0, (self.viewport().height() - self.action.height()) // 2),
        )
        self.action.setVisible(not self.isSectionHidden(column))

    def resizeEvent(self, event) -> None:
        """
        Retain header layout before repositioning the action inside its newly sized viewport.
        """
        super().resizeEvent(event)
        self.position_action()

    def showEvent(self, event) -> None:
        """
        Position the action after the native style has established the visible header height.
        """
        super().showEvent(event)
        self.position_action()


class _AccountComboBox(QComboBox):
    """
    Choose an existing account through one click anywhere in the field or native keyboard navigation.
    """

    def __init__(self):
        """
        Keep the account label selection-only, without an editable caret or free-text account names.
        """
        super().__init__()
        self._field_press = False
        self.setEditable(False)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)

    def mousePressEvent(self, event) -> None:
        """
        Defer a field click until release so the opening release cannot dismiss the choices.
        """
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            self._field_press = True
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """
        Open the selector after the pointer has been released.
        """
        if event.button() == Qt.MouseButton.LeftButton and self._field_press:
            self._field_press = False
            QTimer.singleShot(0, self.showPopup)
            event.accept()
            return
        super().mouseReleaseEvent(event)


def _set_choices(combo: QComboBox, choices: list[tuple], value) -> None:
    """
    Refresh contextual choices without turning a displayed value into a user edit.
    """
    blocked = combo.blockSignals(True)
    combo.clear()
    for label, key in choices:
        combo.addItem(label, key)
    combo.setCurrentIndex(max(0, combo.findData(value)))
    combo.blockSignals(blocked)


def _posting_action(label: str, symbol: str) -> QToolButton:
    """
    Give posting actions compact icons with accessible names and normal keyboard focus.
    """
    button = QToolButton()
    button.setObjectName("journalPostingAction")
    button.setAutoRaise(True)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setFixedSize(30, 30)
    button.setIconSize(QSize(18, 18))
    button.setIcon(icon(symbol, size=18))
    button.setStyleSheet("QToolButton#journalPostingAction { padding: 0; }")
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setToolTip(label)
    button.setAccessibleName(label)
    return button


def _editor_action(label: str, symbol: str) -> QToolButton:
    """
    Match the manual composer's compact footer actions while retaining explicit accessible names.
    """
    button = QToolButton()
    button.setObjectName("viewerIconAction")
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setIcon(icon(symbol, size=20))
    button.setIconSize(QSize(20, 20))
    button.setFixedSize(36, 36)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setToolTip(label)
    button.setAccessibleName(label)
    return button


def _posting_combo(name: str, *, account_choices: bool = False) -> QComboBox:
    """
    Give posting choices a visible arrow and a Qt list popup that works inside the macOS table.
    """
    combo = _AccountComboBox() if account_choices else QComboBox()
    combo.setView(QListView(combo))
    combo.setAccessibleName(name)
    arrow = (Path(__file__).parent / "assets" / "chevron_down.svg").as_posix()
    combo.setStyleSheet(
        'QComboBox { padding-right: 28px; } '
        f'QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 6px; }} '
    )
    return combo


def _money(value: str, number: int) -> Decimal:
    """
    Parse exact AUD text and reject nonfinite, negative or fractional-cent amounts.
    """
    try:
        amount = Decimal(value.strip() or "0")
        if not amount.is_finite() or amount < 0:
            raise ValueError
        numerator, denominator = amount.as_integer_ratio()
        if numerator * 100 % denominator:
            raise ValueError
        return amount
    except (InvalidOperation, ValueError):
        message = f"Posting {number}: use a nonnegative dollar amount with at most two decimals."
        raise ValueError(message) from None


def _is_bank(account: dict) -> bool:
    """
    Recognise bank accounts from the compact editor catalogue without requiring routing details.
    """
    return bool(account.get("is_bank") or account.get("bank_details"))


def _validation_message(error: Exception) -> str:
    """
    Show concise validation guidance without exposing a raw Pydantic input dump.
    """
    if isinstance(error, ValidationError):
        first = error.errors(include_input=False, include_url=False)[0]
        return first["msg"].removeprefix("Value error, ")
    return str(error)
