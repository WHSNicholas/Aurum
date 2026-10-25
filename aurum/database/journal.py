# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Database - Journal                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum stores each account's debit or credit as a journal row. Posted rows share an entry UUID with the
other lines of the same balanced event; unfinished bank rows retain only their processing status.

This module saves and reconstructs complete entries, checking their accounts, balance and statement links.
Each source keeps its exact description, effective date and full signed amount, and can be posted only once.
Only posted rows enter returned JournalEntry objects or balances.

Investment lines retain security IDs, signed units and optional trade prices and fees. These decimal values
are stored as text to preserve fractional quantities exactly. Saving a new investment event also checks its
security against the supplied catalogue; ordinary bank entries need no security configuration.

Account names make saved rows readable, while persistent UUIDs remain the authoritative links. Processing
commentary and request history belong to review storage, outside the journal.

Optional budget labels live in a companion table and are saved with their postings. Older databases without
that table remain readable, and unlabelled entries retain their original serialized form and review revisions.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from decimal import Decimal
import sqlite3
from uuid import UUID

# Aurum imports
from aurum.database.transactions import from_sqlite_dollars, to_sqlite_dollars
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.budget import BudgetSettings, validate_budget_labels
from aurum.finance.journal import InvestmentDetails, JournalEntry, JournalLine
from aurum.finance.securities import SecurityRegistry


# %% 1. Journal Storage --------------------------------------------------------------------------------------
def prepare_journal(connection: sqlite3.Connection, accounts: AccountRegistry) -> None:
    """
    Add pending rows for unrepresented statement lines and refresh readable account names and types.

    Names follow the current registry; UUIDs remain authoritative. Every account used by existing journal
    rows must remain configured.

    Raises:
        ValueError: A source or actual journal account is missing, unsupported, or a write is pending.
        sqlite3.Error: Preparing the journal fails. All changes are rolled back.
    """
    if connection.in_transaction:
        raise ValueError("Finish the pending database write before preparing the journal")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        _prepare_journal(connection, accounts)


def save_journal_entry(
    connection: sqlite3.Connection, entry: JournalEntry, accounts: AccountRegistry,
    *, securities: SecurityRegistry | None = None, budget: BudgetSettings | None = None,
) -> None:
    """
    Save a complete balanced event, reusing its source rows and inserting any counterpart rows atomically.

    Args:
        connection: Open Aurum connection without a pending write.
        entry: Validated event, including ordered lines, accounts and source links.
        accounts: Registry containing every actual account in the event.
        securities: Security catalogue, required when the event includes investment units.
        budget: Optional category configuration, required when assigning new reporting labels.

    Raises:
        ValueError: Accounts, source links or money are invalid, or a database write is pending.
        sqlite3.Error: An entry UUID or posted source is reused, or saving fails.

    Posting an event supersedes its unfinished source statuses. Categorisation storage saves AI results and
    their validated postings together without adding processing commentary to the journal.
    """
    entry = JournalEntry.model_validate(entry)
    _check_investment_securities(entry, securities)
    validate_budget_labels((entry,), accounts, budget)
    if connection.in_transaction:
        raise ValueError("Finish the pending database write before saving a journal entry")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        _check_journal_sources(connection, entry, accounts)
        _write_entry(connection, entry, accounts)


def load_journal_entries(
    connection: sqlite3.Connection, accounts: AccountRegistry,
    *, securities: SecurityRegistry | None = None,
) -> list[JournalEntry]:
    """
    Reconstruct only posted events, checking their line order, shared details, accounts, sources and balance.

    Unfinished rows never enter returned entries or balances. The original event UUID and line order are
    preserved, and money is reconstructed as exact two-decimal Decimal values.
    Investment quantities and prices retain their full decimal precision. Supplying a security catalogue
    also checks their identities; omitting it lets existing bank review workflows read events.

    Raises:
        ValueError: Saved events or their account/source references are invalid.
        sqlite3.Error: The journal cannot be read.
    """
    labelled = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'journal_budget'"
    ).fetchone() is not None
    label = "b.budget_category" if labelled else "NULL"
    join = "LEFT JOIN journal_budget b ON b.journal_row_id = j.id" if labelled else ""
    return _entries_from_rows(connection, connection.execute(
        f"""
        SELECT j.journal_entry_id, j.line_number, j.date,
               j.account_id, j.debit, j.credit, j.transaction_id, j.description, j.effective_date,
               j.security_id, j.units, j.unit_price, j.fees, {label}
        FROM journal j {join} WHERE j.status = 'posted'
        ORDER BY j.date, j.journal_entry_id, j.line_number
        """
    ), accounts, securities=securities)


