# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Workflows - Manual                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum exchanges manual-entry instructions through monthly CSV files while keeping SQLite authoritative.

Each row has a stable identity and one accounting instruction. The workflow saves generated IDs before
inference, presents relevant statement evidence, and keeps unanswered or failed requests from becoming
ordinary bank categorisations. A valid result posts automatically; a correction replaces its whole event.

CSV export preserves concurrent edits and shows saved questions and proposed postings. The same models and
AI/storage functions can later serve a GUI without changing how instructions become journal entries.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Callable
import csv
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import sqlite3
from uuid import UUID, uuid4

from pydantic import ValidationError

# Aurum imports
from aurum.ai.chatgpt import ChatGPTConnection, ChatGPTError, record_usage
from aurum.ai.manual import MAX_MANUAL_BATCH_SIZE, ManualBatchError, complete_investment, manual_batch
from aurum.ai.skills import Skill, load_manual_skill
from aurum.database.journal import load_journal_entries
from aurum.database.manual import (
    defer_manual_case, fail_manual_case, load_manual_entries, manual_case_revision,
    manual_claims, manual_context_revision, manual_needs_processing, manual_owner_ids,
    release_manual_claims, reserve_manual_case,
    save_investment_proposal, save_manual_outcome, submit_manual_input,
)
from aurum.database.reviews import load_active_guidance
from aurum.database.transactions import load_transactions
from aurum.finance.accounts import AccountRegistry
from aurum.finance.investments import get_quote
from aurum.finance.journal import JournalEntry
from aurum.finance.manual import ManualCase, ManualInput, ManualOutcome
from aurum.finance.transaction import Transaction
from aurum.finance.securities import SecurityRegistry
from aurum.workflows.review import ReviewFileError, ReviewSession, _atomic_write, _safe_text

# CSV fields
EDITABLE_COLUMNS = ("date", "instruction", "source_transactions", "human_response")
COLUMNS = EDITABLE_COLUMNS + (
    "manual_entry_id", "status", "question", "proposed_postings", "journal_entry_id", "context_revision",
)


# %% 1. Manual CSV Exchange ----------------------------------------------------------------------------------
@dataclass
class ManualSession:
    """
    Keep editable file snapshots and ready cases together until the run's final CSV export.
    """

    folder: Path
    hashes: dict[Path, str] = field(default_factory=dict)
    rows: dict[Path, list[dict[str, str]]] = field(default_factory=dict)
    cases: list[ManualCase] = field(default_factory=list)
    blocked: set[UUID] = field(default_factory=set)
    messages: dict[UUID, str] = field(default_factory=dict)
    conflicts: int = 0


