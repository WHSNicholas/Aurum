# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Database - Manual                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum keeps manual instructions and their revisions beside the journal they create.

A durable request is saved before contacting AI. Accepted results replace whole events and save their audit
history in one database transaction; questions and failures leave existing accounting untouched. Stable manual
identities connect later CSV corrections to their original events, including events without bank sources.

Candidate statement rows are protected while an instruction needs attention. Bank reviews cannot replace
manual-owned events, and manual corrections retire review guidance whose accounting evidence they supersede.
Virtual savings splits reuse this review history but save only in savings_allocations, without postings.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import json
import re
import sqlite3
from uuid import UUID, uuid4

# Aurum imports
from aurum.database.database import MANUAL_COLUMNS
from aurum.database.journal import (
    _check_investment_securities, _check_journal_sources, _ensure_source_row, _write_entry,
    load_journal_entries,
)
from aurum.database.reviews import _context, _digest, _json, _now, _require_idle, load_review_cases
from aurum.database.transactions import load_transactions
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.budget import BudgetSettings, budget_revision, validate_budget_labels
from aurum.finance.journal import preserve_budget_categories
from aurum.finance.investments import calculate_investment
from aurum.finance.securities import SecurityRegistry
from aurum.database.prices import load_quote
from aurum.finance.manual import ManualCase, ManualInput, ManualOutcome
from aurum.database.savings import load_savings_allocations, savings_context, save_savings_allocation


# %% 1. Manual Submissions -----------------------------------------------------------------------------------
def load_manual_entries(connection: sqlite3.Connection) -> list[dict]:
    """
    Read the latest revision of each manual instruction, retaining its current posted event link.
    """
    cursor = connection.execute("SELECT " + ", ".join(MANUAL_COLUMNS) + " FROM manual_entries ORDER BY rowid")
    latest = {}
    for values in cursor:
        record = dict(zip(MANUAL_COLUMNS, values))
        record["input_revision"] = UUID(record.pop("id"))
        record["manual_id"] = UUID(record["manual_id"])
        record["submission"] = ManualInput.model_validate_json(record["submission"])
        payload = record.pop("case_payload")
        record["case"] = ManualCase.model_validate_json(payload) if payload else None
        record["outcome"] = (
            ManualOutcome.model_validate_json(record["outcome"]) if record["outcome"] else None
        )
        record["investment_proposal"] = (
            json.loads(record["investment_proposal"]) if record["investment_proposal"] else None
        )
        record["journal_entry_id"] = UUID(record["journal_entry_id"]) if record["journal_entry_id"] else None
        record["context_revision"] = record["submission"].context_revision
        previous = latest.get(record["manual_id"], {})
        record["is_savings"] = (
            previous.get("is_savings", False)
            or record["case"] is not None and record["case"].savings_context is not None
            or record["outcome"] is not None and record["outcome"].event_kind == "savings"
        )
        latest[record["manual_id"]] = record
    return list(latest.values())


def submit_manual_input(
    connection: sqlite3.Connection, submission: ManualInput, accounts: AccountRegistry,
    *, retry_errors: bool = False,
) -> dict:
    """
    Append a changed instruction only when its CSV revision still describes the current accounting.

    Unchanged text remains unchanged even when its exported context was refreshed. Changing an interrupted
    request requires explicit retry_errors; its reserved history is retained as superseded.
    """
    _require_idle(connection)
    submission = ManualInput.model_validate(submission.model_dump())
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        saved = _latest(connection, submission.id)
        if saved and saved["status"] != "withdrawn" and (
            _input_values(saved["submission"]) == _input_values(submission)
        ):
            return saved
        if saved:
            if saved["status"] == "requesting" and not retry_errors:
                raise ValueError("Retry the interrupted manual request before replacing its instruction")
            if not _matches_context_revision(connection, saved, submission.context_revision, accounts):
                raise ValueError(
                    "This manual CSV is stale; refresh its accounting before changing the instruction"
                )
            if saved["status"] == "requesting":
                connection.execute(
                    """
                    UPDATE manual_entries SET status = 'superseded', error = ?, updated_at = ? WHERE id = ?
                    """,
                    (
                        "Replaced by changed instructions with explicit retry", _now(),
                        str(saved["input_revision"]),
                    ),
                )
        connection.execute(
            """
            INSERT INTO manual_entries (
                id, manual_id, submission, status, journal_entry_id, question, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?)
            """,
            (
                str(uuid4()), str(submission.id), submission.model_dump_json(),
                str(saved["journal_entry_id"]) if saved and saved["journal_entry_id"] else None,
                (saved["question"] or saved["error"]) if saved else None,
                _now(), _now(),
            ),
        )
    result = _latest(connection, submission.id)
    if result is None:
        raise ValueError("The saved manual instruction could not be read")
    return result


