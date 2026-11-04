# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                   Finance - Manual                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum carries written manual-entry instructions through the same validated journal used for bank activity.

A submission keeps the user's accounting date, explanation and optional statement references together.
Its case adds relevant bank evidence and existing events without changing those facts. An outcome either
supplies one complete balanced entry, a budget-only monthly savings split, or explains what still prevents it.
Savings splits have no journal lines and cannot claim bank sources. Database reservations, desktop
editing and AI calls remain separate from these immutable models.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import UTC, date, datetime
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Aurum imports
from aurum.finance.dates import parse_input_date
from aurum.finance.journal import JournalEntry
from aurum.finance.investments import InvestmentCalculation, InvestmentInstruction
from aurum.finance.transaction import Transaction
from aurum.finance.savings import SavingsAllocation, SavingsContext


# %% 1. Manual Instructions ----------------------------------------------------------------------------------
class ManualInput(BaseModel):
    """
    Keep one saved instruction and its stable identity independent of the desktop editor.

    Source references are optional; they identify imported evidence rather than extra journal lines.
    Context revision identifies the generated accounting state shown beside the user's instruction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    id: UUID = Field(default_factory=uuid4)
    date: date
    instruction: str = Field(min_length=1)
    source_transactions: tuple[UUID, ...] = ()
    human_response: str = ""
    context_revision: str = Field(default="", pattern=r"^([0-9a-f]{64})?$")

    @field_validator("date", mode="before")
    @classmethod
    def _parse_date(cls, value: object) -> date | None:
        """
        Accept either supported date format before validating the required accounting date.
        """
        return parse_input_date(value)

    @field_validator("instruction")
    @classmethod
    def _require_instruction(cls, value: str) -> str:
        """
        Reject blank instructions without changing the user's wording.
        """
        if not value.strip():
            raise ValueError("A manual instruction must not be blank.")
        return value

    @field_validator("source_transactions")
    @classmethod
    def _distinct_sources(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        """
        Require each explicit source reference once.
        """
        if len(set(value)) != len(value):
            raise ValueError("Manual source references must be distinct.")
        return value


class ManualCase(BaseModel):
    """
    Supply a manual instruction with required sources and separate monthly evidence for interpretation.

    Required sources include explicit references and sources already owned by this manual entry. Other
    monthly evidence is read-only until selected. Choosing a source from an existing event requires preserving
    that event's complete source group. The catalogue version identifies the configuration used for an
    interpretation, so changed instruments can release an earlier investment question once.
    The separate budget revision prevents a saved proposal from silently adopting different report labels.
    The optional memory revision rejects stale interpretations after explicitly remembered notes change.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    submission: ManualInput
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    transactions: tuple[Transaction, ...] = ()
    required_sources: tuple[UUID, ...] = ()
    entries: tuple[JournalEntry, ...] = ()
    evidence_transactions: tuple[Transaction, ...] = Field(default=(), exclude_if=lambda value: not value)
    evidence_entries: tuple[JournalEntry, ...] = Field(default=(), exclude_if=lambda value: not value)
    evidence_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None,
    )
    memory_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None,
    )
    question: str | None = None
    catalogue_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    savings_context: SavingsContext | None = Field(default=None, exclude_if=lambda value: value is None)
    budget_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def _check_context(self) -> Self:
        """
        Require complete, distinct evidence and keep all required references inside the case.
        """
        if self.savings_context is not None and (
            self.transactions or self.required_sources or self.entries or self.evidence_transactions
            or self.evidence_entries
        ):
            raise ValueError("Virtual savings cannot claim bank sources or change journal entries")
        source_ids = {row.transaction_id for row in self.transactions}
        if len(source_ids) != len(self.transactions):
            raise ValueError("A manual case cannot contain duplicate transactions.")
        required = set(self.required_sources)
        if len(required) != len(self.required_sources) or not required <= source_ids:
            raise ValueError("Required manual sources must be distinct candidates.")
        if not set(self.submission.source_transactions) <= required:
            raise ValueError("Explicit manual sources must be required.")
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("A manual case cannot contain duplicate journal events.")
        linked = [
            line.transaction_id for entry in self.entries for line in entry.lines
            if line.transaction_id is not None
        ]
        if len(linked) != len(set(linked)) or not set(linked) <= source_ids:
            raise ValueError("Current manual events need complete, distinct candidate sources.")
        available_ids = [row.transaction_id for row in self.available_transactions]
        if len(set(available_ids)) != len(available_ids):
            raise ValueError("Read-only evidence must not repeat claimed transactions.")
        if len({entry.id for entry in self.available_entries}) != len(self.available_entries):
            raise ValueError("Read-only evidence must not repeat claimed events.")
        for entry in self.evidence_entries:
            if not {line.transaction_id for line in entry.lines if line.transaction_id} <= set(available_ids):
                raise ValueError("Monthly evidence must include complete existing source groups.")
        return self

    @property
    def available_transactions(self) -> tuple[Transaction, ...]:
        """
        Offer shared monthly facts without treating them as claims on bank activity.
        """
        return self.transactions + self.evidence_transactions

    @property
    def available_entries(self) -> tuple[JournalEntry, ...]:
        """
        Include existing accounting available to an explicitly selected correction.
        """
        return self.entries + self.evidence_entries


