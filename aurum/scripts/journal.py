# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Scripts - Journal                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Prepare Aurum's journal from imported bank transactions, manual instructions and human review answers.

Manual entries and changed review answers run first. Python then matches clear transfers between configured
bank accounts and asks ChatGPT to categorise other clear income or expenses. Complete events post atomically;
uncertainty and failures stay in private processing history for review or explicit retry.

The journal stores posting facts and status. Source-linked lines retain their original bank amounts,
accounts, descriptions and effective dates. Investment opening holdings and purchases use configured
securities, supplied trade facts and cached AUD reference prices where needed.

Run python aurum/scripts/journal.py after PDF importing or editing monthly CSV answers. Use --review for
read-only saved questions, --retry-errors for failed requests or pricing, and --learn-guidance for accepted
bank reviews. USER_GUIDE.md describes configuration, supported instructions and the complete workflow.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import argparse
from collections.abc import Callable
from contextlib import AbstractContextManager, ExitStack, closing
from pathlib import Path
import re
import shlex
import sqlite3
import sys
from uuid import UUID

from pydantic import ValidationError

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

# Aurum imports
from aurum.ai.categorisation import CategorisationBatchError, Decision, MAX_BATCH_SIZE, classify_batch
from aurum.ai.chatgpt import ChatGPTConnection, ChatGPTError, connect, record_usage
from aurum.ai.skills import Skill, load_categorisation_skill, load_manual_skill
from aurum.database.database import DEFAULT_DATABASE, _check_schema, open_database
from aurum.database.categorisation import (
    load_decisions, reserve_decisions, save_categorisation_batch, save_decisions,
)
from aurum.database.journal import posted_transaction_ids, prepare_journal, save_journal_entry
from aurum.database.transactions import load_transactions
from aurum.database.manual import load_manual_entries, manual_claims
from aurum.finance.accounts import DEFAULT_ACCOUNTS, Account, AccountRegistry, AccountType, load_accounts
from aurum.finance.journal import JournalEntry, JournalLine
from aurum.finance.manual import ManualOutcome
from aurum.finance.securities import DEFAULT_SECURITIES, SecurityRegistry, load_securities
from aurum.finance.transaction import Transaction
from aurum.database.reviews import (
    list_guidance_cases, load_active_guidance, load_guidance_assessments,
    load_review_cases, load_review_exports,
)
from aurum.workflows.review import (
    ReviewFileError, export_guidance, export_reviews, process_guidance, process_reviews, read_reviews,
)
from aurum.workflows.manual import coordinate_manuals, export_manuals, process_manuals, read_manuals

DEFAULT_MODEL = "gpt-6-luna"
TRANSFER_REVIEW_REASON = "A possible internal transfer has no unique eligible counterpart."