def manual_context_revision(
    connection: sqlite3.Connection, manual_id: UUID, accounts: AccountRegistry,
) -> str:
    """
    Identify the latest instruction and the complete accounting currently shown in its CSV row.
    """
    saved = _latest(connection, manual_id)
    if saved is None:
        return ""
    sources = set(saved["submission"].source_transactions)
    if saved["case"] is not None:
        sources.update(item.transaction_id for item in saved["case"].transactions)
    snapshot = _live_context(connection, sources, {saved["journal_entry_id"]}, accounts)
    snapshot = _with_savings(connection, manual_id, snapshot)
    return _digest({"input_revision": str(saved["input_revision"]), "context": snapshot})


def manual_case_revision(
    connection: sqlite3.Connection, submission: ManualInput, transactions, entries,
    accounts: AccountRegistry,
) -> str:
    """
    Fingerprint candidate facts and complete events, rejecting stale or incomplete context.
    """
    expected = _context(transactions, entries)
    actual = _live_context(
        connection, {item.transaction_id for item in transactions}, {entry.id for entry in entries}, accounts,
    )
    if actual != expected:
        raise ValueError("Manual candidates are stale or omit part of an existing event")
    actual = _with_savings(connection, submission.id, actual)
    return _digest({"submission": _input_values(submission), "context": actual})


def manual_owner_ids(connection: sqlite3.Connection) -> dict[UUID, UUID]:
    """
    Map posted source transactions to the manual instruction that owns their current event.
    """
    owners = {}
    for record in load_manual_entries(connection):
        if record["journal_entry_id"] is None:
            continue
        for (source,) in connection.execute(
            "SELECT transaction_id FROM journal WHERE journal_entry_id = ? AND transaction_id IS NOT NULL",
            (str(record["journal_entry_id"]),),
        ):
            owners[UUID(source)] = record["manual_id"]
    return owners


def manual_claims(connection: sqlite3.Connection) -> dict[UUID, UUID]:
    """
    Protect unresolved candidate evidence; completed manual ownership is checked separately.
    """
    claims = {}
    for record in load_manual_entries(connection):
        if record["status"] not in {"pending", "requesting", "needs_review", "failed", "unsupported"}:
            continue
        sources = set(record["submission"].source_transactions)
        if record["case"] is not None:
            sources.update(item.transaction_id for item in record["case"].transactions)
        for identifier in sources:
            claims.setdefault(identifier, record["manual_id"])
    return claims


def release_manual_claims(
    connection: sqlite3.Connection, manual_id: UUID, accounts: AccountRegistry | None = None,
    *, context_revision: str | None = None,
) -> None:
    """
    Withdraw a cleared instruction's unresolved candidates without undoing postings or erasing history.

    An in-flight request cannot be withdrawn silently. Restoring a withdrawn instruction creates a new
    revision, while current posted event ownership stays with the original manual identity.
    """
    _require_idle(connection)
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        saved = _latest(connection, manual_id)
        if saved is None or saved["status"] in {"applied", "withdrawn"}:
            return
        if context_revision is not None:
            if accounts is None:
                raise ValueError("Checking a cleared manual instruction requires its account registry")
            if not _matches_context_revision(connection, saved, context_revision, accounts):
                raise ValueError(
                    "This cleared manual instruction is stale; refresh its CSV before withdrawing it"
                )
        if saved["status"] == "requesting":
            raise ValueError("Retry the interrupted manual request before withdrawing its instruction")
        connection.execute(
            "UPDATE manual_entries SET status = 'withdrawn', updated_at = ? WHERE id = ?",
            (_now(), str(saved["input_revision"])),
        )


