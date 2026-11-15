# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                Database - Bank Feeds                                                 #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Retain bank-feed ownership, source identities and reviewable import conflicts beside immutable transactions.

Feed tables do not change journal accounting. Remote observations are linked permanently to Aurum UUIDs;
later observations never replace source facts or delete saved transactions.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from datetime import UTC, date, datetime
from decimal import Decimal
import hashlib
import json
import sqlite3
from uuid import UUID, uuid4

# Aurum imports
from aurum.database.transactions import load_transactions, to_sqlite_dollars
from aurum.finance.transaction import Transaction

BANK_FEED_SCHEMA = """
CREATE TABLE IF NOT EXISTS bank_feed_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    connected INTEGER NOT NULL CHECK (connected IN (0, 1)),
    auto_sync INTEGER NOT NULL CHECK (auto_sync IN (0, 1)),
    revision TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS bank_feed_accounts (
    external_account_id TEXT PRIMARY KEY NOT NULL,
    account_id TEXT NOT NULL,
    handover_date TEXT NOT NULL,
    name TEXT NOT NULL,
    masked_number TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    last_success TEXT,
    checked_through TEXT,
    error TEXT NOT NULL DEFAULT ''
) STRICT;
CREATE UNIQUE INDEX IF NOT EXISTS bank_feed_active_account
    ON bank_feed_accounts(account_id) WHERE enabled = 1;
CREATE TABLE IF NOT EXISTS bank_feed_transactions (
    external_account_id TEXT NOT NULL REFERENCES bank_feed_accounts(external_account_id),
    external_transaction_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
    payload TEXT NOT NULL,
    accepted_payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (external_account_id, external_transaction_id)
) STRICT;
CREATE TABLE IF NOT EXISTS bank_feed_conflicts (
    id TEXT PRIMARY KEY NOT NULL,
    external_account_id TEXT NOT NULL REFERENCES bank_feed_accounts(external_account_id),
    external_transaction_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('duplicate', 'changed')),
    payload TEXT NOT NULL,
    candidates TEXT NOT NULL,
    revision TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (external_account_id, external_transaction_id)
) STRICT;
"""


# %% 1. Saved Feed State -------------------------------------------------------------------------------------
def feed_snapshot(connection: sqlite3.Connection) -> dict:
    """
    Read feed settings and evidence without creating missing tables or exposing credentials.
    """
    result = {
        "configured": False, "auto_sync": False, "revision": "", "mappings": [],
        "conflicts": [], "provenance": {}, "last_success": None,
    }
    if not _has_tables(connection):
        return result
    settings = connection.execute(
        "SELECT connected, auto_sync, revision FROM bank_feed_settings WHERE id = 1"
    ).fetchone()
    if settings:
        result.update(configured=bool(settings[0]), auto_sync=bool(settings[1]), revision=settings[2])
    columns = (
        "external_account_id", "account_id", "handover_date", "name", "masked_number",
        "enabled", "last_success", "checked_through", "error",
    )
    result["mappings"] = [
        dict(zip(columns, row)) for row in connection.execute(
            "SELECT " + ", ".join(columns) + " FROM bank_feed_accounts ORDER BY rowid"
        )
    ]
    mappings = {item["external_account_id"]: item for item in result["mappings"]}
    for external_id, transaction_id, payload in connection.execute(
        "SELECT external_account_id, transaction_id, payload FROM bank_feed_transactions"
    ):
        evidence = json.loads(payload)
        evidence.update(
            source_label="Redbark · " + mappings[external_id]["name"], provider="redbark",
            external_account_id=external_id,
        )
        result["provenance"].setdefault(transaction_id, evidence)
    conflict_rows = connection.execute(
        "SELECT id, external_account_id, external_transaction_id, kind, payload, candidates, revision "
        "FROM bank_feed_conflicts ORDER BY created_at, id"
    ).fetchall()
    sources = {
        str(item.transaction_id): item for item in load_transactions(connection)
    } if conflict_rows else {}
    for row in conflict_rows:
        payload = json.loads(row[4])
        mapping = mappings[row[1]]
        candidates = json.loads(row[5])
        booking_date = payload.get("post_date") or payload["date"]
        review_date = min(
            [booking_date] + [sources[key].date.isoformat() for key in candidates if key in sources]
        ) if row[3] == "changed" else booking_date
        result["conflicts"].append({
            "id": row[0], "external_account_id": row[1], "external_transaction_id": row[2],
            "kind": row[3], "payload": payload, "candidates": candidates, "revision": row[6],
            "candidate_records": [
                {"transaction_id": key, "date": sources[key].date.isoformat(),
                 "description": sources[key].description, "amount": str(sources[key].amount),
                 "source_file": sources[key].source_file, "account_id": str(sources[key].account_id)}
                for key in candidates if key in sources
            ],
            "account_id": mapping["account_id"], "account_name": mapping["name"],
            "date": booking_date, "review_date": review_date, "description": payload["description"],
            "amount": str(Decimal(payload["amount"]["amount"]) / 100),
        })
    successful = [
        item["last_success"] for item in result["mappings"] if item["enabled"] and item["last_success"]
    ]
    enabled_count = sum(bool(item["enabled"]) for item in result["mappings"])
    result["last_success"] = min(successful) if successful and len(successful) == enabled_count else None
    return result


