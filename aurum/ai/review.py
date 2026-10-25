# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     AI - Review                                                      #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum uses a person's review answers to resolve uncertain events and correct existing journal entries.

This module asks for complete posting plans using the supplied accounts and source transactions. Python
preserves every original bank fact, checks exact source coverage and balanced amounts, and owns event UUIDs.
The same response can suggest narrowly scoped guidance for future transactions; unusable optional guidance
never discards a valid accounting plan. Guidance can also be assessed from already accepted reviews without
proposing accounting changes. Each assessment records useful
preferences, an explicit reason for learning none, or safe validation errors. Database saving and the monthly
CSV workflow remain outside this module.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import UTC, date, datetime
from decimal import Decimal
import json
import re
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# Aurum imports
from aurum.ai.chatgpt import ChatGPTConnection
from aurum.ai.privacy import redact_bank_details
from aurum.ai.skills import Skill, guidance_context, load_categorisation_skill
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.budget import BudgetSettings, budget_catalogue, validate_budget_labels
from aurum.finance.journal import JournalEntry, JournalLine, preserve_budget_categories
from aurum.finance.review import Guidance, GuidanceResult, ReviewCase, ReviewOutcome
from aurum.finance.transaction import Transaction


MAX_REVIEW_BATCH_SIZE = 20


# %% 1. Review Responses -------------------------------------------------------------------------------------
class ReviewBatchError(ValueError):
    """
    Explain an unusable completed response while retaining its provider-reported token usage.
    """

    def __init__(self, message: str, usage: dict) -> None:
        """
        Keep a safe diagnostic and a separate copy of the completed request's usage.
        """
        super().__init__(message)
        self.usage = dict(usage)


class _Posting(BaseModel):
    """
    Accept an account-key posting plan, with an optional reference to an unchanged imported source.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    account_key: str
    budget_category: str | None = None
    source_ref: str | None
    debit: str = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$")
    credit: str = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]{1,2})?$")
    description: str | None = Field(min_length=1, max_length=240)


class _Event(BaseModel):
    """
    Describe a complete balanced event without letting the model assign persistent identifiers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    date: date
    lines: tuple[_Posting, ...] = Field(min_length=2, max_length=50)


class _Review(BaseModel):
    """
    Return either complete proposed events or a specific question that still needs a human answer.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)

    case_ref: str
    status: Literal["apply", "needs_review"]
    reason: str = Field(min_length=1, max_length=240)
    question: str | None = Field(min_length=1, max_length=240)
    entries: tuple[_Event, ...]

    @model_validator(mode="after")
    def _check_state(self) -> Self:
        """
        Keep an applied plan separate from an unresolved review question.
        """
        if self.status == "apply" and (not self.entries or self.question is not None):
            raise ValueError("An applied review needs entries and no question")
        if self.status == "needs_review" and (self.entries or self.question is None):
            raise ValueError("An unresolved review needs a question and no entries")
        return self


class _ReviewBatch(BaseModel):
    """
    Describe the requested batch schema; completed cases are validated independently when read.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    outcomes: tuple[_Review, ...] = Field(min_length=1, max_length=MAX_REVIEW_BATCH_SIZE)


