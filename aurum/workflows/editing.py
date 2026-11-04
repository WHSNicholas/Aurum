# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 Workflows - Editing                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum saves explicit journal edits without asking AI to interpret accounting the person has already chosen.

The editor reads complete events and a revision of their evidence. Saving validates a balanced replacement,
protects imported bank facts and existing security trades, and records a human manual revision atomically.
The same functions serve desktop forms and other explicit editing interfaces.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections import Counter
from datetime import date as Date
import sqlite3
from uuid import UUID, uuid4

# Aurum imports
from aurum.database.journal import _check_journal_sources, load_journal_entries
from aurum.database.manual import (
    _audit_snapshot, _check_claims, _latest, _replace_entry, _retire_reviews,
    load_manual_entries, manual_case_revision, manual_context_revision,
)
from aurum.database.reviews import _context, _digest, _json, _now, _require_idle, load_review_cases
from aurum.database.transactions import load_transactions
from aurum.finance.accounts import AccountRegistry
from aurum.finance.budget import BudgetSettings, budget_revision, validate_budget_labels
from aurum.finance.journal import JournalEntry, JournalLine
from aurum.finance.manual import ManualCase, ManualInput, ManualOutcome
from aurum.finance.securities import SecurityRegistry


# %% 1. Journal Editing --------------------------------------------------------------------------------------
def editor_snapshot(
    connection: sqlite3.Connection, accounts: AccountRegistry, manual_id: UUID | str | None = None,
) -> dict:
    """
    Read source choices and one saved instruction without rebuilding the unrelated monthly review queue.

    All bank facts remain available, including linked sources from other months. The selected instruction
    carries its live revision; saving still validates source ownership and complete-event revisions normally.
    """
    by_account = {item.id: item for item in accounts.accounts.values()}
    transactions = [
        {
            "transaction_id": str(row.transaction_id), "date": row.date.isoformat(),
            "effective_date": row.effective_date.isoformat() if row.effective_date else "",
            "account": by_account[row.account_id].name, "account_id": str(row.account_id),
            "amount": format(row.amount, ".2f"), "description": row.description,
            "source_file": row.source_file, "transfer_reference": row.transfer_reference or "",
        }
        for row in load_transactions(connection)
    ]
    record = next((
        row for row in load_manual_entries(connection) if str(row["manual_id"]) == str(manual_id)
    ), None) if manual_id else None
    manuals = []
    if record is not None:
        submission, outcome = record["submission"], record["outcome"]
        entry = next((
            item for item in load_journal_entries(connection, accounts)
            if item.id == record["journal_entry_id"]
        ), None) if record["journal_entry_id"] else None
        manuals.append({
            "manual_entry_id": str(submission.id), "date": submission.date.isoformat(),
            "entry_mode": "direct" if outcome is not None and outcome.origin == "human" else "instruction",
            "is_savings": bool(record.get("is_savings")),
            "instruction": submission.instruction, "human_response": submission.human_response,
            "source_transactions": [str(identifier) for identifier in submission.source_transactions],
            "status": record["status"], "question": record["question"] or "", "error": record["error"] or "",
            "context_revision": manual_context_revision(connection, submission.id, accounts),
            "journal_entry_id": str(entry.id) if entry else "",
            "postings": [
                {**line.model_dump(mode="json"), "account": by_account[line.account_id].name,
                 "debit": format(line.debit, ".2f"), "credit": format(line.credit, ".2f")}
                for line in entry.lines
            ] if entry else [],
        })
    return {"transactions": transactions, "manuals": manuals}


