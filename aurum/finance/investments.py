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
from decimal import Decimal, ROUND_HALF_UP, localcontext
import sqlite3
from typing import Literal, Self
from uuid import UUID
import warnings

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Aurum imports
from aurum.finance.journal import JournalEntry
from aurum.finance.securities import Security, SecurityType


# %% 1. Investment Details -----------------------------------------------------------------------------------
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

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        """
        Require a recent earlier close and an aware retrieval timestamp.
        """
        if not 0 <= (self.requested_date - self.price_date).days <= 7:
            raise ValueError("A quote must be on the requested date or within the previous seven days")
        if self.fetched_at.utcoffset() is None:
            raise ValueError("Quote retrieval timestamps must include a timezone")
        return self


class InvestmentInstruction(BaseModel):
    """
    Preserve the human-supplied investment facts after AI resolves account and security references.

    amount means total spent unless amount_includes_fees is False. Missing prices and quantities remain
    None until Python calculates them. transaction_id identifies an unchanged bank settlement, not a new
    cash posting. Only opening holdings and purchases are supported in this version.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    operation: Literal["opening", "buy"]
    account_key: str = Field(min_length=1)
    security_key: str = Field(min_length=1)
    counterpart_key: str = Field(min_length=1)
    transaction_id: UUID | None = None
    units: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    amount: Decimal | None = Field(default=None, gt=0, decimal_places=2, allow_inf_nan=False)
    unit_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)
    fees: Decimal | None = Field(default=None, ge=0, decimal_places=2, allow_inf_nan=False)
    amount_includes_fees: bool = True

    @model_validator(mode="after")
    def _check_facts(self) -> Self:
        """
        Require units or an amount, and keep opening holdings separate from bank payments.
        """
        if self.units is None and self.amount is None and self.transaction_id is None:
            raise ValueError("Supply investment units or the total amount spent")
        if self.operation == "opening" and self.transaction_id is not None:
            raise ValueError("An opening holding cannot also consume an imported bank payment")
        return self


class InvestmentCalculation(BaseModel):
    """
    Explain the units and carrying cost posted for one opening holding or purchase.

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


def holdings_as_of(entries: Iterable[JournalEntry], as_of: date) -> tuple[HoldingPosition, ...]:
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
                if line.investment is None:
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
    Resolve an explicit provider symbol, an AUD crypto pair or an ASX ticker.

    Other exchanges require an explicit price_symbol whose returned currency is verified as AUD.
    """
    if security.price_symbol is not None:
        return security.price_symbol.strip()
    if security.security_type == SecurityType.CRYPTO:
        return security.symbol.strip().upper() + "-AUD"
    if security.market.strip().upper() == "ASX":
        symbol = security.symbol.strip().upper()
        return symbol if symbol.endswith(".AX") else symbol + ".AX"
    raise PriceUnavailable("Configure an AUD price_symbol or supply the actual AUD unit price")


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
            if metadata.get("currency") != "AUD":
                raise PriceUnavailable("This provider symbol is not quoted in AUD; supply an AUD unit price")
            if security.security_type == SecurityType.SHARE:
                splits = ticker.get_splits()
                if any(timestamp.date() > requested_date for timestamp in splits.index):
                    raise PriceUnavailable(
                        "A later share split makes historical units ambiguous; supply an AUD unit price"
                    )
        now = datetime.now(UTC)
        for timestamp, row in history.iloc[::-1].iterrows():
            if timestamp.tzinfo is None:
                raise PriceUnavailable("The provider omitted the quote timezone; supply an AUD unit price")
            price_date = timestamp.date()
            if not 0 <= (requested_date - price_date).days <= 7:
                continue
            if security.security_type == SecurityType.CRYPTO:
                if now < timestamp.to_pydatetime().astimezone(UTC) + timedelta(days=1):
                    continue
            elif price_date >= now.astimezone(timestamp.tzinfo).date():
                close = metadata.get("currentTradingPeriod", {}).get("regular", {}).get("end")
                if close is None or now.timestamp() < close:
                    continue
            price = Decimal(str(row["Close"]))
            if price.is_finite() and price > 0:
                return PriceQuote(
                    security_id=security.id, requested_date=requested_date,
                    price_date=price_date, price=price, symbol=symbol,
                )
    except PriceUnavailable:
        raise
    except Exception:
        raise PriceUnavailable(
            "The market close could not be retrieved; supply an AUD unit price in human_response "
            "or retry with --retry-errors"
        ) from None
    raise PriceUnavailable(
        "No completed AUD close is available within seven days; supply an AUD unit price in human_response"
    )