# %% 2. Review Resolution ------------------------------------------------------------------------------------
def review_batch(
    client: ChatGPTConnection,
    cases: list[ReviewCase],
    accounts: AccountRegistry,
    *,
    guidance: list[Guidance] | None = None,
    skill: Skill | None = None,
    questions: dict[UUID, str | None] | None = None,
    budget: BudgetSettings | None = None,
) -> tuple[list[ReviewOutcome], dict[UUID, str], dict]:
    """
    Request complete accounting plans for answered review cases without writing any database records.

    Args:
        client: Authenticated connection with an explicitly selected model.
        cases: At most twenty independent groups of source rows, current events and human answers.
        accounts: Registry of every account available for a proposed event.
        guidance: Previously inferred private guidance, used as context rather than automatic rules.
        skill: Accounting instructions loaded once by the command, or None to load the project skill here.
        questions: Current saved review questions, used to interpret brief answers without changing them.
        budget: Optional permitted reporting labels for explicit human exceptions to ordinary account mapping.

    Returns:
        Valid outcomes in input order, safe failure messages keyed by case UUID, and provider-reported usage.
        One invalid case does not discard its neighbours. Rejected guidance candidates retain safe errors
        while the accounting result remains usable.

    Raises:
        ValueError: Sources overlap or an input answer or account is invalid before inference.
        ReviewBatchError: Completed JSON or its case references cannot be interpreted safely; retains usage.
        ChatGPTError: The request fails or does not complete.
    """
    if not cases:
        return [], {}, {}
    model = client.model
    if not model:
        raise ValueError("Select a model before resolving reviews")
    skill = skill or load_categorisation_skill()
    references, sources, guidance_refs, payload = _request_context(
        cases, accounts, guidance, questions=questions, budget=budget,
    )
    response, usage = client.request(
        instructions=skill.instructions, payload=payload,
        output_schema=_response_schema(references, sources, accounts, guidance_refs, budget),
    )
    grouped = _read_response(response, references, usage)
    outcomes = []
    failures = {}
    for reference, case in references.items():
        records = grouped.get(reference, [])
        if not records:
            failures[case.id] = "ValueError: the response omitted this case"
            continue
        if len(records) != 1:
            failures[case.id] = "ValueError: the response repeated this case reference"
            continue
        try:
            result, extra = _read_case(records[0])
            entries = _build_entries(case, result, sources, accounts, budget)
            inferred = (
                _read_guidance(extra, accounts, guidance_refs) if entries
                else GuidanceResult(reason="Accounting review is unresolved.")
            )
            outcomes.append(ReviewOutcome(
                case_id=case.id, status=result.status, entries=entries, reason=result.reason,
                question=result.question, guidance=inferred.guidance, guidance_reason=inferred.reason,
                guidance_errors=inferred.errors,
                model=model, skill_version=skill.version, updated_at=datetime.now(UTC),
            ))
        except (TypeError, ValueError) as error:
            failures[case.id] = _validation_message(error)
    return outcomes, failures, usage


# %% 3. Guidance Learning ------------------------------------------------------------------------------------
def learn_guidance_batch(
    client: ChatGPTConnection, cases: list[ReviewCase], accounts: AccountRegistry,
    *, guidance: list[Guidance] | None = None, skill: Skill | None = None,
) -> tuple[dict[UUID, GuidanceResult], dict[UUID, str], dict]:
    """
    Assess reusable preferences from accepted reviews without asking for replacement accounting.

    Args:
        client: Authenticated connection with an explicitly selected model.
        cases: At most twenty accepted source groups with human answers and their current complete entries.
        accounts: Current account registry used to validate learned account references.
        guidance: Existing private guidance available for explicit supersession or duplicate recognition.
        skill: Instructions loaded by the command, or None to load the project skills here.

    Returns:
        Assessments and safe case failures indexed by case UUID, followed by reported token usage. Invalid
        individual candidates remain in assessment errors while valid neighbours are retained.

    Raises:
        ValueError: Input cases are incomplete, overlap, or lack accepted entries or a selected model.
        ReviewBatchError: Completed JSON or case references are unusable; reported usage is retained.
        ChatGPTError: The request fails or does not complete.
    """
    if not cases:
        return {}, {}, {}
    if not client.model:
        raise ValueError("Select a model before learning guidance")
    references, _, guidance_refs, payload = _request_context(cases, accounts, guidance)
    for case in references.values():
        linked = {line.transaction_id for entry in case.entries for line in entry.lines
                  if line.transaction_id is not None}
        if not case.entries or linked != {row.transaction_id for row in case.transactions}:
            raise ValueError("Guidance learning needs complete accepted entries for every source")
    payload["mode"] = "learn_guidance"
    for case_payload in payload["cases"]:
        case_payload["accepted_entries"] = case_payload.pop("current_entries")
    skill = skill or load_categorisation_skill()
    fields = _guidance_fields(accounts, guidance_refs)
    fields["case_ref"] = {"type": "string", "enum": list(references)}
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {"outcomes": {
            "type": "array", "minItems": len(cases), "maxItems": len(cases),
            "items": {"type": "object", "additionalProperties": False,
                      "properties": fields, "required": list(fields)},
        }},
        "required": ["outcomes"],
    }
    response, usage = client.request(instructions=skill.instructions, payload=payload, output_schema=schema)
    grouped = _read_response(response, references, usage)
    assessments, failures = {}, {}
    for reference, case in references.items():
        records = grouped.get(reference, [])
        if len(records) != 1:
            failures[case.id] = "ValueError: the guidance response omitted or repeated this case"
        elif set(records[0]) - {"case_ref", "guidance", "guidance_reason"}:
            failures[case.id] = "ValueError: the guidance response included unexpected fields"
        else:
            assessments[case.id] = _read_guidance(records[0], accounts, guidance_refs)
    return assessments, failures, usage


