# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                               Database - Transactions                                                #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum keeps each imported bank statement line as a validated Transaction in SQLite.

This module saves a PDF's transactions and import history together. A saved content hash skips unchanged
PDFs, while matching statement facts and their counts prevents overlapping statements from duplicating
transactions. Legitimate repeated lines keep separate UUIDs and retain their original descriptions.

Loading reconstructs exact two-decimal Decimal amounts for Python calculations. The shared money conversion
functions also let journal storage use the same checks when reading and writing numeric SQLite dollars.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections import Counter, deque
from datetime import UTC, date, datetime
from decimal import Decimal
import math
from pathlib import Path
import re
import sqlite3
from uuid import UUID

# Aurum imports
from aurum.finance.transaction import Transaction


# %% 1. Transaction Storage ----------------------------------------------------------------------------------
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
    *, cumulative_period: tuple[date, date] | None = None,
) -> int:
    """
    Save a PDF's additional transactions atomically, retaining existing source names and transaction UUIDs.

    Validated cumulative_period bounds permit absent optional details to match successive monthly snapshots.
    Omit them to retain exact source-fact matching for other imports.
    """
    inserted, _, _ = _save_import(
        connection, source_file, source_hash, transactions, cumulative_period=cumulative_period,
    )
    return inserted


def save_managed_import(
    connection: sqlite3.Connection,
    source_file: Path | str,
    source_hash: str,
    transactions: list[Transaction],
    *, cumulative_period: tuple[date, date] | None = None,
) -> tuple[int, int, int]:
    """
    Save a validated managed PDF and reconcile supported legacy display filenames in the same transaction.

    The caller supplies the canonical filename derived from the statement's matched account and end date.
    Supply validated cumulative_period bounds for successive monthly statement or transaction-list snapshots.
    Returns inserted transactions, changed filename fields and ambiguous older rows left unchanged.
    Economic source facts, UUIDs, posted accounting and historical review payloads are never rewritten.
    """
    return _save_import(
        connection, source_file, source_hash, transactions, canonical_source=True,
        cumulative_period=cumulative_period,
    )


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


# %% 2. Money Conversion -------------------------------------------------------------------------------------
def to_sqlite_dollars(amount: Decimal | None) -> float | None:
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


def from_sqlite_dollars(value: float | None) -> Decimal | None:
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


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _save_import(
    connection: sqlite3.Connection,
    source_file: Path | str,
    source_hash: str,
    transactions: list[Transaction],
    *, canonical_source: bool = False,
    cumulative_period: tuple[date, date] | None = None,
) -> tuple[int, int, int]:
    """
    Save one PDF's additional transactions and import history together, or roll back on failure.

    Args:
        connection: Open database connection without a pending write transaction.
        source_file: Original PDF path. Only its filename stem is stored.
        source_hash: SHA-256 hash of the same PDF bytes that were parsed.
        transactions: Complete validated transaction list, including an empty list for an empty statement.
        cumulative_period: Validated document bounds enabling compatible optional-detail matching for
            successive monthly snapshots. None retains exact source-fact matching.

    Returns:
        Inserted transactions, changed filename fields and ambiguous legacy rows retained.

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
    if cumulative_period is not None:
        start, end = cumulative_period
        if end < start or any(not start <= item.date <= end for item in transactions):
            raise ValueError("Cumulative PDF transactions must fall within its validated period")
    if connection.in_transaction:
        raise ValueError("Finish the pending database write before saving an import")

    # Atomic import
    # The lock covers reading existing counts and inserting rows, so simultaneous imports cannot both add
    # the same additional occurrences. Both tables commit together.
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        known = is_imported(connection, source_hash)
        changed, ambiguous = (0, 0)
        if canonical_source:
            changed, ambiguous = _reconcile_source_files(
                connection, source_name, source_hash, transactions,
            )
        if known:
            return 0, changed, ambiguous

        # Existing transactions
        saved = _read_transactions(connection, transactions[0].account_id) if transactions else []
        additional = _additional_rows(transactions, saved, cumulative_period=cumulative_period)
        # Import history
        connection.execute(
            """
            INSERT INTO imports (source_hash, source_file, imported_at, parsed_count, inserted_count)
            VALUES (?, ?, ?, ?, 0)
            """,
            (source_hash, source_name, datetime.now(UTC).isoformat(), len(transactions)),
        )

        # Additional transactions
        inserted = 0
        for item in additional:
            connection.execute(
                """
                INSERT INTO transactions (
                    transaction_id, account_id, date, effective_date, description, amount, statement_balance,
                    source_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.transaction_id),
                    str(item.account_id),
                    item.date.isoformat(),
                    item.effective_date.isoformat() if item.effective_date is not None else None,
                    item.description,
                    to_sqlite_dollars(item.amount),
                    to_sqlite_dollars(item.statement_balance),
                    item.source_file,
                ),
            )
            inserted += 1
        # Import summary
        connection.execute(
            "UPDATE imports SET inserted_count = ? WHERE source_hash = ?", (inserted, source_hash)
        )
    return inserted, changed, ambiguous


