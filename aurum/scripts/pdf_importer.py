# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                Scripts - PDF Importer                                                #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Run this script to import a folder of ANZ Plus PDF statements into Aurum.

It brings together your saved account definitions, the PDF parser and SQLite storage. Each statement is
checked before any additional transactions are saved. PDFs already imported are skipped.

The original PDFs and existing transactions stay unchanged. The console reports import counts without
printing transaction details.

Run from the project root: python aurum/scripts/pdf_importer.py
Statements default to data/bank_statements/.
Optional overrides: --accounts config/accounts.yaml --database data/aurum.sqlite3
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import argparse
from contextlib import closing
import hashlib
from pathlib import Path
import shlex
import sqlite3
import sys

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Direct execution starts import searches in scripts/. Make the project root available for Aurum imports.
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from pdfplumber.utils.exceptions import PdfminerException
from pydantic import ValidationError

# Aurum imports
from aurum.database.database import DEFAULT_DATABASE, open_database
from aurum.database.transactions import is_imported, save_import
from aurum.finance.accounts import DEFAULT_ACCOUNTS, load_accounts
from aurum.workflows.pdf_importer import parse_anz_pdf

# Default paths
DEFAULT_STATEMENTS = PROJECT_ROOT / "data" / "bank_statements"


# %% 1. Importing --------------------------------------------------------------------------------------------
def main() -> None:
    """
    Run folder imports using configured accounts, exiting with status 1 if any PDF fails.

    The folder defaults to data/bank_statements/, account configuration to config/accounts.yaml, and the
    database to data/aurum.sqlite3 beneath the project root. An unchanged PDF is skipped before parsing. Each
    successful PDF is committed independently; failed files leave no partial rows and can be retried.
    """
    # Command-line arguments
    parser = argparse.ArgumentParser(description="Import ANZ Plus PDF statements into SQLite.")
    parser.add_argument(
        "folder", nargs="?", type=Path, default=DEFAULT_STATEMENTS,
        help="PDF folder (default: data/bank_statements/)"
    )
    parser.add_argument(
        "--accounts", type=Path, default=DEFAULT_ACCOUNTS,
        help="Account configuration (default: config/accounts.yaml)"
    )
    parser.add_argument(
        "--database", type=Path, default=DEFAULT_DATABASE, help="SQLite file (default: data/aurum.sqlite3)"
    )
    # Input paths
    args = parser.parse_args()
    folder = args.folder.resolve()
    database = args.database.resolve()
    command = ["python", "aurum/scripts/pdf_importer.py"]
    if args.folder != DEFAULT_STATEMENTS:
        command.append(str(args.folder))
    if args.accounts != DEFAULT_ACCOUNTS:
        command.extend(("--accounts", str(args.accounts)))
    if args.database != DEFAULT_DATABASE:
        command.extend(("--database", str(args.database)))
    print("Aurum | PDF import")
    if not folder.is_dir():
        parser.error(f"Not a folder: {folder}")
    # Statement files
    try:
        paths = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
    except OSError as error:
        parser.exit(1, f"Could not read folder: {error}\n")
    if not paths:
        print(f"No PDFs found in {folder}")
        print(f"Add statements there, then run: {shlex.join(command)}")
        return

    # Account configuration
    try:
        accounts = load_accounts(args.accounts)
    except (OSError, ValueError) as error:
        if isinstance(error, ValidationError):
            fields = [".".join(map(str, item["loc"])) for item in error.errors(include_input=False)[:3]]
            detail = "check fields " + ", ".join(fields) + "; dates accept YYYY-MM-DD or DD-MM-YYYY"
        else:
            detail = str(error)
        parser.exit(1, f"Could not load account configuration: {detail}\n")

    # PDF imports
    imported = skipped = failed = inserted = 0
    print(f"Checking {len(paths)} PDFs; unchanged files will be skipped...")
    try:
        with closing(open_database(database)) as connection:
            for path in paths:
                try:
                    # Hash and parse the same byte snapshot, even if the original file changes during import.
                    contents = path.read_bytes()
                    source_hash = hashlib.sha256(contents).hexdigest()
                    if is_imported(connection, source_hash):
                        skipped += 1
                        continue
                    transactions = parse_anz_pdf(path, accounts, contents=contents)
                    added = save_import(connection, path, source_hash, transactions)
                except (OSError, ValueError, PdfminerException, sqlite3.Error) as error:
                    failed += 1
                    print(f"{path.name}: failed: {error}", file=sys.stderr)
                    continue
                imported += 1
                inserted += added
                print(f"{path.name}: {len(transactions)} transactions read; {added} added")
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Could not open database: {error}\n")

    # Import summary
    print(f"Added {inserted} transactions; {imported} PDFs imported, {skipped} skipped, {failed} failed.")
    print(f"Database: {database}")
    if failed:
        print(f"Retry failed PDFs: {shlex.join(command)}")
        parser.exit(1)
    next_command = ["python", "aurum/scripts/journal.py"]
    if args.accounts != DEFAULT_ACCOUNTS:
        next_command.extend(("--accounts", str(args.accounts)))
    if args.database != DEFAULT_DATABASE:
        next_command.extend(("--database", str(args.database)))
    print(f"Next: {shlex.join(next_command)}")


# %% 2. Execution --------------------------------------------------------------------------------------------
if __name__ == "__main__":
    main()
