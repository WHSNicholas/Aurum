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
    for external_id, transaction_id, raw, accepted in connection.execute(
        "SELECT external_account_id, transaction_id, payload, accepted_payload FROM bank_feed_transactions"
    ):
        evidence = json.loads(raw)
        if raw != accepted and not _extended(evidence):
            baseline = json.loads(accepted)
            if not _fact_differences(evidence, baseline):
                evidence = _with_extended_description(evidence, baseline)
        evidence.update(
            source_label="Redbark · " + mappings[external_id]["name"], provider="redbark",
            external_account_id=external_id,
        )
        result["provenance"].setdefault(transaction_id, evidence)
    conflict_rows = connection.execute(
        "SELECT c.id, c.external_account_id, c.external_transaction_id, c.kind, c.payload, c.candidates, "
        "c.revision, t.accepted_payload FROM bank_feed_conflicts c LEFT JOIN bank_feed_transactions t "
        "USING (external_account_id, external_transaction_id) ORDER BY c.created_at, c.id"
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
            "differences": _fact_differences(json.loads(row[7]), payload)
            if row[3] == "changed" and row[7] else {},
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
    Identify repeated or changed observations by provider identity, accepting each unseen ID separately.

    Equal amounts, descriptions and overlapping dates do not establish that two bank records are the same.
    """
    links = {
        key: (linked, json.loads(saved), json.loads(pending) if pending else None)
        for key, linked, saved, pending in connection.execute(
            "SELECT t.external_transaction_id, t.transaction_id, t.accepted_payload, c.payload "
            "FROM bank_feed_transactions t LEFT JOIN bank_feed_conflicts c "
            "ON c.external_account_id = t.external_account_id AND c.external_transaction_id = "
            "t.external_transaction_id AND c.kind = 'changed' WHERE t.external_account_id = ?",
            (mapping["external_account_id"],),
        )
    } if _has_tables(connection) else {}
    handover = date.fromisoformat(mapping["handover_date"])
    observations = {}
    for payload in records:
        transaction_from_feed(mapping, payload)
        external_id = payload["id"]
        previous = observations.get(external_id)
        if previous is not None and _fact_differences(previous, payload):
            raise ValueError("Redbark returned contradictory versions of one transaction.")
        observations[external_id] = _with_extended_description(previous or payload, payload)

    result = []
    for external_id, payload in observations.items():
        if external_id in links:
            linked_id, accepted, pending = links[external_id]
            # A sparse repeat cannot settle a previously reported disagreement about optional wording.
            if pending and not _fact_differences(pending, payload):
                payload = _with_extended_description(payload, pending)
            if pending and not _fact_differences(accepted, pending):
                accepted = _with_extended_description(accepted, pending)
            kind = "changed" if _fact_differences(accepted, payload) else "existing"
            outcome = {"kind": kind, "payload": payload, "candidates": [linked_id]}
            if kind == "existing" and not _extended(accepted) and _extended(payload):
                outcome["enriched"] = _with_extended_description(accepted, payload)
            result.append(outcome)
        else:
            booking = date.fromisoformat(payload.get("post_date") or payload["date"])
            result.append({"kind": "ignored" if booking < handover else "new",
                           "payload": payload, "candidates": []})
    return result


def apply_feed_records(
    connection: sqlite3.Connection, mapping: dict, records: list[dict], *, checked_through: date,
) -> dict:
    """
    Add new evidence and conflicts within the caller's transaction, leaving every existing source unchanged.
    """
    account = mapping["external_account_id"]
    # Old warnings can outlive the provider's sync window. Recheck their saved evidence on account sync.
    for key, raw, candidates, linked_id, accepted in connection.execute(
        "SELECT c.external_transaction_id, c.payload, c.candidates, t.transaction_id, t.accepted_payload "
        "FROM bank_feed_conflicts c JOIN bank_feed_transactions t USING "
        "(external_account_id, external_transaction_id) WHERE c.external_account_id = ? "
        "AND c.kind = 'changed'",
        (account,),
    ).fetchall():
        if json.loads(candidates) != [linked_id]:
            continue
        incoming, saved = json.loads(raw), json.loads(accepted)
        if _fact_differences(saved, incoming):
            continue
        if not _extended(saved) and _extended(incoming):
            _save_accepted_payload(connection, account, _with_extended_description(saved, incoming))
        connection.execute(
            "DELETE FROM bank_feed_conflicts WHERE external_account_id = ? AND external_transaction_id = ?",
            (account, key),
        )
    counts = {"new_count": 0, "existing_count": 0, "conflict_count": 0, "ignored_count": 0}
    for outcome in classify_feed_records(connection, mapping, records):
        kind, payload = outcome["kind"], outcome["payload"]
        if kind == "changed":
            counts["conflict_count"] += 1
            _save_conflict(connection, mapping, outcome)
        elif kind == "new":
            item = transaction_from_feed(mapping, payload)
            _insert_transaction(connection, item)
            _link_transaction(connection, mapping, payload, str(item.transaction_id))
            counts["new_count"] += 1
        elif kind == "existing":
            if "enriched" in outcome:
                _save_accepted_payload(connection, account, outcome["enriched"])
            counts["existing_count"] += 1
        else:
            counts["ignored_count"] += 1
        if kind in {"new", "existing"}:
            # Successful imports also clear warnings retained from the former similarity check.
            connection.execute(
                "DELETE FROM bank_feed_conflicts WHERE external_account_id = ? "
                "AND external_transaction_id = ?", (mapping["external_account_id"], payload["id"]),
            )
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
) -> dict:
    """
    Resolve an explicitly selected observation without rewriting source facts or journal entries.

    Import releases a record held by the retired duplicate check. Keep acknowledges changed provider facts
    while retaining the saved source. A stale conflict rejects the complete decision.
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
            saved = connection.execute(
                "SELECT accepted_payload FROM bank_feed_transactions WHERE external_account_id = ? "
                "AND external_transaction_id = ?", (mapping["external_account_id"], payload["id"]),
            ).fetchone()
            payload = _with_extended_description(payload, json.loads(saved[0]))
            _save_accepted_payload(connection, mapping["external_account_id"], payload)
        elif action == "import":
            item = transaction_from_feed(mapping, payload)
            _insert_transaction(connection, item)
            _link_transaction(connection, mapping, payload, str(item.transaction_id))
        else:
            raise ValueError("Import this held transaction as a separate bank record.")
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
        "SELECT payload, candidates, kind FROM bank_feed_conflicts "
        "WHERE external_account_id = ? AND external_transaction_id = ?",
        (mapping["external_account_id"], payload["id"]),
    ).fetchone()
    candidates = _json(outcome["candidates"])
    if existing and existing[1] == candidates and existing[2] == outcome["kind"]:
        previous = json.loads(existing[0])
        if not _fact_differences(previous, payload) and bool(_extended(previous)) == bool(_extended(payload)):
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


