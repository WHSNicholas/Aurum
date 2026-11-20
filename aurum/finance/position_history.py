# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                              Finance - Position History                                              #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Calculate historical financial position from posted accounting and the market closes eligible on each date.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from datetime import date
from decimal import Decimal, localcontext
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Aurum imports
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.dates import reporting_month
from aurum.finance.investments import PriceQuote, price_symbol
from aurum.finance.journal import JournalEntry
from aurum.finance.reports import (
    ReportSettings, _ambiguous_valuations, _cents, _money, _validate_entries, _validate_settings,
    apply_equity_earnings, holding_market_value,
)
from aurum.finance.securities import SecurityRegistry


# %% 1. Position History -------------------------------------------------------------------------------------
class HoldingClose(BaseModel):
    """
    Identify the saved market close that contributed to one holding's share of its account value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    price_date: date


class PositionPoint(BaseModel):
    """
    Keep assets, liabilities and the resulting net worth for one inclusive valuation cutoff.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    assets: Decimal
    liabilities: Decimal
    net_worth: Decimal
    provisional_holdings: tuple[str, ...]
    account_balances: tuple[Decimal, ...]
    account_provisional: tuple[tuple[str, ...], ...]
    account_price_dates: tuple[tuple[HoldingClose, ...], ...]

    @property
    def price_dates(self) -> tuple[HoldingClose, ...]:
        """
        Collect the contributing close dates for all accounts represented by the aggregate position.
        """
        return tuple(dict.fromkeys(close for account in self.account_price_dates for close in account))


def position_history(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, securities: SecurityRegistry,
    settings: ReportSettings, quotes: Iterable[PriceQuote], cutoffs: Iterable[date], *,
    cancelled: Callable[[], bool] = lambda: False,
) -> dict[date, PositionPoint]:
    """
    Walk postings and saved prices once, returning a balance-sheet valuation at each requested date.

    A quote first becomes eligible on its original requested date. The best eligible close is kept until
    superseded. Apply all events on a day before valuing open positions, including same-day disposals.
    Cancellation raises InterruptedError and never returns a partial series.
    """
    days = set(cutoffs)
    if not days:
        return {}
    last_day = max(days)
    _validate_settings(settings, accounts)
    postings = defaultdict(list)
    for entry in _validate_entries(_through_cutoff(entries, last_day, cancelled), accounts):
        postings[entry.date].append(entry)

    # Check only prices for the currently configured provider symbols. Discontinued symbols remain in cache.
    symbols = {}
    for security in securities.securities.values():
        try:
            symbols[security.id] = price_symbol(security)
        except ValueError:
            continue
    arrivals = defaultdict(list)
    for quote in quotes:
        if cancelled():
            raise InterruptedError("Historical valuation was stopped.")
        if (quote.requested_date <= last_day and quote.price_date <= quote.requested_date
                and quote.symbol == symbols.get(quote.security_id)):
            arrivals[quote.requested_date].append(quote)

    ledger = _PositionLedger(accounts, settings)
    latest = {}
    result = {}
    point = None
    with localcontext() as context:
        context.prec = 50
        for day in sorted(days | postings.keys() | arrivals.keys()):
            if cancelled():
                raise InterruptedError("Historical valuation was stopped.")
            if ledger.advance_year(reporting_month(day)[0]):
                point = None
            for entry in postings.get(day, ()):
                ledger.post(entry)
                point = None
            for quote in arrivals.get(day, ()):
                previous = latest.get(quote.security_id)
                if previous is None or (quote.price_date, day) > (
                    previous.price_date, previous.requested_date
                ):
                    latest[quote.security_id] = quote
                    if quote.security_id in ledger.price_sensitive_ids:
                        point = None
            if day in days:
                if point is None:
                    point = ledger.value(securities, latest)
                result[day] = point
    return result