# %% 2. Manual Requests --------------------------------------------------------------------------------------
def defer_manual_case(
    connection: sqlite3.Connection, case: ManualCase, accounts: AccountRegistry, question: str,
    *, status: str = "needs_review",
) -> None:
    """
    Save a local question or unsupported explanation without making an AI request or changing postings.
    """
    _require_idle(connection)
    if status not in {"needs_review", "unsupported"} or not question.strip():
        raise ValueError("A deferred manual instruction needs a question or unsupported explanation")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        saved = _check_case(connection, case, accounts)
        if saved["status"] == "requesting":
            raise ValueError("An interrupted request needs explicit retry before its question can change")
        connection.execute(
            """
            UPDATE manual_entries SET status = ?, case_payload = ?, revision = ?, question = ?,
                error = NULL, updated_at = ? WHERE id = ?
            """,
            (status, case.model_dump_json(), case.revision, question, _now(), str(saved["input_revision"])),
        )


def manual_needs_processing(
    saved: dict, *, skill_version: str, retry_errors: bool = False,
    catalogue_revision: str | None = None, budget_version: str | None = None,
) -> bool:
    """
    Reuse one eligibility rule for CSV preparation and atomic request reservation.

    Investment questions are reconsidered once after the account/security catalogue changes. The saved case
    carries its catalogue version, including questions without a priced proposal; no schema change is needed.
    A changed budget catalogue alone needs an edited instruction or explicit retry before another AI request.
    """
    status = saved["status"]
    if status == "pending" or (
        status == "needs_review" and saved["outcome"] is None and saved["model"] is None
    ):
        return True
    previous: ManualCase | None = saved["case"]
    outcome: ManualOutcome | None = saved["outcome"]
    if (
        status == "needs_review" and outcome is not None and outcome.event_kind == "investment"
        and catalogue_revision is not None
        and (previous is None or previous.budget_revision == budget_version)
        and (previous is None or previous.catalogue_revision != catalogue_revision)
    ):
        return True
    return retry_errors and (
        status in {"failed", "requesting"}
        or (status == "unsupported" and saved["skill_version"] != skill_version)
        or (status == "needs_review" and saved["investment_proposal"] is not None)
    )


def reserve_manual_case(
    connection: sqlite3.Connection, case: ManualCase, accounts: AccountRegistry,
    *, model: str, skill_version: str, retry_errors: bool = False,
    budget: BudgetSettings | None = None,
) -> bool:
    """
    Reserve a current instruction before inference, skipping completed or unchanged unresolved attempts.
    """
    _require_idle(connection)
    case = ManualCase.model_validate(case.model_dump())
    if case.budget_revision != budget_revision(budget):
        raise ValueError("Budget categories changed; refresh this manual request before retrying")
    if not model.strip() or not skill_version.strip():
        raise ValueError("A manual request needs a model and skill version")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        saved = _check_case(connection, case, accounts)
        if case.savings_context is not None:
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'manual_entries'"
            ).fetchone()[0]
            if "'$.savings'" not in schema:
                raise ValueError(
                    "This database requires the manual-entry constraint update before savings allocations "
                    "can be saved. No AI request was sent; existing accounting is unchanged."
                )
            if budget is None or savings_context(
                connection, case.submission.id, case.submission.date, accounts, budget,
            ).revision != case.savings_context.revision:
                raise ValueError("Monthly savings changed before this request; refresh its context")
        if not manual_needs_processing(
            saved, skill_version=skill_version, retry_errors=retry_errors,
            catalogue_revision=case.catalogue_revision, budget_version=case.budget_revision,
        ):
            return False
        _check_claims(connection, case)
        connection.execute(
            """
            UPDATE manual_entries SET status = 'requesting', case_payload = ?, revision = ?,
                before_snapshot = ?, outcome = NULL, question = NULL, error = NULL,
                model = ?, skill_version = ?, updated_at = ? WHERE id = ?
            """,
            (
                case.model_dump_json(), case.revision, _json(_audit_snapshot(connection, case)),
                model, skill_version, _now(), str(saved["input_revision"]),
            ),
        )
    return True


def fail_manual_case(connection: sqlite3.Connection, case: ManualCase, reason: str) -> None:
    """
    Record a failed attempt without changing accounting or discarding the saved request.
    """
    _require_idle(connection)
    if not reason.strip():
        raise ValueError("A failed manual request needs an explanation")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        saved = _require_request(connection, case)
        connection.execute(
            "UPDATE manual_entries SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
            (reason, _now(), str(saved["input_revision"])),
        )



