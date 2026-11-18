# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                   GUI - Catalogue                                                    #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Present account and security catalogues as grouped lists with calm, explicit detail forms.

Grouping changes only the display. Saved order, identifiers and untouched metadata travel through the
shared configuration workflow, which validates dependencies and accounting history before saving.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
    QPlainTextEdit, QScrollArea, QSizePolicy, QStyledItemDelegate, QStyleOptionViewItem, QToolButton,
    QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.icons import icon
from aurum.gui.opening import BankOpeningRow

ACCOUNT_GROUPS = (
    ("asset", "Assets"), ("liability", "Liabilities"), ("equity", "Equity"),
    ("income", "Income"), ("expense", "Expenses"),
)
SECURITY_GROUPS = (("share", "Equities"), ("crypto", "Cryptocurrencies"))


# %% 1. Catalogue Forms --------------------------------------------------------------------------------------
class CatalogueForm(QWidget):
    """
    Keep one local draft per definition while a grouped list selects the visible detail form.
    """

    changed = Signal()
    budget_changed = Signal(str, object)

    def __init__(
        self, schema: dict, value: Any, choices: dict, kind: str, parent: QWidget | None = None,
    ) -> None:
        """
        Copy the editable catalogue without validating models or generating persistent identities.
        """
        super().__init__(parent)
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ValueError(f"{kind.title()} must contain a list of named definitions.")
        self.schema, self.choices, self.kind = schema, choices, kind
        self.records = deepcopy(value)
        self.groups = ACCOUNT_GROUPS if kind == "accounts" else SECURITY_GROUPS
        self.type_field = "account_type" if kind == "accounts" else "security_type"
        self.noun = "account" if kind == "accounts" else "security"
        self._active_index = -1
        self.editor = None
        self._budget_context = None
        self._fixed = choices.get("fixed_equity_accounts", {}) if kind == "accounts" else {}
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(24)
        listing = QWidget()
        listing.setMinimumWidth(220)
        listing.setMaximumWidth(270)
        list_layout = QVBoxLayout(listing)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(10)
        heading = QHBoxLayout()
        heading.addStretch()
        self.add_button = QToolButton()
        self.add_button.setObjectName("catalogueAdd")
        self.add_button.setIcon(icon("add", size=18))
        self.add_button.setIconSize(QSize(18, 18))
        self.add_button.setFixedSize(28, 28)
        self.add_button.setAutoRaise(True)
        self.add_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.add_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.add_button.setToolTip("Add " + self.noun)
        self.add_button.setAccessibleName("Add " + self.noun)
        self.add_button.clicked.connect(self._add)
        heading.addWidget(self.add_button)
        list_layout.addLayout(heading)
        self.list_widget = QListWidget()
        self.list_widget.setObjectName("catalogueList")
        self.list_widget.setItemDelegate(_CatalogueDelegate(self.list_widget))
        self.list_widget.setAccessibleName(kind.title() + " grouped by type")
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.currentRowChanged.connect(self._select_row)
        list_layout.addWidget(self.list_widget, 1)
        self.remove_button = QToolButton()
        self.remove_button.setObjectName("catalogueRemove")
        self.remove_button.setIcon(icon("remove", size=18))
        self.remove_button.setIconSize(QSize(18, 18))
        self.remove_button.setFixedSize(28, 28)
        self.remove_button.setAutoRaise(True)
        self.remove_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.remove_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.remove_button.setAccessibleName("Remove selected " + self.noun)
        self.remove_button.setToolTip(
            "Remove this definition from the draft; saved history is checked on save."
        )
        self.remove_button.clicked.connect(self._remove)
        heading.addWidget(self.remove_button)
        layout.addWidget(listing, 1)

        details = QWidget()
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(0, self.add_button.height() + list_layout.spacing(), 0, 0)
        detail_layout.setSpacing(12)
        self.detail = QScrollArea()
        self.detail.setObjectName("catalogueDetails")
        self.detail.setWidgetResizable(True)
        self.detail.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        detail_layout.addWidget(self.detail, 1)
        layout.addWidget(details, 2)
        self._rebuild_list(0 if self.records else -1)
        self._select_index(0 if self.records else -1)

    def value(self) -> list[dict]:
        """
        Return all local drafts in their original order for the shared validated save operation.
        """
        self._store_active()
        return deepcopy(self.records)

    def view_state(self) -> dict:
        """
        Remember selection by stable identity, with a positional fallback for newly added definitions.
        """
        self._store_active()
        state = {
            "index": self._active_index, "list_scroll": self.list_widget.verticalScrollBar().value(),
            "detail_scroll": self.detail.verticalScrollBar().value(),
        }
        if 0 <= self._active_index < len(self.records):
            record = self.records[self._active_index]
            state["selection"] = {
                key: str(record[key]) for key in ("id", "key", "name") if record.get(key)
            }
        return state

    def restore_view_state(self, state: dict) -> None:
        """
        Restore navigation after a save or reload without producing a configuration change signal.
        """
        if not self.records:
            return
        selection = state.get("selection", {})
        index = None
        for field in ("id", "key"):
            if field in selection:
                index = next((position for position, record in enumerate(self.records)
                              if str(record.get(field, "")) == selection[field]), None)
                if index is not None:
                    break
        if index is None:
            candidate = min(max(state.get("index", 0), 0), len(self.records) - 1)
            if not selection.get("name") or self.records[candidate].get("name") == selection["name"]:
                index = candidate
            else:
                index = next((position for position, record in enumerate(self.records)
                              if record.get("name") == selection["name"]), candidate)
        self._store_active()
        self._rebuild_list(index)
        self._select_index(index)
        self.list_widget.verticalScrollBar().setValue(state.get("list_scroll", 0))
        self.detail.verticalScrollBar().setValue(state.get("detail_scroll", 0))

    def _label(self, record: dict) -> str:
        """
        Display readable names and closure state without altering saved account labels.
        """
        label = self._fixed.get(record.get("key")) or record.get("name") or f"New {self.noun}"
        return str(label) + (" · Closed" if record.get("closing_date") is not None else "")

    def _rebuild_list(self, selected: int, *, reveal: bool = False) -> None:
        """
        Project records into ordered type groups while retaining their storage indices as row data.
        """
        scroll = self.list_widget.verticalScrollBar().value()
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        groups = list(self.groups)
        known = {key for key, _ in groups}
        if any(record.get(self.type_field) not in known for record in self.records):
            groups.append(("", "Choose a type"))
        selected_row = -1
        for group, title in groups:
            indices = [index for index, record in enumerate(self.records) if (
                record.get(self.type_field) == group if group
                else record.get(self.type_field) not in known
            )]
            header = QListWidgetItem(f"{title}  ·  {len(indices)}")
            header.setFlags(Qt.ItemFlag.NoItemFlags)
            font = header.font()
            font.setBold(True)
            header.setFont(font)
            self.list_widget.addItem(header)
            for index in indices:
                record = self.records[index]
                item = QListWidgetItem(self._label(record))
                item.setData(Qt.ItemDataRole.UserRole, index)
                item.setToolTip(self._label(record))
                self.list_widget.addItem(item)
                if index == selected:
                    selected_row = self.list_widget.count() - 1
        self.list_widget.setCurrentRow(selected_row)
        self.list_widget.blockSignals(False)
        if reveal and selected_row >= 0:
            self.list_widget.scrollToItem(self.list_widget.item(selected_row))
        else:
            self.list_widget.verticalScrollBar().setValue(scroll)

    def _select_row(self, row: int) -> None:
        """
        Resolve a visual selection to its saved-order record, ignoring nonselectable group headings.
        """
        item = self.list_widget.item(row)
        index = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        if index is not None:
            self._store_active()
            self._select_index(index)

    def _select_index(self, index: int) -> None:
        """
        Replace the detail widget after its previous local draft has been retained.
        """
        self._active_index = index
        old = self.detail.takeWidget()
        if old is not None:
            old.deleteLater()
        self.editor = None
        if not 0 <= index < len(self.records):
            empty = QLabel(f"Add a {self.noun} to get started.")
            empty.setObjectName("catalogueHint")
            empty.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            self.detail.setWidget(empty)
            self.remove_button.setEnabled(False)
            return
        record = self.records[index]
        if self.kind == "accounts":
            self.editor = AccountEditor(
                record, self._fixed.get(record.get("key")),
                opening_drafts=self.choices.get("bank_opening_drafts"),
            )
            self.editor.set_budget_context(self._budget_context)
            self.editor.budget_changed.connect(self.budget_changed.emit)
        else:
            self.editor = SecurityEditor(record)
        self.editor.changed.connect(self._detail_changed)
        self.detail.setWidget(self.editor)
        self.remove_button.setEnabled(record.get("key") not in self._fixed)

    def _store_active(self) -> None:
        """
        Retain the complete edited record, including fields that are not exposed by the UI.
        """
        if self.editor is not None and 0 <= self._active_index < len(self.records):
            self.records[self._active_index] = self.editor.value()

    def _detail_changed(self) -> None:
        """
        Update labels and type grouping without replacing or losing focus in the active detail form.
        """
        before = self.records[self._active_index].get(self.type_field)
        self._store_active()
        record = self.records[self._active_index]
        self._rebuild_list(self._active_index, reveal=before != record.get(self.type_field))
        self.changed.emit()

    def _add(self) -> None:
        """
        Start a local blank definition in the selected type, leaving key and UUID creation to saving.
        """
        self._store_active()
        selected_type = self.groups[0][0]
        if 0 <= self._active_index < len(self.records):
            candidate = self.records[self._active_index].get(self.type_field)
            if candidate in {key for key, _ in self.groups}:
                selected_type = candidate
        self.records.append({"name": "", self.type_field: selected_type})
        index = len(self.records) - 1
        self._rebuild_list(index, reveal=True)
        self._select_index(index)
        self.editor.name.setFocus()
        self.changed.emit()

    def _remove(self) -> None:
        """
        Remove a local draft only, retaining the UI's fixed equity definitions unconditionally.
        """
        index = self._active_index
        if not 0 <= index < len(self.records) or self.records[index].get("key") in self._fixed:
            return
        removed = self.records.pop(index)
        index = min(index, len(self.records) - 1)
        self._rebuild_list(index, reveal=True)
        self._select_index(index)
        self.changed.emit()
        if self.kind == "accounts" and removed.get("key"):
            self.budget_changed.emit(removed["key"], None)


