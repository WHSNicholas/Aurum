# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 Database - Database                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum uses one local SQLite database for imported statements, journal postings and human review history.

This module defines the current tables and opens connections with foreign-key checks enabled. Missing tables
are created together. Existing tables must have the current columns; development databases with an older
layout need to be recreated manually. Aurum does not migrate records or make automatic schema backups.

Investment quantities and prices use exact decimal text. Manual instructions, review history and reusable
guidance stay separate from their accounting postings. Storage modules share this schema and commit complete
changes atomically, so a failed operation cannot leave partially saved accounting.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from contextlib import closing
from pathlib import Path
import sqlite3

# Aurum imports
from aurum.database.prices import PRICE_SCHEMA


# %% 1. Database Setup ---------------------------------------------------------------------------------------
# Default paths
DEFAULT_DATABASE = Path(__file__).resolve().parents[2] / "data" / "aurum.sqlite3"

# Investment posting constraints
INVESTMENT_CHECK = """
    (security_id IS NULL AND units IS NULL AND unit_price IS NULL AND fees IS NULL)
    OR (security_id IS NOT NULL AND length(trim(security_id)) > 0
        AND units IS NOT NULL AND length(trim(units)) > 0
        AND status = 'posted' AND account_type = 'asset' AND transaction_id IS NULL)
"""

# Table definitions
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS imports (
    source_hash TEXT PRIMARY KEY NOT NULL,
    source_file TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    parsed_count INTEGER NOT NULL CHECK (parsed_count >= 0),
    inserted_count INTEGER NOT NULL CHECK (inserted_count >= 0 AND inserted_count <= parsed_count)
) STRICT;

CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY NOT NULL,
    account_id TEXT NOT NULL,
    date TEXT NOT NULL,
    effective_date TEXT,
    description TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount = ROUND(amount, 2)),
    statement_balance REAL CHECK (statement_balance = ROUND(statement_balance, 2)),
    source_file TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS journal (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    effective_date TEXT,
    description TEXT NOT NULL CHECK (length(trim(description)) > 0),
    account_name TEXT NOT NULL CHECK (length(trim(account_name)) > 0),
    account_type TEXT NOT NULL CHECK (account_type IN ('asset', 'liability', 'equity', 'income', 'expense')),
    debit REAL NOT NULL CHECK (debit >= 0 AND debit = ROUND(debit, 2)),
    credit REAL NOT NULL CHECK (credit >= 0 AND credit = ROUND(credit, 2)),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'requesting', 'needs_review', 'failed', 'posted')
    ),
    journal_entry_id TEXT,
    line_number INTEGER,
    account_id TEXT NOT NULL,
    transaction_id TEXT UNIQUE REFERENCES transactions(transaction_id),
    security_id TEXT,
    units TEXT,
    unit_price TEXT,
    fees TEXT CHECK ({INVESTMENT_CHECK}),
    UNIQUE (journal_entry_id, line_number),
    CHECK (debit = 0 OR credit = 0),
    CHECK (
        (status = 'posted' AND journal_entry_id IS NOT NULL AND line_number IS NOT NULL
            AND line_number >= 1 AND (debit > 0 OR credit > 0))
        OR (status != 'posted' AND journal_entry_id IS NULL AND line_number IS NULL
            AND transaction_id IS NOT NULL)
    )
) STRICT;
"""

# Guidance assessment constraints
GUIDANCE_CHECK = """
    (guidance_status IS NULL AND guidance_result IS NULL AND guidance_disposition IS NULL
        AND guidance_model IS NULL AND guidance_skill_version IS NULL AND guidance_error IS NULL
        AND guidance_created_at IS NULL AND guidance_updated_at IS NULL)
    OR (kind = 'case' AND guidance_status IS NOT NULL
        AND guidance_status IN ('requesting', 'completed', 'failed')
        AND guidance_model IS NOT NULL AND length(trim(guidance_model)) > 0
        AND guidance_skill_version IS NOT NULL AND length(trim(guidance_skill_version)) > 0
        AND guidance_created_at IS NOT NULL AND guidance_updated_at IS NOT NULL
        AND ((guidance_result IS NULL AND guidance_disposition IS NULL)
            OR (guidance_result IS NOT NULL AND guidance_disposition IS NOT NULL))
        AND (
            (guidance_status = 'requesting' AND guidance_result IS NULL AND guidance_error IS NULL)
            OR (guidance_status = 'completed' AND guidance_result IS NOT NULL AND guidance_error IS NULL)
            OR (guidance_status = 'failed' AND guidance_error IS NOT NULL
                AND length(trim(guidance_error)) > 0)
        ))
