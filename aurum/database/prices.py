# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Database - Prices                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Aurum caches the dated market closes used by investment instructions.

Quotes use exact decimal text and are identified by security, provider symbol and requested date. Once
accepted, a cached quote is reused rather than replaced by a later download. Manual results also retain
their quote so an investment's recorded book cost can be explained independently of future valuations.
Reporting may reuse the latest valid cached close through its cutoff without changing those original dates.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Iterator
from datetime import date
import sqlite3
from uuid import UUID

# Aurum imports
from aurum.finance.investments import DollarConversion, PriceQuote


# %% 1. Price Storage ----------------------------------------------------------------------------------------
PRICE_COLUMNS = (
    "security_id", "requested_date", "price_date", "price", "currency", "provider", "symbol", "fetched_at",
)
QUOTE_SCHEMA = """
CREATE TABLE IF NOT EXISTS price_quotes (
    security_id TEXT NOT NULL,
    requested_date TEXT NOT NULL,
    price_date TEXT NOT NULL,
    price TEXT NOT NULL,
    currency TEXT NOT NULL CHECK (currency = 'AUD'),
    provider TEXT NOT NULL CHECK (provider = 'yahoo'),
    symbol TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (security_id, requested_date, provider, symbol)
) STRICT;
"""
CONVERSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS quote_conversions (
    security_id TEXT NOT NULL,
    requested_date TEXT NOT NULL,
    symbol TEXT NOT NULL,
    details TEXT NOT NULL,
    PRIMARY KEY (security_id, requested_date, symbol)
) STRICT;
"""
PRICE_SCHEMA = QUOTE_SCHEMA + CONVERSION_SCHEMA


def load_quote(
    connection: sqlite3.Connection, security_id: UUID, requested_date: date, symbol: str,
) -> PriceQuote | None:
    """
    Return a cached Yahoo close for this exact request, or None when it has not been fetched.
    """
    row = connection.execute(
        _quote_select(connection)
        + " WHERE security_id = ? AND requested_date = ? AND provider = 'yahoo' AND symbol = ?",
        (str(security_id), requested_date.isoformat(), symbol),
    ).fetchone()
    return _decode_quote(row) if row else None


def load_valuation_quote(
    connection: sqlite3.Connection, security_id: UUID, cutoff: date, symbol: str,
) -> PriceQuote | None:
    """
    Read the latest valid cached close available through a valuation cutoff, preserving its provenance.

    Both the original requested date and the actual close must precede or equal the cutoff. Rank by the
    actual close first, so a later request returning an older close cannot displace a newer market value.
    Invalid cache rows are skipped; this read never repairs records or fetches prices.
    """
    rows = connection.execute(
        _quote_select(connection) + " WHERE security_id = ? AND requested_date <= ? AND price_date <= ? "
        "AND currency = 'AUD' AND provider = 'yahoo' AND symbol = ? "
        "ORDER BY price_date DESC, requested_date DESC",
        (str(security_id), cutoff.isoformat(), cutoff.isoformat(), symbol),
    )
    return next(_valuation_quotes(rows, cutoff), None)


def load_valuation_history(connection: sqlite3.Connection, cutoff: date) -> tuple[PriceQuote, ...]:
    """
    Read all valid saved AUD closes eligible through the final historical cutoff in one query.

    Historical valuation selects the configured symbol separately at each requested point. Older databases
    without a quote cache yield an empty history and retain the recorded values of their positions.
    """
    available = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'price_quotes'"
    ).fetchone()
    if not available:
        return ()
    rows = connection.execute(
        _quote_select(connection)
        + " WHERE requested_date <= ? AND price_date <= ? AND currency = 'AUD' AND provider = 'yahoo'",
        (cutoff.isoformat(), cutoff.isoformat()),
    )
    return tuple(_valuation_quotes(rows, cutoff))


def save_quote(connection: sqlite3.Connection, quote: PriceQuote) -> PriceQuote:
    """
    Save a validated close atomically, keeping an existing quote if another process saved it first.

    Raises:
        ValueError: The caller already has a pending transaction.
        sqlite3.Error: The quote cannot be saved.
    """
    if connection.in_transaction:
        raise ValueError("Save market quotes outside an accounting transaction")
    quote = PriceQuote.model_validate(quote.model_dump())
    values = quote.model_dump(mode="json")
    with connection:
        inserted = connection.execute(
            "INSERT INTO price_quotes (" + ", ".join(PRICE_COLUMNS)
            + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (security_id, requested_date, provider, symbol) DO NOTHING",
            tuple(values[name] for name in PRICE_COLUMNS),
        )
        if inserted.rowcount and quote.conversion is not None:
            # Older workspaces first need this table when a converted quote is actually stored.
            connection.execute(CONVERSION_SCHEMA)
            connection.execute(
                "INSERT INTO quote_conversions VALUES (?, ?, ?, ?)",
                (str(quote.security_id), quote.requested_date.isoformat(), quote.symbol,
                 quote.conversion.model_dump_json()),
            )
    return load_quote(connection, quote.security_id, quote.requested_date, quote.symbol)


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _quote_select(connection: sqlite3.Connection) -> str:
    """
    Include conversion evidence while allowing read-only access to older quote databases.
    """
    available = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='quote_conversions'"
    ).fetchone()
    evidence = "details" if available else "NULL"
    query = "SELECT " + ", ".join(PRICE_COLUMNS) + ", " + evidence + " FROM price_quotes"
    if available:
        query += " LEFT JOIN quote_conversions USING (security_id, requested_date, symbol)"
    return query


def _decode_quote(row: tuple) -> PriceQuote:
    """
    Validate the AUD close together with the original price and FX rate when present.
    """
    values = dict(zip(PRICE_COLUMNS, row))
    if row[-1] is not None:
        values["conversion"] = DollarConversion.model_validate_json(row[-1])
    return PriceQuote.model_validate(values)


def _valuation_quotes(rows: sqlite3.Cursor, cutoff: date) -> Iterator[PriceQuote]:
    """
    Skip invalid cache records and enforce both cutoff dates on validated values.
    """
    for row in rows:
        try:
            quote = _decode_quote(row)
        except ValueError:
            continue
        if quote.requested_date <= cutoff and quote.price_date <= cutoff:
            yield quote
