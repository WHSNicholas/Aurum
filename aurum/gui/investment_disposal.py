# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                              GUI - Investment Disposal                                               #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Collect explicit disposal facts and preview their shared, revision-bound accounting before posting.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from decimal import Decimal

from pydantic import ValidationError
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.gui.icons import icon
from aurum.workflows.editing import build_direct_disposal


# %% 1. Disposal Form ----------------------------------------------------------------------------------------
class InvestmentDisposalDialog(QDialog):
    """
    Keep sale or withdrawal inputs separate from the underlying journal draft until an acknowledged post.
    """

    post_requested = Signal(object)

    def __init__(self, context: dict, parent=None):
        """
        Offer designated holdings and an explicit realised-income choice for one immutable incoming receipt.
        """
        super().__init__(parent)
        self.context = deepcopy(context)
        self._busy = False
        self._preview = None
        self.setWindowTitle("Investment disposal")
        self.setModal(True)
        self.resize(560, 430)
        layout = QVBoxLayout(self)
        heading = QLabel("Investment disposal")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        self.form_widget = QWidget()
        form = QFormLayout(self.form_widget)
        form.setContentsMargins(0, 0, 0, 0)
        self.holding = QComboBox()
        self.holding.setAccessibleName("Investment being disposed")
        self.holding.addItem("Choose investment", None)
        securities = {item["id"]: item for item in context.get("securities", [])}
        for account in context["accounts"]:
            if account["key"] not in context["investment_accounts"]:
                continue
            identifiers = set()
            for entry in context["entries"]:
                for line in entry["lines"]:
                    if line["account_id"] == account["id"] and line.get("investment"):
                        identifiers.add(line["investment"]["security_id"])
            if identifiers:
                for security_id in sorted(identifiers):
                    security = securities.get(security_id)
                    if security is not None:
                        self.holding.addItem(
                            f"{account['name']} · {security['name']}",
                            {"kind": "security_sale", "investment_account_id": account["id"],
                             "security_id": security_id},
                        )
            else:
                self.holding.addItem(
                    account["name"],
                    {"kind": "account_withdrawal", "investment_account_id": account["id"]},
                )
        form.addRow("Investment", self.holding)
        self.proceeds = QLabel(_money(Decimal(context["sources"][0]["amount"])))
        self.proceeds.setProperty("monetary", True)
        form.addRow("Cash received", self.proceeds)
        self.quantity_label = QLabel("Units sold")
        self.quantity = QLineEdit()
        self.quantity.setAccessibleName("Actual units sold")
        form.addRow(self.quantity_label, self.quantity)
        self.value_label = QLabel("Confirmed value before withdrawal")
        self.value = QLineEdit()
        self.value.setProperty("monetary", True)
        self.value.setAccessibleName("Confirmed investment value before withdrawal")
        form.addRow(self.value_label, self.value)
        self.realised = QComboBox()
        self.realised.setAccessibleName("Income account for realised returns")
        self.realised.addItem("Choose income account", None)
        for account in context["accounts"]:
            if account["account_type"] == "income":
                self.realised.addItem(account["name"], account["id"])
        form.addRow("Realised return", self.realised)
        self.cost = QLabel("—")
        self.cost.setProperty("monetary", True)
        self.gain = QLabel("—")
        self.gain.setProperty("monetary", True)
        form.addRow("Recorded cost released", self.cost)
        form.addRow("Realised gain / loss", self.gain)
        layout.addWidget(self.form_widget)
        self.error = QLabel()
        self.error.setObjectName("editorError")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        note = QLabel("Post replaces the draft postings for this receipt.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch()
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_button = _action("Cancel disposal", "close")
        self.cancel_button.clicked.connect(self.reject)
        actions.addWidget(self.cancel_button)
        self.post_button = _action("Post investment disposal", "save")
        self.post_button.clicked.connect(self._post)
        actions.addWidget(self.post_button)
        layout.addLayout(actions)
        self.holding.currentIndexChanged.connect(self._update_preview)
        self.realised.currentIndexChanged.connect(self._update_preview)
        self.quantity.textChanged.connect(self._update_preview)
        self.value.textChanged.connect(self._update_preview)
        self._update_preview()

    def set_busy(self, busy: bool) -> None:
        """
        Keep the submitted facts visible and unchanged until their save finishes.
        """
        self._busy = busy
        self.form_widget.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)
        self.post_button.setEnabled(not busy and self._preview is not None)

    def show_error(self, message: str) -> None:
        """
        Retain entered disposal facts after a rejected or stale atomic save.
        """
        self.error.setText(message)

    def reject(self) -> None:
        """
        Cancel without changing the underlying journal draft or interrupting an active save.
        """
        if not self._busy:
            super().reject()

    def _update_preview(self, *_args) -> None:
        """
        Recalculate complete explicit input locally, keeping unfinished fields quiet.
        """
        selection = self.holding.currentData()
        sale = bool(selection and selection["kind"] == "security_sale")
        self.quantity.setVisible(sale)
        self.quantity_label.setVisible(sale)
        self.value.setVisible(bool(selection) and not sale)
        self.value_label.setVisible(bool(selection) and not sale)
        self._preview = None
        self.cost.setText("—")
        self.gain.setText("—")
        self.error.clear()
        self.post_button.setEnabled(False)
        text = self.quantity.text().strip() if sale else self.value.text().strip()
        if selection is None or self.realised.currentData() is None or not text:
            return
        intent = {**selection, "realised_account_id": self.realised.currentData()}
        intent["units" if sale else "confirmed_value"] = text
        try:
            entry, calculation, _instruction = build_direct_disposal(intent, self.context)
        except (ValueError, ValidationError) as error:
            message = error.errors(include_input=False, include_url=False)[0]["msg"] if isinstance(
                error, ValidationError
            ) else str(error)
            self.error.setText(message.removeprefix("Value error, "))
            return
        cost = calculation.total_cost if sale else calculation.released_cost
        self.cost.setText(_money(cost))
        self.gain.setText(_money(calculation.realised_gain))
        self._preview = {
            "entry": entry.model_dump(mode="json"),
            "disposal": {**intent, "cost_revision": calculation.cost_revision},
        }
        self.post_button.setEnabled(not self._busy)

    def _post(self) -> None:
        """
        Submit only the reviewed calculation; the controller closes this dialog after acknowledgement.
        """
        if not self._busy and self._preview is not None:
            self.post_requested.emit(deepcopy(self._preview))


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _money(amount: Decimal) -> str:
    """
    Keep money grouped with narrow spaces and a leading sign, matching the financial summary.
    """
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.2f}".replace(",", "\u202f")


def _action(label: str, symbol: str) -> QToolButton:
    """
    Match the journal editor's accessible compact footer controls.
    """
    button = QToolButton()
    button.setObjectName("viewerIconAction")
    button.setIcon(icon(symbol, size=20))
    button.setIconSize(QSize(20, 20))
    button.setFixedSize(36, 36)
    button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    button.setToolTip(label)
    button.setAccessibleName(label)
    return button
