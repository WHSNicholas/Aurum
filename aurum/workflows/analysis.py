# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                 Workflows - Analysis                                                 #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Prepare daily balance-sheet accounts and reporting-month cash flows from one posted ledger and its saved prices.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Aurum imports
from aurum.finance.accounts import AccountRegistry, AccountType
from aurum.finance.budget import BudgetProjection, BudgetSettings, _cents, _money, project_budget_entry
from aurum.finance.dates import reporting_month, working_month_end, working_month_start
from aurum.finance.investments import PriceQuote
from aurum.finance.journal import JournalEntry
from aurum.finance.position_history import PositionPoint, position_history
from aurum.finance.reports import ReportPeriod, ReportSettings
from aurum.finance.securities import SecurityRegistry


# %% 1. Analysis History -------------------------------------------------------------------------------------
class BalanceAccount(BaseModel):
    """
    Keep an account's stable identity and display name once for the complete series.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    key: str
    name: str


class CategorySpend(BaseModel):
    """
    Identify one signed expense by its stable budget category or unbudgeted account key.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    amount: Decimal


class AnalysisPoint(PositionPoint):
    """
    Pair the closing position with cash flows during the inclusive observation period.
    """

    start_date: date
    end_date: date
    month: str
    partial: bool
    income: Decimal
    expenses: Decimal
    savings: Decimal
    categories: tuple[CategorySpend, ...]
    budget_issues: tuple[str, ...]


class AnalysisHistory(BaseModel):
    """
    Store daily positions from the first posting and signed cash flows for each reporting month.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    month: str
    cutoff: date
    first_posted: date | None = None
    balance_accounts: tuple[BalanceAccount, ...] = ()
    asset_groups: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    daily: tuple[PositionPoint, ...] = ()
    monthly: tuple[AnalysisPoint, ...] = ()

    def cash_months(self, horizon: str) -> tuple[AnalysisPoint, ...]:
        """
        Select whole reporting-month observations independently of the daily chart's point selection.
        """
        if horizon == "All time":
            return self.monthly
        counts = {"4W": 1, "12W": 3, "6M": 6, "1Y": 12}
        if horizon not in counts:
            raise ValueError(f"Unknown analysis range: {horizon}")
        return self.monthly[-counts[horizon]:]

    def daily_bounds(self, horizon: str) -> tuple[date, date] | None:
        """
        Return inclusive daily bounds, using reporting-month boundaries for the longer ranges.
        """
        months = self.cash_months(horizon)
        if self.first_posted is None or not months:
            return None
        if horizon in {"4W", "12W"}:
            count = 28 if horizon == "4W" else 84
            start = max(self.first_posted.toordinal(), self.cutoff.toordinal() - count + 1)
            return date.fromordinal(start), self.cutoff
        return months[0].start_date, self.cutoff


def build_analysis_history(
    entries: Iterable[JournalEntry], accounts: AccountRegistry, securities: SecurityRegistry,
    settings: ReportSettings, budget: BudgetSettings, period: ReportPeriod, *,
    quotes: Iterable[PriceQuote] = (), opening_entry_ids: Iterable[UUID] = (),
    cancelled: Callable[[], bool] = lambda: False,
) -> AnalysisHistory:
    """
    Project each eligible event into its reporting month and value the daily closing positions once.

    Daily positions share their immutable data across quiet days. Monthly cash-flow points reuse those
    month-end valuations, including the selected month's cutoff when the period is still in progress.
    """
    cutoff = period.cutoff
    by_month = defaultdict(_CashFlow)
    openings = set(opening_entry_ids)
    posted = []
    balance_accounts = tuple(
        BalanceAccount(id=account.id, key=account.key, name=account.name)
        for account in accounts.accounts.values()
        if account.account_type in {AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY}
    )
    if cancelled():
        raise InterruptedError("History loading stopped.")
    for entry in entries:
        if cancelled():
            raise InterruptedError("History loading stopped.")
        if entry.date > cutoff:
            continue
        posted.append(entry)
        projection = project_budget_entry(entry, accounts, budget, opening=entry.id in openings)
        by_month[reporting_month(entry.date)].add(projection)
    if not posted:
        return AnalysisHistory(month=period.month, cutoff=cutoff, balance_accounts=balance_accounts,
                               asset_groups=dict(settings.asset_groups))

    first = min(entry.date for entry in posted)
    dates = (date.fromordinal(ordinal) for ordinal in range(first.toordinal(), cutoff.toordinal() + 1))
    positions = position_history(posted, accounts, securities, settings, quotes, dates, cancelled=cancelled)
    monthly = []
    year, month = reporting_month(first)
    final_month = reporting_month(cutoff)
    while (year, month) <= final_month:
        if cancelled():
            raise InterruptedError("History loading stopped.")
        start, end = working_month_start(year, month), working_month_end(year, month)
        end_date = min(end, cutoff)
        monthly.append(by_month[(year, month)].point(
            positions[end_date], max(start, first), end_date, f"{year:04d}-{month:02d}",
            partial=first > start or cutoff < end,
        ))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return AnalysisHistory(
        month=period.month, cutoff=cutoff, first_posted=first, balance_accounts=balance_accounts,
        asset_groups=dict(settings.asset_groups), daily=tuple(positions.values()), monthly=tuple(monthly),
    )


# %% 2. Helpers ----------------------------------------------------------------------------------------------
@dataclass
class _CashFlow:
    """
    Accumulate signed projection cents and keep the names of unresolved cash movements available.
    """

    income: int = 0
    expenses: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    issues: dict[str, None] = field(default_factory=dict)

    def add(self, projection: BudgetProjection) -> None:
        """
        Add one event's budget income, signed expenses and any unresolved classification.
        """
        self.income += _cents(projection.income)
        self.expenses += _cents(projection.expenses)
        for row in projection.rows:
            if row.kind == "expense":
                self.categories[row.key] = self.categories.get(row.key, 0) + _cents(row.amount)
                self.names[row.key] = row.name
        self.issues.update((issue.message, None) for issue in projection.issues)

    def point(
        self, position: PositionPoint, start: date, end: date, month: str, *,
        partial: bool,
    ) -> AnalysisPoint:
        """
        Return the reporting month's exact signed totals, category details and closing financial position.
        """
        return AnalysisPoint(
            **position.model_dump(), start_date=start, end_date=end, month=month, partial=partial,
            income=_money(self.income), expenses=_money(self.expenses),
            savings=_money(self.income - self.expenses),
            categories=tuple(CategorySpend(key=key, name=self.names[key], amount=_money(amount))
                             for key, amount in self.categories.items()),
            budget_issues=tuple(self.issues),
        )