"""

# Private review history and reusable instructions
REVIEW_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS review_actions (
    id TEXT PRIMARY KEY NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('export', 'answer', 'case', 'categorisation')),
    transaction_id TEXT REFERENCES transactions(transaction_id),
    revision TEXT,
    request_key TEXT UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('exported', 'submitted', 'requesting', 'applied', 'needs_review', 'failed', 'superseded')
    ),
    payload TEXT NOT NULL,
    outcome TEXT,
    before_snapshot TEXT,
    after_snapshot TEXT,
    model TEXT,
    skill_version TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    guidance_status TEXT,
    guidance_result TEXT,
    guidance_disposition TEXT,
    guidance_model TEXT,
    guidance_skill_version TEXT,
    guidance_error TEXT,
    guidance_created_at TEXT,
    guidance_updated_at TEXT CHECK ({GUIDANCE_CHECK}),
    CHECK (
        (kind = 'export' AND transaction_id IS NOT NULL AND status = 'exported')
        OR (kind = 'answer' AND transaction_id IS NOT NULL AND revision IS NOT NULL AND status = 'submitted')
        OR (kind = 'categorisation' AND transaction_id IS NOT NULL AND revision IS NULL
            AND request_key IS NULL
            AND status IN ('requesting', 'applied', 'needs_review', 'failed') AND skill_version IS NOT NULL)
        OR (kind = 'case' AND transaction_id IS NULL AND revision IS NOT NULL AND request_key IS NOT NULL
            AND status IN ('requesting', 'applied', 'needs_review', 'failed', 'superseded')
            AND model IS NOT NULL AND skill_version IS NOT NULL AND before_snapshot IS NOT NULL)
    )
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS review_categorisation_source
    ON review_actions(transaction_id) WHERE kind = 'categorisation';

CREATE TABLE IF NOT EXISTS categorisation_guidance (
    id TEXT PRIMARY KEY NOT NULL,
    scope TEXT NOT NULL CHECK (length(trim(scope)) > 0),
    instruction TEXT NOT NULL CHECK (length(trim(instruction)) > 0),
    account_ids TEXT NOT NULL,
    supersedes TEXT NOT NULL,
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    review_action_id TEXT NOT NULL REFERENCES review_actions(id),
    superseded_by TEXT REFERENCES categorisation_guidance(id),
    retired_by_action_id TEXT REFERENCES review_actions(id),
    created_at TEXT NOT NULL,
    CHECK (
        (active = 1 AND superseded_by IS NULL AND retired_by_action_id IS NULL)
        OR (active = 0 AND retired_by_action_id IS NOT NULL)
    )
) STRICT;
"""

# Manual instructions and their complete revision history
MANUAL_COLUMNS = (
    "id", "manual_id", "submission", "status", "case_payload", "revision", "question", "outcome",
    "journal_entry_id", "before_snapshot", "after_snapshot", "model", "skill_version", "error",
    "created_at", "updated_at", "investment_proposal",
)
MANUAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_entries (
    id TEXT PRIMARY KEY NOT NULL,
    manual_id TEXT NOT NULL,
    submission TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'pending', 'requesting', 'applied', 'needs_review', 'unsupported', 'failed',
            'superseded', 'withdrawn'
        )
    ),
    case_payload TEXT,
    revision TEXT,
    question TEXT,
    outcome TEXT,
    journal_entry_id TEXT,
    before_snapshot TEXT,
    after_snapshot TEXT,
    model TEXT,
    skill_version TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    investment_proposal TEXT,
    CHECK (status != 'requesting' OR (
        case_payload IS NOT NULL AND revision IS NOT NULL AND before_snapshot IS NOT NULL
        AND model IS NOT NULL AND skill_version IS NOT NULL
    )),
    CHECK (status != 'applied' OR (outcome IS NOT NULL AND journal_entry_id IS NOT NULL)),
    CHECK (status != 'failed' OR (error IS NOT NULL AND length(trim(error)) > 0))
) STRICT;
CREATE INDEX IF NOT EXISTS manual_entries_manual_id ON manual_entries(manual_id);
"""


def open_database(path: Path | str = DEFAULT_DATABASE) -> sqlite3.Connection:
    """
    Open a current SQLite database or create its missing tables.

    Args:
        path: Database file, or ":memory:" for a synthetic check.

    Returns:
        An open connection, which the caller must close.

    Raises:
        ValueError: Existing table columns are incompatible or evidence references are broken.
        OSError: The database directory cannot be created.
        sqlite3.Error: Opening or initialising the database fails.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        _check_schema(connection)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            _create_tables(connection)
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("The database contains broken evidence references")
    except Exception:
        connection.close()
        raise
    return connection


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _create_tables(connection: sqlite3.Connection) -> None:
    """
    Create current tables inside the caller's transaction without an implicit commit.
    """
    for statement in (SCHEMA + REVIEW_SCHEMA + MANUAL_SCHEMA + PRICE_SCHEMA).split(";"):
        if statement.strip():
            connection.execute(statement)


def _check_schema(connection: sqlite3.Connection) -> None:
    """
    Compare existing columns with a fresh in-memory schema without changing the selected database.
    """
    with closing(sqlite3.connect(":memory:")) as expected:
        _create_tables(expected)
        tables = expected.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        for (table,) in tables:
            current = {(row[1], row[2]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
            required = {(row[1], row[2]) for row in expected.execute(f'PRAGMA table_info("{table}")')}
            if current and current != required:
                raise ValueError(
                    f"The {table} table uses an outdated or incompatible layout. "
                    "Recreate the development database or select a fresh path with --database; "
                    "automatic upgrades are not supported."
                )
