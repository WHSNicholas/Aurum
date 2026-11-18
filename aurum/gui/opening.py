# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 Gui - Bank Openings                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Keep explicit bank opening postings beside account settings without making balances configuration fields.

Drafts belong to stable account identities and survive catalogue selection and read-only refreshes. Only the
row's post action submits accounting; ordinary configuration saves never submit these drafts.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMenu, QToolButton, QVBoxLayout, QWidget,
)

# Aurum imports
from aurum.finance.dates import parse_input_date
from aurum.gui.controls import DateField, confirm_discard
from aurum.gui.icons import icon


# %% 1. Opening Drafts ---------------------------------------------------------------------------------------
class BankOpeningDrafts(QObject):
    """
    Retain independent ledger drafts and their original revisions while Setup rebuilds account forms.
    """

    changed = Signal()
    action_requested = Signal(str, object)

    def __init__(self, parent=None) -> None:
        """
        Start without accounting context and wait for the workspace's read-only snapshot.
        """
        super().__init__(parent)
        self.contexts = {}
        self.drafts = {}
        self.blocked = "Refresh the workspace to load opening balances."
        self.pending = None

    def set_contexts(self, contexts: dict) -> None:
        """
        Refresh clean rows while keeping edited values and their original revision for stale-save checks.
        """
        self.contexts = deepcopy(contexts)
        for identifier in tuple(self.drafts):
            if not self.is_dirty(identifier):
                self.drafts.pop(identifier)
        self.changed.emit()

    def set_blocked(self, reason: str) -> None:
        """
        Explain why posting is unavailable without clearing the current draft.
        """
        if reason != self.blocked:
            self.blocked = reason
            self.changed.emit()

    def draft(self, identifier: str) -> dict:
        """
        Seed one account from its saved opening and preserve that revision until acknowledged or discarded.
        """
        if identifier not in self.drafts:
            context = self.contexts.get(identifier, {})
            amount, when = str(context.get("amount") or ""), str(context.get("date") or "")
            self.drafts[identifier] = {
                "amount": amount, "date": when, "baseline": (amount, when),
                "revision": context.get("revision", ""), "error": "",
            }
        return self.drafts[identifier]

    def is_dirty(self, identifier: str) -> bool:
        """
        Distinguish edited row fields from the saved date and amount.
        """
        draft = self.drafts.get(identifier)
        return bool(draft and (draft["amount"], draft["date"]) != draft["baseline"])

    def has_unsaved_changes(self) -> bool:
        """
        Protect every unposted opening draft, including accounts no longer selected.
        """
        return any(self.is_dirty(identifier) for identifier in self.drafts)

    def edit(self, identifier: str, amount: str, when: str) -> None:
        """
        Keep typed fields outside YAML and clear only their previous validation message.
        """
        self.draft(identifier).update(amount=amount, date=when, error="")
        self.changed.emit()

    def discard(self) -> None:
        """
        Restore the latest read-only snapshot after an explicit discard decision.
        """
        self.drafts.clear()
        self.changed.emit()

    def guard(self, parent: QWidget) -> bool:
        """
        Require a deliberate discard before navigation abandons unposted accounting fields.
        """
        if not self.has_unsaved_changes():
            return True
        if not confirm_discard(parent):
            return False
        self.discard()
        return True

    def post(self, identifier: str) -> None:
        """
        Validate one explicit row submission, leaving the workflow to recheck accounting under its lock.
        """
        context = self.contexts.get(identifier, {})
        if self.blocked or self.pending or context.get("state") not in {"empty", "editable"}:
            return
        draft = self.draft(identifier)
        try:
            amount = Decimal(draft["amount"].strip())
            if not amount.is_finite() or amount == 0 or amount != amount.quantize(Decimal("0.01")):
                raise ValueError("Enter a nonzero balance with at most two decimal places.")
            when = parse_input_date(draft["date"].strip())
            if when is None:
                raise ValueError("Choose the opening balance date.")
        except (InvalidOperation, ValueError) as error:
            draft["error"] = str(error) if isinstance(error, ValueError) else "Enter a valid AUD balance."
            self.changed.emit()
            return
        self.pending = identifier
        self.changed.emit()
        self.action_requested.emit("save_bank_opening", {
            "account_id": identifier, "date": when.isoformat(), "amount": format(amount, ".2f"),
            "revision": draft["revision"],
        })

    def complete(self, result: dict) -> None:
        """
        Clear only the row acknowledged by the posting workflow and retain every other account's draft.
        """
        identifier = str(result.get("account_id") or "")
        context = result.get("bank_opening")
        if identifier != self.pending or not isinstance(context, dict):
            self.fail("The opening balance was not saved. Try again.")
            return
        self.contexts[identifier] = deepcopy(context)
        self.drafts.pop(identifier, None)
        self.pending = None
        self.changed.emit()

    def fail(self, message: str) -> None:
        """
        Keep failed values and the original revision visible for correction or explicit reload.
        """
        if self.pending:
            self.draft(self.pending)["error"] = message
        self.pending = None
        self.changed.emit()


