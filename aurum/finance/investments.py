# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                Finance - Investments                                                 #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum calculates investment units and book cost and retrieves the market prices needed to complete them.

Opening holdings and purchases use exact decimal calculations. Supplied trade figures and bank settlements
take precedence over reference prices. When a price is needed, Aurum checks its SQLite cache before fetching
a completed AUD daily close through yfinance. Missing or unsuitable prices produce a question rather than
an invented value.

Holdings are reconstructed from posted journal movements up to a requested date. Market quotes retain their
provider and actual trading date, while SQLite storage lives in database/prices.py. Fetching a quote does not
change the journal or create valuation adjustments.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from hashlib import sha256
import json
import sqlite3
from typing import Literal, Self
from uuid import UUID, uuid4
import warnings

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Aurum imports
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.journal import AccountInvestmentDetails, InvestmentDetails, JournalEntry, JournalLine
from aurum.finance.securities import Security, SecurityRegistry, SecurityType
from aurum.finance.transaction import Transaction


# %% 1. Investment Details -----------------------------------------------------------------------------------
class DollarConversion(BaseModel):
    """
    Preserve the USD security close and same-date Yahoo USD/AUD rate behind an AUD valuation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    usd_close: Decimal = Field(gt=0, allow_inf_nan=False)
    aud_per_usd: Decimal = Field(gt=0, allow_inf_nan=False)

    def aud_price(self) -> Decimal:
        """
        Convert one unit before the final holding value is rounded to cents.
        """
        with localcontext() as context:
            context.prec = 50
            return self.usd_close * self.aud_per_usd


class PriceQuote(BaseModel):
    """
    Keep a positive AUD daily close and its exact date and provider together.

    requested_date is the accounting date being priced; price_date can be an earlier trading day within
    seven calendar days. Historical closes remain fixed when cached or attached to an accepted instruction.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    security_id: UUID
    requested_date: date
    price_date: date
    price: Decimal = Field(gt=0, allow_inf_nan=False)
    currency: Literal["AUD"] = "AUD"
    provider: Literal["yahoo"] = "yahoo"
    symbol: str = Field(min_length=1)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    conversion: DollarConversion | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        """
        Require a recent earlier close and an aware retrieval timestamp.
        """
        if not 0 <= (self.requested_date - self.price_date).days <= 7:
            raise ValueError("A quote must be on the requested date or within the previous seven days")
        if self.conversion and self.conversion.aud_price() != self.price:
            raise ValueError("The AUD price must match the recorded USD close and conversion rate")
        if self.fetched_at.utcoffset() is None:
            raise ValueError("Quote retrieval timestamps must include a timezone")
        return self


class InvestmentInstruction(BaseModel):
    """
    Preserve the human-supplied investment facts after AI resolves account and security references.

    amount means total spent unless amount_includes_fees is False. Missing prices and quantities remain
    None until Python calculates them. transaction_id identifies an unchanged bank settlement, not a new
    cash posting. Sales require actual units and proceeds; their removed cost comes from posted history.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    operation: Literal["opening", "buy", "sell"]
    account_key: str = Field(min_length=1)
    security_key: str = Field(min_length=1)
    counterpart_key: str = Field(min_length=1)
    transaction_id: UUID | None = None
    units: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    amount: Decimal | None = Field(default=None, gt=0, decimal_places=2, allow_inf_nan=False)
    unit_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    fees: Decimal | None = Field(default=None, ge=0, decimal_places=2, allow_inf_nan=False)
    amount_includes_fees: bool = True
    realised_account_key: str | None = Field(
        default=None, min_length=1, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def _check_facts(self) -> Self:
        """
        Require units or an amount, and keep opening holdings separate from bank payments.
        """
        if self.units is None and self.amount is None and self.transaction_id is None:
            raise ValueError("Supply investment units or the total amount spent")
        if self.operation == "opening" and self.transaction_id is not None:
            raise ValueError("An opening holding cannot also consume an imported bank payment")
        if self.operation == "sell" and (self.units is None or self.realised_account_key is None):
            raise ValueError("A sale needs actual units and an explicitly selected realised-return account")
        return self


class InvestmentCalculation(BaseModel):
    """
    Explain units and recorded cost added by an acquisition or released by a sale.

    quote records reference pricing, never an execution price. units_estimated identifies quantities
    inferred from a market quote or from an amount with unspecified fees. The total cost includes fees once.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    units: Decimal = Field(gt=0, allow_inf_nan=False)
    total_cost: Decimal = Field(gt=0, decimal_places=2, allow_inf_nan=False)
    unit_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    fees: Decimal | None = Field(default=None, ge=0, decimal_places=2, allow_inf_nan=False)
    quote: PriceQuote | None = None
    units_estimated: bool = False
    proceeds: Decimal | None = Field(
        default=None, gt=0, decimal_places=2, allow_inf_nan=False, exclude_if=lambda value: value is None,
    )
    realised_gain: Decimal | None = Field(
        default=None, decimal_places=2, allow_inf_nan=False, exclude_if=lambda value: value is None,
    )
    cost_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$", exclude_if=lambda value: value is None,
    )


