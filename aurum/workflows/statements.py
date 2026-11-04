# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                Workflows - Statements                                                #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Bring selected statement PDFs into Aurum through one shared desktop and command-line workflow.

Explicit staging validates PDFs before placing them in the managed statement folder. Importing remains a
separate action, with successful content hashes checked before parsing. External originals stay unchanged;
managed replacements are atomic. Independent failures leave the remaining files available to complete.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from contextlib import closing
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import sqlite3
from tempfile import NamedTemporaryFile
from unicodedata import normalize

from pdfplumber.utils.exceptions import PdfminerException

# Aurum imports
from aurum.database.database import open_database
from aurum.database.transactions import is_imported, save_import, save_managed_import
from aurum.finance.accounts import AccountRegistry
from aurum.workflows.pdf_importer import ParsedStatement, parse_anz_statement, read_anz_document_metadata


# %% 1. Statement Files --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ImportResult:
    """
    Describe one selected PDF's outcome without requiring callers to interpret terminal output.
    """

    path: Path
    status: str
    parsed: int = 0
    inserted: int = 0
    message: str = ""


@dataclass(frozen=True)
class StageResult:
    """
    Link a selected source to its managed PDF, or retain the source and error when staging fails.
    """

    source_path: Path
    path: Path
    status: str
    message: str = ""


def stage_statements(
    paths: Iterable[Path | str],
    accounts: AccountRegistry,
    managed_folder: Path,
    *,
    progress: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    prepared_statements: MutableMapping[str, ParsedStatement] | None = None,
    expected_hashes: Mapping[Path, str] | None = None,
    expected_destinations: Mapping[Path, str | None] | None = None,
) -> list[StageResult]:
    """
    Validate and retain selected PDFs without opening a database or importing transactions.

    Validate the whole selection before writing so conflicting versions leave every candidate unchanged.
    Results follow the attempted prefix of unique resolved sources; cancellation retains unattempted paths.
    A different PDF cannot replace another selected source in the same batch. An optional in-memory cache
    retains staged validation by content hash. Rename previews may supply source and destination hashes
    to reject files changed since confirmation.
    """
    emit = progress or (lambda message: None)
    selected = list(dict.fromkeys(Path(path).resolve() for path in paths))
    folder = Path(managed_folder).resolve()
    protected = set(selected)
    validated = {}
    failures = {}
    destinations = {}
    # Validate the whole selection first so colliding candidates are retained, regardless of file order.
    for index, path in enumerate(selected, 1):
        if cancelled and cancelled():
            emit("Stopped before preparation; selected PDFs have not been changed.")
            return []
        emit(f"Checking PDF {index} of {len(selected)}: {path.name}")
        try:
            if path.suffix.lower() != ".pdf":
                raise ValueError("Choose a PDF statement.")
            contents = path.read_bytes()
            source_hash = hashlib.sha256(contents).hexdigest()
            if expected_hashes is not None and expected_hashes.get(path) != source_hash:
                raise ValueError("The selected PDF changed after the rename preview; preview it again.")
            statement = parse_anz_statement(path, accounts, contents=contents)
            destination = statement_destination(statement, accounts, folder)
            validated[path] = (contents, statement)
            destinations.setdefault(destination, []).append((path, source_hash))
        except (OSError, ValueError, PdfminerException) as error:
            failures[path] = str(error)
    for destination, candidates in destinations.items():
        if len({digest for _, digest in candidates}) > 1:
            for path, _ in candidates:
                failures[path] = (
                    f"Multiple selected PDFs would replace {destination.name} with different contents. "
                    "Choose one version and prepare it separately."
                )
    results = []
    for path in selected:
        if cancelled and cancelled():
            emit("Stopped between files; prepared PDFs are kept and nothing has been imported.")
            break
        if path in failures:
            results.append(StageResult(path, path, "failed", failures[path]))
            continue
        contents, statement = validated[path]
        try:
            if path.read_bytes() != contents:
                raise ValueError("The selected PDF changed while being checked; select it again.")
            result = _stage_statement(
                path, contents, statement, accounts, folder, protected,
                expected_destinations=expected_destinations,
            )
            results.append(result)
            protected.add(result.path)
            if prepared_statements is not None:
                prepared_statements[hashlib.sha256(contents).hexdigest()] = statement
        except (OSError, ValueError, PdfminerException) as error:
            results.append(StageResult(path, path, "failed", str(error)))
    return results