def source_revision(connection: sqlite3.Connection) -> str:
    """
    Fingerprint saved sources and feed decisions to reject a preview after another import or resolution.
    """
    source = connection.execute(
        "SELECT * FROM transactions ORDER BY transaction_id"
    ).fetchall()
    state = feed_snapshot(connection)
    return hashlib.sha256(_json({"sources": source, "feed": state}).encode()).hexdigest()


def create_feed_tables(connection: sqlite3.Connection) -> None:
    """
    Create optional feed tables inside an explicitly authorised connection save transaction.
    """
    if not connection.in_transaction:
        raise ValueError("Create bank-feed storage inside the explicit connection save.")
    for statement in BANK_FEED_SCHEMA.split(";"):
        if statement.strip():
            connection.execute(statement)


def save_feed_settings(
    connection: sqlite3.Connection, mappings: list[dict], *, auto_sync: bool, expected_revision: str,
) -> None:
    """
    Save validated mappings inside the caller's transaction, retaining disconnected source identities.
    """
    _require_revision(connection, expected_revision)
    connection.execute("UPDATE bank_feed_accounts SET enabled = 0")
    for mapping in mappings:
        existing = connection.execute(
            "SELECT account_id, handover_date FROM bank_feed_accounts WHERE external_account_id = ?",
            (mapping["external_account_id"],),
        ).fetchone()
        if existing and tuple(existing) != (mapping["account_id"], mapping["handover_date"]):
            raise ValueError("A saved bank connection's account and handover date cannot be reassigned.")
        connection.execute(
            "INSERT INTO bank_feed_accounts "
            "(external_account_id, account_id, handover_date, name, masked_number, enabled) "
            "VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT(external_account_id) DO UPDATE SET "
            "name = excluded.name, masked_number = excluded.masked_number, enabled = 1",
            tuple(mapping[key] for key in (
                "external_account_id", "account_id", "handover_date", "name", "masked_number",
            )),
        )
    connection.execute(
        "INSERT INTO bank_feed_settings (id, connected, auto_sync, revision) VALUES (1, 1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET connected = 1, auto_sync = excluded.auto_sync, "
        "revision = excluded.revision", (int(auto_sync), str(uuid4())),
    )


def set_auto_sync(connection: sqlite3.Connection, enabled: bool, expected_revision: str) -> dict:
    """
    Explicitly change startup syncing while guarding against a stale settings form.
    """
    _begin(connection)
    with connection:
        _require_revision(connection, expected_revision)
        if not feed_snapshot(connection)["configured"]:
            raise ValueError("Connect Redbark before enabling automatic syncing.")
        connection.execute(
            "UPDATE bank_feed_settings SET auto_sync = ?, revision = ? WHERE id = 1",
            (int(enabled), str(uuid4())),
        )
    return feed_snapshot(connection)


def disconnect_feed(connection: sqlite3.Connection, expected_revision: str) -> dict:
    """
    Disable this workspace's connection without deleting mappings, source ownership or accounting.
    """
    _begin(connection)
    with connection:
        _require_revision(connection, expected_revision)
        connection.execute(
            "UPDATE bank_feed_settings SET connected = 0, auto_sync = 0, revision = ? WHERE id = 1",
            (str(uuid4()),),
        )
        connection.execute("UPDATE bank_feed_accounts SET enabled = 0")
        state = feed_snapshot(connection)
    return state


