# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  GUI - Workspace                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Connect the desktop's overview, ChatGPT setup and native report views to explicit user actions.

These widgets display results and collect choices. Accounting, authentication and report generation are
handled by shared services in background jobs; simply visiting a screen never calls AI or changes accounting.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QEvent, QLineF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QPainter, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QListView,
    QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.controls import (
    HeaderActionButton, PageActionRow, QuoteBlock, ReportSegmentedControl, confirm_discard,
    reduced_motion, watch_reduced_motion,
)
from aurum.gui.icons import connection_button, icon
from aurum.gui.theme import gui_colour


# %% 1. Monthly Overview -------------------------------------------------------------------------------------
class OverviewPage(QWidget):
    """
    Present posted finances and let each workflow stage open its corresponding page.
    """

    navigate = Signal(str)

    def __init__(self, parent=None) -> None:
        """
        Lead with financial position, then group monthly cash flow and the work still to finish.
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

        layout.insertLayout(0, header)

        self.financials = QFrame()
        self.financials.setObjectName("overviewFinancials")
        self.metrics = QGridLayout(self.financials)
        self.metrics.setContentsMargins(24, 24, 24, 24)
        self.metrics.setHorizontalSpacing(24)
        self.metrics.setVerticalSpacing(20)
        self.monthly = QFrame()
        self.monthly.setObjectName("overviewMonthly")
        monthly_layout = QVBoxLayout(self.monthly)
        monthly_layout.setContentsMargins(0, 0, 0, 0)
        monthly_layout.setSpacing(0)
        self.financial_divider = QFrame()
        self.financial_divider.setObjectName("overviewFinancialDivider")
        self.cards, self.values, self.captions = [], {}, {}
        for key, name, symbol, caption in (
            ("net_worth", "Net Worth", "wallet", "Assets less liabilities"),
            ("income", "Income", "income", "—"),
            ("expenses", "Expenses", "expenses", "—"),
            ("savings", "Total savings", "savings", "Income less expenses"),
        ):
            frame = QFrame()
            position = key == "net_worth"
            frame.setObjectName("overviewNetWorth" if position else "overviewMetric")
            frame.setProperty("separator", key in {"income", "expenses"})
            label = QLabel(name)
            label.setObjectName("overviewMetricLabel")
            value = QLabel("—")
            value.setObjectName("overviewNetWorthValue" if position else "overviewValue")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            detail = QLabel(caption)
            detail.setObjectName("overviewCaption")
            detail.setWordWrap(True)
            detail.setVisible(bool(caption))
            if key in {"income", "expenses"}:
                detail.setToolTip(
                    f"Transactions included in this month's {name.lower()}. Split postings count once."
                )
            if position:
                box = QVBoxLayout(frame)
                box.setContentsMargins(0, 12, 0, 12)
                box.setSpacing(12)
                box.addStretch()
                top = QHBoxLayout()
                top.setSpacing(8)
                top.addStretch()
                top.addWidget(self._symbol(symbol, 22))
                top.addWidget(label)
                top.addStretch()
                box.addLayout(top)
                value.setAlignment(Qt.AlignmentFlag.AlignCenter)
                detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
                box.addWidget(value)
                box.addWidget(detail)
                box.addStretch()
            else:
                box = QHBoxLayout(frame)
                box.setContentsMargins(0, 12, 0, 12)
                box.setSpacing(12)
                box.addWidget(self._symbol(symbol, 22))
                description = QVBoxLayout()
                description.setSpacing(3)
                description.addWidget(label)
                description.addWidget(detail)
                box.addLayout(description, 1)
                value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                box.addWidget(value)
                monthly_layout.addWidget(frame)
            self.cards.append(frame)
            self.values[key], self.captions[key] = value, detail
        self._arrange_metrics(2)
        body.addWidget(self.financials)

        process = QFrame()
        process.setObjectName("overviewProcess")
        process_layout = QVBoxLayout(process)
        process_layout.setContentsMargins(24, 20, 24, 20)
        process_layout.setSpacing(16)
        flow = QHBoxLayout()
        flow.setSpacing(8)
        self.stages = {}
        for number, (key, name, symbol) in enumerate((
            ("sources", "Sources", "statements"), ("journal", "Journal", "journal"),
            ("review", "Review", "review"), ("reports", "Reports", "reports"),
        ), 1):
            if number > 1:
                flow.addWidget(self._symbol("right", 16))
            frame = _WorkflowStage(f"{number} · {name}")
            frame.setObjectName("overviewStage")
            destination = "Transactions" if key == "sources" else name
            frame.clicked.connect(lambda checked=False, page=destination: self.navigate.emit(page))
            stage = QVBoxLayout(frame)
            stage.setContentsMargins(16, 16, 16, 16)
            stage.setSpacing(6)
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
            stage.addSpacing(4)
            stage.addWidget(frame.title)
            value = QLabel("—")
            value.setObjectName("overviewStageValue")
            value.setWordWrap(True)
            stage.addWidget(value)
            stage.addStretch()
            for child in frame.findChildren(QLabel):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            flow.addWidget(frame, 1)
            self.stages[key] = frame, state, value, emblem, symbol
        process_layout.addLayout(flow)
        body.addWidget(process)
        self.error = QLabel()
        self.error.setObjectName("workspaceError")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        self.error.hide()
        body.addWidget(self.error)

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
        self.captions["net_worth"].setText("—")
        self.captions["net_worth"].setToolTip("")
        self.error.clear()
        self.error.hide()

    def refresh(self, snapshot: dict) -> None:
        """
        Infer workflow completion from saved work and the selected month's report files.
        """
        self.error.clear()
        self.error.hide()
        self.captions["net_worth"].setToolTip("")
        for key in ("income", "expenses"):
            self.captions[key].setText("—")
        if not snapshot.get("ready"):
            for value in self.values.values():
                value.setText("—")
                value.setToolTip("")
            self.captions["net_worth"].setText("—")
            missing = bool(snapshot.get("missing_files"))
            for key in self.stages:
                self._set_stage(key, "waiting", "WAITING", "—", "Workspace not ready")
            self.error.setText(
                snapshot.get("error") or ("Add accounts in Setup." if missing else "Refresh the workspace."),
            )
            self.error.show()
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
        for key in ("income", "expenses", "savings"):
            if key == "savings":
                self.captions[key].setText("Income less expenses")
            if snapshot.get("budget_provisional"):
                self.captions[key].setText(self.captions[key].text() + " · Review")
                self.values[key].setToolTip(
                    "Known cash flows only. Resolve the budget items in Review to complete these totals."
                )
        cutoff = snapshot.get("cutoff", snapshot.get("period_end", ""))
        try:
            cutoff = datetime.fromisoformat(cutoff).strftime("%d %b %Y").lstrip("0")
        except (TypeError, ValueError):
            cutoff = "the reporting cutoff"
        unpriced = snapshot.get("net_worth_unpriced_holdings", [])
        caption = f"As of {cutoff}" + (" · Provisional" if unpriced else "")
        prices = snapshot.get("net_worth_price_dates", [])
        self.captions["net_worth"].setText(caption)
        valuation = (
            "Uses the balance sheet's calculation and the latest valid saved market prices "
            "on or before the reporting cutoff."
        )
        for item in prices:
            price_date = datetime.fromisoformat(item["price_date"]).strftime("%d %b %Y").lstrip("0")
            valuation += f"\n{item['name']}: {price_date}"
        if prices:
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
        questions = work["bank_attention"] + work["manual_attention"] + len(snapshot.get("budget_reviews", []))
        feed_state = snapshot.get("bank_feed", {})
        feed_conflicts = len(feed_state.get("review_conflicts", feed_state.get("conflicts", [])))
        attention = questions + failed + work["investment"] + feed_conflicts
        investment_error = str(snapshot.get("investment_reviews_error") or "")
        imported = len(snapshot.get("transactions", []))
        manuals = len(snapshot.get("manuals", []))
        posted = len(snapshot.get("entries", []))
        checked_feeds = any(
            item.get("last_success") and not item.get("error")
            and str(item.get("checked_through") or "") >= str(snapshot.get("cutoff") or "")
            for item in snapshot.get("bank_feed", {}).get("mappings", [])
        )
        activity = bool(imported or manuals or posted or checked_feeds)
        report_paths = tuple(dict.fromkeys(snapshot.get("report_paths", [])))
        saved = sum(Path(path).is_file() for path in report_paths)
        previous_steps_complete = activity and not (queued or attention or investment_error)
        reports_complete = previous_steps_complete and len(report_paths) == 4 and saved == 4
        completion = snapshot.get("statement_completion", {})
        source_details = [_overview_count(manuals, "manual instruction")]
        outside = snapshot.get("counts", {}).get("linked_outside_month", 0)
        if outside:
            source_details.append(
                f"Includes {_overview_count(outside, 'linked transaction')} outside this month."
            )
        if completion.get("message"):
            source_details.append(completion["message"])
        if not activity:
            source_details.append("Import statements or add a manual entry.")
        self._set_stage(
            "sources", "done" if activity else "active", "ADDED" if activity else "START HERE",
            f"{imported} imported", "\n".join(source_details),
        )
        journal_detail = (
            _overview_count(queued + failed, "item to process", "items to process") if queued or failed
            else _overview_count(questions, "answer needed", "answers needed") if questions
            else "No work queued"
        )
        auth = snapshot.get("auth", {})
        if (queued or failed) and (
            auth.get("error") or not auth.get("signed_in") or not auth.get("model")
        ):
            journal_detail += "\nCheck your ChatGPT connection and saved model in Setup before processing."
        self._set_stage(
            "journal", "attention" if failed else "active" if queued or questions
            else "done" if activity else "waiting",
            "WAITING" if queued or failed or questions
            else "PREPARED" if activity else "WAITING",
            _overview_count(posted, "posted event"),
            journal_detail,
        )
        self._set_stage(
            "review", "attention" if investment_error or questions or work["investment"] or feed_conflicts
            else "waiting" if queued or failed
            or not activity else "done",
            "UNAVAILABLE" if investment_error else "ATTENTION" if questions or work["investment"] or feed_conflicts
            else "PENDING" if queued or failed
            or not activity else "CLEAR",
            "—" if investment_error else
            _overview_count(questions + work["investment"] + feed_conflicts, "item to check", "items to check"),
            _overview_count(questions, "known question") + "\nInvestment review could not load: " + investment_error
            if investment_error and questions else
            "Investment review could not load: " + investment_error if investment_error else
            _overview_count(feed_conflicts, "bank import issue") if feed_conflicts else
            _overview_count(questions, "question") + f" · {work['investment']} investment"
            if questions or work["investment"] else "Check again after processing" if queued or failed
            else "No outstanding questions",
        )
        report_detail = "Complete earlier steps" if not previous_steps_complete else "Ready to generate"
        if reports_complete:
            report_detail = (
                completion.get("message") or "Final statement awaited."
                if completion.get("pending") else "All done. All four reports are saved."
            )
        if investment_error:
            report_detail = "Investment review unavailable"
        self._set_stage(
            "reports", "done" if reports_complete else "active" if previous_steps_complete else "waiting",
            "COMPLETE" if reports_complete else "WAITING", f"{saved} of 4 PDFs", report_detail,
        )

    def action_failed(self, action: str, message: str) -> None:
        """
        Replace the loading placeholder with an actionable refresh failure.
        """
        if action == "refresh":
            self.refresh({"ready": False, "error": message})

    def resizeEvent(self, event) -> None:
        """
        Keep position and monthly activity together, stacking them only in narrow content areas.
        """
        super().resizeEvent(event)
        self._arrange_metrics(1 if self.width() < 700 else 2)

    def _arrange_metrics(self, columns: int) -> None:
        """
        Reflow the two financial groups without rebuilding values or changing their accounting.
        """
        if columns == self._metric_columns:
            return
        for widget in (self.cards[0], self.financial_divider, self.monthly):
            self.metrics.removeWidget(widget)
        self.metrics.addWidget(self.cards[0], 0, 0)
        if columns == 2:
            self.financial_divider.setFixedSize(1, 160)
            self.metrics.addWidget(self.financial_divider, 0, 1, Qt.AlignmentFlag.AlignVCenter)
            self.metrics.addWidget(self.monthly, 0, 2)
        else:
            self.financial_divider.setMinimumSize(0, 1)
            self.financial_divider.setMaximumSize(16777215, 1)
            self.metrics.addWidget(self.financial_divider, 1, 0)
            self.metrics.addWidget(self.monthly, 2, 0)
        for index in range(3):
            self.metrics.setColumnStretch(index, 1 if index == 0 or index == 2 and columns == 2 else 0)
        self._metric_columns = columns

    def _set_stage(self, key: str, tone: str, state: str, value: str, detail: str) -> None:
        """
        Show each stage's title and count, retaining supporting details in its tooltip and accessibility text.
        """
        frame, badge, number, emblem, symbol = self.stages[key]
        frame.setProperty("tone", tone)
        for widget in (frame, *frame.findChildren(QLabel)):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        number.setText(value)
        frame.setToolTip(detail)
        role = "sidebar_text" if tone == "done" else "icon"
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
        destination = "Transactions" if key == "sources" else key.capitalize()
        frame.setAccessibleName(f"Open {destination}. {state.lower()}. {value}.")
        frame.setAccessibleDescription(detail)

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


class _WorkflowStage(QPushButton):
    """
    Make the whole stage a keyboard-accessible link with a quiet hover and focus cue.
    """

    def __init__(self, caption: str) -> None:
        """
        Retain ordinary button activation while drawing content through the stage's child layout.
        """
        super().__init__()
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAutoDefault(False)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self._hovered = False
        self.title = _StageTitle(caption, self)

    def sizeHint(self) -> QSize:
        """
        Size the card to its full child layout instead of an empty native button label.
        """
        return self.layout().sizeHint() if self.layout() is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        """
        Keep the stage's icon and both text rows visible at compact window sizes.
        """
        return self.layout().minimumSize() if self.layout() is not None else super().minimumSizeHint()

    def event(self, event: QEvent) -> bool:
        """
        Emphasize the title on hover or keyboard focus without introducing another status colour.
        """
        result = super().event(event)
        kind = event.type()
        if kind in {QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.FocusIn, QEvent.Type.FocusOut}:
            if kind in {QEvent.Type.Enter, QEvent.Type.Leave}:
                self._hovered = kind == QEvent.Type.Enter
            self.title.emphasize(self._hovered or self.hasFocus())
        return result


class _StageTitle(QLabel):
    """
    Fade the workflow link underline without moving its label or changing the card's colour.
    """

    def __init__(self, caption: str, parent: QWidget) -> None:
        """
        Keep the native label's typography, wrapping and accessibility.
        """
        super().__init__(caption, parent)
        self.setObjectName("overviewStageTitle")
        self.setWordWrap(True)
        self._emphasized = False
        self._opacity = 0.0
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(100)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._paint_opacity)
        watch_reduced_motion(self._snap_underline)

    def emphasize(self, active: bool) -> None:
        """
        Retarget a hover or keyboard-focus transition from the underline's current opacity.
        """
        if active == self._emphasized:
            return
        self._emphasized = active
        self._fade.stop()
        if not self.isVisible() or reduced_motion(self):
            self._snap_underline()
            return
        self._fade.setStartValue(self._opacity)
        self._fade.setEndValue(float(active))
        self._fade.start()

    def _snap_underline(self) -> None:
        """
        Apply the requested emphasis immediately when the motion preference changes.
        """
        self._fade.stop()
        self._paint_opacity(float(self._emphasized))

    def _paint_opacity(self, opacity: float) -> None:
        """
        Invalidate only the title while its underline fades.
        """
        self._opacity = opacity
        self.update()

    def paintEvent(self, event) -> None:
        """
        Underline each visible line using its actual font, text colour and wrapped width.
        """
        super().paintEvent(event)
        if self._opacity == 0.0:
            return
        bounds = self.contentsRect()
        layout = QTextLayout(self.text(), self.font(), self)
        options = QTextOption()
        options.setWrapMode(QTextOption.WrapMode.WordWrap)
        layout.setTextOption(options)
        layout.beginLayout()
        lines = []
        height = 0.0
        while (line := layout.createLine()).isValid():
            line.setLineWidth(bounds.width())
            y = height + line.ascent() + self.fontMetrics().underlinePos()
            lines.append(QLineF(0, y, line.naturalTextWidth(), y))
            height += line.height()
        layout.endLayout()
        painter = QPainter(self)
        painter.translate(bounds.x(), bounds.y() + int((bounds.height() - height) / 2))
        painter.setPen(QPen(self.palette().windowText().color(), self.fontMetrics().lineWidth()))
        painter.setOpacity(self._opacity)
        painter.drawLines(lines)


# %% 2. ChatGPT Setup ----------------------------------------------------------------------------------------
class ChatGPTPage(QWidget):
    """
    Keep ChatGPT connection actions and an independently saved model inline in Accounts.
    """

    action_requested = Signal(str, object)
    cancel_requested = Signal()
    changed = Signal()

    def __init__(self, parent=None) -> None:
        """
        Preserve connection and model drafts across ordinary Setup form reloads.
        """
        super().__init__(parent)
        self._auth = {}
        self._busy = False
        self._saved_model = None
        self._models_loaded = False
        self._available_models = []
        self._checked_online = False
        self._active_action = ""
        self._error = ""
        self._submitted_model = None
        self._reported_selection = None
        self.setObjectName("setupConnectionRow")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        identity = QWidget()
        identity.setObjectName("connectionIdentity")
        identity.setFixedWidth(128)
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(2)
        title = QLabel("ChatGPT")
        title.setObjectName("setupConnectionTitle")
        identity_layout.addWidget(title)
        self.status = QLabel()
        self.status.setObjectName("connectionStatus")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        identity_layout.addWidget(self.status)
        row.addWidget(identity)
        self.models = QComboBox()
        self.models.setObjectName("connectionControl")
        self.models.setFixedHeight(34)
        self.models.setView(QListView(self.models))
        self.models.setAccessibleName("Processing model")
        self.models.setMinimumWidth(200)
        self.models.setMaximumWidth(300)
        self.models.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.models.setMinimumContentsLength(20)
        self.models.setPlaceholderText("Choose a model")
        self.models.currentIndexChanged.connect(self._update_controls)
        row.addWidget(self.models, 1)
        self.connect_button = connection_button("ChatGPT")
        self.connect_button.clicked.connect(lambda: self.action_requested.emit("connect_chatgpt", {}))
        self.reconnect = self._icon_button(
            "login", "Sign in again to renew access or change your ChatGPT account",
        )
        self.reconnect.clicked.connect(
            lambda: self.action_requested.emit("connect_chatgpt", {"force_sign_in": True})
        )
        self.save = self._icon_button("save", "Save model", primary=True)
        self.save.clicked.connect(self.save_changes)
        row.addWidget(self.save)
        self.cancel = self._icon_button("undo", "Discard model change")
        self.cancel.clicked.connect(self._cancel)
        row.addWidget(self.cancel)
        row.addStretch()
        row.addWidget(self.connect_button)
        row.addWidget(self.reconnect)
        layout.addLayout(row)
        self.feedback = QLabel()
        self.feedback.setObjectName("workspaceError")
        self.feedback.setWordWrap(True)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.feedback.hide()
        layout.addWidget(self.feedback)
        self.refresh({})

    def refresh(self, snapshot: dict) -> None:
        """
        Refresh local status while retaining a model choice that has not yet been saved.
        """
        staged = self.models.currentData() if self.has_unsaved_changes() else None
        self._auth = dict(snapshot.get("auth", {}))
        self._saved_model = self._auth.get("model")
        if self._auth.get("error") or not self._auth.get("signed_in"):
            self._checked_online = False
            self._models_loaded = False
            self._available_models = []
        self._populate_models(staged or self._saved_model)
        self._update_controls()

    def set_busy(self, busy: bool) -> None:
        """
        Suspend connection and model actions while the desktop is running a job.
        """
        self._busy = busy
        self._update_controls()

    def guard_close(self) -> bool:
        """
        Guard leaving Accounts without saving a connection or unrelated Setup settings implicitly.
        """
        if self._busy:
            return not self.has_unsaved_changes() and not self._active_action
        if self.has_unsaved_changes():
            if not confirm_discard(self, question="Discard model change?"):
                return False
            self.discard_changes()
        return True

    def _cancel(self) -> None:
        """
        Explicitly discard a model draft without checking access or writing saved configuration.
        """
        self.guard_close()

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
        self._error = ""
        self._populate_models(self._saved_model)
        self._update_controls()

    def save_changes(self) -> None:
        """
        Submit only an available changed model for the shared connection and save workflow.
        """
        if self._busy or not self.has_unsaved_changes():
            return
        if not self._models_loaded:
            self._error = "Check the connection before saving your model."
        elif self.models.currentData() in {item["slug"] for item in self._available_models}:
            self._submitted_model = self.models.currentData()
            self.action_requested.emit("save_model", {"model": self._submitted_model})
            return
        self._update_controls()

    def action_started(self, action: str, payload: dict) -> None:
        """
        Show which explicit connection operation is running without claiming it succeeded.
        """
        if action not in {"connect_chatgpt", "save_model"}:
            return
        self._active_action = "sign_in" if payload.get("force_sign_in") else action
        self._checked_online = False
        self._error = ""
        self._update_controls()

    def action_failed(self, action: str, message: str) -> None:
        """
        Keep the staged selection available after a failed or interrupted connection operation.
        """
        if action not in {"connect_chatgpt", "save_model"}:
            return
        self._active_action = ""
        self._submitted_model = None
        self._checked_online = False
        self._error = message
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
            self._submitted_model = None
            self._error = (result or {}).get("message") or "Connection check did not complete."
            self._update_controls()
            return
        acknowledged = action == "save_model" and self._submitted_model is not None and (
            data.get("model") == self._submitted_model
        )
        staged = self.models.currentData() if self.has_unsaved_changes() and not acknowledged else None
        if action != "save_model" or acknowledged:
            self._saved_model = data.get("model")
        self._auth = {"signed_in": True, "model": self._saved_model, "expired": False}
        self._checked_online = True
        self._models_loaded = True
        self._available_models = models
        self._error = (
            "The model was not saved. Check your selection and try again."
            if action == "save_model" and not acknowledged else ""
        )
        self._submitted_model = None
        self._populate_models(staged or self._saved_model)
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
            self.status.clear()
        elif self._auth.get("error") or self._error:
            self.status.setText("Needs attention")
        elif self._checked_online:
            self.status.setText("Connected")
        elif signed_in:
            self.status.setText("Not checked")
        else:
            self.status.setText("Not connected")
        self.status.setToolTip(
            "Access verified during this session." if self._checked_online else
            "Saved access needs renewal; check the connection." if self._auth.get("expired") else
            "Saved sign-in has not been checked during this session." if signed_in else
            "Sign in with ChatGPT to load available models."
        )
        label = "Check ChatGPT connection" if signed_in else "Sign in with ChatGPT"
        self.connect_button.setAccessibleName(label)
        self.connect_button.setToolTip(label)
        self.connect_button.setEnabled(not self._busy)
        self.reconnect.setVisible(True)
        self.reconnect.setEnabled(not self._busy)
        self.models.setEnabled(self._models_loaded and not self._busy)
        dirty = self.has_unsaved_changes()
        self.save.setEnabled(dirty and not self._busy)
        self.save.setVisible(dirty)
        self.cancel.setEnabled(not self._busy)
        self.cancel.setVisible(dirty)
        name = self._model_label(self._saved_model) if self._saved_model else ""
        self.models.setToolTip(f"Saved model · {name}" if name else "Choose a model")
        self.models.setAccessibleDescription(
            (f"Saved model: {name}." if name else "No model saved.")
            + (" Save applies only this model." if self._models_loaded
               else " Check the connection to choose a model.")
        )
        selected = self.models.currentData()
        saved_display = bool(selected) and not self._models_loaded
        if self.models.property("savedDisplay") != saved_display:
            self.models.setProperty("savedDisplay", saved_display)
            self.models.style().unpolish(self.models)
            self.models.style().polish(self.models)
        unavailable = self._models_loaded and selected and selected not in {
            item["slug"] for item in self._available_models
        }
        message = self._error or str(self._auth.get("error") or "")
        if not message and unavailable:
            message = ("Selected" if dirty else "Saved") + " model is unavailable. Choose another model."
        self.feedback.setText(message)
        self.feedback.setVisible(bool(message))
        selection = (self.models.currentData(), self._saved_model, dirty)
        if selection != self._reported_selection:
            self._reported_selection = selection
            self.changed.emit()

    def _populate_models(self, selected: str | None) -> None:
        """
        Keep saved and staged model names visible without silently replacing an unavailable choice.
        """
        self.models.blockSignals(True)
        self.models.clear()
        for model in self._available_models:
            self.models.addItem(self._model_label(model["slug"]), model["slug"])
            self.models.setItemData(self.models.count() - 1, model["slug"], Qt.ItemDataRole.ToolTipRole)
        if selected and self.models.findData(selected) < 0:
            self.models.insertItem(0, self._model_label(selected), selected)
            self.models.setItemData(0, selected, Qt.ItemDataRole.ToolTipRole)
            if self._models_loaded:
                item = self.models.model().item(0)
                item.setEnabled(False)
        self.models.setCurrentIndex(self.models.findData(selected) if selected else -1)
        self.models.blockSignals(False)

    def _icon_button(self, symbol: str, label: str, *, primary: bool = False) -> QPushButton:
        """
        Keep compact actions keyboard accessible and identifiable through a descriptive tooltip.
        """
        button = QPushButton()
        button.setObjectName("chatGPTIconButton")
        button.setFixedSize(32, 32)
        button.setProperty("primary", primary)
        button.setIcon(icon(symbol, "#ffffff" if primary else "#355653", 16))
        button.setIconSize(QSize(16, 16))
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


# %% 3. Dashboard Reports ------------------------------------------------------------------------------------
class ReportsPage(QWidget):
    """
    Restore the last generated report as native dashboard content without recalculating saved figures.
    """

    action_requested = Signal(str, object)
    TITLES = ("Balance Sheet", "Income Statement", "Budget Report", "Investment Report")
    SUBTITLES = (
        "Statement of Financial Position", "Statement of Financial Performance",
        "Summary of Budget Allocation", "Summary of Investment Holdings",
    )

    def __init__(self, parent=None) -> None:
        """
        Keep report selection and explicit generation above one continuously scrolling financial view.
        """
        super().__init__(parent)
        from aurum.gui.report_view import ReportView

        self.paths = []
        self._busy = False
        self._workspace_available = False
        self._month = None
        self._view = None
        self._appearance = {}
        self._location = None
        self._file_notice = ""
        self._stale_notice = ""
        self._failure_notice = ""
        self._workspace_error = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 16)
        header.setSpacing(0)
        self.heading = QLabel(self.TITLES[0])
        self.heading.setObjectName("workspacePageTitle")
        self.title_row = QHBoxLayout()
        self.title_row.addWidget(self.heading)
        self.title_row.addStretch()
        header.addLayout(self.title_row)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("nativeReportSubtitle")
        self.subtitle.setTextFormat(Qt.TextFormat.RichText)
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet(
            "QLabel#nativeReportSubtitle { font: 400 13px 'Geist'; padding: 0; margin: 0; }"
        )
        header.addWidget(self.subtitle)
        header.addSpacing(12)
        actions = PageActionRow()
        actions.setSpacing(10)
        self.generate = HeaderActionButton("Generate", icon_only=True)
        self.generate.clicked.connect(lambda: self.action_requested.emit("generate_reports", {}))
        actions.addWidget(self.generate)
        self.kind = ReportSegmentedControl()
        self.kind.setAccessibleName("Report type")
        self.kind.setToolTip("Choose report")
        self.kind.currentIndexChanged.connect(self._show_report)
        actions.addWidget(self.kind)
        actions.addStretch()
        header.addLayout(actions)
        layout.addLayout(header)
        self.notice = QLabel()
        self.notice.setObjectName("workspaceMessage")
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.hide()
        layout.addWidget(self.notice)
        self.report_stack = QStackedWidget()
        self.viewer = ReportView()
        self.report_stack.addWidget(self.viewer)
        self.empty = QLabel()
        self.empty.setObjectName("workspaceInset")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setMargin(24)
        self.empty.setTextFormat(Qt.TextFormat.PlainText)
        self.empty.setAccessibleName("No saved dashboard reports")
        self.empty.setToolTip("Generate to create this month's dashboard reports.")
        self.report_stack.addWidget(self.empty)
        layout.addWidget(self.report_stack, 1)
        QWidget.setTabOrder(self.generate, self.kind)
        self._show_report()

    def refresh(self, snapshot: dict) -> None:
        """
        Reload only a published dashboard snapshot, never replacing it with newly calculated accounting.
        """
        month = snapshot.get("month")
        paths = list(snapshot.get("report_paths", []))
        if month == self._month and not snapshot.get("ready") and "report_view" not in snapshot:
            paths = self.paths
        location = str(Path(paths[0]).parent) if paths else None
        changed = (month, location) != (self._month, self._location)
        if changed:
            self._view = None
            self._file_notice = self._stale_notice = self._failure_notice = ""
            self.viewer.clear()
        self._month, self._location = month, location
        self.paths = paths
        self._workspace_available = bool(snapshot.get("ready")) and not snapshot.get("error")
        if self._workspace_available and "report_appearance" in snapshot:
            self._appearance = snapshot["report_appearance"]
        self._workspace_error = str(snapshot.get("error") or (
            "Complete workspace setup before generating reports." if snapshot.get("missing_files") else ""
        ))
        candidate = snapshot.get("report_view")
        if candidate is not None and candidate.month == month:
            self._view = candidate
            self._file_notice = str(snapshot.get("report_view_notice") or "")
        elif ("report_view" in snapshot or changed) and not self._failure_notice:
            self._view = None
            self._file_notice = str(snapshot.get("report_view_notice") or "")
        self._show_report()

    def apply_appearance(self, appearance: dict) -> None:
        """
        Recolour the displayed report as soon as Setup acknowledges a saved theme.
        """
        if self._appearance != appearance:
            self._appearance = appearance
            self._show_report()

    def action_started(self, action: str, payload: dict) -> None:
        """
        Clear an earlier generation error while retaining the last published report during work.
        """
        if action == "generate_reports":
            self._failure_notice = ""
            self._show_report()

    def action_completed(self, action: str, result=None) -> None:
        """
        Replace all four views together only after the PDFs and dashboard snapshot were published.
        """
        if action == "import_statements" and isinstance(result, list) and result:
            self.mark_stale()
            return
        result = result if isinstance(result, dict) else {}
        if action != "generate_reports":
            identifiers = {
                "apply_journal_edit": "journal_entry_id", "save_review": "transaction_id",
                "save_reviews": "review_ids", "discard_review": "transaction_id",
                "save_manual": "manual_entry_id", "save_bank_opening": "journal_entry_id",
            }
            acknowledged = bool(result.get(identifiers[action])) if action in identifiers else False
            acknowledged = acknowledged or (
                action in {"process_journal", "redbark_sync", "import_statements"} and "data" in result
            )
            if acknowledged:
                self.mark_stale()
            return
        view = result.get("report_view")
        paths = result.get("paths")
        if view is None or view.month != self._month or not isinstance(paths, (list, tuple)) or len(paths) != 4:
            self._failure_notice = "Generation did not finish. Generate again to update the saved reports."
            self._show_report()
            return
        self._view = view
        self.paths = list(paths)
        self._location = str(Path(paths[0]).parent)
        self._file_notice = self._stale_notice = self._failure_notice = ""
        self._show_report()

    def action_failed(self, action: str, message: str) -> None:
        """
        Keep the last valid display while making unsuccessful writes or workspace reads explicit.
        """
        if action == "generate_reports":
            self._failure_notice = f"Reports could not be generated. {message}"
        elif action == "refresh":
            self._workspace_available = False
            self._workspace_error = f"Workspace details could not be refreshed. {message}"
        elif action in {"process_journal", "import_statements"}:
            self._stale_notice = "Workspace processing stopped. Generate again after resolving the error."
        self._show_report()

    def mark_stale(self, message: str = "Changes ready · Generate to update reports.") -> None:
        """
        Explain that saved report figures remain a snapshot after later accounting or settings changes.
        """
        self._stale_notice = message
        self._show_report()

    def set_busy(self, busy: bool) -> None:
        """
        Block competing generation while leaving saved report selection and inspection available.
        """
        self._busy = busy
        self.generate.setEnabled(self._workspace_available and not busy)

    def _show_report(self, *_args) -> None:
        """
        Present published figures with the workspace's saved colours and the original draft explanations.
        """
        index = max(0, self.kind.currentIndex())
        self.heading.setText(self.TITLES[index])
        self.subtitle.setText(self.SUBTITLES[index])
        reasons = ()
        if self._view is not None:
            reports = self._view.reports
            settings = self._view.settings.model_copy(update=self._appearance)
            report = (
                reports.balance_sheet, reports.income_statement,
                reports.budget_report, reports.investment_report,
            )[index]
            self.viewer.set_report(reports, settings, index)
            self.subtitle.setStyleSheet(
                "QLabel#nativeReportSubtitle { font: 400 13px 'Geist'; padding: 0; margin: 0; color: "
                + self.viewer.caption_colour + "; }"
            )
            self.report_stack.setCurrentWidget(self.viewer)
            reasons = report.draft_reasons
        else:
            self.empty.setPixmap(icon("report", gui_colour(self, "muted"), 36).pixmap(36, 36))
            self.report_stack.setCurrentWidget(self.empty)
        messages = [
            self._workspace_error, self._failure_notice,
            self._file_notice if not self._file_notice.startswith("Generate to create") else "",
        ]
        self.notice.setText("\n".join(dict.fromkeys(message for message in messages if message)))
        self.notice.setToolTip("\n".join(reasons))
        self.notice.setVisible(bool(self.notice.text()))
        self.generate.setEnabled(self._workspace_available and not self._busy)
        destination = (f"Generate all four reports in {Path(self.paths[0]).parent}" if self.paths else
                       "Generate and save all four reports for the selected month.")
        details = [destination]
        if self._stale_notice:
            details.append(self._stale_notice)
        if reasons:
            details.extend(("Draft details:", *reasons))
        tip = f"Generate in {Path(self.paths[0]).parent}" if self.paths else "Generate reports"
        self.generate.setToolTip(tip)
        self.generate.setAccessibleDescription("\n".join(details))

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
        if row.get("retired") or row.get("is_savings"):
            continue
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
