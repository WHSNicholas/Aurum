# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 Workflows - Opening                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Record bank starting positions from Setup as ordinary balanced journal entries.

Configuration remains descriptive. The saved journal and its explicit opening provenance supply every
displayed amount, and account-level revisions prevent competing forms from creating duplicate positions.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from decimal import Decimal, InvalidOperation
import sqlite3
from uuid import UUID

# Aurum imports
from aurum.database.journal import load_journal_entries
from aurum.database.manual import load_opening_entry_ids
from aurum.database.reviews import _digest, _require_idle
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.budget import BudgetSettings
from aurum.finance.dates import parse_input_date
from aurum.finance.journal import JournalEntry, JournalLine
from aurum.finance.securities import SecurityRegistry
from aurum.workflows.editing import _apply_journal_edit, load_journal_edit


# %% 1. Bank Starting Positions -----------------------------------------------------------------------------
def load_bank_openings(
    connection: sqlite3.Connection, accounts: AccountRegistry, capital_account_key: str,
    *, entries: list[JournalEntry] | tuple[JournalEntry, ...] | None = None,
    opening_ids: set[UUID] | None = None,
) -> dict[str, dict]:
    """
    Read saved bank positions and revision tokens without inferring balances or changing configuration.

    Complex and multiple openings remain visible but must be edited as complete journal events. The same
    context builder is used under the posting lock, so its account-level revision also protects new entries.
    """
    entries = list(entries) if entries is not None else load_journal_entries(connection, accounts)
    opening_ids = opening_ids if opening_ids is not None else load_opening_entry_ids(connection)
    manuals = _opening_owners(connection)
    capital = accounts.accounts.get(capital_account_key)
    contexts = {}
    for account in accounts.accounts.values():
        if account.account_type != AccountType.ASSET or account.bank_details is None:
            continue
        related = [entry for entry in entries if any(line.account_id == account.id for line in entry.lines)]
        openings = [entry for entry in related if entry.id in opening_ids]
        # Old source-free bank entries need explicit review, not another inferred starting position.
        unexplained = [entry for entry in related if entry.id not in opening_ids and any(
            line.account_id == account.id and line.transaction_id is None for line in entry.lines
        )]
        related_ids = {entry.id for entry in related}
        owners = [record for record in manuals if record["journal_entry_id"] in related_ids]
        revision = _digest({
            "account": account.model_dump(mode="json"),
            "capital": capital.model_dump(mode="json") if capital else None,
            "entries": [entry.model_dump(mode="json") for entry in related],
            "opening_ids": sorted(str(entry.id) for entry in openings),
            "owners": [{
                "manual_id": str(record["manual_id"]), "revision": str(record["input_revision"]),
                "status": record["status"], "journal_entry_id": str(record["journal_entry_id"]),
            } for record in owners],
        })
        candidates = openings + unexplained
        summaries = [{
            "journal_entry_id": str(entry.id), "date": entry.date.isoformat(),
            "amount": _amount(entry, account.id),
        } for entry in candidates]
        selected = candidates[0] if len(candidates) == 1 else None
        context = {
            "account_id": str(account.id), "account_key": account.key, "account_name": account.name,
            "state": "empty", "amount": _amount(selected, account.id) if selected else "",
            "date": selected.date.isoformat() if selected else "", "revision": revision,
            "journal_entry_id": str(selected.id) if selected else None, "manual_entry_id": None,
            "journal_entry_ids": [row["journal_entry_id"] for row in summaries],
            "entries": summaries, "reason": "",
        }
        if capital is None or capital.account_type != AccountType.EQUITY:
            context.update(state="readonly", reason="Restore the configured Capital account first.")
        elif any(line.investment is not None for entry in related for line in entry.lines
                 if line.account_id == account.id):
            context.update(state="readonly", reason="Accounts holding securities use their trade entries.")
        elif unexplained:
            context.update(state="readonly", reason="Review the existing unlinked bank entries first.")
        elif len(openings) > 1:
            context.update(state="readonly", reason="Edit the existing opening entries in the journal.")
        elif selected is not None:
            owner = [record for record in owners if record["journal_entry_id"] == selected.id]
            if _simple_opening(selected, account.id, capital.id) and len(owner) == 1:
                context.update(state="editable", manual_entry_id=str(owner[0]["manual_id"]))
            else:
                context.update(state="readonly", reason="Edit this complete opening entry in the journal.")
        contexts[str(account.id)] = context
    return contexts