def read_manuals(
    connection: sqlite3.Connection, accounts: AccountRegistry, folder: Path, *, retry_errors: bool = False,
    skill_version: str | None = None, securities: SecurityRegistry | None = None,
) -> ManualSession:
    """
    Read instructions, persist row IDs, and prepare new answers, catalogue updates or explicit retries.

    Blank rows are ignored. Malformed files stop processing before inference. A row-level validation
    problem remains visible beside that row, while independent valid instructions can still proceed.
    """
    session = ManualSession(folder=Path(folder))
    session.folder.mkdir(parents=True, exist_ok=True)
    transactions = load_transactions(connection)
    entries = load_journal_entries(connection, accounts)
    records = {row["submission"].id: row for row in load_manual_entries(connection)}
    skill_version = skill_version or load_manual_skill().version
    catalogue_revision = _catalogue_revision(accounts, securities)
    months = {date.today().strftime("%Y-%m")} | {row.date.strftime("%Y-%m") for row in transactions}
    seen = set()

    # Read all files before submitting any instructions; duplicate row identities must not post twice.
    for path in sorted(session.folder.glob("????-??.csv")):
        try:
            date.fromisoformat(path.stem + "-01")
            content = path.read_bytes()
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
            if reader.fieldnames is None or tuple(reader.fieldnames) != COLUMNS:
                raise ReviewFileError(f"{path.name}: keep the manual template's columns and order.")
            rows = list(reader)
            for row in rows:
                if None in row or any(value is None for value in row.values()):
                    raise ReviewFileError(f"{path.name}: a manual row has the wrong number of columns.")
                if not row["instruction"].strip() and not row["manual_entry_id"].strip():
                    continue
                identifier = UUID(row["manual_entry_id"]) if row["manual_entry_id"].strip() else uuid4()
                if identifier in seen:
                    raise ReviewFileError("A manual-entry ID appears more than once in the CSV files.")
                seen.add(identifier)
                row["manual_entry_id"] = str(identifier)
            session.rows[path] = rows
            session.hashes[path] = sha256(content).hexdigest()
        except (UnicodeError, csv.Error, ValueError) as error:
            if isinstance(error, ReviewFileError):
                raise
            raise ReviewFileError(f"{path.name}: check the month, UUIDs and UTF-8 CSV format.") from error

    for month in sorted(months):
        path = session.folder / f"{month}.csv"
        if path not in session.rows:
            session.rows[path] = []
    for path, rows in session.rows.items():
        content = _csv_bytes(rows)
        if sha256(content).hexdigest() != session.hashes.get(path):
            if not _atomic_write(path, content, expected_hash=session.hashes.get(path), check_current=True):
                raise ReviewFileError(f"{path.name} changed while assigning manual IDs; run again.")
            session.hashes[path] = sha256(content).hexdigest()

    # Only durable CSV identities may acquire a database revision or an AI reservation.
    for path, rows in session.rows.items():
        for row_number, row in enumerate(rows, start=2):
            if not row["instruction"].strip():
                if row["manual_entry_id"]:
                    identifier = UUID(row["manual_entry_id"])
                    try:
                        release_manual_claims(
                            connection, identifier, accounts, context_revision=row["context_revision"],
                        )
                    except ValueError as error:
                        session.messages[identifier] = str(error)
                continue
            identifier = UUID(row["manual_entry_id"])
            previous = records.get(identifier)
            try:
                instruction = _input_from_row(row, previous)
                row["date"] = instruction.date.isoformat()
                if instruction.date.strftime("%Y-%m") != path.stem:
                    raise ValueError("Keep the accounting date in the CSV's named month.")
                record = submit_manual_input(connection, instruction, accounts, retry_errors=retry_errors)
                records[identifier] = record
                case = _build_case(
                    connection, record, transactions, entries, accounts,
                    catalogue_revision=catalogue_revision,
                )
                if manual_needs_processing(
                    record, skill_version=skill_version, retry_errors=retry_errors,
                    catalogue_revision=catalogue_revision,
                ):
                    if len(case.transactions) > 20:
                        defer_manual_case(
                            connection, case, accounts,
                            "More than 20 bank rows could relate to this instruction. Add the exact "
                            "source_transactions IDs from the bank-review CSV, then run again.",
                        )
                    else:
                        session.cases.append(case)
                if record["status"] in {"pending", "requesting", "needs_review", "failed", "unsupported"}:
                    session.blocked.update(item.transaction_id for item in case.transactions)
            except ValueError as error:
                if isinstance(error, ValidationError):
                    fields = {str(item["loc"][0]) for item in error.errors(include_input=False)}
                    message = (
                        "Enter a valid date as YYYY-MM-DD or DD-MM-YYYY."
                        if "date" in fields else "Check editable fields: " + ", ".join(sorted(fields)) + "."
                    )
                else:
                    message = str(error)
                session.messages[identifier] = f"{path.name}, row {row_number}: {message}"
                # Keep old candidate evidence protected when a correction is stale or incomplete.
                old_case: ManualCase | None = previous.get("case") if previous else None
                if old_case is not None:
                    session.blocked.update(item.transaction_id for item in old_case.transactions)
    session.blocked.update(manual_claims(connection))
    return session