# %% 2. Importing And Resolving Evidence ---------------------------------------------------------------------
def classify_feed_records(connection: sqlite3.Connection, mapping: dict, records: list[dict]) -> list[dict]:
    """
    Compare a validated account observation with saved IDs and possible handover duplicates.
    """
    saved = [item for item in load_transactions(connection) if str(item.account_id) == mapping["account_id"]]
    links = {
        row[0]: (row[1], json.loads(row[2])) for row in connection.execute(
            "SELECT external_transaction_id, transaction_id, accepted_payload "
            "FROM bank_feed_transactions WHERE external_account_id = ?", (mapping["external_account_id"],),
        )
    } if _has_tables(connection) else {}
    existing_conflicts = {
        row[0]: (row[1], json.loads(row[2])) for row in connection.execute(
            "SELECT external_transaction_id, kind, payload FROM bank_feed_conflicts "
            "WHERE external_account_id = ?", (mapping["external_account_id"],),
        )
    } if _has_tables(connection) else {}
    handover = date.fromisoformat(mapping["handover_date"])
    previous_feed = feed_snapshot(connection)["provenance"]
    seen = {}
    result = []
    for payload in records:
        item = transaction_from_feed(mapping, payload)
        external_id = payload["id"]
        signature = _economic_facts(payload)
        if external_id in seen:
            if seen[external_id] != signature:
                raise ValueError("Redbark returned contradictory versions of one transaction.")
            continue
        seen[external_id] = signature
        if external_id in links:
            linked_id, accepted = links[external_id]
            kind = "existing" if _economic_facts(accepted) == signature else "changed"
            result.append({"kind": kind, "payload": payload, "candidates": [linked_id]})
            continue
        if item.date < handover:
            result.append({"kind": "ignored", "payload": payload, "candidates": []})
            continue
        purchase = date.fromisoformat(payload["date"])
        candidates = [
            str(other.transaction_id) for other in saved if other.amount == item.amount and (
                (other.date == item.date and _normalise(other.description) == _normalise(item.description))
                or (purchase < handover <= item.date and purchase <= other.date < handover)
                or (
                    str(other.transaction_id) in previous_feed
                    and _dates_overlap(purchase, item.date, other, previous_feed[str(other.transaction_id)])
                )
            )
        ]
        previous = existing_conflicts.get(external_id)
        if previous and _economic_facts(previous[1]) == signature and previous[0] == "duplicate":
            kind = "duplicate"
        else:
            kind = "duplicate" if candidates else "new"
        result.append({"kind": kind, "payload": payload, "candidates": candidates})
    return result


def apply_feed_records(
    connection: sqlite3.Connection, mapping: dict, records: list[dict], *, checked_through: date,
) -> dict:
    """
    Add new evidence and conflicts within the caller's transaction, leaving every existing source unchanged.
    """
    outcomes = classify_feed_records(connection, mapping, records)
    counts = {"new_count": 0, "existing_count": 0, "conflict_count": 0, "ignored_count": 0}
    for outcome in outcomes:
        kind, payload = outcome["kind"], outcome["payload"]
        if kind in {"duplicate", "changed"}:
            counts["conflict_count"] += 1
            _save_conflict(connection, mapping, outcome)
        elif kind == "new":
            item = transaction_from_feed(mapping, payload)
            _insert_transaction(connection, item)
            _link_transaction(connection, mapping, payload, str(item.transaction_id))
            counts["new_count"] += 1
        elif kind == "existing":
            counts["existing_count"] += 1
            connection.execute(
                "DELETE FROM bank_feed_conflicts WHERE external_account_id = ? "
                "AND external_transaction_id = ?", (mapping["external_account_id"], payload["id"]),
            )
        else:
            counts["ignored_count"] += 1
    unresolved = connection.execute(
        "SELECT COUNT(*) FROM bank_feed_conflicts WHERE external_account_id = ?",
        (mapping["external_account_id"],),
    ).fetchone()[0]
    connection.execute(
        "UPDATE bank_feed_accounts SET last_success = ?, checked_through = ?, error = ? "
        "WHERE external_account_id = ?",
        (_now(), checked_through.isoformat(),
         f"{unresolved} import conflict(s) need Review." if unresolved else "",
         mapping["external_account_id"]),
    )
    return {"external_account_id": mapping["external_account_id"], "name": mapping["name"], **counts}


