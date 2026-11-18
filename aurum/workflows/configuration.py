#----------------------------------------------------------------------------------------------------------------------#
#                                                        Aurum                                                         #
#                                        aurum - workflows - configuration.py                                          #
#----------------------------------------------------------------------------------------------------------------------#
"""
Let Aurum's desktop forms read and safely save the same private configuration used by the commands.

Configuration stays in YAML. Saves validate the whole workspace, retain comments and persistent identities,
and reject stale edits or changes that would break accounting history. Opening a form never writes files.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from typing import Any

from pydantic import TypeAdapter
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

# Aurum imports
from aurum.database.database import open_database
from aurum.finance.accounts import Account, AccountRegistry
from aurum.finance.budget import (
    BudgetSettings, _validate_settings as validate_budget_settings, normalise_budget_data,
)
from aurum.finance.dates import reporting_month
from aurum.finance.reports import ReportSettings, _validate_settings as validate_report_settings
from aurum.finance.securities import Security, SecurityRegistry

CONFIGURATION_MODELS = {
    "accounts": list[Account], "securities": list[Security],
    "budget": BudgetSettings, "reports": ReportSettings,
}


# %% 1. Configuration Editing --------------------------------------------------------------------------------
@dataclass(frozen=True)
class ConfigurationDocument:
    """
    Carry a form's original YAML and all configuration versions for optimistic save checks.
    """

    workspace: Path
    kind: str
    data: Any
    originals: dict[str, bytes | None]


def read_configuration(workspace: Path, kind: str) -> ConfigurationDocument:
    """
    Read editable settings without generating identities or touching the database.
    """
    if kind not in CONFIGURATION_MODELS:
        raise ValueError(f"Unknown configuration: {kind}")
    workspace = Path(workspace).resolve()
    originals = _read_originals(workspace)
    content = originals[kind]
    try:
        data = _yaml().load(content.decode("utf-8")) if content is not None else _default(kind)
    except (YAMLError, UnicodeError) as error:
        raise ValueError(f"Cannot read {kind} configuration: {error}") from None
    expected = list if kind in {"accounts", "securities"} else dict
    if not isinstance(data, expected):
        raise ValueError(f"{kind.title()} configuration must contain a {expected.__name__}.")
    if kind == "budget":
        definitions = _definitions(originals)
        definitions["budget"] = data
        accounts = AccountRegistry([Account.model_validate(item) for item in definitions["accounts"]])
        data = _normalised_budget(definitions, accounts, workspace)
    return ConfigurationDocument(workspace, kind, data, originals)


def configuration_schema(kind: str) -> dict:
    """
    Describe the actual model fields for structured forms, including nested themes and monthly targets.
    """
    return TypeAdapter(CONFIGURATION_MODELS[kind]).json_schema()


def save_configuration(document: ConfigurationDocument, data: Any) -> ConfigurationDocument:
    """
    Validate dependencies and history before atomically replacing one configuration file.

    New accounts and securities receive UUIDs only in the successful saved configuration. An external edit to
    any configuration invalidates the form's snapshot so dependencies cannot silently change underneath it.
    """
    return save_configurations({document.kind: document}, {document.kind: data})[document.kind]


def save_configurations(
    documents: dict[str, ConfigurationDocument], changes: dict[str, Any],
) -> dict[str, ConfigurationDocument]:
    """
    Validate related form changes together and restore earlier writes if the coordinated save fails.

    Every supplied document must belong to the same workspace revision. Account and budget edits can then
    change their cross-file references in one save without an invalid intermediate validation. Rollback uses
    the exact bytes written by this operation, so a concurrent external edit is never overwritten.
    """
    if not changes:
        return {}
    if set(changes) - set(documents) or set(changes) - set(CONFIGURATION_MODELS):
        raise ValueError("Every changed configuration needs its original document.")
    first = documents[next(iter(changes))]
    workspace, originals = first.workspace, first.originals
    if any(
        kind != document.kind or document.workspace != workspace or document.originals != originals
        for kind, document in documents.items()
    ):
        raise ValueError("Configuration forms use different revisions. Reload before saving your changes.")
    _check_originals(workspace, originals)
    if any(content is None for content in originals.values()):
        raise ValueError("Create the missing workspace files before saving configuration.")
    proposed = _definitions(originals)
    proposed.update(deepcopy(changes))
    prepared, models = _validate(proposed)
    contents = {}
    for kind in CONFIGURATION_MODELS:
        if kind not in changes:
            continue
        document = documents[kind]
        _protect_identities(document, prepared)
        _protect_equity_roles(document, prepared)
        try:
            _protect_history(workspace, kind, document.data, prepared[kind], models)
        except sqlite3.Error as error:
            raise ValueError(f"Could not check saved accounting history: {error}") from None
        original = document.data
        if kind == "budget":
            original = _yaml().load(originals[kind].decode("utf-8"))
        updated = _merge_yaml(deepcopy(original), prepared[kind])
        stream = io.StringIO()
        _yaml().dump(updated, stream)
        contents[kind] = stream.getvalue().encode("utf-8")
    expected, written = dict(originals), []
    try:
        for kind, content in contents.items():
            _check_originals(workspace, expected)
            if content == originals[kind]:
                continue
            _replace(workspace / "config" / f"{kind}.yaml", content, expected[kind])
            expected[kind] = content
            written.append(kind)
        _check_originals(workspace, expected)
        result = {kind: read_configuration(workspace, kind) for kind in changes}
        if any(document.originals != expected for document in result.values()):
            raise ValueError("Configuration changed while saving. Reload it and try again.")
        _check_originals(workspace, expected)
        return result
    except Exception as error:
        unrestored = []
        for kind in reversed(written):
            try:
                _replace(workspace / "config" / f"{kind}.yaml", originals[kind], contents[kind])
            except (OSError, ValueError):
                unrestored.append(kind)
        if unrestored:
            raise ValueError(
                "Configuration save did not finish. Could not restore " + ", ".join(unrestored)
                + "; reload and inspect these settings before continuing. " + str(error)
            ) from error
        raise


def initialise_workspace(workspace: Path) -> None:
    """
    Explicitly create missing setup files and the current database, leaving existing content untouched.

    The only starting accounts are the three report equity roles. Add bank and category accounts in Setup;
    starting amounts are balanced manual entries, never configuration balances.
    """
    workspace = Path(workspace).resolve()
    originals = _read_originals(workspace)
    # Partial workspaces can add missing files before the user completes cross-file account references.
    # Existing files are never rewritten here, including account catalogues with omitted UUIDs.
    prepared, _ = _validate(_definitions(originals), check_references=False)
    _check_originals(workspace, originals)
    for kind, content in originals.items():
        if content is not None:
            continue
        stream = io.StringIO()
        stream.write(f"# Aurum - {kind.replace('_', ' ').title()}\n\n")
        _yaml().dump(prepared[kind], stream)
        _replace(workspace / "config" / f"{kind}.yaml", stream.getvalue().encode("utf-8"), None)
    connection = open_database(workspace / "data" / "aurum.sqlite3")
    connection.close()
    for name in ("bank_statements", "reports"):
        (workspace / "data" / name).mkdir(parents=True, exist_ok=True)


def account_choices(workspace: Path) -> dict[str, str]:
    """
    Supply readable account labels without loading or saving generated identities.
    """
    document = read_configuration(workspace, "accounts")
    return {item["key"]: item["name"] for item in document.data if item.get("key") and item.get("name")}


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _yaml() -> YAML:
    """
    Keep the user's quotes, comments and field ordering when updating YAML values.
    """
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.allow_duplicate_keys = False
    yaml.width = 110
    return yaml


def _default(kind: str) -> Any:
    """
    Provide small public defaults without copying private financial configuration.
    """
    if kind == "accounts":
        return [
            {"key": key, "name": name, "account_type": "equity"}
            for key, name in (
                ("capital", "Capital"), ("retained_earnings", "Retained Earnings"),
                ("unrealised_capital_gains", "Unrealised Capital Gains"),
            )
        ]
    if kind == "budget":
        year, month = reporting_month(date.today())
        return {"monthly_plans_from": f"{year:04d}-{month:02d}"}
    return [] if kind == "securities" else {}


def _read_originals(workspace: Path) -> dict[str, bytes | None]:
    """
    Record the exact bytes needed to detect another editor's changes.
    """
    result = {}
    for kind in CONFIGURATION_MODELS:
        path = workspace / "config" / f"{kind}.yaml"
        result[kind] = path.read_bytes() if path.exists() else None
    return result


def _check_originals(workspace: Path, originals: dict[str, bytes | None]) -> None:
    """
    Reject stale forms rather than discarding changes made by another editor.
    """
    if _read_originals(workspace) != originals:
        raise ValueError("Configuration changed outside this form. Reload before saving your changes.")


def _definitions(originals: dict[str, bytes | None]) -> dict:
    """
    Parse each configuration with a visible file-specific error instead of leaking parser exceptions.
    """
    result = {}
    for kind, content in originals.items():
        try:
            result[kind] = _yaml().load(content.decode("utf-8")) if content is not None else _default(kind)
        except (YAMLError, UnicodeError) as error:
            raise ValueError(f"Cannot read {kind} configuration: {error}") from None
    return result


def _validate(definitions: dict, *, check_references: bool = True) -> tuple[dict, dict]:
    """
    Validate complete models and their cross-file dependencies before any save is attempted.
    """
    prepared = deepcopy(definitions)
    for kind, model in (("accounts", Account), ("securities", Security)):
        items = prepared[kind]
        if not isinstance(items, list):
            raise ValueError(f"{kind.title()} must contain a list of definitions")
        keys = {item.get("key") for item in items if isinstance(item, dict) and item.get("key")}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"Every {kind} item must contain named fields")
            if not item.get("key"):
                base = re.sub(r"[^a-z0-9]+", "_", str(item.get("name", "")).lower()).strip("_")
                if not base:
                    raise ValueError("Enter a name before saving a new definition")
                key, suffix = base, 2
                while key in keys:
                    key, suffix = f"{base}_{suffix}", suffix + 1
                item["key"] = key
                keys.add(key)
            if item.get("id") is None:
                item.pop("id", None)
            validated = model.model_validate(item)
            item["id"] = str(validated.id)
            if kind == "accounts" and validated.closing_date:
                item["closing_date"] = validated.closing_date.isoformat()
    accounts = AccountRegistry([Account.model_validate(item) for item in prepared["accounts"]])
    securities = SecurityRegistry([Security.model_validate(item) for item in prepared["securities"]])
    bank_ids = [
        account.bank_details.full_details for account in accounts.accounts.values()
        if account.bank_details is not None
    ]
    if len(bank_ids) != len(set(bank_ids)):
        raise ValueError("Multiple accounts use the same bank identifiers")
    reports = ReportSettings.model_validate(prepared["reports"])
    prepared["budget"] = _normalised_budget(prepared, accounts)
    budget = BudgetSettings.model_validate(prepared["budget"])
    if check_references:
        validate_budget_settings(budget, accounts)
        validate_report_settings(reports, accounts)
    return prepared, {"accounts": accounts, "securities": securities, "budget": budget, "reports": reports}


def _normalised_budget(definitions: dict, accounts: AccountRegistry, workspace: Path | None = None) -> dict:
    """
    Translate retired plans in memory and seed roles only from explicit investment evidence.

    Existing role selections, including empty lists, always win. Reading never rewrites configuration
    or saved journal labels; those labels are retained solely as historical data.
    """
    reports = definitions.get("reports") or {}
    investments = set((reports.get("account_investments") or {}).keys())
    if workspace is not None:
        database = workspace / "data" / "aurum.sqlite3"
        if database.exists():
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
                present = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'journal'"
                ).fetchone()
                if present:
                    holders = {row[0] for row in connection.execute(
                        "SELECT DISTINCT account_id FROM journal WHERE status = 'posted' "
                        "AND security_id IS NOT NULL"
                    )}
                    investments.update(account.key for account in accounts.accounts.values()
                                       if str(account.id) in holders)
    return normalise_budget_data(
        definitions.get("budget") or {}, accounts, investment_account_keys=investments,
        capital_account=reports.get("capital_account", "capital"),
        unrealised_gains_account=reports.get("unrealised_gains_account", "unrealised_capital_gains"),
    )


def _protect_identities(document: ConfigurationDocument, proposed: dict) -> None:
    """
    Keep the existing key and UUID pair stable even if an item has not yet been used.
    """
    if document.kind not in {"accounts", "securities"}:
        return
    old = {str(item["id"]): item for item in document.data if item.get("id")}
    new = {str(item["id"]): item for item in proposed[document.kind]}
    old_keys = {item["key"]: identifier for identifier, item in old.items()}
    for identifier, item in new.items():
        if identifier in old and old[identifier]["key"] != item["key"]:
            raise ValueError("Existing keys are stable. Edit the display name instead.")
        if item["key"] in old_keys and old_keys[item["key"]] != identifier:
            raise ValueError("An existing definition's UUID cannot be replaced.")


def _protect_equity_roles(document: ConfigurationDocument, proposed: dict) -> None:
    """
    Keep the desktop's three system equity accounts fixed while retaining legacy keys and display names.
    """
    if document.kind not in {"accounts", "reports"}:
        return
    original = _definitions(document.originals)
    reports = ReportSettings.model_validate(original["reports"])
    fields = ("capital_account", "retained_earnings_account", "unrealised_gains_account")
    if document.kind == "reports":
        updated = ReportSettings.model_validate(proposed["reports"])
        if any(getattr(reports, field) != getattr(updated, field) for field in fields):
            raise ValueError("Aurum's system equity accounts are fixed and cannot be reassigned in Setup.")
        return
    old = {item["key"]: item for item in original["accounts"]}
    new = {item["key"]: item for item in proposed["accounts"]}
    for field in fields:
        key = getattr(reports, field)
        if key not in old:
            continue
        if key not in new:
            raise ValueError(f"{old[key]['name']} is a system equity account and cannot be removed.")
        protected = ("name", "account_type", "closing_date", "bank_details")
        before, after = Account.model_validate(old[key]), Account.model_validate(new[key])
        if any(getattr(before, item) != getattr(after, item) for item in protected):
            raise ValueError(f"{old[key]['name']} is a system equity account; its accounting details are fixed.")


def _protect_history(workspace: Path, kind: str, previous: Any, proposed: Any, models: dict) -> None:
    """
    Protect live references while retaining completed processing snapshots as historical evidence.
    """
    path = workspace / "data" / "aurum.sqlite3"
    if not path.exists():
        return
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        references = set()
        live_references = set()
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        table_names = {row[0] for row in tables}
        for (table,) in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            cursor = connection.execute(f"SELECT * FROM {quoted}")
            columns = [column[0] for column in cursor.description]
            for row in cursor:
                completed = (
                    table in {"manual_entries", "review_actions"} and "status" in columns
                    and row[columns.index("status")] in {"applied", "superseded", "withdrawn"}
                )
                for value in row:
                    _collect_references(value, references)
                    if not completed:
                        _collect_references(value, live_references)
        if kind in {"accounts", "securities"}:
            new = {str(item["id"]): item for item in proposed}
            old = {str(item.get("id", "")): item for item in previous}
            identity_column = "account_id" if kind == "accounts" else "security_id"
            identity_tables = (
                {"transactions", "journal", "bank_feed_accounts"} if kind == "accounts" else {"journal"}
            )
            for table in identity_tables & table_names:
                stored = connection.execute(
                    f'SELECT DISTINCT {identity_column} FROM "{table}" WHERE {identity_column} IS NOT NULL'
                )
                missing = {str(identifier) for (identifier,) in stored} - new.keys()
                removed = sorted(missing & old.keys(), key=lambda identifier: old[identifier]["name"])
                if removed:
                    names = ", ".join(old[identifier]["name"] for identifier in removed)
                    guidance = (
                        "Set its closing date instead to retain its accounting history."
                        if kind == "accounts" and len(removed) == 1 else
                        "Set their closing dates instead to retain their accounting history."
                        if kind == "accounts" else "Keep the original security definitions to retain history."
                    )
                    raise ValueError(f"Cannot delete {names}: used by saved accounting. {guidance}")
                if missing:
                    raise ValueError(
                        f"Saved accounting refers to missing {kind} UUIDs. Restore the original definitions "
                        "and IDs before saving; replacing their identities would break history."
                    )
            protected = ("account_type", "bank_details") if kind == "accounts" else (
                "security_type", "symbol", "market", "currency",
            )
            for item in previous:
                identifier = str(item.get("id", ""))
                if identifier not in references:
                    continue
                if identifier not in new:
                    if identifier in live_references:
                        raise ValueError(
                            f"{item['name']} is used by saved work and cannot be removed. "
                            "Resolve that work or retain the definition."
                        )
                    continue
                for field in protected:
                    if item.get(field) != new[identifier].get(field):
                        label = field.replace("_", " ")
                        raise ValueError(f"{item['name']}: {label} is used by saved history.")
        if kind == "reports" and any(row[0] == "journal" for row in tables):
            for key in models["reports"].account_investments:
                account = models["accounts"].accounts[key]
                if connection.execute(
                    "SELECT 1 FROM journal WHERE status = 'posted' AND account_id = ? "
                    "AND security_id IS NOT NULL LIMIT 1", (str(account.id),),
                ).fetchone():
                    raise ValueError(f"{account.name} holds securities; do not also count the whole account.")
    finally:
        connection.close()


def _collect_references(value: Any, references: set[str]) -> None:
    """
    Extract exact scalar references from storage and nested JSON, without substring guesses.
    """
    if isinstance(value, str):
        references.add(value)
        if value.startswith(("{", "[")):
            try:
                _collect_references(json.loads(value), references)
            except ValueError:
                pass
    elif isinstance(value, dict):
        for key, item in value.items():
            references.add(str(key))
            _collect_references(item, references)
    elif isinstance(value, list):
        for item in value:
            _collect_references(item, references)


def _merge_yaml(original: Any, proposed: Any) -> Any:
    """
    Update existing round-trip nodes in place so unchanged fields keep comments and quotes.
    """
    if isinstance(proposed, dict):
        result = original if isinstance(original, dict) else CommentedMap()
        for key in list(result):
            if key not in proposed:
                del result[key]
        for key, value in proposed.items():
            result[key] = _merge_yaml(result.get(key), value)
        if isinstance(result, CommentedMap):
            for key in proposed:
                result.move_to_end(key)
        return result
    if isinstance(proposed, list):
        result = original if isinstance(original, list) else CommentedSeq()
        by_id = {str(item.get("id", item.get("key"))): item for item in result if isinstance(item, dict)}
        values = []
        for index, value in enumerate(proposed):
            prior = result[index] if index < len(result) else None
            if isinstance(value, dict) and ("id" in value or "key" in value):
                prior = by_id.get(str(value.get("id", value.get("key"))))
            values.append(_merge_yaml(prior, value))
        result[:] = values
        return result
    if isinstance(original, str) and isinstance(proposed, str):
        return type(original)(proposed)
    if isinstance(proposed, date):
        return proposed.isoformat()
    return proposed


def _replace(path: Path, content: bytes, original: bytes | None) -> None:
    """
    Atomically write one validated file with private defaults and a final external-change check.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        current = path.read_bytes() if path.exists() else None
        if current != original:
            raise ValueError("Configuration changed while saving. Reload it and try again.")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