class AccountsForm(CatalogueForm):
    """
    Group the flat account catalogue into the five existing accounting types.
    """

    def __init__(self, schema: dict, value: Any, choices: dict, parent=None) -> None:
        """
        Use account details while retaining Setup's common configuration-form interface.
        """
        super().__init__(schema, value, choices, "accounts", parent)

    def set_budget_context(self, data: dict | None) -> None:
        """
        Read the shared budget draft without rebuilding account fields or emitting an edit.
        """
        self._budget_context = deepcopy(data)
        if self.editor is not None:
            self.editor.set_budget_context(self._budget_context)


class SecuritiesForm(CatalogueForm):
    """
    Group investment instruments into equities and cryptocurrencies.
    """

    def __init__(self, schema: dict, value: Any, choices: dict, parent=None) -> None:
        """
        Use security details while retaining Setup's common configuration-form interface.
        """
        super().__init__(schema, value, choices, "securities", parent)


# %% 2. Definition Details -----------------------------------------------------------------------------------
class AccountEditor(QWidget):
    """
    Edit account metadata with visible bank fields and aligned optional inputs.
    """

    changed = Signal()
    budget_changed = Signal(str, object)

    def __init__(
        self, record: dict, fixed_name: str | None = None, parent=None, *, opening_drafts=None,
    ) -> None:
        """
        Present bank identifiers and the optional closing date directly, preserving their exact input.
        """
        super().__init__(parent)
        self.original = deepcopy(record)
        self.fixed_name = fixed_name
        self._budget_context = None
        self._budget_mode = "role"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 12, 8)
        layout.setSpacing(14)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        form = _form_layout()
        self._form = form
        layout.addLayout(form)
        self.name = _text_field(fixed_name or record.get("name"), "Account name")
        self.name.setReadOnly(bool(fixed_name))
        if fixed_name and fixed_name != record.get("name"):
            self.name.setToolTip(f"Saved account name: {record.get('name', '')}")
        _add_row(form, "Name", self.name)
        self.account_type = _choice_field(
            tuple((key, key.title()) for key, _ in ACCOUNT_GROUPS),
            record.get("account_type"),
        )
        self.account_type.setEnabled(not bool(fixed_name))
        if fixed_name:
            self.account_type.setToolTip("Required by financial reports. Its type and identity are fixed.")
        _add_row(form, "Account type", self.account_type)
        self.institution = _text_field(record.get("institution"), "Optional")
        _add_row(form, "Institution", self.institution)
        bank = record.get("bank_details") or {}
        if not isinstance(bank, dict):
            raise ValueError("Bank details must contain a BSB and account number.")
        self.bsb = _text_field(bank.get("bsb"), "Optional · 6 digits")
        self.account_number = _text_field(bank.get("account_number"), "Optional bank account number")
        for field in (self.bsb, self.account_number):
            field.setObjectName("catalogueBankField")
            field.setToolTip("For a bank account, enter both its BSB and account number.")
            field.textChanged.connect(self._bank_tone)
        _add_row(form, "BSB", self.bsb)
        _add_row(form, "Account number", self.account_number)
        self._bank_tone()
        self.closing_date = _text_field(record.get("closing_date"), "YYYY-MM-DD or DD-MM-YYYY")
        self.closing_date.setToolTip("Optional. Leave blank while the account remains open.")
        _add_row(form, "Closing date", self.closing_date)
        self.opening_row = None
        if opening_drafts is not None and record.get("account_type") == "asset" and bank:
            self.opening_row = BankOpeningRow(record, opening_drafts, self)
            _add_row(form, "Opening balance", self.opening_row)
        self.budget_choice = _choice_field((), None)
        _add_row(form, "Asset account type", self.budget_choice)
        self.budget_label = form.labelForField(self.budget_choice)
        self.budget_choice.currentIndexChanged.connect(self._choose_budget)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Optional notes")
        self.notes.setPlainText(_text(record.get("notes")))
        self.notes.setFixedHeight(88)
        _add_row(form, "Notes", self.notes)
        self.key = _text_field(record.get("key"), "Generated from the name when saved")
        self.key.setObjectName("catalogueMeta")
        self.key.setReadOnly(True)
        self.key.setToolTip("Stable internal key. The account's UUID is retained without being shown.")
        _add_row(form, "Internal key", self.key)
        if fixed_name:
            for field in (self.bsb, self.account_number, self.closing_date):
                field.setEnabled(False)
        for field in (self.name, self.institution, self.bsb, self.account_number, self.closing_date):
            field.textChanged.connect(self.changed)
        self.account_type.currentIndexChanged.connect(self.changed)
        self.notes.textChanged.connect(self.changed)
        self.name.textChanged.connect(self._refresh_budget_row)
        self.account_type.currentIndexChanged.connect(self._refresh_budget_row)
        self._refresh_budget_row()
        layout.addStretch(1)

    def value(self) -> dict:
        """
        Infer bank details from entered identifiers and keep blank optional inputs absent or null.
        """
        result = deepcopy(self.original)
        if not self.fixed_name:
            result["name"] = self.name.text()
            result["account_type"] = self.account_type.currentData()
            if self.bsb.text() or self.account_number.text():
                bank = deepcopy(self.original.get("bank_details") or {})
                bank.update(bsb=self.bsb.text(), account_number=self.account_number.text())
                result["bank_details"] = bank
            elif "bank_details" in result:
                result["bank_details"] = None
        _optional_text(result, self.original, "institution", self.institution.text())
        _optional_text(result, self.original, "notes", self.notes.toPlainText())
        if not self.fixed_name:
            if self.closing_date.text():
                result["closing_date"] = self.closing_date.text()
            elif "closing_date" in result:
                result["closing_date"] = None
        return result

    def set_budget_context(self, data: dict | None) -> None:
        """
        Display the authoritative budget draft while leaving all account metadata drafts intact.
        """
        self._budget_context = deepcopy(data)
        self._refresh_budget_row()

    def _refresh_budget_row(self) -> None:
        """
        Offer one applicable account setting and retain incompatible references for explicit repair.
        """
        data = self._budget_context or {}
        key = self.original.get("key")
        kind = self.account_type.currentData()
        cash = key in data.get("cash_accounts", ())
        investment = key in data.get("investment_accounts", ())
        repayment = data.get("liability_categories", {}).get(key)
        assigned = cash or investment or repayment is not None
        visible = kind in {"asset", "liability"} or assigned
        self._form.setRowVisible(self.budget_choice, visible)
        self._budget_mode = "liability" if kind == "liability" else "role"
        label = "Expense category" if self._budget_mode == "liability" else "Asset account type"
        self.budget_label.setText(label)
        self.budget_choice.setAccessibleName(label + " for " + (self.name.text() or "new account"))
        options = [(None, "Choose expense category" if kind == "liability" else "None")]
        if kind == "liability":
            options.extend((category_key, category.get("name", category_key))
                           for category_key, category in data.get("categories", {}).items()
                           if category.get("kind") == "expense")
        elif kind == "asset":
            options.extend((("cash", "Cash"), ("investment", "Investment")))
        current = (
            repayment if kind == "liability" else "cash" if cash else "investment" if investment else None
        )
        invalid = None
        if sum((cash, investment, repayment is not None)) > 1:
            invalid = "Multiple roles (unavailable)"
        elif kind == "liability" and (cash or investment):
            invalid = ("Cash" if cash else "Investment") + " (unavailable)"
        elif kind != "liability" and repayment is not None:
            category = data.get("categories", {}).get(repayment, {})
            invalid = str(category.get("name", repayment)) + " (unavailable)"
        elif current not in {value for value, _text in options}:
            label = {"cash": "Cash", "investment": "Investment"}.get(current, current)
            category = data.get("categories", {}).get(current, {})
            invalid = str(category.get("name", label)) + " (unavailable)"
        self.budget_choice.blockSignals(True)
        self.budget_choice.clear()
        for value, text in options:
            self.budget_choice.addItem(text, value)
        if invalid:
            self.budget_choice.addItem(invalid, "__unavailable__")
            index = self.budget_choice.count() - 1
            self.budget_choice.model().item(index).setEnabled(False)
        else:
            index = max(0, self.budget_choice.findData(current))
        self.budget_choice.setCurrentIndex(index)
        self.budget_choice.blockSignals(False)
        self.budget_choice.setEnabled(bool(key) and self._budget_context is not None)
        self.budget_choice.setToolTip(
            "Save this account before choosing its budget setting." if not key else
            "Budget settings are unavailable. Reload Setup to try again." if self._budget_context is None else
            "Choose an expense category for this account's full cash repayments." if kind == "liability" else
            "Cash includes this account's movements in the budget. Investment identifies an asset whose "
            "transfers to and from Cash accounts count as investing and investment returns. "
            "These choices do not change its accounting type or bank details."
        )

    def _choose_budget(self, _index: int) -> None:
        """
        Submit one explicit role choice to Setup without writing or changing account metadata.
        """
        key = self.original.get("key")
        value = self.budget_choice.currentData()
        if not key or self._budget_context is None or value == "__unavailable__":
            return
        choice = {"liability_category": value} if self._budget_mode == "liability" else value
        self.budget_changed.emit(key, choice)

    def _bank_tone(self) -> None:
        """
        Mute optional bank fields when both are empty without preventing typing or keyboard focus.
        """
        empty = not self.bsb.text() and not self.account_number.text()
        for field in (self.bsb, self.account_number):
            if field.property("emptyBankDetails") != empty:
                field.setProperty("emptyBankDetails", empty)
                field.style().unpolish(field)
                field.style().polish(field)
                field.update()


