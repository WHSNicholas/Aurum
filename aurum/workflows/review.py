# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Workflows - Review                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum processes plain-language corrections saved through the desktop Review page.

Each statement row appears once, including both observations of a transfer and transactions already posted.
Only the saved human response is read as an instruction. SQLite keeps the accounting facts, saved answers
and outcomes. Related source rows are reviewed together so a correction replaces complete events.

Answers carry the accounting revision shown when the response was written. Changed or stale accounting cannot
be silently overwritten, and unchanged answers are never sent again. Private learned guidance is also exported
as readable Markdown in the workspace AI directory.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections import Counter
from dataclasses import dataclass, field
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
from aurum.database.memory import load_explicit_memory, remember_text
from aurum.database.reviews import (
    fail_guidance_case, list_guidance_cases, load_guidance_assessments, reserve_guidance_case,
    save_guidance_result,
    fail_review_case, load_active_guidance, load_review_answers, load_review_cases, load_review_baselines,
    reserve_review_case, review_revision, save_review_outcome,
)
from aurum.finance.budget import BudgetSettings, budget_revision
from aurum.finance.reports import ReportSettings
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.review import GuidanceResult, ReviewAnswer, ReviewCase

# %% 1. Saved Reviews ----------------------------------------------------------------------------------------
@dataclass
class ReviewSession:
    """
    Keep pending cases, protected sources and local questions for one desktop processing run.
    """

    cases: list[ReviewCase] = field(default_factory=list)
    blocked: set[UUID] = field(default_factory=set)
    cleared: set[UUID] = field(default_factory=set)
    messages: dict[UUID, str] = field(default_factory=dict)


