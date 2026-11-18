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
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal, localcontext, ROUND_HALF_UP
from html import escape
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette, QTextDocument
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QListView, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QStyle, QStyleOptionComboBox, QStylePainter, QTabWidget, QToolButton,
    QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.reports import DEFAULT_THEMES, ReportSettings
from aurum.finance.dates import reporting_month
from aurum.gui.bank_feeds import BankConnectionPanel
from aurum.gui.budget import BudgetForm
from aurum.gui.catalogue import AccountsForm, SecuritiesForm
from aurum.gui.controls import confirm_discard, confirm_unsaved
from aurum.gui.icons import icon
from aurum.gui.opening import BankOpeningDrafts
from aurum.gui.report_settings import ReportForm
from aurum.gui.theme import monetary_html
from aurum.gui.theme_picker import ThemeComboBox
from aurum.gui.workspace import ChatGPTPage
from aurum.workflows.configuration import (
    CONFIGURATION_MODELS, configuration_schema, initialise_workspace, read_configuration, save_configuration,
    save_configurations,
)

# %% 1. Setup Screen -----------------------------------------------------------------------------------------
class SetupPage(QWidget):
    """
    Keep four Setup sections and independent connection settings beside protected configuration drafts.
    """

    changed = Signal()
    section_changed = Signal(str)
    section_labels_changed = Signal(dict)
    action_requested = Signal(str, object)

    def __init__(self, workspace: Path, parent: QWidget | None = None) -> None:
        """
        Open saved configuration without writing files, and keep actions outside the scrolling forms.
        """
        super().__init__(parent)
        self.workspace = Path(workspace).resolve()
        self._dirty = set()
        self._account_roles_dirty = False
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
        self.kinds = self.configuration_kinds
        self.chatgpt = ChatGPTPage()
        self.bank_connection = BankConnectionPanel()
        self.bank_openings = BankOpeningDrafts(self)
        self.bank_openings.action_requested.connect(self.action_requested)
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
        self.palette_pill = QFrame()
        self.palette_pill.setObjectName("setupPalettePill")
        self.palette_pill.setFixedHeight(18)
        self.palette_layout = QHBoxLayout(self.palette_pill)
        self.palette_layout.setContentsMargins(6, 6, 6, 6)
        self.palette_layout.setSpacing(4)
        self.theme_swatches = []
        swatches.addWidget(self.palette_pill)
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
            section_title.setProperty("headingLevel", 2)
            section_heading = QHBoxLayout()
            section_heading.addWidget(section_title)
            section_heading.addStretch()
            page_layout.addLayout(section_heading)
            if kind == "accounts":
                connections_title = QLabel("Connections")
                connections_title.setObjectName("setupConnectionsTitle")
                page_layout.addWidget(connections_title)
                connections = QFrame()
                connections.setObjectName("setupConnections")
                connections.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
                connections_layout = QVBoxLayout(connections)
                connections_layout.setContentsMargins(0, 0, 0, 0)
                connections_layout.setSpacing(0)
                connections_layout.addWidget(self.bank_connection)
                connections_layout.addWidget(self.chatgpt)
                page_layout.addWidget(connections)
                page_layout.addSpacing(12)
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
                scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            page_layout.addWidget(scroll, 1)
            self.pages[kind], self.errors[kind] = scroll, error
            self.tabs.addTab(page, _section_name(kind))

        footer = QFrame()
        self.footer = footer
        footer.setObjectName("setupFooter")
        actions = QHBoxLayout(footer)
        actions.setContentsMargins(0, 0, 0, 0)
        self.save_status = QLabel()
        self.save_status.setObjectName("muted")
        self.save_status.setWordWrap(True)
        actions.addWidget(self.save_status)
        actions.addStretch()
        self.reload_button = QToolButton()
        self.reload_button.setObjectName("setupIconAction")
        self.reload_button.setIcon(icon("refresh", size=18))
        self.reload_button.setIconSize(QSize(18, 18))
        self.reload_button.setFixedSize(32, 32)
        self.reload_button.setAccessibleName("Reload current settings")
        self.reload_button.setToolTip("Reload this section from its saved settings.")
        self.reload_button.clicked.connect(lambda: self._reload(self.kinds[self._current]))
        actions.addWidget(self.reload_button)
        self.save_button = QToolButton()
        self.save_button.setObjectName("setupIconAction")
        self.save_button.setIcon(icon("save", "#ffffff", size=18))
        self.save_button.setIconSize(QSize(18, 18))
        self.save_button.setFixedSize(32, 32)
        self.save_button.setToolTip("Save Setup changes")
        self.save_button.setAccessibleName("Save Setup changes")
        self.save_button.setProperty("primary", True)
        self.save_button.clicked.connect(self.save_changes)
        actions.addWidget(self.save_button)
        layout.addWidget(footer)
        self.bank_openings.changed.connect(self._update_labels)
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
        self._account_roles_dirty = False
        self._messages.clear()
        choices = self._choices()
        self._update_workspace_notice(choices)
        for kind in self.configuration_kinds:
            self._load(kind, choices)
        self._sync_account_budget()
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

    def set_bank_openings(self, contexts: dict) -> None:
        """
        Apply saved ledger context without replacing unposted opening fields or their original revisions.
        """
        self.bank_openings.set_contexts(contexts)
        self._update_labels()

    def guard_opening_changes(self) -> bool:
        """
        Keep configuration saves separate from the explicit action that posts a bank opening.
        """
        return self.bank_openings.guard(self)

    def guard_connection_changes(self) -> bool:
        """
        Protect independent connection drafts before leaving or explicitly reloading Accounts.
        """
        return all(connection.guard_close() for connection in (self.bank_connection, self.chatgpt))

    def action_completed(self, action: str, result: dict) -> None:
        """
        Clear only an opening balance whose atomic ledger save has been acknowledged.
        """
        if action == "save_bank_opening":
            self.bank_openings.complete(result)

    def action_failed(self, action: str, message: str) -> None:
        """
        Leave unsuccessful bank opening drafts available for correction or explicit discard.
        """
        if action == "save_bank_opening":
            self.bank_openings.fail(message)

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
        kind = "accounts" if kind == "chatgpt" else kind
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
        if "accounts" in self._dirty and (self._account_roles_dirty or "budget" in self._dirty):
            if not self._save_account_budget():
                self.tabs.blockSignals(True)
                self.tabs.setCurrentIndex(self.kinds.index("accounts"))
                self.tabs.blockSignals(False)
                self._current = self.kinds.index("accounts")
                self.section_changed.emit("accounts")
                self._update_labels()
                return False
            saved.extend(("accounts", "budget"))
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
                    self.save_status.show()
                return False
            saved.append(kind)
        if saved:
            self.save_status.setText("Changes saved")
            self.save_status.show()
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
        totals = info.get("totals", {})
        labels = {"balanced": "Ready", "incomplete": "Not set", "invalid": "Needs attention"}
        month = info.get("month") or self._month
        month_name = date.fromisoformat(month + "-01").strftime("%B %Y") if month else ""
        allocation_lines = [
            f"{_whole_money(totals.get(kind))} {label}" for kind, label in (
                ("income", "Income"), ("expense", "Expenses"), ("savings", "Savings"),
            )
        ]
        allocation_text = " · ".join(
            f"{info.get(kind, '—')} {label}" for kind, label in (
                ("income", "Income"), ("expense", "Expenses"), ("savings", "Savings"),
            )
        )
        self.budget_detail.set_summary(" · ".join(allocation_lines[:2]), allocation_lines[2])
        self.budget_value.setToolTip(f"{month_name} · {labels.get(info.get('status'), 'Unavailable')}")
        self.budget_detail.setToolTip("\n".join(filter(None, (allocation_text, info.get("detail", "")))))
        reports = self.forms.get("reports")
        if reports is None:
            return
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
        while len(self.theme_swatches) > len(palette):
            swatch = self.theme_swatches.pop()
            self.palette_layout.removeWidget(swatch)
            swatch.deleteLater()
        while len(self.theme_swatches) < len(palette):
            swatch = QLabel()
            self.palette_layout.addWidget(swatch)
            self.theme_swatches.append(swatch)
        count = len(palette)
        gap = 4 if count <= 5 else 2 if count <= 15 else 1 if count <= 42 else 0
        width = min(22, max(1, (126 - gap * max(0, count - 1)) // max(1, count)))
        self.palette_layout.setSpacing(gap)
        self.palette_pill.setFixedWidth(count * width + gap * max(0, count - 1) + 12)
        self.palette_pill.setVisible(bool(palette))
        for index, swatch in enumerate(self.theme_swatches):
            swatch.setFixedSize(width, 6)
            colour = QColor(palette[index])
            swatch.setStyleSheet(f"background: {colour.name()}; border-radius: 3px;")
        fixed = bool(theme.get("fixed"))
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

    def save_owner_name(self, document, value: str) -> str:
        """
        Save only the sidebar name, rebasing matching form revisions without committing their drafts.

        Older form revisions remain stale when saved settings changed before name editing began.
        The shared configuration save rejects any change made after the edit's snapshot was captured.
        """
        if self._busy:
            raise ValueError("Wait for the current workspace action before saving your name.")
        if document.kind != "reports" or document.workspace != self.workspace:
            raise ValueError("Reload this workspace before editing your name.")
        name = ReportSettings(name=value.strip() or "Aurum").owner_name
        if name == ReportSettings(name=document.data.get("name", "Aurum")).owner_name:
            return name
        data = deepcopy(document.data)
        data["name"] = name or "Aurum"
        saved = save_configuration(document, data)
        for kind, original in tuple(self.documents.items()):
            if original.originals != document.originals:
                continue
            self.documents[kind] = replace(
                original, data=saved.data if kind == "reports" else original.data,
                originals=dict(saved.originals),
            )
            if kind == "reports" and kind in self.forms:
                self.forms[kind].data["name"] = saved.data["name"]
        self._refresh_version = None
        self._update_labels()
        self.changed.emit()
        return name

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
        return bool(self._dirty) or self.bank_openings.has_unsaved_changes()

    def has_unsaved_changes(self) -> bool:
        """
        Expose the main window's common unsaved-edit interface.
        """
        return self.is_dirty()

    def discard_changes(self) -> None:
        """
        Reload saved files after the user explicitly discards form changes.
        """
        self.bank_openings.discard()
        self.refresh(force=True)

    def maybe_leave(self) -> bool:
        """
        Ask to save, discard or keep editing before leaving a dirty setup section.
        """
        if not self.guard_connection_changes() or not self.guard_opening_changes():
            return False
        if not self.is_dirty():
            return True
        answer = confirm_unsaved(self)
        if answer == "cancel":
            return False
        if answer == "discard":
            self.discard_changes()
            return True
        return self.save_changes()

    def set_busy(self, busy: bool) -> None:
        """
        Suspend settings changes while the current workspace job uses saved configuration.
        """
        self._busy = busy
        self.bank_connection.set_busy(busy)
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
            form = editor(configuration_schema(kind), document.data, {
                **choices, "configuration_kind": kind, "bank_opening_drafts": self.bank_openings,
            })
            if kind == "budget":
                form.set_reporting_month(self._month)
                form.summary_changed.connect(self._update_summary)
            elif kind == "accounts":
                form.budget_changed.connect(self._edit_account_budget)
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
            "accounts": {}, "asset_accounts": {}, "non_bank_asset_accounts": {}, "bank_accounts": {},
            "equity_accounts": {},
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
                if kind == "asset" and item.get("bank_details"):
                    choices["bank_accounts"][item["key"]] = item["name"]
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
        if kind == "budget":
            self._sync_account_budget()
        self._update_labels()

    def _sync_account_budget(self) -> None:
        """
        Supply account rows with the current budget draft without replacing any account metadata.
        """
        accounts, budget = self.forms.get("accounts"), self.forms.get("budget")
        if accounts is not None:
            accounts.set_budget_context(budget.value() if budget is not None else None)

    def _edit_account_budget(self, account: str, choice: object) -> None:
        """
        Keep the shared mapping draft under Accounts while leaving monthly targets untouched.
        """
        budget = self.forms.get("budget")
        if self._busy or budget is None or not budget.set_account_role(account, choice):
            return
        self._account_roles_dirty = True
        self._sync_account_budget()
        self._mark_dirty("accounts")

    def _update_labels(self) -> None:
        """
        Keep tab markers, save state and action availability consistent with the selected section.
        """
        for index, kind in enumerate(self.kinds):
            dirty = kind in self._dirty
            self.tabs.setTabText(index, _section_name(kind) + (" *" if dirty else ""))
        kind = self.kinds[self._current]
        self.save_button.setEnabled(
            bool(self._dirty)
            and not self._busy and (not self._missing or not self._dirty)
        )
        self.reload_button.setEnabled(not self._busy)
        self.reload_button.setToolTip("Reload this section's settings")
        self.tabs.setEnabled(not self._busy)
        for selected in self.configuration_kinds:
            self.pages[selected].setEnabled(not self._busy and not self._missing)
        self.initialise_button.setEnabled(not self._busy)
        self.theme.setEnabled(not self._busy and not self._missing and "reports" in self.forms)
        self.appearance.setEnabled(not self._busy and not self._missing and "reports" in self.forms)
        self.bank_connection.set_config_blocked(
            "Create the workspace files first." if self._missing else
            "Save account changes before linking bank accounts." if "accounts" in self._dirty else ""
        )
        self.bank_openings.set_blocked(
            "Wait for the current workspace action to finish." if self._busy else
            "Create the workspace files first." if self._missing else
            "Save account settings before posting an opening balance." if "accounts" in self._dirty else
            "Save report settings before posting an opening balance." if "reports" in self._dirty else ""
        )
        dirty_kinds = [item for item in self.configuration_kinds if item in self._dirty]
        self.save_status.setText(
            "Check the settings above" if self.errors[kind].text() else
            "Unsaved: " + ", ".join(_section_name(item) for item in dirty_kinds)
            if dirty_kinds else "Unposted opening balance" if self.bank_openings.has_unsaved_changes()
            else self._messages.get(kind, "")
        )
        self.save_status.setVisible(bool(self.save_status.text()))
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
        if kind == "accounts" and (self._account_roles_dirty or "budget" in self._dirty):
            return self._save_account_budget()
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
            self._sync_account_budget()
        else:
            self.refresh(force=True)
        self._messages[kind] = kind.title() + " saved"
        self._update_labels()
        self.changed.emit()
        return True

    def _save_account_budget(self) -> bool:
        """
        Validate account edits and their budget roles together before acknowledging either draft.
        """
        kinds = ("accounts", "budget")
        if self._busy or self._missing or any(
            kind not in self.forms or kind not in self.documents for kind in kinds
        ):
            self._show_error("accounts", "Reload Accounts and Budget before saving account settings.")
            return False
        try:
            saved = save_configurations(
                {kind: self.documents[kind] for kind in kinds},
                {kind: self.forms[kind].value() for kind in kinds},
            )
        except (ValueError, OSError, TypeError) as error:
            self._show_error("accounts", _configuration_error(error))
            self._update_labels()
            return False
        self.documents.update(saved)
        originals = saved["accounts"].originals
        for kind, document in tuple(self.documents.items()):
            if kind not in saved:
                self.documents[kind] = replace(document, originals=dict(originals))
        self._dirty.difference_update(kinds)
        self._account_roles_dirty = False
        if self._dirty:
            choices = self._choices()
            for kind in kinds:
                self._load(kind, choices)
            self._sync_account_budget()
        else:
            self.refresh(force=True)
        self._messages["accounts"] = "Accounts saved"
        self._messages["budget"] = "Budget saved"
        self._update_labels()
        self.changed.emit()
        return True

    def _reload(self, kind: str) -> None:
        """
        Replace the current section from disk only after any local changes have been explicitly discarded.
        """
        if self._busy:
            return
        if kind == "accounts" and not self.guard_connection_changes():
            return
        if kind == "accounts" and not self.guard_opening_changes():
            return
        if kind in self._dirty and not confirm_discard(
            self, question="Discard changes in " + _section_name(kind) + "?",
        ):
            return
        budget = self.forms.get("budget")
        retained_roles = budget.account_roles() if (
            kind == "budget" and self._account_roles_dirty and budget is not None
        ) else None
        if kind == "accounts":
            try:
                saved_budget = read_configuration(self.workspace, "budget")
            except (ValueError, OSError, TypeError) as error:
                self._show_error("accounts", _configuration_error(error))
                return
            if budget is not None:
                budget.set_account_roles(saved_budget.data)
            self._account_roles_dirty = False
        self._dirty.discard(kind)
        self._messages.pop(kind, None)
        if self._dirty:
            choices = self._choices()
            self._update_workspace_notice(choices)
            self._load(kind, choices)
            if kind == "accounts" and "budget" not in self._dirty:
                self._load("budget", choices)
            if retained_roles is not None and "budget" in self.forms:
                self.forms["budget"].set_account_roles(retained_roles)
            self._sync_account_budget()
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
def _whole_money(amount: Decimal | None) -> str:
    """
    Round card totals to whole dollars without changing their exact accounting or showing negative zero.
    """
    if amount is None or not amount.is_finite():
        return "—"
    with localcontext() as context:
        context.prec = max(context.prec, amount.adjusted() + 2)
        rounded = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    digits = format(rounded.copy_abs(), ".0f")
    return ("-" if rounded < 0 else "") + "$" + digits


def _section_name(kind: str) -> str:
    """
    Use product spelling consistently in the sidebar, section titles and unsaved-state messages.
    """
    return "ChatGPT" if kind == "chatgpt" else kind.title()


class _TwoLineSummary(QLabel):
    """
    Keep cash totals on one line and total savings beneath them, with complete accessible wording.
    """

    def __init__(self, parent=None) -> None:
        """
        Keep the full summary separate from its width-dependent presentation.
        """
        super().__init__(parent)
        self._summary_text = ""
        self._summary_lines = ()
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def set_summary(self, first_line: str, second_line: str = "") -> None:
        """
        Update the visible totals without inserting a third line into the summary card.
        """
        self._summary_lines = tuple(filter(None, (first_line, second_line)))
        self._summary_text = " · ".join(self._summary_lines)
        self.setAccessibleName(self._summary_text)
        self._fit_text()

    def resizeEvent(self, event) -> None:
        """
        Reflow the existing totals as the card becomes wider or narrower.
        """
        super().resizeEvent(event)
        self._fit_text()

    def _fit_text(self) -> None:
        """
        Preserve the intended two rows, eliding long lines while keeping full values in the tooltip.
        """
        width = max(1, self.contentsRect().width())
        lines = list(self._summary_lines)
        for index, line in enumerate(lines):
            if self._text_width(line) > width:
                while line and self._text_width(line + "…") > width:
                    line = line[:-1]
                lines[index] = line + "…"
        self.setText("<br>".join(self._line_html(line) for line in lines))

    def _line_html(self, text: str) -> str:
        """
        Use the same numeric markup when measuring and displaying each summary line.
        """
        return " ".join(
            monetary_html(word) if word.startswith(("$", "-$")) else escape(word)
            for word in text.split(" ")
        )

    def _text_width(self, text: str) -> int:
        """
        Measure Qt's actual rich-text width so narrow spaces and the numeric font cannot clip the ellipsis.
        """
        document = QTextDocument()
        document.setDocumentMargin(0)
        document.setDefaultFont(self.font())
        document.setHtml(self._line_html(text))
        return int(document.idealWidth() + 1)


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
