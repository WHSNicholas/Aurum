# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Workflows - Review                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum's monthly review files let you explain or correct any imported transaction in plain language.

Each statement row appears once, including both observations of a transfer and transactions already posted.
Only human_review is read as an instruction. The database remains the source of accounting facts, saved
answers and outcomes. Related source rows are reviewed together so a correction replaces complete events.

Answers carry the accounting revision shown in their CSV. Changed or stale accounting cannot be silently
overwritten, and unchanged answers are never sent again. CSV writes preserve edits made while a run is in
progress. Private learned guidance is also exported as readable Markdown beside the review directory.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import csv
from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
import io
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Callable
from uuid import UUID, uuid4

# Aurum imports
from aurum.ai.chatgpt import ChatGPTConnection, ChatGPTError, record_usage
from aurum.ai.review import MAX_REVIEW_BATCH_SIZE, ReviewBatchError, learn_guidance_batch, review_batch
from aurum.ai.skills import Skill
from aurum.database.categorisation import load_decisions
from aurum.database.journal import load_journal_entries
from aurum.database.transactions import load_transactions
from aurum.database.reviews import (
    fail_guidance_case, list_guidance_cases, load_guidance_assessments, reserve_guidance_case,
    save_guidance_result,
    fail_review_case, load_active_guidance, load_review_answers, load_review_cases, load_review_exports,
    reserve_review_case, review_revision, save_review_answers, save_review_exports, save_review_outcome,
)
from aurum.finance.budget import BudgetSettings, budget_revision
from aurum.finance.accounts import AccountRegistry
from aurum.finance.review import ReviewAnswer, ReviewCase, ReviewOutcome

COLUMNS = (
    "date", "effective_date", "account", "amount", "description",
    "debit_accounts", "credit_accounts", "status", "question", "human_review", "review_status",
    "review_result", "transaction_id", "journal_entry_id", "context_revision",
)


# %% 1. Monthly Reviews --------------------------------------------------------------------------------------
class ReviewFileError(ValueError):
    """
    Report a review file that cannot be safely interpreted or overwritten.
    """


@dataclass
class ReviewSession:
    """
    Keep one run's input snapshots, pending cases and local messages until CSV export finishes.
    """

    folder: Path
    hashes: dict[Path, str] = field(default_factory=dict)
    cases: list[ReviewCase] = field(default_factory=list)
    blocked: set[UUID] = field(default_factory=set)
    cleared: set[UUID] = field(default_factory=set)
    messages: dict[UUID, str] = field(default_factory=dict)
    conflicts: int = 0