def coordinate_manuals(
    connection: sqlite3.Connection, accounts: AccountRegistry,
    session: ManualSession, reviews: ReviewSession | None,
) -> None:
    """
    Stop competing manual and bank-review answers before either workflow spends tokens.
    """
    owners = manual_owner_ids(connection)
    claims = manual_claims(connection)
    review_sources = set()
    if reviews is not None:
        retained = []
        for case in reviews.cases:
            owned = {owners[row.transaction_id] for row in case.transactions if row.transaction_id in owners}
            if owned:
                label = ", ".join(str(identifier) for identifier in sorted(owned, key=str))
                for row in case.transactions:
                    reviews.messages[row.transaction_id] = (
                        f"Edit the original manual CSV entry instead: {label}."
                    )
                continue
            claimed = {
                claims[row.transaction_id] for row in case.transactions if row.transaction_id in claims
            }
            active_ids = {case.submission.id for case in session.cases}
            if claimed - active_ids:
                label = ", ".join(str(identifier) for identifier in sorted(claimed, key=str))
                for row in case.transactions:
                    reviews.messages[row.transaction_id] = (
                        f"Resolve or clear the pending manual instruction: {label}."
                    )
                continue
            retained.append(case)
            review_sources.update(row.transaction_id for row in case.transactions)
        reviews.cases = retained

    sources = [{row.transaction_id for row in case.transactions} for case in session.cases]
    retained = []
    for index, case in enumerate(session.cases):
        overlap = sources[index] & review_sources
        for other, identifiers in enumerate(sources):
            if index != other:
                overlap |= sources[index] & identifiers
        if overlap:
            message = (
                "This instruction overlaps another manual entry or a new bank-review answer. "
                "Keep the correction in one place and identify its exact source_transactions."
            )
            defer_manual_case(connection, case, accounts, message)
            if reviews is not None:
                for identifier in overlap & review_sources:
                    reviews.messages[identifier] = message
            session.messages[case.submission.id] = message
        else:
            retained.append(case)
    session.cases = retained
    if reviews is not None:
        reviews.cases = [
            case for case in reviews.cases
            if not any(row.transaction_id in session.blocked for row in case.transactions)
        ]


def export_manuals(
    connection: sqlite3.Connection, accounts: AccountRegistry, session: ManualSession,
) -> None:
    """
    Refresh saved outcomes without overwriting files edited during the run or losing an erased instruction.

    Missing saved rows are restored for visibility, but deleting or clearing a row never reverses postings.
    """
    records = {row["submission"].id: row for row in load_manual_entries(connection)}
    seen = set()
    by_account = {account.id: account for account in accounts.accounts.values()}
    for path, rows in session.rows.items():
        for row in rows:
            if not row["manual_entry_id"]:
                continue
            identifier = UUID(row["manual_entry_id"])
            seen.add(identifier)
            record = records.get(identifier)
            if record is not None:
                _display_result(row, record, connection, accounts, by_account)
            if identifier in session.messages:
                row["status"] = "needs_review"
                row["question"] = session.messages[identifier]
    for identifier, record in records.items():
        if identifier in seen:
            continue
        submission = record["submission"]
        path = session.folder / f"{submission.date:%Y-%m}.csv"
        row = _input_row(submission)
        if record["status"] == "withdrawn":
            row["instruction"] = ""
            row["human_response"] = ""
        _display_result(row, record, connection, accounts, by_account)
        session.rows.setdefault(path, []).append(row)
    for path, rows in session.rows.items():
        content = _csv_bytes(rows)
        if sha256(content).hexdigest() == session.hashes.get(path):
            continue
        if not _atomic_write(path, content, expected_hash=session.hashes.get(path), check_current=True):
            session.conflicts += 1
            print(f"{path.name} changed during processing; its manual edits were preserved.")
            continue
        session.hashes[path] = sha256(content).hexdigest()