def save_investment_proposal(
    connection: sqlite3.Connection, case: ManualCase, outcome: ManualOutcome, accounts: AccountRegistry,
    catalogue_revision: str,
) -> None:
    """
    Persist a validated interpretation before any market request, without changing accounting.

    A retry can reuse this proposal only for the same case, model, skill and configuration. Changed human
    instructions receive a new input revision and cannot inherit this cache.
    """
    _require_idle(connection)
    if outcome.status != "pricing" or outcome.manual_id != case.submission.id:
        raise ValueError("Only a validated investment interpretation can be cached for this instruction")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        saved = _require_request(connection, case)
        _check_case(connection, case, accounts)
        if (outcome.model, outcome.skill_version) != (saved["model"], saved["skill_version"]):
            raise ValueError("The investment proposal does not match the reserved request")
        connection.execute(
            "UPDATE manual_entries SET investment_proposal = ?, updated_at = ? WHERE id = ?",
            (
                _json({
                    "case_revision": case.revision, "catalogue_revision": catalogue_revision,
                    "outcome": outcome.model_dump(mode="json"),
                }),
                _now(), str(saved["input_revision"]),
            ),
        )


def save_manual_outcome(
    connection: sqlite3.Connection, case: ManualCase, outcome: ManualOutcome, accounts: AccountRegistry,
    *, securities: SecurityRegistry | None = None, budget: BudgetSettings | None = None,
) -> None:
    """
    Save a result and complete accounting replacement together, preserving original bank evidence.

    One-to-one corrections keep their event UUID. Merging events requires a new UUID and complete source
    coverage. Any failed validation or write rolls back both the accounting and the manual result.
    """
    _require_idle(connection)
    outcome = ManualOutcome.model_validate(outcome.model_dump())
    if case.budget_revision != budget_revision(budget):
        raise ValueError("Budget categories changed after this manual request was prepared")
    if outcome.status == "pricing":
        raise ValueError("Resolve investment pricing before saving an accounting outcome")
    if outcome.manual_id != case.submission.id:
        raise ValueError("The manual outcome belongs to another instruction")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        saved = _require_request(connection, case)
        _check_case(connection, case, accounts)
        if (outcome.model, outcome.skill_version) != (saved["model"], saved["skill_version"]):
            raise ValueError("Manual provenance must match the reserved model and skills")
        before = _audit_snapshot(connection, case)
        event_id = saved["journal_entry_id"]
        entry = outcome.entry
        if outcome.status == "apply" and outcome.event_kind == "savings":
            if case.savings_context is None or budget is None or outcome.savings is None:
                raise ValueError("A savings allocation needs its configured monthly context")
            if event_id is not None or case.required_sources or case.entries or case.transactions:
                raise ValueError("Savings allocations cannot replace or claim accounting events")
            if outcome.savings.date != case.submission.date:
                raise ValueError("A savings allocation must keep the manual accounting date")
            save_savings_allocation(connection, case.submission.id, outcome.savings, accounts, budget)
            after = _audit_snapshot(connection, case)
            status = "applied"
        elif outcome.status == "apply":
            if case.savings_context is not None:
                raise ValueError("A virtual-pot instruction cannot create journal postings")
            if entry is None:
                raise ValueError("An applied manual outcome needs a complete entry")
            _check_claims(connection, case)
            replaced = _check_replacement(connection, case, outcome, saved, accounts, securities)
            entry = preserve_budget_categories((entry,), tuple(replaced))[0]
            validate_budget_labels((entry,), accounts, budget, tuple(replaced))
            outcome = ManualOutcome.model_validate({**outcome.model_dump(), "entry": entry})
            _replace_entry(connection, replaced, outcome, accounts)
            source_ids = {line.transaction_id for line in entry.lines if line.transaction_id is not None}
            _retire_reviews(connection, source_ids, case.submission.id)
            event_id = entry.id
            after = _audit_snapshot(connection, case, event_id=event_id)
            status = "applied"
        else:
            after = before
            status = outcome.status
        connection.execute(
            """
            UPDATE manual_entries SET status = ?, outcome = ?, question = ?, journal_entry_id = ?,
                before_snapshot = ?, after_snapshot = ?, error = NULL, updated_at = ? WHERE id = ?
            """,
            (
                status, outcome.model_dump_json(), outcome.question,
                str(event_id) if event_id else None, _json(before), _json(after),
                outcome.updated_at.isoformat(), str(saved["input_revision"]),
            ),
        )


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _with_savings(connection: sqlite3.Connection, manual_id: UUID, snapshot: dict) -> dict:
    """
    Add only this instruction's current split, preserving older unallocated CSV revision hashes.
    """
    allocation = load_savings_allocations(connection).get(manual_id)
    if allocation is not None:
        return {**snapshot, "savings": allocation.model_dump(mode="json")}
    return snapshot


