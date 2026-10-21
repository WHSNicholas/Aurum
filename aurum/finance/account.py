# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Finance - Account                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Define and validate Aurum's standalone accounts.

AccountType identifies an account's fundamental accounting classification. BankDetails stores optional
Australian bank identifiers. Account describes identity, classification, institution, and lifecycle dates.

AccountRegistry checks account keys and UUIDs and provides read-only lookups. Account balances will eventually
be derived from journal entries, starting at zero.

This module defines the accounting structure only. Configuration loading, journal processing, balance
calculations, report grouping, and any account subdivisions will be considered in later steps.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
from collections.abc import Mapping
from datetime import date
from enum import Enum
from types import MappingProxyType
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# %% 1. Accounts ---------------------------------------------------------------------------------------------
class AccountType(Enum):
    """
    High-level accounting classification for an account.

    Attributes:
        ASSET: Resources owned or controlled with future economic benefit (bank accounts, investments)
        LIABILITY: Obligations you owe to others (credit card balances, student debt)
        EQUITY: Net worth components representing ownership interest (retained earnings)
        INCOME: Earnings or sources of cash inflow (salary, interest income)
        EXPENSE: Costs incurred (groceries, account fees)

    Each member has a lowercase string value for use in configuration and storage. Account accepts either
    the enum member, such as AccountType.ASSET, or its string value, such as "asset".
    """

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"


class BankDetails(BaseModel):
    """
    Hold Australian bank identifiers without treating them as numbers.

    Attributes:
        bsb: Six-digit Bank State Branch code.
        account_number: Nonempty sequence of digits. Keeping it as text preserves leading zeros.
        full_details: Read-only BSB-account-number string for display.

    Pydantic validates both identifiers. Access full_details to display them together. Other kinds of accounts
    can leave Account.bank_details unset.

    Raises:
        ValidationError: The BSB or account number has an invalid format.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    bsb: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]+$")
    account_number: str = Field(min_length=1, pattern=r"^[0-9]+$")

    @property
    def full_details(self) -> str:
        """
        Return the BSB and account number separated by a hyphen for display.

        Returns:
            A string such as "123456-00012345", preserving any leading zeros.
        """
        return f"{self.bsb}-{self.account_number}"


class Account(BaseModel):
    """
    Describe one standalone account that can receive journal postings.

    Accounts are flat: they have no reporting hierarchy or nested balances. Bank details and institution are
    optional metadata; no balance is stored here. The opening date may be unknown or not applicable. A missing
    closing date means the account is active. Closed accounts remain available for historical reporting.

    Attributes:
        key: Stable, case-sensitive identifier used in configuration and transactions.
        name: Human-readable label shown in dashboards and reports.
        account_type: Fundamental accounting classification.
        opening_date: Real opening date if known and applicable, or None.
        id: UUID generated on first construction, or supplied again when restoring a saved account.
        bank_details: Optional Australian BSB and bank account number.
        institution: Optional display name of the bank or other institution, without an entity link.
        closing_date: Real closure date, or None while the account remains active.
        notes: Optional free-text context.

    Pydantic accepts valid string forms of account_type, UUID, and dates. Fields cannot be reassigned after
    construction. To change an account, validate a new Account from its data, then rebuild the registry.
    Pydantic's model_copy(update=...) does not validate updates. The UUID must later be persisted by
    configuration so a reload retains the same identity.

    Example:
        Create an account with synthetic bank identifiers::

            everyday = Account(
                key="everyday",
                name="Everyday bank account",
                account_type=AccountType.ASSET,
                opening_date="2020-01-01",
                institution="Example Bank",
                bank_details=BankDetails(bsb="123456", account_number="00012345")
            )

    Raises:
        ValidationError: Required text is blank, a field is invalid, or known dates are out of order.
    """

    # Configuration
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Identity
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    id: UUID = Field(default_factory=uuid4)

    # Core Fields
    account_type: AccountType
    bank_details: BankDetails | None = None
    institution: str | None = None

    # Miscellaneous
    opening_date: date | None = None
    closing_date: date | None = None
    notes: str | None = None

    # Validation
    @field_validator("key", "name", "institution")
    @classmethod
    def _reject_blank_text(cls, value: str | None) -> str | None:
        """
        Reject blank text without changing valid values.

        Args:
            value: Text to check, or None for an optional field.

        Returns:
            The unchanged value.

        Raises:
            ValueError: If the text contains only whitespace.
        """
        if value is not None and not value.strip():
            raise ValueError("text must not be blank")
        return value

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        """
        Reject a closing date before a known opening date.

        Returns:
            This account when its dates are valid.

        Raises:
            ValueError: If both dates are known and the closing date is earlier.
        """
        if (
            self.opening_date is not None
            and self.closing_date is not None
            and self.closing_date < self.opening_date
        ):
            raise ValueError("closing_date must not precede opening_date")
        return self


class AccountRegistry:
    """
    Store accounts in a read-only lookup by key.

    This registry is Aurum's chart of accounts. Keys and UUIDs must be unique. Closed accounts remain
    available for historical reporting. The registry does not calculate balances or decide report groups.

    Args:
        accounts: Account objects in lookup and display order.

    Raises:
        TypeError: The input is not a list of Account objects.
        ValueError: A key or UUID is duplicated.
    """

    def __init__(self, accounts: list[Account]) -> None:
        """
        Build the account lookup and reject duplicates.

        Args:
            accounts: Account objects to include.

        Raises:
            TypeError: If the input or an item has the wrong type.
            ValueError: If a key or UUID is duplicated.
        """
        if not isinstance(accounts, list):
            raise TypeError("accounts must be a list of Account objects")

        by_key: dict[str, Account] = {}
        seen_ids: set[UUID] = set()
        for account in accounts:
            if not isinstance(account, Account):
                raise TypeError("accounts must contain only Account objects")
            if account.key in by_key:
                raise ValueError(f"Duplicate account key: {account.key!r}")
            if account.id in seen_ids:
                raise ValueError(f"Duplicate account id: {account.id}")
            by_key[account.key] = account
            seen_ids.add(account.id)

        # A mapping proxy prevents changes to the lookup after validation.
        self._accounts: Mapping[str, Account] = MappingProxyType(by_key)

    @property
    def accounts(self) -> Mapping[str, Account]:
        """
        Return accounts indexed by key in a read-only mapping.

        Returns:
            Accounts in input order, including closed accounts.
        """
        return self._accounts