def read_reviews(
    connection: sqlite3.Connection, accounts: AccountRegistry, folder: Path, *, retry_errors: bool = False,
    budget: BudgetSettings | None = None,
) -> ReviewSession:
    """
    Read all monthly files before processing, save changed answers and group complete review cases.

    Only nonblank changes to human_review submit work. Identity and revision columns must still match the
    exported row. Stale answers are retained for the user to revise after seeing current accounting.

    Raises:
        ReviewFileError: A CSV is malformed, has duplicate identities, or contains unrecognised source rows.
    """
    session = ReviewSession(folder=Path(folder))
    baselines = load_review_exports(connection)
    answers = load_review_answers(connection)
    transactions = load_transactions(connection)
    by_id = {row.transaction_id: row for row in transactions}
    entries = load_journal_entries(connection, accounts)
    changed = []
    seen = set()
    # Remember a deliberate clearing even if its CSV must later be rebuilt from saved history.
    session.cleared = {
        identifier for identifier, baseline in baselines.items()
        if baseline.get("human_review_cleared", False)
    }
    for path in sorted(session.folder.glob("????-??.csv")):
        content = path.read_bytes()
        session.hashes[path] = sha256(content).hexdigest()
        try:
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
            if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ReviewFileError(f"{path.name}: the header is missing or has duplicate columns.")
            if not {"transaction_id", "context_revision", "human_review"} <= set(reader.fieldnames):
                raise ReviewFileError(f"{path.name}: keep the identity, revision and human_review columns.")
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ReviewFileError(f"{path.name}: a row has the wrong number of columns.")
                try:
                    identifier = UUID(row["transaction_id"])
                except ValueError as error:
                    raise ReviewFileError(f"{path.name}: a transaction identity is invalid.") from error
                baseline = baselines.get(identifier)
                if identifier not in by_id or baseline is None or identifier in seen:
                    raise ReviewFileError(f"{path.name}: a row is unknown or appears more than once.")
                if path.stem != baseline["month"]:
                    raise ReviewFileError(f"{path.name}: keep each row in its exported month.")
                seen.add(identifier)
                text = row["human_review"]
                if text == baseline.get("displayed_human_review", ""):
                    text = baseline.get("human_review", "")
                if not text.strip():
                    session.cleared.add(identifier)
                    continue
                session.cleared.discard(identifier)
                if text == baseline.get("human_review", ""):
                    continue
                revision = row["context_revision"]
                if revision != baseline["context_revision"]:
                    raise ReviewFileError(
                        f"{path.name}: an identity or revision was edited or is stale; refresh that row."
                    )
                answer = ReviewAnswer(
                    transaction_id=identifier, revision=revision, human_review=text,
                )
                changed.append(answer)
                answers[identifier] = answer
        except (UnicodeError, csv.Error) as error:
            raise ReviewFileError(
                f"{path.name}: save a valid UTF-8 CSV without changing its columns."
            ) from error
    save_review_answers(connection, changed)

    records = load_review_cases(connection)
    latest = _latest_records(records)
    # Saved cases consume their exact answers, including questions and failures; retries must be explicit.
    consumed = {
        (answer.transaction_id, answer.revision, answer.human_review)
        for record in records for answer in record["case"].answers
    }
    fresh = {
        identifier for identifier, answer in answers.items()
        if identifier not in session.cleared
        and (identifier, answer.revision, answer.human_review) not in consumed
    }
    active_answers = {
        identifier: answer for identifier, answer in answers.items() if identifier not in session.cleared
    }
    groups = _source_groups(transactions, entries, active_answers)
    for group in groups:
        group_answers = [
            answer for identifier, answer in active_answers.items()
            if identifier in group and (
                identifier in fresh or (
                    identifier in latest
                    and latest[identifier]["status"] in {"needs_review", "failed", "requesting"}
                    and answer in latest[identifier]["case"].answers
                )
            )
        ]
        if not group & fresh:
            continue
        stale = [
            answer for answer in group_answers
            if answer.revision != review_revision(connection, [answer.transaction_id], accounts)
        ]
        if stale:
            for identifier in group:
                session.messages[identifier] = (
                    "Accounting changed after this answer was written. Check the refreshed row, then edit "
                    "human_review to submit it again."
                )
            session.blocked.update(group)
            continue
        case = ReviewCase(
            budget_revision=budget_revision(budget),
            revision=review_revision(connection, group, accounts),
            transactions=tuple(by_id[identifier] for identifier in sorted(group, key=str)),
            entries=tuple(entry for entry in entries if _entry_sources(entry) & group),
            answers=tuple(sorted(group_answers, key=lambda item: str(item.transaction_id))),
        )
        session.cases.append(case)
        session.blocked.update(group)

    # The newest outcome controls each source; older failures cannot undo a later correction.
    for identifier, record in latest.items():
        active = any(answer.transaction_id not in session.cleared for answer in record["case"].answers)
        if record["status"] == "requesting" or (
            record["status"] in {"needs_review", "failed"} and active
        ):
            session.blocked.add(identifier)
    if retry_errors:
        queued_sources = {row.transaction_id for case in session.cases for row in case.transactions}
        for record in records:
            case = record["case"]
            if (
                record["status"] in {"failed", "requesting"}
                and not any(answer.transaction_id in session.cleared for answer in case.answers)
                and all(latest.get(row.transaction_id) is record for row in case.transactions)
                and not any(
                    row.transaction_id in queued_sources or row.transaction_id in fresh
                    for row in case.transactions
                )
            ):
                if case.budget_revision != budget_revision(budget):
                    # An explicit retry gets a new request identity when its category catalogue changed.
                    case = ReviewCase.model_validate({
                        **case.model_dump(), "id": uuid4(), "budget_revision": budget_revision(budget),
                    })
                session.cases.append(case)
                queued_sources.update(row.transaction_id for row in case.transactions)
    return session


