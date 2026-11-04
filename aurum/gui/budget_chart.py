# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                GUI - Budget Allocation                                               #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Draw the selected month's entered budget amounts on a shared income and allocation scale.

The chart reads draft form values only. Hover descriptions and an accessible text summary expose exact
amounts while missing targets remain distinct from intentional zero targets.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from decimal import Decimal, localcontext

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QToolTip, QWidget

# Aurum imports
from aurum.gui.theme import BUDGET_COLOURS, gui_colour


# %% 1. Allocation Chart -------------------------------------------------------------------------------------
class BudgetAllocationChart(QWidget):
    """
    Compare income above separate expense and savings pills using one dollar scale.
    """

    def __init__(self, parent=None) -> None:
        """
        Prepare a responsive local chart with keyboard-accessible amount descriptions.
        """
        super().__init__(parent)
        self.setObjectName("budgetAllocationChart")
        self.setFixedHeight(140)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Monthly budget allocation")
        self._rows = []
        self._regions = []
        self._hit_regions = []

    def set_amounts(self, rows: list[tuple[str, str, Decimal | None]]) -> None:
        """
        Retain valid nonnegative amounts and describe missing values without treating them as zero.
        """
        self._rows = rows
        self._regions = []
        self._hit_regions = []
        descriptions = [
            f"{name}: {'not set' if amount is None else _money(amount)}"
            for name, _, amount in rows
        ]
        text = "\n".join(descriptions) or "Add categories and enter monthly amounts."
        self.setToolTip(text)
        self.setAccessibleDescription(text)
        self.update()

    def paintEvent(self, event) -> None:
        """
        Show three fully rounded pills with a common dollar scale and value-ordered tonal segments.
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = max(1, self.width() - 2)
        groups = {
            kind: sorted(
                [(name, amount) for name, item_kind, amount in self._rows
                 if item_kind == kind and amount is not None and amount > 0],
                key=lambda item: item[1], reverse=True,
            )
            for kind in ("income", "expense", "savings")
        }
        values = [amount for rows in groups.values() for _, amount in rows]
        with localcontext() as context:
            context.prec = max([28, *(len(value.as_tuple().digits) + 8 for value in values)])
            totals = {kind: sum((value for _, value in rows), Decimal(0))
                      for kind, rows in groups.items()}
            allocation = totals["expense"] + totals["savings"]
            scale = max(totals["income"], allocation, Decimal(1))
            gap = 8 if totals["expense"] and totals["savings"] else 0
            available = max(1, width - gap)
            lengths = {kind: float(total / scale) * available for kind, total in totals.items()}
            positive = [kind for kind in ("expense", "savings") if totals[kind] > 0]
            minimum = min(10.0, available / max(1, len(positive)))
            for kind in positive:
                lengths[kind] = max(minimum, lengths[kind])
            excess = sum(lengths[kind] for kind in positive) - available
            for kind in sorted(positive, key=lengths.get, reverse=True):
                reduction = min(max(excess, 0), lengths[kind] - minimum)
                lengths[kind] -= reduction
                excess -= reduction
            origins = {
                "income": gap / 2, "expense": 0.0,
                "savings": lengths["expense"] + gap,
            }
            labels = {kind: f"{name} · {_money(totals[kind])}" for kind, name in (
                ("income", "Income"), ("expense", "Expenses"), ("savings", "Savings"),
            )}
            painter.setPen(QColor(gui_colour(self, "text", "#425f59")))
            alignment = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            painter.drawText(QRectF(origins["income"], 0, width - origins["income"], 22),
                             alignment, labels["income"])
            metrics = painter.fontMetrics()
            expense_label_width = metrics.horizontalAdvance(labels["expense"])
            savings_label_width = metrics.horizontalAdvance(labels["savings"])
            crowded = (
                expense_label_width + 12 > origins["savings"]
                or origins["savings"] + savings_label_width > width
            )
            painter.drawText(QRectF(0, 72, width, 22), alignment, labels["expense"])
            if crowded:
                painter.drawText(QRectF(0, 72, width, 22),
                                 Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                                 labels["savings"])
            else:
                painter.drawText(QRectF(origins["savings"], 72, width - origins["savings"], 22),
                                 alignment, labels["savings"])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(gui_colour(self, "panel", "#e8eeeb")))
            if not totals["income"]:
                painter.drawRoundedRect(QRectF(0, 28, width, 36), 18, 18)
            if not allocation:
                placeholder = max(0, (width - 8) / 2)
                painter.drawRoundedRect(QRectF(0, 100, placeholder, 36), 18, 18)
                painter.drawRoundedRect(QRectF(placeholder + 8, 100, placeholder, 36), 18, 18)
            self._regions = []
            self._hit_regions = []
            for kind in ("income", "expense", "savings"):
                span = lengths[kind]
                if span <= 0:
                    continue
                y = 28 if kind == "income" else 100
                offset = origins[kind]
                pill = QPainterPath()
                pill.addRoundedRect(QRectF(offset, y, span, 36), 18, 18)
                painter.save()
                painter.setClipPath(pill)
                rows = groups[kind]
                for index, (name, amount) in enumerate(rows):
                    segment = float(amount / totals[kind]) * span
                    rectangle = QRectF(offset, y, segment, 36)
                    painter.setBrush(_shade(self, kind, index, len(rows)))
                    painter.drawRect(rectangle)
                    self._regions.append((rectangle, f"{name}\n{_money(amount)}"))
                    region = QPainterPath()
                    region.addRect(rectangle)
                    self._hit_regions.append((pill.intersected(region), f"{name}\n{_money(amount)}"))
                    offset += segment
                painter.restore()
    def event(self, event) -> bool:
        """
        Keep ordinary hover help on actual segments, including no amount tooltip in the allocation gap.
        """
        if event.type() == QEvent.Type.ToolTip:
            for path, text in self._hit_regions:
                if path.contains(QPointF(event.pos())):
                    QToolTip.showText(event.globalPos(), text, self)
                    return True
            QToolTip.hideText()
            return True
        return super().event(event)

    def mouseMoveEvent(self, event) -> None:
        """
        Reveal the exact category amount under the pointer.
        """
        for path, text in self._hit_regions:
            if path.contains(event.position()):
                QToolTip.showText(event.globalPosition().toPoint(), text, self)
                return
        QToolTip.hideText()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        """
        Clear segment hover text when the pointer leaves the chart.
        """
        QToolTip.hideText()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:
        """
        Make the complete amount description available without a mouse.
        """
        super().focusInEvent(event)
        QToolTip.showText(self.mapToGlobal(QPoint(8, self.height())), self.toolTip(), self)


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _shade(widget: QWidget, kind: str, index: int, count: int) -> QColor:
    """
    Sample muted blue, red or green while retaining a visible dark-to-light progression.
    """
    role = "budget_expenses" if kind == "expense" else "budget_" + kind
    base = QColor(gui_colour(widget, role, BUDGET_COLOURS[kind]))
    first = base.darker(120)
    last = QColor(*(round(channel + (255 - channel) * 0.42) for channel in base.getRgb()[:3]))
    fraction = index / max(1, count - 1)
    return QColor(*(
        round(start + (end - start) * fraction)
        for start, end in zip(first.getRgb()[:3], last.getRgb()[:3])
    ))


def _money(value: Decimal) -> str:
    """
    Format exact AUD with narrow nonbreaking thousands separators.
    """
    return "$" + f"{value:,.2f}".replace(",", "\u202f")
