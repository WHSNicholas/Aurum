# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                              Database - Market History                                               #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Store completed daily valuation closes separately from the immutable quotes used to post investments.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import date, datetime
import fcntl
import os
from pathlib import Path
import sqlite3
from time import sleep
from uuid import UUID

# Aurum imports
from aurum.database.prices import load_valuation_history, load_valuation_quote
from aurum.finance.investments import PriceQuote


# %% 1. Valuation Reads --------------------------------------------------------------------------------------
def load_market_history(
    connection: sqlite3.Connection, workspace: Path, cutoff: date,
) -> tuple[PriceQuote, ...]:
    """
    Combine eligible trade quotes and dated market closes, giving trade evidence priority on exact ties.

    A missing or unreadable optional cache leaves the accepted quotes available. Each daily observation
    becomes eligible on its own close date, even when the historical range was downloaded later.
    """
    trades = load_valuation_history(connection, cutoff)
    try:
        with _reader(workspace) as cache:
            if cache is None:
                return trades
            daily = tuple(_daily_quotes(cache.execute(
                _SELECT_DAILY + " WHERE price_date <= ?", (cutoff.isoformat(),),
            )))
    except (OSError, sqlite3.Error):
        return trades
    unique = {
        (quote.security_id, quote.symbol, quote.requested_date, quote.price_date): quote
        for quote in (*daily, *trades)
    }
    return tuple(unique.values())


def load_latest_market_quotes(
    connection: sqlite3.Connection, workspace: Path, cutoff: date, symbols: Mapping[UUID, str],
) -> dict[UUID, PriceQuote]:
    """
    Read one latest valid quote per configured holding without scanning unrelated daily price history.
    """
    if not symbols:
        return {}
    result = {}
    trade_table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='price_quotes' AND type='table'"
    ).fetchone()
    if trade_table:
        for identifier, symbol in symbols.items():
            trade = load_valuation_quote(connection, identifier, cutoff, symbol)
            if trade is not None:
                result[identifier] = trade
    try:
        with _reader(workspace) as cache:
            if cache is not None:
                for identifier, symbol in symbols.items():
                    rows = cache.execute(
                        _SELECT_DAILY + " WHERE security_id=? AND symbol=? AND price_date<=? "
                        "ORDER BY price_date DESC", (str(identifier), symbol, cutoff.isoformat()),
                    )
                    daily = next(_daily_quotes(rows), None)
                    previous = result.get(identifier)
                    if daily and (previous is None or daily.price_date > previous.price_date):
                        result[identifier] = daily
    except (OSError, sqlite3.Error):
        pass
    return result


# %% 2. Daily Cache ------------------------------------------------------------------------------------------
@contextmanager
def market_update_lock(workspace: Path, cancelled: Callable[[], bool]) -> Iterator[bool]:
    """
    Serialize provider checks across workspace windows while allowing independent valuation readers.

    A waiting job checks for cancellation and re-reads coverage after acquiring the lock. The file stays
    in place so a second process opens the same inode. Closing it releases the lock even after an error.
    """
    path = _cache_path(workspace).with_name(".market_history.lock")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        while not cancelled():
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                sleep(0.05)
                continue
            yield True
            return
        yield False
    finally:
        os.close(descriptor)