def process_reviews(
    connection: sqlite3.Connection,
    accounts: AccountRegistry,
    session: ReviewSession,
    client_provider: Callable[[], ChatGPTConnection],
    counts: dict[str, int],
    *,
    model: str,
    skill: Skill,
    retry_errors: bool = False,
    progress: Callable[[str], None] | None = None,
    budget: BudgetSettings | None = None,
) -> None:
    """
    Apply changed human answers before ordinary categorisation, retaining valid accounting on any failure.

    Independent cases share a compact request. Invalid results fail only their own cases; valid replacements
    commit independently. Preserve reported usage even when validation fails, and save safe diagnostic details
    for explicit retries. A connection or provider failure stops further review batches in this run.
    """
    report = progress or (lambda message: None)
    baselines = load_review_exports(connection)
    records = load_review_cases(connection)
    decisions = load_decisions(connection)
    questions = {
        answer.transaction_id: _answer_question(
            answer.transaction_id, baselines.get(answer.transaction_id, {}), records, decisions,
        )
        for case in session.cases for answer in case.answers
    }
    for start in range(0, len(session.cases), MAX_REVIEW_BATCH_SIZE):
        batch = []
        for case in session.cases[start:start + MAX_REVIEW_BATCH_SIZE]:
            if any(line.investment is not None for entry in case.entries for line in entry.lines):
                for transaction in case.transactions:
                    session.messages[transaction.transaction_id] = (
                        "Edit this investment in its original manual CSV row. "
                        "The current postings and units have been preserved."
                    )
                counts["review_failed"] += 1
                continue
            try:
                reserved = reserve_review_case(
                    connection, case, accounts, model=model, skill_version=skill.version,
                    retry_errors=retry_errors, budget=budget,
                )
            except ValueError:
                for transaction in case.transactions:
                    session.messages[transaction.transaction_id] = (
                        "This answer conflicts with changed accounting or an interrupted review. "
                        "Check the refreshed row; use --retry-errors to replace an interrupted request."
                    )
                counts["review_failed"] += 1
                continue
            if reserved:
                batch.append(case)
        if not batch:
            continue
        usage = {}
        try:
            client = client_provider()
            report(f"AI: reviewing {len(batch)} answered cases with {model}...")
            counts["requests"] += 1
            outcomes, failures, usage = review_batch(
                client, batch, accounts, skill=skill, guidance=load_active_guidance(connection),
                questions=questions, budget=budget,
            )
            valid_ids = {item.case_id for item in outcomes}
            if (
                len(valid_ids) != len(outcomes) or valid_ids & failures.keys()
                or valid_ids | failures.keys() != {item.id for item in batch}
            ):
                raise ValueError("The review result does not cover its reserved cases.")
        except (ChatGPTError, ValueError, OSError) as error:
            record_usage(counts, getattr(error, "usage", usage))
            reason = _review_error(error)
            for case in batch:
                fail_review_case(connection, case, reason)
                counts["review_failed"] += 1
            report(f"Review batch failed: {reason}")
            if isinstance(error, ChatGPTError):
                break
            continue
        record_usage(counts, usage)
        by_case = {outcome.case_id: outcome for outcome in outcomes}
        for case in batch:
            if case.id in failures:
                fail_review_case(connection, case, failures[case.id])
                counts["review_failed"] += 1
                continue
            outcome = by_case[case.id]
            try:
                save_review_outcome(connection, case, outcome, accounts, budget=budget)
            except (ValueError, sqlite3.Error) as error:
                reason = f"Review storage validation failed ({type(error).__name__}); no change was saved."
                fail_review_case(connection, case, reason)
                failures[case.id] = reason
                counts["review_failed"] += 1
                continue
            if outcome.status == "apply":
                counts["reviews_applied"] += 1
            else:
                counts["review_questions"] += 1
        for reason, number in Counter(failures.values()).items():
            report(f"Review failed for {number} case(s): {reason}")


