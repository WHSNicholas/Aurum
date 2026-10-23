# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                              Database - Categorisation                                               #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum keeps categorisation questions and request history outside its accounting journal.

This module reserves work before inference and saves each result with its journal status or postings in one
transaction. Private categorisation records live in review_actions. The journal's status remains
authoritative; a historical question never sends a posted transaction back through categorisation.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import json
import sqlite3
from uuid import UUID

# Aurum imports
from aurum.ai.categorisation import Decision
from aurum.database.journal import _check_journal_sources, _ensure_source_row, _write_entry
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.journal import JournalEntry


# %% 1. Categorisation Storage -------------------------------------------------------------------------------
def load_decisions(connection: sqlite3.Connection) -> dict[UUID, Decision]:
    """
    Read questions and unfinished requests only for sources whose journal status is still unfinished.
    """
    decisions = {}
    for identifier, status, payload, model, version, updated in connection.execute(
        """
        SELECT journal.transaction_id, journal.status, action.payload,
               action.model, action.skill_version, action.updated_at
        FROM journal LEFT JOIN review_actions AS action
          ON action.kind = 'categorisation' AND action.transaction_id = journal.transaction_id
        WHERE journal.status IN ('requesting', 'needs_review', 'failed')
        ORDER BY journal.transaction_id
        """
    ):
        if payload is None:
            raise ValueError("An unfinished source is missing its categorisation history")
        fields = json.loads(payload)
        decision = Decision(
            transaction_id=identifier, status=status, reason=fields["reason"], question=fields["question"],
            origin=fields["origin"], model=model, prompt_version=version, updated_at=updated,
        )
        decisions[decision.transaction_id] = decision
    return decisions


def save_decisions(
    connection: sqlite3.Connection, decisions: list[Decision], accounts: AccountRegistry,
) -> None:
    """
    Save unfinished requests, failures or review questions together without changing posted accounting.

    Classified results must use save_categorisation_batch so their actual counterpart lines are saved in
    the same commit. A source row is created when needed; posted rows cannot be changed through this helper.

    Raises:
        ValueError: A decision is classified, sources repeat, references are invalid, or a write is pending.
        sqlite3.Error: Saving fails. The complete batch is rolled back.
    """
    if connection.in_transaction:
        raise ValueError("Finish the pending database write before saving categorisation decisions")
    decisions = [Decision.model_validate(decision.model_dump()) for decision in decisions]
    if len({decision.transaction_id for decision in decisions}) != len(decisions):
        raise ValueError("Each transaction may appear only once in a decision batch")
    if any(decision.status == "classified" for decision in decisions):
        raise ValueError("Save classified decisions and their postings with save_categorisation_batch")
    if not decisions:
        return
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        _write_decisions(connection, decisions, accounts)

def save_categorisation_batch(
    connection: sqlite3.Connection, decisions: list[Decision],
    entries_by_transaction: dict[UUID, JournalEntry], accounts: AccountRegistry,
) -> None:
    """
    Save a completed AI batch's accounting entries and review questions in one atomic commit.

    Args:
        connection: Open connection without a pending write.
        decisions: Exactly one classified result or review question for each returned transaction.
        entries_by_transaction: One balanced ordinary entry for each classified source, and none for reviews.
        accounts: Registry containing the source and selected counterpart accounts.

    Raises:
        ValueError: Coverage, reservations, account choices or source details are inconsistent.
        sqlite3.Error: Saving fails. Earlier requesting rows remain available for an explicit retry.

    If posting fails, no part of the response is saved; the earlier requesting state remains and the
    response needs an explicit retry.
    """
    if connection.in_transaction:
        raise ValueError("Finish the pending database write before saving a categorisation batch")
    decisions = [Decision.model_validate(decision.model_dump()) for decision in decisions]
    identifiers = [decision.transaction_id for decision in decisions]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Each transaction may appear only once in a categorisation batch")
    if any(decision.status not in {"classified", "needs_review"} for decision in decisions):
        raise ValueError("A completed categorisation batch must contain classifications or review questions")
    entries = {
        UUID(str(identifier)): JournalEntry.model_validate(entry)
        for identifier, entry in entries_by_transaction.items()
    }
    classified = {decision.transaction_id for decision in decisions if decision.status == "classified"}
    if entries.keys() != classified:
        raise ValueError("Entries must exactly match classified decisions")
    if len({entry.id for entry in entries.values()}) != len(entries):
        raise ValueError("Each classified event must have a distinct journal entry UUID")
    if not decisions:
        return

    with connection:
        connection.execute("BEGIN IMMEDIATE")
        for decision in decisions:
            reserved = connection.execute(
                "SELECT status FROM journal WHERE transaction_id = ?", (str(decision.transaction_id),)
            ).fetchone()
            if reserved is None or reserved[0] != "requesting":
                raise ValueError("Every categorisation result must have a reserved requesting source")
            if decision.status == "classified":
                _check_classification_entry(
                    connection, decision, entries[decision.transaction_id], accounts,
                )
        for decision in decisions:
            if decision.status == "classified":
                _write_entry(connection, entries[decision.transaction_id], accounts)
                _write_record(connection, decision)
            else:
                _write_decisions(connection, [decision], accounts)

