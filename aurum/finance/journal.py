# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Finance - Journal                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum uses journal entries to record how each financial event changes its accounts.

An entry contains ordered debit and credit lines whose totals are equal. A simple purchase has two lines,
while an event such as a loan repayment can split its amount between principal and interest. Expense and
income accounts on these lines will supply categories for reporting.

A line can link to the imported transaction that supports it. A transfer can link both bank observations
within one entry, keeping each statement's original description and optional effective date. The entry has
its own accounting date, while descriptions stay on its individual lines. A manual entry can be recorded
without a statement transaction. These models validate amounts and balance; database storage checks account
identities and imported transaction links.

Investment lines also record which security moved and how many units changed hands. Their debit or credit
records carrying cost in AUD; a trade price and fees explain that cost rather than adding another balance.
Holdings are reconstructed from these movements; sales and disposal-cost calculations remain future work.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import date
from decimal import Decimal
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# %% 1. Journal ----------------------------------------------------------------------------------------------
class InvestmentDetails(BaseModel):
    """
    Describe a security movement on an investment asset line.

    Attributes:
        security_id: Persistent identity from the security catalogue.
        units: Positive units received or negative units given up, including fractional crypto units.
        unit_price: Executed AUD price per unit when known; never a later market quote.
        fees: Actual AUD trade fees when known. None means unknown, whereas zero means no fees.

    The journal line records the carrying cost movement. In particular, a sale's price is not its removed
    average cost. Fees are supporting information and must not be added to the posting a second time.
    Units and prices keep their supplied decimal precision; only fees are restricted to whole cents.

    Raises:
        ValidationError: Units are zero or nonfinite, the price is not positive, or fees are invalid.
    """

    # Configuration
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    # Security movement
    security_id: UUID
    units: Decimal = Field(allow_inf_nan=False)
    unit_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    fees: Decimal | None = Field(default=None, ge=0, decimal_places=2, allow_inf_nan=False)

    @field_validator("units")
    @classmethod
    def _reject_zero_units(cls, value: Decimal) -> Decimal:
        """
        Require an actual unit movement.
        """
        if value == 0:
            raise ValueError("investment units must be nonzero")
        return value


class JournalLine(BaseModel):
    """
    Record one debit or credit against an account.

    Attributes:
        account_id: UUID of the account receiving this posting.
        description: Original statement wording for a source-linked line, or an explanation of a manual line.
        effective_date: Bank's effective date, or None when none was supplied; separate from the entry date.
        debit: Positive AUD amount on the debit side, or zero for a credit line.
        credit: Positive AUD amount on the credit side, or zero for a debit line.
        transaction_id: Supporting imported transaction UUID, or None for a line without a statement source.
        investment: Optional security movement on an asset line without a bank statement link.
        budget_category: Optional reporting category key; exclude omits this line from the budget.

    Exactly one side must be positive. The account type determines how a debit or credit changes its balance;
    for an asset account, a debit increases the balance and a credit decreases it. Imported transactions
    retain their separate signed statement amounts.

    Raises:
        ValidationError: Fields are invalid, the description is blank, amounts are negative or contain
            fractional cents, or the line has both sides filled or neither side filled.
    """

    # Configuration
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    # Posting details
    account_id: UUID
    description: str = Field(min_length=1)
    effective_date: date | None = None
    debit: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2, allow_inf_nan=False)
    credit: Decimal = Field(default=Decimal("0"), ge=0, decimal_places=2, allow_inf_nan=False)
    transaction_id: UUID | None = None
    investment: InvestmentDetails | None = None
    budget_category: str | None = Field(
        default=None, min_length=1, exclude_if=lambda value: value is None,
    )

    # Validation
    @field_validator("description")
    @classmethod
    def _reject_blank_description(cls, value: str) -> str:
        """
        Reject blank descriptions while preserving the original statement text exactly.
        """
        if not value.strip():
            raise ValueError("description must not be blank")
        return value

    @field_validator("budget_category")
    @classmethod
    def _check_budget_key(cls, value: str | None) -> str | None:
        """
        Keep an optional reporting key distinct from accounting accounts and reject blank labels.
        """
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("budget_category must be a nonblank key without surrounding whitespace")
        return value

    @model_validator(mode="after")
    def _check_side(self) -> Self:
        """
        Require one posting side and investment units that follow its asset direction.
        """
        if (self.debit > 0) == (self.credit > 0):
            raise ValueError("a journal line must have exactly one positive debit or credit")
        if self.investment is not None:
            if self.transaction_id is not None:
                raise ValueError("investment units belong on the investment line, not the bank source line")
            if (self.investment.units > 0) != (self.debit > 0):
                raise ValueError("positive investment units require a debit; negative units require a credit")
        return self


