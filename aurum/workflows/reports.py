# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 Workflows - Reports                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum turns a consistent snapshot of posted accounting into four financial report PDFs.

This workflow reads account definitions, journal events and saved review progress without changing them.
It then retrieves any required investment closes, saving only the market-price cache, and passes the
validated figures to the PDF renderer. Unfinished accounting and unavailable valuations remain visible
as draft explanations. Reporting never processes CSV answers or sends requests to ChatGPT.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Callable
from contextlib import closing
from datetime import date
import os
from pathlib import Path
import sqlite3
import tempfile
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Aurum imports
from aurum.database.database import DEFAULT_DATABASE, _check_schema
from aurum.database.journal import load_journal_entries
from aurum.database.manual import load_manual_entries
from aurum.database.reviews import load_review_answers, load_review_cases, load_review_exports
from aurum.database.savings import load_savings_allocations
from aurum.database.transactions import load_transactions
from aurum.finance.accounts import DEFAULT_ACCOUNTS, load_accounts
from aurum.finance.budget import DEFAULT_BUDGET_SETTINGS, load_budget_settings
from aurum.finance.investments import PriceQuote, PriceUnavailable, get_quote, holdings_as_of
from aurum.finance.journal import JournalEntry
from aurum.finance.reports import (
    DEFAULT_REPORT_SETTINGS, Reports, build_reports, load_report_settings, resolve_period,
)
from aurum.finance.securities import DEFAULT_SECURITIES, Security, load_securities
from aurum.reporting.pdf import render_reports

# Default paths
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORTS = PROJECT_ROOT / "data" / "reports"
REPORT_FILENAMES = ("balance_sheet.pdf", "income_statement.pdf", "budget.pdf", "investments.pdf")