def reserve_decisions(
    connection: sqlite3.Connection,
    transaction_ids: list[UUID],
    model: str,
    prompt_version: str,
    *,
    accounts: AccountRegistry,
    retry_errors: bool = False,
) -> list[UUID]:
    """
    Claim pending transactions before sending an AI batch, without repeating completed work.

    Posted sources and saved decisions are skipped. Explicit retry_errors also permits failed or interrupted
    requesting rows. The write lock covers both checking and reserving; do not retry an active request.

    Returns:
        Claimed transaction UUIDs in their supplied order.

    Raises:
        ValueError: Inputs are invalid, a source is repeated, or a database write is pending.
        sqlite3.Error: Reserving the batch fails. All reservations are rolled back.
    """
    if connection.in_transaction:
        raise ValueError("Finish the pending database write before reserving categorisation decisions")
    transaction_ids = [UUID(str(identifier)) for identifier in transaction_ids]
    if len(set(transaction_ids)) != len(transaction_ids):
        raise ValueError("Each transaction may appear only once in a request batch")
    requesting = [
        Decision(
            transaction_id=identifier, status="requesting", reason="Awaiting a completed AI response",
            origin="ai", model=model, prompt_version=prompt_version,
        )
        for identifier in transaction_ids
    ]
    if not requesting:
        return []

    with connection:
        connection.execute("BEGIN IMMEDIATE")
        claimed = []
        for decision in requesting:
            _ensure_source_row(connection, decision.transaction_id, accounts)
            status = connection.execute(
                "SELECT status FROM journal WHERE transaction_id = ?", (str(decision.transaction_id),)
            ).fetchone()[0]
            if status == "pending" or (retry_errors and status in {"failed", "requesting"}):
                claimed.append(decision)
        _write_decisions(connection, claimed, accounts)
    return [decision.transaction_id for decision in claimed]

# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _write_decisions(
    connection: sqlite3.Connection, decisions: list[Decision], accounts: AccountRegistry,
) -> None:
    """
    Save unfinished status and its review-history record inside the caller's transaction.
    """
    for decision in decisions:
        if decision.status == "classified":
            raise ValueError("A classification must be saved together with its actual journal postings")
        _ensure_source_row(connection, decision.transaction_id, accounts)
        cursor = connection.execute(
            "UPDATE journal SET status = ? WHERE transaction_id = ? AND status != 'posted'",
            (decision.status, str(decision.transaction_id)),
        )
        if cursor.rowcount != 1:
            raise ValueError("An unfinished decision cannot replace a posted source")
        _write_record(connection, decision)


def _write_record(connection: sqlite3.Connection, decision: Decision) -> None:
    """
    Keep request commentary in the source's private history without duplicating its selected account.
    """
    identifier = str(decision.transaction_id)
    timestamp = decision.updated_at.isoformat()
    payload = json.dumps(
        {"reason": decision.reason, "question": decision.question, "origin": decision.origin},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    cursor = connection.execute(
        """
        INSERT INTO review_actions (
            id, kind, transaction_id, status, payload, model, skill_version, created_at, updated_at
        ) VALUES (?, 'categorisation', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET status = excluded.status, payload = excluded.payload,
            model = excluded.model, skill_version = excluded.skill_version, updated_at = excluded.updated_at
        WHERE review_actions.kind = 'categorisation'
          AND review_actions.transaction_id = excluded.transaction_id
        """,
        (
            "categorisation:" + identifier, identifier,
            "applied" if decision.status == "classified" else decision.status, payload, decision.model,
            decision.prompt_version, timestamp, timestamp,
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError("The categorisation history identity conflicts with another review record")


def _check_classification_entry(
    connection: sqlite3.Connection, decision: Decision, entry: JournalEntry, accounts: AccountRegistry,
) -> None:
    """
    Require an ordinary classified event to use its exact source and selected income or expense account.
    """
    _check_journal_sources(connection, entry, accounts)
    sources = [line for line in entry.lines if line.transaction_id is not None]
    if len(entry.lines) != 2 or len(sources) != 1 or sources[0].transaction_id != decision.transaction_id:
        raise ValueError("A classified entry needs its one source posting and one counterpart")
    bank = sources[0]
    counterpart = next(line for line in entry.lines if line.transaction_id is None)
    selected = next(
        (account for account in accounts.accounts.values() if account.id == decision.account_id), None,
    )
    expected = AccountType.INCOME if bank.debit > 0 else AccountType.EXPENSE
    if selected is None or selected.account_type != expected or counterpart.account_id != selected.id:
        raise ValueError("The classified entry must post to the selected income or expense account")
    source_date = connection.execute(
        "SELECT date FROM transactions WHERE transaction_id = ?", (str(decision.transaction_id),)
    ).fetchone()[0]
    if entry.date.isoformat() != source_date or counterpart.description != bank.description:
        raise ValueError("An ordinary classification must retain its source date and original description")
    if counterpart.effective_date != bank.effective_date:
        raise ValueError("An ordinary counterpart must retain the source effective date")
