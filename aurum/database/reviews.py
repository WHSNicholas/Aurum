# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Database - Reviews                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum keeps monthly review answers and their accounting changes in its private SQLite database.

Saved snapshots connect each response to the accounting the user actually reviewed. Submitted
answers and reserved requests are retained independently of the journal, so an interrupted request or an
uncertain correction never removes valid postings. Repeated unchanged answers do not spend more tokens.

An accepted review replaces complete affected events in one transaction. Original statement rows keep their
UUIDs, descriptions, amounts and effective dates, while the audit stores the full before and after snapshots.
Reusable guidance becomes active only from accepted accounting. Separate fields on each review case explain
why guidance was saved, omitted or rejected, preserving the accounting review's outcome and timestamps.
Older accepted reviews can be assessed without reposting entries.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import json
import sqlite3
from uuid import UUID, uuid4

# Aurum imports
from aurum.database.journal import (
    _check_journal_sources, _ensure_source_row, _write_entry, load_journal_entries,
)
from aurum.database.transactions import from_sqlite_dollars, load_transactions
from aurum.finance.accounts import AccountRegistry
from aurum.finance.budget import BudgetSettings, budget_revision, validate_budget_labels
from aurum.finance.journal import preserve_account_investments, preserve_budget_categories
from aurum.database.memory import remember_text
from aurum.finance.review import Guidance, GuidanceResult, ReviewAnswer, ReviewCase, ReviewOutcome


# %% 1. Review Snapshots -------------------------------------------------------------------------------------
def review_revision(
    connection: sqlite3.Connection, transaction_ids, accounts: AccountRegistry,
) -> str:
    """
    Fingerprint current source facts and complete affected events, including other sources on those events.

    Display names and request timestamps do not affect the revision. Accounting changes, source changes and
    line descriptions do, so an answer cannot silently correct accounting that changed after the editor opened.
    """
    snapshot = _snapshot(connection, transaction_ids, accounts, include_journal=False)
    return _digest(snapshot["context"])


def review_display_versions(
    connection: sqlite3.Connection, accounts: AccountRegistry, transaction_ids,
    *, sources: dict | None = None, entries: list | None = None, baselines: dict | None = None,
) -> dict[UUID, dict[str, str]]:
    """
    Build desktop review revisions and answer tokens from one shared read of the saved accounting.

    Optional inputs must come from the caller's same read transaction. Complete linked events and original
    answer payloads retain the individual live checks' exact fingerprints. Saving still validates fresh
    accounting and answers inside its own write transaction; these display versions never authorise a save.
    """
    wanted = {UUID(str(identifier)) for identifier in transaction_ids}
    if not wanted:
        return {}
    if sources is None:
        sources = {item.transaction_id: item for item in load_transactions(connection)}
    if entries is None:
        entries = load_journal_entries(connection, accounts)
    if baselines is None:
        baselines = load_review_baselines(connection)
    by_source = {}
    for entry in entries:
        for line in entry.lines:
            if line.transaction_id in wanted:
                by_source.setdefault(line.transaction_id, []).append(entry)
    answers = {}
    for identifier, action_id, payload in connection.execute(
        "SELECT transaction_id, id, payload FROM review_actions WHERE kind = 'answer' "
        "ORDER BY created_at, rowid"
    ):
        identifier = UUID(identifier)
        if identifier in wanted:
            answers[identifier] = (action_id, payload)
    result = {}
    for identifier in wanted:
        snapshot = _snapshot(
            connection, [identifier], accounts, include_journal=False,
            sources=sources, entries=by_source.get(identifier, []),
        )
        result[identifier] = {
            "context_revision": _digest(snapshot["context"]),
            "answer_token": _digest({
                "answer": answers.get(identifier),
                "cleared": baselines.get(identifier, {}).get("human_review_cleared", False),
            }),
        }
    return result


def load_review_baselines(connection: sqlite3.Connection) -> dict[UUID, dict]:
    """
    Read saved response state, retaining historical baselines used for clearing and stale-answer checks.

    The existing storage kind remains unchanged so saved answers keep their identity without a migration.
    """
    return {
        UUID(identifier): json.loads(payload)
        for identifier, payload in connection.execute(
            "SELECT transaction_id, payload FROM review_actions WHERE kind = 'export'"
        )
    }


def load_review_answers(connection: sqlite3.Connection) -> dict[UUID, ReviewAnswer]:
    """
    Read the latest submitted free-text answer for each source, retaining the revision the user reviewed.
    """
    answers = {}
    for (payload,) in connection.execute(
        "SELECT payload FROM review_actions WHERE kind = 'answer' ORDER BY created_at, rowid"
    ):
        answer = ReviewAnswer.model_validate_json(payload)
        answers[answer.transaction_id] = answer
    return answers


