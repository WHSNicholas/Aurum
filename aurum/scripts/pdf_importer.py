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
Optional overrides: --accounts config/accounts.json --database data/aurum.sqlite3
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import argparse
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import sys

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Direct execution starts import searches in scripts/. Make the project root available for Aurum imports.
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

from pdfplumber.utils.exceptions import PdfminerException

# Aurum imports
from aurum.database.database import DEFAULT_DATABASE, is_imported, open_database, save_import
from aurum.finance.accounts import DEFAULT_ACCOUNTS, load_accounts
from aurum.importers.pdf_importer import parse_anz_pdf

# Default paths
DEFAULT_STATEMENTS = PROJECT_ROOT / "data" / "bank_statements"


# %% 1. Importing --------------------------------------------------------------------------------------------
def main() -> None:
    """
    Run folder imports using configured accounts, exiting with status 1 if any PDF fails.

    The folder defaults to data/bank_statements/, account configuration to config/accounts.json, and the
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
        help="Account configuration (default: config/accounts.json)"
    )
    parser.add_argument(
        "--database", type=Path, default=DEFAULT_DATABASE, help="SQLite file (default: data/aurum.sqlite3)"
    )
    # Input paths
    args = parser.parse_args()
    folder = args.folder.resolve()
    database = args.database.resolve()
    if not folder.is_dir():
        parser.error(f"Not a folder: {folder}")
    # Statement files
    try:
        paths = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")
    except OSError as error:
        parser.exit(1, f"Could not read folder: {error}\n")
    if not paths:
        print(f"No PDFs found in {folder}")
        return

    # Account configuration
    try:
        accounts = load_accounts(args.accounts)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Could not load account configuration: {error}\n")

    # PDF imports
    imported = skipped = failed = inserted = 0
    try:
        with closing(open_database(database)) as connection:
            for path in paths:
                try:
                    # Hash and parse the same byte snapshot, even if the original file changes during import.
                    contents = path.read_bytes()
                    source_hash = hashlib.sha256(contents).hexdigest()
                    if is_imported(connection, source_hash):
                        skipped += 1
                        print(f"{path.name}: skipped (already imported)")
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
        parser.exit(1)


# %% 2. Execution --------------------------------------------------------------------------------------------
if __name__ == "__main__":
    main()