class SecurityEditor(QWidget):
    """
    Edit instrument names and market identifiers with a compact, aligned form.
    """

    changed = Signal()

    def __init__(self, record: dict, parent=None) -> None:
        """
        Show supported instrument fields while retaining identities and optional provider symbols.
        """
        super().__init__(parent)
        self.original = deepcopy(record)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 12, 8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        form = _form_layout()
        layout.addLayout(form)
        self.name = _text_field(record.get("name"), "Security name")
        _add_row(form, "Name", self.name)
        self.security_type = _choice_field(SECURITY_GROUPS, record.get("security_type"))
        _add_row(form, "Instrument type", self.security_type)
        self.symbol = _text_field(record.get("symbol"), "Trading symbol")
        _add_row(form, "Symbol", self.symbol)
        self.market = _text_field(record.get("market"), "For example ASX or Crypto")
        self.market.setToolTip(
            "Identifies the exchange or market for this symbol and is used to select market prices."
        )
        _add_row(form, "Exchange / market", self.market)
        self.currency = _text_field(record.get("currency", "AUD"), "AUD")
        self.currency.setReadOnly(True)
        self.currency.setToolTip("Aurum currently supports AUD instruments and quotes.")
        _add_row(form, "Currency", self.currency)
        self.price_symbol = _text_field(record.get("price_symbol"), "Optional market data symbol")
        _add_row(form, "Price symbol", self.price_symbol)
        self.key = _text_field(record.get("key"), "Generated from the name when saved")
        self.key.setObjectName("catalogueMeta")
        self.key.setReadOnly(True)
        self.key.setToolTip("Stable internal key. The security's UUID is retained without being shown.")
        _add_row(form, "Internal key", self.key)
        for field in (self.name, self.symbol, self.market, self.price_symbol):
            field.textChanged.connect(self.changed)
        self.security_type.currentIndexChanged.connect(self.changed)
        layout.addStretch(1)

    def value(self) -> dict:
        """
        Read explicit instrument edits while leaving hidden fields and the supported currency intact.
        """
        result = deepcopy(self.original)
        result.update(
            name=self.name.text(), security_type=self.security_type.currentData(),
            symbol=self.symbol.text(), market=self.market.text(),
        )
        _optional_text(result, self.original, "price_symbol", self.price_symbol.text())
        return result