def prepare_reviews(
    connection: sqlite3.Connection, accounts: AccountRegistry, *,
    retry_errors: bool = False,
    budget: BudgetSettings | None = None,
) -> ReviewSession:
    """
    Build complete review cases from saved answers and their current accounting context.
    """
    session = ReviewSession()
    session.cleared = {
        identifier for identifier, baseline in load_review_baselines(connection).items()
        if baseline.get("human_review_cleared", False)
    }
    answers = load_review_answers(connection)
    transactions = load_transactions(connection)
    by_id = {row.transaction_id: row for row in transactions}
    entries = load_journal_entries(connection, accounts)
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
    groups = _source_groups(transactions, entries, active_answers, accounts)
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
                    "your Response to submit it again."
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
    cancelled: Callable[[], bool] | None = None,
    report_settings: ReportSettings | None = None,
) -> None:
    """
    Apply changed human answers before ordinary categorisation, retaining valid accounting on any failure.

    Independent cases share a compact request. Invalid results fail only their own cases; valid replacements
    commit independently. Preserve reported usage even when validation fails, and save safe diagnostic details
    for explicit retries. A connection or provider failure stops further review batches in this run.
    """
    report = progress or (lambda message: None)
    baselines = load_review_baselines(connection)
    records = load_review_cases(connection)
    decisions = load_decisions(connection)
    questions = {
        answer.transaction_id: _answer_question(
            answer.transaction_id, baselines.get(answer.transaction_id, {}), records, decisions,
        )
        for case in session.cases for answer in case.answers
    }
    for start in range(0, len(session.cases), MAX_REVIEW_BATCH_SIZE):
        if cancelled and cancelled():
            return
        batch = []
        for case in session.cases[start:start + MAX_REVIEW_BATCH_SIZE]:
            if any(line.investment is not None for entry in case.entries for line in entry.lines):
                for transaction in case.transactions:
                    session.messages[transaction.transaction_id] = (
                        "Edit this investment in its original Manual entry. "
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
                        "Refresh the workspace, then use Process to retry an interrupted request."
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
                explicit_memory=load_explicit_memory(connection),
                questions=questions, budget=budget, report_settings=report_settings,
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
    cancelled: Callable[[], bool] | None = None,
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
        if cancelled and cancelled():
            return
        batch = [
            case for case in cases[start:start + MAX_REVIEW_BATCH_SIZE]
            if reserve_guidance_case(
                connection, case, model=model, skill_version=skill.version, retry_errors=retry_errors,
            )
        ]
        if not batch:
            continue
        inferred = []
        for case in batch:
            if not any(remember_text(answer.human_review) for answer in case.answers):
                inferred.append(case)
                continue
            try:
                save_guidance_result(
                    connection, case,
                    GuidanceResult(reason="Explicit memory is retained in the saved human response."),
                    accounts, model=model, skill_version=skill.version,
                )
            except (ValueError, sqlite3.Error):
                fail_guidance_case(connection, case.id, "Explicit memory assessment could not finish.")
            else:
                counts["guidance_assessed"] += 1
        batch = inferred
        if not batch:
            continue
        usage = {}
        try:
            client = client_provider()
            report(f"AI: learning guidance from {len(batch)} accepted reviews with {model}...")
            counts["requests"] += 1
            results, failures, usage = learn_guidance_batch(
                client, batch, accounts, guidance=load_active_guidance(connection), skill=skill,
                explicit_memory=load_explicit_memory(connection),
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


def export_guidance(connection: sqlite3.Connection, path: Path) -> None:
    """
    Show active guidance and each learning outcome; SQLite remains the authoritative private history.
    """
    lines = [
        "# Categorisation guidance", "",
        "Generated from saved human inputs and accepted reviews in SQLite. "
        "Edit the original response or instruction to change an explicit memory note.",
        "",
    ]
    lines.extend(("## Explicit memories", ""))
    for item in load_explicit_memory(connection):
        lines.extend((f"### {item['source_kind']} {item['source_id']}", "", item["text"], ""))
    lines.extend(("## Learned guidance", ""))
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
    Recover the saved question accompanying a response, retaining older database baselines.

    Failed requests have no new question, so look back to the latest completed review outcome before
    falling back to the original categorisation decision.
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


def _source_groups(
    transactions: list, entries: list, answers: dict[UUID, ReviewAnswer], accounts: AccountRegistry,
) -> list[set[UUID]]:
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
    bank_owners = {}
    for account in accounts.accounts.values():
        if account.account_type == AccountType.ASSET and account.bank_details is not None:
            details = account.bank_details
            bank_owners.setdefault((details.bsb, details.account_number), set()).add(account.id)
    within_mentions, within_routes = set(), set()
    for row in transactions:
        if re.search(r"\bTRANSFER\s+WITHIN\b", row.description, re.IGNORECASE):
            within_mentions.add(row.transaction_id)
            route = re.fullmatch(
                r"TRANSFER\s+WITHIN\s+([0-9]{6})\s*-\s*([0-9]+)\s+#[0-9]+\s*",
                row.description, re.IGNORECASE,
            )
            if route is not None and bank_owners.get((route[1], route[2])) == {row.account_id}:
                within_routes.add(row.transaction_id)
        reference = row.transfer_reference
        if reference is not None:
            references.setdefault(reference, []).append(row)
    candidates = {identifier: set() for identifier in by_id}
    for rows in references.values():
        for first in rows:
            for second in rows:
                if first.transaction_id == second.transaction_id or first.amount == 0:
                    continue
                if first.amount != -second.amount or abs((first.date - second.date).days) > 3:
                    continue
                if first.account_id == second.account_id and (
                    first.date != second.date
                    or not {first.transaction_id, second.transaction_id} & within_mentions
                ):
                    continue
                candidates[first.transaction_id].add(second.transaction_id)
    for identifier, matches in candidates.items():
        if len(matches) == 1:
            other = next(iter(matches))
            if (
                by_id[identifier].account_id == by_id[other].account_id
                and not {identifier, other} <= within_routes
            ):
                continue
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


def _atomic_write(path: Path, content: bytes) -> None:
    """
    Replace readable guidance only after its complete contents have been flushed to disk.
    """
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
