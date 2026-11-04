# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 GUI - Report Settings                                                #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Edit report presentation and named account mappings without exposing palette internals or equity roles.

The form retains the complete source document. The global Setup name and theme controls share this draft,
and only the existing configuration workflow writes it to disk.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListView, QListWidget, QMessageBox, QPushButton,
    QSizePolicy, QTableWidget, QTableWidgetItem,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.reports import DEFAULT_THEMES, ReportSettings, ReportTheme
from aurum.gui.icons import icon


# %% 1. Report Settings Editor -------------------------------------------------------------------------------
class ReportForm(QWidget):
    """
    Edit report appearance, ordered asset groups and account-based investment labels in one shared draft.
    """

    changed = Signal()

    def __init__(self, schema: dict, value: dict, choices: dict, parent: QWidget | None = None) -> None:
        """
        Build focused controls while preserving all unexposed configuration fields verbatim.
        """
        super().__init__(parent)
        self.data, self.choices = deepcopy(value), choices
        self.groups = [(name, list(keys)) for name, keys in self.data.get("asset_groups", {}).items()]
        self._group_account_names = dict(choices.get("asset_accounts", {}))
        for _, keys in self.groups:
            for key in keys:
                if key not in self._group_account_names:
                    self._group_account_names[key] = f"{key} (not configured as an asset)"
        self._group_account_keys = list(self._group_account_names)
        self._groups_edited = False
        self._investment_keys = list(self.data.get("account_investments", {}))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)
        directory_row = QHBoxLayout()
        directory_row.setSpacing(10)
        directory_row.addWidget(QLabel("Save reports in"))
        self.directory = QLineEdit(str(self.data.get("output_directory", "data/reports")))
        self.directory.setPlaceholderText("data/reports")
        self.directory.setAccessibleName("Report output folder")
        self.directory.textChanged.connect(lambda text: self._set_field("output_directory", text))
        directory_row.addWidget(self.directory, 1)
        choose = QToolButton()
        choose.setObjectName("reportFolderButton")
        choose.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        choose.setIcon(icon("folder", size=18))
        choose.setIconSize(QSize(18, 18))
        choose.setFixedSize(36, 36)
        choose.setStyleSheet("QToolButton#reportFolderButton { padding: 0; }")
        choose.setToolTip("Choose report folder")
        choose.setAccessibleName("Choose report folder")
        choose.clicked.connect(self._pick_directory)
        directory_row.addWidget(choose, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(directory_row)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("reportSettingsTabs")
        self.tabs.addTab(self._build_groups(), "Asset groups")
        self.tabs.addTab(self._build_investments(), "Account-based investments")
        layout.addWidget(self.tabs)

    def value(self) -> dict:
        """
        Return the complete draft for shared validation, retaining hidden settings and absent defaults.
        """
        result = deepcopy(self.data)
        if self._groups_edited:
            groups = {}
            for name, keys in self.groups:
                name = name.strip()
                if not name:
                    raise ValueError("Give every asset group a name.")
                if name in groups:
                    raise ValueError(f"Asset group names must be unique: {name}")
                groups[name] = list(keys)
            result["asset_groups"] = groups
        return result

    def owner_name(self) -> str:
        """
        Return the personal name for Setup's shared profile field without the Aurum brand prefix.
        """
        return ReportSettings(name=self.data.get("name", "Aurum")).owner_name

    def set_owner_name(self, name: str) -> None:
        """
        Update the shared profile draft only when its meaningful personal name changes.
        """
        name = ReportSettings(name=name.strip() or "Aurum").owner_name
        if name != self.owner_name():
            self._set_field("name", name or "Aurum")

    def theme_key(self) -> str:
        """
        Return the selected theme's stable configuration key.
        """
        return self.data.get("colour_scheme", "teal")

    def set_theme(self, key: str) -> None:
        """
        Update the global theme card's shared report draft without writing configuration.
        """
        if key not in self.theme_choices():
            raise ValueError(f"Unknown report theme: {key}")
        if key == self.theme_key():
            return
        self.data["colour_scheme"] = key
        self.changed.emit()

    def set_dark_mode(self, enabled: bool) -> None:
        """
        Keep the global appearance choice in this draft, preserving fixed themes' independent colours.
        """
        if not isinstance(enabled, bool):
            raise TypeError("Dark mode must be a boolean")
        self._set_field("dark_mode", enabled)

    def theme_choices(self) -> dict[str, str]:
        """
        List built-in and configured themes without editing or dropping custom palette definitions.
        """
        keys = dict.fromkeys((*DEFAULT_THEMES, *self.data.get("themes", {}), self.theme_key()))
        labels = self.choices.get("themes", {})
        return {key: labels.get(key, key.replace("_", " ").title()) for key in keys}

    def theme_info(self) -> dict:
        """
        Describe the selected palette and fixed appearance for Setup's matching theme preview.
        """
        key = self.theme_key()
        definition = self.data.get("themes", {}).get(key)
        theme = ReportTheme.model_validate(definition) if definition is not None else DEFAULT_THEMES.get(key)
        return {
            "key": key, "name": self.theme_choices()[key], "palette": tuple(theme.palette) if theme else (),
            "fixed": theme is not None and theme.fixed is not None,
            "dark_mode": bool(self.data.get("dark_mode", False)),
        }

    def view_state(self) -> dict:
        """
        Keep the active mapping and scroll position when Setup rebuilds a saved form.
        """
        account = self.group_accounts.currentRow()
        key = self._group_account_keys[account] if account >= 0 else None
        group = next((name for name, keys in self.groups if key in keys), None)
        investment = self.investments.currentRow()
        return {
            "tab": self.tabs.currentIndex(),
            "group_account": key, "group": group,
            "investment": self._investment_keys[investment] if investment >= 0 else None,
            "investments_scroll": self.investments.verticalScrollBar().value(),
        }

    def restore_view_state(self, state: dict) -> None:
        """
        Restore navigation without altering values or emitting a configuration change.
        """
        self.tabs.setCurrentIndex(max(0, min(1, state.get("tab", 0))))
        key = state.get("group_account")
        if key not in self._group_account_keys:
            key = next((keys[0] for name, keys in self.groups if name == state.get("group") and keys), None)
        if key in self._group_account_keys:
            self.group_accounts.selectRow(self._group_account_keys.index(key))
        if state.get("investment") in self._investment_keys:
            self.investments.selectRow(self._investment_keys.index(state["investment"]))
        self.investments.verticalScrollBar().setValue(state.get("investments_scroll", 0))

    def eventFilter(self, watched, event) -> bool:
        """
        Fit live mapping panels and select a row before its editor receives focus.

        Deferred sizing expires with the form when Setup replaces it during a refresh.
        """
        if watched is getattr(self, "investment_page", None) and event.type() in {
            QEvent.Type.Resize, QEvent.Type.Show,
        }:
            self._size_investments()
        if watched is getattr(self, "group_page", None) and event.type() in {
            QEvent.Type.Resize, QEvent.Type.Show,
        }:
            self._size_groups()
        group_table = getattr(self, "group_accounts", None)
        if (group_table is not None and watched in (group_table, group_table.horizontalHeader())
                and event.type() in {QEvent.Type.Resize, QEvent.Type.StyleChange, QEvent.Type.FontChange}):
            QTimer.singleShot(0, self, self._size_groups)
        table = getattr(self, "investments", None)
        if table is not None and watched in (table, table.horizontalHeader()) and event.type() in {
            QEvent.Type.Resize, QEvent.Type.StyleChange, QEvent.Type.FontChange,
        }:
            QTimer.singleShot(0, self, self._size_investments)
        if event.type() == QEvent.Type.FocusIn:
            key = watched.property("groupAccountKey")
            if key in self._group_account_keys:
                self.group_accounts.setCurrentCell(self._group_account_keys.index(key), 0)
            key = watched.property("investmentKey")
            if key in self._investment_keys:
                self.investments.setCurrentCell(self._investment_keys.index(key), 0)
        return super().eventFilter(watched, event)

    def minimumSizeHint(self) -> QSize:
        """
        Let a narrow viewport reduce the preferred investment-table width without adding side scrolling.
        """
        hint = super().minimumSizeHint()
        return QSize(min(380, hint.width()), hint.height())

    def _set_field(self, key: str, value) -> None:
        """
        Record a scalar edit and notify Setup only when the stored value changes.
        """
        defaults = {"output_directory": "data/reports", "dark_mode": False}
        if self.data.get(key, defaults.get(key)) != value:
            self.data[key] = value
            self.changed.emit()

    def _pick_directory(self) -> None:
        """
        Keep folders inside the workspace portable when choosing where PDFs will be generated.
        """
        workspace = Path(self.choices.get("workspace", Path.cwd())).resolve()
        current = Path(self.directory.text() or "data/reports").expanduser()
        current = current if current.is_absolute() else workspace / current
        selected = QFileDialog.getExistingDirectory(self, "Save reports in", str(current))
        if selected:
            path = Path(selected).resolve()
            try:
                path = path.relative_to(workspace)
            except ValueError:
                pass
            self.directory.setText(str(path))

    def _build_groups(self) -> QWidget:
        """
        Give every asset one direct report-group choice, with ungrouped accounts in Other Assets.
        """
        page = QWidget()
        self.group_page = page
        page.installEventFilter(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        self.group_panel = QWidget()
        self.group_panel.setObjectName("reportAssetGroupPanel")
        panel_layout = QVBoxLayout(self.group_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(10)
        controls = QHBoxLayout()
        controls.addStretch()
        self.manage_groups = QPushButton("Manage groups")
        self.manage_groups.setObjectName("reportManageGroups")
        self.manage_groups.setToolTip("Add, rename, reorder or remove report groups.")
        self.manage_groups.clicked.connect(self._manage_groups)
        controls.addWidget(self.manage_groups)
        panel_layout.addLayout(controls)
        self.group_accounts = QTableWidget(0, 2)
        self.group_accounts.setObjectName("reportAssetGroupTable")
        self.group_accounts.setAccessibleName("Asset report groups")
        self.group_accounts.setShowGrid(False)
        self.group_accounts.setHorizontalHeaderLabels(["Account", "Report group"])
        self.group_accounts.horizontalHeaderItem(1).setToolTip(
            "Other Assets leaves the account ungrouped. Choosing a group moves the account there."
        )
        self.group_accounts.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.group_accounts.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.group_accounts.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.group_accounts.verticalHeader().hide()
        self.group_accounts.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.group_accounts.horizontalHeader().setStyleSheet("QHeaderView::section { border-right: none; }")
        self.group_accounts.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.group_accounts.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.group_accounts.installEventFilter(self)
        self.group_accounts.horizontalHeader().installEventFilter(self)
        panel_layout.addWidget(self.group_accounts)
        layout.addWidget(self.group_panel, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self._refresh_groups()
        return page

    def _refresh_groups(self, selected: str | None = None) -> None:
        """
        Rebuild account choices without changing group membership or the order of saved account keys.
        """
        self.group_accounts.setRowCount(len(self._group_account_keys))
        for row, key in enumerate(self._group_account_keys):
            name = self._group_account_names[key]
            item = QTableWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(key)
            self.group_accounts.setItem(row, 0, item)
            choice = QComboBox()
            choice.setView(QListView(choice))
            choice.setProperty("groupAccountKey", key)
            choice.setAccessibleName(f"{name}: report group")
            choice.addItem("Other Assets", None)
            for group, _ in self.groups:
                label = f"{group} (configured)" if group == "Other Assets" else group
                choice.addItem(label, group)
            current = next((group for group, keys in self.groups if key in keys), None)
            choice.setCurrentIndex(choice.findData(current))
            choice.installEventFilter(self)
            choice.currentIndexChanged.connect(
                lambda index, account=key, combo=choice: self._assign_group(account, combo.itemData(index))
            )
            self.group_accounts.setCellWidget(row, 1, choice)
        if selected in self._group_account_keys:
            self.group_accounts.selectRow(self._group_account_keys.index(selected))
        self._size_groups()

    def _size_groups(self) -> None:
        """
        Keep account assignments compact and fully visible inside the outer Setup scroll area.
        """
        if not hasattr(self, "group_accounts"):
            return
        margins = self.group_page.layout().contentsMargins()
        available = max(1, self.group_page.width() - margins.left() - margins.right())
        width = min(740, available, max(600, int(available * 0.8)))
        self.group_panel.setFixedWidth(width)
        self.group_accounts.ensurePolished()
        self.group_accounts.horizontalHeader().ensurePolished()
        for row in range(self.group_accounts.rowCount()):
            choice = self.group_accounts.cellWidget(row, 1)
            choice.ensurePolished()
            self.group_accounts.setRowHeight(row, max(44, choice.sizeHint().height() + 12))
        rows = sum(self.group_accounts.rowHeight(row) for row in range(self.group_accounts.rowCount())) or 44
        height = self.group_accounts.horizontalHeader().sizeHint().height() + rows
        self.group_accounts.setFixedHeight(height + self.group_accounts.frameWidth() * 2)
        self.group_panel.setFixedHeight(self.group_panel.layout().sizeHint().height())

    def _assign_group(self, key: str, target: str | None) -> None:
        """
        Move one account in a single draft update, retaining the relative order of all unchanged members.
        """
        if key not in self._group_account_keys:
            return
        if target is not None and target not in [name for name, _ in self.groups]:
            return
        current = [name for name, keys in self.groups if key in keys]
        if current == ([target] if target is not None else []):
            return
        updated = []
        for name, keys in self.groups:
            members = [member for member in keys if member != key]
            if name == target:
                members.append(key)
            updated.append((name, members))
        self.groups = updated
        self._groups_edited = True
        self.changed.emit()

    def _manage_groups(self) -> None:
        """
        Apply accepted group edits to this page's draft; cancellation leaves every assignment untouched.
        """
        row = self.group_accounts.currentRow()
        selected = self._group_account_keys[row] if row >= 0 else None
        dialog = _GroupDialog(self.groups, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.groups == self.groups:
            return
        self.groups = deepcopy(dialog.groups)
        self._groups_edited = True
        self._refresh_groups(selected)
        self.changed.emit()

    def _build_investments(self) -> QWidget:
        """
        Keep whole-account investment inclusion, grouping and report labels together by stable account key.
        """
        page = QWidget()
        self.investment_page = page
        page.installEventFilter(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 14, 14, 14)
        self.investment_panel = QWidget()
        self.investment_panel.setObjectName("reportInvestmentPanel")
        self.investment_panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        panel_layout = QVBoxLayout(self.investment_panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(10)
        controls = QHBoxLayout()
        controls.addStretch()
        add = _action_button("Add investment account", "add")
        add.setToolTip(
            "Add a whole-account investment, such as Superannuation. "
            "Accounts with security holdings cannot also be included."
        )
        add.clicked.connect(self._add_investment)
        controls.addWidget(add)
        self.remove_investment = _action_button("Remove investment account", "remove")
        self.remove_investment.clicked.connect(self._remove_investment)
        controls.addWidget(self.remove_investment)
        panel_layout.addLayout(controls)
        self.investments = QTableWidget(0, 3)
        self.investments.setObjectName("reportInvestmentTable")
        self.investments.setShowGrid(False)
        self.investments.setHorizontalHeaderLabels(["Account", "Investment group", "Report label"])
        self.investments.horizontalHeaderItem(0).setToolTip(
            "Whole-account investment balances, such as Superannuation. "
            "Accounts with security holdings cannot also be included."
        )
        self.investments.horizontalHeaderItem(2).setToolTip(
            "Optional display label, such as UNISUPER. "
            "It does not rename the account or select a market price."
        )
        self.investments.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.investments.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.investments.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.investments.verticalHeader().hide()
        self.investments.verticalHeader().setDefaultSectionSize(44)
        self.investments.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.investments.horizontalHeader().setStyleSheet("QHeaderView::section { border-right: none; }")
        self.investments.installEventFilter(self)
        self.investments.horizontalHeader().installEventFilter(self)
        self.investments.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.investments.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.investments.itemSelectionChanged.connect(
            lambda: self.remove_investment.setEnabled(self.investments.currentRow() >= 0)
        )
        panel_layout.addWidget(self.investments)
        layout.addWidget(self.investment_panel, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self._refresh_investments()
        return page

    def _size_investments(self) -> None:
        """
        Centre a compact table panel and reserve its full row height for the outer Setup scroll area.
        """
        if not hasattr(self, "investments"):
            return
        margins = self.investment_page.layout().contentsMargins()
        available = max(1, self.investment_page.width() - margins.left() - margins.right())
        width = min(740, available, max(600, int(available * 0.8)))
        if self.investment_panel.minimumWidth() != width or self.investment_panel.maximumWidth() != width:
            self.investment_panel.setFixedWidth(width)
        self.investments.ensurePolished()
        self.investments.horizontalHeader().ensurePolished()
        for row in range(self.investments.rowCount()):
            editor = self.investments.cellWidget(row, 1)
            editor.ensurePolished()
            self.investments.setRowHeight(row, max(44, editor.sizeHint().height() + 12))
        rows = sum(self.investments.rowHeight(row) for row in range(self.investments.rowCount())) or 44
        height = self.investments.horizontalHeader().sizeHint().height() + rows
        height += self.investments.frameWidth() * 2
        if self.investments.minimumHeight() != height or self.investments.maximumHeight() != height:
            self.investments.setFixedHeight(height)
        panel_height = self.investment_panel.layout().totalHeightForWidth(width)
        if panel_height < 0:
            panel_height = self.investment_panel.layout().sizeHint().height()
        if (self.investment_panel.minimumHeight() != panel_height
                or self.investment_panel.maximumHeight() != panel_height):
            self.investment_panel.setFixedHeight(panel_height)

    def _refresh_investments(self, selected: str | None = None) -> None:
        """
        Rebuild named rows while keeping each text editor bound to its original account key.
        """
        accounts = self.choices.get("accounts", {})
        groups = self.data.get("account_investments", {})
        labels = self.data.get("investment_symbols", {})
        self._investment_keys = list(groups)
        self.investments.clearSelection()
        self.investments.setCurrentCell(-1, -1)
        self.investments.setRowCount(len(groups))
        for row, key in enumerate(self._investment_keys):
            name = accounts.get(key, key)
            item = QTableWidgetItem(name)
            item.setToolTip(name)
            self.investments.setItem(row, 0, item)
            for column, value, field in ((1, groups[key], "account_investments"),
                                         (2, labels.get(key, ""), "investment_symbols")):
                editor = QLineEdit(value)
                editor.setProperty("investmentKey", key)
                editor.setAccessibleName(f"{name}: {'investment group' if column == 1 else 'report label'}")
                editor.setPlaceholderText("e.g. Superannuation" if column == 1 else name)
                if column == 2:
                    editor.setToolTip(self.investments.horizontalHeaderItem(2).toolTip())
                editor.installEventFilter(self)
                editor.textChanged.connect(
                    lambda text, account=key, mapping=field: self._investment_changed(account, mapping, text)
                )
                self.investments.setCellWidget(row, column, editor)
                self.investments.setRowHeight(row, max(44, editor.sizeHint().height() + 12))
        if selected in self._investment_keys:
            self.investments.selectRow(self._investment_keys.index(selected))
        self.remove_investment.setEnabled(self.investments.currentRow() >= 0)
        self._size_investments()

    def _investment_changed(self, key: str, mapping: str, text: str) -> None:
        """
        Apply a display edit to its stable account, omitting an optional label when explicitly cleared.
        """
        current = self.data.get(mapping, {})
        if current.get(key, "") == text:
            return
        values = self.data.setdefault(mapping, {})
        if mapping == "investment_symbols" and not text:
            values.pop(key, None)
        else:
            values[key] = text
        self.changed.emit()

    def _add_investment(self) -> None:
        """
        Include a chosen account without deriving cost, financial movements or a market-data symbol.
        """
        accounts = self.choices.get("non_bank_asset_accounts", self.choices.get("asset_accounts", {}))
        available = [(key, name) for key, name in accounts.items() if key not in self._investment_keys]
        if not available:
            QMessageBox.information(
                self, "No available accounts", "Add a suitable asset account in Setup first.",
            )
            return
        labels = [f"{name} ({key})" for key, name in available]
        selected, accepted = QInputDialog.getItem(self, "Add investment account", "Account", labels, 0, False)
        if not accepted:
            return
        key, name = available[labels.index(selected)]
        self.data.setdefault("account_investments", {})[key] = name
        self._refresh_investments(key)
        self.changed.emit()

    def _remove_investment(self) -> None:
        """
        Remove report inclusion and its optional label together, leaving all accounting untouched.
        """
        row = self.investments.currentRow()
        if row < 0:
            return
        key = self._investment_keys[row]
        self.data["account_investments"].pop(key)
        self.data.get("investment_symbols", {}).pop(key, None)
        self._refresh_investments()
        self.changed.emit()


# %% 2. Report Group Management -----------------------------------------------------------------------------
class _GroupDialog(QDialog):
    """
    Edit a private copy of ordered report groups before applying it to the Setup draft.
    """

    def __init__(self, groups: list[tuple[str, list[str]]], parent: QWidget) -> None:
        """
        Offer focused group naming and ordering controls without changing the parent form.
        """
        super().__init__(parent)
        self.groups = deepcopy(groups)
        self._original_groups = deepcopy(groups)
        self.setObjectName("reportGroupsDialog")
        self.setWindowTitle("Manage report groups")
        self.resize(440, 380)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.listing = QListWidget()
        self.listing.setObjectName("reportGroupList")
        self.listing.setAccessibleName("Report groups in display order")
        self.listing.currentRowChanged.connect(self._select)
        layout.addWidget(self.listing, 1)
        controls = QHBoxLayout()
        add = _action_button("Add report group", "add")
        add.clicked.connect(self._add)
        controls.addWidget(add)
        self.remove = _action_button("Remove report group", "remove")
        self.remove.setToolTip("Remove this group. Its accounts move to Other Assets.")
        self.remove.clicked.connect(self._remove)
        controls.addWidget(self.remove)
        self.moves = {}
        for direction, symbol, label in ((-1, "up", "Move group up"), (1, "down", "Move group down")):
            button = _action_button(label, symbol)
            button.clicked.connect(lambda checked=False, offset=direction: self._move(offset))
            controls.addWidget(button)
            self.moves[direction] = button
        controls.addStretch()
        layout.addLayout(controls)
        layout.addWidget(QLabel("Group name"))
        self.name = QLineEdit()
        self.name.setAccessibleName("Report group name")
        self.name.textChanged.connect(self._rename)
        layout.addWidget(self.name)
        self.error = QLabel()
        self.error.setObjectName("reportGroupError")
        self.error.setWordWrap(True)
        self.error.hide()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Apply")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh(0)

    def _refresh(self, row: int) -> None:
        """
        Retain the selected group while refreshing names and order within the dialog.
        """
        self.listing.blockSignals(True)
        self.listing.clear()
        self.listing.addItems([name or "Unnamed group" for name, _ in self.groups])
        self.listing.setCurrentRow(min(row, len(self.groups) - 1))
        self.listing.blockSignals(False)
        self._select(self.listing.currentRow())

    def _select(self, row: int) -> None:
        """
        Show the selected name and disable unavailable removal or movement.
        """
        selected = 0 <= row < len(self.groups)
        self.remove.setEnabled(selected)
        self.name.setEnabled(selected)
        for direction, button in self.moves.items():
            button.setEnabled(selected and 0 <= row + direction < len(self.groups))
        self.name.blockSignals(True)
        self.name.setText(self.groups[row][0] if selected else "")
        self.name.blockSignals(False)

    def _rename(self, name: str) -> None:
        """
        Keep all members when the local group's display name changes.
        """
        row = self.listing.currentRow()
        if row >= 0:
            self.groups[row] = (name, self.groups[row][1])
            self.listing.item(row).setText(name or "Unnamed group")
            self.error.hide()

    def _add(self) -> None:
        """
        Append a named group to the dialog's private draft.
        """
        name, accepted = QInputDialog.getText(self, "Add report group", "Group name")
        if not accepted or not name.strip():
            return
        name = name.strip()
        if name in [existing.strip() for existing, _ in self.groups]:
            self.error.setText("Choose a unique group name.")
            self.error.show()
            return
        self.groups.append((name, []))
        self.error.hide()
        self._refresh(len(self.groups) - 1)
        self.name.setFocus()
        self.name.selectAll()

    def _remove(self) -> None:
        """
        Remove the local group; applying the dialog will leave its accounts ungrouped.
        """
        row = self.listing.currentRow()
        if row >= 0:
            self.groups.pop(row)
            self.error.hide()
            self._refresh(row)

    def _move(self, direction: int) -> None:
        """
        Reorder a whole group without changing the order of its members.
        """
        row = self.listing.currentRow()
        target = row + direction
        if 0 <= row < len(self.groups) and 0 <= target < len(self.groups):
            self.groups[row], self.groups[target] = self.groups[target], self.groups[row]
            self._refresh(target)

    def accept(self) -> None:
        """
        Require nonblank, unique names before handing the accepted group copy back to Setup.
        """
        if self.groups == self._original_groups:
            super().accept()
            return
        names = [name.strip() for name, _ in self.groups]
        if any(not name for name in names) or len(names) != len(set(names)):
            self.error.setText("Give every group a unique, nonblank name.")
            self.error.show()
            return
        self.groups = [(name, keys) for name, (_, keys) in zip(names, self.groups)]
        super().accept()


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _action_button(label: str, symbol: str) -> QToolButton:
    """
    Keep collection actions compact while exposing their full purpose to keyboard and screen-reader users.
    """
    button = QToolButton()
    button.setObjectName("setupIconAction")
    button.setAutoRaise(True)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setFixedSize(30, 30)
    size = 14 if symbol in {"up", "down"} else 18
    button.setIconSize(QSize(size, size))
    button.setStyleSheet("QToolButton#setupIconAction { padding: 0; }")
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setToolTip(label)
    button.setAccessibleName(label)
    button.setIcon(icon(symbol, size=size))
    return button
