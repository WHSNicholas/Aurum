# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                  Scripts - Reports                                                   #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Generate Aurum's balance sheet, income, budget and investment PDFs from the posted journal.

Run this command after importing statements and completing journal review. By default it reports the
working-day month containing the latest posted event. Use --month YYYY-MM to revisit a different month.
Statements are saved beneath data/reports/YYYY-MM/; the terminal shows progress and paths, not report tables.

Report settings control the displayed name, equity accounts and asset groups. Fill monthly budget targets
in config/budget.yaml. Market prices may be fetched and cached, but reporting never changes journal postings
or contacts ChatGPT.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import argparse
from pathlib import Path
import sqlite3
import sys

from pydantic import ValidationError

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

# Aurum imports
from aurum.database.database import DEFAULT_DATABASE
from aurum.finance.accounts import DEFAULT_ACCOUNTS
from aurum.finance.budget import DEFAULT_BUDGET_SETTINGS
from aurum.finance.securities import DEFAULT_SECURITIES
from aurum.workflows.reports import DEFAULT_REPORT_SETTINGS, DEFAULT_REPORTS, generate_reports


# %% 1. Reports ----------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    """
    Generate all four PDFs and print their locations, returning a conventional command exit code.

    Successful generation returns 0, including clearly labelled drafts. Invalid inputs or generation
    failures return 1, and interruption returns 130. Argument syntax errors use argparse's exit code 2.
    """
    parser = argparse.ArgumentParser(
        description="Generate balance sheet, income, budget and investment PDFs.",
    )
    parser.add_argument("--month", help="Reporting month YYYY-MM (default: latest posted journal month)")
    parser.add_argument("--accounts", type=Path, default=DEFAULT_ACCOUNTS, help="Account YAML configuration")
    parser.add_argument(
        "--securities", type=Path, default=DEFAULT_SECURITIES, help="Security YAML configuration",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_REPORT_SETTINGS, help="Report YAML configuration",
    )
    parser.add_argument(
        "--budget", type=Path, default=DEFAULT_BUDGET_SETTINGS, help="Budget targets and category YAML",
    )
    parser.add_argument(
        "--database", type=Path, default=DEFAULT_DATABASE, help="Existing Aurum SQLite database",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_REPORTS, help="PDF root (default: data/reports/)",
    )
    args = parser.parse_args(argv)
    print("Aurum | Financial statements", flush=True)
    try:
        result = generate_reports(
            accounts_path=args.accounts, securities_path=args.securities, settings_path=args.config,
            budget_path=args.budget, database_path=args.database, output_dir=args.output, month=args.month,
            progress=lambda stage: print(stage, flush=True),
        )
    except KeyboardInterrupt:
        print("\nReport generation interrupted.", file=sys.stderr)
        return 130
    except (OSError, ValueError, sqlite3.Error, ImportError, RuntimeError) as error:
        if isinstance(error, ValidationError):
            details = [
                ".".join(map(str, item["loc"])) + ": " + item["msg"]
                for item in error.errors(include_input=False)[:3]
            ]
            message = "; ".join(details)
        else:
            message = str(error)
        print(f"Reports could not be generated: {message}", file=sys.stderr)
        return 1

    statements = (
        result.reports.balance_sheet, result.reports.income_statement,
        result.reports.budget_report, result.reports.investment_report,
    )
    for label, statement, path in zip(
        ("Balance sheet", "Income statement", "Budget", "Investments"), statements, result.paths,
    ):
        status = "Draft" if statement.draft_reasons else "Generated"
        print(f"{status} {label.lower()}: {path}")
    reasons = dict.fromkeys(reason for statement in statements for reason in statement.draft_reasons)
    for reason in reasons:
        print(f"Draft: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
