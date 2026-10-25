# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Finance - Savings                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum lets the user divide monthly budget income left after expenses between virtual savings pots.

These immutable models describe the split and the available monthly evidence supplied to the manual workflow.
They are budget instructions, not accounts or journal entries. Editing a saved split replaces that
instruction; an empty split returns its allocation to the residual pot.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import date
from decimal import Decimal
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


Money = Annotated[Decimal, Field(decimal_places=2, allow_inf_nan=False)]


# %% 1. Savings Allocations ----------------------------------------------------------------------------------
class SavingsCapacityError(ValueError):
    """
    Request a smaller allocation when posted monthly savings cannot cover the proposed split.
    """


class SavingsAmount(BaseModel):
    """
    Assign a nonnegative whole-cent AUD amount to one configured virtual pot.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    category_key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    amount: Money = Field(ge=0)


class SavingsAllocation(BaseModel):
    """
    Keep one manual instruction's complete split for the reporting month containing its date.

    Amounts replace this instruction's earlier split. Empty amounts explicitly reset it; other manual
    instructions remain unchanged. Pot names must be unique within the split.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    date: date
    amounts: tuple[SavingsAmount, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def _check_categories(self) -> Self:
        """
        Reject repeated categories rather than silently combining two interpretations.
        """
        keys = [item.category_key for item in self.amounts]
        if len(keys) != len(set(keys)):
            raise ValueError("A savings allocation must name each pot only once")
        return self

    @property
    def total(self) -> Decimal:
        """
        Add whole cents exactly, without rounding through the current Decimal precision.
        """
        cents = sum(item.amount.as_integer_ratio()[0] * 100 // item.amount.as_integer_ratio()[1]
                    for item in self.amounts)
        return Decimal(f"{cents // 100}.{cents % 100:02d}")


class SavingsContext(BaseModel):
    """
    Show monthly budget surplus available for allocation, other splits and this instruction's previous split.

    The revision fingerprints the month, configuration and saved evidence before an AI request. Storage
    checks the current capacity again when applying the resulting fixed amounts. Baseline is income less
    expenses and existing explicit journal allocations, before subtracting other manual splits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    month: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}$")
    baseline: Money
    other_allocated: Money = Field(ge=0)
    current: SavingsAllocation | None = None
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