def _extended(payload: dict) -> str:
    """
    Treat an omitted, null or whitespace-only optional description as absent.
    """
    return (payload.get("extended_description") or "").strip()


def _with_extended_description(primary: dict, other: dict) -> dict:
    """
    Retain available optional wording while preserving the primary observation's other facts.
    """
    if not _extended(primary) and _extended(other):
        return {**primary, "extended_description": other["extended_description"]}
    return primary


def _save_accepted_payload(connection: sqlite3.Connection, account: str, payload: dict) -> None:
    """
    Update the comparison baseline while retaining the original source and posted accounting.
    """
    connection.execute(
        "UPDATE bank_feed_transactions SET accepted_payload = ? WHERE external_account_id = ? "
        "AND external_transaction_id = ?", (_json(payload), account, payload["id"]),
    )


def _bank_facts(payload: dict) -> dict:
    """
    Name comparable provider facts and express signed cents as a readable dollar amount.
    """
    amount = payload["amount"]
    return {
        "Account": payload.get("account"), "Transaction date": payload.get("date"),
        "Booking date": payload.get("post_date"), "Effective date": payload.get("value_date"),
        "Description": payload.get("description"), "Amount": format(Decimal(amount["amount"]) / 100, ".2f"),
        "Currency": amount.get("currency"), "Direction": payload.get("direction"),
        "Reference": payload.get("reference"), "Extended description": _extended(payload),
    }


def _fact_differences(saved: dict, incoming: dict) -> dict:
    """
    Return the differing known facts and the actual values used to explain each conflict in Review.
    """
    differences = {}
    current = _bank_facts(incoming)
    for field, before in _bank_facts(saved).items():
        after = current[field]
        if field == "Extended description" and not (before and after):
            continue
        if field in {"Description", "Extended description", "Currency"}:
            if (before or "").casefold() == (after or "").casefold():
                continue
        if before != after:
            differences[field] = (before, after)
    return differences


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
