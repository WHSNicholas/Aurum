# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Workflows - Manual                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum prepares saved desktop instructions and responses for accounting and virtual savings allocations.

Each instruction has a stable identity and one accounting instruction or virtual savings split. It saves IDs
before inference, presents relevant statement evidence, and keeps unanswered or failed requests from becoming
ordinary bank categorisations. A valid result posts automatically; a correction replaces its whole event.

Virtual savings run after journal preparation and divide monthly income left after expenses without postings.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Callable
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
import sqlite3
from uuid import UUID

from pydantic import ValidationError

# Aurum imports
from aurum.ai.chatgpt import ChatGPTConnection, ChatGPTError, record_usage
from aurum.ai.manual import MAX_MANUAL_BATCH_SIZE, ManualBatchError, complete_investment, manual_batch
from aurum.ai.skills import Skill, load_manual_skill
from aurum.database.journal import load_journal_entries
from aurum.database.memory import load_explicit_memory, memory_revision
from aurum.database.manual import (
    defer_manual_case, fail_manual_case, load_manual_entries, manual_case_revision,
    manual_claims, manual_needs_processing, manual_owner_ids, reserve_manual_case,
    save_investment_proposal, save_manual_outcome,
)
from aurum.database.reviews import load_active_guidance
from aurum.database.transactions import load_transactions
from aurum.finance.budget import BudgetSettings, budget_revision
from aurum.finance.dates import reporting_month
from aurum.finance.savings import SavingsCapacityError
from aurum.database.savings import load_savings_allocations, savings_context
from aurum.finance.accounts import AccountRegistry
from aurum.finance.investments import get_quote
from aurum.finance.journal import JournalEntry
from aurum.finance.manual import ManualCase, ManualInput, ManualOutcome
from aurum.finance.reports import ReportSettings
from aurum.finance.transaction import Transaction
from aurum.finance.securities import SecurityRegistry
from aurum.workflows.review import ReviewSession

# %% 1. Saved Manual Instructions ----------------------------------------------------------------------------
@dataclass
class ManualSession:
    """
    Keep ready cases, protected sources and local questions for one desktop processing run.
    """

    cases: list[ManualCase] = field(default_factory=list)
    blocked: set[UUID] = field(default_factory=set)
    messages: dict[UUID, str] = field(default_factory=dict)
    blocking_manuals: dict[UUID, UUID] = field(default_factory=dict)


def prepare_manuals(
    connection: sqlite3.Connection, accounts: AccountRegistry, *,
    retry_errors: bool = False, skill_version: str | None = None,
    securities: SecurityRegistry | None = None, budget: BudgetSettings | None = None,
    manual_ids: set[UUID] | None = None, persist_questions: bool = True,
) -> ManualSession:
    """
    Prepare saved instructions and responses directly from SQLite.

    Other instructions' claims still protect their evidence. Only selected instructions may acquire new
    questions or requests, and complete existing events remain available as context for corrections.
    """
    session = ManualSession()
    transactions = load_transactions(connection)
    entries = load_journal_entries(connection, accounts)
    skill_version = skill_version or load_manual_skill().version
    catalogue_revision = _catalogue_revision(accounts, securities, budget)
    outside_claims = set()
    for record in load_manual_entries(connection):
        identifier = record["manual_id"]
        if record["status"] == "withdrawn":
            continue
        selected = manual_ids is None or identifier in manual_ids
        try:
            case = _build_case(
                connection, record, transactions, entries, accounts,
                catalogue_revision=catalogue_revision, budget=budget,
            )
            if record["status"] in {"pending", "requesting", "needs_review", "failed", "unsupported"}:
                for item in case.transactions:
                    session.blocking_manuals.setdefault(item.transaction_id, identifier)
            if not selected:
                if record["status"] in {"pending", "requesting", "needs_review", "failed", "unsupported"}:
                    outside_claims.update(item.transaction_id for item in case.transactions)
                continue
            if manual_needs_processing(
                record, skill_version=skill_version, retry_errors=retry_errors,
                catalogue_revision=catalogue_revision, budget_version=budget_revision(budget),
            ):
                session.cases.append(case)
            if record["status"] in {"pending", "requesting", "needs_review", "failed", "unsupported"}:
                session.blocked.update(item.transaction_id for item in case.transactions)
        except ValueError as error:
            if selected:
                session.messages[identifier] = str(error)
            if record["case"] is not None:
                session.blocked.update(item.transaction_id for item in record["case"].transactions)
    session.blocked.update(manual_claims(connection))
    session.blocked.update(outside_claims)
    retained = []
    for case in session.cases:
        if outside_claims & {row.transaction_id for row in case.transactions}:
            session.messages[case.submission.id] = (
                "Another month's pending manual instruction could use this bank evidence. "
                "Resolve it or select exact source transactions before processing."
            )
        else:
            retained.append(case)
    session.cases = retained
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
                        f"Edit the original manual entry instead: {label}."
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
            excluded = set(review_sources)
            for entry in case.evidence_entries:
                linked = {line.transaction_id for line in entry.lines if line.transaction_id is not None}
                if linked & review_sources:
                    excluded.update(linked)
            evidence = tuple(row for row in case.evidence_transactions if row.transaction_id not in excluded)
            evidence_entries = tuple(entry for entry in case.evidence_entries if not excluded & {
                line.transaction_id for line in entry.lines if line.transaction_id is not None
            })
            if evidence != case.evidence_transactions:
                case = ManualCase.model_validate({
                    **case.model_dump(), "evidence_transactions": evidence,
                    "evidence_entries": evidence_entries,
                    "evidence_revision": sha256(json.dumps({
                        "transactions": [row.model_dump(mode="json") for row in evidence],
                        "entries": [entry.model_dump(mode="json") for entry in evidence_entries],
                    }, sort_keys=True).encode()).hexdigest(),
                })
            retained.append(case)
    session.cases = retained
    if reviews is not None:
        reviews.cases = [
            case for case in reviews.cases
            if not any(row.transaction_id in session.blocked for row in case.transactions)
        ]