# %% 4. Helpers ----------------------------------------------------------------------------------------------
def _request_context(
    cases: list[ReviewCase], accounts: AccountRegistry, guidance: list[Guidance] | None,
    *, questions: dict[UUID, str | None] | None = None, budget: BudgetSettings | None = None,
) -> tuple[dict, dict, dict, dict]:
    """
    Validate distinct review groups and prepare the same redacted evidence for accounting and guidance.
    """
    if len(cases) > MAX_REVIEW_BATCH_SIZE or len({case.id for case in cases}) != len(cases):
        raise ValueError("A review batch needs at most twenty distinct cases")
    cases = [ReviewCase.model_validate(case) for case in cases]
    source_ids = [row.transaction_id for case in cases for row in case.transactions]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("Review cases in one batch cannot share a source transaction")
    event_ids = [entry.id for case in cases for entry in case.entries]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("Review cases in one batch cannot share a current event")
    references = {f"c{index}": case for index, case in enumerate(cases, start=1)}
    sources = {f"t{index}": row for index, row in enumerate(
        (row for case in cases for row in case.transactions), start=1,
    )}
    source_refs = {row.transaction_id: reference for reference, row in sources.items()}
    context = " ".join(
        [row.description for row in sources.values()]
        + [answer.human_review for case in cases for answer in case.answers]
    )
    learned, guidance_refs = guidance_context(guidance, accounts, context)
    payload = _review_payload(references, source_refs, accounts, learned, questions=questions, budget=budget)
    return references, sources, guidance_refs, payload


def _review_payload(
    references: dict, source_refs: dict, accounts: AccountRegistry, guidance: list,
    *, questions: dict[UUID, str | None] | None = None, budget: BudgetSettings | None = None,
) -> dict:
    """
    Send short references and relevant facts while retaining UUIDs and bank identifiers locally.
    """
    keys = {account.id: key for key, account in accounts.accounts.items()}
    by_id = {account.id: account for account in accounts.accounts.values()}
    questions = questions or {}
    cases = []
    for reference, case in references.items():
        if any(
            row.account_id not in by_id or by_id[row.account_id].account_type != AccountType.ASSET
            or by_id[row.account_id].bank_details is None
            for row in case.transactions
        ):
            raise ValueError("Every review source must have a configured asset bank account")
        if any(line.account_id not in keys for entry in case.entries for line in entry.lines):
            raise ValueError("Every current posting must have a configured account")
        if not case.answers:
            raise ValueError("A review request needs a human answer")
        cases.append({
            "case_ref": reference,
            "required_source_refs": [source_refs[row.transaction_id] for row in case.transactions],
            "sources": [
                {
                    "source_ref": source_refs[row.transaction_id], "account_key": keys[row.account_id],
                    "date": row.date.isoformat(),
                    "effective_date": row.effective_date.isoformat() if row.effective_date else None,
                    "description": redact_bank_details(row.description, accounts),
                    "amount": format(row.amount, ".2f"),
                }
                for row in case.transactions
            ],
            "current_entries": [
                {
                    "entry_ref": f"e{index}", "date": entry.date.isoformat(),
                    "lines": [
                        {
                            "account_key": keys[line.account_id],
                            "source_ref": source_refs.get(line.transaction_id),
                            "debit": format(line.debit, ".2f"), "credit": format(line.credit, ".2f"),
                            "description": redact_bank_details(line.description, accounts),
                            "budget_category": line.budget_category,
                        }
                        for line in entry.lines
                    ],
                }
                for index, entry in enumerate(case.entries, start=1)
            ],
            "answers": [
                {
                    "source_ref": source_refs[answer.transaction_id],
                    "review_question": _answer_text(
                        questions.get(answer.transaction_id) or "", case, source_refs, accounts,
                    ) if questions.get(answer.transaction_id) else None,
                    "human_review": _answer_text(answer.human_review, case, source_refs, accounts),
                }
                for answer in case.answers
            ],
        })
    return {
        "mode": "review", "currency": "AUD", "cases": cases, "guidance": guidance,
        "budget_categories": [
            {**item, "name": redact_bank_details(item["name"], accounts)}
            for item in budget_catalogue(budget)
        ] if budget is not None else [],
        "accounts": [
            {
                "key": key, "name": redact_bank_details(account.name, accounts),
                "type": account.account_type.value,
                "purpose": redact_bank_details(account.notes or "", accounts),
            }
            for key, account in accounts.accounts.items()
        ],
    }


