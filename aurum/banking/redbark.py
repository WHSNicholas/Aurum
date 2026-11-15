# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Banking - Redbark                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Read Redbark bank evidence without posting accounting or changing the remote connection.

The client follows API v2 pagination, checks response coverage and keeps credentials out of diagnostics.
Its transport can be replaced by synthetic responses. No method writes to Redbark or the local workspace.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import math
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import truststore

API_URL = "https://api.redbark.com/v2"
API_VERSION = "2026-10-01.wattle"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


# %% 1. Read Client ------------------------------------------------------------------------------------------
class RedbarkError(RuntimeError):
    """
    Describe an unsuccessful read using Aurum's own text, without raw provider payloads or credentials.
    """

    def __init__(self, message: str, *, code: str = "", status: int | None = None) -> None:
        """
        Retain a known diagnostic code and HTTP status for callers that need to distinguish failures.
        """
        super().__init__(message)
        self.code = code
        self.status = status


class RedbarkCancelled(RedbarkError):
    """
    Stop at a safe read boundary when the desktop requests cancellation.
    """


@dataclass(frozen=True)
class RedbarkResponse:
    """
    Carry a bounded HTTP response independently of the real or synthetic transport.
    """

    status: int
    headers: Mapping[str, str]
    body: bytes


class RedbarkClient:
    """
    Fetch complete posted bank evidence with explicit dates and cooperative cancellation.

    Transactions are filtered remotely by purchase date. Therefore the client requests all available
    history within Redbark's seven-year window; the import workflow applies its posting-date handover.
    """

    def __init__(
        self, api_key: str, *, cancel: Callable[[], bool] | None = None,
        progress: Callable[[str], None] | None = None,
        transport: Callable[[str, dict[str, str], float], RedbarkResponse] | None = None,
        today: date | None = None,
    ) -> None:
        """
        Keep the secret in memory and allow tests to substitute an entirely local transport and date.
        """
        if not isinstance(api_key, str) or not api_key or any(ord(char) <= 32 for char in api_key):
            raise RedbarkError("Enter a valid Redbark API key.", code="invalid_key")
        if not api_key.isascii() or len(api_key) > 4096:
            raise RedbarkError("Enter a valid Redbark API key.", code="invalid_key")
        self._api_key = api_key
        self._cancel = cancel or (lambda: False)
        self._progress = progress or (lambda message: None)
        self._transport = transport or _http_get
        self._today = today
        self.timezone = ZoneInfo("UTC")
        self._checked = False
        self._requests = {"cheap": deque(), "heavy": deque()}

    @property
    def today(self) -> date:
        """
        Use Redbark's reporting timezone for its inclusive calendar-date filters.
        """
        return self._today if self._today is not None else datetime.now(self.timezone).date()

    @property
    def history_start(self) -> date:
        """
        Keep purchase-date requests inside the provider's approximate seven-year history limit.
        """
        return redbark_history_start(self.today)

    def check_connection(self) -> dict:
        """
        Validate API access, data permission and timezone without requesting bank transactions.
        """
        payload, _ = self._request(f"{API_URL}/me")
        key, entitlements = payload.get("key"), payload.get("entitlements")
        scopes = key.get("scopes") if isinstance(key, dict) else None
        if not isinstance(scopes, list) or not any(item in scopes for item in ("data:read", "data:write")):
            raise RedbarkError("This API key needs Redbark's data:read permission.", code="missing_scope")
        if not isinstance(entitlements, dict) or entitlements.get("api_access") is not True:
            raise RedbarkError("This Redbark account does not have API access.", code="api_access_required")
        try:
            self.timezone = ZoneInfo(payload["timezone"])
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            raise RedbarkError(
                "Redbark returned an unsupported account timezone.", code="invalid_response",
            ) from None
        self._checked = True
        return payload

    def list_accounts(self) -> list[dict]:
        """
        Discover accounts without assuming a masked bank number uniquely identifies an Aurum account.
        """
        if not self._checked:
            self.check_connection()
        rows, truncated = self._pages(f"{API_URL}/accounts?limit=100", "/v2/accounts")
        if truncated:
            raise RedbarkError("Redbark returned an incomplete account list.", code="incomplete_accounts")
        result = {}
        for row in rows:
            identifier = _identifier(row.get("id"), "acct_")
            if not isinstance(row.get("name"), str) or not isinstance(row.get("institution"), dict):
                raise RedbarkError("Redbark returned incomplete account details.", code="invalid_response")
            if identifier in result and result[identifier] != row:
                raise RedbarkError("Redbark account details changed while reading them. Try Sync again.")
            result[identifier] = row
        return list(result.values())

    def list_transactions(self, account_id: str) -> list[dict]:
        """
        Read every supported history page, splitting capped intervals rather than claiming full coverage.
        """
        if not self._checked:
            self.check_connection()
        _identifier(account_id, "acct_")
        result = {}
        for row in self._transaction_window(account_id, self.history_start, self.today):
            _validate_transaction(row, account_id)
            identifier = row["id"]
            if identifier in result and result[identifier] != row:
                raise RedbarkError(
                    "Bank evidence changed during this sync. Try Sync again.", code="changed_page",
                )
            result[identifier] = row
        self._check_cancelled()
        return list(result.values())

    def _transaction_window(self, account_id: str, start: date, end: date) -> list[dict]:
        """
        Discard truncated pages and replace them with two complete, nonoverlapping date intervals.
        """
        self._check_cancelled()
        query = urlencode({
            "account": account_id, "from": start.isoformat(), "to": end.isoformat(),
            "include_pending": "false", "limit": 100,
        })
        rows, truncated = self._pages(f"{API_URL}/transactions?{query}", "/v2/transactions")
        if not truncated:
            for row in rows:
                purchase_date = _iso_date(row.get("date"))
                if not start <= purchase_date <= end:
                    raise RedbarkError("Redbark returned a transaction outside the requested date range.")
            return rows
        if start == end:
            raise RedbarkError(
                "Redbark's daily transaction limit prevents a complete import. No account import was saved.",
                code="truncated_day",
            )
        self._progress("Checking smaller bank-history periods…")
        midpoint = start + (end - start) // 2
        return (
            self._transaction_window(account_id, start, midpoint)
            + self._transaction_window(account_id, midpoint + timedelta(days=1), end)
        )

    def _pages(self, url: str, path: str) -> tuple[list[dict], bool]:
        """
        Follow exact same-endpoint page URLs while checking loops, shape and provider truncation.
        """
        seen, result = set(), []
        while url:
            _check_url(url, path)
            if url in seen or len(seen) >= 10000:
                raise RedbarkError("Redbark pagination did not finish. No account import was saved.")
            seen.add(url)
            payload, headers = self._request(url)
            rows = payload.get("data")
            if payload.get("object") != "list" or not isinstance(rows, list):
                raise RedbarkError("Redbark returned an invalid list response.", code="invalid_response")
            if not all(isinstance(row, dict) for row in rows):
                raise RedbarkError("Redbark returned an invalid list item.", code="invalid_response")
            if headers.get("x-redbark-truncated", "").lower() == "true":
                return [], True
            result.extend(rows)
            if "next_page_url" not in payload:
                raise RedbarkError(
                    "Redbark did not confirm the end of its response.", code="invalid_response",
                )
            next_url = payload["next_page_url"]
            if next_url is not None and (not isinstance(next_url, str) or not next_url):
                raise RedbarkError("Redbark returned an invalid next-page address.", code="invalid_response")
            url = next_url
        return result, False

    def _request(self, url: str) -> tuple[dict, dict[str, str]]:
        """
        Make a bounded read, respecting retry advice without echoing remote messages or request headers.
        """
        _check_url(url)
        for attempt in range(3):
            self._check_cancelled()
            self._pace("heavy" if urlsplit(url).path == "/v2/transactions" else "cheap")
            try:
                response = self._transport(url, {
                    "Authorization": f"Bearer {self._api_key}", "Redbark-Version": API_VERSION,
                    "Accept": "application/json", "User-Agent": "Aurum/Redbark",
                }, 30.0)
            except (URLError, TimeoutError, OSError):
                if attempt == 2:
                    raise RedbarkError(
                        "Could not reach Redbark. Check your connection and try Sync again.",
                    ) from None
                self._wait(2 ** attempt)
                continue
            self._check_cancelled()
            if not isinstance(response, RedbarkResponse) or len(response.body) > MAX_RESPONSE_BYTES:
                raise RedbarkError("Redbark returned an invalid response.", code="invalid_response")
            headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
            if response.status in (429, 503) and attempt < 2:
                delay = _retry_after(headers.get("retry-after"), attempt)
                if delay > 60:
                    raise RedbarkError("Redbark is temporarily busy. Wait a little before trying Sync again.")
                self._progress("Waiting for Redbark…")
                self._wait(delay)
                continue
            if response.status != 200:
                raise _response_error(response)
            try:
                payload = json.loads(response.body)
            except (ValueError, UnicodeError):
                raise RedbarkError("Redbark returned unreadable data.", code="invalid_response") from None
            if not isinstance(payload, dict):
                raise RedbarkError("Redbark returned an invalid response.", code="invalid_response")
            return payload, headers
        raise RedbarkError("Redbark could not finish this request.")

    def _pace(self, tier: str) -> None:
        """
        Keep this client's sequential reads within the documented per-minute request limits.
        """
        times = self._requests[tier]
        now = time.monotonic()
        while times and now - times[0] >= 60:
            times.popleft()
        if len(times) >= (30 if tier == "heavy" else 60):
            self._progress("Waiting for the bank request allowance…")
            self._wait(max(0, 60 - (now - times[0])) + 0.05)
            times.popleft()
        times.append(time.monotonic())

    def _wait(self, seconds: float) -> None:
        """
        Allow Stop to interrupt a retry or rate-limit wait promptly.
        """
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self._check_cancelled()
            time.sleep(min(0.1, max(0, end - time.monotonic())))
        self._check_cancelled()

    def _check_cancelled(self) -> None:
        """
        Distinguish an intentional stop from a failed connection.
        """
        if self._cancel():
            raise RedbarkCancelled("Bank sync stopped. Previously saved transactions are unchanged.")