# %% 2. Inline Row -------------------------------------------------------------------------------------------
class BankOpeningRow(QWidget):
    """
    Offer one compact amount-and-date row using the account form's ordinary field styling.
    """

    def __init__(self, record: dict, drafts: BankOpeningDrafts, parent=None) -> None:
        """
        Bind a saved account identity without adding any values to its configuration record.
        """
        super().__init__(parent)
        self.identifier = str(record.get("id") or "")
        self.drafts = drafts
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        fields = QHBoxLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setSpacing(8)
        self.amount = QLineEdit()
        self.amount.setProperty("monetary", True)
        self.amount.setPlaceholderText("$0.00")
        self.amount.setAccessibleName("Opening bank balance in AUD")
        self.amount.setMinimumWidth(100)
        self.amount.setMaximumWidth(160)
        fields.addWidget(self.amount)
        self.date = DateField("")
        self.date.setPlaceholderText("Date")
        self.date.setAccessibleName("Opening balance date")
        self.date.setMinimumWidth(145)
        self.date.setMaximumWidth(190)
        fields.addWidget(self.date)
        self.post = QToolButton()
        self.post.setObjectName("viewerIconAction")
        self.post.setIcon(icon("save", size=18))
        self.post.setIconSize(QSize(18, 18))
        self.post.setFixedSize(30, 30)
        self.post.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.post.setAccessibleName("Post opening balance")
        self.post.clicked.connect(lambda: drafts.post(self.identifier))
        fields.addWidget(self.post)
        self.edit = QToolButton()
        self.edit.setObjectName("viewerIconAction")
        self.edit.setIcon(icon("pen", size=18))
        self.edit.setIconSize(QSize(18, 18))
        self.edit.setFixedSize(30, 30)
        self.edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.edit.setAccessibleName("Edit opening journal entry")
        self.edit.setToolTip("Edit the complete opening journal entry")
        self.edit.clicked.connect(self._edit_entry)
        fields.addWidget(self.edit)
        fields.addStretch()
        layout.addLayout(fields)
        self.error = QLabel()
        self.error.setObjectName("setupError")
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        self.amount.textEdited.connect(self._changed)
        self.date.textChanged.connect(self._changed)
        drafts.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        """
        Reflect saved context, retained drafts and posting availability without moving text cursors.
        """
        context = self.drafts.contexts.get(self.identifier, {})
        draft = self.drafts.draft(self.identifier)
        for field, value in ((self.amount, draft["amount"]), (self.date, draft["date"])):
            if field.text() != value:
                field.blockSignals(True)
                field.setText(value)
                field.blockSignals(False)
        readonly = context.get("state") == "readonly"
        reason = self.drafts.blocked or (
            "Save account settings and refresh the workspace first." if not context else
            str(context.get("reason") or "") if readonly else ""
        )
        enabled = not reason and not self.drafts.pending
        self.amount.setEnabled(enabled)
        self.date.setEnabled(enabled)
        self.post.setVisible(not readonly)
        self.post.setEnabled(enabled and self.drafts.is_dirty(self.identifier)
                             and bool(draft["amount"].strip()) and bool(draft["date"].strip()))
        tooltip = reason or (
            "Update this opening journal entry against Capital." if context.get("state") == "editable"
            else "Post the bank's starting balance against Capital. This is not a budget inflow."
        )
        self.post.setToolTip(tooltip)
        self.setToolTip(tooltip)
        self.edit.setVisible(readonly and bool(context.get("journal_entry_ids")))
        self.edit.setEnabled(not self.drafts.blocked and not self.drafts.pending)
        self.error.setText(draft["error"])
        self.error.setVisible(bool(draft["error"]))

    def _changed(self) -> None:
        """
        Capture user edits without marking the account YAML as dirty.
        """
        self.drafts.edit(self.identifier, self.amount.text(), self.date.text())

    def _edit_entry(self) -> None:
        """
        Open complete existing events instead of replacing part of a complex opening.
        """
        context = self.drafts.contexts.get(self.identifier, {})
        identifiers = context.get("journal_entry_ids", [])
        if len(identifiers) == 1:
            self.drafts.action_requested.emit("open_entry_editor", {"journal_entry_id": identifiers[0]})
        elif identifiers:
            entries = {str(row["journal_entry_id"]): row for row in context.get("entries", [])}
            menu = QMenu(self)
            for number, identifier in enumerate(identifiers, 1):
                entry = entries.get(str(identifier), {})
                amount = str(entry.get("amount") or "")
                amount = "-$" + amount[1:] if amount.startswith("-") else "$" + amount
                label = f"{entry.get('date', 'Opening ' + str(number))} · {amount}"
                action = menu.addAction(label)
                action.setData(str(identifier))
            selected = menu.exec(self.edit.mapToGlobal(self.edit.rect().bottomLeft()))
            if selected:
                self.drafts.action_requested.emit("open_entry_editor", {
                    "journal_entry_id": selected.data(),
                })
