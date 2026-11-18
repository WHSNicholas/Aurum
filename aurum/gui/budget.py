# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     GUI - Budget                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Edit monthly budget amounts beside a visual comparison of income, expenses and savings.

Existing recurring plans remain readable until an explicit edit adopts independent monthly targets.
Month navigation never creates or saves a plan. Global account roles are edited on Accounts and retained
in this shared draft; targets belong to the selected month. Savings and Investments are fixed destinations.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from math import ceil
from html import escape
import re

from pydantic import ValidationError
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QFrame, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QSizePolicy, QStyleFactory, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.budget import BudgetSettings, budget_plan_totals
from aurum.finance.dates import reporting_month
from aurum.gui.budget_chart import BudgetAllocationChart
from aurum.gui.controls import SearchField, wrapped_text_layout
from aurum.gui.icons import icon
from aurum.gui.theme import gui_colour, monetary_html

KINDS = {"income": "Income", "expense": "Expenses", "savings": "Savings"}
ALLOWED_ACCOUNTS = {
    "income": {"income"}, "expense": {"expense"}, "savings": set(),
}


# %% 1. Monthly Budget Editor --------------------------------------------------------------------------------
class BudgetForm(QWidget):
    """
    Keep monthly targets and deterministic cash-flow account roles in one unsaved draft.
    """

    changed = Signal()
    summary_changed = Signal()

    def __init__(self, schema: dict, value: dict, choices: dict, parent: QWidget | None = None) -> None:
        """
        Build the monthly allocation chart and editable category tables without changing saved values.
        """
        super().__init__(parent)
        self.schema, self.choices = schema, choices
        self.data = deepcopy(value)
        year, month = reporting_month(date.today())
        self._month = f"{year:04d}-{month:02d}"
        self.selected_key = None
        self.tables, self.sections, self.actions = {}, {}, {}
        self.amounts, self._row_keys = {}, {}
        self._account_buttons = {}
        self._account_popup = None
        self._summary = {}
        self._copy_available = False
        self._building = False
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.header = QWidget(self)
        self.header.setObjectName("budgetFixedHeader")
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        header_layout = QVBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        self.copy_month = QToolButton(self)
        self.copy_month.setIcon(icon("copy", size=18))
        self.copy_month.setIconSize(QSize(18, 18))
        self.copy_month.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.copy_month.setFixedSize(30, 30)
        self.copy_month.setAccessibleName("Copy last month's budget")
        self.copy_month.clicked.connect(self._copy_previous)
        self.copy_month.hide()
        self.chart = BudgetAllocationChart()
        header_layout.addWidget(self.chart)
        self.summary = QLabel()
        self.summary.setObjectName("budgetPlanStatus")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        header_layout.addWidget(self.summary)
        self.header.hide()

        self.invalid_notice = QWidget()
        warning = QHBoxLayout(self.invalid_notice)
        warning.setContentsMargins(0, 0, 0, 0)
        self.invalid_label = QLabel()
        self.invalid_label.setObjectName("setupError")
        warning.addWidget(self.invalid_label)
        repair = QToolButton()
        repair.setObjectName("budgetAction")
        repair.setIcon(icon("settings", size=18))
        repair.setFixedSize(28, 28)
        repair.setToolTip("Review invalid categories")
        repair.setAccessibleName("Review invalid categories")
        repair.clicked.connect(self._show_invalid)
        warning.addWidget(repair)
        warning.addStretch()
        layout.addWidget(self.invalid_notice)
        columns = QHBoxLayout()
        columns.setSpacing(12)
        layout.addLayout(columns, 1)
        self.invalid_dialog = QDialog(self)
        self.invalid_dialog.setWindowTitle("Invalid Budget Categories")
        self.invalid_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self.invalid_dialog.resize(640, 360)
        invalid_layout = QVBoxLayout(self.invalid_dialog)
        for kind, name in (*KINDS.items(), ("unknown", "Invalid category section")):
            panel = QFrame()
            panel.setObjectName("budgetSectionPanel")
            panel.setMinimumWidth(0)
            panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            section = QVBoxLayout(panel)
            section.setContentsMargins(0, 0, 0, 0)
            section.setSpacing(8)
            title = QLabel(name)
            title.setObjectName("budgetSection")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            section.addWidget(title)
            action_row = QWidget()
            action_row.setFixedHeight(28)
            actions = QHBoxLayout(action_row)
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(4)
            actions.addStrut(28)
            controls = []
            for symbol, label, callback in (
                ("add", "Add category", lambda _checked=False, selected=kind: self._add_category(selected)),
                ("remove", "Remove category", lambda _checked=False: self._remove_category()),
                ("up", "Move category up", lambda _checked=False: self._move_category(-1)),
                ("down", "Move category down", lambda _checked=False: self._move_category(1)),
            ):
                button = QToolButton()
                button.setObjectName("budgetAction")
                button.setAutoRaise(True)
                button.setFixedSize(28, 28)
                size = 14 if symbol in {"up", "down"} else 18
                button.setIcon(icon(symbol, size=size))
                button.setIconSize(QSize(size, size))
                button.setToolTip(label)
                button.setAccessibleName(label)
                button.clicked.connect(callback)
                actions.addWidget(button)
                controls.append(button)
            controls[0].setVisible(kind not in {"unknown", "savings"})
            if kind == "savings":
                for control in controls:
                    control.hide()
            actions.addStretch()
            section.addWidget(action_row)
            table = _BudgetCategoryTable()
            table.setObjectName("budgetCategoryTable")
            table.setMinimumSize(0, 0)
            table.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            table.setShowGrid(False)
            headers = ["Category", "Amount", "Links"]
            table.setHorizontalHeaderLabels(headers)
            table.horizontalHeaderItem(1).setToolTip("Monthly amount")
            table.horizontalHeaderItem(2).setToolTip("Linked accounts")
            for column in range(3):
                table.horizontalHeaderItem(column).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItemDelegateForColumn(0, _CenteredCategoryDelegate(table))
            table.verticalHeader().hide()
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            table.horizontalHeader().setStyleSheet("QHeaderView::section { border-right: none; }")
            table.currentCellChanged.connect(
                lambda row, _column, _old_row, _old_column, selected=kind: self._select_row(selected, row)
            )
            table.itemChanged.connect(self._rename_category)
            section.addWidget(table, 1)
            if kind == "unknown":
                invalid_layout.addWidget(panel, 1)
            else:
                columns.addWidget(panel, 1)
            self.tables[kind], self.sections[kind] = table, panel
            self.actions[kind] = controls
        close = QToolButton()
        close.setObjectName("budgetAction")
        close.setIcon(icon("close", size=18))
        close.setFixedSize(28, 28)
        close.setToolTip("Close; edits remain in Setup until saved")
        close.setAccessibleName("Close invalid categories")
        close.clicked.connect(self.invalid_dialog.accept)
        invalid_layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        self._build_tables()

    @property
    def categories(self) -> dict:
        """
        Read categories without inserting defaults during a view-only visit.
        """
        return self.data.get("categories", {})

    def value(self) -> dict:
        """
        Return the whole draft for shared validation and comment-preserving configuration persistence.
        """
        return deepcopy(self.data)

    def account_roles(self) -> dict:
        """
        Return only the global mappings edited on Accounts, apart from the monthly budget draft.
        """
        return {
            "cash_accounts": list(self.data.get("cash_accounts", [])),
            "investment_accounts": list(self.data.get("investment_accounts", [])),
            "liability_categories": dict(self.data.get("liability_categories", {})),
        }

    def set_account_roles(self, roles: dict) -> None:
        """
        Replace account-owned mappings without changing targets or emitting a budget-form edit.
        """
        self.data.update({
            "cash_accounts": list(roles.get("cash_accounts", [])),
            "investment_accounts": list(roles.get("investment_accounts", [])),
            "liability_categories": dict(roles.get("liability_categories", {})),
        })

    def set_account_role(self, account: str, choice: object) -> bool:
        """
        Apply one explicit account choice, clearing incompatible roles while retaining all other mappings.
        """
        if not account:
            return False
        before = self.account_roles()
        roles = deepcopy(before)
        target = {"cash": "cash_accounts", "investment": "investment_accounts"}.get(
            choice if isinstance(choice, str) else ""
        )
        for field in ("cash_accounts", "investment_accounts"):
            if field != target:
                roles[field] = [key for key in roles[field] if key != account]
            elif account not in roles[field]:
                roles[field].append(account)
        category = choice.get("liability_category") if isinstance(choice, dict) else None
        if category is None:
            roles["liability_categories"].pop(account, None)
        else:
            roles["liability_categories"][account] = category
        self.set_account_roles(roles)
        return roles != before

    def set_reporting_month(self, month: str) -> None:
        """
        Select an initial or requested planning month without creating targets or emitting a change.
        """
        _valid_month(month)
        self._month = month
        self._build_tables()

    def summary_info(self) -> dict[str, object]:
        """
        Expose the selected month's allocated totals and draft status for Setup's read-only summary card.
        """
        return {**self._summary, "totals": dict(self._summary.get("totals", {}))}

    def view_state(self) -> dict:
        """
        Retain category selection while leaving the shared reporting month with the window.
        """
        return {
            "category": self.selected_key,
            "scroll": {kind: table.verticalScrollBar().value() for kind, table in self.tables.items()},
        }

    def restore_view_state(self, state: dict) -> None:
        """
        Restore category selection without letting an older view state change the reporting month.
        """
        self.selected_key = state.get("category")
        self._build_tables()
        self._restore_scroll(state.get("scroll", {}))

    def eventFilter(self, watched, event) -> bool:
        """
        Keep category actions attached to the row whose amount field receives focus.
        """
        if event.type() in {QEvent.Type.FocusIn, QEvent.Type.MouseButtonPress}:
            key = watched.property("budgetCategory")
            if key in self.categories:
                self._select_key(key)
        return super().eventFilter(watched, event)

    def changeEvent(self, event) -> None:
        """
        Keep reparented header controls disabled whenever the amounts form is disabled.
        """
        super().changeEvent(event)
        if event.type() == QEvent.Type.EnabledChange and hasattr(self, "header"):
            if not self.isEnabled():
                self._close_accounts()
            self.header.setEnabled(self.isEnabled())
            if hasattr(self, "copy_month"):
                self.copy_month.setEnabled(self.isEnabled() and self._copy_available)

    def hideEvent(self, event) -> None:
        """
        Dismiss account choices when leaving the page without reverting already staged links.
        """
        self._close_accounts()
        super().hideEvent(event)

    def minimumSizeHint(self) -> QSize:
        """
        Let the three tables share the available viewport instead of sizing the page to their rows.
        """
        return QSize(0, 160)

    def _restore_scroll(self, positions: dict) -> None:
        """
        Restore each independent table's position after rebuilding or returning to this section.
        """
        for kind, position in positions.items():
            if kind in self.tables:
                self.tables[kind].verticalScrollBar().setValue(position)

    def _show_invalid(self) -> None:
        """
        Keep malformed saved categories repairable without adding a fourth ordinary budget column.
        """
        self.invalid_dialog.show()
        self.invalid_dialog.raise_()
        self.invalid_dialog.activateWindow()

    def _build_tables(self) -> None:
        """
        Rebuild the displayed month while retaining category identities and untouched draft values.
        """
        self._close_accounts()
        positions = {kind: table.verticalScrollBar().value() for kind, table in self.tables.items()}
        self._building = True
        self.amounts, self._row_keys = {}, {}
        self._account_buttons = {}
        targets = _raw_targets(self.data, self._month)
        for kind, table in self.tables.items():
            table.blockSignals(True)
            table.setRowCount(0)
            keys = [
                key for key, category in self.categories.items()
                if (category.get("kind") if category.get("kind") in KINDS else "unknown") == kind
            ]
            self._row_keys[kind] = keys
            if kind == "unknown":
                self.invalid_notice.setVisible(bool(keys))
                self.invalid_label.setText(
                    f"{len(keys)} invalid " + ("category" if len(keys) == 1 else "categories")
                )
            for row, key in enumerate(keys):
                category = self.categories[key]
                table.insertRow(row)
                name = QTableWidgetItem(category.get("name", key))
                name.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                name.setData(Qt.ItemDataRole.UserRole, key)
                name.setToolTip(f"{category.get('name', key)}\nStable category key: {key}")
                if key in {"savings", "investments", "gift_received", "investment_return"}:
                    name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, 0, name)
                amount = targets.get(key)
                editor = QLineEdit("" if amount is None else str(amount))
                editor.setProperty("monetary", True)
                editor.setObjectName("budgetAmount")
                editor.setMinimumWidth(0)
                editor.setPlaceholderText("Amount")
                editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
                editor.setAccessibleName(f"{category.get('name', key)} monthly amount")
                editor.setProperty("budgetCategory", key)
                editor.installEventFilter(self)
                editor.textChanged.connect(lambda text, selected=key: self._set_amount(selected, text))
                editor.ensurePolished()
                editor.setFixedHeight(editor.sizeHint().height())
                amount_cell = QWidget()
                amount_cell.setObjectName("budgetAmountCell")
                amount_layout = QVBoxLayout(amount_cell)
                amount_layout.setContentsMargins(4, 0, 4, 0)
                amount_layout.addWidget(editor, 0, Qt.AlignmentFlag.AlignVCenter)
                table.setCellWidget(row, 1, amount_cell)
                self.amounts[key] = editor
                account_button = _AccountLinkButton()
                account_button.setAccessibleName(f"Accounts for {category.get('name', key)}")
                account_button.clicked.connect(
                    lambda _checked=False, selected=key: self._edit_accounts(selected)
                )
                if key in {"savings", "investments", "gift_received"}:
                    automatic = QLabel("Auto")
                    automatic.setObjectName("budgetAutomatic")
                    automatic.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    automatic.setToolTip(
                        "Calculated from cash movements and account roles in Setup → Accounts."
                    )
                    account_button.deleteLater()
                    table.setCellWidget(row, 2, automatic)
                else:
                    table.setCellWidget(row, 2, account_button)
                    self._account_buttons[key] = account_button
                    self._update_account_button(key)
                table.setRowHeight(row, max(42, editor.sizeHint().height() + 12))
            table.blockSignals(False)
            table.resize_link_rows()
        self._building = False
        self._select_key(self.selected_key)
        self._restore_scroll(positions)
        self._summarise()

    def _select_row(self, kind: str, row: int) -> None:
        """
        Maintain one selected category across the three section tables.
        """
        if self._building:
            return
        keys = self._row_keys.get(kind, [])
        self.selected_key = keys[row] if 0 <= row < len(keys) else None
        for other, table in self.tables.items():
            if other != kind:
                table.blockSignals(True)
                table.clearSelection()
                table.setCurrentCell(-1, -1)
                table.blockSignals(False)
        self._update_actions()

    def _select_key(self, key: str | None) -> None:
        """
        Restore a selected category and clear stale selections when a row no longer exists.
        """
        self.selected_key = key if key in self.categories else None
        for kind, table in self.tables.items():
            table.blockSignals(True)
            keys = self._row_keys.get(kind, [])
            if self.selected_key in keys:
                table.setCurrentCell(keys.index(self.selected_key), 0)
                table.selectRow(keys.index(self.selected_key))
            else:
                table.clearSelection()
                table.setCurrentCell(-1, -1)
            table.blockSignals(False)
        self._update_actions()

    def _update_actions(self) -> None:
        """
        Enable row actions only beside the section containing the selected category.
        """
        for kind, buttons in self.actions.items():
            keys = self._row_keys.get(kind, [])
            selected = self.selected_key in keys and self.selected_key not in {
                "savings", "investments", "gift_received", "investment_return",
            }
            index = keys.index(self.selected_key) if selected else -1
            for button, enabled in zip(buttons[1:], (
                selected, selected and index > 0, selected and index < len(keys) - 1,
            )):
                button.setEnabled(enabled)

    def _adopt_month(self) -> None:
        """
        Start independent planning in the draft while preserving every existing effective override.
        """
        previous = deepcopy(self.data)
        boundary = previous.get("monthly_plans_from")
        overrides = self.data.setdefault("monthly_overrides", {})
        if boundary is None or self._month < boundary:
            for month in tuple(overrides):
                if month >= self._month and (boundary is None or month < boundary):
                    overrides[month] = {
                        key: {"amount": amount} for key, amount in _raw_targets(previous, month).items()
                    }
            self.data["monthly_plans_from"] = self._month
        targets = overrides.setdefault(self._month, {})
        for key, amount in _raw_targets(previous, self._month).items():
            targets.setdefault(key, {"amount": amount})

    def _set_amount(self, key: str, text: str) -> None:
        """
        Keep explicit decimal text or a blank target in the selected month's independent plan.
        """
        self._adopt_month()
        self.data["monthly_overrides"][self._month][key] = {"amount": text.strip() or None}
        self._edited()

    def _rename_category(self, item: QTableWidgetItem) -> None:
        """
        Change a display name without regenerating its key or changing historical labels.
        """
        if self._building or item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        if key in self.categories:
            self._adopt_month()
            self.categories[key]["name"] = item.text().strip()
            self._edited()

    def _edited(self) -> None:
        """
        Refresh draft feedback before informing Setup that an explicit edit needs saving.
        """
        self._summarise()
        self.changed.emit()

    def _summarise(self) -> None:
        """
        Show exact selected-month totals and update the chart without requiring complete targets.
        """
        targets = _raw_targets(self.data, self._month)
        rows = [(category.get("name", key), category.get("kind"), _amount(targets.get(key)))
                for key, category in self.categories.items()]
        self.chart.set_amounts(rows)
        previous = _shift_month(self._month, -1)
        previous_targets = _raw_targets(self.data, previous) if previous else {}
        self._copy_available = any(value is not None for value in previous_targets.values())
        self.copy_month.setEnabled(self.isEnabled() and self._copy_available)
        self.copy_month.setToolTip(
            f"Replace this month's amounts with {_month_name(previous)}."
            if previous else "There is no earlier month."
        )
        status, detail, display_detail = "incomplete", "", ""
        totals = {kind: None for kind in KINDS}
        try:
            settings = BudgetSettings.model_validate(self.data)
            totals = budget_plan_totals(settings, self._month)
            if self.categories:
                if all(value is not None for value in totals.values()):
                    income, expense, savings = (_cents(totals[kind]) for kind in KINDS)
                    difference = income - expense - savings
                    status = "balanced" if difference == 0 else "invalid"
                    if difference:
                        amount = _money(Decimal(abs(difference)) / 100)
                        prefix = "Income has " if difference > 0 else "Expenses and savings exceed income by "
                        suffix = " left to allocate." if difference > 0 else "."
                        detail = prefix + amount + suffix
                        display_detail = prefix + monetary_html(amount) + suffix
        except (ValidationError, ValueError, TypeError) as error:
            status = "invalid"
            detail = _validation_message(error) if isinstance(error, ValidationError) else str(error)
        self.summary.setText(display_detail or escape(detail))
        self.summary.setVisible(status == "invalid")
        self.summary.setProperty("tone", status)
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)
        self._summary = {"month": self._month, "status": status, "detail": detail, "totals": dict(totals)}
        self._summary.update({
            kind: "—" if amount is None else _money(amount) for kind, amount in totals.items()
        })
        self.summary_changed.emit()

    def _copy_previous(self) -> None:
        """
        Explicitly copy the previous month's amounts into this month's unsaved plan.
        """
        previous = _shift_month(self._month, -1)
        targets = _raw_targets(self.data, previous) if previous else {}
        if not any(value is not None for value in targets.values()):
            return
        self._adopt_month()
        self.data["monthly_overrides"][self._month] = {
            key: {"amount": targets.get(key)} for key in self.categories
        }
        self._build_tables()
        self.changed.emit()

    def _add_category(self, kind: str) -> None:
        """
        Add a category in the chosen section with a stable key and an unfinished monthly target.
        """
        if kind not in {"income", "expense"}:
            return
        name, accepted = QInputDialog.getText(self, "Add " + KINDS[kind].lower() + " category", "Name")
        if not accepted or not name.strip():
            return
        base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if not base or not base[0].isalpha() or base in {"default", "exclude"}:
            QMessageBox.warning(self, "Choose another name", "Use a name beginning with a letter.")
            return
        key, suffix = base, 2
        while key in self.categories:
            key, suffix = f"{base}_{suffix}", suffix + 1
        self._adopt_month()
        self.data.setdefault("categories", {})[key] = {"name": name.strip(), "kind": kind, "amount": None}
        self.data["monthly_overrides"][self._month][key] = {"amount": None}
        self.selected_key = key
        self._build_tables()
        self.changed.emit()

    def _remove_category(self) -> None:
        """
        Remove a draft category while leaving saved-history validation to the configuration save.
        """
        key = self.selected_key
        if key not in self.categories or key in {
            "savings", "investments", "gift_received", "investment_return",
        }:
            return
        self._adopt_month()
        del self.categories[key]
        for targets in self.data.get("monthly_overrides", {}).values():
            targets.pop(key, None)
        self.selected_key = None
        self._build_tables()
        self.changed.emit()

    def _move_category(self, offset: int) -> None:
        """
        Reorder one section without changing keys, amounts or account mappings.
        """
        key = self.selected_key
        if key not in self.categories:
            return
        kind = self.categories[key].get("kind")
        group = [item for item, category in self.categories.items() if category.get("kind") == kind]
        index = group.index(key)
        if not 0 <= index + offset < len(group):
            return
        self._adopt_month()
        keys = list(self.categories)
        first, second = keys.index(key), keys.index(group[index + offset])
        keys[first], keys[second] = keys[second], keys[first]
        self.data["categories"] = {item: self.categories[item] for item in keys}
        self._build_tables()
        self.changed.emit()

    def _edit_accounts(self, key: str) -> None:
        """
        Open a searchable multi-select dropdown whose changes join the current Setup draft immediately.
        """
        if not self.isEnabled() or key not in self._account_buttons:
            return
        self._close_accounts()
        self._select_key(key)
        category = self.categories[key]
        current = list(category.get("accounts", []))
        permitted = ALLOWED_ACCOUNTS.get(category.get("kind"), set())
        names, types = self.choices.get("accounts", {}), self.choices.get("account_types", {})
        available = [item for item in names if not types or types.get(item) in permitted or item in current]
        available += [item for item in current if item not in available]
        options = []
        for account in available:
            label = names.get(account, account)
            if account not in names or types and types.get(account) not in permitted:
                label += " (unavailable)"
            options.append((account, label))
        popup = _AccountLinksPopup(
            self, self._account_buttons[key], category.get("name", key),
            options, current, category.get("kind"),
        )
        self._account_popup = popup
        popup.selection_changed.connect(lambda selected: self._set_accounts(key, selected))
        popup.closed.connect(lambda: self._accounts_closed(popup))
        popup.open()

    def _set_accounts(self, key: str, checked: list[str]) -> None:
        """
        Preserve link order and update only the active cell, leaving the dropdown and other edits intact.
        """
        if not self.isEnabled() or key not in self.categories:
            self._close_accounts()
            return
        current = list(self.categories[key].get("accounts", []))
        selected = [item for item in current if item in checked]
        selected += [item for item in checked if item not in selected]
        if selected == current:
            return
        self._adopt_month()
        self.categories[key]["accounts"] = selected
        self._update_account_button(key)
        self._edited()

    def _update_account_button(self, key: str) -> None:
        """
        Show complete staged account names without rebuilding the table or moving keyboard focus.
        """
        button = self._account_buttons[key]
        keys = self.categories[key].get("accounts", [])
        names = self.choices.get("accounts", {})
        labels = [names.get(account, account + " (unavailable)") for account in keys]
        button.setText("\n".join(labels) or "Choose accounts")
        button.setToolTip(
            (", ".join(labels) + "\n" if labels else "Choose accounts. ")
            + "Links apply to every month; save them with Setup."
        )
        button.setAccessibleDescription(f"{len(keys)} linked accounts. " + ", ".join(labels))
        for table in self.tables.values():
            table.resize_link_rows()

    def _close_accounts(self) -> None:
        """
        Close choices before rebuilding, disabling or hiding their owning form.
        """
        popup = self._account_popup
        self._account_popup = None
        if popup is not None:
            popup.close()

    def _accounts_closed(self, popup: QWidget) -> None:
        """
        Release the active popup reference while keeping all explicit checkbox changes staged.
        """
        if self._account_popup is popup:
            self._account_popup = None


