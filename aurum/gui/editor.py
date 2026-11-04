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
import re
from uuid import uuid4

from pydantic import ValidationError
from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QMessageBox,
    QPushButton, QSizePolicy, QStackedWidget, QTableWidget, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.dates import parse_input_date
from aurum.finance.journal import JournalEntry
from aurum.gui.icons import icon
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
        self.setWindowTitle("Edit the Journal Entry")
        self.setModal(not embedded)
        if not embedded:
            self.resize(1320, 800)
            self.setMinimumSize(1040, 570)
        self.context = deepcopy(context)
        self.entry = deepcopy(context.get("entry") or {})
        self.entry.setdefault("id", str(uuid4()))
        self.accounts = {str(row["id"]): row for row in context.get("accounts", [])}
        self._within_transfer_sources = _within_transfer_sources(self.context, self.accounts)
        self.rows = []
        self._selected_row = None
        self._building = True
        self._busy = False
        self._saved = False
        self._baseline = None
        self.protected_trade = bool(context.get("protected_trade"))
        layout = QVBoxLayout(self)
        if embedded:
            layout.setContentsMargins(0, 0, 0, 0)
        self.heading = QLabel("Edit the Journal Entry")
        self.heading.setObjectName("pageTitle")
        self.heading.setVisible(not embedded)
        layout.addWidget(self.heading)
        self.subtitle = QLabel(
            "Saving applies your balanced entry immediately. No AI request is made. "
            "Linked bank facts stay unchanged."
        )
        self.subtitle.setWordWrap(True)
        self.subtitle.setVisible(not embedded)
        layout.addWidget(self.subtitle)
        if self.protected_trade:
            trade = QLabel(
                "This event contains security units. Its date, accounts and amounts are protected; "
                "you can edit non-bank descriptions and budget treatment."
            )
            trade.setWordWrap(True)
            trade.setObjectName("muted")
            layout.addWidget(trade)
        top = QHBoxLayout()
        top.addWidget(QLabel("Accounting date"))
        self.date = QLineEdit(str(self.entry.get("date") or ""))
        self.date.setPlaceholderText("YYYY-MM-DD or DD-MM-YYYY")
        self.date.setMaximumWidth(190)
        self.date.setEnabled(not self.protected_trade)
        top.addWidget(self.date)
        self.opening = QCheckBox("Starting bank balance")
        self.opening.setChecked(bool(context.get("opening")))
        self.opening.setEnabled(not self.protected_trade)
        self.opening.setToolTip(
            "Confirm this entry records a starting bank balance. "
            "Ordinary bank activity must be linked to imported transactions."
        )
        top.addWidget(self.opening)
        top.addStretch()
        self.sources_button = QPushButton("Link statement transactions…")
        self.sources_button.clicked.connect(self._choose_sources)
        self.sources_button.setVisible(not context.get("journal_entry_id"))
        top.addWidget(self.sources_button)
        self.add_button = _posting_action("Add posting", "add")
        self.add_button.clicked.connect(self._add_line)
        self.add_button.setEnabled(not self.protected_trade)
        layout.addLayout(top)
        budget_scope = QHBoxLayout()
        budget_scope.addWidget(QLabel("Budget"))
        self.entry_budget = _posting_combo("Budget treatment for the whole entry")
        for label, value in (
            ("Normal rules", "default"), ("Exclude whole entry", "exclude"),
            ("Custom by line", "custom"),
        ):
            self.entry_budget.addItem(label, value)
        self.entry_budget.model().item(2).setEnabled(False)
        self.entry_budget.setMinimumWidth(190)
        self.entry_budget.setToolTip(
            "Normal rules restores account defaults on every posting. Income and expenses count; "
            "assets, liabilities and equity need an explicit category. Exclude whole entry excludes "
            "every posting. Custom by line indicates different or specific saved line choices."
        )
        self.entry_budget.currentIndexChanged.connect(self._entry_budget_changed)
        budget_scope.addWidget(self.entry_budget)
        self.entry_budget_hint = QLabel()
        self.entry_budget_hint.setObjectName("journalPostingHint")
        self.entry_budget_hint.setWordWrap(True)
        self.entry_budget_hint.hide()
        budget_scope.addStretch()
        budget_scope.addWidget(self.add_button)
        layout.addLayout(budget_scope)
        self.table = QTableWidget(0, 6)
        self.table.setObjectName("journalPostingTable")
        self.table.setShowGrid(False)
        self.table.setStyleSheet(
            "QTableWidget#journalPostingTable QHeaderView::section { border: none; }"
        )
        self.table.setHorizontalHeaderLabels([
            "Account", "Description", "Debit", "Credit", "", "",
        ])
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeaderItem(4).setToolTip("Budget and investment options for this posting")
        for column, width in enumerate((235, 235, 110, 110, 40, 40)):
            self.table.setColumnWidth(column, width)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.table)
        self.details_panel = QWidget()
        self.details_panel.setObjectName("journalPostingDetails")
        self.details_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        details_layout = QVBoxLayout(self.details_panel)
        details_layout.setContentsMargins(12, 10, 12, 10)
        details_layout.setSpacing(6)
        self.details_heading = QLabel()
        self.details_heading.setObjectName("journalPostingHeading")
        self.details_heading.setWordWrap(True)
        details_layout.addWidget(self.details_heading)
        self.details_stack = QStackedWidget()
        details_layout.addWidget(self.details_stack)
        layout.addWidget(self.details_panel)
        self.validation = QLabel("")
        self.validation.setWordWrap(True)
        self.validation.setObjectName("editorError")
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
        self.reload_button = QPushButton("Reload saved entry")
        self.reload_button.clicked.connect(self._reload)
        self.reload_button.hide()
        actions.addWidget(self.reload_button)
        actions.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.save_button = QPushButton("Save journal entry")
        self.save_button.setProperty("primary", True)
        self.save_button.clicked.connect(self.save_changes)
        actions.addWidget(self.save_button)
        self.actions_widget.setVisible(not embedded)
        layout.addWidget(self.actions_widget)
        for line in self.entry.get("lines", []):
            self._append_row(line)
        while len(self.rows) < 2:
            self._append_row({})
        self.date.textChanged.connect(self._changed)
        self.opening.toggled.connect(self._changed)
        self._building = False
        self._resize_postings()
        self._select_row(self.rows[0])
        self._changed()
        self._baseline = self._values()
        if not embedded:
            self.setMinimumHeight(max(570, self.minimumSizeHint().height()))

    def has_unsaved_changes(self) -> bool:
        """
        Compare editable form values with the original displayed event.
        """
        return self._baseline is not None and self._values() != self._baseline

    def set_busy(self, busy: bool) -> None:
        """
        Keep edits and cancellation suspended while an atomic save or reload is running.
        """
        self._busy = busy
        self.table.setEnabled(not busy)
        self.details_panel.setEnabled(not busy)
        self.entry_budget.setEnabled(not busy)
        self.date.setEnabled(not busy and not self.protected_trade)
        self.opening.setEnabled(not busy and not self.protected_trade)
        self.add_button.setEnabled(not busy and not self.protected_trade)
        self.sources_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.reload_button.setEnabled(not busy)
        self._changed()

    def show_error(self, message: str) -> None:
        """
        Retain the user's edits after a failed or stale save and offer an explicit reload.
        """
        self.error.setText(message)
        self.reload_button.setVisible(True)

    def mark_saved(self) -> None:
        """
        Close only after the controller confirms the entire entry was saved.
        """
        self._saved = True
        self.accept()

    def reject(self) -> None:
        """
        Protect incomplete changes when closing the editor or pressing Escape.
        """
        if self._busy:
            return
        if not self._saved and self.has_unsaved_changes():
            choice = QMessageBox.question(
                self, "Discard journal edits?",
                "Discard these unsaved edits? Posted accounting stays unchanged.",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Discard:
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
            self.validation.setText(_validation_message(error))
            return
        self.error.clear()
        self.action_requested.emit("apply_journal_edit", payload)

    def payload(self) -> dict:
        """
        Validate exact amounts and return the whole event, preserving every hidden source and trade field.
        """
        entered_date = parse_input_date(self.date.text())
        if entered_date is None:
            raise ValueError("Enter an accounting date.")
        lines = []
        for number, row in enumerate(self.rows, 1):
            account_id = row["account"].currentData()
            if account_id is None or row["account"].currentText() != row["account"].itemText(
                row["account"].currentIndex()
            ):
                raise ValueError(f"Posting {number}: choose an existing account.")
            debit = _money(row["debit"].text(), number)
            credit = _money(row["credit"].text(), number)
            item = deepcopy(row["original"])
            item.update(
                account_id=account_id, description=row["description"].text(),
                debit=str(debit), credit=str(credit),
            )
            category = row["budget_value"]
            if category is None:
                item.pop("budget_category", None)
            else:
                item["budget_category"] = category
            self._validate_budget_choice(row, number, account_id)
            treatment = row["treatment_value"]
            if self.protected_trade:
                pass
            elif treatment is not None:
                account = self.accounts[account_id]
                if item.get("transaction_id") or item.get("investment"):
                    raise ValueError(f"Posting {number}: classify only a separate non-bank asset line.")
                if account.get("account_type") != "asset" or _is_bank(account):
                    raise ValueError(f"Posting {number}: choose a non-bank asset for investment treatment.")
                cost = _money(row["cost"].text(), number) if treatment == "opening" else None
                if treatment == "opening" and not row["cost"].text().strip():
                    raise ValueError(f"Posting {number}: enter the opening cost basis.")
                item["account_investment"] = {
                    "kind": treatment, "cost_basis": str(cost) if cost is not None else None,
                }
            elif "account_investment" in item:
                item["account_investment"] = None
            lines.append(item)
        event = {"id": self.entry["id"], "date": entered_date.isoformat(), "lines": lines}
        validated = JournalEntry.model_validate(event)
        return {
            "journal_entry_id": self.context.get("journal_entry_id"),
            "revision": self.context.get("revision", ""),
            "manual_entry_id": self.context.get("manual_entry_id"),
            "entry": validated.model_dump(mode="json"),
            "opening": self.opening.isChecked(),
        }

    def _append_row(self, line: dict) -> None:
        """
        Add controls for one posting and disable edits to imported facts and protected trade economics.
        """
        original = deepcopy(line)
        row = {"original": original}
        account = _posting_combo(f"Account for posting {len(self.rows) + 1}", searchable=True)
        account.addItem("Choose account", None)
        for item in self.accounts.values():
            label = f"{item['name']} · {item.get('account_type', '')}"
            account.addItem(label, str(item["id"]))
        account.setCurrentIndex(max(0, account.findData(str(line.get("account_id", "")))))
        account.completer().setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        account.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        row["account"] = account
        row["description"] = QLineEdit(str(line.get("description", "")))
        row["debit"] = QLineEdit(str(line.get("debit", "0")))
        row["credit"] = QLineEdit(str(line.get("credit", "0")))
        row["budget"] = _posting_combo(f"Budget treatment for posting {len(self.rows) + 1}")
        row["budget_value"] = line.get("budget_category")
        self._refresh_budget_choices(row)
        row["treatment"] = _posting_combo(f"Investment treatment for posting {len(self.rows) + 1}")
        for label, value in (
            ("Unclassified", "unclassified"), ("Contribution", "contribution"),
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
        row["cost"].setPlaceholderText("Historical cost in AUD")
        row["details"] = _posting_action("Budget and investment options for this posting", "details")
        row["details"].setCheckable(True)
        row["remove"] = _posting_action("Remove this posting", "remove")
        page = QWidget()
        page.setObjectName("journalPostingOptions")
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(6)
        budget_layout = QHBoxLayout()
        budget_label = QLabel("Budget")
        budget_label.setFixedWidth(100)
        budget_layout.addWidget(budget_label)
        row["budget"].setFixedWidth(260)
        budget_layout.addWidget(row["budget"])
        row["budget_hint"] = QLabel()
        row["budget_hint"].setObjectName("journalPostingHint")
        row["budget_hint"].setWordWrap(True)
        row["budget_hint"].hide()
        budget_layout.addStretch()
        page_layout.addLayout(budget_layout)
        row["investment_group"] = QWidget()
        row["investment_group"].setObjectName("journalInvestmentOptions")
        investment_layout = QVBoxLayout(row["investment_group"])
        investment_layout.setContentsMargins(0, 0, 0, 0)
        investment_layout.setSpacing(4)
        investment_controls = QHBoxLayout()
        investment_label = QLabel("Investment")
        investment_label.setFixedWidth(100)
        investment_controls.addWidget(investment_label)
        row["treatment"].setFixedWidth(260)
        investment_controls.addWidget(row["treatment"])
        row["cost_label"] = QLabel("Opening cost AUD")
        investment_controls.addWidget(row["cost_label"])
        row["cost"].setMaximumWidth(180)
        investment_controls.addWidget(row["cost"])
        investment_controls.addStretch()
        investment_layout.addLayout(investment_controls)
        row["investment_hint"] = QLabel()
        row["investment_hint"].setObjectName("journalPostingHint")
        row["investment_hint"].setWordWrap(True)
        row["investment_hint"].hide()
        page_layout.addWidget(row["investment_group"])
        row["details_page"] = page
        self.details_stack.addWidget(page)
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
            ("account", "description", "debit", "credit", "details", "remove")
        ):
            self.table.setCellWidget(index, column, row[field])
        self.table.setRowHeight(index, 44)
        self.rows.append(row)
        self._resize_postings()
        row["details"].clicked.connect(lambda: self._select_row(row))
        row["remove"].clicked.connect(lambda: self._remove_row(row))
        for field in ("description", "debit", "credit", "cost"):
            row[field].textChanged.connect(self._changed)
        account.currentIndexChanged.connect(self._changed)
        row["budget"].currentIndexChanged.connect(lambda: self._budget_changed(row))
        row["treatment"].currentIndexChanged.connect(lambda: self._treatment_changed(row))
        account.editTextChanged.connect(self._changed)

    def _add_line(self) -> None:
        """
        Append a new ordinary posting without manufacturing statement evidence or security units.
        """
        self._building = True
        line = {"budget_category": "exclude"} if self.entry_budget.currentData() == "exclude" else {}
        self._append_row(line)
        self._building = False
        self._select_row(self.rows[-1])
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
        self.details_stack.removeWidget(row["details_page"])
        row["details_page"].deleteLater()
        if self._selected_row is row:
            self._select_row(self.rows[min(index, len(self.rows) - 1)] if self.rows else None)
        self._changed()

    def _select_row(self, row: dict | None) -> None:
        """
        Show one posting's retained options without changing any budget or investment choices.
        """
        self._selected_row = row
        self.details_panel.setVisible(row is not None)
        for candidate in self.rows:
            candidate["details"].setChecked(candidate is row)
        if row is not None:
            self.details_stack.setCurrentWidget(row["details_page"])
            self._update_details_heading()
            self._resize_details()

    def _resize_details(self) -> None:
        """
        Fit the selected posting's controls without reserving space for hidden investment options.
        """
        page = self.details_stack.currentWidget()
        if page is None:
            return
        page.layout().invalidate()
        page.layout().activate()
        self.details_stack.setFixedHeight(page.sizeHint().height())
        self.details_panel.layout().invalidate()
        self.details_panel.updateGeometry()

    def _update_details_heading(self) -> None:
        """
        Identify the selected posting by its current row and named account.
        """
        if self._selected_row is None:
            return
        number = self.rows.index(self._selected_row) + 1
        account = self.accounts.get(self._selected_row["account"].currentData(), {})
        name = account.get("name", "Choose an account")
        self.details_heading.setText(f"Line {number} · {name}")

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

    def _entry_budget_changed(self) -> None:
        """
        Apply an explicit whole-entry choice to every line while leaving the custom indicator read-only.
        """
        if self._building or self._busy:
            return
        choice = self.entry_budget.currentData()
        if choice in {"default", "exclude"}:
            self._building = True
            for row in self.rows:
                row["budget_value"] = choice
                row["budget"].setCurrentIndex(row["budget"].findData(choice))
            self._building = False
        self._changed()

    def _budget_changed(self, row: dict) -> None:
        """
        Record a deliberate line override independently of its default display label.
        """
        if self._building:
            return
        row["budget_value"] = row["budget"].currentData()
        self._changed()

    def _treatment_changed(self, row: dict) -> None:
        """
        Record an explicit treatment while preserving absent metadata until a choice actually changes.
        """
        if self._building:
            return
        row["treatment_value"] = row["treatment"].currentData()
        self._changed()

    def _refresh_budget_choices(self, row: dict) -> None:
        """
        Offer compatible categories and retain any existing unavailable choice for explicit correction.
        """
        account = self.accounts.get(row["account"].currentData(), {})
        categories = self.context.get("budget_categories", [])
        label = "Not included (default)"
        if not account:
            label = "Account default"
        elif account.get("account_type") in {"income", "expense"}:
            category = next((item for item in categories if item["kind"] == account["account_type"]
                             and account["key"] in item.get("accounts", [])), None)
            name = category["name"] if category else account["name"]
            label = f"{name} ({'default' if category else 'unbudgeted'})"
        choices = [(label, "default"), ("Exclude this posting", "exclude")]
        choices.extend((item["name"], item["key"]) for item in categories if _budget_allowed(item, account))
        selected = row["budget_value"] or "default"
        if selected not in {key for _, key in choices}:
            name = next((item["name"] for item in categories if item["key"] == selected), selected)
            choices.append((f"Unavailable: {name}", selected))
        combo = row["budget"]
        current = [(combo.itemText(index), combo.itemData(index)) for index in range(combo.count())]
        if current != choices or combo.currentData() != selected:
            blocked = combo.blockSignals(True)
            combo.clear()
            for name, key in choices:
                combo.addItem(name, key)
            combo.setCurrentIndex(combo.findData(selected))
            combo.blockSignals(blocked)

    def _validate_budget_choice(self, row: dict, number: int, account_id: str) -> None:
        """
        Explain unavailable explicit labels before submitting the complete entry for shared validation.
        """
        key = row["budget_value"]
        if key is None or key in {"default", "exclude"}:
            return
        category = next((
            item for item in self.context.get("budget_categories", []) if item["key"] == key
        ), None)
        if category is None:
            raise ValueError(
                f"Posting {number}: budget category {key!r} is unavailable; choose a current option."
            )
        account = self.accounts[account_id]
        if not _budget_allowed(category, account):
            raise ValueError(
                f"Posting {number}: {category['name']} cannot label {account['name']}; "
                "choose a permitted option."
            )

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
        return self.date.text(), self.opening.isChecked(), tuple(values)

    def _changed(self, *_args) -> None:
        """
        Update classification controls and inline validation after each deliberate edit.
        """
        if self._building:
            return
        within_transfer = len(self.rows) == 2 and self._within_transfer_sources == {
            row["original"].get("transaction_id") for row in self.rows
        }
        for number, row in enumerate(self.rows, 1):
            account = self.accounts.get(row["account"].currentData(), {})
            self._refresh_budget_choices(row)
            budget_hint = (
                "Only this posting is excluded. Other postings keep their own choices."
                if row["budget_value"] == "exclude" else
                "This account does not affect budget income or expenses by default."
                if row["budget_value"] in {None, "default"}
                and account.get("account_type") not in {"income", "expense"} else
                "Included using the account's category; unbudgeted income and expenses still count."
                if row["budget_value"] in {None, "default"} else
                "Uses this category. Savings categories divide the existing monthly surplus."
            )
            if within_transfer and row["budget_value"] != "exclude":
                outgoing = Decimal(str(row["original"].get("credit") or 0)) > 0
                direction = "Bank withdrawal (ledger credit)" if outgoing else "Bank receipt (ledger debit)"
                role = "source" if outgoing else "destination"
                category = next((
                    item for item in self.context.get("budget_categories", [])
                    if item["key"] == row["budget_value"] and item["kind"] == "savings"
                ), None)
                if row["budget_value"] in {None, "default"}:
                    budget_hint = (
                        f"{direction}. Leave both lines on Normal rules for no report effect, "
                        f"or choose the {role} savings category."
                    )
                elif category is not None:
                    effect = "decreases" if outgoing else "increases"
                    budget_hint = (
                        f"{direction}. With source and destination tagged, this {effect} "
                        f"the {category['name']} savings row's Actual in Budget."
                    )
            row["budget"].setToolTip(budget_hint)
            row["budget_hint"].setText(budget_hint)
            relevant = (
                row["account"].currentData() in self.context.get("account_investments", [])
                or row["treatment_value"] is not None
            )
            row["investment_group"].setVisible(relevant)
            eligible = (
                relevant and account.get("account_type") == "asset" and not _is_bank(account)
                and not row["original"].get("transaction_id")
                and not row["original"].get("investment") and not self.protected_trade
            )
            row["treatment"].setEnabled(eligible)
            opening = row["treatment_value"] == "opening"
            row["cost"].setVisible(opening)
            row["cost_label"].setVisible(opening)
            row["cost"].setEnabled(eligible and opening)
            effect = {
                "contribution": "Adds this asset debit to contributed cost.",
                "earnings": "Changes value without changing contributed cost. Budget choices stay separate.",
                "opening": "Uses the historical cost you supply, which can differ from the opening value.",
            }.get(row["treatment_value"], "Leaves this movement's cost and investment return unknown.")
            if relevant and not eligible:
                effect += " This posting's investment treatment is not editable here."
            row["investment_hint"].setText(effect)
            row["treatment"].setToolTip(effect)
            row["details"].setToolTip(f"Budget and investment options for posting {number}")
            row["details"].setAccessibleName(f"Options for posting {number}")
        values = {row["budget_value"] or "default" for row in self.rows}
        mode = "exclude" if values == {"exclude"} else "default" if values <= {"default"} else "custom"
        blocked = self.entry_budget.blockSignals(True)
        self.entry_budget.setCurrentIndex(self.entry_budget.findData(mode))
        self.entry_budget.blockSignals(blocked)
        entry_hint = {
            "default": "Income and expenses use account defaults; other accounts need a line category.",
            "exclude": "Every posting is excluded from budget totals.",
            "custom": "Individual postings have overrides. Open a line's options to inspect them.",
        }[mode]
        if within_transfer and mode != "exclude":
            entry_hint = (
                "Internal transfer: Normal rules leaves the bank balance and reports unchanged. "
                "Choose source and destination savings categories to show the move in Budget."
                if mode == "default" else
                "Internal transfer: tag the withdrawal's source and the receipt's destination savings category. "
                "The bank balance and total savings stay unchanged."
            )
        self.entry_budget_hint.setText(entry_hint)
        self.entry_budget.setToolTip(entry_hint)
        self.opening.setVisible(any(
            not row["original"].get("transaction_id")
            and _is_bank(self.accounts.get(row["account"].currentData(), {}))
            for row in self.rows
        ))
        self._update_details_heading()
        self._resize_details()
        try:
            self.payload()
        except (ValueError, ValidationError) as error:
            self.validation.setText(_validation_message(error))
            self.save_button.setEnabled(False)
        else:
            self.validation.clear()
            self.save_button.setEnabled(not self._busy)
        self._resize_postings()
        self.changed.emit()

    def _reload(self) -> None:
        """
        Request fresh accounting only after the user explicitly discards the unsaved editor state.
        """
        if self.has_unsaved_changes():
            choice = QMessageBox.question(
                self, "Reload saved entry", "Discard your edits and reload the latest saved accounting?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Discard:
                return
        self.action_requested.emit("reload_entry_editor", {
            "journal_entry_id": self.context.get("journal_entry_id"),
            "manual_entry_id": self.context.get("manual_entry_id"),
            "source_ids": self.context.get("source_ids", []),
            "line_number": self.context.get("focus_line_number"),
        })

    def _choose_sources(self) -> None:
        """
        Rebuild a new draft from explicitly selected immutable statement facts before entering counterparts.
        """
        dialog = SourcePicker(
            self.context.get("transactions", []), self.context.get("source_ids", []), self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if self.has_unsaved_changes():
            choice = QMessageBox.question(
                self, "Replace draft postings",
                "Rebuild the draft with these statement transactions? Your unsaved postings will be lost.",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if choice != QMessageBox.StandardButton.Discard:
                return
        self.action_requested.emit("reload_entry_editor", {
            "manual_entry_id": self.context.get("manual_entry_id"),
            "source_ids": dialog.selected_sources(), "date": self.date.text(),
        })


# %% 2. Helpers ----------------------------------------------------------------------------------------------
class _AccountComboBox(QComboBox):
    """
    Open account choices from the whole editable field while retaining text search and keyboard input.
    """

    def __init__(self):
        """
        Keep the existing editable selector and intercept clicks only in its account text field.
        """
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        """
        Treat a field click like its disclosure button without intercepting typing or completion.
        """
        if (
            watched is self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton and self.isEnabled()
        ):
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self.lineEdit().selectAll()
            self.showPopup()
            return True
        return super().eventFilter(watched, event)


def _within_transfer_sources(context: dict, accounts: dict) -> set[str]:
    """
    Recognise a complete saved source pair for direction hints without changing accounting or labels.

    Actual transfer matching belongs to journal preparation. The editor checks the two linked bank facts
    rather than inferring an internal transfer from a manually typed description alone.
    """
    lines = (context.get("entry") or {}).get("lines", [])
    if len(lines) != 2:
        return set()
    identifiers = {line.get("transaction_id") for line in lines}
    sources = {row.get("transaction_id"): row for row in context.get("transactions", [])}
    if None in identifiers or len(identifiers) != 2 or not identifiers <= sources.keys():
        return set()
    first, second = (sources[line["transaction_id"]] for line in lines)
    account_id = str(first.get("account_id") or "")
    account = accounts.get(account_id, {})
    description = first.get("description") or ""
    if (
        account.get("account_type") != "asset" or not _is_bank(account)
        or second.get("account_id") != first.get("account_id")
        or second.get("date") != first.get("date") or second.get("description") != description
        or re.fullmatch(r"TRANSFER WITHIN [0-9]{6}-[0-9]+ #[0-9]+", description) is None
    ):
        return set()
    try:
        amounts = [Decimal(str(source["amount"])) for source in (first, second)]
        if not all(amount.is_finite() and amount != 0 for amount in amounts) or sum(amounts) != 0:
            return set()
        for line, source, amount in zip(lines, (first, second), amounts):
            if (
                str(line.get("account_id")) != account_id or line.get("description") != description
                or Decimal(str(line.get("debit") or 0)) - Decimal(str(line.get("credit") or 0)) != amount
                or line.get("effective_date") != source.get("effective_date")
            ):
                return set()
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return set()
    return identifiers


def _budget_allowed(category: dict, account: dict) -> bool:
    """
    Mirror shared category type and account restrictions when presenting line-level choices.
    """
    types = {
        "income": {"income", "equity"}, "expense": {"expense"}, "savings": {"asset", "liability"},
    }
    return (
        account.get("account_type") in types.get(category["kind"], set())
        and (not category.get("accounts") or account.get("key") in category["accounts"])
    )


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


def _posting_combo(name: str, *, searchable: bool = False) -> QComboBox:
    """
    Give posting choices a visible arrow and a Qt list popup that works inside the macOS table.
    """
    combo = _AccountComboBox() if searchable else QComboBox()
    combo.setView(QListView(combo))
    combo.setAccessibleName(name)
    arrow = (Path(__file__).parent / "assets" / "chevron_down.svg").as_posix()
    combo.setStyleSheet(
        'QComboBox { padding-right: 28px; } '
        f'QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 6px; }} '
        'QComboBox:disabled { background: #edf2ef; color: #9baaa6; border-color: #dde5e2; }'
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
        location = ".".join(map(str, first["loc"]))
        return f"{location}: {first['msg']}" if location else first["msg"]
    return str(error)
