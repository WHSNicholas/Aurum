# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                             Workflows - Market History                                               #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Backfill completed Yahoo AUD closes for the portions of a chart range where a security was actually held.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
import sqlite3
from typing import NamedTuple
from uuid import UUID
from zoneinfo import ZoneInfo
import warnings

# Aurum imports
from aurum.database.market_history import cached_ranges, market_update_lock, save_market_batch
from aurum.finance.investments import (
    DollarConversion, PriceQuote, PriceUnavailable, completed_yahoo_bar, price_symbol,
    supports_quote_currency, usd_exchange_rates,
)
from aurum.finance.journal import JournalEntry
from aurum.finance.securities import Security, SecurityRegistry, SecurityType

DateRange = tuple[date, date]
GAP_RETRY_DELAY = timedelta(minutes=15)


# %% 1. Historical Price Updates -----------------------------------------------------------------------------
class MarketCloseBatch(NamedTuple):
    """
    Return completed daily closes and the inclusive ranges successfully checked by the provider.
    """

    quotes: tuple[PriceQuote, ...]
    coverage: tuple[DateRange, ...]
    warning: str = ""


class MarketHistoryUpdate(NamedTuple):
    """
    Report newly stored closes, provider or cache issues, and a cooperative stop between requests.
    """

    updated: int
    warnings: tuple[str, ...]
    cancelled: bool


def update_market_history(
    workspace: Path, entries: Iterable[JournalEntry], securities: SecurityRegistry, start: date, cutoff: date,
    *, cancelled: Callable[[], bool] = lambda: False, progress: Callable[[str], None] = lambda message: None,
    fetcher: Callable[[Security, date, date], MarketCloseBatch] | None = None, now: datetime | None = None,
) -> MarketHistoryUpdate:
    """
    Fetch uncovered held intervals in batches and commit each successful request in the private daily cache.

    Seven days before each held interval can supply its opening valuation over closed markets. Failures
    retain earlier successful batches and leave unchecked dates available on a subsequent request.
    """
    if now is not None and now.utcoffset() is None:
        raise ValueError("The price-check time needs a timezone.")
    try:
        ranges = _holding_ranges(entries, start, cutoff, cancelled)
        if cancelled():
            return MarketHistoryUpdate(0, (), True)
        if not ranges:
            return MarketHistoryUpdate(0, (), False)
        with market_update_lock(workspace, cancelled) as acquired:
            if not acquired:
                return MarketHistoryUpdate(0, (), True)
            return _update_ranges(workspace, securities, ranges, fetcher or _YahooHistory(now),
                                  progress, cancelled, now)
    except InterruptedError:
        return MarketHistoryUpdate(0, (), True)
    except OSError as error:
        return MarketHistoryUpdate(0, (str(error),), cancelled())


# %% 2. Holdings and Coverage --------------------------------------------------------------------------------
def _update_ranges(
    workspace: Path, securities: SecurityRegistry, ranges: dict[UUID, list[DateRange]],
    fetch: Callable[[Security, date, date], MarketCloseBatch], progress: Callable[[str], None],
    cancelled: Callable[[], bool], now: datetime | None,
) -> MarketHistoryUpdate:
    """
    Recheck durable observations and recent gap checks while holding the workspace provider lock.
    """
    count, notices = 0, {}
    for identifier, held in ranges.items():
        if cancelled():
            return MarketHistoryUpdate(count, tuple(notices), True)
        security = securities.by_id(identifier)
        try:
            symbol = price_symbol(security)
            covered = cached_ranges(workspace, identifier, symbol, now=now or datetime.now(UTC),
                                    start=held[0][0], end=held[-1][1])
            missing = _session_ranges(_missing_ranges(held, covered), security.security_type)
            for begin, end in missing:
                if cancelled():
                    return MarketHistoryUpdate(count, tuple(notices), True)
                progress(f"Checking {symbol} prices from {begin} to {end}.")
                try:
                    batch = fetch(security, begin, end)
                except PriceUnavailable as error:
                    batch = MarketCloseBatch((), (), str(error))
                except Exception:
                    batch = MarketCloseBatch((), (), "Yahoo daily prices could not be retrieved.")
                if cancelled():
                    return MarketHistoryUpdate(count, tuple(notices), True)
                try:
                    if (any(not begin <= q.price_date <= end for q in batch.quotes)
                            or any(not begin <= left <= right <= end for left, right in batch.coverage)):
                        raise PriceUnavailable("Yahoo returned prices outside the requested dates.")
                    checked_at = now or datetime.now(UTC)
                    # A current session can close between visits. Delay only gaps on earlier market days.
                    past_end = min(end, _market_today(security, checked_at) - timedelta(days=1))
                    observed = [(quote.price_date, quote.price_date) for quote in batch.quotes]
                    gaps = _session_ranges(_missing_ranges([(begin, past_end)], observed),
                                           security.security_type) if begin <= past_end else []
                    count += save_market_batch(
                        workspace, identifier, symbol, batch.quotes, batch.coverage, gaps=gaps,
                        checked_at=checked_at, retry_after=checked_at + GAP_RETRY_DELAY,
                    )
                    if batch.warning:
                        notices[f"{symbol}: {batch.warning}"] = None
                except (ValueError, OSError, sqlite3.Error) as error:
                    notices[f"{symbol}: {error}"] = None
        except (ValueError, OSError, sqlite3.Error) as error:
            notices[f"{security.name}: {error}"] = None
    return MarketHistoryUpdate(count, tuple(notices), cancelled())