def process_guidance(
    connection: sqlite3.Connection,
    accounts: AccountRegistry,
    client_provider: Callable[[], ChatGPTConnection],
    counts: dict[str, int],
    *,
    model: str,
    skill: Skill,
    retry_errors: bool = False,
    progress: Callable[[str], None] | None = None,
) -> None:
    """
    Learn from accepted answers without submitting reviews again or changing their journal postings.

    Reserve each case before inference. Completed assessments are skipped, and failed or interrupted work
    needs an explicit retry. Each result records its guidance, explanation or safe validation errors.
    """
    report = progress or (lambda message: None)
    cases = list_guidance_cases(connection, accounts, retry_errors=retry_errors)
    counts["guidance_assessed"] = 0
    for start in range(0, len(cases), MAX_REVIEW_BATCH_SIZE):
        batch = [
            case for case in cases[start:start + MAX_REVIEW_BATCH_SIZE]
            if reserve_guidance_case(
                connection, case, model=model, skill_version=skill.version, retry_errors=retry_errors,
            )
        ]
        if not batch:
            continue
        usage = {}
        try:
            client = client_provider()
            report(f"AI: learning guidance from {len(batch)} accepted reviews with {model}...")
            counts["requests"] += 1
            results, failures, usage = learn_guidance_batch(
                client, batch, accounts, guidance=load_active_guidance(connection), skill=skill,
            )
            if results.keys() & failures.keys() or results.keys() | failures.keys() != {c.id for c in batch}:
                raise ValueError("The guidance response does not cover its reserved cases.")
        except (ChatGPTError, ValueError, OSError) as error:
            record_usage(counts, getattr(error, "usage", usage))
            reason = _review_error(error)
            for case in batch:
                fail_guidance_case(connection, case.id, reason)
            report(f"Guidance batch failed: {reason}")
            if isinstance(error, ChatGPTError):
                break
            continue
        record_usage(counts, usage)
        for case in batch:
            if case.id in failures:
                fail_guidance_case(connection, case.id, failures[case.id])
                continue
            try:
                save_guidance_result(
                    connection, case, results[case.id], accounts, model=model, skill_version=skill.version,
                )
            except (ValueError, sqlite3.Error) as error:
                fail_guidance_case(
                    connection, case.id,
                    f"Guidance storage validation failed ({type(error).__name__}); accounting was unchanged.",
                )
                continue
            counts["guidance_assessed"] += 1