def cached_ranges(
    workspace: Path, identifier: UUID, symbol: str, *, now: datetime | None = None,
    start: date = date.min, end: date = date.max,
) -> list[tuple[date, date]]:
    """
    Return validated observation dates and, when requested, gaps whose retry time is still in the future.

    Older broad coverage records could include omitted weekdays. Stored close rows are the evidence
    for permanent price coverage in both older and newly created caches.
    """
    with _reader(workspace) as cache:
        if cache is None:
            return []
        tables = {row[0] for row in cache.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            return []
        values = (str(identifier), symbol, start.isoformat(), end.isoformat())
        closes = cache.execute(
            _SELECT_DAILY + " WHERE security_id=? AND symbol=? AND price_date BETWEEN ? AND ?", values,
        )
        result = [(q.price_date, q.price_date) for q in _daily_quotes(closes)]
        if now is not None and "retry_gaps" in tables:
            rows = cache.execute(
                "SELECT start_date, end_date FROM retry_gaps WHERE security_id=? AND symbol=? "
                "AND end_date>=? AND start_date<=? AND retry_after>?", (*values, now.timestamp()),
            )
            result.extend((date.fromisoformat(left), date.fromisoformat(right)) for left, right in rows)
        return result


def save_market_batch(
    workspace: Path, identifier: UUID, symbol: str, quotes: Iterable[PriceQuote],
    coverage: Iterable[tuple[date, date]],
    *, gaps: Iterable[tuple[date, date]] = (), checked_at: datetime | None = None,
    retry_after: datetime | None = None,
) -> int:
    """
    Commit valid daily observations and expiring gaps together. Older callers may still supply checked
    ranges; their endpoints alone cannot certify an intervening weekday had a usable price.
    """
    if any(start > end for start, end in coverage):
        raise ValueError("A checked market-history range ends before it starts.")
    rows = []
    for quote in quotes:
        quote = PriceQuote.model_validate(quote)
        if (quote.security_id != identifier or quote.symbol != symbol
                or quote.price_date != quote.requested_date):
            raise ValueError("The daily price does not match the requested security, symbol and close date.")
        rows.append((str(identifier), symbol, quote.price_date.isoformat(),
                     str(quote.price), quote.fetched_at.isoformat()))
    retries = [(str(identifier), symbol, start.isoformat(), end.isoformat(), retry_after.timestamp())
               for start, end in gaps] if retry_after is not None else []
    if not rows and not retries:
        return 0
    path = _cache_path(workspace)
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT, 0o600))
    with closing(sqlite3.connect(path)) as cache, cache:
        cache.execute("BEGIN IMMEDIATE")
        cache.execute(
            "CREATE TABLE IF NOT EXISTS daily_closes (security_id TEXT NOT NULL, symbol TEXT NOT NULL, "
            "price_date TEXT NOT NULL, price TEXT NOT NULL, fetched_at TEXT NOT NULL, "
            "PRIMARY KEY(security_id, symbol, price_date)) STRICT"
        )
        cache.execute(
            "CREATE TABLE IF NOT EXISTS retry_gaps (security_id TEXT NOT NULL, symbol TEXT NOT NULL, "
            "start_date TEXT NOT NULL, end_date TEXT NOT NULL CHECK(end_date>=start_date), "
            "retry_after REAL NOT NULL, PRIMARY KEY(security_id, symbol, start_date, end_date)) STRICT"
        )
        count = _store_observations(cache, rows)
        if checked_at is not None:
            cache.execute("DELETE FROM retry_gaps WHERE retry_after<=?", (checked_at.timestamp(),))
        cache.executemany(
            "INSERT INTO retry_gaps VALUES(?,?,?,?,?) ON CONFLICT(security_id, symbol, start_date, end_date) "
            "DO UPDATE SET retry_after=MAX(retry_after,excluded.retry_after)", retries,
        )
        return count


# %% 3. Helpers ----------------------------------------------------------------------------------------------
_SELECT_DAILY = "SELECT security_id, symbol, price_date, price, fetched_at FROM daily_closes"


def _store_observations(cache: sqlite3.Connection, rows: list[tuple]) -> int:
    """
    Add new observations and repair only unreadable conflicting rows under the same write transaction.
    """
    count = cache.executemany("INSERT OR IGNORE INTO daily_closes VALUES(?,?,?,?,?)", rows).rowcount
    if count == len(rows):
        return count
    dates = [row[2] for row in rows]
    stored = cache.execute(
        _SELECT_DAILY + " WHERE security_id=? AND symbol=? AND price_date BETWEEN ? AND ?",
        (*rows[0][:2], min(dates), max(dates)),
    )
    valid_dates = {quote.price_date.isoformat() for quote in _daily_quotes(stored)}
    repairs = [(price, fetched, identifier, symbol, day) for identifier, symbol, day, price, fetched in rows
               if day not in valid_dates]
    repaired = cache.executemany(
        "UPDATE daily_closes SET price=?, fetched_at=? WHERE security_id=? AND symbol=? AND price_date=?",
        repairs,
    ).rowcount
    return count + repaired


def _cache_path(workspace: Path) -> Path:
    """
    Locate the private, optional Yahoo AUD daily-close database in this workspace.
    """
    return Path(workspace).resolve() / "data" / "market_history.sqlite3"


@contextmanager
def _reader(workspace: Path) -> Iterator[sqlite3.Connection | None]:
    """
    Open an existing price cache read-only and release its snapshot promptly.
    """
    path = _cache_path(workspace)
    if not path.is_file():
        yield None
        return
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as cache:
        cache.execute("BEGIN")
        yield cache


def _daily_quotes(rows: sqlite3.Cursor) -> Iterator[PriceQuote]:
    """
    Discard malformed or nonpositive rows without modifying the cache.
    """
    for identifier, symbol, day, price, fetched_at in rows:
        try:
            yield PriceQuote(security_id=identifier, symbol=symbol, requested_date=day,
                             price_date=day, price=price, fetched_at=fetched_at)
        except ValueError:
            continue