def _market_today(security: Security, now: datetime) -> date:
    """
    Protect the active session from retry delays, using the earliest possible local day for other markets.
    """
    if security.security_type == SecurityType.CRYPTO:
        return now.astimezone(UTC).date()
    if price_symbol(security).upper().endswith(".AX"):
        return now.astimezone(ZoneInfo("Australia/Sydney")).date()
    return (now.astimezone(UTC) - timedelta(hours=12)).date()


def _session_ranges(ranges: Iterable[DateRange], kind: SecurityType) -> list[DateRange]:
    """
    Skip isolated closed share weekends and keep adjoining missing sessions in one Yahoo range request.
    """
    return [(left, right) for left, right in ranges if kind == SecurityType.CRYPTO
            or (right - left).days >= 2 or left.weekday() < 5 or right.weekday() < 5]


def _holding_ranges(
    entries: Iterable[JournalEntry], start: date, cutoff: date, cancelled: Callable[[], bool],
) -> dict[UUID, list[DateRange]]:
    """
    Combine account-level unit changes by security and date before testing each end-of-day position.
    """
    changes = defaultdict(lambda: defaultdict(Decimal))
    result = {}
    with localcontext() as context:
        context.prec = 50
        for entry in entries:
            if cancelled():
                raise InterruptedError
            if entry.date <= cutoff:
                for line in entry.lines:
                    if line.investment is not None:
                        changes[line.investment.security_id][entry.date] += line.investment.units
        for identifier, movements in changes.items():
            units, opened, intervals = Decimal(0), None, []
            for day, movement in sorted(movements.items()):
                units += movement
                if units > 0 and opened is None:
                    opened = day
                elif units <= 0 and opened is not None:
                    intervals.append((opened, day - timedelta(days=1)))
                    opened = None
            if opened is not None:
                intervals.append((opened, cutoff))
            requested = [
                (date.fromordinal(max(1, max(left, start).toordinal() - 7)), min(right, cutoff))
                for left, right in intervals if max(left, start) <= min(right, cutoff)
            ]
            if requested:
                result[identifier] = _merged(requested)
    return result


def _merged(ranges: Iterable[DateRange]) -> list[DateRange]:
    """
    Coalesce overlapping or abutting inclusive calendar ranges in chronological order.
    """
    result = []
    for left, right in sorted(ranges):
        if result and left.toordinal() <= result[-1][1].toordinal() + 1:
            result[-1] = (result[-1][0], max(right, result[-1][1]))
        else:
            result.append((left, right))
    return result


def _missing_ranges(wanted: Iterable[DateRange], covered: Iterable[DateRange]) -> list[DateRange]:
    """
    Subtract successfully cached date spans while preserving gaps between periods of ownership.
    """
    checked = _merged(covered)
    result = []
    for left, right in wanted:
        cursor = left.toordinal()
        for begin, end in checked:
            if end.toordinal() < cursor:
                continue
            if begin > right:
                break
            if begin.toordinal() > cursor:
                result.append((date.fromordinal(cursor), date.fromordinal(begin.toordinal() - 1)))
            cursor = max(cursor, end.toordinal() + 1)
            if cursor > right.toordinal():
                break
        if cursor <= right.toordinal():
            result.append((date.fromordinal(cursor), right))
    return result