def _additional_rows(
    incoming: list[Transaction], saved: list[Transaction],
    *, cumulative_period: tuple[date, date] | None,
) -> list[Transaction]:
    """
    Find surplus occurrences without changing previously saved identities or source facts.

    Ordinary overlapping statements retain exact count matching. Cumulative monthly documents additionally
    permit an omitted effective date or running balance, but never two contradictory known values.
    """
    if cumulative_period is None:
        counts = Counter(_matching_key(item) for item in saved)
        additional = []
        for item in incoming:
            key = _matching_key(item)
            if counts[key]:
                counts[key] -= 1
            else:
                additional.append(item)
        return additional

    start, end = cumulative_period
    groups = {}
    for item in saved:
        if start <= item.date <= end:
            groups.setdefault(_core_key(item), []).append(item)
    incoming_groups = {}
    for index, item in enumerate(incoming):
        incoming_groups.setdefault(_core_key(item), []).append((index, item))
    additional_indices = set()
    for key, rows in incoming_groups.items():
        exact = {}
        for item in groups.get(key, []):
            exact.setdefault(_matching_key(item), []).append(item)
        remaining = []
        for index, item in rows:
            candidates = exact.get(_matching_key(item), [])
            if candidates:
                candidates.pop()
            else:
                remaining.append((index, item))
        previous = [item for candidates in exact.values() for item in candidates]
        if not remaining:
            continue
        current = [item for _, item in remaining]
        matches = _compatible_matches(current, previous)
        if len(matches) < min(len(current), len(previous)):
            raise ValueError(
                f"Overlapping transactions on {current[0].date} have conflicting effective dates or "
                "running balances. Inspect the PDFs before importing; existing transactions were kept."
            )
        if len(matches) == len(current):
            continue
        # Any surplus must have the same source facts under every valid maximum matching. In particular,
        # one balance-free saved row cannot choose arbitrarily between two different new running balances.
        kinds = {_matching_key(item) for item in current}
        if previous and len(kinds) > 1:
            for kind in kinds:
                same = [item for item in current if _matching_key(item) == kind]
                other = [item for item in current if _matching_key(item) != kind]
                maximum = len(_compatible_matches(same, previous))
                minimum = len(matches) - len(_compatible_matches(other, previous))
                if minimum != maximum:
                    raise ValueError(
                        f"Repeated transactions on {current[0].date} cannot be distinguished across PDFs. "
                        "Import was not saved; keep the existing transactions and inspect the source rows."
                    )
        additional_indices.update(
            index for position, (index, _) in enumerate(remaining) if position not in matches
        )
    return [item for index, item in enumerate(incoming) if index in additional_indices]