def export_reviews(
    connection: sqlite3.Connection, accounts: AccountRegistry, session: ReviewSession,
) -> None:
    """
    Refresh monthly CSVs from SQLite while preserving answers and concurrent user edits.

    Missing files are rebuilt from saved answers, preserving deliberately cleared cells without deleting
    their history. Concurrently edited files are left untouched. Only successfully written files advance
    their stored export baseline.
    """
    from aurum.database.manual import manual_owner_ids

    owners = manual_owner_ids(connection)
    transactions = load_transactions(connection)
    entries = load_journal_entries(connection, accounts)
    entry_by_source = {identifier: entry for entry in entries for identifier in _entry_sources(entry)}
    decisions = load_decisions(connection)
    answers = load_review_answers(connection)
    latest = _latest_records(load_review_cases(connection))
    previous = load_review_exports(connection)
    by_account = {account.id: account for account in accounts.accounts.values()}
    months: dict[str, list[dict]] = {}
    baselines = {}
    for transaction in transactions:
        identifier = transaction.transaction_id
        entry = entry_by_source.get(identifier)
        answer = answers.get(identifier)
        record = latest.get(identifier)
        decision = decisions.get(identifier)
        outcome: ReviewOutcome | None = record["outcome"] if record else None
        text = answer.human_review if answer and identifier not in session.cleared else ""
        review_status = record["status"] if record else ""
        result = record["error"] or "" if record else ""
        if outcome is not None:
            result = outcome.question or outcome.reason
        if identifier in session.messages:
            review_status, result = "stale", session.messages[identifier]
        elif previous.get(identifier, {}).get("review_status") == "stale" and not record:
            review_status = "stale"
            result = previous[identifier].get("review_result", "")
        if text and entry is not None and any(line.investment is not None for line in entry.lines):
            review_status = "manual_required"
            result = (
                "Edit this investment in its original manual CSV row. "
                "The current postings and units have been preserved."
            )
        if identifier in owners:
            review_status = "manual_required" if text else "manual_entry"
            result = f"Edit the original manual CSV entry: {owners[identifier]}."
        question = outcome.question if outcome is not None else decision.question if decision else ""
        revision = review_revision(connection, [identifier], accounts)
        month = transaction.date.strftime("%Y-%m")
        row = {
            "date": transaction.date.isoformat(),
            "effective_date": transaction.effective_date.isoformat() if transaction.effective_date else "",
            "account": by_account[transaction.account_id].name,
            "amount": format(transaction.amount, ".2f"),
            "description": transaction.description,
            "debit_accounts": _postings_label(entry, by_account, "debit"),
            "credit_accounts": _postings_label(entry, by_account, "credit"),
            "status": "posted" if entry else decision.status if decision else "pending",
            "question": question or "",
            "human_review": text,
            "review_status": review_status,
            "review_result": result,
            "transaction_id": str(identifier),
            "journal_entry_id": str(entry.id) if entry else "",
            "context_revision": revision,
        }
        safe = {key: value if key == "amount" else _safe_text(value) for key, value in row.items()}
        months.setdefault(month, []).append(safe)
        baselines[identifier] = {
            "context_revision": revision, "human_review": text,
            "human_review_cleared": identifier in session.cleared,
            "displayed_human_review": safe["human_review"], "month": month,
            "review_status": review_status, "review_result": result, "question": question or None,
        }

    session.folder.mkdir(parents=True, exist_ok=True)
    for month, rows in months.items():
        path = session.folder / f"{month}.csv"
        expected = session.hashes.get(path)
        current = sha256(path.read_bytes()).hexdigest() if path.exists() else None
        if current != expected:
            session.conflicts += 1
            print(f"{path.name} changed during processing; its edits were preserved. Run the command again.")
            continue
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        if not _atomic_write(
            path, stream.getvalue().encode("utf-8-sig"), expected_hash=expected, check_current=True,
        ):
            session.conflicts += 1
            print(f"{path.name} changed during processing; its edits were preserved. Run the command again.")
            continue
        save_review_exports(connection, {
            UUID(row["transaction_id"]): baselines[UUID(row["transaction_id"])] for row in rows
        })
    export_guidance(connection, session.folder.parent / "ai" / "categorisation_guidelines.md")