# %% 3. Helpers ----------------------------------------------------------------------------------------------
class _CatalogueDelegate(QStyledItemDelegate):
    """
    Indent selectable definitions beneath their type headings without modifying their visible names.
    """

    def paint(self, painter, option, index) -> None:
        """
        Inset definition rows while keeping native selection, elision and keyboard behaviour.
        """
        if index.data(Qt.ItemDataRole.UserRole) is not None:
            option = QStyleOptionViewItem(option)
            option.rect.adjust(16, 0, 0, 0)
        super().paint(painter, option, index)


def _form_layout() -> QFormLayout:
    """
    Give all catalogue detail rows one label alignment and consistent control spacing.
    """
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setHorizontalSpacing(18)
    form.setVerticalSpacing(14)
    return form


def _add_row(form: QFormLayout, text: str, field: QWidget) -> None:
    """
    Align concise labels without optional-setting checkboxes or nested disclosure panels.
    """
    label = QLabel(text)
    label.setMinimumWidth(min(140, max(112, label.fontMetrics().horizontalAdvance(text) + 2)))
    label.setMaximumWidth(140)
    label.setFixedHeight(34)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    label.setBuddy(field)
    form.addRow(label, field)


def _text(value: Any) -> str:
    """
    Display optional values and dates without treating identifiers as numbers.
    """
    return value.isoformat() if isinstance(value, date) else "" if value is None else str(value)


