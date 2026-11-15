# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                               Workflows - Bank Feeds                                                 #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Preview, connect and sync posted ANZ Plus bank evidence without invoking accounting or AI.

A confirmed handover separates PDF-owned dates from feed-owned dates. Complete account downloads are
validated before atomic saves; source conflicts remain explicit human decisions in Review.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Callable
from datetime import date
import hashlib
import json
import sqlite3
from uuid import UUID

# Aurum imports
from aurum.banking.redbark import RedbarkCancelled, RedbarkClient, redbark_history_start
from aurum.database.bank_feeds import (
    apply_feed_records, classify_feed_records, create_feed_tables, disconnect_feed, feed_snapshot,
    resolve_feed_conflict,
    save_feed_settings, set_auto_sync, source_revision, transaction_from_feed,
)
from aurum.database.transactions import load_transactions
from aurum.finance.accounts import AccountRegistry, AccountType


# %% 1. Connecting And Importing -----------------------------------------------------------------------------
def preview_connection(
    connection: sqlite3.Connection, accounts: AccountRegistry, client: RedbarkClient, mappings: list[dict],
    *, auto_sync: bool = True, cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """
    Fetch and validate a proposed connection without saving mappings, bank evidence or credentials.

    The returned private preview retains downloaded source facts and stale-input signatures. The GUI should
    cache it behind an opaque token, displaying only account summaries and the explicit confirmation action.
    """
    _check_cancelled(cancelled)
    client.check_connection()
    remote_accounts = client.list_accounts()
    state = feed_snapshot(connection)
    prepared = _validate_mappings(accounts, remote_accounts, mappings, client.today, state["mappings"])
    _validate_handover_history(connection, prepared)
    baseline = source_revision(connection)
    result = {
        "revision": state["revision"], "source_revision": baseline,
        "account_signature": _account_signature(accounts), "mappings": prepared,
        "auto_sync": bool(auto_sync), "accounts": [], "records": {},
        "checked_through": client.today.isoformat(),
    }
    for mapping in prepared:
        _check_cancelled(cancelled)
        if progress:
            progress("Checking " + mapping["name"])
        records = client.list_transactions(mapping["external_account_id"])
        _check_cancelled(cancelled)
        outcomes = classify_feed_records(connection, mapping, records)
        result["records"][mapping["external_account_id"]] = records
        result["accounts"].append({
            "external_account_id": mapping["external_account_id"], "name": mapping["name"],
            "new_count": sum(item["kind"] == "new" for item in outcomes),
            "existing_count": sum(item["kind"] == "existing" for item in outcomes),
            "conflict_count": sum(item["kind"] in {"duplicate", "changed"} for item in outcomes),
            "ignored_count": sum(item["kind"] == "ignored" for item in outcomes),
        })
    if source_revision(connection) != baseline:
        raise ValueError("Saved bank sources changed during this preview. Preview the connection again.")
    return result


def save_connection(
    connection: sqlite3.Connection, accounts: AccountRegistry, preview: dict, *,
    verify_configuration: Callable[[], None] | None = None,
) -> dict:
    """
    Commit the explicitly confirmed connection and its first validated import together or save neither.
    """
    if connection.in_transaction:
        raise ValueError("Finish the pending database operation before connecting Redbark.")
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if verify_configuration:
            verify_configuration()
        if _account_signature(accounts) != preview["account_signature"]:
            raise ValueError("Account settings changed after this preview. Preview the connection again.")
        if source_revision(connection) != preview["source_revision"]:
            raise ValueError("Saved bank sources changed after this preview. Preview the connection again.")
        create_feed_tables(connection)
        save_feed_settings(
            connection, preview["mappings"], auto_sync=preview["auto_sync"],
            expected_revision=preview["revision"],
        )
        results = [
            apply_feed_records(
                connection, mapping, preview["records"][mapping["external_account_id"]],
                checked_through=date.fromisoformat(preview["checked_through"]),
            ) for mapping in preview["mappings"]
        ]
    return {**feed_snapshot(connection), "results": results}


def sync_bank_feeds(
    connection: sqlite3.Connection, accounts: AccountRegistry, client: RedbarkClient, *,
    cancelled: Callable[[], bool] | None = None, progress: Callable[[str], None] | None = None,
    verify_configuration: Callable[[], None] | None = None,
) -> dict:
    """
    Download and atomically save each connected account, preserving successful accounts on later failures.
    """
    state = feed_snapshot(connection)
    if not state["configured"]:
        raise ValueError("Connect Redbark in Setup before syncing bank transactions.")
    _check_cancelled(cancelled)
    try:
        client.check_connection()
        remote_accounts = client.list_accounts()
    except RedbarkCancelled:
        raise
    except (OSError, ValueError, RuntimeError) as error:
        _record_sync_error(
            connection, [item["external_account_id"] for item in state["mappings"] if item["enabled"]],
            str(error), state["revision"],
        )
        raise
    remote = {item["id"]: item for item in remote_accounts}
    results = []
    was_cancelled = False
    for mapping in (item for item in state["mappings"] if item["enabled"]):
        try:
            _check_cancelled(cancelled)
            if progress:
                progress("Syncing " + mapping["name"])
            if mapping["external_account_id"] not in remote:
                raise ValueError("This bank account is no longer returned. Reconnect it in Setup.")
            _validate_mappings(accounts, remote_accounts, [mapping], client.today, state["mappings"])
            checked_through = client.today
            records = client.list_transactions(mapping["external_account_id"])
            _check_cancelled(cancelled)
            # Validate every record before acquiring a write lock.
            for payload in records:
                transaction_from_feed(mapping, payload)
            if connection.in_transaction:
                raise ValueError("Another database operation is unfinished.")
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                if verify_configuration:
                    verify_configuration()
                if feed_snapshot(connection)["revision"] != state["revision"]:
                    raise ValueError("Bank connection settings changed during syncing. Sync again.")
                _check_cancelled(cancelled)
                outcome = apply_feed_records(connection, mapping, records, checked_through=checked_through)
            results.append({"status": "synced", **outcome})
        except RedbarkCancelled:
            was_cancelled = True
            break
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            # The API client supplies sanitised errors; credentials never enter feed state.
            message = str(error)
            _record_sync_error(connection, [mapping["external_account_id"]], message, state["revision"])
            results.append({
                "status": "failed", "external_account_id": mapping["external_account_id"],
                "name": mapping["name"], "error": message, "new_count": 0, "existing_count": 0,
                "conflict_count": 0, "ignored_count": 0,
            })
    return {**feed_snapshot(connection), "results": results, "cancelled": was_cancelled}


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _record_sync_error(
    connection: sqlite3.Connection, external_ids: list[str], message: str, revision: str,
) -> None:
    """
    Retain a safe failure without advancing coverage or overwriting a newer connection's status.
    """
    with connection:
        connection.execute("BEGIN IMMEDIATE")
        if feed_snapshot(connection)["revision"] != revision:
            return
        for external_id in external_ids:
            connection.execute(
                "UPDATE bank_feed_accounts SET error = ? WHERE external_account_id = ?",
                (message, external_id),
            )


def _validate_mappings(
    accounts: AccountRegistry, remote_accounts: list[dict], mappings: list[dict], today: date,
    saved_mappings: list[dict],
) -> list[dict]:
    """
    Require unique explicit ANZ bank links and stable handovers before any source can be imported.
    """
    if not mappings:
        raise ValueError("Link at least one ANZ Plus bank account before continuing.")
    remote = {item["id"]: item for item in remote_accounts}
    local = {str(item.id): item for item in accounts.accounts.values()}
    saved = {item["external_account_id"]: item for item in saved_mappings}
    prepared = []
    seen_remote, seen_local = set(), set()
    for item in mappings:
        external_id = item.get("external_account_id")
        account_id = str(UUID(str(item.get("account_id"))))
        handover = date.fromisoformat(item["handover_date"])
        if external_id in seen_remote or account_id in seen_local:
            raise ValueError("Each Redbark account must link to a different Aurum bank account.")
        seen_remote.add(external_id)
        seen_local.add(account_id)
        account = local.get(account_id)
        bank = remote.get(external_id)
        if account is None or account.account_type is not AccountType.ASSET or account.bank_details is None:
            raise ValueError("Choose a saved asset bank account with BSB and account number.")
        if bank is None or bank.get("category") != "banking":
            raise ValueError("The selected bank account is no longer available from Redbark.")
        if bank.get("status") != "available":
            raise ValueError(
                "This Redbark account is unavailable or still syncing. Try again when it is ready."
            )
        institution = bank.get("institution") or {}
        if "anz" not in str(institution.get("name", "")).casefold():
            raise ValueError("This first bank integration supports ANZ Plus accounts only.")
        if (
            str(bank.get("currency", "")).lower() != "aud"
            or bank.get("type") not in {"transaction", "savings"}
        ):
            raise ValueError("Choose an AUD ANZ Plus transaction or savings account.")
        masked = str(bank.get("account_number") or "")
        digits = "".join(character for character in masked if character.isdigit())
        if digits and not account.bank_details.account_number.endswith(digits[-4:]):
            raise ValueError("The selected bank account's ending digits do not match the Aurum account.")
        if handover > today:
            raise ValueError("Choose a handover date on or before today.")
        previous = saved.get(external_id)
        previous_handovers = [
            item["handover_date"] for item in saved_mappings if item["account_id"] == account_id
        ]
        oldest = redbark_history_start(today)
        if not previous_handovers and handover < oldest:
            raise ValueError(
                "Redbark supports about seven years of history. Choose a handover on or after "
                + oldest.isoformat() + "."
            )
        if previous_handovers and handover.isoformat() != min(previous_handovers):
            raise ValueError(
                "Reconnect this account with its original handover date " + min(previous_handovers)
                + " so earlier bank transactions are reconciled."
            )
        if previous and (previous["account_id"], previous["handover_date"]) != (
            account_id, handover.isoformat(),
        ):
            raise ValueError("A saved bank account's handover and Aurum link cannot be reassigned.")
        prepared.append({
            "external_account_id": external_id, "account_id": account_id,
            "handover_date": handover.isoformat(), "name": account.name, "masked_number": masked,
        })
    return prepared


def _validate_handover_history(connection: sqlite3.Connection, mappings: list[dict]) -> None:
    """
    Require the handover to follow already imported PDF rows instead of quietly owning overlapping dates.
    """
    sources = load_transactions(connection)
    provenance = feed_snapshot(connection)["provenance"]
    for mapping in mappings:
        existing_pdf_dates = [
            item.date for item in sources if str(item.account_id) == mapping["account_id"]
            and str(item.transaction_id) not in provenance and not item.source_file.startswith("redbark:")
        ]
        if existing_pdf_dates and max(existing_pdf_dates) >= date.fromisoformat(mapping["handover_date"]):
            raise ValueError(
                "Choose a handover after " + max(existing_pdf_dates).isoformat()
                + " for " + mapping["name"] + "; PDF transactions already cover that date."
            )


def _account_signature(accounts: AccountRegistry) -> str:
    """
    Guard a preview against changed account identity, type or bank routing details.
    """
    payload = [item.model_dump(mode="json") for item in accounts.accounts.values()]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _check_cancelled(cancelled: Callable[[], bool] | None) -> None:
    """
    Stop before another request or atomic save when the desktop job was cancelled.
    """
    if cancelled and cancelled():
        raise RedbarkCancelled("Bank syncing was stopped; completed account imports were kept.")