def load_journal_edit(
    connection: sqlite3.Connection, event_id: UUID | str | None, accounts: AccountRegistry,
    *, securities: SecurityRegistry | None = None, budget: BudgetSettings | None = None,
    manual_id: UUID | str | None = None, source_ids: tuple = (), date: Date | str | None = None,
) -> dict:
    """
    Read an editable event or seed a new form, including the revision needed for a safe save.

    A new form may be incomplete until the person adds balancing lines. Existing events always load in full.
    Source rows supplied for a pending review expand to the complete events they already belong to.
    """
    event_id = UUID(str(event_id)) if event_id else None
    manual_id = UUID(str(manual_id)) if manual_id else None
    supplied_manual_id = manual_id
    records = load_manual_entries(connection)
    owned = next((row for row in records if row["manual_id"] == manual_id), None)
    if owned is not None and owned.get("is_savings"):
        raise ValueError("Savings-pot allocations stay outside the journal; edit their manual instruction.")
    if event_id is None and owned is not None:
        event_id = owned["journal_entry_id"]
    if event_id is not None:
        owner = next((row for row in records if row["journal_entry_id"] == event_id), None)
        if owner is not None:
            if manual_id is not None and manual_id != owner["manual_id"]:
                raise ValueError("Edit the manual instruction that owns this journal event.")
            manual_id, owned = owner["manual_id"], owner
    manual_id = manual_id or uuid4()
    sources = {UUID(str(value)) for value in source_ids}
    if owned is not None:
        sources.update(owned["submission"].source_transactions)
    transactions = {row.transaction_id: row for row in load_transactions(connection)}
    all_entries = load_journal_entries(connection, accounts)
    entries = [entry for entry in all_entries if entry.id == event_id or sources & {
        line.transaction_id for line in entry.lines if line.transaction_id is not None
    }]
    if event_id is not None and event_id not in {entry.id for entry in entries}:
        raise ValueError("This journal event no longer exists; refresh the page.")
    for entry in entries:
        sources.update(line.transaction_id for line in entry.lines if line.transaction_id is not None)
    if not sources <= transactions.keys():
        raise ValueError("This edit references an unknown bank transaction.")
    selected = [transactions[key] for key in sorted(sources, key=str)]
    if len(entries) == 1:
        event_id = entries[0].id
        owner = next((row for row in records if row["journal_entry_id"] == event_id), None)
        if owner is not None:
            if supplied_manual_id is not None and supplied_manual_id != owner["manual_id"]:
                raise ValueError("Edit the manual instruction that owns this journal event.")
            manual_id, owned = owner["manual_id"], owner
        seed = entries[0].model_dump(mode="json")
    else:
        initial_date = date or (
            owned["submission"].date if owned else min((row.date for row in selected), default=Date.today())
        )
        seed = {
            "id": str(uuid4()), "date": str(initial_date),
            "lines": [JournalLine(
                account_id=row.account_id, transaction_id=row.transaction_id,
                debit=max(row.amount, 0), credit=max(-row.amount, 0),
                description=row.description, effective_date=row.effective_date,
            ).model_dump(mode="json") for row in selected],
        }
    relevant_reviews = [
        {"id": str(row["id"]), "status": row["status"], "case": row["case"].model_dump(mode="json")}
        for row in load_review_cases(connection)
        if sources & {item.transaction_id for item in row["case"].transactions}
    ]
    answers = [tuple(row) for row in connection.execute(
        "SELECT id, transaction_id, payload, revision, status FROM review_actions "
        "WHERE kind = 'answer' ORDER BY rowid"
    ) if UUID(row[1]) in sources]
    revision = _digest({
        "manual_id": str(manual_id),
        "manual_revision": manual_context_revision(connection, manual_id, accounts),
        "context": _context(selected, entries), "reviews": relevant_reviews, "answers": answers,
        "accounts": [item.model_dump(mode="json") for item in accounts.accounts.values()],
        "securities": [item.model_dump(mode="json") for item in securities.securities.values()]
        if securities else [], "budget": budget_revision(budget),
    })
    return {
        "journal_entry_id": str(event_id) if event_id else None, "manual_entry_id": str(manual_id),
        "revision": revision, "entry": seed, "source_ids": [str(key) for key in sorted(sources, key=str)],
        "protected_trade": any(line.investment is not None for entry in entries for line in entry.lines),
    }


