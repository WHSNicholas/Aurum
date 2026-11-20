# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     AI - Manual                                                      #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum interprets written manual instructions using the configured accounts and relevant bank evidence.

The model proposes an accounting treatment or asks for missing facts. Python reconstructs bank lines from
their unchanged transactions, calculates one requested split remainder and validates the complete event.
This module returns validated results for the desktop workflow to save. Investment proposals preserve supplied
facts while Python calculates units and carrying cost.
The workflow saves these proposals before retrieving any missing market price.
Budget effects come only from the shared cash-flow projection; AI never proposes budget overrides.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Mapping
from decimal import Decimal
import json
import re
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

# Aurum imports
from aurum.ai.chatgpt import ChatGPTConnection
from aurum.ai.privacy import redact_bank_details
from aurum.ai.skills import Skill, explicit_memory_context, fund_context, guidance_context, load_manual_skill
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.budget import (
    BudgetSettings, budget_policy, project_budget_entry, validate_budget_labels,
)
from aurum.finance.journal import (
    AccountInvestmentDetails, InvestmentDetails, JournalEntry, JournalLine,
    preserve_account_investments, preserve_budget_categories,
)
from aurum.finance.investments import (
    AccountWithdrawalInstruction, InvestmentInstruction, PriceQuote, build_account_withdrawal,
    build_security_sale, calculate_investment, investment_cash_available, is_fund_cost_deduction,
)
from aurum.finance.manual import ManualCase, ManualOutcome
from aurum.finance.reports import ReportSettings
from aurum.finance.review import Guidance
from aurum.finance.transaction import Transaction
from aurum.finance.securities import SecurityRegistry


MAX_MANUAL_BATCH_SIZE = 20


# %% 1. Manual Responses -------------------------------------------------------------------------------------
class ManualBatchError(ValueError):
    """
    Retain reported usage when a completed response has no usable result envelope.
    """

    def __init__(self, message: str, usage: dict) -> None:
        """
        Keep a safe error and a separate copy of provider-reported usage.
        """
        super().__init__(message)
        self.usage = dict(usage)


class _SourceClarification(ValueError):
    """
    Stop an inferred source choice when supplied evidence cannot distinguish identical bank rows.
    """


class _Posting(BaseModel):
    """
    Select an unchanged source or specify one manual debit or credit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    account_key: str
    account_investment: AccountInvestmentDetails | None = None
    source_ref: str | None
    side: Literal["debit", "credit"] | None
    amount: str | None = Field(pattern=r"^(remaining|[1-9][0-9]*(\.[0-9]{1,2})?|0\.[0-9]{1,2})$")
    description: str | None = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def _check_source(self) -> Self:
        """
        Leave bank facts to Python and require an amount and side for other postings.
        """
        if self.source_ref is not None:
            if self.side is not None or self.amount is not None or self.description is not None:
                raise ValueError("A source posting must leave bank facts unset.")
        elif self.side is None or self.amount is None:
            raise ValueError("A manual posting needs an amount and posting side.")
        return self


class _Investment(BaseModel):
    """
    Identify an opening holding or purchase without inventing missing trade figures.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation: Literal["opening", "buy", "sell"]
    realised_account_key: str | None
    account_key: str
    security_key: str
    counterpart_key: str
    source_ref: str | None
    units: str | None = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    amount: str | None = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")
    unit_price: str | None = Field(pattern=r"^[0-9]+(?:\.[0-9]+)?$")
    fees: str | None = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")
    amount_includes_fees: bool


class _Withdrawal(BaseModel):
    """
    Copy confirmed balance-only withdrawal facts for deterministic cost and gain calculation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    account_key: str
    counterpart_key: str
    realised_account_key: str
    source_ref: str | None
    amount: str = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")
    confirmed_value: str = Field(pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")


class _Proposal(BaseModel):
    """
    Separate a supported posting plan from questions and deferred investment requests.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, str_strip_whitespace=True)

    case_ref: str
    event_kind: Literal["general", "opening", "investment", "withdrawal"]
    status: Literal["apply", "needs_review", "unsupported"]
    reason: str = Field(min_length=1, max_length=400)
    question: str | None = Field(min_length=1, max_length=400)
    lines: tuple[_Posting, ...] = Field(max_length=50)
    investment: _Investment | None = None
    withdrawal: _Withdrawal | None = None

    @model_validator(mode="after")
    def _check_state(self) -> Self:
        """
        Require one complete plan or an explained unresolved result.
        """
        if self.status == "apply":
            if self.question is not None:
                raise ValueError("An applied proposal cannot have a question.")
            if self.event_kind == "investment":
                if self.investment is None or self.lines:
                    raise ValueError("An investment needs facts instead of model-calculated postings.")
            elif self.event_kind == "withdrawal":
                if self.withdrawal is None or self.lines or self.investment is not None:
                    raise ValueError("A withdrawal needs confirmed facts instead of model-calculated postings")
            elif len(self.lines) < 2:
                raise ValueError("An applied proposal needs at least two lines.")
        if self.withdrawal is not None and (self.event_kind != "withdrawal" or self.status != "apply"):
            raise ValueError("Only a complete withdrawal proposal can contain confirmed facts")
        if self.investment is not None and (
            self.event_kind != "investment" or self.status != "apply"
        ):
            raise ValueError("Only a complete investment proposal can contain investment facts.")
        if self.status != "apply" and self.lines:
            raise ValueError("An unresolved proposal cannot contain postings.")
        if self.status == "needs_review" and self.question is None:
            raise ValueError("An unresolved proposal needs a question.")
        if self.status == "unsupported" and self.question is not None:
            raise ValueError("An unsupported proposal needs a reason, not a question.")
        return self


