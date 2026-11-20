# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  GUI - Report View                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Present generated financial reports as native, selectable dashboard content.

The report models and saved presentation settings are the only inputs. This surface never reads accounting,
fetches prices or writes PDFs. It shares the renderer's formatting and financial colours. Sunset Hues
uses desktop surfaces for the on-screen report.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections import Counter
from decimal import Decimal
from math import atan2, cos, degrees, pi, sin
import re

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QFontMetricsF, QKeySequence, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication, QBoxLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QScrollArea, QSizePolicy,
    QStyledItemDelegate, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.reports import Reports, ReportSettings
from aurum.gui.controls import hide_tooltip, show_tooltip, wrapped_text_layout
from aurum.gui.theme import resolve_gui_theme
from aurum.reporting.pdf import (
    ASSETS, ICON_FILES, _bar_text_colour, _count, _display_rows, _expense_colour, _money,
    _palette_colour, _percentage, _quantity, _signed_money, _sum_values, _theme_styles,
)


# %% 1. Native Reports ---------------------------------------------------------------------------------------
class ReportView(QWidget):
    """
    Keep all four saved reports in one continuous, adaptive native reading surface.
    """

    def __init__(self, parent=None) -> None:
        """
        Create one outer scroll area; individual report sections follow their full content height.
        """
        super().__init__(parent)
        self.setObjectName("nativeReportView")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setProperty("aurumReportTheme", True)
        self._selection = -1
        self._signature = None
        self._positions = {}
        self._colours = {}
        self._canvas = None
        layout = QVBoxLayout(self)
        # Keep the scrolling viewport and its scrollbar inside the curved report background.
        layout.setContentsMargins(6, 6, 6, 6)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("nativeReportScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.scroll)

    def clear(self) -> None:
        """
        Forget the previous month's presentation and scroll positions without affecting saved files.
        """
        self._signature = None
        self._selection = -1
        self._positions.clear()
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._canvas = None

    def set_report(self, reports: Reports, settings: ReportSettings, index: int) -> None:
        """
        Display one generated result, preserving each report's reading position across selection changes.
        """
        if not 0 <= index <= 3:
            raise ValueError("Choose one of the four financial reports")
        signature = (reports.model_dump_json(), settings.model_dump_json(), index)
        if signature == self._signature:
            return
        if self._selection >= 0:
            self._positions[self._selection] = self.scroll.verticalScrollBar().value()
        self._selection = index
        self._signature = signature
        self._colours = _native_report_colours(settings)
        self._style_surface()
        old = self.scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._canvas = QWidget()
        self._canvas.setObjectName("reportCanvas")
        body = QVBoxLayout(self._canvas)
        body.setContentsMargins(0, 0, 0, 14)
        body.setSpacing(24)
        if index == 0:
            self._balance_sheet(body, reports.balance_sheet)
        elif index == 1:
            self._income_statement(body, reports.income_statement, settings)
        elif index == 2:
            self._budget(body, reports.budget_report)
        else:
            self._investments(body, reports.investment_report, settings)
        body.addStretch()
        self.scroll.setWidget(self._canvas)
        position = self._positions.get(index, 0)
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(position))

    @property
    def caption_colour(self) -> str:
        """
        Match the page subtitle to the captions in the displayed report.
        """
        return self._colours["muted"]

    def _style_surface(self) -> None:
        """
        Apply the native report palette to the canvas, panels, headers and scrollbars.
        """
        c = self._colours
        scroll_hover = ""
        if "scroll-hover" in c:
            scroll_hover = f"""
                QWidget#nativeReportView QScrollBar::handle:vertical:hover,
                QScrollArea#reportHorizontal QScrollBar::handle:horizontal:hover {{
                    background: {c['scroll-hover']};
                }}
            """
        self.setStyleSheet(f"""
            QWidget#nativeReportView {{
                background: {c['page']}; color: {c['text']}; border: none; border-radius: 14px;
            }}
            QWidget#reportCanvas,
            QScrollArea#nativeReportScroll, QWidget#reportColumns {{
                background: {c['page']}; color: {c['text']}; border: none;
            }}
            QWidget#nativeReportView QLabel {{
                font-family: Geist; font-size: 12px; color: {c['text']};
                background: transparent; border: none; padding: 0;
            }}
            QFrame#reportCard {{
                background: {c.get('card', c['surface'])}; border: none; border-radius: 14px;
            }}
            QFrame#reportCard[highlight="true"] {{ background: {c['highlight']}; }}
            QFrame#reportPanel {{ background: transparent; border: none; }}
            QFrame#reportPanelBody {{
                background: {c['surface']}; border: none;
                border-bottom-left-radius: 12px; border-bottom-right-radius: 12px;
            }}
            QWidget#nativeReportView QLabel#reportSectionTitle {{
                background: {c['primary']}; color: {c['on-primary']};
                font-size: 14px; font-weight: 600; padding: 10px 12px;
                border-top-left-radius: 12px; border-top-right-radius: 12px;
            }}
            QWidget#nativeReportView QLabel#reportChartTitle {{
                background: {c['primary']}; color: {c['on-primary']};
                font-size: 13px; font-weight: 600; border-radius: 8px; padding: 8px 12px;
            }}
            QTableWidget#reportTable {{
                background: {c['surface']}; color: {c['text']}; border: none;
                gridline-color: transparent; selection-background-color: {c['group']};
                selection-color: {c['group-text']}; font: 12px Geist;
                outline: none;
            }}
            QTableWidget#reportTable::item {{ border: none; padding: 5px 9px; }}
            QTableWidget#reportTable::item:focus {{ outline: none; border: none; }}
            QTableWidget#reportTable QHeaderView {{ background: {c['surface']}; border: none; }}
            QTableWidget#reportTable QHeaderView::section {{
                background: {c['table-header']}; color: {c['on-table-header']};
                font: 600 10px Geist; padding: 7px 9px; border: none;
            }}
            QTableWidget#reportTable QHeaderView::section:first {{
                border-top-left-radius: 8px; border-bottom-left-radius: 8px;
            }}
            QTableWidget#reportTable QHeaderView::section:last {{
                border-top-right-radius: 8px; border-bottom-right-radius: 8px;
            }}
            QScrollArea#nativeReportScroll > QWidget#qt_scrollarea_vcontainer {{
                background: {c['page']};
            }}
            QScrollArea#reportHorizontal, QScrollArea#reportHorizontal > QWidget#qt_scrollarea_hcontainer {{
                background: {c['surface']}; border: none;
            }}
            QWidget#nativeReportView QScrollBar:vertical {{
                background: {c['page']}; width: 10px; margin: 2px 0 2px 4px;
            }}
            QWidget#nativeReportView QScrollBar::handle:vertical {{
                background: {c.get('scroll', c['divider'])}; border-radius: 3px; min-height: 28px;
            }}
            QScrollArea#reportHorizontal QScrollBar:horizontal {{
                background: {c['surface']}; height: 8px; margin: 0 2px;
            }}
            QScrollArea#reportHorizontal QScrollBar::handle:horizontal {{
                background: {c.get('scroll', c['divider'])}; border-radius: 4px; min-width: 32px;
            }}
            {scroll_hover}
            QWidget#nativeReportView QScrollBar::add-line,
            QWidget#nativeReportView QScrollBar::sub-line {{ width: 0; height: 0; }}
            QWidget#nativeReportView QScrollBar::add-page,
            QWidget#nativeReportView QScrollBar::sub-page {{ background: transparent; }}
        """)

    def _cards(self, body: QVBoxLayout, values: tuple, *, signed_last: bool = False) -> None:
        """
        Reproduce the three printed metrics with the same icons, captions and highlighted final value.
        """
        row = QWidget()
        row.setObjectName("reportColumns")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        for index, (symbol, title, value, detail) in enumerate(values):
            frame = QFrame()
            frame.setObjectName("reportCard")
            frame.setProperty("highlight", index == 2)
            card = QVBoxLayout(frame)
            card.setContentsMargins(16, 18, 16, 18)
            card.setSpacing(5)
            colour = self._colours["highlight-text"] if index == 2 else self._colours["muted"]
            text_colour = self._colours["on-highlight"] if index == 2 else self._colours["text"]
            image = QLabel()
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icon_colour = self._colours["on-highlight"] if index == 2 else (
                self._colours.get("icon", self._colours["primary"])
            )
            image.setPixmap(_report_icon(symbol, icon_colour, 32))
            card.addWidget(image)
            label = _label(title.upper(), colour=colour)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.addWidget(label)
            amount = _signed_money(value) if signed_last and index == 2 else _money(value, missing="-")
            number = _label(amount, money=True, colour=text_colour, size=25 if len(amount) < 16 else 20)
            number.setAlignment(Qt.AlignmentFlag.AlignCenter)
            card.addWidget(number)
            caption = _label(detail, colour=colour)
            caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
            caption.setWordWrap(True)
            card.addWidget(caption)
            layout.addWidget(frame, 1)
        body.addWidget(row)

    def _balance_sheet(self, body: QVBoxLayout, report) -> None:
        """
        Place Assets beside the complete Liabilities and Equity stack.
        """
        ratio = (f"Debt Ratio: {report.total_liabilities / report.total_assets * 100:.1f}%"
                 if report.total_assets > 0 else "Assets less liabilities")
        self._cards(body, (
            ("assets", "Total Assets", report.total_assets, _count(len(report.assets), "account")),
            ("liabilities", "Total Liabilities", report.total_liabilities,
             _count(len(report.liabilities), "account")),
            ("net_worth", "Net Worth", report.net_worth, ratio),
        ))
        columns = _Columns(370)
        columns.add(self._account_panel("Assets", report.assets, report.total_assets))
        columns.add(
            self._account_panel("Liabilities", report.liabilities, report.total_liabilities),
            self._account_panel("Equity", report.equity, report.total_equity),
        )
        body.addWidget(columns)

    def _income_statement(self, body: QVBoxLayout, report, settings: ReportSettings) -> None:
        """
        Pair the expense ranking with the exact income and expense account figures.
        """
        rate = (f"Savings Rate: {report.net_income / report.total_income * 100:.1f}%"
                if report.total_income > 0 else "Income less expenses")
        self._cards(body, (
            ("income", "Total Income", report.total_income, _count(len(report.income), "category")),
            ("expenses", "Total Expenses", report.total_expenses, _count(len(report.expenses), "category")),
            ("net_income", "Net Income", report.net_income, rate),
        ))
        rows = sorted((row for row in report.expenses if row.amount > 0),
                      key=lambda row: row.amount, reverse=True)
        if rows:
            body.addWidget(self._chart("Expense Breakdown", rows, settings, donut=False))
        columns = _Columns(370)
        columns.add(self._account_panel("Income", report.income, report.total_income))
        columns.add(self._account_panel("Expenses", report.expenses, report.total_expenses))
        body.addWidget(columns)

    def _budget(self, body: QVBoxLayout, report) -> None:
        """
        Preserve target availability, signed actuals and the budget's three progress meanings.
        """
        def target(value) -> str:
            """
            Retain the printed distinction between an unset target and a zero target.
            """
            return "Budget: Not set" if value is None else f"Budget: {_money(value)}"

        self._cards(body, (
            ("income", "Income", report.total_income, target(report.income_target)),
            ("expenses", "Expenses", report.total_expenses, target(report.expense_target)),
            ("savings", "Total Savings", report.total_savings, target(report.savings_target)),
        ))
        columns = _Columns(490)
        columns.add(
            self._budget_panel("Income", report.income, report.total_income),
            self._budget_panel("Savings", report.savings, report.total_savings),
        )
        columns.add(self._budget_panel("Expenses", report.expenses, report.total_expenses))
        body.addWidget(columns)

    def _investments(self, body: QVBoxLayout, report, settings: ReportSettings) -> None:
        """
        Show the allocation donut and every holding without introducing printed page boundaries.
        """
        recorded = any(row.security_key is None for row in report.positions)
        self._cards(body, (
            ("invested_cost", "Total Invested", report.total_cost, _count(len(report.positions), "position")),
            ("market_value", "Portfolio Value" if recorded else "Market Value", report.total_value,
             "Includes recorded balances" if recorded else
             _count(len({row.account_key for row in report.positions}), "account")),
            ("unrealised_gain", "Investment P&L", report.total_gain,
             f"Return: {_percentage(report.return_percentage)}"),
        ), signed_last=True)
        groups = [(name, tuple(sorted((row for row in report.positions if row.asset_class == name),
                                     key=lambda row: row.allocation_value, reverse=True)))
                  for name in dict.fromkeys(row.asset_class for row in report.positions)]
        groups.sort(key=lambda group: _sum_values(row.allocation_value for row in group[1]), reverse=True)
        allocations = [(name, _sum_values(row.allocation_value for row in rows)) for name, rows in groups]
        allocations = [(name, value) for name, value in allocations if value > 0]
        if allocations:
            body.addWidget(self._chart("Portfolio Allocation", allocations, settings, donut=True))
        panel, content = self._panel("Portfolio Holdings")
        rows = []
        counts = Counter(row.security_key for row in report.positions if row.security_key is not None)
        for name, holdings in groups:
            cost = _sum_values(row.cost_basis for row in holdings)
            value = _sum_values(row.value for row in holdings)
            gain = _sum_values(row.unrealised_gain for row in holdings)
            percentage = gain / cost * 100 if gain is not None and cost else None
            rows.append((
                [name, "", "", "", _money(cost, missing="-"), _money(value, missing="-"),
                 _signed_money(gain), _percentage(percentage)], "group", None,
            ))
            for holding in holdings:
                name = holding.symbol or holding.account_name
                if holding.security_key is not None and counts[holding.security_key] > 1:
                    name += "\n" + holding.account_name
                rows.append((
                    [name, _quantity(holding.units), _quantity(holding.average_cost, money=True),
                     _quantity(holding.price, money=True), _money(holding.cost_basis, missing="-"),
                     _money(holding.value, missing="-"), _signed_money(holding.unrealised_gain),
                     _percentage(holding.return_percentage)],
                    "crypto" if "crypto" in holding.asset_class.casefold() else "holding", None,
                ))
        if not rows:
            rows.append((["No investment holdings", "", "", "", "", "", "", ""], "empty", None))
        rows.append((
            ["GRAND TOTAL", "", "", "", _money(report.total_cost, missing="-"),
             _money(report.total_value, missing="-"), _signed_money(report.total_gain),
             _percentage(report.return_percentage)], "total", None,
        ))
        table = _ReportTable(
            ("Symbol", "Units", "Avg Cost", "Price", "Cost Basis", "Value", "P&L", "Return"),
            rows, self._colours, (0.20, 0.08, 0.10, 0.10, 0.14, 0.14, 0.14, 0.10),
            alignments=(Qt.AlignmentFlag.AlignLeft, *(Qt.AlignmentFlag.AlignHCenter,) * 6,
                        Qt.AlignmentFlag.AlignRight),
        )
        horizontal = QScrollArea()
        horizontal.setObjectName("reportHorizontal")
        horizontal.setWidgetResizable(True)
        horizontal.setFrameShape(QFrame.Shape.NoFrame)
        horizontal.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setMinimumWidth(1010)
        horizontal.setWidget(table)
        horizontal.setFixedHeight(table.height() + 18)
        table.height_changed = lambda height: horizontal.setFixedHeight(height + 18)
        content.addWidget(horizontal)
        body.addWidget(panel)

    def _panel(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        """
        Build a rounded report section with its coloured heading and padded table surface.
        """
        frame = QFrame()
        frame.setObjectName("reportPanel")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        label = _label(title.upper())
        label.setObjectName("reportSectionTitle")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(label)
        panel_body = QFrame()
        panel_body.setObjectName("reportPanelBody")
        content = QVBoxLayout(panel_body)
        content.setContentsMargins(10, 10, 10, 10)
        content.setSpacing(8)
        outer.addWidget(panel_body, 1)
        return frame, content

    def _account_panel(self, title: str, values, total: Decimal) -> QFrame:
        """
        Keep configured groups and all account rows, with one final printed-style total band.
        """
        frame, content = self._panel(title)
        rows = [([row.name, _money(row.amount)], "group" if row.group else
                 "indent" if row.indent else "normal", None) for row in _display_rows(values)]
        if not rows:
            rows = [(["No balances", "$0.00"], "empty", None)]
        content.addWidget(_ReportTable(
            ("Account", "Amount"), rows, self._colours, (0.62, 0.38),
            neutral_negative=title in {"Assets", "Liabilities", "Equity"},
        ))
        content.addWidget(self._total_strip("Total " + title, total), 1, Qt.AlignmentFlag.AlignBottom)
        return frame

    def _budget_panel(self, title: str, values, total: Decimal) -> QFrame:
        """
        Display all budget rows without nested vertical scrolling or zero-target invention.
        """
        frame, content = self._panel(title)
        rows = []
        for row in values:
            rows.append((
                [row.name, _money(row.target) if row.target is not None else "—", _money(row.actual),
                 "", _money(row.remaining) if row.remaining is not None else "—"], "normal", row,
            ))
        if not rows:
            rows.append((["No activity", "", "", "", ""], "empty", None))
        table = _ReportTable(
            ("Category", "Budget", "Actual", "Progress", "Remaining"), rows, self._colours,
            (0.24, 0.19, 0.19, 0.17, 0.21), budget_kind=title.lower(),
            alignments=(Qt.AlignmentFlag.AlignLeft, *(Qt.AlignmentFlag.AlignHCenter,) * 3,
                        Qt.AlignmentFlag.AlignRight),
        )
        content.addWidget(table)
        content.addWidget(self._total_strip(title, total), 1, Qt.AlignmentFlag.AlignBottom)
        return frame

    def _total_strip(self, title: str, total: Decimal) -> QFrame:
        """
        Place a separate rounded total band beneath the table, exactly as the printed account panels do.
        """
        frame = QFrame()
        frame.setObjectName("reportTotalStrip")
        frame.setStyleSheet(
            f"QFrame#reportTotalStrip {{ background: {self._colours['table-header']};"
            " border: none; border-radius: 8px; }"
        )
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        label = _label(title.upper(), colour=self._colours["on-table-header"], size=11)
        label.setStyleSheet(label.styleSheet() + " font-weight: 600;")
        layout.addWidget(label)
        layout.addStretch()
        value = _label(_money(total), money=True, colour=self._colours["on-table-header"], size=11)
        layout.addWidget(value)
        return frame

    def _chart(self, title: str, values, settings: ReportSettings, *, donut: bool) -> QWidget:
        """
        Centre a native report chart at its printed proportion, with an accessible exact-value description.
        """
        wrapper = _ChartContainer(.396 if donut else .55, 340 if donut else 470)
        wrapper.setObjectName("reportColumns")
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        frame = QWidget()
        frame.setObjectName("reportColumns")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        label = _label(title.upper())
        label.setObjectName("reportChartTitle")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        chart = _ReportChart(values, settings, self._colours, donut=donut)
        layout.addWidget(chart)
        row.addStretch()
        row.addWidget(frame, 4 if donut else 6)
        row.addStretch()
        frame.setMinimumWidth(340 if donut else 470)
        frame.setMaximumWidth(460 if donut else 760)
        wrapper.frame = frame
        return wrapper


# %% 2. Report Components ------------------------------------------------------------------------------------
class _Columns(QWidget):
    """
    Align paired report sections at their top and bottom edges and stack them on narrow screens.
    """

    def __init__(self, minimum: int) -> None:
        """
        Start horizontal and allow the actual viewport width to decide when stacking is necessary.
        """
        super().__init__()
        self.setObjectName("reportColumns")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._minimum = minimum
        self._layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(20)

    def add(self, upper: QFrame, lower: QFrame | None = None) -> None:
        """
        Extend a lone panel to the opposite total, or put the spare height between two natural panels.
        """
        if lower is None:
            upper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self._layout.addWidget(upper, 1)
            return
        column = QWidget()
        column.setObjectName("reportColumns")
        stack = QVBoxLayout(column)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)
        stack.addWidget(upper)
        stack.addSpacing(self._layout.spacing())
        stack.addStretch()
        stack.addWidget(lower)
        self._layout.addWidget(column, 1)

    def resizeEvent(self, event) -> None:
        """
        Reflow entire panels instead of shrinking financial columns beyond readable widths.
        """
        side_by_side = self.width() >= 2 * self._minimum + self._layout.spacing()
        direction = QBoxLayout.Direction.LeftToRight if side_by_side else QBoxLayout.Direction.TopToBottom
        if self._layout.direction() != direction:
            self._layout.setDirection(direction)
            # Share width equally side by side and use each column's natural height when stacked.
            for index in range(self._layout.count()):
                self._layout.setStretch(index, 1 if side_by_side else 0)
        super().resizeEvent(event)


