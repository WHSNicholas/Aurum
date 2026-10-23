# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                   Finance - Manual                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum carries written manual-entry instructions through the same validated journal used for bank activity.

A submission keeps the user's accounting date, explanation and optional statement references together.
Its case adds relevant bank evidence and existing events without changing those facts. An outcome either
supplies one complete balanced entry or explains what still prevents posting. Database reservations, CSV
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


# %% 1. Manual Instructions ----------------------------------------------------------------------------------
class ManualInput(BaseModel):
    """
    Keep one editable instruction and its stable identity independent of the CSV interface.

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
    Supply a manual instruction with candidate sources and the existing events it could replace.

    Required sources include explicit references and sources already owned by this manual entry. Other
    candidates are available for selection, but choosing a source from an existing event requires preserving
    that event's complete source group. The catalogue version identifies the configuration used for an
    interpretation, so changed instruments can release an earlier investment question once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    submission: ManualInput
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    transactions: tuple[Transaction, ...] = ()
    required_sources: tuple[UUID, ...] = ()
    entries: tuple[JournalEntry, ...] = ()
    question: str | None = None
    catalogue_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _check_context(self) -> Self:
        """
        Require complete, distinct evidence and keep all required references inside the case.
        """
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
        return self


# %% 2. Manual Results ---------------------------------------------------------------------------------------
class ManualOutcome(BaseModel):
    """
    Return a complete event, a focused question or an explanation of an unsupported operation.

    Model and skill metadata record the instructions used to interpret this submission. A pricing result
    retains a validated investment instruction while Python retrieves its quote; it cannot be posted yet.
    Completed investments retain both that instruction and their calculation outside the journal table.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    manual_id: UUID
    status: Literal["apply", "needs_review", "unsupported", "pricing"]
    event_kind: Literal["general", "opening", "investment"] = "general"
    entry: JournalEntry | None = None
    investment: InvestmentInstruction | None = None
    pricing: InvestmentCalculation | None = None
    reason: str = Field(min_length=1)
    question: str | None = Field(default=None, min_length=1)
    model: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
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
        if self.status == "apply":
            if self.entry is None or self.question is not None:
                raise ValueError("An applied manual result needs an entry and no question.")
        elif self.entry is not None:
            raise ValueError("An unresolved manual result cannot contain postings.")
        if self.status == "needs_review" and self.question is None:
            raise ValueError("An unresolved manual result needs a question.")
        if self.status == "unsupported" and self.question is not None:
            raise ValueError("An unsupported manual result needs a reason, not a clarification question.")
        if self.status == "pricing" and (
            self.investment is None or self.event_kind != "investment" or self.question is not None
        ):
            raise ValueError("A pricing result needs an investment instruction and no question.")
        if self.status == "apply" and self.event_kind == "investment" and (
            self.investment is None or self.pricing is None
        ):
            raise ValueError("A posted investment needs its instruction and calculation.")
        return self
