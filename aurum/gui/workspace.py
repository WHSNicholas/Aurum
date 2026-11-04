# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  GUI - Workspace                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Connect the desktop's overview, ChatGPT setup and PDF viewer to explicit user actions.

These widgets display results and collect choices. Accounting, authentication and report generation are
handled by shared services in background jobs; simply visiting a screen never calls AI or changes accounting.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import datetime
from pathlib import Path
import stat

from PySide6.QtCore import Qt, Signal
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QListView,
    QPushButton, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.controls import HeaderActionButton, PageActionRow, QuoteBlock
from aurum.gui.icons import icon
from aurum.gui.theme import gui_colour


# %% 1. Monthly Overview -------------------------------------------------------------------------------------
class OverviewPage(QWidget):
    """
    Present posted finances and saved monthly progress, leaving navigation in the sidebar.
    """

    navigate = Signal(str)

    def __init__(self, parent=None) -> None:
        """
        Arrange owner metadata, financial cards and a connected monthly workflow.
        """
        super().__init__(parent)
        self._metric_columns = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        scroll = QScrollArea()
        scroll.setObjectName("overviewScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        scroll.setWidget(content)
        layout.addWidget(scroll)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(20)

        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 16)
        header.setSpacing(0)
        heading = QHBoxLayout()
        self.title_row = heading
        title = QLabel("Overview")
        title.setObjectName("workspacePageTitle")
        heading.addWidget(title)
        heading.addStretch()
        header.addLayout(heading)

        identity = QHBoxLayout()
        identity.setContentsMargins(0, 0, 0, 0)
        self.owner = QLabel("Your finances at a glance")
        self.owner.setTextFormat(Qt.TextFormat.PlainText)
        self.owner.setObjectName("workspacePageSubtitle")
        identity.addWidget(self.owner)
        identity.addStretch()
        header.addLayout(identity)
        layout.insertLayout(0, header)

        self.metrics = QGridLayout()
        self.metrics.setSpacing(12)
        self.cards, self.values, self.captions = [], {}, {}
        for key, name, symbol, caption in (
            ("net_worth", "Net Worth", "wallet", "Assets less liabilities"),
            ("income", "Income", "income", "—"),
            ("expenses", "Expenses", "expenses", "—"),
            ("savings", "Savings", "savings", "Income less expenses"),
        ):
            frame = QFrame()
            frame.setObjectName("overviewMetric")
            box = QVBoxLayout(frame)
            box.setContentsMargins(18, 12, 18, 12)
            box.setSpacing(7)
            top = QHBoxLayout()
            top.addStretch()
            top.addWidget(self._symbol(symbol, 23))
            top.addStretch()
            box.addLayout(top)
            label = QLabel(name)
            label.setObjectName("overviewMetricLabel")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(label)
            value = QLabel("—")
            value.setObjectName("overviewValue")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            box.addWidget(value)
            detail = QLabel(caption)
            detail.setObjectName("overviewCaption")
            detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
            detail.setWordWrap(True)
            detail.setVisible(bool(caption))
            if key in {"income", "expenses"}:
                detail.setToolTip(
                    f"Transactions included in this month's {name.lower()}. Split postings count once."
                )
            box.addWidget(detail)
            box.addStretch()
            self.cards.append(frame)
            self.values[key], self.captions[key] = value, detail
        self._arrange_metrics(4)
        body.addLayout(self.metrics)

        process = QFrame()
        process.setObjectName("overviewProcess")
        process_layout = QVBoxLayout(process)
        process_layout.setContentsMargins(20, 18, 20, 20)
        process_layout.setSpacing(16)
        process_heading = QHBoxLayout()
        label = QLabel("Workflow")
        label.setObjectName("overviewSection")
        process_heading.addWidget(label)
        process_heading.addStretch()
        self.process_status = QLabel("Waiting for workspace")
        self.process_status.setObjectName("overviewCaption")
        process_heading.addWidget(self.process_status)
        process_layout.addLayout(process_heading)
        flow = QHBoxLayout()
        flow.setSpacing(8)
        self.stages = {}
        for number, (key, name, symbol) in enumerate((
            ("sources", "Sources", "statements"), ("journal", "Journal", "journal"),
            ("review", "Review", "review"), ("reports", "Reports", "reports"),
        ), 1):
            if number > 1:
                flow.addWidget(self._symbol("right", 16))
            frame = QFrame()
            frame.setObjectName("overviewStage")
            stage = QVBoxLayout(frame)
            stage.setContentsMargins(12, 13, 12, 13)
            stage.setSpacing(9)
            top = QHBoxLayout()
            emblem = self._symbol(symbol, 23)
            top.addWidget(emblem)
            top.addStretch()
            state = QLabel()
            state.setObjectName("overviewStageState")
            state.setFixedSize(18, 18)
            state.hide()
            top.addWidget(state)
            stage.addLayout(top)
            title = QLabel(f"{number} · {name}")
            title.setObjectName("overviewStageTitle")
            stage.addWidget(title)
            value = QLabel("—")
            value.setObjectName("overviewStageValue")
            value.setWordWrap(True)
            stage.addWidget(value)
            detail = QLabel("Awaiting saved activity")
            detail.setObjectName("overviewCaption")
            detail.setWordWrap(True)
            stage.addWidget(detail)
            stage.addStretch()
            flow.addWidget(frame, 1)
            self.stages[key] = frame, state, value, detail, emblem, symbol
        process_layout.addLayout(flow)
        self.scope = QLabel()
        self.scope.setObjectName("overviewCaption")
        self.scope.setWordWrap(True)
        self.scope.hide()
        process_layout.addWidget(self.scope)
        body.addWidget(process)

        self.next_panel = QFrame()
        self.next_panel.setObjectName("overviewNext")
        hero_layout = QHBoxLayout(self.next_panel)
        hero_layout.setContentsMargins(20, 18, 20, 18)
        hero_layout.setSpacing(14)
        self.next_icon = self._symbol("clock", 26)
        hero_layout.addWidget(self.next_icon, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(6)
        self.next_eyebrow = QLabel("NEXT STEP")
        self.next_eyebrow.setObjectName("overviewEyebrow")
        text.addWidget(self.next_eyebrow)
        self.next_title = QLabel("Your month at a glance")
        self.next_title.setObjectName("overviewNextTitle")
        self.next_title.setWordWrap(True)
        text.addWidget(self.next_title)
        self.next_detail = QLabel("Saved activity will appear here.")
        self.next_detail.setWordWrap(True)
        self.next_detail.setTextFormat(Qt.TextFormat.PlainText)
        text.addWidget(self.next_detail)
        hero_layout.addLayout(text, 1)
        body.addWidget(self.next_panel)

        self.other_work = QLabel("")
        self.other_work.setObjectName("overviewCaption")
        self.other_work.setTextFormat(Qt.TextFormat.PlainText)
        self.other_work.setWordWrap(True)
        self.other_work.hide()
        body.addWidget(self.other_work)
        body.addStretch(1)
        quote = QuoteBlock(
            "It is not the man who has too little, but the man who craves more, that is poor.",
            "Seneca",
            "https://upload.wikimedia.org/wikipedia/commons/c/c8/"
            "Seneca_Vol.1_%28IA_in.ernet.dli.2015.23185%29.pdf",
        )
        quote.setMaximumWidth(720)
        quote_row = QHBoxLayout()
        quote_row.setContentsMargins(0, 12, 0, 4)
        quote_row.addStretch(1)
        quote_row.addWidget(quote, 8)
        quote_row.addStretch(1)
        body.addLayout(quote_row)

    def show_loading(self) -> None:
        """
        Clear stale month values while the header's refresh indicator handles loading feedback.
        """
        for key in ("income", "expenses"):
            self.captions[key].setText("—")
        for value in self.values.values():
            value.setText("—")
            value.setToolTip("")
        for key in self.stages:
            self._set_stage(key, "waiting", "—", "—", "Updating saved activity")
        self.process_status.setText("Updating…")
        self._show_completion(False)
        self.captions["net_worth"].setText("—")
        self.captions["net_worth"].setToolTip("")
        self.next_title.setText("Your month at a glance")
        self.next_detail.setText("Updating saved figures and progress.")
        self.other_work.hide()
        self.scope.hide()

    def refresh(self, snapshot: dict) -> None:
        """
        Infer workflow completion from saved work and the selected month's report files.
        """
        self.owner.setText("Your finances at a glance")
        self.other_work.hide()
        self.scope.hide()
        self._show_completion(False)
        self.captions["net_worth"].setToolTip("")
        for key in ("income", "expenses"):
            self.captions[key].setText("—")
        if not snapshot.get("ready"):
            for value in self.values.values():
                value.setText("—")
                value.setToolTip("")
            self.captions["net_worth"].setText("—")
            missing = bool(snapshot.get("missing_files"))
            self.process_status.setText("Setup needed" if missing else "Refresh needed")
            for key in self.stages:
                self._set_stage(key, "waiting", "WAITING", "—", "Workspace not ready")
            self.next_title.setText("Finish setting up Aurum" if missing else "Check your workspace")
            guidance = "Configure your workspace in Setup to start recording this month." if missing else (
                "Refresh the workspace to try again."
            )
            self.next_detail.setText(snapshot.get("error") or guidance)
            return

        financials = snapshot.get("financials", {})
        for key, value in self.values.items():
            amount = financials.get(key)
            formatted = "—" if amount is None else f"${abs(amount):,.2f}".replace(",", "\u202f")
            value.setText("-" + formatted if amount is not None and amount < 0 else formatted)
            value.setToolTip(value.text())
        transaction_counts = snapshot.get("financial_transaction_counts", {})
        for key in ("income", "expenses"):
            count = transaction_counts.get(key)
            if count is not None:
                number = f"{count:,}".replace(",", "\u202f")
                self.captions[key].setText(f"{number} transaction" + ("" if count == 1 else "s"))
        cutoff = snapshot.get("cutoff", snapshot.get("period_end", ""))
        try:
            cutoff = datetime.fromisoformat(cutoff).strftime("%d %b %Y").lstrip("0")
        except (TypeError, ValueError):
            cutoff = "the reporting cutoff"
        unpriced = snapshot.get("net_worth_unpriced_holdings", [])
        caption = f"As at {cutoff}" + (" · Provisional" if unpriced else "")
        prices = snapshot.get("net_worth_price_dates", [])
        dates = sorted({datetime.fromisoformat(item["price_date"]) for item in prices})
        if dates:
            first, last = dates[0], dates[-1]
            price_dates = last.strftime("%d %b %Y").lstrip("0")
            if first != last:
                pattern = "%d %b" if first.year == last.year else "%d %b %Y"
                price_dates = first.strftime(pattern).lstrip("0") + " – " + price_dates
            caption += f"\nPrices {price_dates}"
        self.captions["net_worth"].setText(caption)
        valuation = (
            "Uses the balance sheet's calculation and the latest valid saved market prices "
            "on or before the reporting cutoff."
        )
        for item in prices:
            price_date = datetime.fromisoformat(item["price_date"]).strftime("%d %b %Y").lstrip("0")
            valuation += f"\n{item['name']}: {price_date}"
        if dates:
            valuation += "\nGenerate reports to check for newer closing prices."
        if unpriced:
            valuation += (
                "\nRecorded value retained for: " + ", ".join(unpriced)
                + ". Generate reports to check prices or any required investment review."
            )
        self.captions["net_worth"].setToolTip(valuation)
        self.values["net_worth"].setToolTip(self.values["net_worth"].text() + "\n" + valuation)

        work = _overview_work(snapshot)
        queued = work["bank_queued"] + work["manual_queued"]
        failed = work["bank_failed"] + work["manual_failed"]
        questions = work["bank_attention"] + work["manual_attention"]
        attention = questions + failed + work["investment"]
        investment_error = str(snapshot.get("investment_reviews_error") or "")
        imported = len(snapshot.get("transactions", []))
        manuals = len(snapshot.get("manuals", []))
        posted = len(snapshot.get("entries", []))
        activity = bool(imported or manuals or posted)
        report_paths = tuple(dict.fromkeys(snapshot.get("report_paths", [])))
        saved = sum(Path(path).is_file() for path in report_paths)
        previous_steps_complete = activity and not (queued or attention or investment_error)
        reports_complete = previous_steps_complete and len(report_paths) == 4 and saved == 4
        self._set_stage(
            "sources", "done" if activity else "active", "ADDED" if activity else "START HERE",
            f"{imported} imported", _overview_count(manuals, "manual instruction"),
        )
        self._set_stage(
            "journal", "attention" if failed else "active" if queued or questions
            else "done" if activity else "waiting",
            "WAITING" if queued or failed or questions
            else "PREPARED" if activity else "WAITING",
            _overview_count(posted, "posted event"),
            _overview_count(queued + failed, "item to process", "items to process") if queued or failed
            else _overview_count(questions, "answer needed", "answers needed") if questions
            else "No work queued",
        )
        self._set_stage(
            "review", "attention" if investment_error or questions or work["investment"]
            else "waiting" if queued or failed
            or not activity else "done",
            "UNAVAILABLE" if investment_error else "ATTENTION" if questions or work["investment"]
            else "PENDING" if queued or failed
            or not activity else "CLEAR",
            "—" if investment_error else
            _overview_count(questions + work["investment"], "item to check", "items to check"),
            _overview_count(questions, "known question") + " · Investment unavailable"
            if investment_error and questions else
            "Investment review could not load" if investment_error else
            f"{questions} questions · {work['investment']} investment"
            if questions or work["investment"] else "Check again after processing" if queued or failed
            else "No outstanding questions",
        )
        self._set_stage(
            "reports", "done" if reports_complete else "active" if previous_steps_complete else "waiting",
            "COMPLETE" if reports_complete else "WAITING", f"{saved} of 4 PDFs",
            "Investment review unavailable" if investment_error else
            "All four reports available" if reports_complete else "Complete earlier steps"
            if not previous_steps_complete else "Ready to generate",
        )
        self.process_status.setText(
            "Investment review unavailable" if investment_error else
            _overview_count(attention, "item needs attention", "items need attention") if attention else
            _overview_count(queued, "item waiting", "items waiting") if queued else
            "No pending work found" if activity else "Ready to begin"
        )
        outside = snapshot.get("counts", {}).get("linked_outside_month", 0)
        self.scope.setText(
            f"Includes {_overview_count(outside, 'linked transaction')} outside this month."
        )
        self.scope.setVisible(bool(outside))
        auth = snapshot.get("auth", {})
        connection_needed = bool(queued or failed) and (
            bool(auth.get("error")) or not auth.get("signed_in") or not auth.get("model")
        )
        tasks = [
            ("Check investment review",
             "Investment review could not load. Refresh the workspace, then check Review. "
             + investment_error,
             bool(investment_error)),
            ("Check your manual entries",
             f"{_overview_count(work['manual_attention'], 'manual entry', 'manual entries')} "
             f"{'needs' if work['manual_attention'] == 1 else 'need'} a response in Review. "
             "Save the answers before processing again.",
             work["manual_attention"]),
            ("Review bank transactions",
             f"{_overview_count(work['bank_attention'], 'bank transaction')} "
             f"{'needs' if work['bank_attention'] == 1 else 'need'} attention. "
             "Check the details in Review, including any linked manual instructions.",
             work["bank_attention"]),
            ("Connect ChatGPT before processing",
             "Check your sign-in and saved model in ChatGPT. Your posted figures remain available.",
             connection_needed),
            ("Process month",
             f"{_overview_count(queued + failed, 'item')} ready to process. "
             "Choose Process month in Review to apply saved instructions and answers, "
             "including any interrupted work.", queued or failed),
            ("Classify investment movements",
             f"{_overview_count(work['investment'], 'investment movement')} "
             f"{'needs' if work['investment'] == 1 else 'need'} a treatment in Review, "
             "including earlier movements affecting this month.",
             work["investment"]),
        ]
        active = [task for task in tasks if task[2]]
        if active:
            title, detail, _ = active[0]
            self.next_title.setText(title)
            self.next_detail.setText(detail + " The figures above reflect posted entries only.")
            self.other_work.setText("Also waiting: " + " · ".join(task[0] for task in active[1:]))
            self.other_work.setVisible(len(active) > 1)
        elif reports_complete and snapshot.get("statement_completion", {}).get("pending"):
            completion = snapshot["statement_completion"]
            title, status = {
                "unavailable": ("Check statement PDFs", "Source PDFs unavailable"),
                "unimported": ("Import updated statement", "Updated source awaiting import"),
            }.get(completion.get("status"), ("Up to date", "Final statement awaited"))
            self.next_title.setText(title)
            self.next_detail.setText(completion["message"])
            self.process_status.setText(status)
        elif reports_complete:
            self._show_completion(True)
            self.next_title.setText("All done")
            self.next_detail.setText("Your workflow is complete and all four reports are saved.")
            self.process_status.setText("Complete")
        elif not activity:
            self.next_title.setText("Start this month's journal")
            self.next_detail.setText(
                "Add your statements and any manual instructions using the sidebar. "
                "Your progress will appear here as work is saved."
            )
        else:
            self.next_title.setText("Ready to generate reports")
            self.next_detail.setText(
                "No pending monthly journal work was found. Generate all four PDFs in Reports to check "
                "valuations and any remaining draft reasons."
                + (f" {saved} saved PDFs are already available." if saved else "")
            )

    def action_failed(self, action: str, message: str) -> None:
        """
        Replace the loading placeholder with an actionable refresh failure.
        """
        if action == "refresh":
            self.refresh({"ready": False, "error": message})

    def resizeEvent(self, event) -> None:
        """
        Keep financial values readable by using two columns in narrower windows.
        """
        super().resizeEvent(event)
        self._arrange_metrics(2 if self.width() < 880 else 4)

    def _arrange_metrics(self, columns: int) -> None:
        """
        Reflow the existing cards without rebuilding their values or interactions.
        """
        if columns == self._metric_columns:
            return
        for frame in self.cards:
            self.metrics.removeWidget(frame)
        for index, frame in enumerate(self.cards):
            self.metrics.addWidget(frame, index // columns, index % columns)
        for index in range(4):
            self.metrics.setColumnStretch(index, 1 if index < columns else 0)
        self._metric_columns = columns

    def _set_stage(self, key: str, tone: str, state: str, value: str, detail: str) -> None:
        """
        Keep each stage's icon, highlighting completion with its background and a separate tick.
        """
        frame, badge, number, caption, emblem, symbol = self.stages[key]
        frame.setProperty("tone", tone)
        for widget in (frame, *frame.findChildren(QLabel)):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        number.setText(value)
        caption.setText(detail)
        role = "sidebar_text" if tone == "done" else (
            "warning_text" if tone == "attention" else "icon" if tone == "active" else "muted"
        )
        colour = gui_colour(self, role, "#ffffff" if tone == "done" else "#2e6a61")
        emblem.setProperty("aurumIconRole", role)
        badge.setProperty("aurumIconRole", role)
        emblem.setPixmap(icon(symbol, colour, 23).pixmap(23, 23))
        badge.clear()
        if tone in {"done", "attention"}:
            badge.setPixmap(icon("check" if tone == "done" else "error", colour, 18).pixmap(18, 18))
        badge.setVisible(tone in {"done", "attention"})
        badge.setToolTip(state.capitalize())
        badge.setAccessibleName(state.capitalize())
        frame.setAccessibleName(f"{key}: {state.lower()}. {value}. {detail}")

    def _show_completion(self, complete: bool) -> None:
        """
        Confirm completion when the earlier steps are complete and all report files are present.
        """
        self.next_panel.setProperty("complete", complete)
        for widget in (self.next_panel, *self.next_panel.findChildren(QLabel)):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        self.next_eyebrow.setText("MONTH COMPLETE" if complete else "NEXT STEP")
        role = "sidebar_text" if complete else "icon"
        colour = gui_colour(self, role, "#ffffff" if complete else "#2e6a61")
        self.next_icon.setProperty("aurumIconRole", role)
        self.next_icon.setPixmap(
            icon("check" if complete else "clock", colour, 26).pixmap(26, 26)
        )

    @staticmethod
    def _symbol(name: str, size: int) -> QLabel:
        """
        Give local replaceable outline icons consistent size and spacing.
        """
        label = QLabel()
        label.setObjectName("overviewIcon")
        label.setProperty("aurumIconRole", "icon")
        label.setPixmap(icon(name, "#3f7168", size).pixmap(size, size))
        label.setFixedSize(size, size)
        return label


# %% 2. ChatGPT Setup ----------------------------------------------------------------------------------------
class ChatGPTPage(QWidget):
    """
    Keep workspace sign-in and the saved processing model separate from monthly accounting.
    """

    action_requested = Signal(str, object)
    changed = Signal()

    def __init__(self, parent=None, *, embedded: bool = False) -> None:
        """
        Keep explicit connection actions and model saving compact enough for the Setup section.
        """
        super().__init__(parent)
        self._auth = {}
        self._busy = False
        self._saved_model = None
        self._models_loaded = False
        self._checked_online = False
        self._active_action = ""
        self._feedback_success = False
        self._reported_selection = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        if not embedded:
            header = QVBoxLayout()
            header.setContentsMargins(0, 0, 0, 20)
            header.setSpacing(0)
            title = QLabel("ChatGPT")
            title.setObjectName("workspacePageTitle")
            header.addWidget(title)
            subtitle = QLabel("Connect your account and choose a model.")
            subtitle.setObjectName("workspacePageSubtitle")
            header.addWidget(subtitle)
            layout.addLayout(header)
        connection = QFrame()
        connection.setObjectName("workspacePanel")
        connection_layout = QVBoxLayout(connection)
        connection_layout.setContentsMargins(16, 14, 16, 14)
        connection_layout.setSpacing(8)
        heading = QHBoxLayout()
        title = QLabel("Connection")
        title.setObjectName("workspaceSection")
        heading.addWidget(title)
        heading.addStretch()
        self.connect_button = QPushButton()
        self.connect_button.setFixedHeight(36)
        self.connect_button.setProperty("primary", True)
        self.connect_button.clicked.connect(lambda: self.action_requested.emit("connect_chatgpt", {}))
        heading.addWidget(self.connect_button)
        self.reconnect = self._icon_button(
            "login", "Sign in again to renew access or change your ChatGPT account",
        )
        self.reconnect.clicked.connect(
            lambda: self.action_requested.emit("connect_chatgpt", {"force_sign_in": True})
        )
        heading.addWidget(self.reconnect)
        connection_layout.addLayout(heading)
        self.status = QLabel()
        self.status.setObjectName("workspaceStatus")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        connection_layout.addWidget(self.status)
        self.connection_detail = QLabel()
        self.connection_detail.setObjectName("workspaceHint")
        self.connection_detail.setWordWrap(True)
        self.connection_detail.setTextFormat(Qt.TextFormat.PlainText)
        connection_layout.addWidget(self.connection_detail)
        self.feedback = QLabel()
        self.feedback.setObjectName("workspaceError")
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.hide()
        connection_layout.addWidget(self.feedback)
        layout.addWidget(connection)

        model = QFrame()
        model.setObjectName("workspacePanel")
        model_layout = QVBoxLayout(model)
        model_layout.setContentsMargins(16, 14, 16, 14)
        model_layout.setSpacing(8)
        heading = QLabel("Processing model")
        heading.setObjectName("workspaceSection")
        model_layout.addWidget(heading)
        self.saved_model = QLabel()
        self.saved_model.setObjectName("workspaceHint")
        self.saved_model.setWordWrap(True)
        self.saved_model.setTextFormat(Qt.TextFormat.PlainText)
        model_layout.addWidget(self.saved_model)
        selection = QHBoxLayout()
        selection.setSpacing(10)
        self.models = QComboBox()
        self.models.setObjectName("workspaceSelect")
        self.models.setView(QListView(self.models))
        self.models.setAccessibleName("Processing model")
        self.models.setMinimumWidth(220)
        self.models.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.models.setMinimumContentsLength(20)
        self.models.setPlaceholderText("Check connection to load models")
        arrow = (Path(__file__).parent / "assets" / "chevron_down.svg").as_posix()
        self.models.setStyleSheet(
            'QComboBox { padding-right: 28px; } '
            f'QComboBox::down-arrow {{ image: url("{arrow}"); width: 10px; height: 6px; }} '
            'QComboBox:disabled { background: #edf2ef; color: #9baaa6; border-color: #dde5e2; }'
        )
        self.models.currentIndexChanged.connect(self._update_controls)
        selection.addWidget(self.models, 1)
        self.save = self._icon_button("save", "Save processing model", primary=True)
        self.save.clicked.connect(self.save_changes)
        selection.addWidget(self.save)
        model_layout.addLayout(selection)
        self.selection_status = QLabel()
        self.selection_status.setObjectName("workspaceHint")
        self.selection_status.setWordWrap(True)
        self.selection_status.setTextFormat(Qt.TextFormat.PlainText)
        model_layout.addWidget(self.selection_status)
        layout.addWidget(model)
        layout.addStretch()
        self._update_controls()

    def refresh(self, snapshot: dict) -> None:
        """
        Refresh local status while retaining a model choice that has not yet been saved.
        """
        staged = self.models.currentData() if self.has_unsaved_changes() else None
        self._auth = dict(snapshot.get("auth", {}))
        if self._feedback_success and (
            self._auth.get("error") or not self._auth.get("signed_in")
            or self._auth.get("model") != self._saved_model
        ):
            self.feedback.clear()
            self.feedback.hide()
            self._feedback_success = False
        self._saved_model = self._auth.get("model")
        if self._auth.get("error") or not self._auth.get("signed_in"):
            self._checked_online = False
            self._models_loaded = False
        if staged is None:
            self.models.setCurrentIndex(self.models.findData(self._saved_model))
        self._update_controls()

    def set_busy(self, busy: bool) -> None:
        """
        Suspend connection and model actions while the desktop is running a job.
        """
        self._busy = busy
        self._update_controls()

    def has_unsaved_changes(self) -> bool:
        """
        Identify a staged model choice without treating a loaded catalogue as an edit.
        """
        selected = self.models.currentData()
        return bool(selected) and selected != self._saved_model

    def discard_changes(self) -> None:
        """
        Restore the saved selection without changing the workspace or checking access online.
        """
        self.models.setCurrentIndex(self.models.findData(self._saved_model))
        self._update_controls()

    def save_changes(self) -> None:
        """
        Submit only an available changed model for the shared connection and save workflow.
        """
        if not self._busy and self._models_loaded and self.has_unsaved_changes():
            self.action_requested.emit("save_model", {"model": self.models.currentData()})

    def action_started(self, action: str, payload: dict) -> None:
        """
        Show which explicit connection operation is running without claiming it succeeded.
        """
        if action not in {"connect_chatgpt", "save_model"}:
            return
        self._active_action = "sign_in" if payload.get("force_sign_in") else action
        self._feedback_success = False
        self.feedback.clear()
        self.feedback.hide()
        self._update_controls()

    def action_failed(self, action: str, message: str) -> None:
        """
        Keep the staged selection available after a failed or interrupted connection operation.
        """
        if action not in {"connect_chatgpt", "save_model"}:
            return
        self._active_action = ""
        self._checked_online = False
        self._feedback_success = False
        self.feedback.setText(message)
        self.feedback.show()
        self._update_controls()

    def action_completed(self, action: str, result=None) -> None:
        """
        Accept an online check only when it returns available models, preserving cancelled selections.
        """
        if action not in {"connect_chatgpt", "save_model"}:
            return
        self._active_action = ""
        data = (result or {}).get("data", result or {})
        models = data.get("models") if isinstance(data, dict) else None
        if not models:
            self._checked_online = False
            self._feedback_success = False
            self.feedback.setText((result or {}).get("message") or "Connection check did not complete.")
            self.feedback.show()
            self._update_controls()
            return
        staged = (
            self.models.currentData() if self.has_unsaved_changes() and action != "save_model" else None
        )
        self._saved_model = data.get("model")
        self._auth = {"signed_in": True, "model": self._saved_model, "expired": False}
        self._checked_online = True
        self._models_loaded = True
        self._feedback_success = True
        self.models.blockSignals(True)
        self.models.clear()
        for model in models:
            self.models.addItem(self._model_label(model["slug"]), model["slug"])
            self.models.setItemData(self.models.count() - 1, model["slug"], Qt.ItemDataRole.ToolTipRole)
        self.models.setCurrentIndex(self.models.findData(staged or self._saved_model))
        self.models.setPlaceholderText("Choose a model")
        self.models.blockSignals(False)
        if staged and self.models.findData(staged) < 0:
            self._feedback_success = False
            self.feedback.setText("Your previous selection is no longer available. Choose another model.")
        else:
            self.feedback.clear()
        self.feedback.setVisible(bool(self.feedback.text()))
        self._update_controls()

    def state_info(self) -> dict:
        """
        Describe the visible connection and draft model for Setup without performing an online check.
        """
        return {
            "status": self.status.text(), "checked_online": self._checked_online,
            "signed_in": bool(self._auth.get("signed_in")), "dirty": self.has_unsaved_changes(),
            "saved_model": self._model_label(self._saved_model) if self._saved_model else "",
            "selected_model": self.models.currentText(), "models_loaded": self._models_loaded,
        }

    def _update_controls(self, *_args) -> None:
        """
        Distinguish saved access from verified access and report changes in the staged model once.
        """
        signed_in = bool(self._auth.get("signed_in"))
        if self._active_action:
            signing_in = self._active_action == "sign_in" or not signed_in
            message = "Signing in…" if signing_in else "Checking connection…"
            self.status.setText("Saving model…" if self._active_action == "save_model" else message)
            self.connection_detail.setText(
                "Follow the sign-in instructions if your browser opens." if signing_in
                else "Confirming access with ChatGPT."
            )
        elif self._auth.get("error"):
            self.status.setText("Check your connection settings")
            self.connection_detail.setText(str(self._auth["error"]))
        elif self._checked_online:
            self.status.setText("Connected · checked this session")
            self.connection_detail.setText("Your available models are ready to choose below.")
        elif signed_in:
            self.status.setText("Sign-in saved on this device")
            self.connection_detail.setText(
                "Check the connection to confirm access and load models."
                + (" Saved access will renew when checked." if self._auth.get("expired") else "")
            )
        else:
            self.status.setText("Not signed in")
            self.connection_detail.setText("Sign in with your ChatGPT account through your browser.")
        self.connect_button.setText("" if signed_in else "Sign in with ChatGPT")
        self.connect_button.setIcon(icon("refresh" if signed_in else "chat", "#ffffff", 18))
        self.connect_button.setAccessibleName("Check connection" if signed_in else "Sign in with ChatGPT")
        self.connect_button.setToolTip(self.connect_button.accessibleName())
        self.connect_button.setMinimumWidth(36 if signed_in else 0)
        self.connect_button.setMaximumWidth(36 if signed_in else 16777215)
        self.connect_button.setStyleSheet("padding: 0;" if signed_in else "")
        self.connect_button.setEnabled(not self._busy)
        self.reconnect.setVisible(signed_in)
        self.reconnect.setEnabled(not self._busy)
        self.models.setEnabled(self._models_loaded and not self._busy)
        dirty = self.has_unsaved_changes()
        self.save.setEnabled(self._models_loaded and not self._busy and dirty)
        name = self._model_label(self._saved_model) if self._saved_model else ""
        self.saved_model.setText(f"Current model: {name}" if name else "No model saved yet.")
        self.saved_model.setVisible(not self._models_loaded)
        if not self._models_loaded:
            message = ""
        elif dirty:
            message = "Unsaved selection" + (f" · Current model: {name}" if name else "")
        elif not self.models.currentData():
            message = "Choose an available model, then save your selection."
        else:
            message = ""
        self.selection_status.setText(message)
        self.selection_status.setVisible(bool(message))
        selection = (self.models.currentData(), self._saved_model, dirty)
        if selection != self._reported_selection:
            self._reported_selection = selection
            self.changed.emit()

    def _icon_button(self, symbol: str, label: str, *, primary: bool = False) -> QPushButton:
        """
        Keep compact actions keyboard accessible and identifiable through a descriptive tooltip.
        """
        button = QPushButton()
        button.setObjectName("chatGPTIconButton")
        button.setFixedSize(36, 36)
        button.setProperty("primary", primary)
        button.setIcon(icon(symbol, "#ffffff" if primary else "#355653", 18))
        button.setStyleSheet("QPushButton#chatGPTIconButton { padding: 0; }")
        button.setToolTip(label)
        button.setAccessibleName(label)
        return button

    @staticmethod
    def _model_label(slug: str) -> str:
        """
        Present model identifiers as readable names while preserving their exact stored slug elsewhere.
        """
        parts = str(slug).split("-")
        if parts[0].lower() == "gpt" and len(parts) > 1:
            label = "GPT " + parts[1]
            return label + (" - " + " ".join(part.title() for part in parts[2:]) if len(parts) > 2 else "")
        return " - ".join(part.title() for part in parts)


# %% 3. PDF Reports ------------------------------------------------------------------------------------------
class ReportsPage(QWidget):
    """
    Inspect saved PDF snapshots and generate reports through an explicit workspace action.
    """

    action_requested = Signal(str, object)

    def __init__(self, parent=None) -> None:
        """
        Put generation and a compact report picker above one spacious PDF preview.
        """
        super().__init__(parent)
        self.paths = []
        self.loaded = None
        self._busy = False
        self._workspace_available = False
        self._workspace_error = ""
        self._month = None
        self._draft_reasons = []
        self._generation_files = {}
        self._generated = False
        self._generation_notice = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        heading_row = QHBoxLayout()
        title_block = QVBoxLayout()
        title_block.setContentsMargins(0, 0, 0, 24)
        title_block.setSpacing(0)
        heading = QLabel("Reports")
        heading.setObjectName("workspacePageTitle")
        self.title_row = QHBoxLayout()
        self.title_row.addWidget(heading)
        self.title_row.addStretch()
        title_block.addLayout(self.title_row)
        subtitle = QLabel("Preview and save your monthly financial reports.")
        subtitle.setObjectName("workspacePageSubtitle")
        subtitle.setWordWrap(False)
        subtitle_row = PageActionRow()
        subtitle_row.setContentsMargins(0, 0, 14, 0)
        subtitle_row.addWidget(subtitle, 1, Qt.AlignmentFlag.AlignTop)
        title_block.addLayout(subtitle_row)
        heading_row.addLayout(title_block, 1)
        self.generate = HeaderActionButton("Generate")
        self.generate.setToolTip("Save all four PDFs for the selected reporting month.")
        self.generate.clicked.connect(lambda: self.action_requested.emit("generate_reports", {}))
        subtitle_row.addWidget(self.generate, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(heading_row)
        self.notice = QLabel(self._generation_notice)
        self.notice.setObjectName("workspaceMessage")
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.hide()
        layout.addWidget(self.notice)

        preview_layout = QVBoxLayout()
        preview_layout.setSpacing(8)
        layout.addLayout(preview_layout, 1)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self.kind = QComboBox()
        self.kind.addItems(["Balance sheet", "Income statement", "Budget", "Investments"])
        self.kind.setMinimumWidth(195)
        self.kind.setAccessibleName("Report type")
        self.kind.setToolTip("Choose a saved report to preview.")
        self.kind.setObjectName("reportTypePicker")
        self.kind.currentIndexChanged.connect(self._show_pdf)
        toolbar.addWidget(self.kind)
        self.fit = QToolButton()
        self.fit.setObjectName("viewerIconAction")
        self.fit.setIcon(icon("fit", size=18))
        self.fit.setFixedSize(34, 34)
        self.fit.setToolTip("Fit report to width")
        self.fit.setAccessibleName("Fit report to width")
        self.fit.clicked.connect(lambda: self.viewer.setZoomMode(QPdfView.ZoomMode.FitToWidth))
        toolbar.addWidget(self.fit)
        self.zoom_buttons = []
        for label, factor, tooltip in (("−", 0.85, "Zoom out"), ("+", 1.18, "Zoom in")):
            button = QPushButton(label)
            button.setFixedWidth(32)
            button.setToolTip(tooltip)
            button.clicked.connect(lambda checked=False, scale=factor: self._zoom(scale))
            self.zoom_buttons.append(button)
            toolbar.addWidget(button)
        toolbar.addStretch()
        preview_layout.addLayout(toolbar)
        self.preview_stack = QStackedWidget()
        self.document = QPdfDocument(self)
        self.document.statusChanged.connect(self._document_status)
        self.viewer = QPdfView(self)
        self.viewer.setDocument(self.document)
        self.viewer.setPageMode(QPdfView.PageMode.MultiPage)
        self.viewer.setZoomMode(QPdfView.ZoomMode.FitToWidth)
        self.preview_stack.addWidget(self.viewer)
        self.empty_preview = QLabel()
        self.empty_preview.setObjectName("workspaceInset")
        self.empty_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_preview.setWordWrap(True)
        self.empty_preview.setMargin(24)
        self.empty_preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview_stack.addWidget(self.empty_preview)
        preview_layout.addWidget(self.preview_stack, 1)
        self._show_pdf()

    def refresh(self, snapshot: dict) -> None:
        """
        Read saved output without generating it, preserving the selected report and unchanged preview.
        """
        self._workspace_available = bool(snapshot.get("ready"))
        self._workspace_error = str(
            snapshot.get("error") or (
                "Complete workspace setup before generating reports." if snapshot.get("missing_files")
                else "Workspace details are unavailable. Refresh the workspace to try again."
            )
        )
        month = snapshot.get("month")
        paths = list(snapshot.get("report_paths", [])) if self._workspace_available else []
        if month != self._month or (self._workspace_available and paths != self.paths):
            self._draft_reasons = []
            self._generation_files = {}
            self._generated = False
            self._generation_notice = ""
        self._month = month
        self.paths = paths
        self._show_destination()
        self._show_notice(
            self._generation_notice if self._workspace_available else self._workspace_error
        )
        self._show_pdf()

    def action_started(self, action: str, payload: dict) -> None:
        """
        Show generation progress without changing the currently saved snapshot.
        """
        if action == "generate_reports":
            self._show_notice("Generating reports…")

    def action_completed(self, action: str, result=None) -> None:
        """
        Acknowledge only completed generation and flag changes that can affect later report output.
        """
        if action == "import_statements" and isinstance(result, list) and result:
            self.mark_stale()
            return
        result = result if isinstance(result, dict) else {}
        if action != "generate_reports":
            identifiers = {
                "apply_journal_edit": "journal_entry_id", "save_review": "transaction_id",
                "save_reviews": "transaction_ids",
                "discard_review": "transaction_id", "save_manual": "manual_entry_id",
            }
            acknowledged = bool(result.get(identifiers[action])) if action in identifiers else False
            acknowledged = acknowledged or (
                action in {"process_journal", "import_statements"}
                and "data" in result
            )
            if acknowledged:
                self.mark_stale()
            return
        paths = result.get("paths")
        if not isinstance(paths, (list, tuple)) or len(paths) != 4 or not all(paths):
            self._generation_notice = "Generation did not finish. Previously saved PDFs are unchanged."
            self._show_notice(self._generation_notice)
            return
        self.paths = list(paths)
        self._generation_files = self._report_signatures()
        self._generated = True
        self._draft_reasons = list(dict.fromkeys(result.get("draft_reasons") or []))
        self._generation_notice = (
            "Draft reports · Resolve outstanding items in Review."
            if self._draft_reasons else ""
        )
        self._show_notice(self._generation_notice)
        self._show_destination()
        self._show_pdf()

    def action_failed(self, action: str, message: str) -> None:
        """
        Keep saved files viewable after generation errors and block writes if workspace refresh fails.
        """
        if action == "generate_reports":
            self._generation_notice = f"Reports could not be generated. {message}"
            self._show_notice(self._generation_notice)
        elif action == "refresh":
            self._workspace_available = False
            self._workspace_error = f"Workspace details could not be refreshed. {message}"
            self._show_notice(self._workspace_error)
        elif action in {"process_journal", "import_statements"}:
            self.mark_stale("Workspace processing stopped with an error. Refresh, then regenerate reports.")
        self._update_actions()

    def mark_stale(
        self, message: str = "Changes ready · Generate to update reports.",
    ) -> None:
        """
        Replace any generation status after accounting or report configuration changes.
        """
        self._generation_notice = message
        self._show_notice(message)

    def set_busy(self, busy: bool) -> None:
        """
        Prevent generation during a workspace operation while allowing saved PDF inspection.
        """
        self._busy = busy
        self._update_actions()

    def _show_notice(self, message: str) -> None:
        """
        Reserve page space only for current progress, stale output or an actionable problem.
        """
        self.notice.setText(message)
        self.notice.setVisible(bool(message))

    def _show_destination(self) -> None:
        """
        Keep the output folder available on the Generate tooltip without adding a path label.
        """
        text = f"Generate all four reports in {Path(self.paths[0]).parent}" if self.paths else (
            "Generate all four reports for the selected month."
        )
        self.generate.setToolTip(text)

    def _show_pdf(self) -> None:
        """
        Reload changed files and handle missing or inaccessible PDFs without retaining a stale preview.
        """
        if self._generated and self._generation_files != self._report_signatures():
            self._generated = False
            self._draft_reasons = []
            self._generation_files = {}
            self.mark_stale("Saved PDF files changed. Generate again to check their draft reasons.")
        index = self.kind.currentIndex()
        path = Path(self.paths[index]) if len(self.paths) > index >= 0 else None
        try:
            info = path.stat() if path is not None else None
            if info is not None and not stat.S_ISREG(info.st_mode):
                self._clear_preview("The configured report path is not a PDF file.")
                return
        except FileNotFoundError:
            info = None
        except OSError as error:
            self._clear_preview(f"The saved PDF could not be read. {error}")
            return
        if info is None:
            if path is None:
                message = "Load the workspace to locate saved PDFs."
            else:
                message = (
                    f"No saved {self.kind.currentText().lower()} PDF for this month.\n"
                    "Choose Generate to create your reports."
                )
            self._clear_preview(message)
            return
        signature = (str(path), info.st_mtime_ns, info.st_size)
        self.viewer.setToolTip(str(path))
        if signature == self.loaded:
            self._update_actions()
            return
        self.loaded = None
        self.document.close()
        self.loaded = signature
        self._preview_message("Loading saved PDF…")
        error = self.document.load(str(path))
        if error != QPdfDocument.Error.None_:
            self._preview_message(
                f"PDF preview could not load this file ({error.name}). Generate the reports again."
            )
        else:
            self._document_status(self.document.status())
        self._update_actions()

    def _report_signatures(self) -> dict:
        """
        Recognise file replacement without attributing earlier draft findings to new saved PDFs.
        """
        signatures = {}
        for value in self.paths:
            try:
                info = Path(value).stat()
                signatures[value] = (info.st_mtime_ns, info.st_size)
            except OSError:
                signatures[value] = None
        return signatures

    def _clear_preview(self, message: str) -> None:
        """
        Clear the previous file and its timestamp before showing an empty or failed state.
        """
        self.loaded = None
        self.document.close()
        self.viewer.setToolTip("")
        self._preview_message(message)
        self._update_actions()

    def _preview_message(self, message: str) -> None:
        """
        Replace the PDF viewport with a readable local preview status.
        """
        self.empty_preview.setText(message)
        self.preview_stack.setCurrentWidget(self.empty_preview)

    def _document_status(self, status: QPdfDocument.Status) -> None:
        """
        Account for asynchronous PDF load failures as well as immediate load results.
        """
        if self.loaded is None:
            return
        if status == QPdfDocument.Status.Ready:
            self.preview_stack.setCurrentWidget(self.viewer)
        elif status == QPdfDocument.Status.Error:
            self._preview_message(
                f"PDF preview could not load this file ({self.document.error().name}). Generate again."
            )
        self._update_actions()

    def _update_actions(self) -> None:
        """
        Keep write actions tied to workspace readiness and file controls tied to the selected saved file.
        """
        self.generate.setEnabled(self._workspace_available and not self._busy)
        has_file = self.loaded is not None
        preview_ready = has_file and self.document.status() == QPdfDocument.Status.Ready
        self.fit.setEnabled(preview_ready)
        for button in self.zoom_buttons:
            button.setEnabled(preview_ready)

    def _zoom(self, factor: float) -> None:
        """
        Switch from fit mode to a bounded manual zoom.
        """
        zoom = self.viewer.zoomFactor()
        self.viewer.setZoomMode(QPdfView.ZoomMode.Custom)
        self.viewer.setZoomFactor(min(4.0, max(0.25, zoom * factor)))

# %% 4. Helpers ----------------------------------------------------------------------------------------------
def _overview_work(snapshot: dict) -> dict[str, int]:
    """
    Separate queued work from questions and retries, giving current human answers precedence.

    A saved answer can await processing while its original transaction still says needs_review or failed.
    Count each bank row once, and leave events owned by a manual instruction on the manual side.
    """
    work = {f"{source}_{status}": 0 for source in ("bank", "manual")
            for status in ("queued", "attention", "failed")}
    selected_manual_ids = {row.get("manual_entry_id") for row in snapshot.get("manuals", [])}
    failures = {"failed", "requesting", "interrupted"}
    for row in snapshot.get("reviews", []):
        status = row.get("status")
        review = row.get("review_status")
        blocker = row.get("blocking_manual_entry_id")
        if blocker:
            if blocker not in selected_manual_ids:
                work["bank_attention"] += 1
            continue
        if review == "manual_entry" or row.get("manual_entry_id"):
            continue
        if review in {"pending", "submitted"}:
            group = "queued"
        elif review in failures:
            group = "failed"
        elif review in {"needs_review", "stale"}:
            group = "attention"
        elif status in failures:
            group = "failed"
        elif status == "needs_review":
            group = "attention"
        elif status != "posted":
            group = "queued"
        else:
            continue
        work[f"bank_{group}"] += 1
    for row in snapshot.get("manuals", []):
        status = row.get("status")
        if status == "pending":
            work["manual_queued"] += 1
        elif status in {"needs_review", "unsupported"}:
            work["manual_attention"] += 1
        elif status in failures:
            work["manual_failed"] += 1
    work["investment"] = len(snapshot.get("investment_reviews", []))
    return work


def _overview_count(count: int, singular: str, plural: str | None = None) -> str:
    """
    Keep short status sentences natural for both one item and several items.
    """
    return f"{count} {singular if count == 1 else plural or singular + 's'}"