def apply_journal_edit(
    connection: sqlite3.Connection, payload: dict, accounts: AccountRegistry,
    *, securities: SecurityRegistry | None = None, budget: BudgetSettings | None = None,
) -> JournalEntry:
    """
    Commit a balanced human edit and its audit history together, or leave all accounting unchanged.
    """
    _require_idle(connection)
    entry = JournalEntry.model_validate(payload["entry"])
    manual_id = UUID(str(payload["manual_entry_id"]))
    source_ids = tuple(line.transaction_id for line in entry.lines if line.transaction_id is not None)
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        current = load_journal_edit(
            connection, payload.get("journal_entry_id"), accounts, securities=securities, budget=budget,
            manual_id=manual_id, source_ids=source_ids,
        )
        if current["revision"] != payload.get("revision"):
            raise ValueError("This journal form is stale; refresh it before saving your changes.")
        if set(current["source_ids"]) != {str(key) for key in source_ids}:
            raise ValueError("A correction must preserve every original bank source.")
        entries = [old for old in load_journal_entries(connection, accounts) if
                   old.id == UUID(current["entry"]["id"]) or set(source_ids) & {
                       line.transaction_id for line in old.lines if line.transaction_id is not None
                   }]
        if len(entries) == 1 and entry.id != entries[0].id:
            raise ValueError("A correction must preserve its journal event identity.")
        if len(entries) != 1 and entry.id in {old.id for old in entries}:
            raise ValueError("A merged event needs a new journal event identity.")
        if not entries and any(old.id == entry.id for old in load_journal_entries(connection, accounts)):
            raise ValueError("This event identity already belongs to another journal entry.")
        _protect_trades(entries, entry)
        _check_journal_sources(connection, entry, accounts)
        validate_budget_labels((entry,), accounts, budget, tuple(entries))
        by_id = {account.id: account for account in accounts.accounts.values()}
        opening = payload.get("opening", False)
        if not isinstance(opening, bool):
            raise ValueError("Opening-position confirmation must be true or false.")
        old_unlinked_banks = Counter(
            (line.account_id, line.debit, line.credit) for old in entries for line in old.lines
            if line.transaction_id is None and by_id[line.account_id].bank_details is not None
        )
        new_unlinked_banks = Counter(
            (line.account_id, line.debit, line.credit)
            for line in entry.lines
            if line.transaction_id is None and by_id[line.account_id].bank_details is not None
        )
        if new_unlinked_banks - old_unlinked_banks and not opening:
            raise ValueError("Bank activity needs imported evidence, or an explicit opening position.")
        opening = opening or bool(old_unlinked_banks)
        saved = _latest(connection, manual_id)
        if saved is not None and saved["status"] == "requesting":
            raise ValueError("A manual request is still in progress; finish or retry it before editing.")
        if len(entries) == 1 and entries[0] == entry and saved is not None and saved["status"] == "applied":
            return entry
        instruction = ("Opening position. " if opening else "") + "Direct journal entry: " + "; ".join(
            f"{'Dr' if line.debit else 'Cr'} {by_id[line.account_id].name} "
            f"{line.debit or line.credit:.2f}" for line in entry.lines
        )
        submission = ManualInput(
            id=manual_id, date=entry.date, instruction=instruction, source_transactions=source_ids,
            context_revision=manual_context_revision(connection, manual_id, accounts),
        )
        sources = tuple(row for row in load_transactions(connection) if row.transaction_id in source_ids)
        case = ManualCase(
            submission=submission, transactions=sources, required_sources=source_ids, entries=tuple(entries),
            revision=manual_case_revision(connection, submission, sources, entries, accounts),
            budget_revision=budget_revision(budget),
        )
        _check_claims(connection, case)
        before = _audit_snapshot(connection, case)
        outcome = ManualOutcome(
            manual_id=manual_id, status="apply", origin="human", entry=entry,
            event_kind="investment" if current["protected_trade"] else "opening" if opening else "general",
            reason="Saved explicit journal postings supplied by the person.",
        )
        _replace_entry(connection, entries, outcome, accounts)
        _retire_reviews(connection, set(source_ids), manual_id)
        after = _audit_snapshot(connection, case, event_id=entry.id)
        if saved is not None and saved["status"] not in {"applied", "withdrawn", "superseded"}:
            connection.execute(
                "UPDATE manual_entries SET status = 'superseded', updated_at = ? WHERE id = ?",
                (_now(), str(saved["input_revision"])),
            )
        connection.execute(
            """
            INSERT INTO manual_entries (
                id, manual_id, submission, status, case_payload, revision, outcome, journal_entry_id,
                before_snapshot, after_snapshot, created_at, updated_at
            ) VALUES (?, ?, ?, 'applied', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()), str(manual_id), submission.model_dump_json(), case.model_dump_json(),
                case.revision, outcome.model_dump_json(), str(entry.id),
                _json(before), _json(after), _now(), _now(),
            ),
        )
    return entry


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _protect_trades(previous: list[JournalEntry], entry: JournalEntry) -> None:
    """
    Permit wording and budget edits while preserving every economic fact of an existing security trade.
    """
    protected = [old for old in previous if any(line.investment is not None for line in old.lines)]
    if protected:
        if (
            len(previous) != 1 or entry.date != previous[0].date
            or len(entry.lines) != len(previous[0].lines)
        ):
            raise ValueError("Security trade dates and posting structure are protected in this editor.")
        for old, new in zip(previous[0].lines, entry.lines):
            if old.model_dump(exclude={"description", "budget_category"}) != new.model_dump(
                exclude={"description", "budget_category"},
            ):
                raise ValueError("Security trade accounts, amounts, units and prices are protected.")
    elif any(line.investment is not None for line in entry.lines):
        raise ValueError("Create a security trade through its investment instruction before editing it.")