def posted_transaction_ids(connection: sqlite3.Connection) -> set[UUID]:
    """
    Return sources whose journal rows are posted, excluding every unfinished source row.
    """
    return {
        UUID(row[0])
        for row in connection.execute(
            "SELECT transaction_id FROM journal WHERE status = 'posted' AND transaction_id IS NOT NULL"
        )
    }


def _prepare_journal(connection: sqlite3.Connection, accounts: AccountRegistry) -> None:
    """
    Add source drafts and refresh display fields within the caller's existing transaction.
    """
    by_id = {str(account.id): account for account in accounts.accounts.values()}
    actual_ids = {row[0] for row in connection.execute("SELECT DISTINCT account_id FROM journal")}
    if not actual_ids <= by_id.keys():
        raise ValueError("Every actual journal account must remain in the account registry")
    for (identifier,) in connection.execute("SELECT transaction_id FROM transactions").fetchall():
        _ensure_source_row(connection, UUID(identifier), accounts)
    for identifier, account in by_id.items():
        connection.execute(
            "UPDATE journal SET account_name = ?, account_type = ? WHERE account_id = ?",
            (account.name, account.account_type.value, identifier),
        )


def _ensure_source_row(
    connection: sqlite3.Connection, transaction_id: UUID, accounts: AccountRegistry,
) -> None:
    """
    Create a pending bank row when a known source has no journal row yet.
    """
    identifier = str(transaction_id)
    if connection.execute("SELECT 1 FROM journal WHERE transaction_id = ?", (identifier,)).fetchone():
        return
    source = connection.execute(
        "SELECT account_id, date, description, amount, effective_date "
        "FROM transactions WHERE transaction_id = ?",
        (identifier,),
    ).fetchone()
    if source is None:
        raise ValueError(f"Unknown source transaction: {transaction_id}")
    account = next(
        (account for account in accounts.accounts.values() if str(account.id) == source[0]), None
    )
    if account is None or account.account_type != AccountType.ASSET:
        raise ValueError("Source journal rows require a configured asset account")
    amount = from_sqlite_dollars(source[3])
    connection.execute(
        """
        INSERT INTO journal (
            date, description, effective_date, account_id, account_name, account_type,
            debit, credit, status, transaction_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            source[1], source[2], source[4], str(account.id), account.name, account.account_type.value,
            to_sqlite_dollars(max(amount, Decimal("0"))),
            to_sqlite_dollars(max(amount.copy_negate(), Decimal("0"))), identifier,
        ),
    )


def _write_entry(
    connection: sqlite3.Connection, entry: JournalEntry, accounts: AccountRegistry,
) -> None:
    """
    Insert ordered posting rows within the caller's transaction.
    """
    if connection.execute(
        "SELECT 1 FROM journal WHERE journal_entry_id = ?", (str(entry.id),)
    ).fetchone():
        raise sqlite3.IntegrityError("A journal entry with this UUID already exists")
    by_id = {account.id: account for account in accounts.accounts.values()}
    for number, line in enumerate(entry.lines, start=1):
        account = by_id[line.account_id]
        values = (
            str(entry.id), number, entry.date.isoformat(),
            line.effective_date.isoformat() if line.effective_date else None, line.description,
            str(line.account_id), account.name, account.account_type.value,
            to_sqlite_dollars(line.debit), to_sqlite_dollars(line.credit),
            *_investment_values(line.investment),
        )
        if line.transaction_id is None:
            cursor = connection.execute(
                """
                INSERT INTO journal (
                    journal_entry_id, line_number, date, effective_date, description,
                    account_id, account_name, account_type, debit, credit,
                    security_id, units, unit_price, fees, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'posted')
                """,
                values,
            )
            _save_budget_label(connection, cursor.lastrowid, line.budget_category)
            continue
        _ensure_source_row(connection, line.transaction_id, accounts)
        identifier = str(line.transaction_id)
        status = connection.execute(
            "SELECT status FROM journal WHERE transaction_id = ?", (identifier,)
        ).fetchone()[0]
        if status == "posted":
            raise sqlite3.IntegrityError("This source transaction is already posted")
        # The source row keeps its identity while its complete event becomes posted.
        connection.execute(
            """
            UPDATE journal SET journal_entry_id = ?, line_number = ?, date = ?, effective_date = ?,
                description = ?, account_id = ?, account_name = ?, account_type = ?,
                debit = ?, credit = ?,
                security_id = ?, units = ?, unit_price = ?, fees = ?, status = 'posted'
            WHERE transaction_id = ?
            """,
            (*values, identifier),
        )
        row_id = connection.execute(
            "SELECT id FROM journal WHERE transaction_id = ?", (identifier,),
        ).fetchone()[0]
        _save_budget_label(connection, row_id, line.budget_category)


def _entries_from_rows(
    connection: sqlite3.Connection, rows: sqlite3.Cursor, accounts: AccountRegistry,
    *, securities: SecurityRegistry | None = None,
) -> list[JournalEntry]:
    """
    Validate grouped posting rows while retaining original entry UUIDs and line order.
    """
    grouped = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row)
    entries = []
    for identifier, postings in grouped.items():
        entry_date = postings[0][2]
        if [row[1] for row in postings] != list(range(1, len(postings) + 1)):
            raise ValueError("Journal line numbers must be consecutive from one")
        if any(row[2] != entry_date for row in postings):
            raise ValueError("All rows of an entry must share its date")
        lines = tuple(
            JournalLine(
                account_id=row[3], debit=from_sqlite_dollars(row[4]), credit=from_sqlite_dollars(row[5]),
                transaction_id=row[6], description=row[7], effective_date=row[8],
                budget_category=row[13],
                investment=InvestmentDetails(
                    security_id=row[9], units=row[10], unit_price=row[11], fees=row[12],
                ) if any(value is not None for value in row[9:13]) else None,
            )
            for row in postings
        )
        entry = JournalEntry(
            id=identifier, date=entry_date, lines=lines,
        )
        _check_journal_sources(connection, entry, accounts)
        if securities is not None:
            _check_investment_securities(entry, securities)
        entries.append(entry)
    return entries


def _investment_values(investment: InvestmentDetails | None) -> tuple:
    """
    Preserve exact decimal values as SQLite text; absent details remain four NULLs.
    """
    if investment is None:
        return (None, None, None, None)
    return (
        str(investment.security_id), str(investment.units),
        str(investment.unit_price) if investment.unit_price is not None else None,
        str(investment.fees) if investment.fees is not None else None,
    )


def _check_investment_securities(entry: JournalEntry, securities: SecurityRegistry | None) -> None:
    """
    Require configured securities before accepting new investment postings.
    """
    identifiers = {line.investment.security_id for line in entry.lines if line.investment is not None}
    if not identifiers:
        return
    if securities is None:
        raise ValueError("A security catalogue is required to save investment entries")
    known = {security.id for security in securities.securities.values()}
    if not identifiers <= known:
        raise ValueError("An investment entry references a security missing from the catalogue")


def _check_journal_sources(
    connection: sqlite3.Connection, entry: JournalEntry, accounts: AccountRegistry
) -> None:
    """
    Require known accounts and check each linked statement line against its full bank posting.

    Source amount signs currently describe asset bank accounts: an inflow is a debit and an outflow is
    a credit. Other statement types need an agreed sign convention before they can supply source links.
    Journal and statement dates may differ, as a transfer can appear on different days at each bank.
    """
    # Account references
    by_id = {account.id: account for account in accounts.accounts.values()}
    for line in entry.lines:
        if line.account_id not in by_id:
            raise ValueError(f"Unknown journal account: {line.account_id}")
        account = by_id[line.account_id]
        if line.investment is not None and (
            account.account_type != AccountType.ASSET or account.bank_details is not None
        ):
            raise ValueError("Investment units require a non-bank asset account")
        if line.transaction_id is None:
            continue

        # Statement evidence
        if account.account_type != AccountType.ASSET or account.bank_details is None:
            raise ValueError("Statement links currently support asset bank accounts only")
        source = connection.execute(
            "SELECT account_id, amount, description, effective_date "
            "FROM transactions WHERE transaction_id = ?",
            (str(line.transaction_id),),
        ).fetchone()
        if source is None:
            raise ValueError(f"Unknown source transaction: {line.transaction_id}")
        if UUID(source[0]) != line.account_id:
            raise ValueError("The journal line and its source transaction must use the same account")
        if line.description != source[2]:
            raise ValueError("A source-linked journal line must retain the exact statement description")
        effective_date = line.effective_date.isoformat() if line.effective_date is not None else None
        if effective_date != source[3]:
            raise ValueError("A source-linked journal line must retain the exact source effective date")
        # Choose the nonzero side without rounding a subtraction through Decimal's current precision.
        amount = line.debit if line.debit > 0 else line.credit.copy_negate()
        if amount != from_sqlite_dollars(source[1]):
            raise ValueError("The journal line must match its source transaction's full signed amount")


def _save_budget_label(connection: sqlite3.Connection, row_id: int, category: str | None) -> None:
    """
    Replace one row's optional reporting label inside its posting transaction.
    """
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'journal_budget'"
    ).fetchone()
    if not exists:
        if category is not None:
            raise ValueError("Open the database normally before saving budget labels")
        return
    connection.execute("DELETE FROM journal_budget WHERE journal_row_id = ?", (row_id,))
    if category is not None:
        connection.execute(
            "INSERT INTO journal_budget (journal_row_id, budget_category) VALUES (?, ?)",
            (row_id, category),
        )