# %% 1. Journal Processing -----------------------------------------------------------------------------------
def process_journal(
    connection: sqlite3.Connection,
    accounts: AccountRegistry,
    *,
    model: str = DEFAULT_MODEL,
    retry_errors: bool = False,
    reviews: Path | None = None,
    manuals: Path | None = None,
    securities: SecurityRegistry | None = None,
    quote_fetcher: Callable | None = None,
    client_factory: Callable[[], AbstractContextManager[ChatGPTConnection]] = connect,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """
    Match transfers and save each validated classification batch together with its accounting entries.

    Args:
        connection: Initialised SQLite connection without a pending write.
        accounts: Configured bank and category accounts.
        model: Explicit ChatGPT model slug; an unavailable model raises an error.
        retry_errors: Allow failed or interrupted requests to be attempted again.
        reviews: Monthly CSV directory; None leaves CSV exchange disabled for library calls.
        manuals: Manual instruction CSV directory; None leaves manual processing disabled for library calls.
        securities: Configured instruments; None leaves investment posting unavailable for library calls.
        quote_fetcher: Optional synthetic market provider used by isolated checks.
        client_factory: Connection factory, replaceable with a synthetic client for local checks.
        progress: Optional callback for brief stage messages; library calls are quiet by default.

    Returns:
        Counts for this run, remaining reviews and reported token usage.

    Raises:
        ValueError: A source account is missing or unsupported.
        ChatGPTError: Sign-in or model selection fails before a batch can be sent.
        sqlite3.Error: Decisions or journal entries cannot be stored safely.
    """
    report = progress or (lambda message: None)
    report("Reading bank reviews and manual instructions...")
    counts = {
        "entries_created": 0, "linked_sources": 0,
        "already_posted": len(posted_transaction_ids(connection)),
        "requests": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
        "reviews_applied": 0, "review_questions": 0, "review_failed": 0,
        "manual_posted": 0, "manual_questions": 0, "manual_failed": 0,
    }
    skill = load_categorisation_skill()
    session = read_reviews(connection, accounts, reviews, retry_errors=retry_errors) if reviews else None
    manual_session = manual_skill = None
    if manuals:
        manual_skill = load_manual_skill()
        manual_session = read_manuals(
            connection, accounts, manuals, retry_errors=retry_errors, skill_version=manual_skill.version,
            securities=securities,
        )
    prepare_journal(connection, accounts)
    try:
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

            if manual_session is not None and manual_skill is not None:
                coordinate_manuals(connection, accounts, manual_session, session)
                process_manuals(
                    connection, accounts, manual_session, get_client, counts, model=model,
                    skill=manual_skill, retry_errors=retry_errors,
                    securities=securities, quote_fetcher=quote_fetcher, progress=report,
                )
            if session is not None:
                process_reviews(
                    connection, accounts, session, get_client, counts, model=model, skill=skill,
                    retry_errors=retry_errors, progress=report,
                )
            _process_transactions(
                connection, accounts, get_client, counts, model=model, skill=skill,
                retry_errors=retry_errors, blocked=(session.blocked if session else set())
                | (manual_session.blocked if manual_session else set()), progress=report,
            )
    finally:
        if manual_session is not None:
            export_manuals(connection, accounts, manual_session)
            counts["manual_file_conflicts"] = manual_session.conflicts
            counts["manual_local_questions"] = len(manual_session.messages)
        if session is not None:
            export_reviews(connection, accounts, session)
            counts["review_file_conflicts"] = session.conflicts
    return _summary(connection, counts)


def learn_from_reviews(
    connection: sqlite3.Connection,
    accounts: AccountRegistry,
    *,
    model: str = DEFAULT_MODEL,
    retry_errors: bool = False,
    guidance_path: Path,
    client_factory: Callable[[], AbstractContextManager[ChatGPTConnection]] = connect,
    progress: Callable[[str], None] | None = None,
) -> dict[str, int]:
    """
    Backfill learning outcomes from accepted reviews while keeping all accounting and CSV answers unchanged.

    A connection is opened only when an eligible review needs inference. Completed assessments are reused;
    --retry-errors permits another attempt at a failed or interrupted assessment.
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
                progress=progress,
            )
    finally:
        export_guidance(connection, guidance_path)
    return counts


def main() -> int:
    """
    Run journal preparation, inspect saved reviews or learn guidance from accepted answers.

    Returns:
        Zero on success, one for errors or unfinished accounting, or 130 when interrupted.
    """
    parser = argparse.ArgumentParser(description="Prepare Aurum's journal from imported bank transactions.")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="SQLite database path.")
    parser.add_argument("--accounts", type=Path, default=DEFAULT_ACCOUNTS, help="Private account YAML path.")
    parser.add_argument(
        "--securities", type=Path, default=DEFAULT_SECURITIES, help="Private security YAML path.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"ChatGPT model; default: {DEFAULT_MODEL}.")
    parser.add_argument(
        "--retry-errors", action="store_true",
        help="Retry errors, saved pricing questions and unsupported manuals from an older skill.",
    )
    parser.add_argument(
        "--reviews", type=Path, help="Monthly review CSV directory; default: reviews beside the database.",
    )
    parser.add_argument(
        "--manuals", type=Path, help="Manual instruction CSV directory; default: manual beside the database.",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--review", action="store_true",
        help="Read saved questions and errors without changes.",
    )
    modes.add_argument(
        "--learn-guidance", action="store_true",
        help="Learn only from accepted reviews; leave journal postings and review CSVs unchanged.",
    )
    args = parser.parse_args()

    mode = (
        "Saved journal issues" if args.review else "Guidance learning" if args.learn_guidance else "Journal"
    )
    print(f"Aurum | {mode}")
    print("Loading configuration and database...")
    try:
        accounts = load_accounts(args.accounts, initialise_ids=not (args.review or args.learn_guidance))
        if args.review:
            uri = args.database.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as database:
                return 0 if _show_review(database, accounts) else 1
        securities = None
        if not args.learn_guidance:
            if args.securities.exists():
                securities = load_securities(args.securities)
            elif args.securities != DEFAULT_SECURITIES:
                raise ValueError("The selected security configuration does not exist")
            else:
                securities = SecurityRegistry([])
        with closing(open_database(args.database)) as database:
            if args.learn_guidance:
                path = args.database.resolve().parent / "ai" / "categorisation_guidelines.md"
                try:
                    result = learn_from_reviews(
                        database, accounts, model=args.model, retry_errors=args.retry_errors,
                        guidance_path=path, progress=lambda message: print(message, flush=True),
                    )
                except (KeyboardInterrupt, EOFError):
                    print("Stopped. Guidance needs --retry-errors to resume; accounting is unchanged.")
                    return 130
                except (ChatGPTError, ValueError, sqlite3.Error, OSError):
                    print("Guidance learning stopped. Saved progress remains; accounting is unchanged.")
                    return 1
                print(
                    f"Guidance assessments this run: {result['guidance_assessed']}; "
                    f"AI requests: {result['requests']}; reported tokens: {result['total_tokens']}."
                )
                failed = _print_guidance_status(
                    database, accounts,
                    retry_command=_journal_command(args, "--learn-guidance", "--retry-errors"),
                )
                print(f"Guidance and learning outcomes: {path}")
                return 1 if failed else 0
            try:
                result = process_journal(
                    database, accounts, model=args.model, retry_errors=args.retry_errors,
                    reviews=args.reviews or args.database.resolve().parent / "reviews",
                    manuals=args.manuals or args.database.resolve().parent / "manual",
                    securities=securities, progress=lambda message: print(message, flush=True),
                )
            except (KeyboardInterrupt, EOFError):
                print("\nStopped. Saved decisions remain; interrupted requests need --retry-errors.")
                _print_summary(_summary(database))
                return 130
            except (ChatGPTError, ValueError, sqlite3.Error, OSError) as error:
                if isinstance(error, (ChatGPTError, ReviewFileError)):
                    print(f"Journal preparation stopped: {error}", file=sys.stderr)
                else:
                    print(f"Journal preparation stopped: {_safe_error(error)}", file=sys.stderr)
                _print_summary(_summary(database))
                return 1
            _print_summary(result)
            _print_guidance_status(
                database, accounts,
                retry_command=_journal_command(args, "--learn-guidance", "--retry-errors"),
            )
            _print_next_steps(database, args, result)
            incomplete = (
                result["errors"] or result["unprocessed"] or result["needs_review"]
                or result.get("human_review_questions", 0)
                or result.get("review_failed", 0) or result.get("review_file_conflicts", 0)
                or result.get("human_review_errors", 0)
                or result.get("human_review_stale", 0)
                or result.get("manual_failed", 0) or result.get("manual_file_conflicts", 0)
                or result.get("manual_pending", 0) or result.get("manual_local_questions", 0)
            )
            return 1 if incomplete else 0
    except (ValueError, sqlite3.Error, OSError) as error:
        print(f"Could not load Aurum: {_safe_error(error)}", file=sys.stderr)
        print("Check --accounts, --securities and --database.", file=sys.stderr)
        if args.review:
            print(
                "--review is read-only. If accounts have no IDs yet, call load_accounts() once to save them.",
                file=sys.stderr,
            )
        return 1


# %% 2. Helpers ----------------------------------------------------------------------------------------------
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


def _print_guidance_status(
    connection: sqlite3.Connection, accounts: AccountRegistry, *, retry_command: str,
) -> int:
    """
    Show saved learning progress separately from accounting, returning the number needing an explicit retry.
    """
    actions = load_guidance_assessments(connection)
    completed = sum(action["status"] == "completed" for action in actions)
    failed_ids = {
        action["review_action_id"] for action in actions if action["status"] in {"requesting", "failed"}
    }
    eligible = {
        case.id for case in list_guidance_cases(connection, accounts, retry_errors=True)
    } if failed_ids else set()
    failed = len(failed_ids & eligible)
    historical = len(failed_ids - eligible)
    active = len(load_active_guidance(connection))
    if actions or active:
        print(
            f"Guidance: {active} active guidelines; "
            f"{completed} completed assessments; {failed} failed/interrupted."
        )
    if failed:
        print("Read learning outcomes in categorisation_guidelines.md.")
        print(f"Retry guidance: {retry_command}")
    if historical:
        print(f"Historical guidance failures with replaced evidence: {historical}; no retry needed.")
    return failed


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
) -> None:
    """
    Match and classify sources that are not waiting on a human review, preserving existing safeguards.
    """
    report = progress or (lambda message: None)
    report("Matching transfers and preparing uncategorised bank transactions...")
    transactions = load_transactions(connection)
    by_account = {account.id: account for account in accounts.accounts.values()}
    posted = posted_transaction_ids(connection)
    pending = [row for row in transactions if row.transaction_id not in posted]
    _check_sources(pending, by_account)
    saved = load_decisions(connection)
    eligible = {
        row.transaction_id for row in pending
        if row.transaction_id not in blocked and (
            row.transaction_id not in saved
            or (retry_errors and saved[row.transaction_id].status in {"failed", "requesting"})
        )
    }
    transfer_eligible = eligible | {
        identifier for identifier, decision in saved.items()
        if decision.status == "needs_review" and decision.origin == "local"
        and decision.reason == TRANSFER_REVIEW_REASON and identifier not in blocked
    }

    # Find every possible pair before choosing one, so repeated amounts cannot hide an ambiguous match.
    pairs, internal = _match_transfers(pending, by_account)
    for outgoing, incoming in pairs:
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
        if row.transaction_id not in posted and row.transaction_id not in blocked and (
            row.transaction_id not in saved
            or (retry_errors and saved[row.transaction_id].status in {"failed", "requesting"})
        )
    ]
    if work:
        client = client_provider()
        _classify_transactions(
            connection, work, accounts, client, counts, posted, model=model, retry_errors=retry_errors,
            skill=skill, progress=report,
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
    progress: Callable[[str], None] | None = None,
) -> None:
    """
    Save each complete classification batch and its accounting entries atomically.

    A failed request leaves decisions available for an explicit retry.
    """
    report = progress or (lambda message: None)
    for start in range(0, len(transactions), MAX_BATCH_SIZE):
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

        reference = row.transfer_reference
        if reference is None:
            continue
        # Keep the full reference as text so #00123 and #123 remain different bank references.
        grouped.setdefault(reference, []).append(row)
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
                    first.account_id == second.account_id or first.amount == 0
                    or first.amount != -second.amount or abs((first.date - second.date).days) > 3
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
        if (
            len(neighbours[incoming.transaction_id]) == 1
            and counterparts.get(outgoing.transaction_id) == incoming.account_id
            and counterparts.get(incoming.transaction_id) == outgoing.account_id
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


def _summary(connection: sqlite3.Connection, counts: dict[str, int] | None = None) -> dict[str, int]:
    """
    Count posted sources and remaining accounting work.
    """
    result = dict(counts or {})
    posted = posted_transaction_ids(connection)
    all_ids = {row.transaction_id for row in load_transactions(connection)}
    decisions = load_decisions(connection)
    pending = all_ids - posted
    reviews = {identifier for identifier in pending if identifier in decisions
               and decisions[identifier].status == "needs_review"}
    errors = {identifier for identifier in pending if identifier in decisions
              and decisions[identifier].status in {"failed", "requesting"}}
    manual_records = load_manual_entries(connection)
    manual_sources = set(manual_claims(connection)) & pending
    latest_reviews = {
        row.transaction_id: record for record in load_review_cases(connection)
        for row in record["case"].transactions
    }
    human_questions = {identifier for identifier, record in latest_reviews.items()
                       if record["status"] == "needs_review"}
    human_errors = {identifier for identifier, record in latest_reviews.items()
                    if record["status"] in {"failed", "requesting"}}
    result.update(
        transactions=len(all_ids), posted_sources=len(posted), needs_review=len(reviews), errors=len(errors),
        unprocessed=len(pending - reviews - errors - human_questions - human_errors - manual_sources),
        manual_waiting_sources=len(manual_sources),
        human_review_questions=len(human_questions), human_review_errors=len(human_errors),
        human_review_stale=sum(
            item.get("review_status") == "stale" for item in load_review_exports(connection).values()
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


def _print_summary(result: dict[str, int]) -> None:
    """
    Separate work completed this run from the saved bank, manual and human-review queues.
    """
    if "entries_created" in result:
        print(
            f"\nThis run: {result['entries_created']} bank events posted; "
            f"{result.get('manual_posted', 0)} manual entries applied; "
            f"{result.get('reviews_applied', 0)} human reviews applied."
        )
        requests, tokens = result["requests"], result["total_tokens"]
        usage = f"{tokens} reported tokens" if tokens else "token usage unavailable"
        print(f"AI: {requests} requests; {usage}." if requests else "AI: no requests sent.")
    print(
        f"Bank transactions: {result['posted_sources']}/{result['transactions']} posted; "
        f"{result['needs_review']} need review; {result['errors']} failed/interrupted; "
        f"{result.get('manual_waiting_sources', 0)} waiting on manual entries; "
        f"{result['unprocessed']} unprocessed."
    )
    if result.get("manual_applied") or result.get("manual_pending") or result.get("manual_local_questions"):
        print(
            f"Manual instructions: {result.get('manual_applied', 0)} applied; "
            f"{result.get('manual_queued', 0)} pending; {result.get('manual_questions_saved', 0)} questions; "
            f"{result.get('manual_errors_saved', 0)} failed/interrupted; "
            f"{result.get('manual_unsupported', 0)} unsupported; "
            f"{result.get('manual_local_questions', 0)} input/conflict issues."
        )
    if any(result.get(name) for name in (
        "human_review_questions", "human_review_errors", "human_review_stale",
    )):
        print(
            f"Human review source rows: {result.get('human_review_questions', 0)} questions; "
            f"{result.get('human_review_errors', 0)} failed/interrupted; "
            f"{result.get('human_review_stale', 0)} stale answers."
        )


def _journal_command(args: argparse.Namespace, *flags: str) -> str:
    """
    Build a copyable journal command retaining nondefault paths and the selected model.
    """
    parts = ["python", "aurum/scripts/journal.py"]
    defaults = {
        "accounts": DEFAULT_ACCOUNTS, "securities": DEFAULT_SECURITIES, "database": DEFAULT_DATABASE,
        "model": DEFAULT_MODEL, "reviews": None, "manuals": None,
    }
    for name, default in defaults.items():
        value = getattr(args, name)
        if value is not None and value != default:
            parts.extend(("--" + name, str(value)))
    return shlex.join([*parts, *flags])


def _print_next_steps(
    connection: sqlite3.Connection, args: argparse.Namespace, result: dict[str, int],
) -> None:
    """
    Show relevant CSV actions and explicit retry commands without repeating the full user guide.
    """
    normal = _journal_command(args)
    waiting = any(result.get(name) for name in (
        "needs_review", "human_review_questions", "human_review_stale",
    ))
    conflicts = result.get("manual_file_conflicts", 0) + result.get("review_file_conflicts", 0)
    if conflicts:
        print(f"{conflicts} CSV files changed during processing. Save your edits, then run: {normal}")
    if waiting:
        folder = args.reviews or args.database.resolve().parent / "reviews"
        print(f"Review CSVs: {folder} — edit human_review, save, then run: {normal}")
    if result.get("manual_pending") or result.get("manual_local_questions"):
        folder = args.manuals or args.database.resolve().parent / "manual"
        months = sorted({
            f"{record['submission'].date:%Y-%m}.csv" for record in load_manual_entries(connection)
            if record["status"] not in {"applied", "withdrawn"}
        })
        location = ", ".join(months[:3]) if months else "monthly CSVs"
        print(f"Manual CSVs: {folder} ({location}) — check question, date/instruction or human_response.")
        print(f"After saving changed answers, run: {normal}")
    retry = any(result.get(name) for name in (
        "errors", "human_review_errors", "manual_errors_saved", "manual_pricing_retries",
    ))
    if retry:
        print(f"Retry failed requests or saved pricing: {_journal_command(args, '--retry-errors')}")
    if waiting or result.get("manual_pending") or result.get("manual_local_questions") or retry:
        print(f"Inspect saved issues: {_journal_command(args, '--review')}")
    elif not result["unprocessed"] and not conflicts:
        print(f"Up to date. Refresh {args.database} in PyCharm to inspect the journal.")


def _safe_error(error: Exception) -> str:
    """
    Show validation locations without repeating private input values.
    """
    if isinstance(error, ValidationError):
        fields = [".".join(map(str, item["loc"])) for item in error.errors(include_input=False)[:3]]
        date_hint = (
            ". Dates accept YYYY-MM-DD or DD-MM-YYYY."
            if any("date" in item["loc"][-1] for item in error.errors(include_input=False)
                   if item["loc"] and isinstance(item["loc"][-1], str)) else ""
        )
        return "Check configuration fields: " + ", ".join(fields) + date_hint
    return str(error)


def _show_review(connection: sqlite3.Connection, accounts: AccountRegistry) -> bool:
    """
    Print saved questions and errors using a database connection opened in read-only mode.

    Returns:
        True when the journal can be read, or False when it is missing or uses an unsupported older schema.
    """
    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    if "journal" not in tables:
        print("No journal exists yet. Run this command without --review to create it.")
        return False
    try:
        _check_schema(connection)
    except ValueError as error:
        print(str(error))
        return False
    decisions = load_decisions(connection)
    posted = posted_transaction_ids(connection)
    by_account = {account.id: account for account in accounts.accounts.values()}
    rows = {row.transaction_id: row for row in load_transactions(connection)}
    shown = 0
    for identifier, decision in decisions.items():
        if identifier in posted:
            continue
        row = rows[identifier]
        account = by_account.get(row.account_id)
        label = account.name if account is not None else "unknown account"
        print(f"\n{row.date} | {label} | AUD {row.amount:.2f} | {identifier}")
        print(f"  {decision.status}: {decision.reason}")
        if decision.question is not None:
            print(f"  Question: {decision.question}")
        shown += 1
    if "review_actions" in tables:
        latest_reviews = {
            row.transaction_id: record for record in load_review_cases(connection)
            for row in record["case"].transactions
        }
        for record in load_review_cases(connection):
            if record["status"] in {"applied", "superseded"} or not any(
                latest_reviews.get(row.transaction_id) is not None
                and latest_reviews[row.transaction_id]["id"] == record["id"]
                for row in record["case"].transactions
            ):
                continue
            outcome = record["outcome"]
            detail = record["error"] or "Request interrupted."
            if outcome is not None:
                detail = outcome.question or outcome.reason
            print(f"\nHuman review {record['id']} | {record['status']}")
            print(f"  {detail}")
            shown += 1
        for identifier, exported in load_review_exports(connection).items():
            if exported.get("review_status") == "stale":
                print(f"\nHuman review {identifier} | stale")
                detail = exported.get("review_result", "Check the refreshed CSV before editing its answer.")
                print(f"  {detail}")
                shown += 1
    if "manual_entries" in tables:
        for record in load_manual_entries(connection):
            if record["status"] in {"applied", "withdrawn"}:
                continue
            print(f"\nManual entry {record['submission'].id} | {record['status']}")
            manual_outcome: ManualOutcome | None = record.get("outcome")
            detail = record.get("error") or record.get("question") or (
                manual_outcome.reason if manual_outcome is not None else "Instruction has not completed."
            )
            print(f"  {detail}")
            shown += 1
    print(f"\n{shown} saved review/error items. No changes or ChatGPT requests were made.")
    return True


# %% 3. Execution --------------------------------------------------------------------------------------------
if __name__ == "__main__":
    raise SystemExit(main())