def _answer_text(
    text: str, case: ReviewCase, source_refs: dict[UUID, str], accounts: AccountRegistry,
) -> str:
    """
    Translate UUID references in an answer into the same short labels as its supplied accounting context.

    Unknown references remain explicitly unidentified; the skill requests clarification rather than guessing.
    The original private answer is never changed.
    """
    references: dict[str, str] = {
        str(row.transaction_id): f"source {source_refs[row.transaction_id]}" for row in case.transactions
    }
    references.update({
        str(entry.id): f"current entry e{index}" for index, entry in enumerate(case.entries, 1)
    })
    pattern = re.compile(
        r"(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])",
        flags=re.IGNORECASE,
    )
    text = pattern.sub(
        lambda match: references.get(match.group().lower(), "[unknown source or event reference]"),
        text,
    )
    return redact_bank_details(text, accounts)


def _read_response(response: str, references: dict, usage: dict) -> dict[str, list[dict]]:
    """
    Check the outer response and group known references without rejecting other cases for local defects.
    """
    try:
        data = json.loads(response)
    except (TypeError, ValueError) as error:
        category = "TypeError" if isinstance(error, TypeError) else type(error).__name__
        raise ReviewBatchError(
            f"{category}: the completed review response was not valid JSON", usage,
        ) from None
    if not isinstance(data, dict) or set(data) != {"outcomes"} or not isinstance(data["outcomes"], list):
        raise ReviewBatchError("ValueError: the review envelope must contain an outcomes list", usage)
    grouped = {}
    for record in data["outcomes"]:
        if not isinstance(record, dict) or not isinstance(record.get("case_ref"), str):
            raise ReviewBatchError("ValueError: a review result has no usable case reference", usage)
        reference = record["case_ref"]
        if reference not in references:
            raise ReviewBatchError("ValueError: the response contains an unknown case reference", usage)
        grouped.setdefault(reference, []).append(record)
    return grouped


def _read_case(record: dict) -> tuple[_Review, dict]:
    """
    Separate optional guidance, then validate only this case's accounting fields.
    """
    fields = dict(record)
    extra = {key: fields.pop(key) for key in ("guidance", "guidance_reason") if key in fields}
    result = _Review.model_validate_json(json.dumps(fields))
    return result, extra


def _validation_message(error: TypeError | ValueError) -> str:
    """
    Report validation categories and fixed field paths without repeating provider output or financial data.
    """
    safe_messages = {
        "The review selected an unknown account",
        "A counterpart posting needs a description",
        "Each review source must belong to its case and appear only once",
        "A review cannot change the source bank account",
        "A review cannot change a source transaction's full signed amount",
        "A review cannot introduce an unrelated manual event",
        "A resolved review must preserve every supplied source transaction",
        "An applied review needs entries and no question",
        "An unresolved review needs a question and no entries",
        "description must not be blank",
        "a journal line must have exactly one positive debit or credit",
        "an imported transaction cannot appear twice in one journal entry",
        "journal entry debits and credits must balance exactly",
    }
    if isinstance(error, ValidationError):
        known_fields = {
            "case_ref", "status", "reason", "question", "entries", "date", "lines", "account_key",
            "source_ref", "debit", "credit", "description", "transaction_id", "account_id", "id",
            "notes", "effective_date", "case_id", "guidance", "model", "skill_version",
            "updated_at",
        }
        details = []
        for item in error.errors(include_input=False, include_url=False)[:3]:
            parts = [
                str(part) if isinstance(part, int) or part in known_fields else "unknown_field"
                for part in item["loc"]
            ]
            path = ".".join(parts) or "model"
            kind = item["type"] if re.fullmatch(r"[a-z0-9_]+", item["type"]) else "invalid_value"
            detail = f"{path} ({kind})"
            message = str(item.get("ctx", {}).get("error", ""))
            if message in safe_messages:
                detail += ": " + message
            details.append(detail)
        return "ValidationError: " + "; ".join(details)
    message = str(error)
    if isinstance(error, ValueError) and message in safe_messages:
        return "ValueError: " + message
    category = "TypeError" if isinstance(error, TypeError) else "ValueError"
    return f"{category}: invalid review value"