# %% 3. Yahoo Daily Closes -----------------------------------------------------------------------------------
class _YahooHistory:
    """
    Request whole daily series, reusing the instrument and split check for each security within one job.
    """

    def __init__(self, now: datetime | None = None) -> None:
        """
        Defer the provider import and every network operation until an uncovered interval is requested.
        """
        self.tickers = {}
        self.latest_splits = {}
        self.now = now

    def __call__(self, security: Security, start: date, end: date) -> MarketCloseBatch:
        """
        Require the configured instrument, supported currency, raw daily Close and completed sessions.
        """
        import yfinance as yf
        from yfinance.exceptions import YFPricesMissingError

        symbol = price_symbol(security)
        try:
            if symbol not in self.tickers:
                self.tickers[symbol] = yf.Ticker(symbol)
            ticker = self.tickers[symbol]
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore", message=r"The inplace keyword in DataFrame\..* is deprecated.*",
                    module=r"yfinance\..*",
                )
                try:
                    history = ticker.history(
                        start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(), interval="1d",
                        auto_adjust=False, back_adjust=False, rounding=False, keepna=True, timeout=15,
                        raise_errors=True,
                    )
                except YFPricesMissingError as error:
                    # yfinance also uses this exception for explicit Yahoo errors and HTTP statuses.
                    reason = str(error.yahoo_reason or "").strip()
                    no_data = reason.casefold().rstrip(".") in {
                        "", "no data found", "no data found, symbol may be delisted",
                    } or reason.casefold().startswith("data doesn't exist for startdate")
                    if no_data and "Yahoo status_code" not in error.debug_info:
                        return MarketCloseBatch((), ())
                    raise PriceUnavailable(reason or "Yahoo rejected the daily price request.") from None
                if history.empty:
                    return MarketCloseBatch((), ())
                metadata = ticker.get_history_metadata()
                if str(metadata.get("symbol", "")).casefold() != symbol.casefold():
                    raise PriceUnavailable("Yahoo did not confirm the requested instrument.")
                if not supports_quote_currency(security.security_type, metadata.get("currency")):
                    raise PriceUnavailable("Yahoo did not confirm a supported quote currency.")
                if security.security_type == SecurityType.SHARE and symbol not in self.latest_splits:
                    self.latest_splits[symbol] = max((stamp.date() for stamp in ticker.get_splits().index),
                                                     default=None)
            return _completed_closes(security, symbol, start, end, history, metadata,
                                     self.latest_splits.get(symbol), self.now or datetime.now(UTC))
        except PriceUnavailable:
            raise
        except Exception:
            raise PriceUnavailable("Yahoo daily prices could not be retrieved.") from None


def _completed_closes(
    security: Security, symbol: str, start: date, end: date, history, metadata: dict,
    last_split: date | None, now: datetime,
) -> MarketCloseBatch:
    """
    Preserve good bars in a partial response and keep incomplete sessions and bad rows open for retry.

    Only observed closes and ordinary share weekends provide lasting coverage. An omitted weekday may be
    a closed exchange session or a provider gap. It stays eligible for a later request.
    """
    from pandas import isna

    prices = {}
    invalid, absent, pending = set(), set(), set()
    for stamp, row in history.iterrows():
        if stamp.utcoffset() is None:
            raise PriceUnavailable("Yahoo omitted the exchange timezone.")
        day = stamp.date()
        if not start <= day <= end:
            continue
        if last_split is not None and day < last_split:
            invalid.add(day)
            continue
        if not completed_yahoo_bar(stamp, security.security_type, metadata, now):
            pending.add(day)
            continue
        try:
            close = row["Close"]
            if isna(close):
                absent.add(day)
                continue
            value = Decimal(str(close))
        except (InvalidOperation, KeyError):
            invalid.add(day)
            continue
        if value.is_nan():
            absent.add(day)
        elif not value.is_finite() or value <= 0 or (day in prices and prices[day] != value):
            invalid.add(day)
        else:
            prices[day] = value
    omitted = invalid | absent | pending
    for day in omitted:
        prices.pop(day, None)
    if not prices and not pending and invalid:
        if last_split is not None and max(invalid) < last_split:
            raise PriceUnavailable("A later share split makes these historical unit values ambiguous.")
        raise PriceUnavailable("Yahoo returned invalid daily close values.")
    quotes = []
    warning = ""
    rates = None
    if prices and metadata.get("currency") == "USD":
        rates = usd_exchange_rates(min(prices), max(prices), now)
    for day, value in sorted(prices.items()):
        conversion = None
        if rates is not None:
            if day not in rates:
                omitted.add(day)
                warning = "A completed USD/AUD exchange rate is missing for one or more daily closes."
                continue
            conversion = DollarConversion(usd_close=value, aud_per_usd=rates[day])
            value = conversion.aud_price()
        quotes.append(PriceQuote(security_id=security.id, symbol=symbol, price_date=day, requested_date=day,
                                 price=value, fetched_at=now, conversion=conversion))
    checked = []
    for ordinal in range(start.toordinal(), end.toordinal() + 1):
        day = date.fromordinal(ordinal)
        closed_session = security.security_type == SecurityType.SHARE and day.weekday() >= 5
        if (day in prices or closed_session) and day not in omitted:
            checked.append((day, day))
    return MarketCloseBatch(tuple(quotes), tuple(_merged(checked)), warning)