def _latest(connection: sqlite3.Connection, manual_id: UUID) -> dict | None:
    """
    Find the latest stored revision of one manual instruction.
    """
    return next((row for row in load_manual_entries(connection) if row["manual_id"] == manual_id), None)


def _matches_context_revision(
    connection: sqlite3.Connection, saved: dict, revision: str, accounts: AccountRegistry,
) -> bool:
    """
    Require the CSV revision to match the current accounting shown for this manual instruction.
    """
    return revision == manual_context_revision(connection, saved["manual_id"], accounts)


def _input_values(submission: ManualInput) -> dict:
    """
    Compare editable instruction values without treating generated CSV context as a new answer.
    """
    return submission.model_dump(mode="json", exclude={"context_revision"})


def _live_context(
    connection: sqlite3.Connection, source_ids: set, entry_ids: set, accounts: AccountRegistry,
) -> dict:
    """
    Read complete live events and source facts for a candidate group, including source-free manual events.
    """
    source_ids = set(source_ids)
    entry_ids = set(entry_ids) - {None}
    sources = {item.transaction_id: item for item in load_transactions(connection)}
    all_entries = load_journal_entries(connection, accounts)
    entries = [
        entry for entry in all_entries
        if entry.id in entry_ids or source_ids & {line.transaction_id for line in entry.lines}
    ]
    if not entry_ids <= {entry.id for entry in entries}:
        raise ValueError("An existing manual event is no longer in the journal")
    for entry in entries:
        source_ids.update(line.transaction_id for line in entry.lines if line.transaction_id is not None)
    if not source_ids <= sources.keys():
        raise ValueError("A manual instruction references an unknown imported transaction")
    return _context([sources[identifier] for identifier in source_ids], entries)


def _check_case(
    connection: sqlite3.Connection, case: ManualCase, accounts: AccountRegistry,
) -> dict:
    """
    Require the exact current instruction, complete evidence and its unchanged accounting revision.
    """
    saved = _latest(connection, case.submission.id)
    if saved is None or _input_values(saved["submission"]) != _input_values(case.submission):
        raise ValueError("The manual case does not match the latest saved instruction")
    revision = manual_case_revision(connection, case.submission, case.transactions, case.entries, accounts)
    if revision != case.revision:
        raise ValueError("Manual accounting changed after preparation; refresh its CSV before correcting it")
    if saved["journal_entry_id"] and saved["journal_entry_id"] not in {entry.id for entry in case.entries}:
        raise ValueError("A manual correction must include its current complete event")
    return saved


def _require_request(connection: sqlite3.Connection, case: ManualCase) -> dict:
    """
    Require the exact latest manual request to remain reserved before saving an outcome or error.
    """
    saved = _latest(connection, case.submission.id)
    if saved is None or saved["status"] != "requesting" or saved["case"] != case:
        raise ValueError("This exact manual request is not reserved or has already finished")
    return saved


def _check_claims(connection: sqlite3.Connection, case: ManualCase) -> None:
    """
    Prevent concurrent manual instructions and bank reviews from spending tokens on competing source groups.
    """
    sources = {item.transaction_id for item in case.transactions}
    owners = manual_owner_ids(connection)
    if any(owners.get(identifier, case.submission.id) != case.submission.id for identifier in sources):
        raise ValueError("An affected transaction belongs to another manual entry; edit its original CSV row")
    for record in load_manual_entries(connection):
        if record["manual_id"] == case.submission.id:
            continue
        if record["status"] not in {"pending", "requesting", "needs_review", "failed", "unsupported"}:
            continue
        other = set(record["submission"].source_transactions)
        if record["case"] is not None:
            other.update(item.transaction_id for item in record["case"].transactions)
        if sources & other:
            raise ValueError("Another manual instruction claims the same candidate transactions")
    for record in load_review_cases(connection):
        claimed = {item.transaction_id for item in record["case"].transactions}
        if record["status"] == "requesting" and sources & claimed:
            raise ValueError("A bank review request already claims these sources; resolve that request first")


