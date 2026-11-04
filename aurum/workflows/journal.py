# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 Workflows - Journal                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum turns imported bank transactions and saved human instructions into balanced journal events.

The desktop Process action applies saved manual instructions and review answers
before ordinary categorisation, matches clear bank transfers locally, and allocates virtual savings after
accounting is complete. Existing storage validates complete events and retains retryable request history.

Processing uses the selected reporting month while preserving complete linked events and checking wider
evidence for ambiguity. Snapshots expose saved facts without making AI calls merely to display a page.
"""


# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path
import re
import sqlite3
from uuid import UUID

# Aurum imports
from aurum.ai.categorisation import CategorisationBatchError, Decision, MAX_BATCH_SIZE, classify_batch
from aurum.ai.chatgpt import ChatGPTConnection, ChatGPTError, connect, record_usage
from aurum.ai.skills import Skill, load_categorisation_skill, load_manual_skill
from aurum.database.categorisation import (
    load_decisions, reserve_decisions, save_categorisation_batch, save_decisions,
)
from aurum.database.journal import (
    load_journal_entries, posted_transaction_ids, prepare_journal, save_journal_entry,
)
from aurum.database.transactions import load_transactions
from aurum.database.memory import load_explicit_memory
from aurum.database.manual import (
    load_manual_entries, manual_claims, manual_context_revision, manual_owner_ids,
)
from aurum.finance.accounts import Account, AccountRegistry, AccountType
from aurum.finance.budget import BudgetSettings
from aurum.finance.journal import JournalEntry, JournalLine
from aurum.finance.securities import SecurityRegistry
from aurum.finance.transaction import Transaction
from aurum.database.reviews import (
    load_active_guidance,
    load_review_cases, load_review_baselines, load_review_answers, review_display_versions,
)
from aurum.workflows.review import (
    export_guidance, prepare_reviews, process_guidance, process_reviews,
)
from aurum.workflows.manual import (
    coordinate_manuals, prepare_manuals, process_manuals,
)
from aurum.finance.reports import ReportPeriod, ReportSettings

DEFAULT_MODEL = "gpt-6-luna"
TRANSFER_REVIEW_REASON = "A possible internal transfer has no unique eligible counterpart."


# %% 1. Journal Processing -----------------------------------------------------------------------------------
def process_journal(
    connection: sqlite3.Connection,
    accounts: AccountRegistry,
    *,
    model: str = DEFAULT_MODEL,
    retry_errors: bool = False,
    securities: SecurityRegistry | None = None,
    budget: BudgetSettings | None = None,
    quote_fetcher: Callable | None = None,
    client_factory: Callable[[], AbstractContextManager[ChatGPTConnection]] = connect,
    progress: Callable[[str], None] | None = None,
    period: ReportPeriod | None = None,
    report_settings: ReportSettings | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, int]:
    """
    Match transfers and save each validated classification batch together with its accounting entries.

    Args:
        connection: Initialised SQLite connection without a pending write.
        accounts: Configured bank and category accounts.
        model: Explicit ChatGPT model slug; an unavailable model raises an error.
        retry_errors: Allow failed or interrupted requests to be attempted again.
        securities: Configured instruments; None leaves investment posting unavailable for library calls.
        budget: Optional category catalogue for explicit gift and savings labels supplied through review.
        quote_fetcher: Optional synthetic market provider used by isolated checks.
        client_factory: Connection factory, replaceable with a synthetic client for local checks.
        progress: Optional callback for brief stage messages; library calls are quiet by default.
        period: Optional reporting month; linked sources stay together and unrelated months are not written.
        cancelled: Stop before the next batch while finishing any current atomic result.

    Returns:
        Counts for this run, remaining reviews and reported token usage.

    Raises:
        ValueError: A source account is missing or unsupported.
        ChatGPTError: Sign-in or model selection fails before a batch can be sent.
        sqlite3.Error: Decisions or journal entries cannot be stored safely.
    """
    report = progress or (lambda message: None)
    source_ids, manual_ids = _processing_scope(connection, accounts, period)
    report("Reading saved reviews and manual instructions...")
    counts = {
        "entries_created": 0, "linked_sources": 0,
        "already_posted": len(posted_transaction_ids(connection) & source_ids) if source_ids is not None
        else len(posted_transaction_ids(connection)),
        "requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "reviews_applied": 0, "review_questions": 0, "review_failed": 0,
        "manual_posted": 0, "manual_questions": 0, "manual_failed": 0, "savings_applied": 0,
    }
    if cancelled and cancelled():
        counts["stopped"] = 1
        return _summary(connection, counts, transaction_ids=source_ids, manual_ids=manual_ids)
    skill = load_categorisation_skill()
    session = prepare_reviews(connection, accounts, retry_errors=retry_errors, budget=budget)
    if source_ids is not None:
        session.cases = [
            case for case in session.cases
            if any(row.transaction_id in source_ids for row in case.transactions)
        ]
        source_ids.update(row.transaction_id for case in session.cases for row in case.transactions)
    manual_skill = load_manual_skill()
    manual_session = prepare_manuals(
        connection, accounts, retry_errors=retry_errors, skill_version=manual_skill.version,
        securities=securities, budget=budget, manual_ids=manual_ids,
    )
    if source_ids is not None:
        source_ids.update(identifier for case in manual_session.cases for identifier in case.required_sources)
    if cancelled and cancelled():
        counts["stopped"] = 1
        return _summary(connection, counts, transaction_ids=source_ids, manual_ids=manual_ids)
    prepare_journal(connection, accounts, transaction_ids=source_ids)
    with ExitStack() as stack:
        client = None

        def get_client() -> ChatGPTConnection:
            """
            Open and select a model only when work needs inference, then share the connection this run.
            """
            nonlocal client
            if client is None:
                report(f"Connecting to ChatGPT with {model}...")
                client = _select_client(stack, client_factory, model)
            return client

        if not (cancelled and cancelled()):
            coordinate_manuals(connection, accounts, manual_session, session)
            process_manuals(
                connection, accounts, manual_session, get_client, counts, model=model,
                skill=manual_skill, retry_errors=retry_errors,
                securities=securities, budget=budget, quote_fetcher=quote_fetcher, progress=report,
                cancelled=cancelled, report_settings=report_settings,
            )
        if not (cancelled and cancelled()):
            process_reviews(
                connection, accounts, session, get_client, counts, model=model, skill=skill,
                retry_errors=retry_errors, budget=budget, progress=report, cancelled=cancelled,
                report_settings=report_settings,
            )
        _process_transactions(
            connection, accounts, get_client, counts, model=model, skill=skill,
            retry_errors=retry_errors, blocked=session.blocked | manual_session.blocked, progress=report,
            transaction_ids=source_ids, cancelled=cancelled,
        )
        if not (cancelled and cancelled()):
            process_manuals(
                connection, accounts, manual_session, get_client, counts, model=model,
                skill=manual_skill, retry_errors=retry_errors, savings_only=True,
                securities=securities, budget=budget, quote_fetcher=quote_fetcher, progress=report,
                cancelled=cancelled,
            )
    counts["manual_local_questions"] = len(manual_session.messages)
    counts["stopped"] = int(bool(cancelled and cancelled()))
    if period is not None:
        counts["linked_outside_month"] = sum(
            row.transaction_id in source_ids and not period.start_date <= row.date <= period.end_date
            for row in load_transactions(connection)
        )
    return _summary(connection, counts, transaction_ids=source_ids, manual_ids=manual_ids)


def learn_from_reviews(
    connection: sqlite3.Connection,
    accounts: AccountRegistry,
    *,
    model: str = DEFAULT_MODEL,
    retry_errors: bool = False,
    guidance_path: Path,
    client_factory: Callable[[], AbstractContextManager[ChatGPTConnection]] = connect,
    progress: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, int]:
    """
    Backfill learning outcomes from accepted reviews while keeping saved accounting and responses unchanged.

    A connection is opened only when an eligible review needs inference. Completed assessments are reused;
    Process retries failed or interrupted assessments without reposting their accepted accounting.
    """
    counts = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    skill = load_categorisation_skill()
    try:
        with ExitStack() as stack:
            client = None

            def get_client() -> ChatGPTConnection:
                """
                Share one model connection across the guidance batches that need it.
                """
                nonlocal client
                if client is None:
                    client = _select_client(stack, client_factory, model)
                return client

            process_guidance(
                connection, accounts, get_client, counts, model=model, skill=skill, retry_errors=retry_errors,
                progress=progress, cancelled=cancelled,
            )
    finally:
        export_guidance(connection, guidance_path)
    counts["stopped"] = int(bool(cancelled and cancelled()))
    return counts


# %% 2. Desktop Inputs ---------------------------------------------------------------------------------------
def journal_snapshot(
    connection: sqlite3.Connection, accounts: AccountRegistry, period: ReportPeriod | None = None,
    *, securities: SecurityRegistry | None = None, budget: BudgetSettings | None = None,
) -> dict:
    """
    Read current source, review, manual and posting details for a desktop screen without changing storage.

    Returned dates, amounts and identities are strings. Revisions accompany editable inputs so later saves
    can reject concurrent changes. Related events may include sources outside the selected reporting month.
    Manual rows expose their latest instruction or direct-entry mode and retain virtual-savings ownership.
    """
    transactions = load_transactions(connection)
    entries = load_journal_entries(connection, accounts)
    source_ids, manual_ids = _processing_scope(connection, accounts, period)
    selected = {row.transaction_id for row in transactions} if source_ids is None else source_ids
    by_account = {item.id: item for item in accounts.accounts.values()}
    by_source = {
        line.transaction_id: entry for entry in entries for line in entry.lines
        if line.transaction_id is not None
    }
    decisions = load_decisions(connection)
    answers = load_review_answers(connection)
    baselines = load_review_baselines(connection)
    records = load_review_cases(connection)
    latest = {row.transaction_id: record for record in records for row in record["case"].transactions}
    review_session = prepare_reviews(connection, accounts, budget=budget)
    manual_session = prepare_manuals(
        connection, accounts, securities=securities, budget=budget,
        manual_ids=manual_ids, persist_questions=False,
    )
    ready = {row.transaction_id for case in review_session.cases for row in case.transactions}
    owners = manual_owner_ids(connection)
    review_versions = review_display_versions(
        connection, accounts, (row.transaction_id for row in transactions if row.transaction_id in selected),
        sources={row.transaction_id: row for row in transactions}, entries=entries, baselines=baselines,
    )
    review_rows, source_rows = [], []
    for transaction in transactions:
        identifier = transaction.transaction_id
        if identifier not in selected:
            continue
        entry = by_source.get(identifier)
        answer = answers.get(identifier)
        record = latest.get(identifier)
        decision = decisions.get(identifier)
        cleared = baselines.get(identifier, {}).get("human_review_cleared", False)
        outcome = record["outcome"] if record else None
        question = outcome.question if outcome else decision.question if decision else ""
        status = "posted" if entry else decision.status if decision else "pending"
        review_status = record["status"] if record else ""
        result = (record["error"] or "") if record else ""
        if outcome is not None:
            result = outcome.question or outcome.reason
        if identifier in ready:
            review_status = "pending"
        if identifier in review_session.messages:
            review_status, result = "stale", review_session.messages[identifier]
        if cleared:
            review_status, result = "discarded", "Saved answer discarded; existing accounting is unchanged."
        if identifier in owners:
            review_status, result = "manual_entry", "Correct this event through its original manual entry."
        elif identifier in manual_session.blocking_manuals:
            manual_id = manual_session.blocking_manuals[identifier]
            review_status = "needs_review"
            result = (
                f"Resolve pending manual instruction {manual_id} before categorising this bank line. "
                "The instruction may be in an adjacent reporting month."
            )
        postings = _posting_rows(entry, by_account) if entry else []
        source = {
            "transaction_id": str(identifier), "date": transaction.date.isoformat(),
            "effective_date": transaction.effective_date.isoformat() if transaction.effective_date else "",
            "account": by_account[transaction.account_id].name, "account_id": str(transaction.account_id),
            "amount": format(transaction.amount, ".2f"), "description": transaction.description,
            "source_file": transaction.source_file,
            "transfer_reference": transaction.transfer_reference or "",
        }
        source_rows.append(source)
        review_rows.append({
            **source, "status": status, "review_status": review_status,
            "question": question or "", "review_result": result,
            "human_review": answer.human_review if answer and not cleared else "",
            **review_versions[identifier],
            "journal_entry_id": str(entry.id) if entry else "",
            "manual_entry_id": str(owners[identifier]) if identifier in owners else "",
            "blocking_manual_entry_id": str(manual_session.blocking_manuals[identifier])
            if identifier in manual_session.blocking_manuals else "",
            "postings": postings,
            "debit_accounts": "; ".join(row["account"] for row in postings if row["debit"] != "0.00"),
            "credit_accounts": "; ".join(row["account"] for row in postings if row["credit"] != "0.00"),
        })
    manuals = []
    entry_by_id = {entry.id: entry for entry in entries}
    for record in load_manual_entries(connection):
        if manual_ids is not None and record["manual_id"] not in manual_ids:
            continue
        submission = record["submission"]
        entry = entry_by_id.get(record["journal_entry_id"])
        outcome = record["outcome"]
        manuals.append({
            "manual_entry_id": str(submission.id), "date": submission.date.isoformat(),
            "entry_mode": "direct" if outcome is not None and outcome.origin == "human" else "instruction",
            "is_savings": bool(record.get("is_savings")),
            "instruction": submission.instruction, "human_response": submission.human_response,
            "source_transactions": [str(identifier) for identifier in submission.source_transactions],
            "status": "needs_review" if submission.id in manual_session.messages else record["status"],
            "question": manual_session.messages.get(submission.id, record["question"] or ""),
            "error": record["error"] or "",
            "context_revision": manual_context_revision(connection, submission.id, accounts),
            "journal_entry_id": str(entry.id) if entry else "",
            "postings": _posting_rows(entry, by_account) if entry else [],
        })
    event_rows = [
        {
            "journal_entry_id": str(entry.id), "date": entry.date.isoformat(),
            "description": next((line.description for line in entry.lines if line.description), ""),
            "source_transactions": [
                str(line.transaction_id) for line in entry.lines if line.transaction_id is not None
            ],
            "postings": _posting_rows(entry, by_account),
        }
        for entry in entries
        if period is None or period.start_date <= entry.date <= period.end_date
        or any(line.transaction_id in selected for line in entry.lines if line.transaction_id is not None)
    ]
    counts = _summary(connection, transaction_ids=source_ids, manual_ids=manual_ids)
    counts["saved_answers"] = sum(row["review_status"] == "pending" for row in review_rows)
    counts["human_review_stale"] = sum(row["review_status"] == "stale" for row in review_rows)
    counts["manual_local_questions"] = len(manual_session.messages)
    counts["linked_outside_month"] = sum(
        not period.start_date <= row.date <= period.end_date
        for row in transactions if row.transaction_id in selected
    ) if period else 0
    return {
        "transactions": source_rows, "reviews": review_rows, "manuals": manuals,
        "entries": event_rows, "counts": counts,
    }



# %% 3. Helpers ----------------------------------------------------------------------------------------------


def _processing_scope(
    connection: sqlite3.Connection, accounts: AccountRegistry, period: ReportPeriod | None,
) -> tuple[set[UUID] | None, set[UUID] | None]:
    """
    Select period sources and complete linked events while keeping global evidence available to matching.
    """
    if period is None:
        return None, None
    transactions = load_transactions(connection)
    entries = load_journal_entries(connection, accounts)
    in_month = lambda day: period.start_date <= day <= period.end_date
    sources = {row.transaction_id for row in transactions if in_month(row.date)}
    event_ids = {entry.id for entry in entries if in_month(entry.date)}
    for entry in entries:
        if entry.id in event_ids:
            sources.update(line.transaction_id for line in entry.lines if line.transaction_id is not None)
    # Matching must see all pending rows before the scope grows; an outside-month duplicate still blocks it.
    posted = posted_transaction_ids(connection)
    pairs, _ = _match_transfers(
        [row for row in transactions if row.transaction_id not in posted],
        {account.id: account for account in accounts.accounts.values()},
    )
    for outgoing, incoming in pairs:
        if {outgoing.transaction_id, incoming.transaction_id} & sources:
            sources.update((outgoing.transaction_id, incoming.transaction_id))
    manual_ids = set()
    for record in load_manual_entries(connection):
        submission = record["submission"]
        if (
            in_month(submission.date) or record["journal_entry_id"] in event_ids
            or set(submission.source_transactions) & sources
        ):
            manual_ids.add(submission.id)
            sources.update(submission.source_transactions)
            if record["journal_entry_id"]:
                event_ids.add(record["journal_entry_id"])
    # A correction always carries every line and source of an affected event.
    changed = True
    while changed:
        previous = len(sources)
        for entry in entries:
            linked = {line.transaction_id for line in entry.lines if line.transaction_id is not None}
            if entry.id in event_ids or linked & sources:
                sources.update(linked)
        changed = len(sources) != previous
    return sources, manual_ids


def _posting_rows(entry: JournalEntry, accounts: dict[UUID, Account]) -> list[dict]:
    """
    Present exact posting facts with readable account names and no AI processing metadata.
    """
    return [
        {
            "account_id": str(line.account_id), "account": accounts[line.account_id].name,
            "debit": format(line.debit, ".2f"), "credit": format(line.credit, ".2f"),
            "description": line.description or "",
            "effective_date": line.effective_date.isoformat() if line.effective_date else "",
            "transaction_id": str(line.transaction_id) if line.transaction_id else "",
            "budget_category": line.budget_category or "",
            **({"account_investment": line.account_investment.model_dump(mode="json")}
               if line.account_investment is not None else {}),
        }
        for line in entry.lines
    ]


def _select_client(
    stack: ExitStack, client_factory: Callable[[], AbstractContextManager[ChatGPTConnection]], model: str,
) -> ChatGPTConnection:
    """
    Select the requested model on a managed connection, without silently substituting another model.
    """
    client = stack.enter_context(client_factory())
    available = {item["slug"] for item in client.list_models()}
    if model not in available:
        raise ChatGPTError(
            f"Model {model!r} is unavailable for this ChatGPT connection. "
            "Choose an available model explicitly with --model; no fallback was used."
        )
    client.select_model(model)
    return client


def _process_transactions(
    connection: sqlite3.Connection,
    accounts: AccountRegistry,
    client_provider: Callable[[], ChatGPTConnection],
    counts: dict[str, int],
    *,
    model: str,
    skill: Skill,
    retry_errors: bool,
    blocked: set[UUID],
    progress: Callable[[str], None] | None = None,
    transaction_ids: set[UUID] | None = None, cancelled: Callable[[], bool] | None = None,
) -> None:
    """
    Match and classify sources that are not waiting on a human review, preserving existing safeguards.
    """
    report = progress or (lambda message: None)
    if cancelled and cancelled():
        return
    report("Matching transfers and preparing uncategorised bank transactions...")
    transactions = load_transactions(connection)
    by_account = {account.id: account for account in accounts.accounts.values()}
    posted = posted_transaction_ids(connection)
    pending = [row for row in transactions if row.transaction_id not in posted]
    _check_sources(pending, by_account)
    saved = load_decisions(connection)
    eligible = {
        row.transaction_id for row in pending
        if row.transaction_id not in blocked
        and (transaction_ids is None or row.transaction_id in transaction_ids) and (
            row.transaction_id not in saved
            or (retry_errors and saved[row.transaction_id].status in {"failed", "requesting"})
        )
    }
    transfer_eligible = eligible | {
        identifier for identifier, decision in saved.items()
        if decision.status == "needs_review" and decision.origin == "local"
        and decision.reason == TRANSFER_REVIEW_REASON and identifier not in blocked
        and (transaction_ids is None or identifier in transaction_ids)
    }

    # Find every possible pair before choosing one, so repeated amounts cannot hide an ambiguous match.
    pairs, internal = _match_transfers(pending, by_account)
    for outgoing, incoming in pairs:
        if cancelled and cancelled():
            return
        if (
            outgoing.transaction_id not in transfer_eligible
            or incoming.transaction_id not in transfer_eligible
        ):
            continue
        entry = JournalEntry(
            date=outgoing.date,
            lines=(_bank_line(incoming), _bank_line(outgoing)),
        )
        save_journal_entry(connection, entry, accounts)
        posted.update((outgoing.transaction_id, incoming.transaction_id))
        counts["entries_created"] += 1
        counts["linked_sources"] += 2

    # Unmatched internal movements and zero lines cannot become ordinary income or expense postings.
    local_reviews = []
    for row in pending:
        if row.transaction_id in posted or row.transaction_id not in eligible:
            continue
        if row.amount.is_zero():
            local_reviews.append(_review(
                row, "The statement amount is zero.",
                "Is this a non-financial statement line, or does its amount need correction?",
            ))
        elif row.transaction_id in internal:
            local_reviews.append(_review(
                row, TRANSFER_REVIEW_REASON,
                "Which statement line is the other side of this transfer, or is it a different event?",
            ))
    save_decisions(connection, local_reviews, accounts)
    saved.update({decision.transaction_id: decision for decision in local_reviews})

    work = [
        row for row in pending
        if row.transaction_id not in posted and row.transaction_id not in blocked
        and (transaction_ids is None or row.transaction_id in transaction_ids) and (
            row.transaction_id not in saved
            or (retry_errors and saved[row.transaction_id].status in {"failed", "requesting"})
        )
    ]
    if work:
        client = client_provider()
        _classify_transactions(
            connection, work, accounts, client, counts, posted, model=model, retry_errors=retry_errors,
            skill=skill, progress=report, cancelled=cancelled,
        )


def _classify_transactions(
    connection: sqlite3.Connection,
    transactions: list[Transaction],
    accounts: AccountRegistry,
    client: ChatGPTConnection,
    counts: dict[str, int],
    posted: set[UUID],
    *,
    model: str,
    retry_errors: bool,
    skill: Skill,
    progress: Callable[[str], None] | None = None, cancelled: Callable[[], bool] | None = None,
) -> None:
    """
    Save each complete classification batch and its accounting entries atomically.

    A failed request leaves decisions available for an explicit retry.
    """
    report = progress or (lambda message: None)
    for start in range(0, len(transactions), MAX_BATCH_SIZE):
        if cancelled and cancelled():
            return
        candidates = transactions[start:start + MAX_BATCH_SIZE]
        reserved = set(reserve_decisions(
            connection, [row.transaction_id for row in candidates], model, skill.version,
            accounts=accounts, retry_errors=retry_errors,
        ))
        batch = [row for row in candidates if row.transaction_id in reserved]
        if not batch:
            continue
        report(f"AI: categorising {len(batch)} bank transactions with {model}...")
        counts["requests"] += 1
        try:
            decisions, usage = classify_batch(
                client, batch, accounts, skill=skill, guidance=load_active_guidance(connection),
                explicit_memory=load_explicit_memory(connection),
            )
        except (ChatGPTError, ValueError, OSError) as error:
            record_usage(counts, getattr(error, "usage", {}))
            # These exception types carry messages written by Aurum, without raw provider output.
            reason = str(error) if isinstance(error, (ChatGPTError, CategorisationBatchError)) else (
                "The request failed or its complete response did not pass validation."
            )
            save_decisions(connection, [
                Decision(
                    transaction_id=row.transaction_id, status="failed", model=model,
                    prompt_version=skill.version,
                    reason=reason,
                )
                for row in batch
            ], accounts)
            report("Bank categorisation failed; earlier results are saved. An explicit retry is needed.")
            break

        batch_rows = {row.transaction_id: row for row in batch}
        entries = {
            decision.transaction_id: _classification_entry(
                batch_rows[decision.transaction_id], decision, accounts,
            )
            for decision in decisions if decision.status == "classified"
        }
        save_categorisation_batch(connection, decisions, entries, accounts)
        record_usage(counts, usage)
        counts["entries_created"] += len(entries)
        counts["linked_sources"] += len(entries)
        posted.update(entries)


def _check_sources(transactions: list[Transaction], accounts: dict[UUID, Account]) -> None:
    """
    Reject missing source accounts or sources other than configured asset bank accounts before any requests.
    """
    for row in transactions:
        source = accounts.get(row.account_id)
        if source is None or source.account_type != AccountType.ASSET or source.bank_details is None:
            raise ValueError("Every pending transaction needs a configured asset bank account.")


def _match_transfers(
    transactions: list[Transaction], accounts: dict[UUID, Account],
) -> tuple[list[tuple[Transaction, Transaction]], set[UUID]]:
    """
    Find uniquely paired internal transfers and identify all rows needing internal-transfer handling.

    A pair first needs the same complete trailing # reference. Its two descriptions must identify reciprocal
    BSB/account tuples, with TRANSFER TO on the outgoing row and TRANSFER FROM on the incoming row. Amounts
    must be opposite, source banks distinct and statement dates within three calendar days. Every pending
    row participates in the uniqueness check, including reviews that cannot themselves be posted.

    TRANSFER WITHIN is a separate same-account rule: both rows must name their own configured bank details,
    share the complete reference and statement date, and carry equal opposite amounts. Both source rows
    remain journal postings on that bank account; their total is zero.

    Known internal movements with missing or conflicting details remain in review. Equal amounts alone
    never substitute for a missing transfer reference. Effective dates do not change matching or event dates.

    Returns:
        Outgoing/incoming pairs, and IDs of every identified or potentially matched internal movement.
    """
    bank_accounts = [
        account for account in accounts.values()
        if account.account_type == AccountType.ASSET and account.bank_details is not None
    ]
    bank_ids = {account.id for account in bank_accounts}
    identifiers: dict[tuple[str, str], set[UUID]] = {}
    numbers: set[str] = set()
    for account in bank_accounts:
        details = account.bank_details
        if details is not None:
            identifiers.setdefault((details.bsb, details.account_number), set()).add(account.id)
            numbers.add(details.account_number)

    grouped: dict[str, list[Transaction]] = {}
    counterparts: dict[UUID, UUID] = {}
    within_mentions, within_routes = set(), set()
    internal = set()
    for row in transactions:
        if row.account_id not in bank_ids:
            continue
        # Recognise own-account mentions even when their format is too uncertain for automatic matching.
        transfer_word = re.search(r"\bTRANSFER\b", row.description, re.IGNORECASE)
        known_number = transfer_word is not None and any(
            re.search(
                r"(?<!\w)" + r"[ \t-]*".join(number) + r"(?!\w)",
                row.description,
            )
            for number in numbers
        )
        if known_number or _names_internal_account(row, bank_accounts):
            internal.add(row.transaction_id)
        if re.search(r"\bTRANSFER\s+WITHIN\b", row.description, re.IGNORECASE):
            within_mentions.add(row.transaction_id)
            internal.add(row.transaction_id)

        reference = row.transfer_reference
        if reference is None:
            continue
        # Keep the full reference as text so #00123 and #123 remain different bank references.
        grouped.setdefault(reference, []).append(row)
        if row.transaction_id in within_mentions:
            route = re.fullmatch(
                r"TRANSFER\s+WITHIN\s+([0-9]{6})\s*-\s*([0-9]+)\s+#[0-9]+\s*",
                row.description, re.IGNORECASE,
            )
            if (
                route is not None and row.amount != 0
                and identifiers.get((route[1], route[2])) == {row.account_id}
            ):
                within_routes.add(row.transaction_id)
            continue
        routes = re.findall(
            r"\bTRANSFER\s+(FROM|TO)\s+([0-9]{6})\s*-\s*([0-9]+)(?!\w)",
            row.description, re.IGNORECASE,
        )
        if len(routes) != 1:
            continue
        direction, bsb, number = routes[0]
        owners = identifiers.get((bsb, number), set())
        if len(owners) != 1 or row.amount == 0:
            continue
        expected_direction = "TO" if row.amount < 0 else "FROM"
        if direction.upper() == expected_direction:
            counterparts[row.transaction_id] = next(iter(owners))

    neighbours: dict[UUID, list[Transaction]] = {row.transaction_id: [] for row in transactions}
    for rows in grouped.values():
        if any(row.transaction_id in internal for row in rows):
            internal.update(row.transaction_id for row in rows)
        for index, first in enumerate(rows):
            for second in rows[index + 1:]:
                if (
                    first.amount == 0 or first.amount != -second.amount
                    or abs((first.date - second.date).days) > 3
                ):
                    continue
                if first.account_id == second.account_id and (
                    first.date != second.date
                    or not {first.transaction_id, second.transaction_id} & within_mentions
                ):
                    continue
                # A shared reference and matching cash movement can reveal an incomplete internal transfer.
                internal.update((first.transaction_id, second.transaction_id))
                # Count plausible counterparts before route validation, including malformed or protected rows.
                neighbours[first.transaction_id].append(second)
                neighbours[second.transaction_id].append(first)

    pairs = []
    for outgoing in transactions:
        if outgoing.amount >= 0 or len(neighbours[outgoing.transaction_id]) != 1:
            continue
        incoming = neighbours[outgoing.transaction_id][0]
        within_pair = (
            outgoing.account_id == incoming.account_id
            and {outgoing.transaction_id, incoming.transaction_id} <= within_routes
        )
        between_pair = (
            outgoing.account_id != incoming.account_id
            and counterparts.get(outgoing.transaction_id) == incoming.account_id
            and counterparts.get(incoming.transaction_id) == outgoing.account_id
        )
        if (
            len(neighbours[incoming.transaction_id]) == 1
            and (within_pair or between_pair)
        ):
            pairs.append((outgoing, incoming))
    return pairs, internal


def _names_internal_account(transaction: Transaction, accounts: list[Account]) -> bool:
    """
    Keep explicitly named own-account transfers in review when an exact account-number match is absent.
    """
    description = transaction.description.casefold()
    if "transfer" not in description:
        return False
    if "between my accounts" in description or "between own accounts" in description:
        return True
    for account in accounts:
        if account.id == transaction.account_id:
            continue
        for label in (account.name, account.key.replace("_", " ")):
            if re.search(r"(?<!\w)" + re.escape(label.casefold()) + r"(?!\w)", description):
                return True
    return False


def _bank_line(transaction: Transaction) -> JournalLine:
    """
    Preserve a source transaction's UUID, account, wording, effective date and full signed amount.
    """
    if transaction.amount > 0:
        return JournalLine(
            account_id=transaction.account_id, debit=transaction.amount,
            transaction_id=transaction.transaction_id,
            description=transaction.description,
            effective_date=transaction.effective_date,
        )
    return JournalLine(
        account_id=transaction.account_id, credit=-transaction.amount,
        transaction_id=transaction.transaction_id,
        description=transaction.description,
        effective_date=transaction.effective_date,
    )


def _review(transaction: Transaction, reason: str, question: str) -> Decision:
    """
    Build a saved local review question without asking ChatGPT about an unsupported event.
    """
    return Decision(
        transaction_id=transaction.transaction_id, status="needs_review", origin="local",
        reason=reason, question=question,
    )


def _classification_entry(
    transaction: Transaction,
    decision: Decision,
    accounts: AccountRegistry,
) -> JournalEntry:
    """
    Build the bank and income/expense postings for one validated response without saving anything.

    Original wording remains on both postings.

    Returns:
        A balanced entry ready to be saved together with the complete classification batch.

    Raises:
        ValueError: The decision does not select a compatible configured account for this source.
    """
    selected = next(
        (account for account in accounts.accounts.values() if account.id == decision.account_id), None,
    )
    expected = AccountType.EXPENSE if transaction.amount < 0 else AccountType.INCOME
    if (
        decision.status != "classified" or decision.transaction_id != transaction.transaction_id
        or transaction.amount == 0 or selected is None or selected.account_type != expected
    ):
        raise ValueError("The classification must select a compatible account for this source transaction.")

    bank = _bank_line(transaction)
    counterpart = (
        JournalLine(
            account_id=selected.id, debit=-transaction.amount, description=transaction.description,
            effective_date=transaction.effective_date,
        )
        if transaction.amount < 0 else JournalLine(
            account_id=selected.id, credit=transaction.amount, description=transaction.description,
            effective_date=transaction.effective_date,
        )
    )
    return JournalEntry(
        date=transaction.date,
        lines=(counterpart, bank) if transaction.amount < 0 else (bank, counterpart),
    )


def _summary(
    connection: sqlite3.Connection, counts: dict[str, int] | None = None, *,
    transaction_ids: set[UUID] | None = None, manual_ids: set[UUID] | None = None,
) -> dict[str, int]:
    """
    Count posted sources and remaining accounting work.
    """
    result = dict(counts or {})
    posted = posted_transaction_ids(connection)
    all_ids = {row.transaction_id for row in load_transactions(connection)}
    if transaction_ids is not None:
        all_ids &= transaction_ids
        posted &= transaction_ids
    decisions = load_decisions(connection)
    pending = all_ids - posted
    reviews = {identifier for identifier in pending if identifier in decisions
               and decisions[identifier].status == "needs_review"}
    errors = {identifier for identifier in pending if identifier in decisions
              and decisions[identifier].status in {"failed", "requesting"}}
    manual_records = [
        row for row in load_manual_entries(connection)
        if manual_ids is None or row['manual_id'] in manual_ids
    ]
    manual_sources = set(manual_claims(connection)) & pending
    latest_reviews = {
        row.transaction_id: record for record in load_review_cases(connection)
        for row in record["case"].transactions
    }
    cleared = {
        identifier for identifier, item in load_review_baselines(connection).items()
        if item.get("human_review_cleared", False)
    }
    human_questions = {identifier for identifier, record in latest_reviews.items()
                       if record["status"] == "needs_review" and identifier in all_ids
                       and any(answer.transaction_id not in cleared for answer in record["case"].answers)}
    human_errors = {identifier for identifier, record in latest_reviews.items()
                    if record["status"] in {"failed", "requesting"} and identifier in all_ids
                    and any(answer.transaction_id not in cleared for answer in record["case"].answers)}
    result.update(
        transactions=len(all_ids), posted_sources=len(posted), needs_review=len(reviews), errors=len(errors),
        unprocessed=len(pending - reviews - errors - human_questions - human_errors - manual_sources),
        manual_waiting_sources=len(manual_sources),
        human_review_questions=len(human_questions), human_review_errors=len(human_errors),
        human_review_stale=sum(
            item.get("review_status") == "stale"
            for identifier, item in load_review_baselines(connection).items()
            if identifier in all_ids and identifier not in cleared
        ),
        manual_pending=sum(row["status"] not in {"applied", "withdrawn"} for row in manual_records),
        manual_applied=sum(row["status"] == "applied" for row in manual_records),
        manual_queued=sum(row["status"] == "pending" for row in manual_records),
        manual_questions_saved=sum(row["status"] == "needs_review" for row in manual_records),
        manual_errors_saved=sum(row["status"] in {"failed", "requesting"} for row in manual_records),
        manual_unsupported=sum(row["status"] == "unsupported" for row in manual_records),
        manual_pricing_retries=sum(
            row["status"] == "needs_review" and row["investment_proposal"] is not None
            for row in manual_records
        ),
    )
    return result
