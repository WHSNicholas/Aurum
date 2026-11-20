# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                GUI - Analysis Charts                                                 #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Draw posted financial histories and signed category amounts using the active workspace's desktop colours.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import ceil, floor, log10

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

# Aurum imports
from aurum.gui.controls import reduced_motion, watch_reduced_motion
from aurum.gui.theme import budget_colour, gui_colour


# %% 1. History Charts ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ChartTrace:
    """
    Associate one financial measure's exact plotted amounts with its workspace colour.
    """

    role: str
    values: tuple[Decimal, ...]


class HistoryChart(QWidget):
    """
    Draw a financial time series and report pointer and keyboard inspection to its owning Analysis page.
    """

    preview = Signal(int, int)
    pin = Signal(int, int)
    step = Signal(int)
    clear = Signal()

    def __init__(self, title: str, *, cash: bool = False, background_role: str = "content") -> None:
        """
        Reserve a taller plot for financial position and a common signed scale for cash measures.
        """
        super().__init__()
        self.cash = cash
        self._background_role = background_role
        self.dates = ()
        self.traces = ()
        self.weekly = False
        self.date_ticks = ()
        self.current = -1
        self._series = -1
        self._max_group_width = 42
        self._keyboard_focus = False
        self.provisional = ()
        self._ticks = ()
        self._tick_labels = ()
        self.axis_label_width = 0
        self._plot = QRectF()
        self._line_path = None
        self._pin_animation = QVariantAnimation(self)
        self._pin_animation.setDuration(120)
        self._pin_animation.setStartValue(4.7)
        self._pin_animation.setEndValue(3.3)
        self._pin_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pin_animation.valueChanged.connect(self._repaint_marker)
        watch_reduced_motion(self._settle_pin)
        self.setFixedHeight(172 if cash else 188)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(title)

    def set_data(
        self, dates: tuple[date, ...], traces: tuple[ChartTrace, ...], *, provisional: tuple = (),
        max_group_width: int = 42, weekly: bool = False,
    ) -> None:
        """
        Derive scale and axis labels once per view change, keeping inspection independent of calculations.
        """
        self._pin_animation.stop()
        self.dates, self.traces = dates, traces
        self.weekly = weekly
        self.date_ticks = tuple(i for i, day in enumerate(dates)
                                if (day.weekday() == 6 if weekly else day.day == 1))
        self._max_group_width = max_group_width
        self.provisional = tuple(i for i, holdings in enumerate(provisional) if holdings)
        self._line_path = None
        values = [float(v) for trace in traces for v in trace.values]
        if self.cash:
            values.append(0.0)
        self._ticks = _axis_ticks(values)
        self._tick_labels = _axis_labels(self._ticks)
        metrics = QFontMetrics(_font(10, money=True), self)
        self.axis_label_width = max(metrics.horizontalAdvance(text) for text in self._tick_labels)
        self.current = -1
        self.update()

    def inspect(
        self, index: int, tooltip: str, description: str, price_detail: str = "", *, series: int = -1,
    ) -> None:
        """
        Move the shared cursor and expose the same exact financial details to assistive technology.
        """
        if self.current != index:
            self._pin_animation.stop()
        self.current, self._series = index, series
        instructions = "Click or press Enter to toggle a pin. Arrow keys move and Escape clears."
        self.setToolTip(tooltip if index >= 0 else "")
        self.setAccessibleDescription("\n".join(filter(None, (description, price_detail, instructions))))
        self.update()

    def emphasize_pin(self) -> None:
        """
        Briefly enlarge the chosen marker when the user explicitly pins a point.
        """
        self._settle_pin()
        if not self.cash and self.current >= 0 and self.isVisible() and not reduced_motion(self):
            self._pin_animation.start()

    def _settle_pin(self) -> None:
        """
        Draw the marker at its ordinary radius as soon as a motion preference or pin changes.
        """
        self._pin_animation.stop()
        self._repaint_marker()

    def _repaint_marker(self) -> None:
        """
        Repaint only the column containing the daily marker.
        """
        if 0 <= self.current < len(self.dates) and not self._plot.isEmpty():
            column = QRectF(self._x(self.current) - 7, 0, 14, self.height())
            self.update(column.toAlignedRect())

    def paintEvent(self, event) -> None:
        """
        Draw daily closing balances or grouped reporting-month bars on one shared value scale.
        """
        if not self.dates or not self.traces:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(_font(10, money=True))
        width = self.axis_label_width
        plot = QRectF(width + 10, 8, max(1, self.width() - width - 20), self.height() - 34)
        if plot != self._plot:
            self._plot = plot
            self._line_path = None
        muted = _colour(self, "muted")
        lo, hi = self._ticks[0], self._ticks[-1]
        for tick, text in zip(self._ticks, self._tick_labels):
            y = self._plot.bottom() - self._plot.height() * (tick - lo) / (hi - lo)
            painter.setPen(QPen(_colour(self, "control_border" if self.cash and tick == 0 else "border"), 1))
            painter.drawLine(QPointF(self._plot.left(), y), QPointF(self._plot.right(), y))
            painter.setPen(muted)
            painter.drawText(QRectF(0, y - 8, width, 16), Qt.AlignmentFlag.AlignRight |
                             Qt.AlignmentFlag.AlignVCenter, text)
        self._draw_dates(painter, muted)
        painter.save()
        painter.setClipRect(self._plot.adjusted(-6, -6, 6, 6))
        for sequence, trace in enumerate(self.traces):
            ink = _colour(self, trace.role)
            if self.cash:
                self._draw_bars(painter, sequence, trace, ink)
            else:
                self._draw_line(painter, trace.values, ink)
        if self.provisional:
            painter.setBrush(_colour(self, self._background_role))
            painter.setPen(QPen(_colour(self, "warning_text"), 1.4))
            last_x = -10.0
            for index in self.provisional:
                if self._x(index) >= last_x + 6:
                    painter.drawEllipse(self._point(index, self.traces[0].values[index]), 2.6, 2.6)
                    last_x = self._x(index)
        if self.current >= 0 or self._keyboard_focus:
            index = self.current if self.current >= 0 else len(self.dates) - 1
            self._draw_inspection(painter, index, muted)
        painter.restore()

    def _draw_inspection(self, painter: QPainter, index: int, muted: QColor) -> None:
        """
        Mark a single daily point or draw the value guide across the selected cash bar.
        """
        painter.setPen(QPen(muted, 1, Qt.PenStyle.DashLine))
        if self.cash:
            x = self._x(index)
            painter.drawLine(QPointF(x, self._plot.top()), QPointF(x, self._plot.bottom()))
            series = max(0, self._series) if self._keyboard_focus else self._series
            if series >= 0:
                trace = self.traces[series]
                y = self._point(index, trace.values[index]).y()
                painter.drawLine(QPointF(self._plot.left(), y), QPointF(self._plot.right(), y))
                if self._keyboard_focus:
                    bar = self._bar_rect(index, series)
                    painter.setPen(QPen(_colour(self, "text"), 2))
                    painter.drawLine(QPointF(bar.left(), y), QPointF(bar.right(), y))
            return
        trace = self.traces[0]
        point = self._point(index, trace.values[index])
        painter.drawLine(QPointF(point.x(), self._plot.top()), QPointF(point.x(), self._plot.bottom()))
        radius = 3.3
        if self._pin_animation.state() == QAbstractAnimation.State.Running:
            radius = self._pin_animation.currentValue()
        painter.setPen(QPen(_colour(self, self._background_role), 1.5))
        painter.setBrush(_colour(self, trace.role))
        painter.drawEllipse(point, radius, radius)
        if self._keyboard_focus:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(_colour(self, "text"), 1))
            painter.drawEllipse(point, 5.5, 5.5)

    def focusInEvent(self, event) -> None:
        """
        Give keyboard navigation a local point cue without framing the plot on a mouse click.
        """
        self._keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason,
                                                 Qt.FocusReason.BacktabFocusReason)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:
        """
        Retain the inspection when focus leaves and remove its keyboard-only cue.
        """
        self._keyboard_focus = False
        super().focusOutEvent(event)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        """
        Preview the nearest dated observation while the pointer is in the plot.
        """
        self.preview.emit(*self._observation(event.position()))

    def leaveEvent(self, event) -> None:
        """
        Release the pointer preview while allowing a pinned selection to remain.
        """
        self.preview.emit(-1, -1)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        """
        Pin only a left-clicked plotted observation.
        """
        index, series = self._observation(event.position())
        if event.button() == Qt.MouseButton.LeftButton and index >= 0:
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            self._keyboard_focus = False
            self.pin.emit(index, series)
            event.accept()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        """
        Step between actual points, pin the active date, or release inspection.
        """
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._keyboard_focus = True
            self.step.emit(-1 if key == Qt.Key.Key_Left else 1)
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.dates:
            self._keyboard_focus = True
            series = max(0, self._series) if self.cash else -1
            self.pin.emit(self.current if self.current >= 0 else len(self.dates) - 1, series)
        elif key == Qt.Key.Key_Escape:
            self.clear.emit()
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def _observation(self, position: QPointF) -> tuple[int, int]:
        """
        Hit-test a month and its actual bar, including zero amounts on the baseline.
        """
        index = self._index(position)
        if self.cash and index >= 0:
            for series in range(len(self.traces)):
                bar = self._bar_rect(index, series)
                if bar.adjusted(0, -1, 0, 1).contains(position):
                    return index, series
        return index, -1

    def _index(self, position: QPointF) -> int:
        """
        Map the full plot to discrete points, including the sole point in a new workspace.
        """
        if not self.dates or not self._plot.adjusted(-4, -4, 4, 4).contains(position):
            return -1
        if len(self.dates) == 1:
            return 0
        pitch = self._x(1) - self._x(0)
        return max(0, min(len(self.dates) - 1, round((position.x() - self._x(0)) / pitch)))

    def _x(self, index: int) -> float:
        """
        Centre monthly columns in their date bins and spread line points across the plot.
        """
        if self.cash:
            return self._plot.left() + self._plot.width() * (index + .5) / len(self.dates)
        if len(self.dates) == 1:
            return self._plot.center().x()
        return self._plot.left() + self._plot.width() * index / (len(self.dates) - 1)

    def _point(self, index: int, value: Decimal | int) -> QPointF:
        """
        Map exact amounts to floating-point coordinates only at paint time.
        """
        lo, hi = self._ticks[0], self._ticks[-1]
        y = self._plot.bottom() - self._plot.height() * (float(value) - lo) / (hi - lo)
        return QPointF(self._x(index), y)

    def _draw_line(self, painter: QPainter, values: tuple, ink: QColor) -> None:
        """
        Retain every daily close in the path while keeping repeated pointer inspections inexpensive.
        """
        painter.setPen(QPen(ink, 2.4, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if len(values) == 1:
            painter.drawPoint(self._point(0, values[0]))
            return
        if self._line_path is None:
            self._line_path = QPainterPath(self._point(0, values[0]))
            for index in range(1, len(values)):
                self._line_path.lineTo(self._point(index, values[index]))
        painter.drawPath(self._line_path)

    def _draw_bars(self, painter: QPainter, series: int, trace: ChartTrace, ink: QColor) -> None:
        """
        Draw the three signed cash measures from the same zero line within each reporting month.
        """
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ink)
        for index, value in enumerate(trace.values):
            if value:
                bar = self._bar_rect(index, series)
                radius = bar.width() / 2
                # A short value reveals only the tip of a full-width semicircle above or below zero.
                extension = max(radius, bar.width() - bar.height())
                extended = (bar.adjusted(0, 0, 0, extension) if value > 0
                            else bar.adjusted(0, -extension, 0, 0))
                painter.save()
                painter.setClipRect(bar, Qt.ClipOperation.IntersectClip)
                painter.drawRoundedRect(extended, radius, radius)
                painter.restore()

    def _bar_rect(self, index: int, series: int) -> QRectF:
        """
        Use the same signed bar bounds for painting and pointer inspection at every horizon.
        """
        group = min(self._max_group_width, self._plot.width() / len(self.dates) * .72)
        width = group / len(self.traces)
        zero = self._point(index, 0).y()
        point = self._point(index, self.traces[series].values[index])
        x = point.x() - group / 2 + series * width
        return QRectF(x, min(zero, point.y()), max(.4, width - min(2, width * .16)), abs(zero - point.y()))

    def _draw_dates(self, painter: QPainter, colour: QColor) -> None:
        """
        Space actual date labels along the timeline without collisions at its ends.
        """
        painter.setFont(_font(10))
        painter.setPen(colour)
        if self.cash:
            capacity = min(6, max(2, int(self._plot.width() // 90)))
            last = len(self.dates) - 1
            stride = max(1, ceil(last / (capacity - 1)))
            indices = list(range(0, last + 1, stride))
            if indices[-1] != last:
                if len(indices) > 1 and self._x(last) - self._x(indices[-1]) < 65:
                    indices.pop()
                indices.append(last)
        else:
            painter.setPen(QPen(_colour(self, "border"), 1))
            for index in self.date_ticks:
                x = self._x(index)
                painter.drawLine(QPointF(x, self._plot.bottom()), QPointF(x, self._plot.bottom() + 4))
            label_width = painter.fontMetrics().horizontalAdvance("28 May") + 12
            capacity = max(1, int(self._plot.width() / label_width))
            indices = self.date_ticks[::max(1, ceil(len(self.date_ticks) / capacity))]
        painter.setPen(colour)
        previous_right = -1
        for index in sorted(indices):
            day = self.dates[index]
            label = day.strftime("%d %b" if self.weekly else "%b %y").lstrip("0")
            width = painter.fontMetrics().horizontalAdvance(label) + 2
            x = max(self._plot.left(), min(self._x(index) - width / 2, self._plot.right() - width))
            if x < previous_right + 4:
                continue
            previous_right = x + width
            painter.drawText(QRectF(x, self.height() - 19, width, 18), Qt.AlignmentFlag.AlignCenter, label)


# %% 2. Spending Categories ----------------------------------------------------------------------------------
class CategoryChart(QWidget):
    """
    Rank signed spending by magnitude and draw refunds on the opposite side of the zero baseline.
    """

    def __init__(self) -> None:
        """
        Size the chart to its real rows and use the surrounding Analysis page for scrolling.
        """
        super().__init__()
        self.values = ()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName("Spending by category")
        self.setMouseTracking(True)

    def set_values(self, values: tuple, expenses: Decimal) -> None:
        """
        Preserve exact signed amounts in the visible ranking and accessible descriptions.
        """
        self.values = tuple(sorted(values, key=lambda item: (-abs(item[1]), item[0].casefold())))
        self.shares = tuple(f"{value / expenses:.1%}" if expenses else "—" for _, value in self.values)
        self.setFixedHeight(max(32, len(self.values) * 36))
        self.details = tuple(f"{name}  {money(value)}  {share}"
                             for (name, value), share in zip(self.values, self.shares))
        self.setAccessibleDescription("\n".join(self.details) or "No spending in this month.")
        self.setToolTip("")
        self.update()

    def mouseMoveEvent(self, event) -> None:
        """
        Expose complete category names and exact amounts even when a long name needs eliding.
        """
        index = int(event.position().y() // 36)
        self.setToolTip(self.details[index] if 0 <= index < len(self.values) else "")

    def paintEvent(self, event) -> None:
        """
        Pair each category and exact amount with a scaled directional bar on the next line.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(_font(12))
        if not self.values:
            painter.setPen(_colour(self, "muted"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter, "No spending in this month.")
            return
        largest = float(max(max(v for _, v in self.values), 0))
        refund = float(max(-min(v for _, v in self.values), 0))
        scale = self.width() / max(1e-20, largest + refund)
        zero = refund * scale
        painter.setFont(_font(11, money=True))
        share_width = max(painter.fontMetrics().horizontalAdvance(text) for text in self.shares) + 2
        for index, (name, value) in enumerate(self.values):
            y = index * 36
            amount = money(value)
            painter.setFont(_font(11, money=True))
            painter.setPen(_colour(self, "muted"))
            right = self.width() - share_width
            painter.drawText(QRectF(right, y, share_width, 19), Qt.AlignmentFlag.AlignRight,
                             self.shares[index])
            painter.setPen(_colour(self, "text"))
            painter.setFont(_font(12, money=True))
            w = painter.fontMetrics().horizontalAdvance(amount) + 3
            amount_left = right - w - 10
            painter.drawText(QRectF(amount_left, y, w, 19), Qt.AlignmentFlag.AlignRight, amount)
            painter.setFont(_font(12))
            available = max(0, amount_left - 10)
            name = painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, available)
            painter.drawText(QRectF(0, y, available, 19), Qt.AlignmentFlag.AlignLeft, name)
            endpoint = zero + float(value) * scale
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_colour(self, "analysis_category"))
            if value:
                bar = QRectF(min(zero, endpoint), y + 20, abs(endpoint - zero), 8)
                painter.drawRoundedRect(bar, 4, 4)
            if refund:
                painter.setPen(QPen(_colour(self, "muted"), 1))
                anchor = min(max(.5, zero), self.width() - .5)
                painter.drawLine(QPointF(anchor, y + 18), QPointF(anchor, y + 30))


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def money(value: Decimal, *, signed: bool = False) -> str:
    """
    Format ungrouped Analysis amounts with cents and a sign before the currency symbol.
    """
    sign = "-" if value < 0 else "+" if value > 0 and signed else ""
    return f"{sign}${abs(value):.2f}"


def _axis_ticks(values: list[float]) -> tuple[float, ...]:
    """
    Choose a small set of round tick amounts that includes every visible value.
    """
    if not values:
        return 0.0, 1.0
    low, high = min(values), max(values)
    if low == high:
        pad = max(1, abs(low) * .05)
        low -= pad
        high += pad
    raw = (high - low) / 3
    magnitude = 10 ** floor(log10(raw))
    step = next(n for n in (1, 2, 2.5, 5, 10) if n >= raw / magnitude) * magnitude
    start, end = floor(low / step), ceil(high / step)
    return tuple(i * step for i in range(start, end + 1))


def _axis_labels(ticks: tuple) -> tuple[str, ...]:
    """
    Abbreviate large axis values only while each adjacent tick remains distinguishable.
    """
    scale, suffix = next(((scale, suffix) for scale, suffix in ((1e9, "b"), (1e6, "m"), (1e3, "k"))
                          if max(map(abs, ticks)) >= scale), (1, ""))
    for digits in range(0, 10):
        values = tuple(("-" if n < 0 else "") + f"${abs(n) / scale:.{digits}f}{suffix}" if n else "$0"
                       for n in ticks)
        precise = all(abs(round(n / scale, digits) - n / scale) < 1e-9 for n in ticks)
        if len(set(values)) == len(ticks) and precise:
            return values
    return tuple(f"${n:g}" for n in ticks)


def _colour(widget: QWidget, role: str) -> QColor:
    """
    Use the desktop's semantic colours with readable defaults before a workspace theme is available.
    """
    if role.startswith("budget_"):
        return QColor(budget_colour(widget, role.removeprefix("budget_")))
    fallback = {"content": "#ffffff", "featured": "#e7efeb", "text": "#243b3b", "muted": "#637a78",
                "heading": "#235b58",
                "border": "#d3ded9", "control_border": "#7b8d87", "warning_text": "#8a4b2d",
                "analysis_category": "#476967"}
    return QColor(gui_colour(widget, role, fallback[role]))


def _font(size: int, *, money: bool = False) -> QFont:
    """
    Keep dates and category text in Geist and monetary chart labels in Geist Mono.
    """
    font = QFont("Geist Mono" if money else "Geist")
    font.setPixelSize(size)
    return font