def save_bank_opening(
    connection: sqlite3.Connection, payload: dict, accounts: AccountRegistry, capital_account_key: str,
    *, securities: SecurityRegistry | None = None, budget: BudgetSettings | None = None,
) -> dict:
    """
    Post or correct one explicit bank position, checking identity and revisions inside the write lock.

    A repeated request for the exact saved amount and date is a no-op. Changing either value still needs
    the current revision, and every successful correction retains its existing event and manual identity.
    """
    try:
        account_id = UUID(str(payload.get("account_id", "")))
        amount = Decimal(str(payload.get("amount", "")))
    except (ValueError, InvalidOperation):
        raise ValueError("Choose a saved bank account and enter a valid starting amount.") from None
    if not amount.is_finite() or amount == 0:
        raise ValueError("Enter a nonzero starting amount; a zero balance needs no journal entry.")
    numerator, denominator = amount.as_integer_ratio()
    if numerator * 100 % denominator:
        raise ValueError("The starting amount must use whole cents.")
    when = parse_input_date(payload.get("date"))
    if when is None:
        raise ValueError("Choose the accounting date for this starting balance.")
    if not isinstance(payload.get("revision"), str) or not payload["revision"]:
        raise ValueError("Refresh the saved bank account before posting its starting balance.")
    _require_idle(connection)
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        contexts = load_bank_openings(connection, accounts, capital_account_key)
        current = contexts.get(str(account_id))
        if current is None:
            raise ValueError("Starting balances in Setup are available only for saved asset bank accounts.")
        if current["state"] == "readonly":
            raise ValueError(current["reason"])
        if current["state"] == "editable" and (
            current["date"] == when.isoformat() and Decimal(current["amount"]) == amount
        ):
            return {
                "account_id": str(account_id), "journal_entry_id": current["journal_entry_id"],
                "bank_opening": current,
            }
        if current["revision"] != payload["revision"]:
            raise ValueError("This bank starting balance changed. Reload Accounts to review it before posting.")
        context = load_journal_edit(
            connection, current["journal_entry_id"], accounts, securities=securities, budget=budget,
            manual_id=current["manual_entry_id"], date=when,
        )
        capital = accounts.accounts[capital_account_key]
        lines = context["entry"]["lines"]
        if not lines:
            lines = [
                {"account_id": str(identifier), "description": "Starting bank balance"}
                for identifier in (account_id, capital.id)
            ]
        postings = []
        for line in lines:
            movement = amount if UUID(str(line["account_id"])) == account_id else amount.copy_negate()
            postings.append(JournalLine.model_validate({
                **line, "debit": max(movement, Decimal("0")),
                "credit": max(movement.copy_negate(), Decimal("0")),
            }))
        entry = JournalEntry(id=UUID(context["entry"]["id"]), date=when, lines=tuple(postings))
        saved = _apply_journal_edit(connection, {
            "journal_entry_id": context["journal_entry_id"],
            "manual_entry_id": context["manual_entry_id"],
            "revision": context["revision"], "entry": entry.model_dump(mode="json"), "opening": True,
        }, accounts, securities=securities, budget=budget)
        fresh = load_bank_openings(connection, accounts, capital_account_key)[str(account_id)]
        return {"account_id": str(account_id), "journal_entry_id": str(saved.id), "bank_opening": fresh}


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _opening_owners(connection: sqlite3.Connection) -> list[dict]:
    """
    Read latest manual ownership revisions without decoding instructions or accounting payloads on refresh.
    """
    latest = {}
    for revision, manual_id, status, event_id in connection.execute(
        "SELECT id, manual_id, status, journal_entry_id FROM manual_entries ORDER BY rowid"
    ):
        latest[manual_id] = {
            "input_revision": revision, "manual_id": manual_id, "status": status,
            "journal_entry_id": UUID(event_id) if event_id else None,
        }
    return list(latest.values())


def _simple_opening(entry: JournalEntry, account_id: UUID, capital_id: UUID) -> bool:
    """
    Recognise only a complete two-line bank and Capital entry that the inline fields can represent.
    """
    return (
        len(entry.lines) == 2
        and {line.account_id for line in entry.lines} == {account_id, capital_id}
        and all(line.transaction_id is None and line.investment is None and line.account_investment is None
                for line in entry.lines)
    )


def _amount(entry: JournalEntry, account_id: UUID) -> str:
    """
    Format the bank's signed posting total without changing Decimal precision or rounding stored cents.
    """
    cents = 0
    for line in entry.lines:
        if line.account_id == account_id:
            for sign, value in ((1, line.debit), (-1, line.credit)):
                numerator, denominator = value.as_integer_ratio()
                cents += sign * numerator * 100 // denominator
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}{whole}.{fraction:02d}"