class _ChartContainer(QWidget):
    """
    Keep each chart centred at its PDF width proportion with a readable native-screen minimum.
    """

    def __init__(self, ratio: float, minimum: int) -> None:
        """
        Retain the chart's target fraction without fixing the surrounding report canvas width.
        """
        super().__init__()
        self.ratio = ratio
        self.minimum = minimum
        self.maximum = 460 if ratio < .5 else 760
        self.frame = None

    def resizeEvent(self, event) -> None:
        """
        Resize the chart frame while preserving the report's continuous vertical flow.
        """
        if self.frame is not None:
            width = min(self.maximum, max(self.minimum, int(self.width() * self.ratio)))
            self.frame.setFixedWidth(width)
        super().resizeEvent(event)


class _ReportTable(QTableWidget):
    """
    Give generated report rows native selection, precise fonts and full outer-scroll height.
    """

    def __init__(
        self, headers, rows, colours, widths, *, budget_kind: str = "", neutral_negative: bool = False,
        alignments: tuple | None = None,
    ) -> None:
        """
        Construct read-only cells and preserve the row's group, total and progress semantics.
        """
        super().__init__(len(rows), len(headers))
        self.setObjectName("reportTable")
        self.colours = colours
        self.rows = rows
        self.widths = widths
        self.budget_kind = budget_kind
        self.alignments = alignments or (
            Qt.AlignmentFlag.AlignLeft, *(Qt.AlignmentFlag.AlignRight,) * (len(headers) - 1),
        )
        if len(self.alignments) != len(headers):
            raise ValueError("Every report column needs one alignment")
        self.height_changed = None
        self.setHorizontalHeaderLabels([name.upper() for name in headers])
        for column, alignment in enumerate(self.alignments):
            self.horizontalHeaderItem(column).setTextAlignment(alignment | Qt.AlignmentFlag.AlignVCenter)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectItems)
        self.setShowGrid(False)
        self.setWordWrap(True)
        self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setMinimumSectionSize(34)
        self.horizontalHeader().setFixedHeight(32)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setItemDelegate(_ReportDelegate(self))
        for row_number, (values, kind, progress) in enumerate(rows):
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                item.setFont(_font(12 if column == 0 else 12, money=column > 0))
                item.setTextAlignment(self.alignments[column] | Qt.AlignmentFlag.AlignVCenter)
                colour = colours["on-table-header"] if kind == "total" else (
                    colours["group-text"] if kind == "group" else colours["muted"] if kind == "empty"
                    else colours["text"]
                )
                if (
                    not neutral_negative and kind not in {"group", "total"}
                    and value.startswith("-") and len(value) > 1
                ):
                    colour = colours["negative"]
                if kind not in {"group", "total"} and value.startswith("+"):
                    colour = colours["positive"]
                if progress is not None and column == 4:
                    colour = colours["budget-" + budget_kind] if (
                        progress.remaining is not None and progress.remaining < 0 and budget_kind != "savings"
                    ) else colours["remaining"]
                item.setForeground(QColor(colour))
                if progress is not None and column == 3:
                    text = ("Budget not set" if progress.target is None else "Zero budget"
                            if progress.progress is None else f"{progress.progress:.0f}%")
                    if progress.target == 0 and progress.actual > 0:
                        text = "Zero budget exceeded"
                    item.setToolTip(text)
                self.setItem(row_number, column, item)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fit)
        self.viewport().installEventFilter(self)
        self._fit()

    def eventFilter(self, watched, event) -> bool:
        """
        Coalesce viewport changes before measuring wrapped labels and the complete table height.
        """
        if watched is self.viewport() and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}:
            self._timer.start(0)
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event) -> None:
        """
        Copy selected financial cells in visual order without altering saved accounting.
        """
        if event.matches(QKeySequence.StandardKey.Copy):
            selected = {(item.row(), item.column()): item.text() for item in self.selectedItems()}
            if selected:
                rows = sorted({row for row, _ in selected})
                columns = sorted({column for _, column in selected})
                QApplication.clipboard().setText("\n".join(
                    "\t".join(selected.get((row, column), "") for column in columns) for row in rows
                ))
            event.accept()
            return
        super().keyPressEvent(event)

    def _fit(self) -> None:
        """
        Allocate every column and grow rows for names; never clip a report behind an inner scrollbar.
        """
        width = max(200, self.viewport().width())
        total = sum(self.widths)
        used = 0
        for index, fraction in enumerate(self.widths):
            size = width - used if index == len(self.widths) - 1 else int(width * fraction / total)
            self.setColumnWidth(index, size)
            used += size
        for index, (_, kind, _) in enumerate(self.rows):
            minimum = 28 if kind == "holding" else 34
            height = minimum
            for column in range(self.columnCount()):
                item = self.item(index, column)
                if item is None:
                    continue
                indent = 14 if kind in {"indent", "holding", "crypto"} and column == 0 else 0
                available = max(10, self.columnWidth(column) - 18 - indent)
                if column == 0:
                    layout = wrapped_text_layout(item.text(), item.font(), available)
                    text_height = layout.boundingRect().height()
                else:
                    text_height = QFontMetrics(item.font()).height()
                height = max(height, int(text_height + .99) + (10 if kind != "holding" else 6))
            self.setRowHeight(index, height)
        height = self.horizontalHeader().height() + sum(self.rowHeight(i) for i in range(self.rowCount()))
        if height != self.height():
            self.setFixedHeight(height)
            if self.height_changed is not None:
                self.height_changed(height)