def review_answer_token(connection: sqlite3.Connection, transaction_id: UUID) -> str:
    """
    Identify the saved answer and its cleared state so two editors cannot overwrite each other.
    """
    row = connection.execute(
        "SELECT id, payload FROM review_actions WHERE kind = 'answer' AND transaction_id = ? "
        "ORDER BY created_at DESC, rowid DESC LIMIT 1", (str(transaction_id),),
    ).fetchone()
    cleared = load_review_baselines(connection).get(transaction_id, {}).get("human_review_cleared", False)
    return _digest({"answer": row, "cleared": cleared})


def submit_review(
    connection: sqlite3.Connection, accounts: AccountRegistry, *,
    transaction_id: UUID | str, text: str, revision: str, answer_token: str,
) -> None:
    """
    Save an authoritative GUI answer only while both its accounting and saved text remain current.

    Saving does not call AI or change the journal. Saved response state retains its exact accounting
    revision and cleared status so another open editor cannot silently overwrite newer work.
    """
    _edit_review(
        connection, accounts, UUID(str(transaction_id)), revision, answer_token, text=text,
    )


def submit_reviews(
    connection: sqlite3.Connection, accounts: AccountRegistry, *, answers: list[dict],
) -> None:
    """
    Save a complete set of edited bank answers atomically, retaining every original stale-answer check.

    Read common accounting evidence once. A stale answer rejects the entire batch, so no local draft is
    silently consumed while another remains unsaved.
    """
    edits = []
    for payload in answers:
        if set(payload) != {"transaction_id", "revision", "answer_token", "text"}:
            raise ValueError("Each review answer needs its source, revision, answer token and text")
        answer = ReviewAnswer(
            transaction_id=payload["transaction_id"], revision=payload["revision"],
            human_review=payload["text"],
        )
        edits.append((answer.transaction_id, answer.revision, payload["answer_token"], answer.human_review))
    if not edits or len({item[0] for item in edits}) != len(edits):
        raise ValueError("Save at least one answer, with only one answer per transaction")
    _edit_reviews(connection, accounts, edits)


def discard_review(
    connection: sqlite3.Connection, accounts: AccountRegistry, *,
    transaction_id: UUID | str, revision: str, answer_token: str,
) -> None:
    """
    Clear an unprocessed answer without deleting its history or undoing any posted accounting.
    """
    _edit_review(connection, accounts, UUID(str(transaction_id)), revision, answer_token, text=None)


# %% 2. Review Requests --------------------------------------------------------------------------------------
def load_review_cases(connection: sqlite3.Connection) -> list[dict]:
    """
    Read saved requests and outcomes so Process can retry work and Review can display saved questions.
    """
    return [
        {
            "id": UUID(identifier), "revision": revision, "status": status,
            "case": ReviewCase.model_validate_json(payload),
            "outcome": ReviewOutcome.model_validate_json(outcome) if outcome else None,
            "error": error, "model": model, "skill_version": version,
        }
        for identifier, revision, status, payload, outcome, error, model, version in connection.execute(
            """
            SELECT id, revision, status, payload, outcome, error, model, skill_version
            FROM review_actions WHERE kind = 'case' ORDER BY created_at, rowid
            """
        )
    ]


def reserve_review_case(
    connection: sqlite3.Connection, case: ReviewCase, accounts: AccountRegistry,
    *, model: str, skill_version: str, retry_errors: bool = False,
    budget: BudgetSettings | None = None,
) -> bool:
    """
    Commit a current review request before inference, skipping unchanged completed or interrupted requests.

    A changed answer receives its own request even when the accounting has not changed. Retrying a failed
    or interrupted request requires retry_errors and the original saved case UUID. With that explicit flag,
    changed answers may supersede overlapping interrupted requests before reserving their replacement.
    """
    _require_idle(connection)
    case = ReviewCase.model_validate(case.model_dump())
    if any(line.investment is not None for entry in case.entries for line in entry.lines):
        raise ValueError("Edit investment corrections in their original Manual entry.")
    if not model.strip() or not skill_version.strip():
        raise ValueError("A review request needs a model and skill version")
    if case.budget_revision != budget_revision(budget):
        raise ValueError("Budget categories changed; refresh this request before retrying")
    key = _request_key(case)
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        # An existing UUID must still identify exactly the same case before it can be retried.
        existing = connection.execute(
            "SELECT id, status, payload FROM review_actions WHERE kind = 'case' AND id = ?", (str(case.id),)
        ).fetchone()
        if existing is not None:
            if ReviewCase.model_validate_json(existing[2]) != case:
                raise ValueError("Retry the exact saved review case rather than changing its payload")
        else:
            existing = connection.execute(
                "SELECT id, status, payload FROM review_actions WHERE kind = 'case' AND request_key = ?",
                (key,),
            ).fetchone()
        if existing:
            if existing[1] not in {"failed", "requesting"} or not retry_errors:
                return False
            if existing[0] != str(case.id):
                raise ValueError("Retry the original saved case rather than creating a new request UUID")
        snapshot = _check_case(connection, case, accounts)
        # Overlapping requests must not spend tokens on two competing versions of the same source group.
        source_ids = {item.transaction_id for item in case.transactions}
        for saved in load_review_cases(connection):
            if saved["id"] != case.id and saved["status"] == "requesting":
                if source_ids & {item.transaction_id for item in saved["case"].transactions}:
                    if not retry_errors:
                        raise ValueError("Another unfinished review request already includes these sources")
                    connection.execute(
                        """
                        UPDATE review_actions SET status = 'superseded', error = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        ("Replaced by a changed answer with explicit retry", _now(), str(saved["id"])),
                    )
        if existing:
            connection.execute(
                """
                UPDATE review_actions SET status = 'requesting', outcome = NULL, error = NULL,
                    model = ?, skill_version = ?, before_snapshot = ?, updated_at = ? WHERE id = ?
                """,
                (model, skill_version, _json(snapshot), _now(), str(case.id)),
            )
        else:
            connection.execute(
                """
                INSERT INTO review_actions (
                    id, kind, revision, request_key, status, payload, before_snapshot,
                    model, skill_version, created_at, updated_at
                ) VALUES (?, 'case', ?, ?, 'requesting', ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(case.id), case.revision, key, case.model_dump_json(), _json(snapshot),
                    model, skill_version, _now(), _now(),
                ),
            )
    return True