def import_statements(
    paths: Iterable[Path | str],
    accounts: AccountRegistry,
    database_path: Path,
    *,
    managed_folder: Path | None = None,
    progress: Callable[[str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    prepared_statements: Mapping[str, ParsedStatement] | None = None,
) -> list[ImportResult]:
    """
    Import each PDF independently and stop between files when cancellation is requested.

    managed_folder uses the same validated names and replacements as staging. Leaving it unset preserves
    command-line imports in place. A successful hash never triggers parsing again. A matching staged
    validation can reconcile legacy source filenames during this explicit import, without duplicate rows.
    """
    emit = progress or (lambda message: None)
    results = []
    selected = list(dict.fromkeys(Path(path).resolve() for path in paths))
    protected = set(selected)
    folder = Path(managed_folder).resolve() if managed_folder is not None else None
    with closing(open_database(database_path)) as connection:
        for index, path in enumerate(selected, 1):
            if cancelled and cancelled():
                emit("Stopped between files; completed imports are saved.")
                break
            emit(f"Checking statement {index} of {len(selected)}: {path.name}")
            try:
                if path.suffix.lower() != ".pdf":
                    raise ValueError("Choose a PDF statement.")
                contents = path.read_bytes()
                source_hash = hashlib.sha256(contents).hexdigest()
                known = is_imported(connection, source_hash)
                prepared = (prepared_statements or {}).get(source_hash) if folder is not None else None
                if known and prepared is None:
                    message = "Already imported."
                    if folder is not None:
                        message += " Select the PDF again to validate and reconcile its source filename."
                    results.append(ImportResult(path, "skipped", message=message))
                    continue
                if folder is None:
                    stored = path
                    statement = parse_anz_statement(path, accounts, contents=contents)
                    transactions = list(statement.transactions)
                    inserted = save_import(
                        connection, stored, source_hash, transactions,
                        cumulative_period=(statement.start_date, statement.end_date),
                    )
                    changed, ambiguous = 0, 0
                else:
                    statement = (
                        _current_statement(prepared, accounts) if prepared is not None
                        else parse_anz_statement(path, accounts, contents=contents)
                    )
                    destination = statement_destination(statement, accounts, folder)
                    previous = destination.read_bytes() if destination.exists() else None
                    staged = _stage_statement(
                        path, contents, statement, accounts, folder, protected, rename_source=False,
                    )
                    stored = staged.path
                    transactions = [
                        transaction.model_copy(update={"source_file": stored.stem})
                        for transaction in statement.transactions
                    ]
                    try:
                        inserted, changed, ambiguous = save_managed_import(
                            connection, stored, source_hash, transactions,
                            cumulative_period=(statement.start_date, statement.end_date),
                        )
                    except (OSError, ValueError, sqlite3.Error):
                        _restore_statement(stored, contents, previous)
                        raise
                    protected.add(stored)
                messages = ["Already imported."] if known else []
                if changed:
                    messages.append("Saved source filenames reconciled.")
                if ambiguous:
                    messages.append(f"Kept {ambiguous} ambiguous repeated-row source labels unchanged.")
                if folder:
                    warning = _remove_managed_source(path, stored, contents, folder)
                    if warning:
                        messages.append(warning)
                results.append(ImportResult(
                    stored, "skipped" if known else "imported", len(transactions), inserted,
                    " ".join(messages),
                ))
            except (OSError, ValueError, PdfminerException, sqlite3.Error) as error:
                results.append(ImportResult(path, "failed", message=str(error)))
    return results


def statement_destination(statement: ParsedStatement, accounts: AccountRegistry, folder: Path) -> Path:
    """
    Name statements and cumulative lists separately, rejecting colliding or unsafe account slugs.
    """
    slug = _account_slug(statement.account.key)
    if not slug.strip() or any(character in "/\\\0" or ord(character) < 32 for character in slug):
        raise ValueError("The matched account key cannot be used as a statement filename.")
    filename_slug = slug + ("_list" if statement.document_kind == "transaction_list" else "")
    slug_identity = normalize("NFC", filename_slug).casefold()
    collisions = [
        account.key for account in accounts.accounts.values()
        if account.bank_details is not None and account.id != statement.account.id
        and slug_identity in {
            normalize("NFC", _account_slug(account.key) + suffix).casefold() for suffix in ("", "_list")
        }
    ]
    if collisions:
        raise ValueError(
            f"Statement filename {filename_slug!r} matches more than one bank account: "
            + ", ".join((statement.account.key, *collisions))
        )
    destination = Path(folder) / f"{statement.reporting_month}_{filename_slug}.pdf"
    if destination.is_symlink():
        raise ValueError("The managed statement destination must be a regular file, not a symbolic link.")
    return destination


def statement_completion(
    month: str, accounts: AccountRegistry, folder: Path, imports: list[dict], *,
    metadata_cache: MutableMapping[str, ParsedStatement] | None = None,
) -> dict:
    """
    Check the current month's source PDFs for Overview without changing accounting or storing document flags.

    An absent account PDF is considered only when its canonical name already appears in successful imports.
    This check does not claim that every expected account or transaction has been supplied.
    A validated, imported final statement supersedes its retained cumulative list for completion.
    """
    known_hashes = {item["source_hash"] for item in imports}
    imported_names = {item["source_file"] for item in imports}
    awaiting_final = awaiting_import = unavailable = False
    folder = Path(folder)
    for account in accounts.accounts.values():
        if account.bank_details is None:
            continue
        stem = f"{month}_{_account_slug(account.key)}"
        names = [name for name in (stem, stem + "_list") if (folder / (name + ".pdf")).exists()]
        if not names and imported_names.intersection({stem, stem + "_list"}):
            unavailable = True
        account_final = False
        account_list = account_unimported = account_unavailable = False
        for name in names:
            path = folder / (name + ".pdf")
            try:
                if path.is_symlink():
                    raise ValueError("Expected a regular managed statement PDF.")
                contents = path.read_bytes()
                digest = hashlib.sha256(contents).hexdigest()
                metadata = (metadata_cache or {}).get(digest)
                metadata = _current_statement(metadata, accounts) if metadata is not None else (
                    read_anz_document_metadata(path, accounts, contents=contents)
                )
                if metadata.account.id != account.id or metadata.reporting_month != month:
                    raise ValueError("The PDF does not match its managed month and account.")
                if metadata_cache is not None:
                    metadata_cache[digest] = metadata
                if name == stem and metadata.document_kind == "statement" and digest in known_hashes:
                    account_final = True
                    break
                account_list |= metadata.document_kind == "transaction_list"
                account_unimported |= digest not in known_hashes
            except (OSError, ValueError, PdfminerException):
                account_unavailable = True
        if not account_final:
            awaiting_final |= account_list
            awaiting_import |= account_unimported
            unavailable |= account_unavailable
    message = (
        "Restore the month's statement PDFs, then refresh." if unavailable else
        "Import the updated statement before completing this month." if awaiting_import else
        "Your saved work is processed. The final bank statement is still awaited." if awaiting_final else ""
    )
    status = (
        "unavailable" if unavailable else "unimported" if awaiting_import else
        "partial" if awaiting_final else "complete"
    )
    return {"pending": status != "complete", "status": status, "message": message}


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _current_statement(statement: ParsedStatement, accounts: AccountRegistry) -> ParsedStatement:
    """
    Reuse staged facts only while the matched account identity and bank details remain configured.
    """
    current = next((item for item in accounts.accounts.values() if item.id == statement.account.id), None)
    if (
        current is None or current.bank_details is None
        or current.bank_details != statement.account.bank_details
    ):
        raise ValueError(
            "Account details changed after this PDF was prepared; select it again before importing."
        )
    matches = [item for item in accounts.accounts.values() if item.bank_details == current.bank_details]
    if len(matches) != 1:
        raise ValueError("The prepared PDF no longer matches exactly one configured bank account.")
    return replace(statement, account=current)


def _stage_statement(
    source: Path,
    contents: bytes,
    statement: ParsedStatement,
    accounts: AccountRegistry,
    folder: Path,
    protected: set[Path],
    *,
    rename_source: bool = True,
    expected_destinations: Mapping[Path, str | None] | None = None,
) -> StageResult:
    """
    Retain a validated snapshot under its account-and-period name without replacing another selected source.
    """
    destination = statement_destination(statement, accounts, folder)
    if destination.is_symlink():
        raise ValueError("The managed statement destination must be a regular file, not a symbolic link.")
    existing = destination.read_bytes() if destination.exists() else None
    if expected_destinations is not None and (
        destination not in expected_destinations
        or expected_destinations[destination] != (
            hashlib.sha256(existing).hexdigest() if existing is not None else None
        )
    ):
        raise ValueError("The destination changed after the rename preview; preview it again.")
    if existing is not None and existing != contents and statement.document_kind == "transaction_list":
        previous = read_anz_document_metadata(destination, accounts, contents=existing)
        if previous.document_kind == "statement":
            raise ValueError("A transaction list cannot replace a final statement. Keep the final PDF.")
        if (
            previous.account.id != statement.account.id
            or previous.reporting_month != statement.reporting_month
        ):
            raise ValueError("The managed PDF belongs to another account or reporting month; check its name.")
        if previous.generated_at is not None and (
            statement.generated_at is None or statement.generated_at <= previous.generated_at
        ):
            raise ValueError(
                "This transaction list is not newer than the managed PDF; the current file was kept."
            )
    same_source = source == destination or (existing is not None and source.samefile(destination))
    if same_source and existing != contents:
        raise ValueError("The source PDF changed while being checked; select it again.")
    other_source = any(
        path != source and path.parent == folder
        and normalize("NFC", path.name).casefold() == normalize("NFC", destination.name).casefold()
        for path in protected
    )
    if other_source and existing != contents:
        raise ValueError(
            f"{destination.name} is already used by another selected PDF with different contents. "
            "Prepare these PDFs separately to choose which file to keep."
        )
    rename_alias = source.parent == folder and source != destination and same_source
    changed = existing != contents or rename_alias
    if changed:
        _replace_statement(destination, contents)
    message = (
        f"Replaced {destination.name}." if changed and existing is not None
        else f"Saved as {destination.name}." if changed else "Already in the statement folder."
    )
    if rename_source and source.parent == folder and source != destination:
        warning = _remove_managed_source(source, destination, contents, folder)
        if warning:
            message += " " + warning
        else:
            changed = True
            message = f"Renamed to {destination.name}."
    return StageResult(source, destination, "staged" if changed else "unchanged", message)


def _restore_statement(destination: Path, contents: bytes, previous: bytes | None) -> None:
    """
    Undo this import's file replacement after a failed database save without overwriting later file changes.
    """
    if previous == contents:
        return
    if destination.read_bytes() != contents:
        raise ValueError("Import failed and the managed PDF changed externally; its current bytes were kept.")
    if previous is None:
        destination.unlink()
    else:
        _replace_statement(destination, previous)


def _account_slug(key: str) -> str:
    """
    Match existing ANZ statement names while retaining every other account key unchanged.
    """
    for prefix in ("anz_plus_", "anz_"):
        if key.startswith(prefix):
            return key[len(prefix):]
    return key


def _remove_managed_source(source: Path, destination: Path, contents: bytes, folder: Path) -> str:
    """
    Finish an internal rename only after replacement succeeds, retaining changed or unreadable originals.
    """
    if source.parent != folder or source == destination:
        return ""
    try:
        if source.read_bytes() != contents:
            return "The original managed file changed while being checked and was kept."
        if source.samefile(destination):
            source.rename(destination)
            return ""
        source.unlink()
    except OSError as error:
        return f"The original managed filename was kept: {error}"
    return ""


def _replace_statement(destination: Path, contents: bytes) -> None:
    """
    Replace one managed PDF atomically from a complete validated byte snapshot, without keeping a backup.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="wb", dir=destination.parent, prefix=".aurum-statement-", suffix=".tmp", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