class _ReportDelegate(QStyledItemDelegate):
    """
    Paint rounded row bands and budget progress without adding interactive cell controls.
    """

    def paint(self, painter: QPainter, option, index) -> None:
        """
        Draw financial text consistently while keeping logical selection visually quiet.
        """
        table = self.parent()
        _, kind, progress = table.rows[index.row()]
        c = table.colours
        painter.save()
        painter.setClipRect(option.rect)
        painter.fillRect(option.rect, _colour(c["surface"]))
        if kind in {"group", "total"}:
            colour = c["table-header"] if kind == "total" else c["group"]
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_colour(colour))
            rect = QRectF(0, option.rect.y(), table.viewport().width(), option.rect.height())
            painter.drawRoundedRect(rect, 8, 8)
        if progress is not None and index.column() == 3:
            self._progress(painter, option.rect, progress, table.budget_kind, c)
        else:
            item = table.item(index.row(), index.column())
            painter.setFont(item.font())
            painter.setPen(item.foreground().color())
            if index.column() == 0:
                inset = 3 if kind == "holding" else 5
                indent = 14 if kind in {"indent", "holding", "crypto"} else 0
                rect = QRectF(option.rect).adjusted(9 + indent, inset, -9, -inset)
                layout = wrapped_text_layout(item.text(), item.font(), max(10, rect.width()))
                layout.draw(painter, QPointF(rect.left(), rect.top() + max(
                    0, (rect.height() - layout.boundingRect().height()) / 2,
                )))
            else:
                painter.drawText(
                    QRectF(option.rect).adjusted(9, 0, -9, 0),
                    item.textAlignment() | Qt.TextFlag.TextSingleLine, item.text(),
                )
        if kind not in {"group", "total"} and index.row() < table.rowCount() - 1:
            next_kind = table.rows[index.row() + 1][1]
            if next_kind not in {"group", "total"}:
                painter.setPen(QPen(_colour(c["divider"]), 0.6))
                painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()

    @staticmethod
    def _progress(painter, rect, row, kind, colours) -> None:
        """
        Use the same capped progress and zero-target overflow handling as the printed budget.
        """
        painter.save()
        track = QRectF(rect.x() + 8, rect.center().y() - 6, max(12, rect.width() - 16), 12)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        exceeded = row.remaining is not None and row.remaining < 0
        value = Decimal(100) if exceeded else max(Decimal(0), min(Decimal(100), row.progress or Decimal(0)))
        painter.setBrush(_colour(colours[kind + ("-fill" if value == 100 else "-track")]))
        painter.drawRoundedRect(track, 6, 6)
        if 0 < value < 100:
            path = QPainterPath()
            path.addRoundedRect(track, 6, 6)
            painter.setClipPath(path, Qt.ClipOperation.IntersectClip)
            filled = QRectF(track)
            filled.setWidth(track.width() * float(value) / 100)
            painter.setBrush(_colour(colours[kind + "-fill"]))
            painter.drawRoundedRect(filled, 6, 6)
        painter.restore()