def fail_review_case(connection: sqlite3.Connection, case: ReviewCase, reason: str) -> None:
    """
    Record a failed or interrupted attempt without changing any journal rows or active guidance.
    """
    _require_idle(connection)
    if not reason.strip():
        raise ValueError("A failed review needs an explanation")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        _require_request(connection, case)
        connection.execute(
            "UPDATE review_actions SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (reason, _now(), str(case.id)),
        )


def save_review_outcome(
    connection: sqlite3.Connection, case: ReviewCase, outcome: ReviewOutcome, accounts: AccountRegistry,
    *, budget: BudgetSettings | None = None,
) -> None:
    """
    Save a review result, replacing complete affected events and activating guidance in one atomic commit.

    Every source must appear exactly once with its original account, signed amount, description and effective
    date. One-to-one corrections retain their event UUID; regrouped events receive new UUIDs. Source journal
    row IDs survive replacement. Validation, stale revisions or write failures roll back the whole change.
    """
    _require_idle(connection)
    case = ReviewCase.model_validate(case.model_dump())
    outcome = ReviewOutcome.model_validate(outcome.model_dump())
    if case.budget_revision != budget_revision(budget):
        raise ValueError("Budget categories changed after this review was prepared")
    if outcome.status == "apply":
        entries = preserve_budget_categories(outcome.entries, case.entries)
        entries = preserve_account_investments(entries, case.entries)
        validate_budget_labels(entries, accounts, budget, case.entries)
        outcome = ReviewOutcome.model_validate({**outcome.model_dump(), "entries": entries})
    if outcome.case_id != case.id:
        raise ValueError("The review outcome belongs to a different request")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        request = _require_request(connection, case)
        if (outcome.model, outcome.skill_version) != request:
            raise ValueError("Review provenance must match the reserved model and skill version")
        before = _check_case(connection, case, accounts)
        if outcome.status == "apply":
            _check_replacements(connection, case, outcome, accounts)
            _replace_entries(connection, case, outcome, accounts)
            if any(remember_text(answer.human_review) for answer in case.answers):
                outcome = outcome.model_copy(update={
                    "guidance": (), "guidance_errors": (),
                    "guidance_reason": "Explicit memory is retained in the saved human response.",
                })
            disposition = _activate_guidance(connection, case, outcome.guidance, accounts)
            if outcome.guidance or outcome.guidance_reason is not None or outcome.guidance_errors:
                result = GuidanceResult(
                    guidance=outcome.guidance, reason=outcome.guidance_reason, errors=outcome.guidance_errors,
                )
                _store_guidance_result(
                    connection, case.id, result, disposition, outcome.model, outcome.skill_version,
                )
            after = _snapshot(connection, [item.transaction_id for item in case.transactions], accounts)
            status = "applied"
        else:
            after = before
            status = "needs_review"
        connection.execute(
            """
            UPDATE review_actions SET status = ?, outcome = ?, before_snapshot = ?, after_snapshot = ?,
                error = NULL, updated_at = ? WHERE id = ?
            """,
            (
                status, outcome.model_dump_json(), _json(before), _json(after),
                outcome.updated_at.isoformat(), str(case.id),
            ),
        )


def load_active_guidance(connection: sqlite3.Connection) -> list[Guidance]:
    """
    Read validated private instructions whose supporting accounting reviews committed successfully.
    """
    return [
        Guidance(
            id=identifier, scope=scope, instruction=instruction,
            account_ids=json.loads(account_ids), supersedes=json.loads(supersedes),
        )
        for identifier, scope, instruction, account_ids, supersedes in connection.execute(
            """
            SELECT id, scope, instruction, account_ids, supersedes
            FROM categorisation_guidance WHERE active = 1 ORDER BY created_at, rowid
            """
        )
    ]