def _check_replacement(
    connection: sqlite3.Connection, case: ManualCase, outcome: ManualOutcome, saved: dict,
    accounts: AccountRegistry,
    securities: SecurityRegistry | None = None,
) -> list:
    """
    Validate the selected source closure, existing event identity and manual-only accounting boundaries.

    Opening wording is required only for bank postings without imported evidence, not non-bank changes.
    """
    entry = outcome.entry
    if entry is None:
        raise ValueError("A manual replacement needs a complete entry")
    if entry.date != case.submission.date:
        raise ValueError("A manual event must use its explicitly supplied accounting date")
    if any(line.investment is not None for line in entry.lines) or outcome.event_kind == "investment":
        _check_investment_result(connection, case, outcome, saved, accounts, securities)
    selected = {line.transaction_id for line in entry.lines if line.transaction_id is not None}
    candidates = {item.transaction_id for item in case.transactions}
    if not set(case.required_sources) <= selected <= candidates:
        raise ValueError("A manual entry must retain required sources and select only supplied evidence")
    replaced = [
        old for old in case.entries
        if old.id == saved["journal_entry_id"]
        or selected & {line.transaction_id for line in old.lines if line.transaction_id is not None}
    ]
    for old in replaced:
        if any(line.investment is not None for line in old.lines) and outcome.event_kind != "investment":
            raise ValueError("Correct investment units and cost using an investment instruction")
        if not {line.transaction_id for line in old.lines if line.transaction_id is not None} <= selected:
            raise ValueError("A correction must preserve every source belonging to each replaced event")
    if len(replaced) == 1 and entry.id != replaced[0].id:
        raise ValueError("A one-to-one correction must preserve its existing event UUID")
    if len(replaced) != 1 and entry.id in {old.id for old in case.entries}:
        raise ValueError("A merged event must receive a new UUID")
    by_id = {account.id: account for account in accounts.accounts.values()}
    for line in entry.lines:
        account = by_id.get(line.account_id)
        if account is not None and account.bank_details is not None and line.transaction_id is None:
            if outcome.event_kind != "opening":
                raise ValueError("Bank activity must reuse imported evidence; import its statement first")
            if not re.search(
                r"\b(opening|initial|starting|brought[ -]forward)\b",
                case.submission.instruction + " " + case.submission.human_response, re.IGNORECASE,
            ):
                raise ValueError("A bank posting without a source needs an explicit opening instruction")
    _check_journal_sources(connection, entry, accounts)
    return replaced



