# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                   Finance - Review                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum turns written review answers into validated corrections to complete journal events.

These models carry the statement facts, current postings and user answers through the review workflow.
A revision identifies the accounting state the user reviewed, so a later change cannot be overwritten
silently. A successful outcome contains complete replacement entries; an uncertain outcome asks a focused
question and leaves existing accounting in place. Reusable guidance remains separate from journal postings.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Aurum imports
from aurum.finance.journal import JournalEntry
from aurum.finance.transaction import Transaction


# %% 1. Review Context ---------------------------------------------------------------------------------------
class ReviewAnswer(BaseModel):
    """
    Keep one user's answer linked to the statement row and accounting revision they reviewed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    transaction_id: UUID
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_review: str = Field(min_length=1)

    @field_validator("human_review")
    @classmethod
    def _reject_blank_answer(cls, value: str) -> str:
        """
        Require an answer without changing the user's original wording.
        """
        if not value.strip():
            raise ValueError("A review answer must not be blank.")
        return value


class ReviewCase(BaseModel):
    """
    Group complete source transactions and existing events with the answers needed to review them together.

    Transactions include every source linked to an affected event, even when only one source row has an
    answer. The revision describes this complete group's accounting state before any correction.
    An optional budget revision records the category catalogue supplied to this specific review request.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    id: UUID = Field(default_factory=uuid4)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    budget_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None,
    )
    transactions: tuple[Transaction, ...] = Field(min_length=1)
    entries: tuple[JournalEntry, ...] = ()
    answers: tuple[ReviewAnswer, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_context(self) -> Self:
        """
        Require distinct sources and events, with answers and current postings inside the source group.
        """
        source_ids = {transaction.transaction_id for transaction in self.transactions}
        if len(source_ids) != len(self.transactions):
            raise ValueError("A review case cannot contain duplicate transactions.")
        answer_ids = {answer.transaction_id for answer in self.answers}
        if len(answer_ids) != len(self.answers) or not answer_ids <= source_ids:
            raise ValueError("Review answers must refer to distinct transactions in the case.")
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("A review case cannot contain duplicate journal entries.")
        linked_ids = [
            line.transaction_id for entry in self.entries for line in entry.lines
            if line.transaction_id is not None
        ]
        if len(set(linked_ids)) != len(linked_ids) or not set(linked_ids) <= source_ids:
            raise ValueError("Current entries must link distinct transactions from the case.")
        return self


# %% 2. Review Results ---------------------------------------------------------------------------------------
class Guidance(BaseModel):
    """
    Describe a narrowly scoped preference learned from a review, separately from the corrected accounting.

    Scope states when the instruction applies. Account references make renamed or removed configuration
    detectable, while supersedes identifies earlier guidance explicitly replaced by this instruction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    id: UUID = Field(default_factory=uuid4)
    scope: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=800)
    account_ids: tuple[UUID, ...]
    supersedes: tuple[UUID, ...] = ()

    @field_validator("scope", "instruction")
    @classmethod
    def _reject_blank_guidance(cls, value: str) -> str:
        """
        Require meaningful scope and instruction text.
        """
        if not value.strip():
            raise ValueError("Guidance text must not be blank.")
        return value

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        """
        Keep references distinct and prevent guidance from replacing itself.
        """
        if len(set(self.account_ids)) != len(self.account_ids):
            raise ValueError("Guidance account references must be distinct.")
        if len(set(self.supersedes)) != len(self.supersedes) or self.id in self.supersedes:
            raise ValueError("Guidance replacements must be distinct and cannot include itself.")
        return self


class GuidanceResult(BaseModel):
    """
    Keep useful learned preferences, an explicit no-guidance explanation and safe validation errors together.

    Partial success retains valid candidates alongside errors for the rejected ones. An empty successful
    assessment needs a reason, so missing or malformed model output is never mistaken for deliberate silence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    guidance: tuple[Guidance, ...] = ()
    reason: str | None = Field(default=None, min_length=1, max_length=500)
    errors: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check_assessment(self) -> Self:
        """
        Require an explained result and meaningful diagnostics without discarding valid partial guidance.
        """
        if self.reason is not None and not self.reason.strip():
            raise ValueError("A guidance reason must not be blank.")
        if any(not error.strip() for error in self.errors):
            raise ValueError("Guidance errors must not be blank.")
        if not self.guidance and self.reason is None and not self.errors:
            raise ValueError("An empty guidance assessment needs a reason or validation errors.")
        if len({item.id for item in self.guidance}) != len(self.guidance):
            raise ValueError("Learned guidance UUIDs must be distinct.")
        return self


class ReviewOutcome(BaseModel):
    """
    Return either complete replacement events or a clarification question for one review case.

    Model and skill metadata record which instructions produced the result. Optional learned guidance
    accompanies successful accounting without being needed to represent a valid correction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    case_id: UUID
    status: Literal["apply", "needs_review"]
    entries: tuple[JournalEntry, ...] = ()
    reason: str = Field(min_length=1)
    question: str | None = Field(default=None, min_length=1)
    guidance: tuple[Guidance, ...] = ()
    guidance_reason: str | None = Field(default=None, min_length=1, max_length=500)
    guidance_errors: tuple[str, ...] = ()
    model: str = Field(min_length=1)
    skill_version: str = Field(min_length=1)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("reason", "question", "guidance_reason", "model", "skill_version")
    @classmethod
    def _reject_blank_text(cls, value: str | None) -> str | None:
        """
        Reject blank explanations or request metadata.
        """
        if value is not None and not value.strip():
            raise ValueError("Review result text must not be blank.")
        return value

    @model_validator(mode="after")
    def _check_result(self) -> Self:
        """
        Keep applied corrections and clarification requests separate, with unique events and source links.
        """
        if self.updated_at.tzinfo is None or self.updated_at.utcoffset() is None:
            raise ValueError("Review timestamps must include a timezone.")
        if self.status == "needs_review":
            if self.question is None or self.entries or self.guidance:
                raise ValueError(
                    "A clarification needs a question and no replacement accounting or guidance."
                )
        elif not self.entries or self.question is not None:
            raise ValueError("An applied review needs replacement entries and no clarification question.")

        entry_ids = {entry.id for entry in self.entries}
        if len(entry_ids) != len(self.entries):
            raise ValueError("Replacement journal entry UUIDs must be distinct.")
        source_ids = [
            line.transaction_id for entry in self.entries for line in entry.lines
            if line.transaction_id is not None
        ]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("A source transaction cannot appear in multiple replacement entries.")
        if len({item.id for item in self.guidance}) != len(self.guidance):
            raise ValueError("Learned guidance UUIDs must be distinct.")
        if any(not error.strip() for error in self.guidance_errors):
            raise ValueError("Guidance errors must not be blank.")
        return self
