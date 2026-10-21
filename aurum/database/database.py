# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 Database - Database                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
This file keeps Aurum's imported transactions in a local SQLite database between runs.

The `transactions` table holds bank statement lines, and the `imports` table records which PDFs have been
processed. Other parts of Aurum can load the saved rows as Transaction objects.

Import history identifies PDFs already processed. For overlapping statements, the code compares transaction
values and counts, keeping legitimate repeated lines and saving only the additional ones.

Each PDF's transactions and import record are saved together. If any part of the save fails, its changes
are undone so the database is not left with a partially imported file.

Money is stored as signed AUD dollar amounts in SQLite and restored as Decimal values for calculations
in Python.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
import math
from pathlib import Path
import re
import sqlite3
from uuid import UUID

# Aurum imports
from aurum.finance.transaction import Transaction


# %% 1. Database Setup ---------------------------------------------------------------------------------------
# Default paths
DEFAULT_DATABASE = Path(__file__).resolve().parents[2] / "data" / "aurum.sqlite3"

# Table definitions
SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
    source_hash TEXT PRIMARY KEY NOT NULL,
    source_file TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    parsed_count INTEGER NOT NULL CHECK (parsed_count >= 0),
    inserted_count INTEGER NOT NULL CHECK (inserted_count >= 0 AND inserted_count <= parsed_count)
) STRICT;

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY NOT NULL,
    account_id TEXT NOT NULL,
    date TEXT NOT NULL,
    effective_date TEXT,
    description TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount = ROUND(amount, 2)),
    statement_balance REAL CHECK (statement_balance = ROUND(statement_balance, 2)),
    source_file TEXT NOT NULL
) STRICT;
"""


def open_database(path: Path | str = DEFAULT_DATABASE) -> sqlite3.Connection:
    """
    Open SQLite and create missing tables.

    Args:
        path: Database file, or ":memory:" for an in-memory check.

    Returns:
        An open connection. The caller must close it, for example with contextlib.closing.

    Raises:
        OSError: The database directory cannot be created.
        ValueError: An existing transactions table uses an incompatible schema.
        sqlite3.Error: The database cannot be opened or its schema cannot be initialised.
    """
    # Database connection
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        # Schema validation
        columns = {row[1]: row[2] for row in connection.execute("PRAGMA table_info(transactions)")}
        if columns and (
            set(columns) != set(Transaction.model_fields)
            or columns.get("amount") != "REAL"
            or columns.get("statement_balance") != "REAL"
        ):
            raise ValueError("Database schema has changed; migrate it or rebuild from the original PDFs")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
    except (ValueError, sqlite3.Error):
        connection.close()
        raise
    return connection


# %% 2. Transaction Storage ----------------------------------------------------------------------------------
def is_imported(connection: sqlite3.Connection, source_hash: str) -> bool:
    """
    Return whether this exact PDF content has already been imported successfully.
    """
    return connection.execute(
        "SELECT 1 FROM imports WHERE source_hash = ?", (source_hash,)
    ).fetchone() is not None


def save_import(
    connection: sqlite3.Connection,
    source_file: Path | str,
    source_hash: str,
    transactions: list[Transaction],
) -> int:
    """
    Save one PDF's additional transactions and import history together, or roll back on failure.

    Args:
        connection: Open database connection without a pending write transaction.
        source_file: Original PDF path. Only its filename stem is stored.
        source_hash: SHA-256 hash of the same PDF bytes that were parsed.
        transactions: Complete validated transaction list, including an empty list for an empty statement.

    Returns:
        Number of additional transactions inserted. Existing records and their UUIDs are never overwritten.

    Raises:
        ValueError: Source data is inconsistent, a write is pending, or money cannot round-trip safely.
        sqlite3.Error: The save fails. Neither partial transactions nor completed import history is retained.
    """
    # Input validation
    source_name = Path(source_file).stem
    if not source_name.strip():
        raise ValueError("Expected a nonblank source filename stem")
    if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise ValueError("Expected a SHA-256 PDF content hash")
    if any(item.source_file != source_name for item in transactions):
        raise ValueError("All transactions must belong to the supplied PDF filename")
    if len({item.account_id for item in transactions}) > 1:
        raise ValueError("All transactions in a PDF must belong to the same source account")
    if connection.in_transaction:
        raise ValueError("Finish the pending database write before saving an import")

    # Atomic import
    # The lock covers reading existing counts and inserting rows, so simultaneous imports cannot both add
    # the same additional occurrences. Both tables commit together.
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if is_imported(connection, source_hash):
            return 0

        # Existing transactions
        existing_counts = Counter()
        if transactions:
            existing_counts.update(
                _matching_key(saved)
                for saved in _read_transactions(connection, transactions[0].account_id)
            )
        # Import history
        connection.execute(
            """
            INSERT INTO imports (source_hash, source_file, imported_at, parsed_count, inserted_count)
            VALUES (?, ?, ?, ?, 0)
            """,
            (source_hash, source_name, datetime.now(UTC).isoformat(), len(transactions)),
        )

        # Additional transactions
        incoming_counts = Counter()
        inserted = 0
        for item in transactions:
            key = _matching_key(item)
            incoming_counts[key] += 1
            # Match the first N identical rows already saved. A larger count in this PDF adds only the
            # surplus, preserving legitimate repeated transactions without saved matching metadata.
            if incoming_counts[key] <= existing_counts[key]:
                continue
            connection.execute(
                """
                INSERT INTO transactions (
                    id, account_id, date, effective_date, description, amount, statement_balance, source_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.id),
                    str(item.account_id),
                    item.date.isoformat(),
                    item.effective_date.isoformat() if item.effective_date is not None else None,
                    item.description,
                    _dollars(item.amount),
                    _dollars(item.statement_balance),
                    item.source_file,
                ),
            )
            inserted += 1
        # Import summary
        connection.execute(
            "UPDATE imports SET inserted_count = ? WHERE source_hash = ?", (inserted, source_hash)
        )
    return inserted