# %% 2. Table Controls ---------------------------------------------------------------------------------------
class _AccountLinkButton(QToolButton):
    """
    Keep complete account names readable in a wrapping, keyboard-accessible dropdown field.
    """

    def __init__(self, parent=None) -> None:
        """
        Reserve a small trailing chevron while the label uses the rest of the cell.
        """
        super().__init__(parent)
        self._text = ""
        self.setObjectName("budgetAccountLink")
        self.setMinimumWidth(0)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def setText(self, text: str) -> None:
        """
        Update the wrapping label without drawing a second native button caption.
        """
        self._text = text
        self.update()

    def text(self) -> str:
        """
        Expose the full visible account names to accessibility and inspection.
        """
        return self._text

    def heightForWidth(self, width: int) -> int:
        """
        Give the table enough room for every linked account at the current column width.
        """
        text = wrapped_text_layout(self._text, self.font(), width - 24, Qt.AlignmentFlag.AlignCenter)
        return max(42, ceil(text.boundingRect().height()) + 16)

    def paintEvent(self, event) -> None:
        """
        Retain native interaction fills and draw the shared thin disclosure chevron at the trailing edge.
        """
        super().paintEvent(event)
        painter = QPainter(self)
        text = wrapped_text_layout(self._text, self.font(), self.width() - 24, Qt.AlignmentFlag.AlignCenter)
        painter.setPen(QColor(gui_colour(self, "text")))
        text.draw(painter, QPointF(4, (self.height() - text.boundingRect().height()) / 2))
        icon("chevron_down", gui_colour(self, "icon"), 10).paint(
            painter, self.width() - 16, (self.height() - 10) // 2, 10, 10,
        )