# %% 1. Report Generation ------------------------------------------------------------------------------------
class ReportResult(BaseModel):
    """
    Return the calculated reports and their four PDF paths after successful publication.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    reports: Reports
    paths: tuple[Path, Path, Path, Path]


def generate_reports(
    *,
    accounts_path: Path | str = DEFAULT_ACCOUNTS,
    securities_path: Path | str = DEFAULT_SECURITIES,
    settings_path: Path | str = DEFAULT_REPORT_SETTINGS,
    budget_path: Path | str = DEFAULT_BUDGET_SETTINGS,
    database_path: Path | str = DEFAULT_DATABASE,
    output_dir: Path | str = DEFAULT_REPORTS,
    month: str | None = None,
    today: date | None = None,
    fetcher: Callable[[Security, date], PriceQuote] | None = None,
    progress: Callable[[str], None] | None = None,
) -> ReportResult:
    """
    Generate balance sheet, income, budget and investment PDFs from posted journal events.

    Defaults resolve from the project root; supplied relative paths use the current working directory.
    month selects a working-day reporting month, or defaults to the latest posted event's month.
    fetcher and today allow repeatable synthetic checks without live prices or a changing clock.

    Returns:
        Validated report figures and saved paths in balance sheet, income, budget and investments order.

    Raises:
        ValueError: Configuration, accounting, reporting dates or database layout is invalid.
        OSError: A required file cannot be read or a PDF output cannot be written.
        sqlite3.Error: The existing database or its quote cache cannot be accessed.
    """
    emit = progress or (lambda message: None)
    emit("Reading account configuration and posted journal...")
    accounts = load_accounts(accounts_path, initialise_ids=False)
    settings = load_report_settings(settings_path, accounts)
    budget_settings = load_budget_settings(budget_path, accounts)
    database = Path(database_path).resolve()

    # A read transaction gives all four reports the same accounting snapshot.
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")
        _check_schema(connection)
        required = {"journal", "transactions", "manual_entries", "review_actions", "price_quotes"}
        present = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not required <= present:
            raise ValueError("Prepare the journal before reporting; required accounting tables are missing")
        entries = load_journal_entries(connection, accounts)
        savings_allocations = tuple(load_savings_allocations(connection).values())
        period = resolve_period(entries, month=month, today=today)
        draft_reasons = _unfinished_accounting(connection, entries, period.cutoff)

    # Release the snapshot lock before any cache write; no accounting connection is reopened for writing.
    positions = holdings_as_of(entries, period.cutoff)
    securities = load_securities(securities_path, initialise_ids=False) if positions else None
    quotes: dict[UUID, PriceQuote] = {}
    valuation_errors: dict[UUID, str] = {}
    if securities is not None:
        required_ids = {position.security_id for position in positions}
        known_ids = {security.id for security in securities.securities.values()}
        if not required_ids <= known_ids:
            raise ValueError("Restore securities referenced by posted holdings before generating reports")
        emit(
            f"Checking closing prices for {len(required_ids)} securities as of {period.cutoff.isoformat()}..."
        )
        with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True)) as connection:
            for identifier in sorted(required_ids, key=str):
                security = securities.by_id(identifier)
                try:
                    quotes[identifier] = get_quote(connection, security, period.cutoff, fetcher=fetcher)
                except PriceUnavailable:
                    valuation_errors[identifier] = (
                        f"No completed AUD close on or within seven days before {period.cutoff.isoformat()}. "
                        "Check the symbol or rerun reports"
                    )
    reports = build_reports(
        entries, accounts, settings, period, quotes=quotes, securities=securities,
        budget_settings=budget_settings, valuation_errors=valuation_errors, draft_reasons=draft_reasons,
        savings_allocations=savings_allocations,
    )
    emit(f"Rendering {period.month} balance sheet, income, budget and investment reports...")
    documents = render_reports(reports, settings)
    paths = _save_pdfs(Path(output_dir).resolve() / period.month, documents)
    return ReportResult(reports=reports, paths=paths)


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _unfinished_accounting(
    connection: sqlite3.Connection, entries: list[JournalEntry], cutoff: date,
) -> tuple[str, ...]:
    """
    Identify current unfinished work through the cutoff, including sources without any journal row.

    Saved corrections may leave valid older postings in place; those events still need a draft notice.
    Later periods, superseded requests and guidance-only failures do not invalidate this snapshot.
    Unsaved CSV edits are outside the database and must be submitted with the journal command first.
    """
    transactions = load_transactions(connection)
    posted = {
        line.transaction_id for entry in entries for line in entry.lines if line.transaction_id is not None
    }
    unposted = sum(row.date <= cutoff and row.transaction_id not in posted for row in transactions)
    reasons = []
    if unposted:
        reasons.append(f"{unposted} imported transaction(s) through {cutoff.isoformat()} are not posted.")

    # A correction to an older event matters even if its proposed new date is after this report.
    event_dates = {entry.id: entry.date for entry in entries}
    posting_dates = {
        line.transaction_id: entry.date for entry in entries for line in entry.lines
        if line.transaction_id is not None
    }
    manual_pending = 0
    for record in load_manual_entries(connection):
        if record["status"] in {"applied", "withdrawn", "superseded"}:
            continue
        dates = [record["submission"].date]
        dates.extend(
            posting_dates[identifier] for identifier in record["submission"].source_transactions
            if identifier in posting_dates
        )
        if record["journal_entry_id"] in event_dates:
            dates.append(event_dates[record["journal_entry_id"]])
        if record["case"] is not None:
            dates.extend(entry.date for entry in record["case"].entries)
        if min(dates) <= cutoff:
            manual_pending += 1
    if manual_pending:
        reasons.append(f"{manual_pending} manual instruction(s) affecting this cutoff still need attention.")

    latest = {
        row.transaction_id: record for record in load_review_cases(connection)
        for row in record["case"].transactions
    }
    outstanding = set()
    exports = load_review_exports(connection)
    source_dates = {row.transaction_id: row.date for row in transactions}
    for identifier, record in latest.items():
        if record["status"] in {"applied", "superseded"}:
            continue
        if exports.get(identifier, {}).get("human_review_cleared") and record["status"] != "requesting":
            continue
        dates = [row.date for row in record["case"].transactions]
        dates.extend(entry.date for entry in record["case"].entries)
        if min(dates) <= cutoff:
            outstanding.add(identifier)
    saved_answers = load_review_answers(connection)
    for identifier, export in exports.items():
        if export.get("review_status") not in {"stale", "manual_required"}:
            continue
        if not export.get("human_review", "").strip():
            continue
        record = latest.get(identifier)
        resolved_answer = record is not None and record["status"] in {"applied", "superseded"} and any(
            answer == saved_answers.get(identifier) and answer.human_review == export["human_review"]
            for answer in record["case"].answers if answer.transaction_id == identifier
        )
        if resolved_answer:
            continue
        affected_date = min(source_dates.get(identifier, date.max), posting_dates.get(identifier, date.max))
        if affected_date <= cutoff:
            outstanding.add(identifier)
    if outstanding:
        reasons.append(f"{len(outstanding)} source transaction(s) have unresolved saved review corrections.")
    return tuple(reasons)


def _save_pdfs(folder: Path, documents: tuple[bytes, bytes, bytes, bytes]) -> tuple[Path, Path, Path, Path]:
    """
    Stage all four rendered PDFs before replacing the period's existing files.

    Rendering and staging failures leave previous PDFs intact. Each final replacement is atomic, so an
    individual PDF is never exposed while partially written.
    """
    if len(documents) != 4 or any(not content.startswith(b"%PDF-") for content in documents):
        raise ValueError("The renderer did not return four valid PDF documents")
    folder.mkdir(parents=True, exist_ok=True)
    paths = tuple(folder / name for name in REPORT_FILENAMES)
    with tempfile.TemporaryDirectory(prefix=".reports-", dir=folder) as temporary:
        staged = []
        for target, content in zip(paths, documents):
            path = Path(temporary) / target.name
            path.write_bytes(content)
            staged.append(path)
        for source, target in zip(staged, paths):
            os.replace(source, target)
    return paths
