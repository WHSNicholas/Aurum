# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     GUI - Budget                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Edit monthly budget amounts beside a visual comparison of income, expenses and savings.

Existing recurring plans remain readable until an explicit edit adopts independent monthly targets.
Month navigation never creates or saves a plan. Category identities, account mappings and virtual pots
remain separate from the selected month's amounts.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from pydantic import ValidationError
from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QRadioButton, QSizePolicy, QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.budget import BudgetSettings, budget_plan_totals
from aurum.finance.dates import reporting_month
from aurum.gui.budget_chart import BudgetAllocationChart
from aurum.gui.icons import icon
from aurum.gui.theme import gui_colour

KINDS = {"income": "Income", "expense": "Expenses", "savings": "Savings"}
ALLOWED_ACCOUNTS = {
    "income": {"income", "equity"}, "expense": {"expense"}, "savings": {"asset", "liability"},
}


# %% 1. Monthly Budget Editor --------------------------------------------------------------------------------
class BudgetForm(QWidget):
    """
    Keep direct monthly inputs, account assignments and virtual-pot choices in one unsaved draft.
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
        self.tables, self.sections, self.actions, self.totals = {}, {}, {}, {}
        self.amounts, self._row_keys = {}, {}
        self._summary = {}
        self._copy_available = False
        self._building = False
        self._residual_group = QButtonGroup(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
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
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        header_layout.addWidget(self.summary)
        self.header.hide()

        for kind, name in (*KINDS.items(), ("unknown", "Invalid category section")):
            panel = QFrame()
            panel.setObjectName("budgetSectionPanel")
            panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            section = QVBoxLayout(panel)
            section.setContentsMargins(0, 0, 0, 0)
            section.setSpacing(8)
            heading = QHBoxLayout()
            title = QLabel(name)
            title.setObjectName("budgetSection")
            heading.addWidget(title)
            total = QLabel()
            total.setObjectName("budgetSectionTotal")
            heading.addWidget(total)
            heading.addStretch()
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
                button.setFixedSize(30, 30)
                size = 14 if symbol in {"up", "down"} else 18
                button.setIcon(icon(symbol, size=size))
                button.setIconSize(QSize(size, size))
                button.setToolTip(label)
                button.setAccessibleName(label)
                button.clicked.connect(callback)
                heading.addWidget(button)
                controls.append(button)
            controls[0].setVisible(kind != "unknown")
            section.addLayout(heading)
            table = QTableWidget(0, 5 if kind == "savings" else 3)
            table.setObjectName("budgetCategoryTable")
            table.setShowGrid(False)
            headers = ["Category", "Monthly amount", "Accounts"]
            if kind == "savings":
                headers += ["Pot", "Residual"]
            table.setHorizontalHeaderLabels(headers)
            for column in (0, 1):
                table.horizontalHeaderItem(column).setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            table.setItemDelegateForColumn(0, _CenteredCategoryDelegate(table))
            table.verticalHeader().hide()
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
            table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            table.horizontalHeader().setStyleSheet("QHeaderView::section { border-right: none; }")
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
            table.setColumnWidth(1, 140)
            table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            for column in range(3, table.columnCount()):
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(column, 52 if column == 3 else 86)
            table.currentCellChanged.connect(
                lambda row, _column, _old_row, _old_column, selected=kind: self._select_row(selected, row)
            )
            table.itemChanged.connect(self._rename_category)
            section.addWidget(table)
            layout.addWidget(panel, 0, Qt.AlignmentFlag.AlignHCenter)
            self.tables[kind], self.sections[kind] = table, panel
            self.actions[kind], self.totals[kind] = controls, total
        layout.addStretch()
        self._build_tables()
        self._size_sections()

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

    def set_reporting_month(self, month: str) -> None:
        """
        Select an initial or requested planning month without creating targets or emitting a change.
        """
        _valid_month(month)
        self._month = month
        self._build_tables()

    def summary_info(self) -> dict[str, str]:
        """
        Expose the selected month's allocated totals and draft status for Setup's read-only summary card.
        """
        return dict(self._summary)

    def view_state(self) -> dict:
        """
        Retain category selection while leaving the shared reporting month with the window.
        """
        return {"category": self.selected_key}

    def restore_view_state(self, state: dict) -> None:
        """
        Restore category selection without letting an older view state change the reporting month.
        """
        self.selected_key = state.get("category")
        self._build_tables()

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
            self.header.setEnabled(self.isEnabled())
            if hasattr(self, "copy_month"):
                self.copy_month.setEnabled(self.isEnabled() and self._copy_available)

    def resizeEvent(self, event) -> None:
        """
        Keep amount tables comfortably narrow and centred within the available scroll viewport.
        """
        super().resizeEvent(event)
        self._size_sections()

    def minimumSizeHint(self) -> QSize:
        """
        Allow a narrow viewport to reduce the preferred table width without horizontal scrolling.
        """
        hint = super().minimumSizeHint()
        return QSize(min(380, hint.width()), hint.height())

    def _size_sections(self) -> None:
        """
        Prefer eighty percent of the form width, capped at 740 pixels and bounded by its viewport.
        """
        if not hasattr(self, "sections"):
            return
        available = max(1, self.contentsRect().width())
        width = min(740, available, max(600, int(available * 0.8)))
        for panel in self.sections.values():
            if panel.minimumWidth() != width or panel.maximumWidth() != width:
                panel.setFixedWidth(width)

    def _build_tables(self) -> None:
        """
        Rebuild the displayed month while retaining category identities and untouched draft values.
        """
        self._building = True
        self.amounts, self._row_keys = {}, {}
        for button in self._residual_group.buttons():
            self._residual_group.removeButton(button)
        targets = _raw_targets(self.data, self._month)
        savings = self.data.get("savings") or {}
        pots = savings.get("category_keys", [])
        for kind, table in self.tables.items():
            table.blockSignals(True)
            table.setRowCount(0)
            keys = [
                key for key, category in self.categories.items()
                if (category.get("kind") if category.get("kind") in KINDS else "unknown") == kind
            ]
            self._row_keys[kind] = keys
            self.sections[kind].setVisible(kind != "unknown" or bool(keys))
            for row, key in enumerate(keys):
                category = self.categories[key]
                table.insertRow(row)
                name = QTableWidgetItem(category.get("name", key))
                name.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                name.setData(Qt.ItemDataRole.UserRole, key)
                name.setToolTip(f"Stable category key: {key}")
                table.setItem(row, 0, name)
                amount = targets.get(key)
                editor = QLineEdit("" if amount is None else str(amount))
                editor.setPlaceholderText("Amount")
                editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
                editor.setAccessibleName(f"{category.get('name', key)} monthly amount")
                editor.setProperty("budgetCategory", key)
                editor.installEventFilter(self)
                editor.textChanged.connect(lambda text, selected=key: self._set_amount(selected, text))
                table.setCellWidget(row, 1, editor)
                self.amounts[key] = editor
                account_keys = category.get("accounts", [])
                names = [self.choices.get("accounts", {}).get(item, item) for item in account_keys]
                account_button = QPushButton(", ".join(names) if names else "Choose accounts")
                account_button.setToolTip(
                    ", ".join(names) if names else "Choose the accounts for this category"
                )
                account_button.setAccessibleName(f"Accounts for {category.get('name', key)}")
                account_button.clicked.connect(
                    lambda _checked=False, selected=key: self._edit_accounts(selected)
                )
                table.setCellWidget(row, 2, account_button)
                if kind == "savings":
                    pot = _PotSwitch()
                    pot.setObjectName("budgetPotToggle")
                    pot.setChecked(key in pots)
                    pot.setAccessibleName(f"Use {category.get('name', key)} as a savings pot")
                    pot.setAccessibleDescription("Independent choice; several savings pots can be selected.")
                    pot.setToolTip("Include this category as a savings pot. Several can be selected.")
                    pot.setProperty("budgetCategory", key)
                    pot.installEventFilter(self)
                    pot.toggled.connect(lambda checked, selected=key: self._set_pot(selected, checked))
                    table.setCellWidget(row, 3, _control_cell(pot))
                    residual = _ResidualChoice()
                    residual.setObjectName("budgetResidualChoice")
                    self._residual_group.addButton(residual)
                    residual.setChecked(key == savings.get("residual_category"))
                    residual.setEnabled(key in pots)
                    residual.setAccessibleName(f"Use {category.get('name', key)} for remaining savings")
                    residual.setAccessibleDescription("Exclusive choice; select one residual savings pot.")
                    residual.setToolTip("The one pot receiving remaining savings.")
                    residual.setProperty("budgetCategory", key)
                    residual.installEventFilter(self)
                    residual.clicked.connect(
                        lambda _checked=False, selected=key: self._set_residual(selected)
                    )
                    table.setCellWidget(row, 4, _control_cell(residual))
                table.setRowHeight(row, max(42, editor.sizeHint().height() + 12))
            row_height = sum(table.rowHeight(row) for row in range(len(keys))) or 42
            height = table.horizontalHeader().sizeHint().height() + row_height + table.frameWidth() * 2
            table.setFixedHeight(height)
            table.blockSignals(False)
        self._building = False
        self._select_key(self.selected_key)
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
            selected = self.selected_key in keys
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
        status, detail = "incomplete", ""
        totals = {kind: None for kind in KINDS}
        try:
            settings = BudgetSettings.model_validate(self.data)
            totals = budget_plan_totals(settings, self._month)
            for kind, label in self.totals.items():
                amount = totals.get(kind)
                label.setText("" if kind == "unknown" else "Not set" if amount is None else _money(amount))
            if self.categories:
                if all(value is not None for value in totals.values()):
                    income, expense, savings = (_cents(totals[kind]) for kind in KINDS)
                    difference = income - expense - savings
                    status = "balanced" if difference == 0 else "invalid"
                    detail = (
                        "" if difference == 0 else
                        f"Income has {_money(Decimal(difference) / 100)} left to allocate."
                        if difference > 0 else
                        f"Expenses and savings exceed income by {_money(Decimal(-difference) / 100)}."
                    )
        except (ValidationError, ValueError, TypeError) as error:
            status = "invalid"
            detail = _validation_message(error) if isinstance(error, ValidationError) else str(error)
            for label in self.totals.values():
                label.setText("Check amounts")
        self.summary.setText(detail)
        self.summary.setVisible(status == "invalid")
        self.summary.setProperty("tone", status)
        self.summary.style().unpolish(self.summary)
        self.summary.style().polish(self.summary)
        self._summary = {"month": self._month, "status": status, "detail": detail}
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
        if kind not in KINDS:
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
        if key not in self.categories:
            return
        self._adopt_month()
        self._remove_pot(key)
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
        Edit account mappings with a searchable checklist while retaining unavailable saved references.
        """
        self._select_key(key)
        category = self.categories[key]
        current = list(category.get("accounts", []))
        permitted = ALLOWED_ACCOUNTS.get(category.get("kind"), set())
        names, types = self.choices.get("accounts", {}), self.choices.get("account_types", {})
        available = [item for item in names if not types or types.get(item) in permitted or item in current]
        available += [item for item in current if item not in available]
        dialog = QDialog(self)
        dialog.setWindowTitle("Accounts for " + category.get("name", key))
        dialog.resize(440, 390)
        layout = QVBoxLayout(dialog)
        search = QLineEdit()
        search.setPlaceholderText("Search accounts")
        layout.addWidget(search)
        listing = QListWidget()
        for account_key in available:
            label = names.get(account_key, account_key + " (unavailable)")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, account_key)
            item.setToolTip(account_key)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if account_key in current else Qt.CheckState.Unchecked)
            listing.addItem(item)
        search.textChanged.connect(
            lambda text: [listing.item(index).setHidden(text.casefold() not in (
                listing.item(index).text() + " " + listing.item(index).data(Qt.ItemDataRole.UserRole)
            ).casefold()) for index in range(listing.count())]
        )
        layout.addWidget(listing)
        note = QLabel("Account mappings apply to every month.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel,
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            checked = [listing.item(index).data(Qt.ItemDataRole.UserRole) for index in range(listing.count())
                       if listing.item(index).checkState() == Qt.CheckState.Checked]
            selected = [item for item in current if item in checked]
            selected += [item for item in checked if item not in selected]
            if selected != current:
                self._adopt_month()
                self.categories[key]["accounts"] = selected
                self._build_tables()
                self.changed.emit()

    def _set_pot(self, key: str, checked: bool) -> None:
        """
        Choose virtual savings pots directly without introducing ledger accounts.
        """
        self._adopt_month()
        if checked:
            savings = deepcopy(self.data.get("savings") or {"residual_category": key, "category_keys": []})
            savings["category_keys"] = list(savings["category_keys"])
            if key not in savings["category_keys"]:
                savings["category_keys"].append(key)
            self.data["savings"] = savings
        else:
            self._remove_pot(key)
        self._build_tables()
        self.changed.emit()

    def _remove_pot(self, key: str) -> None:
        """
        Keep the remaining pot choices valid when a draft category leaves the pot list.
        """
        savings = self.data.get("savings")
        if not savings or key not in savings.get("category_keys", []):
            return
        remaining = [item for item in savings["category_keys"] if item != key]
        if not remaining:
            self.data["savings"] = None
        else:
            savings["category_keys"] = remaining
            if savings.get("residual_category") == key:
                savings["residual_category"] = remaining[0]

    def _set_residual(self, key: str) -> None:
        """
        Select the one configured pot receiving unallocated monthly savings.
        """
        savings = self.data.get("savings") or {}
        if key in savings.get("category_keys", []) and savings.get("residual_category") != key:
            self._adopt_month()
            savings["residual_category"] = key
            self._build_tables()
            self.changed.emit()


# %% 2. Table Controls ---------------------------------------------------------------------------------------
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


class _PotSwitch(QCheckBox):
    """
    Present an independent savings-pot choice as a compact switch with native checkbox behavior.
    """

    def __init__(self, parent=None) -> None:
        """
        Use a small fixed switch area while retaining Qt's keyboard and accessibility implementation.
        """
        super().__init__(parent)
        self.setFixedSize(36, 22)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self) -> QSize:
        """
        Reserve enough room for the track, sliding thumb and focus ring.
        """
        return QSize(36, 22)

    def hitButton(self, position) -> bool:
        """
        Make the complete compact switch respond to pointer activation.
        """
        return self.rect().contains(position)

    def paintEvent(self, event) -> None:
        """
        Draw a rounded track and white thumb without inheriting a tick-image stylesheet.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(gui_colour(self, "accent", "#82afa1")), 1.2))
            painter.drawRoundedRect(QRectF(0.7, 0.7, 34.6, 20.6), 10, 10)
        track = gui_colour(self, "accent" if self.isChecked() else "border", "#3b896c")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(track if self.isEnabled() else gui_colour(self, "disabled", "#dde3df")))
        painter.drawRoundedRect(QRectF(1, 2, 34, 18), 9, 9)
        painter.setBrush(QColor(gui_colour(self, "on_accent" if self.isChecked() else "surface", "#ffffff")))
        painter.drawEllipse(QRectF(19 if self.isChecked() else 3, 4, 14, 14))


class _ResidualChoice(QRadioButton):
    """
    Present the one residual savings choice as a radio dot with native exclusive-choice behavior.
    """

    def __init__(self, parent=None) -> None:
        """
        Keep the radio's drawing separate from shared checkbox tick styling.
        """
        super().__init__(parent)
        self.setFixedSize(22, 22)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self) -> QSize:
        """
        Reserve a centred radio circle and an outer keyboard-focus ring.
        """
        return QSize(22, 22)

    def hitButton(self, position) -> bool:
        """
        Let the full radio control area respond to pointer activation.
        """
        return self.rect().contains(position)

    def paintEvent(self, event) -> None:
        """
        Draw a standard circular outline and selected dot, preserving disabled and focus states.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(gui_colour(self, "accent", "#82afa1")), 1.2))
            painter.drawEllipse(QRectF(0.7, 0.7, 20.6, 20.6))
        colour = gui_colour(self, "icon" if self.isChecked() else "border", "#397a68")
        if not self.isEnabled():
            colour = gui_colour(self, "disabled", "#ced8d2")
        painter.setPen(QPen(QColor(colour), 1.2))
        painter.setBrush(QColor(gui_colour(self, "surface", "#ffffff")))
        painter.drawEllipse(QRectF(3, 3, 16, 16))
        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            colour = gui_colour(self, "icon" if self.isEnabled() else "disabled", "#397a68")
            painter.setBrush(QColor(colour))
            painter.drawEllipse(QRectF(7, 7, 8, 8))


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _control_cell(control: QWidget) -> QWidget:
    """
    Centre a compact savings control horizontally and vertically inside its table cell.
    """
    cell = QWidget()
    cell.setObjectName("budgetControlCell")
    layout = QHBoxLayout(cell)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
    layout.addWidget(control)
    return cell


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
