# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Finance - Accounts                                                  #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum uses accounts to organise financial activity and identify where each transaction belongs.

This file defines an account's identity, accounting type, institution and optional closing date,
along with optional Australian bank details. It keeps accounts in a read-only registry and checks that
their configuration keys and UUIDs are unique.

Account definitions are loaded from private YAML configuration.
New accounts receive UUIDs that are saved back to the configuration before use, keeping their identity
stable between runs. Bank details identify one configured account for statement imports.

Accounts are flat. Balances start at zero and change only through posted journal entries. Starting positions
are recorded as balanced manual entries with debit and credit legs. Report groupings and account subdivisions
remain separate future work.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Mapping
from datetime import date
from enum import Enum
import io
import os
from pathlib import Path
import stat
import tempfile
from types import MappingProxyType
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

# Aurum imports
from aurum.finance.dates import parse_input_date

# Default paths
DEFAULT_ACCOUNTS = Path(__file__).resolve().parents[2] / "config" / "accounts.yaml"


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

    # Configuration
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Bank identifiers
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
    optional metadata; no balance is stored here. A missing closing date means the account is active.
    Closed accounts remain available for historical reporting.

    Attributes:
        key: Stable, case-sensitive identifier used in configuration and account lookups.
        name: Human-readable label shown in dashboards and reports.
        account_type: Fundamental accounting classification.
        id: UUID generated on first construction, or supplied again when restoring a saved account.
        bank_details: Optional Australian BSB and bank account number.
        institution: Optional display name of the bank or other institution, without an entity link.
        closing_date: Real closure date, or None while the account remains active.
        notes: Optional free-text context.

    Pydantic accepts valid string forms of account_type, UUID, and dates. Fields cannot be reassigned after
    construction. To change an account, validate a new Account from its data, then rebuild the registry.
    Pydantic's model_copy(update=...) does not validate updates. Persist UUIDs in account configuration so
    load_accounts restores the same identities on every run.

    Example:
        Create an account with synthetic bank identifiers::

            everyday = Account(
                key="everyday",
                name="Everyday bank account",
                account_type=AccountType.ASSET,
                institution="Example Bank",
                bank_details=BankDetails(bsb="123456", account_number="00012345")
            )

    Raises:
        ValidationError: Required text is blank or a field, including the closing date, is invalid.
    """

    # Configuration
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Identity
    key: str = Field(min_length=1)
    name: str = Field(min_length=1)
    id: UUID = Field(default_factory=uuid4)

    # Core fields
    account_type: AccountType
    bank_details: BankDetails | None = None
    institution: str | None = None

    # Miscellaneous
    closing_date: date | None = None
    notes: str | None = None

    # Validation
    @field_validator("closing_date", mode="before")
    @classmethod
    def _parse_closing_date(cls, value: object) -> date | None:
        """
        Accept either supported input format for an optional closing date.
        """
        return parse_input_date(value)

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
        # Input validation
        if not isinstance(accounts, list):
            raise TypeError("accounts must be a list of Account objects")

        # Account lookup
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

        self._accounts: Mapping[str, Account] = MappingProxyType(by_key)

    @property
    def accounts(self) -> Mapping[str, Account]:
        """
        Return accounts indexed by key in a read-only mapping.

        Returns:
            Accounts in input order, including closed accounts.
        """
        return self._accounts


# %% 2. Account Configuration --------------------------------------------------------------------------------
def load_accounts(
    path: Path | str = DEFAULT_ACCOUNTS, *, initialise_ids: bool = True,
) -> AccountRegistry:
    """
    Load account definitions and save missing UUIDs once, after validating the complete configuration.

    An omitted or null id creates a new UUID; an existing id is never replaced. YAML comments, quoted text
    and field order are retained when IDs are added. A file with every UUID already saved is not rewritten.
    Quote bank identifiers in YAML so leading zeros remain part of the text.

    Args:
        path: Private .yaml or .yml file containing a list of Account definitions.
        initialise_ids: Save missing identities when True; reject them without writing when False.

    Returns:
        An AccountRegistry with unique keys, UUIDs, and any supplied bank identifiers.

    Raises:
        OSError: The configuration cannot be read or generated UUIDs cannot be saved.
        ValueError: File syntax, account fields or identifiers are invalid, or the file changed while saving.
    """
    # Configuration file
    path = Path(path).resolve()
    if path.suffix.lower() not in {".yaml", ".yml"}:
        raise ValueError("Account configuration must use a .yaml or .yml extension")
    original = path.read_bytes()
    text = original.decode("utf-8")
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.allow_duplicate_keys = False
    yaml.width = 110
    try:
        data = yaml.load(text)
    except YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        line, column = getattr(mark, "line", None), getattr(mark, "column", None)
        location = ""
        if isinstance(line, int) and isinstance(column, int):
            location = f" at line {line + 1}, column {column + 1}"
        raise ValueError(
            f"Invalid YAML account configuration{location}; check syntax and duplicate keys"
        ) from None
    if not isinstance(data, list):
        raise ValueError("Account configuration must contain a list of accounts")

    # Account definitions
    accounts = []
    generated = []
    seen_mappings = set()
    seen_bank_details = set()
    for index, fields in enumerate(data, start=1):
        if not isinstance(fields, dict):
            raise ValueError(f"Account {index} must be a mapping of field names to values")
        if id(fields) in seen_mappings:
            raise ValueError("Each account needs its own definition; do not repeat an account mapping alias")
        seen_mappings.add(id(fields))
        missing_id = fields.get("id") is None
        if missing_id and not initialise_ids:
            raise ValueError(
                f"Account {index} needs a saved UUID; load accounts normally before using read-only mode"
            )
        values = {key: value for key, value in fields.items() if key != "id"} if missing_id else fields
        account = Account.model_validate(values)
        if account.bank_details is not None:
            identifiers = (account.bank_details.bsb, account.bank_details.account_number)
            if identifiers in seen_bank_details:
                raise ValueError("Multiple configured accounts use the same bank identifiers")
            seen_bank_details.add(identifiers)
        accounts.append(account)
        if missing_id:
            own_fields = dict(fields.non_merged_items()) if isinstance(fields, CommentedMap) else fields
            generated.append((fields, account.id, "id" in own_fields))
    registry = AccountRegistry(accounts)

    # Persist identities only after every account and duplicate check has passed.
    if generated:
        for fields, identifier, has_id in generated:
            if isinstance(fields, CommentedMap) and not has_id:
                # Section comments can belong to the last field; insert before them, not after them.
                fields.insert(0, "id", str(identifier))
            else:
                fields["id"] = str(identifier)
        stream = io.StringIO()
        yaml.dump(data, stream)
        _save_account_ids(path, stream.getvalue().encode("utf-8"), original)
    return registry


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _save_account_ids(path: Path, content: bytes, original: bytes) -> None:
    """
    Replace validated configuration atomically, keeping permissions and detecting concurrent content edits.

    Any write failure leaves the caller without a registry, so unsaved UUIDs cannot escape into transactions.
    A temporary file in the same folder is removed after a failed write or content conflict.
    """
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != original:
            raise ValueError("Account configuration changed while UUIDs were being saved; load it again")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