class JournalEntry(BaseModel):
    """
    Keep the balanced postings for one financial event together.

    Attributes:
        id: Persistent UUID generated when the entry is first constructed.
        date: Accounting date chosen for the event; supporting statement dates can differ.
        lines: Ordered tuple of at least two postings whose debit and credit totals match exactly.

    Fields and nested lines are immutable. Validate a replacement to make changes; model_copy(update=...)
    does not validate updates. Existing model instances are revalidated when passed into these models, so
    replacements cannot silently bypass the accounting rules.

    Raises:
        ValidationError: Fields are invalid, fewer than two lines are supplied, debit and credit totals
            differ, or an imported transaction is linked more than once.
    """

    # Configuration
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    # Event details
    id: UUID = Field(default_factory=uuid4)
    date: date
    lines: tuple[JournalLine, ...] = Field(min_length=2)

    # Validation
    @model_validator(mode="after")
    def _check_postings(self) -> Self:
        """
        Require equal debit and credit totals and distinct imported transaction links.
        """
        debit_cents = 0
        credit_cents = 0
        transaction_ids = set()
        for line in self.lines:
            # Compare whole-cent totals exactly, even when amounts exceed Decimal's current precision.
            numerator, denominator = line.debit.as_integer_ratio()
            debit_cents += numerator * 100 // denominator
            numerator, denominator = line.credit.as_integer_ratio()
            credit_cents += numerator * 100 // denominator

            if line.transaction_id is not None:
                if line.transaction_id in transaction_ids:
                    raise ValueError("an imported transaction cannot appear twice in one journal entry")
                transaction_ids.add(line.transaction_id)

        if debit_cents != credit_cents:
            raise ValueError("journal entry debits and credits must balance exactly")
        return self


# %% 2. Budget Labels ----------------------------------------------------------------------------------------
def preserve_budget_categories(
    entries: tuple[JournalEntry, ...], previous: tuple[JournalEntry, ...],
) -> tuple[JournalEntry, ...]:
    """
    Keep labels through a correction unless its replacement explicitly chooses a category or default.

    Match source links and event lineage first, then account, posting side and amount. Ambiguous changes
    require explicit labels rather than dropping, duplicating or combining competing budget choices.
    """
    proposed = {}
    for old_entry in previous:
        old_sources = {line.transaction_id for line in old_entry.lines if line.transaction_id is not None}
        related = [
            (index, entry) for index, entry in enumerate(entries)
            if entry.id == old_entry.id or old_sources & {
                line.transaction_id for line in entry.lines if line.transaction_id is not None
            }
        ]
        for old in old_entry.lines:
            if old.budget_category is None:
                continue
            candidates = [
                (index, number, line) for index, entry in related
                for number, line in enumerate(entry.lines)
                if line.transaction_id == old.transaction_id and (line.debit > 0) == (old.debit > 0)
            ]
            same_account = [item for item in candidates if item[2].account_id == old.account_id]
            if same_account:
                candidates = same_account
            same_amount = [
                item for item in candidates if (item[2].debit, item[2].credit) == (old.debit, old.credit)
            ]
            if len(same_amount) == 1:
                candidates = same_amount
            if candidates and all(line.budget_category is not None for _, _, line in candidates):
                continue
            if len(candidates) != 1:
                raise ValueError("Assign budget categories explicitly when splitting labelled postings")
            index, number, line = candidates[0]
            # Explicit choices win. Inherited choices must agree when old postings are merged.
            if line.budget_category is None:
                proposed.setdefault((index, number), set()).add(old.budget_category)
    results = []
    for index, entry in enumerate(entries):
        lines = []
        for number, line in enumerate(entry.lines):
            labels = proposed.get((index, number), set())
            if len(labels) > 1:
                raise ValueError("Merged postings have different budget labels; choose one explicitly")
            category = next(iter(labels)) if labels else line.budget_category
            lines.append(JournalLine.model_validate({**line.model_dump(), "budget_category": category}))
        results.append(JournalEntry(id=entry.id, date=entry.date, lines=tuple(lines)))
    return tuple(results)