def _text_field(value: Any, placeholder: str) -> QLineEdit:
    """
    Create an aligned text field while preserving original spelling and leading zeros.
    """
    field = QLineEdit(_text(value))
    field.setPlaceholderText(placeholder)
    field.setFixedHeight(34)
    return field


def _choice_field(options: tuple, value: Any) -> QComboBox:
    """
    Keep a native Qt list popup and retain unexpected saved values for explicit correction.
    """
    field = QComboBox()
    field.setObjectName("catalogueSelect")
    field.setView(QListView(field))
    arrow = (Path(__file__).parent / "assets" / "chevron_down.svg").as_posix()
    field.setStyleSheet(
        "QComboBox { padding-right: 28px; } "
        f'QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 6px; }}'
    )
    field.setFixedHeight(34)
    field.addItem("Choose a type…", "")
    for key, label in options:
        field.addItem(label, key)
    index = field.findData(value)
    if index < 0 and value is not None:
        field.addItem(str(value) + " (check type)", value)
        index = field.count() - 1
    field.setCurrentIndex(max(0, index))
    return field


def _optional_text(result: dict, original: dict, key: str, text: str) -> None:
    """
    Keep absent and null values untouched until their visible field changes.
    """
    if text != _text(original.get(key)):
        result[key] = text if text else None