# %% 2. Manual Processing ------------------------------------------------------------------------------------
def process_manuals(
    connection: sqlite3.Connection, accounts: AccountRegistry, session: ManualSession,
    client_provider: Callable[[], ChatGPTConnection], counts: dict[str, int],
    *, model: str, skill: Skill, retry_errors: bool = False,
    securities: SecurityRegistry | None = None, quote_fetcher: Callable | None = None,
    progress: Callable[[str], None] | None = None, budget: BudgetSettings | None = None,
    savings_only: bool = False, cancelled: Callable[[], bool] | None = None,
    report_settings: ReportSettings | None = None,
) -> None:
    """
    Interpret new instructions, resume saved pricing and save validated complete events independently.

    Cached proposals require the same input, accounting context, explicit memory, model, skill and configuration.
    Price retrieval follows durable proposal storage, so an interrupted market request need not spend AI tokens.
    quote_fetcher supports synthetic checks without contacting a provider. The default phase handles
    accounting; savings_only divides the budget surplus after journal preparation using each month's context.
    """
    report = progress or (lambda message: None)
    catalogue_revision = _catalogue_revision(accounts, securities, budget)
    cases = [case for case in session.cases if (case.savings_context is not None) == savings_only]
    batch_size = 1 if savings_only else MAX_MANUAL_BATCH_SIZE
    if cases:
        label = "Savings allocations" if savings_only else "Manual entries"
        report(f"{label}: {len(cases)} ready for processing.")
    for start in range(0, len(cases), batch_size):
        if cancelled and cancelled():
            return
        batch = []
        for case in cases[start:start + batch_size]:
            try:
                if savings_only:
                    records = {item["manual_id"]: item for item in load_manual_entries(connection)}
                    case = _build_case(
                        connection, records[case.submission.id], load_transactions(connection),
                        load_journal_entries(connection, accounts), accounts,
                        catalogue_revision=catalogue_revision, budget=budget,
                    )
                if reserve_manual_case(
                    connection, case, accounts, model=model, skill_version=skill.version,
                    retry_errors=retry_errors, budget=budget,
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
                and cached.get("evidence_revision") == case.evidence_revision
                and cached.get("memory_revision") == case.memory_revision
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
                    explicit_memory=load_explicit_memory(connection),
                    securities=securities, budget=budget, report_settings=report_settings,
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
                        connection, case, outcome, accounts, securities, quote_fetcher, budget,
                    )
                try:
                    save_manual_outcome(
                        connection, case, outcome, accounts, securities=securities, budget=budget,
                    )
                except SavingsCapacityError as error:
                    outcome = ManualOutcome(
                        manual_id=outcome.manual_id, status="needs_review", event_kind="savings",
                        reason="The requested split exceeds the available monthly savings.",
                        question=str(error), model=outcome.model, skill_version=outcome.skill_version,
                    )
                    save_manual_outcome(connection, case, outcome, accounts, budget=budget)
            except (ValueError, sqlite3.Error) as error:
                fail_manual_case(
                    connection, case,
                    f"Manual storage validation failed ({type(error).__name__}); accounting was unchanged.",
                )
                counts["manual_failed"] += 1
                continue
            if outcome.status == "apply" and outcome.event_kind == "savings":
                counts["savings_applied"] = counts.get("savings_applied", 0) + 1
                report("Saved monthly savings allocation; journal balances were unchanged.")
            elif outcome.status == "apply":
                counts["manual_posted"] += 1
            else:
                counts["manual_questions"] += 1
        if stop:
            break


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _catalogue_revision(
    accounts: AccountRegistry, securities: SecurityRegistry | None, budget: BudgetSettings | None = None,
) -> str:
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
    if budget is not None:
        payload["budget_revision"] = budget_revision(budget)
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _price_investment(
    connection, case: ManualCase, outcome: ManualOutcome, accounts: AccountRegistry,
    securities: SecurityRegistry | None, quote_fetcher: Callable | None,
    budget: BudgetSettings | None = None,
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
        return complete_investment(case, outcome, accounts, securities, quote=quote, budget=budget)
    except ValueError as error:
        message = "Investment figures could not be validated; check the supplied facts."
        if not isinstance(error, ValidationError):
            message = str(error)
        return ManualOutcome(
            manual_id=outcome.manual_id, status="needs_review", event_kind="investment",
            investment=outcome.investment, reason="The investment needs pricing or corrected trade facts.",
            investment_budget_category=outcome.investment_budget_category,
            question=message, model=outcome.model, skill_version=outcome.skill_version,
        )


def _build_case(
    connection, record: dict, transactions: list[Transaction], entries: list[JournalEntry],
    accounts: AccountRegistry, *, catalogue_revision: str | None = None,
    budget: BudgetSettings | None = None,
) -> ManualCase:
    """
    Find nearby statement evidence and expand existing events to their complete source groups.
    """
    submission = record["submission"]
    stored = load_savings_allocations(connection)
    prior_case = record.get("case")
    savings_mode = (
        submission.id in stored
        or record.get("is_savings", False)
        or (prior_case is not None and prior_case.savings_context is not None)
        or _is_savings_instruction(submission, budget)
    )
    if savings_mode:
        text = submission.instruction + " " + submission.human_response
        references = re.findall(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", text)
        if submission.source_transactions or references or record.get("journal_entry_id") is not None:
            raise ValueError("Virtual savings splits cannot use source IDs or replace an accounting entry")
        if budget is None or budget.savings is None:
            raise ValueError("Configure the budget's virtual pots before allocating savings")
        return ManualCase(
            submission=submission,
            revision=manual_case_revision(connection, submission, (), (), accounts),
            question=record.get("question"), catalogue_revision=catalogue_revision,
            budget_revision=budget_revision(budget), memory_revision=memory_revision(connection),
            savings_context=savings_context(connection, submission.id, submission.date, accounts, budget),
        )
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
    candidates = set(required)
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
    # Monthly evidence is shared read-only context, never an exclusive claim on every bank row.
    owners = manual_owner_ids(connection)
    claims = manual_claims(connection)
    unavailable = {
        key for key, owner in owners.items() | claims.items() if owner != submission.id
    }
    evidence_ids = {
        row.transaction_id for row in transactions
        if reporting_month(row.date) == reporting_month(submission.date)
        and row.transaction_id not in unavailable
    } - candidates
    evidence_entries = []
    for entry in entries:
        source_ids = {line.transaction_id for line in entry.lines if line.transaction_id is not None}
        if entry not in selected_entries and evidence_ids & source_ids:
            if source_ids & unavailable:
                evidence_ids.difference_update(source_ids)
                continue
            evidence_ids.update(source_ids - candidates)
            evidence_entries.append(entry)
    evidence = tuple(by_id[key] for key in sorted(evidence_ids, key=str))
    evidence_entries = tuple(sorted(evidence_entries, key=lambda entry: str(entry.id)))
    evidence_version = sha256(json.dumps({
        "transactions": [row.model_dump(mode="json") for row in evidence],
        "entries": [entry.model_dump(mode="json") for entry in evidence_entries],
    }, sort_keys=True).encode()).hexdigest()
    return ManualCase(
        submission=submission,
        revision=manual_case_revision(connection, submission, selected, selected_entries, accounts),
        transactions=selected, required_sources=tuple(sorted(required, key=str)), entries=selected_entries,
        question=record.get("question"), catalogue_revision=catalogue_revision,
        budget_revision=budget_revision(budget), memory_revision=memory_revision(connection),
        evidence_transactions=evidence, evidence_entries=evidence_entries, evidence_revision=evidence_version,
    )


def _is_savings_instruction(submission: ManualInput, budget: BudgetSettings | None) -> bool:
    """
    Route explicit virtual-pot language to budget context without interpreting amounts or bank transfers.
    """
    if budget is None or budget.savings is None:
        return False
    text = (submission.instruction + " " + submission.human_response).casefold()
    if not re.search(r"\b(allocat(?:e|ed|ion|ions|ing)|sav(?:e|ed|ing|ings)|pots?)\b", text):
        return False
    # A residual named Savings must not route ordinary savings-bank activity into virtual allocations.
    explicit_allocation = re.search(r"\b(allocat(?:e|ed|ion|ions|ing)|pots?)\b", text)
    names = [
        name.casefold() for key in budget.savings.category_keys
        if key != budget.savings.residual_category or explicit_allocation
        for name in (key, key.replace("_", " "), budget.categories[key].name)
    ]
    return bool(re.search(r"\b(?:virtual|savings) pots?\b", text)) or any(
        re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text) for name in names
    )