# %% 2. Helpers ----------------------------------------------------------------------------------------------
class _PositionLedger:
    """
    Accumulate signed book balances and per-account security units and cost across successive dates.
    """

    def __init__(self, accounts: AccountRegistry, settings: ReportSettings) -> None:
        """
        Resolve account roles once and start all balances at zero as in the posted journal.
        """
        definitions = accounts.accounts.values()
        self.book = {
            account.id: 0 for account in definitions
            if account.account_type in {AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY}
        }
        self.asset_ids = {a.id for a in definitions if a.account_type == AccountType.ASSET}
        self.liability_ids = {a.id for a in definitions if a.account_type == AccountType.LIABILITY}
        self.capital = accounts.accounts[settings.capital_account].id
        self.retained = accounts.accounts[settings.retained_earnings_account].id
        self.year = None
        self.earlier_profit = self.current_profit = self.earlier_retained = 0
        self.account_investments = {accounts.accounts[key].id: key for key in settings.account_investments}
        self.gains_id = accounts.accounts[settings.unrealised_gains_account].id
        self.ambiguous = set()
        self.price_sensitive_ids = set()
        self.positions: dict[tuple[UUID, UUID], tuple[Decimal, int]] = {}

    def advance_year(self, year: int) -> bool:
        """
        Roll earnings at the January reporting boundary even when there are no postings that day.
        """
        if self.year == year:
            return False
        self.year = year
        self.earlier_profit += self.current_profit
        self.current_profit = 0
        self.earlier_retained = -self.book[self.retained]
        return True

    def post(self, entry: JournalEntry) -> None:
        """
        Apply one complete event, retaining ambiguous manual valuation history even before a holding opens.
        """
        self.ambiguous.update(_ambiguous_valuations((entry,), self.asset_ids, self.gains_id))
        for line in entry.lines:
            movement = _cents(line.debit) - _cents(line.credit)
            if line.account_id in self.book:
                self.book[line.account_id] += movement
            else:
                self.current_profit -= movement
            if line.investment is not None:
                key = (line.account_id, line.investment.security_id)
                units, cost = self.positions.get(key, (Decimal(0), 0))
                units += line.investment.units
                cost += movement
                if units or cost:
                    self.positions[key] = (units, cost)
                else:
                    self.positions.pop(key, None)

    def value(self, securities: SecurityRegistry, quotes: Mapping[UUID, PriceQuote]) -> PositionPoint:
        """
        Replace only each priced holding's carrying cost, preserving unrelated cash in the same account.
        """
        balances = self.book.copy()
        apply_equity_earnings(balances, self.capital, self.retained, self.earlier_profit,
                              self.current_profit, self.earlier_retained)
        provisional = defaultdict(set)
        close_dates = defaultdict(list)
        self.price_sensitive_ids.clear()
        for (account_id, security_id), (units, cost) in self.positions.items():
            if units <= 0 or cost < 0:
                raise ValueError("Investment history produces inconsistent units or carrying cost")
            if account_id in self.account_investments:
                key = self.account_investments[account_id]
                raise ValueError(
                    f"Account {key!r} has security holdings; remove its whole-account investment setting"
                )
            try:
                security = securities.by_id(security_id)
            except KeyError:
                message = "Restore securities referenced by posted holdings before reporting"
                raise ValueError(message) from None
            if account_id in self.ambiguous:
                provisional[account_id].add(security.name)
                continue
            self.price_sensitive_ids.add(security_id)
            quote = quotes.get(security_id)
            if quote is None:
                provisional[account_id].add(security.name)
            else:
                adjustment = _cents(holding_market_value(units, quote.price)) - cost
                balances[account_id] += adjustment
                balances[self.gains_id] -= adjustment
                close_dates[account_id].append(HoldingClose(name=security.name, price_date=quote.price_date))
        assets = sum(balances[key] for key in self.asset_ids)
        liabilities = -sum(balances[key] for key in self.liability_ids)
        missing = tuple(sorted({name for names in provisional.values() for name in names}))
        provisional[self.gains_id] = missing
        all_closes = dict.fromkeys(close for dates in close_dates.values() for close in dates)
        close_dates[self.gains_id] = tuple(all_closes)
        return PositionPoint(
            assets=_money(assets), liabilities=_money(liabilities),
            net_worth=_money(assets - liabilities),
            provisional_holdings=missing,
            account_balances=tuple(_money(cents if key in self.asset_ids else -cents)
                                   for key, cents in balances.items()),
            account_provisional=tuple(tuple(sorted(provisional.get(key, ()))) for key in balances),
            account_price_dates=tuple(tuple(sorted(close_dates.get(key, ()), key=lambda item: item.name))
                                    for key in balances),
        )


def _through_cutoff(
    entries: Iterable[JournalEntry], cutoff: date, cancelled: Callable[[], bool],
) -> Iterable[JournalEntry]:
    """
    Supply only eligible posted events and honour cancellation while validating a long history.
    """
    for entry in entries:
        if cancelled():
            raise InterruptedError("Historical valuation was stopped.")
        if entry.date <= cutoff:
            yield entry