def resolve_feed_conflict(
    connection: sqlite3.Connection, conflict_id: str, revision: str, action: str,
    transaction_id: str | None = None,
) -> dict:
    """
    Resolve an explicitly selected observation without rewriting source facts or journal entries.

    Link confirms an existing source; import confirms a distinct event. Keep acknowledges changed provider
    facts while retaining the saved source. A stale conflict rejects the complete decision.
    """
    _begin(connection)
    with connection:
        state = feed_snapshot(connection)
        conflict = next((item for item in state["conflicts"] if item["id"] == conflict_id), None)
        if conflict is None or conflict["revision"] != revision:
            raise ValueError("This import conflict changed. Refresh Review before resolving it.")
        mapping = next(item for item in state["mappings"]
                       if item["external_account_id"] == conflict["external_account_id"])
        payload = conflict["payload"]
        if conflict["kind"] == "changed":
            if action != "keep":
                raise ValueError(
                    "Changed bank facts cannot replace a saved transaction. Keep the saved record."
                )
            connection.execute(
                "UPDATE bank_feed_transactions SET accepted_payload = ? WHERE external_account_id = ? "
                "AND external_transaction_id = ?",
                (_json(payload), mapping["external_account_id"], payload["id"]),
            )
        elif action == "link":
            if transaction_id not in conflict["candidates"]:
                raise ValueError("Choose one of this conflict's existing transaction candidates.")
            source = next((item for item in load_transactions(connection)
                           if str(item.transaction_id) == transaction_id), None)
            observed = transaction_from_feed(mapping, payload)
            if source is None or source.account_id != observed.account_id or source.amount != observed.amount:
                raise ValueError("The chosen source no longer matches this account and signed amount.")
            _link_transaction(connection, mapping, payload, transaction_id)
        elif action == "import":
            item = transaction_from_feed(mapping, payload)
            _insert_transaction(connection, item)
            _link_transaction(connection, mapping, payload, str(item.transaction_id))
        else:
            raise ValueError("Choose an existing transaction or confirm a distinct new transaction.")
        connection.execute("DELETE FROM bank_feed_conflicts WHERE id = ?", (conflict_id,))
        remaining = connection.execute(
            "SELECT COUNT(*) FROM bank_feed_conflicts WHERE external_account_id = ?",
            (mapping["external_account_id"],),
        ).fetchone()[0]
        if not mapping["error"] or mapping["error"].endswith(" import conflict(s) need Review."):
            connection.execute(
                "UPDATE bank_feed_accounts SET error = ? WHERE external_account_id = ?",
                (f"{remaining} import conflict(s) need Review." if remaining else "",
                 mapping["external_account_id"]),
            )
    return feed_snapshot(connection)


def assert_pdf_handover(
    connection: sqlite3.Connection, account_id: UUID, transactions: list[Transaction],
) -> None:
    """
    Reject PDF rows owned by any saved Redbark handover, including after a local disconnect.
    """
    if not _has_tables(connection):
        return
    dates = [
        date.fromisoformat(row[0]) for row in connection.execute(
            "SELECT handover_date FROM bank_feed_accounts WHERE account_id = ?", (str(account_id),),
        )
    ]
    if dates and any(item.date >= min(dates) for item in transactions):
        raise ValueError(
            f"Redbark owns this account from {min(dates).isoformat()}. "
            "Import PDFs only for earlier transactions or other accounts."
        )


def transfer_evidence(connection: sqlite3.Connection) -> dict:
    """
    Return separately preserved bank wording for local transfer validation without changing descriptions.
    """
    return {
        UUID(key): value for key, value in feed_snapshot(connection)["provenance"].items()
    }


def transaction_from_feed(mapping: dict, payload: dict) -> Transaction:
    """
    Validate posted AUD source facts and construct one immutable Aurum transaction.
    """
    external_id = payload.get("id")
    if not isinstance(external_id, str) or not external_id.strip():
        raise ValueError("Redbark returned a transaction without a stable identifier.")
    if payload.get("account") != mapping["external_account_id"] or payload.get("status") != "posted":
        raise ValueError("Redbark returned a transaction for another account or an unsettled transaction.")
    amount = payload.get("amount")
    if not isinstance(amount, dict) or str(amount.get("currency", "")).lower() != "aud":
        raise ValueError("Only AUD bank transactions can be imported.")
    cents = amount.get("amount")
    if type(cents) is not int:
        raise ValueError("A bank transaction must contain an integer-cent amount.")
    direction = payload.get("direction")
    if direction not in {"debit", "credit"} or (cents and (cents < 0) != (direction == "debit")):
        raise ValueError("A bank transaction's signed amount and direction disagree.")
    purchase = date.fromisoformat(payload["date"])
    posted = date.fromisoformat(payload["post_date"]) if payload.get("post_date") else purchase
    value_date = date.fromisoformat(payload["value_date"]) if payload.get("value_date") else None
    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("A bank transaction must retain nonblank original wording.")
    for field in ("reference", "extended_description"):
        if payload.get(field) is not None and not isinstance(payload[field], str):
            raise ValueError("Redbark returned invalid bank reference text.")
    return Transaction(
        account_id=UUID(mapping["account_id"]), date=posted, effective_date=value_date,
        description=description, amount=Decimal(cents) / 100,
        source_file="redbark:" + mapping["external_account_id"],
    )