# %% 2. Manual Results ---------------------------------------------------------------------------------------
class ManualOutcome(BaseModel):
    """
    Return a complete event, a focused question or an explanation of an unsupported operation.

    AI outcomes retain model and skill metadata; explicit human postings record their origin without a model.
    A pricing result
    retains a validated investment instruction while Python retrieves its quote; it cannot be posted yet.
    Completed investments retain both that instruction and their calculation outside the journal table.
    An optional investment budget label follows the saved interpretation onto the receiving holding line.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    manual_id: UUID
    status: Literal["apply", "needs_review", "unsupported", "pricing"]
    event_kind: Literal["general", "opening", "investment", "savings"] = "general"
    entry: JournalEntry | None = None
    savings: SavingsAllocation | None = Field(default=None, exclude_if=lambda value: value is None)
    investment: InvestmentInstruction | None = None
    pricing: InvestmentCalculation | None = None
    investment_budget_category: str | None = Field(
        default=None, min_length=1, exclude_if=lambda value: value is None,
    )
    reason: str = Field(min_length=1)
    question: str | None = Field(default=None, min_length=1)
    origin: Literal["ai", "human"] = "ai"
    model: str | None = Field(default=None, min_length=1)
    skill_version: str | None = Field(default=None, min_length=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("reason", "question", "model", "skill_version")
    @classmethod
    def _require_text(cls, value: str | None) -> str | None:
        """
        Reject blank explanations and request metadata.
        """
        if value is not None and not value.strip():
            raise ValueError("Manual result text must not be blank.")
        return value

    @model_validator(mode="after")
    def _check_result(self) -> Self:
        """
        Keep applied postings separate from unresolved questions and unsupported requests.
        """
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("Manual result timestamps must include a timezone.")
        if self.origin == "ai" and (self.model is None or self.skill_version is None):
            raise ValueError("AI outcomes must retain their model and skill version.")
        if self.origin == "human" and (self.model is not None or self.skill_version is not None):
            raise ValueError("Direct human edits do not make an AI request.")
        if self.status == "apply":
            if self.event_kind == "savings":
                if self.savings is None or self.entry is not None or self.question is not None:
                    raise ValueError("Applied savings needs an allocation without a journal or question")
            elif self.entry is None or self.question is not None:
                raise ValueError("An applied manual result needs an entry and no question.")
        elif self.entry is not None:
            raise ValueError("An unresolved manual result cannot contain postings.")
        if self.savings is not None and (self.event_kind != "savings" or self.status != "apply"):
            raise ValueError("Only an applied savings result can contain an allocation")
        if self.event_kind == "savings" and (
            self.investment is not None or self.pricing is not None
            or self.investment_budget_category is not None or self.entry is not None
        ):
            raise ValueError("Savings allocations cannot contain investment facts or accounting postings")
        if self.status == "needs_review" and self.question is None:
            raise ValueError("An unresolved manual result needs a question.")
        if self.status == "unsupported" and self.question is not None:
            raise ValueError("An unsupported manual result needs a reason, not a clarification question.")
        if self.status == "pricing" and (
            self.investment is None or self.event_kind != "investment" or self.question is not None
        ):
            raise ValueError("A pricing result needs an investment instruction and no question.")
        if self.origin == "ai" and self.status == "apply" and self.event_kind == "investment" and (
            self.investment is None or self.pricing is None
        ):
            raise ValueError("A posted investment needs its instruction and calculation.")
        return self
