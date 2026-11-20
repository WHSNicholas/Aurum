# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                    GUI - Analysis                                                    #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Explore posted financial history with synchronized balance, cash-flow and spending inspections.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import date, timedelta
from decimal import Decimal
from functools import partial
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QBoxLayout, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLayout, QScrollArea, QSizePolicy,
    QStackedWidget, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.dates import reporting_month
from aurum.gui.analysis_charts import CategoryChart, ChartTrace, HistoryChart, money
from aurum.gui.controls import SegmentedControl
from aurum.gui.theme import budget_colour, monetary_html

if TYPE_CHECKING:
    from aurum.workflows.analysis import AnalysisHistory


# %% 1. Analysis Page ----------------------------------------------------------------------------------------
class AnalysisPage(QWidget):
    """
    Inspect daily financial positions and monthly cash flows through the selected workspace month.
    """

    HORIZONS = ("4W", "12W", "6M", "1Y", "All time")
    CASH_FIELDS = ("income", "expenses", "savings")

    def __init__(self, parent=None) -> None:
        """
        Keep the selected account, horizon and pinned observation local to this workspace window.
        """
        super().__init__(parent)
        self.history = None
        self.points = ()
        self._dates = ()
        self._months = {}
        self._cash_months = ()
        self._cash_indices = {}
        self._account_id = None
        self._balance_members = {}
        self._spending_month = None
        self._cash_series = -1
        self.selected = -1
        self.pinned = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)
        self.title_row = QHBoxLayout()
        heading = QLabel("Analysis")
        heading.setObjectName("workspacePageTitle")
        self.title_row.addWidget(heading)
        self.title_row.addStretch()
        root.addLayout(self.title_row)
        self.ranges = SegmentedControl((*self.HORIZONS[:-1], "All"), "History range", 268)
        self.ranges.setCurrentIndex(self.HORIZONS.index("6M"))
        root.addWidget(self.ranges, 0, Qt.AlignmentFlag.AlignLeft)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.message = _label("workspaceHint")
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setWordWrap(True)
        self.stack.addWidget(self.message)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack.addWidget(self.scroll)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        body.setContentsMargins(0, 4, 6, 10)
        body.setSpacing(16)

        position = QFrame()
        position.setObjectName("analysisPosition")
        hero = QVBoxLayout(position)
        hero.setContentsMargins(22, 14, 22, 14)
        hero.setSpacing(4)
        net_title = _heading("Net Worth")
        net_title.setObjectName("analysisNetHeading")
        headline = QHBoxLayout()
        headline.addWidget(net_title)
        headline.addStretch()
        self.balance = _label("analysisBalance")
        headline.addWidget(self.balance)
        hero.addLayout(headline)
        numbers = QHBoxLayout()
        numbers.addStretch()
        numbers.setSpacing(8)
        self.inspection_date = _label("overviewCaption")
        numbers.addWidget(self.inspection_date)
        self.delta = _label("analysisChange")
        self.delta.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.delta.setToolTip("Since the first day shown")
        numbers.addWidget(self.delta)
        hero.addLayout(numbers)
        self.net = HistoryChart("Net worth history", background_role="featured")
        hero.addWidget(self.net)
        self.net_detail = _label("analysisPositionDetail")
        self.net_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.net_detail.setWordWrap(True)
        hero.addSpacing(6)
        hero.addWidget(self.net_detail)
        self.valuation = _label("overviewCaption")
        self.valuation.setWordWrap(True)
        hero.addWidget(self.valuation)
        body.addWidget(position)

        self.columns = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.columns.setSpacing(16)
        body.addLayout(self.columns)
        left = QVBoxLayout()
        left.setSpacing(16)
        self.columns.addLayout(left, 6)
        self.cash_date = _label("overviewCaption")
        date_policy = self.cash_date.sizePolicy()
        date_policy.setRetainSizeWhenHidden(True)
        self.cash_date.setSizePolicy(date_policy)
        cash_frame, cash = _section("Cash Flow", caption=self.cash_date)
        self.cash = HistoryChart("Monthly income, expenses and savings", cash=True)
        cash.addWidget(self.cash)
        self.cash_value = _label("analysisReadout")
        self.cash_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cash_value.setWordWrap(True)
        cash.addSpacing(6)
        cash.addWidget(self.cash_value)
        self.issues = _label("overviewCaption")
        self.issues.setTextFormat(Qt.TextFormat.PlainText)
        self.issues.setWordWrap(True)
        cash.addWidget(self.issues)
        left.addWidget(cash_frame)

        asset_frame = QFrame()
        asset_frame.setObjectName("analysisSection")
        asset_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        assets = QGridLayout(asset_frame)
        # The selector extends into the gutter so its padded text lines up with the heading and plot.
        assets.setContentsMargins(4, 12, 16, 14)
        assets.setHorizontalSpacing(0)
        assets.setVerticalSpacing(3)
        assets.setColumnMinimumWidth(0, 12)
        assets.setColumnStretch(1, 1)
        assets.addWidget(_heading("Account Balances"), 0, 1)
        asset_row = QHBoxLayout()
        self.accounts = QComboBox()
        self.accounts.setObjectName("analysisAssetAccount")
        self.accounts.setAccessibleName("Balance sheet account or asset group")
        self.accounts.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.accounts.setMinimumContentsLength(12)
        self.accounts.setMaximumWidth(320)
        asset_row.addWidget(self.accounts, 1, Qt.AlignmentFlag.AlignTop)
        asset_row.addStretch(1)
        self.asset_value = _label("overviewValue")
        figures = QVBoxLayout()
        figures.setSpacing(2)
        figures.addWidget(self.asset_value, 0, Qt.AlignmentFlag.AlignRight)
        self.account_delta = _label("analysisChange")
        figures.addWidget(self.account_delta, 0, Qt.AlignmentFlag.AlignRight)
        asset_row.addLayout(figures)
        assets.addLayout(asset_row, 1, 0, 1, 2)
        asset_plot = QVBoxLayout()
        asset_plot.setContentsMargins(0, 6, 0, 0)
        asset_plot.setSpacing(9)
        assets.addLayout(asset_plot, 2, 1)
        self.asset = HistoryChart("Account balance history")
        # Keep the chart's normal height as its minimum when a long category list expands the asset panel.
        self.asset.setMaximumHeight(asset_frame.maximumHeight())
        self.asset.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        asset_plot.addWidget(self.asset, 1)
        self.asset_note = _label("overviewCaption")
        self.asset_note.setTextFormat(Qt.TextFormat.PlainText)
        self.asset_note.setWordWrap(True)
        asset_plot.addWidget(self.asset_note)
        self._asset_layout = assets
        left.addWidget(asset_frame, 1)

        spending_frame, spending = _section("Spending by Category")
        self.spending_scope = _label("overviewCaption")
        spending.addWidget(self.spending_scope)
        self.categories = CategoryChart()
        spending.addWidget(self.categories)
        spending.addStretch()
        self.columns.addWidget(spending_frame, 5)
        body.addStretch()
        self.scroll.setWidget(content)
        self.scroll.viewport().installEventFilter(self)
        for chart in (self.net, self.cash, self.asset):
            chart.preview.connect(partial(self._preview, chart))
            chart.pin.connect(partial(self._pin, chart))
            chart.step.connect(partial(self._step, chart))
            chart.clear.connect(self._clear_selection)
        self.ranges.currentIndexChanged.connect(self._set_range)
        self.accounts.currentIndexChanged.connect(self._change_account)
        escape_selection = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape_selection.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape_selection.activated.connect(self._clear_selection)
        self.clear_history()

    def refresh(self, snapshot: dict) -> None:
        """
        Keep current-month observations usable while the window refreshes the saved ledger and prices.
        """
        if snapshot.get("error"):
            self.show_error(str(snapshot["error"]))
        elif snapshot.get("missing_files"):
            self.show_error("Complete workspace setup to view Analysis.")
        elif not snapshot.get("ready") or not self.history or snapshot.get("month") != self.history.month:
            self.clear_history()

    def clear_history(self) -> None:
        """
        Remove stale observations while retaining this window's horizon and preferred account.
        """
        self.history = None
        self.points = self._dates = self._cash_months = ()
        self._months = {}
        self._cash_indices = {}
        self.selected, self.pinned = -1, False
        self._cash_series = -1
        self.message.clear()
        self.ranges.setEnabled(False)
        self.stack.setCurrentWidget(self.message)

    def set_loading(self) -> None:
        """
        Leave progress indication to the shared sidebar while loading the first workspace history.
        """
        self.clear_history()

    def show_error(self, message: str) -> None:
        """
        Show an actionable ledger read failure in place of any stale observations.
        """
        self.clear_history()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setText(message)

    def set_history(self, history: "AnalysisHistory") -> None:
        """
        Replace displayed data, preserving the actual inspected day across a same-month price update.
        """
        day = None
        if self.history and self.history.month == history.month and self.selected >= 0:
            day = self._dates[self.selected]
        self.history = history
        self._months = {point.month: point for point in history.monthly}
        self.ranges.setEnabled(bool(history.daily))
        self.accounts.blockSignals(True)
        self.accounts.clear()
        self._balance_members.clear()
        positions = {account.key: i for i, account in enumerate(history.balance_accounts)}
        account_names = {account.name for account in history.balance_accounts}
        default = len(history.asset_groups) if history.balance_accounts else 0
        for name, keys in history.asset_groups.items():
            identifier = f"group:{name}"
            self._balance_members[identifier] = tuple(positions[key] for key in keys)
            if name.casefold() == "liquid cash assets":
                default = self.accounts.count()
            self.accounts.addItem(f"{name} (group)" if name in account_names else name, identifier)
        for i, account in enumerate(history.balance_accounts):
            self._balance_members[str(account.id)] = (i,)
            self.accounts.addItem(account.name, str(account.id))
        index = self.accounts.findData(self._account_id) if self._account_id else -1
        if self.accounts.count():
            self.accounts.setCurrentIndex(index if index >= 0 else default)
            self._account_id = self.accounts.currentData()
        self.accounts.blockSignals(False)
        self._display_range(day, self.pinned and day is not None)

    def eventFilter(self, watched, event) -> bool:
        """
        Stack cash flow, account balances and spending at their natural heights in narrow windows.
        """
        if watched is self.scroll.viewport() and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            direction = (QBoxLayout.Direction.LeftToRight if watched.width() >= 890
                         else QBoxLayout.Direction.TopToBottom)
            if self.columns.direction() != direction:
                self.columns.setDirection(direction)
                wide = direction == QBoxLayout.Direction.LeftToRight
                self.columns.setStretch(0, 6 if wide else 0)
                self.columns.setStretch(1, 5 if wide else 0)
        return super().eventFilter(watched, event)

    def changeEvent(self, event) -> None:
        """
        Recolour the rich-text legend while retaining the inspected month when the workspace theme changes.
        """
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            if self.points and self._spending_month is not None:
                self._monthly_readouts(self._spending_month, self.selected >= 0)

    def _set_range(self, *_args) -> None:
        """
        Start a fresh inspection when the user chooses a different visible horizon.
        """
        if self.history is not None:
            self._display_range(None, False)

    def _display_range(self, day: date | None, pinned: bool) -> None:
        """
        Slice daily balances and whole-month cash flows independently for the selected horizon.
        """
        horizon = self.HORIZONS[self.ranges.currentIndex()]
        bounds = self.history.daily_bounds(horizon)
        self.selected, self.pinned = -1, False
        self._spending_month = None
        if bounds is None:
            self.points = self._dates = ()
            self.message.setText("No posted history through " + _date(self.history.cutoff) + ".")
            self.stack.setCurrentWidget(self.message)
            return
        start, end = bounds
        first = (start - self.history.first_posted).days
        self.points = self.history.daily[first:first + (end - start).days + 1]
        self._dates = tuple(start + timedelta(days=i) for i in range(len(self.points)))
        self._cash_months = self.history.cash_months(horizon)
        self._cash_indices = {p.month: i for i, p in enumerate(self._cash_months)}
        self._range_issues = tuple(dict.fromkeys(i for p in self._cash_months for i in p.budget_issues))
        if day is not None and start <= day <= end:
            self.selected, self.pinned = (day - start).days, pinned
        if self.selected < 0:
            self._cash_series = -1
        self.net.set_data(self._dates, (ChartTrace("heading", tuple(p.net_worth for p in self.points)),),
                          weekly=horizon in {"4W", "12W"},
                          provisional=tuple(p.provisional_holdings for p in self.points))
        self.net_detail.setContentsMargins(self.net.axis_label_width, 0, 0, 0)
        traces = tuple(ChartTrace("budget_" + field, tuple(getattr(p, field) for p in self._cash_months))
                       for field in self.CASH_FIELDS)
        self.cash.set_data(tuple(date.fromisoformat(p.month + "-01") for p in self._cash_months), traces,
                           max_group_width={"4W": 84, "12W": 66}.get(horizon, 42))
        self.cash_value.setContentsMargins(self.cash.axis_label_width, 0, 0, 0)
        self._change_account()
        self.stack.setCurrentWidget(self.scroll)

    def _change_account(self, *_args) -> None:
        """
        Update only the account trace when the user picks an account, retaining the synchronized day.
        """
        if not self.points:
            return
        index = self.accounts.currentIndex()
        self.accounts.setVisible(index >= 0)
        self.asset.setVisible(index >= 0)
        self.asset_value.setVisible(index >= 0)
        self.account_delta.setVisible(index >= 0)
        # When there is no account, let the empty space follow the message instead of separating its rows.
        self._asset_layout.setRowStretch(2, int(index >= 0))
        self._asset_layout.setRowStretch(3, int(index < 0))
        if index >= 0:
            self._account_id = self.accounts.currentData()
            self.accounts.setToolTip(self.accounts.currentText())
            members = self._balance_members[self._account_id]
            balances = tuple(sum((p.account_balances[i] for i in members), Decimal(0)) for p in self.points)
            self.asset.set_data(self._dates, (ChartTrace("heading", balances),),
                                weekly=self.net.weekly,
                                provisional=tuple(any(p.account_provisional[i] for i in members)
                                                  for p in self.points))
        self._readouts()

    def _daily_index(self, chart: HistoryChart, index: int) -> int:
        """
        Inspect a monthly cash group at its actual reporting close or the current month's cutoff.
        """
        if index >= 0 and chart is self.cash:
            return (self._cash_months[index].end_date - self._dates[0]).days
        return index

    def _preview(self, chart: HistoryChart, index: int, series: int = -1) -> None:
        """
        Synchronize previews from either time scale until a point is pinned.
        """
        if not self.pinned and self.points:
            day_index = self._daily_index(chart, index)
            if day_index != self.selected or series != self._cash_series:
                self.selected, self._cash_series = day_index, series
                self._readouts()

    def _pin(self, chart: HistoryChart, index: int, series: int = -1) -> None:
        """
        Pin a day or reporting-month close across all charts.
        """
        if self.points and index >= 0:
            day_index = self._daily_index(chart, index)
            if self.pinned and day_index == self.selected:
                self._clear_selection()
                return
            self.selected, self.pinned, self._cash_series = day_index, True, series
            self._readouts()
            chart.emphasize_pin()

    def _step(self, chart: HistoryChart, offset: int) -> None:
        """
        Move by one day on position charts and one reporting month on cash flow.
        """
        if chart.dates:
            index = chart.current if chart.current >= 0 else (len(chart.dates) if offset < 0 else -1)
            index = max(0, min(len(chart.dates) - 1, index + offset))
            self.selected = self._daily_index(chart, index)
            self._cash_series = max(0, self._cash_series) if chart.cash else -1
            self._readouts()

    def _clear_selection(self) -> None:
        """
        Return to the latest position and the selected workspace month's spending.
        """
        self.selected, self.pinned = -1, False
        self._cash_series = -1
        if self.points:
            self._readouts()

    def _readouts(self) -> None:
        """
        Resolve both daily traces and the matching reporting month's cash-flow and spending breakdowns.
        """
        inspected = self.selected >= 0
        point = self.points[self.selected]
        day = self._dates[self.selected]
        month = "%04d-%02d" % reporting_month(day) if inspected else self.history.month
        self.inspection_date.setText(_date(day) if inspected else "")
        self.balance.setText(money(point.net_worth))
        self.delta.setText(_change(point.net_worth, self.points[0].net_worth))
        self.net_detail.setText(f"Assets {monetary_html(money(point.assets))} &nbsp;·&nbsp; "
                                f"Liabilities {monetary_html(money(point.liabilities))}")
        note = _provisional(point.provisional_holdings, self.net)
        self.valuation.setTextFormat(Qt.TextFormat.PlainText)
        self.valuation.setText(note)
        self.valuation.setVisible(bool(note))
        details = (f"{_date(day)}. Net worth {money(point.net_worth)}. Assets {money(point.assets)}. "
                   f"Liabilities {money(point.liabilities)}. {note}")
        closes = _close_details(point.price_dates)
        self.net.inspect(self.selected, f"{_date(day)} · {money(point.net_worth)}", details, closes)
        self.balance.setToolTip(closes or self.net.toolTip())
        self._monthly_readouts(month, inspected)
        account = self.accounts.currentIndex()
        note = "No balance sheet accounts are configured."
        if account >= 0:
            members = self._balance_members[self._account_id]
            balances = self.asset.traces[0].values
            amount = money(balances[self.selected])
            self.asset_value.setText(amount)
            self.account_delta.setText(_change(balances[self.selected], balances[0]))
            missing = {name for i in members for name in point.account_provisional[i]}
            note = _provisional(tuple(sorted(missing)), self.asset)
            member_closes = dict.fromkeys(close for i in members for close in point.account_price_dates[i])
            closes = _close_details(tuple(member_closes))
            details = f"{self.accounts.currentText()}. {_date(day)}. Balance {amount}. {note}"
            self.asset.inspect(self.selected, f"{_date(day)} · {amount}", details, closes)
            self.asset_value.setToolTip(closes or self.asset.toolTip())
        self.asset_note.setText(note)
        self.asset_note.setVisible(bool(note))

    def _monthly_readouts(self, month: str, inspected: bool) -> None:
        """
        Keep the ranking and cash-flow values tied to the working month that contains the inspected day.
        """
        point = self._months[month]
        label = date.fromisoformat(month + "-01").strftime("%B %Y")
        cash_index = self._cash_indices.get(month, -1) if inspected else -1
        chunks = []
        for field in self.CASH_FIELDS:
            swatch = budget_colour(self, field)
            chunk = f'<span style="color:{swatch}">●</span>&nbsp;{field.title()}'
            if inspected:
                chunk += "&nbsp;" + monetary_html(money(getattr(point, field)))
            chunks.append(chunk)
        self.cash_date.setText(label)
        self.cash_date.setVisible(inspected)
        self.cash_value.setText(" &nbsp; ".join(chunks))
        descriptions = ", ".join(f"{k.title()} {money(getattr(point, k))}" for k in self.CASH_FIELDS)
        tip = label
        if self._cash_series >= 0 and cash_index >= 0:
            field = self.CASH_FIELDS[self._cash_series]
            tip = f"{label} · {field.title()} {money(getattr(point, field))}"
        self.cash.inspect(cash_index, tip, f"{label}. {descriptions}.", series=self._cash_series)
        issues = point.budget_issues if inspected else self._range_issues
        self.issues.setText(" ".join(issues))
        self.issues.setVisible(bool(issues))
        if month != self._spending_month:
            self._spending_month = month
            self.spending_scope.setText(label)
            self.categories.set_values(tuple((i.name, i.amount) for i in point.categories if i.amount),
                                       point.expenses)


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _section(title: str, *, caption: QLabel | None = None) -> tuple[QFrame, QVBoxLayout]:
    """
    Give the supporting analyses common heading insets and allow their frames to fill a shared row.
    """
    frame = QFrame()
    frame.setObjectName("analysisSection")
    frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 14)
    layout.setSpacing(9)
    if caption is None:
        layout.addWidget(_heading(title))
    else:
        header = QVBoxLayout()
        header.setSpacing(3)
        header.addWidget(_heading(title))
        header.addWidget(caption)
        layout.addLayout(header)
    return frame, layout


