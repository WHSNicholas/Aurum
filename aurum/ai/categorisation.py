# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 AI - Categorisation                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum asks ChatGPT to choose an income or expense account when a bank transaction has a clear purpose.

This module prepares small batches with a compact account catalogue and replaces configured bank identifiers
before sending descriptions. The model returns either an account choice or a question for manual review,
and Python validates the account selection against the supplied statement row and permitted account types.

Decisions keep transaction UUIDs locally so the journal command can associate results with their sources.
This module does not change transactions, write to the database or decide journal amounts.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Aurum imports
from aurum.ai.chatgpt import ChatGPTConnection
from aurum.ai.privacy import redact_bank_details
from aurum.ai.skills import Skill, guidance_context, load_categorisation_skill
from aurum.finance.accounts import Account, AccountRegistry, AccountType
from aurum.finance.transaction import Transaction
from aurum.finance.review import Guidance

# Changing the prompt never invalidates or automatically resends saved decisions.
PROMPT_VERSION = "journal-v3"
MAX_BATCH_SIZE = 20


# %% 1. Decisions --------------------------------------------------------------------------------------------
class CategorisationBatchError(ValueError):
    """
    Retain reported usage when a completed categorisation response fails validation.
    """

    def __init__(self, message: str, usage: dict) -> None:
        """
        Keep a safe response-validation message with its provider-reported usage.
        """
        super().__init__(message)
        self.usage = dict(usage)


class Decision(BaseModel):
    """
    Record one transaction's progress, accepted account choice or unresolved review question.

    A classified decision carries a counterpart account choice in memory until the journal command saves
    its actual postings and audit details together. It is not stored as a separate classified state. A review
    decision instead asks a question; requesting and failed decisions carry neither an account nor a question.
    Model and prompt version record which instructions produced the result without forcing recategorisation.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    transaction_id: UUID
    status: Literal["requesting", "classified", "needs_review", "failed"]
    account_id: UUID | None = None
    reason: str = Field(min_length=1)
    question: str | None = Field(default=None, min_length=1)
    origin: Literal["ai", "local"] = "ai"
    model: str | None = Field(default=None, min_length=1)
    prompt_version: str = Field(default=PROMPT_VERSION, min_length=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _check_state(self) -> Self:
        """
        Require the account choice or question appropriate to the decision's status and an aware timestamp.
        """
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("Decision timestamps must include a timezone.")
        if self.status == "classified":
            if self.account_id is None or self.question is not None:
                raise ValueError("A classified decision needs an account and no question.")
        elif self.status == "needs_review":
            if self.account_id is not None or self.question is None:
                raise ValueError("A review decision needs a question and no account.")
        elif self.account_id is not None or self.question is not None:
            raise ValueError("Requesting and failed decisions cannot include an account or question.")
        return self


class _ResponseDecision(BaseModel):
    """
    Require a structured account choice or review question from ChatGPT, without accepting extra fields.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, str_strip_whitespace=True)

    row_id: str
    status: Literal["classified", "needs_review"]
    account_key: str | None
    reason: str = Field(min_length=1, max_length=240)
    question: str | None = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def _check_state(self) -> Self:
        """
        Reject a missing account choice, a missing review question or a mixture of the two.
        """
        if self.status == "classified":
            if self.account_key is None or self.question is not None:
                raise ValueError("A classification needs an account choice and no question.")
        elif self.account_key is not None or self.question is None:
            raise ValueError("A review needs a question and no account choice.")
        return self