# %% 3. Guidance Assessments ---------------------------------------------------------------------------------
def list_guidance_cases(
    connection: sqlite3.Connection, accounts: AccountRegistry, *, retry_errors: bool = False,
) -> list[ReviewCase]:
    """
    Find accepted reviews whose accounting still applies and whose guidance has not been assessed.

    Return the original answers and source facts with the accepted entries. Summary text and display-name
    changes do not invalidate the evidence. Failed or interrupted assessments require explicit retry_errors.
    """
    states = {item["review_action_id"]: item["status"] for item in load_guidance_assessments(connection)}
    cases = []
    for (identifier,) in connection.execute(
        "SELECT id FROM review_actions WHERE kind = 'case' AND status = 'applied' ORDER BY created_at, rowid"
    ):
        status = states.get(UUID(identifier))
        if status is not None and not (retry_errors and status in {"failed", "requesting"}):
            continue
        try:
            cases.append(_accepted_guidance_case(connection, UUID(identifier), accounts))
        except ValueError:
            # Changed source facts, postings or later accepted answers no longer support this old case.
            continue
    return cases


def load_guidance_assessments(connection: sqlite3.Connection) -> list[dict]:
    """
    Read each accepted review's latest guidance assessment, including safe errors and activation decisions.
    """
    return [
        {
            "review_action_id": UUID(identifier), "status": status,
            "result": GuidanceResult.model_validate_json(result) if result else None,
            "disposition": json.loads(disposition) if disposition else None,
            "model": model, "skill_version": version, "error": error,
            "created_at": created, "updated_at": updated,
        }
        for identifier, status, result, disposition, model, version, error, created, updated in
        connection.execute(
            """
            SELECT id, guidance_status, guidance_result, guidance_disposition, guidance_model,
                   guidance_skill_version, guidance_error, guidance_created_at, guidance_updated_at
            FROM review_actions WHERE kind = 'case' AND guidance_status IS NOT NULL
            ORDER BY guidance_created_at, rowid
            """
        )
    ]


def reserve_guidance_case(
    connection: sqlite3.Connection, case: ReviewCase, *, model: str, skill_version: str,
    retry_errors: bool = False,
) -> bool:
    """
    Reserve a still-current accepted review before requesting guidance without changing its accounting.

    Completed assessments are skipped. Retrying a failed or interrupted request must be explicit; the
    caller must not retry a request that is still running elsewhere.
    """
    _require_idle(connection)
    case = ReviewCase.model_validate(case.model_dump())
    if not model.strip() or not skill_version.strip():
        raise ValueError("A guidance request needs a model and skill version")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if _accepted_guidance_case(connection, case.id) != case:
            raise ValueError("Guidance must use the exact accepted review context")
        existing = connection.execute(
            "SELECT guidance_status FROM review_actions WHERE kind = 'case' AND id = ?", (str(case.id),),
        ).fetchone()
        if existing[0] is not None and not (retry_errors and existing[0] in {"failed", "requesting"}):
            return False
        cursor = connection.execute(
            """
            UPDATE review_actions SET guidance_status = 'requesting', guidance_result = NULL,
                guidance_disposition = NULL, guidance_model = ?, guidance_skill_version = ?,
                guidance_error = NULL, guidance_created_at = COALESCE(guidance_created_at, ?),
                guidance_updated_at = ? WHERE kind = 'case' AND status = 'applied' AND id = ?
            """,
            (model, skill_version, _now(), _now(), str(case.id)),
        )
        if cursor.rowcount != 1:
            raise ValueError("Guidance requires an accepted accounting review")
    return True


def save_guidance_result(
    connection: sqlite3.Connection, case: ReviewCase, result: GuidanceResult, accounts: AccountRegistry,
    *, model: str, skill_version: str,
) -> None:
    """
    Save a reserved guidance assessment and valid instructions without changing accepted journal entries.

    Invalid optional candidates are recorded independently. A partial result can activate valid guidance
    while remaining failed for explicit retry; duplicates on a retry do not create another instruction.
    Explicitly tagged answers retain their editable memory without creating a separate inferred copy.
    """
    _require_idle(connection)
    case = ReviewCase.model_validate(case.model_dump())
    result = GuidanceResult.model_validate(result.model_dump())
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        request = connection.execute(
            """
            SELECT guidance_status, guidance_model, guidance_skill_version
            FROM review_actions WHERE kind = 'case' AND id = ?
            """,
            (str(case.id),),
        ).fetchone()
        if request != ("requesting", model, skill_version):
            raise ValueError("A guidance result must match its reserved request")
        if _accepted_guidance_case(connection, case.id, accounts) != case:
            raise ValueError("The accepted review evidence changed before guidance was saved")
        if any(remember_text(answer.human_review) for answer in case.answers):
            result = GuidanceResult(reason="Explicit memory is retained in the saved human response.")
        disposition = _activate_guidance(
            connection, case, result.guidance, accounts, retire_evidence=False,
        )
        _store_guidance_result(connection, case.id, result, disposition, model, skill_version)