def _heading(text: str) -> QLabel:
    """
    Use Aurum's Cormorant section heading for each financial question.
    """
    label = _label("workspaceSection")
    label.setText(text)
    label.setProperty("headingLevel", "2")
    return label


def _label(name: str) -> QLabel:
    """
    Allow labels to wrap to the available chart width without enlarging the workspace's minimum size.
    """
    label = QLabel()
    label.setObjectName(name)
    label.setMinimumWidth(0)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    return label


def _change(value, opening) -> str:
    """
    Compare the inspected balance with the opening visible day, including negative opening balances.
    """
    delta = value - opening
    percent = f"{delta / abs(opening):+.1%}" if opening else "—"
    return monetary_html(f"{money(delta, signed=True)} | {percent}")


def _provisional(holdings: tuple[str, ...], chart: HistoryChart) -> str:
    """
    Identify provisional holdings for this day and explain markers on surrounding dates.
    """
    if holdings:
        return "Provisional · " + ", ".join(holdings)
    return "Hollow markers indicate provisional balances." if chart.provisional else ""


def _close_details(closes: tuple) -> str:
    """
    Identify the actual market close used for each priced holding at the inspected cutoff.
    """
    return "\n".join(f"{p.name} · Close {_date(p.price_date)}" for p in closes)


def _date(value: date) -> str:
    """
    Present unambiguous calendar dates across daily and multi-year histories.
    """
    return f"{value.day} {value:%b %Y}"
