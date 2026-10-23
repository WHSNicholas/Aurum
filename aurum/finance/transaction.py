# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                Finance - Transaction                                                 #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum uses Transaction objects to carry bank statement lines through importing and storage.

Each object keeps the bank's description, dates, signed AUD amount, source account UUID and source filename
without its directory or extension. Positive amounts are money entering the account, and negative amounts
are money leaving it.

Transfers between accounts keep a separate line from each account's statement. Journal preparation uses
these imported lines for categorisation, source validation and matching the two sides of transfers.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import date
from decimal import Decimal
import re
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


# %% 1. Transactions -----------------------------------------------------------------------------------------
class Transaction(BaseModel):
    """
    Preserve one statement line with exact amounts and traceable source information.

    Attributes:
        transaction_id: UUID generated on first construction and retained when saved and loaded.
        account_id: Persistent UUID of the configured account whose statement contains this line.
        date: Date printed beside the transaction on the statement.
        effective_date: Separately printed effective date, or None when the bank does not supply one.
        description: Original bank wording, with wrapped description lines joined by spaces.
        amount: Signed AUD amount. A credit is positive and a debit is negative; this is not categorisation.
        statement_balance: Running balance printed beside this line, if supplied.
        source_file: Original filename without its directory or extension, such as "2026-05_everyday".
        transfer_reference: Single trailing # reference, preserving leading zeros, or None.

    Fields cannot be reassigned. Later enrichment should validate replacement objects while retaining their
    IDs and source data. The PDF importer matches bank identifiers to a configured account first.

    Raises:
        ValidationError: A field is invalid, required text is blank, or money is non-finite or has fractional
            cents.
    """

    # Configuration
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Identity
    transaction_id: UUID = Field(default_factory=uuid4)
    account_id: UUID

    # Statement details
    date: date
    effective_date: date | None = None
    description: str = Field(min_length=1)

    # Amounts
    # Statement data: amounts are parsed from text directly into Decimal, never through float.
    amount: Decimal = Field(decimal_places=2, allow_inf_nan=False)
    statement_balance: Decimal | None = Field(default=None, decimal_places=2, allow_inf_nan=False)

    # Source
    # PDF hashes belong to import history; page and row positions are temporary parser diagnostics.
    source_file: str = Field(min_length=1)

    # Transfer details
    @property
    def transfer_reference(self) -> str | None:
        """
        Read a single trailing numeric # reference without deciding whether this is an internal transfer.

        Preserve leading zeros and reject descriptions containing multiple references. This is derived
        from the original description and is not stored as another database column.
        """
        match = re.search(r"(?<!\w)#([0-9]+)\s*$", self.description)
        references = re.findall(r"(?<!\w)#([0-9]+)(?!\w)", self.description)
        return match.group(1) if match is not None and len(references) == 1 else None

    # Validation
    @field_validator("description", "source_file")
    @classmethod
    def _reject_blank_text(cls, value: str) -> str:
        """
        Reject blank text without changing the bank's wording or filename stem.
        """
        if not value.strip():
            raise ValueError("text must not be blank")
        return value