def fail_guidance_case(connection: sqlite3.Connection, case_id: UUID, message: str) -> None:
    """
    Record a failed guidance-only request while retaining all accepted accounting and active guidance.
    """
    _require_idle(connection)
    if not message.strip():
        raise ValueError("A failed guidance request needs a reason")
    with connection:
        cursor = connection.execute(
            """
            UPDATE review_actions SET guidance_status = 'failed', guidance_error = ?, guidance_updated_at = ?
            WHERE kind = 'case' AND id = ? AND guidance_status = 'requesting'
            """,
            (message, _now(), str(case_id)),
        )
        if cursor.rowcount != 1:
            raise ValueError("This guidance request is not reserved")


# %% 4. Helpers ----------------------------------------------------------------------------------------------
def _edit_review(
    connection: sqlite3.Connection, accounts: AccountRegistry, identifier: UUID,
    revision: str, token: str, *, text: str | None,
) -> None:
    """
    Save a single answer or clearing marker through the same atomic validation as a batch.
    """
    _edit_reviews(connection, accounts, [(identifier, revision, token, text)])


def _edit_reviews(connection: sqlite3.Connection, accounts: AccountRegistry, edits: list[tuple]) -> None:
    """
    Compare all original revisions and persist answers together using one shared accounting snapshot.
    """
    _require_idle(connection)
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        sources = {row.transaction_id: row for row in load_transactions(connection)}
        entries = load_journal_entries(connection, accounts)
        baselines = load_review_baselines(connection)
        answers = load_review_answers(connection)
        cases = load_review_cases(connection)
        for identifier, revision, token, text in edits:
            _write_review_edit(
                connection, accounts, identifier, revision, token, text=text,
                sources=sources, entries=entries, baselines=baselines, answers=answers, cases=cases,
            )


def _write_review_edit(
    connection: sqlite3.Connection, accounts: AccountRegistry, identifier: UUID,
    revision: str, token: str, *, text: str | None, sources: dict, entries: list,
    baselines: dict, answers: dict, cases: list,
) -> None:
    """
    Validate and write one edit inside the caller's complete batch transaction.
    """
    answer = ReviewAnswer(
        transaction_id=identifier, revision=revision, human_review=text,
    ) if text is not None else None
    snapshot = _snapshot(connection, [identifier], accounts, include_journal=False,
                         sources=sources, entries=entries)
    if _digest(snapshot["context"]) != revision:
        raise ValueError("The accounting changed. Reload this review before saving your answer.")
    if review_answer_token(connection, identifier) != token:
        raise ValueError("A newer answer was saved. Reload this review before changing it.")
    baseline = dict(baselines.get(identifier, {}))
    previous = answers.get(identifier)
    if (
        answer is not None and previous is not None and text == previous.human_review
        and not baseline.get("human_review_cleared", False)
    ):
        return
    latest = next((
        item for item in reversed(cases)
        if any(row.transaction_id == identifier for row in item["case"].transactions)
    ), None)
    if latest and latest["status"] == "requesting":
        raise ValueError("Retry the interrupted review before changing or discarding its answer.")
    if answer is not None and previous != answer:
        connection.execute(
            """
            INSERT INTO review_actions (
                id, kind, transaction_id, revision, status, payload, created_at, updated_at
            ) VALUES (?, 'answer', ?, ?, 'submitted', ?, ?, ?)
            """,
            (str(uuid4()), str(identifier), revision, answer.model_dump_json(), _now(), _now()),
        )
    baseline["human_review_cleared"] = text is None
    connection.execute(
        """
        INSERT INTO review_actions (
            id, kind, transaction_id, status, payload, created_at, updated_at
        ) VALUES (?, 'export', ?, 'exported', ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
        """,
        (f"export:{identifier}", str(identifier), _json(baseline), _now(), _now()),
    )


def _snapshot(
    connection: sqlite3.Connection, transaction_ids, accounts: AccountRegistry,
    *, include_journal: bool = True, sources: dict | None = None, entries: list | None = None,
) -> dict:
    """
    Collect complete affected events and source facts, adding exact journal rows only for audit snapshots.

    Revision checks use the same accounting context without fetching journal metadata they do not compare.
    """
    wanted = {UUID(str(identifier)) for identifier in transaction_ids}
    if not wanted:
        raise ValueError("A review must include at least one source transaction")
    if sources is None:
        sources = {item.transaction_id: item for item in load_transactions(connection)}
    if not wanted <= sources.keys():
        raise ValueError("A review references an unknown source transaction")
    entries = [
        entry for entry in (load_journal_entries(connection, accounts) if entries is None else entries)
        if wanted & {line.transaction_id for line in entry.lines if line.transaction_id is not None}
    ]
    for entry in entries:
        wanted.update(line.transaction_id for line in entry.lines if line.transaction_id is not None)
    ordered_sources = [sources[identifier] for identifier in sorted(wanted, key=str)]
    entries.sort(key=lambda entry: str(entry.id))
    snapshot: dict = {"context": _context(ordered_sources, entries)}
    if not include_journal:
        return snapshot
    source_ids = {str(identifier) for identifier in wanted}
    entry_ids = {str(entry.id) for entry in entries}
    cursor = connection.execute("SELECT * FROM journal ORDER BY id")
    columns = [item[0] for item in cursor.description]
    source_column = columns.index("transaction_id")
    entry_column = columns.index("journal_entry_id")
    snapshot["journal"] = [
        dict(zip(columns, row)) for row in cursor
        if row[source_column] in source_ids or row[entry_column] in entry_ids
    ]
    return snapshot


