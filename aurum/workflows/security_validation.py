#----------------------------------------------------------------------------------------------------------------------#
#                                                        Aurum                                                         #
#                                           Workflows - Security Validation                                            #
#----------------------------------------------------------------------------------------------------------------------#
"""
Check changed security-provider identifiers before publishing a coordinated Setup configuration draft.

Only explicit saves perform these read-only provider lookups. Missing tickers need correction; provider
outages retain the distinction between an invalid instrument and an unverified one.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

# Aurum imports
from aurum.finance.investments import price_symbol, PriceUnavailable, supports_quote_currency
from aurum.finance.securities import Security, SecurityType


# %% 1. Provider Checks --------------------------------------------------------------------------------------
class SecuritySuggestion(BaseModel):
    """
    Present a provider identifier without changing the security's label, stable key or identity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    name: str
    exchange: str = ""


class SecurityCheck(BaseModel):
    """
    Return one changed security's validation outcome and optional explicit repair choices.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    name: str
    symbol: str
    status: Literal["verified", "invalid", "unverified"]
    message: str = ""
    suggestions: tuple[SecuritySuggestion, ...] = ()


def changed_securities(previous: list[dict], proposed: list[dict]) -> tuple[Security, ...]:
    """
    Check additions and changed provider identities, ignoring label-only edits and catalogue ordering.
    """
    old = {item["key"]: Security.model_validate(item) for item in previous}
    result = []
    for value in proposed:
        security = Security.model_validate(value)
        before = old.get(security.key)
        if before is None or _provider_identity(before) != _provider_identity(security):
            result.append(security)
    return tuple(result)


def validate_security_symbols(
    previous: list[dict], proposed: list[dict], *, cancelled: Callable[[], bool] = lambda: False,
    lookup: Callable[[Security, str], dict] | None = None,
) -> tuple[SecurityCheck, ...]:
    """
    Check changed tickers with cooperative cancellation and distinguish provider outages from invalid data.

    The injectable lookup returns status, message and suggestions and makes synthetic checks independent of
    live services. No quote is written to Aurum's database.
    """
    lookup = lookup or _lookup_yahoo
    checks = []
    for security in changed_securities(previous, proposed):
        if cancelled():
            break
        try:
            symbol = price_symbol(security)
        except PriceUnavailable as error:
            checks.append(SecurityCheck(
                key=security.key, name=security.name, symbol=security.symbol,
                status="invalid", message=str(error),
            ))
            continue
        try:
            value = lookup(security, symbol)
            check = SecurityCheck(key=security.key, name=security.name, symbol=symbol, **value)
        except Exception:
            check = SecurityCheck(
                key=security.key, name=security.name, symbol=symbol, status="unverified",
                message="Yahoo Finance is unavailable; the ticker could not be verified.",
            )
        checks.append(check)
    return tuple(checks)


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _provider_identity(security: Security) -> tuple:
    """
    Compare economic/provider fields without treating harmless case or whitespace as a new instrument.
    """
    return (
        security.security_type.value, security.symbol.strip().casefold(), security.market.strip().casefold(),
        (security.price_symbol or "").strip().casefold(),
    )


def _lookup_yahoo(security: Security, symbol: str) -> dict:
    """
    Require an exact Yahoo search result and compatible metadata without requiring a recent closing price.
    """
    import yfinance as yf

    result = yf.Search(
        symbol, max_results=8, news_count=0, lists_count=0, include_cb=False,
        timeout=8, raise_errors=True,
    )
    response = result.response
    if (
        not isinstance(response, dict) or response.get("error")
        or not isinstance(response.get("quotes"), list)
    ):
        raise ValueError("Yahoo returned an incomplete search response")
    exact = next((
        item for item in result.quotes if item.get("symbol", "").casefold() == symbol.casefold()
    ), None)
    if exact is None:
        return {
            "status": "invalid", "message": f"Yahoo Finance could not find {symbol}. Check the price symbol.",
            "suggestions": _suggestions(security, symbol, result.quotes),
        }
    allowed = {"CRYPTOCURRENCY"} if security.security_type == SecurityType.CRYPTO else {
        "EQUITY", "ETF", "MUTUALFUND",
    }
    if not exact.get("quoteType"):
        raise ValueError("Yahoo did not confirm the instrument type")
    if exact["quoteType"] not in allowed:
        return {
            "status": "invalid", "message": "This Yahoo ticker does not match the selected instrument type.",
            "suggestions": _suggestions(security, symbol, result.quotes),
        }
    currency = exact.get("currency")
    if not currency:
        currency = yf.Ticker(symbol).get_history_metadata().get("currency")
    if not currency:
        raise ValueError("Yahoo did not confirm the quote currency")
    if not supports_quote_currency(security.security_type, str(currency).upper()):
        supported = "AUD or USD" if security.security_type == SecurityType.SHARE else "AUD"
        return {
            "status": "invalid",
            "message": f"{symbol} is quoted in {currency}. Choose a symbol quoted in {supported}.",
            "suggestions": _suggestions(security, symbol, result.quotes),
        }
    return {"status": "verified"}


def _suggestions(security: Security, symbol: str, initial: list[dict]) -> tuple[SecuritySuggestion, ...]:
    """
    Offer supported provider candidates, preserving their original names for an explicit user choice.
    """
    import yfinance as yf

    quotes = list(initial)
    try:
        canonical = price_symbol(security.model_copy(update={"price_symbol": None}))
        if canonical.casefold() != symbol.casefold():
            candidate = yf.Search(
                canonical, max_results=5, news_count=0, lists_count=0, include_cb=False,
                timeout=8, raise_errors=True,
            )
            quotes.extend(candidate.quotes)
    except Exception:
        pass
    try:
        result = yf.Search(
            security.name, max_results=8, news_count=0, lists_count=0, include_cb=False,
            enable_fuzzy_query=True, timeout=8, raise_errors=True,
        )
        quotes.extend(result.quotes)
    except Exception:
        pass
    allowed = {"CRYPTOCURRENCY"} if security.security_type == SecurityType.CRYPTO else {
        "EQUITY", "ETF", "MUTUALFUND",
    }
    found = {}
    for item in quotes:
        candidate = str(item.get("symbol") or "")
        aud = candidate.upper().endswith("-AUD" if security.security_type == SecurityType.CRYPTO else ".AX")
        if not candidate or candidate.casefold() == symbol.casefold():
            continue
        currency = item.get("currency") or ("AUD" if aud else None)
        if (item.get("quoteType") not in allowed
                or not supports_quote_currency(security.security_type, currency)):
            continue
        found.setdefault(candidate.casefold(), SecuritySuggestion(
            symbol=candidate, name=item.get("longname") or item.get("shortname") or candidate,
            exchange=item.get("exchDisp") or item.get("exchange") or "",
        ))
    return tuple(list(found.values())[:5])
