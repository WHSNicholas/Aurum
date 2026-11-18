# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                Finance - Transaction                                                 #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum uses Transaction objects to carry bank statement lines and settled bank-feed activity through storage.

Each object keeps the bank's description, dates, signed AUD amount, source account UUID and source filename
or a stable feed source identifier. Positive amounts are money entering the account, and negative amounts
are money leaving it.

Transfers between accounts keep a separate line from each account's statement. Journal preparation uses
these imported lines for categorisation, source validation and matching the two sides of transfers.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
import re
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Aurum imports
from aurum.finance.accounts import Account, AccountType


# %% 1. Transactions -----------------------------------------------------------------------------------------
class Transaction(BaseModel):
    """
    Preserve one statement line with exact amounts and traceable source information.

    Attributes:
        transaction_id: UUID generated on first construction and retained when saved and loaded.
        account_id: Persistent UUID of the configured account whose statement contains this line.
        date: Statement date, or a feed's posting date with its transaction date as fallback.
        effective_date: Separately supplied effective/value date, or None when the bank does not supply one.
        description: Original bank wording, with wrapped description lines joined by spaces.
        amount: Signed AUD amount. A credit is positive and a debit is negative; this is not categorisation.
        statement_balance: Running balance printed beside this line, if supplied.
        source_file: PDF filename stem, or a stable "redbark:" source identifier for API activity.
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


# %% 2. Transfer Matching ------------------------------------------------------------------------------------
def match_transfers(
    transactions: list[Transaction], accounts: dict[UUID, Account],
    *, evidence: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[list[tuple[Transaction, Transaction]], set[UUID]]:
    """
    Find uniquely paired internal transfers and identify all rows needing internal-transfer handling.

    A pair first needs the same complete trailing # reference. Its two descriptions must identify reciprocal
    BSB/account tuples, with TRANSFER TO on the outgoing row and TRANSFER FROM on the incoming row. Amounts
    must be opposite, source banks distinct and statement dates within three calendar days. Every pending
    row participates in the uniqueness check, including reviews that cannot themselves be posted.

    TRANSFER WITHIN is a separate same-account rule: both rows must name their own configured bank details,
    share the complete reference and statement date, and carry equal opposite amounts. Both source rows
    remain journal postings on that bank account; their total is zero.

    Known internal movements with missing or conflicting details remain in review. Equal amounts alone
    never substitute for a missing transfer reference. Effective dates do not change matching or event dates.
    Feed evidence can supply a complete ANZ route and trailing reference in extended_description. It never
    replaces source wording, combines separate partial fields, or treats a provider reference as an ANZ #.

    Returns:
        Outgoing/incoming pairs, and IDs of every identified or potentially matched internal movement.
    """
    bank_accounts = [
        account for account in accounts.values()
        if account.account_type == AccountType.ASSET and account.bank_details is not None
    ]
    bank_ids = {account.id for account in bank_accounts}
    identifiers: dict[tuple[str, str], set[UUID]] = {}
    numbers: set[str] = set()
    for account in bank_accounts:
        details = account.bank_details
        if details is not None:
            identifiers.setdefault((details.bsb, details.account_number), set()).add(account.id)
            numbers.add(details.account_number)

    grouped: dict[str, list[Transaction]] = {}
    counterparts: dict[UUID, UUID] = {}
    within_mentions, within_routes = set(), set()
    internal = set()
    for row in transactions:
        if row.account_id not in bank_ids:
            continue
        source = (evidence or {}).get(str(row.transaction_id), {})
        extended = source.get("extended_description")
        descriptions = (row.description,)
        if isinstance(extended, str) and extended.strip() and extended != row.description:
            descriptions += (extended,)
        # Observe each original field separately. Partial bank fields must never manufacture a full route.
        description = _transfer_route_text(descriptions)
        # Recognise own-account mentions even when their format is too uncertain for automatic matching.
        for wording in descriptions:
            transfer_word = re.search(r"\bTRANSFER\b", wording, re.IGNORECASE)
            known_number = transfer_word is not None and any(
                re.search(r"(?<!\w)" + r"[ \t-]*".join(number) + r"(?!\w)", wording)
                for number in numbers
            )
            if known_number or _names_internal_account(row, bank_accounts, description=wording):
                internal.add(row.transaction_id)
            if source and transfer_word:
                # An API transfer with insufficient routing needs a human rather than inferred categories.
                internal.add(row.transaction_id)
            if re.search(r"\bTRANSFER\s+WITHIN\b", wording, re.IGNORECASE):
                within_mentions.add(row.transaction_id)
                internal.add(row.transaction_id)
        references = {
            reference for wording in descriptions
            if (reference := _wording_reference(wording)) is not None
        }
        # Conflicting observations still participate in ambiguity checks for every reference they name.
        for reference in references:
            grouped.setdefault(reference, []).append(row)
        if description is None:
            continue
        if row.transaction_id in within_mentions:
            route = re.fullmatch(
                r"TRANSFER\s+WITHIN\s+([0-9]{6})\s*-\s*([0-9]+)\s+#[0-9]+\s*",
                description, re.IGNORECASE,
            )
            if (
                route is not None and row.amount != 0
                and identifiers.get((route[1], route[2])) == {row.account_id}
            ):
                within_routes.add(row.transaction_id)
            continue
        routes = re.findall(
            r"\bTRANSFER\s+(FROM|TO)\s+([0-9]{6})\s*-\s*([0-9]+)(?!\w)",
            description, re.IGNORECASE,
        )
        if len(routes) != 1:
            continue
        direction, bsb, number = routes[0]
        owners = identifiers.get((bsb, number), set())
        if len(owners) != 1 or row.amount == 0:
            continue
        expected_direction = "TO" if row.amount < 0 else "FROM"
        if direction.upper() == expected_direction:
            counterparts[row.transaction_id] = next(iter(owners))

    neighbours: dict[UUID, list[Transaction]] = {row.transaction_id: [] for row in transactions}
    for rows in grouped.values():
        if any(row.transaction_id in internal for row in rows):
            internal.update(row.transaction_id for row in rows)
        for index, first in enumerate(rows):
            for second in rows[index + 1:]:
                if (
                    first.amount == 0 or first.amount != -second.amount
                    or abs((first.date - second.date).days) > 3
                ):
                    continue
                if first.account_id == second.account_id and (
                    first.date != second.date
                    or not {first.transaction_id, second.transaction_id} & within_mentions
                ):
                    continue
                # A shared reference and matching cash movement can reveal an incomplete internal transfer.
                internal.update((first.transaction_id, second.transaction_id))
                # Count plausible counterparts before route validation, including malformed or protected rows.
                neighbours[first.transaction_id].append(second)
                neighbours[second.transaction_id].append(first)

    pairs = []
    for outgoing in transactions:
        if outgoing.amount >= 0 or len(neighbours[outgoing.transaction_id]) != 1:
            continue
        incoming = neighbours[outgoing.transaction_id][0]
        within_pair = (
            outgoing.account_id == incoming.account_id
            and {outgoing.transaction_id, incoming.transaction_id} <= within_routes
        )
        between_pair = (
            outgoing.account_id != incoming.account_id
            and counterparts.get(outgoing.transaction_id) == incoming.account_id
            and counterparts.get(incoming.transaction_id) == outgoing.account_id
        )
        if (
            len(neighbours[incoming.transaction_id]) == 1
            and (within_pair or between_pair)
        ):
            pairs.append((outgoing, incoming))
    return pairs, internal


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _transfer_route_text(descriptions: tuple[str, ...]) -> str | None:
    """
    Choose one complete original field only when its reference, direction and route are uncontradicted.

    A provider payment reference is not supplied here: it is not guaranteed to be ANZ's trailing # value.
    Returning text only for matching leaves both saved source objects and resulting journal lines unchanged.
    """
    references, directions, routes = set(), set(), set()
    candidates = []
    for description in descriptions:
        observed = re.findall(r"(?<!\w)#([0-9]+)(?!\w)", description)
        if len(observed) > 1:
            return None
        references.update(observed)
        directions.update(
            match.upper() for match in re.findall(
                r"\bTRANSFER\s+(FROM|TO|WITHIN)\b", description, re.IGNORECASE,
            )
        )
        matches = re.findall(
            r"\bTRANSFER\s+(FROM|TO|WITHIN)\s+([0-9]{6})\s*-\s*([0-9]+)(?!\w)",
            description, re.IGNORECASE,
        )
        routes.update((direction.upper(), bsb, number) for direction, bsb, number in matches)
        if _wording_reference(description) is not None and len(matches) == 1:
            candidates.append(description)
    if len(references) != 1 or len(directions) != 1 or len(routes) != 1:
        return None
    return next(iter(candidates), None)


def _wording_reference(description: str) -> str | None:
    """
    Keep a single complete trailing numeric reference as text, including its leading zeroes.
    """
    match = re.search(r"(?<!\w)#([0-9]+)\s*$", description)
    references = re.findall(r"(?<!\w)#([0-9]+)(?!\w)", description)
    return match[1] if match is not None and len(references) == 1 else None


def _names_internal_account(
    transaction: Transaction, accounts: list[Account], *, description: str | None = None,
) -> bool:
    """
    Keep explicitly named own-account transfers in review when an exact account-number match is absent.
    """
    description = (transaction.description if description is None else description).casefold()
    if "transfer" not in description:
        return False
    if "between my accounts" in description or "between own accounts" in description:
        return True
    for account in accounts:
        if account.id == transaction.account_id:
            continue
        for label in (account.name, account.key.replace("_", " ")):
            if re.search(r"(?<!\w)" + re.escape(label.casefold()) + r"(?!\w)", description):
                return True
    return False
