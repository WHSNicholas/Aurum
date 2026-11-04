#----------------------------------------------------------------------------------------------------------------------#
#                                                        Aurum                                                         #
#                                             aurum - gui - setup.py                                                   #
#----------------------------------------------------------------------------------------------------------------------#
"""
Give Aurum a setup screen for accounts, securities, budget plans and report appearance.

All configuration uses ordinary forms. Saved definitions are validated together, while unsaved edits and
outside changes are protected before navigation or replacement. This screen never processes transactions.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from dataclasses import replace
from datetime import date
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListView, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QStyle, QStyleOptionComboBox, QStylePainter, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.reports import DEFAULT_THEMES
from aurum.finance.dates import reporting_month
from aurum.gui.budget import BudgetForm
from aurum.gui.catalogue import AccountsForm, SecuritiesForm
from aurum.gui.icons import icon
from aurum.gui.report_settings import ReportForm
from aurum.gui.theme_picker import ThemeComboBox
from aurum.gui.workspace import ChatGPTPage
from aurum.workflows.configuration import (
    CONFIGURATION_MODELS, configuration_schema, initialise_workspace, read_configuration, save_configuration,
)

HELP = {
    "accounts": "Select an account to edit its details, or add a new one. "
                "Close accounts with history instead of removing them.",
    "securities": "Add the equities and cryptocurrencies you hold before recording purchases. "
                  "Select an investment to edit its symbol and pricing details.",
    "budget": "",
    "reports": "",
    "chatgpt": "",
}


# %% 1. Setup Screen -----------------------------------------------------------------------------------------
class SetupPage(QWidget):
    """
    Keep all five Setup sections together with one persistent save bar and protected local changes.
    """

    changed = Signal()
    section_changed = Signal(str)
    section_labels_changed = Signal(dict)

    def __init__(self, workspace: Path, parent: QWidget | None = None) -> None:
        """
        Open saved configuration without writing files, and keep actions outside the scrolling forms.
        """
        super().__init__(parent)
        self.workspace = Path(workspace).resolve()
        self._dirty = set()
        self._busy = False
        self._missing = False
        self._current = 0
        self.leave_guard = None
        year, month = reporting_month(date.today())
        self._month = f"{year:04d}-{month:02d}"
        self.documents, self.forms, self.pages, self.errors = {}, {}, {}, {}
        self._refresh_version = None
        self.budget_header = None
        self.budget_copy = None
        self.budget_header_layout = QVBoxLayout()
        self.budget_header_layout.setContentsMargins(0, 0, 0, 0)
        self._messages = {}
        self.configuration_kinds = tuple(CONFIGURATION_MODELS)
        self.kinds = (*self.configuration_kinds, "chatgpt")
        self.chatgpt = ChatGPTPage(embedded=True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 20)
        header.setSpacing(0)
        heading = QHBoxLayout()
        self.title_row = heading
        title = QLabel("Setup")
        title.setObjectName("workspacePageTitle")
        heading.addWidget(title)
        heading.addStretch()
        header.addLayout(heading)
        profile = QHBoxLayout()
        profile.setContentsMargins(0, 0, 0, 0)
        profile.setSpacing(10)
        name_label = QLabel("&Name")
        name_label.setObjectName("workspacePageSubtitle")
        profile.addWidget(name_label)
        self.owner_name = QLineEdit()
        self.owner_name.setObjectName("setupOwner")
        self.owner_name.setPlaceholderText("Your name")
        self.owner_name.setMinimumWidth(200)
        self.owner_name.setMaximumWidth(400)
        self.owner_name.setAccessibleName("Name")
        self.owner_name.setToolTip("Used throughout Aurum and in generated reports. Save Setup changes to apply.")
        name_label.setBuddy(self.owner_name)
        self.owner_name.textEdited.connect(self._edit_owner)
        profile.addWidget(self.owner_name)
        profile.addStretch()
        header.addLayout(profile)
        layout.addLayout(header)
        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.account_value, self.account_detail = self._summary_card(cards, "ACCOUNTS", "wallet")
        self.budget_value, self.budget_detail = self._summary_card(cards, "BUDGET", "savings", flow=True)
        theme_card = QFrame()
        theme_card.setObjectName("setupSummary")
        theme_card.setFixedHeight(132)
        theme_layout = QVBoxLayout(theme_card)
        theme_layout.setContentsMargins(16, 18, 16, 6)
        theme_layout.setSpacing(4)
        theme_icon = QLabel()
        theme_icon.setPixmap(icon("reports", "#3f7168", 20).pixmap(20, 20))
        theme_icon.setFixedSize(20, 20)
        theme_layout.addWidget(theme_icon, 0, Qt.AlignmentFlag.AlignHCenter)
        theme_label = QLabel("THEME")
        theme_label.setObjectName("setupSummaryValue")
        theme_label.setFixedHeight(30)
        theme_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme_layout.addWidget(theme_label)
        theme_controls = QWidget()
        theme_controls.setObjectName("setupThemeControls")
        theme_controls.setFixedHeight(50)
        controls_layout = QVBoxLayout(theme_controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(2)
        controls_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.theme = ThemeComboBox()
        self.theme.setFixedWidth(216)
        self.theme.setFixedHeight(26)
        self.theme.setObjectName("setupTheme")
        self.theme.setAccessibleName("Theme")
        self.theme.currentIndexChanged.connect(self._edit_theme)
        controls_layout.addWidget(self.theme, 0, Qt.AlignmentFlag.AlignHCenter)
        swatches = QHBoxLayout()
        swatches.setSpacing(4)
        swatches.addStretch()
        self.theme_swatches = []
        for _ in range(5):
            swatch = QLabel()
            swatch.setFixedSize(22, 6)
            swatches.addWidget(swatch)
            self.theme_swatches.append(swatch)
        self.theme_appearance = QLabel()
        self.theme_appearance.setObjectName("overviewCaption")
        swatches.addWidget(self.theme_appearance)
        self.appearance = _CentredComboBox()
        self.appearance.setObjectName("setupAppearance")
        self.appearance.setFixedHeight(18)
        self.appearance.setView(QListView(self.appearance))
        self.appearance.addItem("Light", False)
        self.appearance.addItem("Dark", True)
        self.appearance.setAccessibleName("Report appearance")
        self.appearance.currentIndexChanged.connect(self._edit_appearance)
        swatches.addWidget(self.appearance)
        swatches.addStretch()
        controls_layout.addLayout(swatches)
        theme_layout.addWidget(theme_controls)
        theme_layout.addStretch()
        cards.addWidget(theme_card, 1)
        layout.addLayout(cards)
        self.notice = QFrame()
        self.notice.setObjectName("setupNotice")
        notice_layout = QHBoxLayout(self.notice)
        notice_layout.setContentsMargins(16, 12, 16, 12)
        self.notice_message = QLabel()
        self.notice_message.setTextFormat(Qt.TextFormat.PlainText)
        self.notice_message.setWordWrap(True)
        notice_layout.addWidget(self.notice_message, 1)
        self.initialise_button = QPushButton("Create missing workspace files")
        self.initialise_button.setProperty("primary", True)
        self.initialise_button.clicked.connect(self._initialise)
        notice_layout.addWidget(self.initialise_button)
        layout.addWidget(self.notice)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("setupTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().hide()
        self.tabs.currentChanged.connect(self._change_tab)
        layout.addWidget(self.tabs, 1)
        for kind in self.kinds:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, 8, 0, 0)
            page_layout.setSpacing(12)
            section_title = QLabel(_section_name(kind))
            section_title.setObjectName("setupSectionTitle")
            section_heading = QHBoxLayout()
            section_heading.addWidget(section_title)
            section_heading.addStretch()
            page_layout.addLayout(section_heading)
            description = QLabel(HELP[kind])
            description.setObjectName("muted")
            description.setWordWrap(True)
            description.setVisible(bool(HELP[kind]))
            page_layout.addWidget(description)
            if kind == "budget":
                self.budget_actions_layout = section_heading
                page_layout.addLayout(self.budget_header_layout)
                page_layout.addSpacing(16)
            error = QLabel()
            error.setObjectName("setupError")
            error.setTextFormat(Qt.TextFormat.PlainText)
            error.setWordWrap(True)
            error.hide()
            page_layout.addWidget(error)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            if kind == "budget":
                scroll.setMinimumHeight(160)
            page_layout.addWidget(scroll, 1)
            self.pages[kind], self.errors[kind] = scroll, error
            self.tabs.addTab(page, _section_name(kind))
            if kind == "chatgpt":
                scroll.setWidget(self.chatgpt)

        footer = QFrame()
        footer.setObjectName("setupFooter")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(0, 14, 0, 0)
        self.save_status = QLabel("No unsaved changes")
        self.save_status.setObjectName("muted")
        self.save_status.setWordWrap(True)
        actions.addWidget(self.save_status, 1)
        self.reload_button = QToolButton()
        self.reload_button.setObjectName("setupIconAction")
        self.reload_button.setIcon(icon("refresh", size=18))
        self.reload_button.setIconSize(QSize(18, 18))
        self.reload_button.setFixedSize(36, 36)
        self.reload_button.setAccessibleName("Reload current settings")
        self.reload_button.setToolTip("Reload this section from its saved settings.")
        self.reload_button.clicked.connect(lambda: self._reload(self.kinds[self._current]))
        actions.addWidget(self.reload_button)
        self.save_button = QToolButton()
        self.save_button.setObjectName("setupIconAction")
        self.save_button.setIcon(icon("save", "#ffffff", size=18))
        self.save_button.setIconSize(QSize(18, 18))
        self.save_button.setFixedSize(36, 36)
        self.save_button.setToolTip("Save Setup changes")
        self.save_button.setAccessibleName("Save Setup changes")
        self.save_button.setProperty("primary", True)
        self.save_button.clicked.connect(self._save_all)
        actions.addWidget(self.save_button)
        layout.addWidget(footer)
        self.chatgpt.changed.connect(self._update_labels)
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        """
        Reload changed settings, retaining clean forms and unfinished edits when their files are unchanged.
        """
        if self._dirty and not force:
            return
        version = self._configuration_version()
        if not force and version is not None and version == self._refresh_version:
            return
        self._dirty.clear()
        self._messages.clear()
        choices = self._choices()
        self._update_workspace_notice(choices)
        for kind in self.configuration_kinds:
            self._load(kind, choices)
        # Only reuse a complete set of forms read from the same on-disk configuration snapshot.
        originals = dict(zip(self.configuration_kinds, version[:-1])) if version is not None else {}
        self._refresh_version = version if all(
            kind in self.forms and self.documents[kind].originals == originals
            for kind in self.configuration_kinds
        ) else None
        self._update_labels()

    def _configuration_version(self) -> tuple | None:
        """
        Compare the small settings files exactly, including external edits and missing workspace files.
        """
        try:
            paths = [self.workspace / "config" / f"{kind}.yaml" for kind in self.configuration_kinds]
            return (*[path.read_bytes() if path.is_file() else None for path in paths],
                    (self.workspace / "data" / "aurum.sqlite3").is_file())
        except OSError:
            return None

    def set_reporting_month(self, month: str) -> None:
        """
        Follow the desktop month for a clean budget draft without saving or replacing local edits.
        """
        if month == self._month:
            return
        self._month = month
        if "budget" in self.forms and "budget" not in self._dirty:
            self.forms["budget"].set_reporting_month(month)
        self._update_summary()

    def current_section(self) -> str:
        """
        Return the configuration section selected through the main sidebar.
        """
        return self.kinds[self._current]

    def select_section(self, kind: str) -> bool:
        """
        Request a section through the same unsaved-change guard as the original tab navigation.
        """
        if kind not in self.kinds or self._busy:
            return False
        self.tabs.setCurrentIndex(self.kinds.index(kind))
        return self.current_section() == kind

    def save_changes(self) -> bool:
        """
        Save all edited sections, keeping unsuccessful drafts visible and acknowledging partial saves.
        """
        if self._busy or (self._missing and self._dirty):
            return False
        saved = []
        for kind in ("accounts", "securities", "reports", "budget"):
            if kind not in self._dirty:
                continue
            if not self._save(kind):
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(self.kinds.index(kind))
                self.tabs.blockSignals(False)
                self._current = self.kinds.index(kind)
                self.section_changed.emit(kind)
                self._update_labels()
                if saved:
                    self.save_status.setText(
                        ", ".join(item.title() for item in saved) + " saved. Correct the remaining changes."
                    )
                return False
            saved.append(kind)
        if saved:
            self.save_status.setText("Changes saved")
        return True

    def _save_all(self) -> bool:
        """
        Save configuration first, then submit a staged model through the existing asynchronous action.
        """
        if not self.save_changes():
            return False
        if self.chatgpt.has_unsaved_changes():
            self.chatgpt.save_changes()
            return False
        return True

    def _summary_card(
        self, row: QHBoxLayout, title: str, symbol: str, *, flow: bool = False,
    ) -> tuple[QLabel, QLabel]:
        """
        Create a compact read-only summary with a local icon and enough room for account breakdowns.
        """
        frame = QFrame()
        frame.setObjectName("setupSummary")
        frame.setFixedHeight(132)
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        box = QVBoxLayout(frame)
        box.setContentsMargins(16, 18, 16, 6)
        box.setSpacing(4)
        emblem = QLabel()
        emblem.setPixmap(icon(symbol, "#3f7168", 20).pixmap(20, 20))
        emblem.setFixedSize(20, 20)
        box.addWidget(emblem, 0, Qt.AlignmentFlag.AlignHCenter)
        value = QLabel(title)
        value.setObjectName("setupSummaryValue")
        value.setFixedHeight(30)
        value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value.setWordWrap(True)
        box.addWidget(value)
        detail = _TwoLineSummary() if flow else QLabel()
        detail.setObjectName("setupSummaryDetail")
        detail.setFixedHeight(50)
        detail.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        detail.setWordWrap(not flow)
        box.addWidget(detail)
        box.addStretch()
        row.addWidget(frame, 1)
        return value, detail

    def _update_summary(self) -> None:
        """
        Reflect current drafts in the account and budget summaries and keep both theme controls in sync.
        """
        account_form = self.forms.get("accounts")
        values = account_form.value() if account_form is not None else []
        counts = {kind: 0 for kind in ("asset", "liability", "equity", "income", "expense")}
        for item in values:
            if item.get("account_type") in counts:
                counts[item["account_type"]] += 1
        self.account_detail.setText(
            f"{counts['asset']} assets · {counts['liability']} liabilities · {counts['equity']} equity\n"
            f"{counts['income']} income · {counts['expense']} expenses"
            if account_form is not None else "Unavailable"
        )
        budget = self.forms.get("budget")
        info = budget.summary_info() if budget is not None else {}
        labels = {"balanced": "Ready", "incomplete": "Not set", "invalid": "Needs attention"}
        month = info.get("month") or self._month
        month_name = date.fromisoformat(month + "-01").strftime("%B %Y") if month else ""
        allocation_text = " · ".join(
            f"{info.get(kind, '—')} {label}" for kind, label in (
                ("income", "Income"), ("expense", "Expenses"), ("savings", "Savings"),
            )
        )
        self.budget_detail.set_summary(allocation_text)
        self.budget_value.setToolTip(f"{month_name} · {labels.get(info.get('status'), 'Unavailable')}")
        self.budget_detail.setToolTip("\n".join(filter(None, (allocation_text, info.get("detail", "")))))
        reports = self.forms.get("reports")
        if reports is None:
            return
        if not self.owner_name.hasFocus():
            self.owner_name.setText(reports.owner_name())
        choices = reports.theme_choices()
        selected = reports.theme_key()
        self.theme.blockSignals(True)
        if [self.theme.itemData(index) for index in range(self.theme.count())] != list(choices):
            self.theme.clear()
            for key, label in choices.items():
                self.theme.addItem(label, key)
        self.theme.setCurrentIndex(self.theme.findData(selected))
        self.theme.blockSignals(False)
        self.theme.set_theme_palettes({
            key: (reports.data.get("themes", {}).get(key) or {}).get("palette")
            or (DEFAULT_THEMES[key].palette if key in DEFAULT_THEMES else ()) for key in choices
        })
        theme = reports.theme_info()
        palette = theme.get("palette") or []
        for index, swatch in enumerate(self.theme_swatches):
            swatch.setVisible(bool(palette))
            colour = QColor(palette[min(index, len(palette) - 1)]) if palette else QColor("#dce5df")
            swatch.setStyleSheet(f"background: {colour.name()}; border-radius: 3px;")
        fixed = bool(theme.get("fixed"))
        self.theme_appearance.setText("Fixed palette" if fixed else "")
        self.theme_appearance.setVisible(fixed)
        self.appearance.setVisible(not fixed)
        self.appearance.blockSignals(True)
        self.appearance.setCurrentIndex(int(bool(theme.get("dark_mode"))))
        self.appearance.blockSignals(False)

    def _edit_appearance(self, index: int) -> None:
        """
        Stage report page appearance beside its theme, without exposing palette settings.
        """
        reports = self.forms.get("reports")
        if reports is not None and not self._busy:
            reports.set_dark_mode(bool(self.appearance.itemData(index)))

    def _edit_owner(self, value: str) -> None:
        """
        Keep the global name in the same report draft used by the explicit Save changes action.
        """
        reports = self.forms.get("reports")
        if reports is not None and not self._busy:
            reports.set_owner_name(value)

    def _edit_theme(self, index: int) -> None:
        """
        Apply the summary-card theme choice to the Reports form without saving automatically.
        """
        reports = self.forms.get("reports")
        key = self.theme.itemData(index)
        if reports is not None and key is not None and not self._busy:
            reports.set_theme(key)

    def _update_workspace_notice(self, choices: dict) -> None:
        """
        Recheck missing files and dependencies so external repairs are visible when settings are reloaded.
        """
        missing_config = any(not (self.workspace / "config" / f"{kind}.yaml").is_file()
                             for kind in self.configuration_kinds)
        missing_database = not (self.workspace / "data" / "aurum.sqlite3").is_file()
        self._missing = missing_config or missing_database
        self.initialise_button.setVisible(self._missing)
        notices = []
        if missing_config:
            notices.append("Create the missing workspace files, then add your accounts.")
        elif missing_database:
            notices.append("Create the missing database to start recording activity. "
                           "Your existing settings will be kept.")
        if not missing_config:
            try:
                reports = read_configuration(self.workspace, "reports").data
                roles = {
                    "capital_account": "capital", "retained_earnings_account": "retained_earnings",
                    "unrealised_gains_account": "unrealised_capital_gains",
                }
                absent = [
                    reports.get(field, default).replace("_", " ").title()
                    for field, default in roles.items()
                    if reports.get(field, default) not in choices["equity_accounts"]
                ]
                if absent:
                    notices.append(
                        "Reports need these equity accounts: " + ", ".join(absent) + ". "
                        "Restore the original accounts before saving your workspace settings."
                    )
            except (ValueError, OSError, TypeError, AttributeError):
                pass
        self.notice_message.setText("\n".join(notices))
        self.notice.setVisible(bool(notices))

    def is_dirty(self) -> bool:
        """
        Tell the main window whether navigation might discard configuration edits.
        """
        return bool(self._dirty) or self.chatgpt.has_unsaved_changes()

    def has_unsaved_changes(self) -> bool:
        """
        Expose the main window's common unsaved-edit interface.
        """
        return self.is_dirty()

    def discard_changes(self) -> None:
        """
        Reload saved files after the user explicitly discards form changes.
        """
        self.chatgpt.discard_changes()
        self.refresh(force=True)

    def maybe_leave(self) -> bool:
        """
        Ask to save, discard or keep editing before leaving a dirty setup section.
        """
        if not self.is_dirty():
            return True
        answer = QMessageBox.question(
            self, "Unsaved setup changes", "Save your configuration changes before continuing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
            QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Cancel:
            return False
        if answer == QMessageBox.StandardButton.Discard:
            self.discard_changes()
            return True
        return self._save_all()

    def set_busy(self, busy: bool) -> None:
        """
        Suspend settings changes while the current workspace job uses saved configuration.
        """
        self._busy = busy
        self._update_labels()

    def _load(self, kind: str, choices: dict) -> None:
        """
        Replace a form from disk, retaining its view state and clearing stale controls if loading fails.
        """
        previous = self.forms.get(kind)
        state = previous.view_state() if hasattr(previous, "view_state") else None
        position = self.pages[kind].verticalScrollBar().value()
        try:
            document = read_configuration(self.workspace, kind)
            editor = {
                "accounts": AccountsForm, "securities": SecuritiesForm,
                "budget": BudgetForm, "reports": ReportForm,
            }[kind]
            form = editor(configuration_schema(kind), document.data, {**choices, "configuration_kind": kind})
            if kind == "budget":
                form.set_reporting_month(self._month)
                form.summary_changed.connect(self._update_summary)
            form.changed.connect(lambda selected=kind: self._mark_dirty(selected))
        except (ValueError, OSError, TypeError, KeyError) as error:
            if kind == "budget" and self.budget_header is not None:
                self.budget_header_layout.removeWidget(self.budget_header)
                self.budget_header.hide()
                self.budget_header.deleteLater()
                self.budget_header = None
            if kind == "budget" and self.budget_copy is not None:
                self.budget_actions_layout.removeWidget(self.budget_copy)
                self.budget_copy.hide()
                self.budget_copy.deleteLater()
                self.budget_copy = None
            old = self.pages[kind].takeWidget()
            if old is not None:
                old.deleteLater()
            self.forms.pop(kind, None)
            self.documents.pop(kind, None)
            self._show_error(kind, str(error))
            return
        old = self.pages[kind].takeWidget()
        if old is not None:
            old.deleteLater()
        self.pages[kind].setWidget(form)
        if kind == "budget":
            if self.budget_header is not None:
                self.budget_header_layout.removeWidget(self.budget_header)
                self.budget_header.hide()
                self.budget_header.deleteLater()
            self.budget_header = form.header
            self.budget_header_layout.addWidget(self.budget_header)
            self.budget_header.show()
            if self.budget_copy is not None:
                self.budget_actions_layout.removeWidget(self.budget_copy)
                self.budget_copy.hide()
                self.budget_copy.deleteLater()
            self.budget_copy = form.copy_month
            self.budget_copy.setObjectName("budgetCopy")
            self.budget_actions_layout.addWidget(self.budget_copy)
            self.budget_copy.show()
        self.forms[kind], self.documents[kind] = form, document
        if kind == "reports":
            self.owner_name.setText(form.owner_name())
        self.errors[kind].clear()
        self.errors[kind].hide()
        if state is not None:
            QTimer.singleShot(0, lambda: self._restore_view(kind, form, state, position))

    def _restore_view(self, kind: str, form: QWidget, state: dict, position: int) -> None:
        """
        Restore the selected item and scroll only if this is still the currently loaded form.
        """
        if self.forms.get(kind) is form:
            form.restore_view_state(state)
            self.pages[kind].verticalScrollBar().setValue(position)
            self._update_summary()

    def _choices(self) -> dict:
        """
        Build readable reference pickers from saved settings without generating UUIDs.
        """
        choices = {
            "accounts": {}, "asset_accounts": {}, "non_bank_asset_accounts": {}, "equity_accounts": {},
            "savings_categories": {}, "account_types": {}, "fixed_equity_accounts": {},
        }
        choices["workspace"] = self.workspace
        choices["themes"] = {key: key.replace("_", " ").title() for key in DEFAULT_THEMES}
        try:
            for item in read_configuration(self.workspace, "accounts").data:
                if not isinstance(item, dict) or not item.get("key") or not item.get("name"):
                    continue
                choices["accounts"][item["key"]] = item["name"]
                kind = item.get("account_type")
                choices["account_types"][item["key"]] = kind
                if kind in {"asset", "equity"}:
                    choices[f"{kind}_accounts"][item["key"]] = item["name"]
                if kind == "asset" and not item.get("bank_details"):
                    choices["non_bank_asset_accounts"][item["key"]] = item["name"]
            budget = read_configuration(self.workspace, "budget").data
            choices["categories"] = {
                key: category.get("name", key) for key, category in budget.get("categories", {}).items()
            }
            choices["savings_categories"] = {
                key: category.get("name", key) for key, category in budget.get("categories", {}).items()
                if category.get("kind") == "savings"
            }
            reports = read_configuration(self.workspace, "reports").data
            choices["fixed_equity_accounts"] = {
                reports.get(field, default): label for field, default, label in (
                    ("capital_account", "capital", "Capital"),
                    ("retained_earnings_account", "retained_earnings", "Retained Earnings"),
                    ("unrealised_gains_account", "unrealised_capital_gains", "Unrealised Capital Gains"),
                )
            }
            choices["themes"].update({
                key: key.replace("_", " ").title() for key in reports.get("themes", {})
            })
        except (ValueError, OSError, TypeError, AttributeError):
            pass
        return choices

    def _mark_dirty(self, kind: str) -> None:
        """
        Show an unsaved state without validating or writing while the user types.
        """
        self._dirty.add(kind)
        self._messages.pop(kind, None)
        self.errors[kind].clear()
        self.errors[kind].hide()
        self._update_labels()

    def _update_labels(self) -> None:
        """
        Keep tab markers, save state and action availability consistent with the selected section.
        """
        for index, kind in enumerate(self.kinds):
            dirty = self.chatgpt.has_unsaved_changes() if kind == "chatgpt" else kind in self._dirty
            self.tabs.setTabText(index, _section_name(kind) + (" *" if dirty else ""))
        kind = self.kinds[self._current]
        self.save_button.setEnabled(
            self.is_dirty() and not self._busy and (not self._missing or not self._dirty)
        )
        self.reload_button.setEnabled(not self._busy)
        self.reload_button.setToolTip(
            "Restore the saved model selection" if kind == "chatgpt" else "Reload this section's settings"
        )
        self.tabs.setEnabled(not self._busy)
        for selected in self.configuration_kinds:
            self.pages[selected].setEnabled(not self._busy and not self._missing)
        self.initialise_button.setEnabled(not self._busy)
        self.owner_name.setEnabled(not self._busy and not self._missing and "reports" in self.forms)
        self.theme.setEnabled(not self._busy and not self._missing and "reports" in self.forms)
        self.appearance.setEnabled(not self._busy and not self._missing and "reports" in self.forms)
        dirty_kinds = [item for item in self.configuration_kinds if item in self._dirty]
        if self.chatgpt.has_unsaved_changes():
            dirty_kinds.append("chatgpt")
        self.save_status.setText(
            "Check the settings above" if self.errors[kind].text() else
            "Unsaved: " + ", ".join(_section_name(item) for item in dirty_kinds)
            if dirty_kinds else self._messages.get(kind, "No unsaved changes")
        )
        self._update_summary()
        self.section_labels_changed.emit({
            item: _section_name(item) + (" *" if item in dirty_kinds else "") for item in self.kinds
        })

    def _show_error(self, kind: str, message: str) -> None:
        """
        Keep actionable errors beside their form instead of mixing them with successful save messages.
        """
        self.errors[kind].setText(message)
        self.errors[kind].show()

    def _save(self, kind: str) -> bool:
        """
        Persist validated settings through the shared service, preserving local input when saving fails.
        """
        if self._busy or self._missing or kind not in self.forms or kind not in self.documents:
            return False
        if kind not in self._dirty:
            return True
        try:
            document = save_configuration(self.documents[kind], self.forms[kind].value())
        except (ValueError, OSError, TypeError) as error:
            self._show_error(kind, _configuration_error(error))
            self._update_labels()
            return False
        self.documents[kind] = document
        for other, original in tuple(self.documents.items()):
            if other != kind:
                self.documents[other] = replace(
                    original, originals={**original.originals, kind: document.originals[kind]},
                )
        self._dirty.discard(kind)
        if self._dirty:
            self._load(kind, self._choices())
        else:
            self.refresh(force=True)
        self._messages[kind] = kind.title() + " saved"
        self._update_labels()
        self.changed.emit()
        return True

    def _reload(self, kind: str) -> None:
        """
        Replace the current section from disk only after any local changes have been explicitly discarded.
        """
        if self._busy:
            return
        if kind == "chatgpt":
            if self.chatgpt.has_unsaved_changes() and QMessageBox.question(
                self, "Discard model selection?", "Restore the saved processing model?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) == QMessageBox.StandardButton.Discard:
                self.chatgpt.discard_changes()
            return
        if kind in self._dirty and QMessageBox.question(
            self, "Discard local changes?", "Reloading replaces your unsaved changes in this section.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Discard:
            return
        self._dirty.discard(kind)
        self._messages.pop(kind, None)
        if self._dirty:
            choices = self._choices()
            self._update_workspace_notice(choices)
            self._load(kind, choices)
            self._update_labels()
        else:
            self.refresh(force=True)

    def _change_tab(self, index: int) -> None:
        """
        Protect unsaved changes before switching to a different configuration section.
        """
        if index == self._current:
            return
        allowed = self.leave_guard(
            lambda: self.select_section(self.kinds[index]),
        ) if self.leave_guard is not None else self.maybe_leave()
        if not allowed:
            self.tabs.blockSignals(True)
            self.tabs.setCurrentIndex(self._current)
            self.tabs.blockSignals(False)
            return
        self._current = index
        self._update_labels()
        self.section_changed.emit(self.current_section())

    def _initialise(self) -> None:
        """
        Create missing workspace files after protecting any edits in the current forms.
        """
        if self._busy:
            return
        allowed = self.leave_guard(self._initialise) if self.leave_guard is not None else self.maybe_leave()
        if not allowed:
            return
        try:
            initialise_workspace(self.workspace)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "Setup could not finish", str(error))
            return
        self.refresh(force=True)
        self.changed.emit()


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _section_name(kind: str) -> str:
    """
    Use product spelling consistently in the sidebar, section titles and unsaved-state messages.
    """
    return "ChatGPT" if kind == "chatgpt" else kind.title()


class _TwoLineSummary(QLabel):
    """
    Flow compact totals across at most two lines while retaining the full wording for accessibility.
    """

    def __init__(self, parent=None) -> None:
        """
        Keep the full summary separate from its width-dependent presentation.
        """
        super().__init__(parent)
        self._summary_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def set_summary(self, text: str) -> None:
        """
        Update the visible totals without inserting a third line into the summary card.
        """
        self._summary_text = text
        self.setAccessibleName(text)
        self._fit_text()

    def resizeEvent(self, event) -> None:
        """
        Reflow the existing totals as the card becomes wider or narrower.
        """
        super().resizeEvent(event)
        self._fit_text()

    def _fit_text(self) -> None:
        """
        Wrap at ordinary spaces, keeping monetary separators intact and eliding only extreme overflow.
        """
        metrics = self.fontMetrics()
        width = max(1, self.contentsRect().width())
        remaining = self._summary_text.split(" ")
        first = []
        while remaining and metrics.horizontalAdvance(" ".join((*first, remaining[0]))) <= width:
            first.append(remaining.pop(0))
        if not first and remaining:
            first.append(remaining.pop(0))
        if first and first[-1] == "·":
            first.pop()
        if remaining and remaining[0] == "·":
            remaining.pop(0)
        lines = [metrics.elidedText(" ".join(first), Qt.TextElideMode.ElideRight, width)]
        if remaining:
            lines.append(metrics.elidedText(" ".join(remaining), Qt.TextElideMode.ElideRight, width))
        self.setText("\n".join(lines))


class _CentredComboBox(QComboBox):
    """
    Centre the selected theme text while retaining the standard popup, arrow and keyboard controls.
    """

    def paintEvent(self, event) -> None:
        """
        Centre theme names on the whole card while retaining space for compact appearance labels.
        """
        painter = QStylePainter(self)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, option)
        rectangle = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxEditField, self,
        )
        if self.objectName() == "setupTheme":
            inset = max(rectangle.left(), self.width() - rectangle.right() - 1)
            rectangle.setLeft(inset)
            rectangle.setRight(self.width() - inset - 1)
        group = QPalette.ColorGroup.Active if self.isEnabled() else QPalette.ColorGroup.Disabled
        painter.setPen(self.palette().color(group, QPalette.ColorRole.Text))
        text = self.fontMetrics().elidedText(option.currentText, Qt.TextElideMode.ElideRight, rectangle.width())
        painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, text)


def _configuration_error(error: Exception) -> str:
    """
    Show the fields needing correction without exposing the validator's full input dumps.
    """
    if not isinstance(error, ValidationError):
        return str(error)
    problems = error.errors(include_input=False)
    messages = []
    for problem in problems[:3]:
        location = " → ".join(str(part) for part in problem["loc"]) or "Settings"
        messages.append(f"{location}: {problem['msg']}")
    if len(problems) > 3:
        messages.append(f"Correct these first; {len(problems) - 3} more fields also need attention.")
    return "\n".join(messages)