# %% 2. Manual Processing ------------------------------------------------------------------------------------
def process_manuals(
    connection: sqlite3.Connection, accounts: AccountRegistry, session: ManualSession,
    client_provider: Callable[[], ChatGPTConnection], counts: dict[str, int],
    *, model: str, skill: Skill, retry_errors: bool = False,
    securities: SecurityRegistry | None = None, quote_fetcher: Callable | None = None,
    progress: Callable[[str], None] | None = None,
) -> None:
    """
    Interpret new instructions, resume saved pricing and save validated complete events independently.

    Cached proposals require the same input, accounting context, model, skill and configuration. Price
    retrieval follows durable proposal storage, so an interrupted market request need not spend AI tokens.
    quote_fetcher supports synthetic checks without contacting a provider.
    """
    report = progress or (lambda message: None)
    catalogue_revision = _catalogue_revision(accounts, securities)
    if session.cases:
        report(f"Manual entries: {len(session.cases)} ready for processing.")
    for start in range(0, len(session.cases), MAX_MANUAL_BATCH_SIZE):
        batch = []
        for case in session.cases[start:start + MAX_MANUAL_BATCH_SIZE]:
            try:
                if reserve_manual_case(
                    connection, case, accounts, model=model, skill_version=skill.version,
                    retry_errors=retry_errors,
                ):
                    batch.append(case)
            except ValueError as error:
                session.messages[case.submission.id] = str(error)
                counts["manual_failed"] += 1
        if not batch:
            continue
        records = {row["manual_id"]: row for row in load_manual_entries(connection)}
        outcomes = {}
        for case in batch:
            cached = records[case.submission.id]["investment_proposal"]
            if cached and (
                cached["case_revision"] == case.revision
                and cached["catalogue_revision"] == catalogue_revision
            ):
                saved = ManualOutcome.model_validate(cached["outcome"])
                if saved.model == model and saved.skill_version == skill.version:
                    outcomes[case.submission.id] = saved
        fresh = [case for case in batch if case.submission.id not in outcomes]
        failures, usage, stop = {}, {}, False
        if fresh:
            try:
                report(f"AI: interpreting {len(fresh)} manual entries with {model}...")
                client = client_provider()
                counts["requests"] += 1
                interpreted, failures, usage = manual_batch(
                    client, fresh, accounts, skill=skill, guidance=load_active_guidance(connection),
                    securities=securities,
                )
                expected = {case.submission.id for case in fresh}
                if set(interpreted) & set(failures) or set(interpreted) | set(failures) != expected:
                    raise ValueError("Manual results must cover the reserved inputs exactly once.")
                outcomes.update(interpreted)
            except (ChatGPTError, ValueError, OSError) as error:
                usage = getattr(error, "usage", usage)
                message = str(error) if isinstance(error, (ChatGPTError, ManualBatchError)) else (
                    f"Manual interpretation failed ({type(error).__name__}); accounting was unchanged."
                )
                failures = {case.submission.id: message for case in fresh}
                stop = isinstance(error, ChatGPTError)
        record_usage(counts, usage)
        pricing = sum(outcome.status == "pricing" for outcome in outcomes.values())
        if pricing:
            report(f"Investments: validating {pricing} entries and resolving any missing AUD prices...")
        for case in batch:
            identifier = case.submission.id
            if identifier in failures:
                fail_manual_case(connection, case, failures[identifier])
                counts["manual_failed"] += 1
                continue
            outcome = outcomes[identifier]
            try:
                if outcome.status == "pricing":
                    save_investment_proposal(connection, case, outcome, accounts, catalogue_revision)
                    outcome = _price_investment(
                        connection, case, outcome, accounts, securities, quote_fetcher,
                    )
                save_manual_outcome(connection, case, outcome, accounts, securities=securities)
            except (ValueError, sqlite3.Error) as error:
                fail_manual_case(
                    connection, case,
                    f"Manual storage validation failed ({type(error).__name__}); accounting was unchanged.",
                )
                counts["manual_failed"] += 1
                continue
            if outcome.status == "apply":
                counts["manual_posted"] += 1
            else:
                counts["manual_questions"] += 1
        if stop:
            break


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _catalogue_revision(accounts: AccountRegistry, securities: SecurityRegistry | None) -> str:
    """
    Identify the immutable configuration used to interpret a cached investment proposal.
    """
    payload = {
        "accounts": [accounts.accounts[key].model_dump(mode="json") for key in sorted(accounts.accounts)],
        "securities": [
            securities.securities[key].model_dump(mode="json") for key in sorted(securities.securities)
        ]
        if securities is not None else [],
    }
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _price_investment(
    connection, case: ManualCase, outcome: ManualOutcome, accounts: AccountRegistry,
    securities: SecurityRegistry | None, quote_fetcher: Callable | None,
) -> ManualOutcome:
    """
    Resolve required market pricing or return a focused question while retaining the saved interpretation.
    """
    try:
        if securities is None:
            raise ValueError("Configure securities.yaml before posting an investment")
        instruction = outcome.investment
        if instruction is None:
            raise ValueError("An investment needs a saved instruction before pricing")
        security = securities.securities.get(instruction.security_key)
        if security is None:
            raise ValueError("Add the intended instrument to securities.yaml using its existing key")
        quote = None
        if instruction.unit_price is None and (
            instruction.units is None or (
                instruction.amount is None and instruction.transaction_id is None
            )
        ):
            quote = get_quote(connection, security, case.submission.date, fetcher=quote_fetcher)
        return complete_investment(case, outcome, accounts, securities, quote=quote)
    except ValueError as error:
        message = "Investment figures could not be validated; check the supplied facts."
        if not isinstance(error, ValidationError):
            message = str(error)
        return ManualOutcome(
            manual_id=outcome.manual_id, status="needs_review", event_kind="investment",
            investment=outcome.investment, reason="The investment needs pricing or corrected trade facts.",
            question=message, model=outcome.model, skill_version=outcome.skill_version,
        )