def _compatible_matches(incoming: list[Transaction], saved: list[Transaction]) -> dict[int, int]:
    """
    Find a maximum one-to-one match using only compatible optional source details.

    Augmenting paths retain earlier matches while finding room for later rows; an iterative search avoids
    recursion limits for long repeated-transaction groups. Returned indices are temporary, never persisted.
    """
    candidates = {
        index: [number for number, previous in enumerate(saved) if all(
            left is None or right is None or left == right
            for left, right in (
                (item.effective_date, previous.effective_date),
                (item.statement_balance, previous.statement_balance),
            )
        )]
        for index, item in enumerate(incoming)
    }
    matches, owners = {}, {}
    for root in candidates:
        queue = deque([root])
        seen = {root}
        parents = {}
        endpoint = None
        while queue and endpoint is None:
            index = queue.popleft()
            for number in candidates[index]:
                if number in parents:
                    continue
                parents[number] = index
                if number not in owners:
                    endpoint = number
                    break
                owner = owners[number]
                if owner not in seen:
                    queue.append(owner)
                    seen.add(owner)
        while endpoint is not None:
            index = parents[endpoint]
            previous = matches.get(index)
            matches[index] = endpoint
            owners[endpoint] = index
            endpoint = previous
    return matches


def _core_key(transaction: Transaction) -> tuple:
    """
    Match required bank facts before considering fields a transaction-list PDF may omit.
    """
    return (
        transaction.account_id, transaction.date, transaction.amount,
        " ".join(transaction.description.split()),
    )


def _reconcile_source_files(
    connection: sqlite3.Connection, source_name: str, source_hash: str, transactions: list[Transaction],
) -> tuple[int, int]:
    """
    Rename only supported filename metadata while holding the import's write transaction.

    Exact source facts and occurrence counts justify each existing row. Other canonical source names stay
    intact. When repeated rows exceed the selected PDF's count, only an unshared name recorded for this exact
    imported hash identifies its own occurrences; otherwise keep the ambiguous labels rather than guess.
    """
    imported = connection.execute(
        "SELECT source_file FROM imports WHERE source_hash = ?", (source_hash,),
    ).fetchone()
    previous = imported[0] if imported else None
    unique_previous = bool(previous) and connection.execute(
        "SELECT COUNT(*) FROM imports WHERE source_file = ?", (previous,),
    ).fetchone()[0] == 1
    expected = Counter(_source_facts(item) for item in transactions)
    saved = {}
    if transactions:
        for item in _read_transactions(connection, transactions[0].account_id):
            saved.setdefault(_source_facts(item), []).append(item)
    changed, ambiguous = 0, 0
    for facts, count in expected.items():
        matching = saved.get(facts, [])
        candidates = [
            item for item in matching if item.source_file != source_name and (
                unique_previous and item.source_file == previous
                or not re.fullmatch(r"[0-9]{4}-(?:0[1-9]|1[0-2])_.+", item.source_file)
            )
        ]
        if len(matching) > count:
            identified = [
                item for item in candidates if unique_previous and item.source_file == previous
            ]
            if not identified or len(identified) > count:
                ambiguous += len(candidates)
                continue
            ambiguous += len(candidates) - len(identified)
            candidates = identified
        for item in candidates:
            changed += connection.execute(
                "UPDATE transactions SET source_file = ? WHERE transaction_id = ? AND source_file = ?",
                (source_name, str(item.transaction_id), item.source_file),
            ).rowcount
    if previous is not None and previous != source_name:
        changed += connection.execute(
            "UPDATE imports SET source_file = ? WHERE source_hash = ?", (source_name, source_hash),
        ).rowcount
    return changed, ambiguous


def _source_facts(transaction: Transaction) -> tuple:
    """
    Match immutable statement facts exactly when changing a stored display filename.
    """
    return (
        transaction.account_id, transaction.date, transaction.effective_date, transaction.amount,
        transaction.description, transaction.statement_balance,
    )



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
        SELECT transaction_id, account_id, date, effective_date, description, amount, statement_balance,
               source_file
        FROM transactions
    """
    parameters = ()
    if account_id is not None:
        query += " WHERE account_id = ?"
        parameters = (str(account_id),)
    query += " ORDER BY date, source_file, transaction_id"
    cursor = connection.execute(query, parameters)
    # Transaction objects
    columns = [column[0] for column in cursor.description]
    transactions = []
    for row in cursor:
        fields = dict(zip(columns, row))
        fields["amount"] = from_sqlite_dollars(fields["amount"])
        fields["statement_balance"] = from_sqlite_dollars(fields["statement_balance"])
        transactions.append(Transaction(**fields))
    return transactions