def export_guidance(connection: sqlite3.Connection, path: Path) -> None:
    """
    Show active guidance and each learning outcome; SQLite remains the authoritative private history.
    """
    lines = [
        "# Categorisation guidance", "",
        "Generated from reviewed answers in SQLite. "
        "Edit human_review in a transaction CSV to change guidance.",
        "",
    ]
    for item in load_active_guidance(connection):
        lines.extend((f"## {item.scope}", "", item.instruction, ""))
    lines.extend(("## Learning outcomes", ""))
    actions = load_guidance_assessments(connection)
    if not actions:
        lines.extend(("No guidance assessments have been recorded yet.", ""))
    for action in actions:
        lines.extend((f"### Review {action['review_action_id']}", "", f"Status: {action['status']}", ""))
        result = action["result"]
        if result is not None:
            if result.reason:
                lines.extend((result.reason, ""))
            for error in result.errors:
                lines.extend((f"Validation: {error}", ""))
        disposition = action.get("disposition") or {}
        if disposition:
            lines.extend((f"Guidelines saved: {disposition.get('saved', 0)}.", ""))
            for message in disposition.get("messages", []):
                lines.extend((message, ""))
        if action.get("error"):
            lines.extend((action["error"], ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines).encode("utf-8")
    if not path.exists() or path.read_bytes() != content:
        _atomic_write(path, content)


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _answer_question(identifier: UUID, baseline: dict, records: list[dict], decisions: dict) -> str | None:
    """
    Recover the trusted exported question, with saved database context for exports made before it was stored.

    The editable CSV question is never used as evidence. Failed requests have no new question, so look back
    to the latest completed review outcome before falling back to the original categorisation decision.
    """
    if "question" in baseline:
        return baseline["question"]
    for record in reversed(records):
        sources = {row.transaction_id for row in record["case"].transactions}
        if record["status"] == "superseded" or identifier not in sources:
            continue
        if record["outcome"] is not None:
            return record["outcome"].question
    return decisions[identifier].question if identifier in decisions else None


def _review_error(error: Exception) -> str:
    """
    Retain deliberately sanitised AI diagnostics without exposing inputs through general exception messages.
    """
    if isinstance(error, (ChatGPTError, ReviewBatchError)):
        return str(error)
    return f"Review preparation failed ({type(error).__name__}); no accounting change was saved."


def _source_groups(transactions: list, entries: list, answers: dict[UUID, ReviewAnswer]) -> list[set[UUID]]:
    """
    Connect complete current events, unique plausible transfer pairs and explicit UUID references in answers.

    Candidate transfer grouping supplies context to AI; it never permits posting by amount alone. Ambiguous
    candidates stay separate unless the user identifies a particular transaction or existing event UUID.
    """
    by_id = {row.transaction_id: row for row in transactions}
    neighbours = {identifier: {identifier} for identifier in by_id}
    event_sources = {entry.id: _entry_sources(entry) for entry in entries}
    for identifiers in event_sources.values():
        for identifier in identifiers:
            neighbours[identifier].update(identifiers)
    references = {}
    for row in transactions:
        reference = row.transfer_reference
        if reference is not None:
            references.setdefault(reference, []).append(row)
    candidates = {identifier: set() for identifier in by_id}
    for rows in references.values():
        for first in rows:
            for second in rows:
                if (
                    first.account_id != second.account_id and first.amount != 0
                    and first.amount == -second.amount and abs((first.date - second.date).days) <= 3
                ):
                    candidates[first.transaction_id].add(second.transaction_id)
    for identifier, matches in candidates.items():
        if len(matches) == 1:
            other = next(iter(matches))
            if len(candidates[other]) == 1:
                neighbours[identifier].add(other)
                neighbours[other].add(identifier)
    for identifier, answer in answers.items():
        tokens = re.findall(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", answer.human_review)
        for token in tokens:
            target = UUID(token)
            linked = {target} if target in by_id else event_sources.get(target, set())
            for other in linked:
                neighbours[identifier].add(other)
                neighbours[other].add(identifier)
    groups = []
    unseen = set(by_id)
    while unseen:
        group, pending = set(), {next(iter(unseen))}
        while pending:
            identifier = pending.pop()
            if identifier not in group:
                group.add(identifier)
                pending.update(neighbours[identifier] - group)
        unseen.difference_update(group)
        groups.append(group)
    return groups


def _entry_sources(entry) -> set[UUID]:
    """
    Return every imported source linked to a complete event.
    """
    return {line.transaction_id for line in entry.lines if line.transaction_id is not None}


def _latest_records(records: list[dict]) -> dict[UUID, dict]:
    """
    Choose the newest case per source; superseded cases prevent older failures from resurfacing.
    """
    return {
        row.transaction_id: record for record in records
        for row in record["case"].transactions
    }


def _postings_label(entry, accounts: dict, side: str) -> str:
    """
    Display each debit or credit account and amount without pretending a split has only one category.
    """
    if entry is None:
        return ""
    return "; ".join(
        f"{accounts[line.account_id].name}: {getattr(line, side):.2f}"
        + (f" [budget: {line.budget_category}]" if line.budget_category is not None else "")
        for line in entry.lines if getattr(line, side) > 0
    )


def _safe_text(value: str) -> str:
    """
    Prevent spreadsheet programs from evaluating exported text as a formula.
    """
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def _atomic_write(
    path: Path, content: bytes, *, expected_hash: str | None = None, check_current: bool = False,
) -> bool:
    """
    Replace a generated file after flushing it, checking for concurrent edits immediately before replacement.

    Returns:
        False when the destination changed during this run and must be preserved.
    """
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if check_current:
            current = sha256(path.read_bytes()).hexdigest() if path.exists() else None
            if current != expected_hash:
                return False
        os.replace(temporary, path)
        return True
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