def _build_case(
    connection, record: dict, transactions: list[Transaction], entries: list[JournalEntry],
    accounts: AccountRegistry, *, catalogue_revision: str | None = None,
) -> ManualCase:
    """
    Find nearby statement evidence and expand existing events to their complete source groups.
    """
    submission = record["submission"]
    by_id = {row.transaction_id: row for row in transactions}
    required = set(submission.source_transactions)
    previous_id = record.get("journal_entry_id")
    previous = next((entry for entry in entries if str(entry.id) == str(previous_id)), None)
    if previous is not None:
        required.update(line.transaction_id for line in previous.lines if line.transaction_id is not None)
    text = submission.instruction + " " + submission.human_response
    for token in re.findall(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", text):
        if UUID(token) in by_id:
            required.add(UUID(token))
    if not required <= by_id.keys():
        raise ValueError("A source_transactions ID is not in the imported transactions.")
    if required:
        candidates = set(required)
    else:
        candidates = _nearby_candidates(submission, transactions, accounts)
    selected_entries = []
    changed = True
    while changed:
        changed = False
        for entry in entries:
            sources = {line.transaction_id for line in entry.lines if line.transaction_id is not None}
            if entry == previous or candidates & sources:
                if entry not in selected_entries:
                    selected_entries.append(entry)
                if not sources <= candidates:
                    candidates.update(sources)
                    changed = True
                if required & sources:
                    required.update(sources)
    selected = tuple(by_id[identifier] for identifier in sorted(candidates, key=str))
    selected_entries = tuple(sorted(selected_entries, key=lambda entry: str(entry.id)))
    return ManualCase(
        submission=submission,
        revision=manual_case_revision(connection, submission, selected, selected_entries, accounts),
        transactions=selected, required_sources=tuple(sorted(required, key=str)), entries=selected_entries,
        question=record.get("question"), catalogue_revision=catalogue_revision,
    )


def _nearby_candidates(
    submission: ManualInput, transactions: list[Transaction], accounts: AccountRegistry,
) -> set[UUID]:
    """
    Offer bounded local evidence; matching an amount alone never authorises a posting.
    """
    text = (submission.instruction + " " + submission.human_response).casefold()
    if re.search(r"\b(opening|initial|starting)\b|\bbalance at\b", text):
        return set()
    mentioned = {
        account.id for account in accounts.accounts.values() if account.bank_details is not None
        and any(re.search(rf"(?<!\w){re.escape(label.casefold())}(?!\w)", text)
                for label in (account.key, account.key.replace("_", " "), account.name))
    }
    bank_activity = re.search(r"\b(pay|paid|repay|repayment|transfer|deposit|bank|bought|sold)\b", text)
    if not mentioned and not bank_activity:
        return set()
    candidates = [row for row in transactions if abs((row.date - submission.date).days) <= 3]
    if mentioned:
        candidates = [row for row in candidates if row.account_id in mentioned]
    amounts = {
        Decimal(value.replace(",", "")) for value in re.findall(
            r"(?<![\w.])\$?([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)(?![\w.])", text,
        )
    }
    if amounts:
        matching_amounts = [row for row in candidates if row.amount.copy_abs() in amounts]
        if matching_amounts:
            candidates = matching_amounts
    return {row.transaction_id for row in candidates}


def _input_from_row(row: dict, previous: dict | None) -> ManualInput:
    """
    Parse editable cells and undo only formula escaping previously generated for the same text.
    """
    values = {name: row[name] for name in EDITABLE_COLUMNS}
    if previous is not None:
        old = previous["submission"]
        for name in ("instruction", "human_response"):
            if values[name] == _safe_text(getattr(old, name)):
                values[name] = getattr(old, name)
    source_ids = re.split(r"[;,\s]+", values["source_transactions"].strip())
    sources = tuple(UUID(value) for value in source_ids if value)
    return ManualInput(
        id=UUID(row["manual_entry_id"]), date=values["date"], instruction=values["instruction"],
        source_transactions=sources, human_response=values["human_response"],
        context_revision=row["context_revision"],
    )


def _input_row(submission: ManualInput) -> dict:
    """
    Restore a missing saved row from its editable submission without losing its persistent identity.
    """
    row = dict.fromkeys(COLUMNS, "")
    row.update(
        date=submission.date.isoformat(), instruction=_safe_text(submission.instruction),
        source_transactions=";".join(str(identifier) for identifier in submission.source_transactions),
        human_response=_safe_text(submission.human_response), manual_entry_id=str(submission.id),
    )
    return row


def _display_result(row: dict, record: dict, connection, accounts, by_account: dict) -> None:
    """
    Render saved output fields while leaving the user's editable cells intact.
    """
    outcome: ManualOutcome | None = record.get("outcome")
    row["status"] = record["status"]
    row["question"] = _safe_text(record.get("error") or record.get("question") or (
        outcome.reason if outcome is not None and outcome.status == "unsupported" else ""
    ))
    row["journal_entry_id"] = str(record.get("journal_entry_id") or "")
    row["context_revision"] = manual_context_revision(connection, record["submission"].id, accounts)
    entry = outcome.entry if outcome is not None else None
    row["proposed_postings"] = _safe_text("; ".join(
        f"{'Dr' if line.debit else 'Cr'} {by_account[line.account_id].name} "
        f"AUD {line.debit or line.credit:.2f}"
        for line in entry.lines
    )) if entry is not None else ""
    if outcome is not None and outcome.pricing is not None and outcome.investment is not None:
        calculation = outcome.pricing
        units = format(calculation.units, ".12g") if calculation.units_estimated else str(calculation.units)
        label = "estimated units" if calculation.units_estimated else "units"
        row["proposed_postings"] += (
            f"; {outcome.investment.security_key}: {units} {label}, "
            f"book cost AUD {calculation.total_cost:.2f}"
        )
        if calculation.quote is not None:
            row["proposed_postings"] += (
                f"; reference close AUD {calculation.quote.price} on {calculation.quote.price_date}"
            )
        if calculation.fees is not None:
            row["proposed_postings"] += f"; included fees AUD {calculation.fees:.2f}"


def _csv_bytes(rows: list[dict]) -> bytes:
    """
    Encode a spreadsheet-friendly CSV, quoting commas and multiline human instructions.
    """
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")