class _BudgetCategoryTable(QTableWidget):
    """
    Share available width between category, amount and links without forcing the page wider.
    """

    def __init__(self, parent=None) -> None:
        """
        Observe the viewport so adding a vertical scrollbar also recalculates the column widths.
        """
        super().__init__(0, 3, parent)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.horizontalHeader().setMinimumSectionSize(0)
        self.viewport().installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:
        """
        Reserve a usable amount field and give account names most of the remaining space.
        """
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            width = max(0, self.viewport().width())
            amount = min(width, max(104, round(width * 0.25)))
            heading = self.horizontalHeaderItem(0)
            minimum = (
                self.horizontalHeader().fontMetrics().horizontalAdvance(heading.text()) + 10 if heading else 0
            )
            category = min(width - amount, max(minimum, round((width - amount) * 0.4)))
            for column, size in enumerate((category, amount, width - amount - category)):
                self.setColumnWidth(column, size)
            self.resize_link_rows()
        return super().eventFilter(watched, event)

    def resize_link_rows(self) -> None:
        """
        Fit full linked names while ordinary rows retain their compact height.
        """
        for row in range(self.rowCount()):
            editor, links = self.cellWidget(row, 1), self.cellWidget(row, 2)
            height = max(42, editor.sizeHint().height() + 12) if editor is not None else 42
            if isinstance(links, _AccountLinkButton):
                height = max(height, links.heightForWidth(self.columnWidth(2)))
            self.setRowHeight(row, height)


