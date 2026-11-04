# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                    GUI - Services                                                    #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Give the desktop one workspace-aware interface to Aurum's existing workflows.

Each operation opens and closes its own database connection, allowing the window to run it in a worker.
Reading a snapshot is strictly read-only. Explicit actions save inputs, import PDFs, run accounting or
generate reports; no operation shells out to a command or derives its result from terminal output.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from contextlib import closing
from dataclasses import asdict
from datetime import date
from functools import partial
from pathlib import Path
import sqlite3

# Aurum imports
from aurum.ai.chatgpt import connect, connection_status
from aurum.database.database import _check_schema
from aurum.database.journal import load_journal_entries
from aurum.database.manual import submit_manual_input
from aurum.database.memory import remember_text
from aurum.database.prices import load_quote
from aurum.database.reviews import (
    discard_review, list_guidance_cases, load_guidance_assessments, submit_review, submit_reviews,
)
from aurum.database.savings import load_savings_allocations
from aurum.finance.accounts import load_accounts
from aurum.finance.budget import budget_transaction_counts, load_budget_settings
from aurum.finance.dates import reporting_month
from aurum.finance.investments import holdings_as_of, price_symbol
from aurum.finance.manual import ManualInput
from aurum.finance.reports import build_reports, load_report_settings, resolve_period
from aurum.finance.securities import load_securities
from aurum.gui.theme import resolve_gui_theme
from aurum.workflows.configuration import initialise_workspace
from aurum.workflows.editing import apply_journal_edit, editor_snapshot, load_journal_edit
from aurum.workflows.journal import (
    DEFAULT_MODEL, journal_snapshot, learn_from_reviews, process_journal,
)
from aurum.workflows.reports import (
    generate_reports, investment_review_items, report_filenames, resolve_output_directory,
)
from aurum.workflows.statements import import_statements, stage_statements, statement_completion