class _Batch(BaseModel):
    """
    Supply the response envelope for at most twenty independent manual events.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    outcomes: tuple[_Proposal, ...] = Field(min_length=1, max_length=MAX_MANUAL_BATCH_SIZE)


# %% 2. Manual Interpretation --------------------------------------------------------------------------------
def manual_batch(
    client: ChatGPTConnection, cases: list[ManualCase], accounts: AccountRegistry, *,
    skill: Skill | None = None, guidance: list[Guidance] | None = None,
    explicit_memory: list[dict] | None = None,
    securities: SecurityRegistry | None = None, budget: BudgetSettings | None = None,
    report_settings: ReportSettings | None = None,
    transfer_evidence: Mapping[str, Mapping[str, object]] | None = None,
    ledger_entries: list[JournalEntry] | None = None,
) -> tuple[dict[UUID, ManualOutcome], dict[UUID, str], dict]:
    """
    Interpret independent manual cases and validate each result without changing saved accounting.

    Args:
        client: Authenticated connection with a selected model.
        cases: At most twenty submissions with complete candidate evidence.
        accounts: Registry of existing accounts available for posting.
        skill: Manual-entry instructions loaded once by the workflow, or None to load the project skill here.
        guidance: Accepted preferences supplied as context, never as automatic posting rules.
        securities: Existing instruments available for opening holdings and purchases.
        budget: Shared deterministic cash-flow roles; no model-selected budget labels are accepted.

    Returns:
        Valid outcomes, safe per-submission failures and reported token usage. Empty input makes no request.

    Raises:
        ValueError: Cases overlap, context is invalid, or no model has been selected.
        ManualBatchError: Completed JSON or its references are unusable; retains reported usage.
        ChatGPTError: The request fails or does not complete.
    """
    if not cases:
        return {}, {}, {}
    model = client.model
    if not model:
        raise ValueError("Select a model before interpreting manual entries.")
    references, sources, payload = _request_context(
        cases, accounts, guidance, securities, budget, report_settings, explicit_memory,
        transfer_evidence,
    )
    investment_cash = {}
    for reference, case in references.items():
        available = {}
        for key in budget.investment_accounts if budget is not None else ():
            if key not in accounts.accounts or report_settings and key in report_settings.account_investments:
                continue
            value = investment_cash_available(
                ledger_entries or [], accounts.accounts[key].id, case.submission.date,
                excluding=case.entries[0].id if len(case.entries) == 1 else None,
            )
            if value is not None:
                available[key] = value
        investment_cash[reference] = available
    for item in payload["cases"]:
        item["verified_brokerage_cash"] = {
            key: format(value, ".2f") for key, value in investment_cash[item["case_ref"]].items()
        }
    skill = skill or load_manual_skill()
    response, usage = client.request(
        instructions=skill.instructions + "\n\n" + budget_policy(budget)["rule"], payload=payload,
        output_schema=_response_schema(references, sources, accounts, securities, budget),
    )
    records = _read_response(response, references, usage)
    outcomes, failures = {}, {}
    for reference, case in references.items():
        values = records.get(reference, [])
        if len(values) != 1:
            failures[case.submission.id] = "The response omitted or repeated this manual case."
            continue
        try:
            proposal = _Proposal.model_validate_json(json.dumps(values[0]))
            if proposal.event_kind == "withdrawal" and proposal.status == "apply":
                facts = proposal.withdrawal
                if facts is None or report_settings is None or not report_settings.unrealised_gains_account:
                    raise _SourceClarification("Confirm the fund facts and configure its unrealised-gain account.")
                if facts.realised_account_key not in accounts.accounts:
                    raise _SourceClarification("Choose the configured income account for realised returns.")
                source = sources.get(facts.source_ref) if facts.source_ref else None
                if facts.source_ref is not None and source is None:
                    raise ValueError("The manual proposal selected an unknown source.")
                instruction = AccountWithdrawalInstruction.model_validate({
                    **facts.model_dump(exclude={"source_ref"}),
                    "transaction_id": source.transaction_id if source else None,
                    "unrealised_account_key": report_settings.unrealised_gains_account,
                })
                outcomes[case.submission.id] = ManualOutcome(
                    manual_id=case.submission.id, status="pricing", event_kind="withdrawal",
                    withdrawal=instruction, reason=proposal.reason, model=model, skill_version=skill.version,
                )
                continue
            if proposal.event_kind == "investment" and proposal.status == "apply":
                if proposal.investment is None:
                    raise ValueError("An investment proposal needs its supplied facts")
                if proposal.investment.operation == "sell" and not proposal.investment.realised_account_key:
                    raise _SourceClarification("Choose the configured income account for the realised return.")
                instruction = _investment_instruction(proposal.investment, sources, accounts, securities)
                outcomes[case.submission.id] = ManualOutcome(
                    manual_id=case.submission.id, status="pricing", event_kind="investment",
                    investment=instruction,
                    reason=proposal.reason,
                    model=model, skill_version=skill.version,
                )
                continue
            if proposal.status == "apply" and _replaces_investment(case, proposal, sources):
                outcomes[case.submission.id] = ManualOutcome(
                    manual_id=case.submission.id, status="needs_review",
                    reason="Investment corrections must retain their units and carrying cost.",
                    question="Enter the corrected holding, purchase, sale or withdrawal in Transactions.",
                    model=model, skill_version=skill.version,
                )
                continue
            entry = _build_entry(
                case, proposal, sources, accounts, budget, transfer_evidence,
                investment_cash=investment_cash[reference],
            )
            outcomes[case.submission.id] = ManualOutcome(
                manual_id=case.submission.id,
                status="needs_review" if proposal.status == "unsupported" else proposal.status,
                event_kind=proposal.event_kind,
                entry=entry, reason=proposal.reason,
                question=proposal.reason if proposal.status == "unsupported" else proposal.question,
                model=model, skill_version=skill.version,
            )
        except _SourceClarification as error:
            outcomes[case.submission.id] = ManualOutcome(
                manual_id=case.submission.id, status="needs_review",
                reason="This event needs additional supported accounting facts.",
                question=str(error), model=model, skill_version=skill.version,
            )
        except (TypeError, ValueError) as error:
            failures[case.submission.id] = _validation_message(error)
    # Shared evidence is not ownership. Two completed proposals may not consume the same source.
    selections = {}
    for identifier, outcome in outcomes.items():
        selected = (
            {line.transaction_id for line in outcome.entry.lines if line.transaction_id}
            if outcome.entry is not None else set()
        )
        if outcome.investment is not None and outcome.investment.transaction_id is not None:
            selected.add(outcome.investment.transaction_id)
        if outcome.withdrawal is not None and outcome.withdrawal.transaction_id is not None:
            selected.add(outcome.withdrawal.transaction_id)
        selections[identifier] = selected
    competing = {identifier for identifier, selected in selections.items() if any(
        selected & other for key, other in selections.items() if key != identifier
    )}
    for identifier in competing:
        outcomes[identifier] = ManualOutcome(
            manual_id=identifier, status="needs_review",
            reason="Two instructions selected the same bank evidence.",
            question="These instructions use the same bank movement. Keep one correction or clarify them.",
            model=model, skill_version=skill.version,
        )
    return outcomes, failures, usage



def complete_investment(
    case: ManualCase, outcome: ManualOutcome, accounts: AccountRegistry,
    securities: SecurityRegistry | None, *, quote: PriceQuote | None = None,
    budget: BudgetSettings | None = None, ledger_entries: list[JournalEntry] | None = None,
) -> ManualOutcome:
    """
    Turn a saved investment interpretation into a balanced event using exact Python calculations.

    Supplied bank amounts and source groups are checked by the same builder used for general manuals.
    Reference quotes remain in the result; only actual execution prices go onto investment journal lines.
    """
    if outcome.withdrawal is not None:
        instruction = outcome.withdrawal
        realised = accounts.accounts.get(instruction.realised_account_key)
        unrealised = accounts.accounts.get(instruction.unrealised_account_key)
        if realised is None or unrealised is None:
            raise ValueError("Restore the explicitly selected realised and unrealised return accounts")
        entry, calculation = build_account_withdrawal(
            instruction, ledger_entries or [], accounts, case.submission.date,
            realised.id, unrealised.id,
            source=next((row for row in case.available_transactions
                         if row.transaction_id == instruction.transaction_id), None),
            entry_id=case.entries[0].id if len(case.entries) == 1 else None,
        )
        _validate_completed_investment(case, entry, accounts, budget)
        return ManualOutcome(
            manual_id=outcome.manual_id, status="apply", event_kind="withdrawal", entry=entry,
            withdrawal=instruction, withdrawal_calculation=calculation, reason=outcome.reason,
            model=outcome.model, skill_version=outcome.skill_version,
        )
    instruction = outcome.investment
    if outcome.status != "pricing" or instruction is None:
        raise ValueError("An investment needs a saved interpretation before pricing")
    if securities is None:
        raise ValueError("Configure the security before completing this investment")
    security = securities.securities.get(instruction.security_key)
    asset = accounts.accounts.get(instruction.account_key)
    counterpart = accounts.accounts.get(instruction.counterpart_key)
    if security is None or asset is None or counterpart is None:
        raise ValueError("The selected investment, security or counterpart is no longer configured")
    if asset.account_type != AccountType.ASSET or asset.bank_details is not None:
        raise ValueError("Select a non-bank asset account for the investment holding")
    sources = {row.transaction_id: row for row in case.available_transactions}
    source = sources.get(instruction.transaction_id) if instruction.transaction_id is not None else None
    if instruction.operation == "sell":
        if instruction.realised_account_key not in accounts.accounts:
            raise ValueError("Choose the configured income account for the realised return")
        entry, calculation = build_security_sale(
            instruction, ledger_entries or [], accounts, securities, case.submission.date,
            accounts.accounts[instruction.realised_account_key].id, source=source,
            entry_id=case.entries[0].id if len(case.entries) == 1 else None,
        )
        _validate_completed_investment(case, entry, accounts, budget)
        return ManualOutcome(
            manual_id=outcome.manual_id, status="apply", event_kind="investment", entry=entry,
            investment=instruction, pricing=calculation, reason=outcome.reason,
            model=outcome.model, skill_version=outcome.skill_version,
        )
    if instruction.transaction_id is not None and (
        source is None or source.account_id != counterpart.id or source.amount >= 0
    ):
        raise ValueError("Select the actual outgoing bank payment and its matching funding account")
    if instruction.operation == "opening" and counterpart.bank_details is not None:
        raise ValueError("An opening investment needs a non-bank counterpart, such as Capital")
    if quote is not None and (
        quote.security_id != security.id or quote.requested_date != case.submission.date
    ):
        raise ValueError("The market quote belongs to another security or accounting date")
    calculation = calculate_investment(
        instruction, quote=quote, settlement=source.amount.copy_negate() if source is not None else None,
    )
    total = format(calculation.total_cost, ".2f")
    proposal = _Proposal(
        case_ref="local", event_kind="general", status="apply", reason=outcome.reason,
        question=None, lines=(
            _Posting(
                account_key=asset.key, source_ref=None, side="debit", amount=total, description=None,
            ),
            _Posting(
                account_key=counterpart.key, source_ref="settlement" if source is not None else None,
                side=None if source is not None else "credit",
                amount=None if source is not None else total, description=None,
            ),
        ),
    )
    entry = _build_entry(
        case, proposal, {"settlement": source} if source is not None else {}, accounts, budget,
        investment_build=True,
    )
    if entry is None:
        raise ValueError("An investment needs complete debit and credit postings")
    holding = JournalLine.model_validate({
        **entry.lines[0].model_dump(),
        "investment": InvestmentDetails(
            security_id=security.id, units=calculation.units,
            unit_price=calculation.unit_price, fees=calculation.fees,
        ),
    })
    entry = JournalEntry(id=entry.id, date=entry.date, lines=(holding, *entry.lines[1:]))
    return ManualOutcome(
        manual_id=outcome.manual_id, status="apply", event_kind="investment",
        entry=entry, investment=instruction, pricing=calculation, reason=outcome.reason,
        model=outcome.model, skill_version=outcome.skill_version,
    )

# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _request_context(
    cases: list[ManualCase], accounts: AccountRegistry, guidance: list[Guidance] | None,
    securities: SecurityRegistry | None = None, budget: BudgetSettings | None = None,
    report_settings: ReportSettings | None = None,
    explicit_memory: list[dict] | None = None,
    transfer_evidence: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[dict, dict, dict]:
    """
    Validate independent cases and send a compact account catalogue with short, redacted evidence references.
    """
    if len(cases) > MAX_MANUAL_BATCH_SIZE:
        raise ValueError("Manual batches cannot exceed twenty cases.")
    cases = [ManualCase.model_validate(case) for case in cases]
    if len({case.submission.id for case in cases}) != len(cases):
        raise ValueError("Manual batches need distinct submissions.")
    all_sources = [row.transaction_id for case in cases for row in case.transactions]
    all_entries = [entry.id for case in cases for entry in case.entries]
    if len(set(all_sources)) != len(all_sources) or len(set(all_entries)) != len(all_entries):
        raise ValueError("Manual cases in a batch cannot share sources or current events.")
    references = {f"m{index}": case for index, case in enumerate(cases, 1)}
    unique_sources = {
        row.transaction_id: row for case in cases for row in case.available_transactions
    }
    sources = {f"t{index}": row for index, row in enumerate(unique_sources.values(), 1)}
    source_refs = {row.transaction_id: reference for reference, row in sources.items()}
    unique_entries = {entry.id: entry for case in cases for entry in case.available_entries}
    entry_refs = {identifier: f"e{index}" for index, identifier in enumerate(unique_entries, 1)}
    shared_entries = {}
    by_id = {account.id: account for account in accounts.accounts.values()}
    learned, _ = guidance_context(
        guidance, accounts, " ".join(case.submission.instruction + " " + case.submission.human_response
                                    for case in cases),
    )
    for item in learned:
        item["scope"] = _safe_text(item["scope"], accounts, {})
        item["instruction"] = _safe_text(item["instruction"], accounts, {})
    items = []
    known_securities = (
        {item.id for item in securities.securities.values()} if securities is not None else set()
    )
    for reference, case in references.items():
        if securities is not None and any(
            line.investment is not None and line.investment.security_id not in known_securities
            for entry in case.available_entries for line in entry.lines
        ):
            raise ValueError("Current investment postings need their original configured securities.")
        if any(
            row.account_id not in by_id or by_id[row.account_id].account_type != AccountType.ASSET
            or by_id[row.account_id].bank_details is None
            for row in case.available_transactions
        ):
            raise ValueError("Manual sources need configured asset bank accounts.")
        if any(line.account_id not in by_id for entry in case.available_entries for line in entry.lines):
            raise ValueError("Current manual postings need configured accounts.")
        identifiers = {
            str(row.transaction_id): "source " + source_refs[row.transaction_id]
            for row in case.available_transactions
        }
        identifiers.update({str(account.id): account.key for account in by_id.values()})
        identifiers.update({
            str(entry.id): "event " + entry_refs[entry.id] for entry in case.available_entries
        })
        items.append({
            "case_ref": reference, "date": case.submission.date.isoformat(),
            "instruction": _safe_text(case.submission.instruction, accounts, identifiers),
            "human_response": _safe_text(case.submission.human_response, accounts, identifiers),
            "question": _safe_text(case.question or "", accounts, identifiers) or None,
            "required_source_refs": [source_refs[identifier] for identifier in case.required_sources],
            "available_source_refs": [source_refs[row.transaction_id] for row in case.available_transactions],
            "current_entries": [
                {
                    "entry_ref": entry_refs[entry.id], "date": entry.date.isoformat(),
                    "has_investment_details": any(line.investment is not None for line in entry.lines),
                    "lines": [
                        {
                            "account_key": by_id[line.account_id].key,
                            "source_ref": source_refs[line.transaction_id] if line.transaction_id else None,
                            "debit": format(line.debit, ".2f"), "credit": format(line.credit, ".2f"),
                            "description": _safe_text(line.description, accounts, identifiers),
                            "account_investment": line.account_investment.model_dump(mode="json")
                            if line.account_investment is not None else None,
                            "investment": line.investment.model_dump(mode="json", exclude={"security_id"})
                            | {"security_key": securities.by_id(line.investment.security_id).key}
                            if line.investment is not None and securities is not None else None,
                        }
                        for line in entry.lines
                    ],
                }
                for index, entry in enumerate(case.available_entries, 1)
            ],
        })
        current = items[-1].pop("current_entries")
        items[-1]["current_entry_refs"] = [item["entry_ref"] for item in current]
        for item in current:
            shared_entries[item["entry_ref"]] = item
    return references, sources, {
        "mode": "manual", "currency": "AUD", "cases": items, "guidance": learned,
        "explicit_memory": explicit_memory_context(explicit_memory, accounts),
        "unrealised_gains_account": report_settings.unrealised_gains_account if report_settings else None,
        "current_entries": list(shared_entries.values()),
        "sources": [
            {
                "source_ref": reference, "account_key": by_id[row.account_id].key,
                "date": row.date.isoformat(), "amount": format(row.amount, ".2f"),
                "effective_date": row.effective_date.isoformat() if row.effective_date else None,
                "description": _safe_text(row.description, accounts, {}),
            }
            for reference, row in sources.items()
        ],
        "budget_policy": budget_policy(budget),
        "securities": [
            {
                "key": item.key, "name": _safe_text(item.name, accounts, {}),
                "symbol": item.symbol, "market": item.market, "type": item.security_type.value,
            }
            for item in securities.securities.values()
        ] if securities is not None else [],
        "accounts": [
            {
                "key": key, "name": _safe_text(account.name, accounts, {}),
                "type": account.account_type.value, "is_bank": account.bank_details is not None,
                **fund_context(account, report_settings),
                "purpose": _safe_text(account.notes or "", accounts, {}),
            }
            for key, account in accounts.accounts.items()
        ],
    }


def _safe_text(text: str, accounts: AccountRegistry, references: dict[str, str]) -> str:
    """
    Replace UUIDs with local short labels and redact configured bank identifiers in outbound text.
    """
    pattern = re.compile(
        r"(?<![0-9a-f])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![0-9a-f])",
        flags=re.IGNORECASE,
    )
    text = pattern.sub(lambda match: references.get(match.group().lower(), "[unknown reference]"), text)
    return redact_bank_details(text, accounts)


def _read_response(response: str, references: dict, usage: dict) -> dict[str, list[dict]]:
    """
    Validate the outer response and reject unknown references without leaking provider text into errors.
    """
    try:
        data = json.loads(response)
    except (TypeError, ValueError):
        raise ManualBatchError("The completed manual response was not valid JSON.", usage) from None
    if not isinstance(data, dict) or set(data) != {"outcomes"} or not isinstance(data["outcomes"], list):
        raise ManualBatchError("The manual response needs an outcomes list.", usage)
    grouped = {}
    for record in data["outcomes"]:
        if not isinstance(record, dict) or not isinstance(record.get("case_ref"), str):
            raise ManualBatchError("A manual outcome has no usable case reference.", usage)
        if record["case_ref"] not in references:
            raise ManualBatchError("The manual response contains an unknown case reference.", usage)
        grouped.setdefault(record["case_ref"], []).append(record)
    return grouped



def _investment_instruction(
    proposal: _Investment, sources: dict[str, Transaction], accounts: AccountRegistry,
    securities: SecurityRegistry | None,
) -> InvestmentInstruction:
    """
    Resolve supplied short references into a durable instruction without calculating or fetching prices.
    """
    if securities is None or proposal.security_key not in securities.securities:
        raise ValueError("The manual proposal needs a security from the configured catalogue.")
    if proposal.account_key not in accounts.accounts or proposal.counterpart_key not in accounts.accounts:
        raise ValueError("The manual proposal selected an unknown account.")
    source = sources.get(proposal.source_ref) if proposal.source_ref is not None else None
    if proposal.source_ref is not None and source is None:
        raise ValueError("The manual proposal selected an unknown source.")
    values = proposal.model_dump(exclude={"source_ref"})
    return InvestmentInstruction.model_validate({
        **values, "transaction_id": source.transaction_id if source is not None else None,
    })


def _replaces_investment(case: ManualCase, proposal: _Proposal, sources: dict) -> bool:
    """
    Protect existing investment movements from corrections that would discard units or carrying-cost history.
    """
    selected = {
        sources[line.source_ref].transaction_id for line in proposal.lines if line.source_ref in sources
    }
    for entry in case.available_entries:
        source_ids = {line.transaction_id for line in entry.lines if line.transaction_id is not None}
        affected = not source_ids or bool(source_ids & selected)
        if affected and any(line.investment is not None for line in entry.lines):
            return True
    return False


def _build_entry(
    case: ManualCase, proposal: _Proposal, sources: dict[str, Transaction],
    accounts: AccountRegistry, budget: BudgetSettings | None = None,
    transfer_evidence: Mapping[str, Mapping[str, object]] | None = None,
    *, investment_cash: dict[str, Decimal] | None = None, investment_build: bool = False,
) -> JournalEntry | None:
    """
    Build one event from unchanged bank facts and exact manual amounts, preserving complete prior events.

    Non-bank changes can use an explicitly selected counterpart without opening-related wording. A bank
    posting without imported evidence still requires an explicitly instructed opening position.
    """
    if proposal.status != "apply":
        return None
    candidate_ids = {row.transaction_id for row in case.available_transactions}
    selected = set()
    fixed = []
    remaining = None
    linked = [posting.source_ref for posting in proposal.lines if posting.source_ref is not None]
    ordinary_source = sources.get(linked[0]) if len(proposal.lines) == 2 and len(linked) == 1 else None
    for index, posting in enumerate(proposal.lines):
        account = accounts.accounts.get(posting.account_key)
        if account is None:
            raise ValueError("The manual proposal selected an unknown account.")
        if posting.source_ref is not None:
            source = sources.get(posting.source_ref)
            if (
                source is None or source.transaction_id not in candidate_ids
                or source.transaction_id in selected
            ):
                raise ValueError("Manual sources must be distinct candidates from this case.")
            if account.id != source.account_id or account.account_type != AccountType.ASSET:
                raise ValueError("A manual proposal cannot change the source bank account.")
            if source.transaction_id not in case.required_sources and any(
                other.transaction_id != source.transaction_id
                and (other.account_id, other.date, other.effective_date, other.amount, other.description)
                == (source.account_id, source.date, source.effective_date, source.amount, source.description)
                for other in case.available_transactions
            ):
                raise _SourceClarification(
                    "Two bank rows have identical account, dates, amount and description. Identify which "
                    "statement movement this instruction refers to, or select its row in the app."
                )
            selected.add(source.transaction_id)
            fixed.append(JournalLine(
                account_id=source.account_id, transaction_id=source.transaction_id,
                debit=source.amount if source.amount > 0 else Decimal("0"),
                credit=source.amount.copy_negate() if source.amount < 0 else Decimal("0"),
                description=source.description, effective_date=source.effective_date,
                account_investment=posting.account_investment,
            ))
            continue
        if account.bank_details is not None:
            if proposal.event_kind != "opening":
                raise ValueError(
                    "A bank posting needs an imported source unless it is an explicit opening position."
                )
            if not re.search(
                r"\b(opening|initial|starting|brought[ -]forward)\b",
                case.submission.instruction + " " + case.submission.human_response, flags=re.IGNORECASE,
            ):
                raise ValueError("A bank posting without a source needs an explicit opening instruction.")
        text = posting.description or case.submission.instruction
        effective_date = None
        if ordinary_source is not None and account.account_type in (AccountType.INCOME, AccountType.EXPENSE):
            text, effective_date = ordinary_source.description, ordinary_source.effective_date
        if posting.amount == "remaining":
            if remaining is not None:
                raise ValueError("A manual event can contain only one remaining amount.")
            remaining = (
                index, account.id, posting.side, text, effective_date,
                None, posting.account_investment,
            )
            fixed.append(None)
            continue
        if posting.amount is None:
            raise ValueError("A manual event needs an amount for every unlinked posting.")
        amount = Decimal(posting.amount)
        fixed.append(JournalLine(
            account_id=account.id, description=text, effective_date=effective_date,
            account_investment=posting.account_investment,
            debit=amount if posting.side == "debit" else Decimal("0"),
            credit=amount if posting.side == "credit" else Decimal("0"),
        ))
    if not set(case.required_sources) <= selected:
        raise ValueError("A manual event must preserve every required source.")
    for entry in case.available_entries:
        prior_sources = {line.transaction_id for line in entry.lines if line.transaction_id is not None}
        if selected & prior_sources and not prior_sources <= selected:
            raise ValueError("A manual correction must preserve the complete existing source group.")
    if remaining is not None:
        index, account_id, side, text, effective_date, category, account_investment = remaining
        # Integer cents avoid precision loss when summing Decimal values near its context limit.
        balance = sum(_cents(line.debit) - _cents(line.credit) for line in fixed if line is not None)
        cents = -balance if side == "debit" else balance
        if cents <= 0:
            raise ValueError("The remaining split amount must be positive on its requested side.")
        amount = Decimal(f"{cents // 100}.{cents % 100:02d}")
        fixed[index] = JournalLine(
            account_id=account_id, description=text, effective_date=effective_date, budget_category=category,
            account_investment=account_investment,
            debit=amount if side == "debit" else Decimal("0"),
            credit=amount if side == "credit" else Decimal("0"),
        )
    previous = [
        entry for entry in case.available_entries
        if {line.transaction_id for line in entry.lines if line.transaction_id is not None} == selected
    ]
    original = previous[0] if len(previous) == 1 else None
    entry = JournalEntry(
        id=original.id if original is not None else uuid4(), date=case.submission.date,
        lines=tuple(fixed),
    )
    affected = tuple(
        old for old in case.available_entries if old.id == entry.id
        or selected & {line.transaction_id for line in old.lines if line.transaction_id is not None}
    )
    entry = preserve_budget_categories((entry,), affected)[0]
    entry = preserve_account_investments((entry,), affected)[0]
    if budget is not None and not investment_build and not is_fund_cost_deduction(entry, accounts):
        for key in budget.investment_accounts:
            account = accounts.accounts.get(key)
            if account is None:
                continue
            outgoing = sum((line.credit for line in entry.lines if line.account_id == account.id
                            and (line.account_investment is None
                                 or line.account_investment.kind != "earnings")), Decimal("0"))
            if outgoing > (investment_cash or {}).get(key, Decimal("0")):
                raise _SourceClarification(
                    "Supply the security sale facts or confirm the fund withdrawal value and basis. "
                    "A later cash transfer needs evidence that the sale was already recorded."
                )
    validate_budget_labels(
        (entry,), accounts, budget, affected, transactions=sources.values(),
        transfer_evidence=transfer_evidence,
    )
    issues = project_budget_entry(
        entry, accounts, budget, opening=proposal.event_kind == "opening",
    ).issues if budget is not None else ()
    if issues:
        raise _SourceClarification(" ".join(issue.message for issue in issues))
    return entry


def _validate_completed_investment(
    case: ManualCase, entry: JournalEntry, accounts: AccountRegistry, budget: BudgetSettings | None,
) -> None:
    """
    Preserve required source coverage and reject unsupported cash-flow projection after deterministic building.
    """
    selected = {line.transaction_id for line in entry.lines if line.transaction_id is not None}
    if not set(case.required_sources) <= selected:
        raise ValueError("An investment must preserve every required bank source")
    for previous in case.available_entries:
        sources = {line.transaction_id for line in previous.lines if line.transaction_id is not None}
        if selected & sources and not sources <= selected:
            raise ValueError("An investment must preserve its complete existing source group")
    issues = project_budget_entry(entry, accounts, budget).issues if budget is not None else ()
    if issues:
        raise ValueError(" ".join(issue.message for issue in issues))


def _cents(value: Decimal) -> int:
    """
    Convert an already validated whole-cent amount without Decimal-context rounding.
    """
    numerator, denominator = value.as_integer_ratio()
    return numerator * 100 // denominator


def _validation_message(error: TypeError | ValueError) -> str:
    """
    Report fixed validation locations and safe internal errors without echoing raw financial input.
    """
    if isinstance(error, ValidationError):
        known = set(_Proposal.model_fields) | set(_Posting.model_fields) | set(JournalLine.model_fields)
        details = []
        for item in error.errors(include_input=False, include_url=False)[:3]:
            location = ".".join(
                str(part) if isinstance(part, int) or part in known else "unknown_field"
                for part in item["loc"]
            ) or "model"
            details.append(location + " (" + item["type"] + ")")
        return "Invalid manual proposal: " + "; ".join(details)
    if isinstance(error, ValueError) and str(error).startswith((
        "The manual proposal ", "Manual sources ", "A manual proposal ",
        "A bank posting ", "A manual event ", "A manual correction ", "The remaining split ",
    )):
        return str(error)
    return "Invalid manual proposal; no accounting was changed."


def _response_schema(
    references: dict, sources: dict, accounts: AccountRegistry, securities: SecurityRegistry | None = None,
    budget: BudgetSettings | None = None,
) -> dict:
    """
    Restrict case, account and source references to the supplied context.
    """
    schema = _Batch.model_json_schema()
    definitions = schema["$defs"]
    details = definitions["AccountInvestmentDetails"]
    details["required"] = list(details["properties"])
    for field in details["properties"].values():
        field.pop("default", None)
    result = definitions["_Proposal"]
    result["properties"]["case_ref"]["enum"] = list(references)
    result["required"] = list(result["properties"])
    result["properties"]["investment"].pop("default", None)
    result["properties"]["withdrawal"].pop("default", None)
    withdrawal = definitions["_Withdrawal"]["properties"]
    for key in ("account_key", "counterpart_key", "realised_account_key"):
        withdrawal[key]["enum"] = list(accounts.accounts)
    withdrawal["source_ref"] = (
        {"anyOf": [{"type": "string", "enum": list(sources)}, {"type": "null"}]}
        if sources else {"type": "null"}
    )
    definitions["_Withdrawal"]["required"] = list(withdrawal)
    if securities is None or not securities.securities:
        result["properties"]["investment"] = {"type": "null"}
    else:
        investment = definitions["_Investment"]["properties"]
        investment["security_key"]["enum"] = list(securities.securities)
        for key in ("account_key", "counterpart_key"):
            investment[key]["enum"] = list(accounts.accounts)
        if sources:
            investment["source_ref"]["anyOf"][0]["enum"] = list(sources)
        else:
            investment["source_ref"] = {"type": "null"}
    income_keys = [
        key for key, account in accounts.accounts.items() if account.account_type == AccountType.INCOME
    ]
    if income_keys:
        definitions["_Investment"]["properties"]["realised_account_key"]["anyOf"][0]["enum"] = income_keys
        withdrawal["realised_account_key"]["enum"] = income_keys
    else:
        definitions["_Investment"]["properties"]["realised_account_key"] = {"type": "null"}
        result["properties"]["withdrawal"] = {"type": "null"}
    posting = definitions["_Posting"]["properties"]
    posting["account_investment"].pop("default", None)
    posting["account_key"]["enum"] = list(accounts.accounts)
    definitions["_Posting"]["required"] = list(posting)
    definitions["_Investment"]["required"] = list(definitions["_Investment"]["properties"])
    if sources:
        posting["source_ref"]["anyOf"][0]["enum"] = list(sources)
    else:
        posting["source_ref"] = {"type": "null"}
    schema["properties"]["outcomes"]["minItems"] = len(references)
    schema["properties"]["outcomes"]["maxItems"] = len(references)
    return schema