# %% 2. History Range ----------------------------------------------------------------------------------------
def redbark_history_start(today: date) -> date:
    """
    Use 365-day years and a one-day margin inside Redbark's approximate seven-year boundary.

    Calendar-year subtraction includes leap days and can exceed a duration-based server limit. The API
    does not publish an exact boundary, so requests and new-handover validation share this conservative date.
    """
    return today - timedelta(days=7 * 365 - 1)


# %% 3. Helpers ----------------------------------------------------------------------------------------------
class _NoRedirect(HTTPRedirectHandler):
    """
    Reject all redirects so an Authorization header never follows a remote redirect target.
    """

    def redirect_request(self, request, response, code, message, headers, new_url):
        """
        Let urllib report the redirect status to the safe error handler instead of following it.
        """
        return None


def _http_get(url: str, headers: dict[str, str], timeout: float) -> RedbarkResponse:
    """
    Read one HTTPS response using macOS/system trust and a fixed maximum response size.
    """
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    opener = build_opener(_NoRedirect(), HTTPSHandler(context=context))
    request = Request(url, headers=headers, method="GET")
    try:
        response = opener.open(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        body = response.read(MAX_RESPONSE_BYTES + 1)
        return RedbarkResponse(response.code, dict(response.headers.items()), body)


def _check_url(url: str, expected_path: str | None = None) -> None:
    """
    Allow credentials only on exact Redbark HTTPS read endpoints, including supplied pagination URLs.
    """
    try:
        parsed = urlsplit(url)
        valid = (
            parsed.scheme == "https" and parsed.hostname == "api.redbark.com"
            and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
            and not parsed.fragment and not any(ord(char) <= 32 for char in url)
            and parsed.path in ("/v2/me", "/v2/accounts", "/v2/transactions")
            and (expected_path is None or parsed.path == expected_path)
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise RedbarkError("Redbark returned an unsafe page address. The API key was not sent there.")


def _identifier(value: object, prefix: str) -> str:
    """
    Validate a bounded opaque resource identity without changing its characters.
    """
    if (
        not isinstance(value, str) or not value.startswith(prefix) or len(value) <= len(prefix)
        or len(value) > 1024 or any(ord(char) <= 32 for char in value)
    ):
        raise RedbarkError("Redbark returned an invalid resource identity.", code="invalid_response")
    return value


def _iso_date(value: object) -> date:
    """
    Accept a full ISO calendar date without silently interpreting another date representation.
    """
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise RedbarkError("Redbark returned an invalid transaction date.", code="invalid_response") from None


def _validate_transaction(row: dict, account_id: str) -> None:
    """
    Reject pending, foreign-account or unsupported-currency evidence before callers can save it.
    """
    _identifier(row.get("id"), "txn_")
    if row.get("account") != account_id or row.get("status") != "posted":
        raise RedbarkError(
            "Redbark returned unexpected account or pending evidence.", code="invalid_response",
        )
    amount = row.get("amount")
    if not isinstance(amount, dict) or type(amount.get("amount")) is not int:
        raise RedbarkError("Redbark returned an invalid minor-unit amount.", code="invalid_response")
    if amount.get("currency") != "aud":
        raise RedbarkError("Aurum currently imports AUD bank transactions only.", code="unsupported_currency")
    direction, cents = row.get("direction"), amount["amount"]
    if direction not in ("debit", "credit") or (cents < 0 and direction != "debit") or (
        cents > 0 and direction != "credit"
    ):
        raise RedbarkError("Redbark returned an inconsistent transaction direction.", code="invalid_response")
    _iso_date(row.get("date"))
    for field in ("post_date", "value_date"):
        if row.get(field) is not None:
            _iso_date(row[field])
    if not isinstance(row.get("description"), str) or not row["description"].strip():
        raise RedbarkError("Redbark returned a transaction without bank wording.", code="invalid_response")
    for field in ("reference", "extended_description"):
        if row.get(field) is not None and not isinstance(row[field], str):
            raise RedbarkError("Redbark returned invalid transaction wording.", code="invalid_response")


def _retry_after(value: str | None, attempt: int) -> float:
    """
    Interpret seconds or an HTTP date, using a short bounded backoff for missing or malformed advice.
    """
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            moment = parsedate_to_datetime(value)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            delay = (moment - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError, AttributeError):
            delay = 2 ** (attempt + 1)
    return max(0, delay) if math.isfinite(delay) else 61


def _response_error(response: RedbarkResponse) -> RedbarkError:
    """
    Explain documented failures using local wording and allowlisted fields, never remote error messages.
    """
    messages = {
        "account_unavailable": (
            "This bank account is unavailable in Redbark. Check its connection before syncing again."
        ),
        "from_too_old": "Redbark rejected the history start date as too old. No account import was saved.",
        "invalid_date_range": "Redbark rejected the requested date range. No account import was saved.",
        "upstream_bad_request": (
            "The bank rejected Redbark's transaction request. Check the bank connection and authorised "
            "history in Redbark. No account import was saved."
        ),
        "parameter_invalid": "Redbark rejected a request field.",
        "parameter_missing": "Redbark requires a missing request field.",
        "version_required": "Redbark requires an API version. Aurum's bank connection needs updating.",
        "version_invalid": "Redbark rejected the API version. Aurum's bank connection needs updating.",
        "version_unknown": "Redbark does not recognise this API version. Aurum needs updating.",
        "page_invalid": "Redbark rejected the transaction page. Preview or sync again.",
        "page_filters_changed": "Redbark rejected the transaction page filters. Preview or sync again.",
        "resource_missing": "Redbark no longer recognises the selected account. Check the connection again.",
        "wrong_endpoint_for_category": "Redbark cannot read bank transactions for this account type.",
        "insufficient_scope": (
            "The Redbark API key needs data:read permission. Reconnect in Setup → Accounts."
        ),
        "upstream_auth_failed": "The bank rejected its authorisation. Reconnect the bank in Redbark.",
    }
    fields = {
        "from": "start date", "to": "end date", "account": "bank account", "limit": "page size",
        "page": "page token", "include_pending": "pending-transaction filter",
    }
    code, field = "", ""
    try:
        payload = json.loads(response.body)
        error = payload.get("error", {})
        remote_code, remote_field = error.get("code"), error.get("param")
        if isinstance(remote_code, str) and remote_code in messages:
            code = remote_code
        if isinstance(remote_field, str) and remote_field in fields:
            field = remote_field
    except (ValueError, TypeError, AttributeError, UnicodeError):
        pass
    if code:
        message = messages[code]
        if code in ("parameter_invalid", "parameter_missing") and field:
            verb = "rejected" if code == "parameter_invalid" else "requires"
            message = f"Redbark {verb} the {fields[field]} ({field})."
        message += f" [{code}]"
    else:
        message = {
            401: "The Redbark API key is invalid or expired. Reconnect in Setup → Accounts.",
            403: "Redbark denied access. Check the key's data:read permission, API plan and IP restrictions.",
            429: "Redbark's request limit was reached. Wait a little before trying Sync again.",
            503: "Redbark or the bank is temporarily unavailable. Try Sync again later.",
            424: "Redbark could not read this bank connection. Check it in Redbark before trying again.",
        }.get(response.status, f"Redbark could not finish this request (HTTP {response.status}).")
    return RedbarkError(message, code=code, status=response.status)