class _AccountLinksPopup(QFrame):
    """
    Toggle category-account links in place while leaving persistence to Setup Save.
    """

    selection_changed = Signal(object)
    closed = Signal()

    def __init__(
        self, parent: QWidget, anchor: QWidget, category: str, options: list[tuple[str, str]],
        current: list[str], kind: str,
    ) -> None:
        """
        Build a searchable checklist without emitting changes while its saved selections are populated.
        """
        super().__init__(parent, Qt.WindowType.Popup)
        self.anchor = anchor
        self.setObjectName("budgetAccountsPopup")
        self.setAccessibleName("Accounts for " + category)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setStyleSheet(
            "QFrame#budgetAccountsPopup { background: transparent; } "
            "QListWidget#budgetAccountChecklist { background: transparent; border: none; padding: 4px; } "
            "QLabel#budgetAccountEmpty { background: transparent; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.search = SearchField("Search accounts")
        self.search.setAccessibleName("Search accounts for " + category)
        self.search.installEventFilter(self)
        layout.addWidget(self.search)
        self.listing = QListWidget()
        self.listing.setObjectName("budgetAccountChecklist")
        self.listing.setAccessibleName("Linked accounts for " + category)
        self.listing.setItemDelegate(_AccountCheckDelegate(self.listing))
        self.listing.setUniformItemSizes(True)
        self.listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.listing.setCursor(Qt.CursorShape.PointingHandCursor)
        # Fusion keeps unchecked indicators visible on macOS, alongside the shared themed checkmarks.
        self.listing._check_style = QStyleFactory.create("Fusion")
        self.listing._check_style.setParent(self.listing)
        self.listing.setStyle(self.listing._check_style)
        for account, label in options:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, account)
            item.setToolTip(label + "\n" + account)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if account in current else Qt.CheckState.Unchecked)
            self.listing.addItem(item)
        self.listing.setFixedHeight(min(300, max(42, len(options) * 34 + 8)))
        self.listing.setVisible(bool(options))
        layout.addWidget(self.listing)
        account_kind = {"income": "income", "expense": "expense"}.get(kind, "eligible")
        self.empty = QLabel(f"Add and save an {account_kind} account in Setup → Accounts first.")
        self.empty.setObjectName("budgetAccountEmpty")
        self.empty.setWordWrap(True)
        self.empty.setVisible(not options)
        layout.addWidget(self.empty)
        self.search.textChanged.connect(self._filter)
        self.listing.itemChanged.connect(self._changed)

    def open(self) -> None:
        """
        Anchor beneath the cell, or above it when necessary, within the current display's bounds.
        """
        bounds = self.anchor.screen().availableGeometry()
        self.setFixedWidth(min(320, bounds.width() - 16))
        self.adjustSize()
        below = self.anchor.mapToGlobal(QPoint(0, self.anchor.height() + 4))
        top = self.anchor.mapToGlobal(QPoint(0, -4))
        x = max(bounds.left() + 8, min(below.x(), bounds.right() - self.width() - 7))
        y = below.y() if below.y() + self.height() <= bounds.bottom() - 8 else top.y() - self.height()
        self.move(x, max(bounds.top() + 8, y))
        self.show()
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def eventFilter(self, watched, event) -> bool:
        """
        Let Down move directly from search to the first visible account for keyboard selection.
        """
        if watched is self.search and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Down:
            for index in range(self.listing.count()):
                if not self.listing.item(index).isHidden():
                    self.listing.setCurrentRow(index)
                    self.listing.setFocus(Qt.FocusReason.TabFocusReason)
                    return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        """
        Escape dismisses the dropdown while retaining changes already staged through its checkboxes.
        """
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            self.anchor.setFocus(Qt.FocusReason.PopupFocusReason)
            return
        super().keyPressEvent(event)

    def hideEvent(self, event) -> None:
        """
        Release the popup after dismissal without undoing its explicit selections.
        """
        super().hideEvent(event)
        self.closed.emit()
        self.deleteLater()

    def paintEvent(self, event) -> None:
        """
        Fill the rounded native popup with the owning workspace's saved surface colour.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(gui_colour(self, "surface", "#ffffff")))
        painter.drawRoundedRect(QRectF(self.rect()), 12, 12)

    def resizeEvent(self, event) -> None:
        """
        Round the native window itself so the backing never leaves square corner artefacts.
        """
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def _filter(self, text: str) -> None:
        """
        Filter only visibility, preserving every checked account even when it is hidden by search.
        """
        visible = 0
        for index in range(self.listing.count()):
            item = self.listing.item(index)
            matches = text.casefold() in (item.text() + " " + item.data(Qt.ItemDataRole.UserRole)).casefold()
            item.setHidden(not matches)
            visible += matches
        if self.listing.count():
            self.empty.setText("No matching accounts")
            self.empty.setVisible(not visible)

    def _changed(self, _item: QListWidgetItem) -> None:
        """
        Publish the complete checked set after one interaction without closing the dropdown.
        """
        self.selection_changed.emit([
            self.listing.item(index).data(Qt.ItemDataRole.UserRole) for index in range(self.listing.count())
            if self.listing.item(index).checkState() == Qt.CheckState.Checked
        ])


class _AccountCheckDelegate(QStyledItemDelegate):
    """
    Toggle account links across the full row while retaining native check-state and keyboard support.
    """

    def sizeHint(self, option, index) -> QSize:
        """
        Keep account labels and their visible checkboxes comfortably separated.
        """
        size = super().sizeHint(option, index)
        size.setHeight(max(34, size.height()))
        return size

    def editorEvent(self, event, model, option, index) -> bool:
        """
        Treat a left-click on the label like its checkbox, without toggling checkbox clicks twice.
        """
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            flags = index.flags()
            if (flags & Qt.ItemFlag.ItemIsEnabled and flags & Qt.ItemFlag.ItemIsUserCheckable
                    and option.rect.contains(event.position().toPoint())):
                checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
                state = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
                return model.setData(index, state.value, Qt.ItemDataRole.CheckStateRole)
        return super().editorEvent(event, model, option, index)


class _CenteredCategoryDelegate(QStyledItemDelegate):
    """
    Keep category names centred when editing as well as when displaying their table cells.
    """

    def createEditor(self, parent, option, index):
        """
        Use the normal category editor and retain its existing commit behavior with centred text.
        """
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return editor


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _raw_targets(data: dict, month: str) -> dict:
    """
    Resolve editable values while retaining incomplete decimal text that cannot yet form a valid model.
    """
    boundary = data.get("monthly_plans_from")
    independent = boundary is not None and month >= boundary
    overrides = data.get("monthly_overrides", {}).get(month, {})
    return {
        key: overrides.get(key, {} if independent else category).get("amount")
        for key, category in data.get("categories", {}).items()
    }


def _valid_month(month: str) -> None:
    """
    Accept a valid planning month without restricting future budget preparation.
    """
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}", month):
        raise ValueError("Choose a month using YYYY-MM")
    date.fromisoformat(month + "-01")


def _month_name(month: str) -> str:
    """
    Show a readable month label while preserving its stored key.
    """
    return date.fromisoformat(month + "-01").strftime("%B %Y")


def _shift_month(month: str, offset: int) -> str | None:
    """
    Move across year boundaries while staying within supported calendar years.
    """
    year, number = map(int, month.split("-"))
    ordinal = year * 12 + number - 1 + offset
    shifted_year, shifted_month = divmod(ordinal, 12)
    return f"{shifted_year:04d}-{shifted_month + 1:02d}" if 1 <= shifted_year <= 9999 else None


def _amount(value) -> Decimal | None:
    """
    Return a valid whole-cent nonnegative chart amount, leaving malformed or blank input unset.
    """
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            return None
        numerator, denominator = amount.as_integer_ratio()
        return amount if (numerator * 100) % denominator == 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _cents(value: Decimal) -> int:
    """
    Convert exact money to integer cents without depending on Decimal's working precision.
    """
    numerator, denominator = value.as_integer_ratio()
    return numerator * 100 // denominator


def _money(value: Decimal) -> str:
    """
    Format AUD amounts with narrow nonbreaking thousands separators.
    """
    return "$" + f"{value:,.2f}".replace(",", "\u202f")


def _validation_message(error: ValidationError) -> str:
    """
    Identify an invalid field without exposing a complete configuration dump.
    """
    messages = []
    for issue in error.errors(include_input=False):
        path = " › ".join(str(part).replace("_", " ") for part in issue["loc"])
        messages.append(f"{path}: {issue['msg']}")
    return "\n".join(messages)