def _build_entries(
    case: ReviewCase, result: _Review, sources: dict[str, Transaction],
    accounts: AccountRegistry, budget: BudgetSettings | None = None,
) -> tuple[JournalEntry, ...]:
    """
    Reconstruct every source exactly once and validate complete events with Python-owned persistent UUIDs.
    """
    if result.status == "needs_review":
        return ()
    expected = {row.transaction_id for row in case.transactions}
    seen = set()
    entries = []
    for event in result.entries:
        lines = []
        linked = [posting.source_ref for posting in event.lines if posting.source_ref is not None]
        ordinary_source = (
            sources.get(linked[0]) if len(event.lines) == 2 and len(linked) == 1 else None
        )
        for posting in event.lines:
            account = accounts.accounts.get(posting.account_key)
            if account is None:
                raise ValueError("The review selected an unknown account")
            debit, credit = Decimal(posting.debit), Decimal(posting.credit)
            if posting.source_ref is None:
                # Ordinary counterpart wording comes from the bank, before constructing its required text.
                if ordinary_source is not None and account.account_type in (
                    AccountType.INCOME, AccountType.EXPENSE,
                ):
                    text, effective_date = ordinary_source.description, ordinary_source.effective_date
                else:
                    text, effective_date = posting.description, None
                if text is None:
                    raise ValueError("A counterpart posting needs a description")
                lines.append(JournalLine(
                    account_id=account.id, debit=debit, credit=credit, description=text,
                    effective_date=effective_date, budget_category=posting.budget_category,
                ))
                continue
            source = sources.get(posting.source_ref)
            if source is None or source.transaction_id not in expected or source.transaction_id in seen:
                raise ValueError("Each review source must belong to its case and appear only once")
            if account.id != source.account_id or account.account_type != AccountType.ASSET:
                raise ValueError("A review cannot change the source bank account")
            expected_debit = source.amount if source.amount > 0 else Decimal("0")
            expected_credit = source.amount.copy_negate() if source.amount < 0 else Decimal("0")
            if debit != expected_debit or credit != expected_credit:
                raise ValueError("A review cannot change a source transaction's full signed amount")
            seen.add(source.transaction_id)
            lines.append(JournalLine(
                account_id=source.account_id, transaction_id=source.transaction_id,
                debit=debit, credit=credit,
                description=source.description, effective_date=source.effective_date,
                budget_category=posting.budget_category,
            ))
        source_ids = {line.transaction_id for line in lines if line.transaction_id is not None}
        if not source_ids:
            raise ValueError("A review cannot introduce an unrelated manual event")
        previous = [
            entry for entry in case.entries
            if {line.transaction_id for line in entry.lines if line.transaction_id is not None} == source_ids
        ]
        original = previous[0] if len(previous) == 1 else None
        identifier = original.id if original is not None else uuid4()
        entries.append(JournalEntry(
            id=identifier, date=event.date,
            lines=tuple(lines),
        ))
    if seen != expected:
        raise ValueError("A resolved review must preserve every supplied source transaction")
    entries = preserve_budget_categories(tuple(entries), case.entries)
    validate_budget_labels(entries, accounts, budget, case.entries)
    return entries