class AccountWithdrawalInstruction(BaseModel):
    """
    Retain explicit cash proceeds and a confirmed pre-withdrawal value for a balance-only investment.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    account_key: str = Field(min_length=1)
    counterpart_key: str = Field(min_length=1)
    realised_account_key: str = Field(min_length=1)
    unrealised_account_key: str = Field(min_length=1)
    transaction_id: UUID | None = None
    amount: Decimal = Field(gt=0, decimal_places=2, allow_inf_nan=False)
    confirmed_value: Decimal = Field(gt=0, decimal_places=2, allow_inf_nan=False)


class AccountWithdrawalCalculation(BaseModel):
    """
    Explain proportional principal release and the corresponding supported unrealised-gain transfer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    value_before: Decimal = Field(gt=0, decimal_places=2, allow_inf_nan=False)
    cost_before: Decimal = Field(gt=0, decimal_places=2, allow_inf_nan=False)
    proceeds: Decimal = Field(gt=0, decimal_places=2, allow_inf_nan=False)
    released_cost: Decimal = Field(gt=0, decimal_places=2, allow_inf_nan=False)
    realised_gain: Decimal = Field(decimal_places=2, allow_inf_nan=False)
    cost_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


class HoldingPosition(BaseModel):
    """
    Summarise journal-derived units and carrying cost for one account and security as of a date.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: UUID
    security_id: UUID
    date: date
    units: Decimal = Field(gt=0, allow_inf_nan=False)
    cost_basis: Decimal = Field(ge=0, decimal_places=2, allow_inf_nan=False)

    @property
    def average_cost(self) -> Decimal:
        """
        Return unrounded book cost per unit, without using a market price.
        """
        with localcontext() as context:
            context.prec = 50
            return self.cost_basis / self.units


# %% 2. Investment Calculations ------------------------------------------------------------------------------
def calculate_investment(
    instruction: InvestmentInstruction, *, quote: PriceQuote | None = None,
    settlement: Decimal | None = None,
) -> InvestmentCalculation:
    """
    Calculate positive units and whole-cent carrying cost from supplied facts and an optional quote.

    A settlement is the total positive AUD outflow from the original bank line. It must agree with any
    separately stated total. Explicit units, price and fees must reconcile; differences require a human
    correction rather than an invented rounding or fee line.

    Raises:
        ValueError: Facts conflict, a required price is absent or fees exhaust the amount invested.
    """
    if instruction.operation == "sell":
        raise ValueError("Use the recorded-cost sale builder for a disposal")
    fees = instruction.fees or Decimal("0")
    supplied_total = instruction.amount
    if supplied_total is not None and not instruction.amount_includes_fees:
        supplied_total += fees
    if settlement is not None and supplied_total is not None and settlement != supplied_total:
        raise ValueError("The stated total does not match the bank payment; clarify the amount and fees")
    total = settlement if settlement is not None else supplied_total
    reference = instruction.unit_price or (quote.price if quote is not None else None)
    units = instruction.units
    quote_used = None
    with localcontext() as context:
        context.prec = 50
        if units is None:
            if total is None or reference is None:
                raise ValueError("Supply an AUD unit price or units so this investment can be calculated")
            if total <= fees:
                raise ValueError("The total spent must exceed the included fees")
            units = (total - fees) / reference
            if instruction.unit_price is None:
                quote_used = quote
        elif total is None:
            if reference is None:
                raise ValueError("Supply an AUD unit price or total cost for these units")
            total = (units * reference + fees).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if instruction.unit_price is None:
                quote_used = quote
        if total <= fees:
            raise ValueError("The total spent must exceed the included fees")
        if instruction.units is not None and instruction.unit_price is not None:
            expected = (units * instruction.unit_price + fees).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
            if expected != total:
                raise ValueError("Units, execution price, fees and total disagree; clarify the trade figures")
    return InvestmentCalculation(
        units=units, total_cost=total,
        unit_price=instruction.unit_price if instruction.operation == "buy" else None,
        fees=instruction.fees, quote=quote_used,
        units_estimated=instruction.units is None and (
            instruction.unit_price is None or instruction.fees is None
        ),
    )


def investment_cash_available(
    entries: Iterable[JournalEntry], account_id: UUID, when: date, *, excluding: UUID | None = None,
) -> Decimal | None:
    """
    Recognise supported brokerage cash already received from a recorded sale, without calling it another gain.

    A cash account has no unit or whole-account investment movements. The caller additionally excludes its
    configured balance-only funds. Unknown account purpose and same-day ordering remain Review questions.
    """
    entries = tuple(entry for entry in entries if entry.id != excluding)
    if any(
        line.account_id == account_id and (line.investment is not None or line.account_investment is not None)
        for entry in entries for line in entry.lines
    ):
        return None
    if any(
        entry.date == when and any(line.account_id == account_id for line in entry.lines) for entry in entries
    ):
        return None
    prior = tuple(entry for entry in entries if entry.date < when)
    proved = any(
        any(line.account_id == account_id and line.debit > 0 for line in entry.lines)
        and any(line.investment is not None and line.investment.units < 0 for line in entry.lines)
        for entry in prior
    )
    if not proved:
        return None
    amount = sum((
        line.debit - line.credit for entry in prior for line in entry.lines if line.account_id == account_id
    ), Decimal("0"))
    return amount if amount > 0 else None


def is_fund_cost_deduction(entry: JournalEntry, accounts: AccountRegistry) -> bool:
    """
    Recognise a charge explicitly taken from invested capital and offset against expense or income.

    Existing journals can record the charge as a reduction of contribution income. A transfer to another
    asset always requires the normal withdrawal checks, regardless of its description.
    """
    if len(entry.lines) != 2:
        return False
    credit, debit = entry.lines if entry.lines[0].credit else reversed(entry.lines)
    if (credit.account_investment is None or credit.account_investment.kind != "contribution"
            or debit.transaction_id or debit.investment or debit.account_investment):
        return False
    by_id = {account.id: account for account in accounts.accounts.values()}
    fund = by_id[credit.account_id]
    return (fund.account_type == AccountType.ASSET and fund.bank_details is None
            and by_id[debit.account_id].account_type in {AccountType.EXPENSE, AccountType.INCOME})


def validate_investment_change(
    entry: JournalEntry, history: Iterable[JournalEntry], accounts: AccountRegistry,
    *, replacing: Iterable[JournalEntry] = (),
) -> None:
    """
    Recheck disposal economics and later dependent costs against the live ledger before any write.

    This validates affected investment accounts only. Existing history is never rewritten to accommodate
    an earlier edit, and the same-day and zero-cent boundaries remain explicit Review questions.
    """
    replacing = tuple(replacing)
    removed = {entry.id, *(old.id for old in replacing)}
    history = tuple(old for old in history if old.id not in removed)
    combined = (*history, entry)
    touched = {line.account_id for item in (*replacing, entry) for line in item.lines}
    investment_accounts = {
        line.account_id for item in combined for line in item.lines
        if line.investment is not None or line.account_investment is not None
    }
    affected = touched & investment_accounts
    for item in combined:
        for account_id in affected:
            security_sales = [
                line for line in item.lines
                if line.account_id == account_id and line.investment is not None and line.investment.units < 0
            ]
            principal_returns = [
                line for line in item.lines if line.account_id == account_id
                and line.account_investment is not None
                and line.account_investment.kind == "contribution" and line.credit > 0
            ]
            if security_sales:
                _validate_security_sale(item, combined, accounts, security_sales)
            if principal_returns and not is_fund_cost_deduction(item, accounts):
                _validate_account_withdrawal(item, combined, accounts, principal_returns)
    # A new acquisition or corrected event must not create negative units or leave cost without units.
    for cutoff in sorted({item.date for item in combined if any(
        line.account_id in affected and line.investment is not None for line in item.lines
    )}):
        holdings_as_of(combined, cutoff, account_ids=affected)


def investment_postings_match(first: JournalEntry, second: JournalEntry) -> bool:
    """
    Compare calculated economics while allowing ordinary descriptions and legacy reporting labels to survive.
    """
    excluded = {"description", "budget_category"}
    return first.date == second.date and [
        line.model_dump(exclude=excluded) for line in first.lines
    ] == [line.model_dump(exclude=excluded) for line in second.lines]


def _validate_security_sale(
    entry: JournalEntry, history: tuple[JournalEntry, ...], accounts: AccountRegistry,
    sales: list[JournalLine],
) -> None:
    """
    Require the exact average-cost credit and a single proceeds receipt plus the realised difference.
    """
    if len(sales) != 1:
        raise ValueError("Review multi-security disposals separately before posting")
    holding = sales[0]
    by_id = {account.id: account for account in accounts.accounts.values()}
    prior = _before_disposal(history, holding.account_id, entry.date, entry.id)
    if any(
        any(line.account_id == holding.account_id and line.investment is None for line in item.lines)
        and any(by_id[line.account_id].account_type == AccountType.EQUITY for line in item.lines)
        for item in prior
    ):
        raise ValueError("Review the holding's posted valuation before disposing of its units")
    position = next((
        item for item in holdings_as_of(prior, entry.date)
        if item.account_id == holding.account_id and item.security_id == holding.investment.security_id
    ), None)
    units = -holding.investment.units
    if position is None or units > position.units:
        raise ValueError("The disposal exceeds the recorded units available before its date")
    with localcontext() as context:
        context.prec = 50
        cost = position.cost_basis if units == position.units else (
            position.cost_basis * units / position.units
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if cost <= 0 or holding.credit != cost:
        raise ValueError("The disposal's recorded average cost changed; review the investment history")
    others = [line for line in entry.lines if line is not holding]
    receipts = [
        line for line in others if by_id[line.account_id].account_type == AccountType.ASSET
        and line.account_id != holding.account_id and line.debit > 0
        and line.investment is None and line.account_investment is None
    ]
    returns = [line for line in others if by_id[line.account_id].account_type == AccountType.INCOME]
    if len(receipts) != 1 or len(returns) > 1 or len(others) != len(receipts) + len(returns):
        raise ValueError("A sale needs one proceeds receipt and its separate realised investment return")
    gain = receipts[0].debit - cost
    if sum((line.credit - line.debit for line in returns), Decimal("0")) != gain:
        raise ValueError("Sale proceeds, released cost and realised return do not reconcile")
    details = holding.investment
    if details.unit_price is not None:
        with localcontext() as context:
            context.prec = 50
            net = (units * details.unit_price - (details.fees or Decimal("0"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
        if net != receipts[0].debit:
            raise ValueError("Sale execution figures differ from the proceeds receipt")


def _validate_account_withdrawal(
    entry: JournalEntry, history: tuple[JournalEntry, ...], accounts: AccountRegistry,
    principal: list[JournalLine],
) -> None:
    """
    Reconcile capital returned, carrying value and supported equity reclassification for a whole-account fund.
    """
    if len(principal) != 1:
        raise ValueError("Review combined investment withdrawals separately")
    capital = principal[0]
    by_id = {account.id: account for account in accounts.accounts.values()}
    receipts = [
        line for line in entry.lines if by_id[line.account_id].account_type == AccountType.ASSET
        and line.account_id != capital.account_id and line.debit > 0
        and line.account_investment is None and line.investment is None
    ]
    equity = [line for line in entry.lines if by_id[line.account_id].account_type == AccountType.EQUITY]
    returns = [line for line in entry.lines if by_id[line.account_id].account_type == AccountType.INCOME]
    value_lines = [
        line for line in entry.lines if line.account_id == capital.account_id and line is not capital
        and line.account_investment is not None and line.account_investment.kind == "earnings"
    ]
    if (
        len(receipts) != 1 or len(equity) > 1 or len(returns) > 1 or len(value_lines) > 1
        or len(entry.lines) != 1 + len(receipts) + len(equity) + len(returns) + len(value_lines)
    ):
        raise ValueError("A withdrawal needs separate proceeds, principal and supported return postings")
    prior = _before_disposal(history, capital.account_id, entry.date, entry.id)
    if equity:
        value, cost = _account_disposal_basis(prior, capital.account_id, equity[0].account_id)
    else:
        # Rounded zero gains omit the equity line; establish its history without inventing an allocation.
        candidates = [UUID(int=0), *(item.id for item in accounts.accounts.values()
                                    if item.account_type == AccountType.EQUITY)]
        bases = set()
        for identifier in candidates:
            try:
                bases.add(_account_disposal_basis(prior, capital.account_id, identifier))
            except ValueError:
                continue
        if len(bases) != 1:
            raise ValueError("Review the withdrawal's historical gain attribution")
        value, cost = next(iter(bases))
    proceeds = receipts[0].debit
    if proceeds > value:
        raise ValueError("The withdrawal exceeds its recorded pre-withdrawal value")
    with localcontext() as context:
        context.prec = 50
        expected = cost if proceeds == value else (
            cost * proceeds / value
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if expected <= 0 or capital.credit != expected:
        raise ValueError("The withdrawal's proportional recorded cost changed; review its valuation")
    gain = proceeds - expected
    value_change = sum((line.credit - line.debit for line in value_lines), Decimal("0"))
    realised = sum((line.credit - line.debit for line in returns), Decimal("0"))
    released = sum((line.debit - line.credit for line in equity), Decimal("0"))
    if (value_change, realised, released) != (gain, gain, gain):
        raise ValueError("Withdrawal value, realised return and unrealised equity do not reconcile")


def investment_cost_revision(
    entries: Iterable[JournalEntry], account_id: UUID, *, excluding: UUID | None = None,
) -> str:
    """
    Bind a calculation to all recorded movements of its asset, including later dependent disposals.
    """
    relevant = sorted((
        entry.model_dump(mode="json") for entry in entries
        if entry.id != excluding and any(line.account_id == account_id for line in entry.lines)
    ), key=lambda item: (item["date"], item["id"]))
    return sha256(json.dumps(relevant, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def build_security_sale(
    instruction: InvestmentInstruction, entries: Iterable[JournalEntry], accounts: AccountRegistry,
    securities: SecurityRegistry, when: date, realised_account_id: UUID,
    *, source: Transaction | None = None, entry_id: UUID | None = None,
) -> tuple[JournalEntry, InvestmentCalculation]:
    """
    Sell actual units at recorded average cost, retaining the full receipt and recognising only its gain.

    Same-day order, zero-cent cost and unattributed posted valuations require Review. Reference prices never
    establish disposal proceeds. The caller must recheck the returned cost revision inside its atomic save.
    """
    instruction = InvestmentInstruction.model_validate(instruction)
    if instruction.operation != "sell":
        raise ValueError("Use a sale instruction to dispose of security units")
    asset, counterpart, realised = _disposal_accounts(
        accounts, instruction.account_key, instruction.counterpart_key,
        instruction.realised_account_key, realised_account_id,
    )
    security = securities.securities.get(instruction.security_key)
    if security is None:
        raise ValueError("Choose the configured security being sold")
    entries = tuple(entries)
    prior = _before_disposal(entries, asset.id, when, entry_id)
    by_id = {account.id: account for account in accounts.accounts.values()}
    if any(
        any(line.account_id == asset.id and line.investment is None for line in entry.lines)
        and any(by_id[line.account_id].account_type == AccountType.EQUITY for line in entry.lines)
        for entry in prior
    ):
        raise ValueError(
            "Review the holding's posted valuation before selling; its gain attribution is unclear"
        )
    position = next((
        item for item in holdings_as_of(prior, when)
        if item.account_id == asset.id and item.security_id == security.id
    ), None)
    if position is None or instruction.units > position.units:
        raise ValueError("The sale exceeds the recorded units available before this date")
    with localcontext() as context:
        context.prec = 50
        released = position.cost_basis if instruction.units == position.units else (
            position.cost_basis * instruction.units / position.units
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if released <= 0:
        raise ValueError("Review this disposal: its recorded cost rounds to zero")
    proceeds = _sale_proceeds(instruction, source)
    receipt = _disposal_receipt(counterpart, source, instruction.transaction_id, proceeds)
    gain = proceeds - released
    lines = [JournalLine(
        account_id=asset.id, description=f"Sale of {security.name}", credit=released,
        investment=InvestmentDetails(
            security_id=security.id, units=-instruction.units,
            unit_price=instruction.unit_price, fees=instruction.fees,
        ),
    ), receipt]
    if gain:
        lines.append(_signed_line(realised.id, -gain, "Realised investment return"))
    calculation = InvestmentCalculation(
        units=instruction.units, total_cost=released, unit_price=instruction.unit_price,
        fees=instruction.fees, proceeds=proceeds, realised_gain=gain,
        cost_revision=investment_cost_revision(entries, asset.id, excluding=entry_id),
    )
    return JournalEntry(id=entry_id or uuid4(), date=when, lines=tuple(lines)), calculation


def build_account_withdrawal(
    instruction: AccountWithdrawalInstruction, entries: Iterable[JournalEntry], accounts: AccountRegistry,
    when: date, realised_account_id: UUID, unrealised_account_id: UUID,
    *, source: Transaction | None = None, entry_id: UUID | None = None,
) -> tuple[JournalEntry, AccountWithdrawalCalculation]:
    """
    Release confirmed balance-only principal proportionally and realise only supported recorded gains.

    Separate principal and value postings preserve both the remaining asset and historical cost using the
    existing contribution and earnings metadata. No valuation or opening-equity attribution is invented.
    """
    instruction = AccountWithdrawalInstruction.model_validate(instruction)
    asset, counterpart, realised = _disposal_accounts(
        accounts, instruction.account_key, instruction.counterpart_key,
        instruction.realised_account_key, realised_account_id,
    )
    unrealised = accounts.accounts.get(instruction.unrealised_account_key)
    if unrealised is None or unrealised.id != unrealised_account_id or (
        unrealised.account_type != AccountType.EQUITY
    ):
        raise ValueError("Choose the configured unrealised-gains equity account")
    entries = tuple(entries)
    prior = _before_disposal(entries, asset.id, when, entry_id)
    value, cost = _account_disposal_basis(prior, asset.id, unrealised.id)
    if instruction.confirmed_value != value:
        raise ValueError(
            "The confirmed pre-withdrawal value differs from the recorded value; review valuation"
        )
    if instruction.amount > value:
        raise ValueError("The withdrawal exceeds the confirmed investment value")
    with localcontext() as context:
        context.prec = 50
        released = cost if instruction.amount == value else (
            cost * instruction.amount / value
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if released <= 0:
        raise ValueError("Review this withdrawal: its proportional recorded cost rounds to zero")
    gain = instruction.amount - released
    lines = [
        _disposal_receipt(counterpart, source, instruction.transaction_id, instruction.amount),
        JournalLine(
            account_id=asset.id, description="Investment principal returned", credit=released,
            account_investment=AccountInvestmentDetails(kind="contribution"),
        ),
    ]
    if gain:
        lines.extend((
            _signed_line(
                asset.id, -gain, "Investment value withdrawn",
                account_investment=AccountInvestmentDetails(kind="earnings"),
            ),
            _signed_line(unrealised.id, gain, "Unrealised return released"),
            _signed_line(realised.id, -gain, "Realised investment return"),
        ))
    calculation = AccountWithdrawalCalculation(
        value_before=value, cost_before=cost, proceeds=instruction.amount, released_cost=released,
        realised_gain=gain, cost_revision=investment_cost_revision(entries, asset.id, excluding=entry_id),
    )
    return JournalEntry(id=entry_id or uuid4(), date=when, lines=tuple(lines)), calculation


def _before_disposal(
    entries: tuple[JournalEntry, ...], account_id: UUID, when: date, excluding: UUID | None,
) -> tuple[JournalEntry, ...]:
    """
    Require an unambiguous prior-day position instead of assigning an order to same-day trades.
    """
    remaining = tuple(entry for entry in entries if entry.id != excluding)
    if any(
        entry.date == when and any(line.account_id == account_id for line in entry.lines)
        for entry in remaining
    ):
        raise ValueError("Review the same-day investment movements; their order is not established")
    return tuple(entry for entry in remaining if entry.date < when)


def _disposal_accounts(
    accounts: AccountRegistry, asset_key: str, counterpart_key: str,
    realised_key: str | None, realised_id: UUID,
) -> tuple:
    """
    Require separate existing asset accounts and an explicitly chosen income account for realised returns.
    """
    asset = accounts.accounts.get(asset_key)
    counterpart = accounts.accounts.get(counterpart_key)
    realised = accounts.accounts.get(realised_key)
    if asset is None or asset.account_type != AccountType.ASSET or asset.bank_details is not None:
        raise ValueError("Choose a non-bank investment asset")
    if counterpart is None or counterpart.account_type != AccountType.ASSET or counterpart.id == asset.id:
        raise ValueError("Choose the separate asset account receiving the investment proceeds")
    if realised is None or realised.id != realised_id or realised.account_type != AccountType.INCOME:
        raise ValueError("Choose the income account for realised investment returns")
    return asset, counterpart, realised


def _disposal_receipt(
    account, source: Transaction | None, source_id: UUID | None, amount: Decimal,
) -> JournalLine:
    """
    Preserve a supplied receipt exactly and require evidence for any bank counterpart.
    """
    if source is not None:
        if source.transaction_id != source_id or source.account_id != account.id or source.amount != amount:
            raise ValueError("The investment receipt must match its full original bank source")
        if source.amount <= 0:
            raise ValueError("An investment disposal requires an incoming receipt")
        return JournalLine(
            account_id=account.id, transaction_id=source.transaction_id,
            description=source.description, effective_date=source.effective_date, debit=amount,
        )
    if source_id is not None or account.bank_details is not None:
        raise ValueError("Link the original incoming bank transaction before posting investment proceeds")
    return JournalLine(account_id=account.id, description="Investment proceeds received", debit=amount)


def _sale_proceeds(instruction: InvestmentInstruction, source: Transaction | None) -> Decimal:
    """
    Reconcile actual net sale proceeds and supplied fees without estimating any disposal facts.
    """
    fees = instruction.fees or Decimal("0")
    supplied = instruction.amount
    if supplied is not None and not instruction.amount_includes_fees:
        if instruction.fees is None:
            raise ValueError("Supply the sale fee to convert gross proceeds to the net receipt")
        supplied -= fees
    proceeds = source.amount if source is not None else supplied
    if proceeds is None or proceeds <= 0:
        raise ValueError("Supply the actual net sale proceeds")
    if source is not None and supplied is not None and proceeds != supplied:
        raise ValueError("The supplied sale proceeds differ from the bank receipt")
    if instruction.unit_price is not None:
        with localcontext() as context:
            context.prec = 50
            expected = (instruction.units * instruction.unit_price - fees).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP,
            )
        if expected != proceeds:
            raise ValueError("Sale units, execution price, fees and proceeds do not reconcile")
    return proceeds


def _account_disposal_basis(
    entries: tuple[JournalEntry, ...], account_id: UUID, unrealised_id: UUID,
) -> tuple[Decimal, Decimal]:
    """
    Require known invested principal and equity attribution for every remaining gain or loss.
    """
    value = Decimal("0")
    cost = Decimal("0")
    for entry in entries:
        fund_lines = [line for line in entry.lines if line.account_id == account_id]
        if not fund_lines:
            continue
        entry_gain = Decimal("0")
        for line in fund_lines:
            details = line.account_investment
            if line.investment is not None or details is None or details.kind == "unclassified":
                raise ValueError("Review the investment's historical cost and withdrawal classification")
            movement = line.debit - line.credit
            value += movement
            if details.kind == "contribution":
                cost += movement
            elif details.kind == "opening":
                cost += details.cost_basis
                entry_gain += movement - details.cost_basis
            else:
                entry_gain += movement
        equity_gain = sum(
            (line.credit - line.debit for line in entry.lines if line.account_id == unrealised_id),
            Decimal("0"),
        )
        if entry_gain != equity_gain:
            raise ValueError(
                "Review whether this fund's return was already realised or belongs to opening equity"
            )
    if value <= 0 or cost <= 0:
        raise ValueError("A withdrawal needs positive confirmed value and recorded cost")
    return value, cost


def _signed_line(account_id: UUID, movement: Decimal, description: str, **metadata) -> JournalLine:
    """
    Express one nonzero signed debit movement as a positive debit or credit.
    """
    return JournalLine(
        account_id=account_id, description=description,
        debit=max(movement, Decimal("0")), credit=max(-movement, Decimal("0")), **metadata,
    )


def holdings_as_of(
    entries: Iterable[JournalEntry], as_of: date, *, account_ids: set[UUID] | None = None,
) -> tuple[HoldingPosition, ...]:
    """
    Reconstruct units and cost from investment postings dated on or before as_of.

    Entries must already be posted and validated. Cash lines without investment details are excluded, even
    when they use the same asset account. Returned holdings contain no market-value adjustments.

    Raises:
        ValueError: The supplied history produces negative holdings or leaves cost without units.
    """
    totals: dict[tuple[UUID, UUID], tuple[Decimal, Decimal]] = {}
    with localcontext() as context:
        context.prec = 50
        for entry in entries:
            if entry.date > as_of:
                continue
            for line in entry.lines:
                if line.investment is None or account_ids is not None and line.account_id not in account_ids:
                    continue
                key = (line.account_id, line.investment.security_id)
                units, cost = totals.get(key, (Decimal("0"), Decimal("0")))
                totals[key] = (
                    units + line.investment.units, cost + line.debit - line.credit,
                )
    positions = []
    for (account_id, security_id), (units, cost) in sorted(totals.items(), key=lambda item: str(item[0])):
        if units < 0 or cost < 0 or (units == 0 and cost != 0):
            raise ValueError("Investment history produces inconsistent units or carrying cost")
        if units:
            positions.append(HoldingPosition(
                account_id=account_id, security_id=security_id, date=as_of, units=units, cost_basis=cost,
            ))
    return tuple(positions)


# %% 3. Market Prices ----------------------------------------------------------------------------------------
class PriceUnavailable(ValueError):
    """
    Explain why automatic pricing cannot safely complete an investment instruction.
    """


def price_symbol(security: Security) -> str:
    """
    Resolve an explicit provider symbol or the default for a supported market.
    """
    if security.price_symbol is not None:
        return security.price_symbol.strip()
    if security.security_type == SecurityType.CRYPTO:
        return security.symbol.strip().upper() + "-AUD"
    if security.market.strip().upper() == "ASX":
        symbol = security.symbol.strip().upper()
        return symbol if symbol.endswith(".AX") else symbol + ".AX"
    if security.market.strip().upper() in {"US", "USA", "NASDAQ", "NYSE", "NYSE ARCA", "NYSEARCA",
                                           "AMEX", "NYSE AMERICAN", "CBOE BZX", "BATS"}:
        return security.symbol.strip().upper()
    raise PriceUnavailable("Configure the Yahoo market data symbol or supply the actual AUD unit price")


def supports_quote_currency(kind: SecurityType, currency: str | None) -> bool:
    """
    Accept direct AUD pricing and USD conversions for shares and exchange-traded funds.
    """
    return currency == "AUD" or (kind == SecurityType.SHARE and currency == "USD")


def usd_exchange_rates(start: date, end: date, now: datetime) -> dict[date, Decimal]:
    """
    Fetch completed Yahoo USD/AUD daily rates for the inclusive security close dates.

    A missing, conflicting or unfinished daily rate leaves that date unavailable for conversion.
    """
    import yfinance as yf

    try:
        ticker = yf.Ticker("USDAUD=X")
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=r"The inplace keyword in DataFrame\..* is deprecated.*",
                module=r"yfinance\..*",
            )
            history = ticker.history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                     interval="1d", auto_adjust=False, back_adjust=False, rounding=False,
                                     timeout=15, raise_errors=True)
        metadata = ticker.get_history_metadata()
        # Yahoo can report the equivalent short USD-base identifier in its history metadata.
        pair = str(metadata.get("symbol", "")).upper()
        if metadata.get("currency") != "AUD" or pair not in {"USDAUD=X", "AUD=X"}:
            raise PriceUnavailable("Yahoo did not confirm the USD/AUD exchange rate.")
        rates, invalid = {}, set()
        for stamp, row in history.iterrows():
            day = stamp.date()
            if not start <= day <= end or not completed_yahoo_bar(stamp, SecurityType.SHARE, metadata, now):
                continue
            try:
                rate = Decimal(str(row["Close"]))
            except (KeyError, InvalidOperation):
                invalid.add(day)
                continue
            if not rate.is_finite() or rate <= 0 or (day in rates and rate != rates[day]):
                invalid.add(day)
            else:
                rates[day] = rate
        return {day: rate for day, rate in rates.items() if day not in invalid}
    except PriceUnavailable:
        raise
    except Exception:
        raise PriceUnavailable("The historical USD/AUD exchange rate could not be retrieved.") from None


def get_quote(
    connection: sqlite3.Connection, security: Security, requested_date: date, *,
    fetcher: Callable[[Security, date], PriceQuote] | None = None,
) -> PriceQuote:
    """
    Reuse the exact cached close or fetch, validate and persist it before accounting is saved.

    fetcher allows small synthetic checks without a network request.
    """
    # Cache storage also uses PriceQuote, so defer its import until the models are available.
    from aurum.database.prices import load_quote, save_quote

    symbol = price_symbol(security)
    cached = load_quote(connection, security.id, requested_date, symbol)
    if cached is not None:
        return cached
    quote = (fetcher or fetch_yahoo_quote)(security, requested_date)
    if (
        quote.security_id != security.id or quote.requested_date != requested_date
        or quote.symbol != symbol
    ):
        raise PriceUnavailable("The returned quote does not match the requested security and date")
    return save_quote(connection, quote)


def fetch_yahoo_quote(security: Security, requested_date: date) -> PriceQuote:
    """
    Fetch the most recent completed AUD daily close within seven days of requested_date.

    Daily dates follow the provider's instrument timezone. Current crypto bars close at the next UTC day;
    current share bars are used only after the exchange's reported regular session ends. Raw daily Close
    is requested with automatic price adjustments disabled.
    """
    symbol = price_symbol(security)
    if requested_date > date.today():
        raise PriceUnavailable("A future market close is unavailable; supply an AUD unit price")
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore", message=r"The inplace keyword in DataFrame\..* is deprecated.*",
                module=r"yfinance\..*",
            )
            history = ticker.history(
                start=(requested_date - timedelta(days=7)).isoformat(),
                end=(requested_date + timedelta(days=1)).isoformat(),
                interval="1d", auto_adjust=False, back_adjust=False, rounding=False,
                timeout=15, raise_errors=True,
            )
            metadata = ticker.get_history_metadata()
            currency = metadata.get("currency")
            if not supports_quote_currency(security.security_type, currency):
                raise PriceUnavailable("Unsupported quote currency; supply the actual AUD unit price")
            if currency == "USD" and str(metadata.get("symbol", "")).casefold() != symbol.casefold():
                raise PriceUnavailable("Yahoo did not confirm the requested USD security.")
            if security.security_type == SecurityType.SHARE:
                splits = ticker.get_splits()
                if any(timestamp.date() > requested_date for timestamp in splits.index):
                    raise PriceUnavailable(
                        "A later share split makes historical units ambiguous; supply an AUD unit price"
                    )
        now = datetime.now(UTC)
        rates = None
        for timestamp, row in history.iloc[::-1].iterrows():
            if timestamp.tzinfo is None:
                raise PriceUnavailable("The provider omitted the quote timezone; supply an AUD unit price")
            price_date = timestamp.date()
            if not 0 <= (requested_date - price_date).days <= 7:
                continue
            if not completed_yahoo_bar(timestamp, security.security_type, metadata, now):
                continue
            price = Decimal(str(row["Close"]))
            if price.is_finite() and price > 0:
                conversion = None
                if currency == "USD":
                    if rates is None:
                        rates = usd_exchange_rates(requested_date - timedelta(days=7), price_date, now)
                    if price_date not in rates:
                        continue
                    conversion = DollarConversion(usd_close=price, aud_per_usd=rates[price_date])
                    price = conversion.aud_price()
                return PriceQuote(
                    security_id=security.id, requested_date=requested_date,
                    price_date=price_date, price=price, symbol=symbol, conversion=conversion,
                )
    except PriceUnavailable:
        raise
    except Exception:
        raise PriceUnavailable(
            "The market close could not be retrieved; enter an AUD unit price in the Manual response "
            "or use Process to retry"
        ) from None
    if rates is not None:
        raise PriceUnavailable("No matching completed USD/AUD rate; supply an AUD unit price or retry")
    raise PriceUnavailable(
        "No completed close is available within seven days; "
        "enter an AUD unit price in the Manual response"
    )


def completed_yahoo_bar(timestamp: datetime, kind: SecurityType, metadata: dict, now: datetime) -> bool:
    """
    Accept a timezone-aware daily bar only after its exchange session or full UTC crypto day has ended.
    """
    if timestamp.utcoffset() is None:
        raise PriceUnavailable("The provider omitted the quote timezone.")
    if kind == SecurityType.CRYPTO:
        return now >= timestamp.astimezone(UTC) + timedelta(days=1)
    today = now.astimezone(timestamp.tzinfo).date()
    if timestamp.date() != today:
        return timestamp.date() < today
    close = metadata.get("currentTradingPeriod", {}).get("regular", {}).get("end")
    if isinstance(close, datetime):
        if close.utcoffset() is None:
            return False
        session_end = close.astimezone(timestamp.tzinfo)
    elif isinstance(close, (int, float)):
        try:
            session_end = datetime.fromtimestamp(close, timestamp.tzinfo)
        except (OSError, OverflowError, ValueError):
            return False
    else:
        return False
    return session_end.date() == today and now >= session_end
