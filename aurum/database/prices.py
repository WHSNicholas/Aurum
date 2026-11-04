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
from datetime import date
import sqlite3
from uuid import UUID

# Aurum imports
from aurum.finance.investments import PriceQuote


# %% 1. Price Storage ----------------------------------------------------------------------------------------
PRICE_COLUMNS = (
    "security_id", "requested_date", "price_date", "price", "currency", "provider", "symbol", "fetched_at",
)
PRICE_SCHEMA = """
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


def load_quote(
    connection: sqlite3.Connection, security_id: UUID, requested_date: date, symbol: str,
) -> PriceQuote | None:
    """
    Return a cached Yahoo close for this exact request, or None when it has not been fetched.
    """
    row = connection.execute(
        "SELECT " + ", ".join(PRICE_COLUMNS) + " FROM price_quotes "
        "WHERE security_id = ? AND requested_date = ? AND provider = 'yahoo' AND symbol = ?",
        (str(security_id), requested_date.isoformat(), symbol),
    ).fetchone()
    return PriceQuote.model_validate(dict(zip(PRICE_COLUMNS, row))) if row else None


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
        "SELECT " + ", ".join(PRICE_COLUMNS) + " FROM price_quotes "
        "WHERE security_id = ? AND requested_date <= ? AND price_date <= ? "
        "AND currency = 'AUD' AND provider = 'yahoo' AND symbol = ? "
        "ORDER BY price_date DESC, requested_date DESC",
        (str(security_id), cutoff.isoformat(), cutoff.isoformat(), symbol),
    )
    for row in rows:
        try:
            quote = PriceQuote.model_validate(dict(zip(PRICE_COLUMNS, row)))
        except ValueError:
            continue
        if quote.requested_date <= cutoff and quote.price_date <= cutoff:
            return quote
    return None


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
        connection.execute(
            "INSERT INTO price_quotes (" + ", ".join(PRICE_COLUMNS)
            + ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (security_id, requested_date, provider, symbol) DO NOTHING",
            tuple(values[name] for name in PRICE_COLUMNS),
        )
    return load_quote(connection, quote.security_id, quote.requested_date, quote.symbol)