class _ReportChart(QWidget):
    """
    Draw the PDF's chart geometry natively and expose exact values through tooltips and accessibility.
    """

    BAR_HEIGHT = 25
    BAR_GAP = 8
    BAR_PADDING = 6

    def __init__(self, values, settings: ReportSettings, colours: dict, *, donut: bool) -> None:
        """
        Store chart inputs without fetching values or creating another report calculation.
        """
        super().__init__()
        self.values = values
        self.settings = settings
        self.colours = colours
        self.donut = donut
        self._hits = []
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Portfolio allocation" if donut else "Expense breakdown")
        descriptions = [(name, value) for name, value in values] if donut else [
            (row.name, row.amount) for row in values[:8]
        ]
        self.setAccessibleDescription("; ".join(f"{name}: {_money(value)}" for name, value in descriptions))
        if donut:
            self.setFixedHeight(260)
        else:
            row_count = len(descriptions)
            self.setFixedHeight(
                2 * self.BAR_PADDING + row_count * self.BAR_HEIGHT + max(0, row_count - 1) * self.BAR_GAP
            )

    def paintEvent(self, event) -> None:
        """
        Draw crisp native chart shapes at the current display scale.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._hits = []
        if self.donut:
            self._donut(painter)
        else:
            self._bars(painter)

    def mouseMoveEvent(self, event) -> None:
        """
        Reveal precise chart amounts without introducing buttons or a separate chart legend.
        """
        for path, text in self._hits:
            if path.contains(event.position()):
                show_tooltip(event.globalPosition().toPoint(), text, self)
                return
        hide_tooltip()

    def leaveEvent(self, event) -> None:
        """
        Clear an exact-value tooltip once the pointer leaves the chart.
        """
        hide_tooltip()
        super().leaveEvent(event)

    def _bars(self, painter: QPainter) -> None:
        """
        Centre the visible category labels and their bars together beneath the expense chart heading.
        """
        rows = self.values[:8]
        largest = rows[0].amount
        total = sum((row.amount for row in self.values), Decimal(0))
        label_gap = 12
        label_limit = min(150, self.width() * .27) - label_gap
        bar_width = self.width() - label_limit - label_gap - 10
        label_font = _font(11)
        metrics = QFontMetricsF(label_font, painter.device())
        labels = [metrics.elidedText(row.name, Qt.TextElideMode.ElideRight, label_limit) for row in rows]
        bounds = [metrics.tightBoundingRect(label) for label in labels]
        # Measure visible glyphs after elision so short names leave equal margins on both sides.
        label_width = max(box.width() for box in bounds)
        left = (self.width() - (label_width + label_gap + bar_width)) / 2
        label_right = left + label_width
        for index, (row, label, box) in enumerate(zip(rows, labels, bounds)):
            colour = _expense_colour(self.settings.theme.palette, index, len(rows))
            y = self.BAR_PADDING + index * (self.BAR_HEIGHT + self.BAR_GAP)
            rect = QRectF(label_right + label_gap, y, max(46, float(row.amount / largest) * bar_width),
                          self.BAR_HEIGHT)
            path = QPainterPath()
            path.setFillRule(Qt.FillRule.WindingFill)
            path.addRoundedRect(rect, 12, 12)
            path.addRect(QRectF(rect.x(), rect.y(), min(12, rect.width()), rect.height()))
            painter.fillPath(path, _colour(colour))
            painter.setFont(label_font)
            painter.setPen(_colour(self.colours.get("chart-text", self.colours["text"])))
            baseline = rect.center().y() + (metrics.ascent() - metrics.descent()) / 2
            painter.drawText(QPointF(label_right - box.right(), baseline), label)
            percent = row.amount / total * 100
            percentage = "<1%" if percent < 1 else f"{percent:.0f}%"
            value = f"${row.amount:.0f} ({percentage})"
            painter.setFont(_font(10, money=True))
            if QFontMetrics(painter.font()).horizontalAdvance(value) + 18 > rect.width():
                value = percentage
            painter.setPen(_colour(_bar_text_colour(
                colour, self.colours["bar-text-dark"], self.colours["bar-text-light"],
            )))
            painter.drawText(rect.adjusted(6, 0, -8, 0),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, value)
            self._hits.append((path, f"{row.name}\n{_money(row.amount)} · {percent:.2f}%"))

    def _donut(self, painter: QPainter) -> None:
        """
        Use the printed ring, medium-to-light slices and compact outside allocation labels.
        """
        total = sum((value for _, value in self.values), Decimal(0))
        centre = QPointF(self.width() / 2, 122)
        radius = min(84, self.width() * .22)
        inner = radius * .45
        start = -90.0
        label_positions = []
        for index, (name, value) in enumerate(self.values):
            fraction = float(value / total)
            sweep = fraction * 360
            angle = (start + sweep / 2) * pi / 180
            offset = QPointF(1.5 * cos(angle), 1.5 * sin(angle))
            ring = QRectF(centre.x() - radius + offset.x(), centre.y() - radius + offset.y(),
                          radius * 2, radius * 2)
            hole = QRectF(centre.x() - inner + offset.x(), centre.y() - inner + offset.y(),
                          inner * 2, inner * 2)
            path = QPainterPath()
            path.arcMoveTo(ring, -start)
            path.arcTo(ring, -start, -sweep)
            path.arcTo(hole, -(start + sweep), sweep)
            path.closeSubpath()
            position = (.25 + .4 * index / max(1, len(self.values) - 1)) * (
                len(self.settings.theme.palette) - 1
            )
            painter.fillPath(path, _colour(_palette_colour(self.settings.theme.palette, position)))
            self._hits.append((path, f"{name}\n{_money(value)} · {fraction * 100:.2f}%"))
            label_positions.append((name, value, fraction, angle))
            start += sweep
        painter.setPen(_colour(self.colours.get("chart-text", self.colours["text"])))
        for (name, value, fraction, _), box, align in self._donut_label_rects(
            label_positions, centre, radius,
        ):
            rect = QRectF(box.left(), box.top(), box.width(), 17)
            painter.setFont(_font(10))
            text = QFontMetrics(painter.font()).elidedText(
                name, Qt.TextElideMode.ElideRight, int(rect.width()),
            )
            painter.drawText(rect, align | Qt.AlignmentFlag.AlignVCenter, text)
            painter.setFont(_font(10, money=True))
            painter.drawText(rect.translated(0, 15), align | Qt.AlignmentFlag.AlignVCenter,
                             f"{fraction * 100:.3g}%")
            path = QPainterPath()
            path.addRect(box)
            self._hits.append((path, f"{name}\n{_money(value)} · {fraction * 100:.2f}%"))

    def _donut_label_rects(self, labels, centre: QPointF, radius: float) -> list:
        """
        Keep allocation labels outside the ring and separate crowded same-side labels vertically.

        One near-vertical slice may use each centred top/bottom position. Any additional nearby slices
        use the fixed side gutters, so tiny adjacent allocations never push text into the ring.
        """
        result = []
        centred = set()
        for top in (True, False):
            candidates = [index for index, label in enumerate(labels)
                          if abs(cos(label[3])) < .25 and (sin(label[3]) < 0) == top]
            if not candidates:
                continue
            index = min(candidates, key=lambda item: abs(cos(labels[item][3])))
            centred.add(index)
            y = max(0, centre.y() - radius - 46) if top else min(
                self.height() - 32, centre.y() + radius + 14,
            )
            box = QRectF(centre.x() - radius, y, radius * 2, 32)
            result.append((labels[index], box, Qt.AlignmentFlag.AlignHCenter))
        for right in (False, True):
            side = [label for index, label in enumerate(labels)
                    if index not in centred and (cos(label[3]) >= 0) == right]
            side.sort(key=lambda label: sin(label[3]))
            tops = []
            for label in side:
                desired = centre.y() + (radius + 17) * sin(label[3]) - 16
                top = max(0, min(self.height() - 32, desired))
                tops.append(max(top, tops[-1] + 34) if tops else top)
            for index in range(len(tops) - 1, -1, -1):
                limit = self.height() - 32 if index == len(tops) - 1 else tops[index + 1] - 34
                tops[index] = min(tops[index], limit)
            anchor = centre.x() + radius + 14 if right else centre.x() - radius - 14
            left = anchor if right else 8
            width = self.width() - anchor - 8 if right else anchor - 8
            align = Qt.AlignmentFlag.AlignLeft if right else Qt.AlignmentFlag.AlignRight
            for label, top in zip(side, tops):
                result.append((label, QRectF(left, top, max(1, width), 32), align))
        return result


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _native_report_colours(settings: ReportSettings) -> dict[str, str]:
    """
    Use Sunset Hues desktop colours for interface elements while retaining the financial chart palette.
    """
    colours = dict(re.findall(r"--report-([\w-]+):\s*([^;]+);", _theme_styles(settings)))
    if settings.colour_scheme == "sunset_hues":
        desktop = resolve_gui_theme(settings)
        for role in ("card", "text", "muted", "icon", "scroll", "positive", "negative"):
            colours[role] = desktop[role]
        colours.update({
            "page": desktop["page"], "surface": desktop["content"],
            "group": desktop["panel"], "group-text": desktop["text"],
            "table-header": desktop["table_header"], "on-table-header": desktop["on_table_header"],
            "divider": desktop["border"], "scroll-hover": desktop["scroll_hover"],
            "chart-text": desktop["text"], "remaining": desktop["text"],
        })
    return colours


def _font(size: int, *, money: bool = False) -> QFont:
    """
    Use the same local Geist family for labels and Geist Mono for financial values as the PDFs.
    """
    font = QFont("Geist Mono" if money else "Geist")
    font.setPixelSize(size)
    return font


def _label(text: str = "", *, money: bool = False, colour: str = "", size: int = 12) -> QLabel:
    """
    Make report text natively selectable without changing its saved spelling or numerical convention.
    """
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setFont(_font(size, money=money))
    if money or colour or size != 12:
        label.setStyleSheet(
            f"font-family: {'Geist Mono' if money else 'Geist'}; font-size: {size}px;"
            + (f" color: {colour};" if colour else "")
        )
    return label


def _colour(value: str) -> QColor:
    """
    Convert report CSS hex colours, including trailing alpha, to Qt's native colour convention.
    """
    if value.startswith("#") and len(value) == 9:
        colour = QColor(value[:7])
        colour.setAlpha(int(value[7:], 16))
        return colour
    return QColor(value)


def _report_icon(name: str, colour: str, size: int) -> QPixmap:
    """
    Tint the original PDF silhouette at device-independent high resolution without changing the asset.
    """
    pixmap = QPixmap(str(ASSETS / ICON_FILES[name]))
    pixmap = pixmap.scaled(size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), _colour(colour))
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return pixmap