# %% 3. Helpers ----------------------------------------------------------------------------------------------
def _has_tables(connection: sqlite3.Connection) -> bool:
    """
    Recognise optional feed storage without writing during a read-only workspace refresh.
    """
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return {"bank_feed_settings", "bank_feed_accounts", "bank_feed_transactions",
            "bank_feed_conflicts"} <= tables


def _require_revision(connection: sqlite3.Connection, expected: str) -> None:
    """
    Reject settings changed since the user's connection form was opened.
    """
    if feed_snapshot(connection)["revision"] != expected:
        raise ValueError("Bank connection settings changed. Refresh and check the account links again.")


def _begin(connection: sqlite3.Connection) -> None:
    """
    Start a guarded write only when the caller has no unfinished transaction.
    """
    if connection.in_transaction:
        raise ValueError("Finish the pending database operation before changing bank feeds.")
    connection.execute("BEGIN IMMEDIATE")


def _insert_transaction(connection: sqlite3.Connection, item: Transaction) -> None:
    """
    Insert a newly accepted bank source without creating accounting postings.
    """
    connection.execute(
        "INSERT INTO transactions "
        "(transaction_id, account_id, date, effective_date, description, amount, "
        "statement_balance, source_file) "
        "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
        (str(item.transaction_id), str(item.account_id), item.date.isoformat(),
         item.effective_date.isoformat() if item.effective_date else None, item.description,
         to_sqlite_dollars(item.amount), item.source_file),
    )


def _link_transaction(
    connection: sqlite3.Connection, mapping: dict, payload: dict, transaction_id: str,
) -> None:
    """
    Bind one provider identity to its permanent source and retain the first observed facts.
    """
    connection.execute(
        "INSERT INTO bank_feed_transactions "
        "(external_account_id, external_transaction_id, transaction_id, payload, "
        "accepted_payload, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (mapping["external_account_id"], payload["id"], transaction_id,
         _json(payload), _json(payload), _now()),
    )


def _save_conflict(connection: sqlite3.Connection, mapping: dict, outcome: dict) -> None:
    """
    Preserve one unresolved observation with stable revision until its facts or candidates change.
    """
    payload = outcome["payload"]
    existing = connection.execute(
        "SELECT payload, candidates FROM bank_feed_conflicts "
        "WHERE external_account_id = ? AND external_transaction_id = ?",
        (mapping["external_account_id"], payload["id"]),
    ).fetchone()
    candidates = _json(outcome["candidates"])
    if (
        existing and _economic_facts(json.loads(existing[0])) == _economic_facts(payload)
        and existing[1] == candidates
    ):
        return
    connection.execute(
        "INSERT INTO bank_feed_conflicts "
        "(id, external_account_id, external_transaction_id, kind, payload, candidates, revision, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(external_account_id, external_transaction_id) DO UPDATE SET "
        "kind = excluded.kind, payload = excluded.payload, candidates = excluded.candidates, "
        "revision = excluded.revision",
        (str(uuid4()), mapping["external_account_id"], payload["id"], outcome["kind"], _json(payload),
         candidates, str(uuid4()), _now()),
    )


def _economic_facts(payload: dict) -> tuple:
    """
    Compare bank evidence while ignoring mutable enrichment categories and display timestamps.
    """
    return (
        payload.get("account"), payload.get("date"), payload.get("post_date"), payload.get("value_date"),
        payload.get("description"), _json(payload.get("amount")), payload.get("direction"),
        payload.get("reference"), payload.get("extended_description"),
    )


def _normalise(value: str) -> str:
    """
    Compare whitespace only when identifying a possible duplicate for explicit human review.
    """
    return " ".join(value.split())


def _dates_overlap(purchase: date, posted: date, saved: Transaction, evidence: dict) -> bool:
    """
    Flag a possible replacement provider ID even when its wording or posting date changed.
    """
    old_purchase = date.fromisoformat(evidence["date"])
    old_posted = date.fromisoformat(evidence.get("post_date") or evidence["date"])
    return max(min(purchase, posted), min(old_purchase, old_posted, saved.date)) <= min(
        max(purchase, posted), max(old_purchase, old_posted, saved.date),
    )


def _json(value: object) -> str:
    """
    Encode private evidence deterministically without serialising credentials.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    """
    Return an unambiguous UTC timestamp for local import history.
    """
    return datetime.now(UTC).isoformat()