# %% 1. Desktop Operations -----------------------------------------------------------------------------------
class WorkspaceService:
    """
    Resolve every desktop operation against one explicitly selected local workspace.
    """

    def __init__(self, workspace: Path) -> None:
        """
        Retain paths without creating files or opening a database.
        """
        self.root = Path(workspace).resolve()
        self.config = self.root / "config"
        self.data = self.root / "data"
        self.database = self.data / "aurum.sqlite3"
        self._prepared_statements = {}
        self._statement_metadata = {}

    def default_month(self) -> str:
        """
        Prefer the latest posted accounting month, then the latest imported date, then the current month.
        """
        latest = None
        if self.database.is_file():
            with closing(self._connection(read_only=True)) as connection:
                row = connection.execute("SELECT MAX(date) FROM journal WHERE status = 'posted'").fetchone()
                latest = row[0] if row else None
                if not latest:
                    latest = connection.execute("SELECT MAX(date) FROM transactions").fetchone()[0]
        value = min(date.fromisoformat(latest), date.today()) if latest else date.today()
        year, month = reporting_month(value)
        return f"{year:04d}-{month:02d}"

    def snapshot(self, month: str) -> dict:
        """
        Read one consistent accounting view and safe connection metadata without altering the workspace.
        """
        period = resolve_period([], month=month)
        missing = [
            f"config/{kind}.yaml" for kind in ("accounts", "securities", "budget", "reports")
            if not (self.config / f"{kind}.yaml").is_file()
        ]
        if not self.database.is_file():
            missing.append("data/aurum.sqlite3")
        result = {
            "ready": False, "missing_files": missing, "month": month,
            "period_start": period.start_date.isoformat(), "period_end": period.end_date.isoformat(),
            "cutoff": period.cutoff.isoformat(),
            "transactions": [], "reviews": [], "manuals": [], "entries": [], "counts": {}, "imports": [],
            "investment_reviews": [], "investment_reviews_error": "",
            "owner_name": "", "financials": {}, "financial_transaction_counts": {},
            "net_worth_unpriced_holdings": [],
            "posted_entry_count": 0,
            "statement_folder": str(self.data / "bank_statements"),
            "report_paths": [str(self.data / "reports" / month / name) for name in report_filenames(month)],
        }
        try:
            result["auth"] = connection_status(self.data / "ai")
        except (OSError, ValueError, RuntimeError) as error:
            result["auth"] = {"error": str(error)}
        if missing:
            return result
        try:
            accounts, securities, budget = self._models()
            settings = load_report_settings(self.config / "reports.yaml", accounts)
            result["owner_name"] = settings.owner_name
            output = resolve_output_directory(settings, self.root)
            result["report_paths"] = [str(output / month / name) for name in report_filenames(month)]
            with closing(self._connection(read_only=True)) as connection:
                connection.execute("BEGIN")
                tables = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )}
                if not {"transactions", "journal", "imports", "manual_entries", "review_actions"} <= tables:
                    result["missing_files"].append("database tables")
                    result["error"] = "Create the missing workspace tables before importing or processing."
                    return result
                result.update(journal_snapshot(
                    connection, accounts, period=period, securities=securities, budget=budget,
                ))
                entries = load_journal_entries(connection, accounts, securities=securities)
                quotes = {}
                if "price_quotes" in tables:
                    for holding in holdings_as_of(entries, period.cutoff):
                        security = securities.by_id(holding.security_id)
                        if security.id in quotes:
                            continue
                        try:
                            quote = load_quote(
                                connection, security.id, period.cutoff, price_symbol(security),
                            )
                        except ValueError:
                            # Reports retain recorded value when a saved quote cannot be used.
                            continue
                        if quote is not None:
                            quotes[security.id] = quote
                reports = build_reports(
                    entries, accounts, settings, period, quotes=quotes, securities=securities,
                    budget_settings=budget,
                    savings_allocations=load_savings_allocations(connection).values(),
                )
                budget_report = reports.budget_report
                result["financials"] = {
                    "net_worth": reports.balance_sheet.net_worth,
                    "income": budget_report.total_income, "expenses": budget_report.total_expenses,
                    "savings": budget_report.total_savings,
                }
                result["net_worth_unpriced_holdings"] = [
                    position.name for position in reports.investment_report.positions
                    if position.security_key is not None and position.market_value is None
                ]
                result["posted_entry_count"] = sum(entry.date <= period.cutoff for entry in entries)
                financial_counts = budget_transaction_counts(entries, accounts, budget, period)
                try:
                    result["investment_reviews"] = investment_review_items(
                        connection, accounts, settings, period.cutoff,
                    )
                except Exception as error:
                    from aurum.gui.background import error_message
                    result["investment_reviews_error"] = error_message(error)
                import_rows = [
                    dict(zip(("source_hash", "source_file", "imported_at", "parsed_count", "inserted_count"), row))
                    for row in connection.execute(
                        "SELECT source_hash, source_file, imported_at, parsed_count, inserted_count "
                        "FROM imports ORDER BY imported_at DESC, rowid DESC"
                    )
                ]
                history = {}
                for record in import_rows:
                    history.setdefault(record["source_file"], {
                        key: value for key, value in record.items() if key != "source_hash"
                    })
                result["imports"] = list(history.values())
            result["statement_completion"] = statement_completion(
                month, accounts, self.data / "bank_statements", import_rows,
                metadata_cache=self._statement_metadata,
            )
            result["financial_transaction_counts"] = financial_counts
            result["gui_theme"] = resolve_gui_theme(settings)
            result["ready"] = True
        except (OSError, ValueError, sqlite3.Error) as error:
            from aurum.gui.background import error_message
            result["error"] = error_message(error)
        return result

    def run(self, action: str, payload: dict, month: str, progress, cancelled) -> dict:
        """
        Dispatch one explicit desktop action and return a structured result for its page.
        """
        if cancelled():
            return {"message": "Stopped before starting."}
        if action == "reload_entry_editor":
            action = "open_entry_editor"
        if action == "initialise":
            initialise_workspace(self.root)
            return {"message": "Workspace ready. Add your bank and category accounts in Setup."}
        if action in {"connect_chatgpt", "save_model"}:
            return self._chatgpt(action, payload, progress, cancelled)
        if action == "stage_statements":
            results = stage_statements(
                payload["paths"], load_accounts(self.config / "accounts.yaml"),
                self.data / "bank_statements", progress=progress, cancelled=cancelled,
                prepared_statements=self._prepared_statements,
            )
            failed = sum(result.status == "failed" for result in results)
            ready = len(results) - failed
            return {
                "message": f"{ready} PDF(s) ready to import. {failed} could not be prepared.",
                "data": [dict(asdict(result), source_path=str(result.source_path), path=str(result.path))
                         for result in results],
            }
        accounts, securities, budget = self._models()
        if action == "import_statements":
            results = import_statements(
                payload["paths"], accounts, self.database, managed_folder=self.data / "bank_statements",
                progress=progress, cancelled=cancelled,
                prepared_statements=self._prepared_statements,
            )
            inserted = sum(result.inserted for result in results)
            failed = sum(result.status == "failed" for result in results)
            return {
                "message": f"Added {inserted} transactions. {failed} file(s) failed.",
                "data": [dict(asdict(result), path=str(result.path)) for result in results],
            }
        if action == "generate_reports":
            result = generate_reports(
                accounts_path=self.config / "accounts.yaml", securities_path=self.config / "securities.yaml",
                settings_path=self.config / "reports.yaml", budget_path=self.config / "budget.yaml",
                database_path=self.database, workspace=self.root, month=month, progress=progress,
            )
            reasons = list(dict.fromkeys(
                reason for report in (
                    result.reports.balance_sheet, result.reports.income_statement,
                    result.reports.budget_report, result.reports.investment_report,
                ) for reason in report.draft_reasons
            ))
            return {
                "message": f"Four {'draft ' if reasons else ''}PDFs saved to {result.paths[0].parent}.",
                "paths": list(map(str, result.paths)), "draft_reasons": reasons,
            }
        editor_actions = {"open_entry_editor", "open_manual_editor", "reload_manual_editor"}
        with closing(self._connection(read_only=action in editor_actions)) as connection:
            if action in editor_actions:
                connection.execute("BEGIN")
                period = resolve_period([], month=month)
                snapshot = editor_snapshot(connection, accounts, payload.get("manual_entry_id"))
                record = next((
                    row for row in snapshot["manuals"]
                    if row["manual_entry_id"] == payload.get("manual_entry_id")
                ), None)
                manual_view = action in {"open_manual_editor", "reload_manual_editor"}
                sources = tuple(payload.get("source_ids", ()))
                if payload.get("transaction_id"):
                    sources += (payload["transaction_id"],)
                initial_date = payload.get("date")
                if not initial_date and not payload.get("manual_entry_id") and not sources:
                    initial_date = min(period.end_date, date.today())
                if manual_view and record and record.get("is_savings"):
                    context = {
                        "manual_entry_id": record["manual_entry_id"],
                        "entry": {"date": record["date"], "lines": []},
                        "direct_unavailable": "Savings-pot allocations stay outside the journal. "
                        "Edit this allocation as an instruction.",
                    }
                else:
                    context = load_journal_edit(
                        connection, payload.get("journal_entry_id"), accounts,
                        securities=securities, budget=budget, manual_id=payload.get("manual_entry_id"),
                        source_ids=sources, date=initial_date,
                    )
                context["transactions"] = snapshot["transactions"]
                line_number = payload.get("line_number")
                if type(line_number) is int and line_number > 0:
                    context["focus_line_number"] = line_number
                settings = load_report_settings(self.config / "reports.yaml", accounts)
                context.update({
                    "accounts": [
                        {"id": str(item.id), "key": item.key, "name": item.name,
                         "account_type": item.account_type.value, "is_bank": item.bank_details is not None,
                         "bank_details": item.bank_details is not None}
                        for item in accounts.accounts.values()
                    ],
                    "budget_categories": [
                        {"key": key, "name": item.name, "kind": item.kind, "accounts": list(item.accounts)}
                        for key, item in budget.categories.items()
                    ],
                    "account_investments": [
                        str(accounts.accounts[key].id) for key in settings.account_investments
                    ],
                    "unrealised_gains_account_id": str(
                        accounts.accounts[settings.unrealised_gains_account].id
                    ),
                })
                if manual_view:
                    context.update({
                        "manual_composer": True,
                        "mode": payload.get("mode", (record or {}).get("entry_mode", "instruction")),
                        "manual_record": record or {
                            "manual_entry_id": context["manual_entry_id"],
                            "date": context["entry"]["date"],
                            "source_transactions": context.get("source_ids", []),
                        },
                        "snapshot": {**snapshot, "period_end": period.end_date.isoformat()},
                        "reload_instruction": bool(payload.get("reload_instruction")),
                    })
                return {"message": "Journal editor ready.", "editor": context}
            if action == "apply_journal_edit":
                entry = apply_journal_edit(
                    connection, payload, accounts, securities=securities, budget=budget,
                )
                return {
                    "message": "Journal entry saved. No AI request was made.",
                    "journal_entry_id": str(entry.id),
                }
            if action == "save_reviews":
                answers = payload.get("answers", [])
                submit_reviews(connection, accounts, answers=answers)
                return {
                    "transaction_ids": [answer["transaction_id"] for answer in answers],
                    "message": f"{len(answers)} answers saved. Process to apply them." + (
                        " Memory saved." if any(remember_text(item["text"]) for item in answers) else ""
                    ),
                }
            if action in {"save_review", "discard_review"}:
                operation = submit_review if action == "save_review" else discard_review
                operation(connection, accounts, **payload)
                return {
                    "transaction_id": payload["transaction_id"],
                    "message": ("Answer saved. Process the month to apply it." + (
                        " Memory saved." if remember_text(payload["text"]) else ""
                    )) if action == "save_review"
                    else "Pending answer discarded; posted accounting is unchanged.",
                }
            if action == "save_manual":
                submission = ManualInput.model_validate(payload)
                submit_manual_input(connection, submission, accounts)
                return {"message": "Manual instruction saved. Process the month to apply it." + (
                            " Memory saved." if any(remember_text(text) for text in (
                                submission.instruction, submission.human_response,
                            )) else ""
                        ),
                        "manual_entry_id": str(submission.id)}
            if action == "process_journal":
                return self._process(
                    connection, accounts, securities, budget, month, progress, cancelled,
                )
        raise ValueError(f"Unknown desktop action: {action}")

    def _connection(self, *, read_only: bool = False) -> sqlite3.Connection:
        """
        Open only an existing compatible database; creation belongs to explicit workspace setup.
        """
        mode = "ro" if read_only else "rw"
        connection = sqlite3.connect(self.database.as_uri() + f"?mode={mode}", uri=True)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            if read_only:
                connection.execute("PRAGMA query_only = ON")
            _check_schema(connection)
            return connection
        except Exception:
            connection.close()
            raise

    def _models(self) -> tuple:
        """
        Validate saved definitions without initializing IDs as a side effect of visiting a screen.
        """
        accounts = load_accounts(self.config / "accounts.yaml", initialise_ids=False)
        securities = load_securities(self.config / "securities.yaml", initialise_ids=False)
        budget = load_budget_settings(self.config / "budget.yaml", accounts)
        return accounts, securities, budget

    def _chatgpt(self, action: str, payload: dict, progress, cancelled) -> dict:
        """
        Check sign-in or save an available model, releasing the credential lock before the user chooses.
        """
        progress("Checking ChatGPT access and available models…")
        with connect(
            force_sign_in=payload.get("force_sign_in", False), state_directory=self.data / "ai",
            progress=progress, cancelled=cancelled,
        ) as client:
            models = client.list_models()
            if cancelled():
                return {"message": "Connection check stopped; model selection is unchanged."}
            if not models:
                raise ValueError("Your ChatGPT account did not return available models.")
            if action == "save_model":
                selected = payload["model"]
                if selected not in {model["slug"] for model in models}:
                    raise ValueError("That model is unavailable. Check the connection and choose again.")
                client.select_model(selected)
            return {
                "message": "ChatGPT connection checked.",
                "data": {"models": models, "model": client.model},
            }

    def _process(self, connection, accounts, securities, budget, month, progress, cancelled) -> dict:
        """
        Process monthly accounting, then assess remaining accepted reviews without risking saved postings.
        """
        model = connection_status(self.data / "ai").get("model") or DEFAULT_MODEL
        client_factory = partial(
            connect, state_directory=self.data / "ai", progress=progress, cancelled=cancelled,
        )
        counts = process_journal(
            connection, accounts, model=model, retry_errors=True,
            securities=securities, budget=budget, client_factory=client_factory, progress=progress,
            period=resolve_period([], month=month), cancelled=cancelled,
            report_settings=load_report_settings(self.config / "reports.yaml", accounts),
        )
        warnings = []
        guidance_counts_complete = True
        counts.setdefault("guidance_assessed", 0)
        if not counts.get("stopped") and not cancelled():
            try:
                learning = learn_from_reviews(
                    connection, accounts, model=model, retry_errors=True,
                    guidance_path=self.data / "ai" / "categorisation_guidelines.md",
                    client_factory=client_factory, progress=progress, cancelled=cancelled,
                )
            except Exception as error:
                from aurum.gui.background import error_message
                guidance_counts_complete = False
                warnings.append(
                    "Accounting is saved. Guidance learning or its export could not finish: "
                    + error_message(error)
                )
            else:
                for key in ("requests", "input_tokens", "output_tokens", "total_tokens", "guidance_assessed"):
                    counts[key] = counts.get(key, 0) + learning.get(key, 0)
                counts["stopped"] = int(bool(counts.get("stopped") or learning.get("stopped")))
            try:
                states = {
                    item["review_action_id"]: item["status"] for item in load_guidance_assessments(connection)
                }
                eligible = list_guidance_cases(connection, accounts, retry_errors=True)
                failed = sum(states.get(case.id) in {"failed", "requesting"} for case in eligible)
                counts["guidance_failed"] = failed
                counts["guidance_pending"] = len(eligible) - failed
                if failed:
                    warnings.append(
                        f"{failed} accepted review guidance assessment(s) could not finish; "
                        "Process month will retry them. "
                        "Saved accounting is unchanged."
                    )
            except Exception as error:
                from aurum.gui.background import error_message
                warnings.append("Guidance status is unavailable: " + error_message(error))
        counts["stopped"] = int(bool(counts.get("stopped") or cancelled()))
        request_summary = (
            f"{counts['requests']} AI request(s)." if guidance_counts_complete else
            f"At least {counts['requests']} AI request(s); unfinished learning usage is unavailable."
        )
        return {
            "message": (
                ("Stopped. " if counts.get("stopped") else "") +
                f"{counts['entries_created']} bank events created; "
                f"{counts['reviews_applied']} reviews and {counts['manual_posted']} manuals applied. "
                + request_summary + (" " + " ".join(warnings) if warnings else "")
            ),
            "data": counts, "guidance_warnings": warnings,
            "guidance_counts_complete": guidance_counts_complete,
        }