def _read_guidance(record: dict, accounts: AccountRegistry, references: dict) -> GuidanceResult:
    """
    Retain valid guidance and explain rejected candidates without exposing raw provider text in errors.
    """
    values = record.get("guidance")
    retained, errors = [], []
    if not isinstance(values, list):
        errors.append("Guidance must contain a list of candidates.")
        values = []
    if len(values) > 12:
        errors.append("Guidance contains more than twelve candidates; additional candidates were rejected.")
    for index, value in enumerate(values[:12], start=1):
        prefix = f"Guidance candidate {index}: "
        try:
            if not isinstance(value, dict) or set(value) != {
                "scope", "instruction", "account_keys", "supersedes",
            }:
                errors.append(prefix + "expected scope, instruction, account_keys and supersedes.")
                continue
            keys, replaced = value["account_keys"], value["supersedes"]
            if not isinstance(keys, list) or not isinstance(replaced, list):
                errors.append(prefix + "account_keys and supersedes must be lists.")
                continue
            if any(not isinstance(key, str) or key not in accounts.accounts for key in keys):
                errors.append(prefix + "contains an unknown account key.")
                continue
            if any(not isinstance(reference, str) or reference not in references for reference in replaced):
                errors.append(prefix + "contains an unknown guidance reference.")
                continue
            retained.append(Guidance(
                scope=value["scope"], instruction=value["instruction"],
                account_ids=tuple(accounts.accounts[key].id for key in keys),
                supersedes=tuple(references[reference].id for reference in replaced),
            ))
        except (TypeError, ValueError):
            errors.append(prefix + "invalid scope, instruction or repeated references.")
    reason = record.get("guidance_reason")
    if "guidance_reason" not in record:
        errors.append("Guidance response omitted guidance_reason.")
    if reason is not None and (not isinstance(reason, str) or not reason.strip() or len(reason) > 500):
        errors.append("Guidance reason must be nonblank text of at most 500 characters, or null.")
        reason = None
    if not retained and reason is None and not errors:
        errors.append("An empty guidance result needs an explicit reason.")
    return GuidanceResult(guidance=tuple(retained), reason=reason, errors=tuple(errors))


def _response_schema(
    cases: dict, sources: dict, accounts: AccountRegistry, guidance: dict,
    budget: BudgetSettings | None = None,
) -> dict:
    """
    Constrain response references and account keys while keeping optional text separate from accounting.
    """
    schema = _ReviewBatch.model_json_schema()
    definitions = schema["$defs"]
    definitions["_Review"]["properties"]["case_ref"]["enum"] = list(cases)
    posting = definitions["_Posting"]["properties"]
    posting["account_key"]["enum"] = list(accounts.accounts)
    labels = (
        [item["key"] for item in budget_catalogue(budget)] if budget is not None else
        sorted({line.budget_category for case in cases.values() for entry in case.entries
                for line in entry.lines if line.budget_category is not None})
    )
    posting["budget_category"] = (
        {"anyOf": [{"type": "string", "enum": labels}, {"type": "null"}]} if labels else {"type": "null"}
    )
    definitions["_Posting"]["required"] = list(posting)
    posting["source_ref"]["anyOf"][0]["enum"] = list(sources)
    fields = _guidance_fields(accounts, guidance)
    result = definitions["_Review"]
    result["properties"].update(fields)
    result["required"].extend(fields)
    schema["properties"]["outcomes"]["minItems"] = len(cases)
    schema["properties"]["outcomes"]["maxItems"] = len(cases)
    return schema


def _guidance_fields(accounts: AccountRegistry, guidance: dict) -> dict:
    """
    Share the required candidate list and explicit no-guidance reason across both assessment modes.
    """
    candidates: dict = {
        "type": "array", "maxItems": 12,
        "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "scope": {"type": "string", "maxLength": 200},
                "instruction": {"type": "string", "maxLength": 800},
                "account_keys": {
                    "type": "array", "items": {"type": "string", "enum": list(accounts.accounts)},
                },
                "supersedes": {
                    "type": "array", "items": {"type": "string", "enum": list(guidance) or ["unavailable"]},
                },
            },
            "required": ["scope", "instruction", "account_keys", "supersedes"],
        },
    }
    if not guidance:
        replacement = candidates["items"]["properties"]["supersedes"]
        replacement["maxItems"] = 0
        replacement["items"] = {"type": "string"}
    return {
        "guidance": candidates,
        "guidance_reason": {"type": ["string", "null"], "minLength": 1, "maxLength": 500},
    }