def _check_case(
    connection: sqlite3.Connection, case: ReviewCase, accounts: AccountRegistry,
) -> dict:
    """
    Reject outdated answers, incomplete event groups and case facts that differ from the current ledger.
    """
    snapshot = _snapshot(connection, [item.transaction_id for item in case.transactions], accounts)
    # Manual entries retain one correction interface, including ownership of any linked statement evidence.
    from aurum.database.manual import manual_claims, manual_owner_ids

    protected = manual_claims(connection).keys() | manual_owner_ids(connection).keys()
    if {item.transaction_id for item in case.transactions} & protected:
        raise ValueError("These sources belong to a manual instruction; edit its original Manual entry")
    context = _context(case.transactions, case.entries)
    if snapshot["context"] != context or _digest(context) != case.revision:
        raise ValueError("This review is stale or omits part of an event; reload it before saving your Response")
    for answer in case.answers:
        if answer.revision != review_revision(connection, [answer.transaction_id], accounts):
            raise ValueError("An answer belongs to older accounting; reload the review before saving your Response")
    return snapshot


def _check_replacements(
    connection: sqlite3.Connection, case: ReviewCase, outcome: ReviewOutcome, accounts: AccountRegistry,
) -> None:
    """
    Validate source conservation, whole-event identity and all accounting before replacing any rows.
    """
    if any(
        line.investment is not None
        for entry in (*case.entries, *outcome.entries) for line in entry.lines
    ):
        raise ValueError("Edit investment corrections in their original Manual entry.")
    expected = {item.transaction_id for item in case.transactions}
    linked = [
        line.transaction_id for entry in outcome.entries for line in entry.lines
        if line.transaction_id is not None
    ]
    if len(linked) != len(set(linked)) or set(linked) != expected:
        raise ValueError("Replacement events must use the complete review source set exactly once")
    old_groups = {
        frozenset(line.transaction_id for line in entry.lines if line.transaction_id is not None): entry.id
        for entry in case.entries
    }
    old_ids = {entry.id for entry in case.entries}
    for entry in outcome.entries:
        source_ids = frozenset(line.transaction_id for line in entry.lines if line.transaction_id is not None)
        if not source_ids:
            raise ValueError("A review replacement cannot introduce an unrelated manual event")
        if source_ids in old_groups:
            if entry.id != old_groups[source_ids]:
                raise ValueError("A one-to-one correction must preserve its existing event UUID")
        elif entry.id in old_ids:
            raise ValueError("A regrouped or merged event must receive a new UUID")
        _check_journal_sources(connection, entry, accounts)


def _replace_entries(
    connection: sqlite3.Connection, case: ReviewCase, outcome: ReviewOutcome, accounts: AccountRegistry,
) -> None:
    """
    Replace counterpart lines while reusing original source rows inside the caller's write transaction.
    """
    for entry in case.entries:
        connection.execute(
            "DELETE FROM journal WHERE journal_entry_id = ? AND transaction_id IS NULL", (str(entry.id),)
        )
    for source in case.transactions:
        _ensure_source_row(connection, source.transaction_id, accounts)
        connection.execute(
            """
            UPDATE journal SET status = 'pending', journal_entry_id = NULL, line_number = NULL
            WHERE transaction_id = ?
            """,
            (str(source.transaction_id),),
        )
    for entry in outcome.entries:
        _write_entry(connection, entry, accounts)