def _check_investment_result(
    connection, case: ManualCase, outcome: ManualOutcome, saved: dict,
    accounts: AccountRegistry, securities: SecurityRegistry | None,
) -> None:
    """
    Independently reconcile investment postings to their durable interpretation and exact calculation.
    """
    if securities is None:
        raise ValueError("Investment entries require the configured security catalogue")
    entry = outcome.entry
    if entry is None:
        raise ValueError("An investment result needs a complete entry")
    _check_investment_securities(entry, securities)
    instruction, calculation = outcome.investment, outcome.pricing
    cached = saved["investment_proposal"]
    if instruction is None or calculation is None or cached is None:
        raise ValueError("An investment needs its saved interpretation and calculation")
    proposal = ManualOutcome.model_validate(cached["outcome"])
    if (
        proposal.investment != instruction or cached["case_revision"] != case.revision
        or proposal.investment_budget_category != outcome.investment_budget_category
    ):
        raise ValueError("Investment facts changed after the interpreted proposal was saved")
    security = securities.securities.get(instruction.security_key)
    asset = accounts.accounts.get(instruction.account_key)
    counterpart = accounts.accounts.get(instruction.counterpart_key)
    if security is None or asset is None or counterpart is None:
        raise ValueError("Investment accounts and security must remain configured")
    if asset.account_type != AccountType.ASSET or asset.bank_details is not None:
        raise ValueError("Investment units require a non-bank asset account")
    if instruction.operation == "opening" and counterpart.bank_details is not None:
        raise ValueError("An opening holding cannot manufacture an unlinked bank payment")
    source = next(
        (row for row in case.transactions if row.transaction_id == instruction.transaction_id), None,
    )
    if instruction.transaction_id is not None and (
        source is None or source.account_id != counterpart.id or source.amount >= 0
    ):
        raise ValueError("Investment settlement must be the actual outgoing payment")
    quote = calculation.quote
    if quote is not None:
        if quote.security_id != security.id or quote.requested_date != case.submission.date:
            raise ValueError("The reference quote does not match this investment")
        if load_quote(connection, security.id, quote.requested_date, quote.symbol) != quote:
            raise ValueError("The reference price must match a saved market quote")
    expected = calculate_investment(
        instruction, quote=quote, settlement=source.amount.copy_negate() if source is not None else None,
    )
    if expected != calculation or len(entry.lines) != 2:
        raise ValueError("Investment postings must match their complete calculation")
    holding, funding = entry.lines
    details = holding.investment
    if (
        details is None or holding.account_id != asset.id or details.security_id != security.id
        or details.units != expected.units or details.unit_price != expected.unit_price
        or details.fees != expected.fees or holding.debit != expected.total_cost
        or funding.account_id != counterpart.id or funding.credit != expected.total_cost
        or funding.transaction_id != instruction.transaction_id or funding.investment is not None
        or (outcome.investment_budget_category is not None
            and holding.budget_category != outcome.investment_budget_category)
    ):
        raise ValueError("Investment units, carrying cost or funding differ from the saved calculation")


def _replace_entry(connection: sqlite3.Connection, replaced, outcome: ManualOutcome, accounts) -> None:
    """
    Reuse bank source rows while replacing complete events inside the caller's transaction.
    """
    entry = outcome.entry
    if entry is None:
        raise ValueError("A manual replacement needs a complete entry")
    for old in replaced:
        connection.execute(
            "DELETE FROM journal WHERE journal_entry_id = ? AND transaction_id IS NULL", (str(old.id),)
        )
    for line in entry.lines:
        if line.transaction_id is None:
            continue
        _ensure_source_row(connection, line.transaction_id, accounts)
        connection.execute(
            """
            UPDATE journal SET status = 'pending', journal_entry_id = NULL, line_number = NULL
            WHERE transaction_id = ?
            """,
            (str(line.transaction_id),),
        )
    _write_entry(connection, entry, accounts)


def _retire_reviews(connection: sqlite3.Connection, source_ids: set[UUID], manual_id: UUID) -> None:
    """
    Retire displaced review evidence using its existing audit IDs, preserving every foreign-key reference.
    """
    for record in load_review_cases(connection):
        if not source_ids & {item.transaction_id for item in record["case"].transactions}:
            continue
        identifier = str(record["id"])
        connection.execute(
            """
            UPDATE categorisation_guidance SET active = 0, retired_by_action_id = review_action_id
            WHERE review_action_id = ? AND active = 1
            """,
            (identifier,),
        )
        connection.execute(
            """
            UPDATE review_actions SET status = 'superseded', error = ?, updated_at = ?
            WHERE id = ? AND status != 'superseded'
            """,
            (
                f"Accounting replaced by manual entry {manual_id}; edit its original manual CSV",
                _now(), identifier,
            ),
        )


def _audit_snapshot(
    connection: sqlite3.Connection, case: ManualCase, *, event_id: UUID | None = None,
) -> dict:
    """
    Keep exact affected journal rows alongside the submitted facts for correction history.
    """
    sources = {str(item.transaction_id) for item in case.transactions}
    entries = {str(entry.id) for entry in case.entries}
    if event_id is not None:
        entries.add(str(event_id))
    cursor = connection.execute("SELECT * FROM journal ORDER BY id")
    columns = [item[0] for item in cursor.description]
    rows = [dict(zip(columns, values)) for values in cursor]
    return _with_savings(connection, case.submission.id, {
        "submission": case.submission.model_dump(mode="json"),
        "journal": [
            row for row in rows if row["transaction_id"] in sources or row["journal_entry_id"] in entries
        ],
    })