def load_transactions(connection: sqlite3.Connection) -> list[Transaction]:
    """
    Reconstruct saved statement lines as validated Transaction objects in a stable order.

    Args:
        connection: Open database connection using the current schema.

    Returns:
        Transactions ordered by date, source filename stem, and UUID. Dollars have two decimal places.

    Raises:
        ValueError: Stored money is non-finite or contains fractional cents.
        ValidationError: Stored data fails Transaction validation.
        sqlite3.Error: The records cannot be read.
    """
    return _read_transactions(connection)


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _dollars(amount: Decimal | None) -> float | None:
    """
    Convert money to numeric SQLite dollars, rejecting fractional cents and lost cent values.

    REAL uses floating point. Compare its shortest decimal representation with the original amount before
    saving; this prevents accepting a value whose cents would change when reconstructed in Python.
    """
    if amount is None:
        return None
    if not amount.is_finite():
        raise ValueError("Money must be finite")
    numerator, denominator = amount.as_integer_ratio()
    if numerator * 100 % denominator:
        raise ValueError("Money must not contain fractional cents")
    value = float(amount)
    if not math.isfinite(value) or Decimal(str(value)) != amount:
        raise ValueError("Money cannot round-trip through SQLite REAL without changing its cent value")
    return value


def _decimal_amount(value: float | None) -> Decimal | None:
    """
    Restore numeric dollars to a two-decimal Decimal, retaining None for a missing statement balance.
    """
    if value is None:
        return None
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("Stored money must be finite")
    formatted = Decimal(format(amount, ".2f"))
    if formatted != amount:
        raise ValueError("Stored money must not contain fractional cents")
    return formatted


def _matching_key(transaction: Transaction) -> tuple:
    """
    Group matching statement facts independently of transaction UUIDs and filenames.

    Dollar values are Decimals in both imported and loaded records. Normalise description whitespace for
    comparison only; keep the original wording in the transaction itself.
    """
    return (
        transaction.account_id,
        transaction.date,
        transaction.effective_date,
        transaction.amount,
        " ".join(transaction.description.split()),
        transaction.statement_balance,
    )


def _read_transactions(
    connection: sqlite3.Connection, account_id: UUID | None = None
) -> list[Transaction]:
    """
    Read validated transactions, optionally selecting one source account for repeat-import comparisons.
    """
    # Saved rows
    query = """
        SELECT id, account_id, date, effective_date, description, amount, statement_balance, source_file
        FROM transactions
    """
    parameters = ()
    if account_id is not None:
        query += " WHERE account_id = ?"
        parameters = (str(account_id),)
    query += " ORDER BY date, source_file, id"
    cursor = connection.execute(query, parameters)
    # Transaction objects
    columns = [column[0] for column in cursor.description]
    transactions = []
    for row in cursor:
        fields = dict(zip(columns, row))
        fields["amount"] = _decimal_amount(fields["amount"])
        fields["statement_balance"] = _decimal_amount(fields["statement_balance"])
        transactions.append(Transaction(**fields))
    return transactions