def _activate_guidance(
    connection: sqlite3.Connection, case: ReviewCase,
    guidance: tuple[Guidance, ...], accounts: AccountRegistry, *, retire_evidence: bool = True,
) -> dict:
    """
    Activate valid instructions and explain every duplicate or rejected candidate.

    Accounting corrections retire earlier instructions based on the corrected sources. Guidance-only retries
    keep accepted evidence active, so repeating a partial assessment does not remove its useful result.
    """
    disposition: dict = {"saved": 0, "duplicates": 0, "rejected": 0, "messages": []}
    known_accounts = {account.id for account in accounts.accounts.values()}
    source_ids = {item.transaction_id for item in case.transactions}
    if retire_evidence:
        for identifier, payload in connection.execute(
            """
            SELECT guidance.id, action.payload FROM categorisation_guidance AS guidance
            JOIN review_actions AS action ON action.id = guidance.review_action_id WHERE guidance.active = 1
            """
        ).fetchall():
            supporting = ReviewCase.model_validate_json(payload)
            if source_ids & {item.transaction_id for item in supporting.transactions}:
                connection.execute(
                    "UPDATE categorisation_guidance SET active = 0, retired_by_action_id = ? WHERE id = ?",
                    (str(case.id), identifier),
                )
    for number, item in enumerate(guidance, start=1):
        if not set(item.account_ids) <= known_accounts:
            disposition["rejected"] += 1
            disposition["messages"].append(
                f"Guidance {number}: an account reference is no longer configured."
            )
            continue
        active = load_active_guidance(connection)
        eligible = {
            UUID(row[0]) for row in connection.execute(
                "SELECT id FROM categorisation_guidance WHERE active = 1 OR retired_by_action_id = ?",
                (str(case.id),),
            )
        }
        if connection.execute(
            "SELECT 1 FROM categorisation_guidance WHERE id = ?", (str(item.id),)
        ).fetchone():
            disposition["duplicates"] += 1
            disposition["messages"].append(
                f"Guidance {number}: this instruction identifier is already stored."
            )
            continue
        same_scope = [
            saved for saved in active if saved.scope.casefold().strip() == item.scope.casefold().strip()
        ]
        duplicates = [
            saved for saved in same_scope
            if saved.instruction == item.instruction and set(saved.account_ids) == set(item.account_ids)
        ]
        if duplicates and not item.supersedes:
            disposition["duplicates"] += 1
            disposition["messages"].append(
                f"Guidance {number}: an identical active instruction already exists."
            )
            continue
        if not set(item.supersedes) <= eligible or any(
            saved.id not in item.supersedes for saved in same_scope
        ):
            for saved in same_scope:
                if saved not in duplicates:
                    connection.execute(
                        """
                        UPDATE categorisation_guidance SET active = 0, retired_by_action_id = ? WHERE id = ?
                        """,
                        (str(case.id), str(saved.id)),
                    )
            disposition["rejected"] += 1
            disposition["messages"].append(
                f"Guidance {number}: conflicting scope or invalid supersession references; not activated."
            )
            continue
        connection.execute(
            """
            INSERT INTO categorisation_guidance (
                id, scope, instruction, account_ids, supersedes, active, review_action_id, created_at
            ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                str(item.id), item.scope, item.instruction,
                _json([str(identifier) for identifier in item.account_ids]),
                _json([str(identifier) for identifier in item.supersedes]), str(case.id), _now(),
            ),
        )
        disposition["saved"] += 1
        for identifier in item.supersedes:
            connection.execute(
                """
                UPDATE categorisation_guidance SET active = 0, superseded_by = ?, retired_by_action_id = ?
                WHERE id = ?
                """,
                (str(item.id), str(case.id), str(identifier)),
            )
    return disposition


def _accepted_guidance_case(
    connection: sqlite3.Connection, case_id: UUID, accounts: AccountRegistry | None = None,
) -> ReviewCase:
    """
    Rebuild accepted evidence only while source facts and complete posted accounting still match it.

    The original revision and answers remain provenance, while entries come from the accepted outcome.
    Ignore display labels and processing metadata when comparing current postings.
    """
    saved = connection.execute(
        """
        SELECT status, payload, outcome, updated_at, rowid
        FROM review_actions WHERE kind = 'case' AND id = ?
        """,
        (str(case_id),),
    ).fetchone()
    if saved is None or saved[0] != "applied" or saved[2] is None:
        raise ValueError("Guidance requires an accepted accounting review")
    original = ReviewCase.model_validate_json(saved[1])
    outcome = ReviewOutcome.model_validate_json(saved[2])
    if outcome.case_id != original.id or outcome.status != "apply":
        raise ValueError("The saved outcome must belong to its accepted review")
    source_ids = {item.transaction_id for item in original.transactions}
    current_sources = {item.transaction_id: item for item in load_transactions(connection)}
    if any(current_sources.get(item.transaction_id) != item for item in original.transactions):
        raise ValueError("Accepted guidance evidence has changed source facts")
    accepted_order = (datetime.fromisoformat(saved[3]), saved[4])
    for payload, updated, row_number in connection.execute(
        "SELECT payload, updated_at, rowid FROM review_actions WHERE kind = 'case' AND status = 'applied'"
    ):
        if (datetime.fromisoformat(updated), row_number) <= accepted_order:
            continue
        later = ReviewCase.model_validate_json(payload)
        if source_ids & {item.transaction_id for item in later.transactions}:
            raise ValueError("A later accepted review replaces this guidance evidence")
    for entry in outcome.entries:
        if accounts is not None:
            _check_journal_sources(connection, entry, accounts)
        expected = [
            (
                number, entry.date.isoformat(), str(line.account_id),
                format(line.debit, ".2f"), format(line.credit, ".2f"),
                str(line.transaction_id) if line.transaction_id is not None else None,
                line.description, line.effective_date.isoformat() if line.effective_date else None,
                str(line.investment.security_id) if line.investment is not None else None,
                line.investment.units if line.investment is not None else None,
                line.investment.unit_price if line.investment is not None else None,
                line.investment.fees if line.investment is not None else None,
            )
            for number, line in enumerate(entry.lines, start=1)
        ]
        current = [
            (number, date, account, format(from_sqlite_dollars(debit), ".2f"),
             format(from_sqlite_dollars(credit), ".2f"), source, description, effective, security,
             Decimal(units) if units is not None else None,
             Decimal(price) if price is not None else None,
             Decimal(fees) if fees is not None else None)
            for number, date, account, debit, credit, source, description, effective, security, units, price,
            fees in connection.execute(
                """
                SELECT line_number, date, account_id, debit, credit, transaction_id,
                       description, effective_date, security_id, units, unit_price, fees
                FROM journal WHERE status = 'posted' AND journal_entry_id = ? ORDER BY line_number
                """,
                (str(entry.id),),
            )
        ]
        if current != expected:
            raise ValueError("Accepted guidance evidence no longer matches the posted accounting")
    return ReviewCase.model_validate({**original.model_dump(), "entries": outcome.entries})


def _store_guidance_result(
    connection: sqlite3.Connection, case_id: UUID, result: GuidanceResult, disposition: dict,
    model: str, skill_version: str,
) -> None:
    """
    Update only the case's guidance assessment fields within the caller's transaction.

    Optional validation errors and rejected candidates remain retryable without undoing valid guidance.
    """
    failed = bool(result.errors or disposition["rejected"])
    messages = list(result.errors) + (disposition["messages"] if disposition["rejected"] else [])
    cursor = connection.execute(
        """
        UPDATE review_actions SET guidance_status = ?, guidance_result = ?, guidance_disposition = ?,
            guidance_model = ?, guidance_skill_version = ?, guidance_error = ?,
            guidance_created_at = COALESCE(guidance_created_at, ?), guidance_updated_at = ?
        WHERE kind = 'case' AND id = ?
        """,
        (
            "failed" if failed else "completed", result.model_dump_json(), _json(disposition),
            model, skill_version, "; ".join(messages) if failed else None, _now(), _now(), str(case_id),
        ),
    )
    if cursor.rowcount != 1:
        raise ValueError("A guidance assessment must belong to a saved review case")


def _context(transactions, entries) -> dict:
    """
    Canonicalise accounting values without changing revisions for entries predating investment details.
    """
    context = {"transactions": [], "entries": []}
    for item in sorted(transactions, key=lambda item: str(item.transaction_id)):
        fields = item.model_dump(mode="json")
        fields["amount"] = format(item.amount, ".2f")
        fields["statement_balance"] = (
            format(item.statement_balance, ".2f") if item.statement_balance is not None else None
        )
        context["transactions"].append(fields)
    for entry in sorted(entries, key=lambda entry: str(entry.id)):
        fields = entry.model_dump(mode="json")
        for line, values in zip(entry.lines, fields["lines"]):
            values["debit"] = format(line.debit, ".2f")
            values["credit"] = format(line.credit, ".2f")
            if line.account_investment is not None and line.account_investment.cost_basis is not None:
                values["account_investment"]["cost_basis"] = format(line.account_investment.cost_basis, ".2f")
            if line.investment is None:
                # Older saved revisions did not contain an investment field.
                values.pop("investment", None)
            else:
                for name in ("units", "unit_price", "fees"):
                    value = getattr(line.investment, name)
                    if value is not None:
                        text = format(value, "f")
                        if value == 0:
                            text = "0"
                        elif "." in text:
                            text = text.rstrip("0").rstrip(".")
                        values["investment"][name] = text
        context["entries"].append(fields)
    return context


def _require_request(connection: sqlite3.Connection, case: ReviewCase) -> tuple[str, str]:
    """
    Require the exact saved case to still be reserved before accepting its response or failure.
    """
    row = connection.execute(
        "SELECT status, payload, model, skill_version FROM review_actions WHERE kind = 'case' AND id = ?",
        (str(case.id),),
    ).fetchone()
    if row is None or row[0] != "requesting" or ReviewCase.model_validate_json(row[1]) != case:
        raise ValueError("This exact review case has not been reserved or has already finished")
    return row[2], row[3]


def _request_key(case: ReviewCase) -> str:
    """
    Identify the same accounting context and answers independently of a newly generated case UUID.
    """
    return _digest({
        "revision": case.revision,
        **({"budget_revision": case.budget_revision} if case.budget_revision is not None else {}),
        "sources": sorted(str(item.transaction_id) for item in case.transactions),
        "answers": [
            answer.model_dump(mode="json")
            for answer in sorted(case.answers, key=lambda answer: str(answer.transaction_id))
        ],
    })


def _require_idle(connection: sqlite3.Connection) -> None:
    """
    Avoid accidentally committing a caller's unrelated pending write.
    """
    if connection.in_transaction:
        raise ValueError("Finish the pending database write before saving review state")


def _now() -> str:
    """
    Return an aware UTC timestamp for review storage metadata.
    """
    return datetime.now(UTC).isoformat()


def _json(value) -> str:
    """
    Encode a stable, compact JSON snapshot while retaining original Unicode wording.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value) -> str:
    """
    Produce the stable SHA-256 identity used for accounting revisions and duplicate requests.
    """
    return sha256(_json(value).encode("utf-8")).hexdigest()