class _ResponseBatch(BaseModel):
    """
    Describe the response envelope used for one completed batch of model decisions.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    decisions: tuple[_ResponseDecision, ...] = Field(min_length=1, max_length=MAX_BATCH_SIZE)


# %% 2. Categorisation ---------------------------------------------------------------------------------------
def classify_batch(
    client: ChatGPTConnection,
    transactions: list[Transaction],
    accounts: AccountRegistry,
    *,
    skill: Skill | None = None,
    guidance: list[Guidance] | None = None,
) -> tuple[list[Decision], dict]:
    """
    Send one batch and accept it only when every decision agrees with the supplied rows and account types.

    Args:
        client: Authenticated connection with an explicitly selected model.
        transactions: At most twenty nonzero, unposted statement lines selected by the journal command.
        accounts: Registry containing their source accounts and the permitted income/expense accounts.
        skill: Accounting instructions loaded once by the command, or None to load the project skill here.
        guidance: Private guidance inferred from human answers; its application remains an AI decision.

    Returns:
        Decisions in input order and reported token usage. Empty input makes no request.

    Raises:
        ValueError: Input records or any part of the completed response fail validation.
        ChatGPTError: The selected model or completed response is unavailable.
    """
    if not transactions:
        return [], {}
    if len(transactions) > MAX_BATCH_SIZE:
        raise ValueError(f"Categorisation batches cannot exceed {MAX_BATCH_SIZE} transactions.")
    if len({row.transaction_id for row in transactions}) != len(transactions):
        raise ValueError("A categorisation batch cannot contain duplicate transaction UUIDs.")
    model = client.model
    if not model:
        raise ValueError("Select a model before categorising transactions.")

    by_id = {account.id: account for account in accounts.accounts.values()}
    for row in transactions:
        source = by_id.get(row.account_id)
        if source is None or source.account_type != AccountType.ASSET or source.bank_details is None:
            raise ValueError("Categorisation currently requires configured asset bank accounts.")
        if row.amount == 0:
            raise ValueError("Zero-value transactions must be reviewed locally.")

    categories: dict[str, Account] = {
        key: account for key, account in accounts.accounts.items()
        if account.account_type in (AccountType.INCOME, AccountType.EXPENSE)
    }
    if not categories:
        raise ValueError("Configure income and expense accounts before categorisation.")

    skill = skill or load_categorisation_skill()
    learned, _ = guidance_context(guidance, accounts, " ".join(row.description for row in transactions))
    rows = {f"t{index}": row for index, row in enumerate(transactions, start=1)}
    payload = {
        "mode": "categorise",
        "currency": "AUD",
        "guidance": learned,
        "accounts": [
            {
                "key": key, "name": redact_bank_details(account.name, accounts),
                "type": account.account_type.value,
                "purpose": redact_bank_details(account.notes or "", accounts),
            }
            for key, account in categories.items()
        ],
        "transactions": [
            {
                "row_id": row_id, "date": row.date.isoformat(),
                "description": redact_bank_details(row.description, accounts),
                "amount": format(row.amount, ".2f"),
            }
            for row_id, row in rows.items()
        ],
    }
    response, usage = client.request(
        instructions=skill.instructions, payload=payload, output_schema=_response_schema(rows, categories)
    )
    try:
        try:
            batch = _ResponseBatch.model_validate_json(response)
        except (TypeError, ValueError):
            # Detailed validation errors can repeat arbitrary model output or financial descriptions.
            raise ValueError("The model response did not match the required decision schema.") from None

        identifiers = [decision.row_id for decision in batch.decisions]
        if len(set(identifiers)) != len(identifiers) or set(identifiers) != set(rows):
            raise ValueError("The model response must include each requested transaction exactly once.")

        decisions = {}
        for result in batch.decisions:
            row = rows[result.row_id]
            account_id = None
            if result.status == "classified":
                if result.account_key is None:
                    raise ValueError("The model omitted the selected account key.")
                account = categories.get(result.account_key)
                if account is None:
                    raise ValueError("The model chose an account outside the permitted catalogue.")
                expected = AccountType.EXPENSE if row.amount < 0 else AccountType.INCOME
                if account.account_type != expected:
                    raise ValueError(
                        "The model chose an account incompatible with the transaction direction."
                    )
                account_id = account.id
            decisions[row.transaction_id] = Decision(
                transaction_id=row.transaction_id, status=result.status,
                account_id=account_id,
                reason=result.reason, question=result.question, model=model, prompt_version=skill.version,
            )
        return [decisions[row.transaction_id] for row in transactions], usage

    except (TypeError, ValueError):
        raise CategorisationBatchError(
            "The completed categorisation response failed validation; no postings were saved.", usage,
        ) from None

# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _response_schema(rows: dict, categories: dict) -> dict:
    """
    Limit structured output to this batch's short row identifiers and permitted account keys.
    """
    schema = _ResponseBatch.model_json_schema()
    fields = schema["$defs"]["_ResponseDecision"]["properties"]
    fields["row_id"]["enum"] = list(rows)
    fields["account_key"]["anyOf"][0]["enum"] = list(categories)
    schema["properties"]["decisions"]["minItems"] = len(rows)
    schema["properties"]["decisions"]["maxItems"] = len(rows)
    return schema
